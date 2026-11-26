"""Hold new work back while the disk under the results is nearly full."""

from __future__ import annotations

import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from gerbil.execution.errors import ExecutionError

_POLL_SECONDS = 30.0


def free_gb(directory: Path) -> float:
    return shutil.disk_usage(directory).free / 10**9


@dataclass(frozen=True)
class FreeSpace:
    """Work that waits here starts once `directory`'s disk has `min_free_gb` free."""

    directory: Path
    min_free_gb: int

    def short(self) -> bool:
        return free_gb(self.directory) < self.min_free_gb

    def wait(self, stop: threading.Event | None = None) -> None:
        """Return once enough is free; give up, with an error, once `stop` is set."""
        waited = False
        while (free := free_gb(self.directory)) < self.min_free_gb:
            if not waited:
                _report(
                    f"waiting: {self.directory} has {int(free)} GB free; "
                    f"new work starts at {self.min_free_gb}"
                )
                waited = True
            if stop is None:
                time.sleep(_POLL_SECONDS)
            elif stop.wait(_POLL_SECONDS):
                raise ExecutionError("stopped while waiting for free disk space")
        if waited:
            _report(f"resumed: {self.directory} has {int(free)} GB free")


def _report(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
