"""Distribution of CRUD-operation combinations over fully CRUD-resolved
(test, resource) sequences: which subset of create/read/update/delete each
sequence exercises, counted and shared across the corpus."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations
from typing import Any

from gerbil.statistics.records import CRUD_OPERATIONS, TestRecord

# Every non-empty subset of the four CRUD operations, ordered by size then
# canonical create/read/update/delete order. Keying the distribution on all of
# them makes an unobserved combination report a genuine 0 rather than vanish.
CRUD_COMBINATIONS: tuple[tuple[str, ...], ...] = tuple(
    combination
    for size in range(1, len(CRUD_OPERATIONS) + 1)
    for combination in combinations(CRUD_OPERATIONS, size)
)

# The subset that exercises more than one distinct operation (create-read and up).
# Backs the multi-operation view, whose shares re-base on this population only.
MULTI_OPERATION_COMBINATIONS: tuple[tuple[str, ...], ...] = tuple(
    combination for combination in CRUD_COMBINATIONS if len(combination) >= 2
)

_CREATE = "create"
_DELETE = "delete"

# Multi-operation combinations that involve a create; backs the create->delete
# pairing rate (how often a create is accompanied by a delete in the same sequence).
MULTI_OPERATION_CREATE_COMBINATIONS: tuple[tuple[str, ...], ...] = tuple(
    combination
    for combination in MULTI_OPERATION_COMBINATIONS
    if _CREATE in combination
)


def combination_label(operations: tuple[str, ...]) -> str:
    """Hyphenated combination name; a lone operation is suffixed '-only'
    (('create',) -> 'create-only', ('create', 'read') -> 'create-read')."""
    if len(operations) == 1:
        return f"{operations[0]}-only"
    return "-".join(operations)


def combination_entries(
    counts: dict[tuple[str, ...], int],
    keys: Sequence[tuple[str, ...]],
    denominator: int,
    *,
    value_field: str = "operations",
) -> dict[str, Any]:
    """Count + share for each combination over a denominator (None share when 0).
    value_field names the member list ("operations" for CRUD, "verbs" for verbs)."""
    return {
        combination_label(combination): {
            value_field: list(combination),
            "count": counts[combination],
            "pct": (
                (100.0 * counts[combination] / denominator) if denominator else None
            ),
        }
        for combination in keys
    }


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    """Combination counts and shares over every resolved (test, resource) sequence.

    The unit is one resource-interaction sequence (a (test, resource) pair). A
    sequence is included only when its resource resolved (always true once
    grouped) and every request mapped to a CRUD operation; sequences with an
    unmapped method are excluded and tallied separately so the denominator is the
    fully characterized population.
    """
    api_tests = [test for test in tests if test.is_api_test]
    counts = {combination: 0 for combination in CRUD_COMBINATIONS}
    resolved_total = 0
    unresolved_total = 0
    for test in api_tests:
        for combination in test.resource_crud_combinations:
            counts[combination] += 1
            resolved_total += 1
        unresolved_total += test.unresolved_resource_crud_sequence_count
    multi_operation_total = sum(
        counts[combination] for combination in MULTI_OPERATION_COMBINATIONS
    )
    create_multi_operation_total = sum(
        counts[combination] for combination in MULTI_OPERATION_CREATE_COMBINATIONS
    )
    create_delete_multi_operation_total = sum(
        counts[combination]
        for combination in MULTI_OPERATION_CREATE_COMBINATIONS
        if _DELETE in combination
    )
    return {
        "scope": "resolved_test_resource_sequences",
        "api_test_count": len(api_tests),
        "resolved_sequence_count": resolved_total,
        "unresolved_sequence_count": unresolved_total,
        "total_sequence_count": resolved_total + unresolved_total,
        "combinations": combination_entries(counts, CRUD_COMBINATIONS, resolved_total),
        # The same counts restricted to sequences that span 2+ operations, with
        # shares re-based on that subset so single-verb sequences do not dominate.
        "multi_operation_combinations": {
            "scope": "resolved_multi_operation_test_resource_sequences",
            "multi_operation_sequence_count": multi_operation_total,
            "pct_of_resolved_sequences": (
                (100.0 * multi_operation_total / resolved_total)
                if resolved_total
                else None
            ),
            "combinations": combination_entries(
                counts, MULTI_OPERATION_COMBINATIONS, multi_operation_total
            ),
            # Of the multi-operation sequences that create a resource, how many also
            # delete it in the same (test, resource) sequence.
            "create_delete_pairing": {
                "scope": "resolved_multi_operation_sequences_with_create",
                "create_sequence_count": create_multi_operation_total,
                "paired_with_delete_count": create_delete_multi_operation_total,
                "pct_paired_with_delete": (
                    (
                        100.0
                        * create_delete_multi_operation_total
                        / create_multi_operation_total
                    )
                    if create_multi_operation_total
                    else None
                ),
            },
        },
    }
