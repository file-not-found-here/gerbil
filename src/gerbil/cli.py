from __future__ import annotations

import hashlib
import json
import logging
import multiprocessing
import os
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import partial
from pathlib import Path

import typer
from cldk import CLDK
from cldk.analysis import AnalysisLevel
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from gerbil.analysis.shared.constants import TEST_DIRS
from gerbil.analysis.project import ProjectAnalysisInfo
from gerbil.statistics import (
    build_candidate_pool,
    build_inventory_payload,
    build_sample_payload,
    collect_api_test_projects,
    compute_all_statistics,
    discover_gerbil_files,
    draw_random_sample,
    load_project_records,
    rank_interesting_projects,
    select_sample,
    write_inventory,
    write_sample,
    write_statistics,
)

app = typer.Typer(
    help="Gerbil static analysis",
    add_completion=False,
    pretty_exceptions_enable=False,
    pretty_exceptions_show_locals=False,
)

LOGGER = logging.getLogger(__name__)


@app.callback()
def main(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose (DEBUG-level) logging with rich output.",
    ),
) -> None:
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(message)s",
            handlers=[RichHandler(rich_tracebacks=True)],
            force=True,
        )


def _parse_test_dirs(raw_value: str) -> tuple[str, ...]:
    test_dirs = tuple(
        test_dir.strip() for test_dir in raw_value.split(",") if test_dir.strip()
    )
    if not test_dirs:
        raise typer.BadParameter(
            "test_dirs must contain at least one comma-separated path pattern"
        )
    return test_dirs


def _discover_project_roots(input_root: str) -> list[Path]:
    input_root_path = Path(input_root).resolve()
    if not input_root_path.exists():
        raise typer.BadParameter(f"input_root does not exist: {input_root}")
    if not input_root_path.is_dir():
        raise typer.BadParameter(f"input_root is not a directory: {input_root}")

    project_roots = sorted(
        entry
        for entry in input_root_path.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    )
    if not project_roots:
        raise typer.BadParameter(
            f"input_root does not contain any project directories: {input_root}"
        )
    return project_roots


class _BatchProgress:
    """Batch progress on stderr: a live bar on a terminal, [n/total] lines otherwise."""

    def __init__(
        self,
        total: int,
        console: Console | None = None,
        description: str = "Analyzing projects",
    ) -> None:
        self._total = total
        self._done = 0
        self._console = console if console is not None else Console(stderr=True)
        self._bar: tuple[Progress, TaskID] | None = None
        if self._console.is_terminal:
            bar = Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=self._console,
            )
            self._bar = (bar, bar.add_task(description, total=total))

    def __enter__(self) -> _BatchProgress:
        if self._bar is not None:
            self._bar[0].start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._bar is not None:
            self._bar[0].stop()

    def _echo(self, line: str) -> None:
        self._console.print(line, markup=False, highlight=False, soft_wrap=True)

    def finish_ok(
        self, project_name: str, elapsed_seconds: float | None = None
    ) -> None:
        self._done += 1
        if self._bar is not None:
            self._bar[0].advance(self._bar[1])
            return
        suffix = "" if elapsed_seconds is None else f" ({elapsed_seconds:.1f}s)"
        self._echo(f"[{self._done}/{self._total}] ok: {project_name}{suffix}")

    def finish_failed(self, project_name: str, error: str) -> None:
        self._done += 1
        if self._bar is not None:
            self._echo(f"failed: {project_name}: {error}")
            self._bar[0].advance(self._bar[1])
            return
        self._echo(f"[{self._done}/{self._total}] failed: {project_name}: {error}")

    def finish_skipped(self, project_name: str, reason: str) -> None:
        self._done += 1
        message = f"skip: {project_name} ({reason})"
        if self._bar is not None:
            self._echo(message)
            self._bar[0].advance(self._bar[1])
            return
        self._echo(f"[{self._done}/{self._total}] {message}")


def _timed_analyze(
    analyze: Callable[..., Path], /, **kwargs: object
) -> tuple[Path, float]:
    start = time.perf_counter()
    return analyze(**kwargs), time.perf_counter() - start


