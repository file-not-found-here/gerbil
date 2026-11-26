from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scipy.stats import mannwhitneyu, spearmanr
from typer.testing import CliRunner

from gerbil import cli
from gerbil.execution.projects import PROJECTS
from gerbil.statistics.records import (
    ProjectStatsRecord,
    TestRecord,
    project_endpoint,
    project_test,
)
from gerbil.statistics.subset_selection import (
    TESTED_MEASURES,
    ProjectValue,
    compute,
    is_in_scope,
    project_value,
    spearman_correlation,
)
from gerbil.statistics.suite_comparison import (
    MEASURES,
    DescriptiveMeasure,
    build_suite,
    holm_adjust,
)
from tests.statistics_builders import (
    api_test,
    endpoint_entry,
    non_api_test,
    project,
    write_gerbil_output,
)

_BASE_API_TEST = project_test(api_test())
_BASE_NON_API_TEST = project_test(non_api_test())

_STATUS_ONLY = "tests_checking_only_status"
_BUILDERS = "request_builders_per_step"
_SERVER_ERRORS = "status_assertions_expecting_5xx"


def _record(
    name: str,
    *,
    status_only: int = 0,
    api_tests: int = 10,
    builders: tuple[int, ...] = (),
    status_ranges: tuple[str, ...] = (),
    endpoints: int = 1,
) -> ProjectStatsRecord:
    """A project whose first `status_only` API tests check only the status, and
    whose first API test dispatches one step per entry of `builders` and asserts
    the `status_ranges` in one step."""
    tests: list[TestRecord] = [
        replace(
            _BASE_API_TEST,
            response_surface_combination=(
                "status-only" if index < status_only else "none"
            ),
            dispatch_event_request_builder_counts=builders if index == 0 else (),
            http_sequence_status_ranges=(
                (status_ranges,) if index == 0 and status_ranges else ()
            ),
        )
        for index in range(api_tests)
    ]
    return ProjectStatsRecord(
        dataset_name=name,
        tests=tuple(tests),
        test_classes=(),
        endpoints=tuple(
            project_endpoint(endpoint_entry(covering_test_count=1))
            for _ in range(endpoints)
        ),
        endpoint_parameters=(),
        resources=(),
    )


def _corpus(
    selected: tuple[int, ...], rest: tuple[int, ...]
) -> tuple[list[ProjectStatsRecord], list[str]]:
    """Projects of ten API tests with the given numbers checking only the status."""
    selected_records = [
        _record(f"selected-{index}", status_only=count)
        for index, count in enumerate(selected)
    ]
    rest_records = [
        _record(f"rest-{index}", status_only=count) for index, count in enumerate(rest)
    ]
    return (
        [*selected_records, *rest_records],
        [record.dataset_name for record in selected_records],
    )


def _measure(payload: dict[str, Any], name: str) -> dict[str, Any]:
    (entry,) = [entry for entry in payload["measures"] if entry["name"] == name]
    return entry


def _value(name: str, record: ProjectStatsRecord) -> ProjectValue:
    (measure,) = [measure for measure in TESTED_MEASURES if measure.name == name]
    return project_value(measure, build_suite(record.dataset_name, [record]))


# --- scope -------------------------------------------------------------------


def test_scope_is_ten_api_tests_and_one_endpoint() -> None:
    assert is_in_scope(_record("proj", api_tests=10))
    assert not is_in_scope(_record("proj", api_tests=9))
    assert not is_in_scope(_record("proj", api_tests=50, endpoints=0))
    # Only API tests count toward the threshold.
    padded = _record("proj", api_tests=9)
    assert not is_in_scope(
        replace(padded, tests=(*padded.tests, *(_BASE_NON_API_TEST,) * 5))
    )


# --- per-project values ------------------------------------------------------


def test_tested_measures_leave_out_only_the_descriptive_rows() -> None:
    left_out = [measure for measure in MEASURES if measure not in TESTED_MEASURES]

    assert left_out
    assert all(isinstance(measure, DescriptiveMeasure) for measure in left_out)
    assert not any(isinstance(m, DescriptiveMeasure) for m in TESTED_MEASURES)


def test_project_value_is_the_share_of_a_proportion_measure() -> None:
    value = _value(_STATUS_ONLY, _record("proj", status_only=4, api_tests=10))

    assert value.to_dict() == {"value": pytest.approx(0.4), "units": 10}


def test_project_value_counts_the_units_of_the_measure_not_the_tests() -> None:
    record = _record("proj", status_ranges=("2xx", "2xx", "5xx", "unknown"))

    value = _value(_SERVER_ERRORS, record)

    # One of the three status assertions with a resolved range, over ten tests.
    assert value.to_dict() == {"value": pytest.approx(1 / 3), "units": 3}


