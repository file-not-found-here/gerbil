from __future__ import annotations

import pytest

from gerbil.statistics import endpoints as endpoints_stats
from gerbil.statistics.records import EndpointRecord


def make_endpoint(
    *,
    covering_test_count: int,
    path_variable_count: int = 0,
    route_depth: int = 0,
    has_body: bool = False,
    parameter_count_by_source: dict[str, int] | None = None,
    required_count_by_source: dict[str, int] | None = None,
    optional_count_by_source: dict[str, int] | None = None,
    http_method: str = "GET",
    is_method_wildcard: bool = False,
) -> EndpointRecord:
    return EndpointRecord(
        covering_test_count=covering_test_count,
        route_depth=route_depth,
        path_variable_count=path_variable_count,
        has_body=has_body,
        parameter_count_by_source=parameter_count_by_source or {},
        required_count_by_source=required_count_by_source or {},
        optional_count_by_source=optional_count_by_source or {},
        http_method=http_method,
        is_method_wildcard=is_method_wildcard,
    )


def test_coverage_share_counts_covered_gated_endpoints() -> None:
    gated = [
        make_endpoint(covering_test_count=0),
        make_endpoint(covering_test_count=0),
        make_endpoint(covering_test_count=2),
        make_endpoint(covering_test_count=5),
    ]

    coverage = endpoints_stats.compute(gated, gated)["endpoint_coverage"]["coverage"]

    assert coverage == {
        "count": 2,
        "total": 4,
        "proportion": pytest.approx(0.5),
    }


def test_tests_per_endpoint_among_covered_excludes_zeros() -> None:
    gated = [
        make_endpoint(covering_test_count=0),
        make_endpoint(covering_test_count=0),
        make_endpoint(covering_test_count=2),
        make_endpoint(covering_test_count=4),
    ]

    section = endpoints_stats.compute(gated, gated)["endpoint_coverage"]
    among_covered = section["tests_per_endpoint_among_covered"]

    # Zeros are excluded: only the covered endpoints (2, 4) contribute.
    assert among_covered["count"] == 2
    assert among_covered["min"] == 2.0
    assert among_covered["max"] == 4.0
    assert among_covered["mean"] == pytest.approx(3.0)
    # The summarized count matches the coverage count exactly.
    assert among_covered["count"] == section["coverage"]["count"]


def test_universe_uses_pooled_input_and_coverage_uses_gated_input() -> None:
    pooled = [
        make_endpoint(covering_test_count=0, has_body=True),
        make_endpoint(covering_test_count=0, has_body=True),
        make_endpoint(covering_test_count=0, has_body=False),
    ]
    gated = [
        make_endpoint(covering_test_count=1, has_body=True),
        make_endpoint(covering_test_count=2),
    ]

    result = endpoints_stats.compute(pooled, gated)

    universe = result["endpoint_universe"]
    coverage_section = result["endpoint_coverage"]

    assert universe["endpoint_count"] == 3
    # Parameter surface reflects the pooled list (2 of 3 carry a body).
    assert universe["parameter_surface"]["endpoints_with_body"] == {
        "count": 2,
        "total": 3,
        "proportion": pytest.approx(2 / 3),
    }
    # Coverage section reflects the gated list (both covered).
    assert coverage_section["endpoint_count"] == 2
    assert coverage_section["coverage"]["total"] == 2
    assert coverage_section["coverage"]["count"] == 2
    assert coverage_section["parameter_surface"]["endpoints_with_body"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }


