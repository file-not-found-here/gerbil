"""HTTP test-sequence distributions over API tests."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from gerbil.statistics.distributions import count_share, share, summarize
from gerbil.statistics.records import (
    CRUD_OPERATIONS,
    CRUD_VERBS,
    WRITE_OPERATIONS,
    TestRecord,
    request_event_total,
)

_READ_OPERATION = "read"
_CREATE_OPERATION = "create"
_DELETE_OPERATION = "delete"
_UNCATEGORIZED = "uncategorized"


_SEQUENCE_SHAPE_LABELS: tuple[str, ...] = (
    "build-dispatch-verification",
    "build-dispatch-no-verification",
    "dispatch-only",
    "dispatch-verification-no-build",
)


def _sequence_shape_label(
    request_build_count: int, http_request_count: int, response_check_count: int
) -> str | None:
    has_build = request_build_count > 0
    has_dispatch = http_request_count > 0
    has_verification = response_check_count > 0

    if has_build and has_dispatch and has_verification:
        return "build-dispatch-verification"
    if has_build and has_dispatch and not has_verification:
        return "build-dispatch-no-verification"
    if not has_build and has_dispatch and not has_verification:
        return "dispatch-only"
    if not has_build and has_dispatch and has_verification:
        return "dispatch-verification-no-build"
    return None


def _sequence_shape_distribution(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    sequence_count = 0

    for test in api_tests:
        for build_count, dispatch_count, check_count in zip(
            test.http_sequence_request_build_counts,
            test.http_sequence_http_request_counts,
            test.http_sequence_response_check_lengths,
            strict=True,
        ):
            sequence_count += 1
            label = _sequence_shape_label(build_count, dispatch_count, check_count)
            if label is not None:
                counts[label] += 1

    classified_sequence_count = sum(counts.values())
    return {
        "scope": "http_test_sequences",
        "sequence_count": sequence_count,
        "classified_sequence_count": classified_sequence_count,
        "excluded_sequence_count": sequence_count - classified_sequence_count,
        "labels": {
            label: {
                "count": counts[label],
                "pct_of_classified_sequences": (
                    100.0 * counts[label] / classified_sequence_count
                    if classified_sequence_count
                    else None
                ),
            }
            for label in _SEQUENCE_SHAPE_LABELS
        },
    }


def _dispatch_event_fan_distribution(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    request_builder_counts = [
        count
        for test in api_tests
        for count in test.dispatch_event_request_builder_counts
    ]
    response_check_counts = [
        count
        for test in api_tests
        for count in test.dispatch_event_response_check_counts
    ]
    return {
        "scope": "sequenced_request_dispatch_events",
        "dispatch_event_count": len(request_builder_counts),
        "request_builder_fan_in": summarize(request_builder_counts).to_dict(),
        "verification_fan_out": summarize(response_check_counts).to_dict(),
    }


# Top-ranked fractions reported by the verification-concentration Lorenz cut.
_CONCENTRATION_TOP_PERCENTS: tuple[int, ...] = (1, 5, 10, 25)


def _concentration(values: Sequence[int]) -> dict[str, Any]:
    """Share of the summed quantity held by the highest-ranked top-k% of items,
    for each k in _CONCENTRATION_TOP_PERCENTS (a discrete Lorenz cut)."""
    ranked = sorted(values, reverse=True)
    item_count = len(ranked)
    total = sum(ranked)
    by_top_fraction: dict[str, Any] = {}
    for percent in _CONCENTRATION_TOP_PERCENTS:
        # At least one item once the sample is non-empty, so top-1% is never zero.
        top_count = max(1, math.ceil(item_count * percent / 100)) if item_count else 0
        top_values = ranked[:top_count]
        by_top_fraction[f"top_{percent}pct"] = {
            "item_count": top_count,
            "share_of_total": (sum(top_values) / total) if total else None,
            # Spread of the per-item quantity within this top-ranked cut itself.
            "distribution": summarize(top_values).to_dict(),
        }
    return {
        "item_count": item_count,
        "total": total,
        "by_top_fraction": by_top_fraction,
    }


def _verification_concentration(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    # How unevenly response checks are spread across sequences: a thin tail of
    # snapshot-style sequences holds a large share of all verification, while
    # most sequences carry one check or none.
    check_counts = [
        length
        for test in api_tests
        for length in test.http_sequence_response_check_lengths
    ]
    return {
        "scope": "http_test_sequences",
        **_concentration(check_counts),
        "share_zero_checks": share(count == 0 for count in check_counts).to_dict(),
        "share_at_most_one_check": share(
            count <= 1 for count in check_counts
        ).to_dict(),
        "share_more_than_five_checks": share(
            count > 5 for count in check_counts
        ).to_dict(),
        "share_more_than_ten_checks": share(
            count > 10 for count in check_counts
        ).to_dict(),
    }


def _bucket_for_sequence(crud_operations: tuple[str, ...]) -> str:
    # Real sequences dispatch exactly one request, so this is a single operation;
    # the residual catches unmapped verbs (and, defensively, any multi-operation
    # sequence) so no no-verification sequence is dropped from the total.
    return crud_operations[0] if len(crud_operations) == 1 else _UNCATEGORIZED


def _unverified_sequence_crud(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    # Sequences carrying no response check ("no verification"), bucketed by the
    # CRUD operation of their dispatched request. Two within-test follow signals:
    #   followed_by_later_sequence — another sequence exists after this one, so the
    #     unverified dispatch is a non-terminal step of a stateful chain (setup or
    #     intermediate) rather than the test's final action;
    #   followed_by_read — a *later* sequence dispatches a read; for a write this
    #     reads back as out-of-band confirmation, but for a read-typed sequence it
    #     is just a subsequent read, not verification.
    # Both are reported per bucket since their meaning depends on the operation.
    sequence_count = 0
    no_verification_count = 0
    bucket_counts: Counter[str] = Counter()
    bucket_followed_read: Counter[str] = Counter()
    bucket_followed_sequence: Counter[str] = Counter()
    for test in api_tests:
        later_has_read = False
        has_later_sequence = False
        for check_count, crud_operations in zip(
            reversed(test.http_sequence_response_check_lengths),
            reversed(test.http_sequence_crud_operations),
            strict=True,
        ):
            sequence_count += 1
            if check_count == 0:
                no_verification_count += 1
                bucket = _bucket_for_sequence(crud_operations)
                bucket_counts[bucket] += 1
                if later_has_read:
                    bucket_followed_read[bucket] += 1
                if has_later_sequence:
                    bucket_followed_sequence[bucket] += 1
            if _READ_OPERATION in crud_operations:
                later_has_read = True
            has_later_sequence = True

    crud_mapped_count = no_verification_count - bucket_counts[_UNCATEGORIZED]
    return {
        "scope": "http_test_sequences",
        "sequence_count": sequence_count,
        "no_verification_sequence_count": no_verification_count,
        "pct_of_sequences": (
            100.0 * no_verification_count / sequence_count if sequence_count else None
        ),
        # Denominator for the operations mix: no-verification sequences whose
        # request resolved to a CRUD-mapped HTTP method (uncategorized excluded).
        "crud_mapped_no_verification_sequence_count": crud_mapped_count,
        "operations": {
            operation: {
                "count": bucket_counts[operation],
                "pct_of_crud_mapped_no_verification_sequences": (
                    100.0 * bucket_counts[operation] / crud_mapped_count
                    if crud_mapped_count
                    else None
                ),
                "followed_by_later_sequence": count_share(
                    bucket_followed_sequence[operation], bucket_counts[operation]
                ).to_dict(),
                "followed_by_read": count_share(
                    bucket_followed_read[operation], bucket_counts[operation]
                ).to_dict(),
            }
            for operation in CRUD_OPERATIONS
        },
        "uncategorized": {
            "count": bucket_counts[_UNCATEGORIZED],
            "pct_of_no_verification_sequences": (
                100.0 * bucket_counts[_UNCATEGORIZED] / no_verification_count
                if no_verification_count
                else None
            ),
            "followed_by_later_sequence": count_share(
                bucket_followed_sequence[_UNCATEGORIZED], bucket_counts[_UNCATEGORIZED]
            ).to_dict(),
            "followed_by_read": count_share(
                bucket_followed_read[_UNCATEGORIZED], bucket_counts[_UNCATEGORIZED]
            ).to_dict(),
        },
        "followed_by_later_sequence": count_share(
            sum(bucket_followed_sequence.values()), no_verification_count
        ).to_dict(),
        "followed_by_read": count_share(
            sum(bucket_followed_read.values()), no_verification_count
        ).to_dict(),
    }


def _multi_sequence_cohort(tests: Sequence[TestRecord]) -> dict[str, Any]:
    return {
        "test_count": len(tests),
        "sequence_count": summarize(
            test.http_sequence_count for test in tests
        ).to_dict(),
        "distinct_endpoint_count": summarize(
            test.distinct_endpoint_count for test in tests
        ).to_dict(),
        "share_multiple_endpoints": share(
            test.distinct_endpoint_count >= 2 for test in tests
        ).to_dict(),
        "re_dispatches_endpoint": share(
            test.re_dispatches_endpoint for test in tests
        ).to_dict(),
        "has_duplicate_sequence": share(
            test.has_repeated_http_sequence for test in tests
        ).to_dict(),
    }


def _multi_sequence_cohort_with_top_decile(
    tests: Sequence[TestRecord],
) -> dict[str, Any]:
    ranked = sorted(tests, key=lambda test: test.http_sequence_count, reverse=True)
    top_decile_count = math.ceil(len(tests) * 0.1) if tests else 0
    return {
        **_multi_sequence_cohort(tests),
        "top_decile": {
            "scope": "largest_10pct_by_sequence_count",
            **_multi_sequence_cohort(ranked[:top_decile_count]),
        },
    }


def _multi_sequence_composition(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    # How multi-request scenarios are built. The size tail is over all API tests;
    # the endpoint-breadth and repetition signals are reported over two cohorts of
    # multi-sequence tests, clearly separated:
    #   * fully_resolved_multi_sequence - tests in which *every* dispatch resolves
    #     to a method+path endpoint. This is the duplication-honest default: an
    #     unresolved dispatch fingerprints to "*", so a test with any unresolved
    #     dispatch can read as a self-duplicate it is not.
    #   * all_multi_sequence - every multi-sequence test regardless of resolution,
    #     so scenarios with dynamically built (statically unresolved) request URLs
    #     are included (e.g. the developer genome-nexus integration tests). Here
    #     distinct_endpoint_count/share_multiple_endpoints/re_dispatches_endpoint
    #     stay resolved-only and honest, but has_duplicate_sequence can over-count
    #     because unresolved steps collapse to the "*" fingerprint.
    # Each cohort is reported again over its largest-10%-by-size tail.
    multi_tests = [test for test in api_tests if test.has_multiple_http_sequences]
    fully_resolved = [test for test in multi_tests if test.all_dispatch_events_resolved]
    return {
        "scope": "api_tests",
        "api_test_count": len(api_tests),
        "multi_sequence_test_count": len(multi_tests),
        "fully_resolved_multi_sequence_test_count": len(fully_resolved),
        "share_two_or_more_sequences": share(
            test.http_sequence_count >= 2 for test in api_tests
        ).to_dict(),
        "share_five_or_more_sequences": share(
            test.http_sequence_count >= 5 for test in api_tests
        ).to_dict(),
        "share_ten_or_more_sequences": share(
            test.http_sequence_count >= 10 for test in api_tests
        ).to_dict(),
        "fully_resolved_multi_sequence": {
            "scope": ("api_tests_with_multiple_sequences_and_all_dispatches_resolved"),
            **_multi_sequence_cohort_with_top_decile(fully_resolved),
        },
        "all_multi_sequence": {
            "scope": "api_tests_with_multiple_sequences_any_resolution",
            "note": (
                "Includes multi-sequence tests with statically unresolved dispatches "
                "(e.g. dynamically built URLs); has_duplicate_sequence may over-count "
                "via the '*' fingerprint, while re_dispatches_endpoint and "
                "distinct_endpoint_count remain resolved-only."
            ),
            **_multi_sequence_cohort_with_top_decile(multi_tests),
        },
    }


def _scenario_verification_placement(
    api_tests: Sequence[TestRecord],
) -> dict[str, Any]:
    # Where verification falls across a multi-sequence scenario. A sequence's
    # position carries meaning: interior sequences tend to drive or set up state
    # while the terminal sequence delivers the action under test, so checks
    # concentrate at the end. terminal_sequence_verified is the share of scenarios
    # whose last sequence carries a check; interior_sequence_verified is the
    # check-rate of every non-terminal sequence pooled, exposing the back-loading.
    multi_tests = [test for test in api_tests if test.has_multiple_http_sequences]
    terminal_verified = 0
    interior_verified = 0
    interior_total = 0
    no_verification = 0
    terminal_only = 0
    any_interior_verified = 0
    fully_verified = 0
    verified_fractions: list[float] = []
    for test in multi_tests:
        checks = test.http_sequence_response_check_lengths
        if not checks:
            continue
        interior = checks[:-1]
        interior_hits = sum(1 for count in interior if count > 0)
        interior_verified += interior_hits
        interior_total += len(interior)
        if checks[-1] > 0:
            terminal_verified += 1
        if sum(checks) == 0:
            no_verification += 1
        if checks[-1] > 0 and interior_hits == 0:
            terminal_only += 1
        if interior_hits > 0:
            any_interior_verified += 1
        if all(count > 0 for count in checks):
            fully_verified += 1
        verified_fractions.append(sum(1 for count in checks if count > 0) / len(checks))
    test_count = len(multi_tests)
    return {
        "scope": "api_tests_with_multiple_sequences",
        "multi_sequence_test_count": test_count,
        "terminal_sequence_verified": count_share(
            terminal_verified, test_count
        ).to_dict(),
        "interior_sequence_verified": count_share(
            interior_verified, interior_total
        ).to_dict(),
        "shapes": {
            "no_verification": count_share(no_verification, test_count).to_dict(),
            "terminal_only": count_share(terminal_only, test_count).to_dict(),
            "any_interior_verified": count_share(
                any_interior_verified, test_count
            ).to_dict(),
            "fully_verified": count_share(fully_verified, test_count).to_dict(),
        },
        # Fraction of a scenario's own sequences carrying a check; a low mean over
        # longer scenarios means verification does not scale with scenario size.
        "verified_sequence_fraction": summarize(verified_fractions).to_dict(),
    }


def _single_grouped_operation(operations: tuple[str, ...]) -> str | None:
    # A real sequence dispatches one request, so a resolved sequence carries
    # exactly one operation (CRUD class or verb); anything else (unmapped method,
    # or defensively a multi-operation sequence) is not a clean transition endpoint.
    return operations[0] if len(operations) == 1 else None


def _operation_self_affinity(
    transition_counts: Counter[tuple[str, str]],
    crud_mapped_pairs: int,
    same_operation_repeat: int,
) -> dict[str, Any]:
    # Whether a sequence's operation predicts its successor's, separating real
    # clustering (bulk runs of one verb) from read-heavy base rate. The
    # independence baseline for "next == op" is op's target marginal, so lift =
    # observed self-repeat / that marginal; lift > 1 means the operation recurs
    # more than its prevalence alone would produce. Lifts are None when a marginal
    # is empty (the ratio is undefined, not zero).
    source_totals: Counter[str] = Counter()
    target_totals: Counter[str] = Counter()
    for (source, target), count in transition_counts.items():
        source_totals[source] += count
        target_totals[target] += count

    def _lift(numerator: int, source_total: int, target_total: int) -> float | None:
        denominator = source_total * target_total
        return (numerator * crud_mapped_pairs / denominator) if denominator else None

    expected_self_repeat = (
        sum(source_totals[op] * target_totals[op] for op in CRUD_OPERATIONS)
        / crud_mapped_pairs**2
        if crud_mapped_pairs
        else None
    )
    observed_self_repeat = (
        (same_operation_repeat / crud_mapped_pairs) if crud_mapped_pairs else None
    )
    return {
        "scope": "crud_mapped_consecutive_sequence_pairs",
        "by_operation": {
            op: {
                "source_share": count_share(
                    source_totals[op], crud_mapped_pairs
                ).to_dict(),
                "target_share": count_share(
                    target_totals[op], crud_mapped_pairs
                ).to_dict(),
                "self_repeat_given_source": count_share(
                    transition_counts[(op, op)], source_totals[op]
                ).to_dict(),
                "expected_if_independent": (
                    (target_totals[op] / crud_mapped_pairs)
                    if crud_mapped_pairs
                    else None
                ),
                "lift": _lift(
                    transition_counts[(op, op)], source_totals[op], target_totals[op]
                ),
            }
            for op in CRUD_OPERATIONS
        },
        "aggregate": {
            "observed_self_repeat": observed_self_repeat,
            "expected_self_repeat_if_independent": expected_self_repeat,
            "lift": (
                (observed_self_repeat / expected_self_repeat)
                if observed_self_repeat is not None and expected_self_repeat
                else None
            ),
        },
    }


def _count_consecutive_transitions(
    api_tests: Sequence[TestRecord],
    getter: Callable[[TestRecord], tuple[tuple[str, ...], ...]],
) -> tuple[int, int, Counter[tuple[str, str]]]:
    """Count (sequence-i -> sequence-i+1) value transitions within each test.
    getter yields a test's per-sequence value tuples (CRUD ops or verbs); a
    sequence is a clean transition endpoint only when it carries exactly one
    value. Returns (pair_count, mapped_pair_count, transition_counts)."""
    pair_count = 0
    mapped_pairs = 0
    transition_counts: Counter[tuple[str, str]] = Counter()
    for test in api_tests:
        sequences = getter(test)
        for previous, following in zip(sequences, sequences[1:], strict=False):
            pair_count += 1
            previous_value = _single_grouped_operation(previous)
            following_value = _single_grouped_operation(following)
            if previous_value is None or following_value is None:
                continue
            mapped_pairs += 1
            transition_counts[(previous_value, following_value)] += 1
    return pair_count, mapped_pairs, transition_counts


def _sequence_operation_transitions(
    api_tests: Sequence[TestRecord],
) -> dict[str, Any]:
    # CRUD operation of each sequence against the next within the same test. The
    # adjacency captures scenario structure a per-sequence view cannot: read->read
    # is polling or paginated re-querying; a write followed by a read is
    # out-of-band confirmation; create->delete is in-test setup then teardown.
    pair_count, crud_mapped_pairs, transition_counts = _count_consecutive_transitions(
        api_tests, lambda test: test.http_sequence_crud_operations
    )

    same_operation_repeat = sum(
        count
        for (source, target), count in transition_counts.items()
        if source == target
    )
    write_then_read = sum(
        count
        for (source, target), count in transition_counts.items()
        if source in WRITE_OPERATIONS and target == _READ_OPERATION
    )
    return {
        "scope": "consecutive_sequence_pairs",
        "consecutive_pair_count": pair_count,
        "crud_mapped_pair_count": crud_mapped_pairs,
        "transitions": {
            f"{source}->{target}": count_share(
                transition_counts[(source, target)], crud_mapped_pairs
            ).to_dict()
            for source in CRUD_OPERATIONS
            for target in CRUD_OPERATIONS
        },
        "operation_self_affinity": _operation_self_affinity(
            transition_counts, crud_mapped_pairs, same_operation_repeat
        ),
        "same_operation_repeat": count_share(
            same_operation_repeat, crud_mapped_pairs
        ).to_dict(),
        "read_to_read": count_share(
            transition_counts[(_READ_OPERATION, _READ_OPERATION)], crud_mapped_pairs
        ).to_dict(),
        "write_then_read": count_share(write_then_read, crud_mapped_pairs).to_dict(),
        "create_then_delete": count_share(
            transition_counts[(_CREATE_OPERATION, _DELETE_OPERATION)], crud_mapped_pairs
        ).to_dict(),
    }


def _sequence_verb_transitions(
    api_tests: Sequence[TestRecord],
) -> dict[str, Any]:
    # HTTP verb of each sequence against the next within the same test, the
    # verb-granularity counterpart of sequence_operation_transitions. PUT and
    # PATCH (and GET and HEAD) are no longer merged, so a PUT followed by a PATCH
    # counts as a verb change rather than a repeated update. The consecutive-pair
    # population is identical to the CRUD view; only the mapping differs. Per-verb
    # self-repeat (GET->GET, ...) is the diagonal of "transitions", so it is not
    # restated; "same_verb_repeat" is the aggregate across that diagonal.
    pair_count, verb_mapped_pairs, transition_counts = _count_consecutive_transitions(
        api_tests, lambda test: test.http_sequence_verb_operations
    )
    same_verb_repeat = sum(
        count
        for (source, target), count in transition_counts.items()
        if source == target
    )
    return {
        "scope": "consecutive_sequence_pairs",
        "consecutive_pair_count": pair_count,
        "verb_mapped_pair_count": verb_mapped_pairs,
        "transitions": {
            f"{source}->{target}": count_share(
                transition_counts[(source, target)], verb_mapped_pairs
            ).to_dict()
            for source in CRUD_VERBS
            for target in CRUD_VERBS
        },
        "same_verb_repeat": count_share(same_verb_repeat, verb_mapped_pairs).to_dict(),
    }


def _method_diversity(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    # Whether a test exercises more than one HTTP verb, contrasting all API tests
    # with the multi-request subset (a single dispatch is single-verb by
    # construction). The multi-request denominator matches response_extraction's:
    # tests with at least two dispatched request events across all origins.
    multi_request_tests = [test for test in api_tests if request_event_total(test) >= 2]
    return {
        "distinct_method_count_per_test": summarize(
            test.distinct_http_method_count for test in api_tests
        ).to_dict(),
        "tests_with_multiple_methods": share(
            test.distinct_http_method_count >= 2 for test in api_tests
        ).to_dict(),
        "multi_request_tests": {
            "scope": "api_tests_with_multiple_request_events",
            "test_count": len(multi_request_tests),
            "distinct_method_count_per_test": summarize(
                test.distinct_http_method_count for test in multi_request_tests
            ).to_dict(),
            "tests_with_multiple_methods": share(
                test.distinct_http_method_count >= 2 for test in multi_request_tests
            ).to_dict(),
        },
    }


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    api_tests = [test for test in tests if test.is_api_test]

    return {
        "scope": "api_tests",
        "api_test_count": len(api_tests),
        "sequence_count_per_test": summarize(
            test.http_sequence_count for test in api_tests
        ).to_dict(),
        "sequence_length": summarize(
            length for test in api_tests for length in test.http_sequence_lengths
        ).to_dict(),
        "http_assertion_count_per_test": summarize(
            sum(test.verification_counts) for test in api_tests
        ).to_dict(),
        "sequenced_response_check_count_per_test": summarize(
            test.http_sequence_response_check_count for test in api_tests
        ).to_dict(),
        "request_side_sequence_length": summarize(
            length
            for test in api_tests
            for length in test.http_sequence_request_side_lengths
        ).to_dict(),
        "response_side_sequence_length": summarize(
            length
            for test in api_tests
            for length in test.http_sequence_response_check_lengths
        ).to_dict(),
        "verification_concentration": _verification_concentration(api_tests),
        "unverified_sequence_crud": _unverified_sequence_crud(api_tests),
        "request_dispatch_event_fan": _dispatch_event_fan_distribution(api_tests),
        "sequence_shape_distribution": _sequence_shape_distribution(api_tests),
        "multi_sequence_composition": _multi_sequence_composition(api_tests),
        "scenario_verification_placement": _scenario_verification_placement(api_tests),
        "sequence_operation_transitions": _sequence_operation_transitions(api_tests),
        "sequence_verb_transitions": _sequence_verb_transitions(api_tests),
        "tests_with_multiple_sequences": share(
            test.has_multiple_http_sequences for test in api_tests
        ).to_dict(),
        "tests_with_repeated_sequence": share(
            test.has_repeated_http_sequence for test in api_tests
        ).to_dict(),
        "tests_with_shared_sequence": share(
            test.has_shared_http_sequence for test in api_tests
        ).to_dict(),
        "distinct_endpoint_count_per_test": summarize(
            test.distinct_endpoint_count for test in api_tests
        ).to_dict(),
        "http_method_diversity": _method_diversity(api_tests),
    }
