from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

import pytest
import typer
from rich.console import Console
from rich.logging import RichHandler
from typer.testing import CliRunner

from gerbil import cli


def test_analysis_command_rejects_missing_project_path(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "analysis",
            "--project-path",
            str(tmp_path / "missing"),
            "--analysis-path",
            str(tmp_path / "cache"),
            "--output-path",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code != 0
    assert "project_path does not exist" in result.stderr


def test_analysis_command_rejects_non_directory_project_path(tmp_path: Path) -> None:
    project_file = tmp_path / "project.txt"
    project_file.write_text("not-a-directory", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "analysis",
            "--project-path",
            str(project_file),
            "--analysis-path",
            str(tmp_path / "cache"),
            "--output-path",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code != 0
    assert "project_path is not a directory" in result.stderr


def test_parse_test_dirs_rejects_whitespace_only_values() -> None:
    with pytest.raises(
        typer.BadParameter,
        match="test_dirs must contain at least one comma-separated path pattern",
    ):
        cli._parse_test_dirs(" , , ")


def test_analysis_command_hashes_output_and_analysis_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "service"
    project_root.mkdir()
    analysis_root = tmp_path / "cache"
    output_root = tmp_path / "output"

    cldk_call_args: dict[str, object] = {}
    project_info_args: dict[str, object] = {}

    class _FakeProjectAnalysis:
        def model_dump_json(self, **_: object) -> str:
            return "{}"

    class _FakeProjectAnalysisInfo:
        def __init__(self, **kwargs: object) -> None:
            project_info_args.update(kwargs)

        def gather_project_analysis_info(self) -> _FakeProjectAnalysis:
            return _FakeProjectAnalysis()

    class _FakeCLDK:
        def __init__(self, *, language: str) -> None:
            assert language == "java"

        def analysis(self, **kwargs: object) -> object:
            cldk_call_args.update(kwargs)
            return object()

    monkeypatch.setattr(cli, "CLDK", _FakeCLDK)
    monkeypatch.setattr(cli, "ProjectAnalysisInfo", _FakeProjectAnalysisInfo)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "analysis",
            "--project-path",
            str(project_root),
            "--analysis-path",
            str(analysis_root),
            "--output-path",
            str(output_root),
        ],
    )

    assert result.exit_code == 0

    expected_hash = hashlib.sha256(
        str(project_root.resolve()).encode("utf-8")
    ).hexdigest()[:8]
    expected_bucket = f"{project_root.name}-{expected_hash}"
    expected_output = output_root.resolve() / expected_bucket / "gerbil.json"

    assert cldk_call_args["analysis_json_path"] == str(
        analysis_root.resolve() / expected_bucket
    )
    assert project_info_args["dataset_name"] == expected_bucket
    assert expected_output.read_text(encoding="utf-8") == "{}"


def test_analysis_command_uses_resolved_project_path_for_bucket_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_project_root = tmp_path / "workspace" / "service"
    real_project_root.mkdir(parents=True)
    analysis_root = tmp_path / "cache"
    output_root = tmp_path / "output"

    project_path_with_parent_segment = real_project_root / ".." / "service"

    cldk_call_args: dict[str, object] = {}
    project_info_args: dict[str, object] = {}

    class _FakeProjectAnalysis:
        def model_dump_json(self, **_: object) -> str:
            return "{}"

    class _FakeProjectAnalysisInfo:
        def __init__(self, **kwargs: object) -> None:
            project_info_args.update(kwargs)

        def gather_project_analysis_info(self) -> _FakeProjectAnalysis:
            return _FakeProjectAnalysis()

    class _FakeCLDK:
        def __init__(self, *, language: str) -> None:
            assert language == "java"

        def analysis(self, **kwargs: object) -> object:
            cldk_call_args.update(kwargs)
            return object()

    monkeypatch.setattr(cli, "CLDK", _FakeCLDK)
    monkeypatch.setattr(cli, "ProjectAnalysisInfo", _FakeProjectAnalysisInfo)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "analysis",
            "--project-path",
            str(project_path_with_parent_segment),
            "--analysis-path",
            str(analysis_root),
            "--output-path",
            str(output_root),
        ],
    )

    assert result.exit_code == 0

    expected_hash = hashlib.sha256(
        str(real_project_root.resolve()).encode("utf-8")
    ).hexdigest()[:8]
    expected_bucket = f"{real_project_root.name}-{expected_hash}"

    assert cldk_call_args["analysis_json_path"] == str(
        analysis_root.resolve() / expected_bucket
    )
    assert project_info_args["dataset_name"] == expected_bucket


