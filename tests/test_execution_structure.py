from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gerbil import cli
from gerbil.execution import structure
from gerbil.execution.errors import ExecutionError
from gerbil.execution.launcher import TestStatus
from gerbil.execution.mutation import MutantRecord
from gerbil.execution.pipeline import SuiteMutation, SuitePaths, SuiteTests, TestRecord
from gerbil.execution.projects import BuildTool, ExecutionProject, Suite
from gerbil.execution.structure import (
    Encounter,
    SuiteKills,
    compute,
    find_analysis,
    load_suite_kills,
)
from gerbil.statistics.records import TestRecord as Structure
from gerbil.statistics.records import project_test

from tests.statistics_builders import (
    api_test,
    class_analysis,
    non_api_test,
    project,
    write_gerbil_output,
)

_PROJECT = ExecutionProject("demo", "org_demo", BuildTool.MAVEN, 17)
_OTHER_PROJECT = ExecutionProject("other", "org_other", BuildTool.MAVEN, 17)
_COMMIT = "abc"


def _structure(**fields: object) -> Structure:
    record = project_test(api_test())
    for name, value in fields.items():
        setattr(record, name, value)
    return record


def _trait(scope: dict[str, Any], name: str) -> dict[str, Any]:
    return next(entry for entry in scope["traits"] if entry["name"] == name)


def _kills(
    tests: dict[str, Structure], encounters: list[tuple[str, str, bool]]
) -> SuiteKills:
    """Encounters are (mutant, test, killed). A mutant `Class.n` is of class `Class`,
    and a test `Class#method` of test class `Class`; other tests share one class."""
    return SuiteKills(
        project="demo",
        tests=tests,
        encounters=tuple(
            Encounter(
                mutant,
                mutant.split(".")[0],
                test,
                test.split("#")[0] if "#" in test else "T",
                killed,
            )
            for mutant, test, killed in encounters
        ),
        unmatched_tests=0,
        stopped_mutants=0,
    )


_LONG_AND_SHORT = {
    "long": _structure(http_sequence_count=2),
    "short": _structure(http_sequence_count=1),
}


def _multi_step(encounters: list[tuple[str, str, bool]]) -> dict[str, Any]:
    """The shared mutants of a two-step test `long` and a one-step test `short`."""
    payload = compute("developer", [_kills(_LONG_AND_SHORT, encounters)])
    shared: dict[str, Any] = _trait(payload["pooled"], "multi_step")["shared_mutants"]
    return shared


def _both_run(mutant: str, long_kills: bool, short_kills: bool) -> list[Any]:
    return [(mutant, "long", long_kills), (mutant, "short", short_kills)]


def test_a_trait_splits_the_tests_and_compares_them_on_the_mutants_both_run() -> None:
    tests = {
        "long": _structure(http_sequence_count=3),
        "longer": _structure(http_sequence_count=2),
        "short": _structure(http_sequence_count=1),
        "stepless": _structure(http_sequence_count=0),
    }
    payload = compute(
        "developer",
        [
            _kills(
                tests,
                [
                    ("A.shared", "long", True),
                    ("A.shared", "longer", False),
                    ("A.shared", "short", False),
                    ("A.long-only", "long", True),
                    ("A.short-only", "short", True),
                    ("A.shared", "stepless", True),
                ],
            )
        ],
    )

    assert payload["pooled"]["tests"] == 4
    assert payload["pooled"]["encounters"] == 6
    multi_step = _trait(payload["pooled"], "multi_step")
    assert multi_step["holding"] == {
        "tests": 2,
        "tests_running_a_mutant": 2,
        "encounters": 3,
        "kills": 2,
        "kill_rate": pytest.approx(2 / 3),
        "mutants_run": 2,
        "mutants_killed": 2,
        "test_strength": 1.0,
    }
    assert multi_step["lacking"]["tests"] == 1
    assert multi_step["lacking"]["kill_rate"] == 0.5
    # One mutated class is too few to resample or to flip.
    assert multi_step["shared_mutants"] == {
        "mutants": 1,
        "mutated_classes": 1,
        "mean_kill_rate_holding": 0.5,
        "mean_kill_rate_lacking": 0.0,
        "mean_difference": 0.5,
        "difference_interval": None,
        "holding_higher": 1,
        "lacking_higher": 0,
        "tied": 0,
        "test": None,
        "killed_only_by_holding": 1,
        "killed_only_by_lacking": 0,
        "killed_by_both": 0,
        "killed_by_neither": 0,
    }


