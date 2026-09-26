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
    StatusCodeDistribution,
)
from gerbil.statistics import assertion_verification as assertion_stats
from gerbil.statistics.records import project_test
from tests.statistics_builders import api_test, non_api_test

_SPAN = SourceSpan(start_line=1, start_column=1, end_line=1, end_column=2)
_ORIGIN = OriginContext(phase=LifecyclePhase.TEST, kind=CallSiteOriginKind.TEST_METHOD)


def _status_step(
    order: int, *, status_range: str | None = None, status_code: int | None = None
) -> ApiSequenceStep:
    return ApiSequenceStep(
        order=order,
        kind=SequenceStepKind.RESPONSE_CHECK,
        phase=LifecyclePhase.TEST,
        origin=_ORIGIN,
        method_name="status",
        source_span=_SPAN,
        assertion_role=AssertionRole.STATUS,
        status_range=status_range,
        status_code=status_code,
    )


def _status_sequence(order: int, *steps: ApiSequenceStep) -> HttpTestSequence:
    return HttpTestSequence(order=order, steps=list(steps), fingerprint=f"seq:{order}")


def _api_records():
    tests = [
        api_test(oracle_type_label="implicit"),
        api_test(
            assertion_summary=AssertionSummary(status_count=2, general_count=1),
            status_distribution=StatusCodeDistribution(range_2xx=2),
            status_code_counts={"200": 2},
            oracle_type_label="example-based",
        ),
        api_test(
            assertion_summary=AssertionSummary(body_count=3),
            oracle_type_label="contract",
        ),
        api_test(
            assertion_summary=AssertionSummary(header_count=1, exception_count=1),
            oracle_type_label="property-based",
            has_exception_assertion=True,
        ),
        api_test(
            assertion_summary=AssertionSummary(status_count=1, body_count=1),
            status_distribution=StatusCodeDistribution(range_2xx=1, range_4xx=1),
            status_code_counts={"201": 1, "404": 1},
            oracle_type_label="example-based",
        ),
        api_test(
            assertion_summary=AssertionSummary(status_count=1, header_count=1),
            status_distribution=StatusCodeDistribution(range_5xx=1),
            status_code_counts={"500": 1},
        ),
        api_test(
            assertion_summary=AssertionSummary(body_count=1, header_count=1),
            oracle_type_label="contract",
        ),
        api_test(
            assertion_summary=AssertionSummary(
                status_count=1, body_count=1, header_count=1
            ),
            status_distribution=StatusCodeDistribution(unknown=1),
            oracle_type_label="property-based",
        ),
        non_api_test(),
    ]
    return [project_test(test) for test in tests]


def test_empty_input_returns_zeroed_assertion_verification_payload() -> None:
    result = assertion_stats.compute([])

    assert result["scope"] == "api_tests"
    assert result["api_test_count"] == 0
    assert result["assertion_targets"]["countable_assertion_count"] == 0
    assert result["assertion_targets"]["by_target"]["status"]["proportion"] is None
    assert result["target_assertions_per_test"]["status"]["count"] == 0
    assert result["response_surface_combinations"]["total"] == 0
    assert result["oracle_types"] == {"total": 0, "by_type": {}}
    assert result["status_assertions"]["status_assertion_count"] == 0
    coverage = result["status_range_coverage"]
    assert coverage["resolved_assertion_range_share"]["status_assertion_count"] == 0
    assert (
        coverage["resolved_assertion_range_share"]["by_range"]["2xx"]["proportion"]
        is None
    )
    assert coverage["all_sequence_membership"]["sequence_count"] == 0
    assert coverage["all_sequence_membership"]["by_range"]["5xx"]["proportion"] is None
    assert (
        coverage["sequence_membership"]["resolved_status_bearing_sequence_count"] == 0
    )
    assert result["status_outcome_mix"]["test_count"] == 0
    assert result["status_outcome_mix"]["by_mix"]["2xx_only"]["proportion"] is None
    assert result["has_exception_assertion"]["proportion"] is None


