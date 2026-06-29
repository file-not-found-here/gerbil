from __future__ import annotations

import pytest

from gerbil.analysis.schema import (
    ApiSequenceStep,
    AssertionRole,
    AssertionSummary,
    CallSiteOriginKind,
    HttpTestSequence,
    LifecyclePhase,
    OriginContext,
    SequenceStepKind,
    SourceSpan,
    TestMethodAnalysis,
)
from gerbil.statistics import assertion_clustering as clustering_stats
from gerbil.statistics.records import TestRecord, project_project
from gerbil.statistics.runner import (
    ASSERTION_CLUSTERING_DISTRIBUTION,
    compute_all_statistics,
)
from tests.statistics_builders import api_test, non_api_test, project

_SPAN = SourceSpan(start_line=1, start_column=1, end_line=1, end_column=2)
_ORIGIN = OriginContext(phase=LifecyclePhase.TEST, kind=CallSiteOriginKind.TEST_METHOD)


def _step(
    order: int,
    kind: SequenceStepKind,
    *,
    assertion_role: AssertionRole | None = None,
) -> ApiSequenceStep:
    return ApiSequenceStep(
        order=order,
        kind=kind,
        phase=LifecyclePhase.TEST,
        origin=_ORIGIN,
        method_name="m",
        source_span=_SPAN,
        http_method="GET" if kind == SequenceStepKind.HTTP_REQUEST else None,
        http_path="/x" if kind == SequenceStepKind.HTTP_REQUEST else None,
        assertion_role=assertion_role,
    )


def _sequence(*roles: AssertionRole, fingerprint: str = "fp") -> HttpTestSequence:
    """One dispatch followed by a response check per given assertion role."""
    steps = [_step(1, SequenceStepKind.HTTP_REQUEST)]
    for index, role in enumerate(roles, start=2):
        steps.append(_step(index, SequenceStepKind.RESPONSE_CHECK, assertion_role=role))
    return HttpTestSequence(order=1, steps=steps, fingerprint=fingerprint)


def _records(*analyses: TestMethodAnalysis) -> list[TestRecord]:
    return list(project_project(project(tests=list(analyses))).tests)


_STATUS = AssertionRole.STATUS
_BODY = AssertionRole.BODY
_HEADER = AssertionRole.HEADER


# --- Projection: sequence steps resolve to per-role per-sequence counts ---


def test_projection_resolves_per_sequence_role_counts() -> None:
    (record,) = _records(
        api_test(
            test_sequences=[
                _sequence(_STATUS, _BODY, _BODY, fingerprint="a"),
                _sequence(_STATUS, _HEADER, fingerprint="b"),
                _sequence(_BODY, AssertionRole.GENERAL, fingerprint="c"),
            ]
        )
    )
    assert record.http_sequence_status_check_counts == (1, 1, 0)
    assert record.http_sequence_body_check_counts == (2, 0, 1)
    assert record.http_sequence_header_check_counts == (0, 1, 0)
    # General/exception roles are not body/header/status and stay uncounted, but
    # the total response-check length still includes them.
    assert record.http_sequence_response_check_lengths == (3, 2, 2)


# --- Per-sequence distribution conditions on bearing sequences ---


def test_per_sequence_distribution_is_conditioned_on_presence() -> None:
    records = _records(
        api_test(
            test_sequences=[
                _sequence(_BODY, fingerprint="a"),
                _sequence(_BODY, _BODY, _BODY, fingerprint="b"),
                _sequence(_STATUS, fingerprint="c"),  # no body
            ]
        )
    )
    body = clustering_stats.compute(records)["per_sequence_assertion_count"][
        "by_target"
    ]["body"]
    # Two of three sequences carry a body check; the empty one is excluded from
    # the per-bearing-sequence distribution but counts in the denominator.
    assert body["sequences_with_assertion"]["count"] == 2
    assert body["sequences_with_assertion"]["total"] == 3
    assert body["checks_per_bearing_sequence"]["count"] == 2
    assert body["checks_per_bearing_sequence"]["mean"] == pytest.approx(2.0)
    assert body["share_exactly_one_check"]["proportion"] == pytest.approx(0.5)
    assert body["share_two_or_more_checks"]["proportion"] == pytest.approx(0.5)