def test_groups_that_never_differ_are_not_tested() -> None:
    shared = _multi_step(_both_run("A.1", True, True) + _both_run("B.1", False, False))

    assert shared["tied"] == 2
    assert shared["difference_interval"] == [0.0, 0.0]
    assert shared["test"] is None
    assert shared["killed_by_both"] == 1
    assert shared["killed_by_neither"] == 1


def test_the_interval_resamples_whole_mutated_classes() -> None:
    encounters = _both_run("B.1", False, True)
    for index in range(10):
        encounters += _both_run(f"A.{index}", True, False)

    shared = _multi_step(encounters)

    assert shared["mean_difference"] == pytest.approx(9 / 11)
    # A quarter of the resamples draw only B and a quarter only A; resampling the
    # eleven mutants instead would keep the interval well above zero.
    assert shared["difference_interval"] == [-1.0, 1.0]


@pytest.mark.parametrize(
    ("classes", "expected"),
    [
        # Both signs of every class are as likely, so 2 of the 2^n flips match.
        (3, 2 / 2**3),
        (5, 2 / 2**5),
    ],
)
def test_the_test_flips_the_sign_of_whole_classes(
    classes: int, expected: float
) -> None:
    encounters = [
        encounter
        for mutated_class in range(classes)
        for index in range(20)
        for encounter in _both_run(f"C{mutated_class}.{index}", True, False)
    ]

    shared = _multi_step(encounters)

    assert shared["mutants"] == 20 * classes
    assert shared["test"]["name"] == "class_sign_flip"
    # Sixty mutants that all favor one group are still only `classes` units.
    assert shared["test"]["p_value"] == pytest.approx(expected, abs=0.02)
    assert not shared["test"]["significant"]


def test_classes_that_cancel_out_give_no_evidence() -> None:
    shared = _multi_step(_both_run("A.1", True, False) + _both_run("B.1", False, True))

    assert shared["mean_difference"] == 0.0
    assert shared["test"]["p_value"] == 1.0


def test_p_values_are_holm_adjusted_over_the_tested_traits() -> None:
    tests = {
        "long": _structure(
            http_sequence_count=2, response_surface_combination="status+body"
        ),
        "short": _structure(
            http_sequence_count=1, response_surface_combination="status-only"
        ),
    }
    encounters = [
        encounter
        for mutated_class in range(12)
        for encounter in _both_run(f"C{mutated_class}.1", True, False)
    ]

    payload = compute("developer", [_kills(tests, encounters)])

    tested = {
        (entry["name"], comparison): entry[comparison]["test"]
        for entry in payload["pooled"]["traits"]
        for comparison in ("shared_mutants", "same_test_class")
        if entry[comparison]["test"] is not None
    }
    # The two traits split the tests alike, and the tests share a test class, so
    # the four comparisons have one p-value, which Holm multiplies by four.
    assert sorted(tested) == [
        ("checks_body", "same_test_class"),
        ("checks_body", "shared_mutants"),
        ("multi_step", "same_test_class"),
        ("multi_step", "shared_mutants"),
    ]
    for test in tested.values():
        assert test["p_value"] < 0.001
        assert test["p_adjusted"] == pytest.approx(4 * test["p_value"])
        assert test["significant"]


