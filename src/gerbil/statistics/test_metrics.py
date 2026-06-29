"""Distributions of expanded test-method metrics across API, non-API, and
controller-unit-test cohorts."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from gerbil.statistics.distributions import summarize
from gerbil.statistics.records import TestRecord

# Expanded (whole-runtime) metrics compared across cohorts.
_METRIC_ACCESSORS: tuple[tuple[str, Callable[[TestRecord], int]], ...] = (
    ("expanded_ncloc", lambda test: test.expanded_ncloc),
    (
        "expanded_cyclomatic_complexity",
        lambda test: test.expanded_cyclomatic_complexity,
    ),
    ("expanded_helper_method_count", lambda test: test.expanded_helper_method_count),
    ("expanded_objects_created", lambda test: test.expanded_objects_created),
    ("expanded_assertion_count", lambda test: test.expanded_assertion_count),
)

# The expanded assertion summary is only built for API tests: non-API tests
# early-return from get_test_method_analysis_info before build_assertion_summary,
# so expanded_assertion_count is structurally zero for every other cohort.
# Report it for the API cohort only rather than emitting misleading zero columns.
_API_ONLY_METRICS: frozenset[str] = frozenset({"expanded_assertion_count"})


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    cohorts: dict[str, list[TestRecord]] = {
        "api": [test for test in tests if test.is_api_test],
        "non_api": [test for test in tests if not test.is_api_test],
        "controller_unit_test": [
            test for test in tests if test.is_controller_unit_test
        ],
    }

    comparisons: dict[str, dict[str, Any]] = {}
    for metric_name, accessor in _METRIC_ACCESSORS:
        emitted = (
            {"api": cohorts["api"]} if metric_name in _API_ONLY_METRICS else cohorts
        )
        comparisons[metric_name] = {
            cohort: summarize(accessor(test) for test in cohort_tests).to_dict()
            for cohort, cohort_tests in emitted.items()
        }

    return {
        "scope": "all_tests",
        "test_counts": {
            "total": len(tests),
            **{cohort: len(cohort_tests) for cohort, cohort_tests in cohorts.items()},
        },
        "comparisons": comparisons,
    }