def test_analyze_project_creates_output_and_analysis_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "service"
    project_root.mkdir()

    analysis_dir = tmp_path / "nested" / "cache" / "service"
    output_dir = tmp_path / "nested" / "output" / "service"
    dump_kwargs: dict[str, object] = {}

    class _FakeProjectAnalysis:
        def model_dump_json(self, **kwargs: object) -> str:
            dump_kwargs.update(kwargs)
            return "{}"

    class _FakeProjectAnalysisInfo:
        def __init__(self, **_: object) -> None:
            return

        def gather_project_analysis_info(self) -> _FakeProjectAnalysis:
            return _FakeProjectAnalysis()

    class _FakeCLDK:
        def __init__(self, *, language: str) -> None:
            assert language == "java"

        def analysis(self, **_: object) -> object:
            return object()

    monkeypatch.setattr(cli, "CLDK", _FakeCLDK)
    monkeypatch.setattr(cli, "ProjectAnalysisInfo", _FakeProjectAnalysisInfo)

    output_file = cli._analyze_project(
        project_root=project_root,
        dataset_name=project_root.name,
        analysis_dir=analysis_dir,
        output_dir=output_dir,
        analysis_backend_path=None,
        eager=False,
        expanded_helper_depth=1,
        test_dirs=("src/test/java",),
    )

    assert analysis_dir.is_dir()
    assert output_dir.is_dir()
    assert output_file == output_dir / "gerbil.json"
    assert output_file.read_text(encoding="utf-8") == "{}"
    assert dump_kwargs == {"indent": 2}


def test_analyze_project_propagates_cldk_analysis_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "service"
    project_root.mkdir()

    class _FakeCLDK:
        def __init__(self, *, language: str) -> None:
            assert language == "java"

        def analysis(self, **_: object) -> object:
            raise RuntimeError("simulated CLDK failure")

    monkeypatch.setattr(cli, "CLDK", _FakeCLDK)

    with pytest.raises(RuntimeError, match="simulated CLDK failure"):
        cli._analyze_project(
            project_root=project_root,
            dataset_name=project_root.name,
            analysis_dir=tmp_path / "cache" / "service",
            output_dir=tmp_path / "output" / "service",
            analysis_backend_path=None,
            eager=False,
            expanded_helper_depth=1,
            test_dirs=("src/test/java",),
        )


def test_batch_analysis_reports_failures_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    for project_name in ("ok-a", "fail", "ok-b"):
        (input_root / project_name).mkdir(parents=True)
    output_root = tmp_path / "output"

    def fake_analyze_project(
        project_root: Path,
        dataset_name: str,
        analysis_dir: Path,
        output_dir: Path,
        analysis_backend_path: str | None,
        eager: bool,
        expanded_helper_depth: int,
        test_dirs: tuple[str, ...],
    ) -> Path:
        del (
            project_root,
            analysis_dir,
            analysis_backend_path,
            eager,
            expanded_helper_depth,
            test_dirs,
        )
        if dataset_name == "fail":
            raise RuntimeError("simulated failure")
        return output_dir / "gerbil.json"

    monkeypatch.setattr(cli, "_analyze_project", fake_analyze_project)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(input_root),
            "--analysis-root",
            str(tmp_path / "cache"),
            "--output-root",
            str(output_root),
            "--jobs",
            "1",
        ],
    )

    failed_project = input_root.resolve() / "fail"

    assert result.exit_code == 1
    assert "[1/3] failed: fail: RuntimeError: simulated failure" in result.stderr
    assert "[2/3] ok: ok-a (" in result.stderr
    assert "[3/3] ok: ok-b (" in result.stderr
    assert "failed: fail: RuntimeError: simulated failure" not in result.stdout

    summary = json.loads(result.stdout)

    assert summary["projects"] == 3
    assert summary["succeeded"] == 2
    assert summary["failed"] == 1
    assert summary["outputs"] == [
        str(output_root.resolve() / "ok-a" / "gerbil.json"),
        str(output_root.resolve() / "ok-b" / "gerbil.json"),
    ]
    assert summary["failures"] == [
        {
            "project_path": str(failed_project),
            "error": "RuntimeError: simulated failure",
        }
    ]


def test_batch_progress_renders_live_bar_on_terminal() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, width=80)

    with cli._BatchProgress(total=2, console=console) as progress:
        progress.finish_ok("service-a", 12.3)
        progress.finish_failed("service-b", "boom")

    output = buffer.getvalue()
    assert "Analyzing projects" in output
    assert "2/2" in output
    assert "failed: service-b: boom" in output
    assert "[1/2] ok:" not in output


def test_batch_progress_prints_counter_lines_when_not_a_terminal() -> None:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=80)
    long_error = "request failed: " + "x" * 120

    with cli._BatchProgress(total=3, console=console) as progress:
        progress.finish_ok("service-a", 1.0)
        progress.finish_failed("service-b", "boom [Errno 2]")
        progress.finish_failed("service-c", long_error)

    lines = buffer.getvalue().splitlines()
    assert "[1/3] ok: service-a (1.0s)" in lines
    # Errors render literally (no rich markup) and stay on one line past console width.
    assert "[2/3] failed: service-b: boom [Errno 2]" in lines
    assert f"[3/3] failed: service-c: {long_error}" in lines
    assert "Analyzing projects" not in buffer.getvalue()