def test_groups_are_also_compared_within_a_test_class() -> None:
    tests = {
        "X#long": _structure(http_sequence_count=2),
        "X#short": _structure(http_sequence_count=1),
        "Y#long": _structure(http_sequence_count=2),
        "Z#short": _structure(http_sequence_count=1),
    }
    payload = compute(
        "developer",
        [
            _kills(
                tests,
                [
                    ("A.both-in-x", "X#long", True),
                    ("A.both-in-x", "X#short", False),
                    ("A.both-in-x", "Y#long", False),
                    ("B.apart", "Y#long", True),
                    ("B.apart", "Z#short", False),
                ],
            )
        ],
    )

    multi_step = _trait(payload["pooled"], "multi_step")
    assert multi_step["shared_mutants"]["mutants"] == 2
    assert multi_step["shared_mutants"]["mean_kill_rate_holding"] == 0.75
    # Only X has tests of both groups on a mutant, and Y's test is not among them.
    within = multi_step["same_test_class"]
    assert within["mutants"] == 1
    assert within["test_classes"] == 1
    assert within["mean_kill_rate_holding"] == 1.0
    assert within["mean_kill_rate_lacking"] == 0.0
    assert within["mean_difference"] == 1.0


def test_pooling_keeps_projects_apart_and_reports_each() -> None:
    first = SuiteKills(
        project="one",
        tests={"one:t": _structure(http_sequence_count=1)},
        encounters=(Encounter("one:A.1", "one:A", "one:t", "one:T", True),),
        unmatched_tests=3,
        stopped_mutants=1,
    )
    second = SuiteKills(
        project="two",
        tests={"two:t": _structure(http_sequence_count=2)},
        encounters=(),
        unmatched_tests=0,
        stopped_mutants=0,
    )

    payload = compute("developer", [first, second], left_out=["three"])

    assert payload["projects"] == ["one", "two"]
    assert payload["projects_left_out"] == ["three"]
    assert payload["pooled"]["tests"] == 2
    assert payload["by_project"]["one"]["unmatched_tests"] == 3
    assert payload["by_project"]["one"]["stopped_mutants"] == 1
    assert payload["by_project"]["two"]["encounters"] == 0
    # Each trait's pooled difference is also given without each project.
    fixture_request = _trait(payload["pooled"], "fixture_request")
    for comparison in ("shared_mutants", "same_test_class"):
        assert fixture_request[comparison]["mean_difference_without"] == {
            "one": None,
            "two": None,
        }


@pytest.mark.parametrize(
    ("trait", "fields", "expected"),
    [
        ("multi_step", {"http_sequence_count": 2}, True),
        ("multi_step", {"http_sequence_count": 1}, False),
        ("multi_step", {"http_sequence_count": 0}, None),
        ("five_or_more_steps", {"http_sequence_count": 5}, True),
        ("five_or_more_steps", {"http_sequence_count": 4}, False),
        ("five_or_more_steps", {"http_sequence_count": 0}, None),
        ("checks_body", {"response_surface_combination": "status+body"}, True),
        ("checks_body", {"response_surface_combination": "body-only"}, True),
        ("checks_body", {"response_surface_combination": "status-only"}, False),
        ("checks_body", {"response_surface_combination": "status+header"}, None),
        ("checks_body", {"response_surface_combination": "none"}, None),
        ("deep_body_check", {"http_sequence_body_check_counts": (1, 3)}, True),
        ("deep_body_check", {"http_sequence_body_check_counts": (1, 0)}, False),
        ("deep_body_check", {"http_sequence_body_check_counts": (0,)}, None),
        ("deep_body_check", {"http_sequence_body_check_counts": ()}, None),
        (
            "re_dispatches_endpoint",
            {
                "has_multiple_http_sequences": True,
                "all_dispatch_events_resolved": True,
                "re_dispatches_endpoint": True,
            },
            True,
        ),
        (
            "re_dispatches_endpoint",
            {
                "has_multiple_http_sequences": True,
                "all_dispatch_events_resolved": True,
            },
            False,
        ),
        (
            "re_dispatches_endpoint",
            {
                "has_multiple_http_sequences": True,
                "all_dispatch_events_resolved": False,
            },
            None,
        ),
        (
            "re_dispatches_endpoint",
            {"all_dispatch_events_resolved": True},
            None,
        ),
        (
            "reads_after_write",
            {
                "resource_lifecycle_labels": ("create-read",),
                "writes_resource": True,
                "has_read_after_write": True,
            },
            True,
        ),
        (
            "reads_after_write",
            {"resource_lifecycle_labels": ("create",), "writes_resource": True},
            False,
        ),
        (
            "reads_after_write",
            {"resource_lifecycle_labels": ("read",), "writes_resource": False},
            None,
        ),
        ("fixture_request", {"event_counts": (1, 0, 2)}, True),
        ("fixture_request", {"event_counts": (1, 0, 0)}, False),
    ],
)
def test_traits_split_the_tests_of_their_population(
    trait: str, fields: dict[str, object], expected: bool | None
) -> None:
    holds = next(entry.holds for entry in structure.TRAITS if entry.name == trait)

    assert holds(_structure(**fields)) is expected


