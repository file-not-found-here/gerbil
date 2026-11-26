from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from gerbil import cli
from gerbil.execution.coverage import CoverageCounter, CoverageReport
from gerbil.execution.errors import ExecutionError
from gerbil.execution.launcher import TestStatus
from gerbil.execution.mutation import MutantRecord
from gerbil.execution.pipeline import (
    ServiceMutants,
    SuiteCoverage,
    SuiteMutation,
    SuitePaths,
    SuiteTests,
    TestRecord,
    service_mutants_path,
)
from gerbil.execution.projects import BuildTool, ExecutionProject, Suite
from gerbil.execution.sampling import sample_digest
from gerbil.execution.summary import summarize, write_summary

_PROJECT = ExecutionProject("demo", "org_demo", BuildTool.MAVEN, 17)


def _write_suite(
    output_dir: Path,
    suite: Suite,
    statuses: list[TestStatus],
    killed: dict[str, bool] | None = None,
    sample: list[str] | None = None,
) -> None:
    """`sample` is the one the suite's mutants were drawn from, when it is not the
    service's current one (see `_write_service_mutants`)."""
    paths = SuitePaths.of(output_dir, _PROJECT, suite)
    paths.root.mkdir(parents=True)
    records = [
        TestRecord(
            class_name="org.demo.ApiTest",
            method_signature=f"t{index}()",
            dispatch=["in-process"],
            module="api",
            binary_class_name="org.demo.ApiTest",
            status=status,
        )
        for index, status in enumerate(statuses)
    ]
    paths.tests.write_text(SuiteTests(commit="abc", tests=records).model_dump_json())
    paths.coverage.write_text(
        SuiteCoverage(
            heap="-Xmx16g",
            measured_tests=["org.demo.ApiTest#t0"],
            report=CoverageReport(
                counters={"LINE": CoverageCounter(covered=3, missed=1)},
                covered_classes=["org.demo.Service"],
            ),
        ).model_dump_json()
    )
    if killed is None:
        return
    mutants = [
        MutantRecord(
            mutant_id=mutant_id,
            mutated_class="org.demo.Service",
            mutated_method="m",
            line=1,
            mutator="M",
            status="KILLED" if is_killed else "SURVIVED",
            killed=is_killed,
            covered=is_killed or mutant_id != "uncovered",
            killing_tests=["org.demo.ApiTest#t0"] if is_killed else [],
            surviving_tests=(
                [] if is_killed or mutant_id == "uncovered" else ["org.demo.ApiTest#t0"]
            ),
        )
        for mutant_id, is_killed in killed.items()
    ]
    paths.mutation.write_text(
        SuiteMutation(
            target_classes=1,
            unreached_mutants=2 if suite is Suite.CODEX else 0,
            line_covered_mutants=3,
            untested_line_covered_mutants=1 if suite is Suite.CODEX else 0,
            sample_sha256=sample_digest(sample) if sample is not None else _PENDING,
            runs=[],
            mutants=mutants,
        ).model_dump_json()
    )


_PENDING = "the service's sample"


def _write_service_mutants(
    output_dir: Path, sample: list[str], unsampled: tuple[str, ...] = ()
) -> None:
    """Write the service's mutants and sample, which the suites written so far ran on."""
    service_mutants_path(output_dir, _PROJECT).write_text(
        ServiceMutants(
            mutants=sorted([*sample, *unsampled]), sample=sample, sample_seed="seed"
        ).model_dump_json()
    )
    for suite in Suite:
        path = SuitePaths.of(output_dir, _PROJECT, suite).mutation
        if path.exists() and _PENDING in path.read_text():
            path.write_text(path.read_text().replace(_PENDING, sample_digest(sample)))