def test_batch_progress_reports_skipped_projects() -> None:
    terminal_buffer = io.StringIO()
    terminal_console = Console(file=terminal_buffer, force_terminal=True, width=80)
    with cli._BatchProgress(total=2, console=terminal_console) as progress:
        progress.finish_skipped("service-a", "no analysis.json")
        progress.finish_ok("service-b", 1.0)
    terminal_output = terminal_buffer.getvalue()
    assert "skip: service-a (no analysis.json)" in terminal_output
    assert "2/2" in terminal_output

    plain_buffer = io.StringIO()
    plain_console = Console(file=plain_buffer, force_terminal=False, width=80)
    with cli._BatchProgress(total=2, console=plain_console) as progress:
        progress.finish_skipped("service-a", "analysis.json exists")
        progress.finish_ok("service-b", 1.0)
    plain_lines = plain_buffer.getvalue().splitlines()
    assert "[1/2] skip: service-a (analysis.json exists)" in plain_lines
    assert "[2/2] ok: service-b (1.0s)" in plain_lines


def _spawnable_fake_analyze_project(
    project_root: Path,
    dataset_name: str,
    analysis_dir: Path,
    output_dir: Path,
    analysis_backend_path: str | None,
    eager: bool,
    expanded_helper_depth: int,
    test_dirs: tuple[str, ...],
) -> Path:
    del (
        project_root,
        analysis_dir,
        analysis_backend_path,
        eager,
        expanded_helper_depth,
        test_dirs,
    )
    if dataset_name == "fail":
        raise RuntimeError("simulated failure")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "gerbil.json"
    output_file.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    return output_file


def test_batch_analysis_parallel_runs_in_worker_processes_and_keeps_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    for project_name in ("ok-a", "fail", "ok-b"):
        (input_root / project_name).mkdir(parents=True)
    output_root = tmp_path / "output"

    monkeypatch.setattr(cli, "_analyze_project", _spawnable_fake_analyze_project)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(input_root),
            "--analysis-root",
            str(tmp_path / "cache"),
            "--output-root",
            str(output_root),
            "--jobs",
            "2",
        ],
    )

    failed_project = input_root.resolve() / "fail"

    assert result.exit_code == 1
    # Completion order is nondeterministic across workers, so skip the counter prefix.
    assert "failed: fail: RuntimeError: simulated failure" in result.stderr

    summary = json.loads(result.stdout)
    assert summary["projects"] == 3
    assert summary["succeeded"] == 2
    assert summary["failed"] == 1
    assert summary["outputs"] == [
        str(output_root.resolve() / "ok-a" / "gerbil.json"),
        str(output_root.resolve() / "ok-b" / "gerbil.json"),
    ]
    assert summary["failures"] == [
        {
            "project_path": str(failed_project),
            "error": "RuntimeError: simulated failure",
        }
    ]

    worker_pids = {
        json.loads(
            (output_root.resolve() / name / "gerbil.json").read_text(encoding="utf-8")
        )["pid"]
        for name in ("ok-a", "ok-b")
    }
    assert os.getpid() not in worker_pids
    assert len(worker_pids) == 2


def test_batch_analysis_skips_projects_without_cached_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    for project_name in ("has-cache", "no-cache"):
        (input_root / project_name).mkdir(parents=True)
    analysis_root = tmp_path / "cache"
    output_root = tmp_path / "output"

    cached_analysis = analysis_root / "has-cache" / "analysis.json"
    cached_analysis.parent.mkdir(parents=True)
    cached_analysis.write_text("{}", encoding="utf-8")

    analyzed_projects: list[str] = []

    def fake_analyze_project(
        project_root: Path,
        dataset_name: str,
        analysis_dir: Path,
        output_dir: Path,
        analysis_backend_path: str | None,
        eager: bool,
        expanded_helper_depth: int,
        test_dirs: tuple[str, ...],
    ) -> Path:
        del (
            project_root,
            analysis_dir,
            analysis_backend_path,
            eager,
            expanded_helper_depth,
            test_dirs,
        )
        analyzed_projects.append(dataset_name)
        return output_dir / "gerbil.json"

    monkeypatch.setattr(cli, "_analyze_project", fake_analyze_project)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(input_root),
            "--analysis-root",
            str(analysis_root),
            "--output-root",
            str(output_root),
            "--jobs",
            "1",
            "--skip-missing-analysis",
        ],
    )

    assert result.exit_code == 0
    assert analyzed_projects == ["has-cache"]
    assert "[1/2] skip: no-cache (no analysis.json)" in result.stderr
    assert "[2/2] ok: has-cache (" in result.stderr

    summary = json.loads(result.stdout)
    assert summary["projects"] == 2
    assert summary["succeeded"] == 1
    assert summary["skipped"] == 1
    assert summary["failed"] == 0
    assert summary["outputs"] == [
        str(output_root.resolve() / "has-cache" / "gerbil.json")
    ]
    assert summary["skips"] == [str(input_root.resolve() / "no-cache")]
    assert summary["failures"] == []


