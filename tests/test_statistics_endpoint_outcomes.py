from __future__ import annotations

import pytest

from gerbil.statistics import endpoint_outcomes as endpoint_outcomes_stats
from gerbil.statistics.records import EndpointRecord


def make_endpoint(
    *,
    covering_test_count: int = 0,
    attributed_request_count: int = 0,
    status_asserted_request_count: int = 0,
    asserting_test_count: int = 0,
    asserted_status_range_counts: dict[str, int] | None = None,
    asserted_status_code_counts: dict[str, int] | None = None,
) -> EndpointRecord:
    return EndpointRecord(
        covering_test_count=covering_test_count,
        route_depth=1,
        path_variable_count=0,
        has_body=False,
        parameter_count_by_source={},
        required_count_by_source={},
        optional_count_by_source={},
        attributed_request_count=attributed_request_count,
        status_asserted_request_count=status_asserted_request_count,
        asserting_test_count=asserting_test_count,
        asserted_status_range_counts=asserted_status_range_counts or {},
        asserted_status_code_counts=asserted_status_code_counts or {},
    )


def _asserted_endpoint(
    ranges: dict[str, int],
    codes: dict[str, int] | None = None,
    *,
    requests: int = 1,
    tests: int = 1,
) -> EndpointRecord:
    return make_endpoint(
        covering_test_count=tests,
        attributed_request_count=requests,
        status_asserted_request_count=requests,
        asserting_test_count=tests,
        asserted_status_range_counts=ranges,
        asserted_status_code_counts=codes or {},
    )


# Attribution and assertion shares


def test_attribution_and_assertion_shares_over_gated_endpoints() -> None:
    endpoints = [
        make_endpoint(),
        make_endpoint(attributed_request_count=2),
        _asserted_endpoint({"2xx": 1}),
    ]

    payload = endpoint_outcomes_stats.compute(endpoints)

    assert payload["scope"] == "endpoints_api_tests_and_resolved_endpoint_events"
    assert payload["endpoint_count"] == 3
    assert payload["request_attributed"] == {
        "count": 2,
        "total": 3,
        "proportion": pytest.approx(2 / 3),
    }
    assert payload["status_asserted"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }


def test_empty_endpoint_sample_reports_none_proportions() -> None:
    payload = endpoint_outcomes_stats.compute([])

    assert payload["endpoint_count"] == 0
    assert payload["request_attributed"]["proportion"] is None
    assert payload["outcome_mix"]["endpoint_count"] == 0
    assert (
        payload["asserted_status_ranges"]["endpoints_with_range_among_all_endpoints"][
            "5xx"
        ]["proportion"]
        is None
    )


# Outcome mix partitions status-asserted endpoints


def test_outcome_mix_buckets_partition_asserted_endpoints() -> None:
    endpoints = [
        _asserted_endpoint({"2xx": 2}),
        _asserted_endpoint({"4xx": 1}),
        _asserted_endpoint({"5xx": 1}),
        _asserted_endpoint({"2xx": 1, "4xx": 1}),
        _asserted_endpoint({"3xx": 1}),
        # Never status-asserted: stays out of the mix sample entirely.
        make_endpoint(attributed_request_count=1),
    ]

    mix = endpoint_outcomes_stats.compute(endpoints)["outcome_mix"]

    assert mix["endpoint_count"] == 5
    by_mix = mix["by_mix"]
    assert by_mix["success_only"]["count"] == 1
    assert by_mix["error_without_success"]["count"] == 2
    assert by_mix["success_and_error"]["count"] == 1
    assert by_mix["neither_success_nor_error"]["count"] == 1
    assert by_mix["success_and_error"]["proportion"] == pytest.approx(0.2)


def test_auth_denial_share_counts_401_and_403_codes() -> None:
    endpoints = [
        _asserted_endpoint({"4xx": 1}, {"401": 1}),
        _asserted_endpoint({"4xx": 1}, {"403": 2}),
        _asserted_endpoint({"4xx": 1}, {"404": 1}),
        _asserted_endpoint({"2xx": 1}, {"200": 1}),
    ]

    mix = endpoint_outcomes_stats.compute(endpoints)["outcome_mix"]

    assert mix["has_auth_denial_assertion"] == {
        "count": 2,
        "total": 4,
        "proportion": pytest.approx(0.5),
    }


# Asserted ranges and codes pool across endpoints


def test_asserted_ranges_pool_assertions_and_endpoint_counts() -> None:
    endpoints = [
        _asserted_endpoint({"2xx": 3, "4xx": 1}),
        _asserted_endpoint({"2xx": 1}),
        make_endpoint(attributed_request_count=1),
    ]

    ranges = endpoint_outcomes_stats.compute(endpoints)["asserted_status_ranges"]

    assert ranges["attributed_assertion_counts"]["2xx"]["count"] == 4
    assert ranges["attributed_assertion_counts"]["4xx"]["count"] == 1
    assert ranges["attributed_assertion_counts"]["2xx"]["proportion"] == pytest.approx(
        0.8
    )
    # Absent ranges report a genuine 0 over the canonical keys.
    assert ranges["attributed_assertion_counts"]["5xx"]["count"] == 0
    assert ranges["endpoints_with_range"]["2xx"]["count"] == 2
    assert ranges["endpoints_with_range"]["4xx"]["count"] == 1
    assert ranges["endpoints_with_range"]["4xx"]["total"] == 2
    assert ranges["endpoints_with_range_among_all_endpoints"]["2xx"] == {
        "count": 2,
        "total": 3,
        "proportion": pytest.approx(2 / 3),
    }
    assert ranges["endpoints_with_range_among_all_endpoints"]["5xx"] == {
        "count": 0,
        "total": 3,
        "proportion": pytest.approx(0.0),
    }


def test_asserted_codes_sort_numerically_and_count_endpoints() -> None:
    endpoints = [
        _asserted_endpoint({"2xx": 1, "4xx": 1}, {"200": 1, "404": 1}),
        _asserted_endpoint({"2xx": 1}, {"200": 2}),
    ]

    codes = endpoint_outcomes_stats.compute(endpoints)["asserted_status_codes"]

    assert list(codes["attributed_assertion_counts"]) == ["200", "404"]
    assert codes["attributed_assertion_counts"]["200"]["count"] == 3
    assert codes["endpoints_with_code"]["200"]["count"] == 2
    assert codes["endpoints_with_code"]["404"]["count"] == 1


# Per-endpoint distributions stay within their subpopulations


def test_per_endpoint_distributions_use_attributed_and_asserted_samples() -> None:
    endpoints = [
        make_endpoint(),
        make_endpoint(attributed_request_count=4),
        _asserted_endpoint({"2xx": 1}, requests=2, tests=2),
    ]

    per_endpoint = endpoint_outcomes_stats.compute(endpoints)["per_endpoint"]

    attributed = per_endpoint["attributed_request_count_among_attributed"]
    assert attributed["count"] == 2
    assert attributed["min"] == 2.0
    assert attributed["max"] == 4.0
    asserted = per_endpoint["status_asserted_request_count_among_asserted"]
    assert asserted["count"] == 1
    assert asserted["min"] == 2.0
    asserting_tests = per_endpoint["asserting_test_count_among_asserted"]
    assert asserting_tests["count"] == 1
    assert asserting_tests["max"] == 2.0
