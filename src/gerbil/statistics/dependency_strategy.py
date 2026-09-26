"""Dependency-strategy distributions over API tests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from gerbil.analysis.schema import DependencyStrategy
from gerbil.statistics.distributions import share, summarize
from gerbil.statistics.records import TestRecord

_STRATEGIES: tuple[str, ...] = tuple(strategy.value for strategy in DependencyStrategy)


def _strategy_split(tests: Sequence[TestRecord]) -> dict[str, Any]:
    total = len(tests)
    split: dict[str, Any] = {}
    for strategy in _STRATEGIES:
        count = sum(1 for test in tests if strategy in test.dependency_strategy_labels)
        split[strategy] = {
            "test_count": count,
            "pct_of_tests": (100.0 * count / total if total else None),
        }
    return split


def _cohort_payload(tests: Sequence[TestRecord]) -> dict[str, Any]:
    return {
        "test_count": len(tests),
        "strategy_split": _strategy_split(tests),
        "multiple_strategy_tests": share(
            len(set(test.dependency_strategy_labels)) >= 2 for test in tests
        ).to_dict(),
        "strategy_label_count_per_test": summarize(
            len(set(test.dependency_strategy_labels)) for test in tests
        ).to_dict(),
    }


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    api_tests = [test for test in tests if test.is_api_test]
    return {
        "scope": "api_tests",
        **_cohort_payload(api_tests),
    }