def test_batch_analysis_skips_all_projects_without_spawning_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    for project_name in ("repo-a", "repo-b"):
        (input_root / project_name).mkdir(parents=True)

    def fail_if_called(**_: object) -> Path:
        raise AssertionError("_analyze_project should not run when all are skipped")

    def fail_if_pool_created(**_: object) -> object:
        raise AssertionError(
            "the worker pool should not be created when all are skipped"
        )

    monkeypatch.setattr(cli, "_analyze_project", fail_if_called)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", fail_if_pool_created)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(input_root),
            "--analysis-root",
            str(tmp_path / "cache"),
            "--output-root",
            str(tmp_path / "output"),
            "--jobs",
            "4",
            "--skip-missing-analysis",
        ],
    )

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert summary["succeeded"] == 0
    assert summary["skipped"] == 2
    assert summary["outputs"] == []
    assert summary["skips"] == [
        str(input_root.resolve() / "repo-a"),
        str(input_root.resolve() / "repo-b"),
    ]


def test_batch_analysis_without_skip_flag_analyzes_missing_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    for project_name in ("repo-a", "repo-b"):
        (input_root / project_name).mkdir(parents=True)

    analyzed_projects: list[str] = []

    def fake_analyze_project(
        project_root: Path,
        dataset_name: str,
        analysis_dir: Path,
        output_dir: Path,
        analysis_backend_path: str | None,
        eager: bool,
        expanded_helper_depth: int,
        test_dirs: tuple[str, ...],
    ) -> Path:
        del (
            project_root,
            analysis_dir,
            analysis_backend_path,
            eager,
            expanded_helper_depth,
            test_dirs,
        )
        analyzed_projects.append(dataset_name)
        return output_dir / "gerbil.json"

    monkeypatch.setattr(cli, "_analyze_project", fake_analyze_project)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(input_root),
            "--analysis-root",
            str(tmp_path / "cache"),
            "--output-root",
            str(tmp_path / "output"),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert analyzed_projects == ["repo-a", "repo-b"]
    summary = json.loads(result.stdout)
    assert summary["skipped"] == 0
    assert summary["skips"] == []


def test_batch_analysis_rejects_non_positive_jobs(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    (input_root / "repo-a").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(input_root),
            "--analysis-root",
            str(tmp_path / "cache"),
            "--output-root",
            str(tmp_path / "output"),
            "--jobs",
            "0",
        ],
    )

    assert result.exit_code != 0
    assert "--jobs" in result.stderr


def test_batch_analysis_defaults_to_two_jobs_and_depth_ten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    (input_root / "repo-a").mkdir(parents=True)

    observed_depths: list[int] = []
    observed_max_workers: list[int] = []

    def fake_analyze_project(
        project_root: Path,
        dataset_name: str,
        analysis_dir: Path,
        output_dir: Path,
        analysis_backend_path: str | None,
        eager: bool,
        expanded_helper_depth: int,
        test_dirs: tuple[str, ...],
    ) -> Path:
        del (
            project_root,
            dataset_name,
            analysis_dir,
            analysis_backend_path,
            eager,
            test_dirs,
        )
        observed_depths.append(expanded_helper_depth)
        return output_dir / "gerbil.json"

    class _ImmediateFuture:
        def __init__(self, fn: Callable[[], tuple[Path, float]]) -> None:
            self._fn = fn

        def result(self) -> tuple[Path, float]:
            return self._fn()

    class _FakePool:
        def __init__(
            self, *, max_workers: int, mp_context: object, max_tasks_per_child: int
        ) -> None:
            del mp_context, max_tasks_per_child
            observed_max_workers.append(max_workers)

        def __enter__(self) -> "_FakePool":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def submit(self, fn: Callable[[], tuple[Path, float]]) -> _ImmediateFuture:
            return _ImmediateFuture(fn)

    monkeypatch.setattr(cli, "_analyze_project", fake_analyze_project)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", _FakePool)
    monkeypatch.setattr(cli, "as_completed", lambda futures: list(futures))

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(input_root),
            "--analysis-root",
            str(tmp_path / "cache"),
            "--output-root",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code == 0
    assert observed_max_workers == [2]
    assert observed_depths == [10]