def test_all_universe_scores_coverage_over_every_project_ungated() -> None:
    # The full surface spans 4 endpoints, 2 covered; the gate keeps only 2 of them
    # (1 covered). all_universe must score the covered count over the full surface.
    pooled = [
        make_endpoint(covering_test_count=0),
        make_endpoint(covering_test_count=0),
        make_endpoint(covering_test_count=3),
        make_endpoint(covering_test_count=5),
    ]
    gated = [
        make_endpoint(covering_test_count=0),
        make_endpoint(covering_test_count=5),
    ]

    result = endpoints_stats.compute(pooled, gated)
    all_universe = result["all_universe"]

    assert all_universe["scope"] == "all_projects"
    assert all_universe["endpoint_count"] == 4
    # Covered count (2) is taken over the full 4-endpoint denominator.
    assert all_universe["coverage"] == {
        "count": 2,
        "total": 4,
        "proportion": pytest.approx(0.5),
    }
    # The gate keeps a narrower denominator: 1 covered of 2.
    assert result["endpoint_coverage"]["coverage"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    # The full decomposition is present on the ungated view too.
    assert set(all_universe["coverage_buckets"]) == {
        "no_test",
        "one_to_three",
        "more_than_three",
    }
    assert "coverage_by_http_method" in all_universe


def test_coverage_buckets_computed_over_gated_endpoints_only() -> None:
    pooled = [make_endpoint(covering_test_count=0) for _ in range(10)]
    gated = [
        make_endpoint(
            covering_test_count=0,
            route_depth=2,
            parameter_count_by_source={"query": 1},
        ),
        make_endpoint(
            covering_test_count=1,
            route_depth=3,
            parameter_count_by_source={"query": 2},
        ),
        make_endpoint(
            covering_test_count=3,
            route_depth=3,
            parameter_count_by_source={"query": 4},
        ),
        make_endpoint(
            covering_test_count=5,
            route_depth=5,
            path_variable_count=1,
            has_body=True,
            parameter_count_by_source={"query": 6, "header": 1, "form": 1, "body": 1},
            required_count_by_source={"query": 2},
            optional_count_by_source={"query": 4},
        ),
    ]

    buckets = endpoints_stats.compute(pooled, gated)["endpoint_coverage"][
        "coverage_buckets"
    ]

    assert buckets["no_test"]["endpoint_count"] == 1
    assert buckets["one_to_three"]["endpoint_count"] == 2
    assert buckets["more_than_three"]["endpoint_count"] == 1
    assert buckets["one_to_three"]["query_variable_count"]["mean"] == pytest.approx(3.0)
    assert buckets["more_than_three"]["required_query_variable_count"]["max"] == 2.0
    assert buckets["more_than_three"]["optional_query_variable_count"]["max"] == 4.0
    assert buckets["more_than_three"]["route_depth"]["max"] == 5.0
    assert buckets["no_test"]["route_depth"]["mean"] == pytest.approx(2.0)


def test_parameter_surface_by_source_and_body() -> None:
    endpoints = [
        make_endpoint(
            covering_test_count=1,
            has_body=True,
            parameter_count_by_source={"query": 2, "body": 1},
            required_count_by_source={"query": 2},
        ),
        make_endpoint(
            covering_test_count=0,
            parameter_count_by_source={"query": 1, "header": 1},
            required_count_by_source={"query": 1},
            optional_count_by_source={"header": 1},
        ),
    ]

    surface = endpoints_stats.compute(endpoints, endpoints)["endpoint_universe"][
        "parameter_surface"
    ]

    assert surface["endpoints_with_body"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    # Required query across both endpoints: 2 and 1 -> mean 1.5.
    assert surface["required_by_source"]["query"]["mean"] == pytest.approx(1.5)
    # Optional header is observed on one endpoint; the other contributes a 0.
    assert surface["optional_by_source"]["header"]["count"] == 2
    assert surface["optional_by_source"]["header"]["max"] == 1.0
    # Sources never carrying a required/optional parameter are omitted.
    assert "form" not in surface["required_by_source"]
    assert "query" not in surface["optional_by_source"]


def test_scope_labels_tie_sections_to_composition_quadrants() -> None:
    result = endpoints_stats.compute([], [])
    assert result["endpoint_universe"]["scope"] == "projects_with_endpoints"
    assert (
        result["endpoint_coverage"]["scope"]
        == "endpoints_api_tests_and_resolved_endpoint_events"
    )


def test_empty_inputs_yield_none_proportion_and_zeroed_distributions() -> None:
    result = endpoints_stats.compute([], [])

    universe = result["endpoint_universe"]
    coverage_section = result["endpoint_coverage"]

    assert universe["endpoint_count"] == 0
    assert universe["parameter_surface"]["endpoints_with_body"]["total"] == 0
    assert universe["parameter_surface"]["required_by_source"] == {}
    assert universe["parameter_surface"]["optional_by_source"] == {}

    assert coverage_section["endpoint_count"] == 0
    assert coverage_section["parameter_surface"]["endpoints_with_body"]["total"] == 0
    assert coverage_section["parameter_surface"]["required_by_source"] == {}
    assert coverage_section["parameter_surface"]["optional_by_source"] == {}
    assert coverage_section["coverage"]["proportion"] is None
    assert coverage_section["coverage"]["count"] == 0
    assert coverage_section["tests_per_endpoint_among_covered"]["count"] == 0
    assert coverage_section["coverage_buckets"]["no_test"]["endpoint_count"] == 0


def test_removed_legacy_top_level_keys_are_absent() -> None:
    result = endpoints_stats.compute([], [])

    assert "tests_per_endpoint" not in result
    assert "parameter_surface" not in result
    assert "coverage_buckets" not in result
    assert "endpoint_count" not in result


def test_coverage_by_http_method_splits_methods() -> None:
    gated = [
        make_endpoint(covering_test_count=2, http_method="GET"),
        make_endpoint(covering_test_count=0, http_method="GET"),
        make_endpoint(covering_test_count=0, http_method="DELETE"),
        make_endpoint(covering_test_count=4, http_method="POST"),
    ]

    by_method = endpoints_stats.compute(gated, gated)["endpoint_coverage"][
        "coverage_by_http_method"
    ]

    assert by_method["GET"]["endpoint_count"] == 2
    assert by_method["GET"]["coverage"]["proportion"] == pytest.approx(0.5)
    assert by_method["DELETE"]["endpoint_count"] == 1
    assert by_method["DELETE"]["coverage"]["proportion"] == pytest.approx(0.0)
    assert by_method["POST"]["coverage"]["proportion"] == pytest.approx(1.0)
    assert by_method["POST"]["tests_per_endpoint_among_covered"]["min"] == 4.0
    # Methods with no endpoints report empty samples, not omissions.
    assert by_method["PUT"]["endpoint_count"] == 0
    assert by_method["PUT"]["coverage"]["proportion"] is None


def test_coverage_by_http_method_buckets_wildcards_separately() -> None:
    gated = [
        make_endpoint(
            covering_test_count=1, http_method="UNKNOWN", is_method_wildcard=True
        ),
        make_endpoint(covering_test_count=0, http_method="GET"),
    ]

    by_method = endpoints_stats.compute(gated, gated)["endpoint_coverage"][
        "coverage_by_http_method"
    ]

    assert by_method["wildcard"]["endpoint_count"] == 1
    assert by_method["wildcard"]["coverage"]["count"] == 1
    # Wildcard endpoints never fold into the UNKNOWN method bucket.
    assert by_method["UNKNOWN"]["endpoint_count"] == 0