def test_assertion_targets_use_countable_assertion_denominators() -> None:
    result = assertion_stats.compute(_api_records())
    targets = result["assertion_targets"]

    assert targets["countable_assertion_count"] == 17
    assert targets["response_surface_assertion_count"] == 15
    assert targets["excluded_countable_assertions"] == {"general": 1, "exception": 1}
    assert targets["by_target"]["status"] == {
        "count": 5,
        "total": 15,
        "proportion": pytest.approx(5 / 15),
        "proportion_of_countable_assertions": pytest.approx(5 / 17),
    }
    assert targets["by_target"]["body"]["count"] == 6
    assert targets["by_target"]["header"]["count"] == 4


def test_target_assertions_per_test_reports_separate_distributions() -> None:
    distributions = assertion_stats.compute(_api_records())[
        "target_assertions_per_test"
    ]

    assert distributions["status"]["count"] == 8
    assert distributions["status"]["min"] == 0.0
    assert distributions["status"]["max"] == 2.0
    assert distributions["status"]["mean"] == pytest.approx(5 / 8)
    assert distributions["status"]["p50"] == pytest.approx(0.5)
    assert distributions["body"]["mean"] == pytest.approx(6 / 8)
    assert distributions["body"]["p90"] == pytest.approx(1.6)
    assert distributions["header"]["mean"] == pytest.approx(4 / 8)
    assert distributions["header"]["p75"] == pytest.approx(1.0)


def test_response_surface_combinations_partition_api_tests() -> None:
    combinations = assertion_stats.compute(_api_records())[
        "response_surface_combinations"
    ]

    assert combinations["total"] == 8
    assert {
        label: entry["count"] for label, entry in combinations["by_combination"].items()
    } == {
        "none": 1,
        "status-only": 1,
        "body-only": 1,
        "header-only": 1,
        "status+body": 1,
        "status+header": 1,
        "body+header": 1,
        "status+body+header": 1,
    }
    assert (
        sum(entry["count"] for entry in combinations["by_combination"].values())
        == combinations["total"]
    )


def test_oracle_types_partition_api_tests() -> None:
    oracle_types = assertion_stats.compute(_api_records())["oracle_types"]

    assert oracle_types["total"] == 8
    assert {
        label: entry["count"] for label, entry in oracle_types["by_type"].items()
    } == {
        "contract": 2,
        "example-based": 2,
        "implicit": 2,
        "property-based": 2,
    }
    assert sum(entry["count"] for entry in oracle_types["by_type"].values()) == 8


def test_status_assertions_report_counts_and_overlapping_test_flags() -> None:
    result = assertion_stats.compute(_api_records())
    status = result["status_assertions"]

    assert status["status_assertion_count"] == 6
    assert status["status_asserted_test_count"] == 4
    assert status["range_assertion_counts"]["2xx"] == {
        "count": 3,
        "total": 6,
        "proportion": pytest.approx(0.5),
    }
    assert status["range_assertion_counts"]["4xx"]["count"] == 1
    assert status["range_assertion_counts"]["5xx"]["count"] == 1
    assert status["range_assertion_counts"]["unknown"]["count"] == 1
    assert status["tests_with_range"]["2xx"] == {
        "count": 2,
        "total": 8,
        "proportion": pytest.approx(0.25),
    }
    assert status["tests_with_range"]["4xx"]["count"] == 1
    assert status["tests_with_range"]["5xx"] == {
        "count": 1,
        "total": 8,
        "proportion": pytest.approx(0.125),
    }
    assert status["tests_with_range_among_status_asserted"]["5xx"] == {
        "count": 1,
        "total": 4,
        "proportion": pytest.approx(0.25),
    }
    assert status["exact_status_code_assertion_count"] == 5
    assert status["exact_status_code_assertion_counts"]["200"]["count"] == 2
    assert status["exact_status_code_assertion_counts"]["404"]["total"] == 5
    assert status["tests_with_exact_status_code"]["200"] == {
        "count": 1,
        "total": 8,
        "proportion": pytest.approx(0.125),
    }