def test_project_value_is_the_mean_of_a_count_per_unit() -> None:
    value = _value(_BUILDERS, _record("proj", builders=(1, 2, 6)))

    assert value.to_dict() == {"value": pytest.approx(3.0), "units": 3}


def test_project_value_is_undefined_without_units() -> None:
    assert _value(_BUILDERS, _record("proj")) == ProjectValue(value=None, units=0)
    assert _value(_SERVER_ERRORS, _record("proj")) == ProjectValue(value=None, units=0)


# --- selection comparison ----------------------------------------------------


def test_selected_projects_are_compared_with_the_remaining_ones() -> None:
    records, selected = _corpus(selected=(8, 9), rest=(1, 2, 3, 9))

    payload = compute(records, selected)

    assert payload["project_count"] == 6
    assert payload["selected_project_count"] == 2
    assert payload["rest_project_count"] == 4
    assert payload["selected_projects"] == ["selected-0", "selected-1"]
    entry = _measure(payload, _STATUS_ONLY)
    assert entry["kind"] == "proportion"
    assert entry["unit"] == "tests"
    assert entry["selected"] == {
        "project_count": 2,
        "min": pytest.approx(0.8),
        "p25": pytest.approx(0.825),
        "median": pytest.approx(0.85),
        "p75": pytest.approx(0.875),
        "max": pytest.approx(0.9),
    }
    assert entry["rest"]["project_count"] == 4
    assert entry["rest"]["median"] == pytest.approx(0.25)
    assert entry["test"]["name"] == "mann_whitney_u"
    assert entry["test"]["p_value"] == pytest.approx(
        mannwhitneyu([0.8, 0.9], [0.1, 0.2, 0.3, 0.9], alternative="two-sided").pvalue
    )
    # Of the 8 selected-rest pairs, the selected project is higher in 6, tied in 1
    # and lower in 1.
    assert entry["effect_size"] == {
        "name": "cliffs_delta",
        "value": pytest.approx((6 - 1) / 8),
        "magnitude": "large",
    }


def test_effect_is_negative_when_selected_projects_are_lower() -> None:
    records, selected = _corpus(selected=(0, 1), rest=(5, 6, 7))

    entry = _measure(compute(records, selected), _STATUS_ONLY)

    assert entry["effect_size"]["value"] == pytest.approx(-1.0)


def test_count_measures_compare_the_per_project_means() -> None:
    records = [
        _record("selected", builders=(4, 6)),
        _record("rest-a", builders=(1,)),
        _record("rest-b", builders=(2, 2, 2)),
    ]

    entry = _measure(compute(records, ["selected"]), _BUILDERS)

    assert entry["kind"] == "distribution"
    assert entry["selected"]["median"] == pytest.approx(5.0)
    assert entry["rest"]["median"] == pytest.approx(1.5)
    assert entry["effect_size"]["value"] == pytest.approx(1.0)


def test_out_of_scope_projects_are_not_compared() -> None:
    records, selected = _corpus(selected=(8,), rest=(1, 2))
    records.append(_record("few-tests", status_only=9, api_tests=9))
    records.append(_record("no-endpoints", status_only=10, endpoints=0))

    payload = compute(records, selected)

    assert payload["analyzed_project_count"] == 5
    assert payload["project_count"] == 3
    assert payload["min_api_test_count"] == 10
    assert payload["min_endpoint_count"] == 1
    assert _measure(payload, _STATUS_ONLY)["rest"]["max"] == pytest.approx(0.2)
    assert {row["dataset_name"] for row in payload["projects"]} == {
        "selected-0",
        "rest-0",
        "rest-1",
    }


def test_project_without_units_is_left_out_of_that_measure_only() -> None:
    records = [
        _record("selected", status_only=5, builders=(3,)),
        _record("rest-a", status_only=1, builders=(1,)),
        _record("rest-b", status_only=2),
    ]

    payload = compute(records, ["selected"])

    assert _measure(payload, _BUILDERS)["rest"]["project_count"] == 1
    assert _measure(payload, _STATUS_ONLY)["rest"]["project_count"] == 2
    (row,) = [row for row in payload["projects"] if row["dataset_name"] == "rest-b"]
    assert row["measures"][_BUILDERS] == {"value": None, "units": 0}
    assert row["measures"][_STATUS_ONLY] == {"value": pytest.approx(0.2), "units": 10}


