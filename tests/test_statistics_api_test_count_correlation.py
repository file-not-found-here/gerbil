from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scipy.stats import spearmanr
from typer.testing import CliRunner

from gerbil import cli
from gerbil.analysis.schema import AssertionSummary
from gerbil.statistics.api_test_count_correlation import (
    MEASURES,
    MIN_API_TEST_COUNT,
    MIN_ENDPOINT_COUNT,
    SCOPE,
    SCOPE_DESCRIPTION,
    compute,
    is_in_scope,
    spearman_correlation,
)
from gerbil.statistics.records import (
    STATUS_RANGE_KEYS,
    ProjectStatsRecord,
    TestRecord,
    project_endpoint,
    project_endpoint_parameter,
    project_test,
)
from tests.statistics_builders import (
    api_test,
    endpoint_entry,
    endpoint_parameter_entry,
    non_api_test,
    project,
    write_gerbil_output,
)

_BASE_API_TEST = project_test(api_test())
_BASE_NON_API_TEST = project_test(non_api_test())


def _test(**overrides: Any) -> TestRecord:
    return replace(_BASE_API_TEST, **overrides)


def _status_ranges(**counts: int) -> tuple[int, ...]:
    return tuple(counts.get(f"r{key}", 0) for key in STATUS_RANGE_KEYS)


def _record(
    name: str = "proj",
    *,
    tests: list[TestRecord] | None = None,
    api_tests: int = MIN_API_TEST_COUNT,
    covering_test_counts: tuple[int, ...] = (1,),
    simple_1_way: list[tuple[int, float | None]] | None = None,
) -> ProjectStatsRecord:
    """A record whose extra API tests pad `tests` up to `api_tests` API tests."""
    tests = list(tests or [])
    padding = api_tests - sum(1 for test in tests if test.is_api_test)
    tests.extend(_test() for _ in range(max(padding, 0)))
    return ProjectStatsRecord(
        dataset_name=name,
        tests=tuple(tests),
        test_classes=(),
        endpoints=tuple(
            project_endpoint(endpoint_entry(covering_test_count=count))
            for count in covering_test_counts
        ),
        endpoint_parameters=tuple(
            project_endpoint_parameter(
                endpoint_parameter_entry(
                    route_covering_test_count=covering,
                    simple_1_way_optional_coverage=coverage,
                )
            )
            for covering, coverage in simple_1_way or []
        ),
        resources=(),
    )


def _measure(name: str, record: ProjectStatsRecord) -> float | None:
    (measure,) = [measure for measure in MEASURES if measure.name == name]
    return measure.value(record)


# --- scope ------------------------------------------------------------------


def test_scope_thresholds_are_ten_api_tests_and_one_endpoint() -> None:
    assert MIN_API_TEST_COUNT == 10
    assert MIN_ENDPOINT_COUNT == 1
    assert SCOPE_DESCRIPTION == (
        "Projects with at least 10 API tests and at least 1 identified "
        "application endpoint."
    )


def test_scope_boundaries() -> None:
    assert is_in_scope(_record(api_tests=10))
    assert not is_in_scope(_record(api_tests=9))
    assert not is_in_scope(_record(api_tests=50, covering_test_counts=()))
    # Only API tests count toward the threshold.
    assert not is_in_scope(
        _record(tests=[_BASE_NON_API_TEST for _ in range(5)], api_tests=9)
    )


def test_payload_reports_scope_and_excludes_out_of_scope_projects() -> None:
    records = [
        _record("in-a", api_tests=10),
        _record("in-b", api_tests=30),
        _record("few-tests", api_tests=9),
        _record("no-endpoints", api_tests=40, covering_test_counts=()),
    ]

    payload = compute(records)

    assert payload["scope"] == SCOPE
    assert payload["scope_description"] == SCOPE_DESCRIPTION
    assert payload["min_api_test_count"] == 10
    assert payload["min_endpoint_count"] == 1
    assert payload["analyzed_project_count"] == 4
    assert payload["project_count"] == 2
    # Most API tests first.
    assert [row["dataset_name"] for row in payload["projects"]] == ["in-b", "in-a"]
    assert payload["projects"][0]["api_test_count"] == 30
    assert payload["projects"][0]["endpoint_count"] == 1
    assert payload["method"]["implementation"] == "scipy.stats.spearmanr"
    assert payload["method"]["alternative"] == "two-sided"


# --- per-project measures ---------------------------------------------------


def test_rates_are_over_api_tests_only() -> None:
    record = _record(
        tests=[
            _test(assertion_header_count=2),
            _test(assertion_body_count=1),
            # A non-API test never enters any rate's numerator or denominator.
            replace(_BASE_NON_API_TEST, assertion_header_count=1),
        ]
    )

    assert _measure("header_check_rate", record) == pytest.approx(1 / 10)
    assert _measure("body_verification_rate", record) == pytest.approx(1 / 10)


def test_multi_request_rate_requires_two_request_events() -> None:
    record = _record(
        tests=[
            _test(event_counts=(2, 0, 0)),
            # Events across origin buckets add up.
            _test(event_counts=(1, 1, 0)),
            _test(event_counts=(1, 0, 0)),
        ]
    )

    assert _measure("multi_request_rate", record) == pytest.approx(2 / 10)


def test_error_status_assertion_rate_counts_4xx_and_5xx_only() -> None:
    record = _record(
        tests=[
            _test(status_range_counts=_status_ranges(r4xx=1)),
            _test(status_range_counts=_status_ranges(r2xx=1, r5xx=1)),
            _test(status_range_counts=_status_ranges(r2xx=3, r3xx=1)),
        ]
    )

    assert _measure("error_status_assertion_rate", record) == pytest.approx(2 / 10)


