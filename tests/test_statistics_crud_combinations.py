from __future__ import annotations

import pytest

from gerbil.statistics import crud_combinations as crud_combinations_stats
from gerbil.statistics.records import CRUD_OPERATIONS, HTTP_METHODS, TestRecord


def make_test(
    *,
    is_api_test: bool = True,
    resource_crud_combinations: tuple[tuple[str, ...], ...] = (),
    unresolved_resource_crud_sequence_count: int = 0,
) -> TestRecord:
    return TestRecord(
        is_api_test=is_api_test,
        is_controller_unit_test=False,
        expanded_ncloc=0,
        expanded_cyclomatic_complexity=0,
        expanded_helper_method_count=0,
        test_helper_method_count=0,
        expanded_objects_created=0,
        expanded_assertion_count=0,
        mocked_interaction_count=0,
        dependency_strategy_label_count=0,
        dispatch_labels=(),
        has_read_after_write=False,
        has_cleanup_delete=False,
        resource_lifecycle_labels=(),
        setup_fixture_count=0,
        teardown_fixture_count=0,
        status_range_counts=(0, 0, 0, 0, 0, 0),
        builder_counts=(0, 0, 0),
        event_counts=(0, 0, 0),
        verification_counts=(0, 0, 0),
        fixture_builder_phase_counts=(0, 0),
        fixture_event_phase_counts=(0, 0),
        fixture_verification_phase_counts=(0, 0),
        http_method_counts=(0,) * len(HTTP_METHODS),
        crud_operation_counts=(0,) * len(CRUD_OPERATIONS),
        resource_crud_combinations=resource_crud_combinations,
        unresolved_resource_crud_sequence_count=unresolved_resource_crud_sequence_count,
    )


# --- Combination enumeration and labels ---


def test_combinations_enumerate_all_15_nonempty_subsets_in_canonical_order() -> None:
    combinations = crud_combinations_stats.CRUD_COMBINATIONS

    assert len(combinations) == 15
    # Singletons come first, in canonical create/read/update/delete order.
    assert combinations[:4] == (("create",), ("read",), ("update",), ("delete",))
    assert combinations[-1] == ("create", "read", "update", "delete")
    # Sizes are non-decreasing, so smaller combinations sort before larger ones.
    sizes = [len(combination) for combination in combinations]
    assert sizes == sorted(sizes)
    # Every entry is itself in canonical order.
    for combination in combinations:
        ordered = tuple(op for op in CRUD_OPERATIONS if op in set(combination))
        assert combination == ordered


def test_combination_label_uses_only_suffix_for_singletons() -> None:
    assert crud_combinations_stats.combination_label(("create",)) == "create-only"
    assert crud_combinations_stats.combination_label(("read",)) == "read-only"
    assert (
        crud_combinations_stats.combination_label(("create", "read")) == "create-read"
    )
    assert (
        crud_combinations_stats.combination_label(
            ("create", "read", "update", "delete")
        )
        == "create-read-update-delete"
    )


# --- Distribution over resolved sequences ---


def test_compute_counts_combinations_over_resolved_sequences() -> None:
    tests = [
        make_test(resource_crud_combinations=(("create", "read"), ("read",))),
        make_test(resource_crud_combinations=(("create", "read"),)),
        # Non-API test: its sequences never enter the population.
        make_test(is_api_test=False, resource_crud_combinations=(("delete",),)),
    ]

    result = crud_combinations_stats.compute(tests)

    assert result["scope"] == "resolved_test_resource_sequences"
    assert result["api_test_count"] == 2
    assert result["resolved_sequence_count"] == 3
    assert result["unresolved_sequence_count"] == 0
    assert result["total_sequence_count"] == 3

    combinations = result["combinations"]
    assert combinations["create-read"] == {
        "operations": ["create", "read"],
        "count": 2,
        "pct": pytest.approx(100.0 * 2 / 3),
    }
    assert combinations["read-only"] == {
        "operations": ["read"],
        "count": 1,
        "pct": pytest.approx(100.0 / 3),
    }
    # The non-API test's delete sequence is excluded -> genuine 0.
    assert combinations["delete-only"] == {
        "operations": ["delete"],
        "count": 0,
        "pct": pytest.approx(0.0),
    }
    # All 15 combinations are keyed even when unobserved.
    assert len(combinations) == 15