@pytest.mark.parametrize(
    ("selected_builders", "rest_builders"),
    [((), (1,)), ((1,), ())],
    ids=["no selected value", "no remaining value"],
)
def test_measure_is_untested_when_a_group_has_no_value(
    selected_builders: tuple[int, ...], rest_builders: tuple[int, ...]
) -> None:
    records = [
        _record("selected", status_only=3, builders=selected_builders),
        _record("rest-a", status_only=1, builders=rest_builders),
        _record("rest-b", status_only=2, builders=rest_builders),
    ]

    payload = compute(records, ["selected"])

    entry = _measure(payload, _BUILDERS)
    assert entry["test"] is None
    assert entry["effect_size"] is None
    tested = [entry for entry in payload["measures"] if entry["test"] is not None]
    assert _BUILDERS not in {entry["name"] for entry in tested}
    assert _STATUS_ONLY in {entry["name"] for entry in tested}
    assert payload["summary"]["tested_measure_count"] == len(tested)


def test_measure_with_one_shared_value_shows_no_difference() -> None:
    records, selected = _corpus(selected=(0,), rest=(0, 0))

    entry = _measure(compute(records, selected), _STATUS_ONLY)

    assert entry["test"]["p_value"] == pytest.approx(1.0)
    assert entry["effect_size"]["value"] == pytest.approx(0.0)
    assert entry["effect_size"]["magnitude"] == "negligible"


def test_tests_and_correlations_are_holm_adjusted_as_separate_families() -> None:
    # API test counts differ, so that the correlations are defined too.
    records = [
        _record("selected-0", status_only=9, builders=(9,), api_tests=40),
        _record("selected-1", status_only=8, builders=(1,), api_tests=35),
        *(
            _record(f"rest-{n}", status_only=n, builders=(n + 2,), api_tests=10 + 3 * n)
            for n in range(5)
        ),
    ]

    payload = compute(records, ["selected-0", "selected-1"], alpha=0.2)

    tests = [entry["test"] for entry in payload["measures"] if entry["test"]]
    correlations = [
        entry["api_test_count_correlation"]
        for entry in payload["measures"]
        if entry["api_test_count_correlation"]["p_value"] is not None
    ]
    assert len(tests) >= 2
    assert len(correlations) >= 2
    for family in (tests, correlations):
        assert [member["p_adjusted"] for member in family] == pytest.approx(
            holm_adjust([member["p_value"] for member in family])
        )
        for member in family:
            assert member["significant"] == (member["p_adjusted"] < 0.2)
    # One family over both would adjust the smallest p-value by more members.
    together = holm_adjust([member["p_value"] for member in tests + correlations])
    assert [member["p_adjusted"] for member in tests + correlations] != pytest.approx(
        together
    )
    summary = payload["summary"]
    assert summary["significant"] == [
        entry["name"]
        for entry in payload["measures"]
        if entry["test"] and entry["test"]["significant"]
    ]
    assert summary["significant_correlations"] == [
        entry["name"]
        for entry in payload["measures"]
        if entry["api_test_count_correlation"].get("significant")
    ]
    assert sum(summary["magnitude_counts"].values()) == len(tests)
    assert payload["method"]["alpha"] == 0.2


def test_spearman_correlation_matches_scipy() -> None:
    xs = [10, 12, 15, 40, 90]
    ys = [0.1, 0.0, 0.3, 0.2, 0.5]

    rho, p_value = spearman_correlation(xs, ys)

    expected = spearmanr(xs, ys)
    assert rho == pytest.approx(float(expected.statistic))
    assert p_value == pytest.approx(float(expected.pvalue))


def test_spearman_correlation_is_undefined_for_small_or_constant_samples() -> None:
    assert spearman_correlation([10, 20], [0.1, 0.2]) == (None, None)
    assert spearman_correlation([10, 20, 30], [0.5, 0.5, 0.5]) == (None, None)
    assert spearman_correlation([10, 10, 10], [0.1, 0.2, 0.3]) == (None, None)


def test_correlation_is_between_api_test_count_and_the_project_values() -> None:
    records = [
        _record("selected", status_only=9, api_tests=40),
        _record("rest-a", status_only=6, api_tests=30),
        _record("rest-b", status_only=1, api_tests=20),
        _record("rest-c", status_only=2, api_tests=10),
    ]

    correlation = _measure(compute(records, ["selected"]), _STATUS_ONLY)[
        "api_test_count_correlation"
    ]

    expected = spearmanr([40, 30, 20, 10], [9 / 40, 6 / 30, 1 / 20, 2 / 10])
    assert correlation["project_count"] == 4
    assert correlation["rho"] == pytest.approx(expected.statistic)
    assert correlation["p_value"] == pytest.approx(expected.pvalue)


