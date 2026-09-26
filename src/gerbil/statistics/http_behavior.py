"""Distributions of where API-test HTTP behavior lives (test method vs. test
helper vs. fixtures, fixture-helper folded into fixtures, fixtures further split
into setup vs. teardown) plus the fixture and helper-method surface around each
API test."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from gerbil.statistics.distributions import summarize
from gerbil.statistics.records import (
    FIXTURE_PHASE_BUCKETS,
    ORIGIN_BUCKETS,
    TestRecord,
)

# "total" is the per-test sum across all origin buckets.
_TOTAL_BUCKET = "total"
# Key under the fixture origin bucket holding its setup/teardown sub-distributions.
_BY_PHASE_KEY = "by_phase"
_FIXTURE_BUCKET = "fixture"


def _unit_distributions(
    api_tests: Sequence[TestRecord], bucket_index: int | None
) -> dict[str, Any]:
    def unit_count(counts: tuple[int, ...]) -> int:
        if bucket_index is None:
            return sum(counts)
        return counts[bucket_index]

    return {
        "http_builders": summarize(
            unit_count(test.builder_counts) for test in api_tests
        ).to_dict(),
        "http_events": summarize(
            unit_count(test.event_counts) for test in api_tests
        ).to_dict(),
        "http_verifications": summarize(
            unit_count(test.verification_counts) for test in api_tests
        ).to_dict(),
    }


def _fixture_phase_distributions(
    api_tests: Sequence[TestRecord], phase_index: int
) -> dict[str, Any]:
    return {
        "http_builders": summarize(
            test.fixture_builder_phase_counts[phase_index] for test in api_tests
        ).to_dict(),
        "http_events": summarize(
            test.fixture_event_phase_counts[phase_index] for test in api_tests
        ).to_dict(),
        "http_verifications": summarize(
            test.fixture_verification_phase_counts[phase_index] for test in api_tests
        ).to_dict(),
    }


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    api_tests = [test for test in tests if test.is_api_test]

    by_origin: dict[str, Any] = {
        bucket: _unit_distributions(api_tests, index)
        for index, bucket in enumerate(ORIGIN_BUCKETS)
    }
    by_origin[_FIXTURE_BUCKET][_BY_PHASE_KEY] = {
        phase: _fixture_phase_distributions(api_tests, phase_index)
        for phase_index, phase in enumerate(FIXTURE_PHASE_BUCKETS)
    }
    by_origin[_TOTAL_BUCKET] = _unit_distributions(api_tests, None)

    return {
        "scope": "api_tests",
        "api_test_count": len(api_tests),
        "by_origin": by_origin,
        "assertion_count": summarize(
            test.expanded_assertion_count for test in api_tests
        ).to_dict(),
        "mocked_interaction_count": summarize(
            test.mocked_interaction_count for test in api_tests
        ).to_dict(),
        "fixtures": {
            "setup_method_count": summarize(
                test.setup_fixture_count for test in api_tests
            ).to_dict(),
            "teardown_method_count": summarize(
                test.teardown_fixture_count for test in api_tests
            ).to_dict(),
        },
        "test_helper_method_count": summarize(
            test.test_helper_method_count for test in api_tests
        ).to_dict(),
    }
