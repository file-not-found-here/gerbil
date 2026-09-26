from __future__ import annotations

import pytest

from gerbil.statistics import verb_combinations as verb_combinations_stats
from gerbil.statistics.records import (
    CRUD_OPERATIONS,
    CRUD_VERBS,
    HTTP_METHODS,
    TestRecord,
)


def make_test(
    *,
    is_api_test: bool = True,
    resource_verb_combinations: tuple[tuple[str, ...], ...] = (),
    unresolved_resource_verb_sequence_count: int = 0,
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
        resource_verb_combinations=resource_verb_combinations,
        unresolved_resource_verb_sequence_count=unresolved_resource_verb_sequence_count,
    )


# --- Single-verb keying ---


def test_single_verb_combinations_cover_all_six_crud_verbs() -> None:
    assert verb_combinations_stats.SINGLE_VERB_COMBINATIONS == (
        ("GET",),
        ("HEAD",),
        ("POST",),
        ("PUT",),
        ("PATCH",),
        ("DELETE",),
    )
    # The verb order is exactly CRUD_VERBS, so the two cannot drift.
    assert (
        tuple(verb for (verb,) in verb_combinations_stats.SINGLE_VERB_COMBINATIONS)
        == CRUD_VERBS
    )


def test_compute_counts_combinations_over_resolved_sequences() -> None:
    tests = [
        make_test(resource_verb_combinations=(("GET", "POST"), ("GET",))),
        make_test(resource_verb_combinations=(("GET", "POST"),)),
        # Non-API test: its sequences never enter the population.
        make_test(is_api_test=False, resource_verb_combinations=(("DELETE",),)),
    ]

    result = verb_combinations_stats.compute(tests)

    assert result["scope"] == "resolved_test_resource_sequences"
    assert result["api_test_count"] == 2
    assert result["resolved_sequence_count"] == 3
    assert result["unresolved_sequence_count"] == 0
    assert result["single_verb_sequence_count"] == 1

    combinations = result["combinations"]
    # The member list is keyed "verbs" (not "operations") for the verb view.
    assert combinations["GET-POST"] == {
        "verbs": ["GET", "POST"],
        "count": 2,
        "pct": pytest.approx(100.0 * 2 / 3),
    }
    assert combinations["GET-only"] == {
        "verbs": ["GET"],
        "count": 1,
        "pct": pytest.approx(100.0 / 3),
    }
    # Every single verb is keyed even when unobserved -> genuine 0.
    assert combinations["DELETE-only"] == {
        "verbs": ["DELETE"],
        "count": 0,
        "pct": pytest.approx(0.0),
    }


def test_put_and_patch_are_separate_single_verbs() -> None:
    # The same resource sequences a CRUD view would both label update-only split
    # into PUT-only and PATCH-only here.
    tests = [make_test(resource_verb_combinations=(("PUT",), ("PATCH",), ("PUT",)))]

    combinations = verb_combinations_stats.compute(tests)["combinations"]

    assert combinations["PUT-only"]["count"] == 2
    assert combinations["PATCH-only"]["count"] == 1


# --- Multi-verb subset, re-based ---


def test_multi_verb_combinations_rebase_share_on_multi_verb_subset() -> None:
    tests = [
        make_test(
            resource_verb_combinations=(
                ("GET",),
                ("POST",),
                ("POST",),
                ("GET", "POST"),
                ("GET", "POST"),
                ("GET", "DELETE"),
            )
        ),
    ]

    multi = verb_combinations_stats.compute(tests)["multi_verb_combinations"]

    assert multi["scope"] == "resolved_multi_verb_test_resource_sequences"
    assert multi["multi_verb_sequence_count"] == 3
    assert multi["pct_of_resolved_sequences"] == pytest.approx(100.0 * 3 / 6)

    combinations = multi["combinations"]
    # Observed combinations only, most frequent first.
    assert list(combinations) == ["GET-POST", "GET-DELETE"]
    assert combinations["GET-POST"]["pct"] == pytest.approx(100.0 * 2 / 3)
    assert combinations["GET-DELETE"]["pct"] == pytest.approx(100.0 / 3)
    # Shares sum to 100 over the multi-verb subset.
    assert sum(entry["pct"] for entry in combinations.values()) == pytest.approx(100.0)


def test_multi_verb_combinations_sorted_by_count_then_canonical_order() -> None:
    tests = [
        make_test(
            resource_verb_combinations=(
                ("GET", "DELETE"),
                ("GET", "POST"),
                ("GET", "POST"),
                ("GET", "PUT"),
            )
        )
    ]

    combinations = verb_combinations_stats.compute(tests)["multi_verb_combinations"][
        "combinations"
    ]

    # GET-POST (2) leads; the two singletons tie at 1 and break on canonical verb
    # order (POST < PUT < DELETE), so GET-PUT precedes GET-DELETE.
    assert list(combinations) == ["GET-POST", "GET-PUT", "GET-DELETE"]


def test_post_delete_pairing_within_multi_verb_sequences() -> None:
    tests = [
        make_test(
            resource_verb_combinations=(
                ("GET", "POST"),  # POST, not paired with DELETE
                ("POST", "DELETE"),  # POST paired with DELETE
                ("GET", "POST", "DELETE"),  # POST paired with DELETE
                ("GET", "DELETE"),  # DELETE but no POST -> outside denominator
                ("POST",),  # single-verb POST -> not multi-verb, excluded
            )
        ),
    ]

    pairing = verb_combinations_stats.compute(tests)["multi_verb_combinations"][
        "post_delete_pairing"
    ]

    assert pairing["scope"] == "resolved_multi_verb_sequences_with_post"
    assert pairing["post_sequence_count"] == 3
    assert pairing["paired_with_delete_count"] == 2
    assert pairing["pct_paired_with_delete"] == pytest.approx(100.0 * 2 / 3)


def test_post_delete_pairing_none_when_no_multi_verb_post() -> None:
    tests = [
        make_test(
            resource_verb_combinations=(
                ("GET", "DELETE"),  # DELETE without POST
                ("POST",),  # single-verb POST
            )
        )
    ]

    pairing = verb_combinations_stats.compute(tests)["multi_verb_combinations"][
        "post_delete_pairing"
    ]

    assert pairing["post_sequence_count"] == 0
    assert pairing["paired_with_delete_count"] == 0
    assert pairing["pct_paired_with_delete"] is None


def test_unresolved_sequences_excluded_from_resolved_denominator() -> None:
    tests = [
        make_test(
            resource_verb_combinations=(("GET", "POST"),),
            unresolved_resource_verb_sequence_count=2,
        ),
    ]

    result = verb_combinations_stats.compute(tests)

    assert result["resolved_sequence_count"] == 1
    assert result["unresolved_sequence_count"] == 2
    assert result["total_sequence_count"] == 3
    assert result["combinations"]["GET-POST"]["pct"] == pytest.approx(100.0)


def test_empty_input_yields_zeroed_distribution() -> None:
    result = verb_combinations_stats.compute([])

    assert result["api_test_count"] == 0
    assert result["resolved_sequence_count"] == 0
    assert result["single_verb_sequence_count"] == 0
    # All six single verbs are keyed even with no data.
    assert len(result["combinations"]) == 6
    assert result["combinations"]["GET-only"]["count"] == 0
    assert result["combinations"]["GET-only"]["pct"] is None
    multi = result["multi_verb_combinations"]
    assert multi["multi_verb_sequence_count"] == 0
    assert multi["combinations"] == {}
