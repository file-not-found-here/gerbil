from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gerbil import cli
from gerbil.evomaster.pipeline import RunPaths, Stage, StageOutcome, TargetRun
from gerbil.evomaster.targets import target_for
from gerbil.execution import bundle
from gerbil.execution.bundle import (
    MANIFEST,
    ExecutionInputs,
    build_bundle,
    derive_clearing,
    tracked_patch,
)
from gerbil.execution.errors import ExecutionError
from gerbil.execution.projects import MEASURED_PROJECTS, PROJECTS, Suite
from gerbil.execution.sources import EvoMasterRuns, SuiteSources
from gerbil.execution.workspace import materialize_workspace

_PROJECT = PROJECTS["hertzbeat"]
_TARGET = target_for(_PROJECT)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _analysis(path: Path, commit: str, class_name: str) -> None:
    _write(
        path,
        json.dumps(
            {
                "metadata": {"git_commit_hash": commit},
                "test_class_analyses": [
                    {
                        "qualified_class_name": class_name,
                        "test_method_analyses": [
                            {
                                "identity": {"method_signature": "creates(String)"},
                                "is_api_test": True,
                                "http": {
                                    "request_dispatch": {"labels": ["in-process"]}
                                },
                            }
                        ],
                    }
                ],
            }
        ),
    )


class _World:
    """A pinned hertzbeat commit with every suite's untracked and committed inputs."""

    def __init__(self, root: Path) -> None:
        self.outputs = root / "outputs"
        self.repos = root / "repos"
        self.runs = EvoMasterRuns(root / "evomaster", 1234, "1h")
        self.seed = root / "seed"
        self.bundle = root / "bundle"
        self.sources = SuiteSources(self.outputs, self.repos)
        repo = self.repos / _PROJECT.name
        _write(repo / "manager/src/test/java/OldTest.java", "class OldTest {}\n")
        _write(repo / "manager/src/test/java/[odd] Test.java", "class Odd {}\n")
        _write(repo / "manager/pom.xml", "<project/>\n")
        _git(repo, "init", "--quiet")
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "t")
        _git(repo, "add", "-A")
        _git(repo, "commit", "--quiet", "-m", "pinned")
        self.commit = _git(repo, "rev-parse", "HEAD").strip()
        _git(repo, "rm", "--quiet", "-r", "manager/src/test")
        clearing = _git(repo, "diff", "--cached", "--binary")
        _git(repo, "commit", "--quiet", "-m", "cleared")
        for suite, test in (
            (Suite.CLAUDE_CODE, "manager/src/test/java/ClaudeTest.java"),
            (Suite.CODEX, "manager/src/test/java/CodexTest.java"),
            (Suite.EVOMASTER, "manager/src/test/java/em/generated/EmTest.java"),
        ):
            _write(repo / test, "class Generated {}\n")
            _git(repo, "add", "-A")
            diff = _git(repo, "diff", "--cached", "--binary")
            _git(repo, "reset", "--quiet", "--hard")
            _write(tracked_patch(self.outputs, _PROJECT, suite), diff)
            if suite is not Suite.EVOMASTER:
                run = (
                    self.outputs / f"{suite.value}-raw" / f"x__{suite.value}__hertzbeat"
                )
                _write(run / "output/test_clearing.patch", clearing)
                _write(run / "output/git_diff.patch", diff)
                _analysis(
                    self.outputs / f"{suite.value}-gerbil/hertzbeat/gerbil.json",
                    self.commit,
                    f"org.{suite.name.lower()}.ApiTest",
                )
        _git(repo, "checkout", "--quiet", self.commit)
        _analysis(
            self.outputs / "dev-full-gerbil/apache_hertzbeat/gerbil.json",
            self.commit,
            "org.dev.ApiTest",
        )
        self.record()
        _write(
            RunPaths.of(self.runs.output_dir, _TARGET, 1234).generated
            / "em/generated/EmTest.java",
            "class EmTest {\n    @Test\n    public void test_0_getOnUsers() {}\n}\n",
        )
        _write(self.seed / "org/kie/kie-api/1/kie-api-1.jar", "jar")

    def record(self, commit: str | None = None) -> None:
        run = TargetRun(
            target=_TARGET.name,
            project=_PROJECT.name,
            max_time="1h",
            seed=1234,
            until=Stage.VERIFY,
            commit=commit or self.commit,
            stages=[StageOutcome(stage=Stage.VERIFY, seconds=1.0)],
            finished=True,
        )
        record = RunPaths.of(self.runs.output_dir, _TARGET, 1234).record
        text = run.model_dump_json()
        _write(record, text)
        _write(
            tracked_patch(self.outputs, _PROJECT, Suite.EVOMASTER).parents[1]
            / "run.json",
            text,
        )

    def build(self) -> bundle.Manifest:
        return build_bundle(
            self.sources, self.runs, self.seed, self.bundle, projects=[_PROJECT]
        )

    def inputs(self) -> ExecutionInputs:
        return ExecutionInputs.load(self.bundle, self.outputs, self.repos)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _World:
    monkeypatch.setattr(bundle, "gerbil_revision", lambda: "gerbil-commit")
    return _World(tmp_path)


