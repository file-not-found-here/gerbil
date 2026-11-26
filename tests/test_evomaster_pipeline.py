from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from gerbil.evomaster import pipeline, targets
from gerbil.evomaster.pipeline import (
    EVOMASTER_IMAGE,
    RunPaths,
    RunSettings,
    Stage,
    TargetRun,
    apply_harness,
    driver_arguments,
    evomaster_arguments,
    generated_tests,
    prepare_tree,
    read_search_result,
    reset_tracked_sources,
    run_target,
)
from gerbil.evomaster.targets import TARGETS, EvoMasterTarget
from gerbil.execution.build import ModuleClasspath
from gerbil.execution.container import OUTPUT
from gerbil.execution.errors import ExecutionError
from gerbil.execution.sources import SuiteSources
from gerbil.execution.launcher import JUnitRuntime, TestKey, TestStatus
from gerbil.execution.selection import SuiteAnalysis

_COLUMNS = (
    "generatedTests,endpoints,covered2xx,potentialFaults,coveredTargets,"
    "coveredLines,numberOfLines,coveredBranches,numberOfBranches,"
    "sqlHeuristicsEvaluationCount,sqlParsingFailureCount\n"
)
_STATISTICS = _COLUMNS + "2,130,100,2,900,300,1000,50,400,70,3\n"
_SCHEMA: dict[str, Any] = {"paths": {"/a": {"get": {}}, "/b": {"post": {}}}}
_SUITE = """package em.generated;

public class EvoMaster_Test {
    @Test @Timeout(60)
    public void test_0_getOnA() throws Exception {
    }

    @Test @Timeout(60)
    public void test_1_postOnB() throws Exception {
    }
}
"""
# A class whose test probes for SSRF but which has no SSRF fault, as EvoMaster writes it.
_PROBING_SUITE = """package em.generated;

import static org.junit.jupiter.api.Assertions.*;

public class EvoMaster_Test {
    private static String baseUrlOfSut;

    @BeforeAll
    public static void initClass() {
        baseUrlOfSut = controller.startSut();
    }

    @AfterAll
    public static void tearDown() {
        controller.stopSut();
    }

    @Test @Timeout(60)
    public void test_0_getOnA() throws Exception {
        httpCallbackVerifier44399.resetAll();
    }

    @Test @Timeout(60)
    public void test_1_postOnB() throws Exception {
    }
}
"""
_CLASSPATH = ModuleClasspath(
    module="hertzbeat-manager",
    directory="/workspace/hertzbeat-manager",
    entries=["/workspace/hertzbeat-manager/target/test-classes", "/m2/lib.jar"],
    code_directories=[],
    production_directories=[],
)