def test_batch_analysis_analyzes_projects_by_name_in_sorted_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    for project_name in ("service-b", "service-a"):
        (input_root / project_name).mkdir(parents=True)
    analysis_root = tmp_path / "cache"
    output_root = tmp_path / "output"

    observed_calls: list[dict[str, object]] = []

    def fake_analyze_project(
        project_root: Path,
        dataset_name: str,
        analysis_dir: Path,
        output_dir: Path,
        analysis_backend_path: str | None,
        eager: bool,
        expanded_helper_depth: int,
        test_dirs: tuple[str, ...],
    ) -> Path:
        del analysis_backend_path, eager, expanded_helper_depth, test_dirs
        observed_calls.append(
            {
                "project_root": project_root,
                "dataset_name": dataset_name,
                "analysis_dir": analysis_dir,
                "output_dir": output_dir,
            }
        )
        return output_dir / "gerbil.json"

    monkeypatch.setattr(cli, "_analyze_project", fake_analyze_project)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(input_root),
            "--analysis-root",
            str(analysis_root),
            "--output-root",
            str(output_root),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert "[1/2] ok: service-a (" in result.stderr
    assert "[2/2] ok: service-b (" in result.stderr
    assert observed_calls == [
        {
            "project_root": input_root.resolve() / "service-a",
            "dataset_name": "service-a",
            "analysis_dir": analysis_root.resolve() / "service-a",
            "output_dir": output_root.resolve() / "service-a",
        },
        {
            "project_root": input_root.resolve() / "service-b",
            "dataset_name": "service-b",
            "analysis_dir": analysis_root.resolve() / "service-b",
            "output_dir": output_root.resolve() / "service-b",
        },
    ]

    summary = json.loads(result.stdout)
    assert summary["projects"] == 2
    assert summary["succeeded"] == 2
    assert summary["failed"] == 0
    assert summary["outputs"] == [
        str(output_root.resolve() / "service-a" / "gerbil.json"),
        str(output_root.resolve() / "service-b" / "gerbil.json"),
    ]
    assert summary["failures"] == []


def test_batch_analysis_skips_hidden_directories_and_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    (input_root / "repo-a").mkdir(parents=True)
    (input_root / ".hidden").mkdir()
    (input_root / "notes.txt").write_text("not a repo", encoding="utf-8")

    observed_project_names: list[str] = []

    def fake_analyze_project(
        project_root: Path,
        dataset_name: str,
        analysis_dir: Path,
        output_dir: Path,
        analysis_backend_path: str | None,
        eager: bool,
        expanded_helper_depth: int,
        test_dirs: tuple[str, ...],
    ) -> Path:
        del (
            project_root,
            analysis_dir,
            analysis_backend_path,
            eager,
            expanded_helper_depth,
            test_dirs,
        )
        observed_project_names.append(dataset_name)
        return output_dir / "gerbil.json"

    monkeypatch.setattr(cli, "_analyze_project", fake_analyze_project)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(input_root),
            "--analysis-root",
            str(tmp_path / "cache"),
            "--output-root",
            str(tmp_path / "output"),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert observed_project_names == ["repo-a"]

    summary = json.loads(result.stdout)
    assert summary["projects"] == 1
    assert summary["succeeded"] == 1