def test_correlation_is_undefined_for_a_constant_value() -> None:
    records, selected = _corpus(selected=(0,), rest=(0, 0))

    correlation = _measure(compute(records, selected), _STATUS_ONLY)[
        "api_test_count_correlation"
    ]

    assert correlation == {"project_count": 3, "rho": None, "p_value": None}


def test_projects_are_listed_with_most_api_tests_first() -> None:
    records = [
        _record("small", api_tests=10),
        _record("large", api_tests=30),
        _record("mid", api_tests=20, endpoints=2),
    ]

    rows = compute(records, ["large"])["projects"]

    assert [row["dataset_name"] for row in rows] == ["large", "mid", "small"]
    assert [row["selected"] for row in rows] == [True, False, False]
    assert rows[1]["api_test_count"] == 20
    assert rows[1]["endpoint_count"] == 2
    assert set(rows[0]["measures"]) == {measure.name for measure in TESTED_MEASURES}


@pytest.mark.parametrize(
    ("selected", "message"),
    [
        ([], "no project is selected"),
        (["absent", "a"], "not in the corpus: absent$"),
        (["few-tests"], "out of scope .*: few-tests$"),
        (["a", "b"], "none is left to compare"),
    ],
)
def test_selection_must_split_the_in_scope_projects(
    selected: list[str], message: str
) -> None:
    records = [_record("a"), _record("b"), _record("few-tests", api_tests=9)]

    with pytest.raises(ValueError, match=message):
        compute(records, selected)


def test_projects_sharing_a_dataset_name_are_rejected() -> None:
    records = [_record("a"), _record("a"), _record("b"), _record("c")]

    with pytest.raises(ValueError, match="share a dataset name: a$"):
        compute(records, ["a"])


# --- CLI ---------------------------------------------------------------------

_SUBSET = sorted(project.dataset_name for project in PROJECTS.values())


def _write_project(root: Path, name: str, *, api_tests: int = 10) -> None:
    write_gerbil_output(
        root,
        name,
        project(
            dataset_name=name,
            tests=[api_test() for _ in range(api_tests)],
            endpoints=[endpoint_entry(covering_test_count=1)],
        ),
    )


def _write_corpus(root: Path) -> None:
    for name in (*_SUBSET, "other-a", "other-b"):
        _write_project(root, name)


def _invoke(input_root: Path, output_dir: Path) -> Any:
    return CliRunner().invoke(
        cli.app,
        [
            "subset-selection",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
        ],
    )


def test_subset_selection_cli_selects_the_comparison_subset(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    _write_corpus(corpus_root)
    output_dir = tmp_path / "out"

    result = _invoke(corpus_root, output_dir)

    assert result.exit_code == 0, result.output
    payload = json.loads((output_dir / "subset_selection.json").read_text())
    assert payload["selected_projects"] == _SUBSET
    assert payload["rest_project_count"] == 2
    assert sorted(
        row["dataset_name"] for row in payload["projects"] if not row["selected"]
    ) == ["other-a", "other-b"]
    assert [entry["name"] for entry in payload["measures"]] == [
        measure.name for measure in TESTED_MEASURES
    ]


def test_subset_selection_cli_writes_nothing_when_an_analysis_fails_to_load(
    tmp_path: Path,
) -> None:
    corpus_root = tmp_path / "corpus"
    _write_corpus(corpus_root)
    corrupt = corpus_root / "broken" / "gerbil.json"
    corrupt.parent.mkdir()
    corrupt.write_text("{not json", encoding="utf-8")
    output_dir = tmp_path / "out"

    result = _invoke(corpus_root, output_dir)

    assert result.exit_code == 1
    assert '"failed": 1' in result.output
    assert str(corrupt) in result.output
    assert not output_dir.exists()


def test_subset_selection_cli_rejects_a_corpus_missing_a_subset_project(
    tmp_path: Path,
) -> None:
    corpus_root = tmp_path / "corpus"
    for name in (*_SUBSET[1:], "other-a"):
        _write_project(corpus_root, name)
    output_dir = tmp_path / "out"

    result = _invoke(corpus_root, output_dir)

    assert result.exit_code == 2
    assert "not in the corpus" in result.output
    assert _SUBSET[0] in result.output
    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("directory", "message"),
    [
        ("missing", "input_root is not a directory"),
        ("empty", "input_root does not contain any gerbil.json"),
    ],
)
def test_subset_selection_cli_rejects_an_invalid_input_root(
    tmp_path: Path, directory: str, message: str
) -> None:
    (tmp_path / "empty").mkdir()
    output_dir = tmp_path / "out"

    result = _invoke(tmp_path / directory, output_dir)

    assert result.exit_code == 2
    assert message in result.output
    assert not output_dir.exists()