def _execute_batch(
    project_roots: list[Path],
    skipped_projects: list[Path],
    projects_to_run: list[Path],
    make_task: Callable[[Path], partial[tuple[Path, float]]],
    jobs: int,
    description: str,
    skip_reason: str,
) -> None:
    """Run per-project tasks, report progress, and emit the JSON summary."""
    output_by_project: dict[Path, Path] = {}
    error_by_project: dict[Path, str] = {}

    with _BatchProgress(total=len(project_roots), description=description) as progress:
        for project_root in skipped_projects:
            progress.finish_skipped(project_root.name, skip_reason)

        def _record(
            project_root: Path, resolve: Callable[[], tuple[Path, float]]
        ) -> None:
            try:
                output_file, elapsed_seconds = resolve()
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                LOGGER.debug("analysis failed for %s", project_root, exc_info=exc)
                error_by_project[project_root] = error
                progress.finish_failed(project_root.name, error)
            else:
                output_by_project[project_root] = output_file
                progress.finish_ok(project_root.name, elapsed_seconds)

        if jobs == 1:
            for project_root in projects_to_run:
                _record(project_root, make_task(project_root))
        elif projects_to_run:
            # spawn is required (fork is incompatible with max_tasks_per_child);
            # one task per child returns each project's analysis memory to the OS.
            with ProcessPoolExecutor(
                max_workers=jobs,
                mp_context=multiprocessing.get_context("spawn"),
                max_tasks_per_child=1,
            ) as pool:
                future_to_project = {
                    pool.submit(make_task(project_root)): project_root
                    for project_root in projects_to_run
                }
                for future in as_completed(future_to_project):
                    _record(future_to_project[future], future.result)

    output_files = [
        str(output_by_project[project_root])
        for project_root in project_roots
        if project_root in output_by_project
    ]
    failures = [
        {"project_path": str(project_root), "error": error_by_project[project_root]}
        for project_root in project_roots
        if project_root in error_by_project
    ]
    skips = [str(project_root) for project_root in skipped_projects]

    typer.echo(
        json.dumps(
            {
                "projects": len(project_roots),
                "succeeded": len(output_files),
                "skipped": len(skips),
                "failed": len(failures),
                "outputs": output_files,
                "skips": skips,
                "failures": failures,
            },
            indent=2,
        )
    )

    if failures:
        raise typer.Exit(code=1)


