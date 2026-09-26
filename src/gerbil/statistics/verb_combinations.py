"""HTTP-verb counterpart of crud_combinations: which set of HTTP verbs each
fully-resolved (test, resource) sequence exercises, counted and shared. The
resolution rule is shared with the CRUD view, so the two differ only in
granularity (PUT/PATCH and GET/HEAD stay distinct instead of folding together)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from gerbil.statistics.crud_combinations import combination_entries
from gerbil.statistics.records import CRUD_VERBS, TestRecord

# Every single HTTP verb, keyed so an unobserved single-verb sequence reports a
# genuine 0. Multi-verb sets span a 2**6 space that is overwhelmingly sparse and
# mostly meaningless (e.g. HEAD-PATCH), so unlike the CRUD module we key those on
# the observed combinations only rather than enumerating the full lattice.
SINGLE_VERB_COMBINATIONS: tuple[tuple[str, ...], ...] = tuple(
    (verb,) for verb in CRUD_VERBS
)

_POST = "POST"
_DELETE = "DELETE"


def _sorted_multi_keys(counts: Counter[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Observed multi-verb combinations, most frequent first, ties broken by the
    canonical verb order so output is deterministic."""
    verb_rank = {verb: index for index, verb in enumerate(CRUD_VERBS)}
    return sorted(
        (combination for combination in counts if len(combination) > 1),
        key=lambda combination: (
            -counts[combination],
            tuple(verb_rank[verb] for verb in combination),
        ),
    )


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    """Verb-combination counts and shares over every resolved (test, resource)
    sequence; see crud_combinations.compute for the shared sequence semantics."""
    api_tests = [test for test in tests if test.is_api_test]
    counts: Counter[tuple[str, ...]] = Counter()
    resolved_total = 0
    unresolved_total = 0
    for test in api_tests:
        for combination in test.resource_verb_combinations:
            counts[combination] += 1
            resolved_total += 1
        unresolved_total += test.unresolved_resource_verb_sequence_count

    multi_keys = _sorted_multi_keys(counts)
    multi_total = sum(counts[combination] for combination in multi_keys)
    create_keys = [combination for combination in multi_keys if _POST in combination]
    create_total = sum(counts[combination] for combination in create_keys)
    create_delete_total = sum(
        counts[combination] for combination in create_keys if _DELETE in combination
    )
    combination_keys = list(SINGLE_VERB_COMBINATIONS) + multi_keys
    return {
        "scope": "resolved_test_resource_sequences",
        "api_test_count": len(api_tests),
        "resolved_sequence_count": resolved_total,
        "unresolved_sequence_count": unresolved_total,
        "total_sequence_count": resolved_total + unresolved_total,
        "single_verb_sequence_count": sum(
            counts[combination] for combination in SINGLE_VERB_COMBINATIONS
        ),
        "combinations": combination_entries(
            counts, combination_keys, resolved_total, value_field="verbs"
        ),
        # The same counts restricted to sequences spanning 2+ verbs, with shares
        # re-based on that subset so single-verb sequences do not dominate.
        "multi_verb_combinations": {
            "scope": "resolved_multi_verb_test_resource_sequences",
            "multi_verb_sequence_count": multi_total,
            "pct_of_resolved_sequences": (
                (100.0 * multi_total / resolved_total) if resolved_total else None
            ),
            "combinations": combination_entries(
                counts, multi_keys, multi_total, value_field="verbs"
            ),
            # Of the multi-verb sequences that POST a resource, how many also DELETE
            # it in the same (test, resource) sequence (the verb-level create->delete
            # pairing rate).
            "post_delete_pairing": {
                "scope": "resolved_multi_verb_sequences_with_post",
                "post_sequence_count": create_total,
                "paired_with_delete_count": create_delete_total,
                "pct_paired_with_delete": (
                    (100.0 * create_delete_total / create_total)
                    if create_total
                    else None
                ),
            },
        },
    }
