"""Share a budget of PIT workers among every mutation process of one results directory."""

from __future__ import annotations

import fcntl
import os
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from gerbil.execution.errors import ExecutionError

_POLL_SECONDS = 2.0


@dataclass(frozen=True)
class WorkerPool:
    """`total` workers, each a lock file in `directory` that a holder keeps locked.

    The kernel drops a lock when its process dies, so a killed run frees its workers.
    """

    directory: Path
    total: int

    @contextmanager
    def hold(self, workers: int, stop: threading.Event | None = None) -> Iterator[None]:
        """Wait for `workers` free workers and keep them for the block; give up, with
        an error, once `stop` is set."""
        if not 1 <= workers <= self.total:
            raise ExecutionError(
                f"{workers} workers were asked of a budget of {self.total}"
            )
        self.directory.mkdir(parents=True, exist_ok=True)
        with ExitStack() as held:
            while not self._take(workers, held):
                if stop is None:
                    time.sleep(_POLL_SECONDS)
                elif stop.wait(_POLL_SECONDS):
                    break
            if stop is not None and stop.is_set():
                raise ExecutionError("stopped while waiting for PIT workers")
            yield

    @contextmanager
    def joined(self) -> Iterator[None]:
        """Share the directory with the other runs for the block, refusing a budget
        other than theirs: each would see free workers the other counts as taken."""
        self.directory.mkdir(parents=True, exist_ok=True)
        record = self.directory / "total"
        runs = os.open(self.directory / "runs.lock", os.O_CREAT | os.O_RDWR, 0o644)
        try:
            with _locked(self.directory / "take.lock", blocking=True):
                try:
                    # Held alone only when no other run has joined.
                    fcntl.flock(runs, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    record.write_text(str(self.total))
                except BlockingIOError:
                    if record.read_text() != str(self.total):
                        raise ExecutionError(
                            f"another run shares {self.directory} under a budget of "
                            f"{record.read_text()} workers; give this run the same, "
                            f"not {self.total}"
                        ) from None
                fcntl.flock(runs, fcntl.LOCK_SH)
            yield
        finally:
            os.close(runs)

    def _take(self, workers: int, held: ExitStack) -> bool:
        """Lock `workers` free workers, or none when fewer are free."""
        # One taker at a time, so two cannot each hold part of what both need.
        with _locked(self.directory / "take.lock", blocking=True), ExitStack() as taken:
            count = 0
            for index in range(self.total):
                if count == workers:
                    break
                try:
                    taken.enter_context(
                        _locked(self.directory / f"worker-{index}.lock", blocking=False)
                    )
                except BlockingIOError:
                    continue
                count += 1
            if count < workers:
                return False
            held.push(taken.pop_all())
        return True


@contextmanager
def _locked(path: Path, *, blocking: bool) -> Iterator[None]:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        # Closing the descriptor releases its lock.
        os.close(descriptor)
