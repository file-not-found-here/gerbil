from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from gerbil.execution import disk
from gerbil.execution.disk import FreeSpace, free_gb
from gerbil.execution.errors import ExecutionError


@pytest.fixture(autouse=True)
def _poll_quickly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disk, "_POLL_SECONDS", 0.01)


def _disk(monkeypatch: pytest.MonkeyPatch, *free_gbs: float) -> list[Path]:
    """A disk that has each of `free_gbs` free in turn, then the last; returns the
    directories it was asked about."""
    asked: list[Path] = []
    remaining = list(free_gbs)

    def disk_usage(directory: Path) -> SimpleNamespace:
        asked.append(directory)
        free = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return SimpleNamespace(free=free * 10**9)

    monkeypatch.setattr(disk.shutil, "disk_usage", disk_usage)
    return asked


def test_free_space_is_that_of_the_directorys_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _disk(monkeypatch, 12.5)

    assert free_gb(tmp_path) == 12.5
    assert asked == [tmp_path]


def test_work_starts_at_once_on_a_disk_with_enough_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = _disk(monkeypatch, 10)

    FreeSpace(tmp_path, 10).wait()

    assert len(asked) == 1 and capsys.readouterr().err == ""


def test_work_waits_until_enough_is_free_and_says_so_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = _disk(monkeypatch, 9.9, 3, 4, 11.5)

    FreeSpace(tmp_path, 10).wait(threading.Event())

    assert len(asked) == 4
    # Rounded down, so the free space never reads as the minimum it falls short of.
    assert capsys.readouterr().err.splitlines() == [
        f"waiting: {tmp_path} has 9 GB free; new work starts at 10",
        f"resumed: {tmp_path} has 11 GB free",
    ]


def test_space_is_short_below_the_minimum_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disk(monkeypatch, 9.9, 10)

    assert FreeSpace(tmp_path, 10).short()
    assert not FreeSpace(tmp_path, 10).short()


def test_waiting_work_gives_up_once_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disk(monkeypatch, 1)
    stop = threading.Event()
    outcome: list[str] = []

    def wait() -> None:
        try:
            FreeSpace(tmp_path, 10).wait(stop)
            outcome.append("started")
        except ExecutionError as error:
            outcome.append(str(error))

    waiter = threading.Thread(target=wait)
    waiter.start()
    waiter.join(timeout=0.2)
    assert waiter.is_alive()
    stop.set()
    waiter.join(timeout=10)

    assert outcome == ["stopped while waiting for free disk space"]


def test_no_minimum_never_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disk(monkeypatch, 0)

    FreeSpace(tmp_path, 0).wait()