def _analyze_project(
    project_root: Path,
    dataset_name: str,
    analysis_dir: Path,
    output_dir: Path,
    analysis_backend_path: str | None,
    eager: bool,
    expanded_helper_depth: int,
    test_dirs: tuple[str, ...],
) -> Path:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    analysis = CLDK(language="java").analysis(
        project_path=str(project_root),
        analysis_level=AnalysisLevel.symbol_table,
        analysis_json_path=str(analysis_dir),
        analysis_backend_path=analysis_backend_path,
        eager=eager,
    )

    project_analysis = ProjectAnalysisInfo(
        analysis=analysis,
        dataset_name=dataset_name,
        project_path=str(project_root),
        expanded_helper_depth=expanded_helper_depth,
        test_dirs=test_dirs,
    ).gather_project_analysis_info()

    output_file = output_dir / "gerbil.json"
    output_file.write_text(
        project_analysis.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return output_file


def _generate_cldk_cache(
    project_root: Path,
    analysis_dir: Path,
    analysis_backend_path: str | None,
) -> Path:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    CLDK(language="java").analysis(
        project_path=str(project_root),
        analysis_level=AnalysisLevel.symbol_table,
        analysis_json_path=str(analysis_dir),
        analysis_backend_path=analysis_backend_path,
        eager=True,
    )
    return analysis_dir / "analysis.json"


@app.command("analysis")
def analysis_command(
    project_path: str = typer.Option(..., help="Path to a Java project root directory"),
    analysis_path: str = typer.Option(
        ..., help="Directory where CLDK analysis.json cache is stored"
    ),
    output_path: str = typer.Option(..., help="Directory for gerbil.json output"),
    analysis_backend_path: str | None = typer.Option(
        None,
        help="Optional directory containing codeanalyzer-*.jar",
    ),
    eager: bool = typer.Option(
        False,
        help="Force regeneration of CLDK analysis cache",
    ),
    expanded_helper_depth: int = typer.Option(
        10,
        "--expanded-helper-depth",
        min=0,
        help=(
            "Depth for helper expansion in expanded analysis. "
            "0 keeps root methods only; 1 includes direct helpers."
        ),
    ),
    test_dirs: str = typer.Option(
        ",".join(TEST_DIRS),
        "--test-dirs",
        help=(
            "Comma-separated test-dir path patterns used for class "
            "categorization (segment-safe matching)."
        ),
    ),
) -> None:
    parsed_test_dirs = _parse_test_dirs(test_dirs)
    project_root = Path(project_path).resolve()
    if not project_root.exists():
        raise typer.BadParameter(f"project_path does not exist: {project_path}")
    if not project_root.is_dir():
        raise typer.BadParameter(f"project_path is not a directory: {project_path}")

    project_bucket_name = (
        f"{project_root.name}-"
        f"{hashlib.sha256(str(project_root).encode('utf-8')).hexdigest()[:8]}"
    )

    output_file = _analyze_project(
        project_root=project_root,
        dataset_name=project_bucket_name,
        analysis_dir=Path(analysis_path).resolve() / project_bucket_name,
        output_dir=Path(output_path).resolve() / project_bucket_name,
        analysis_backend_path=analysis_backend_path,
        eager=eager,
        expanded_helper_depth=expanded_helper_depth,
        test_dirs=parsed_test_dirs,
    )
    typer.echo(f"Wrote analysis: {output_file}")


@app.command("batch-analysis")
def batch_analysis_command(
    input_root: str = typer.Option(
        ...,
        help="Directory containing one repository per subdirectory",
    ),
    analysis_root: str = typer.Option(
        ...,
        help="Root directory holding <project-name>/analysis.json CLDK caches",
    ),
    output_root: str = typer.Option(
        ...,
        help="Root directory for <project-name>/gerbil.json outputs",
    ),
    analysis_backend_path: str | None = typer.Option(
        None,
        help="Optional directory containing codeanalyzer-*.jar",
    ),
    eager: bool = typer.Option(
        False,
        help="Force regeneration of CLDK analysis cache",
    ),
    expanded_helper_depth: int = typer.Option(
        10,
        "--expanded-helper-depth",
        min=0,
        help=(
            "Depth for helper expansion in expanded analysis. "
            "0 keeps root methods only; 1 includes direct helpers."
        ),
    ),
    test_dirs: str = typer.Option(
        ",".join(TEST_DIRS),
        "--test-dirs",
        help=(
            "Comma-separated test-dir path patterns used for class "
            "categorization (segment-safe matching)."
        ),
    ),
    jobs: int = typer.Option(
        2,
        "--jobs",
        min=1,
        help=(
            "Number of projects to analyze concurrently. Each worker loads a "
            "full analysis.json into memory, so size this to available RAM; "
            "cache misses additionally spawn a JVM per worker."
        ),
    ),
    skip_missing_analysis: bool = typer.Option(
        False,
        "--skip-missing-analysis",
        help=(
            "Skip projects whose analysis dir has no cached analysis.json "
            "instead of letting CLDK run JavaAnalysis to generate one."
        ),
    ),
) -> None:
    parsed_test_dirs = _parse_test_dirs(test_dirs)
    project_roots = _discover_project_roots(input_root)
    analysis_root_path = Path(analysis_root).resolve()
    output_root_path = Path(output_root).resolve()

    # CLDK loads the cache from <analysis_dir>/analysis.json; without one it would
    # run JavaAnalysis (spawning a JVM), which this flag avoids by skipping.
    skipped_projects: list[Path] = []
    projects_to_analyze = list(project_roots)
    if skip_missing_analysis:
        projects_to_analyze = []
        for project_root in project_roots:
            if (analysis_root_path / project_root.name / "analysis.json").is_file():
                projects_to_analyze.append(project_root)
            else:
                skipped_projects.append(project_root)

    def _project_task(project_root: Path) -> partial[tuple[Path, float]]:
        return partial(
            _timed_analyze,
            _analyze_project,
            project_root=project_root,
            dataset_name=project_root.name,
            analysis_dir=analysis_root_path / project_root.name,
            output_dir=output_root_path / project_root.name,
            analysis_backend_path=analysis_backend_path,
            eager=eager,
            expanded_helper_depth=expanded_helper_depth,
            test_dirs=parsed_test_dirs,
        )

    _execute_batch(
        project_roots=project_roots,
        skipped_projects=skipped_projects,
        projects_to_run=projects_to_analyze,
        make_task=_project_task,
        jobs=jobs,
        description="Analyzing projects",
        skip_reason="no analysis.json",
    )


@app.command("batch-cldk-cache")
def batch_cldk_cache_command(
    input_root: str = typer.Option(
        ...,
        help="Directory containing one repository per subdirectory",
    ),
    output_root: str = typer.Option(
        ...,
        help="Root directory for <project-name>/analysis.json CLDK caches",
    ),
    analysis_backend_path: str | None = typer.Option(
        None,
        help="Optional directory containing codeanalyzer-*.jar",
    ),
    skip_existing: bool = typer.Option(
        False,
        "--skip-existing",
        help=(
            "Skip projects whose output dir already holds an analysis.json "
            "instead of eagerly regenerating it."
        ),
    ),
    jobs: int = typer.Option(
        2,
        "--jobs",
        min=1,
        help=(
            "Number of caches to generate concurrently. Each worker spawns a "
            "JVM and parses the resulting analysis.json, so size this to "
            "available RAM and cores."
        ),
    ),
) -> None:
    project_roots = _discover_project_roots(input_root)
    output_root_path = Path(output_root).resolve()

    skipped_projects: list[Path] = []
    projects_to_cache = list(project_roots)
    if skip_existing:
        projects_to_cache = []
        for project_root in project_roots:
            if (output_root_path / project_root.name / "analysis.json").is_file():
                skipped_projects.append(project_root)
            else:
                projects_to_cache.append(project_root)

    def _project_task(project_root: Path) -> partial[tuple[Path, float]]:
        return partial(
            _timed_analyze,
            _generate_cldk_cache,
            project_root=project_root,
            analysis_dir=output_root_path / project_root.name,
            analysis_backend_path=analysis_backend_path,
        )

    _execute_batch(
        project_roots=project_roots,
        skipped_projects=skipped_projects,
        projects_to_run=projects_to_cache,
        make_task=_project_task,
        jobs=jobs,
        description="Generating CLDK caches",
        skip_reason="analysis.json exists",
    )


@app.command("figures")
def figures_command(
    stats_root: str = typer.Option(
        ...,
        help="Root directory holding one statistics directory per tool",
    ),
    output_dir: str = typer.Option(
        ...,
        help="Directory for the generated figure files",
    ),
    dev_dir: str = typer.Option(
        ...,
        help=(
            "Name of the statistics directory under stats_root that receives "
            "dev-only figures"
        ),
    ),
    ignore_unknown: bool = typer.Option(
        False,
        "--ignore-unknown",
        help=(
            "Orient categorical figures to classified items only: drop "
            "'unknown' categories and renormalize share-based splits over "
            "the classified portion."
        ),
    ),
) -> None:
    # Imported lazily: matplotlib is slow to import and the batch commands
    # re-import this module in every spawned worker.
    from gerbil.figures import (
        FigureOptions,
        generate_all_figures,
        load_stats_directories,
    )

    stats_root_path = Path(stats_root).resolve()
    if not stats_root_path.exists():
        raise typer.BadParameter(f"stats_root does not exist: {stats_root}")
    if not stats_root_path.is_dir():
        raise typer.BadParameter(f"stats_root is not a directory: {stats_root}")

    try:
        stats_dirs = load_stats_directories(stats_root_path, dev_dir)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    results, skipped = generate_all_figures(
        stats_dirs,
        dev_dir,
        Path(output_dir).resolve(),
        options=FigureOptions(ignore_unknown=ignore_unknown),
    )

    typer.echo(
        json.dumps(
            {
                "stats_dirs": [directory.name for directory in stats_dirs],
                "dev_dir": dev_dir,
                "ignore_unknown": ignore_unknown,
                "figures": len(results),
                "outputs": [str(path) for result in results for path in result.paths],
                "skipped": [
                    {"figure": skip.name, "reason": skip.reason} for skip in skipped
                ],
            },
            indent=2,
        )
    )


@app.command("statistics")
def statistics_command(
    input_root: str = typer.Option(
        ...,
        help="Root directory holding <project>/gerbil.json analysis outputs",
    ),
    output_dir: str = typer.Option(
        ...,
        help="Directory for the per-type statistics .json files",
    ),
    jobs: int = typer.Option(
        os.cpu_count() or 4,
        "--jobs",
        min=1,
        help=(
            "Number of analyses to load and validate concurrently. Loading is "
            "CPU-bound JSON parsing, so size this to available cores."
        ),
    ),
) -> None:
    input_root_path = Path(input_root).resolve()
    if not input_root_path.exists():
        raise typer.BadParameter(f"input_root does not exist: {input_root}")
    if not input_root_path.is_dir():
        raise typer.BadParameter(f"input_root is not a directory: {input_root}")

    gerbil_files = discover_gerbil_files(input_root_path)
    if not gerbil_files:
        raise typer.BadParameter(
            f"input_root does not contain any gerbil.json outputs: {input_root}"
        )

    output_dir_path = Path(output_dir).resolve()

    with _BatchProgress(
        total=len(gerbil_files), description="Loading analyses"
    ) as progress:
        records, failures = load_project_records(
            gerbil_files,
            jobs=jobs,
            on_loaded=lambda path: progress.finish_ok(path.parent.name),
            on_failed=lambda path, error: progress.finish_failed(
                path.parent.name, error
            ),
        )

    statistics = compute_all_statistics(records)
    written = write_statistics(statistics, output_dir_path)

    typer.echo(
        json.dumps(
            {
                "analyses": len(gerbil_files),
                "loaded": len(records),
                "failed": len(failures),
                "tests": sum(len(record.tests) for record in records),
                "endpoints": sum(len(record.endpoints) for record in records),
                "endpoint_parameters": sum(
                    len(record.endpoint_parameters) for record in records
                ),
                "resources": sum(len(record.resources) for record in records),
                "outputs": [str(path) for path in written],
                "failures": [
                    {"path": str(failure.path), "error": failure.error}
                    for failure in failures
                ],
            },
            indent=2,
        )
    )

    if failures:
        raise typer.Exit(code=1)


@app.command("sample-projects")
def sample_projects_command(
    input_root: str = typer.Option(
        ...,
        help="Root directory holding <project>/gerbil.json analysis outputs",
    ),
    output_dir: str = typer.Option(
        ...,
        help="Directory for the sampled-projects .json file",
    ),
    jobs: int = typer.Option(
        os.cpu_count() or 4,
        "--jobs",
        min=1,
        help=(
            "Number of analyses to load and validate concurrently. Loading is "
            "CPU-bound JSON parsing, so size this to available cores."
        ),
    ),
    count: int | None = typer.Option(
        None,
        "--count",
        min=1,
        help=(
            "Keep the top N projects by API test count. Mutually exclusive with "
            "--percentile; defaults to 10 when neither is given."
        ),
    ),
    percentile: float | None = typer.Option(
        None,
        "--percentile",
        help=(
            "Keep the top P%% (0 < P <= 100) of the interesting project set. "
            "Mutually exclusive with --count."
        ),
    ),
    random_sample: bool = typer.Option(
        False,
        "--random/--no-random",
        help=(
            "Draw the sample randomly from the top --pool-percent%% of interesting "
            "projects instead of the top by API test count. Reproducible via --seed."
        ),
    ),
    seed: int = typer.Option(
        0,
        "--seed",
        help="Seed for --random sampling (ignored without --random).",
    ),
    pool_percent: float = typer.Option(
        10.0,
        "--pool-percent",
        help=(
            "With --random, the candidate pool is the top P%% of interesting "
            "projects by API test count (default 10). Ignored without --random."
        ),
    ),
) -> None:
    if count is not None and percentile is not None:
        raise typer.BadParameter("pass either --count or --percentile, not both")
    if percentile is not None and not 0 < percentile <= 100:
        raise typer.BadParameter("--percentile must be in the range (0, 100]")
    if random_sample and not 0 < pool_percent <= 100:
        raise typer.BadParameter("--pool-percent must be in the range (0, 100]")
    # Neither given: sample the top 10 projects by API test count by default.
    if count is None and percentile is None:
        count = 10

    input_root_path = Path(input_root).resolve()
    if not input_root_path.exists():
        raise typer.BadParameter(f"input_root does not exist: {input_root}")
    if not input_root_path.is_dir():
        raise typer.BadParameter(f"input_root is not a directory: {input_root}")

    gerbil_files = discover_gerbil_files(input_root_path)
    if not gerbil_files:
        raise typer.BadParameter(
            f"input_root does not contain any gerbil.json outputs: {input_root}"
        )

    output_dir_path = Path(output_dir).resolve()

    with _BatchProgress(
        total=len(gerbil_files), description="Loading analyses"
    ) as progress:
        records, failures = load_project_records(
            gerbil_files,
            jobs=jobs,
            on_loaded=lambda path: progress.finish_ok(path.parent.name),
            on_failed=lambda path, error: progress.finish_failed(
                path.parent.name, error
            ),
        )

    # The loader returns records in gerbil_files order, successes only, so the
    # non-failed paths align positionally with the records.
    failed_paths = {failure.path for failure in failures}
    loaded_paths = [path for path in gerbil_files if path not in failed_paths]

    ranked = rank_interesting_projects(records, loaded_paths)
    if random_sample:
        pool = build_candidate_pool(ranked, pool_percent)
        selected = draw_random_sample(
            pool, count=count, percentile=percentile, seed=seed
        )
        payload = build_sample_payload(
            ranked,
            selected,
            count=count,
            percentile=percentile,
            mode="random",
            seed=seed,
            pool_percent=pool_percent,
            pool_project_count=len(pool),
        )
    else:
        selected = select_sample(ranked, count=count, percentile=percentile)
        payload = build_sample_payload(
            ranked, selected, count=count, percentile=percentile
        )
    output_file = write_sample(payload, output_dir_path)

    typer.echo(
        json.dumps(
            {
                "analyses": len(gerbil_files),
                "loaded": len(records),
                "failed": len(failures),
                "interesting_projects": len(ranked),
                "selected_projects": len(selected),
                "output": str(output_file),
                "failures": [
                    {"path": str(failure.path), "error": failure.error}
                    for failure in failures
                ],
            },
            indent=2,
        )
    )

    if failures:
        raise typer.Exit(code=1)


@app.command("api-test-projects")
def api_test_projects_command(
    input_root: str = typer.Option(
        ...,
        help="Root directory holding <project>/gerbil.json analysis outputs",
    ),
    output_dir: str = typer.Option(
        ...,
        help="Directory for the api_test_projects.json inventory file",
    ),
    jobs: int = typer.Option(
        os.cpu_count() or 4,
        "--jobs",
        min=1,
        help=(
            "Number of analyses to load and validate concurrently. Loading is "
            "CPU-bound JSON parsing, so size this to available cores."
        ),
    ),
) -> None:
    input_root_path = Path(input_root).resolve()
    if not input_root_path.exists():
        raise typer.BadParameter(f"input_root does not exist: {input_root}")
    if not input_root_path.is_dir():
        raise typer.BadParameter(f"input_root is not a directory: {input_root}")

    gerbil_files = discover_gerbil_files(input_root_path)
    if not gerbil_files:
        raise typer.BadParameter(
            f"input_root does not contain any gerbil.json outputs: {input_root}"
        )

    output_dir_path = Path(output_dir).resolve()

    with _BatchProgress(
        total=len(gerbil_files), description="Loading analyses"
    ) as progress:
        records, failures = load_project_records(
            gerbil_files,
            jobs=jobs,
            on_loaded=lambda path: progress.finish_ok(path.parent.name),
            on_failed=lambda path, error: progress.finish_failed(
                path.parent.name, error
            ),
        )

    entries = collect_api_test_projects(records)
    payload = build_inventory_payload(entries)
    output_file = write_inventory(payload, output_dir_path)

    typer.echo(
        json.dumps(
            {
                "analyses": len(gerbil_files),
                "loaded": len(records),
                "failed": len(failures),
                "api_test_projects": len(entries),
                "output": str(output_file),
                "failures": [
                    {"path": str(failure.path), "error": failure.error}
                    for failure in failures
                ],
            },
            indent=2,
        )
    )

    if failures:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