def _write_run(
    output_dir: Path,
    records: list[TestRecord],
    mutants: list[MutantRecord],
    *,
    project: ExecutionProject = _PROJECT,
    suite: Suite = Suite.DEVELOPER,
) -> None:
    paths = SuitePaths.of(output_dir, project, suite)
    paths.root.mkdir(parents=True)
    paths.tests.write_text(SuiteTests(commit=_COMMIT, tests=records).model_dump_json())
    paths.mutation.write_text(
        SuiteMutation(
            target_classes=1,
            unreached_mutants=0,
            line_covered_mutants=0,
            untested_line_covered_mutants=0,
            sample_sha256="sample",
            runs=[],
            mutants=mutants,
        ).model_dump_json()
    )


def _executed(
    method: str,
    status: TestStatus = TestStatus.PASSED,
    *,
    class_name: str = "org.demo.ApiTest",
    binary_class_name: str = "org.demo.ApiTest",
    module: str = "api",
) -> TestRecord:
    return TestRecord(
        class_name=class_name,
        method_signature=method,
        dispatch=["in-process"],
        module=module,
        binary_class_name=binary_class_name,
        status=status,
    )


def _mutant(
    name: str, killing: list[str], surviving: list[str], *, status: str | None = None
) -> MutantRecord:
    """A mutant PIT stopped the worker of is given its `status` and counts as killed."""
    return MutantRecord(
        mutant_id=name,
        mutated_class="org.demo.Service",
        mutated_method="run",
        line=1,
        mutator="M",
        status=status or ("KILLED" if killing else "SURVIVED"),
        killed=bool(killing) or status is not None,
        covered=True,
        killing_tests=killing,
        surviving_tests=surviving,
    )


def _write_analysis(
    root: Path,
    methods: list[str],
    *,
    name: str = "demo",
    class_name: str = "org.demo.ApiTest",
    non_api_methods: tuple[str, ...] = (),
    commit: str = _COMMIT,
) -> Path:
    tests = []
    for method in methods:
        test = non_api_test() if method in non_api_methods else api_test()
        test.identity.method_signature = method
        tests.append(test)
    analysis = project(
        test_classes=[class_analysis(qualified_class_name=class_name, tests=tests)]
    )
    analysis.metadata.git_commit_hash = commit
    return write_gerbil_output(root, name, analysis)