def test_exception_assertion_test_share_is_reported() -> None:
    result = assertion_stats.compute(_api_records())

    # 2xx/4xx/5xx test coverage is reported via status_assertions.tests_with_range;
    # exceptions are not a status range, so they get their own per-test share.
    assert result["has_exception_assertion"] == {
        "count": 1,
        "total": 8,
        "proportion": pytest.approx(0.125),
    }


def test_status_outcome_mix_partitions_tests_by_success_error_ranges() -> None:
    mix = assertion_stats.compute(_api_records())["status_outcome_mix"]

    # Only the four tests with a status assertion are in scope (2xx-only, 2xx+4xx,
    # 5xx-only, and an unknown-only test that lands in neither).
    assert mix["scope"] == "api_tests_with_status_assertion"
    assert mix["test_count"] == 4
    assert {label: entry["count"] for label, entry in mix["by_mix"].items()} == {
        "2xx_only": 1,
        "4xx_only": 0,
        "5xx_only": 1,
        "4xx_and_5xx_only": 0,
        "success_and_error": 1,
        "neither_success_nor_error": 1,
    }
    assert sum(entry["count"] for entry in mix["by_mix"].values()) == mix["test_count"]
    breakdown = mix["success_and_error_breakdown"]
    assert breakdown["success_and_client_error"] == {
        "count": 1,
        "total": 1,
        "proportion": pytest.approx(1.0),
    }
    assert breakdown["success_and_server_error"]["count"] == 0


def test_status_range_coverage_assertion_share_and_sequence_membership() -> None:
    tests = [
        # One multi-range sequence (200 + 404) and one single-2xx sequence.
        api_test(
            test_sequences=[
                _status_sequence(
                    1,
                    _status_step(1, status_code=200),
                    _status_step(2, status_code=404),
                ),
                _status_sequence(2, _status_step(3, status_range="2xx")),
            ]
        ),
        # An unknown-only sequence (excluded from resolved denominators) and a 5xx.
        api_test(
            test_sequences=[
                _status_sequence(1, _status_step(1)),
                _status_sequence(2, _status_step(2, status_code=500)),
            ]
        ),
        non_api_test(),
    ]
    coverage = assertion_stats.compute([project_test(test) for test in tests])[
        "status_range_coverage"
    ]

    assertion_share = coverage["resolved_assertion_range_share"]
    assert assertion_share["status_assertion_count"] == 5
    assert assertion_share["unknown_assertion_count"] == 1
    assert assertion_share["resolved_assertion_count"] == 4
    assert assertion_share["by_range"]["2xx"] == {
        "count": 2,
        "total": 4,
        "proportion": pytest.approx(0.5),
    }
    assert assertion_share["by_range"]["4xx"]["count"] == 1
    assert assertion_share["by_range"]["5xx"]["count"] == 1
    # Resolved buckets partition the resolved assertions.
    assert (
        sum(entry["count"] for entry in assertion_share["by_range"].values())
        == assertion_share["resolved_assertion_count"]
    )

    all_sequences = coverage["all_sequence_membership"]
    assert all_sequences["scope"] == "http_test_sequences"
    assert all_sequences["sequence_count"] == 4
    assert all_sequences["by_range"]["2xx"] == {
        "count": 2,
        "total": 4,
        "proportion": pytest.approx(0.5),
    }
    assert all_sequences["by_range"]["5xx"] == {
        "count": 1,
        "total": 4,
        "proportion": pytest.approx(0.25),
    }

    membership = coverage["sequence_membership"]
    assert membership["status_bearing_sequence_count"] == 4
    assert membership["resolved_status_bearing_sequence_count"] == 3
    assert membership["by_range"]["2xx"]["count"] == 2
    assert membership["by_range"]["4xx"]["count"] == 1
    assert membership["by_range"]["5xx"]["count"] == 1
    # Multi-range sequences count in every range they assert, so the membership
    # counts overlap and exceed the resolved-bearing sequence count.
    assert (
        sum(entry["count"] for entry in membership["by_range"].values())
        > membership["resolved_status_bearing_sequence_count"]
    )
