from __future__ import annotations

import pytest

from gerbil.statistics import parameter_exercise as parameter_exercise_stats
from gerbil.statistics.records import EndpointParameterRecord


def make_param(
    *,
    route_covering_test_count: int = 0,
    exercise_rate: float | None = None,
    optional_exercise_rate: float | None = None,
    required_exercise_rate: float | None = None,
    simple_1_way_optional_coverage: float | None = None,
    simple_2_way_optional_coverage: float | None = None,
    total_2_way_optional_coverage: float | None = None,
    exercise_rate_by_source: dict[str, float | None] | None = None,
    optional_exercise_rate_by_source: dict[str, float | None] | None = None,
    simple_1_way_optional_coverage_by_source: dict[str, float | None] | None = None,
    simple_2_way_optional_coverage_by_source: dict[str, float | None] | None = None,
    total_2_way_optional_coverage_by_source: dict[str, float | None] | None = None,
    optional_count_by_source: dict[str, int] | None = None,
    optional_exercised_count_by_source: dict[str, int] | None = None,
) -> EndpointParameterRecord:
    return EndpointParameterRecord(
        route_covering_test_count=route_covering_test_count,
        exercise_rate=exercise_rate,
        optional_exercise_rate=optional_exercise_rate,
        required_exercise_rate=required_exercise_rate,
        simple_1_way_optional_coverage=simple_1_way_optional_coverage,
        simple_2_way_optional_coverage=simple_2_way_optional_coverage,
        total_2_way_optional_coverage=total_2_way_optional_coverage,
        exercise_rate_by_source=exercise_rate_by_source or {},
        optional_exercise_rate_by_source=optional_exercise_rate_by_source or {},
        simple_1_way_optional_coverage_by_source=(
            simple_1_way_optional_coverage_by_source or {}
        ),
        simple_2_way_optional_coverage_by_source=(
            simple_2_way_optional_coverage_by_source or {}
        ),
        total_2_way_optional_coverage_by_source=(
            total_2_way_optional_coverage_by_source or {}
        ),
        optional_count_by_source=optional_count_by_source or {},
        optional_exercised_count_by_source=optional_exercised_count_by_source or {},
    )


# --- Pooled (parameter-level) optional exercise ------------------------------


def test_pooled_optional_exercise_pools_parameters_not_endpoints() -> None:
    # Endpoint A: 1 of 4 optional query params exercised; endpoint B: 1 of 1.
    # Per-endpoint (macro) mean would be (0.25 + 1.0)/2 = 0.625; pooled (micro)
    # weighs each parameter equally: (1 + 1) / (4 + 1) = 0.4.
    records = [
        make_param(
            route_covering_test_count=1,
            optional_count_by_source={"query": 4},
            optional_exercised_count_by_source={"query": 1},
        ),
        make_param(
            route_covering_test_count=1,
            optional_count_by_source={"query": 1},
            optional_exercised_count_by_source={"query": 1},
        ),
        # Uncovered endpoint is excluded from the covered subpopulation.
        make_param(
            route_covering_test_count=0,
            optional_count_by_source={"query": 10},
            optional_exercised_count_by_source={"query": 10},
        ),
    ]

    pooled = parameter_exercise_stats.compute(records)["among_covered"][
        "pooled_optional_exercise_by_source"
    ]

    assert pooled["query"] == {
        "exercised_parameter_count": 2,
        "total_parameter_count": 5,
        "exercise_rate": 0.4,
    }


def test_pooled_optional_exercise_omits_sources_with_no_parameters() -> None:
    pooled = parameter_exercise_stats.compute(
        [make_param(route_covering_test_count=1)]
    )["among_covered"]["pooled_optional_exercise_by_source"]

    assert pooled == {}


# --- Optional exercise extremes (fully / none) -------------------------------


def test_optional_exercise_extremes_counts_fully_and_none_by_source() -> None:
    records = [
        make_param(
            route_covering_test_count=1,
            optional_exercise_rate_by_source={"query": 1.0},
        ),
        make_param(
            route_covering_test_count=1,
            optional_exercise_rate_by_source={"query": 0.5},
        ),
        make_param(
            route_covering_test_count=1,
            optional_exercise_rate_by_source={"query": 0.0},
        ),
        # No optional query param -> None -> excluded from the denominator.
        make_param(
            route_covering_test_count=1,
            optional_exercise_rate_by_source={"query": None},
        ),
    ]

    extremes = parameter_exercise_stats.compute(records)["among_covered"][
        "optional_exercise_extremes"
    ]["by_source"]["query"]

    assert extremes["fully_exercised"] == {"count": 1, "total": 3, "proportion": 1 / 3}
    assert extremes["none_exercised"] == {"count": 1, "total": 3, "proportion": 1 / 3}


# --- Scope and coverage proportion ------------------------------------------


def test_scope_label_is_endpoints_api_tests_and_resolved_endpoint_events() -> None:
    result = parameter_exercise_stats.compute([make_param(route_covering_test_count=1)])
    assert result["scope"] == "endpoints_api_tests_and_resolved_endpoint_events"


