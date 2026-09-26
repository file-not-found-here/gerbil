from __future__ import annotations

from collections import Counter

import pytest

from gerbil.statistics import resource_interaction as resource_interaction_stats
from gerbil.statistics.records import (
    CRUD_OPERATIONS,
    HTTP_METHODS,
    ResourceCrudRecord,
    TestRecord,
)


def _method_counts(*http_methods: str) -> tuple[int, ...]:
    counter = Counter(method.upper() for method in http_methods)
    return tuple(counter.get(method, 0) for method in HTTP_METHODS)


def _crud_counts(*operations: str) -> tuple[int, ...]:
    counter = Counter(operations)
    return tuple(counter.get(operation, 0) for operation in CRUD_OPERATIONS)


def make_test(
    *,
    is_api_test: bool = True,
    is_controller_unit_test: bool = False,
    expanded_ncloc: int = 0,
    expanded_cyclomatic_complexity: int = 0,
    expanded_helper_method_count: int = 0,
    test_helper_method_count: int = 0,
    expanded_objects_created: int = 0,
    expanded_assertion_count: int = 0,
    mocked_interaction_count: int = 0,
    dependency_strategy_label_count: int = 0,
    dispatch_labels: tuple[str, ...] = (),
    has_read_after_write: bool = False,
    has_cleanup_delete: bool = False,
    resource_lifecycle_labels: tuple[str, ...] = (),
    setup_fixture_count: int = 0,
    teardown_fixture_count: int = 0,
    status_range_counts: tuple[int, ...] = (0, 0, 0, 0, 0, 0),
    builder_counts: tuple[int, ...] = (0, 0, 0),
    event_counts: tuple[int, ...] = (0, 0, 0),
    verification_counts: tuple[int, ...] = (0, 0, 0),
    http_method_counts: tuple[int, ...] = (0,) * len(HTTP_METHODS),
    crud_operation_counts: tuple[int, ...] = (0,) * len(CRUD_OPERATIONS),
) -> TestRecord:
    return TestRecord(
        is_api_test=is_api_test,
        is_controller_unit_test=is_controller_unit_test,
        expanded_ncloc=expanded_ncloc,
        expanded_cyclomatic_complexity=expanded_cyclomatic_complexity,
        expanded_helper_method_count=expanded_helper_method_count,
        test_helper_method_count=test_helper_method_count,
        expanded_objects_created=expanded_objects_created,
        expanded_assertion_count=expanded_assertion_count,
        mocked_interaction_count=mocked_interaction_count,
        dependency_strategy_label_count=dependency_strategy_label_count,
        dispatch_labels=dispatch_labels,
        has_read_after_write=has_read_after_write,
        has_cleanup_delete=has_cleanup_delete,
        resource_lifecycle_labels=resource_lifecycle_labels,
        setup_fixture_count=setup_fixture_count,
        teardown_fixture_count=teardown_fixture_count,
        status_range_counts=status_range_counts,
        builder_counts=builder_counts,
        event_counts=event_counts,
        verification_counts=verification_counts,
        fixture_builder_phase_counts=(0, 0),
        fixture_event_phase_counts=(0, 0),
        fixture_verification_phase_counts=(0, 0),
        http_method_counts=http_method_counts,
        crud_operation_counts=crud_operation_counts,
    )


def make_resource(
    *,
    available: tuple[str, ...] = (),
    exercised: tuple[str, ...] = (),
    missing: tuple[str, ...] | None = None,
    full_crud_test_count: int = 0,
    available_verbs: tuple[str, ...] = (),
    exercised_verbs: tuple[str, ...] = (),
    missing_verbs: tuple[str, ...] | None = None,
    exercising_sequence_verb_sets: tuple[tuple[str, ...], ...] = (),
) -> ResourceCrudRecord:
    if missing is None:
        missing = tuple(
            operation for operation in available if operation not in exercised
        )
    if missing_verbs is None:
        missing_verbs = tuple(
            verb for verb in available_verbs if verb not in exercised_verbs
        )
    return ResourceCrudRecord(
        available_operations=available,
        exercised_operations=exercised,
        missing_available_operations=missing,
        full_crud_test_count=full_crud_test_count,
        available_verbs=available_verbs,
        exercised_verbs=exercised_verbs,
        missing_available_verbs=missing_verbs,
        exercising_sequence_verb_sets=exercising_sequence_verb_sets,
    )


