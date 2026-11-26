from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from gerbil.execution.build import ModuleClasspath
from gerbil.execution.launcher import JUnitRuntime
from gerbil.execution.mutation import (
    PitMutant,
    PitRun,
    PitSettings,
    attribute_mutants,
    list_mutants,
    parse_mutations,
    parse_pit_test_name,
    run_pit,
)
from gerbil.execution.errors import ExecutionError
from tests.execution_fakes import RecordingContainer, recording_container

_JUPITER = (
    "org.demo.UserApiTest.[engine:junit-jupiter]/[class:org.demo.UserApiTest]"
    "/[method:createsUser()]"
)
_MUTATIONS = f"""<?xml version="1.0" encoding="UTF-8"?>
<mutations partial="false">
<mutation detected='true' status='KILLED' numberOfTestsRun='2'>
<sourceFile>UserService.java</sourceFile>
<mutatedClass>org.demo.UserService</mutatedClass>
<mutatedMethod>create</mutatedMethod>
<methodDescription>(Ljava/lang/String;)V</methodDescription>
<lineNumber>21</lineNumber>
<mutator>org.pitest.mutationtest.engine.gregor.mutators.VoidMethodCallMutator</mutator>
<indexes><index>7</index></indexes>
<blocks><block>1</block></blocks>
<killingTests>{_JUPITER}</killingTests>
<succeedingTests>org.demo.OtherTest.lists(org.demo.OtherTest)</succeedingTests>
<description>removed call</description>
</mutation>
<mutation detected='false' status='NO_COVERAGE' numberOfTestsRun='0'>
<sourceFile>UserService.java</sourceFile>
<mutatedClass>org.demo.UserService</mutatedClass>
<mutatedMethod>delete</mutatedMethod>
<methodDescription>()V</methodDescription>
<lineNumber>30</lineNumber>
<mutator>org.pitest.mutationtest.engine.gregor.mutators.VoidMethodCallMutator</mutator>
<indexes><index>2</index><index>3</index></indexes>
<blocks><block>0</block></blocks>
<killingTests/>
<succeedingTests/>
<description>removed call</description>
</mutation>
</mutations>
"""


def test_parse_mutations_reads_full_matrix(tmp_path: Path) -> None:
    xml_file = tmp_path / "mutations.xml"
    xml_file.write_text(_MUTATIONS)

    killed, uncovered = parse_mutations(xml_file)

    assert killed == PitMutant(
        mutant_id=(
            "org.demo.UserService|create|(Ljava/lang/String;)V|"
            "org.pitest.mutationtest.engine.gregor.mutators.VoidMethodCallMutator|7"
        ),
        mutated_class="org.demo.UserService",
        mutated_method="create",
        line=21,
        mutator="org.pitest.mutationtest.engine.gregor.mutators.VoidMethodCallMutator",
        status="KILLED",
        killing_tests=[_JUPITER],
        succeeding_tests=["org.demo.OtherTest.lists(org.demo.OtherTest)"],
    )
    assert uncovered.mutant_id.endswith("|2,3")
    assert uncovered.status == "NO_COVERAGE"
    assert uncovered.killing_tests == []


def test_parse_pit_test_name_handles_junit_platform_and_junit4() -> None:
    assert parse_pit_test_name(_JUPITER) == ("org.demo.UserApiTest", "createsUser")
    assert parse_pit_test_name(
        "org.demo.AuditTests.[engine:junit-jupiter]/[class:org.demo.AuditTests]"
        "/[nested-class:AsAdmin]/[method:deletes(org.springframework.test.web."
        "servlet.MockMvc)]"
    ) == ("org.demo.AuditTests$AsAdmin", "deletes")
    assert parse_pit_test_name(
        "org.demo.PagingTest.[engine:junit-jupiter]/[class:org.demo.PagingTest]"
        "/[test-template:pages(int)]/[test-template-invocation:#2]"
    ) == ("org.demo.PagingTest", "pages")
    assert parse_pit_test_name(
        "org.demo.LegacyIT.[engine:junit-vintage]/[runner:org.demo.LegacyIT]"
        "/[test:findsItem(org.demo.LegacyIT)]"
    ) == ("org.demo.LegacyIT", "findsItem")
    assert parse_pit_test_name("org.demo.LegacyIT.findsItem(org.demo.LegacyIT)") == (
        "org.demo.LegacyIT",
        "findsItem",
    )
    assert parse_pit_test_name("unrecognized") is None