@dataclass
class _Docker:
    """Stands in for the build, the driver's container and the test launcher."""

    output: Path | None = None
    fail_on: str | None = None
    interrupt_on: str | None = None
    calls: list[str] = field(default_factory=list)
    containers: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    served: str = json.dumps(_SCHEMA)
    suite: str | None = _SUITE
    statistics: str | None = _STATISTICS
    # The outcome of each verification run, in order.
    statuses: list[dict[TestKey, TestStatus]] = field(default_factory=list)
    selected: list[TestKey] = field(default_factory=list)
    search: list[str] = field(default_factory=list)
    jvm_args: list[list[str]] = field(default_factory=list)
    timeouts: dict[str, int | None] = field(default_factory=dict)
    cpus: dict[str, int | None] = field(default_factory=dict)

    def call(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_on == name:
            raise ExecutionError(f"{name} failed")
        if self.interrupt_on == name:
            raise KeyboardInterrupt

    def host(self, path: str) -> Path:
        assert self.output is not None
        return self.output / PurePosixPath(path).relative_to(OUTPUT)

    def run(self, command: Sequence[str]) -> None:
        if "/opt/evomaster/fetch-schema.sh" in command:
            self.call("schema")
            documents = list(
                command[command.index("/opt/evomaster/fetch-schema.sh") + 1 :]
            )
            for destination in documents[1::2]:
                self.host(destination).write_text(self.served)
        elif "/opt/evomaster/evomaster.jar" in command:
            self.call("evomaster")
            self.search = list(command)
            if self.suite is not None:
                suite = self.host(
                    str(OUTPUT / "generated/em/generated/EvoMaster_Test.java")
                )
                suite.parent.mkdir(parents=True)
                suite.write_text(self.suite)
            if self.statistics is not None:
                self.host(str(OUTPUT / "statistics.csv")).write_text(self.statistics)
        elif command[0] == "javac":
            self.call("javac")
        else:
            raise AssertionError(f"unexpected command {command}")


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    docker = _Docker()
    commits = {"head": "abc123", "gerbil": "def456"}

    class _Container:
        """Records the image and network of each container that runs something."""

        def __init__(
            self,
            workspace: Path,
            output: Path,
            jdk: int,
            image: str = "gerbil-execution",
            network: str | None = None,
            hostname: str | None = None,
            cpus: int | None = None,
        ) -> None:
            docker.output = output
            self.arguments = (workspace, output, jdk)
            self.settings = (image, network, hostname)
            self.cpus = cpus

        def offline(self) -> _Container:
            return _Container(
                *self.arguments, self.settings[0], "none", "localhost", self.cpus
            )

        def run(self, command: Sequence[str], **kwargs: Any) -> None:
            docker.containers.append(self.settings)
            docker.run(command)
            docker.timeouts[docker.calls[-1]] = kwargs.get("timeout_seconds")
            docker.cpus[docker.calls[-1]] = self.cpus

    def compile_test_modules(
        container: Any, project: Any, modules: Sequence[str], log: Path
    ) -> list[ModuleClasspath]:
        docker.call("build")
        assert list(modules) == [TARGETS["hertzbeat"].module]
        return [_CLASSPATH]

    def run_tests(
        container: Any,
        classpath: ModuleClasspath,
        runtime: JUnitRuntime,
        tests: Sequence[TestKey],
        **kwargs: Any,
    ) -> dict[TestKey, TestStatus]:
        docker.containers.append(container.settings)
        docker.call("tests")
        docker.selected = list(tests)
        docker.jvm_args.append(list(kwargs["jvm_args"]))
        docker.timeouts["tests"] = kwargs["timeout_seconds"]
        docker.cpus["tests"] = container.cpus
        assert classpath.entries[0] == str(OUTPUT / "verify/classes")
        run = len(docker.jvm_args) - 1
        if run < len(docker.statuses):
            return docker.statuses[run]
        return {test: TestStatus.PASSED for test in tests}

    monkeypatch.setattr(pipeline, "Container", _Container)
    monkeypatch.setattr(pipeline, "compile_test_modules", compile_test_modules)
    monkeypatch.setattr(
        pipeline, "resolve_junit_runtime", lambda *_: JUnitRuntime((), True)
    )
    monkeypatch.setattr(pipeline, "run_tests", run_tests)
    monkeypatch.setattr(pipeline, "build_image", lambda *_: None)
    monkeypatch.setattr(pipeline, "head_commit", lambda _: commits["head"])
    monkeypatch.setattr(pipeline, "gerbil_revision", lambda: commits["gerbil"])
    monkeypatch.setattr(pipeline, "require_case_sensitive_mount", lambda _: None)
    monkeypatch.setattr(
        pipeline, "load_api_tests", lambda _: SuiteAnalysis("abc123", ())
    )
    monkeypatch.setattr(
        pipeline,
        "materialize_workspace",
        lambda _repo, _commit, destination: destination.mkdir(
            parents=True, exist_ok=True
        ),
    )
    monkeypatch.setattr(pipeline, "prepare_tree", lambda *_: docker.call("harness"))
    return {
        "docker": docker,
        "commits": commits,
        "sources": SuiteSources(tmp_path / "outputs", tmp_path / "repos"),
        "output": tmp_path / "out",
    }


def _run(
    environment: dict[str, Any],
    settings: RunSettings | None = None,
    target: EvoMasterTarget | None = None,
) -> TargetRun:
    return run_target(
        target or TARGETS["hertzbeat"],
        environment["sources"],
        environment["output"],
        settings or RunSettings(),
    )


def _paths(environment: dict[str, Any], **kwargs: Any) -> RunPaths:
    return RunPaths.of(environment["output"], TARGETS["hertzbeat"], 1234, **kwargs)


def test_a_full_run_records_each_stage_the_search_and_the_suite_outcome(
    environment: dict[str, Any],
) -> None:
    run = _run(environment)

    assert [outcome.stage for outcome in run.stages] == list(Stage)
    assert run.failed_stage is None
    assert run.finished
    assert run.commit == "abc123"
    assert {outcome.gerbil_commit for outcome in run.stages} == {"def456"}
    assert run.schema_operations == run.scoped_operations == 2
    assert run.search is not None
    assert run.search.generated_tests == 2
    assert run.search.covered_lines == 300
    assert run.verification is not None
    assert run.verification.passed == 2
    assert run.repaired_classes == []
    assert not _paths(environment).originals.exists()
    assert environment["docker"].calls == [
        "harness",
        "build",
        "schema",
        "evomaster",
        "javac",
        "tests",
        "tests",
    ]
    assert environment["docker"].selected == [
        ("em.generated.EvoMaster_Test", "test_0_getOnA"),
        ("em.generated.EvoMaster_Test", "test_1_postOnB"),
    ]
    paths = _paths(environment)
    assert TargetRun.model_validate_json(paths.record.read_text()) == run
    assert json.loads((paths.seed_dir / "schema.json").read_text())["paths"] == (
        _SCHEMA["paths"]
    )
    assert (
        ModuleClasspath.model_validate_json(paths.classpath.read_text()) == _CLASSPATH
    )


def test_the_driver_search_and_suite_run_cut_off_from_every_network(
    environment: dict[str, Any],
) -> None:
    _run(environment)

    containers = environment["docker"].containers
    assert (EVOMASTER_IMAGE, "none", "localhost") in containers
    # Only the builds that download dependencies may reach a network.
    assert all(
        (network, hostname) == ("none", "localhost")
        for image, network, hostname in containers
        if image == EVOMASTER_IMAGE
    )


def test_the_suite_runs_with_the_service_jvm_flags_but_not_the_agents(
    environment: dict[str, Any],
) -> None:
    target = replace(TARGETS["hertzbeat"], jvm_args=("-Xmx2g",))

    _run(environment, target=target)

    first, _ = environment["docker"].jvm_args
    assert first == [*target.project.jvm_args, "-Xmx2g"]


def test_every_stage_waiting_on_the_service_gives_up_eventually(
    environment: dict[str, Any],
) -> None:
    _run(environment, RunSettings(search_timeout_seconds=99))

    timeouts = environment["docker"].timeouts
    assert timeouts["evomaster"] == timeouts["tests"] == 99
    assert timeouts["schema"] is not None


def test_only_the_search_runs_under_the_cpu_cap_its_record_states(
    environment: dict[str, Any],
) -> None:
    run = _run(environment, RunSettings(cpus=6))

    assert run.cpus == 6
    assert environment["docker"].cpus == {
        "schema": None,
        "evomaster": 6,
        "javac": None,
        "tests": None,
    }


def test_the_search_logs_its_heap_beside_the_run_logs(
    environment: dict[str, Any],
) -> None:
    run = _run(environment)

    search = environment["docker"].search
    jvm = search[search.index("/opt/java/jdk17/bin/java") + 1 : search.index("-jar")]
    assert jvm == ["-Xmx16g", "-Xlog:gc:file=/out/logs/evomaster-gc.log:uptime"]
    assert run.finished


def test_verification_gives_each_class_the_callback_servers_its_tests_probe(
    environment: dict[str, Any],
) -> None:
    environment["docker"].suite = _PROBING_SUITE

    run = _run(environment)

    suite = _paths(environment).generated / "em/generated/EvoMaster_Test.java"
    assert run.repaired_classes == ["em.generated.EvoMaster_Test"]
    assert (
        "private static WireMockServer httpCallbackVerifier44399;" in suite.read_text()
    )
    assert "httpCallbackVerifier44399.stop();" in suite.read_text()
    assert run.verification is not None
    assert run.verification.passed == 2


def test_verification_keeps_evomasters_text_of_each_class_it_changes(
    environment: dict[str, Any],
) -> None:
    environment["docker"].suite = _PROBING_SUITE

    _run(environment)

    original = _paths(environment).originals / "em/generated/EvoMaster_Test.java"
    assert original.read_text() == _PROBING_SUITE


def test_a_new_search_drops_the_previous_suites_originals(
    environment: dict[str, Any],
) -> None:
    environment["docker"].suite = _PROBING_SUITE
    _run(environment)
    environment["docker"].suite = _SUITE

    _run(environment)

    assert not _paths(environment).originals.exists()


def test_a_resumed_run_reruns_only_the_stages_from_where_it_resumes(
    environment: dict[str, Any],
) -> None:
    environment["docker"].suite = _PROBING_SUITE
    first = _run(environment)
    docker = environment["docker"]
    docker.calls.clear()
    docker.jvm_args.clear()
    failing = {
        ("em.generated.EvoMaster_Test", "test_0_getOnA"): TestStatus.FAILED,
        ("em.generated.EvoMaster_Test", "test_1_postOnB"): TestStatus.PASSED,
    }
    docker.statuses = [failing, failing]
    environment["commits"]["gerbil"] = "fed789"

    run = _run(environment, RunSettings(resume_from=Stage.VERIFY))

    assert docker.calls == ["javac", "tests", "tests"]
    assert run.stages[:-1] == first.stages[:-1]
    assert run.stages[-1].gerbil_commit == "fed789"
    assert [outcome.stage for outcome in run.stages] == list(Stage)
    assert (run.commit, run.search) == (first.commit, first.search)
    # The suite already holds the additions, which the record still lists.
    assert run.repaired_classes == ["em.generated.EvoMaster_Test"]
    original = _paths(environment).originals / "em/generated/EvoMaster_Test.java"
    assert original.read_text() == _PROBING_SUITE
    assert run.verification is not None
    assert run.verification.failed == 1
    assert run.finished
    assert TargetRun.model_validate_json(_paths(environment).record.read_text()) == run


def test_resuming_clears_what_every_rerun_stage_records(
    environment: dict[str, Any],
) -> None:
    _run(environment)
    environment["docker"].fail_on = "schema"

    run = _run(environment, RunSettings(resume_from=Stage.SCHEMA))

    assert run.failed_stage is Stage.SCHEMA
    assert run.commit == "abc123"
    assert (run.schema_operations, run.search, run.verification) == (None,) * 3


@pytest.mark.parametrize("failing", ["evomaster", None])
def test_resuming_needs_a_run_that_passed_every_earlier_stage(
    environment: dict[str, Any], failing: str | None
) -> None:
    if failing is not None:
        environment["docker"].fail_on = failing
        _run(environment)
    record = _paths(environment).record
    before = record.read_text() if record.exists() else None

    with pytest.raises(ExecutionError):
        _run(environment, RunSettings(resume_from=Stage.VERIFY))

    assert (record.read_text() if record.exists() else None) == before


def test_resuming_keeps_the_search_budget_its_record_states(
    environment: dict[str, Any],
) -> None:
    _run(environment, RunSettings(cpus=6))

    with pytest.raises(ExecutionError, match="6 CPUs"):
        _run(environment, RunSettings(resume_from=Stage.VERIFY))


def test_a_run_cannot_resume_after_the_stage_it_stops_at(
    environment: dict[str, Any],
) -> None:
    _run(environment)

    with pytest.raises(ExecutionError, match="earlier stage"):
        _run(environment, RunSettings(resume_from=Stage.VERIFY, until=Stage.GENERATE))


def test_a_failed_stage_stops_the_run(environment: dict[str, Any]) -> None:
    environment["docker"].fail_on = "build"

    run = _run(environment)

    assert run.failed_stage is Stage.BUILD
    assert run.stages[-1].error is not None and "build failed" in run.stages[-1].error
    assert run.finished
    assert environment["docker"].calls == ["harness", "build"]


def test_each_seed_keeps_its_own_results(environment: dict[str, Any]) -> None:
    _run(environment, RunSettings(seed=1))
    _run(environment, RunSettings(seed=2))

    root = environment["output"] / "hertzbeat"
    assert (root / "seed-1" / "run.json").exists()
    assert (root / "seed-2" / "run.json").exists()


def test_a_probe_leaves_a_full_runs_record_and_suite_alone(
    environment: dict[str, Any],
) -> None:
    _run(environment)
    full = _paths(environment)
    before = full.record.read_text()

    probe = _run(environment, RunSettings(until=Stage.SCHEMA))

    assert [outcome.stage for outcome in probe.stages][-1] is Stage.SCHEMA
    assert full.record.read_text() == before
    assert full.generated.exists()
    assert _paths(environment, until=Stage.SCHEMA).record.exists()


def test_a_schema_that_is_not_json_fails_its_stage(
    environment: dict[str, Any],
) -> None:
    environment["docker"].served = "<html>login</html>"

    run = _run(environment)

    assert run.failed_stage is Stage.SCHEMA


def test_a_schema_without_paths_fails_its_stage(environment: dict[str, Any]) -> None:
    environment["docker"].served = json.dumps({"openapi": "3.0.1"})

    run = _run(environment)

    assert run.failed_stage is Stage.SCHEMA
    error = run.stages[-1].error
    assert error is not None and "did not serve a schema" in error


def test_a_schema_with_no_operations_in_scope_fails_its_stage(
    environment: dict[str, Any],
) -> None:
    target = replace(TARGETS["hertzbeat"], excluded_paths=("/.*",))

    run = _run(environment, target=target)

    assert run.failed_stage is Stage.SCHEMA
    assert run.schema_operations == 2
    assert run.scoped_operations == 0


def test_every_schema_path_is_fetched_and_merged(environment: dict[str, Any]) -> None:
    target = replace(TARGETS["hertzbeat"], schema_paths=("/docs/a", "/docs/b"))

    run = _run(environment, target=target)

    assert run.failed_stage is None
    seed_dir = _paths(environment).seed_dir
    assert (seed_dir / "schema.fetched.docs-a.json").exists()
    assert (seed_dir / "schema.fetched.docs-b.json").exists()
    # The fake serves one document at both paths, whose operations then agree.
    assert run.shadowed_operations == 0


def test_a_run_writing_no_tests_fails_generation(environment: dict[str, Any]) -> None:
    environment["docker"].suite = None

    run = _run(environment)

    assert run.failed_stage is Stage.GENERATE


def test_a_stale_suite_does_not_pass_for_a_rerun_writing_none(
    environment: dict[str, Any],
) -> None:
    _run(environment)
    environment["docker"].suite = None

    run = _run(environment)

    assert run.failed_stage is Stage.GENERATE
    assert not _paths(environment).generated.exists()


def test_unreadable_statistics_fail_generation(environment: dict[str, Any]) -> None:
    environment["docker"].statistics = "generatedTests\n"

    run = _run(environment)

    assert run.failed_stage is Stage.GENERATE
    error = run.stages[-1].error
    assert error is not None and "statistics.csv" in error


def test_a_search_where_nothing_answered_2xx_fails_generation(
    environment: dict[str, Any],
) -> None:
    environment["docker"].statistics = _COLUMNS + "2,130,0,2,900,300,1000,50,400,70,3\n"

    run = _run(environment)

    assert run.failed_stage is Stage.GENERATE
    error = run.stages[-1].error
    assert error is not None and "login" in error


_A = ("em.generated.EvoMaster_Test", "test_0_getOnA")
_B = ("em.generated.EvoMaster_Test", "test_1_postOnB")


def test_failing_tests_are_a_result_and_not_a_failed_verification(
    environment: dict[str, Any],
) -> None:
    outcome = {_A: TestStatus.PASSED, _B: TestStatus.FAILED}
    environment["docker"].statuses = [outcome, outcome]

    run = _run(environment)

    assert run.failed_stage is None
    assert run.verification is not None
    assert (run.verification.passed, run.verification.failed) == (1, 1)
    assert run.verification.unstable == 0


def test_a_test_whose_outcome_changes_between_runs_is_unstable_only(
    environment: dict[str, Any],
) -> None:
    environment["docker"].statuses = [
        {_A: TestStatus.PASSED, _B: TestStatus.PASSED},
        {_A: TestStatus.PASSED, _B: TestStatus.FAILED},
    ]

    run = _run(environment)

    assert run.verification is not None
    assert (run.verification.passed, run.verification.unstable) == (1, 1)
    outcomes = json.loads(
        (_paths(environment).seed_dir / "verify/outcomes.json").read_text()
    )
    assert outcomes == {
        "em.generated.EvoMaster_Test#test_0_getOnA": ["passed", "passed"],
        "em.generated.EvoMaster_Test#test_1_postOnB": ["passed", "failed"],
    }


def test_the_second_run_reorders_each_classs_tests(
    environment: dict[str, Any],
) -> None:
    _run(environment)

    first, second = environment["docker"].jvm_args
    assert second[: len(first)] == first
    (reordering,) = second[len(first) :]
    assert "MethodOrderer$Random" in reordering


def test_tests_that_did_not_run_fail_verification(
    environment: dict[str, Any],
) -> None:
    outcome = {_A: TestStatus.PASSED, _B: TestStatus.NOT_RUN}
    environment["docker"].statuses = [outcome, outcome]

    run = _run(environment)

    assert run.failed_stage is Stage.VERIFY
    assert run.verification is not None and run.verification.not_run == 1


def test_a_suite_with_fewer_tests_than_generated_fails_verification(
    environment: dict[str, Any],
) -> None:
    environment["docker"].statistics = (
        _COLUMNS + "3,130,100,2,900,300,1000,50,400,70,3\n"
    )

    run = _run(environment)

    assert run.failed_stage is Stage.VERIFY
    error = run.stages[-1].error
    assert error is not None and "2 of 3" in error


def test_a_failed_compilation_fails_verification(environment: dict[str, Any]) -> None:
    environment["docker"].fail_on = "javac"

    run = _run(environment)

    assert run.failed_stage is Stage.VERIFY
    assert "tests" not in environment["docker"].calls


def test_a_missing_analysis_is_recorded_as_a_failed_stage(
    environment: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(path: Path) -> SuiteAnalysis:
        raise FileNotFoundError(path)

    monkeypatch.setattr(pipeline, "load_api_tests", missing)

    run = _run(environment)

    assert run.failed_stage is Stage.PREPARE


def test_a_workspace_at_another_commit_is_refused(
    environment: dict[str, Any],
) -> None:
    environment["commits"]["head"] = "def456"

    run = _run(environment)

    assert run.failed_stage is Stage.PREPARE
    assert "harness" not in environment["docker"].calls


def test_an_interrupted_run_reads_as_unfinished(environment: dict[str, Any]) -> None:
    environment["docker"].interrupt_on = "evomaster"

    with pytest.raises(KeyboardInterrupt):
        _run(environment)

    record = TargetRun.model_validate_json(_paths(environment).record.read_text())
    assert not record.finished
    assert [outcome.stage for outcome in record.stages] == [
        Stage.PREPARE,
        Stage.BUILD,
        Stage.SCHEMA,
    ]


def test_an_interruption_before_the_first_stage_ends_is_still_recorded(
    environment: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(_: Path) -> SuiteAnalysis:
        raise KeyboardInterrupt

    monkeypatch.setattr(pipeline, "load_api_tests", interrupted)

    with pytest.raises(KeyboardInterrupt):
        _run(environment)

    record = TargetRun.model_validate_json(_paths(environment).record.read_text())
    assert record.stages == [] and not record.finished


def test_evomaster_searches_the_scoped_schema_through_the_driver() -> None:
    arguments = evomaster_arguments(TARGETS["hertzbeat"], RunSettings("5m", 7))

    assert "--overrideOpenAPIUrl=file:///out/schema.json" in arguments
    assert "--maxTime=5m" in arguments
    assert "--seed=7" in arguments
    assert "--outputFolder=/out/generated" in arguments
    assert "--outputFilePrefix=em.generated.EvoMaster_hertzbeat" in arguments
    assert "--testSuiteSplitType=NONE" in arguments
    assert "--maxTestsPerTestSuite=500" in arguments
    assert "--snapshotInterval=5" in arguments
    assert "--snapshotStatisticsFile=/out/snapshot.csv" in arguments
    # Since 6.0.0, EvoMaster runs black-box unless told otherwise.
    assert "--blackBox=false" in arguments
    # The driver chooses the test framework its module runs.
    assert not any(argument.startswith("--outputFormat") for argument in arguments)


def test_a_targets_own_search_options_follow_the_defaults() -> None:
    target = replace(TARGETS["hertzbeat"], search_arguments=("--a=1", "--b=2"))

    arguments = evomaster_arguments(target, RunSettings())

    assert arguments[-2:] == ["--a=1", "--b=2"]


def test_generated_class_names_are_valid_java_for_hyphenated_targets() -> None:
    target = replace(TARGETS["hertzbeat"], name="kogito-springboot")

    arguments = evomaster_arguments(target, RunSettings())

    assert "--outputFilePrefix=em.generated.EvoMaster_kogito_springboot" in arguments


def test_the_driver_jvm_attaches_evomasters_agent_to_itself() -> None:
    target = replace(TARGETS["hertzbeat"], jvm_args=("-Xmx2g",))

    arguments = driver_arguments(target, _CLASSPATH)

    assert arguments[: len(target.project.jvm_args)] == list(target.project.jvm_args)
    assert "-Xmx2g" in arguments
    assert "-Djdk.attach.allowAttachSelf=true" in arguments
    assert "--add-opens=java.base/java.lang=ALL-UNNAMED" in arguments
    assert arguments[-3:] == ["-cp", ":".join(_CLASSPATH.entries), target.driver]


def test_read_search_result_reads_the_last_row_of_white_box_statistics(
    tmp_path: Path,
) -> None:
    statistics = tmp_path / "statistics.csv"
    statistics.write_text(
        _COLUMNS + "1,1,1,1,1,1,1,1,1,1,1\n" + "5,6,7,8,9,10,11,12,13,14,15\n"
    )

    result = read_search_result(statistics)

    assert result.generated_tests == 5
    assert result.endpoints_2xx == 7
    assert result.covered_targets == 9
    assert (result.covered_lines, result.total_lines) == (10, 11)
    assert (result.covered_branches, result.total_branches) == (12, 13)
    assert (result.sql_heuristics_evaluations, result.sql_parsing_failures) == (14, 15)


def test_generated_tests_are_keyed_by_binary_class_name(tmp_path: Path) -> None:
    suite = tmp_path / "em/generated/EvoMaster_Test.java"
    suite.parent.mkdir(parents=True)
    suite.write_text(_SUITE)

    assert generated_tests(tmp_path) == [
        ("em.generated.EvoMaster_Test", "test_0_getOnA"),
        ("em.generated.EvoMaster_Test", "test_1_postOnB"),
    ]


def test_a_suite_split_into_classes_is_listed_whole(tmp_path: Path) -> None:
    for index in (0, 1):
        suite = tmp_path / f"em/generated/EvoMaster_openvsx_{index}_Test.java"
        suite.parent.mkdir(parents=True, exist_ok=True)
        suite.write_text(_SUITE)

    assert [class_name for class_name, _ in generated_tests(tmp_path)] == [
        "em.generated.EvoMaster_openvsx_0_Test",
        "em.generated.EvoMaster_openvsx_0_Test",
        "em.generated.EvoMaster_openvsx_1_Test",
        "em.generated.EvoMaster_openvsx_1_Test",
    ]


def _git(directory: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "app").mkdir(parents=True)
    (workspace / "app" / "pom.xml").write_text(
        "<project>\n<dependencies>\n</dependencies>\n</project>\n"
    )
    (workspace / ".gitignore").write_text("target/\n")
    for test_file in (
        "app/src/test/java/DevTest.java",
        "app/src/test/resources/seed.sql",
    ):
        (workspace / test_file).parent.mkdir(parents=True, exist_ok=True)
        (workspace / test_file).write_text("developer\n")
    _git(workspace, "init", "--quiet")
    _git(workspace, "add", ".")
    _git(
        workspace,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "--quiet",
        "-m",
        "pinned",
    )
    return workspace


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EvoMasterTarget:
    """HertzBeat's target, with its harness replaced by one for `repository`."""
    monkeypatch.setattr(targets, "DOCKER_DIRECTORY", tmp_path / "docker")
    target = TARGETS["hertzbeat"]
    driver = target.files / "app/src/test/java/em/embedded/app/Driver.java"
    driver.parent.mkdir(parents=True)
    driver.write_text("class Driver {}\n")
    target.build_patch.write_text(
        "diff --git a/app/pom.xml b/app/pom.xml\n"
        "--- a/app/pom.xml\n"
        "+++ b/app/pom.xml\n"
        "@@ -1,4 +1,5 @@\n"
        " <project>\n"
        " <dependencies>\n"
        "+<dependency>evomaster</dependency>\n"
        " </dependencies>\n"
        " </project>\n"
    )
    return target


def test_the_harness_adds_the_driver_and_its_build_changes(
    repository: Path, harness: EvoMasterTarget
) -> None:
    apply_harness(harness, repository)

    assert (repository / "app/src/test/java/em/embedded/app/Driver.java").exists()
    assert "evomaster" in (repository / "app/pom.xml").read_text()


def test_a_harness_that_does_not_apply_is_reported(
    repository: Path, harness: EvoMasterTarget
) -> None:
    (repository / "app/pom.xml").write_text("<project/>\n")

    with pytest.raises(ExecutionError, match="does not apply"):
        apply_harness(harness, repository)


def test_resetting_undoes_a_harness_but_keeps_build_output(
    repository: Path, harness: EvoMasterTarget
) -> None:
    apply_harness(harness, repository)
    built = repository / "app/target/classes/App.class"
    built.parent.mkdir(parents=True)
    built.write_text("compiled")

    reset_tracked_sources(repository)

    assert not (repository / "app/src/test/java/em").exists()
    assert "evomaster" not in (repository / "app/pom.xml").read_text()
    assert built.exists()
    apply_harness(harness, repository)


def test_the_tree_is_the_agents_starting_point_plus_the_harness(
    repository: Path, harness: EvoMasterTarget, tmp_path: Path
) -> None:
    for test_file in (
        "app/src/test/java/DevTest.java",
        "app/src/test/resources/seed.sql",
    ):
        (repository / test_file).unlink()
    clearing = tmp_path / "outputs/codex-raw/x__codex__hertzbeat/output"
    clearing.mkdir(parents=True)
    (clearing / "test_clearing.patch").write_text(
        subprocess.run(
            ["git", "-C", str(repository), "diff"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    _git(repository, "checkout", "--quiet", "--", ".")
    target = replace(
        harness,
        project=replace(harness.project, workspace_restores=(), workspace_copies=()),
        restores=("app/src/test/resources/seed.sql",),
    )

    prepare_tree(target, SuiteSources(tmp_path / "outputs", tmp_path), repository)

    assert not (repository / "app/src/test/java/DevTest.java").exists()
    assert (repository / "app/src/test/resources/seed.sql").exists()
    assert (repository / "app/src/test/java/em/embedded/app/Driver.java").exists()
    assert "evomaster" in (repository / "app/pom.xml").read_text()


def test_gerbils_revision_is_its_commit_marked_when_tracked_files_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *arguments],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    git("init", "-q")
    (tmp_path / "README.md").write_text("gerbil\n")
    git("add", "README.md")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    monkeypatch.setattr(pipeline, "_GERBIL_ROOT", tmp_path)
    commit = git("rev-parse", "HEAD")

    (tmp_path / "notes.md").write_text("untracked\n")
    assert pipeline.gerbil_revision() == commit
    (tmp_path / "README.md").write_text("changed\n")
    assert pipeline.gerbil_revision() == f"{commit}-dirty"
