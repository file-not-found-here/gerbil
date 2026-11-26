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
from gerbil.evomaster.pipeline import RunSettings, Stage, run_target
from gerbil.evomaster.staging import export_generated_suite, stage_generated_suite
from gerbil.evomaster.summary import TargetStatus, funnel
from gerbil.evomaster.targets import TARGETS, EvoMasterTarget
from gerbil.execution.bundle import ExecutionInputs, build_bundle
from gerbil.execution.container import (
    build_image,
    export_maven_seed,
    require_case_sensitive_mount,
    require_memory_accounting,
)
from gerbil.execution.disk import free_gb
from gerbil.execution.errors import ExecutionError
from gerbil.execution.mutation import PitSettings
from gerbil.execution.pipeline import (
    MutationSettings,
    SuitePaths,
    run_coverage,
    run_mutation,
)
from gerbil.execution.projects import (
    MEASURED_PROJECTS,
    PROJECTS,
    ExecutionProject,
    Suite,
)
from gerbil.execution.resample import resample_project
from gerbil.execution.archive import zip_directory
from gerbil.execution.results import collect_results
from gerbil.execution.sampling import DEFAULT_SAMPLE_SEED, DEFAULT_SAMPLE_SIZE
from gerbil.execution.sources import EVOMASTER_RUNS_DIR, EvoMasterRuns, SuiteSources
from gerbil.execution.structure import compute as compute_test_structure
from gerbil.execution.structure import find_analysis, load_suite_kills
from gerbil.execution.summary import write_summary
from gerbil.validation.bundle import (
    BUNDLE_FILE,
    BundleError,
    build_bundle as build_validation_bundle,
    read_bundle as read_validation_bundle,
    write_bundle as write_validation_bundle,
)
from gerbil.validation.page import PAGE_FILE, write_page
from gerbil.validation.sampling import (
    MODELS_FILE,
    SAMPLE_FILE,
    DEFAULT_PER_PROJECT_CAP as VALIDATION_PER_PROJECT_CAP,
    DEFAULT_SAMPLE_SEED as VALIDATION_SAMPLE_SEED,
    DEFAULT_SAMPLE_SIZE as VALIDATION_SAMPLE_SIZE,
    build_sample_payload as build_validation_sample_payload,
    draw_sample,
    load_candidates,
    load_sampled_tests,
    read_sample,
    read_sample_payload,
    write_sample as write_validation_sample,
)
from gerbil.validation.scoring import (
    ANNOTATIONS_DIRECTORY,
    DEFAULT_BOOTSTRAP_SAMPLES,
    SCORES_FILE,
    ScoringError,
    read_annotations,
    score as score_annotations,
    write_scores,
)
from gerbil.statistics import (
    LoadFailure,
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
from gerbil.statistics import subset_selection as subset_selection_stats
from gerbil.statistics import suite_comparison as suite_comparison_stats

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
    saint_comparison: bool = typer.Option(
        False,
        "--saint-comparison",
        help=(
            "Also write the SAINT comparison views: context-path-stripped endpoint "
            "and parameter coverage, and production-resource sequence grouping."
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

    statistics = compute_all_statistics(records, saint_comparison=saint_comparison)
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


def _parse_suite(spec: str) -> tuple[str, Path]:
    name, separator, root = spec.partition("=")
    if not separator or not name or not root:
        raise typer.BadParameter(f"expected NAME=ROOT, got: {spec}")
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise typer.BadParameter(f"suite root is not a directory: {root}")
    return name, root_path


@app.command("suite-comparison")
def suite_comparison_command(
    baseline: str = typer.Option(
        ...,
        help=(
            "Baseline suite as NAME=ROOT, where ROOT holds <project>/gerbil.json "
            "analysis outputs"
        ),
    ),
    suite: list[str] = typer.Option(
        ...,
        "--suite",
        help="Suite to compare against the baseline, as NAME=ROOT; repeatable",
    ),
    output_dir: str = typer.Option(
        ...,
        help="Directory for suite_comparison.json",
    ),
    jobs: int = typer.Option(
        os.cpu_count() or 4,
        "--jobs",
        min=1,
        help="Number of analyses to load and validate concurrently.",
    ),
) -> None:
    specs = [_parse_suite(spec) for spec in (baseline, *suite)]
    names = [name for name, _ in specs]
    if len(set(names)) != len(names):
        raise typer.BadParameter(f"suite names must be distinct: {', '.join(names)}")
    suite_files = [(name, discover_gerbil_files(root)) for name, root in specs]
    for (name, gerbil_files), (_, root) in zip(suite_files, specs):
        if not gerbil_files:
            raise typer.BadParameter(
                f"suite {name} does not contain any gerbil.json outputs: {root}"
            )

    suites: list[suite_comparison_stats.Suite] = []
    loaded: dict[str, dict[str, int]] = {}
    failures: list[LoadFailure] = []
    for name, gerbil_files in suite_files:
        with _BatchProgress(
            total=len(gerbil_files), description=f"Loading {name}"
        ) as progress:
            records, suite_failures = load_project_records(
                gerbil_files,
                jobs=jobs,
                on_loaded=lambda path: progress.finish_ok(path.parent.name),
                on_failed=lambda path, error: progress.finish_failed(
                    path.parent.name, error
                ),
            )
        failures.extend(suite_failures)
        loaded[name] = {
            "analyses": len(gerbil_files),
            "loaded": len(records),
            "failed": len(suite_failures),
        }
        suites.append(suite_comparison_stats.build_suite(name, records))

    report: dict[str, object] = {
        "loaded": loaded,
        "failures": [
            {"path": str(failure.path), "error": failure.error} for failure in failures
        ],
    }
    # A comparison over a partially loaded suite would pass for a complete one.
    if failures:
        typer.echo(json.dumps(report, indent=2))
        raise typer.Exit(code=1)

    payload = suite_comparison_stats.compute(suites[0], suites[1:])
    written = write_statistics(
        {suite_comparison_stats.SUITE_COMPARISON: payload}, Path(output_dir).resolve()
    )
    summary = payload["summary"]
    typer.echo(
        json.dumps(
            report
            | {
                "baseline": payload["baseline"],
                "suites": payload["suites"],
                "summary": {
                    "tested_comparison_count": summary["tested_comparison_count"],
                    "significant_count": summary["significant_count"],
                    "magnitude_counts": summary["magnitude_counts"],
                },
                "outputs": [str(path) for path in written],
            },
            indent=2,
        )
    )


def _analysis_outputs(input_root: str) -> list[Path]:
    input_root_path = Path(input_root).resolve()
    if not input_root_path.is_dir():
        raise typer.BadParameter(f"input_root is not a directory: {input_root}")
    gerbil_files = discover_gerbil_files(input_root_path)
    if not gerbil_files:
        raise typer.BadParameter(
            f"input_root does not contain any gerbil.json outputs: {input_root}"
        )
    return gerbil_files


@app.command("subset-selection")
def subset_selection_command(
    input_root: str = typer.Option(
        ...,
        help="Root directory holding the corpus's <project>/gerbil.json outputs",
    ),
    output_dir: str = typer.Option(
        ...,
        help="Directory for subset_selection.json",
    ),
    jobs: int = typer.Option(
        os.cpu_count() or 4,
        "--jobs",
        min=1,
        help="Number of analyses to load and validate concurrently.",
    ),
) -> None:
    """Test whether the comparison subset's projects differ from the rest of the
    corpus on the comparison table's measures."""
    gerbil_files = _analysis_outputs(input_root)

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

    report: dict[str, object] = {
        "analyses": len(gerbil_files),
        "loaded": len(records),
        "failed": len(failures),
        "failures": [
            {"path": str(failure.path), "error": failure.error} for failure in failures
        ],
    }
    # A project missing from the corpus would shift the group the subset is
    # compared with.
    if failures:
        typer.echo(json.dumps(report, indent=2))
        raise typer.Exit(code=1)

    try:
        payload = subset_selection_stats.compute(
            records, [project.dataset_name for project in PROJECTS.values()]
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    written = write_statistics(
        {subset_selection_stats.SUBSET_SELECTION: payload}, Path(output_dir).resolve()
    )
    typer.echo(
        json.dumps(
            report
            | {
                "project_count": payload["project_count"],
                "selected_project_count": payload["selected_project_count"],
                "rest_project_count": payload["rest_project_count"],
                "summary": payload["summary"],
                "outputs": [str(path) for path in written],
            },
            indent=2,
        )
    )


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


execution_app = typer.Typer(
    help="Run selected API tests under JaCoCo coverage and PIT mutation analysis."
)
app.add_typer(execution_app, name="execution")


def _execution_project(name: str) -> ExecutionProject:
    project = MEASURED_PROJECTS.get(name)
    if project is None:
        raise typer.BadParameter(
            f"unknown project {name!r}; choose from {', '.join(sorted(MEASURED_PROJECTS))}"
        )
    return project


def _finish_execution_stage(
    project: ExecutionProject, output_dir: Path, failures: dict[Suite, str]
) -> None:
    try:
        summary = write_summary(project, output_dir)
    except ExecutionError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(summary.read_text())
    for suite, message in failures.items():
        typer.echo(f"error: {suite.value}: {message}", err=True)
    if failures:
        raise typer.Exit(code=1)


def _directory_size(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def _megabytes(size: int) -> str:
    return f"{size / 1e6:.1f} MB"


def _report_archive(kind: str, directory: Path, parts: list[Path]) -> None:
    typer.echo(f"{kind}: {directory} ({_megabytes(_directory_size(directory))})")
    for part in parts:
        typer.echo(f"zip: {part} ({_megabytes(part.stat().st_size)})")
    if len(parts) > 1:
        typer.echo(
            f"send all {len(parts)} zips; extracting each into one directory rebuilds "
            f"{directory.name}/"
        )


_PART_MB_HELP = "Largest zip part in megabytes, to fit an email attachment"


_BUNDLE = "outputs/execution-bundle"
_BUNDLE_HELP = "Directory `gerbil execution bundle` wrote"
_OUTPUTS_ROOT_HELP = "Root holding the committed suite patches (outputs/<suite>/runs)"
_PAUSE_BELOW_HELP = (
    "New suites and PIT runs wait while the output directory's disk has fewer GB free"
)


def _load_inputs(bundle: str, outputs_root: str, repos_root: str) -> ExecutionInputs:
    try:
        return ExecutionInputs.load(
            Path(bundle).resolve(),
            Path(outputs_root).resolve(),
            Path(repos_root).resolve(),
        )
    except ExecutionError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error


_MAVEN_SEED = "outputs/execution-maven-seed"


@execution_app.command("seed")
def execution_seed_command(
    output: str = typer.Option(_MAVEN_SEED, help="New directory for the seed"),
) -> None:
    """Export the gerbil-m2 cache's snapshots and non-Central artifacts as the bundle's
    Maven seed.

    Snapshot repositories have dropped builds the projects pin, such as Kogito's own
    999-SNAPSHOT modules, and Kylin's Kyligence repository is gone, so a fresh cache
    cannot download them.
    """
    destination = Path(output).resolve()
    try:
        export_maven_seed(destination)
    except ExecutionError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"seed: {destination} ({_megabytes(_directory_size(destination))})")


@execution_app.command("bundle")
def execution_bundle_command(
    output: str = typer.Option(
        _BUNDLE, help="New directory for the bundle, or an earlier bundle to replace"
    ),
    outputs_root: str = typer.Option(
        "outputs", help="Root holding the Gerbil analyses and agent run directories"
    ),
    repos_root: str = typer.Option(
        "resources", help="Directory holding each project's source repository"
    ),
    evomaster_dir: str = typer.Option(
        EVOMASTER_RUNS_DIR, help="Directory `gerbil evomaster run` wrote the suites to"
    ),
    evomaster_seed: int = typer.Option(1234, help="Seed of the EvoMaster suites"),
    evomaster_max_time: str = typer.Option(
        "1h", help="Search budget of the EvoMaster suites"
    ),
    maven_seed: str = typer.Option(
        _MAVEN_SEED,
        help="Maven repository of artifacts no remote serves any more",
    ),
    part_mb: int = typer.Option(15, min=1, help=_PART_MB_HELP),
) -> None:
    """Write what execution needs beyond a checkout, to send to the machine that runs it.

    That is each measured project's test clearing and each suite's API tests, plus the
    Maven seed; the suites' patches are committed. Writes zip parts beside the directory.
    """
    destination = Path(output).resolve()
    try:
        build_bundle(
            SuiteSources(Path(outputs_root).resolve(), Path(repos_root).resolve()),
            EvoMasterRuns(
                Path(evomaster_dir).resolve(), evomaster_seed, evomaster_max_time
            ),
            Path(maven_seed).resolve(),
            destination,
        )
    except ExecutionError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    _report_archive("bundle", destination, zip_directory(destination, part_mb * 10**6))


@execution_app.command("check")
def execution_check_command(
    projects: list[str] | None = typer.Option(
        None, "--project", help="Project to check; repeatable (default: every project)"
    ),
    bundle: str = typer.Option(_BUNDLE, help=_BUNDLE_HELP),
    outputs_root: str = typer.Option("outputs", help=_OUTPUTS_ROOT_HELP),
    repos_root: str = typer.Option(
        "resources", help="Directory holding each project's source repository"
    ),
    output_dir: str | None = typer.Option(
        None,
        help="Also check that containers see this results directory, with letter case",
    ),
    min_free_gb: int = typer.Option(
        40, min=0, help="Free disk the results directory needs, in gigabytes"
    ),
) -> None:
    """Verify the bundle, the committed patches and the source repositories, and the
    results directory if given."""
    inputs = _load_inputs(bundle, outputs_root, repos_root)
    selected = (
        [_execution_project(name) for name in projects]
        if projects
        else list(MEASURED_PROJECTS.values())
    )
    typer.echo(
        f"bundle {inputs.bundle} from Gerbil {inputs.manifest.gerbil_commit}, "
        f"created {inputs.manifest.created}"
    )
    typer.echo(f"inputs {inputs.identity}")
    problems = inputs.problems(selected)
    if output_dir is not None:
        problems += _output_dir_problems(Path(output_dir).resolve(), min_free_gb)
    for problem in problems:
        typer.echo(f"error: {problem}", err=True)
    if problems:
        raise typer.Exit(code=1)
    typer.echo("inputs ready")


def _output_dir_problems(output_dir: Path, min_free_gb: int) -> list[str]:
    try:
        build_image()
        require_case_sensitive_mount(output_dir)
        require_memory_accounting()
    except ExecutionError as error:
        return [str(error)]
    free = free_gb(output_dir)
    typer.echo(f"{output_dir}: {int(free)} GB free")
    if free < min_free_gb:
        return [f"{output_dir} has {int(free)} GB free; a run needs {min_free_gb}"]
    return []


@execution_app.command("coverage")
def execution_coverage_command(
    project: str = typer.Option(..., help="Project to run, e.g. hertzbeat"),
    suites: list[Suite] | None = typer.Option(
        None, "--suite", help="Suite to run; repeatable (default: every suite)"
    ),
    bundle: str = typer.Option(_BUNDLE, help=_BUNDLE_HELP),
    outputs_root: str = typer.Option("outputs", help=_OUTPUTS_ROOT_HELP),
    repos_root: str = typer.Option(
        "resources", help="Directory holding each project's source repository"
    ),
    output_dir: str = typer.Option(
        "outputs/execution", help="Directory for workspaces and execution results"
    ),
    pause_below_gb: int = typer.Option(0, min=0, help=_PAUSE_BELOW_HELP),
) -> None:
    """Baseline-run the selected API tests and measure the passing tests' coverage."""
    execution_project = _execution_project(project)
    output_dir_path = Path(output_dir).resolve()
    inputs = _load_inputs(bundle, outputs_root, repos_root)
    try:
        failures = run_coverage(
            execution_project,
            inputs,
            output_dir_path,
            suites or list(Suite),
            pause_below_gb,
        )
    except ExecutionError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    _finish_execution_stage(execution_project, output_dir_path, failures)


@execution_app.command("mutation")
def execution_mutation_command(
    project: str = typer.Option(..., help="Project to run, e.g. hertzbeat"),
    suites: list[Suite] | None = typer.Option(
        None, "--suite", help="Suite to run; repeatable (default: every suite)"
    ),
    output_dir: str = typer.Option(
        "outputs/execution", help="Directory for workspaces and execution results"
    ),
    threads: int = typer.Option(
        4, min=1, help="PIT mutation workers of one suite's run"
    ),
    workers: int | None = typer.Option(
        None,
        min=1,
        help=(
            "PIT workers running at once, over every mutation process writing to this "
            "output directory; each may use the tests' whole heap (default: --threads)"
        ),
    ),
    timeout: int = typer.Option(
        180,
        min=1,
        help=(
            "Seconds PIT allows a test beyond its baseline time; must cover JVM and "
            "Spring context startup in each fresh PIT worker"
        ),
    ),
    sample_size: int = typer.Option(
        DEFAULT_SAMPLE_SIZE,
        min=1,
        help=(
            "Mutants sampled from the service's, the same for every suite; the default "
            "keeps a mutation score within 2 points of the full one at 95% confidence"
        ),
    ),
    sample_seed: str = typer.Option(
        DEFAULT_SAMPLE_SEED, help="Seed of the sample of mutants"
    ),
    extend_from: int | None = typer.Option(
        None,
        min=1,
        help=(
            "Sample size the suites' mutation results ran on: challenge them with the "
            "rest of the larger sample only and add those results to the kept originals"
        ),
    ),
    pause_below_gb: int = typer.Option(0, min=0, help=_PAUSE_BELOW_HELP),
) -> None:
    """Run PIT on a random sample of the service's mutants, challenging the ones each
    suite covers with its passing tests."""
    execution_project = _execution_project(project)
    output_dir_path = Path(output_dir).resolve()
    try:
        failures = run_mutation(
            execution_project,
            output_dir_path,
            suites or list(Suite),
            MutationSettings(
                pit=PitSettings(threads=threads, timeout_seconds=timeout),
                workers=workers or threads,
                sample_size=sample_size,
                sample_seed=sample_seed,
                min_free_gb=pause_below_gb,
                extend_from=extend_from,
            ),
        )
    except ExecutionError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    _finish_execution_stage(execution_project, output_dir_path, failures)


@execution_app.command("resample")
def execution_resample_command(
    project: str = typer.Option(..., help="Project to rescore, e.g. dolphinscheduler"),
    suites: list[Suite] = typer.Option(
        ..., "--suite", help="Suite to rescore; repeatable"
    ),
    from_size: int = typer.Option(
        ..., min=1, help="Sample size the suites' mutation stage ran on"
    ),
    sample_size: int = typer.Option(
        ..., min=1, help="Smaller sample size to score them on, nested in the first"
    ),
    output_dir: str = typer.Option(
        "outputs/execution", help="Directory the mutation stage wrote to"
    ),
) -> None:
    """Rescore mutation results on the first mutants of the sample they ran on, which a
    run on the smaller sample would have challenged; keeps the originals beside them."""
    execution_project = _execution_project(project)
    output_dir_path = Path(output_dir).resolve()
    try:
        rescored = resample_project(
            output_dir_path, execution_project, suites, from_size, sample_size
        )
    except ExecutionError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        f"rescored on {sample_size} mutants: "
        f"{', '.join(suite.value for suite in rescored) or 'none'}"
    )
    _finish_execution_stage(execution_project, output_dir_path, {})


@execution_app.command("results")
def execution_results_command(
    output_dir: str = typer.Option(
        "outputs/execution", help="Directory the coverage and mutation stages wrote to"
    ),
    destination: str = typer.Option(
        "outputs/execution-results",
        help="New directory for the results, or earlier results to replace",
    ),
    log_tail_kb: int = typer.Option(
        256, min=1, help="Kilobytes kept from the end of each log"
    ),
    part_mb: int = typer.Option(15, min=1, help=_PART_MB_HELP),
) -> None:
    """Gather the results and the ends of the logs to send back; writes zip parts beside them."""
    target = Path(destination).resolve()
    try:
        parts = collect_results(
            Path(output_dir).resolve(), target, log_tail_kb * 1024, part_mb * 10**6
        )
    except ExecutionError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    _report_archive("results", target, parts)


@execution_app.command("structure")
def execution_structure_command(
    suite: Suite = typer.Option(..., help="Suite whose tests to split by structure"),
    analysis_root: str = typer.Option(
        ..., help="Directory of the suite's <project>/gerbil.json analysis outputs"
    ),
    statistics_dir: str = typer.Option(
        ..., help="Directory for test_structure_<suite>.json"
    ),
    projects: list[str] = typer.Option(
        [],
        "--project",
        help="Project to include; repeatable. Default: every project with mutation "
        "results for the suite. The payload names the measured projects left out",
    ),
    output_dir: str = typer.Option(
        "outputs/execution", help="Directory the mutation stage wrote to"
    ),
) -> None:
    """Compare how often tests with and without each structural trait kill the sampled
    mutants they run, on the mutants that tests of both kinds run."""
    if len(set(projects)) != len(projects):
        raise typer.BadParameter("a project is given more than once")
    output_dir_path = Path(output_dir).resolve()
    selected = [_execution_project(name) for name in projects] or [
        project
        for project in MEASURED_PROJECTS.values()
        if SuitePaths.of(output_dir_path, project, suite).mutation.exists()
    ]
    try:
        if not selected:
            raise ExecutionError(
                f"no project has mutation results for the {suite.value} suite"
            )
        kills = [
            load_suite_kills(
                project,
                suite,
                output_dir_path,
                find_analysis(Path(analysis_root).resolve(), project),
            )
            for project in selected
        ]
    except ExecutionError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    left_out = sorted(MEASURED_PROJECTS.keys() - {project.name for project in selected})
    stem = f"test_structure_{suite.value.replace('-', '_')}"
    written = write_statistics(
        {stem: compute_test_structure(suite.value, kills, left_out=left_out)},
        Path(statistics_dir).resolve(),
    )
    typer.echo(
        json.dumps(
            {
                "suite": suite.value,
                "projects": {
                    project.project: {
                        "tests": len(project.tests),
                        "encounters": len(project.encounters),
                        "unmatched_tests": project.unmatched_tests,
                        "stopped_mutants": project.stopped_mutants,
                    }
                    for project in kills
                },
                "projects_left_out": left_out,
                "outputs": [str(path) for path in written],
            },
            indent=2,
        )
    )


evomaster_app = typer.Typer(
    help="Generate white-box EvoMaster suites for the comparison-subset services."
)
app.add_typer(evomaster_app, name="evomaster")


def _evomaster_target(name: str) -> EvoMasterTarget:
    target = TARGETS.get(name)
    if target is None:
        raise typer.BadParameter(
            f"unknown target {name!r}; choose from {', '.join(sorted(TARGETS))}"
        )
    return target


@evomaster_app.command("targets")
def evomaster_targets_command() -> None:
    """List the services EvoMaster can run against."""
    typer.echo("\n".join(sorted(TARGETS)))


@evomaster_app.command("run")
def evomaster_run_command(
    target: str = typer.Option(..., help="Service to run, e.g. hertzbeat"),
    outputs_root: str = typer.Option(
        "outputs", help="Root holding the Gerbil analyses and agent run directories"
    ),
    repos_root: str = typer.Option(
        "resources", help="Directory holding each project's source repository"
    ),
    output_dir: str = typer.Option(
        ...,
        help=(
            "Case-sensitive directory for workspaces and generated suites; a seed run "
            f"there before is replaced (the comparison's are in {EVOMASTER_RUNS_DIR})"
        ),
    ),
    max_time: str = typer.Option("1h", help="EvoMaster's search budget, e.g. 30m"),
    seed: int = typer.Option(1234, help="EvoMaster's random seed"),
    until: Stage = typer.Option(Stage.VERIFY, help="Last stage to run"),
    search_timeout: int = typer.Option(
        4 * 60 * 60,
        min=1,
        help="Seconds to wait for EvoMaster's search, and then for its suite to run",
    ),
    cpus: int | None = typer.Option(
        None, min=1, help="CPUs the search may use (default: every CPU Docker has)"
    ),
    resume_from: Stage | None = typer.Option(
        None,
        help=(
            "Rerun the recorded run from this stage, keeping the earlier stages; its "
            "max time and CPUs must be given again"
        ),
    ),
) -> None:
    """Build a service's driver at its pinned commit, then run EvoMaster through it."""
    evomaster_target = _evomaster_target(target)
    sources = SuiteSources(Path(outputs_root).resolve(), Path(repos_root).resolve())
    settings = RunSettings(
        max_time=max_time,
        seed=seed,
        until=until,
        search_timeout_seconds=search_timeout,
        cpus=cpus,
        resume_from=resume_from,
    )
    try:
        run = run_target(
            evomaster_target, sources, Path(output_dir).resolve(), settings
        )
    except ExecutionError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(run.model_dump_json(indent=2))
    if run.failed_stage is not None:
        raise typer.Exit(code=1)


@evomaster_app.command("stage")
def evomaster_stage_command(
    staged_root: str = typer.Option(
        ..., help="New directory to hold a project tree per target for batch-analysis"
    ),
    targets: list[str] | None = typer.Option(
        None, "--target", help="Service to stage; repeatable (default: every target)"
    ),
    outputs_root: str = typer.Option(
        "outputs", help="Root holding the Gerbil analyses and agent run directories"
    ),
    repos_root: str = typer.Option(
        "resources", help="Directory holding each project's source repository"
    ),
    output_dir: str = typer.Option(
        EVOMASTER_RUNS_DIR, help="Directory `gerbil evomaster run` wrote to"
    ),
    seed: int = typer.Option(1234, help="Seed of the suites to stage"),
    max_time: str | None = typer.Option(
        None, help="Stage only runs of this search budget (default: any)"
    ),
) -> None:
    """Add completed suites and their drivers to test-free trees for batch-analysis.

    Without --target, targets that did not complete are skipped and reported.
    """
    _for_each_completed_suite(
        targets,
        SuiteSources(Path(outputs_root).resolve(), Path(repos_root).resolve()),
        EvoMasterRuns(Path(output_dir).resolve(), seed, max_time),
        lambda target, sources, runs: stage_generated_suite(
            target, sources, runs, Path(staged_root).resolve()
        ),
    )


@evomaster_app.command("export")
def evomaster_export_command(
    export_root: str = typer.Option(
        "outputs/evomaster/runs", help="Directory to hold each project's saved suite"
    ),
    targets: list[str] | None = typer.Option(
        None, "--target", help="Service to export; repeatable (default: every target)"
    ),
    outputs_root: str = typer.Option(
        "outputs", help="Root holding the Gerbil analyses and agent run directories"
    ),
    repos_root: str = typer.Option(
        "resources", help="Directory holding each project's source repository"
    ),
    output_dir: str = typer.Option(
        EVOMASTER_RUNS_DIR, help="Directory `gerbil evomaster run` wrote to"
    ),
    seed: int = typer.Option(1234, help="Seed of the suites to export"),
    max_time: str | None = typer.Option(
        None, help="Export only runs of this search budget (default: any)"
    ),
) -> None:
    """Save completed suites as the agents' runs are saved: `test_patches/git_diff.patch`
    from the agents' starting point, and the run's `run.json`.

    Without --target, targets that did not complete are skipped and reported.
    """
    _for_each_completed_suite(
        targets,
        SuiteSources(Path(outputs_root).resolve(), Path(repos_root).resolve()),
        EvoMasterRuns(Path(output_dir).resolve(), seed, max_time),
        lambda target, sources, runs: export_generated_suite(
            target, sources, runs, Path(export_root).resolve()
        ),
    )


def _for_each_completed_suite(
    targets: list[str] | None,
    sources: SuiteSources,
    runs: EvoMasterRuns,
    action: Callable[[EvoMasterTarget, SuiteSources, EvoMasterRuns], Path],
) -> None:
    if targets:
        selected = [_evomaster_target(name) for name in targets]
    else:
        rows = funnel(runs.output_dir, runs.seed, runs.max_time)
        for row in rows:
            if row.status is not TargetStatus.COMPLETED:
                typer.echo(f"skipped {row.target}: {row.status.value}", err=True)
        selected = [
            TARGETS[row.target] for row in rows if row.status is TargetStatus.COMPLETED
        ]
    failed = False
    for target in selected:
        try:
            typer.echo(action(target, sources, runs))
        except ExecutionError as error:
            typer.echo(f"error: {target.name}: {error}", err=True)
            failed = True
    if failed:
        raise typer.Exit(code=1)


@evomaster_app.command("summary")
def evomaster_summary_command(
    output_dir: str = typer.Option(
        EVOMASTER_RUNS_DIR, help="Directory `gerbil evomaster run` wrote to"
    ),
    seed: int = typer.Option(1234, help="Seed to summarize"),
    max_time: str | None = typer.Option(
        None, help="Budget of the experiment; runs with another are marked"
    ),
) -> None:
    """Report how far each target got, for the paper's deployment funnel."""
    rows = funnel(Path(output_dir).resolve(), seed, max_time)
    typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))


validation_app = typer.Typer(
    help="Sample API tests, build the page annotators validate the analysis on, "
    "and score their annotations."
)
app.add_typer(validation_app, name="validation")

_VALIDATION_DIR = "outputs/validation"
_VALIDATION_DIR_HELP = "Directory `gerbil validation sample` wrote"


@validation_app.command("sample")
def validation_sample_command(
    input_root: str = typer.Option(
        ..., help="Root directory holding <project>/gerbil.json analysis outputs"
    ),
    output_dir: str = typer.Option(
        _VALIDATION_DIR, help="Directory for the sample and its tests' models"
    ),
    size: int = typer.Option(
        VALIDATION_SAMPLE_SIZE,
        min=1,
        help="Tests to draw, in the seeded order",
    ),
    per_project_cap: int = typer.Option(
        VALIDATION_PER_PROJECT_CAP, min=1, help="Most tests one project contributes"
    ),
    seed: str = typer.Option(VALIDATION_SAMPLE_SEED, help="Seed of the draw"),
    exclude_dataset: list[str] = typer.Option(
        [],
        help="Project to keep out of the sample, such as one the heuristics were "
        "tuned on (repeatable)",
    ),
    jobs: int = typer.Option(
        os.cpu_count() or 4, min=1, help="Analyses to load concurrently"
    ),
) -> None:
    """Draw the seeded sample of API tests to annotate.

    The output directory holds everything the later steps read, so the analysis
    outputs themselves do not have to travel with it.
    """
    gerbil_files = discover_gerbil_files(Path(input_root).resolve())
    if not gerbil_files:
        raise typer.BadParameter(
            f"input_root does not contain any gerbil.json outputs: {input_root}"
        )
    with _BatchProgress(
        total=len(gerbil_files), description="Loading analyses"
    ) as progress:
        candidates, model_files = load_candidates(
            gerbil_files,
            jobs=jobs,
            on_loaded=lambda path: progress.finish_ok(path.parent.name),
        )
    excluded = set(exclude_dataset)
    unknown = excluded - {candidate.dataset_name for candidate in candidates}
    if unknown:
        raise typer.BadParameter(
            f"no API tests in excluded dataset(s): {', '.join(sorted(unknown))}"
        )
    candidates = [c for c in candidates if c.dataset_name not in excluded]
    sample = draw_sample(
        candidates, size=size, per_project_cap=per_project_cap, seed=seed
    )
    payload = build_validation_sample_payload(
        candidates,
        sample,
        size=size,
        per_project_cap=per_project_cap,
        seed=seed,
        excluded_datasets=sorted(excluded),
    )
    write_validation_sample(
        payload, load_sampled_tests(sample, model_files), Path(output_dir)
    )
    typer.echo(
        json.dumps(
            {
                "universe": payload["universe"],
                "sample": payload["sample"],
                "files": [SAMPLE_FILE, MODELS_FILE],
            },
            indent=2,
        )
    )
    if len(sample) < size:
        typer.echo(
            f"error: drew {len(sample)} of {size} tests; the corpus cannot fill the "
            "sample under this cap",
            err=True,
        )
        raise typer.Exit(code=1)


@validation_app.command("page")
def validation_page_command(
    repos_root: str = typer.Option(
        ...,
        help="Directory holding each sampled project's checkout, under its "
        "directory name",
    ),
    sample_dir: str = typer.Option(_VALIDATION_DIR, help=_VALIDATION_DIR_HELP),
) -> None:
    """Build the annotation page for a sample, from the projects' source checkouts.

    Writes annotate.html, the one file annotators need, and bundle.json, which
    scoring reads, into the sample directory.
    """
    sample_path = Path(sample_dir)
    payload, tests, models = read_sample(sample_path)
    try:
        bundle = build_validation_bundle(
            payload, tests, models, Path(repos_root).resolve()
        )
    except BundleError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    write_validation_bundle(bundle, sample_path)
    write_page(bundle, sample_path / PAGE_FILE)
    items = [item for test in bundle["tests"] for item in test["items"]]
    typer.echo(
        json.dumps(
            {
                "tests": len(bundle["tests"]),
                "items": len(items),
                "items_without_source_position": sum(
                    item["token"] is None for item in items
                ),
                "tests_with_missing_helper_source": sum(
                    bool(test["missing_units"]) for test in bundle["tests"]
                ),
                "bundle_digest": bundle["bundle_digest"],
                "files": [PAGE_FILE, BUNDLE_FILE],
            },
            indent=2,
        )
    )


@validation_app.command("score")
def validation_score_command(
    sample_dir: str = typer.Option(_VALIDATION_DIR, help=_VALIDATION_DIR_HELP),
    annotations: str | None = typer.Option(
        None,
        help="Directory holding each annotator's exported file; defaults to "
        f"<sample-dir>/{ANNOTATIONS_DIRECTORY}",
    ),
    bootstrap_samples: int = typer.Option(
        DEFAULT_BOOTSTRAP_SAMPLES,
        min=0,
        help="Resamples of the projects behind the 95% intervals",
    ),
    remove_duplicates: bool = typer.Option(
        False,
        "--remove-duplicates/--keep-duplicates",
        help="Score a call site that runs several times, and the items annotators "
        "added on it, once: as of the first step it runs in. Whether a test is an "
        "API test is read from every run",
    ),
) -> None:
    """Score the annotators' exports against the bundle they annotated.

    Writes scores.json into the sample directory: precision and recall of the
    extracted items, accuracy of what Gerbil says about them, and agreement.
    """
    sample_path = Path(sample_dir)
    annotations_path = (
        sample_path / ANNOTATIONS_DIRECTORY
        if annotations is None
        else Path(annotations)
    )
    bundle = read_validation_bundle(sample_path)
    try:
        scores = score_annotations(
            bundle,
            read_sample_payload(sample_path),
            read_annotations(sorted(annotations_path.glob("*.json")), bundle),
            bootstrap_samples=bootstrap_samples,
            remove_duplicates=remove_duplicates,
        )
    except ScoringError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error
    write_scores(scores, sample_path)
    typer.echo(
        json.dumps(
            {
                "annotators": scores["annotators"],
                "tests": scores["tests"],
                "duplicates": scores["duplicates"],
                "api_test_precision": scores["overall"]["api_test"]["precision"],
                "extraction": {
                    scope: {
                        rate: result[rate] for rate in ("precision", "recall", "ci95")
                    }
                    for scope, result in scores["overall"]["extraction"].items()
                },
                "file": SCORES_FILE,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
