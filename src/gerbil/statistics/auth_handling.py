"""Auth-handling label distribution over API tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from gerbil.statistics.records import AUTH_HANDLING_LABELS, TestRecord


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    api_tests = [test for test in tests if test.is_api_test]
    total = len(api_tests)

    label_split: dict[str, Any] = {}
    for label in AUTH_HANDLING_LABELS:
        count = sum(1 for test in api_tests if test.auth_handling_label == label)
        label_split[label] = {
            "test_count": count,
            "pct_of_tests": (100.0 * count / total) if total else None,
        }

    return {
        "scope": "api_tests",
        "test_count": total,
        "label_split": label_split,
    }