def test_bundle_records_each_suites_tests_and_the_clearing(world: _World) -> None:
    manifest = world.build()

    entry = manifest.projects["hertzbeat"]
    assert entry.commit == world.commit
    assert {
        suite: suite_entry.api_tests for suite, suite_entry in entry.suites.items()
    } == {suite: 1 for suite in Suite}
    assert entry.suites[Suite.DEVELOPER].patch_sha256 is None
    assert manifest.gerbil_commit == "gerbil-commit"
    assert (manifest.evomaster.seed, manifest.evomaster.max_time) == (1234, "1h")
    clearing = json.loads((world.bundle / "hertzbeat/clearing.json").read_text())
    assert clearing["paths"] == [
        "manager/src/test/java/OldTest.java",
        "manager/src/test/java/[odd] Test.java",
    ]
    assert (world.bundle / "maven-seed/org/kie/kie-api/1/kie-api-1.jar").is_file()
    assert not world.bundle.with_name("bundle.partial").exists()
    inputs = world.inputs()
    (generated,) = inputs.selection(_PROJECT, Suite.EVOMASTER).api_tests
    assert (generated.class_name, generated.method_signature, generated.in_scope) == (
        "em.generated.EmTest",
        "test_0_getOnUsers()",
        True,
    )
    (developer,) = inputs.selection(_PROJECT, Suite.DEVELOPER).api_tests
    assert developer.class_name == "org.dev.ApiTest"
    assert inputs.problems([_PROJECT]) == []


@pytest.mark.parametrize("suite", [Suite.CODEX, Suite.EVOMASTER])
def test_a_suites_tree_is_rebuilt_from_the_bundle_and_its_committed_patch(
    world: _World, tmp_path: Path, suite: Suite
) -> None:
    world.build()
    tree = world.inputs().tree(_PROJECT, suite)
    workspace = tmp_path / "workspace"

    materialize_workspace(
        world.repos / "hertzbeat",
        tree.commit,
        workspace,
        clearing=tree.clearing,
        patches=tree.patches,
    )

    tests = sorted(
        p.name for p in (workspace / "manager/src/test/java").rglob("*.java")
    )
    expected = {Suite.CODEX: ["CodexTest.java"], Suite.EVOMASTER: ["EmTest.java"]}
    assert tests == expected[suite]
    assert (workspace / "manager/pom.xml").is_file()


def test_the_developers_tree_is_the_pinned_commit(world: _World) -> None:
    world.build()

    tree = world.inputs().tree(_PROJECT, Suite.DEVELOPER)

    assert (tree.commit, tree.clearing, tree.patches) == (world.commit, None, ())


