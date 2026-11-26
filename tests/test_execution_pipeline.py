from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from gerbil import cli
from gerbil.execution import pipeline
from gerbil.execution.build import ModuleClasspath
from gerbil.execution.errors import ExecutionError
from gerbil.execution.launcher import TestStatus
from gerbil.execution.pipeline import (
    SuiteCoverage,
    SuitePaths,
    SuiteTests,
    TestRecord,
)
from gerbil.evomaster.targets import TARGETS
from gerbil.execution.bundle import ExecutionInputs, SuiteTree
from gerbil.execution.coverage import CoverageReport
from gerbil.execution.disk import FreeSpace
from gerbil.execution.mutation import MutantRecord, PitMutant, PitRun, PitSettings
from gerbil.execution.projects import PROJECTS, BuildTool, ExecutionProject, Suite
from gerbil.execution.sampling import sample_digest, sample_mutants
from gerbil.execution.selection import ApiTest, SuiteAnalysis, load_api_tests
from gerbil.execution.workers import WorkerPool
from gerbil.execution.workspace import Clearing, write_atomically
from tests.execution_fakes import Effect, RecordingContainer

_PROJECT = ExecutionProject("demo", "org_demo", BuildTool.MAVEN, 17)
_SETTINGS = PitSettings(threads=2, timeout_seconds=60)
_MUTATION = pipeline.MutationSettings(
    pit=_SETTINGS, workers=4, sample_size=10, sample_seed="seed", min_free_gb=0
)
_SPACE = FreeSpace(Path("."), 0)
_PIT_RUN = PitRun(module="api", threads=2, seconds=1.0, peak_memory_bytes=None)
_TARGET = replace(TARGETS["hertzbeat"], module="svc")


def _source(workspace: Path, relative: str) -> None:
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("class X {}")


def _analysis(path: Path, methods: dict[str, list[tuple[str, list[str]]]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "metadata": {"git_commit_hash": "abc"},
                "test_class_analyses": [
                    {
                        "qualified_class_name": class_name,
                        "test_method_analyses": [
                            {
                                "identity": {"method_signature": signature},
                                "is_api_test": True,
                                "http": {"request_dispatch": {"labels": labels}},
                            }
                            for signature, labels in tests
                        ],
                    }
                    for class_name, tests in methods.items()
                ],
            }
        )
    )
    return path


def test_plan_suite_assigns_each_api_test_a_starting_status(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _source(workspace, "api/src/test/java/org/demo/UserApiTest.java")
    _source(workspace, "tools/src/test/java/org/demo/ToolApiTest.java")
    analysis = _analysis(
        tmp_path / "gerbil.json",
        {
            "org.demo.UserApiTest.Nested": [("creates(String)", ["local-network"])],
            "org.demo.UserApiTest": [("remote()", ["in-process", "remote-network"])],
            "org.demo.ToolApiTest": [("lists()", ["in-process"])],
            "org.demo.GoneTest": [("missing()", ["in-process"])],
        },
    )

    plan = pipeline._plan_suite(
        _PROJECT, load_api_tests(analysis), workspace, "abc", {".", "api"}
    )

    statuses = {
        (record.class_name, record.status, record.module, record.binary_class_name)
        for record in plan.tests
    }
    assert statuses == {
        (
            "org.demo.UserApiTest.Nested",
            TestStatus.NOT_RUN,
            "api",
            "org.demo.UserApiTest$Nested",
        ),
        ("org.demo.UserApiTest", TestStatus.OUT_OF_SCOPE, None, None),
        ("org.demo.ToolApiTest", TestStatus.NOT_IN_BUILD, None, None),
        ("org.demo.GoneTest", TestStatus.NOT_IN_BUILD, None, None),
    }


def _record(method: str) -> TestRecord:
    return TestRecord(
        class_name="org.demo.ApiTest",
        method_signature=f"{method}()",
        dispatch=["in-process"],
        module="api",
        binary_class_name="org.demo.ApiTest",
        status=TestStatus.NOT_RUN,
    )


_REPORT = """<report name="r">
<package name="org/demo"><class name="org/demo/Service">
<counter type="LINE" missed="1" covered="3"/></class></package>
<counter type="LINE" missed="1" covered="3"/></report>"""


def _fake_build_and_tests(
    failing_by_run: list[set[str]],
) -> tuple[Effect, list[list[str]]]:
    """Simulate Maven, GerbilLauncher, and JaCoCo; run N fails `failing_by_run[N]`."""
    runs: list[list[str]] = []

    def effect(
        container: RecordingContainer, command: list[str], workdir: PurePosixPath
    ) -> None:
        if command[0] == "mvn":
            for module in ("api", "core", "fixtures"):
                classes = container.workspace / module / "target" / "classes"
                classes.mkdir(parents=True, exist_ok=True)
            target = container.workspace / "api" / "target"
            (target / "gerbil-classpath.txt").write_text(
                "/workspace/core/target/classes:/workspace/fixtures/target/classes:"
                "/root/.m2/junit-platform-launcher-1.10.0.jar:"
                "/root/.m2/junit-platform-engine-1.10.0.jar"
            )
            (target / "gerbil-dependencies.txt").write_text(
                "   org.demo:core:jar:1:compile:/workspace/core/target/classes\n"
                "   org.demo:fixtures:jar:1:test:/workspace/fixtures/target/classes\n"
            )
        elif command[0] == "java" and command[1].startswith("@"):
            run_directory = container.host_path(PurePosixPath(command[1][1:])).parent
            selected = (run_directory / "selection.tsv").read_text().splitlines()
            failing = failing_by_run[len(runs)]
            runs.append([line.split("\t")[1] for line in selected])
            (run_directory / "results.tsv").write_text(
                "".join(
                    ("FAILED" if line.split("\t")[1] in failing else "SUCCESSFUL")
                    + f"\t{line}\n"
                    for line in selected
                )
            )
        elif command[:2] == ["java", "-jar"]:
            container.host_path(PurePosixPath(command[-1])).write_text(_REPORT)

    return effect, runs


def _recording_containers(
    monkeypatch: pytest.MonkeyPatch, effect: Effect | None = None
) -> list[RecordingContainer]:
    containers: list[RecordingContainer] = []

    def container_factory(
        workspace: Path, output: Path, jdk: int
    ) -> RecordingContainer:
        containers.append(RecordingContainer(workspace, output, jdk, effect=effect))
        return containers[-1]

    monkeypatch.setattr(pipeline, "Container", container_factory)
    return containers


@pytest.mark.parametrize(
    ("failing_by_run", "expected_runs", "statuses", "exec_dir"),
    [
        ([set()], [["a", "b", "c"]], "PPP", "baseline"),
        ([{"b"}, set()], [["a", "b", "c"], ["a", "c"]], "PFP", "coverage"),
        # A test that passes the baseline but fails the re-measurement is dropped.
        ([{"b"}, {"c"}], [["a", "b", "c"], ["a", "c"]], "PFF", "coverage"),
    ],
)
def test_suite_coverage_measures_passing_tests_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_by_run: list[set[str]],
    expected_runs: list[list[str]],
    statuses: str,
    exec_dir: str,
) -> None:
    effect, runs = _fake_build_and_tests(failing_by_run)
    containers = _recording_containers(monkeypatch, effect)
    paths = SuitePaths(tmp_path / "suite")
    paths.workspace.mkdir(parents=True)
    plan = SuiteTests(commit="abc", tests=[_record(m) for m in "abc"])

    pipeline._run_suite_coverage(_PROJECT, Suite.DEVELOPER, paths, plan, ["api"], "api")

    assert runs == expected_runs
    expected = {
        method: TestStatus.PASSED if status == "P" else TestStatus.FAILED
        for method, status in zip("abc", statuses)
    }
    tests = SuiteTests.model_validate_json(paths.tests.read_text())
    assert {record.key[1]: record.status for record in tests.tests} == expected
    coverage = SuiteCoverage.model_validate_json(paths.coverage.read_text())
    assert coverage.measured_tests == [
        f"org.demo.ApiTest#{method}"
        for method, status in expected.items()
        if status is TestStatus.PASSED
    ]
    assert coverage.report.covered_classes == ["org.demo.Service"]
    (report_command,) = [
        command
        for command, _ in containers[0].commands
        if command[:2] == ["java", "-jar"]
    ]
    assert f"/out/runs/api/{exec_dir}/jacoco.exec" in report_command
    # Coverage is of the service's production classes, not its tests' fixtures.
    classfiles = [
        report_command[index + 1]
        for index, argument in enumerate(report_command)
        if argument == "--classfiles"
    ]
    assert classfiles == [
        "/workspace/api/target/classes",
        "/workspace/core/target/classes",
    ]
    assert all(container.network is None for container in containers)


def test_evomaster_tests_run_offline_with_their_targets_jvm_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    effect, _ = _fake_build_and_tests([{"b"}, set()])
    networks: list[tuple[str, str | None, str | None]] = []

    def recording(
        container: RecordingContainer, command: list[str], workdir: PurePosixPath
    ) -> None:
        step = command[1] if command[0] == "java" else command[0]
        networks.append((step, container.network, container.hostname))
        effect(container, command, workdir)

    _recording_containers(monkeypatch, recording)
    target = replace(TARGETS["hertzbeat"], jvm_args=("-Dtarget=1",))
    monkeypatch.setattr(pipeline, "target_for", lambda _: target)
    project = replace(_PROJECT, jvm_args=("-Dproject=1",))
    paths = SuitePaths(tmp_path / "evomaster")
    paths.workspace.mkdir(parents=True)
    plan = SuiteTests(commit="abc", tests=[_record("a"), _record("b")])

    pipeline._run_suite_coverage(project, Suite.EVOMASTER, paths, plan, ["api"], "api")

    assert networks == [
        ("mvn", None, None),
        ("@/out/runs/api/baseline/java.args", "none", "localhost"),
        ("@/out/runs/api/coverage/java.args", "none", "localhost"),
        ("-jar", None, None),
    ]
    for run in ("baseline", "coverage"):
        arguments = (paths.root / "runs/api" / run / "java.args").read_text()
        assert "-Dproject=1" in arguments and "-Dtarget=1" in arguments
        assert '"-Xmx16g"' in arguments
        assert '"-XX:+StartAttachListener"' in arguments


