"""Framework-identity distributions: testing frameworks over test classes and
HTTP dispatch frameworks over HTTP call sites."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from gerbil.analysis.schema import TestingFramework
from gerbil.statistics.distributions import share, summarize
from gerbil.statistics.records import (
    HTTP_DISPATCH_FRAMEWORKS,
    TestClassRecord,
    TestRecord,
)

# Testing-framework labels in schema declaration order, for stable output.
TESTING_FRAMEWORKS: tuple[str, ...] = tuple(
    framework.value for framework in TestingFramework
)

# Categories partition TestingFramework by the role the library plays in a
# test class: executing tests, expressing assertions, building test doubles,
# or driving API/integration tests (harnesses that bundle HTTP dispatch and
# verification into one test-only library).
TESTING_FRAMEWORK_CATEGORIES: tuple[tuple[str, tuple[TestingFramework, ...]], ...] = (
    (
        "test-runner",
        (
            TestingFramework.JUNIT3,
            TestingFramework.JUNIT4,
            TestingFramework.JUNIT5,
            TestingFramework.TESTNG,
        ),
    ),
    (
        "assertion-library",
        (
            TestingFramework.ASSERTJ,
            TestingFramework.HAMCREST,
            TestingFramework.GOOGLE_TRUTH,
        ),
    ),
    (
        "mocking-library",
        (
            TestingFramework.MOCKITO,
            TestingFramework.EASYMOCK,
            TestingFramework.POWERMOCK,
            TestingFramework.JMOCKIT,
            TestingFramework.JMOCK,
        ),
    ),
    (
        "api-test-framework",
        (
            TestingFramework.SPRING_TEST,
            TestingFramework.REST_ASSURED,
            TestingFramework.KARATE,
            TestingFramework.PACT,
            TestingFramework.CITRUS,
        ),
    ),
)


def _class_split_entry(class_count: int, total: int) -> dict[str, Any]:
    return {
        "class_count": class_count,
        "pct_of_classes": (100.0 * class_count / total if total else None),
    }


def compute_testing_framework_distribution(
    test_classes: Sequence[TestClassRecord],
) -> dict[str, Any]:
    total = len(test_classes)

    framework_split = {
        framework: _class_split_entry(
            sum(
                1
                for test_class in test_classes
                if framework in test_class.testing_frameworks
            ),
            total,
        )
        for framework in TESTING_FRAMEWORKS
    }

    category_split: dict[str, Any] = {}
    for category, members in TESTING_FRAMEWORK_CATEGORIES:
        member_values = frozenset(member.value for member in members)
        class_count = sum(
            1
            for test_class in test_classes
            if member_values.intersection(test_class.testing_frameworks)
        )
        category_split[category] = {
            "frameworks": [member.value for member in members],
            **_class_split_entry(class_count, total),
        }

    return {
        "scope": "test_classes",
        "test_class_count": total,
        "framework_split": framework_split,
        "category_split": category_split,
        "classes_without_frameworks": share(
            not test_class.testing_frameworks for test_class in test_classes
        ).to_dict(),
        "framework_count_per_class": summarize(
            len(set(test_class.testing_frameworks)) for test_class in test_classes
        ).to_dict(),
    }


def _dispatch_framework_payload(
    counts_per_test: Sequence[tuple[int, ...]],
    *,
    scope: str,
    count_key: str,
    pct_key: str,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for test_counts in counts_per_test:
        for framework, count in zip(HTTP_DISPATCH_FRAMEWORKS, test_counts):
            counts[framework] += count
    total = sum(counts.values())

    framework_split = {
        framework: {
            count_key: counts[framework],
            pct_key: (100.0 * counts[framework] / total if total else None),
        }
        for framework in HTTP_DISPATCH_FRAMEWORKS
    }

    return {
        "scope": scope,
        count_key: total,
        "framework_split": framework_split,
    }


def compute_http_dispatch_framework_distribution(
    tests: Sequence[TestRecord],
) -> dict[str, Any]:
    return _dispatch_framework_payload(
        [test.http_call_framework_counts for test in tests],
        scope="http_call_sites",
        count_key="call_site_count",
        pct_key="pct_of_call_sites",
    )


def compute_http_dispatch_framework_event_distribution(
    tests: Sequence[TestRecord],
) -> dict[str, Any]:
    return _dispatch_framework_payload(
        [test.http_event_framework_counts for test in tests],
        scope="http_dispatch_events",
        count_key="event_count",
        pct_key="pct_of_events",
    )