def test_scope_label_marks_gated_population() -> None:
    result = resource_interaction_stats.compute([make_test(is_api_test=False)], [])

    assert result["scope"] == "endpoints_and_api_tests"
    assert result["api_test_count"] == 0
    assert result["lifecycle_label_distribution"]["scope"] == "test_resource_pairs"
    assert result["per_test"]["scope"] == "api_tests_with_resource_sequences"
    assert result["resource_count_per_test"]["scope"] == "api_tests"
    assert result["http_method_distribution"]["scope"] == "http_dispatch_events"
    assert (
        result["crud_operation_distribution"]["scope"]
        == "crud_mapped_http_dispatch_events"
    )


def test_lifecycle_labels_counted_over_pairs() -> None:
    tests = [
        make_test(resource_lifecycle_labels=("read-only", "full-crud")),
        make_test(resource_lifecycle_labels=("read-only",)),
        make_test(is_api_test=False),  # no resource sequences
    ]

    labels = resource_interaction_stats.compute(tests, [])[
        "lifecycle_label_distribution"
    ]

    assert labels["pair_count"] == 3
    assert labels["labels"]["read-only"]["count"] == 2
    assert labels["labels"]["read-only"]["pct"] == pytest.approx(100.0 * 2 / 3)
    assert labels["labels"]["full-crud"]["count"] == 1
    # Unobserved labels are present with a genuine 0 count and pct.
    assert labels["labels"]["write-only"] == {"count": 0, "pct": pytest.approx(0.0)}


def test_per_test_counts_each_distinct_label_once() -> None:
    tests = [
        # Two resources sharing a label -> the test still counts once under it.
        make_test(resource_lifecycle_labels=("read-only", "read-only", "full-crud")),
        make_test(resource_lifecycle_labels=("read-only",)),
        # Zero-resource API test -> outside the per-test population.
        make_test(resource_lifecycle_labels=()),
        make_test(is_api_test=False),
    ]

    per_test = resource_interaction_stats.compute(tests, [])["per_test"]

    assert per_test["test_count"] == 2
    assert per_test["labels"]["read-only"]["test_count"] == 2
    assert per_test["labels"]["read-only"]["pct_of_tests"] == pytest.approx(100.0)
    assert per_test["labels"]["full-crud"]["test_count"] == 1
    assert per_test["labels"]["full-crud"]["pct_of_tests"] == pytest.approx(50.0)
    # Unobserved labels are present with a genuine 0 count and pct.
    assert per_test["labels"]["write-only"] == {
        "test_count": 0,
        "pct_of_tests": pytest.approx(0.0),
    }


def test_per_test_multiple_distinct_labels_ignores_repeats() -> None:
    tests = [
        # Same label on both resources -> not a multi-label test.
        make_test(resource_lifecycle_labels=("read-only", "read-only")),
        make_test(resource_lifecycle_labels=("read-only", "full-crud")),
    ]

    per_test = resource_interaction_stats.compute(tests, [])["per_test"]

    assert per_test["multiple_distinct_label_tests"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }


def test_per_test_lifecycle_shares_scoped_to_tests_with_sequences() -> None:
    tests = [
        make_test(
            resource_lifecycle_labels=("create-verify",),
            has_read_after_write=True,
            has_cleanup_delete=False,
        ),
        make_test(
            resource_lifecycle_labels=("create-verify-cleanup",),
            has_read_after_write=True,
            has_cleanup_delete=True,
        ),
        # No resource sequences -> excluded from the share denominators.
        make_test(resource_lifecycle_labels=()),
    ]

    per_test = resource_interaction_stats.compute(tests, [])["per_test"]

    assert per_test["has_read_after_write"] == {
        "count": 2,
        "total": 2,
        "proportion": pytest.approx(1.0),
    }
    assert per_test["has_cleanup_delete"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }


