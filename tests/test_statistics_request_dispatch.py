from __future__ import annotations

import pytest

from gerbil.statistics import request_dispatch as request_dispatch_stats
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
        fixture_builder_phase_counts=(0, 0),
        fixture_event_phase_counts=(0, 0),
        fixture_verification_phase_counts=(0, 0),
        http_method_counts=http_method_counts,
        crud_operation_counts=crud_operation_counts,
    )


def test_request_dispatch_split_counts_each_label_and_multilabel() -> None:
    tests = [
        make_test(dispatch_labels=("in-process",), expanded_ncloc=10),
        make_test(dispatch_labels=("in-process", "local-network"), expanded_ncloc=30),
        make_test(dispatch_labels=("local-network",), expanded_ncloc=50),
        make_test(is_api_test=False, dispatch_labels=()),  # excluded
    ]

    result = request_dispatch_stats.compute(tests)

    assert result["scope"] == "api_tests_with_dispatch_labels"
    assert result["labeled_test_count"] == 3
    assert result["label_split"]["in-process"]["test_count"] == 2
    assert result["label_split"]["in-process"]["pct_of_labeled_tests"] == pytest.approx(
        100.0 * 2 / 3
    )
    assert result["label_split"]["local-network"]["test_count"] == 2
    assert result["multiple_label_tests"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }
    # in-process tests are ncloc 10 and 30 -> mean 20.
    assert result["per_label"]["in-process"]["metrics"]["expanded_ncloc"][
        "mean"
    ] == pytest.approx(20.0)
    # "remote-network" never appears, so it is omitted.
    assert "remote-network" not in result["per_label"]


def test_request_dispatch_lifecycle_and_status_ranges_per_label() -> None:
    tests = [
        make_test(
            dispatch_labels=("local-network",),
            has_read_after_write=True,
            has_cleanup_delete=False,
            status_range_counts=(0, 2, 0, 1, 0, 0),
            mocked_interaction_count=1,
            dependency_strategy_label_count=2,
        ),
        make_test(
            dispatch_labels=("local-network",),
            has_read_after_write=False,
            has_cleanup_delete=False,
            status_range_counts=(0, 4, 0, 3, 0, 0),
            mocked_interaction_count=3,
            dependency_strategy_label_count=0,
        ),
    ]

    per_label = request_dispatch_stats.compute(tests)["per_label"]["local-network"]

    assert per_label["resource_lifecycle"]["has_read_after_write"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    assert per_label["resource_lifecycle"]["has_cleanup_delete"]["count"] == 0
    assert per_label["status_range_counts"]["2xx"]["mean"] == pytest.approx(3.0)
    assert per_label["status_range_counts"]["4xx"]["max"] == 3.0
    assert per_label["status_range_counts"]["1xx"]["max"] == 0.0
    assert per_label["metrics"]["mocked_interaction_count"]["mean"] == pytest.approx(
        2.0
    )
    assert per_label["metrics"]["dependency_strategy_label_count"][
        "max"
    ] == pytest.approx(2.0)


def test_empty_inputs_yield_zeroed_distributions() -> None:
    dispatch = request_dispatch_stats.compute([])
    assert dispatch["labeled_test_count"] == 0
    assert dispatch["label_split"] == {}
    assert dispatch["multiple_label_tests"]["proportion"] is None
