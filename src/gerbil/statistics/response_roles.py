"""Verification response-role split and response-data extraction usage over
API tests."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from gerbil.statistics.distributions import count_share_entries, share, summarize
from gerbil.statistics.records import (
    VERIFICATION_RESPONSE_ROLE_BUCKETS,
    TestRecord,
    request_event_total,
)


def _verification_role_split(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for test in api_tests:
        for role, count in zip(
            VERIFICATION_RESPONSE_ROLE_BUCKETS, test.verification_response_role_counts
        ):
            counts[role] += count
    total = sum(counts.values())
    return {
        "scope": "http_verifications",
        "verification_count": total,
        "by_role": count_share_entries(
            counts, VERIFICATION_RESPONSE_ROLE_BUCKETS, total
        ),
    }


def _extraction_usage(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    multi_request_tests = [test for test in api_tests if request_event_total(test) >= 2]
    return {
        "tests_with_extraction": share(
            test.response_extraction_count > 0 for test in api_tests
        ).to_dict(),
        "extraction_count_per_test": summarize(
            test.response_extraction_count for test in api_tests
        ).to_dict(),
        # Extraction inside a multi-request test: a coarse static signal for
        # response-data reuse. Events and extractions count regardless of
        # origin or position, so fixture extractions and extractions after the
        # final request are included.
        "multi_request_tests_with_extraction": {
            "scope": "api_tests_with_multiple_request_events",
            **share(
                test.response_extraction_count > 0 for test in multi_request_tests
            ).to_dict(),
        },
    }


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    api_tests = [test for test in tests if test.is_api_test]
    return {
        "scope": "api_tests",
        "api_test_count": len(api_tests),
        "verification_roles": _verification_role_split(api_tests),
        "response_extraction": _extraction_usage(api_tests),
    }