def test_resource_count_per_test_distribution_and_multiple_resource_share() -> None:
    tests = [
        make_test(resource_lifecycle_labels=("read-only", "write-only")),
        make_test(resource_lifecycle_labels=("full-crud",)),
        make_test(resource_lifecycle_labels=()),
        # Non-API tests carry no resource analysis and are excluded from the
        # per-test denominator rather than diluting it with structural zeros.
        make_test(is_api_test=False),
    ]

    resource_counts = resource_interaction_stats.compute(tests, [])[
        "resource_count_per_test"
    ]

    assert resource_counts["distribution"]["count"] == 3
    assert resource_counts["distribution"]["min"] == 0.0
    assert resource_counts["distribution"]["max"] == 2.0
    assert resource_counts["distribution"]["mean"] == pytest.approx(1.0)
    assert resource_counts["tests_with_multiple_resources"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }


def test_http_method_distribution_counts_events_across_tests() -> None:
    tests = [
        make_test(http_method_counts=_method_counts("GET", "GET", "POST")),
        make_test(http_method_counts=_method_counts("DELETE")),
        make_test(is_api_test=False),  # no events contributed
    ]

    dist = resource_interaction_stats.compute(tests, [])["http_method_distribution"]

    assert dist["request_count"] == 4
    assert dist["methods"]["GET"] == {"count": 2, "pct": pytest.approx(50.0)}
    assert dist["methods"]["POST"]["count"] == 1
    assert dist["methods"]["DELETE"]["count"] == 1
    # Unobserved methods are present with a genuine 0 count and pct.
    assert dist["methods"]["PUT"] == {"count": 0, "pct": pytest.approx(0.0)}


def test_crud_operation_distribution_counts_events_across_tests() -> None:
    tests = [
        make_test(crud_operation_counts=_crud_counts("create", "read", "read")),
        make_test(crud_operation_counts=_crud_counts("delete")),
    ]

    dist = resource_interaction_stats.compute(tests, [])["crud_operation_distribution"]

    assert dist["request_count"] == 4
    assert dist["operations"]["read"] == {"count": 2, "pct": pytest.approx(50.0)}
    assert dist["operations"]["create"]["count"] == 1
    assert dist["operations"]["delete"]["count"] == 1
    # Unobserved operations are present with a genuine 0 count and pct.
    assert dist["operations"]["update"] == {"count": 0, "pct": pytest.approx(0.0)}


def test_tested_share_over_all_resources() -> None:
    resources = [
        make_resource(available=("create", "read"), exercised=("read",)),
        make_resource(available=("read",), exercised=("read",)),
        # Available but never exercised -> counts toward the denominator only.
        make_resource(available=("read", "update"), exercised=()),
        # No operations at all -> still part of the denominator, not tested.
        make_resource(),
    ]

    tested = resource_interaction_stats.compute([], resources)["tested"]

    assert tested == {
        "count": 2,
        "total": 4,
        "proportion": pytest.approx(0.5),
    }


def test_exercised_completeness_among_tested_only() -> None:
    resources = [
        # Tested with a denominator -> completeness 2/4 = 0.5.
        make_resource(
            available=("create", "read", "update", "delete"),
            exercised=("create", "read"),
        ),
        # Tested with a denominator -> completeness 2/2 = 1.0.
        make_resource(available=("read", "update"), exercised=("read", "update")),
        # Exercised but no available operations -> no denominator, excluded.
        make_resource(available=(), exercised=("read",)),
        # Available but untested -> excluded from the among-tested sample.
        make_resource(available=("read", "update"), exercised=()),
    ]

    completeness = resource_interaction_stats.compute([], resources)[
        "exercised_completeness"
    ]["among_tested"]

    assert completeness["count"] == 2
    assert completeness["min"] == pytest.approx(0.5)
    assert completeness["max"] == pytest.approx(1.0)
    assert completeness["mean"] == pytest.approx(0.75)


