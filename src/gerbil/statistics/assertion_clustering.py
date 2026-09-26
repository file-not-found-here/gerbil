"""How sequenced body and header assertions cluster: their per-sequence count
distribution, whether a test's checks pile onto one dispatch or spread across
many, and whether body and header checks co-occur."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from gerbil.statistics.distributions import count_share, summarize
from gerbil.statistics.records import TestRecord

# Status is reported only as a contrast baseline for the per-sequence view (it is
# the known one-per-dispatch success/error gate); the clustering and co-occurrence
# analyses focus on body and header.
_TARGETS: tuple[str, ...] = ("status", "body", "header")
_CO_TARGETS: tuple[str, ...] = ("body", "header")


def _target_sequence_counts(test: TestRecord, target: str) -> tuple[int, ...]:
    """Per-sequence count of a target role's response checks, aligned with the
    test's sequences."""
    if target == "status":
        return test.http_sequence_status_check_counts
    if target == "body":
        return test.http_sequence_body_check_counts
    return test.http_sequence_header_check_counts


def _top_share_of_checks(
    ranked: Sequence[int], total: int, percent: int
) -> float | None:
    """Share of `total` checks held by the top `percent`% of `ranked` (descending)."""
    item_count = len(ranked)
    top_count = max(1, math.ceil(item_count * percent / 100)) if item_count else 0
    return sum(ranked[:top_count]) / total if total else None


def _check_concentration(bearing_counts: Sequence[int]) -> dict[str, Any]:
    """How top-sided a role's sequenced checks are across the sequences that carry
    it. top_decile_share_of_checks is the headline: the share of all the role's
    checks held by the top 10% of bearing sequences ranked by check count.
    evenness_ratio divides that by the decile's own share of bearing sequences, so
    1.0 is a perfectly even spread and higher is more top-heavy (a thin tail of
    snapshot-style sequences holding a disproportionate share of the checks). The
    bracketing 5%/25% cuts give the surrounding Lorenz shape."""
    ranked = sorted(bearing_counts, reverse=True)
    item_count = len(ranked)
    total = sum(ranked)
    decile_count = max(1, math.ceil(item_count * 10 / 100)) if item_count else 0
    decile_share = sum(ranked[:decile_count]) / total if total else None
    decile_sequence_share = decile_count / item_count if item_count else None
    return {
        "bearing_sequence_count": item_count,
        "total_checks": total,
        "top_decile_sequence_count": decile_count,
        "top_decile_share_of_checks": decile_share,
        # >1 means top-heavy: the decile holds more than its proportional share.
        "evenness_ratio": (
            decile_share / decile_sequence_share
            if decile_share is not None and decile_sequence_share
            else None
        ),
        "top_5pct_share_of_checks": _top_share_of_checks(ranked, total, 5),
        "top_25pct_share_of_checks": _top_share_of_checks(ranked, total, 25),
    }


