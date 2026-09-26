from __future__ import annotations

import pytest

from gerbil.statistics import test_scope as test_scope_stats
from gerbil.statistics.records import CRUD_OPERATIONS, HTTP_METHODS, TestRecord


def make_test(
    *,
    is_api_test: bool = True,
    is_controller_unit_test: bool = False,
    distinct_resource_count: int = 0,
    test_phase_distinct_resource_count: int = 0,
    distinct_endpoint_count: int = 0,
    method_resolved_distinct_resource_count: int | None = None,
    method_resolved_test_phase_distinct_resource_count: int | None = None,
) -> TestRecord:
    # The scope splits read the method-resolved variants; default them to the raw
    # counts (a fully resolved test) unless a test exercises the divergence.
    if method_resolved_distinct_resource_count is None:
        method_resolved_distinct_resource_count = distinct_resource_count
    if method_resolved_test_phase_distinct_resource_count is None:
        method_resolved_test_phase_distinct_resource_count = (
            test_phase_distinct_resource_count
        )
    return TestRecord(
        is_api_test=is_api_test,
        is_controller_unit_test=is_controller_unit_test,
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
        event_counts=(0, 0, 0),
        verification_counts=(0, 0, 0),
        fixture_builder_phase_counts=(0, 0),
        fixture_event_phase_counts=(0, 0),
        fixture_verification_phase_counts=(0, 0),
        http_method_counts=(0,) * len(HTTP_METHODS),
        crud_operation_counts=(0,) * len(CRUD_OPERATIONS),
        distinct_endpoint_count=distinct_endpoint_count,
        distinct_resource_count=distinct_resource_count,
        test_phase_distinct_resource_count=test_phase_distinct_resource_count,
        method_resolved_distinct_resource_count=(
            method_resolved_distinct_resource_count
        ),
        method_resolved_test_phase_distinct_resource_count=(
            method_resolved_test_phase_distinct_resource_count
        ),
    )


# Test type partitions every test


def test_type_split_partitions_all_tests() -> None:
    tests = [
        make_test(),
        make_test(),
        make_test(is_api_test=False, is_controller_unit_test=True),
        make_test(is_api_test=False),
    ]

    payload = test_scope_stats.compute(tests)

    assert payload["test_count"] == 4
    split = payload["test_type_split"]
    assert split["api"]["test_count"] == 2
    assert split["api"]["pct_of_tests"] == pytest.approx(50.0)
    assert split["controller_unit"]["test_count"] == 1
    assert split["other"]["test_count"] == 1
    assert split["other"]["pct_of_tests"] == pytest.approx(25.0)


# Focal resources scope to API tests only


def test_focal_resource_split_scopes_to_api_tests() -> None:
    tests = [
        make_test(distinct_resource_count=3),
        make_test(distinct_resource_count=1),
        make_test(distinct_resource_count=0),
        make_test(is_api_test=False),
    ]

    split = test_scope_stats.compute(tests)["focal_resource_split"]

    assert split["scope"] == "api_tests"
    assert split["test_count"] == 3
    assert split["multi_resource"]["test_count"] == 1
    assert split["single_resource"]["test_count"] == 1
    assert split["no_resource_recovered"]["test_count"] == 1
    assert split["multi_resource"]["pct_of_tests"] == pytest.approx(100.0 / 3.0)


# Multi-resource tests split by where the extra resources come from


def test_multi_resource_origin_split_separates_fixture_only_breadth() -> None:
    tests = [
        make_test(distinct_resource_count=2, test_phase_distinct_resource_count=2),
        make_test(distinct_resource_count=2, test_phase_distinct_resource_count=1),
        make_test(distinct_resource_count=3, test_phase_distinct_resource_count=0),
        make_test(distinct_resource_count=1, test_phase_distinct_resource_count=1),
    ]

    split = test_scope_stats.compute(tests)["multi_resource_origin_split"]

    assert split["test_count"] == 3
    assert split["multi_in_test_body"]["test_count"] == 1
    assert split["multi_only_with_fixture_requests"]["test_count"] == 2


# A resource recovered only from verb-less events collapses to no-resource


def test_focal_splits_drop_resources_without_a_resolved_method() -> None:
    # Mirrors the corpus case where a request resolves a path but never a verb:
    # path-only keying recovers a resource, but the scope splits must not.
    tests = [
        make_test(
            distinct_resource_count=1,
            test_phase_distinct_resource_count=1,
            method_resolved_distinct_resource_count=0,
            method_resolved_test_phase_distinct_resource_count=0,
            distinct_endpoint_count=0,
        )
    ]

    payload = test_scope_stats.compute(tests)

    resource_split = payload["focal_resource_split"]
    assert resource_split["single_resource"]["test_count"] == 0
    assert resource_split["no_resource_recovered"]["test_count"] == 1
    # With no single-resource tests, the endpoint stage is empty (no 49-style
    # "resource but no endpoint" residue).
    endpoint_split = payload["focal_endpoint_split"]
    assert endpoint_split["test_count"] == 0
    assert endpoint_split["no_endpoint_resolved"]["test_count"] == 0


# Focal endpoints scope to single-resource API tests only


def test_focal_endpoint_split_scopes_to_single_resource_tests() -> None:
    tests = [
        make_test(distinct_resource_count=1, distinct_endpoint_count=2),
        make_test(distinct_resource_count=1, distinct_endpoint_count=1),
        make_test(distinct_resource_count=1, distinct_endpoint_count=0),
        make_test(distinct_resource_count=2, distinct_endpoint_count=4),
        make_test(distinct_resource_count=0),
    ]

    split = test_scope_stats.compute(tests)["focal_endpoint_split"]

    assert split["scope"] == "single_resource_api_tests"
    assert split["test_count"] == 3
    assert split["multi_endpoint"]["test_count"] == 1
    assert split["single_endpoint"]["test_count"] == 1
    assert split["no_endpoint_resolved"]["test_count"] == 1


# Empty scopes report None percentages instead of dividing by zero


def test_empty_input_reports_none_percentages() -> None:
    payload = test_scope_stats.compute([])

    assert payload["test_count"] == 0
    assert payload["test_type_split"]["api"]["pct_of_tests"] is None
    assert payload["focal_resource_split"]["test_count"] == 0
    assert payload["focal_resource_split"]["single_resource"]["pct_of_tests"] is None
    assert payload["multi_resource_origin_split"]["test_count"] == 0
    assert payload["focal_endpoint_split"]["single_endpoint"]["pct_of_tests"] is None