def test_per_operation_exercise_rates() -> None:
    resources = [
        make_resource(
            available=("create", "read", "update", "delete"),
            exercised=("create", "read"),
        ),
        make_resource(available=("read", "update"), exercised=("read", "update")),
    ]

    per_op = resource_interaction_stats.compute([], resources)["per_operation_exercise"]

    assert per_op["create"] == {
        "available_resource_count": 1,
        "exercised_resource_count": 1,
        "rate": pytest.approx(1.0),
    }
    assert per_op["read"]["rate"] == pytest.approx(1.0)
    assert per_op["update"]["available_resource_count"] == 2
    assert per_op["update"]["exercised_resource_count"] == 1
    assert per_op["update"]["rate"] == pytest.approx(0.5)
    # Delete is available on one resource but never exercised -> genuine 0.0.
    assert per_op["delete"]["rate"] == pytest.approx(0.0)


def test_read_only_and_full_crud_shapes() -> None:
    resources = [
        # Writable (offers create) but tests only read it.
        make_resource(available=("create", "read"), exercised=("read",)),
        # Full-CRUD capable and full-CRUD tested.
        make_resource(
            available=("create", "read", "update", "delete"),
            exercised=("create", "read", "update", "delete"),
            full_crud_test_count=2,
        ),
        # Writable but untested.
        make_resource(available=("update",), exercised=()),
        # No write available -> outside the writable population.
        make_resource(available=("read",), exercised=("read",)),
    ]

    result = resource_interaction_stats.compute([], resources)

    read_only = result["read_only_when_writable"]
    assert read_only["writable_resource_count"] == 3
    assert read_only["writable_tested_resource_count"] == 2
    assert read_only["read_only_tested_count"] == 1
    assert read_only["proportion_of_writable_tested"] == pytest.approx(1 / 2)
    # The diluted writable-population proportion is no longer reported.
    assert "proportion_of_writable" not in read_only

    full_crud = result["full_crud_tested"]
    assert full_crud["full_crud_capable_resource_count"] == 1
    assert full_crud["full_crud_tested_count"] == 1
    assert full_crud["proportion_of_capable"] == pytest.approx(1.0)
    # The all-resources proportion is redundant with "tested" and is gone.
    assert "proportion_of_all_resources" not in full_crud


def test_replaced_top_level_keys_removed() -> None:
    result = resource_interaction_stats.compute([], [make_resource()])

    assert "resources_with_any_test_count" not in result
    assert "resources_with_full_crud_test_count" not in result


# --- Availability ceiling: the confounder the exercise distribution is read against ---


def test_availability_profile_distribution_counts_resources_by_available_subset() -> (
    None
):
    resources = [
        # Two resources expose only reads.
        make_resource(available=("read",)),
        make_resource(available=("read",), exercised=("read",)),
        # One exposes create+read, one exposes full CRUD.
        make_resource(available=("create", "read")),
        make_resource(available=("create", "read", "update", "delete")),
    ]

    profile = resource_interaction_stats.compute([], resources)[
        "availability_profile_distribution"
    ]

    assert profile["scope"] == "production_resources"
    assert profile["resource_count"] == 4
    assert profile["crud_capable_resource_count"] == 4
    assert profile["no_crud_operation_resource_count"] == 0
    combinations = profile["combinations"]
    assert combinations["read-only"] == {
        "operations": ["read"],
        "count": 2,
        "pct": pytest.approx(50.0),
    }
    assert combinations["create-read"]["count"] == 1
    assert combinations["create-read-update-delete"]["count"] == 1
    # Availability shares sum to 100 over the CRUD-capable population.
    assert sum(entry["pct"] for entry in combinations.values()) == pytest.approx(100.0)
    # Every subset is keyed even when unobserved.
    assert combinations["delete-only"] == {
        "operations": ["delete"],
        "count": 0,
        "pct": pytest.approx(0.0),
    }


def test_availability_profile_separates_resources_with_no_crud_operations() -> None:
    resources = [
        make_resource(available=("read",)),
        # Endpoints exist but expose no CRUD operation (e.g. only OPTIONS, or a
        # method wildcard) -> no profile, counted out of the share denominator.
        make_resource(available=()),
        make_resource(available=()),
    ]

    profile = resource_interaction_stats.compute([], resources)[
        "availability_profile_distribution"
    ]

    assert profile["resource_count"] == 3
    assert profile["crud_capable_resource_count"] == 1
    assert profile["no_crud_operation_resource_count"] == 2
    # The lone read-only resource is the whole capable population.
    assert profile["combinations"]["read-only"]["pct"] == pytest.approx(100.0)


