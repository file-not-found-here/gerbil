from __future__ import annotations

import pytest

from gerbil.statistics import request_construction as request_construction_stats
from gerbil.statistics.records import (
    BUILDER_CONTRIBUTED_PROPERTIES,
    CRUD_OPERATIONS,
    HTTP_METHODS,
    BuilderGroup,
    TestRecord,
)


def make_test(
    *,
    is_api_test: bool = True,
    request_event_count: int = 0,
    with_body: int = 0,
    with_headers: int = 0,
    with_query_params: int = 0,
    with_path_params: int = 0,
    with_form_params: int = 0,
    with_builder_correlation: int = 0,
    event_query_param_counts: tuple[int, ...] = (),
    event_header_name_counts: tuple[int, ...] = (),
    runtime_header_name_counts: tuple[tuple[str, int], ...] = (),
    builder_groups: tuple[BuilderGroup, ...] = (),
) -> TestRecord:
    return TestRecord(
        is_api_test=is_api_test,
        is_controller_unit_test=False,
        expanded_ncloc=0,
        expanded_cyclomatic_complexity=0,
        expanded_helper_method_count=0,
        test_helper_method_count=0,
        expanded_objects_created=0,
        expanded_assertion_count=0,
        mocked_interaction_count=0,
        dependency_strategy_label_count=0,
        dispatch_labels=(),
        has_read_after_write=False,
        has_cleanup_delete=False,
        resource_lifecycle_labels=(),
        setup_fixture_count=0,
        teardown_fixture_count=0,
        status_range_counts=(0, 0, 0, 0, 0, 0),
        builder_counts=(0, 0, 0),
        event_counts=(request_event_count, 0, 0),
        verification_counts=(0, 0, 0),
        fixture_builder_phase_counts=(0, 0),
        fixture_event_phase_counts=(0, 0),
        fixture_verification_phase_counts=(0, 0),
        http_method_counts=(0,) * len(HTTP_METHODS),
        crud_operation_counts=(0,) * len(CRUD_OPERATIONS),
        request_events_with_body=with_body,
        request_events_with_headers=with_headers,
        request_events_with_query_params=with_query_params,
        request_events_with_path_params=with_path_params,
        request_events_with_form_params=with_form_params,
        request_events_with_builder_correlation=with_builder_correlation,
        event_query_param_counts=event_query_param_counts,
        event_header_name_counts=event_header_name_counts,
        runtime_header_name_counts=runtime_header_name_counts,
        builder_groups=builder_groups,
    )


