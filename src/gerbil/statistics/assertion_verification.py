"""Assertion and verification distributions across API tests."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from gerbil.statistics.distributions import (
    count_share,
    count_share_entries,
    share,
    status_code_sort_key,
    summarize,
)
from gerbil.statistics.records import STATUS_RANGE_KEYS, TestRecord

_TARGETS: tuple[str, ...] = ("status", "body", "header")
_COMBINATIONS: tuple[str, ...] = (
    "none",
    "status-only",
    "body-only",
    "header-only",
    "status+body",
    "status+header",
    "body+header",
    "status+body+header",
)
_OUTCOME_RANGES: tuple[str, ...] = ("2xx", "4xx", "5xx")
# Concrete (non-unknown) ranges, in output order. 1xx never occurs in practice;
# the range-coverage metrics below report over these resolved ranges only.
_RESOLVED_RANGES: tuple[str, ...] = ("1xx", "2xx", "3xx", "4xx", "5xx")

# Test-level outcome mix over the success (2xx) and error (4xx/5xx) ranges a test
# asserts; 3xx/1xx/unknown carry no success-or-error meaning, so a test asserting
# only those lands in neither_success_nor_error. Partitions tests with a status
# assertion, in output order.
_OUTCOME_MIX_BUCKETS: tuple[str, ...] = (
    "2xx_only",
    "4xx_only",
    "5xx_only",
    "4xx_and_5xx_only",
    "success_and_error",
    "neither_success_nor_error",
)
# success_and_error splits by which error ranges co-occur with 2xx, so the strict
# happy-plus-negative (2xx+4xx) reading is separable from server-fault (5xx) cases.
_SUCCESS_AND_ERROR_SUBBUCKETS: tuple[str, ...] = (
    "success_and_client_error",
    "success_and_server_error",
    "success_and_both_errors",
)


def _assertion_targets(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    counts = {
        "status": sum(test.assertion_status_count for test in api_tests),
        "body": sum(test.assertion_body_count for test in api_tests),
        "header": sum(test.assertion_header_count for test in api_tests),
    }
    general_count = sum(test.assertion_general_count for test in api_tests)
    exception_count = sum(test.assertion_exception_count for test in api_tests)
    response_surface_total = sum(counts.values())
    countable_total = response_surface_total + general_count + exception_count

    return {
        "countable_assertion_count": countable_total,
        "response_surface_assertion_count": response_surface_total,
        "excluded_countable_assertions": {
            "general": general_count,
            "exception": exception_count,
        },
        "by_target": {
            target: {
                **count_share(counts[target], response_surface_total).to_dict(),
                "proportion_of_countable_assertions": (
                    counts[target] / countable_total if countable_total else None
                ),
            }
            for target in _TARGETS
        },
    }


def _target_assertions_per_test(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    return {
        "status": summarize(
            test.assertion_status_count for test in api_tests
        ).to_dict(),
        "body": summarize(test.assertion_body_count for test in api_tests).to_dict(),
        "header": summarize(
            test.assertion_header_count for test in api_tests
        ).to_dict(),
    }


def _response_surface_combinations(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    total = len(api_tests)
    counts = Counter(test.response_surface_combination for test in api_tests)
    observed_extra = sorted(set(counts) - set(_COMBINATIONS))
    keys = (*_COMBINATIONS, *observed_extra)
    return {
        "total": total,
        "by_combination": count_share_entries(counts, keys, total),
    }


def _oracle_types(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    total = len(api_tests)
    counts = Counter(test.oracle_type_label for test in api_tests)
    keys = sorted(counts)
    return {"total": total, "by_type": count_share_entries(counts, keys, total)}


def _status_assertions(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    range_counts = {
        key: sum(test.status_range_counts[index] for test in api_tests)
        for index, key in enumerate(STATUS_RANGE_KEYS)
    }
    status_assertion_count = sum(range_counts.values())
    status_asserted_test_count = sum(
        1 for test in api_tests if sum(test.status_range_counts) > 0
    )
    test_range_counts = {
        key: sum(
            test.status_range_counts[STATUS_RANGE_KEYS.index(key)] > 0
            for test in api_tests
        )
        for key in _OUTCOME_RANGES
    }
    tests_with_range = {
        key: count_share(test_range_counts[key], len(api_tests)).to_dict()
        for key in _OUTCOME_RANGES
    }
    tests_with_range_among_status_asserted = {
        key: count_share(test_range_counts[key], status_asserted_test_count).to_dict()
        for key in _OUTCOME_RANGES
    }

    exact_counts: Counter[str] = Counter()
    tests_with_exact_code: Counter[str] = Counter()
    for test in api_tests:
        exact_counts.update(test.status_code_counts)
        for status_code, count in test.status_code_counts.items():
            if count > 0:
                tests_with_exact_code[status_code] += 1

    exact_total = sum(exact_counts.values())
    exact_keys = sorted(exact_counts, key=status_code_sort_key)
    return {
        "status_assertion_count": status_assertion_count,
        "status_asserted_test_count": status_asserted_test_count,
        "range_assertion_counts": count_share_entries(
            range_counts, STATUS_RANGE_KEYS, status_assertion_count
        ),
        "tests_with_range": tests_with_range,
        "tests_with_range_among_status_asserted": (
            tests_with_range_among_status_asserted
        ),
        "exact_status_code_assertion_count": exact_total,
        "exact_status_code_assertion_counts": count_share_entries(
            exact_counts, exact_keys, exact_total
        ),
        "tests_with_exact_status_code": count_share_entries(
            tests_with_exact_code, exact_keys, len(api_tests)
        ),
    }


def _status_range_coverage(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    """Coverage of resolved status ranges over sequenced status assertions. The
    assertion-range share partitions resolved status assertions across ranges; the
    sequence membership reports both all-sequence and resolved-status-bearing
    denominators, so multi-range sequences count in every range they assert."""
    assertion_counts: Counter[str] = Counter()
    sequence_membership: Counter[str] = Counter()
    sequence_count = sum(len(test.http_sequence_status_ranges) for test in api_tests)
    status_bearing_sequences = 0
    resolved_status_bearing_sequences = 0
    for test in api_tests:
        for ranges in test.http_sequence_status_ranges:
            if not ranges:
                continue
            status_bearing_sequences += 1
            assertion_counts.update(ranges)
            resolved = {
                range_label for range_label in ranges if range_label != "unknown"
            }
            if not resolved:
                continue
            resolved_status_bearing_sequences += 1
            for range_label in resolved:
                sequence_membership[range_label] += 1

    total_assertions = sum(assertion_counts.values())
    unknown_assertions = assertion_counts.get("unknown", 0)
    resolved_assertions = total_assertions - unknown_assertions
    return {
        "resolved_assertion_range_share": {
            "scope": "sequenced_status_assertions",
            "status_assertion_count": total_assertions,
            "resolved_assertion_count": resolved_assertions,
            "unknown_assertion_count": unknown_assertions,
            "by_range": count_share_entries(
                assertion_counts, _RESOLVED_RANGES, resolved_assertions
            ),
        },
        "all_sequence_membership": {
            "scope": "http_test_sequences",
            "sequence_count": sequence_count,
            "by_range": count_share_entries(
                sequence_membership,
                _RESOLVED_RANGES,
                sequence_count,
            ),
        },
        "sequence_membership": {
            "scope": "http_test_sequences_with_resolved_status_assertion",
            "status_bearing_sequence_count": status_bearing_sequences,
            "resolved_status_bearing_sequence_count": resolved_status_bearing_sequences,
            "by_range": count_share_entries(
                sequence_membership,
                _RESOLVED_RANGES,
                resolved_status_bearing_sequences,
            ),
        },
    }


def _status_outcome_bucket(has_2xx: bool, has_4xx: bool, has_5xx: bool) -> str:
    if has_2xx and (has_4xx or has_5xx):
        return "success_and_error"
    if has_2xx:
        return "2xx_only"
    if has_4xx and has_5xx:
        return "4xx_and_5xx_only"
    if has_4xx:
        return "4xx_only"
    if has_5xx:
        return "5xx_only"
    return "neither_success_nor_error"


def _success_and_error_subbucket(has_4xx: bool, has_5xx: bool) -> str:
    if has_4xx and has_5xx:
        return "success_and_both_errors"
    if has_4xx:
        return "success_and_client_error"
    return "success_and_server_error"


def _status_outcome_mix(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    """Per-test success/error mix over the status ranges a test asserts, scoped to
    tests with at least one status assertion (unknown-only tests count, landing in
    neither_success_nor_error). Folds all sequences of a test together."""
    index = {key: STATUS_RANGE_KEYS.index(key) for key in ("2xx", "4xx", "5xx")}
    asserted = [test for test in api_tests if sum(test.status_range_counts) > 0]
    total = len(asserted)
    mix_counts: Counter[str] = Counter()
    subbucket_counts: Counter[str] = Counter()
    for test in asserted:
        has_2xx = test.status_range_counts[index["2xx"]] > 0
        has_4xx = test.status_range_counts[index["4xx"]] > 0
        has_5xx = test.status_range_counts[index["5xx"]] > 0
        bucket = _status_outcome_bucket(has_2xx, has_4xx, has_5xx)
        mix_counts[bucket] += 1
        if bucket == "success_and_error":
            subbucket_counts[_success_and_error_subbucket(has_4xx, has_5xx)] += 1
    return {
        "scope": "api_tests_with_status_assertion",
        "test_count": total,
        "by_mix": count_share_entries(mix_counts, _OUTCOME_MIX_BUCKETS, total),
        "success_and_error_breakdown": count_share_entries(
            subbucket_counts,
            _SUCCESS_AND_ERROR_SUBBUCKETS,
            mix_counts.get("success_and_error", 0),
        ),
    }


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    api_tests = [test for test in tests if test.is_api_test]
    return {
        "scope": "api_tests",
        "api_test_count": len(api_tests),
        "assertion_targets": _assertion_targets(api_tests),
        "target_assertions_per_test": _target_assertions_per_test(api_tests),
        "response_surface_combinations": _response_surface_combinations(api_tests),
        "oracle_types": _oracle_types(api_tests),
        "status_assertions": _status_assertions(api_tests),
        "status_range_coverage": _status_range_coverage(api_tests),
        "status_outcome_mix": _status_outcome_mix(api_tests),
        # Client/server-error coverage lives in status_assertions.tests_with_range;
        # exceptions are not a status range, so they get their own per-test share.
        "has_exception_assertion": share(
            test.has_exception_assertion for test in api_tests
        ).to_dict(),
    }