def test_loading_joins_the_kill_matrix_to_the_analysis_by_test(tmp_path: Path) -> None:
    analysis_path = _write_analysis(
        tmp_path,
        ["kills()", "survives()", "over(int)", "over(long)", "helper()", "failed()"],
        non_api_methods=("helper()",),
    )
    _write_run(
        tmp_path / "execution",
        [
            _executed("kills()"),
            # Built in a second module, which is still one test.
            _executed("kills()", module="other"),
            _executed("survives()"),
            _executed("over(int)"),
            _executed("over(long)"),
            _executed("absent()"),
            _executed("helper()"),
            _executed("failed()", TestStatus.FAILED),
        ],
        [
            _mutant(
                "m1",
                ["org.demo.ApiTest#kills"],
                [
                    "org.demo.ApiTest#survives",
                    "org.demo.ApiTest#over",
                    "org.demo.ApiTest#helper",
                    "org.demo.ApiTest#failed",
                ],
            ),
            _mutant("m2", ["org.demo.ApiTest#absent"], []),
        ],
    )

    kills = load_suite_kills(
        _PROJECT, Suite.DEVELOPER, tmp_path / "execution", analysis_path
    )

    assert sorted(kills.tests) == [
        "demo:org.demo.ApiTest#kills",
        "demo:org.demo.ApiTest#survives",
    ]
    assert kills.encounters == (
        Encounter(
            "demo:m1",
            "demo:org.demo.Service",
            "demo:org.demo.ApiTest#kills",
            "demo:org.demo.ApiTest",
            True,
        ),
        Encounter(
            "demo:m1",
            "demo:org.demo.Service",
            "demo:org.demo.ApiTest#survives",
            "demo:org.demo.ApiTest",
            False,
        ),
    )
    # The overloads share a name, the analysis has no `absent()` and no API test
    # `helper()`, and `failed()` did not pass.
    assert kills.unmatched_tests == 4
    assert kills.stopped_mutants == 0


def test_a_nested_test_class_joins_by_its_binary_name(tmp_path: Path) -> None:
    analysis_path = _write_analysis(
        tmp_path, ["kills()"], class_name="org.demo.ApiTest.Inner"
    )
    _write_run(
        tmp_path / "execution",
        [
            _executed(
                "kills()",
                class_name="org.demo.ApiTest.Inner",
                binary_class_name="org.demo.ApiTest$Inner",
            )
        ],
        [_mutant("m1", ["org.demo.ApiTest$Inner#kills"], [])],
    )

    kills = load_suite_kills(
        _PROJECT, Suite.DEVELOPER, tmp_path / "execution", analysis_path
    )

    assert [
        (encounter.test, encounter.test_class) for encounter in kills.encounters
    ] == [("demo:org.demo.ApiTest$Inner#kills", "demo:org.demo.ApiTest$Inner")]
    assert kills.unmatched_tests == 0


def test_a_mutant_whose_worker_pit_stopped_is_left_out(tmp_path: Path) -> None:
    analysis_path = _write_analysis(tmp_path, ["kills()", "survives()"])
    _write_run(
        tmp_path / "execution",
        [_executed("kills()"), _executed("survives()")],
        [
            _mutant("timed-out", [], ["org.demo.ApiTest#survives"], status="TIMED_OUT"),
            _mutant("out-of-memory", [], [], status="MEMORY_ERROR"),
            _mutant("m1", ["org.demo.ApiTest#kills"], ["org.demo.ApiTest#survives"]),
        ],
    )

    kills = load_suite_kills(
        _PROJECT, Suite.DEVELOPER, tmp_path / "execution", analysis_path
    )

    assert {encounter.mutant for encounter in kills.encounters} == {"demo:m1"}
    assert kills.stopped_mutants == 2


def test_two_projects_with_the_same_names_pool_without_merging(tmp_path: Path) -> None:
    kills = []
    for execution_project in (_PROJECT, _OTHER_PROJECT):
        analysis_path = _write_analysis(
            tmp_path / "analyses", ["kills()"], name=execution_project.name
        )
        _write_run(
            tmp_path / "execution",
            [_executed("kills()")],
            [_mutant("m1", ["org.demo.ApiTest#kills"], [])],
            project=execution_project,
        )
        kills.append(
            load_suite_kills(
                execution_project,
                Suite.DEVELOPER,
                tmp_path / "execution",
                analysis_path,
            )
        )

    payload = compute("developer", kills)

    assert payload["pooled"]["tests"] == 2
    assert payload["pooled"]["encounters"] == 2
    assert _trait(payload["pooled"], "fixture_request")["lacking"]["mutants_run"] == 2


def test_loading_refuses_a_suite_without_mutation_results(tmp_path: Path) -> None:
    analysis_path = _write_analysis(tmp_path, ["kills()"])

    with pytest.raises(ExecutionError, match="no mutation results"):
        load_suite_kills(_PROJECT, Suite.DEVELOPER, tmp_path, analysis_path)