def test_mutation_requires_coverage_for_the_suites_it_runs(tmp_path: Path) -> None:
    paths = SuitePaths.of(tmp_path, _PROJECT, Suite.DEVELOPER)
    paths.root.mkdir(parents=True)

    with pytest.raises(ExecutionError, match="run coverage for developer first"):
        pipeline.run_mutation(_PROJECT, tmp_path, [Suite.DEVELOPER], _MUTATION)


class _FakeInputs:
    """A bundle's selections, with each suite's tests in a module of its own."""

    def __init__(self, maven_seed: Path | None = None) -> None:
        self.maven_seed = maven_seed

    def entry(self, _project: ExecutionProject) -> SimpleNamespace:
        return SimpleNamespace(commit="abc")

    def selection(self, _project: ExecutionProject, suite: Suite) -> SuiteAnalysis:
        class_name = {
            Suite.DEVELOPER: "org.dev.DevTest",
            Suite.CLAUDE_CODE: "org.cc.CcTest",
            Suite.CODEX: "org.cx.CxTest",
            Suite.EVOMASTER: "em.generated.EmTest",
        }[suite]
        return SuiteAnalysis(
            "abc", (ApiTest(class_name, "test_0()", ("local-network",)),)
        )


def _suite_inputs(monkeypatch: pytest.MonkeyPatch) -> list[Suite]:
    """Every suite's workspace, recording which were materialized."""
    materialized: list[Suite] = []

    def materialize(
        project: ExecutionProject, suite: Suite, _inputs: object, paths: SuitePaths
    ) -> None:
        materialized.append(suite)
        _source(paths.workspace, "dev/src/test/java/org/dev/DevTest.java")
        _source(paths.workspace, "cc/src/test/java/org/cc/CcTest.java")
        _source(paths.workspace, "em/src/test/java/em/generated/EmTest.java")

    monkeypatch.setattr(pipeline, "build_image", lambda: None)
    monkeypatch.setattr(pipeline, "_materialize_suite", materialize)
    monkeypatch.setattr(pipeline, "target_for", lambda _: _TARGET)
    monkeypatch.setattr(
        pipeline, "list_build_modules", lambda *_: {".", "dev", "cc", "em", "svc"}
    )
    return materialized


def _inputs(fake: _FakeInputs) -> ExecutionInputs:
    return cast(ExecutionInputs, fake)


def test_run_coverage_compiles_every_suites_modules_and_isolates_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialized = _suite_inputs(monkeypatch)
    output_dir = tmp_path / "execution"
    ran: list[tuple[Suite, list[str], str]] = []
    plans: dict[Suite, SuiteTests] = {}

    def run_suite(
        project: ExecutionProject,
        suite: Suite,
        paths: SuitePaths,
        plan: SuiteTests,
        modules: Sequence[str],
        service_module: str,
    ) -> None:
        ran.append((suite, list(modules), service_module))
        plans[suite] = plan
        if suite is Suite.CODEX:
            raise ExecutionError("compilation failed")

    guarded: list[Path] = []
    monkeypatch.setattr(pipeline, "require_case_sensitive_mount", guarded.append)
    monkeypatch.setattr(pipeline, "_run_suite_coverage", run_suite)
    stale = SuitePaths.of(output_dir, _PROJECT, Suite.CODEX)
    stale.root.mkdir(parents=True)
    stale.mutation.write_text("{}")
    (stale.root / "mutation-300.json").write_text("{}")
    # Not re-run here, so its PIT targets stand.
    developer = SuitePaths.of(output_dir, _PROJECT, Suite.DEVELOPER)
    developer.root.mkdir(parents=True)
    developer.mutation.write_text("{}")
    developer.error("mutation").write_text("old PIT failure")

    failures = pipeline.run_coverage(
        _PROJECT,
        _inputs(_FakeInputs()),
        output_dir,
        [Suite.CLAUDE_CODE, Suite.CODEX, Suite.EVOMASTER],
        0,
    )

    assert guarded == [output_dir]
    # Every suite's modules count, so even suites not re-run are materialized.
    assert materialized == list(Suite)
    # The service's module holds no test but is built for its production classpath.
    modules = ["cc", "dev", "em", "svc"]
    assert ran == [
        (Suite.CLAUDE_CODE, modules, "svc"),
        (Suite.CODEX, modules, "svc"),
        (Suite.EVOMASTER, modules, "svc"),
    ]
    assert failures == {Suite.CODEX: "compilation failed"}
    (generated,) = plans[Suite.EVOMASTER].tests
    assert (generated.module, generated.binary_class_name, generated.status) == (
        "em",
        "em.generated.EmTest",
        TestStatus.NOT_RUN,
    )
    assert stale.error("coverage").read_text() == "compilation failed"
    assert not stale.mutation.exists()
    assert not (stale.root / "mutation-300.json").exists()
    assert developer.mutation.exists()
    assert developer.error("mutation").exists()


def test_run_coverage_seeds_mavens_cache_before_building(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _suite_inputs(monkeypatch)
    events: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "require_case_sensitive_mount",
        lambda directory: directory.mkdir(parents=True),
    )
    monkeypatch.setattr(
        pipeline, "seed_maven_repository", lambda seed: events.append(f"seed {seed}")
    )

    def list_build_modules(*_: object) -> set[str]:
        events.append("build")
        return {".", "svc"}

    monkeypatch.setattr(pipeline, "list_build_modules", list_build_modules)
    monkeypatch.setattr(pipeline, "_run_suite_coverage", lambda *_: None)

    pipeline.run_coverage(
        _PROJECT, _inputs(_FakeInputs(tmp_path / "seed")), tmp_path / "out", [], 0
    )

    assert events == [f"seed {tmp_path / 'seed'}", "build"]


class _TreeInputs:
    def __init__(self, tree: SuiteTree) -> None:
        self.current = tree

    def tree(self, _project: ExecutionProject, _suite: Suite) -> SuiteTree:
        return self.current

    def source_repo(self, project: ExecutionProject) -> Path:
        return Path("repos") / project.name


def _materializations(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, ...]]:
    """Fake checkouts that record what they were built from, then the build setup."""
    built: list[tuple[object, ...]] = []

    def materialize(
        repo: Path,
        commit: str,
        destination: Path,
        *,
        clearing: Clearing | None,
        patches: Sequence[Path],
    ) -> None:
        built.append(("checkout", repo, commit, clearing, tuple(patches)))
        destination.mkdir(parents=True)
        (destination / "Tree.java").write_text(commit)

    monkeypatch.setattr(pipeline, "materialize_workspace", materialize)
    monkeypatch.setattr(
        pipeline,
        "apply_workspace_copies",
        lambda workspace, copies: built.append(("copies", tuple(copies))),
    )
    monkeypatch.setattr(
        pipeline,
        "restore_workspace_paths",
        lambda workspace, paths: built.append(("restores", tuple(paths))),
    )
    return built


_TREE = SuiteTree(
    "abc",
    Clearing(("Old.java",), "tree1"),
    (Path("git_diff.patch"),),
    ("patch1",),
    "selection1",
)


def _results(paths: SuitePaths) -> list[Path]:
    """The suite's results, as earlier coverage and mutation stages left them."""
    results = [
        paths.tests,
        paths.classpaths,
        paths.coverage,
        paths.error("coverage"),
        paths.mutation,
        paths.root / "mutation-300.json",
    ]
    for path in results:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    return results


def test_a_suites_tree_is_built_once_from_its_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _materializations(monkeypatch)
    project = replace(_PROJECT, workspace_restores=("data",))
    paths = SuitePaths(tmp_path / "codex")
    inputs = cast(ExecutionInputs, _TreeInputs(_TREE))

    pipeline._materialize_suite(project, Suite.CODEX, inputs, paths)
    (paths.workspace / "target").mkdir()
    results = _results(paths)

    pipeline._materialize_suite(project, Suite.CODEX, inputs, paths)
    assert all(path.exists() for path in results)
    assert built == [
        (
            "checkout",
            Path("repos/demo"),
            "abc",
            _TREE.clearing,
            (Path("git_diff.patch"),),
        ),
        ("copies", ()),
        ("restores", ("data",)),
    ]
    assert (paths.workspace / "target").is_dir()
    assert paths.workspace_inputs.read_text() == _TREE.digest(project)


@pytest.mark.parametrize(
    "changed",
    [
        replace(_TREE, patch_digests=("patch2",)),
        replace(_TREE, selection_sha256="selection2"),
        replace(_TREE, clearing=Clearing(("Old.java",), "tree2")),
    ],
    ids=["patch", "selection", "clearing"],
)
def test_a_suites_tree_is_rebuilt_when_its_inputs_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: SuiteTree
) -> None:
    built = _materializations(monkeypatch)
    paths = SuitePaths(tmp_path / "codex")
    inputs = _TreeInputs(_TREE)
    pipeline._materialize_suite(
        _PROJECT, Suite.CODEX, cast(ExecutionInputs, inputs), paths
    )
    (paths.workspace / "target").mkdir()
    results = _results(paths)
    inputs.current = changed

    pipeline._materialize_suite(
        _PROJECT, Suite.CODEX, cast(ExecutionInputs, inputs), paths
    )

    assert not any(path.exists() for path in results)
    assert len([step for step in built if step[0] == "checkout"]) == 2
    assert not (paths.workspace / "target").exists()
    assert paths.workspace_inputs.read_text() == changed.digest(_PROJECT)