def test_fully_exercised_share_over_capable_and_tested_populations() -> None:
    resources = [
        # Fully exercised: nothing available is missing.
        make_resource(available=("create", "read"), exercised=("create", "read")),
        # Capable and tested but incomplete (delete missing).
        make_resource(
            available=("read", "delete"),
            exercised=("read",),
        ),
        # Capable but untested -> not fully exercised, and outside the tested subset.
        make_resource(available=("read", "update"), exercised=()),
        # No available CRUD operations -> outside the capable population entirely.
        make_resource(available=(), exercised=("read",)),
    ]

    fully = resource_interaction_stats.compute([], resources)["fully_exercised"]

    assert fully["crud_capable_resource_count"] == 3
    assert fully["tested_resource_count"] == 2
    assert fully["fully_exercised_count"] == 1
    assert fully["not_fully_exercised_count"] == 2
    assert fully["pct_fully_exercised_of_capable"] == pytest.approx(100.0 / 3)
    assert fully["pct_not_fully_exercised_of_capable"] == pytest.approx(100.0 * 2 / 3)
    # Restricting to tested resources separates "never touched" from "incomplete".
    assert fully["pct_fully_exercised_of_tested"] == pytest.approx(50.0)


def test_exercise_by_availability_breadth_rate_and_composition() -> None:
    resources = [
        # Single-operation available, exercised.
        make_resource(available=("read",), exercised=("read",)),
        # Single-operation available, untested.
        make_resource(available=("create",), exercised=()),
        # Multi-operation available, exercised.
        make_resource(available=("create", "read"), exercised=("create",)),
        # Multi-operation available, untested.
        make_resource(available=("read", "update", "delete"), exercised=()),
        # Tested but no CRUD operation available -> residual of the composition.
        make_resource(available=(), exercised=("read",)),
    ]

    breadth = resource_interaction_stats.compute([], resources)[
        "exercise_by_availability_breadth"
    ]

    # Rate view: exercised share among resources offering exactly one / more verbs.
    single = breadth["single_operation_available"]
    assert single["resource_count"] == 2
    assert single["exercised_count"] == 1
    assert single["exercised_rate"] == pytest.approx(0.5)
    # The lone touched single-verb resource exercised its one available verb.
    assert single["fully_exercised_count"] == 1
    assert single["pct_fully_exercised_of_exercised"] == pytest.approx(100.0)
    multi = breadth["multi_operation_available"]
    assert multi["resource_count"] == 2
    assert multi["exercised_count"] == 1
    assert multi["exercised_rate"] == pytest.approx(0.5)
    # The touched multi-verb resource exercised create but not read -> not full.
    assert multi["fully_exercised_count"] == 0
    assert multi["pct_fully_exercised_of_exercised"] == pytest.approx(0.0)

    # Composition view: of the 3 tested resources, one is single-op-available, one
    # multi-op-available, and one exposes no CRUD verb (the honest residual).
    composition = breadth["tested_composition"]
    assert composition["tested_resource_count"] == 3
    assert composition["single_operation_available_count"] == 1
    assert composition["multi_operation_available_count"] == 1
    assert composition["no_crud_operation_available_count"] == 1
    assert composition["pct_single_operation_available"] == pytest.approx(100.0 / 3)
    assert composition["pct_multi_operation_available"] == pytest.approx(100.0 / 3)