def test_loading_refuses_an_analysis_of_another_commit(tmp_path: Path) -> None:
    analysis_path = _write_analysis(tmp_path, ["kills()"], commit="def")
    _write_run(tmp_path / "execution", [_executed("kills()")], [])

    with pytest.raises(ExecutionError, match="analyses commit def"):
        load_suite_kills(
            _PROJECT, Suite.DEVELOPER, tmp_path / "execution", analysis_path
        )


def test_loading_refuses_an_analysis_naming_a_test_twice(tmp_path: Path) -> None:
    analysis_path = _write_analysis(tmp_path, ["kills()", "kills()"])
    _write_run(tmp_path / "execution", [_executed("kills()")], [])

    with pytest.raises(ExecutionError, match="more than once"):
        load_suite_kills(
            _PROJECT, Suite.DEVELOPER, tmp_path / "execution", analysis_path
        )


def test_an_analysis_is_found_under_the_dataset_or_the_project_name(
    tmp_path: Path,
) -> None:
    developer = write_gerbil_output(tmp_path / "developer", "org_demo", project())
    generated = write_gerbil_output(tmp_path / "generated", "demo", project())

    assert find_analysis(tmp_path / "developer", _PROJECT) == developer
    assert find_analysis(tmp_path / "generated", _PROJECT) == generated
    with pytest.raises(ExecutionError, match="no analysis of demo"):
        find_analysis(tmp_path, _PROJECT)


def _run_structure(tmp_path: Path, *arguments: str) -> Any:
    return CliRunner().invoke(
        cli.app,
        [
            "execution",
            "structure",
            "--suite",
            "claude-code",
            "--analysis-root",
            str(tmp_path / "analyses"),
            "--statistics-dir",
            str(tmp_path / "statistics"),
            "--output-dir",
            str(tmp_path / "execution"),
            *arguments,
        ],
    )


@pytest.fixture
def measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two measured projects, of which only `demo` has mutation results."""
    monkeypatch.setattr(
        cli, "MEASURED_PROJECTS", {"demo": _PROJECT, "other": _OTHER_PROJECT}
    )
    _write_analysis(tmp_path / "analyses", ["kills()", "survives()"])
    _write_run(
        tmp_path / "execution",
        [_executed("kills()"), _executed("survives()")],
        [_mutant("m1", ["org.demo.ApiTest#kills"], ["org.demo.ApiTest#survives"])],
        suite=Suite.CLAUDE_CODE,
    )
    return tmp_path


def test_structure_command_takes_the_projects_with_mutation_results(
    measured: Path,
) -> None:
    result = _run_structure(measured)

    assert result.exit_code == 0, result.output
    payload = json.loads(
        (measured / "statistics" / "test_structure_claude_code.json").read_text()
    )
    assert payload["suite"] == "claude-code"
    assert payload["projects"] == ["demo"]
    assert payload["projects_left_out"] == ["other"]
    assert payload["by_project"]["demo"]["encounters"] == 2
    assert json.loads(result.output)["projects_left_out"] == ["other"]


def test_structure_command_fails_on_a_named_project_without_results(
    measured: Path,
) -> None:
    result = _run_structure(measured, "--project", "demo", "--project", "other")

    assert result.exit_code == 1
    assert "has no analysis of other" in result.output
    assert not (measured / "statistics").exists()


def test_structure_command_refuses_a_repeated_or_unknown_project(
    measured: Path,
) -> None:
    repeated = _run_structure(measured, "--project", "demo", "--project", "demo")
    unknown = _run_structure(measured, "--project", "absent")

    assert repeated.exit_code == 2
    assert "more than once" in repeated.output
    assert unknown.exit_code == 2
    assert "unknown project" in unknown.output


def test_structure_command_fails_when_no_project_has_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "MEASURED_PROJECTS", {"demo": _PROJECT})

    result = _run_structure(tmp_path)

    assert result.exit_code == 1
    assert "no project has mutation results" in result.output
    assert not (tmp_path / "statistics").exists()