def _mutant(
    mutant_id: str,
    status: str,
    killing: list[str] | None = None,
    succeeding: list[str] | None = None,
) -> PitMutant:
    return PitMutant(
        mutant_id=mutant_id,
        mutated_class="org.demo.UserService",
        mutated_method="create",
        line=1,
        mutator="M",
        status=status,
        killing_tests=killing or [],
        succeeding_tests=succeeding or [],
    )


def _junit4(class_name: str, method: str) -> str:
    return f"{class_name}.{method}({class_name})"


def test_attribute_mutants_credits_selected_tests_only() -> None:
    selected = {("org.demo.ApiTest", "creates")}
    records = attribute_mutants(
        [
            _mutant(
                "selected-kill",
                "KILLED",
                [_junit4("org.demo.ApiTest", "creates"), _junit4("org.demo.Unit", "x")],
            ),
            # PIT selects methods by bare name, so a same-named unit test can run.
            _mutant("other-kill", "KILLED", [_junit4("org.demo.Unit", "creates")]),
            _mutant(
                "survived",
                "SURVIVED",
                succeeding=[
                    _junit4("org.demo.ApiTest", "creates"),
                    _junit4("org.demo.Unit", "x"),
                ],
            ),
            _mutant("uncovered", "NO_COVERAGE"),
            _mutant("timeout", "TIMED_OUT"),
            _mutant("out-of-memory", "MEMORY_ERROR"),
            # Covered only by tests of the selected classes that are not selected, which
            # PIT runs for coverage but against no mutant.
            _mutant("unselected-tests-only", "RUN_ERROR"),
            _mutant("non-viable", "NON_VIABLE"),
        ],
        selected,
    )

    outcomes = {
        r.mutant_id: (r.killed, r.covered, r.killing_tests, r.surviving_tests)
        for r in records
    }
    assert outcomes == {
        "selected-kill": (True, True, ["org.demo.ApiTest#creates"], []),
        "other-kill": (False, False, [], []),
        "survived": (False, True, [], ["org.demo.ApiTest#creates"]),
        "uncovered": (False, False, [], []),
        "timeout": (True, True, [], []),
        "out-of-memory": (True, True, [], []),
        "unselected-tests-only": (False, False, [], []),
        "non-viable": (False, False, [], []),
    }


def test_a_method_with_a_killing_invocation_is_not_also_a_surviving_test() -> None:
    factory = (
        "[engine:junit-jupiter]/[class:org.demo.ApiTest]/[test-factory:routes()]"
        "/[dynamic-test:#{}]"
    )

    (record,) = attribute_mutants(
        [
            _mutant(
                "m",
                "KILLED",
                [factory.format(1)],
                succeeding=[factory.format(2), _junit4("org.demo.ApiTest", "lists")],
            )
        ],
        {("org.demo.ApiTest", "routes"), ("org.demo.ApiTest", "lists")},
    )

    assert record.killing_tests == ["org.demo.ApiTest#routes"]
    assert record.surviving_tests == ["org.demo.ApiTest#lists"]


def test_attribute_mutants_merges_runs_from_several_modules() -> None:
    selected = {("org.a.ApiTest", "x"), ("org.b.ApiTest", "y")}

    (record,) = attribute_mutants(
        [
            _mutant("m", "SURVIVED", succeeding=[_junit4("org.a.ApiTest", "x")]),
            _mutant("m", "KILLED", [_junit4("org.b.ApiTest", "y")]),
            _mutant("m", "NO_COVERAGE"),
        ],
        selected,
    )

    assert (record.status, record.killed, record.covered) == ("KILLED", True, True)
    assert record.killing_tests == ["org.b.ApiTest#y"]
    assert record.surviving_tests == ["org.a.ApiTest#x"]


def test_a_module_whose_selected_tests_ran_the_mutant_outranks_one_whose_did_not() -> (
    None
):
    (record,) = attribute_mutants(
        [
            _mutant("m", "RUN_ERROR"),
            _mutant("m", "SURVIVED", succeeding=[_junit4("org.a.ApiTest", "x")]),
        ],
        {("org.a.ApiTest", "x")},
    )

    assert (record.status, record.killed, record.covered) == ("SURVIVED", False, True)


def _write_report(
    container: RecordingContainer, command: list[str], workdir: PurePosixPath
) -> None:
    argfile = container.host_path(PurePosixPath(command[-1].removeprefix("@")))
    (argfile.parent / "mutations.xml").write_text("<mutations/>")


