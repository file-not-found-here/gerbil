"""Run PIT over the passing selected tests, attribute each mutant's kills to them, and
list every mutant of a service's classes."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from gerbil.execution.build import ModuleClasspath
from gerbil.execution.container import (
    MEMORY_EVENTS,
    TOOLS,
    Container,
    clear_memory_report,
    memory_kills,
    peak_memory,
    write_argfile,
)
from gerbil.execution.errors import ExecutionError
from gerbil.execution.launcher import JUnitRuntime, TestKey, test_id

# A worker PIT stopped while a test ran the mutant, which prior work counts as a kill.
# PIT names no test for it, so the suite is credited.
_STOPPED_STATUSES = frozenset({"TIMED_OUT", "MEMORY_ERROR"})
# When module runs disagree on a mutant, the earliest status here wins; a mutant the
# JVM rejects is rejected in every run that loads it. RUN_ERROR is what PIT gives,
# without running a test, a mutant that the selected test classes run only outside
# their selected tests: in tests left out of the selection, or in a class's set-up,
# such as the service EvoMaster's tests start. Such a mutant is neither covered nor
# killed, like one a worker died on for a reason other than time or memory.
_STATUS_PRECEDENCE = (
    "NON_VIABLE",
    "KILLED",
    "TIMED_OUT",
    "MEMORY_ERROR",
    "SURVIVED",
    "RUN_ERROR",
    "NO_COVERAGE",
)

_PIT_LIBRARIES = str(TOOLS / "lib" / "pitest" / "*")
_PIT_JUNIT5_PLUGIN = str(TOOLS / "lib" / "pitest-junit5" / "pitest-junit5-plugin.jar")
# GerbilMutantFilter, which provides PIT's KEEPLISTED feature.
_MUTANT_FILTER = str(TOOLS / "lib" / "mutant-filter")
_KEPT_MUTANTS = "kept-mutants.txt"
_PLATFORM_CLASS = re.compile(r"\[class:([^\]]+)\]")
_PLATFORM_NESTED_CLASS = re.compile(r"\[nested-class:([^\]]+)\]")
_PLATFORM_METHOD = re.compile(r"\[(?:method|test-template|test-factory):([^(\]]+)\(")
_VINTAGE_TEST = re.compile(r"\[test:([^(\]]+)\(([^)\]]+)\)\]")
_JUNIT4_TEST = re.compile(r"\.([^.(]+)\(([^)]+)\)$")
# A test filter no class matches, so PIT lists mutants without running a test.
_NO_TESTS: TestKey = ("gerbil.NoTests", "none")


@dataclass(frozen=True)
class PitSettings:
    """`timeout_seconds` is PIT's allowance beyond a test's baseline time.

    Every fresh PIT worker restarts the JVM and any Spring context before its first
    test, so the allowance must exceed that startup or PIT reports false timeouts.
    """

    threads: int
    timeout_seconds: int


class PitRun(BaseModel):
    """One PIT run's cost; `peak_memory_bytes` is its container's, when the kernel tells."""

    module: str
    threads: int
    seconds: float
    peak_memory_bytes: int | None


class PitMutant(BaseModel):
    mutant_id: str
    mutated_class: str
    mutated_method: str
    line: int
    mutator: str
    status: str
    killing_tests: list[str]
    succeeding_tests: list[str]


class MutantRecord(BaseModel):
    """A mutant's outcome against one suite's selected tests; `status` is PIT's.

    `surviving_tests` ran the mutant and passed, so with `killing_tests` they are the
    selected tests that reached it. A method of several invocations, such as a test
    factory, kills when any of them does. A mutant whose worker PIT stopped names
    neither.
    """

    mutant_id: str
    mutated_class: str
    mutated_method: str
    line: int
    mutator: str
    status: str
    killed: bool
    covered: bool
    killing_tests: list[str]
    surviving_tests: list[str]


def run_pit(
    container: Container,
    classpath: ModuleClasspath,
    runtime: JUnitRuntime,
    tests: Sequence[TestKey],
    target_classes: Sequence[str],
    *,
    mutable_directories: Sequence[str],
    jvm_args: Sequence[str],
    settings: PitSettings,
    report_directory: PurePosixPath,
    log: Path,
    kept_mutants: Sequence[str] | None = None,
) -> PitRun:
    """Mutate `target_classes` found in `mutable_directories` and challenge each mutant
    with every covering test; of their mutants, only `kept_mutants` when given.

    Fails if the kernel killed a process for want of memory, since PIT counts a
    worker that dies as a kill.
    """
    host_directory = container.host_path(report_directory)
    host_directory.mkdir(parents=True, exist_ok=True)
    (host_directory / "mutations.xml").unlink(missing_ok=True)
    clear_memory_report(host_directory)
    (host_directory / "classpath.txt").write_text(
        "\n".join(classpath.entries), encoding="utf-8"
    )
    arguments = [
        "org.pitest.mutationtest.commandline.MutationCoverageReport",
        "--reportDir",
        str(report_directory),
        "--classPathFile",
        str(report_directory / "classpath.txt"),
        "--mutableCodePaths",
        ",".join(mutable_directories),
        # Required by PIT's command line even though XML reports never read sources.
        "--sourceDirs",
        classpath.directory,
        "--targetClasses",
        ",".join(target_classes),
        # JUnit 5 discovers nested test classes through their top-level class.
        "--targetTests",
        ",".join(sorted({class_name.split("$")[0] for class_name, _ in tests})),
        # PIT matches included methods by bare name; kills by other tests that
        # share a name are dropped when attributing mutants.
        "--includedTestMethods",
        ",".join(sorted({method for _, method in tests})),
        "--outputFormats",
        "XML",
        "--fullMutationMatrix=true",
        "--timestampedReports=false",
        "--skipFailingTests=true",
        "--failWhenNoMutations=false",
        "--threads",
        str(settings.threads),
        "--timeoutConst",
        str(settings.timeout_seconds * 1000),
    ]
    if jvm_args:
        arguments += ["--argLine", " ".join(jvm_args)]
    launch_classpath = [_PIT_LIBRARIES]
    if kept_mutants is not None:
        (host_directory / _KEPT_MUTANTS).write_text(
            "".join(f"{mutant_id}\n" for mutant_id in kept_mutants), encoding="utf-8"
        )
        arguments += [
            "--features",
            f"+KEEPLISTED(file[{report_directory / _KEPT_MUTANTS}])",
        ]
        launch_classpath.append(_MUTANT_FILTER)
    write_argfile(host_directory / "pit.args", arguments)
    if runtime.platform_native:
        launch_classpath += [_PIT_JUNIT5_PLUGIN, *runtime.entries]
    started = time.monotonic()
    try:
        container.run(
            [
                "java",
                "-cp",
                ":".join(launch_classpath),
                f"@{report_directory / 'pit.args'}",
            ],
            log=log,
            workdir=PurePosixPath(classpath.directory),
            memory_report=report_directory,
        )
    except ExecutionError as error:
        # A PIT the kernel killed fails for the same reason its workers would.
        if memory_kills(host_directory):
            raise _memory_kill_error(log) from error
        raise
    seconds = time.monotonic() - started
    kills = memory_kills(host_directory)
    if kills is None:
        raise ExecutionError(
            f"the container did not report its memory kills ({MEMORY_EVENTS}, "
            f"cgroup v2), so PIT's kills cannot be trusted; see {log}"
        )
    if kills:
        raise _memory_kill_error(log)
    # PIT exits successfully after printing usage for invalid arguments.
    if not (host_directory / "mutations.xml").exists():
        raise ExecutionError(f"PIT wrote no mutations.xml; see {log}")
    return PitRun(
        module=classpath.module,
        threads=settings.threads,
        seconds=round(seconds, 1),
        peak_memory_bytes=peak_memory(host_directory),
    )


def _memory_kill_error(log: Path) -> ExecutionError:
    return ExecutionError(
        "the kernel killed a process for want of memory during PIT, which PIT may "
        f"have counted as a kill; run it with fewer workers; see {log}"
    )


def list_mutants(
    container: Container,
    classpath: ModuleClasspath,
    runtime: JUnitRuntime,
    target_classes: Sequence[str],
    *,
    mutable_directories: Sequence[str],
    settings: PitSettings,
    report_directory: PurePosixPath,
    log: Path,
) -> list[str]:
    """The IDs of every mutant PIT makes of `target_classes`, found without a test run."""
    run_pit(
        container,
        classpath,
        runtime,
        [_NO_TESTS],
        target_classes,
        mutable_directories=mutable_directories,
        jvm_args=(),
        settings=settings,
        report_directory=report_directory,
        log=log,
    )
    mutants = parse_mutations(container.host_path(report_directory / "mutations.xml"))
    return sorted({mutant.mutant_id for mutant in mutants})


def parse_mutations(xml_file: Path) -> list[PitMutant]:
    mutants = []
    for element in ElementTree.parse(xml_file).getroot().iter("mutation"):
        fields = {child.tag: (child.text or "") for child in element}
        indexes = ",".join(index.text or "" for index in element.iter("index"))
        killing = fields.get("killingTests") or fields.get("killingTest") or ""
        mutants.append(
            PitMutant(
                mutant_id="|".join(
                    (
                        fields["mutatedClass"],
                        fields["mutatedMethod"],
                        fields["methodDescription"],
                        fields["mutator"],
                        indexes,
                    )
                ),
                mutated_class=fields["mutatedClass"],
                mutated_method=fields["mutatedMethod"],
                line=int(fields["lineNumber"]),
                mutator=fields["mutator"],
                status=element.attrib["status"],
                killing_tests=_split_tests(killing),
                succeeding_tests=_split_tests(fields.get("succeedingTests", "")),
            )
        )
    return mutants


def _split_tests(value: str) -> list[str]:
    return [name for name in value.split("|") if name]


def parse_pit_test_name(name: str) -> TestKey | None:
    """(binary class, method) of a PIT test name from JUnit 4 or the JUnit Platform."""
    if "[engine:" in name:
        vintage = _VINTAGE_TEST.search(name)
        if vintage:
            return vintage.group(2), vintage.group(1)
        class_match = _PLATFORM_CLASS.search(name)
        method_match = _PLATFORM_METHOD.search(name)
        if not class_match or not method_match:
            return None
        nested = "".join(
            f"${nested}" for nested in _PLATFORM_NESTED_CLASS.findall(name)
        )
        return class_match.group(1) + nested, method_match.group(1)
    junit4 = _JUNIT4_TEST.search(name)
    return (junit4.group(2), junit4.group(1)) if junit4 else None


def attribute_mutants(
    mutants: Iterable[PitMutant], selected: set[TestKey]
) -> list[MutantRecord]:
    """Merge a suite's per-module PIT results, crediting only its selected tests.

    A mutant whose worker PIT stopped for time or memory names no test and is credited
    to the suite; per-status counts in the summary expose such cases.
    """
    records: dict[str, MutantRecord] = {}
    for mutant in mutants:
        killers = _selected(mutant.killing_tests, selected)
        survivors = _selected(mutant.succeeding_tests, selected)
        unattributed = mutant.status in _STOPPED_STATUSES
        record = records.setdefault(
            mutant.mutant_id,
            MutantRecord(
                mutant_id=mutant.mutant_id,
                mutated_class=mutant.mutated_class,
                mutated_method=mutant.mutated_method,
                line=mutant.line,
                mutator=mutant.mutator,
                status=mutant.status,
                killed=False,
                covered=False,
                killing_tests=[],
                surviving_tests=[],
            ),
        )
        record.status = min(record.status, mutant.status, key=_precedence)
        record.killed = record.killed or bool(killers) or unattributed
        record.covered = (
            record.covered or bool(killers) or bool(survivors) or unattributed
        )
        record.killing_tests = sorted(set(record.killing_tests) | killers)
        record.surviving_tests = sorted(
            (set(record.surviving_tests) | survivors) - set(record.killing_tests)
        )
    return sorted(records.values(), key=lambda record: record.mutant_id)


def _precedence(status: str) -> int:
    return (
        _STATUS_PRECEDENCE.index(status)
        if status in _STATUS_PRECEDENCE
        else len(_STATUS_PRECEDENCE)
    )


def _selected(names: Iterable[str], selected: set[TestKey]) -> set[str]:
    keys = (parse_pit_test_name(name) for name in names)
    return {test_id(key) for key in keys if key in selected}