def test_a_tree_left_unfinished_is_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _materializations(monkeypatch)
    paths = SuitePaths(tmp_path / "codex")
    (paths.workspace / "half").mkdir(parents=True)

    pipeline._materialize_suite(
        _PROJECT, Suite.CODEX, cast(ExecutionInputs, _TreeInputs(_TREE)), paths
    )

    assert built[0][0] == "checkout"
    assert not (paths.workspace / "half").exists()


def test_a_failed_rebuild_leaves_no_results_of_the_old_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _materializations(monkeypatch)
    paths = SuitePaths(tmp_path / "codex")
    inputs = _TreeInputs(_TREE)
    pipeline._materialize_suite(
        _PROJECT, Suite.CODEX, cast(ExecutionInputs, inputs), paths
    )
    results = _results(paths)
    inputs.current = replace(_TREE, patch_digests=("patch2",))

    def fail(*_: object, **__: object) -> None:
        raise ExecutionError("git apply failed")

    monkeypatch.setattr(pipeline, "materialize_workspace", fail)
    with pytest.raises(ExecutionError, match="git apply failed"):
        pipeline._materialize_suite(
            _PROJECT, Suite.CODEX, cast(ExecutionInputs, inputs), paths
        )

    assert not any(path.exists() for path in results)
    assert not paths.workspace_inputs.exists()


def test_a_tree_digest_follows_the_projects_build_setup() -> None:
    copied = replace(_PROJECT, workspace_copies=(("a", "b"),))

    assert _TREE.digest(_PROJECT) != _TREE.digest(copied)
    assert _TREE.digest(_PROJECT) == _TREE.digest(_PROJECT)


def _write_coverage(
    output_dir: Path, suite: Suite, covered: list[str], heap: str = pipeline.HEAP
) -> SuitePaths:
    paths = SuitePaths.of(output_dir, _PROJECT, suite)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.coverage.write_text(
        SuiteCoverage(
            heap=heap,
            measured_tests=[],
            report=CoverageReport(counters={}, covered_classes=covered),
        ).model_dump_json()
    )
    return paths


def _recording_suite_mutation(
    monkeypatch: pytest.MonkeyPatch,
    failing: Suite | None = None,
    listing: Sequence[str] = ("m1", "m2"),
    during: Callable[[], object] | None = None,
) -> tuple[dict[Suite, tuple[str, int, list[str]]], list[Path]]:
    """Each mutated suite's service module, PIT threads and sample, and where mutants
    were listed; `during` runs inside each suite's mutation."""
    calls: dict[Suite, tuple[str, int, list[str]]] = {}
    listed: list[Path] = []

    def run_suite(
        project: ExecutionProject,
        suite: Suite,
        paths: SuitePaths,
        service_module: str,
        settings: PitSettings,
        sample: Sequence[str],
        pool: WorkerPool,
        space: FreeSpace,
        stop: threading.Event,
        extension: object = None,
    ) -> None:
        calls[suite] = (service_module, settings.threads, list(sample))
        if during is not None:
            during()
        if suite is failing:
            raise ExecutionError("PIT failed")

    def list_service_mutants(
        project: ExecutionProject,
        paths: SuitePaths,
        service_module: str,
        settings: PitSettings,
        directory: Path,
    ) -> list[str]:
        # Listing runs no test, so it holds one worker and needs no more threads.
        assert settings.threads == 1
        listed.append(paths.root)
        return list(listing)

    monkeypatch.setattr(pipeline, "build_image", lambda: None)
    monkeypatch.setattr(pipeline, "require_case_sensitive_mount", lambda _: None)
    monkeypatch.setattr(pipeline, "target_for", lambda _: _TARGET)
    monkeypatch.setattr(pipeline, "_run_suite_mutation", run_suite)
    monkeypatch.setattr(pipeline, "_list_service_mutants", list_service_mutants)
    return calls, listed


def test_run_mutation_lists_the_services_mutants_before_mutating_each_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for suite in Suite:
        _write_coverage(tmp_path, suite, ["org.A"])
    calls, listed = _recording_suite_mutation(monkeypatch, failing=Suite.DEVELOPER)

    failures = pipeline.run_mutation(
        _PROJECT, tmp_path, [Suite.DEVELOPER, Suite.CODEX], _MUTATION
    )

    assert listed == [SuitePaths.of(tmp_path, _PROJECT, Suite.DEVELOPER).root]
    service = pipeline.ServiceMutants.model_validate_json(
        pipeline.service_mutants_path(tmp_path, _PROJECT).read_text()
    )
    # A sample no smaller than the service's mutants is all of them.
    assert (service.mutants, service.sample) == (["m1", "m2"], ["m1", "m2"])
    assert service.sample_seed == "seed"
    assert calls == {
        Suite.DEVELOPER: ("svc", 2, ["m1", "m2"]),
        Suite.CODEX: ("svc", 2, ["m1", "m2"]),
    }
    assert failures == {Suite.DEVELOPER: "PIT failed"}


def test_run_mutation_challenges_every_suite_with_one_sample_of_the_mutants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for suite in Suite:
        _write_coverage(tmp_path, suite, ["org.A"])
    listing = [f"org.A|m|()V|M|{index}" for index in range(50)]
    calls, _ = _recording_suite_mutation(monkeypatch, listing=listing)

    pipeline.run_mutation(_PROJECT, tmp_path, list(Suite), _MUTATION)

    service = pipeline.ServiceMutants.model_validate_json(
        pipeline.service_mutants_path(tmp_path, _PROJECT).read_text()
    )
    assert service.mutants == listing
    assert service.sample == sample_mutants(listing, 10, "seed")
    assert len(service.sample) == 10
    assert {suite: sample for suite, (_, _, sample) in calls.items()} == {
        suite: service.sample for suite in Suite
    }


def test_run_mutation_mutates_its_suites_side_by_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for suite in Suite:
        _write_coverage(tmp_path, suite, ["org.A"])
    # Passed only once every suite's mutation is inside it at the same time.
    together = threading.Barrier(len(Suite), timeout=20)
    calls, _ = _recording_suite_mutation(monkeypatch, during=together.wait)

    failures = pipeline.run_mutation(_PROJECT, tmp_path, list(Suite), _MUTATION)

    assert failures == {} and set(calls) == set(Suite)


def test_run_mutation_gives_a_pit_run_no_more_threads_than_the_worker_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_coverage(tmp_path, Suite.DEVELOPER, ["org.A"])
    calls, _ = _recording_suite_mutation(monkeypatch)

    pipeline.run_mutation(
        _PROJECT,
        tmp_path,
        [Suite.DEVELOPER],
        replace(_MUTATION, pit=PitSettings(threads=8, timeout_seconds=60), workers=3),
    )

    assert calls[Suite.DEVELOPER][1] == 3


def test_run_mutation_needs_coverage_of_the_suites_it_runs_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    developer = SuitePaths.of(tmp_path, _PROJECT, Suite.DEVELOPER)
    developer.root.mkdir(parents=True)
    developer.error("coverage").write_text("compilation failed")
    _write_coverage(tmp_path, Suite.CODEX, ["org.A"])
    calls, listed = _recording_suite_mutation(monkeypatch)

    failures = pipeline.run_mutation(_PROJECT, tmp_path, [Suite.CODEX], _MUTATION)

    # The developers' build failed, so the first suite that was measured lists them.
    assert listed == [SuitePaths.of(tmp_path, _PROJECT, Suite.CODEX).root]
    assert calls == {Suite.CODEX: ("svc", 2, ["m1", "m2"])}
    assert failures == {}


def test_run_mutation_keeps_the_listing_when_no_suite_it_runs_was_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_coverage(tmp_path, Suite.DEVELOPER, ["org.A"])
    codex = SuitePaths.of(tmp_path, _PROJECT, Suite.CODEX)
    codex.root.mkdir(parents=True)
    codex.error("coverage").write_text("compilation failed")
    codex.mutation.write_text("{}")
    mutants = pipeline.service_mutants_path(tmp_path, _PROJECT)
    mutants.write_text("listed")
    _, listed = _recording_suite_mutation(monkeypatch)

    failures = pipeline.run_mutation(_PROJECT, tmp_path, [Suite.CODEX], _MUTATION)

    assert failures == {Suite.CODEX: f"coverage failed; see {codex.error('coverage')}"}
    assert listed == [] and mutants.read_text() == "listed"
    assert not codex.mutation.exists()


def test_a_failed_listing_leaves_no_result_of_the_suites_it_reruns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    developer = _write_coverage(tmp_path, Suite.DEVELOPER, ["org.A"])
    codex = _write_coverage(tmp_path, Suite.CODEX, ["org.A"])
    developer.mutation.write_text("{}")
    codex.mutation.write_text("{}")
    mutants = pipeline.service_mutants_path(tmp_path, _PROJECT)
    mutants.write_text("listed")
    _recording_suite_mutation(monkeypatch)

    def fail(*_: object) -> None:
        raise ExecutionError("PIT failed")

    monkeypatch.setattr(pipeline, "_list_service_mutants", fail)

    with pytest.raises(ExecutionError, match="PIT failed"):
        pipeline.run_mutation(_PROJECT, tmp_path, [Suite.CODEX], _MUTATION)

    # The other suites' results still stand on the earlier listing.
    assert developer.mutation.exists() and mutants.read_text() == "listed"
    assert not codex.mutation.exists()