def _per_sequence_distribution(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    # Per-sequence count of each role's checks, pooled over every API test's
    # sequences. The headline is checks_per_bearing_sequence: given a sequence
    # carries the role at all, how many of that role it carries.
    pooled: dict[str, list[int]] = {target: [] for target in _TARGETS}
    for test in api_tests:
        for target in _TARGETS:
            pooled[target].extend(_target_sequence_counts(test, target))
    sequence_count = len(pooled[_TARGETS[0]])

    by_target: dict[str, Any] = {}
    for target in _TARGETS:
        counts = pooled[target]
        bearing = [count for count in counts if count > 0]
        bearing_count = len(bearing)
        by_target[target] = {
            "sequences_with_assertion": count_share(
                bearing_count, sequence_count
            ).to_dict(),
            # The requested distribution: among sequences that carry this role,
            # how many of it they carry.
            "checks_per_bearing_sequence": summarize(bearing).to_dict(),
            "share_exactly_one_check": count_share(
                sum(1 for count in bearing if count == 1), bearing_count
            ).to_dict(),
            "share_two_or_more_checks": count_share(
                sum(1 for count in bearing if count >= 2), bearing_count
            ).to_dict(),
            "share_more_than_three_checks": count_share(
                sum(1 for count in bearing if count > 3), bearing_count
            ).to_dict(),
            "concentration": _check_concentration(bearing),
        }
    return {
        "scope": "http_test_sequences",
        "sequence_count": sequence_count,
        "by_target": by_target,
    }


def _blob_vs_spread(api_tests: Sequence[TestRecord], target: str) -> dict[str, Any]:
    # For tests that carry the role at all, whether its checks land on a single
    # dispatch or fan across several. single_check is one check total;
    # concentrated_blob is two or more checks all in one sequence (a blob on one
    # dispatch); spread_across_sequences is two or more checks over two or more
    # sequences (one or a few per dispatch, repeated down the scenario).
    single_check = 0
    concentrated_blob = 0
    spread = 0
    bearing_sequence_counts: list[int] = []
    checks_per_test: list[int] = []
    max_in_one_sequence: list[int] = []
    for test in api_tests:
        counts = _target_sequence_counts(test, target)
        total = sum(counts)
        if total == 0:
            continue
        bearing_sequences = sum(1 for count in counts if count > 0)
        bearing_sequence_counts.append(bearing_sequences)
        checks_per_test.append(total)
        max_in_one_sequence.append(max(counts))
        if total == 1:
            single_check += 1
        elif bearing_sequences == 1:
            concentrated_blob += 1
        else:
            spread += 1

    test_count = len(checks_per_test)
    multi_check_count = concentrated_blob + spread
    return {
        "scope": f"api_tests_with_sequenced_{target}_assertion",
        "test_count": test_count,
        "single_check": count_share(single_check, test_count).to_dict(),
        "concentrated_blob": count_share(concentrated_blob, test_count).to_dict(),
        "spread_across_sequences": count_share(spread, test_count).to_dict(),
        "bearing_sequence_count_per_test": summarize(bearing_sequence_counts).to_dict(),
        "checks_per_test": summarize(checks_per_test).to_dict(),
        "max_checks_in_one_sequence": summarize(max_in_one_sequence).to_dict(),
        # Among tests that assert the role more than once, the blob/spread split
        # with single-check tests removed, so it isolates the real placement choice.
        "multi_check_test_count": multi_check_count,
        "share_blob_given_multi_check": count_share(
            concentrated_blob, multi_check_count
        ).to_dict(),
        "share_spread_given_multi_check": count_share(
            spread, multi_check_count
        ).to_dict(),
    }


def _co_occurrence_sequence(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    # Whether body and header checks land on the same sequence. lift is
    # P(both) / (P(body) * P(header)); lift > 1 means they co-occur on a sequence
    # more than their independent rates would predict. The conditionals expose the
    # asymmetry directly: header is rare, so P(body | header) speaks to whether a
    # header check usually rides along with a body check on the same dispatch.
    both = 0
    body_only = 0
    header_only = 0
    sequence_count = 0
    for test in api_tests:
        for body_count, header_count in zip(
            test.http_sequence_body_check_counts,
            test.http_sequence_header_check_counts,
            strict=True,
        ):
            sequence_count += 1
            has_body = body_count > 0
            has_header = header_count > 0
            if has_body and has_header:
                both += 1
            elif has_body:
                body_only += 1
            elif has_header:
                header_only += 1
    body_sequences = both + body_only
    header_sequences = both + header_only
    lift = (
        both * sequence_count / (body_sequences * header_sequences)
        if body_sequences and header_sequences
        else None
    )
    return {
        "scope": "http_test_sequences",
        "sequence_count": sequence_count,
        "body_and_header": count_share(both, sequence_count).to_dict(),
        "body_only": count_share(body_only, sequence_count).to_dict(),
        "header_only": count_share(header_only, sequence_count).to_dict(),
        "header_given_body": count_share(both, body_sequences).to_dict(),
        "body_given_header": count_share(both, header_sequences).to_dict(),
        "lift": lift,
    }


def _co_occurrence_test(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    # The same co-occurrence at the test level, over each test's full assertion
    # summary (not only sequenced checks), so it composes with the existing
    # response-surface combinations while adding the independence lift.
    test_count = len(api_tests)
    both = 0
    body = 0
    header = 0
    for test in api_tests:
        has_body = test.assertion_body_count > 0
        has_header = test.assertion_header_count > 0
        body += has_body
        header += has_header
        if has_body and has_header:
            both += 1
    lift = both * test_count / (body * header) if body and header else None
    return {
        "scope": "api_tests",
        "test_count": test_count,
        "body_and_header": count_share(both, test_count).to_dict(),
        "tests_with_body": count_share(body, test_count).to_dict(),
        "tests_with_header": count_share(header, test_count).to_dict(),
        "header_given_body": count_share(both, body).to_dict(),
        "body_given_header": count_share(both, header).to_dict(),
        "lift": lift,
    }


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    api_tests = [test for test in tests if test.is_api_test]
    return {
        "scope": "api_tests",
        "api_test_count": len(api_tests),
        "per_sequence_assertion_count": _per_sequence_distribution(api_tests),
        "blob_vs_spread": {
            target: _blob_vs_spread(api_tests, target) for target in _CO_TARGETS
        },
        "co_occurrence": {
            "sequence_level": _co_occurrence_sequence(api_tests),
            "test_level": _co_occurrence_test(api_tests),
        },
    }
