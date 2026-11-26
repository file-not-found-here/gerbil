"""The untracked Gerbil analyses, agent runs and EvoMaster runs on the machine that produced
them, from which bundles are built and EvoMaster's trees prepared."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gerbil.execution.errors import ExecutionError
from gerbil.execution.projects import ExecutionProject, Suite

# Where the 1-hour runs the comparison measures are.
EVOMASTER_RUNS_DIR = "outputs/execution-cs/evomaster-1h"


@dataclass(frozen=True)
class EvoMasterRuns:
    """Where `gerbil evomaster run` wrote its suites, and the seed and budget to take.

    A budget of None takes a run of any budget.
    """

    output_dir: Path
    seed: int
    max_time: str | None


@dataclass(frozen=True)
class SuiteSources:
    """Developer analyses come from the full corpus, agent analyses from their own runs."""

    outputs_root: Path
    repos_root: Path

    def analysis_path(self, project: ExecutionProject, suite: Suite) -> Path:
        if suite is Suite.DEVELOPER:
            root = self.outputs_root / "dev-full-gerbil" / project.dataset_name
        else:
            root = self.outputs_root / f"{suite.value}-gerbil" / project.name
        return root / "gerbil.json"

    def source_repo(self, project: ExecutionProject) -> Path:
        return self.repos_root / project.name

    def agent_patches(
        self, project: ExecutionProject, suite: Suite
    ) -> tuple[Path, Path]:
        """The run's test clearing and test patches."""
        runs = sorted(
            (self.outputs_root / f"{suite.value}-raw").glob(
                f"*__{suite.value}__{project.name}"
            )
        )
        if len(runs) != 1:
            raise ExecutionError(
                f"expected one {suite.value} run for {project.name}, found {len(runs)}"
            )
        output = runs[0] / "output"
        return output / "test_clearing.patch", output / "git_diff.patch"

    def test_clearing_patch(self, project: ExecutionProject) -> Path:
        """Codex's clearing, which is byte for byte Claude Code's for every project."""
        return self.agent_patches(project, Suite.CODEX)[0]
