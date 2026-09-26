from __future__ import annotations

import pytest

from gerbil.analysis.schema import (
    ApiSequenceStep,
    AssertionRole,
    CallSiteOriginKind,
    HttpSequenceSummary,
    HttpTestSequence,
    LifecyclePhase,
    OriginContext,
    SequenceStepKind,
    SourceSpan,
    TestMethodAnalysis,
)
from gerbil.statistics import http_sequences as http_sequence_stats
from gerbil.statistics.records import CRUD_OPERATIONS, HTTP_METHODS, TestRecord
from gerbil.statistics.records import project_project
from gerbil.statistics.runner import compute_all_statistics
from tests.statistics_builders import api_test, project


_SPAN = SourceSpan(start_line=1, start_column=1, end_line=1, end_column=2)
_ORIGIN = OriginContext(
    phase=LifecyclePhase.TEST,
    kind=CallSiteOriginKind.TEST_METHOD,
)


def _step(
    order: int,
    kind: SequenceStepKind,
    *,
    method_name: str,
    http_method: str | None = None,
    http_path: str | None = None,
    assertion_role: AssertionRole | None = None,
) -> ApiSequenceStep:
    return ApiSequenceStep(
        order=order,
        kind=kind,
        phase=LifecyclePhase.TEST,
        origin=_ORIGIN,
        method_name=method_name,
        source_span=_SPAN,
        http_method=http_method,
        http_path=http_path,
        assertion_role=assertion_role,
    )


def make_test(
    *,
    is_api_test: bool = True,
    http_sequence_count: int = 0,
    http_sequence_lengths: tuple[int, ...] = (),
    http_sequence_request_build_counts: tuple[int, ...] | None = None,
    http_sequence_http_request_counts: tuple[int, ...] | None = None,
    http_sequence_request_side_lengths: tuple[int, ...] = (),
    http_sequence_response_check_lengths: tuple[int, ...] = (),
    http_sequence_crud_operations: tuple[tuple[str, ...], ...] | None = None,
    http_sequence_verb_operations: tuple[tuple[str, ...], ...] | None = None,
    dispatch_event_request_builder_counts: tuple[int, ...] | None = None,
    dispatch_event_response_check_counts: tuple[int, ...] | None = None,
    http_sequence_response_check_count: int = 0,
    verification_counts: tuple[int, ...] = (0, 0, 0),
    event_counts: tuple[int, ...] = (0, 0, 0),
    has_multiple_http_sequences: bool = False,
    has_repeated_http_sequence: bool = False,
    has_shared_http_sequence: bool = False,
    distinct_endpoint_count: int = 0,
    re_dispatches_endpoint: bool = False,
    all_dispatch_events_resolved: bool = False,
    distinct_http_method_count: int = 0,
) -> TestRecord:
    if http_sequence_crud_operations is None:
        http_sequence_crud_operations = tuple(
            () for _ in http_sequence_response_check_lengths
        )
    if http_sequence_verb_operations is None:
        http_sequence_verb_operations = tuple(
            () for _ in http_sequence_response_check_lengths
        )
    if http_sequence_request_build_counts is None:
        http_sequence_request_build_counts = tuple(
            max(length - 1, 0) for length in http_sequence_request_side_lengths
        )
    if http_sequence_http_request_counts is None:
        http_sequence_http_request_counts = tuple(
            1 if length > 0 else 0 for length in http_sequence_request_side_lengths
        )
    if dispatch_event_request_builder_counts is None:
        dispatch_event_request_builder_counts = tuple(
            build_count
            for build_count, request_count in zip(
                http_sequence_request_build_counts,
                http_sequence_http_request_counts,
                strict=True,
            )
            if request_count > 0
        )
    if dispatch_event_response_check_counts is None:
        dispatch_event_response_check_counts = tuple(
            check_count
            for check_count, request_count in zip(
                http_sequence_response_check_lengths,
                http_sequence_http_request_counts,
                strict=True,
            )
            if request_count > 0
        )
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
        event_counts=event_counts,
        verification_counts=verification_counts,
        fixture_builder_phase_counts=(0, 0),
        fixture_event_phase_counts=(0, 0),
        fixture_verification_phase_counts=(0, 0),
        http_method_counts=(0,) * len(HTTP_METHODS),
        crud_operation_counts=(0,) * len(CRUD_OPERATIONS),
        http_sequence_count=http_sequence_count,
        http_sequence_lengths=http_sequence_lengths,
        http_sequence_request_build_counts=http_sequence_request_build_counts,
        http_sequence_http_request_counts=http_sequence_http_request_counts,
        http_sequence_request_side_lengths=http_sequence_request_side_lengths,
        http_sequence_response_check_lengths=http_sequence_response_check_lengths,
        http_sequence_crud_operations=http_sequence_crud_operations,
        http_sequence_verb_operations=http_sequence_verb_operations,
        dispatch_event_request_builder_counts=dispatch_event_request_builder_counts,
        dispatch_event_response_check_counts=dispatch_event_response_check_counts,
        http_sequence_response_check_count=http_sequence_response_check_count,
        has_multiple_http_sequences=has_multiple_http_sequences,
        has_repeated_http_sequence=has_repeated_http_sequence,
        has_shared_http_sequence=has_shared_http_sequence,
        distinct_endpoint_count=distinct_endpoint_count,
        re_dispatches_endpoint=re_dispatches_endpoint,
        all_dispatch_events_resolved=all_dispatch_events_resolved,
        distinct_http_method_count=distinct_http_method_count,
    )