def test_a_changed_committed_patch_is_refused(world: _World) -> None:
    world.build()
    patch = tracked_patch(world.outputs, _PROJECT, Suite.CODEX)
    patch.write_text(patch.read_text() + "\n")

    with pytest.raises(ExecutionError, match="differs from the bundle's manifest"):
        world.inputs().tree(_PROJECT, Suite.CODEX)
    assert world.inputs().problems([_PROJECT]) == [
        f"{patch} differs from the bundle's manifest"
    ]


def test_problems_name_a_missing_project_and_a_changed_seed(world: _World) -> None:
    world.build()
    (world.bundle / "maven-seed/org/kie/kie-api/1/kie-api-1.jar").write_text("other")

    problems = world.inputs().problems([_PROJECT, PROJECTS["uaa"]])

    assert problems == [
        "the bundle has no uaa",
        f"{world.bundle / 'maven-seed'} differs from the bundle's manifest",
    ]


def test_a_missing_source_repository_is_named_with_its_fix(world: _World) -> None:
    world.build()
    inputs = ExecutionInputs.load(world.bundle, world.outputs, world.repos / "absent")

    (problem,) = inputs.problems([_PROJECT])

    assert problem.startswith(f"{world.repos / 'absent' / 'hertzbeat'} lacks commit")
    assert problem.endswith("git submodule update --init absent/hertzbeat")


def test_bundle_refuses_a_suite_of_another_commit(world: _World) -> None:
    world.record(commit="0" * 40)

    with pytest.raises(ExecutionError, match="evomaster suite is of commit 0{40}"):
        world.build()

    assert not world.bundle.exists()


def test_bundle_refuses_a_committed_patch_other_than_the_runs(world: _World) -> None:
    tracked = tracked_patch(world.outputs, _PROJECT, Suite.CLAUDE_CODE)
    tracked.write_text(tracked.read_text() + "\n")

    with pytest.raises(ExecutionError, match="differs from the run's"):
        world.build()


def test_bundle_refuses_an_evomaster_export_of_another_record(world: _World) -> None:
    saved = tracked_patch(world.outputs, _PROJECT, Suite.EVOMASTER).parents[1]
    (saved / "run.json").write_text("{}")

    with pytest.raises(ExecutionError, match="run gerbil evomaster export again"):
        world.build()


def test_bundle_refuses_agents_starting_from_different_clearings(
    world: _World,
) -> None:
    run = world.outputs / "claude-code-raw/x__claude-code__hertzbeat/output"
    (run / "test_clearing.patch").write_text("other\n")

    with pytest.raises(ExecutionError, match="different clearings"):
        world.build()


def test_bundle_refuses_a_missing_maven_seed(world: _World, tmp_path: Path) -> None:
    with pytest.raises(ExecutionError, match="no Maven seed"):
        build_bundle(
            world.sources,
            world.runs,
            tmp_path / "absent",
            world.bundle,
            projects=[_PROJECT],
        )


def test_a_clearing_must_only_delete_files(world: _World, tmp_path: Path) -> None:
    repo = world.repos / "hertzbeat"
    (repo / "manager/pom.xml").write_text("<project><changed/></project>\n")
    patch = _write(tmp_path / "edit.patch", _git(repo, "diff", "--binary"))
    _git(repo, "checkout", "--quiet", "--", ".")

    with pytest.raises(ExecutionError, match="does more than delete files"):
        derive_clearing(repo, world.commit, patch)


def test_a_rebuilt_bundle_replaces_the_earlier_one(world: _World) -> None:
    world.build()
    (world.bundle / "stale").write_text("left over")

    world.build()

    assert not (world.bundle / "stale").exists()


def test_identity_follows_the_bundles_contents_only(world: _World) -> None:
    world.build()
    first = world.inputs().identity
    manifest = json.loads((world.bundle / MANIFEST).read_text())
    manifest["created"] = "later"
    manifest["gerbil_commit"] = "another"
    (world.bundle / MANIFEST).write_text(json.dumps(manifest))
    assert world.inputs().identity == first

    manifest["evomaster"]["seed"] = 1
    (world.bundle / MANIFEST).write_text(json.dumps(manifest))
    assert world.inputs().identity != first


