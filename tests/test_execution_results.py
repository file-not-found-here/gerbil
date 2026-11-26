from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gerbil import cli
from gerbil.execution import results
from gerbil.execution.archive import zip_directory
from gerbil.execution.errors import ExecutionError
from gerbil.execution.results import collect_results

_PART = re.compile(r"bundle-\d{8}T\d{6}Z-\d+of\d+\.zip")


def _write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data)
    return path


def _extract(parts: list[Path], into: Path) -> None:
    for part in parts:
        with zipfile.ZipFile(part) as archive:
            archive.extractall(into)


def _files(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def test_a_small_directory_is_one_zip_holding_its_folder(tmp_path: Path) -> None:
    directory = tmp_path / "bundle"
    _write(directory / "manifest.json", "{}")
    _write(directory / "demo/clearing.json", "[]")

    parts = zip_directory(directory, part_bytes=10**6)

    (part,) = parts
    assert _PART.fullmatch(part.name) and part.name.endswith("-1of1.zip")
    with zipfile.ZipFile(parts[0]) as archive:
        assert sorted(archive.namelist()) == [
            "bundle/demo/clearing.json",
            "bundle/manifest.json",
        ]


def test_parts_each_hold_whole_files_and_together_rebuild_the_directory(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "bundle"
    for index in range(6):
        _write(directory / f"seed/{index}.jar", os.urandom(4000))
    _write(directory / "seed/big.jar", os.urandom(12000))
    _write(directory / "manifest.json", "{}")

    parts = zip_directory(directory, part_bytes=10000)

    assert len(parts) >= 3
    stamps = {part.name.split("-")[1] for part in parts}
    assert len(stamps) == 1
    assert [part.name.rsplit("-", 1)[1] for part in parts] == [
        f"{n}of{len(parts)}.zip" for n in range(1, len(parts) + 1)
    ]
    # Only a part holding one larger file may exceed the limit.
    for part in parts:
        with zipfile.ZipFile(part) as archive:
            assert part.stat().st_size <= 10000 or len(archive.namelist()) == 1
    _extract(parts, tmp_path / "rebuilt")
    assert _files(tmp_path / "rebuilt" / "bundle") == _files(directory)


def test_zipping_again_removes_the_earlier_parts_only(tmp_path: Path) -> None:
    directory = tmp_path / "bundle"
    _write(directory / "manifest.json", "{}")
    stale = (
        "bundle-1.zip",
        "bundle.zip.partial",
        "bundle-20261101T010203Z-2of3.zip",
        "other.zip",
        "bundle-notes.zip",
    )
    for name in stale:
        _write(tmp_path / name, "stale")

    (part,) = zip_directory(directory, part_bytes=10**6)

    assert sorted(path.name for path in tmp_path.glob("*.zip*")) == sorted(
        [part.name, "other.zip", "bundle-notes.zip"]
    )


def _run_output(root: Path) -> Path:
    """An output directory as the run script and both stages leave it."""
    out = root / "execution"
    _write(out / "run.log", "START coverage hertzbeat\n")
    _write(out / "status.txt", "hertzbeat done done\n")
    _write(out / ".run/pit-timeout", "180\n")
    _write(out / ".run/sample", "2401\n")
    _write(out / ".run/settings", "workers=18 threads=6\n")
    _write(out / ".run/bundle", "abc\n")
    _write(out / ".workers/worker-0.lock", "")
    _write(out / ".run/start.lock/x", "")
    _write(out / ".bundle/manifest.json", "{}")
    _write(out / ".bundle/hertzbeat/codex.json", "{}")
    _write(out / "hertzbeat-coverage.out", "x" * 100 + "the end\n")
    project = out / "hertzbeat"
    _write(project / "summary.json", "{}")
    _write(project / "mutants.json", "{}")
    _write(project / "modules.log", "modules\n")
    _write(project / "service-mutants/pit.log", "pit\n")
    _write(project / "service-mutants/mutations.xml", "<raw/>")
    for suite in ("developer", "evomaster"):
        root_dir = project / suite
        for name in (
            "tests.json",
            "coverage.json",
            "mutation.json",
            "workspace.sha256",
        ):
            _write(root_dir / name, name)
        _write(root_dir / "classpaths.json", "[]")
        _write(root_dir / "logs/coverage/build.log", "built\n")
        _write(root_dir / "logs/mutation/root.log", "mutated\n")
        _write(root_dir / "workspace/pom.xml", "<project/>")
        _write(root_dir / "runs/root/baseline/jacoco.exec", b"\x00")
        _write(root_dir / "pit/root/mutations.xml", "<raw/>")
    _write(project / "evomaster/coverage-error.txt", "compilation failed")
    return out


def test_results_keep_results_and_log_ends_and_leave_trees_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(results, "gerbil_revision", lambda: "gerbil-commit")
    out = _run_output(tmp_path)
    destination = tmp_path / "execution-results"

    parts = collect_results(out, destination, log_tail_bytes=20, part_bytes=10**6)

    collected = _files(destination)
    suite_files = [
        "coverage.json",
        "logs/coverage/build.log",
        "logs/mutation/root.log",
        "mutation.json",
        "tests.json",
        "workspace.sha256",
    ]
    assert sorted(collected) == sorted(
        [
            ".run/bundle",
            ".run/pit-timeout",
            ".run/sample",
            ".run/settings",
            "bundle-manifest.json",
            "collected.json",
            "hertzbeat-coverage.out",
            "hertzbeat/modules.log",
            "hertzbeat/mutants.json",
            "hertzbeat/service-mutants/pit.log",
            "hertzbeat/summary.json",
            *(f"hertzbeat/developer/{name}" for name in suite_files),
            *(f"hertzbeat/evomaster/{name}" for name in suite_files),
            "hertzbeat/evomaster/coverage-error.txt",
            "run.log",
            "status.txt",
        ]
    )
    assert collected["hertzbeat-coverage.out"] == (
        b"[first 88 bytes left out]\n" + b"x" * 12 + b"the end\n"
    )
    assert collected["hertzbeat/developer/logs/coverage/build.log"] == b"built\n"
    record = json.loads(collected["collected.json"])
    assert record["gerbil_commit"] == "gerbil-commit"
    assert record["files"] == {
        name: hashlib.sha256(data).hexdigest()
        for name, data in collected.items()
        if name != "collected.json"
    }
    (part,) = parts
    assert part.name.startswith("execution-results-") and part.name.endswith(
        "-1of1.zip"
    )
    _extract(parts, tmp_path / "rebuilt")
    assert _files(tmp_path / "rebuilt" / "execution-results") == collected


def test_collecting_again_replaces_the_earlier_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(results, "gerbil_revision", lambda: "gerbil-commit")
    out = _run_output(tmp_path)
    destination = tmp_path / "execution-results"
    collect_results(out, destination, log_tail_bytes=1024, part_bytes=10**6)
    _write(destination / "stale.json", "{}")

    collect_results(out, destination, log_tail_bytes=1024, part_bytes=10**6)

    assert not (destination / "stale.json").exists()
    assert not destination.with_name("execution-results.partial").exists()


def test_cli_results_reports_each_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(results, "gerbil_revision", lambda: "gerbil-commit")
    out = _run_output(tmp_path)

    result = CliRunner().invoke(
        cli.app,
        [
            "execution",
            "results",
            "--output-dir",
            str(out),
            "--destination",
            str(tmp_path / "execution-results"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert re.search(
        rf"zip: {re.escape(str(tmp_path))}/execution-results-\S+-1of1\.zip \(",
        result.output,
    )


def test_parts_stay_within_the_limit_with_many_small_files(tmp_path: Path) -> None:
    directory = tmp_path / "bundle"
    for index in range(400):
        _write(directory / f"seed/org/example/artifact-{index:04d}.pom", os.urandom(40))

    parts = zip_directory(directory, part_bytes=8000)

    assert len(parts) > 1
    assert all(part.stat().st_size <= 8000 for part in parts)


@pytest.mark.parametrize("where", ["existing", "inside"])
def test_results_never_replace_a_directory_they_did_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    monkeypatch.setattr(results, "gerbil_revision", lambda: "gerbil-commit")
    out = _run_output(tmp_path)
    destination = {
        "existing": tmp_path / "Downloads",
        "inside": out / "execution-results",
    }[where]
    _write(destination / "bundle.zip", "keep me")

    with pytest.raises(ExecutionError):
        collect_results(out, destination, log_tail_bytes=1024, part_bytes=10**6)

    assert (destination / "bundle.zip").read_text() == "keep me"
    assert (out / "hertzbeat/summary.json").exists()


def test_results_refuse_to_gather_into_a_parent_of_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(results, "gerbil_revision", lambda: "gerbil-commit")
    out = _run_output(tmp_path)
    _write(tmp_path / "collected.json", "{}")

    with pytest.raises(ExecutionError, match="outside it"):
        collect_results(out, tmp_path, log_tail_bytes=1024, part_bytes=10**6)

    assert (out / "hertzbeat/summary.json").exists()
