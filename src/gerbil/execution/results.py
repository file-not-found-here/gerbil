"""Gather a run's results and the ends of its logs into one directory and zip, to send
back from the machine that ran it."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from gerbil.evomaster.pipeline import gerbil_revision
from gerbil.execution.archive import replace_written_directory, zip_directory
from gerbil.execution.errors import ExecutionError
from gerbil.execution.projects import Suite

_RUN_FILES = ("run.log", "status.txt")
_PROJECT_FILES = ("summary.json", "mutants.json")
_SUITE_FILES = ("tests.json", "coverage.json", "mutation.json", "workspace.sha256")
_SUITES = frozenset(suite.value for suite in Suite)
_COLLECTED = "collected.json"
# The settings a run pinned, which its results depend on.
_PINNED = (".run/pit-timeout", ".run/sample", ".run/bundle", ".run/settings")


def collect_results(
    output_dir: Path, destination: Path, log_tail_bytes: int, part_bytes: int
) -> list[Path]:
    """Copy `output_dir`'s results, and each log's last `log_tail_bytes`, to `destination`.

    Workspaces and PIT's and JaCoCo's raw reports stay behind. Returns the zip parts of
    `destination` written beside it.
    """
    if destination.is_relative_to(output_dir) or output_dir.is_relative_to(destination):
        raise ExecutionError(
            f"gather {output_dir}'s results outside it, not in {destination}"
        )
    if destination.exists() and not (destination / _COLLECTED).is_file():
        raise ExecutionError(
            f"{destination} exists and holds no gathered results; choose a new directory"
        )
    building = destination.with_name(f"{destination.name}.partial")
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir(parents=True)
    for name in (*_RUN_FILES, *_PINNED):
        _copy(output_dir / name, building / name)
    _copy(output_dir / ".bundle/manifest.json", building / "bundle-manifest.json")
    for log in sorted(output_dir.glob("*.out")):
        _copy_tail(log, building / log.name, log_tail_bytes)
    for project in sorted(p for p in output_dir.iterdir() if _is_project(p)):
        target = building / project.name
        for name in _PROJECT_FILES:
            _copy(project / name, target / name)
        for log in sorted(project.glob("*.log")):
            _copy_tail(log, target / log.name, log_tail_bytes)
        _copy_logs(
            project / "service-mutants", target / "service-mutants", log_tail_bytes
        )
        for suite in sorted(p for p in project.iterdir() if p.name in _SUITES):
            suite_target = target / suite.name
            for name in _SUITE_FILES:
                _copy(suite / name, suite_target / name)
            for error in sorted(suite.glob("*-error.txt")):
                _copy(error, suite_target / error.name)
            _copy_logs(suite / "logs", suite_target / "logs", log_tail_bytes)
    (building / _COLLECTED).write_text(
        json.dumps(
            {
                "collected": datetime.now(UTC).isoformat(timespec="seconds"),
                "output_dir": str(output_dir),
                "gerbil_commit": gerbil_revision(),
                "log_tail_bytes": log_tail_bytes,
                # Shows a file missing, or left over from other results, after unzipping.
                "files": {
                    path.relative_to(building)
                    .as_posix(): hashlib.sha256(path.read_bytes())
                    .hexdigest()
                    for path in sorted(building.rglob("*"))
                    if path.is_file()
                },
            },
            indent=2,
        )
    )
    replace_written_directory(building, destination, _COLLECTED)
    return zip_directory(destination, part_bytes)


def _is_project(path: Path) -> bool:
    return path.is_dir() and not path.name.startswith(".")


def _copy(source: Path, target: Path) -> None:
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _copy_logs(source: Path, target: Path, tail_bytes: int) -> None:
    if not source.is_dir():
        return
    for log in sorted(source.rglob("*.log")):
        _copy_tail(log, target / log.relative_to(source), tail_bytes)


def _copy_tail(source: Path, target: Path, tail_bytes: int) -> None:
    if not source.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    size = source.stat().st_size
    with source.open("rb") as handle:
        if size > tail_bytes:
            handle.seek(size - tail_bytes)
            data = f"[first {size - tail_bytes} bytes left out]\n".encode()
            target.write_bytes(data + handle.read())
        else:
            target.write_bytes(handle.read())
