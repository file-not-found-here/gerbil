from __future__ import annotations

import pytest

from gerbil.statistics import production_resource_sequences as stats
from gerbil.statistics.records import GroupedResourceSequenceRecord


def _seq(
    crud: tuple[str, ...],
    verbs: tuple[str, ...],
    *,
    read_after_write: bool = False,
    cleanup_delete: bool = False,
    resolved_to_production: bool = True,
) -> GroupedResourceSequenceRecord:
    return GroupedResourceSequenceRecord(
        crud_combination=crud,
        verb_combination=verbs,
        has_read_after_write=read_after_write,
        has_cleanup_delete=cleanup_delete,
        resolved_to_production=resolved_to_production,
    )


def test_reports_observed_and_production_grouping_blocks() -> None:
    # Observed grouping splits the write and its read-back into two single-verb
    # resources; production grouping folds them into one create-read sequence.
    observed = [
        _seq(("create",), ("POST",)),
        _seq(("read",), ("GET",)),
    ]
    production = [
        _seq(("create", "read"), ("GET", "POST"), read_after_write=True),
        _seq(("read",), ("GET",), resolved_to_production=False),
        # A group with an unmapped method (e.g. OPTIONS) resolves to no combination.
        _seq((), (), resolved_to_production=True),
    ]

    result = stats.compute(observed, production)

    assert result["scope"] == "production_resource_sequences"
    assert result["context_path_prefixes_included_when_present"]

    observed_block = result["observed_grouping"]
    assert observed_block["resolved_sequence_count"] == 2
    assert observed_block["multi_verb_sequence_count"] == 0
    assert observed_block["verify_after_mutate"]["among_all_resolved"]["count"] == 0
    # No multi-verb sequences, so the re-based proportion is undefined.
    among_multi = observed_block["verify_after_mutate"]["among_multi_verb_resolved"]
    assert among_multi["total"] == 0
    assert among_multi["proportion"] is None
    assert "resolved_to_production" not in observed_block

    production_block = result["production_grouping"]
    assert production_block["total_sequence_count"] == 3
    assert production_block["resolved_sequence_count"] == 2
    assert production_block["unresolved_sequence_count"] == 1
    assert production_block["multi_verb_sequence_count"] == 1
    assert production_block["verify_after_mutate"]["among_all_resolved"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    assert production_block["verify_after_mutate"]["among_multi_verb_resolved"] == {
        "count": 1,
        "total": 1,
        "proportion": pytest.approx(1.0),
    }
    # Two of three groups were keyed by a matched production resource key.
    assert production_block["resolved_to_production"]["count"] == 2
    assert production_block["resolved_to_production"]["total"] == 3
    assert production_block["combinations"]["create-read"]["count"] == 1


def test_empty_inputs_yield_none_proportions() -> None:
    result = stats.compute([], [])
    for block_key in ("observed_grouping", "production_grouping"):
        block = result[block_key]
        assert block["total_sequence_count"] == 0
        assert block["verify_after_mutate"]["among_all_resolved"]["proportion"] is None