def test_exercised_combination_distribution_among_full_crud_capable() -> None:
    resources = [
        # Full-CRUD capable, exercised create+read only.
        make_resource(
            available=("create", "read", "update", "delete"),
            exercised=("create", "read"),
        ),
        # Full-CRUD capable, exercised the whole surface.
        make_resource(
            available=("create", "read", "update", "delete"),
            exercised=("create", "read", "update", "delete"),
        ),
        # Full-CRUD capable but no test exercised it -> none_exercised.
        make_resource(
            available=("create", "read", "update", "delete"),
            exercised=(),
        ),
        # Not full-CRUD capable -> excluded from this conditioning.
        make_resource(available=("create", "read"), exercised=("create", "read")),
    ]

    conditioned = resource_interaction_stats.compute([], resources)[
        "exercised_combination_when_full_crud_capable"
    ]

    assert conditioned["scope"] == "full_crud_capable_production_resources"
    assert conditioned["capable_resource_count"] == 3
    assert conditioned["none_exercised_count"] == 1
    assert conditioned["none_exercised_pct"] == pytest.approx(100.0 / 3)
    # Of the 2 targeted resources, only the one exercising all four verbs is full.
    assert conditioned["tested_resource_count"] == 2
    assert conditioned["fully_exercised_count"] == 1
    assert conditioned["pct_fully_exercised_of_tested"] == pytest.approx(50.0)
    combinations = conditioned["combinations"]
    # Shares are over the capable population (3), so the two exercised resources
    # plus the untested residual partition it.
    assert combinations["create-read"] == {
        "operations": ["create", "read"],
        "count": 1,
        "pct": pytest.approx(100.0 / 3),
    }
    assert combinations["create-read-update-delete"]["count"] == 1
    combo_share = sum(entry["pct"] for entry in combinations.values())
    assert combo_share + conditioned["none_exercised_pct"] == pytest.approx(100.0)


def test_empty_inputs_yield_zeroed_distributions() -> None:
    result = resource_interaction_stats.compute([], [])

    assert result["scope"] == "endpoints_and_api_tests"
    assert result["resource_count"] == 0
    assert result["tested"]["proportion"] is None
    assert result["lifecycle_label_distribution"]["pair_count"] == 0
    assert result["lifecycle_label_distribution"]["labels"]["read-only"]["pct"] is None
    assert result["per_test"]["test_count"] == 0
    assert result["per_test"]["labels"]["read-only"]["pct_of_tests"] is None
    assert result["per_test"]["multiple_distinct_label_tests"]["proportion"] is None
    assert result["per_test"]["has_read_after_write"]["proportion"] is None
    assert result["per_test"]["has_cleanup_delete"]["proportion"] is None
    assert result["http_method_distribution"]["request_count"] == 0
    assert result["http_method_distribution"]["methods"]["GET"]["pct"] is None
    assert result["crud_operation_distribution"]["request_count"] == 0
    assert result["crud_operation_distribution"]["operations"]["read"]["pct"] is None
    assert result["exercised_completeness"]["among_tested"]["count"] == 0
    profile = result["availability_profile_distribution"]
    assert profile["crud_capable_resource_count"] == 0
    assert profile["combinations"]["read-only"]["pct"] is None
    fully = result["fully_exercised"]
    assert fully["crud_capable_resource_count"] == 0
    assert fully["pct_fully_exercised_of_capable"] is None
    assert fully["pct_not_fully_exercised_of_capable"] is None
    assert fully["pct_fully_exercised_of_tested"] is None
    breadth = result["exercise_by_availability_breadth"]
    assert breadth["single_operation_available"]["exercised_rate"] is None
    assert breadth["multi_operation_available"]["exercised_rate"] is None
    assert (
        breadth["multi_operation_available"]["pct_fully_exercised_of_exercised"] is None
    )
    assert breadth["multi_operation_available"]["fully_exercised_count"] == 0
    assert breadth["tested_composition"]["tested_resource_count"] == 0
    assert breadth["tested_composition"]["pct_multi_operation_available"] is None
    conditioned = result["exercised_combination_when_full_crud_capable"]
    assert conditioned["capable_resource_count"] == 0
    assert conditioned["none_exercised_pct"] is None
    assert conditioned["tested_resource_count"] == 0
    assert conditioned["fully_exercised_count"] == 0
    assert conditioned["pct_fully_exercised_of_tested"] is None
    assert conditioned["combinations"]["create-read-update-delete"]["pct"] is None
    assert result["per_operation_exercise"]["create"]["rate"] is None
    assert result["read_only_when_writable"]["proportion_of_writable_tested"] is None
    assert result["full_crud_tested"]["proportion_of_capable"] is None


# --- Verb-level resource exercise ---