def test_summary_scores_suites_over_the_sample_of_the_services_mutants(
    tmp_path: Path,
) -> None:
    _write_suite(
        tmp_path,
        Suite.DEVELOPER,
        [TestStatus.PASSED, TestStatus.FAILED, TestStatus.OUT_OF_SCOPE],
        {"m1": True, "m2": True, "m3": False, "uncovered": False},
    )
    _write_suite(
        tmp_path,
        Suite.CLAUDE_CODE,
        [TestStatus.PASSED],
        {"m1": True, "m2": False, "m3": False},
    )
    _write_suite(tmp_path, Suite.CODEX, [TestStatus.PASSED], {"m3": True})
    # m5 to m8 are of classes no suite covers, so no suite's PIT run made them; the
    # service's other mutants were not drawn, and count for no suite.
    _write_service_mutants(
        tmp_path,
        ["m1", "m2", "m3", "uncovered", "m5", "m6", "m7", "m8"],
        unsampled=("u1", "u2", "u3"),
    )

    summary = summarize(_PROJECT, tmp_path)

    developer = summary.suites["developer"]
    assert developer.api_tests == 3
    assert developer.status_counts == {"passed": 1, "failed": 1, "out-of-scope": 1}
    assert developer.measured_tests == 1
    assert developer.coverage == {"LINE": CoverageCounter(covered=3, missed=1)}
    assert (summary.service_mutants, summary.mutants) == (11, 8)
    assert summary.sample_seed == "seed"
    assert developer.mutation is not None
    assert (developer.mutation.killed, developer.mutation.covered) == (2, 3)
    assert developer.mutation.mutation_score == pytest.approx(0.25)
    assert developer.mutation.test_strength == pytest.approx(2 / 3)
    assert developer.mutation.status_counts == {
        "KILLED": 2,
        "SURVIVED": 2,
        "NO_COVERAGE": 4,
    }
    codex = summary.suites["codex"].mutation
    assert codex is not None
    assert codex.mutation_score == pytest.approx(0.125)
    assert codex.status_counts == {"KILLED": 1, "NO_COVERAGE": 7}
    assert (developer.mutation.unreached, codex.unreached) == (0, 2)
    assert (codex.line_covered, codex.untested_line_covered) == (3, 1)
    assert summary.kills_by_suites == {
        "developer+claude-code": 1,
        "developer": 1,
        "codex": 1,
        "none": 5,
    }


def test_a_suite_with_mutants_the_sample_lacks_is_not_scored(tmp_path: Path) -> None:
    _write_suite(tmp_path, Suite.DEVELOPER, [TestStatus.PASSED], {"m1": True})
    _write_suite(tmp_path, Suite.CODEX, [TestStatus.PASSED], {"m9": True})
    _write_service_mutants(tmp_path, ["m1", "m2"], unsampled=("m9",))

    summary = summarize(_PROJECT, tmp_path)

    assert summary.mutation_suites == ["developer"]
    assert summary.suites["codex"].mutation is None
    assert summary.suites["codex"].errors == {
        "mutation": "1 mutants are missing from the service's sample, such as m9"
    }
    assert summary.kills_by_suites == {"developer": 1, "none": 1}


def test_a_suite_challenged_with_another_sample_is_not_scored(tmp_path: Path) -> None:
    _write_suite(tmp_path, Suite.DEVELOPER, [TestStatus.PASSED], {"m1": True})
    # Both of its mutants are in the current sample, but so were not all it was given.
    _write_suite(
        tmp_path,
        Suite.CODEX,
        [TestStatus.PASSED],
        {"m1": True, "m2": True},
        sample=["m1", "m2"],
    )
    _write_service_mutants(tmp_path, ["m1", "m2", "m3"])

    summary = summarize(_PROJECT, tmp_path)

    assert summary.mutation_suites == ["developer"]
    assert summary.suites["codex"].mutation is None
    assert summary.suites["codex"].errors == {
        "mutation": "its mutants were drawn from another sample; run mutation again"
    }
    assert summary.kills_by_suites == {"developer": 1, "none": 2}


