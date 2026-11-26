from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gerbil import cli
from gerbil.evomaster import staging, summary
from gerbil.evomaster.pipeline import (
    RunPaths,
    RunSettings,
    SearchResult,
    Stage,
    StageOutcome,
    TargetRun,
)
from gerbil.evomaster.staging import (
    EXPORTED_PATCH,
    EXPORTED_RUN,
    export_generated_suite,
    generated_api_tests,
    stage_generated_suite,
)
from gerbil.evomaster.summary import TargetStatus, funnel
from gerbil.evomaster.targets import TARGETS, EvoMasterTarget
from gerbil.execution.errors import ExecutionError
from gerbil.execution.sources import EvoMasterRuns, SuiteSources
from gerbil.execution.workspace import apply_patches, restore_workspace_paths

_TARGET = TARGETS["hertzbeat"]
# Stand-ins for whichever services the funnel and the CLI iterate over.
_FUNNEL_NAMES = ("completed", "failed", "interrupted", "other-budget", "untried")


@pytest.fixture
def several_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    targets = {name: replace(_TARGET, name=name) for name in _FUNNEL_NAMES}
    monkeypatch.setattr(summary, "TARGETS", targets)
    monkeypatch.setattr(cli, "TARGETS", targets)


def _record(
    output: Path,
    *,
    error: str | None = None,
    finished: bool = True,
    max_time: str = "1h",
    target: str = "hertzbeat",
    commit: str = "abc123",
) -> None:
    run = TargetRun(
        target=target,
        project=_TARGET.project.name,
        max_time=max_time,
        seed=1234,
        until=Stage.VERIFY,
        commit=commit,
        stages=[
            StageOutcome(stage=Stage.GENERATE, seconds=1.0),
            StageOutcome(stage=Stage.VERIFY, seconds=1.0, error=error),
        ],
        search=SearchResult(
            generated_tests=1,
            endpoints=1,
            endpoints_2xx=1,
            potential_faults=0,
            covered_targets=1,
            covered_lines=1,
            total_lines=1,
            covered_branches=0,
            total_branches=0,
            sql_heuristics_evaluations=0,
            sql_parsing_failures=0,
        ),
        finished=finished,
    )
    path = RunPaths.of(output, replace(_TARGET, name=target), 1234).record
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json())


def _write_suite(output: Path) -> None:
    generated = RunPaths.of(output, _TARGET, 1234).generated
    suite = generated / "em/generated/EvoMaster_Test.java"
    suite.parent.mkdir(parents=True)
    suite.write_text(
        "class EvoMaster_Test {\n"
        "    @Test @Timeout(60)\n    public void test_0_getOnUsers() {}\n"
        "    @Test\n    public void test_1_postOnUsers() {}\n"
        "}"
    )
    assets = generated / "assets/report.js"
    assets.parent.mkdir(parents=True)
    assets.write_text("report")
    paths = RunPaths.of(output, _TARGET, 1234)
    paths.statistics.write_text("generatedTests,coveredTargets\n2,10\n")
    paths.snapshot.write_text("generatedTests,coveredTargets\n1,4\n2,10\n")