def test_run_mutation_stops_on_an_unsafe_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for suite in Suite:
        _write_coverage(tmp_path, suite, ["org.A"])
    calls, listed = _recording_suite_mutation(monkeypatch)

    def refuse(_: Path) -> None:
        raise ExecutionError("case-insensitive")

    monkeypatch.setattr(pipeline, "require_case_sensitive_mount", refuse)

    with pytest.raises(ExecutionError, match="case-insensitive"):
        pipeline.run_mutation(_PROJECT, tmp_path, list(Suite), _MUTATION)

    assert calls == {} and listed == []


def test_run_mutation_skips_suites_whose_coverage_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_coverage(tmp_path, Suite.DEVELOPER, ["org.A"])
    _write_coverage(tmp_path, Suite.CLAUDE_CODE, ["org.B"])
    _write_coverage(tmp_path, Suite.EVOMASTER, ["org.B"])
    codex = SuitePaths.of(tmp_path, _PROJECT, Suite.CODEX)
    codex.root.mkdir(parents=True)
    codex.error("coverage").write_text("compilation failed")
    calls, _ = _recording_suite_mutation(monkeypatch)
    capped = replace(_PROJECT, pit_threads=1)

    failures = pipeline.run_mutation(
        capped,
        tmp_path,
        list(Suite),
        replace(_MUTATION, pit=PitSettings(threads=6, timeout_seconds=60), workers=6),
    )

    assert {suite: threads for suite, (_, threads, _) in calls.items()} == {
        Suite.DEVELOPER: 1,
        Suite.CLAUDE_CODE: 1,
        Suite.EVOMASTER: 1,
    }
    assert failures == {Suite.CODEX: f"coverage failed; see {codex.error('coverage')}"}


_LISTING = [f"org.{name}|m|()V|M|{index}" for name in "AB" for index in range(25)]


def _mutant_record(mutant_id: str) -> MutantRecord:
    index = int(mutant_id.rsplit("|", 1)[1])
    killed = index % 3 == 0
    return MutantRecord(
        mutant_id=mutant_id,
        mutated_class=mutant_id.split("|")[0],
        mutated_method="m",
        line=index,
        mutator="M",
        status="KILLED" if killed else "SURVIVED",
        killed=killed,
        covered=True,
        killing_tests=["org.demo.ApiTest#a"] if killed else [],
        surviving_tests=[] if killed else ["org.demo.ApiTest#a"],
    )


def _suite_mutation(sample: Sequence[str], module: str) -> pipeline.SuiteMutation:
    """A run that covered every mutant of `sample`, the even-numbered ones on lines
    coverage ran, PIT having left untested those also divisible by four."""
    return pipeline.SuiteMutation(
        target_classes=len({mutant_id.split("|")[0] for mutant_id in sample}),
        unreached_mutants=1,
        line_covered_mutants=sum(int(m.rsplit("|", 1)[1]) % 2 == 0 for m in sample),
        untested_line_covered_mutants=sum(
            int(m.rsplit("|", 1)[1]) % 4 == 0 for m in sample
        ),
        sample_sha256=sample_digest(sample),
        runs=[_PIT_RUN.model_copy(update={"module": module})],
        mutants=[_mutant_record(mutant_id) for mutant_id in sample],
    )


_BASE_SAMPLE = sample_mutants(_LISTING, 4, "seed")
_EXTENDED_SAMPLE = sample_mutants(_LISTING, 10, "seed")
_REST = sorted(set(_EXTENDED_SAMPLE) - set(_BASE_SAMPLE))


def test_extending_adds_the_rest_of_the_sample_to_a_suites_results() -> None:
    base = _suite_mutation(_BASE_SAMPLE, "first")
    added = _suite_mutation(_REST, "second")

    extended = pipeline.extend_mutation(
        base, added, _BASE_SAMPLE, _EXTENDED_SAMPLE, ["org.A"]
    )

    assert extended.sample_sha256 == sample_digest(_EXTENDED_SAMPLE)
    assert {m.mutant_id for m in extended.mutants} == set(_EXTENDED_SAMPLE)
    assert [run.module for run in extended.runs] == ["first", "second"]
    # Only the classes the suite covers are targets, as in one run on the whole sample.
    assert extended.target_classes == 1
    whole = _suite_mutation(_EXTENDED_SAMPLE, "whole")
    assert (
        extended.unreached_mutants,
        extended.line_covered_mutants,
        extended.untested_line_covered_mutants,
    ) == (
        2,
        whole.line_covered_mutants,
        whole.untested_line_covered_mutants,
    )


def test_extending_refuses_results_of_another_sample() -> None:
    with pytest.raises(ExecutionError, match="another sample"):
        pipeline.extend_mutation(
            _suite_mutation(_REST, "first"),
            _suite_mutation(_REST, "second"),
            _BASE_SAMPLE,
            _EXTENDED_SAMPLE,
            ["org.A"],
        )


def test_extending_refuses_added_results_that_are_not_the_rest_of_the_sample() -> None:
    with pytest.raises(ExecutionError, match="not the rest"):
        pipeline.extend_mutation(
            _suite_mutation(_BASE_SAMPLE, "first"),
            _suite_mutation(_EXTENDED_SAMPLE, "second"),
            _BASE_SAMPLE,
            _EXTENDED_SAMPLE,
            ["org.A"],
        )


def test_extending_refuses_a_sample_that_does_not_hold_the_one_run() -> None:
    elsewhere = sample_mutants(_LISTING, 10, "other")
    with pytest.raises(ExecutionError, match="does not hold"):
        pipeline.extend_mutation(
            _suite_mutation(_BASE_SAMPLE, "first"),
            _suite_mutation(sorted(set(elsewhere) - set(_BASE_SAMPLE)), "second"),
            _BASE_SAMPLE,
            elsewhere,
            ["org.A"],
        )


def _extending_suite_mutation(
    monkeypatch: pytest.MonkeyPatch,
    listing: Sequence[str] = _LISTING,
    failing: Suite | None = None,
) -> dict[Suite, tuple[list[str], Any]]:
    """Each mutated suite's sample and extension; a run writes results on the whole
    sample, as the real one does once it has extended its base."""
    calls: dict[Suite, tuple[list[str], Any]] = {}

    def run_suite(
        project: ExecutionProject,
        suite: Suite,
        paths: SuitePaths,
        service_module: str,
        settings: PitSettings,
        sample: Sequence[str],
        pool: WorkerPool,
        space: FreeSpace,
        stop: threading.Event,
        extension: Any = None,
    ) -> None:
        calls[suite] = (list(sample), extension)
        if suite is failing:
            raise ExecutionError("PIT failed")
        whole = sample if extension is None else extension.sample
        write_atomically(
            paths.mutation, _suite_mutation(whole, "whole").model_dump_json()
        )

    monkeypatch.setattr(pipeline, "build_image", lambda: None)
    monkeypatch.setattr(pipeline, "require_case_sensitive_mount", lambda _: None)
    monkeypatch.setattr(pipeline, "target_for", lambda _: _TARGET)
    monkeypatch.setattr(pipeline, "_run_suite_mutation", run_suite)
    monkeypatch.setattr(pipeline, "_list_service_mutants", lambda *_: list(listing))
    return calls


_EXTENDING = replace(_MUTATION, extend_from=4)


def _with_results(
    output_dir: Path, suite: Suite, mutation: pipeline.SuiteMutation | None
) -> SuitePaths:
    paths = _write_coverage(output_dir, suite, ["org.A"])
    if mutation is not None:
        paths.mutation.write_text(mutation.model_dump_json())
    return paths


def _kept(paths: SuitePaths, size: int = 4) -> pipeline.SuiteMutation:
    return pipeline.SuiteMutation.model_validate_json(
        (paths.root / f"mutation-{size}.json").read_text()
    )


def test_run_mutation_extends_each_suite_with_the_rest_of_the_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _suite_mutation(_BASE_SAMPLE, "first")
    developer = _with_results(tmp_path, Suite.DEVELOPER, base)
    calls = _extending_suite_mutation(monkeypatch)

    failures = pipeline.run_mutation(_PROJECT, tmp_path, [Suite.DEVELOPER], _EXTENDING)

    assert failures == {}
    assert calls == {
        Suite.DEVELOPER: (
            _REST,
            pipeline._Extension(base, _BASE_SAMPLE, _EXTENDED_SAMPLE),
        )
    }
    assert _kept(developer) == base
    service = pipeline.ServiceMutants.model_validate_json(
        pipeline.service_mutants_path(tmp_path, _PROJECT).read_text()
    )
    assert service.sample == _EXTENDED_SAMPLE


def test_an_extension_prefers_fresh_results_to_kept_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Kept by an earlier extension, before the suite was mutated again on 4 mutants.
    fresh = _suite_mutation(_BASE_SAMPLE, "fresh")
    developer = _with_results(tmp_path, Suite.DEVELOPER, fresh)
    stale = _suite_mutation(_BASE_SAMPLE, "stale")
    (developer.root / "mutation-4.json").write_text(stale.model_dump_json())
    calls = _extending_suite_mutation(monkeypatch)

    pipeline.run_mutation(_PROJECT, tmp_path, [Suite.DEVELOPER], _EXTENDING)

    assert calls[Suite.DEVELOPER][1].base == fresh
    assert _kept(developer) == fresh


def test_a_failed_or_finished_extension_reruns_from_the_kept_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _suite_mutation(_BASE_SAMPLE, "first")
    developer = _with_results(tmp_path, Suite.DEVELOPER, base)
    _extending_suite_mutation(monkeypatch, failing=Suite.DEVELOPER)

    failures = pipeline.run_mutation(_PROJECT, tmp_path, [Suite.DEVELOPER], _EXTENDING)

    assert failures == {Suite.DEVELOPER: "PIT failed"}
    assert not developer.mutation.exists() and _kept(developer) == base
    for _ in range(2):
        calls = _extending_suite_mutation(monkeypatch)

        assert (
            pipeline.run_mutation(_PROJECT, tmp_path, [Suite.DEVELOPER], _EXTENDING)
            == {}
        )

        assert calls[Suite.DEVELOPER][1].base == base
        extended = pipeline.SuiteMutation.model_validate_json(
            developer.mutation.read_text()
        )
        assert extended.sample_sha256 == sample_digest(_EXTENDED_SAMPLE)