def test_run_pit_targets_top_level_classes_and_method_names(tmp_path: Path) -> None:
    container = recording_container(tmp_path, _write_report)
    classpath = ModuleClasspath(
        module="api",
        directory="/workspace/api",
        entries=[
            "/workspace/api/target/test-classes",
            "/workspace/api/target/classes",
            "/workspace/core/target/classes",
        ],
        code_directories=[
            "/workspace/api/target/classes",
            "/workspace/core/target/classes",
        ],
        production_directories=[],
    )

    run_pit(
        container,
        classpath,
        JUnitRuntime(("/launcher.jar",), platform_native=True),
        [("org.demo.AuditTests$AsAdmin", "deletes"), ("org.demo.UserApiTest", "lists")],
        ["org.demo.UserService", "org.demo.UserService$1"],
        mutable_directories=["/workspace/api/target/classes"],
        jvm_args=["--add-opens=a/b=C"],
        settings=PitSettings(threads=2, timeout_seconds=90),
        report_directory=PurePosixPath("/out/pit/api"),
        log=tmp_path / "log",
    )

    ((command, workdir),) = container.commands
    assert command == [
        "java",
        "-cp",
        "/opt/gerbil/lib/pitest/*:/opt/gerbil/lib/pitest-junit5/pitest-junit5-plugin.jar"
        ":/launcher.jar",
        "@/out/pit/api/pit.args",
    ]
    assert workdir == PurePosixPath("/workspace/api")
    arguments = [
        line.strip('"') for line in (tmp_path / "pit/api/pit.args").read_text().split()
    ]
    option = dict(zip(arguments[1::2], arguments[2::2]))
    assert option["--targetTests"] == "org.demo.AuditTests,org.demo.UserApiTest"
    assert option["--includedTestMethods"] == "deletes,lists"
    assert option["--targetClasses"] == "org.demo.UserService,org.demo.UserService$1"
    assert option["--mutableCodePaths"] == "/workspace/api/target/classes"
    assert option["--argLine"] == "--add-opens=a/b=C"
    assert option["--threads"] == "2"
    assert option["--timeoutConst"] == "90000"
    assert "--fullMutationMatrix=true" in arguments
    assert (tmp_path / "pit/api/classpath.txt").read_text().splitlines() == (
        classpath.entries
    )


_MODULE = ModuleClasspath(
    module="api",
    directory="/workspace/api",
    entries=[],
    code_directories=["/workspace/api/target/classes"],
    production_directories=[],
)


def _run_pit(
    container: RecordingContainer,
    tmp_path: Path,
    kept_mutants: list[str] | None = None,
) -> PitRun:
    return run_pit(
        container,
        _MODULE,
        JUnitRuntime(("/launcher.jar",), platform_native=True),
        [("org.demo.UserApiTest", "lists")],
        ["org.demo.UserService"],
        mutable_directories=["/workspace/api/target/classes"],
        jvm_args=[],
        settings=PitSettings(threads=3, timeout_seconds=90),
        report_directory=PurePosixPath("/out/pit/api"),
        log=tmp_path / "log",
        kept_mutants=kept_mutants,
    )


def test_run_pit_keeps_only_the_listed_mutants_when_given_them(tmp_path: Path) -> None:
    container = recording_container(tmp_path, _write_report)
    kept = [
        "org.demo.UserService|create|(I)V|M|7",
        "org.demo.UserService|drop|()V|M|2,3",
    ]

    run = _run_pit(container, tmp_path, kept)

    ((command, _),) = container.commands
    assert command[2] == (
        "/opt/gerbil/lib/pitest/*:/opt/gerbil/lib/mutant-filter"
        ":/opt/gerbil/lib/pitest-junit5/pitest-junit5-plugin.jar:/launcher.jar"
    )
    arguments = [
        line.strip('"') for line in (tmp_path / "pit/api/pit.args").read_text().split()
    ]
    option = dict(zip(arguments[1::2], arguments[2::2]))
    assert option["--features"] == "+KEEPLISTED(file[/out/pit/api/kept-mutants.txt])"
    assert (tmp_path / "pit/api/kept-mutants.txt").read_text().splitlines() == kept
    assert run == PitRun(
        module="api", threads=3, seconds=run.seconds, peak_memory_bytes=2048
    )


def test_run_pit_mutates_every_mutant_of_its_classes_unless_given_some(
    tmp_path: Path,
) -> None:
    container = recording_container(tmp_path, _write_report)

    _run_pit(container, tmp_path)

    ((command, _),) = container.commands
    assert "mutant-filter" not in command[2]
    assert "KEEPLISTED" not in (tmp_path / "pit/api/pit.args").read_text()
    assert not (tmp_path / "pit/api/kept-mutants.txt").exists()