@pytest.fixture
def applied(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """Each step building the staged tree, in order."""
    calls: list[tuple[str, object]] = []

    def materialize(_repo: Path, commit: str, dest: Path) -> None:
        calls.append(("checkout", commit))
        dest.mkdir(parents=True)

    def prepare_tree(
        target: EvoMasterTarget, _inputs: SuiteSources, workspace: Path
    ) -> None:
        calls.append(("prepare", target.name))
        root = workspace / target.project.build_root / target.module
        driver = root / "src/test/java/em/embedded/Driver.java"
        driver.parent.mkdir(parents=True)
        driver.write_text("class Driver {}")

    monkeypatch.setattr(staging, "materialize_workspace", materialize)
    monkeypatch.setattr(staging, "prepare_tree", prepare_tree)
    return calls


@pytest.fixture
def sources(tmp_path: Path) -> SuiteSources:
    outputs = tmp_path / "outputs"
    (outputs / "codex-raw" / "x__codex__hertzbeat").mkdir(parents=True)
    return SuiteSources(outputs, tmp_path / "repos")


def _runs(tmp_path: Path, max_time: str | None = None) -> EvoMasterRuns:
    return EvoMasterRuns(tmp_path / "out", 1234, max_time)


def _stage(sources: SuiteSources, tmp_path: Path) -> Path:
    return stage_generated_suite(_TARGET, sources, _runs(tmp_path), tmp_path / "staged")


def test_staging_puts_the_suite_beside_its_driver_in_the_drivers_module(
    sources: SuiteSources, tmp_path: Path, applied: list[tuple[str, object]]
) -> None:
    _write_suite(tmp_path / "out")
    _record(tmp_path / "out")

    destination = _stage(sources, tmp_path)

    test_root = destination / "hertzbeat-manager/src/test/java"
    assert (test_root / "em/generated/EvoMaster_Test.java").is_file()
    assert (test_root / "em/embedded/Driver.java").is_file()
    assert not list(destination.rglob("report.js"))


def test_staging_builds_the_tree_the_suite_was_generated_on(
    sources: SuiteSources, tmp_path: Path, applied: list[tuple[str, object]]
) -> None:
    _write_suite(tmp_path / "out")
    _record(tmp_path / "out")

    _stage(sources, tmp_path)

    assert applied == [("checkout", "abc123"), ("prepare", "hertzbeat")]


def test_staging_resolves_the_module_under_the_build_root(
    sources: SuiteSources, tmp_path: Path, applied: list[tuple[str, object]]
) -> None:
    target = replace(_TARGET, project=replace(_TARGET.project, build_root="server"))
    _write_suite(tmp_path / "out")
    _record(tmp_path / "out")

    destination = stage_generated_suite(
        target, sources, _runs(tmp_path), tmp_path / "staged"
    )

    generated = destination / "server/hertzbeat-manager/src/test/java/em/generated"
    assert (generated / "EvoMaster_Test.java").is_file()


@pytest.mark.parametrize(
    "record", [{"error": "boom"}, {"finished": False}], ids=["failed", "interrupted"]
)
def test_staging_refuses_a_suite_whose_run_did_not_complete(
    sources: SuiteSources,
    tmp_path: Path,
    applied: list[tuple[str, object]],
    record: dict[str, object],
) -> None:
    _write_suite(tmp_path / "out")
    _record(tmp_path / "out", **record)  # type: ignore[arg-type]

    with pytest.raises(ExecutionError, match="seed 1234 is"):
        _stage(sources, tmp_path)


def test_staging_without_a_run_is_an_error(
    sources: SuiteSources, tmp_path: Path
) -> None:
    with pytest.raises(ExecutionError, match="not-attempted"):
        _stage(sources, tmp_path)


def test_staging_refuses_an_existing_tree(
    sources: SuiteSources, tmp_path: Path
) -> None:
    _write_suite(tmp_path / "out")
    _record(tmp_path / "out")
    (tmp_path / "staged/hertzbeat").mkdir(parents=True)

    with pytest.raises(ExecutionError, match="already exists"):
        _stage(sources, tmp_path)


def test_staging_recovers_from_an_interrupted_attempt(
    sources: SuiteSources, tmp_path: Path, applied: list[tuple[str, object]]
) -> None:
    _write_suite(tmp_path / "out")
    _record(tmp_path / "out")
    (tmp_path / "staged/.hertzbeat.building").mkdir(parents=True)

    assert _stage(sources, tmp_path).is_dir()
    assert not (tmp_path / "staged/.hertzbeat.building").exists()


def test_funnel_distinguishes_every_status(
    tmp_path: Path, several_targets: None
) -> None:
    _record(tmp_path, target="completed")
    _record(tmp_path, target="failed", error="boom")
    _record(tmp_path, target="interrupted", finished=False)
    _record(tmp_path, target="other-budget", max_time="5m")

    rows = {row.target: row for row in funnel(tmp_path, 1234, "1h")}

    assert rows["completed"].status is TargetStatus.COMPLETED
    assert rows["failed"].status is TargetStatus.FAILED
    assert rows["failed"].error == "boom"
    assert rows["interrupted"].status is TargetStatus.INTERRUPTED
    assert rows["other-budget"].status is TargetStatus.OTHER_BUDGET
    assert rows["untried"].status is TargetStatus.NOT_ATTEMPTED


def test_cli_rejects_an_unknown_target() -> None:
    result = CliRunner().invoke(cli.app, ["evomaster", "run", "--target", "nope"])

    assert result.exit_code == 2


def test_cli_lists_targets() -> None:
    result = CliRunner().invoke(cli.app, ["evomaster", "targets"])

    assert result.exit_code == 0
    assert set(result.stdout.split()) == set(TARGETS)


def test_cli_run_exits_nonzero_when_a_stage_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    failed = TargetRun(
        target="hertzbeat",
        project="hertzbeat",
        max_time="1h",
        seed=1,
        until=Stage.VERIFY,
        stages=[StageOutcome(stage=Stage.BUILD, seconds=1.0, error="boom")],
        finished=True,
    )
    monkeypatch.setattr(cli, "run_target", lambda *_: failed)

    result = CliRunner().invoke(
        cli.app,
        ["evomaster", "run", "--target", "hertzbeat", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert '"error": "boom"' in result.stdout


def test_cli_run_resumes_a_recorded_run_with_its_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    given: list[RunSettings] = []

    def run_target(*arguments: object) -> TargetRun:
        settings = arguments[-1]
        assert isinstance(settings, RunSettings)
        given.append(settings)
        return TargetRun(
            target="hertzbeat",
            project="hertzbeat",
            max_time="1h",
            seed=1234,
            until=Stage.VERIFY,
            finished=True,
        )

    monkeypatch.setattr(cli, "run_target", run_target)

    result = CliRunner().invoke(
        cli.app,
        [
            "evomaster",
            "run",
            "--target",
            "hertzbeat",
            "--output-dir",
            str(tmp_path),
            "--cpus",
            "6",
            "--resume-from",
            "verify",
        ],
    )

    assert result.exit_code == 0
    assert [(s.resume_from, s.cpus) for s in given] == [(Stage.VERIFY, 6)]


def test_cli_stage_skips_incomplete_targets_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, several_targets: None
) -> None:
    _record(tmp_path / "out", target="completed")
    _record(tmp_path / "out", target="failed", error="boom")
    staged: list[str] = []

    def stage(target, *_args):  # type: ignore[no-untyped-def]
        staged.append(target.name)
        return tmp_path / target.name

    monkeypatch.setattr(cli, "stage_generated_suite", stage)

    result = CliRunner().invoke(
        cli.app,
        [
            "evomaster",
            "stage",
            "--staged-root",
            str(tmp_path / "staged"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0
    assert staged == ["completed"]
    assert "skipped failed: failed" in result.output
    assert "skipped untried: not-attempted" in result.output


def test_cli_stage_continues_past_a_failing_target_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, several_targets: None
) -> None:
    staged: list[str] = []

    def stage(target, *_args):  # type: ignore[no-untyped-def]
        staged.append(target.name)
        if target.name == "failed":
            raise ExecutionError("boom")
        return tmp_path / target.name

    monkeypatch.setattr(cli, "stage_generated_suite", stage)

    result = CliRunner().invoke(
        cli.app,
        [
            "evomaster",
            "stage",
            "--staged-root",
            str(tmp_path / "staged"),
            "--target",
            "failed",
            "--target",
            "completed",
        ],
    )

    assert result.exit_code == 1
    assert staged == ["failed", "completed"]


def test_a_failure_under_another_budget_reads_as_other_budget(tmp_path: Path) -> None:
    _record(tmp_path, max_time="5m", error="boom")

    rows = {row.target: row for row in funnel(tmp_path, 1234, "1h")}

    assert rows["hertzbeat"].status is TargetStatus.OTHER_BUDGET


def test_staging_refuses_a_run_of_another_budget(tmp_path: Path) -> None:
    _record(tmp_path / "out", max_time="5m")

    with pytest.raises(ExecutionError, match="other-budget"):
        stage_generated_suite(
            _TARGET,
            SuiteSources(tmp_path, tmp_path),
            _runs(tmp_path, "1h"),
            tmp_path / "staged",
        )


def test_every_generated_test_is_a_local_api_test(tmp_path: Path) -> None:
    _write_suite(tmp_path / "out")
    _record(tmp_path / "out")

    analysis = generated_api_tests(_TARGET, _runs(tmp_path))

    assert analysis.commit == "abc123"
    assert [
        (test.class_name, test.method_signature) for test in analysis.api_tests
    ] == [
        ("em.generated.EvoMaster_Test", "test_0_getOnUsers()"),
        ("em.generated.EvoMaster_Test", "test_1_postOnUsers()"),
    ]
    assert all(test.in_scope for test in analysis.api_tests)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _files(tree: Path) -> dict[str, bytes]:
    return {
        path.relative_to(tree).as_posix(): path.read_bytes()
        for path in tree.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(tree).parts
    }


@pytest.fixture
def source_commit(sources: SuiteSources, tmp_path: Path) -> str:
    """A hertzbeat repository whose test clearing is Codex's run's patch."""
    repo = sources.source_repo(_TARGET.project)
    module = repo / "hertzbeat-manager"
    (module / "src/test/java/old").mkdir(parents=True)
    (module / "src/test/java/old/OldTest.java").write_text("class OldTest {}\n")
    (module / "src/test/resources").mkdir(parents=True)
    (module / "src/test/resources/fixture.yml").write_text("fixture: true\n")
    (module / "pom.xml").write_text("<project/>\n")
    (repo / ".gitignore").write_text("*.local\n")
    _git(repo.parent, "init", "--quiet", repo.name)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "base")
    _git(repo, "rm", "--quiet", "-r", "hertzbeat-manager/src/test")
    clearing = sources.test_clearing_patch(_TARGET.project)
    clearing.parent.mkdir(parents=True, exist_ok=True)
    clearing.write_text(_git(repo, "diff", "--cached", "--binary"))
    _git(repo, "reset", "--quiet", "--hard")
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prepare as the pipeline does: clear, restore a cleared path, add the harness."""

    def prepare_tree(
        target: EvoMasterTarget, sources: SuiteSources, workspace: Path
    ) -> None:
        apply_patches(workspace, [sources.test_clearing_patch(target.project)])
        restore_workspace_paths(workspace, ["hertzbeat-manager/src/test/resources"])
        module = workspace / "hertzbeat-manager"
        driver = module / "src/test/java/em/embedded/Driver.java"
        driver.parent.mkdir(parents=True)
        driver.write_text("class Driver {}\n")
        (module / "pom.xml").write_text("<project><evomaster/></project>\n")
        (workspace / "driver.local").write_text("ignored by the project\n")

    monkeypatch.setattr(staging, "prepare_tree", prepare_tree)


def test_export_rebuilds_the_analyzed_tree_from_the_agents_starting_point(
    sources: SuiteSources, tmp_path: Path, source_commit: str, harness: None
) -> None:
    _write_suite(tmp_path / "out")
    _record(tmp_path / "out", commit=source_commit)
    staged = _stage(sources, tmp_path)

    exported = export_generated_suite(
        _TARGET, sources, _runs(tmp_path), tmp_path / "export"
    )

    rebuilt = tmp_path / "rebuilt"
    _git(
        tmp_path,
        "clone",
        "--quiet",
        str(sources.source_repo(_TARGET.project)),
        "rebuilt",
    )
    _git(rebuilt, "checkout", "--quiet", source_commit)
    for patch in (
        sources.test_clearing_patch(_TARGET.project),
        exported / EXPORTED_PATCH,
    ):
        _git(rebuilt, "apply", str(patch))
    analyzed = _files(staged)
    assert _files(rebuilt) == analyzed
    assert "hertzbeat-manager/src/test/resources/fixture.yml" in analyzed
    assert "driver.local" in analyzed


def test_export_keeps_the_runs_record_and_statistics_beside_the_diff(
    sources: SuiteSources, tmp_path: Path, source_commit: str, harness: None
) -> None:
    _write_suite(tmp_path / "out")
    _record(tmp_path / "out", commit=source_commit, max_time="30m")

    exported = export_generated_suite(
        _TARGET, sources, _runs(tmp_path), tmp_path / "export"
    )

    assert exported == tmp_path / "export" / _TARGET.project.name
    paths = RunPaths.of(tmp_path / "out", _TARGET, 1234)
    assert (exported / EXPORTED_RUN).read_bytes() == paths.record.read_bytes()
    for statistics in (paths.statistics, paths.snapshot):
        assert (exported / statistics.name).read_bytes() == statistics.read_bytes()
    assert sorted(path.name for path in exported.rglob("*") if path.is_file()) == [
        EXPORTED_PATCH.name,
        EXPORTED_RUN,
        "snapshot.csv",
        "statistics.csv",
    ]


def test_export_refuses_a_suite_whose_run_did_not_complete(
    sources: SuiteSources, tmp_path: Path
) -> None:
    _write_suite(tmp_path / "out")
    _record(tmp_path / "out", error="boom")

    with pytest.raises(ExecutionError, match="failed"):
        export_generated_suite(_TARGET, sources, _runs(tmp_path), tmp_path / "export")

    assert not (tmp_path / "export").exists()


def test_cli_export_skips_incomplete_targets_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, several_targets: None
) -> None:
    _record(tmp_path / "out", target="completed")
    _record(tmp_path / "out", target="failed", error="boom")
    exported: list[tuple[str, Path]] = []

    def export(target, _inputs, _runs, export_root):  # type: ignore[no-untyped-def]
        exported.append((target.name, export_root))
        return export_root / target.name

    monkeypatch.setattr(cli, "export_generated_suite", export)

    result = CliRunner().invoke(
        cli.app,
        [
            "evomaster",
            "export",
            "--export-root",
            str(tmp_path / "export"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0
    assert exported == [("completed", tmp_path / "export")]
    assert "skipped failed: failed" in result.output