def test_an_extension_leaves_out_suites_whose_coverage_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_results(tmp_path, Suite.DEVELOPER, _suite_mutation(_BASE_SAMPLE, "first"))
    codex = SuitePaths.of(tmp_path, _PROJECT, Suite.CODEX)
    codex.root.mkdir(parents=True)
    codex.error("coverage").write_text("compilation failed")
    calls = _extending_suite_mutation(monkeypatch)

    failures = pipeline.run_mutation(
        _PROJECT, tmp_path, [Suite.DEVELOPER, Suite.CODEX], _EXTENDING
    )

    assert failures == {Suite.CODEX: f"coverage failed; see {codex.error('coverage')}"}
    assert set(calls) == {Suite.DEVELOPER}


def test_run_mutation_refuses_to_extend_results_of_another_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    developer = _with_results(
        tmp_path, Suite.DEVELOPER, _suite_mutation(_BASE_SAMPLE, "first")
    )
    results = developer.mutation.read_text()
    mutants = pipeline.service_mutants_path(tmp_path, _PROJECT)
    mutants.write_text("listed")
    # One of the results' mutants is no longer listed.
    calls = _extending_suite_mutation(
        monkeypatch, listing=[m for m in _LISTING if m != _BASE_SAMPLE[0]]
    )

    with pytest.raises(ExecutionError, match="no mutation results on the first 4"):
        pipeline.run_mutation(_PROJECT, tmp_path, [Suite.DEVELOPER], _EXTENDING)

    # Refused before any PIT run or change to the suite's files.
    assert calls == {} and mutants.read_text() == "listed"
    assert developer.mutation.read_text() == results
    assert not (developer.root / "mutation-4.json").exists()


def test_run_mutation_needs_results_to_extend_for_every_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    developer = _with_results(
        tmp_path, Suite.DEVELOPER, _suite_mutation(_BASE_SAMPLE, "first")
    )
    _with_results(tmp_path, Suite.CODEX, None)
    calls = _extending_suite_mutation(monkeypatch)

    with pytest.raises(ExecutionError, match="codex: no mutation results"):
        pipeline.run_mutation(
            _PROJECT, tmp_path, [Suite.DEVELOPER, Suite.CODEX], _EXTENDING
        )

    assert calls == {} and developer.mutation.exists()
    assert not (developer.root / "mutation-4.json").exists()


def test_run_mutation_extends_only_to_a_larger_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    developer = _with_results(
        tmp_path, Suite.DEVELOPER, _suite_mutation(_BASE_SAMPLE, "first")
    )
    _extending_suite_mutation(monkeypatch)

    with pytest.raises(ExecutionError, match="not fewer than 10"):
        pipeline.run_mutation(
            _PROJECT, tmp_path, [Suite.DEVELOPER], replace(_MUTATION, extend_from=10)
        )

    assert developer.mutation.exists()


def test_extensions_extend_one_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    developer = _with_results(
        tmp_path, Suite.DEVELOPER, _suite_mutation(_BASE_SAMPLE, "first")
    )
    calls = _extending_suite_mutation(monkeypatch)
    pipeline.run_mutation(_PROJECT, tmp_path, [Suite.DEVELOPER], _EXTENDING)

    pipeline.run_mutation(
        _PROJECT,
        tmp_path,
        [Suite.DEVELOPER],
        replace(_MUTATION, sample_size=20, extend_from=10),
    )

    sample, extension = calls[Suite.DEVELOPER]
    assert sample == sorted(
        set(sample_mutants(_LISTING, 20, "seed")) - set(_EXTENDED_SAMPLE)
    )
    assert extension.base.sample_sha256 == sample_digest(_EXTENDED_SAMPLE)
    assert _kept(developer, 10) == extension.base
    assert _kept(developer).sample_sha256 == sample_digest(_BASE_SAMPLE)


def test_the_mutation_command_extends_from_the_given_sample_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings: list[pipeline.MutationSettings] = []

    def run_mutation(
        project: ExecutionProject,
        output_dir: Path,
        suites: Sequence[Suite],
        mutation: pipeline.MutationSettings,
    ) -> dict[Suite, str]:
        settings.append(mutation)
        return {}

    summary = tmp_path / "summary.json"
    summary.write_text("{}")
    monkeypatch.setitem(cli.MEASURED_PROJECTS, "demo", _PROJECT)
    monkeypatch.setattr(cli, "run_mutation", run_mutation)
    monkeypatch.setattr(cli, "write_summary", lambda *_: summary)

    result = CliRunner().invoke(
        cli.app,
        [
            "execution",
            "mutation",
            "--project",
            "demo",
            "--output-dir",
            str(tmp_path),
            "--sample-size",
            "600",
            "--extend-from",
            "300",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [(s.sample_size, s.extend_from) for s in settings] == [(600, 300)]


def _classpaths(*classpaths: tuple[str, list[str], list[str]]) -> str:
    """(module, code directories, production directories) of each compiled module."""
    return json.dumps(
        [
            {
                "module": module,
                "directory": f"/workspace/{module}",
                "entries": [],
                "code_directories": code,
                "production_directories": production,
            }
            for module, code, production in classpaths
        ]
    )


def test_list_service_mutants_mutates_every_production_class_of_the_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = SuitePaths(tmp_path / "developer")
    production = ["/workspace/svc/target/classes", "/workspace/core/target/classes"]
    for directory, name in zip(production, ("Api", "Core")):
        classes = paths.workspace / directory.removeprefix("/workspace/") / "org"
        classes.mkdir(parents=True)
        (classes / f"{name}.class").write_bytes(b"")
    paths.classpaths.write_text(
        _classpaths(
            ("tools", ["/workspace/tools/target/classes"], []),
            ("svc", [*production, "/workspace/fixtures/target/classes"], production),
        )
    )
    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    listed: list[tuple[str, list[str], list[str]]] = []

    def list_mutants(
        container: RecordingContainer,
        classpath: ModuleClasspath,
        runtime: object,
        target_classes: Sequence[str],
        *,
        mutable_directories: Sequence[str],
        **_: object,
    ) -> list[str]:
        listed.append(
            (classpath.module, list(target_classes), list(mutable_directories))
        )
        return ["m1"]

    monkeypatch.setattr(pipeline, "list_mutants", list_mutants)
    containers = _recording_containers(monkeypatch)

    listing = tmp_path / "demo" / "service-mutants"
    listing.mkdir(parents=True)
    (listing / "stale.xml").write_text("")

    mutants = pipeline._list_service_mutants(_PROJECT, paths, "svc", _SETTINGS, listing)

    assert mutants == ["m1"]
    assert listed == [("svc", ["org.Api", "org.Core"], production)]
    # PIT reads the suite's build but writes beside the listing, from a clean start.
    (container,) = containers
    assert (container.workspace, container.output) == (paths.workspace, listing)
    assert list(listing.iterdir()) == []


_SERVICE = ["/workspace/api/target/classes"]
_SAMPLED = "org.demo.Service|run|()V|M|3"


def _pit_mutant(mutant_id: str = _SAMPLED) -> PitMutant:
    return PitMutant(
        mutant_id=mutant_id,
        mutated_class=mutant_id.split("|")[0],
        mutated_method="run",
        line=3,
        mutator="M",
        status="KILLED",
        killing_tests=["org.demo.ApiTest.a(org.demo.ApiTest)"],
        succeeding_tests=[],
    )


def _suite_to_mutate(
    tmp_path: Path, suite: Suite = Suite.DEVELOPER, covered: Sequence[str] = ()
) -> SuitePaths:
    """A suite whose one passing test of the api module covers `covered` and
    org.demo.Service, classes of the service with class files."""
    classes = ["org.demo.Service", *covered]
    paths = _write_coverage(tmp_path, suite, classes)
    for name in classes:
        class_file = (
            paths.workspace / "api/target/classes" / f"{name.replace('.', '/')}.class"
        )
        class_file.parent.mkdir(parents=True, exist_ok=True)
        class_file.write_bytes(b"")
    record = _record("a")
    record.status = TestStatus.PASSED
    paths.tests.write_text(SuiteTests(commit="abc", tests=[record]).model_dump_json())
    paths.classpaths.write_text(_classpaths(("api", _SERVICE, _SERVICE)))
    return paths


def _pool(tmp_path: Path, workers: int = 4) -> WorkerPool:
    return pipeline.worker_pool(tmp_path, workers)


def test_suite_mutation_mutates_the_service_classes_its_passing_tests_cover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _write_coverage(
        tmp_path, Suite.DEVELOPER, ["org.demo.Service", "org.demo.Unsampled"]
    )
    class_file = paths.workspace / "api/target/classes/org/demo/Service.class"
    class_file.parent.mkdir(parents=True)
    class_file.write_bytes(b"")
    records = [_record("a"), _record("a"), _record("b"), _record("c")]
    records[2].status = TestStatus.FAILED
    records[0].status = records[1].status = records[3].status = TestStatus.PASSED
    records[3].module = "tools"
    paths.tests.write_text(SuiteTests(commit="abc", tests=records).model_dump_json())
    paths.classpaths.write_text(
        _classpaths(
            ("api", [*_SERVICE, "/workspace/tools/target/classes"], _SERVICE),
            # Another service of the build: its tests run none of this one's classes.
            ("tools", ["/workspace/tools/target/classes"], []),
        )
    )
    challenged: list[tuple[list[tuple[str, str]], list[str], list[str]]] = []
    worker_flags: list[Sequence[str]] = []
    kept: list[Sequence[str] | None] = []

    def run_pit(
        container: RecordingContainer,
        classpath: ModuleClasspath,
        runtime: object,
        tests: Sequence[tuple[str, str]],
        target_classes: Sequence[str],
        *,
        mutable_directories: Sequence[str],
        jvm_args: Sequence[str],
        kept_mutants: Sequence[str] | None,
        **_: object,
    ) -> PitRun:
        challenged.append(
            (list(tests), list(target_classes), list(mutable_directories))
        )
        worker_flags.append(jvm_args)
        kept.append(kept_mutants)
        return _PIT_RUN

    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", run_pit)
    monkeypatch.setattr(pipeline, "parse_mutations", lambda _: [_pit_mutant()])
    # The second is of a class the suite's tests do not run.
    sample = [_SAMPLED, "org.demo.Uncovered|run|()V|M|1"]

    pipeline._run_suite_mutation(
        _PROJECT,
        Suite.DEVELOPER,
        paths,
        "api",
        _SETTINGS,
        sample,
        _pool(tmp_path),
        _SPACE,
        threading.Event(),
    )

    # Of the covered classes, only those with a sampled mutant are mutated.
    assert challenged == [([("org.demo.ApiTest", "a")], ["org.demo.Service"], _SERVICE)]
    assert kept == [sample]
    # The tests' flags, so a test passes or fails in PIT's workers as it did in coverage.
    assert [list(flags) for flags in worker_flags] == [
        ["-Xmx16g", "-XX:+StartAttachListener"]
    ]
    mutation = pipeline.SuiteMutation.model_validate_json(paths.mutation.read_text())
    (mutant,) = mutation.mutants
    assert (mutant.killed, mutant.killing_tests) == (True, ["org.demo.ApiTest#a"])
    assert mutant.surviving_tests == []
    assert (mutation.target_classes, mutation.unreached_mutants) == (1, 0)
    assert mutation.sample_sha256 == sample_digest(sample)
    assert mutation.runs == [_PIT_RUN]


def test_suite_mutation_refuses_a_pit_run_that_left_out_a_sampled_mutant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path)
    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", lambda *_, **__: _PIT_RUN)
    monkeypatch.setattr(pipeline, "parse_mutations", lambda _: [_pit_mutant()])
    lost = "org.demo.Service|stop|()V|M|9"

    with pytest.raises(ExecutionError, match=r"no result for 1 sampled mutants.*stop"):
        pipeline._run_suite_mutation(
            _PROJECT,
            Suite.DEVELOPER,
            paths,
            "api",
            _SETTINGS,
            [_SAMPLED, lost],
            _pool(tmp_path),
            _SPACE,
            threading.Event(),
        )

    assert not paths.mutation.exists()


def test_suite_mutation_refuses_a_pit_run_that_mutated_outside_the_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path)
    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", lambda *_, **__: _PIT_RUN)
    extra = "org.demo.Service|stop|()V|M|9"
    monkeypatch.setattr(
        pipeline, "parse_mutations", lambda _: [_pit_mutant(), _pit_mutant(extra)]
    )

    with pytest.raises(ExecutionError, match=r"1 mutants outside the sample.*stop"):
        pipeline._run_suite_mutation(
            _PROJECT,
            Suite.DEVELOPER,
            paths,
            "api",
            _SETTINGS,
            [_SAMPLED],
            _pool(tmp_path),
            _SPACE,
            threading.Event(),
        )


