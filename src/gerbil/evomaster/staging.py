"""Put completed suites on the trees they were generated on, for batch analysis, and export them."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from gerbil.analysis.schema import RequestDispatch
from gerbil.evomaster.pipeline import (
    RunPaths,
    TargetRun,
    generated_sources,
    generated_tests,
    prepare_tree,
)
from gerbil.evomaster.summary import TargetStatus, target_funnel
from gerbil.evomaster.targets import EvoMasterTarget
from gerbil.execution.errors import ExecutionError
from gerbil.execution.sources import EvoMasterRuns, SuiteSources
from gerbil.execution.selection import ApiTest, SuiteAnalysis
from gerbil.execution.workspace import materialize_workspace

_TEST_SOURCES = Path("src/test/java")
EXPORTED_PATCH = Path("test_patches/git_diff.patch")
EXPORTED_RUN = "run.json"


def completed_run(target: EvoMasterTarget, runs: EvoMasterRuns) -> TargetRun:
    row = target_funnel(target, runs.output_dir, runs.seed, runs.max_time)
    if row.status is not TargetStatus.COMPLETED or row.run is None:
        raise ExecutionError(f"{target.name}'s seed {runs.seed} is {row.status.value}")
    return row.run


def generated_api_tests(target: EvoMasterTarget, runs: EvoMasterRuns) -> SuiteAnalysis:
    """Every generated test, each calling the service its driver starts on localhost."""
    run = completed_run(target, runs)
    assert run.commit is not None
    generated = RunPaths.of(runs.output_dir, target, runs.seed).generated
    return SuiteAnalysis(
        run.commit,
        tuple(
            ApiTest(class_name, f"{method}()", (RequestDispatch.LOCAL_NETWORK.value,))
            for class_name, method in generated_tests(generated)
        ),
    )


def stage_generated_suite(
    target: EvoMasterTarget,
    sources: SuiteSources,
    runs: EvoMasterRuns,
    staged_root: Path,
) -> Path:
    """Put a completed seed's suite on the tree it was generated on: the pinned source
    with the agents' test clearing, plus the harness.

    Clearing the same files as the agent suites leaves every suite the same application.
    """
    run = completed_run(target, runs)
    destination = staged_root / target.project.name
    if destination.exists():
        raise ExecutionError(f"{destination} already exists")
    # Batch analysis skips dot directories, so a failed attempt is not analyzed.
    building = destination.with_name(f".{destination.name}.building")
    _assemble_tree(target, sources, runs, run, building)
    building.rename(destination)
    return destination


def export_generated_suite(
    target: EvoMasterTarget,
    sources: SuiteSources,
    runs: EvoMasterRuns,
    export_root: Path,
) -> Path:
    """Save a completed seed's suite as the agents' runs are saved: the diff from the
    agents' starting point to the tree Gerbil analyzes, the run's record, and EvoMaster's
    statistics of the final suite and of the search as it ran.

    The diff holds the restored paths and the harness as well as the generated tests, so
    the pinned commit plus the agents' test clearing and it rebuild that tree.
    """
    run = completed_run(target, runs)
    project = target.project
    destination = export_root / project.name
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch) / project.name
        _assemble_tree(target, sources, runs, run, tree)
        patch = _diff_from_cleared_commit(tree, sources.test_clearing_patch(project))
    (destination / EXPORTED_PATCH).parent.mkdir(parents=True, exist_ok=True)
    (destination / EXPORTED_PATCH).write_bytes(patch)
    paths = RunPaths.of(runs.output_dir, target, runs.seed)
    shutil.copyfile(paths.record, destination / EXPORTED_RUN)
    for statistics in (paths.statistics, paths.snapshot):
        shutil.copyfile(statistics, destination / statistics.name)
    return destination


def _diff_from_cleared_commit(tree: Path, test_clearing: Path) -> bytes:
    # Only the index changes here: prepare_tree edits the working tree and leaves the
    # index at the pinned commit, so the clearing applies to it cleanly.
    def git(*args: str) -> bytes:
        completed = subprocess.run(["git", "-C", str(tree), *args], capture_output=True)
        if completed.returncode != 0:
            raise ExecutionError(
                f"git {args[0]} in {tree} failed: {completed.stderr.decode().strip()}"
            )
        return completed.stdout

    git("read-tree", "HEAD")
    git("apply", "--cached", "--whitespace=nowarn", str(test_clearing.resolve()))
    cleared = git("write-tree").decode().strip()
    # -f, since a project's ignore rules can cover files the harness adds.
    git("add", "--all", "--force")
    return git(
        "-c",
        "core.quotePath=false",
        "diff",
        "--cached",
        "--binary",
        "--no-renames",
        "--no-color",
        "--no-ext-diff",
        cleared,
    )


def _assemble_tree(
    target: EvoMasterTarget,
    sources: SuiteSources,
    runs: EvoMasterRuns,
    run: TargetRun,
    building: Path,
) -> None:
    assert run.commit is not None
    project = target.project
    shutil.rmtree(building, ignore_errors=True)
    materialize_workspace(sources.source_repo(project), run.commit, building)
    prepare_tree(target, sources, building)
    generated = RunPaths.of(runs.output_dir, target, runs.seed).generated
    test_root = building / project.build_root / target.module / _TEST_SOURCES
    for suite in generated_sources(generated):
        copied = test_root / suite.relative_to(generated)
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(suite, copied)
