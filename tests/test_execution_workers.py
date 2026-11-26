from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gerbil.execution import workers
from gerbil.execution.errors import ExecutionError
from gerbil.execution.workers import WorkerPool


@pytest.fixture(autouse=True)
def _poll_quickly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workers, "_POLL_SECONDS", 0.01)


def test_holders_never_exceed_the_budget(tmp_path: Path) -> None:
    pool = WorkerPool(tmp_path / "workers", 5)
    lock = threading.Lock()
    busy = 0
    peak = 0

    def work(needed: int) -> None:
        nonlocal busy, peak
        with pool.hold(needed):
            with lock:
                busy += needed
                peak = max(peak, busy)
            time.sleep(0.02)
            with lock:
                busy -= needed

    threads = [
        threading.Thread(target=work, args=(n,)) for n in (2, 2, 2, 1, 4, 5, 1, 3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not any(thread.is_alive() for thread in threads)
    assert busy == 0 and 4 <= peak <= 5


def test_a_request_waits_until_enough_workers_are_free(tmp_path: Path) -> None:
    pool = WorkerPool(tmp_path / "workers", 3)
    started = threading.Event()

    def second() -> None:
        with pool.hold(2):
            started.set()

    with pool.hold(2):
        thread = threading.Thread(target=second)
        thread.start()
        assert not started.wait(0.2)
    assert started.wait(10)
    thread.join(timeout=10)


def test_budget_is_shared_between_processes_and_freed_when_one_dies(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "workers"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; from pathlib import Path\n"
            "from gerbil.execution.workers import WorkerPool\n"
            "with WorkerPool(Path(sys.argv[1]), 2).hold(2):\n"
            "    print('held', flush=True); time.sleep(60)\n",
            str(directory),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        acquired = threading.Event()

        def take() -> None:
            with WorkerPool(directory, 2).hold(1):
                acquired.set()

        thread = threading.Thread(target=take)
        thread.start()
        assert not acquired.wait(0.3)
        holder.kill()
        holder.wait()
        assert acquired.wait(10)
        thread.join(timeout=10)
    finally:
        holder.kill()


@pytest.mark.parametrize("needed", [0, 4])
def test_a_request_outside_the_budget_is_refused(tmp_path: Path, needed: int) -> None:
    with pytest.raises(ExecutionError, match="budget of 3"):
        with WorkerPool(tmp_path / "workers", 3).hold(needed):
            pass


def test_a_waiting_request_gives_up_once_stopped(tmp_path: Path) -> None:
    pool = WorkerPool(tmp_path / "workers", 1)
    stop = threading.Event()
    outcome: list[str] = []

    def wait_for_worker() -> None:
        try:
            with pool.hold(1, stop):
                outcome.append("held")
        except ExecutionError as error:
            outcome.append(str(error))

    with pool.hold(1):
        thread = threading.Thread(target=wait_for_worker)
        thread.start()
        stop.set()
        thread.join(timeout=10)

    assert outcome == ["stopped while waiting for PIT workers"]


def test_a_request_that_gets_its_workers_after_a_stop_does_not_start(
    tmp_path: Path,
) -> None:
    stop = threading.Event()
    stop.set()

    with pytest.raises(ExecutionError, match="stopped"):
        with WorkerPool(tmp_path / "workers", 2).hold(1, stop):
            pytest.fail("held workers after the stop")
    with WorkerPool(tmp_path / "workers", 2).hold(2):
        pass


def test_runs_sharing_a_directory_must_agree_on_the_budget(tmp_path: Path) -> None:
    directory = tmp_path / "workers"

    with WorkerPool(directory, 2).joined():
        # Even while the first run holds no worker: counting 4, this run would see two
        # free that the other cannot see taken.
        with pytest.raises(ExecutionError, match="budget of 2 workers.*not 4"):
            with WorkerPool(directory, 4).joined():
                pytest.fail("joined under another budget")
        with WorkerPool(directory, 2).joined(), WorkerPool(directory, 2).hold(2):
            pass

    # With no run left, the budget may change.
    with WorkerPool(directory, 4).joined(), WorkerPool(directory, 4).hold(4):
        assert (directory / "total").read_text() == "4"


def test_a_run_that_dies_leaves_the_budget_free_to_change(tmp_path: Path) -> None:
    directory = tmp_path / "workers"
    run = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; from pathlib import Path\n"
            "from gerbil.execution.workers import WorkerPool\n"
            "with WorkerPool(Path(sys.argv[1]), 2).joined():\n"
            "    print('joined', flush=True); time.sleep(60)\n",
            str(directory),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert run.stdout is not None and run.stdout.readline().strip() == "joined"
        with pytest.raises(ExecutionError, match="budget of 2 workers"):
            with WorkerPool(directory, 3).joined():
                pass
    finally:
        run.kill()
        run.wait()

    with WorkerPool(directory, 3).joined():
        pass