def test_load_refuses_a_missing_or_newer_bundle(world: _World) -> None:
    with pytest.raises(ExecutionError, match="manifest.json is missing"):
        world.inputs()

    world.build()
    manifest = json.loads((world.bundle / MANIFEST).read_text())
    manifest["format"] = 99
    (world.bundle / MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(ExecutionError, match="format 99 bundle"):
        world.inputs()


def _bundle_arguments(world: _World) -> list[str]:
    return [
        "execution",
        "bundle",
        "--output",
        str(world.bundle),
        "--outputs-root",
        str(world.outputs),
        "--repos-root",
        str(world.repos),
        "--evomaster-dir",
        str(world.runs.output_dir),
        "--maven-seed",
        str(world.seed),
    ]


def test_cli_bundles_every_measured_project(world: _World) -> None:
    built = CliRunner().invoke(cli.app, _bundle_arguments(world))

    assert built.exit_code == 1
    assert "apache_dolphinscheduler/gerbil.json is missing" in built.output
    assert not world.bundle.exists()


def test_cli_bundles_zips_then_checks_a_project(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:

    def build_hertzbeat(
        sources: SuiteSources,
        runs: EvoMasterRuns,
        maven_seed: Path | None,
        destination: Path,
    ) -> bundle.Manifest:
        return build_bundle(sources, runs, maven_seed, destination, projects=[_PROJECT])

    monkeypatch.setattr(cli, "build_bundle", build_hertzbeat)
    runner = CliRunner()
    built = runner.invoke(cli.app, [*_bundle_arguments(world), "--part-mb", "1"])

    assert built.exit_code == 0, built.output
    assert f"zip: {world.bundle}-" in built.output
    assert "of1.zip (" in built.output or "send all" in built.output
    checked = runner.invoke(
        cli.app,
        [
            "execution",
            "check",
            "--project",
            "hertzbeat",
            "--bundle",
            str(world.bundle),
            "--outputs-root",
            str(world.outputs),
            "--repos-root",
            str(world.repos),
        ],
    )
    assert checked.exit_code == 0, checked.output
    assert f"inputs {world.inputs().identity}\n" in checked.output
    assert checked.output.endswith("inputs ready\n")


def test_cli_check_reports_every_project_missing_from_the_bundle(
    world: _World,
) -> None:
    world.build()

    checked = CliRunner().invoke(
        cli.app,
        [
            "execution",
            "check",
            "--bundle",
            str(world.bundle),
            "--outputs-root",
            str(world.outputs),
            "--repos-root",
            str(world.repos),
        ],
    )

    assert checked.exit_code == 1
    others = [name for name in MEASURED_PROJECTS if name != "hertzbeat"]
    assert f"the bundle has no {', '.join(others)}" in checked.output


def test_cbioportal_is_not_measured() -> None:
    assert "cbioportal" in PROJECTS
    assert "cbioportal" not in MEASURED_PROJECTS
    assert len(MEASURED_PROJECTS) == 9


def test_bundle_refuses_to_replace_a_directory_that_is_no_bundle(
    world: _World,
) -> None:
    (world.bundle / "notes.txt").parent.mkdir(parents=True)
    (world.bundle / "notes.txt").write_text("keep me")

    with pytest.raises(ExecutionError, match="exists and is not a bundle"):
        world.build()

    assert (world.bundle / "notes.txt").read_text() == "keep me"


def test_problems_name_a_git_too_old_to_clear(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.build()
    monkeypatch.setattr(bundle, "git_version", lambda: (2, 25))

    assert world.inputs().problems([_PROJECT]) == [
        "git 2.25 is older than 2.26, which clearing needs"
    ]


def test_a_seed_checked_out_from_git_matches_the_manifest(world: _World) -> None:
    world.build()
    seed = world.bundle / "maven-seed"
    _git(seed.parent, "init", "--quiet", seed.name)
    _write(seed / ".git" / "extra", "git's own files")

    assert world.inputs().maven_seed == seed
    assert world.inputs().problems([_PROJECT]) == []