def test_a_sampled_mutant_of_a_covered_class_pit_cannot_reach_stays_uncovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Covered, but its class file is in no directory the module's PIT run mutates.
    paths = _suite_to_mutate(tmp_path)
    coverage = SuiteCoverage.model_validate_json(paths.coverage.read_text())
    coverage.report.covered_classes.append("org.demo.FromAJar")
    paths.coverage.write_text(coverage.model_dump_json())
    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", lambda *_, **__: _PIT_RUN)
    monkeypatch.setattr(pipeline, "parse_mutations", lambda _: [_pit_mutant()])

    pipeline._run_suite_mutation(
        _PROJECT,
        Suite.DEVELOPER,
        paths,
        "api",
        _SETTINGS,
        [_SAMPLED, "org.demo.FromAJar|run|()V|M|1"],
        _pool(tmp_path),
        _SPACE,
        threading.Event(),
    )

    mutation = pipeline.SuiteMutation.model_validate_json(paths.mutation.read_text())
    assert [mutant.mutant_id for mutant in mutation.mutants] == [_SAMPLED]
    # Reported, since such mutants lower the suite's score without a test run.
    assert (mutation.target_classes, mutation.unreached_mutants) == (2, 1)


def test_suite_mutation_counts_mutants_on_covered_lines_that_pit_left_untested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path, covered=["org.demo.Service$Inner"])
    reports = paths.root / "coverage"
    reports.mkdir()
    (reports / "jacoco-0.xml").write_text(
        '<report name="r"><package name="org/demo">'
        '<class name="org/demo/Service" sourcefilename="Service.java"/>'
        '<class name="org/demo/Service$Inner" sourcefilename="Service.java"/>'
        '<sourcefile name="Service.java">'
        '<line nr="3" mi="0" ci="4" mb="0" cb="0"/>'
        '<line nr="7" mi="0" ci="2" mb="0" cb="0"/>'
        '<line nr="9" mi="5" ci="0" mb="0" cb="0"/>'
        "</sourcefile></package></report>"
    )
    # The second is on a line the coverage stage's tests ran, in a class's set-up,
    # so PIT ran no selected test on it; the third is on a line they did not run.
    statuses = {
        "org.demo.Service|run|()V|M|3": ("KILLED", 3),
        "org.demo.Service$Inner|start|()V|M|1": ("RUN_ERROR", 7),
        "org.demo.Service|stop|()V|M|5": ("NO_COVERAGE", 9),
    }
    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", lambda *_, **__: _PIT_RUN)
    monkeypatch.setattr(
        pipeline,
        "parse_mutations",
        lambda _: [
            _pit_mutant(mutant_id).model_copy(
                update={
                    "status": status,
                    "line": line,
                    "killing_tests": (
                        ["org.demo.ApiTest.a(org.demo.ApiTest)"]
                        if status == "KILLED"
                        else []
                    ),
                }
            )
            for mutant_id, (status, line) in statuses.items()
        ],
    )

    pipeline._run_suite_mutation(
        _PROJECT,
        Suite.DEVELOPER,
        paths,
        "api",
        _SETTINGS,
        list(statuses),
        _pool(tmp_path),
        _SPACE,
        threading.Event(),
    )

    mutation = pipeline.SuiteMutation.model_validate_json(paths.mutation.read_text())
    assert mutation.line_covered_mutants == 2
    assert mutation.untested_line_covered_mutants == 1


_EXTENSION_LISTING = [
    f"org.demo.{name}|m|()V|M|{index}"
    for name, count in (("Service", 20), ("FromAJar", 10), ("Uncovered", 10))
    for index in range(count)
]


def _indexed_pit_mutant(mutant_id: str) -> PitMutant:
    """On line `index`, killed, surviving or not covered by `index` modulo 3."""
    index = int(mutant_id.rsplit("|", 1)[1])
    status = ("KILLED", "SURVIVED", "NO_COVERAGE")[index % 3]
    test = "org.demo.ApiTest.a(org.demo.ApiTest)"
    return _pit_mutant(mutant_id).model_copy(
        update={
            "status": status,
            "line": index,
            "killing_tests": [test] if status == "KILLED" else [],
            "succeeding_tests": [test] if status == "SURVIVED" else [],
        }
    )


def _fake_pit(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """The mutants each PIT run keeps; a run reports its classes' kept mutants."""
    kept: list[list[str]] = []
    reports: list[list[PitMutant]] = []

    def run_pit(
        container: object,
        classpath: object,
        runtime: object,
        tests: object,
        target_classes: Sequence[str],
        *,
        kept_mutants: Sequence[str],
        **_: object,
    ) -> PitRun:
        kept.append(sorted(kept_mutants))
        reports.append(
            [
                _indexed_pit_mutant(mutant_id)
                for mutant_id in kept_mutants
                if mutant_id.split("|")[0] in target_classes
            ]
        )
        return _PIT_RUN

    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", run_pit)
    monkeypatch.setattr(pipeline, "parse_mutations", lambda _: reports.pop())
    return kept


def _suite_to_extend(root: Path) -> SuitePaths:
    """A suite covering Service, on its even lines, and FromAJar, which no PIT run of
    the suite can mutate."""
    paths = _suite_to_mutate(root)
    coverage = SuiteCoverage.model_validate_json(paths.coverage.read_text())
    coverage.report.covered_classes.append("org.demo.FromAJar")
    paths.coverage.write_text(coverage.model_dump_json())
    reports = paths.root / "coverage"
    reports.mkdir()
    lines = "".join(
        f'<line nr="{line}" mi="{line % 2}" ci="{1 - line % 2}" mb="0" cb="0"/>'
        for line in range(20)
    )
    (reports / "jacoco-0.xml").write_text(
        '<report name="r"><package name="org/demo">'
        '<class name="org/demo/Service" sourcefilename="Service.java"/>'
        f'<sourcefile name="Service.java">{lines}</sourcefile></package></report>'
    )
    return paths


def _mutate(
    paths: SuitePaths, sample: Sequence[str], extension: Any = None
) -> pipeline.SuiteMutation:
    pipeline._run_suite_mutation(
        _PROJECT,
        Suite.DEVELOPER,
        paths,
        "api",
        _SETTINGS,
        sample,
        _pool(paths.root),
        _SPACE,
        threading.Event(),
        extension,
    )
    return pipeline.SuiteMutation.model_validate_json(paths.mutation.read_text())


def test_an_extended_suite_records_what_one_run_on_the_whole_sample_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = _fake_pit(monkeypatch)
    sample = sample_mutants(_EXTENSION_LISTING, 24, "seed")
    base_sample = sample_mutants(_EXTENSION_LISTING, 9, "seed")
    rest = sorted(set(sample) - set(base_sample))
    whole = _mutate(_suite_to_extend(tmp_path / "whole"), sample)
    paths = _suite_to_extend(tmp_path / "extended")
    base = _mutate(paths, base_sample)

    extended = _mutate(paths, rest, pipeline._Extension(base, base_sample, sample))

    assert kept == [sample, base_sample, rest]
    # The case has every count to add up.
    assert whole.unreached_mutants and whole.untested_line_covered_mutants
    assert extended.model_dump(exclude={"runs", "mutants"}) == whole.model_dump(
        exclude={"runs", "mutants"}
    )
    assert sorted(extended.mutants, key=lambda m: m.mutant_id) == sorted(
        whole.mutants, key=lambda m: m.mutant_id
    )
    assert len(extended.runs) == 2


def test_extending_by_no_mutants_runs_no_pit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = _fake_pit(monkeypatch)
    paths = _suite_to_extend(tmp_path)
    # A service with no more mutants than the results already have.
    every = sorted(_EXTENSION_LISTING)
    base = _mutate(paths, every)

    extended = _mutate(paths, [], pipeline._Extension(base, every, every))

    assert len(kept) == 1 and extended == base


def test_a_suite_covering_no_sampled_mutant_runs_no_pit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path)

    def run_pit(*_: object, **__: object) -> PitRun:
        raise AssertionError("PIT has nothing to mutate")

    monkeypatch.setattr(pipeline, "run_pit", run_pit)
    sample = ["org.demo.Elsewhere|run|()V|M|1"]

    pipeline._run_suite_mutation(
        _PROJECT,
        Suite.DEVELOPER,
        paths,
        "api",
        _SETTINGS,
        sample,
        _pool(tmp_path),
        _SPACE,
        threading.Event(),
    )

    mutation = pipeline.SuiteMutation.model_validate_json(paths.mutation.read_text())
    assert (mutation.mutants, mutation.runs, mutation.target_classes) == ([], [], 0)
    assert mutation.sample_sha256 == sample_digest(sample)


