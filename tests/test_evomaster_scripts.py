from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from gerbil.evomaster.targets import DOCKER_DIRECTORY

_WITH_DRIVER = DOCKER_DIRECTORY / "with-driver.sh"
_FETCH_SCHEMA = DOCKER_DIRECTORY / "fetch-schema.sh"


def _fake(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


def _run(script: Path, bin_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        timeout=30,
    )


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    return directory


def test_a_driver_that_exits_before_listening_fails_the_command(
    bin_dir: Path, tmp_path: Path
) -> None:
    _fake(bin_dir, "java", 'echo "no main class"; exit 3')
    _fake(bin_dir, "curl", "exit 7")
    log = tmp_path / "driver.log"

    completed = _run(_WITH_DRIVER, bin_dir, "args", str(log), "true")

    assert completed.returncode == 1
    assert "exited before listening" in completed.stderr
    assert log.read_text() == "no main class\n"


def test_the_commands_status_is_returned_and_the_driver_stopped(
    bin_dir: Path, tmp_path: Path
) -> None:
    stopped, ready = tmp_path / "stopped", tmp_path / "ready"
    _fake(
        bin_dir,
        "java",
        f"trap 'touch {stopped}; exit 0' TERM; touch {ready}; sleep 20 & wait",
    )
    # The driver listens only once its trap is set, so stopping it is observable.
    _fake(bin_dir, "curl", f"[ -e {ready} ]")

    completed = _run(
        _WITH_DRIVER, bin_dir, "args", str(tmp_path / "log"), "bash", "-c", "exit 5"
    )

    assert completed.returncode == 5
    assert stopped.exists()


def _schema_curl(bin_dir: Path, base: str) -> None:
    """Answers the controller's runSUT and infoSUT, and serves each path as its body."""
    _fake(
        bin_dir,
        "curl",
        f"""output=; url=
for argument in "$@"; do
    case "$previous" in -o) output=$argument ;; esac
    case "$argument" in http*) url=$argument ;; esac
    previous=$argument
done
case "$url" in
    */runSUT) exit 0 ;;
    */infoSUT) echo '{{"data":{{"isSutRunning":true,"baseUrlOfSUT":"{base}"}}}}' ;;
    *) echo "document of ${{url#http://sut}}" > "$output" ;;
esac""",
    )


def test_each_schema_path_is_saved_from_the_running_service(
    bin_dir: Path, tmp_path: Path
) -> None:
    _schema_curl(bin_dir, "http://sut")
    first, second = tmp_path / "a.json", tmp_path / "b.json"

    completed = _run(_FETCH_SCHEMA, bin_dir, "/v3/a", str(first), "/v3/b", str(second))

    assert completed.returncode == 0, completed.stderr
    assert first.read_text() == "document of /v3/a\n"
    assert second.read_text() == "document of /v3/b\n"


def test_a_driver_without_a_service_url_fails_the_schema_fetch(
    bin_dir: Path, tmp_path: Path
) -> None:
    _schema_curl(bin_dir, "")

    completed = _run(_FETCH_SCHEMA, bin_dir, "/v3/a", str(tmp_path / "a.json"))

    assert completed.returncode == 1
    assert "no service URL" in completed.stderr