def test_coverage_reports_share_of_endpoints_with_a_covering_test() -> None:
    records = [
        make_param(route_covering_test_count=2),
        make_param(route_covering_test_count=0),
        make_param(route_covering_test_count=5),
        make_param(route_covering_test_count=0),
    ]

    result = parameter_exercise_stats.compute(records)

    assert result["endpoint_count"] == 4
    # Two of four endpoints have a covering test.
    assert result["coverage"] == {"count": 2, "total": 4, "proportion": 0.5}


def test_legacy_condition_keys_are_absent() -> None:
    result = parameter_exercise_stats.compute([make_param(route_covering_test_count=1)])
    assert "unconditional" not in result
    assert "with_covering_test" not in result


def test_empty_input_has_none_proportion_and_no_covered_endpoints() -> None:
    result = parameter_exercise_stats.compute([])

    assert result["endpoint_count"] == 0
    assert result["coverage"] == {"count": 0, "total": 0, "proportion": None}
    assert result["among_covered"]["endpoint_count"] == 0


# --- all_universe: ungated denominator, covered-only among_covered ----------


def test_all_universe_widens_denominator_but_keeps_among_covered_identical() -> None:
    gated = [
        make_param(route_covering_test_count=2, optional_exercise_rate=0.5),
        make_param(route_covering_test_count=0, optional_exercise_rate=1.0),
    ]
    # The full set adds two more endpoints from untested projects, both uncovered.
    extra = [
        make_param(route_covering_test_count=0, optional_exercise_rate=0.9),
        make_param(route_covering_test_count=0, optional_exercise_rate=0.1),
    ]

    result = parameter_exercise_stats.compute(gated, gated + extra)
    all_universe = result["all_universe"]

    assert all_universe["scope"] == "all_projects"
    # Gated coverage: 1 of 2; all_universe coverage: 1 of 4 (same covered count).
    assert result["coverage"] == {"count": 1, "total": 2, "proportion": 0.5}
    assert all_universe["coverage"] == {"count": 1, "total": 4, "proportion": 0.25}
    # among_covered is covered-only, so widening the denominator does not move it.
    assert (
        all_universe["among_covered"]["holistic"] == result["among_covered"]["holistic"]
    )
    assert all_universe["among_covered"]["endpoint_count"] == 1


def test_all_universe_defaults_to_the_gated_set_when_omitted() -> None:
    result = parameter_exercise_stats.compute([make_param(route_covering_test_count=1)])
    # Without a separate ungated set, all_universe mirrors the gated payload.
    assert result["all_universe"]["endpoint_count"] == result["endpoint_count"]
    assert result["all_universe"]["coverage"] == result["coverage"]


# --- saint_comparison: context-path-stripped covered subpopulation ----------


def test_saint_comparison_admits_endpoints_the_baseline_attribution_drops() -> None:
    gated = [
        make_param(route_covering_test_count=1, simple_1_way_optional_coverage=0.0)
    ]
    # Baseline attribution covers only the one gated endpoint; stripping the
    # context path recovers a second endpoint that carries a covered t-way value.
    adjusted = [
        make_param(route_covering_test_count=1, simple_1_way_optional_coverage=0.0),
        make_param(
            route_covering_test_count=2,
            simple_1_way_optional_coverage=0.0,
            total_2_way_optional_coverage=0.25,
        ),
    ]

    result = parameter_exercise_stats.compute(gated, gated, adjusted)
    saint = result["saint_comparison"]

    assert saint["scope"] == "all_projects_context_path_stripped"
    # Two covered endpoints under adjustment vs. one in the baseline all_universe.
    assert saint["among_covered"]["endpoint_count"] == 2
    assert result["all_universe"]["among_covered"]["endpoint_count"] == 1
    # The recovered endpoint makes 2-way coverage measurable (baseline: no pair).
    two_way = saint["among_covered"]["holistic"]["total_2_way_optional_coverage"]
    assert two_way["count"] == 1
    assert two_way["mean"] == pytest.approx(0.25)


def test_saint_comparison_absent_when_not_supplied() -> None:
    result = parameter_exercise_stats.compute([make_param(route_covering_test_count=1)])
    assert "saint_comparison" not in result


# --- among_covered: only covered endpoints, None rates excluded -------------


def test_among_covered_keeps_only_endpoints_with_a_covering_test() -> None:
    records = [
        make_param(route_covering_test_count=2, optional_exercise_rate=0.5),
        make_param(route_covering_test_count=0, optional_exercise_rate=1.0),
        make_param(route_covering_test_count=5, optional_exercise_rate=None),
    ]

    result = parameter_exercise_stats.compute(records)

    among_covered = result["among_covered"]
    # Two route-covered endpoints survive; its count matches coverage.count.
    assert among_covered["endpoint_count"] == 2
    assert among_covered["endpoint_count"] == result["coverage"]["count"]
    # One of the covered endpoints carries a None rate, dropped from the sample.
    conditioned = among_covered["holistic"]["optional_exercise_rate"]
    assert conditioned["count"] == 1
    assert conditioned["mean"] == pytest.approx(0.5)


