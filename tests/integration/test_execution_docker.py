from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gerbil.execution.build import (
    class_names,
    compile_test_modules,
    list_build_modules,
)
from gerbil.execution.container import (
    OUTPUT,
    Container,
    build_image,
    memory_kills,
    require_memory_accounting,
)
from gerbil.execution.coverage import jacoco_agent, parse_report, write_report
from gerbil.execution.launcher import (
    TestStatus,
    resolve_junit_runtime,
    run_tests,
)
from gerbil.execution.mutation import (
    PitSettings,
    attribute_mutants,
    list_mutants,
    parse_mutations,
    run_pit,
)
from gerbil.execution.projects import BuildTool, ExecutionProject
from gerbil.execution.sampling import sample_mutants

pytestmark = pytest.mark.integration

_FIXTURE = Path(__file__).parent / "execution_fixture"
# Docker Desktop shares the checkout but not the system temp directory.
_WORK = Path(__file__).resolve().parents[2] / "outputs" / "execution-fixture"
_PROJECT = ExecutionProject("fixture", "demo_fixture", BuildTool.MAVEN, 17)

_ADDS = ("demo.CalculatorTest", "adds")
_POSITIVES = ("demo.CalculatorTest", "recognizesPositives")
_DIVIDES = ("demo.CalculatorTest$Division", "divides")
_REJECTS_ZERO = ("demo.CalculatorTest$Division", "rejectsZero")
_LEGACY_ADDS = ("demo.LegacyCalculatorTest", "adds")


@pytest.fixture(scope="module")
def container() -> Container:
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    build_image()
    require_memory_accounting()
    shutil.rmtree(_WORK, ignore_errors=True)
    shutil.copytree(_FIXTURE, _WORK / "workspace")
    return Container(_WORK / "workspace", _WORK, _PROJECT.jdk)


