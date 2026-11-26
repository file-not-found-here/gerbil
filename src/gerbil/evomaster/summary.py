"""Summarize how far each target's seed got, as the funnel a paper reports."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from gerbil.evomaster.pipeline import RunPaths, TargetRun
from gerbil.evomaster.targets import TARGETS, EvoMasterTarget


class TargetStatus(str, Enum):
    NOT_ATTEMPTED = "not-attempted"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    COMPLETED = "completed"
    OTHER_BUDGET = "other-budget"


class TargetFunnel(BaseModel):
    target: str
    status: TargetStatus
    last_stage: str | None = None
    error: str | None = None
    run: TargetRun | None = None


def target_funnel(
    target: EvoMasterTarget, output_dir: Path, seed: int, max_time: str | None
) -> TargetFunnel:
    record = RunPaths.of(output_dir, target, seed).record
    if not record.exists():
        return TargetFunnel(target=target.name, status=TargetStatus.NOT_ATTEMPTED)
    run = TargetRun.model_validate_json(record.read_text())
    last = run.stages[-1] if run.stages else None
    if max_time is not None and run.max_time != max_time:
        status = TargetStatus.OTHER_BUDGET
    elif run.failed_stage is not None:
        status = TargetStatus.FAILED
    elif not run.finished:
        status = TargetStatus.INTERRUPTED
    else:
        status = TargetStatus.COMPLETED
    return TargetFunnel(
        target=target.name,
        status=status,
        last_stage=last.stage.value if last is not None else None,
        error=last.error if last is not None else None,
        run=run,
    )


def funnel(output_dir: Path, seed: int, max_time: str | None) -> list[TargetFunnel]:
    """Each target's status; a budget marks runs that used a different one."""
    return [
        target_funnel(target, output_dir, seed, max_time) for target in TARGETS.values()
    ]