def test_shards_split_classes_into_groups_of_about_as_many_sampled_mutants() -> None:
    sample = (
        [f"org.Big|m|()V|M|{index}" for index in range(6)]
        + [f"org.Mid|m|()V|M|{index}" for index in range(3)]
        + [f"org.Small|m|()V|M|{index}" for index in range(2)]
        + ["org.Tiny|m|()V|M|0", "org.Absent|m|()V|M|0"]
    )
    classes = ["org.Big", "org.Mid", "org.Small", "org.Tiny"]

    assert pipeline._shards(sample, classes, 1) == [classes]
    assert pipeline._shards(sample, classes, 2) == [
        ["org.Big"],
        ["org.Mid", "org.Small", "org.Tiny"],
    ]
    # Never more groups than classes, since PIT mutates a class in one worker.
    assert pipeline._shards(sample, classes, 9) == [[name] for name in classes]
    assert pipeline._shards(sample, [], 3) == []


def test_a_project_split_over_containers_runs_pit_once_per_group_of_classes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path, covered=["org.demo.Other", "org.demo.Third"])
    sample = [
        _SAMPLED,
        "org.demo.Other|run|()V|M|1",
        "org.demo.Other|run|()V|M|2",
        "org.demo.Third|run|()V|M|1",
    ]
    containers = _recording_containers(monkeypatch)
    runs: dict[str, tuple[RecordingContainer, list[str], Sequence[str] | None]] = {}
    lock = threading.Lock()
    # Passed only if both of the suite's PIT runs are under way at the same time.
    together = threading.Barrier(2, timeout=20)

    def run_pit(
        container: RecordingContainer,
        classpath: ModuleClasspath,
        runtime: object,
        tests: Sequence[tuple[str, str]],
        target_classes: Sequence[str],
        *,
        report_directory: PurePosixPath,
        kept_mutants: Sequence[str] | None,
        **_: object,
    ) -> PitRun:
        together.wait()
        with lock:
            runs[report_directory.name] = (
                container,
                list(target_classes),
                kept_mutants,
            )
        return _PIT_RUN

    def parse_mutations(xml_file: Path) -> list[PitMutant]:
        mutated = runs[xml_file.parent.name][1]
        return [
            _pit_mutant(mutant_id)
            for mutant_id in sample
            if mutant_id.split("|")[0] in mutated
        ]

    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", run_pit)
    monkeypatch.setattr(pipeline, "parse_mutations", parse_mutations)
    project = replace(
        _PROJECT, pit_containers=2, pit_private_directories=("api/target/state",)
    )

    pipeline._run_suite_mutation(
        project,
        Suite.DEVELOPER,
        paths,
        "api",
        _SETTINGS,
        sample,
        _pool(tmp_path),
        _SPACE,
        threading.Event(),
    )

    assert {name: classes for name, (_, classes, _) in runs.items()} == {
        "api-0": ["org.demo.Other"],
        "api-1": ["org.demo.Service", "org.demo.Third"],
    }
    # Every run is given the whole sample, of which it mutates its own classes'.
    assert all(kept == sample for _, _, kept in runs.values())
    # Each container gets its own copy of the state its JVMs would collide on.
    assert {container.private_directories for container, _, _ in runs.values()} == {
        (PurePosixPath("/workspace/api/target/state"),)
    }
    (build,) = containers
    assert build.private_directories == ()
    mutation = pipeline.SuiteMutation.model_validate_json(paths.mutation.read_text())
    assert sorted(mutant.mutant_id for mutant in mutation.mutants) == sorted(sample)
    assert mutation.runs == [_PIT_RUN, _PIT_RUN]


def test_one_groups_lost_mutant_fails_a_suite_split_over_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path, covered=["org.demo.Other"])
    sample = [_SAMPLED, "org.demo.Other|run|()V|M|1"]
    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", lambda *_, **__: _PIT_RUN)
    # Each run reports the same one mutant, which the other's classes do not have.
    monkeypatch.setattr(pipeline, "parse_mutations", lambda _: [_pit_mutant()])

    with pytest.raises(ExecutionError, match="no result for 1 sampled mutants"):
        pipeline._run_suite_mutation(
            replace(_PROJECT, pit_containers=2),
            Suite.DEVELOPER,
            paths,
            "api",
            _SETTINGS,
            sample,
            _pool(tmp_path),
            _SPACE,
            threading.Event(),
        )

    assert not paths.mutation.exists()


def test_a_failed_pit_run_stops_the_suites_other_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path, covered=["org.demo.Other", "org.demo.Third"])
    sample = [
        _SAMPLED,
        "org.demo.Other|run|()V|M|1",
        "org.demo.Third|run|()V|M|1",
    ]
    stopped: list[Path] = []
    killed = threading.Event()
    first = threading.Lock()
    under_way = threading.Barrier(2, timeout=20)

    def stop_containers(output: Path) -> None:
        stopped.append(output)
        # The second look finds the container that started after the first.
        if len(stopped) == 2:
            killed.set()

    def run_pit(*_: object, **__: object) -> PitRun:
        # Two runs have workers, and one fails once the other is under way too.
        under_way.wait()
        if first.acquire(blocking=False):
            raise ExecutionError("PIT failed")
        # As a container that is killed makes its `docker run` fail.
        assert killed.wait(20)
        raise ExecutionError("java exited with 137")

    monkeypatch.setattr(pipeline, "_STOP_POLL_SECONDS", 0.05)
    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", run_pit)
    monkeypatch.setattr(pipeline, "stop_containers", stop_containers)
    stop = threading.Event()

    with pytest.raises(ExecutionError, match="PIT failed"):
        pipeline._run_suite_mutation(
            replace(_PROJECT, pit_containers=3),
            Suite.DEVELOPER,
            paths,
            "api",
            _SETTINGS,
            sample,
            # Two of the three runs get workers; the third must not wait for them.
            _pool(tmp_path, workers=4),
            _SPACE,
            stop,
        )

    assert stopped[:2] == [paths.root, paths.root] and stop.is_set()
    assert not paths.mutation.exists()


def test_run_mutation_records_a_suites_unexpected_failure_and_runs_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for suite in (Suite.DEVELOPER, Suite.CODEX):
        _write_coverage(tmp_path, suite, ["org.A"])
    calls, _ = _recording_suite_mutation(monkeypatch)
    run_suite = pipeline._run_suite_mutation

    def break_codex(
        project: ExecutionProject, suite: Suite, *rest: Any, **options: Any
    ) -> None:
        run_suite(project, suite, *rest, **options)
        if suite is Suite.CODEX:
            raise ValueError("truncated report")

    monkeypatch.setattr(pipeline, "_run_suite_mutation", break_codex)

    failures = pipeline.run_mutation(
        _PROJECT, tmp_path, [Suite.DEVELOPER, Suite.CODEX], _MUTATION
    )

    assert set(calls) == {Suite.DEVELOPER, Suite.CODEX}
    assert failures == {Suite.CODEX: "unexpected ValueError: truncated report"}
    error = SuitePaths.of(tmp_path, _PROJECT, Suite.CODEX).error("mutation")
    assert "Traceback" in error.read_text() and "truncated report" in error.read_text()