def test_verb_availability_profile_splits_update_into_put_and_patch() -> None:
    resources = [
        make_resource(available_verbs=("GET",)),
        make_resource(available_verbs=("GET",)),
        make_resource(available_verbs=("PUT",)),
        make_resource(available_verbs=("PATCH",)),
        # A resource a CRUD view counts as single-class (update) but that exposes
        # two distinct verbs.
        make_resource(available_verbs=("PUT", "PATCH")),
        make_resource(available_verbs=("GET", "POST")),
        # No verb-capable endpoints -> outside the capable denominator.
        make_resource(available_verbs=()),
    ]

    profile = resource_interaction_stats.compute([], resources)[
        "verb_resource_exercise"
    ]["availability_profile"]

    assert profile["resource_count"] == 7
    assert profile["verb_capable_resource_count"] == 6
    assert profile["no_verb_resource_count"] == 1
    assert profile["single_verb"]["GET"]["count"] == 2
    assert profile["single_verb"]["PUT"]["count"] == 1
    assert profile["single_verb"]["PATCH"]["count"] == 1
    # Two resources expose more than one verb (PUT/PATCH and GET/POST).
    assert profile["expose_multiple_verbs"]["count"] == 2
    assert profile["expose_multiple_verbs"]["pct"] == pytest.approx(100.0 * 2 / 6)
    assert profile["multi_verb_profiles"]["PUT-PATCH"]["count"] == 1
    assert profile["multi_verb_profiles"]["GET-POST"]["count"] == 1


def test_verb_fully_exercised_is_stricter_than_crud() -> None:
    resources = [
        # Exposes PUT and PATCH; a test hit only PUT, so the CRUD view (update)
        # reads as fully exercised but the verb view leaves PATCH missing.
        make_resource(
            available=("update",),
            exercised=("update",),
            available_verbs=("PUT", "PATCH"),
            exercised_verbs=("PUT",),
        ),
        # Both verbs hit -> fully exercised at verb granularity too.
        make_resource(
            available=("read", "create"),
            exercised=("read", "create"),
            available_verbs=("GET", "POST"),
            exercised_verbs=("GET", "POST"),
        ),
        # Untested -> capable but not in the tested denominator.
        make_resource(available_verbs=("GET",)),
    ]

    fully = resource_interaction_stats.compute([], resources)["verb_resource_exercise"][
        "fully_exercised"
    ]

    assert fully["verb_capable_resource_count"] == 3
    assert fully["tested_resource_count"] == 2
    # Only the GET/POST resource is fully exercised at verb granularity.
    assert fully["fully_exercised_count"] == 1
    assert fully["pct_fully_exercised_of_tested"] == pytest.approx(100.0 / 2)


def test_verb_exercise_by_availability_breadth_conditions_on_verb_count() -> None:
    resources = [
        make_resource(available_verbs=("GET",), exercised_verbs=("GET",)),
        make_resource(available_verbs=("GET",)),
        make_resource(available_verbs=("GET", "POST"), exercised_verbs=("GET", "POST")),
        make_resource(available_verbs=("GET", "POST"), exercised_verbs=("GET",)),
        make_resource(available_verbs=("GET", "POST", "DELETE")),
    ]

    breadth = resource_interaction_stats.compute([], resources)[
        "verb_resource_exercise"
    ]["exercise_by_availability_breadth"]

    single = breadth["single_verb_available"]
    assert single["resource_count"] == 2
    assert single["exercised_count"] == 1

    multi = breadth["multi_verb_available"]
    assert multi["resource_count"] == 3
    assert multi["exercised_count"] == 2
    # Of the two multi-verb resources a test touched, one hit every available verb.
    assert multi["pct_fully_exercised_of_exercised"] == pytest.approx(100.0 / 2)