def test_http_sequence_distributions_are_over_api_tests() -> None:
    tests = [
        make_test(
            http_sequence_count=2,
            http_sequence_lengths=(3, 4),
            http_sequence_request_side_lengths=(1, 2),
            http_sequence_response_check_lengths=(2, 2),
            http_sequence_response_check_count=4,
            verification_counts=(3, 1, 1),
            event_counts=(2, 0, 0),
            has_multiple_http_sequences=True,
            has_repeated_http_sequence=True,
            distinct_endpoint_count=1,
            distinct_http_method_count=2,
        ),
        make_test(
            http_sequence_count=1,
            http_sequence_lengths=(2,),
            http_sequence_request_side_lengths=(1,),
            http_sequence_response_check_lengths=(1,),
            http_sequence_response_check_count=1,
            verification_counts=(1, 0, 0),
            event_counts=(1, 0, 0),
            has_shared_http_sequence=True,
            distinct_endpoint_count=1,
            distinct_http_method_count=1,
        ),
        make_test(is_api_test=False, http_sequence_count=99),
    ]

    result = http_sequence_stats.compute(tests)

    assert result["scope"] == "api_tests"
    assert result["api_test_count"] == 2
    assert result["sequence_count_per_test"]["mean"] == pytest.approx(1.5)
    assert result["sequence_length"]["count"] == 3
    assert result["sequence_length"]["max"] == 4.0
    assert result["http_assertion_count_per_test"]["mean"] == pytest.approx(3.0)
    assert result["sequenced_response_check_count_per_test"]["mean"] == pytest.approx(
        2.5
    )
    assert result["request_side_sequence_length"]["mean"] == pytest.approx(4 / 3)
    assert result["response_side_sequence_length"]["count"] == 3
    assert result["response_side_sequence_length"]["mean"] == pytest.approx(5 / 3)
    # Per-sequence response checks are [2, 2, 1] (total 5); every top fraction
    # rounds up to a single sequence holding 2 of the 5 checks.
    concentration = result["verification_concentration"]
    assert concentration["scope"] == "http_test_sequences"
    assert concentration["item_count"] == 3
    assert concentration["total"] == 5
    # Each top cut rounds up to the single highest-check sequence (2 checks), so
    # its within-cut distribution summarizes a one-element [2] sample.
    single_seq_distribution = {
        "count": 1,
        "min": 2.0,
        "max": 2.0,
        "mean": 2.0,
        "p25": 2.0,
        "p50": 2.0,
        "p75": 2.0,
        "p90": 2.0,
    }
    assert concentration["by_top_fraction"] == {
        "top_1pct": {
            "item_count": 1,
            "share_of_total": pytest.approx(0.4),
            "distribution": single_seq_distribution,
        },
        "top_5pct": {
            "item_count": 1,
            "share_of_total": pytest.approx(0.4),
            "distribution": single_seq_distribution,
        },
        "top_10pct": {
            "item_count": 1,
            "share_of_total": pytest.approx(0.4),
            "distribution": single_seq_distribution,
        },
        "top_25pct": {
            "item_count": 1,
            "share_of_total": pytest.approx(0.4),
            "distribution": single_seq_distribution,
        },
    }
    # One of the three sequences carries 0 checks (none here) and one carries
    # exactly 1, so two of three carry at most one.
    assert concentration["share_zero_checks"] == {
        "count": 0,
        "total": 3,
        "proportion": pytest.approx(0.0),
    }
    assert concentration["share_at_most_one_check"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }
    # No sequence here carries more than 5 (or 10) checks.
    assert concentration["share_more_than_five_checks"] == {
        "count": 0,
        "total": 3,
        "proportion": pytest.approx(0.0),
    }
    assert concentration["share_more_than_ten_checks"] == {
        "count": 0,
        "total": 3,
        "proportion": pytest.approx(0.0),
    }
    fan = result["request_dispatch_event_fan"]
    assert fan["dispatch_event_count"] == 3
    assert fan["request_builder_fan_in"]["mean"] == pytest.approx(1 / 3)
    assert fan["verification_fan_out"]["mean"] == pytest.approx(5 / 3)
    assert result["sequence_shape_distribution"]["classified_sequence_count"] == 3
    # The duplicate per-sequence response-check key is no longer reported.
    assert "sequenced_response_check_count_per_sequence" not in result
    assert result["tests_with_multiple_sequences"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    assert result["tests_with_repeated_sequence"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    assert result["tests_with_shared_sequence"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    assert result["distinct_endpoint_count_per_test"]["mean"] == 1.0
    diversity = result["http_method_diversity"]
    assert diversity["distinct_method_count_per_test"]["mean"] == pytest.approx(1.5)
    assert diversity["tests_with_multiple_methods"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    multi_request = diversity["multi_request_tests"]
    assert multi_request["scope"] == "api_tests_with_multiple_request_events"
    assert multi_request["test_count"] == 1
    assert multi_request["distinct_method_count_per_test"]["mean"] == 2.0
    assert multi_request["tests_with_multiple_methods"] == {
        "count": 1,
        "total": 1,
        "proportion": pytest.approx(1.0),
    }


def test_multi_sequence_composition_gates_duplication_on_full_resolution() -> None:
    tests = [
        # A large, repetitive, multi-endpoint, fully resolved scenario: top decile.
        make_test(
            http_sequence_count=12,
            has_multiple_http_sequences=True,
            distinct_endpoint_count=4,
            re_dispatches_endpoint=True,
            has_repeated_http_sequence=True,
            all_dispatch_events_resolved=True,
        ),
        # A small fully resolved test on a single re-dispatched endpoint.
        make_test(
            http_sequence_count=3,
            has_multiple_http_sequences=True,
            distinct_endpoint_count=1,
            re_dispatches_endpoint=True,
            has_repeated_http_sequence=True,
            all_dispatch_events_resolved=True,
        ),
        # Multi-sequence with an unresolved dispatch: a duplicate fingerprint here
        # could be a "*" collapse, so it is excluded from the duplication cohort
        # even though one endpoint resolved.
        make_test(
            http_sequence_count=2,
            has_multiple_http_sequences=True,
            distinct_endpoint_count=1,
            has_repeated_http_sequence=True,
            all_dispatch_events_resolved=False,
        ),
        # Single-sequence test: contributes only to the size-tail denominator.
        make_test(http_sequence_count=1),
        make_test(is_api_test=False, http_sequence_count=99),
    ]

    composition = http_sequence_stats.compute(tests)["multi_sequence_composition"]

    assert composition["api_test_count"] == 4
    assert composition["multi_sequence_test_count"] == 3
    assert composition["fully_resolved_multi_sequence_test_count"] == 2
    assert composition["share_five_or_more_sequences"] == {
        "count": 1,
        "total": 4,
        "proportion": pytest.approx(1 / 4),
    }
    assert composition["share_ten_or_more_sequences"]["count"] == 1
    resolved = composition["fully_resolved_multi_sequence"]
    assert resolved["scope"] == (
        "api_tests_with_multiple_sequences_and_all_dispatches_resolved"
    )
    # Only the two fully resolved tests; both re-dispatch and duplicate. The
    # partially resolved test (which also flagged a duplicate) is excluded.
    assert resolved["test_count"] == 2
    assert resolved["re_dispatches_endpoint"]["proportion"] == pytest.approx(1.0)
    assert resolved["has_duplicate_sequence"]["proportion"] == pytest.approx(1.0)
    assert resolved["share_multiple_endpoints"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    # ceil(2 * 0.1) == 1, so the single largest scenario forms the top decile.
    top = resolved["top_decile"]
    assert top["test_count"] == 1
    assert top["sequence_count"]["min"] == 12.0
    assert top["distinct_endpoint_count"]["p50"] == 4.0

    # The all-resolution cohort additionally includes the partially resolved test,
    # so genome-nexus-style dynamic-URL scenarios are not dropped.
    every = composition["all_multi_sequence"]
    assert every["scope"] == "api_tests_with_multiple_sequences_any_resolution"
    assert "note" in every
    assert every["test_count"] == 3
    # re_dispatches_endpoint stays resolved-only: 2 of 3 tests. has_duplicate_sequence
    # is over-counted here (3 of 3) because the unresolved test collapses to "*".
    assert every["re_dispatches_endpoint"]["proportion"] == pytest.approx(2 / 3)
    assert every["has_duplicate_sequence"]["proportion"] == pytest.approx(1.0)
    assert every["share_multiple_endpoints"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }
    # ceil(3 * 0.1) == 1: the largest scenario forms the top decile here too.
    assert every["top_decile"]["test_count"] == 1
    assert every["top_decile"]["sequence_count"]["min"] == 12.0


def test_scenario_verification_placement_back_loads_checks() -> None:
    tests = [
        # Three sequences, verified only at the very end: terminal-only.
        make_test(
            http_sequence_request_side_lengths=(1, 1, 1),
            http_sequence_response_check_lengths=(0, 0, 2),
            has_multiple_http_sequences=True,
        ),
        # Two sequences, both verified: check-as-you-go (fully verified).
        make_test(
            http_sequence_request_side_lengths=(1, 1),
            http_sequence_response_check_lengths=(1, 1),
            has_multiple_http_sequences=True,
        ),
        # Two sequences, neither verified: a fully silent scenario.
        make_test(
            http_sequence_request_side_lengths=(1, 1),
            http_sequence_response_check_lengths=(0, 0),
            has_multiple_http_sequences=True,
        ),
        # Single-sequence test is not a scenario and is excluded.
        make_test(
            http_sequence_request_side_lengths=(1,),
            http_sequence_response_check_lengths=(3,),
            has_multiple_http_sequences=False,
        ),
        make_test(is_api_test=False, http_sequence_count=99),
    ]

    placement = http_sequence_stats.compute(tests)["scenario_verification_placement"]

    assert placement["scope"] == "api_tests_with_multiple_sequences"
    assert placement["multi_sequence_test_count"] == 3
    # Terminal sequence verified in 2 of 3 scenarios; interior sequences verified
    # in only 1 of the 4 pooled non-terminal sequences.
    assert placement["terminal_sequence_verified"] == {
        "count": 2,
        "total": 3,
        "proportion": pytest.approx(2 / 3),
    }
    assert placement["interior_sequence_verified"] == {
        "count": 1,
        "total": 4,
        "proportion": pytest.approx(1 / 4),
    }
    assert placement["shapes"]["no_verification"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }
    assert placement["shapes"]["terminal_only"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }
    assert placement["shapes"]["any_interior_verified"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }
    assert placement["shapes"]["fully_verified"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }
    # Verified-sequence fractions [1/3, 1, 0] average to 4/9.
    assert placement["verified_sequence_fraction"]["mean"] == pytest.approx(4 / 9)


def test_sequence_operation_transitions_count_consecutive_crud_pairs() -> None:
    tests = [
        make_test(
            http_sequence_request_side_lengths=(1, 1, 1, 1),
            http_sequence_response_check_lengths=(0, 0, 0, 0),
            http_sequence_crud_operations=(
                ("read",),
                ("read",),
                ("create",),
                ("read",),
            ),
            has_multiple_http_sequences=True,
        ),
        make_test(
            http_sequence_request_side_lengths=(1, 1),
            http_sequence_response_check_lengths=(0, 0),
            http_sequence_crud_operations=(("create",), ("delete",)),
            has_multiple_http_sequences=True,
        ),
        # The trailing unmapped sequence forms a pair that is not CRUD-mapped.
        make_test(
            http_sequence_request_side_lengths=(1, 1),
            http_sequence_response_check_lengths=(0, 0),
            http_sequence_crud_operations=(("read",), ()),
            has_multiple_http_sequences=True,
        ),
        make_test(is_api_test=False, http_sequence_count=99),
    ]

    transitions = http_sequence_stats.compute(tests)["sequence_operation_transitions"]

    assert transitions["scope"] == "consecutive_sequence_pairs"
    assert transitions["consecutive_pair_count"] == 5
    assert transitions["crud_mapped_pair_count"] == 4
    assert transitions["transitions"]["read->read"] == {
        "count": 1,
        "total": 4,
        "proportion": pytest.approx(1 / 4),
    }
    assert transitions["transitions"]["create->read"]["count"] == 1
    assert transitions["transitions"]["create->delete"]["count"] == 1
    assert transitions["transitions"]["update->update"]["count"] == 0
    assert transitions["read_to_read"]["proportion"] == pytest.approx(1 / 4)
    assert transitions["same_operation_repeat"]["proportion"] == pytest.approx(1 / 4)
    # create->read is the only write-then-read pair.
    assert transitions["write_then_read"]["proportion"] == pytest.approx(1 / 4)
    assert transitions["create_then_delete"]["proportion"] == pytest.approx(1 / 4)


def test_operation_self_affinity_reports_lift_over_independence() -> None:
    # Two source pairs are read->read and two read->? per the fixture below, so
    # read's source marginal is high; the lift contrasts the conditional
    # self-repeat against each operation's target marginal (independence baseline).
    tests = [
        make_test(
            http_sequence_request_side_lengths=(1, 1, 1, 1),
            http_sequence_response_check_lengths=(0, 0, 0, 0),
            http_sequence_crud_operations=(
                ("read",),
                ("read",),
                ("create",),
                ("read",),
            ),
            has_multiple_http_sequences=True,
        ),
        make_test(
            http_sequence_request_side_lengths=(1, 1),
            http_sequence_response_check_lengths=(0, 0),
            http_sequence_crud_operations=(("create",), ("delete",)),
            has_multiple_http_sequences=True,
        ),
    ]

    transitions = http_sequence_stats.compute(tests)["sequence_operation_transitions"]
    affinity = transitions["operation_self_affinity"]

    assert affinity["scope"] == "crud_mapped_consecutive_sequence_pairs"
    # Pairs: read->read, read->create, create->read, create->delete (4 mapped).
    read = affinity["by_operation"]["read"]
    # read is the source of 2 pairs (read->read, read->create) and the target of 2
    # (read->read, create->read); one of its 2 source pairs repeats.
    assert read["source_share"] == {
        "count": 2,
        "total": 4,
        "proportion": pytest.approx(2 / 4),
    }
    assert read["target_share"]["count"] == 2
    assert read["self_repeat_given_source"]["proportion"] == pytest.approx(1 / 2)
    assert read["expected_if_independent"] == pytest.approx(2 / 4)
    # Conditional self-repeat (1/2) over target marginal (2/4) is exactly 1.0.
    assert read["lift"] == pytest.approx(1.0)

    # create never repeats (its only pairs are create->read, create->delete).
    create = affinity["by_operation"]["create"]
    assert create["self_repeat_given_source"]["proportion"] == pytest.approx(0.0)
    assert create["lift"] == pytest.approx(0.0)

    # update is absent as a source: undefined conditional and lift, not zero.
    update = affinity["by_operation"]["update"]
    assert update["source_share"]["count"] == 0
    assert update["self_repeat_given_source"]["proportion"] is None
    assert update["lift"] is None

    aggregate = affinity["aggregate"]
    assert aggregate["observed_self_repeat"] == pytest.approx(1 / 4)
    # Expected = sum(source_total * target_total) / mapped^2 over read/create/delete.
    # read 2*2 + create 2*1 + delete 0*1 = 6, over 4^2 = 16 -> 0.375.
    assert aggregate["expected_self_repeat_if_independent"] == pytest.approx(6 / 16)
    assert aggregate["lift"] == pytest.approx((1 / 4) / (6 / 16))


# --- Verb-level sequence transitions ---


def test_sequence_verb_transitions_keep_put_and_patch_distinct() -> None:
    # Same fixture shape as the CRUD transition test, but at verb granularity a
    # PUT followed by a PATCH is a verb change, not a repeated update.
    tests = [
        make_test(
            http_sequence_request_side_lengths=(1, 1, 1, 1),
            http_sequence_response_check_lengths=(0, 0, 0, 0),
            http_sequence_verb_operations=(
                ("GET",),
                ("GET",),
                ("POST",),
                ("GET",),
            ),
            has_multiple_http_sequences=True,
        ),
        make_test(
            http_sequence_request_side_lengths=(1, 1),
            http_sequence_response_check_lengths=(0, 0),
            http_sequence_verb_operations=(("PUT",), ("PATCH",)),
            has_multiple_http_sequences=True,
        ),
        # The trailing unmapped (empty) sequence forms a pair that is not mapped.
        make_test(
            http_sequence_request_side_lengths=(1, 1),
            http_sequence_response_check_lengths=(0, 0),
            http_sequence_verb_operations=(("GET",), ()),
            has_multiple_http_sequences=True,
        ),
        make_test(is_api_test=False, http_sequence_count=99),
    ]

    transitions = http_sequence_stats.compute(tests)["sequence_verb_transitions"]

    assert transitions["scope"] == "consecutive_sequence_pairs"
    # GET->GET, GET->POST, POST->GET, PUT->PATCH, GET->(unmapped): 5 pairs, 4 mapped.
    assert transitions["consecutive_pair_count"] == 5
    assert transitions["verb_mapped_pair_count"] == 4
    assert transitions["transitions"]["GET->GET"] == {
        "count": 1,
        "total": 4,
        "proportion": pytest.approx(1 / 4),
    }
    assert transitions["transitions"]["GET->POST"]["count"] == 1
    assert transitions["transitions"]["POST->GET"]["count"] == 1
    # PUT->PATCH is a cross-verb transition, so neither self-repeats.
    assert transitions["transitions"]["PUT->PATCH"]["count"] == 1
    assert transitions["transitions"]["PUT->PUT"]["count"] == 0
    assert transitions["transitions"]["PATCH->PATCH"]["count"] == 0
    # Only GET->GET repeats a verb, so same_verb_repeat is 1/4 (the PUT/PATCH pair
    # would have counted under the CRUD view's update->update). The per-verb
    # self-repeat is read off the diagonal of the transitions matrix.
    assert transitions["same_verb_repeat"]["proportion"] == pytest.approx(1 / 4)
    assert transitions["transitions"]["GET->GET"]["proportion"] == pytest.approx(1 / 4)


def test_sequence_verb_transitions_full_matrix_keyed_over_six_verbs() -> None:
    transitions = http_sequence_stats.compute([])["sequence_verb_transitions"]

    # 6 verbs -> 36 ordered transition keys, all present even with no data, so the
    # self-repeat diagonal (GET->GET, ...) is available for every verb.
    assert len(transitions["transitions"]) == 36
    for verb in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
        assert f"{verb}->{verb}" in transitions["transitions"]
    assert transitions["verb_mapped_pair_count"] == 0
    assert transitions["same_verb_repeat"]["proportion"] is None


def test_method_diversity_multi_request_split_sums_events_across_origins() -> None:
    # request_event_total sums event_counts across origin buckets, so a test that
    # dispatches one request per origin (no single bucket >= 2) still counts as
    # multi-request for the verb-diversity split.
    tests = [
        make_test(event_counts=(1, 1, 0), distinct_http_method_count=2),
        make_test(event_counts=(1, 0, 0), distinct_http_method_count=1),
    ]

    diversity = http_sequence_stats.compute(tests)["http_method_diversity"]

    multi_request = diversity["multi_request_tests"]
    assert multi_request["test_count"] == 1
    assert multi_request["tests_with_multiple_methods"] == {
        "count": 1,
        "total": 1,
        "proportion": pytest.approx(1.0),
    }


def test_empty_http_sequence_distribution_has_stable_shape() -> None:
    result = http_sequence_stats.compute([])

    assert result["api_test_count"] == 0
    assert result["sequence_count_per_test"]["count"] == 0
    assert result["sequence_length"]["mean"] is None
    assert result["response_side_sequence_length"]["count"] == 0
    assert result["response_side_sequence_length"]["mean"] is None
    empty_distribution = {
        "count": 0,
        "min": None,
        "max": None,
        "mean": None,
        "p25": None,
        "p50": None,
        "p75": None,
        "p90": None,
    }
    assert result["verification_concentration"] == {
        "scope": "http_test_sequences",
        "item_count": 0,
        "total": 0,
        "by_top_fraction": {
            "top_1pct": {
                "item_count": 0,
                "share_of_total": None,
                "distribution": empty_distribution,
            },
            "top_5pct": {
                "item_count": 0,
                "share_of_total": None,
                "distribution": empty_distribution,
            },
            "top_10pct": {
                "item_count": 0,
                "share_of_total": None,
                "distribution": empty_distribution,
            },
            "top_25pct": {
                "item_count": 0,
                "share_of_total": None,
                "distribution": empty_distribution,
            },
        },
        "share_zero_checks": {"count": 0, "total": 0, "proportion": None},
        "share_at_most_one_check": {"count": 0, "total": 0, "proportion": None},
        "share_more_than_five_checks": {"count": 0, "total": 0, "proportion": None},
        "share_more_than_ten_checks": {"count": 0, "total": 0, "proportion": None},
    }
    empty_followed = {"count": 0, "total": 0, "proportion": None}
    empty_operation = {
        "count": 0,
        "pct_of_crud_mapped_no_verification_sequences": None,
        "followed_by_later_sequence": empty_followed,
        "followed_by_read": empty_followed,
    }
    assert result["unverified_sequence_crud"] == {
        "scope": "http_test_sequences",
        "sequence_count": 0,
        "no_verification_sequence_count": 0,
        "pct_of_sequences": None,
        "crud_mapped_no_verification_sequence_count": 0,
        "operations": {
            "create": empty_operation,
            "read": empty_operation,
            "update": empty_operation,
            "delete": empty_operation,
        },
        "uncategorized": {
            "count": 0,
            "pct_of_no_verification_sequences": None,
            "followed_by_later_sequence": empty_followed,
            "followed_by_read": empty_followed,
        },
        "followed_by_later_sequence": empty_followed,
        "followed_by_read": empty_followed,
    }
    assert result["request_dispatch_event_fan"] == {
        "scope": "sequenced_request_dispatch_events",
        "dispatch_event_count": 0,
        "request_builder_fan_in": {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p90": None,
        },
        "verification_fan_out": {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p90": None,
        },
    }
    assert result["sequence_shape_distribution"] == {
        "scope": "http_test_sequences",
        "sequence_count": 0,
        "classified_sequence_count": 0,
        "excluded_sequence_count": 0,
        "labels": {
            "build-dispatch-verification": {
                "count": 0,
                "pct_of_classified_sequences": None,
            },
            "build-dispatch-no-verification": {
                "count": 0,
                "pct_of_classified_sequences": None,
            },
            "dispatch-only": {
                "count": 0,
                "pct_of_classified_sequences": None,
            },
            "dispatch-verification-no-build": {
                "count": 0,
                "pct_of_classified_sequences": None,
            },
        },
    }
    empty_share = {"count": 0, "total": 0, "proportion": None}
    composition = result["multi_sequence_composition"]
    assert composition["api_test_count"] == 0
    assert composition["multi_sequence_test_count"] == 0
    assert composition["fully_resolved_multi_sequence_test_count"] == 0
    assert composition["share_five_or_more_sequences"] == empty_share
    assert composition["share_ten_or_more_sequences"] == empty_share
    resolved = composition["fully_resolved_multi_sequence"]
    assert resolved["test_count"] == 0
    assert resolved["re_dispatches_endpoint"] == empty_share
    assert resolved["has_duplicate_sequence"] == empty_share
    assert resolved["top_decile"]["test_count"] == 0
    every = composition["all_multi_sequence"]
    assert every["test_count"] == 0
    assert every["top_decile"]["test_count"] == 0
    placement = result["scenario_verification_placement"]
    assert placement["multi_sequence_test_count"] == 0
    assert placement["terminal_sequence_verified"] == empty_share
    assert placement["interior_sequence_verified"] == empty_share
    assert placement["shapes"] == {
        "no_verification": empty_share,
        "terminal_only": empty_share,
        "any_interior_verified": empty_share,
        "fully_verified": empty_share,
    }
    assert placement["verified_sequence_fraction"]["mean"] is None
    transitions = result["sequence_operation_transitions"]
    assert transitions["consecutive_pair_count"] == 0
    assert transitions["crud_mapped_pair_count"] == 0
    assert transitions["transitions"]["read->read"] == empty_share
    assert transitions["read_to_read"] == empty_share
    assert transitions["write_then_read"] == empty_share
    assert result["tests_with_multiple_sequences"] == {
        "count": 0,
        "total": 0,
        "proportion": None,
    }
    diversity = result["http_method_diversity"]
    assert diversity["distinct_method_count_per_test"]["count"] == 0
    assert diversity["tests_with_multiple_methods"] == {
        "count": 0,
        "total": 0,
        "proportion": None,
    }
    assert diversity["multi_request_tests"]["test_count"] == 0
    assert diversity["multi_request_tests"]["tests_with_multiple_methods"] == {
        "count": 0,
        "total": 0,
        "proportion": None,
    }


def test_http_sequence_stats_survive_project_record_projection() -> None:
    steps = [
        _step(
            1,
            SequenceStepKind.REQUEST_BUILD,
            method_name="get",
            http_method="GET",
            http_path="/items/1",
        ),
        _step(
            2,
            SequenceStepKind.HTTP_REQUEST,
            method_name="exchange",
            http_method="GET",
            http_path="/items/1",
        ),
        _step(
            3,
            SequenceStepKind.RESPONSE_CHECK,
            method_name="isOk",
            assertion_role=AssertionRole.STATUS,
        ),
    ]
    test = api_test()
    test.http.test_sequences = [
        HttpTestSequence(order=1, steps=steps, fingerprint="request-build:GET:/items/*")
    ]
    test.http.sequence_summary = HttpSequenceSummary(
        sequence_count=1,
        sequence_lengths=[3],
        request_build_step_count=1,
        http_request_step_count=1,
        response_check_step_count=1,
        distinct_endpoint_count=1,
        distinct_http_method_count=1,
    )

    statistics = compute_all_statistics([project_project(project(tests=[test]))])

    result = statistics["http_test_sequence_distribution"]
    assert result["sequence_count_per_test"]["mean"] == 1.0
    assert (
        result["http_method_diversity"]["distinct_method_count_per_test"]["mean"] == 1.0
    )
    assert result["sequence_length"]["mean"] == 3.0
    assert result["request_side_sequence_length"]["mean"] == 2.0
    assert result["response_side_sequence_length"]["mean"] == 1.0
    fan = result["request_dispatch_event_fan"]
    assert fan["dispatch_event_count"] == 1
    assert fan["request_builder_fan_in"]["mean"] == 1.0
    assert fan["verification_fan_out"]["mean"] == 1.0
    assert result["distinct_endpoint_count_per_test"]["mean"] == 1.0
    shape = result["sequence_shape_distribution"]
    assert shape["classified_sequence_count"] == 1
    assert shape["excluded_sequence_count"] == 0
    assert shape["labels"]["build-dispatch-verification"] == {
        "count": 1,
        "pct_of_classified_sequences": 100.0,
    }


def test_sequence_shape_projection_counts_all_labels_from_sequence_steps() -> None:
    sequences = [
        HttpTestSequence(
            order=1,
            steps=[
                _step(1, SequenceStepKind.REQUEST_BUILD, method_name="get"),
                _step(2, SequenceStepKind.HTTP_REQUEST, method_name="perform"),
                _step(3, SequenceStepKind.RESPONSE_CHECK, method_name="isOk"),
            ],
            fingerprint="full",
        ),
        HttpTestSequence(
            order=2,
            steps=[
                _step(4, SequenceStepKind.REQUEST_BUILD, method_name="post"),
                _step(5, SequenceStepKind.HTTP_REQUEST, method_name="perform"),
            ],
            fingerprint="build-dispatch",
        ),
        HttpTestSequence(
            order=3,
            steps=[_step(6, SequenceStepKind.HTTP_REQUEST, method_name="exchange")],
            fingerprint="dispatch-only",
        ),
        HttpTestSequence(
            order=4,
            steps=[
                _step(7, SequenceStepKind.HTTP_REQUEST, method_name="exchange"),
                _step(8, SequenceStepKind.RESPONSE_CHECK, method_name="isOk"),
            ],
            fingerprint="dispatch-check",
        ),
        HttpTestSequence(
            order=5,
            steps=[_step(9, SequenceStepKind.REQUEST_BUILD, method_name="request")],
            fingerprint="no-dispatch",
        ),
    ]
    test = api_test()
    test.http.test_sequences = sequences
    test.http.sequence_summary = HttpSequenceSummary(
        sequence_count=5,
        sequence_lengths=[3, 2, 1, 2, 1],
        request_build_step_count=3,
        http_request_step_count=4,
        response_check_step_count=2,
    )

    statistics = compute_all_statistics([project_project(project(tests=[test]))])

    result = statistics["http_test_sequence_distribution"][
        "sequence_shape_distribution"
    ]
    assert result["sequence_count"] == 5
    assert result["classified_sequence_count"] == 4
    assert result["excluded_sequence_count"] == 1
    assert result["labels"] == {
        "build-dispatch-verification": {
            "count": 1,
            "pct_of_classified_sequences": pytest.approx(25.0),
        },
        "build-dispatch-no-verification": {
            "count": 1,
            "pct_of_classified_sequences": pytest.approx(25.0),
        },
        "dispatch-only": {
            "count": 1,
            "pct_of_classified_sequences": pytest.approx(25.0),
        },
        "dispatch-verification-no-build": {
            "count": 1,
            "pct_of_classified_sequences": pytest.approx(25.0),
        },
    }

    fan = statistics["http_test_sequence_distribution"]["request_dispatch_event_fan"]
    assert fan["dispatch_event_count"] == 4
    assert fan["request_builder_fan_in"]["mean"] == pytest.approx(0.5)
    assert fan["request_builder_fan_in"]["max"] == 1.0
    assert fan["verification_fan_out"]["mean"] == pytest.approx(0.5)
    assert fan["verification_fan_out"]["max"] == 1.0


def test_sequence_shape_distribution_counts_only_classified_sequences() -> None:
    tests = [
        make_test(
            http_sequence_count=5,
            http_sequence_lengths=(3, 2, 1, 2, 1),
            http_sequence_request_build_counts=(1, 2, 0, 0, 1),
            http_sequence_http_request_counts=(1, 1, 1, 1, 0),
            http_sequence_request_side_lengths=(2, 3, 1, 1, 1),
            http_sequence_response_check_lengths=(2, 0, 0, 1, 0),
            http_sequence_response_check_count=3,
        ),
        make_test(is_api_test=False, http_sequence_count=99),
    ]

    result = http_sequence_stats.compute(tests)["sequence_shape_distribution"]

    assert result["scope"] == "http_test_sequences"
    assert result["sequence_count"] == 5
    assert result["classified_sequence_count"] == 4
    assert result["excluded_sequence_count"] == 1
    assert result["labels"] == {
        "build-dispatch-verification": {
            "count": 1,
            "pct_of_classified_sequences": pytest.approx(25.0),
        },
        "build-dispatch-no-verification": {
            "count": 1,
            "pct_of_classified_sequences": pytest.approx(25.0),
        },
        "dispatch-only": {
            "count": 1,
            "pct_of_classified_sequences": pytest.approx(25.0),
        },
        "dispatch-verification-no-build": {
            "count": 1,
            "pct_of_classified_sequences": pytest.approx(25.0),
        },
    }


def test_unverified_sequence_crud_buckets_no_verification_sequences() -> None:
    tests = [
        make_test(
            # Six sequences; the fifth carries a verification (check count 3) and
            # so is excluded from the no-verification buckets, though its read
            # still counts as a "later read" for the sequences before it.
            http_sequence_request_side_lengths=(1, 1, 1, 1, 1, 1),
            http_sequence_response_check_lengths=(0, 0, 0, 0, 3, 0),
            http_sequence_crud_operations=(
                ("create",),
                ("read",),
                ("update",),
                ("delete",),
                ("read",),
                (),
            ),
        ),
        make_test(is_api_test=False, http_sequence_count=99),
    ]

    result = http_sequence_stats.compute(tests)["unverified_sequence_crud"]

    assert result["scope"] == "http_test_sequences"
    assert result["sequence_count"] == 6
    assert result["no_verification_sequence_count"] == 5
    assert result["pct_of_sequences"] == pytest.approx(100.0 * 5 / 6)
    # The operations mix is conditioned on a resolved CRUD verb, so the four
    # buckets share the four-sequence CRUD-mapped denominator (the unmapped one
    # is excluded here) and each is followed by a later read.
    assert result["crud_mapped_no_verification_sequence_count"] == 4
    followed_one = {"count": 1, "total": 1, "proportion": pytest.approx(1.0)}

    def operation(pct: float) -> dict[str, object]:
        return {
            "count": 1,
            "pct_of_crud_mapped_no_verification_sequences": pytest.approx(pct),
            # The first four sequences each have a later sequence and a later read.
            "followed_by_later_sequence": followed_one,
            "followed_by_read": followed_one,
        }

    assert result["operations"] == {
        "create": operation(25.0),
        "read": operation(25.0),
        "update": operation(25.0),
        "delete": operation(25.0),
    }
    not_followed = {"count": 0, "total": 1, "proportion": pytest.approx(0.0)}
    # The unmapped-verb sequence is last, so it has neither a later sequence nor a
    # later read, but still counts toward the no-verification total.
    assert result["uncategorized"] == {
        "count": 1,
        "pct_of_no_verification_sequences": pytest.approx(20.0),
        "followed_by_later_sequence": not_followed,
        "followed_by_read": not_followed,
    }
    # Four of five no-verification sequences are non-terminal; the same four are
    # followed by a later read (the verified GET at index 4, or the GET at 1).
    assert result["followed_by_later_sequence"] == {
        "count": 4,
        "total": 5,
        "proportion": pytest.approx(4 / 5),
    }
    assert result["followed_by_read"] == {
        "count": 4,
        "total": 5,
        "proportion": pytest.approx(4 / 5),
    }


def test_unverified_sequence_followed_by_read_is_scoped_within_a_test() -> None:
    tests = [
        make_test(
            http_sequence_request_side_lengths=(1,),
            http_sequence_response_check_lengths=(0,),
            http_sequence_crud_operations=(("create",),),
        ),
        make_test(
            http_sequence_request_side_lengths=(1,),
            http_sequence_response_check_lengths=(0,),
            http_sequence_crud_operations=(("read",),),
        ),
    ]

    result = http_sequence_stats.compute(tests)["unverified_sequence_crud"]

    assert result["no_verification_sequence_count"] == 2
    assert result["operations"]["create"]["count"] == 1
    assert result["operations"]["read"]["count"] == 1
    # Each test has a single sequence, so neither has a later sequence; the read
    # lives in a different test, so it does not "follow" the create either.
    assert result["followed_by_later_sequence"] == {
        "count": 0,
        "total": 2,
        "proportion": pytest.approx(0.0),
    }
    assert result["followed_by_read"] == {
        "count": 0,
        "total": 2,
        "proportion": pytest.approx(0.0),
    }


def test_unverified_sequence_crud_survives_project_record_projection() -> None:
    write_sequence = HttpTestSequence(
        order=1,
        steps=[
            _step(
                1,
                SequenceStepKind.REQUEST_BUILD,
                method_name="post",
                http_method="POST",
                http_path="/items",
            ),
            _step(
                2,
                SequenceStepKind.HTTP_REQUEST,
                method_name="exchange",
                http_method="POST",
                http_path="/items",
            ),
        ],
        fingerprint="post:/items",
    )
    read_sequence = HttpTestSequence(
        order=2,
        steps=[
            _step(
                3,
                SequenceStepKind.HTTP_REQUEST,
                method_name="exchange",
                http_method="GET",
                http_path="/items/1",
            ),
            _step(
                4,
                SequenceStepKind.RESPONSE_CHECK,
                method_name="isOk",
                assertion_role=AssertionRole.STATUS,
            ),
        ],
        fingerprint="get:/items/*",
    )
    test = api_test()
    test.http.test_sequences = [write_sequence, read_sequence]
    test.http.sequence_summary = HttpSequenceSummary(
        sequence_count=2,
        sequence_lengths=[2, 2],
        http_request_step_count=2,
        request_build_step_count=1,
        response_check_step_count=1,
    )

    record = project_project(project(tests=[test]))
    assert record.tests[0].http_sequence_crud_operations == (("create",), ("read",))

    result = compute_all_statistics([record])["http_test_sequence_distribution"][
        "unverified_sequence_crud"
    ]
    assert result["sequence_count"] == 2
    assert result["no_verification_sequence_count"] == 1
    assert result["crud_mapped_no_verification_sequence_count"] == 1
    followed_one = {"count": 1, "total": 1, "proportion": pytest.approx(1.0)}
    assert result["operations"]["create"] == {
        "count": 1,
        "pct_of_crud_mapped_no_verification_sequences": pytest.approx(100.0),
        # The unverified POST is non-terminal and implicitly confirmed by the
        # trailing GET.
        "followed_by_later_sequence": followed_one,
        "followed_by_read": followed_one,
    }
    assert result["operations"]["read"]["count"] == 0
    assert result["followed_by_read"] == {
        "count": 1,
        "total": 1,
        "proportion": pytest.approx(1.0),
    }


def test_re_dispatches_endpoint_projection_detects_repeated_endpoint() -> None:
    def get_sequence(order: int) -> HttpTestSequence:
        return HttpTestSequence(
            order=order,
            steps=[
                _step(
                    order,
                    SequenceStepKind.HTTP_REQUEST,
                    method_name="exchange",
                    http_method="GET",
                    # Distinct ids normalize to the same endpoint key.
                    http_path=f"/items/{order}",
                )
            ],
            fingerprint="get:/items/*",
        )

    repeats = api_test()
    repeats.http.test_sequences = [get_sequence(1), get_sequence(2)]
    repeats.http.sequence_summary = HttpSequenceSummary(
        sequence_count=2, http_request_step_count=2, distinct_endpoint_count=1
    )
    distinct = api_test()
    distinct.http.test_sequences = [
        get_sequence(1),
        HttpTestSequence(
            order=2,
            steps=[
                _step(
                    2,
                    SequenceStepKind.HTTP_REQUEST,
                    method_name="exchange",
                    http_method="GET",
                    http_path="/orders/1",
                )
            ],
            fingerprint="get:/orders/*",
        ),
    ]
    distinct.http.sequence_summary = HttpSequenceSummary(
        sequence_count=2, http_request_step_count=2, distinct_endpoint_count=2
    )

    # A multi-sequence test with one unresolved dispatch (no HTTP method).
    unresolved = api_test()
    unresolved.http.test_sequences = [
        get_sequence(1),
        HttpTestSequence(
            order=2,
            steps=[
                _step(
                    2,
                    SequenceStepKind.HTTP_REQUEST,
                    method_name="exchange",
                    http_method=None,
                    http_path="/orders/1",
                )
            ],
            fingerprint="*:/orders/*",
        ),
    ]
    unresolved.http.sequence_summary = HttpSequenceSummary(
        sequence_count=2, http_request_step_count=2, distinct_endpoint_count=1
    )

    record = project_project(project(tests=[repeats, distinct, unresolved]))

    assert record.tests[0].re_dispatches_endpoint is True
    assert record.tests[1].re_dispatches_endpoint is False
    # Full resolution: the first two resolve every dispatch; the third does not.
    assert record.tests[0].all_dispatch_events_resolved is True
    assert record.tests[1].all_dispatch_events_resolved is True
    assert record.tests[2].all_dispatch_events_resolved is False


def _api_test_with_fingerprints(*fingerprints: str) -> TestMethodAnalysis:
    test = api_test()
    test.http.test_sequences = [
        HttpTestSequence(order=index + 1, steps=[], fingerprint=fingerprint)
        for index, fingerprint in enumerate(fingerprints)
    ]
    test.http.sequence_summary = HttpSequenceSummary(
        sequence_count=len(fingerprints),
        distinct_sequence_fingerprint_count=len(set(fingerprints)),
    )
    return test


def test_shared_sequence_flag_marks_cross_test_duplication_within_project() -> None:
    shared_a = _api_test_with_fingerprints("GET:/items/*")
    shared_b = _api_test_with_fingerprints("GET:/items/*")
    unique = _api_test_with_fingerprints("POST:/orders/*")

    record = project_project(project(tests=[shared_a, shared_b, unique]))

    assert [test.has_shared_http_sequence for test in record.tests] == [
        True,
        True,
        False,
    ]

    result = compute_all_statistics([record])["http_test_sequence_distribution"]
    assert result["tests_with_shared_sequence"] == {
        "count": 2,
        "total": 3,
        "proportion": pytest.approx(2 / 3),
    }


def test_shared_sequence_flag_does_not_span_projects() -> None:
    project_one = project_project(
        project(dataset_name="one", tests=[_api_test_with_fingerprints("GET:/items/*")])
    )
    project_two = project_project(
        project(dataset_name="two", tests=[_api_test_with_fingerprints("GET:/items/*")])
    )

    assert not project_one.tests[0].has_shared_http_sequence
    assert not project_two.tests[0].has_shared_http_sequence

    result = compute_all_statistics([project_one, project_two])[
        "http_test_sequence_distribution"
    ]
    assert result["tests_with_shared_sequence"] == {
        "count": 0,
        "total": 2,
        "proportion": 0.0,
    }
