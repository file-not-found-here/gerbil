from __future__ import annotations

import pytest

from gerbil.statistics import http_behavior as http_behavior_stats
from gerbil.statistics.records import CRUD_OPERATIONS, HTTP_METHODS, TestRecord


def make_test(
    *,
    is_api_test: bool = True,
    is_controller_unit_test: bool = False,
    expanded_ncloc: int = 0,
    expanded_cyclomatic_complexity: int = 0,
    expanded_helper_method_count: int = 0,
    test_helper_method_count: int = 0,
    expanded_objects_created: int = 0,
    expanded_assertion_count: int = 0,
    mocked_interaction_count: int = 0,
    dependency_strategy_label_count: int = 0,
    dispatch_labels: tuple[str, ...] = (),
    has_read_after_write: bool = False,
    has_cleanup_delete: bool = False,
    resource_lifecycle_labels: tuple[str, ...] = (),
    setup_fixture_count: int = 0,
    teardown_fixture_count: int = 0,
    status_range_counts: tuple[int, ...] = (0, 0, 0, 0, 0, 0),
    builder_counts: tuple[int, ...] = (0, 0, 0),
    event_counts: tuple[int, ...] = (0, 0, 0),
    verification_counts: tuple[int, ...] = (0, 0, 0),
    fixture_builder_phase_counts: tuple[int, ...] = (0, 0),
    fixture_event_phase_counts: tuple[int, ...] = (0, 0),
    fixture_verification_phase_counts: tuple[int, ...] = (0, 0),
    http_method_counts: tuple[int, ...] = (0,) * len(HTTP_METHODS),
    crud_operation_counts: tuple[int, ...] = (0,) * len(CRUD_OPERATIONS),
) -> TestRecord:
    return TestRecord(
        is_api_test=is_api_test,
        is_controller_unit_test=is_controller_unit_test,
        expanded_ncloc=expanded_ncloc,
        expanded_cyclomatic_complexity=expanded_cyclomatic_complexity,
        expanded_helper_method_count=expanded_helper_method_count,
        test_helper_method_count=test_helper_method_count,
        expanded_objects_created=expanded_objects_created,
        expanded_assertion_count=expanded_assertion_count,
        mocked_interaction_count=mocked_interaction_count,
        dependency_strategy_label_count=dependency_strategy_label_count,
        dispatch_labels=dispatch_labels,
        has_read_after_write=has_read_after_write,
        has_cleanup_delete=has_cleanup_delete,
        resource_lifecycle_labels=resource_lifecycle_labels,
        setup_fixture_count=setup_fixture_count,
        teardown_fixture_count=teardown_fixture_count,
        status_range_counts=status_range_counts,
        builder_counts=builder_counts,
        event_counts=event_counts,
        verification_counts=verification_counts,
        fixture_builder_phase_counts=fixture_builder_phase_counts,
        fixture_event_phase_counts=fixture_event_phase_counts,
        fixture_verification_phase_counts=fixture_verification_phase_counts,
        http_method_counts=http_method_counts,
        crud_operation_counts=crud_operation_counts,
    )


def test_http_behavior_buckets_units_by_origin_and_totals() -> None:
    tests = [
        make_test(
            builder_counts=(1, 0, 2),
            event_counts=(1, 1, 0),
            verification_counts=(3, 0, 0),
            expanded_assertion_count=4,
            mocked_interaction_count=2,
        ),
        make_test(
            builder_counts=(0, 0, 0),
            event_counts=(1, 0, 0),
            verification_counts=(1, 1, 0),
            expanded_assertion_count=2,
            mocked_interaction_count=0,
        ),
        make_test(is_api_test=False, event_counts=(9, 9, 9)),  # excluded
    ]

    result = http_behavior_stats.compute(tests)

    assert result["api_test_count"] == 2
    fixture = result["by_origin"]["fixture"]
    assert fixture["http_builders"]["max"] == 2.0
    assert fixture["http_builders"]["mean"] == pytest.approx(1.0)
    total = result["by_origin"]["total"]
    # Test 1 total builders 3, test 2 total builders 0 -> mean 1.5.
    assert total["http_builders"]["mean"] == pytest.approx(1.5)
    # Test 1 total events 2, test 2 total events 1 -> mean 1.5.
    assert total["http_events"]["mean"] == pytest.approx(1.5)
    assert result["assertion_count"]["mean"] == pytest.approx(3.0)
    assert result["mocked_interaction_count"]["max"] == 2.0
    assert set(result["by_origin"]) == {
        "test-method",
        "test-helper",
        "fixture",
        "total",
    }