def test_verb_full_traversal_within_single_sequence_requires_one_covering_sequence() -> (
    None
):
    resources = [
        # One sequence walks GET and POST together -> full traversal in a single flow.
        make_resource(
            available_verbs=("GET", "POST"),
            exercised_verbs=("GET", "POST"),
            exercising_sequence_verb_sets=(("GET", "POST"),),
        ),
        # Two separate sequences each cover one verb: the union reaches both, but no
        # single sequence does, so this is not a single-sequence full traversal.
        make_resource(
            available_verbs=("GET", "POST"),
            exercised_verbs=("GET", "POST"),
            exercising_sequence_verb_sets=(("GET",), ("POST",)),
        ),
        # Single-verb resource -> outside the multi-verb scope.
        make_resource(
            available_verbs=("GET",),
            exercised_verbs=("GET",),
            exercising_sequence_verb_sets=(("GET",),),
        ),
        # Multi-verb but no exercising sequence -> outside the targeted scope.
        make_resource(available_verbs=("GET", "DELETE")),
    ]

    traversal = resource_interaction_stats.compute([], resources)[
        "verb_resource_exercise"
    ]["full_traversal_within_single_sequence"]

    assert traversal["scope"] == "multi_verb_resources_with_exercising_sequence"
    assert traversal["resource_count"] == 2
    assert traversal["fully_traversed_count"] == 1
    assert traversal["pct_fully_traversed_in_single_sequence"] == pytest.approx(50.0)


def test_verb_full_traversal_distinguished_from_union_coverage() -> None:
    # Split across two sequences: fully exercised by the union metric yet not by
    # single-sequence traversal, so the two readings must disagree here.
    split = make_resource(
        available_verbs=("GET", "POST"),
        exercised_verbs=("GET", "POST"),
        exercising_sequence_verb_sets=(("GET",), ("POST",)),
    )

    verb = resource_interaction_stats.compute([], [split])["verb_resource_exercise"]

    assert verb["exercise_by_availability_breadth"]["multi_verb_available"][
        "pct_fully_exercised_of_exercised"
    ] == pytest.approx(100.0)
    assert verb["full_traversal_within_single_sequence"][
        "pct_fully_traversed_in_single_sequence"
    ] == pytest.approx(0.0)


def test_verb_exercise_when_full_crud_capable_requires_every_verb() -> None:
    resources = [
        # Full CRUD surface exposing two update verbs; a test exercised every CRUD
        # class but only PUT, so it is fully exercised by class yet not by verb.
        make_resource(
            available=("create", "read", "update", "delete"),
            exercised=("create", "read", "update", "delete"),
            available_verbs=("GET", "POST", "PUT", "PATCH", "DELETE"),
            exercised_verbs=("GET", "POST", "PUT", "DELETE"),
        ),
        # Full CRUD surface fully exercised down to the verb.
        make_resource(
            available=("create", "read", "update", "delete"),
            exercised=("create", "read", "update", "delete"),
            available_verbs=("GET", "POST", "PUT", "DELETE"),
            exercised_verbs=("GET", "POST", "PUT", "DELETE"),
        ),
        # Not full-CRUD-capable -> excluded from this conditioned population.
        make_resource(
            available=("read",),
            available_verbs=("GET",),
            exercised_verbs=("GET",),
        ),
    ]

    conditioned = resource_interaction_stats.compute([], resources)[
        "verb_resource_exercise"
    ]["exercise_when_full_crud_capable"]

    assert conditioned["scope"] == "full_crud_capable_production_resources"
    assert conditioned["capable_resource_count"] == 2
    assert conditioned["tested_resource_count"] == 2
    # Only the resource whose every verb was hit counts as fully exercised.
    assert conditioned["fully_exercised_count"] == 1
    assert conditioned["pct_fully_exercised_of_tested"] == pytest.approx(100.0 / 2)


def test_verb_resource_exercise_empty_reports_none_rates() -> None:
    verb = resource_interaction_stats.compute([], [])["verb_resource_exercise"]

    assert verb["scope"] == "production_resources_http_verb_view"
    assert verb["availability_profile"]["verb_capable_resource_count"] == 0
    assert verb["availability_profile"]["expose_multiple_verbs"]["pct"] is None
    assert verb["fully_exercised"]["pct_fully_exercised_of_tested"] is None
    assert (
        verb["exercise_by_availability_breadth"]["multi_verb_available"][
            "pct_fully_exercised_of_exercised"
        ]
        is None
    )
    assert (
        verb["full_traversal_within_single_sequence"][
            "pct_fully_traversed_in_single_sequence"
        ]
        is None
    )
    assert (
        verb["exercise_when_full_crud_capable"]["pct_fully_exercised_of_tested"] is None
    )