def test_concentration_reports_top_decile_topsidedness() -> None:
    # Ten header-bearing sequences: one holds 10 header checks, nine hold 1 each
    # (19 total). The top decile is one sequence holding 10/19 of all checks.
    counts = [10, 1, 1, 1, 1, 1, 1, 1, 1, 1]
    record = api_test(
        test_sequences=[
            _sequence(*([_HEADER] * count), fingerprint=f"s{index}")
            for index, count in enumerate(counts)
        ]
    )
    header = clustering_stats.compute(_records(record))["per_sequence_assertion_count"][
        "by_target"
    ]["header"]["concentration"]
    assert header["bearing_sequence_count"] == 10
    assert header["total_checks"] == 19
    assert header["top_decile_sequence_count"] == 1
    assert header["top_decile_share_of_checks"] == pytest.approx(10 / 19)
    # Decile holds 10/19 of checks on 1/10 of sequences: (10/19) / (1/10).
    assert header["evenness_ratio"] == pytest.approx((10 / 19) / 0.1)
    # Top 25% rounds up to three sequences holding 10 + 1 + 1 of the 19 checks.
    assert header["top_25pct_share_of_checks"] == pytest.approx(12 / 19)


# --- Blob vs spread classification ---


def test_blob_vs_spread_classifies_each_test() -> None:
    single = api_test(test_sequences=[_sequence(_BODY, fingerprint="s")])
    blob = api_test(test_sequences=[_sequence(_BODY, _BODY, _BODY, fingerprint="b")])
    spread = api_test(
        test_sequences=[
            _sequence(_BODY, fingerprint="x"),
            _sequence(_BODY, fingerprint="y"),
        ]
    )
    body = clustering_stats.compute(_records(single, blob, spread))["blob_vs_spread"][
        "body"
    ]
    assert body["test_count"] == 3
    assert body["single_check"]["count"] == 1
    assert body["concentrated_blob"]["count"] == 1
    assert body["spread_across_sequences"]["count"] == 1
    # Among the two multi-check tests, one is a blob and one is spread.
    assert body["multi_check_test_count"] == 2
    assert body["share_blob_given_multi_check"]["proportion"] == pytest.approx(0.5)
    assert body["max_checks_in_one_sequence"]["max"] == 3.0


# --- Co-occurrence ---


def test_sequence_level_co_occurrence_counts_and_lift() -> None:
    # Four sequences: one body+header, one body-only, one header-only, one status.
    record = api_test(
        test_sequences=[
            _sequence(_BODY, _HEADER, fingerprint="both"),
            _sequence(_BODY, fingerprint="bodyonly"),
            _sequence(_HEADER, fingerprint="headeronly"),
            _sequence(_STATUS, fingerprint="status"),
        ]
    )
    seq = clustering_stats.compute(_records(record))["co_occurrence"]["sequence_level"]
    assert seq["sequence_count"] == 4
    assert seq["body_and_header"]["count"] == 1
    assert seq["body_only"]["count"] == 1
    assert seq["header_only"]["count"] == 1
    # 2 body-bearing, 2 header-bearing sequences; both=1.
    assert seq["header_given_body"]["proportion"] == pytest.approx(0.5)
    assert seq["body_given_header"]["proportion"] == pytest.approx(0.5)
    # lift = both * n / (body * header) = 1 * 4 / (2 * 2) = 1.0
    assert seq["lift"] == pytest.approx(1.0)


def test_test_level_co_occurrence_uses_assertion_summary() -> None:
    both = api_test(
        assertion_summary=AssertionSummary(body_count=1, header_count=1, status_count=1)
    )
    body_only = api_test(assertion_summary=AssertionSummary(body_count=2))
    neither = api_test(assertion_summary=AssertionSummary(status_count=1))
    test_level = clustering_stats.compute(_records(both, body_only, neither))[
        "co_occurrence"
    ]["test_level"]
    assert test_level["test_count"] == 3
    assert test_level["tests_with_body"]["count"] == 2
    assert test_level["tests_with_header"]["count"] == 1
    assert test_level["body_and_header"]["count"] == 1
    assert test_level["body_given_header"]["proportion"] == pytest.approx(1.0)
    assert test_level["header_given_body"]["proportion"] == pytest.approx(0.5)


def test_non_api_tests_are_excluded() -> None:
    records = _records(
        api_test(test_sequences=[_sequence(_BODY, fingerprint="a")]),
        non_api_test(),
    )
    result = clustering_stats.compute(records)
    assert result["api_test_count"] == 1
    assert result["per_sequence_assertion_count"]["sequence_count"] == 1


def test_runner_emits_assertion_clustering_distribution() -> None:
    record = project_project(
        project(tests=[api_test(test_sequences=[_sequence(_BODY, fingerprint="a")])])
    )
    statistics = compute_all_statistics([record])
    assert ASSERTION_CLUSTERING_DISTRIBUTION in statistics
    payload = statistics[ASSERTION_CLUSTERING_DISTRIBUTION]
    assert payload["api_test_count"] == 1
    assert (
        payload["per_sequence_assertion_count"]["by_target"]["body"][
            "sequences_with_assertion"
        ]["count"]
        == 1
    )