def test_fixture_bucket_reports_setup_teardown_subdistribution() -> None:
    tests = [
        make_test(
            builder_counts=(0, 0, 3),
            event_counts=(0, 0, 2),
            verification_counts=(0, 0, 2),
            fixture_builder_phase_counts=(2, 1),
            fixture_event_phase_counts=(2, 0),
            fixture_verification_phase_counts=(1, 1),
        ),
        make_test(
            builder_counts=(0, 0, 1),
            event_counts=(0, 0, 0),
            verification_counts=(0, 0, 1),
            fixture_builder_phase_counts=(1, 0),
            fixture_event_phase_counts=(0, 0),
            fixture_verification_phase_counts=(0, 1),
        ),
    ]

    result = http_behavior_stats.compute(tests)

    by_phase = result["by_origin"]["fixture"]["by_phase"]
    assert set(by_phase) == {"setup", "teardown"}
    # Setup builders per test: 2 and 1 -> mean 1.5, max 2.
    assert by_phase["setup"]["http_builders"]["mean"] == pytest.approx(1.5)
    assert by_phase["setup"]["http_builders"]["max"] == 2.0
    # Teardown builders per test: 1 and 0 -> mean 0.5.
    assert by_phase["teardown"]["http_builders"]["mean"] == pytest.approx(0.5)
    # Setup events per test: 2 and 0 -> mean 1.0.
    assert by_phase["setup"]["http_events"]["mean"] == pytest.approx(1.0)
    # Teardown verifications per test: 1 and 1 -> mean 1.0.
    assert by_phase["teardown"]["http_verifications"]["mean"] == pytest.approx(1.0)
    # The two phases partition the flat fixture builder distribution (3 and 1).
    assert result["by_origin"]["fixture"]["http_builders"]["max"] == 3.0


def test_http_behavior_reports_fixture_and_helper_distributions() -> None:
    tests = [
        make_test(
            setup_fixture_count=2,
            teardown_fixture_count=1,
            test_helper_method_count=3,
        ),
        make_test(
            setup_fixture_count=0,
            teardown_fixture_count=1,
            test_helper_method_count=1,
        ),
        # Non-API test is excluded from the API-test fixture/helper distributions.
        make_test(
            is_api_test=False,
            setup_fixture_count=9,
            teardown_fixture_count=9,
            test_helper_method_count=9,
        ),
    ]

    result = http_behavior_stats.compute(tests)

    fixtures = result["fixtures"]
    assert fixtures["setup_method_count"]["mean"] == pytest.approx(1.0)
    assert fixtures["setup_method_count"]["max"] == 2.0
    assert fixtures["setup_method_count"]["count"] == 2
    assert fixtures["teardown_method_count"]["mean"] == pytest.approx(1.0)
    assert result["test_helper_method_count"]["mean"] == pytest.approx(2.0)
    assert result["test_helper_method_count"]["count"] == 2


def test_empty_inputs_yield_zeroed_distributions() -> None:
    behavior = http_behavior_stats.compute([])
    assert behavior["scope"] == "api_tests"
    assert behavior["api_test_count"] == 0
    assert behavior["by_origin"]["total"]["http_events"]["count"] == 0
