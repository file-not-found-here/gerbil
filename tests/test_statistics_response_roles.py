from __future__ import annotations

import pytest

from gerbil.statistics import response_roles as response_roles_stats
from gerbil.statistics.records import (
    CRUD_OPERATIONS,
    HTTP_METHODS,
    VERIFICATION_RESPONSE_ROLE_BUCKETS,
    TestRecord,
)


def role_counts(**counts: int) -> tuple[int, ...]:
    """Aligned role-count tuple from keyword counts ('status_assertion' form)."""
    by_bucket = {key.replace("_", "-"): value for key, value in counts.items()}
    return tuple(
        by_bucket.get(bucket, 0) for bucket in VERIFICATION_RESPONSE_ROLE_BUCKETS
    )


def make_test(
    *,
    is_api_test: bool = True,
    verification_response_role_counts: tuple[int, ...] | None = None,
    response_extraction_count: int = 0,
    request_event_count: int = 0,
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
        verification_response_role_counts=(
            verification_response_role_counts or role_counts()
        ),
        response_extraction_count=response_extraction_count,
    )


def test_verification_roles_pool_across_api_tests() -> None:
    tests = [
        make_test(
            verification_response_role_counts=role_counts(
                status_assertion=2, body_assertion=1
            )
        ),
        make_test(verification_response_role_counts=role_counts(none=1, matcher=1)),
        # Non-API tests stay out of the pool.
        make_test(
            is_api_test=False,
            verification_response_role_counts=role_counts(status_assertion=5),
        ),
    ]

    roles = response_roles_stats.compute(tests)["verification_roles"]

    assert roles["verification_count"] == 5
    assert list(roles["by_role"]) == list(VERIFICATION_RESPONSE_ROLE_BUCKETS)
    assert roles["by_role"]["status-assertion"]["count"] == 2
    assert roles["by_role"]["matcher"]["count"] == 1
    assert roles["by_role"]["none"]["count"] == 1
    assert roles["by_role"]["status-assertion"]["proportion"] == pytest.approx(0.4)
    assert roles["by_role"]["extractor"]["count"] == 0


def test_extraction_shares_and_distribution() -> None:
    tests = [
        make_test(response_extraction_count=2, request_event_count=1),
        make_test(response_extraction_count=0, request_event_count=1),
    ]

    extraction = response_roles_stats.compute(tests)["response_extraction"]

    assert extraction["tests_with_extraction"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    distribution = extraction["extraction_count_per_test"]
    assert distribution["count"] == 2
    assert distribution["max"] == 2.0


def test_multi_request_extraction_gates_to_multi_event_tests() -> None:
    tests = [
        make_test(response_extraction_count=1, request_event_count=3),
        make_test(response_extraction_count=0, request_event_count=2),
        # Single-request tests stay out of the chaining denominator.
        make_test(response_extraction_count=1, request_event_count=1),
    ]

    chaining = response_roles_stats.compute(tests)["response_extraction"][
        "multi_request_tests_with_extraction"
    ]

    assert chaining["count"] == 1
    assert chaining["total"] == 2
    assert chaining["proportion"] == pytest.approx(0.5)


def test_empty_api_cohort_reports_none_proportions() -> None:
    payload = response_roles_stats.compute([make_test(is_api_test=False)])

    assert payload["api_test_count"] == 0
    assert payload["verification_roles"]["verification_count"] == 0
    assert (
        payload["verification_roles"]["by_role"]["status-assertion"]["proportion"]
        is None
    )
