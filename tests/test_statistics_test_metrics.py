from __future__ import annotations

import pytest

from gerbil.statistics import test_metrics as test_metrics_stats
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


def test_metric_comparison_separates_cohorts() -> None:
    tests = [
        make_test(is_api_test=True, expanded_ncloc=10, expanded_assertion_count=8),
        make_test(is_api_test=True, expanded_ncloc=20, expanded_assertion_count=2),
        make_test(is_api_test=False, expanded_ncloc=4, expanded_assertion_count=1),
        make_test(
            is_api_test=False,
            is_controller_unit_test=True,
            expanded_ncloc=6,
            expanded_assertion_count=3,
        ),
    ]

    result = test_metrics_stats.compute(tests)

    assert result["scope"] == "all_tests"
    assert result["test_counts"] == {
        "total": 4,
        "api": 2,
        "non_api": 2,
        "controller_unit_test": 1,
    }
    ncloc = result["comparisons"]["expanded_ncloc"]
    assert ncloc["api"]["mean"] == pytest.approx(15.0)
    assert ncloc["non_api"]["mean"] == pytest.approx(5.0)
    assert ncloc["controller_unit_test"]["mean"] == pytest.approx(6.0)
    # Expanded assertion summaries are only built for API tests, so this metric is
    # reported for the API cohort alone (no misleading zero non-API columns).
    assertions = result["comparisons"]["expanded_assertion_count"]
    assert set(assertions) == {"api"}
    assert assertions["api"]["mean"] == pytest.approx(5.0)
    assert set(result["comparisons"]) == {
        "expanded_ncloc",
        "expanded_cyclomatic_complexity",
        "expanded_helper_method_count",
        "expanded_objects_created",
        "expanded_assertion_count",
    }


def test_empty_inputs_yield_zeroed_distributions() -> None:
    metrics = test_metrics_stats.compute([])
    assert metrics["test_counts"]["total"] == 0
    assert metrics["comparisons"]["expanded_ncloc"]["api"]["count"] == 0
