"""Tests for the analysis_mover cache-staging script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis_mover.py"


def _run(
    input_root: Path, output_root: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input-root",
            str(input_root),
            "--output-root",
            str(output_root),
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def _make_project(input_root: Path, name: str, content: str) -> None:
    analysis_dir = input_root / name / "symbol_table"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "analysis.json").write_text(content, encoding="utf-8")


def test_copies_each_project_analysis_into_output_layout(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    _make_project(input_root, "service-a", '{"a": 1}')
    _make_project(input_root, "service-b", '{"b": 2}')

    result = _run(input_root, output_root)

    assert result.returncode == 0
    assert (output_root / "service-a" / "analysis.json").read_text(
        encoding="utf-8"
    ) == '{"a": 1}'
    assert (output_root / "service-b" / "analysis.json").read_text(
        encoding="utf-8"
    ) == '{"b": 2}'
    assert (
        "done: 2 copied, 0 skipped (existing), 0 skipped (no source)" in result.stdout
    )


def test_skips_projects_without_source_analysis(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    _make_project(input_root, "service-a", '{"a": 1}')
    (input_root / "empty-project").mkdir()

    result = _run(input_root, output_root)

    assert result.returncode == 0
    assert "skip: empty-project (no symbol_table/analysis.json)" in result.stderr
    assert not (output_root / "empty-project").exists()
    assert (
        "done: 1 copied, 0 skipped (existing), 1 skipped (no source)" in result.stdout
    )


def test_skips_existing_destination_without_overwriting(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    _make_project(input_root, "service-a", '{"new": true}')
    dest_dir = output_root / "service-a"
    dest_dir.mkdir(parents=True)
    (dest_dir / "analysis.json").write_text('{"old": true}', encoding="utf-8")

    result = _run(input_root, output_root)

    assert result.returncode == 0
    assert (dest_dir / "analysis.json").read_text(encoding="utf-8") == '{"old": true}'
    assert "skip: service-a (analysis.json already exists)" in result.stderr
    assert (
        "done: 0 copied, 1 skipped (existing), 0 skipped (no source)" in result.stdout
    )


def test_copies_more_projects_than_default_jobs(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    project_names = [f"service-{index:02d}" for index in range(25)]
    for name in project_names:
        _make_project(input_root, name, f'{{"name": "{name}"}}')

    result = _run(input_root, output_root)

    assert result.returncode == 0
    for name in project_names:
        assert (output_root / name / "analysis.json").read_text(
            encoding="utf-8"
        ) == f'{{"name": "{name}"}}'
    assert (
        "done: 25 copied, 0 skipped (existing), 0 skipped (no source)" in result.stdout
    )


def test_reports_each_project_once_with_progress_counters(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    for name in ("service-b", "service-a", "service-c"):
        _make_project(input_root, name, "{}")

    result = _run(input_root, output_root)

    assert result.returncode == 0
    copied_lines = [line for line in result.stdout.splitlines() if "copied:" in line]
    # Lines stream in completion order, so counters increase but names may not sort.
    assert [line.split("]")[0] + "]" for line in copied_lines] == [
        "[1/3]",
        "[2/3]",
        "[3/3]",
    ]
    assert {line.split("] ", 1)[1] for line in copied_lines} == {
        f"copied: {name} -> {output_root / name / 'analysis.json'}"
        for name in ("service-a", "service-b", "service-c")
    }


def test_progress_counters_span_copied_and_skipped_projects(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    _make_project(input_root, "service-a", "{}")
    (input_root / "empty-project").mkdir()

    result = _run(input_root, output_root)

    assert result.returncode == 0
    progress_lines = [
        line
        for line in (result.stdout + result.stderr).splitlines()
        if line.startswith("[")
    ]
    assert sorted(line.split("]")[0] + "]" for line in progress_lines) == [
        "[1/2]",
        "[2/2]",
    ]


def test_rejects_missing_input_root(tmp_path: Path) -> None:
    result = _run(tmp_path / "missing", tmp_path / "output")

    assert result.returncode == 1
    assert "input root is not a directory" in result.stderr


def test_rejects_non_positive_jobs(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()

    result = _run(input_root, tmp_path / "output", "--jobs", "0")

    assert result.returncode == 1
    assert "jobs must be at least 1" in result.stderr
