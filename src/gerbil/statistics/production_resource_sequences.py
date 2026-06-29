"""SAINT-comparison-only resource-sequence view: verify-after-mutate and CRUD
combinations recomputed with each request grouped by its production resource key
(instance writes fold into their collection reads), alongside the observed-path
baseline so the regrouping's effect is explicit."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from gerbil.statistics.crud_combinations import (
    CRUD_COMBINATIONS,
    combination_entries,
)
from gerbil.statistics.distributions import count_share
from gerbil.statistics.records import (
    SAINT_CONTEXT_PATH_PREFIXES,
    GroupedResourceSequenceRecord,
)


def _grouping_block(
    scope: str,
    sequences: Sequence[GroupedResourceSequenceRecord],
    *,
    include_resolution: bool,
) -> dict[str, Any]:
    resolved = [sequence for sequence in sequences if sequence.crud_combination]
    unresolved_count = len(sequences) - len(resolved)
    multi_verb = [
        sequence for sequence in resolved if len(sequence.verb_combination) > 1
    ]
    verify_after_mutate_count = sum(
        1 for sequence in resolved if sequence.has_read_after_write
    )
    cleanup_delete_count = sum(
        1 for sequence in resolved if sequence.has_cleanup_delete
    )
    counts = {combination: 0 for combination in CRUD_COMBINATIONS}
    for sequence in resolved:
        counts[sequence.crud_combination] += 1

    block: dict[str, Any] = {
        "scope": scope,
        "resolved_sequence_count": len(resolved),
        "unresolved_sequence_count": unresolved_count,
        "total_sequence_count": len(sequences),
        "multi_verb_sequence_count": len(multi_verb),
        # Verify-after-mutate: a write (create/update/delete) followed later by a
        # read on the same grouped resource. Shown over every resolved sequence and
        # re-based on the multi-verb subset, since a single-verb sequence can never
        # exhibit it.
        "verify_after_mutate": {
            "among_all_resolved": count_share(
                verify_after_mutate_count, len(resolved)
            ).to_dict(),
            "among_multi_verb_resolved": count_share(
                verify_after_mutate_count, len(multi_verb)
            ).to_dict(),
        },
        "cleanup_delete": {
            "among_all_resolved": count_share(
                cleanup_delete_count, len(resolved)
            ).to_dict(),
            "among_multi_verb_resolved": count_share(
                cleanup_delete_count, len(multi_verb)
            ).to_dict(),
        },
        "combinations": combination_entries(counts, CRUD_COMBINATIONS, len(resolved)),
    }
    if include_resolution:
        # Groups keyed by a matched production resource key vs. those that fell
        # back to their observed path (request matched no endpoint or split across
        # resource keys, so it was never merged on a guess).
        resolved_to_production_count = sum(
            1 for sequence in sequences if sequence.resolved_to_production
        )
        block["resolved_to_production"] = count_share(
            resolved_to_production_count, len(sequences)
        ).to_dict()
    return block


def compute(
    observed_sequences: Sequence[GroupedResourceSequenceRecord],
    production_sequences: Sequence[GroupedResourceSequenceRecord],
) -> dict[str, Any]:
    """SAINT comparison only. Verify-after-mutate and CRUD combinations over
    resource sequences grouped two ways: ``observed_grouping`` keys each sequence
    by the observed request path (the resource_interaction baseline), while
    ``production_grouping`` resolves every request to its production endpoint and
    keys by that resource key. The observed grouping splits an instance write
    (``POST /products/A/features/F``) from its collection read-back
    (``GET /products/A/features``) into separate resources, hiding the round-trip;
    production grouping folds them together so the verify-after-mutate rate
    reflects SAINT's cross-endpoint verification. Context-path prefixes are
    included in the match for SAINT projects."""
    return {
        "scope": "production_resource_sequences",
        "purpose": (
            "SAINT comparison only: resource sequences regrouped by production "
            "resource key so an instance write and its collection read-back count "
            "as one resource, crediting cross-endpoint verify-after-mutate the "
            "observed-path grouping splits apart."
        ),
        "context_path_prefixes_included_when_present": list(
            SAINT_CONTEXT_PATH_PREFIXES
        ),
        "observed_grouping": _grouping_block(
            "observed_path_grouped_test_resource_sequences",
            observed_sequences,
            include_resolution=False,
        ),
        "production_grouping": _grouping_block(
            "production_resource_key_grouped_test_resource_sequences",
            production_sequences,
            include_resolution=True,
        ),
    }