def test_batch_analysis_rejects_missing_input_root(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(tmp_path / "missing"),
            "--analysis-root",
            str(tmp_path / "cache"),
            "--output-root",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code != 0
    assert "input_root does not exist" in result.stderr


def test_batch_analysis_rejects_non_directory_input_root(tmp_path: Path) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text("not-a-directory", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(input_file),
            "--analysis-root",
            str(tmp_path / "cache"),
            "--output-root",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code != 0
    assert "input_root is not a directory" in result.stderr


def test_batch_analysis_rejects_input_root_without_project_directories(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    (input_root / ".hidden").mkdir()
    (input_root / "notes.txt").write_text("not a repo", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-analysis",
            "--input-root",
            str(input_root),
            "--analysis-root",
            str(tmp_path / "cache"),
            "--output-root",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code != 0
    assert "input_root does not contain any project directories" in result.stderr


def test_generate_cldk_cache_runs_cldk_eagerly_and_creates_analysis_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "service"
    project_root.mkdir()
    analysis_dir = tmp_path / "nested" / "cache" / "service"

    cldk_call_args: dict[str, object] = {}

    class _FakeCLDK:
        def __init__(self, *, language: str) -> None:
            assert language == "java"

        def analysis(self, **kwargs: object) -> object:
            cldk_call_args.update(kwargs)
            return object()

    monkeypatch.setattr(cli, "CLDK", _FakeCLDK)

    cache_file = cli._generate_cldk_cache(
        project_root=project_root,
        analysis_dir=analysis_dir,
        analysis_backend_path=None,
    )

    assert analysis_dir.is_dir()
    assert cache_file == analysis_dir / "analysis.json"
    assert cldk_call_args["project_path"] == str(project_root)
    assert cldk_call_args["analysis_json_path"] == str(analysis_dir)
    assert cldk_call_args["eager"] is True


def test_batch_cldk_cache_reports_failures_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    for project_name in ("ok-a", "fail", "ok-b"):
        (input_root / project_name).mkdir(parents=True)
    output_root = tmp_path / "cache"

    def fake_generate_cldk_cache(
        project_root: Path,
        analysis_dir: Path,
        analysis_backend_path: str | None,
    ) -> Path:
        del analysis_backend_path
        if project_root.name == "fail":
            raise RuntimeError("simulated failure")
        return analysis_dir / "analysis.json"

    monkeypatch.setattr(cli, "_generate_cldk_cache", fake_generate_cldk_cache)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-cldk-cache",
            "--input-root",
            str(input_root),
            "--output-root",
            str(output_root),
            "--jobs",
            "1",
        ],
    )

    failed_project = input_root.resolve() / "fail"

    assert result.exit_code == 1
    assert "[1/3] failed: fail: RuntimeError: simulated failure" in result.stderr
    assert "[2/3] ok: ok-a (" in result.stderr
    assert "[3/3] ok: ok-b (" in result.stderr

    summary = json.loads(result.stdout)
    assert summary["projects"] == 3
    assert summary["succeeded"] == 2
    assert summary["failed"] == 1
    assert summary["outputs"] == [
        str(output_root.resolve() / "ok-a" / "analysis.json"),
        str(output_root.resolve() / "ok-b" / "analysis.json"),
    ]
    assert summary["failures"] == [
        {
            "project_path": str(failed_project),
            "error": "RuntimeError: simulated failure",
        }
    ]


def test_batch_cldk_cache_skip_existing_skips_only_projects_with_cache_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    for project_name in ("has-cache", "empty-dir", "no-dir"):
        (input_root / project_name).mkdir(parents=True)
    output_root = tmp_path / "cache"

    cached_analysis = output_root / "has-cache" / "analysis.json"
    cached_analysis.parent.mkdir(parents=True)
    cached_analysis.write_text("{}", encoding="utf-8")
    # An output dir without analysis.json must not count as cached.
    (output_root / "empty-dir").mkdir()

    generated_projects: list[str] = []

    def fake_generate_cldk_cache(
        project_root: Path,
        analysis_dir: Path,
        analysis_backend_path: str | None,
    ) -> Path:
        del analysis_backend_path
        generated_projects.append(project_root.name)
        return analysis_dir / "analysis.json"

    monkeypatch.setattr(cli, "_generate_cldk_cache", fake_generate_cldk_cache)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-cldk-cache",
            "--input-root",
            str(input_root),
            "--output-root",
            str(output_root),
            "--jobs",
            "1",
            "--skip-existing",
        ],
    )

    assert result.exit_code == 0
    assert generated_projects == ["empty-dir", "no-dir"]
    assert "[1/3] skip: has-cache (analysis.json exists)" in result.stderr

    summary = json.loads(result.stdout)
    assert summary["projects"] == 3
    assert summary["succeeded"] == 2
    assert summary["skipped"] == 1
    assert summary["failed"] == 0
    assert summary["skips"] == [str(input_root.resolve() / "has-cache")]


def test_batch_cldk_cache_without_skip_flag_regenerates_existing_caches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    for project_name in ("repo-a", "repo-b"):
        (input_root / project_name).mkdir(parents=True)
    output_root = tmp_path / "cache"

    cached_analysis = output_root / "repo-a" / "analysis.json"
    cached_analysis.parent.mkdir(parents=True)
    cached_analysis.write_text("{}", encoding="utf-8")

    generated_projects: list[str] = []

    def fake_generate_cldk_cache(
        project_root: Path,
        analysis_dir: Path,
        analysis_backend_path: str | None,
    ) -> Path:
        del analysis_backend_path
        generated_projects.append(project_root.name)
        return analysis_dir / "analysis.json"

    monkeypatch.setattr(cli, "_generate_cldk_cache", fake_generate_cldk_cache)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-cldk-cache",
            "--input-root",
            str(input_root),
            "--output-root",
            str(output_root),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert generated_projects == ["repo-a", "repo-b"]
    summary = json.loads(result.stdout)
    assert summary["skipped"] == 0
    assert summary["skips"] == []


def test_batch_cldk_cache_skips_all_projects_without_spawning_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "cache"
    for project_name in ("repo-a", "repo-b"):
        (input_root / project_name).mkdir(parents=True)
        cached_analysis = output_root / project_name / "analysis.json"
        cached_analysis.parent.mkdir(parents=True)
        cached_analysis.write_text("{}", encoding="utf-8")

    def fail_if_called(**_: object) -> Path:
        raise AssertionError("_generate_cldk_cache should not run when all are skipped")

    def fail_if_pool_created(**_: object) -> object:
        raise AssertionError(
            "the worker pool should not be created when all are skipped"
        )

    monkeypatch.setattr(cli, "_generate_cldk_cache", fail_if_called)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", fail_if_pool_created)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-cldk-cache",
            "--input-root",
            str(input_root),
            "--output-root",
            str(output_root),
            "--jobs",
            "4",
            "--skip-existing",
        ],
    )

    assert result.exit_code == 0
    summary = json.loads(result.stdout)
    assert summary["succeeded"] == 0
    assert summary["skipped"] == 2
    assert summary["outputs"] == []
    assert summary["skips"] == [
        str(input_root.resolve() / "repo-a"),
        str(input_root.resolve() / "repo-b"),
    ]