def test_among_covered_holistic_rates_skip_none_denominators() -> None:
    records = [
        make_param(
            route_covering_test_count=2,
            exercise_rate=0.4,
            optional_exercise_rate=0.5,
            required_exercise_rate=1.0,
            simple_1_way_optional_coverage=None,
            simple_2_way_optional_coverage=0.0,
            total_2_way_optional_coverage=0.25,
        ),
        # Untargeted endpoint is excluded entirely by the coverage gate.
        make_param(
            route_covering_test_count=0,
            exercise_rate=0.9,
            optional_exercise_rate=0.9,
            required_exercise_rate=0.9,
            simple_1_way_optional_coverage=0.9,
            simple_2_way_optional_coverage=0.9,
            total_2_way_optional_coverage=0.9,
        ),
        make_param(
            route_covering_test_count=5,
            exercise_rate=0.8,
            optional_exercise_rate=None,
            required_exercise_rate=0.0,
            simple_1_way_optional_coverage=1.0,
            simple_2_way_optional_coverage=1.0,
            total_2_way_optional_coverage=None,
        ),
    ]

    holistic = parameter_exercise_stats.compute(records)["among_covered"]["holistic"]

    # exercise_rate over covered endpoints: 0.4, 0.8 -> mean 0.6.
    assert holistic["exercise_rate"]["count"] == 2
    assert holistic["exercise_rate"]["mean"] == pytest.approx(0.6)
    # optional_exercise_rate: 0.5 (second covered is N/A).
    assert holistic["optional_exercise_rate"]["count"] == 1
    assert holistic["optional_exercise_rate"]["mean"] == pytest.approx(0.5)
    # required_exercise_rate: 1.0, 0.0 -> mean 0.5.
    assert holistic["required_exercise_rate"]["count"] == 2
    assert holistic["required_exercise_rate"]["mean"] == pytest.approx(0.5)
    # simple_1_way_optional_coverage: 1.0 (first covered is N/A).
    assert holistic["simple_1_way_optional_coverage"]["count"] == 1
    assert holistic["simple_1_way_optional_coverage"]["mean"] == pytest.approx(1.0)
    # simple_2_way_optional_coverage: 0.0, 1.0 -> mean 0.5.
    assert holistic["simple_2_way_optional_coverage"]["count"] == 2
    assert holistic["simple_2_way_optional_coverage"]["mean"] == pytest.approx(0.5)
    # total_2_way_optional_coverage: 0.25 (second covered is N/A).
    assert holistic["total_2_way_optional_coverage"]["count"] == 1
    assert holistic["total_2_way_optional_coverage"]["mean"] == pytest.approx(0.25)


# --- among_covered by-source: organized by source, None/absent dropped ------


def test_among_covered_by_source_rates_are_organized_in_canonical_order() -> None:
    records = [
        make_param(
            route_covering_test_count=1,
            exercise_rate_by_source={"query": 1.0, "header": 0.5},
            simple_1_way_optional_coverage_by_source={"query": 0.0, "header": None},
        ),
        make_param(
            route_covering_test_count=2,
            exercise_rate_by_source={"query": 0.0},
            simple_1_way_optional_coverage_by_source={"query": 1.0, "form": 0.5},
        ),
        # Untargeted endpoint never enters the covered subpopulation.
        make_param(
            route_covering_test_count=0,
            exercise_rate_by_source={"query": 0.9},
        ),
    ]

    by_source = parameter_exercise_stats.compute(records)["among_covered"]["by_source"]

    # exercise_rate query spans both covered endpoints; header only the first.
    assert list(by_source["exercise_rate"]) == ["query", "header"]
    assert by_source["exercise_rate"]["query"]["count"] == 2
    assert by_source["exercise_rate"]["query"]["mean"] == pytest.approx(0.5)
    assert by_source["exercise_rate"]["header"]["count"] == 1
    assert by_source["exercise_rate"]["header"]["mean"] == pytest.approx(0.5)

    # simple 1-way: header is present only as None, so it is omitted entirely;
    # query and form survive, ordered canonically (query before form).
    simple_1_way = by_source["simple_1_way_optional_coverage"]
    assert list(simple_1_way) == ["query", "form"]
    assert simple_1_way["query"]["count"] == 2
    assert simple_1_way["query"]["mean"] == pytest.approx(0.5)
    assert simple_1_way["form"]["count"] == 1
    assert simple_1_way["form"]["mean"] == pytest.approx(0.5)


def test_among_covered_by_source_metric_keys_present_when_no_source_has_data() -> None:
    result = parameter_exercise_stats.compute([make_param(route_covering_test_count=1)])

    by_source = result["among_covered"]["by_source"]
    # Every by-source metric is keyed, each mapping to an empty per-source dict.
    assert set(by_source) == {
        "exercise_rate",
        "optional_exercise_rate",
        "simple_1_way_optional_coverage",
        "simple_2_way_optional_coverage",
        "total_2_way_optional_coverage",
    }
    assert all(per_source == {} for per_source in by_source.values())
