"""Test-scope narrowing splits feeding the scope Sankey: test type, focal
resources (distinct normalized resources targeted), and focal endpoints
(distinct method + normalized path pairs) for single-resource API tests.

A request event with no resolved HTTP method is treated as having no path here,
so it anchors neither a resource nor an endpoint; this keeps the resource and
endpoint stages consistent (every counted resource carries a method+path
endpoint). Resources are therefore counted from
method_resolved_distinct_resource_count rather than the path-only
distinct_resource_count the other statistics use."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from gerbil.statistics.records import TestRecord


def _bucket(count: int, total: int) -> dict[str, Any]:
    return {
        "test_count": count,
        "pct_of_tests": (100.0 * count / total) if total else None,
    }


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    total = len(tests)
    api_tests = [test for test in tests if test.is_api_test]
    controller_unit_count = sum(1 for test in tests if test.is_controller_unit_test)
    api_count = len(api_tests)

    # Focal counts span every lifecycle phase: fixture requests (auth, seeding,
    # cleanup) exercise the same system under test as test-body requests. Resources
    # are counted under the no-method => no-path rule (see module docstring), so a
    # request whose verb never resolved contributes neither a resource nor an
    # endpoint.
    multi_resource = [
        test for test in api_tests if test.method_resolved_distinct_resource_count > 1
    ]
    single_resource = [
        test for test in api_tests if test.method_resolved_distinct_resource_count == 1
    ]
    no_resource_count = sum(
        1 for test in api_tests if test.method_resolved_distinct_resource_count == 0
    )

    # Distinguishes genuinely cross-resource test bodies from tests that are
    # multi-resource only because fixtures touch additional resources.
    multi_in_test_body_count = sum(
        1
        for test in multi_resource
        if test.method_resolved_test_phase_distinct_resource_count > 1
    )

    single_resource_count = len(single_resource)
    multi_endpoint_count = sum(
        1 for test in single_resource if test.distinct_endpoint_count > 1
    )
    single_endpoint_count = sum(
        1 for test in single_resource if test.distinct_endpoint_count == 1
    )
    # A recovered resource with no endpoint means no request on it resolved
    # both an HTTP method and a normalized path.
    no_endpoint_count = sum(
        1 for test in single_resource if test.distinct_endpoint_count == 0
    )

    return {
        "scope": "all_tests",
        "test_count": total,
        "test_type_split": {
            "api": _bucket(api_count, total),
            "controller_unit": _bucket(controller_unit_count, total),
            "other": _bucket(total - api_count - controller_unit_count, total),
        },
        "focal_resource_split": {
            "scope": "api_tests",
            "test_count": api_count,
            "multi_resource": _bucket(len(multi_resource), api_count),
            "single_resource": _bucket(single_resource_count, api_count),
            "no_resource_recovered": _bucket(no_resource_count, api_count),
        },
        "multi_resource_origin_split": {
            "scope": "multi_resource_api_tests",
            "test_count": len(multi_resource),
            "multi_in_test_body": _bucket(
                multi_in_test_body_count, len(multi_resource)
            ),
            "multi_only_with_fixture_requests": _bucket(
                len(multi_resource) - multi_in_test_body_count, len(multi_resource)
            ),
        },
        "focal_endpoint_split": {
            "scope": "single_resource_api_tests",
            "test_count": single_resource_count,
            "multi_endpoint": _bucket(multi_endpoint_count, single_resource_count),
            "single_endpoint": _bucket(single_endpoint_count, single_resource_count),
            "no_endpoint_resolved": _bucket(no_endpoint_count, single_resource_count),
        },
    }