def test_multi_operation_combinations_rebase_share_on_multi_op_subset() -> None:
    tests = [
        # Three single-verb sequences and three multi-op ones.
        make_test(
            resource_crud_combinations=(
                ("read",),
                ("create",),
                ("create",),
                ("create", "read"),
                ("create", "read"),
                ("read", "delete"),
            )
        ),
    ]

    multi = crud_combinations_stats.compute(tests)["multi_operation_combinations"]

    assert multi["scope"] == "resolved_multi_operation_test_resource_sequences"
    # Only the 2+-operation sequences enter this counter (3 of the 6 resolved).
    assert multi["multi_operation_sequence_count"] == 3
    assert multi["pct_of_resolved_sequences"] == pytest.approx(100.0 * 3 / 6)

    combinations = multi["combinations"]
    # The view starts at create-read; single-verb combinations are absent.
    assert list(combinations)[0] == "create-read"
    assert "create-only" not in combinations
    assert "read-only" not in combinations
    assert len(combinations) == 11
    # Shares are over the multi-op subset (3), not the resolved total (6).
    assert combinations["create-read"] == {
        "operations": ["create", "read"],
        "count": 2,
        "pct": pytest.approx(100.0 * 2 / 3),
    }
    assert combinations["read-delete"]["pct"] == pytest.approx(100.0 / 3)
    # Multi-op shares sum to 100 over the subset.
    assert sum(entry["pct"] for entry in combinations.values()) == pytest.approx(100.0)


def test_create_delete_pairing_within_multi_operation_sequences() -> None:
    tests = [
        make_test(
            resource_crud_combinations=(
                ("create", "read"),  # create, not paired with delete
                ("create", "delete"),  # create paired with delete
                ("create", "read", "delete"),  # create paired with delete
                ("read", "delete"),  # delete but no create -> outside denominator
                ("create",),  # single-op create -> not multi-op, excluded
            )
        ),
    ]

    pairing = crud_combinations_stats.compute(tests)["multi_operation_combinations"][
        "create_delete_pairing"
    ]

    assert pairing["scope"] == "resolved_multi_operation_sequences_with_create"
    # create-read, create-delete, create-read-delete are the multi-op creates.
    assert pairing["create_sequence_count"] == 3
    # create-delete and create-read-delete also delete.
    assert pairing["paired_with_delete_count"] == 2
    assert pairing["pct_paired_with_delete"] == pytest.approx(100.0 * 2 / 3)


def test_create_delete_pairing_none_when_no_multi_operation_create() -> None:
    tests = [
        make_test(
            resource_crud_combinations=(
                ("read", "delete"),  # delete without create
                ("create",),  # single-op create
            )
        )
    ]

    pairing = crud_combinations_stats.compute(tests)["multi_operation_combinations"][
        "create_delete_pairing"
    ]

    assert pairing["create_sequence_count"] == 0
    assert pairing["paired_with_delete_count"] == 0
    # An empty create population reports a None rate, distinct from a genuine 0.0.
    assert pairing["pct_paired_with_delete"] is None


def test_multi_operation_combinations_empty_when_only_single_verbs() -> None:
    tests = [make_test(resource_crud_combinations=(("read",), ("create",)))]

    multi = crud_combinations_stats.compute(tests)["multi_operation_combinations"]

    assert multi["multi_operation_sequence_count"] == 0
    assert multi["pct_of_resolved_sequences"] == pytest.approx(0.0)
    # An empty subset reports None shares, distinct from a genuine 0.0.
    assert multi["combinations"]["create-read"] == {
        "operations": ["create", "read"],
        "count": 0,
        "pct": None,
    }


def test_full_crud_combination_counted_under_quad_label() -> None:
    tests = [
        make_test(resource_crud_combinations=(("create", "read", "update", "delete"),))
    ]

    combinations = crud_combinations_stats.compute(tests)["combinations"]

    assert combinations["create-read-update-delete"]["count"] == 1
    assert combinations["create-read-update-delete"]["pct"] == pytest.approx(100.0)


def test_unresolved_sequences_excluded_from_resolved_denominator() -> None:
    tests = [
        make_test(
            resource_crud_combinations=(("create", "read"),),
            unresolved_resource_crud_sequence_count=2,
        ),
    ]

    result = crud_combinations_stats.compute(tests)

    assert result["resolved_sequence_count"] == 1
    assert result["unresolved_sequence_count"] == 2
    assert result["total_sequence_count"] == 3
    # The share denominator is the resolved population, not the total.
    assert result["combinations"]["create-read"]["pct"] == pytest.approx(100.0)


def test_empty_input_yields_zeroed_distribution() -> None:
    result = crud_combinations_stats.compute([])

    assert result["api_test_count"] == 0
    assert result["resolved_sequence_count"] == 0
    assert result["unresolved_sequence_count"] == 0
    assert result["total_sequence_count"] == 0
    assert len(result["combinations"]) == 15
    assert result["combinations"]["create-read"]["count"] == 0
    # An empty population reports a None share, distinct from a genuine 0.0.
    assert result["combinations"]["create-read"]["pct"] is None