def test_fixture_runs_selected_tests_with_coverage_and_mutation(
    container: Container,
) -> None:
    log = _WORK / "logs" / "fixture.log"

    assert list_build_modules(container, _PROJECT, log) == {
        ".",
        "core",
        "jupiter",
        "legacy",
        "testkit",
    }
    jupiter, legacy = compile_test_modules(
        container, _PROJECT, ["jupiter", "legacy"], log
    )
    assert jupiter.code_directories == [
        "/workspace/core/target/classes",
        "/workspace/testkit/target/classes",
    ]
    assert jupiter.production_directories == ["/workspace/core/target/classes"]

    jupiter_runtime = resolve_junit_runtime(container, jupiter, log)
    jupiter_statuses = run_tests(
        container,
        jupiter,
        jupiter_runtime,
        [
            _ADDS,
            _POSITIVES,
            _DIVIDES,
            _REJECTS_ZERO,
            ("demo.CalculatorTest", "fails"),
            ("demo.CalculatorTest", "disabled"),
            ("demo.BrokenSetupTest", "neverRuns"),
            ("demo.MissingTest", "gone"),
        ],
        jvm_args=[jacoco_agent(OUTPUT / "runs/jupiter/jacoco.exec")],
        run_directory=OUTPUT / "runs/jupiter",
        log=log,
    )
    assert jupiter_statuses == {
        _ADDS: TestStatus.PASSED,
        _POSITIVES: TestStatus.PASSED,
        _DIVIDES: TestStatus.PASSED,
        _REJECTS_ZERO: TestStatus.PASSED,
        ("demo.CalculatorTest", "fails"): TestStatus.FAILED,
        ("demo.CalculatorTest", "disabled"): TestStatus.SKIPPED,
        ("demo.BrokenSetupTest", "neverRuns"): TestStatus.FAILED,
        ("demo.MissingTest", "gone"): TestStatus.NOT_RUN,
    }
    assert "unselected" not in (_WORK / "runs/jupiter/results.tsv").read_text()

    legacy_runtime = resolve_junit_runtime(container, legacy, log)
    assert not legacy_runtime.platform_native
    legacy_statuses = run_tests(
        container,
        legacy,
        legacy_runtime,
        [_LEGACY_ADDS],
        jvm_args=[jacoco_agent(OUTPUT / "runs/legacy/jacoco.exec")],
        run_directory=OUTPUT / "runs/legacy",
        log=log,
    )
    assert legacy_statuses == {_LEGACY_ADDS: TestStatus.PASSED}

    write_report(
        container,
        [OUTPUT / "runs/jupiter/jacoco.exec", OUTPUT / "runs/legacy/jacoco.exec"],
        jupiter.code_directories,
        OUTPUT / "coverage.xml",
        log,
    )
    assert parse_report(_WORK / "coverage.xml").covered_classes == ["demo.Calculator"]

    # The jupiter module stands in for the service, as an EvoMaster target's would.
    service = jupiter.production_directories
    settings = PitSettings(threads=2, timeout_seconds=60)
    listed = list_mutants(
        container,
        jupiter,
        jupiter_runtime,
        class_names(container, service),
        mutable_directories=service,
        settings=settings,
        report_directory=OUTPUT / "pit" / "listing",
        log=log,
    )
    assert class_names(container, service) == ["demo.Calculator"]

    passing = [_ADDS, _POSITIVES, _DIVIDES, _REJECTS_ZERO]
    for classpath, runtime, tests, name in (
        (jupiter, jupiter_runtime, passing, "jupiter"),
        (legacy, legacy_runtime, [_LEGACY_ADDS], "legacy"),
    ):
        run_pit(
            container,
            classpath,
            runtime,
            tests,
            ["demo.Calculator"],
            mutable_directories=[
                directory
                for directory in classpath.code_directories
                if directory in service
            ],
            jvm_args=[],
            settings=settings,
            report_directory=OUTPUT / "pit" / name,
            log=log,
        )
    mutants = [
        mutant
        for name in ("jupiter", "legacy")
        for mutant in parse_mutations(_WORK / "pit" / name / "mutations.xml")
    ]
    # Listing without tests finds exactly the mutants a run with tests makes.
    assert {mutant.mutant_id for mutant in mutants} == set(listed)
    assert not any(
        "unselected" in test
        for mutant in mutants
        for test in (*mutant.killing_tests, *mutant.succeeding_tests)
    )
    records = attribute_mutants(mutants, {*passing, _LEGACY_ADDS})
    killers = {test for record in records for test in record.killing_tests}
    assert killers >= {
        "demo.CalculatorTest#adds",
        "demo.CalculatorTest#recognizesPositives",
        "demo.CalculatorTest$Division#divides",
        "demo.LegacyCalculatorTest#adds",
    }
    # Only the unselected test runs `tracked`: PIT counts it as coverage but runs no
    # test against those mutants, which it reports as errors and no suite is credited.
    tracked = [record for record in records if record.mutated_method == "tracked"]
    assert tracked and all(
        (record.status, record.killed, record.covered) == ("RUN_ERROR", False, False)
        for record in tracked
    )
    assert memory_kills(_WORK / "pit" / "jupiter") == 0

    # Given a sample, PIT mutates those mutants only and judges each as it did before,
    # also a mutant merged from several instructions, which the filter must see merged.
    merged = [mutant for mutant in listed if "," in mutant.rsplit("|", 1)[1]]
    assert merged
    sample = sorted({*sample_mutants(listed, 3, "seed"), merged[0]})
    run = run_pit(
        container,
        jupiter,
        jupiter_runtime,
        passing,
        ["demo.Calculator"],
        mutable_directories=service,
        jvm_args=[],
        settings=settings,
        report_directory=OUTPUT / "pit" / "sampled",
        log=log,
        kept_mutants=sample,
    )
    sampled = parse_mutations(_WORK / "pit" / "sampled" / "mutations.xml")
    assert sorted(mutant.mutant_id for mutant in sampled) == sample
    whole = {
        mutant.mutant_id: mutant
        for mutant in parse_mutations(_WORK / "pit" / "jupiter" / "mutations.xml")
    }
    for mutant in sampled:
        assert mutant.status == whole[mutant.mutant_id].status
        assert sorted(mutant.killing_tests) == sorted(
            whole[mutant.mutant_id].killing_tests
        )
    assert (run.module, run.threads) == ("jupiter", 2)
    assert run.peak_memory_bytes is None or run.peak_memory_bytes > 0