def test_run_pit_refuses_results_of_a_run_the_kernel_killed_a_process_of(
    tmp_path: Path,
) -> None:
    container = replace(recording_container(tmp_path, _write_report), memory_kills=2)

    with pytest.raises(ExecutionError, match="killed a process for want of memory"):
        _run_pit(container, tmp_path)


def test_run_pit_blames_memory_when_the_kernel_killed_pit_itself(
    tmp_path: Path,
) -> None:
    def killed(*_: object) -> None:
        raise ExecutionError("java exited with 137")

    container = replace(recording_container(tmp_path, killed), memory_kills=1)

    with pytest.raises(ExecutionError, match="killed a process for want of memory"):
        _run_pit(container, tmp_path)


def test_run_pit_reports_pits_own_failure_when_no_process_was_killed(
    tmp_path: Path,
) -> None:
    def failed(*_: object) -> None:
        raise ExecutionError("java exited with 1")

    with pytest.raises(ExecutionError, match="java exited with 1"):
        _run_pit(recording_container(tmp_path, failed), tmp_path)


def test_run_pit_refuses_results_when_the_container_reports_no_memory_kills(
    tmp_path: Path,
) -> None:
    # A report an earlier run left must not vouch for this one.
    stale = tmp_path / "pit/api/memory.events"
    stale.parent.mkdir(parents=True)
    stale.write_text("oom_kill 0\n")
    container = replace(recording_container(tmp_path, _write_report), memory_kills=None)

    with pytest.raises(ExecutionError, match="did not report its memory kills"):
        _run_pit(container, tmp_path)


def test_run_pit_uses_core_junit4_support_without_junit_platform(
    tmp_path: Path,
) -> None:
    container = recording_container(tmp_path, _write_report)

    run_pit(
        container,
        ModuleClasspath(
            module=".",
            directory="/workspace",
            entries=[],
            code_directories=[],
            production_directories=[],
        ),
        JUnitRuntime(("/opt/gerbil/lib/junit4/*",), platform_native=False),
        [("org.demo.LegacyIT", "finds")],
        ["org.demo.Service"],
        mutable_directories=[],
        jvm_args=[],
        settings=PitSettings(threads=1, timeout_seconds=90),
        report_directory=PurePosixPath("/out/pit/root"),
        log=tmp_path / "log",
    )

    ((command, _),) = container.commands
    assert command[2] == "/opt/gerbil/lib/pitest/*"
    assert "--argLine" not in (tmp_path / "pit/root/pit.args").read_text()


def test_run_pit_fails_when_pit_writes_no_report(tmp_path: Path) -> None:
    with pytest.raises(ExecutionError, match="no mutations.xml"):
        run_pit(
            recording_container(tmp_path),
            ModuleClasspath(
                module=".",
                directory="/workspace",
                entries=[],
                code_directories=[],
                production_directories=[],
            ),
            JUnitRuntime((), platform_native=True),
            [("org.demo.ApiTest", "creates")],
            ["org.demo.Service"],
            mutable_directories=[],
            jvm_args=[],
            settings=PitSettings(threads=1, timeout_seconds=90),
            report_directory=PurePosixPath("/out/pit/root"),
            log=tmp_path / "log",
        )


def test_list_mutants_runs_no_test_and_reads_every_mutant(tmp_path: Path) -> None:
    def write_mutations(
        container: RecordingContainer, command: list[str], workdir: PurePosixPath
    ) -> None:
        argfile = container.host_path(PurePosixPath(command[-1].removeprefix("@")))
        (argfile.parent / "mutations.xml").write_text(_MUTATIONS)

    container = recording_container(tmp_path, write_mutations)
    classpath = ModuleClasspath(
        module=".",
        directory="/workspace",
        entries=[],
        code_directories=[],
        production_directories=["/workspace/target/classes"],
    )

    mutants = list_mutants(
        container,
        classpath,
        JUnitRuntime((), platform_native=True),
        ["org.demo.UserService"],
        mutable_directories=["/workspace/target/classes"],
        settings=PitSettings(threads=2, timeout_seconds=90),
        report_directory=PurePosixPath("/out/service-mutants"),
        log=tmp_path / "log",
    )

    assert [mutant.split("|")[1] for mutant in mutants] == ["create", "delete"]
    arguments = [
        line.strip('"')
        for line in (tmp_path / "service-mutants/pit.args").read_text().split()
    ]
    option = dict(zip(arguments[1::2], arguments[2::2]))
    assert (option["--targetTests"], option["--includedTestMethods"]) == (
        "gerbil.NoTests",
        "none",
    )
    assert option["--mutableCodePaths"] == "/workspace/target/classes"
    assert "--argLine" not in arguments