def _spawnable_fake_generate_cldk_cache(
    project_root: Path,
    analysis_dir: Path,
    analysis_backend_path: str | None,
) -> Path:
    del analysis_backend_path
    if project_root.name == "fail":
        raise RuntimeError("simulated failure")
    analysis_dir.mkdir(parents=True, exist_ok=True)
    cache_file = analysis_dir / "analysis.json"
    cache_file.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    return cache_file


def test_batch_cldk_cache_parallel_runs_in_worker_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    for project_name in ("ok-a", "fail", "ok-b"):
        (input_root / project_name).mkdir(parents=True)
    output_root = tmp_path / "cache"

    monkeypatch.setattr(
        cli, "_generate_cldk_cache", _spawnable_fake_generate_cldk_cache
    )

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-cldk-cache",
            "--input-root",
            str(input_root),
            "--output-root",
            str(output_root),
            "--jobs",
            "2",
        ],
    )

    failed_project = input_root.resolve() / "fail"

    assert result.exit_code == 1
    # Completion order is nondeterministic across workers, so skip the counter prefix.
    assert "failed: fail: RuntimeError: simulated failure" in result.stderr

    summary = json.loads(result.stdout)
    assert summary["projects"] == 3
    assert summary["succeeded"] == 2
    assert summary["failed"] == 1
    assert summary["outputs"] == [
        str(output_root.resolve() / "ok-a" / "analysis.json"),
        str(output_root.resolve() / "ok-b" / "analysis.json"),
    ]
    assert summary["failures"] == [
        {
            "project_path": str(failed_project),
            "error": "RuntimeError: simulated failure",
        }
    ]

    worker_pids = {
        json.loads(
            (output_root.resolve() / name / "analysis.json").read_text(encoding="utf-8")
        )["pid"]
        for name in ("ok-a", "ok-b")
    }
    assert os.getpid() not in worker_pids
    assert len(worker_pids) == 2


def test_batch_cldk_cache_defaults_to_two_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_root = tmp_path / "input"
    (input_root / "repo-a").mkdir(parents=True)

    def fake_generate_cldk_cache(
        project_root: Path,
        analysis_dir: Path,
        analysis_backend_path: str | None,
    ) -> Path:
        del project_root, analysis_backend_path
        return analysis_dir / "analysis.json"

    observed_max_workers: list[int] = []

    class _ImmediateFuture:
        def __init__(self, fn: Callable[[], tuple[Path, float]]) -> None:
            self._fn = fn

        def result(self) -> tuple[Path, float]:
            return self._fn()

    class _FakePool:
        def __init__(
            self, *, max_workers: int, mp_context: object, max_tasks_per_child: int
        ) -> None:
            del mp_context, max_tasks_per_child
            observed_max_workers.append(max_workers)

        def __enter__(self) -> "_FakePool":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def submit(self, fn: Callable[[], tuple[Path, float]]) -> _ImmediateFuture:
            return _ImmediateFuture(fn)

    monkeypatch.setattr(cli, "_generate_cldk_cache", fake_generate_cldk_cache)
    monkeypatch.setattr(cli, "ProcessPoolExecutor", _FakePool)
    monkeypatch.setattr(cli, "as_completed", lambda futures: list(futures))

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-cldk-cache",
            "--input-root",
            str(input_root),
            "--output-root",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0
    assert observed_max_workers == [2]


def test_batch_cldk_cache_rejects_missing_input_root(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-cldk-cache",
            "--input-root",
            str(tmp_path / "missing"),
            "--output-root",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code != 0
    assert "input_root does not exist" in result.stderr


def test_batch_cldk_cache_rejects_non_positive_jobs(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    (input_root / "repo-a").mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "batch-cldk-cache",
            "--input-root",
            str(input_root),
            "--output-root",
            str(tmp_path / "cache"),
            "--jobs",
            "0",
        ],
    )

    assert result.exit_code != 0
    assert "--jobs" in result.stderr


def test_analysis_command_rejects_negative_expanded_helper_depth(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "service"
    project_root.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "analysis",
            "--project-path",
            str(project_root),
            "--analysis-path",
            str(tmp_path / "cache"),
            "--output-path",
            str(tmp_path / "output"),
            "--expanded-helper-depth",
            "-1",
        ],
    )

    assert result.exit_code != 0
    assert "expanded-helper-depth" in f"{result.stdout}{result.stderr}"