def test_state_verification_rate_counts_read_after_write_only() -> None:
    # A state postcondition read from a database is not an API read-back.
    record = _record(
        tests=[
            _test(postcondition_types=("db",)),
            _test(has_read_after_write=True),
        ]
    )

    assert _measure("state_verification_rate", record) == pytest.approx(1 / 10)


def test_measures_are_the_reported_correlation_set() -> None:
    assert [measure.name for measure in MEASURES] == [
        "header_check_rate",
        "body_verification_rate",
        "multi_request_rate",
        "error_status_assertion_rate",
        "state_verification_rate",
        "http_verb_repetition_rate",
        "endpoint_coverage",
        "simple_1_way_optional_coverage",
    ]


def test_http_verb_repetition_rate_is_over_verb_mapped_consecutive_pairs() -> None:
    record = _record(
        tests=[
            # GET->GET repeats, GET->POST does not: 1 of 2.
            _test(http_sequence_verb_operations=(("GET",), ("GET",), ("POST",))),
            # PUT->PATCH is a verb change; the unmapped sequence forms no pair.
            _test(http_sequence_verb_operations=(("PUT",), ("PATCH",), ())),
        ]
    )

    assert _measure("http_verb_repetition_rate", record) == pytest.approx(1 / 3)


def test_http_verb_repetition_rate_is_undefined_without_pairs() -> None:
    record = _record(tests=[_test(http_sequence_verb_operations=(("GET",),))])

    assert _measure("http_verb_repetition_rate", record) is None


def test_endpoint_coverage_is_share_of_covered_endpoints() -> None:
    record = _record(covering_test_counts=(3, 0, 1, 0))

    assert _measure("endpoint_coverage", record) == pytest.approx(2 / 4)


def test_simple_1_way_coverage_averages_covered_endpoints_with_a_value() -> None:
    record = _record(
        simple_1_way=[
            (2, 0.5),
            (1, 0.0),
            # Uncovered endpoints and endpoints without optional parameters are
            # outside the among-covered population.
            (0, 1.0),
            (4, None),
        ]
    )

    assert _measure("simple_1_way_optional_coverage", record) == pytest.approx(0.25)


def test_simple_1_way_coverage_is_undefined_without_covered_values() -> None:
    record = _record(simple_1_way=[(0, 1.0), (3, None)])

    assert _measure("simple_1_way_optional_coverage", record) is None


# --- correlations -----------------------------------------------------------


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


def test_correlations_use_api_test_count_and_skip_undefined_values() -> None:
    records = [
        _record(
            f"p{api_tests}",
            api_tests=api_tests,
            tests=[_test(assertion_header_count=1) for _ in range(headers)],
        )
        for api_tests, headers in ((10, 1), (20, 4), (40, 12), (80, 40))
    ]
    # No project has a consecutive sequence pair, so verb repetition is N/A.
    payload = compute(records)

    header = payload["correlations"]["header_check_rate"]
    assert header["project_count"] == 4
    assert header["rho"] == pytest.approx(1.0)
    assert header["p_value"] == pytest.approx(0.0, abs=1e-9)

    verb_repetition = payload["correlations"]["http_verb_repetition_rate"]
    assert verb_repetition["project_count"] == 0
    assert verb_repetition["rho"] is None
    assert verb_repetition["p_value"] is None

    assert set(payload["correlations"]) == {measure.name for measure in MEASURES}
    for measure in MEASURES:
        assert payload["correlations"][measure.name]["description"] == (
            measure.description
        )


# --- CLI --------------------------------------------------------------------


def _write_corpus(input_root: Path) -> None:
    for name, api_tests, header_tests in (
        ("alpha", 10, 1),
        ("beta", 14, 3),
        ("gamma", 20, 8),
    ):
        tests = [
            api_test(
                assertion_summary=AssertionSummary(
                    header_count=1 if index < header_tests else 0
                ),
            )
            for index in range(api_tests)
        ]
        write_gerbil_output(
            input_root,
            name,
            project(
                dataset_name=name,
                tests=tests,
                endpoints=[endpoint_entry(covering_test_count=1)],
            ),
        )
    # Out of scope: too few API tests.
    write_gerbil_output(
        input_root,
        "tiny",
        project(
            dataset_name="tiny",
            tests=[api_test() for _ in range(3)],
            endpoints=[endpoint_entry(covering_test_count=1)],
        ),
    )


def test_statistics_command_writes_scoped_correlation(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    _write_corpus(input_root)

    result = CliRunner().invoke(
        cli.app,
        [
            "statistics",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
            "--api-test-count-correlation",
        ],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads((output_dir / "api_test_count_correlation.json").read_text())
    assert payload["scope_description"] == SCOPE_DESCRIPTION
    assert payload["analyzed_project_count"] == 4
    assert payload["project_count"] == 3
    assert payload["correlations"]["header_check_rate"] == {
        "description": "Share of API tests asserting on at least one response header.",
        "project_count": 3,
        "rho": pytest.approx(1.0),
        "p_value": pytest.approx(0.0, abs=1e-9),
    }
    assert [row["dataset_name"] for row in payload["projects"]] == [
        "gamma",
        "beta",
        "alpha",
    ]
    assert payload["projects"][2]["measures"]["header_check_rate"] == (
        pytest.approx(1 / 10)
    )
    assert payload["projects"][2]["measures"]["state_verification_rate"] == 0.0
