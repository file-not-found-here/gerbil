"""Project-level composition: counts of projects in each quadrant of endpoint
presence x API-test presence, plus a focused profile of the API-test universe
(every project with API tests, with or without endpoints)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from gerbil.statistics.distributions import summarize
from gerbil.statistics.records import ProjectStatsRecord, api_test_count

# (quadrant name, has endpoints, has API tests), in output order.
_QUADRANTS: tuple[tuple[str, bool, bool], ...] = (
    ("endpoints_and_api_tests", True, True),
    ("endpoints_no_api_tests", True, False),
    ("api_tests_no_endpoints", False, True),
    ("no_endpoints_no_api_tests", False, False),
)


def _quadrant_payload(records: Sequence[ProjectStatsRecord]) -> dict[str, Any]:
    test_counts = [len(record.tests) for record in records]
    api_test_counts = [api_test_count(record) for record in records]
    return {
        "project_count": len(records),
        "test_count": sum(test_counts),
        "api_test_count": sum(api_test_counts),
        # An untested repo versus one whose tests live at a non-HTTP layer.
        "projects_with_zero_tests": sum(1 for count in test_counts if count == 0),
        "tests_per_project": summarize(test_counts).to_dict(),
        "api_tests_per_project": summarize(api_test_counts).to_dict(),
    }


def _api_test_universe(records: Sequence[ProjectStatsRecord]) -> dict[str, Any]:
    """Composition of every project carrying API tests, with or without endpoints.

    Distributions are taken over the natural unit named in each key: per project
    or per API test.
    """
    members = [record for record in records if api_test_count(record) > 0]
    api_test_counts = [api_test_count(record) for record in members]
    api_test_class_counts = [
        sum(1 for test_class in record.test_classes if test_class.api_test_count > 0)
        for record in members
    ]
    api_test_nclocs = [
        test.expanded_ncloc
        for record in members
        for test in record.tests
        if test.is_api_test
    ]
    # Fixtures declared on the project's API-test classes, summed per project.
    fixture_counts = [
        sum(
            test_class.fixture_count
            for test_class in record.test_classes
            if test_class.api_test_count > 0
        )
        for record in members
    ]
    application_class_counts = [record.application_class_count for record in members]
    application_method_counts = [record.application_method_count for record in members]
    endpoint_counts = [len(record.endpoints) for record in members]
    return {
        "scope": "projects_with_api_tests",
        "project_count": len(members),
        "api_test_count": sum(api_test_counts),
        "api_test_class_count": sum(api_test_class_counts),
        "application_class_count": sum(application_class_counts),
        "application_method_count": sum(application_method_counts),
        "endpoint_count": sum(endpoint_counts),
        "api_tests_per_project": summarize(api_test_counts).to_dict(),
        "api_test_classes_per_project": summarize(api_test_class_counts).to_dict(),
        "api_test_ncloc": summarize(api_test_nclocs).to_dict(),
        "fixtures_per_project": summarize(fixture_counts).to_dict(),
        "application_classes_per_project": summarize(
            application_class_counts
        ).to_dict(),
        "application_methods_per_project": summarize(
            application_method_counts
        ).to_dict(),
        "endpoints_per_project": summarize(endpoint_counts).to_dict(),
    }


def compute(records: Sequence[ProjectStatsRecord]) -> dict[str, Any]:
    quadrants: dict[str, Any] = {}
    for name, wants_endpoints, wants_api_tests in _QUADRANTS:
        members = [
            record
            for record in records
            if bool(record.endpoints) == wants_endpoints
            and (api_test_count(record) > 0) == wants_api_tests
        ]
        quadrants[name] = _quadrant_payload(members)
    return {
        "scope": "all_projects",
        "project_count": len(records),
        # Projects carrying no tests at all (not merely no API tests).
        "projects_with_zero_tests": sum(1 for record in records if not record.tests),
        "quadrants": quadrants,
        "api_test_universe": _api_test_universe(records),
    }