def test_analysis_command_rejects_empty_test_dirs_value(tmp_path: Path) -> None:
    project_root = tmp_path / "service"
    project_root.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "analysis",
            "--project-path",
            str(project_root),
            "--analysis-path",
            str(tmp_path / "cache"),
            "--output-path",
            str(tmp_path / "output"),
            "--test-dirs",
            " , , ",
        ],
    )

    assert result.exit_code != 0
    normalized_stderr = " ".join(result.stderr.replace("│", " ").split())
    assert (
        "test_dirs must contain at least one comma-separated path pattern"
        in normalized_stderr
    )


def test_analysis_command_parses_test_dirs_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "service"
    project_root.mkdir()

    observed_test_dirs: list[tuple[str, ...]] = []

    def fake_analyze_project(
        project_root: Path,
        dataset_name: str,
        analysis_dir: Path,
        output_dir: Path,
        analysis_backend_path: str | None,
        eager: bool,
        expanded_helper_depth: int,
        test_dirs: tuple[str, ...],
    ) -> Path:
        del (
            project_root,
            dataset_name,
            analysis_dir,
            output_dir,
            analysis_backend_path,
            eager,
            expanded_helper_depth,
        )
        observed_test_dirs.append(test_dirs)
        return tmp_path / "output" / "result.json"

    monkeypatch.setattr(cli, "_analyze_project", fake_analyze_project)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "analysis",
            "--project-path",
            str(project_root),
            "--analysis-path",
            str(tmp_path / "cache"),
            "--output-path",
            str(tmp_path / "output"),
            "--test-dirs",
            "src/test/java, src/integrationTest/java",
        ],
    )

    assert result.exit_code == 0
    assert observed_test_dirs == [("src/test/java", "src/integrationTest/java")]


def test_analysis_command_uses_default_test_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "service"
    project_root.mkdir()

    observed_test_dirs: list[tuple[str, ...]] = []

    def fake_analyze_project(
        project_root: Path,
        dataset_name: str,
        analysis_dir: Path,
        output_dir: Path,
        analysis_backend_path: str | None,
        eager: bool,
        expanded_helper_depth: int,
        test_dirs: tuple[str, ...],
    ) -> Path:
        del (
            project_root,
            dataset_name,
            analysis_dir,
            output_dir,
            analysis_backend_path,
            eager,
            expanded_helper_depth,
        )
        observed_test_dirs.append(test_dirs)
        return tmp_path / "output" / "result.json"

    monkeypatch.setattr(cli, "_analyze_project", fake_analyze_project)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "analysis",
            "--project-path",
            str(project_root),
            "--analysis-path",
            str(tmp_path / "cache"),
            "--output-path",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code == 0
    assert observed_test_dirs == [
        (
            "src/test/java",
            "src/integrationTest/java",
            "src/functionalTest/java",
        )
    ]


def test_analysis_command_uses_default_expanded_helper_depth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "service"
    project_root.mkdir()

    observed_depths: list[int] = []

    def fake_analyze_project(
        project_root: Path,
        dataset_name: str,
        analysis_dir: Path,
        output_dir: Path,
        analysis_backend_path: str | None,
        eager: bool,
        expanded_helper_depth: int,
        test_dirs: tuple[str, ...],
    ) -> Path:
        del (
            project_root,
            dataset_name,
            analysis_dir,
            output_dir,
            analysis_backend_path,
            eager,
            test_dirs,
        )
        observed_depths.append(expanded_helper_depth)
        return tmp_path / "output" / "result.json"

    monkeypatch.setattr(cli, "_analyze_project", fake_analyze_project)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "analysis",
            "--project-path",
            str(project_root),
            "--analysis-path",
            str(tmp_path / "cache"),
            "--output-path",
            str(tmp_path / "output"),
        ],
    )

    assert result.exit_code == 0
    assert observed_depths == [10]


def test_verbose_flag_configures_rich_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "service"
    project_root.mkdir()

    captured: dict[str, object] = {}

    def fake_basic_config(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)

    runner = CliRunner()
    # Invoke a real command so the callback runs; it will fail at CLDK
    # but the callback executes before the command body.
    runner.invoke(
        cli.app,
        [
            "--verbose",
            "analysis",
            "--project-path",
            str(project_root),
            "--analysis-path",
            str(tmp_path / "cache"),
            "--output-path",
            str(tmp_path / "output"),
        ],
    )

    assert captured.get("level") == logging.DEBUG
    raw_handlers = captured.get("handlers")
    assert isinstance(raw_handlers, list)
    assert any(isinstance(h, RichHandler) for h in raw_handlers)


def test_no_verbose_flag_leaves_default_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "service"
    project_root.mkdir()

    captured: dict[str, object] = {}

    def fake_basic_config(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)

    runner = CliRunner()
    runner.invoke(
        cli.app,
        [
            "analysis",
            "--project-path",
            str(project_root),
            "--analysis-path",
            str(tmp_path / "cache"),
            "--output-path",
            str(tmp_path / "output"),
        ],
    )

    assert not captured
