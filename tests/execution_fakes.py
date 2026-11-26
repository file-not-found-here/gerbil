"""A container double that records commands and simulates their file outputs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from gerbil.execution.container import WORKSPACE, Container

Effect = Callable[["RecordingContainer", list[str], PurePosixPath], None]


@dataclass(frozen=True)
class RecordingContainer(Container):
    """`memory_kills` is what a run asked for its memory accounting reports, if anything."""

    effect: Effect | None = None
    commands: list[tuple[list[str], PurePosixPath]] = field(default_factory=list)
    memory_kills: int | None = 0

    def run(
        self,
        command: Sequence[str],
        *,
        log: Path,
        workdir: PurePosixPath = WORKSPACE,
        timeout_seconds: int | None = None,
        memory_report: PurePosixPath | None = None,
    ) -> None:
        self.commands.append((list(command), workdir))
        if memory_report is not None and self.memory_kills is not None:
            report = self.host_path(memory_report)
            report.mkdir(parents=True, exist_ok=True)
            (report / "memory.events").write_text(
                f"oom 0\noom_kill {self.memory_kills}\n"
            )
            (report / "memory.peak").write_text("2048\n")
        if self.effect is not None:
            self.effect(self, list(command), workdir)


def recording_container(
    tmp_path: Path, effect: Effect | None = None, jdk: int = 17
) -> RecordingContainer:
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    return RecordingContainer(tmp_path / "workspace", tmp_path, jdk, effect=effect)