def test_an_interrupted_run_stops_every_suites_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for suite in (Suite.DEVELOPER, Suite.CODEX):
        _write_coverage(tmp_path, suite, ["org.A"])
    stopped: list[Path] = []
    stops: list[threading.Event] = []

    def run_suite(*arguments: object, **_: object) -> None:
        stop = cast(threading.Event, arguments[-1])
        stops.append(stop)
        assert stop.wait(20)

    _recording_suite_mutation(monkeypatch)
    monkeypatch.setattr(pipeline, "_run_suite_mutation", run_suite)
    monkeypatch.setattr(pipeline, "stop_containers", stopped.append)

    def interrupt(_: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(pipeline, "as_completed", interrupt)

    with pytest.raises(KeyboardInterrupt):
        pipeline.run_mutation(
            _PROJECT, tmp_path, [Suite.DEVELOPER, Suite.CODEX], _MUTATION
        )

    assert sorted(stopped) == sorted(
        SuitePaths.of(tmp_path, _PROJECT, suite).root
        for suite in (Suite.DEVELOPER, Suite.CODEX)
    )
    assert len(stops) == 2 and all(stop.is_set() for stop in stops)


def test_dspace_runs_one_pit_worker_per_container_on_its_own_test_installation() -> (
    None
):
    dspace = PROJECTS["DSpace"]

    assert dspace.pit_threads == 1 and dspace.pit_containers > 1
    (installation,) = dspace.pit_private_directories
    assert f"-Ddspace.dir=/workspace/{installation}" in dspace.jvm_args


def test_the_long_suites_split_over_containers() -> None:
    assert PROJECTS["spring-cloud-dataflow"].pit_containers == 4
    assert PROJECTS["dolphinscheduler"].pit_containers == 6
    assert PROJECTS["kylin"].pit_containers == 6
    assert PROJECTS["uaa"].pit_containers == 6


def test_a_pit_run_holds_a_worker_for_each_of_its_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path)
    pool = _pool(tmp_path, workers=3)
    free_during: list[bool] = []

    def run_pit(*_: object, **__: object) -> PitRun:
        # Of 3 workers the run's 2 threads hold 2, so 1 is free and 2 are not.
        with pool.hold(1):
            pass
        blocked = threading.Event()

        def take_two() -> None:
            with pool.hold(2):
                blocked.set()

        taker = threading.Thread(target=take_two, daemon=True)
        taker.start()
        free_during.append(blocked.wait(0.3))
        return _PIT_RUN

    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", run_pit)
    monkeypatch.setattr(pipeline, "parse_mutations", lambda _: [_pit_mutant()])

    pipeline._run_suite_mutation(
        _PROJECT,
        Suite.DEVELOPER,
        paths,
        "api",
        _SETTINGS,
        [_SAMPLED],
        pool,
        _SPACE,
        threading.Event(),
    )

    assert free_during == [False]
    with pool.hold(3):
        pass


def test_suite_mutation_refuses_coverage_measured_with_another_heap(
    tmp_path: Path,
) -> None:
    paths = _write_coverage(
        tmp_path, Suite.DEVELOPER, ["org.demo.Service"], heap="-Xmx8g"
    )

    with pytest.raises(ExecutionError, match="ran with -Xmx8g, not -Xmx16g"):
        pipeline._run_suite_mutation(
            _PROJECT,
            Suite.DEVELOPER,
            paths,
            "api",
            _SETTINGS,
            [_SAMPLED],
            _pool(tmp_path),
            _SPACE,
            threading.Event(),
        )


def test_evomaster_mutants_are_challenged_offline_with_their_targets_jvm_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path, Suite.EVOMASTER)
    containers = _recording_containers(monkeypatch)
    target = replace(TARGETS["hertzbeat"], jvm_args=("-Dtarget=1",))
    monkeypatch.setattr(pipeline, "target_for", lambda _: target)
    resolved: list[RecordingContainer] = []
    monkeypatch.setattr(
        pipeline,
        "resolve_junit_runtime",
        lambda container, *_: resolved.append(container),
    )
    challenged: list[tuple[RecordingContainer, Sequence[str]]] = []

    def run_pit(
        container: RecordingContainer, *_: object, jvm_args: Sequence[str], **__: object
    ) -> PitRun:
        challenged.append((container, jvm_args))
        return _PIT_RUN

    monkeypatch.setattr(pipeline, "run_pit", run_pit)
    monkeypatch.setattr(pipeline, "parse_mutations", lambda _: [_pit_mutant()])
    project = replace(_PROJECT, jvm_args=("-Dproject=1",))

    pipeline._run_suite_mutation(
        project,
        Suite.EVOMASTER,
        paths,
        "api",
        _SETTINGS,
        [_SAMPLED],
        _pool(tmp_path),
        _SPACE,
        threading.Event(),
    )

    (build,) = containers
    assert resolved == [build] and build.network is None
    ((tests, jvm_args),) = challenged
    assert (tests.network, tests.hostname) == ("none", "localhost")
    assert jvm_args == (
        "-Xmx16g",
        "-XX:+StartAttachListener",
        "-Dproject=1",
        "-Dtarget=1",
    )


def test_write_atomically_replaces_the_file_and_leaves_no_partial(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mutation.json"
    path.write_text("old")

    write_atomically(path, "new")

    assert path.read_text() == "new"
    assert [entry.name for entry in tmp_path.iterdir()] == ["mutation.json"]


@pytest.mark.parametrize("name", ["DSpace", "spinnaker"])
def test_projects_whose_jvms_collide_run_one_pit_worker(name: str) -> None:
    assert PROJECTS[name].pit_threads == 1


class _FullDisk:
    """A disk with nothing free until `freed` is set; `waited` is set once work waits."""

    def __init__(self) -> None:
        self.waited = threading.Event()
        self.freed = threading.Event()

    def short(self) -> bool:
        return not self.freed.is_set()

    def wait(self, stop: threading.Event | None = None) -> None:
        self.waited.set()
        assert stop is not None
        while not self.freed.is_set():
            if stop.wait(0.01):
                raise ExecutionError("stopped while waiting for free disk space")


def test_a_pit_run_waits_for_free_disk_space_without_holding_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path)
    pool = _pool(tmp_path, workers=2)
    disk = _FullDisk()
    started = threading.Event()

    def run_pit(*_: object, **__: object) -> PitRun:
        started.set()
        return _PIT_RUN

    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", run_pit)
    monkeypatch.setattr(pipeline, "parse_mutations", lambda _: [_pit_mutant()])
    suite = threading.Thread(
        target=pipeline._run_suite_mutation,
        args=(
            _PROJECT,
            Suite.DEVELOPER,
            paths,
            "api",
            _SETTINGS,
            [_SAMPLED],
            pool,
            cast(FreeSpace, disk),
            threading.Event(),
        ),
    )
    suite.start()

    assert disk.waited.wait(10) and not started.wait(0.2)
    # The whole budget stays free for the runs already under way.
    late = threading.Event()
    deadline = threading.Timer(10, late.set)
    deadline.start()
    with pool.hold(2, late):
        deadline.cancel()
    disk.freed.set()
    suite.join(timeout=10)
    assert started.is_set() and paths.mutation.exists()


def test_a_pit_run_gives_back_workers_it_got_once_the_disk_had_filled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path)
    pool = _pool(tmp_path, workers=2)
    events: list[str] = []

    class FillingDisk:
        """Short of space when the first workers arrive, and not after a wait."""

        def __init__(self) -> None:
            self.shortages = [True, False]

        def wait(self, stop: threading.Event | None = None) -> None:
            # No worker is held during either wait.
            with pool.hold(2):
                events.append("waited")

        def short(self) -> bool:
            events.append("checked")
            return self.shortages.pop(0)

    def run_pit(*_: object, **__: object) -> PitRun:
        events.append("ran")
        return _PIT_RUN

    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", run_pit)
    monkeypatch.setattr(pipeline, "parse_mutations", lambda _: [_pit_mutant()])

    pipeline._run_suite_mutation(
        _PROJECT,
        Suite.DEVELOPER,
        paths,
        "api",
        _SETTINGS,
        [_SAMPLED],
        pool,
        cast(FreeSpace, FillingDisk()),
        threading.Event(),
    )

    assert events == ["waited", "checked", "waited", "checked", "ran"]


def test_a_pit_run_waiting_for_free_disk_space_ends_with_its_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _suite_to_mutate(tmp_path)
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(pipeline, "resolve_junit_runtime", lambda *_: None)
    monkeypatch.setattr(pipeline, "run_pit", lambda *_, **__: pytest.fail("PIT ran"))
    monkeypatch.setattr(pipeline, "stop_containers", lambda _: None)

    with pytest.raises(ExecutionError, match="free disk space"):
        pipeline._run_suite_mutation(
            _PROJECT,
            Suite.DEVELOPER,
            paths,
            "api",
            _SETTINGS,
            [_SAMPLED],
            _pool(tmp_path),
            cast(FreeSpace, _FullDisk()),
            stop,
        )


def test_coverage_and_the_listing_of_mutants_wait_for_free_disk_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    waits: list[tuple[Path, int]] = []

    def wait(space: FreeSpace, stop: threading.Event | None = None) -> None:
        waits.append((space.directory, space.min_free_gb))

    monkeypatch.setattr(FreeSpace, "wait", wait)
    _suite_inputs(monkeypatch)
    monkeypatch.setattr(pipeline, "require_case_sensitive_mount", lambda _: None)
    monkeypatch.setattr(pipeline, "_run_suite_coverage", lambda *_: None)
    output_dir = tmp_path / "out"

    pipeline.run_coverage(
        _PROJECT, _inputs(_FakeInputs()), output_dir, [Suite.DEVELOPER, Suite.CODEX], 7
    )

    # Before each of the four suites' trees is built, then before each suite run.
    assert waits == [(output_dir, 7)] * 6

    waits.clear()
    _recording_suite_mutation(monkeypatch)
    for suite in Suite:
        _write_coverage(output_dir, suite, [])
    pipeline.run_mutation(
        _PROJECT, output_dir, [Suite.DEVELOPER], replace(_MUTATION, min_free_gb=7)
    )

    assert waits == [(output_dir, 7)]