def test_a_mutant_pit_ran_no_test_against_counts_as_not_covered(tmp_path: Path) -> None:
    _write_suite(
        tmp_path, Suite.DEVELOPER, [TestStatus.PASSED], {"m1": True, "m2": False}
    )
    developer = SuitePaths.of(tmp_path, _PROJECT, Suite.DEVELOPER)
    mutation = SuiteMutation.model_validate_json(developer.mutation.read_text())
    mutation.mutants.append(
        mutation.mutants[1].model_copy(
            update={"mutant_id": "set-up", "status": "RUN_ERROR", "covered": False}
        )
    )
    developer.mutation.write_text(mutation.model_dump_json())
    _write_service_mutants(tmp_path, ["m1", "m2", "set-up", "m4"])

    scores = summarize(_PROJECT, tmp_path).suites["developer"].mutation

    assert scores is not None
    assert (scores.killed, scores.covered) == (1, 2)
    assert scores.mutation_score == pytest.approx(1 / 4)
    assert scores.test_strength == pytest.approx(1 / 2)
    assert scores.status_counts == {
        "KILLED": 1,
        "SURVIVED": 1,
        "RUN_ERROR": 1,
        "NO_COVERAGE": 1,
    }


def test_non_viable_mutants_leave_every_suites_denominator(tmp_path: Path) -> None:
    _write_suite(tmp_path, Suite.DEVELOPER, [TestStatus.PASSED], {"m1": True})
    _write_suite(tmp_path, Suite.CODEX, [TestStatus.PASSED], {"m1": False})
    codex = SuitePaths.of(tmp_path, _PROJECT, Suite.CODEX)
    mutation = SuiteMutation.model_validate_json(codex.mutation.read_text())
    mutation.mutants.append(
        mutation.mutants[0].model_copy(
            update={"mutant_id": "rejected", "status": "NON_VIABLE", "covered": False}
        )
    )
    codex.mutation.write_text(mutation.model_dump_json())
    _write_service_mutants(tmp_path, ["m1", "rejected", "m3", "m4"])

    summary = summarize(_PROJECT, tmp_path)

    assert (summary.mutants, summary.non_viable_mutants) == (3, 1)
    developer = summary.suites["developer"].mutation
    assert developer is not None
    assert developer.mutation_score == pytest.approx(1 / 3)
    assert developer.status_counts == {"KILLED": 1, "NO_COVERAGE": 2}


def test_summary_of_mutation_results_needs_the_service_listing(
    tmp_path: Path,
) -> None:
    _write_suite(tmp_path, Suite.DEVELOPER, [TestStatus.PASSED], {"m1": True})

    with pytest.raises(ExecutionError, match="mutants.json is missing"):
        summarize(_PROJECT, tmp_path)


def test_summary_without_mutation_results_reports_coverage_and_errors(
    tmp_path: Path,
) -> None:
    _write_suite(tmp_path, Suite.CODEX, [TestStatus.PASSED])
    SuitePaths.of(tmp_path, _PROJECT, Suite.CODEX).error("mutation").write_text(
        "pit exited with 1"
    )

    summary = summarize(_PROJECT, tmp_path)

    assert list(summary.suites) == ["codex"]
    assert summary.suites["codex"].mutation is None
    assert summary.suites["codex"].errors == {"mutation": "pit exited with 1"}
    assert summary.mutation_suites == []
    assert summary.mutants is None


def test_a_summary_that_fails_removes_the_earlier_one(tmp_path: Path) -> None:
    _write_suite(tmp_path, Suite.DEVELOPER, [TestStatus.PASSED], {"m1": True})
    stale = tmp_path / "demo" / "summary.json"
    stale.write_text("{}")

    with pytest.raises(ExecutionError, match="mutants.json is missing"):
        write_summary(_PROJECT, tmp_path)

    assert not stale.exists()


def test_write_summary_writes_project_summary(tmp_path: Path) -> None:
    _write_suite(tmp_path, Suite.DEVELOPER, [TestStatus.PASSED])

    path = write_summary(_PROJECT, tmp_path)

    assert path == tmp_path / "demo" / "summary.json"
    assert '"developer"' in path.read_text()


def test_execution_commands_reject_unknown_projects() -> None:
    result = CliRunner().invoke(
        cli.app, ["execution", "coverage", "--project", "petclinic"]
    )

    assert result.exit_code != 0
    assert "unknown project 'petclinic'" in result.stderr
