"""Distributions keyed by request-dispatch label across API tests.

A test with multiple dispatch labels is counted under every label it carries.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from gerbil.analysis.schema import RequestDispatch
from gerbil.statistics.distributions import share, summarize
from gerbil.statistics.records import STATUS_RANGE_KEYS, TestRecord

# Canonical label order for stable output.
_DISPATCH_LABELS: tuple[str, ...] = tuple(label.value for label in RequestDispatch)

_METRIC_ACCESSORS: tuple[tuple[str, Callable[[TestRecord], int]], ...] = (
    ("expanded_ncloc", lambda test: test.expanded_ncloc),
    (
        "expanded_cyclomatic_complexity",
        lambda test: test.expanded_cyclomatic_complexity,
    ),
    ("expanded_objects_created", lambda test: test.expanded_objects_created),
    ("expanded_helper_method_count", lambda test: test.expanded_helper_method_count),
    ("mocked_interaction_count", lambda test: test.mocked_interaction_count),
    (
        "dependency_strategy_label_count",
        lambda test: test.dependency_strategy_label_count,
    ),
)


def _label_metrics(label_tests: Sequence[TestRecord]) -> dict[str, Any]:
    return {
        metric_name: summarize(accessor(test) for test in label_tests).to_dict()
        for metric_name, accessor in _METRIC_ACCESSORS
    }


def _status_range_distributions(label_tests: Sequence[TestRecord]) -> dict[str, Any]:
    return {
        range_key: summarize(
            test.status_range_counts[index] for test in label_tests
        ).to_dict()
        for index, range_key in enumerate(STATUS_RANGE_KEYS)
    }


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    # Dispatch labels are only assigned to API tests; non-API tests carry none.
    labeled_tests = [test for test in tests if test.dispatch_labels]
    labeled_count = len(labeled_tests)

    observed_labels = [
        label
        for label in _DISPATCH_LABELS
        if any(label in test.dispatch_labels for test in labeled_tests)
    ]

    label_split: dict[str, Any] = {}
    per_label: dict[str, Any] = {}
    for label in observed_labels:
        label_tests = [test for test in labeled_tests if label in test.dispatch_labels]
        label_split[label] = {
            "test_count": len(label_tests),
            "pct_of_labeled_tests": (
                100.0 * len(label_tests) / labeled_count if labeled_count else None
            ),
        }
        per_label[label] = {
            "test_count": len(label_tests),
            "metrics": _label_metrics(label_tests),
            "resource_lifecycle": {
                "has_read_after_write": share(
                    test.has_read_after_write for test in label_tests
                ).to_dict(),
                "has_cleanup_delete": share(
                    test.has_cleanup_delete for test in label_tests
                ).to_dict(),
            },
            "status_range_counts": _status_range_distributions(label_tests),
        }

    multiple_label_count = sum(
        1 for test in labeled_tests if len(set(test.dispatch_labels)) >= 2
    )

    return {
        "scope": "api_tests_with_dispatch_labels",
        "labeled_test_count": labeled_count,
        "label_split": label_split,
        "multiple_label_tests": {
            "count": multiple_label_count,
            "total": labeled_count,
            "proportion": (
                multiple_label_count / labeled_count if labeled_count else None
            ),
        },
        "per_label": per_label,
    }