def test_event_surfaces_pool_events_and_gate_tests_to_event_having() -> None:
    tests = [
        make_test(request_event_count=2, with_body=1, with_headers=2),
        make_test(request_event_count=2, with_query_params=1),
        # No dispatched events: excluded from every tests_with_any denominator.
        make_test(),
    ]

    payload = request_construction_stats.compute(tests)

    assert payload["request_event_count"] == 4
    surfaces = payload["event_surfaces"]
    assert surfaces["body_payload"]["events"] == {
        "count": 1,
        "total": 4,
        "proportion": pytest.approx(0.25),
    }
    assert surfaces["headers"]["events"]["count"] == 2
    assert surfaces["body_payload"]["tests_with_any"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    assert surfaces["query_params"]["tests_with_any"]["count"] == 1


def test_per_event_distributions_pool_across_tests() -> None:
    tests = [
        make_test(
            request_event_count=2,
            event_query_param_counts=(2, 0),
            event_header_name_counts=(1, 1),
        ),
        make_test(request_event_count=1, event_query_param_counts=(3,)),
    ]

    per_event = request_construction_stats.compute(tests)["per_event"]

    query = per_event["query_param_names_per_event"]
    assert query["count"] == 3
    assert query["max"] == 3.0
    headers = per_event["header_names_per_event"]
    assert headers["count"] == 2
    assert headers["mean"] == pytest.approx(1.0)


def test_builder_usage_counts_correlated_events_and_properties() -> None:
    tests = [
        make_test(
            request_event_count=2,
            with_builder_correlation=1,
            builder_groups=(
                BuilderGroup(builders=(("path",), ("has_body_payload",), ("path",))),
            ),
        ),
        make_test(request_event_count=1),
    ]

    builder_usage = request_construction_stats.compute(tests)["builder_usage"]

    assert builder_usage["events_with_builder_correlation"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }
    assert builder_usage["tests_with_builder_correlation"]["count"] == 1
    assert builder_usage["builder_group_count"] == 1
    assert builder_usage["builder_count_total"] == 3
    properties = builder_usage["contributed_properties"]
    assert properties["total"] == 3
    assert list(properties["by_property"]) == list(BUILDER_CONTRIBUTED_PROPERTIES)
    assert properties["by_property"]["path"]["count"] == 2
    assert properties["by_property"]["path"]["proportion"] == pytest.approx(2 / 3)
    assert properties["by_property"]["headers"]["count"] == 0


def test_unknown_contributed_property_appended_after_canonical_keys() -> None:
    tests = [
        make_test(
            request_event_count=1,
            with_builder_correlation=1,
            builder_groups=(BuilderGroup(builders=(("path", "novel_property"),)),),
        ),
    ]

    properties = request_construction_stats.compute(tests)["builder_usage"][
        "contributed_properties"
    ]

    assert list(properties["by_property"]) == [
        *BUILDER_CONTRIBUTED_PROPERTIES,
        "novel_property",
    ]
    assert properties["by_property"]["novel_property"]["count"] == 1


def test_builder_types_given_body_conditions_on_body_containing_groups() -> None:
    tests = [
        make_test(
            request_event_count=2,
            builder_groups=(
                # Body-containing chain: a body builder alongside header/query.
                BuilderGroup(
                    builders=(
                        ("has_body_payload",),
                        ("header_names",),
                        ("query_param_names",),
                    )
                ),
                # No body builder: excluded from the conditional cohort entirely.
                BuilderGroup(builders=(("header_names",),)),
            ),
        ),
        make_test(
            request_event_count=1,
            builder_groups=(BuilderGroup(builders=(("has_body_payload",), ("path",))),),
        ),
    ]

    given_body = request_construction_stats.compute(tests)["builder_types_given_body"]

    assert given_body["builder_group_count"] == 3
    assert given_body["body_builder_group_count"] == 2
    assert given_body["share_of_builder_groups_with_body_payload"] == {
        "count": 2,
        "total": 3,
        "proportion": pytest.approx(2 / 3),
    }
    # Five request builders across the two body-containing chains.
    assert given_body["builder_count"] == 5
    by_type = given_body["builder_types"]["by_type"]
    assert list(by_type) == list(BUILDER_CONTRIBUTED_PROPERTIES)
    assert by_type["has_body_payload"]["count"] == 2
    assert by_type["has_body_payload"]["proportion"] == pytest.approx(2 / 5)
    assert by_type["header_names"]["count"] == 1
    assert by_type["query_param_names"]["count"] == 1
    assert by_type["path"]["count"] == 1
    assert by_type["auth_hints"]["count"] == 0


def test_builder_types_given_body_counts_each_builder_once_per_distinct_type() -> None:
    tests = [
        make_test(
            request_event_count=1,
            builder_groups=(
                BuilderGroup(
                    builders=(
                        # A single builder repeating a property counts once, and
                        # an unknown property sorts after the canonical keys.
                        ("has_body_payload", "has_body_payload"),
                        ("novel_property",),
                    )
                ),
            ),
        ),
    ]

    given_body = request_construction_stats.compute(tests)["builder_types_given_body"]

    assert given_body["body_builder_group_count"] == 1
    assert given_body["builder_count"] == 2
    by_type = given_body["builder_types"]["by_type"]
    assert list(by_type) == [*BUILDER_CONTRIBUTED_PROPERTIES, "novel_property"]
    # Duplicate body property within one builder is still one body builder.
    assert by_type["has_body_payload"]["count"] == 1
    assert by_type["has_body_payload"]["proportion"] == pytest.approx(1 / 2)
    assert by_type["novel_property"]["count"] == 1


def test_header_vocabulary_pools_distinct_names_and_test_frequency() -> None:
    tests = [
        make_test(
            request_event_count=1,
            # content-type used three times: occurrence-weighted but still one test.
            runtime_header_name_counts=(
                ("accept", 1),
                ("authorization", 1),
                ("content-type", 3),
            ),
        ),
        make_test(
            request_event_count=1,
            runtime_header_name_counts=(("authorization", 1), ("x-forwarded-for", 2)),
        ),
        # Names sourced only from a builder/fixture chain with no dispatched
        # event: still counted in the distinct vocabulary, occurrences, and
        # per-test distribution, but the event-gated tests_with_any share excludes it.
        make_test(request_event_count=0, runtime_header_name_counts=(("x-trace", 1),)),
        # No dispatched events and no names: excluded from tests_with_any too.
        make_test(),
    ]

    vocabulary = request_construction_stats.compute(tests)["header_vocabulary"]

    assert vocabulary["distinct_header_name_count"] == 5
    # authorization appears in two tests; the rest in one, ordered by frequency
    # then name so the map is deterministic.
    assert list(vocabulary["header_name_test_frequency"]) == [
        "authorization",
        "accept",
        "content-type",
        "x-forwarded-for",
        "x-trace",
    ]
    assert vocabulary["header_name_test_frequency"]["authorization"] == 2
    assert vocabulary["header_name_test_frequency"]["x-trace"] == 1
    # Occurrences weight every use: 9 total, of which accept(1)+content-type(3) are
    # content negotiation, so the rest is scenario-specific header use.
    occurrences = vocabulary["header_name_occurrences"]
    assert occurrences["total"] == 9
    assert occurrences["content_negotiation"] == {
        "count": 4,
        "total": 9,
        "proportion": pytest.approx(4 / 9),
    }
    # The builder-only test carries a name but no event, so it falls out of both
    # the numerator and denominator of the event-gated share.
    assert vocabulary["tests_with_any_header"] == {
        "count": 2,
        "total": 2,
        "proportion": pytest.approx(1.0),
    }
    # The per-test distribution is ungated, so all four tests (3, 2, 1, 0) count.
    per_test = vocabulary["distinct_header_names_per_test"]
    assert per_test["count"] == 4
    assert per_test["max"] == 3.0
    assert per_test["mean"] == pytest.approx(1.5)


def test_empty_cohort_reports_zero_events_and_none_proportions() -> None:
    payload = request_construction_stats.compute([])

    assert payload["request_event_count"] == 0
    assert payload["event_surfaces"]["body_payload"]["events"]["proportion"] is None
    assert payload["builder_usage"]["contributed_properties"]["total"] == 0
    vocabulary = payload["header_vocabulary"]
    assert vocabulary["distinct_header_name_count"] == 0
    assert vocabulary["header_name_test_frequency"] == {}
    assert vocabulary["tests_with_any_header"]["proportion"] is None
    assert vocabulary["header_name_occurrences"]["total"] == 0
    assert (
        vocabulary["header_name_occurrences"]["content_negotiation"]["proportion"]
        is None
    )
    given_body = payload["builder_types_given_body"]
    assert given_body["body_builder_group_count"] == 0
    assert given_body["builder_count"] == 0
    assert given_body["share_of_builder_groups_with_body_payload"]["proportion"] is None
    assert (
        given_body["builder_types"]["by_type"]["has_body_payload"]["proportion"] is None
    )
