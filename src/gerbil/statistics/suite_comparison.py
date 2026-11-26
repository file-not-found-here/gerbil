"""Significance tests and effect sizes for how a baseline suite differs from each
compared suite on the measures of the paper's comparison table, read from the
statistics payloads that table is drawn from."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from gerbil.statistics import assertion_clustering as assertion_clustering_stats
from gerbil.statistics import http_sequences as http_sequences_stats
from gerbil.statistics.distributions import Share, count_share, summarize
from gerbil.statistics.records import ProjectStatsRecord, TestRecord
from gerbil.statistics.runner import (
    ASSERTION_CLUSTERING_DISTRIBUTION,
    ASSERTION_VERIFICATION_DISTRIBUTION,
    HTTP_BEHAVIOR_LOCATION,
    HTTP_TEST_SEQUENCE_DISTRIBUTION,
    REQUEST_CONSTRUCTION_DISTRIBUTION,
    RESOURCE_INTERACTION_DISTRIBUTION,
    TEST_SCOPE_DISTRIBUTION,
    compute_all_statistics,
)

SUITE_COMPARISON = "suite_comparison"

ALPHA = 0.05

MAGNITUDES: tuple[str, ...] = ("negligible", "small", "medium", "large")
# Lower bounds of small, medium, and large: Cohen (1988) for h, and Romano et
# al. (2006) for Cliff's delta.
COHENS_H_THRESHOLDS: tuple[float, float, float] = (0.2, 0.5, 0.8)
CLIFFS_DELTA_THRESHOLDS: tuple[float, float, float] = (0.147, 0.33, 0.474)

_PROPORTION = "proportion"
_DISTRIBUTION = "distribution"
_DESCRIPTIVE = "descriptive"


@dataclass(frozen=True)
class Suite:
    """A named test suite: its statistics payloads and its API tests."""

    name: str
    project_count: int
    statistics: Mapping[str, Mapping[str, Any]]
    api_tests: Sequence[TestRecord]


def build_suite(name: str, records: Sequence[ProjectStatsRecord]) -> Suite:
    return Suite(
        name=name,
        project_count=len(records),
        statistics=compute_all_statistics(records),
        api_tests=[
            test for record in records for test in record.tests if test.is_api_test
        ],
    )


@dataclass(frozen=True)
class ProportionMeasure:
    """A share of units with a property; compared with Fisher's exact test."""

    kind: ClassVar[str] = _PROPORTION
    name: str
    label: str
    unit: str
    share: Callable[[Suite], Share]


@dataclass(frozen=True)
class DistributionMeasure:
    """A count per unit; compared with the Mann-Whitney U test."""

    kind: ClassVar[str] = _DISTRIBUTION
    name: str
    label: str
    unit: str
    values: Callable[[Suite], Sequence[int]]


@dataclass(frozen=True)
class DescriptiveMeasure:
    """A single aggregate per suite, which no test applies to."""

    kind: ClassVar[str] = _DESCRIPTIVE
    name: str
    label: str
    value: Callable[[Suite], float | None]


Measure = ProportionMeasure | DistributionMeasure | DescriptiveMeasure


def _payload(suite: Suite, stem: str, path: tuple[str, ...]) -> Any:
    node: Any = suite.statistics[stem]
    for key in path:
        node = node[key]
    return node


def _share(stem: str, *path: str, complement: bool = False) -> Callable[[Suite], Share]:
    """Reads a count/total entry of a payload, or its complement."""

    def read(suite: Suite) -> Share:
        entry = _payload(suite, stem, path)
        count = entry["total"] - entry["count"] if complement else entry["count"]
        return count_share(count, entry["total"])

    return read


def _value(stem: str, *path: str) -> Callable[[Suite], float | None]:
    def read(suite: Suite) -> float | None:
        value: float | None = _payload(suite, stem, path)
        return value

    return read


def _single_resource_tests(suite: Suite) -> Share:
    split = suite.statistics[TEST_SCOPE_DISTRIBUTION]["focal_resource_split"]
    return count_share(split["single_resource"]["test_count"], split["test_count"])


_BODY_CHECKS = ("per_sequence_assertion_count", "by_target", "body")
_HEADER_CHECKS = ("per_sequence_assertion_count", "by_target", "header")
_MULTI_STEP = ("multi_sequence_composition", "fully_resolved_multi_sequence")

# The rows of the paper's comparison table, in its order.
MEASURES: tuple[Measure, ...] = (
    DistributionMeasure(
        "request_builders_per_step",
        "Request builders per step",
        "steps",
        lambda suite: http_sequences_stats.request_builder_counts_per_dispatch(
            suite.api_tests
        ),
    ),
    ProportionMeasure(
        "steps_setting_header",
        "Steps setting a header",
        "steps",
        _share(
            REQUEST_CONSTRUCTION_DISTRIBUTION, "event_surfaces", "headers", "events"
        ),
    ),
    DescriptiveMeasure(
        "distinct_header_names",
        "Distinct header names",
        _value(
            REQUEST_CONSTRUCTION_DISTRIBUTION,
            "header_vocabulary",
            "distinct_header_name_count",
        ),
    ),
    ProportionMeasure(
        "header_uses_content_negotiation",
        "Header uses: content negotiation",
        "header uses",
        _share(
            REQUEST_CONSTRUCTION_DISTRIBUTION,
            "header_vocabulary",
            "header_name_occurrences",
            "content_negotiation",
        ),
    ),
    ProportionMeasure(
        "steps_with_response_check",
        "Steps with a response check",
        "steps",
        _share(
            HTTP_TEST_SEQUENCE_DISTRIBUTION,
            "verification_concentration",
            "share_zero_checks",
            complement=True,
        ),
    ),
    ProportionMeasure(
        "tests_checking_only_status",
        "Tests checking only the status",
        "tests",
        _share(
            ASSERTION_VERIFICATION_DISTRIBUTION,
            "response_surface_combinations",
            "by_combination",
            "status-only",
        ),
    ),
    ProportionMeasure(
        "tests_checking_status_body_headers",
        "Tests checking status, body, headers",
        "tests",
        _share(
            ASSERTION_VERIFICATION_DISTRIBUTION,
            "response_surface_combinations",
            "by_combination",
            "status+body+header",
        ),
    ),
    DistributionMeasure(
        "body_checks_per_checking_step",
        "Body checks per checking step",
        "steps with a body check",
        lambda suite: assertion_clustering_stats.bearing_sequence_check_counts(
            suite.api_tests, "body"
        ),
    ),
    DescriptiveMeasure(
        "most_body_checks_in_one_step",
        "Most body checks in one step",
        _value(
            ASSERTION_CLUSTERING_DISTRIBUTION,
            *_BODY_CHECKS,
            "checks_per_bearing_sequence",
            "max",
        ),
    ),
    DescriptiveMeasure(
        "body_checks_in_top_decile_of_steps",
        "Body checks in top decile of steps",
        _value(
            ASSERTION_CLUSTERING_DISTRIBUTION,
            *_BODY_CHECKS,
            "concentration",
            "top_decile_share_of_checks",
        ),
    ),
    ProportionMeasure(
        "header_checking_steps_with_two_or_more_checks",
        "Header-checking steps, 2+ checks",
        "steps with a header check",
        _share(
            ASSERTION_CLUSTERING_DISTRIBUTION,
            *_HEADER_CHECKS,
            "share_two_or_more_checks",
        ),
    ),
    ProportionMeasure(
        "status_assertions_expecting_4xx",
        "Status assertions expecting 4xx",
        "status assertions",
        _share(
            ASSERTION_VERIFICATION_DISTRIBUTION,
            "status_range_coverage",
            "resolved_assertion_range_share",
            "by_range",
            "4xx",
        ),
    ),
    ProportionMeasure(
        "status_assertions_expecting_5xx",
        "Status assertions expecting 5xx",
        "status assertions",
        _share(
            ASSERTION_VERIFICATION_DISTRIBUTION,
            "status_range_coverage",
            "resolved_assertion_range_share",
            "by_range",
            "5xx",
        ),
    ),
    ProportionMeasure(
        "tests_mixing_success_and_error",
        "Tests mixing success and error",
        "tests asserting a status",
        _share(
            ASSERTION_VERIFICATION_DISTRIBUTION,
            "status_outcome_mix",
            "by_mix",
            "success_and_error",
        ),
    ),
    ProportionMeasure(
        "single_step_tests",
        "Single-step tests",
        "tests",
        _share(
            HTTP_TEST_SEQUENCE_DISTRIBUTION,
            "multi_sequence_composition",
            "share_one_sequence",
        ),
    ),
    ProportionMeasure(
        "tests_with_five_or_more_steps",
        "Tests with 5+ steps",
        "tests",
        _share(
            HTTP_TEST_SEQUENCE_DISTRIBUTION,
            "multi_sequence_composition",
            "share_five_or_more_sequences",
        ),
    ),
    ProportionMeasure(
        "tests_with_ten_or_more_steps",
        "Tests with 10+ steps",
        "tests",
        _share(
            HTTP_TEST_SEQUENCE_DISTRIBUTION,
            "multi_sequence_composition",
            "share_ten_or_more_sequences",
        ),
    ),
    ProportionMeasure(
        "tests_confined_to_one_resource",
        "Tests confined to one resource",
        "tests",
        _single_resource_tests,
    ),
    ProportionMeasure(
        "multi_step_tests_redispatching_endpoint",
        "Multi-step: re-dispatch an endpoint",
        "multi-step tests",
        _share(HTTP_TEST_SEQUENCE_DISTRIBUTION, *_MULTI_STEP, "re_dispatches_endpoint"),
    ),
    ProportionMeasure(
        "multi_step_tests_with_duplicate_step",
        "Multi-step: contain a duplicate step",
        "multi-step tests",
        _share(HTTP_TEST_SEQUENCE_DISTRIBUTION, *_MULTI_STEP, "has_duplicate_sequence"),
    ),
    ProportionMeasure(
        "tests_with_fixture_request",
        "Tests with a fixture request",
        "tests",
        _share(
            HTTP_BEHAVIOR_LOCATION, "by_origin", "fixture", "tests_with_http_events"
        ),
    ),
    ProportionMeasure(
        "writing_tests_with_read_after_write",
        "Mutating tests: read after write",
        "tests writing a resource, in projects with endpoints",
        _share(
            RESOURCE_INTERACTION_DISTRIBUTION,
            "per_test",
            "read_after_write_among_writing_tests",
        ),
    ),
)


def cohens_h(first: float, second: float) -> float:
    """Cohen's h between two proportions; positive when the first is larger."""
    return 2 * math.asin(math.sqrt(first)) - 2 * math.asin(math.sqrt(second))


def cliffs_delta(u_statistic: float, first_count: int, second_count: int) -> float:
    """Cliff's delta from the first sample's Mann-Whitney U: the share of pairs
    in which the first sample is larger minus the share in which it is smaller."""
    return 2 * u_statistic / (first_count * second_count) - 1


def magnitude(effect_size: float, thresholds: tuple[float, float, float]) -> str:
    return MAGNITUDES[sum(abs(effect_size) >= threshold for threshold in thresholds)]


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values, in input order."""
    order = sorted(range(len(p_values)), key=lambda index: p_values[index])
    adjusted = [0.0] * len(p_values)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def _tested(
    test_name: str,
    p_value: float,
    effect_name: str,
    effect_size: float,
    thresholds: tuple[float, float, float],
) -> dict[str, Any]:
    return {
        "test": {"name": test_name, "p_value": p_value},
        "effect_size": {
            "name": effect_name,
            "value": effect_size,
            "magnitude": magnitude(effect_size, thresholds),
        },
    }


_UNTESTED: Mapping[str, None] = {"test": None, "effect_size": None}


def _proportion_comparison(baseline: Share, other: Share) -> dict[str, Any]:
    if baseline.proportion is None or other.proportion is None:
        return {**other.to_dict(), **_UNTESTED}
    # Imported lazily: scipy.stats takes ~1s to import, and the batch commands
    # re-import the CLI (and with it this package) in every spawned worker.
    from scipy.stats import fisher_exact

    table = [
        [baseline.count, baseline.total - baseline.count],
        [other.count, other.total - other.count],
    ]
    return other.to_dict() | _tested(
        "fisher_exact",
        float(fisher_exact(table, alternative="two-sided").pvalue),
        "cohens_h",
        cohens_h(baseline.proportion, other.proportion),
        COHENS_H_THRESHOLDS,
    )


def _distribution_value(values: Sequence[int]) -> dict[str, Any]:
    summary = summarize(values)
    return {"count": summary.count, "mean": summary.mean, "median": summary.p50}


def mann_whitney_comparison(
    first: Sequence[float], second: Sequence[float]
) -> dict[str, Any]:
    """The two-sided Mann-Whitney U test of two samples with its Cliff's delta,
    positive when the first sample tends to be larger; untested if one is empty."""
    if not first or not second:
        return dict(_UNTESTED)
    from scipy.stats import mannwhitneyu

    result = mannwhitneyu(first, second, alternative="two-sided")
    return _tested(
        "mann_whitney_u",
        float(result.pvalue),
        "cliffs_delta",
        cliffs_delta(float(result.statistic), len(first), len(second)),
        CLIFFS_DELTA_THRESHOLDS,
    )


def _distribution_comparison(
    baseline: Sequence[int], other: Sequence[int]
) -> dict[str, Any]:
    return _distribution_value(other) | mann_whitney_comparison(baseline, other)


def _measure_entry(
    measure: Measure, baseline: Suite, suites: Sequence[Suite]
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": measure.name,
        "label": measure.label,
        "kind": measure.kind,
    }
    if isinstance(measure, ProportionMeasure):
        baseline_share = measure.share(baseline)
        return entry | {
            "unit": measure.unit,
            "baseline": baseline_share.to_dict(),
            "comparisons": {
                suite.name: _proportion_comparison(baseline_share, measure.share(suite))
                for suite in suites
            },
        }
    if isinstance(measure, DistributionMeasure):
        baseline_values = measure.values(baseline)
        return entry | {
            "unit": measure.unit,
            "baseline": _distribution_value(baseline_values),
            "comparisons": {
                suite.name: _distribution_comparison(
                    baseline_values, measure.values(suite)
                )
                for suite in suites
            },
        }
    return entry | {
        "baseline": {"value": measure.value(baseline)},
        "comparisons": {
            suite.name: {"value": measure.value(suite)} for suite in suites
        },
    }


def compute(
    baseline: Suite, suites: Sequence[Suite], *, alpha: float = ALPHA
) -> dict[str, Any]:
    """Serializable payload: every measure's value per suite and, where a test
    applies, the baseline-vs-suite test and effect size, with the p-values
    Holm-adjusted over all tested comparisons."""
    names = [baseline.name, *(suite.name for suite in suites)]
    if len(set(names)) != len(names):
        raise ValueError(f"suite names must be distinct: {', '.join(names)}")

    measures = [_measure_entry(measure, baseline, suites) for measure in MEASURES]
    tested = [
        (entry["name"], suite_name, comparison)
        for entry in measures
        for suite_name, comparison in entry["comparisons"].items()
        if comparison.get("test") is not None
    ]
    adjusted = holm_adjust(
        [comparison["test"]["p_value"] for _, _, comparison in tested]
    )
    for (_, _, comparison), p_adjusted in zip(tested, adjusted):
        comparison["test"]["p_adjusted"] = p_adjusted
        comparison["test"]["significant"] = p_adjusted < alpha

    magnitude_counts = {name: 0 for name in MAGNITUDES}
    not_significant: list[dict[str, str]] = []
    significant_but_negligible: list[dict[str, str]] = []
    for measure_name, suite_name, comparison in tested:
        effect_magnitude = comparison["effect_size"]["magnitude"]
        magnitude_counts[effect_magnitude] += 1
        reference = {"measure": measure_name, "suite": suite_name}
        if not comparison["test"]["significant"]:
            not_significant.append(reference)
        elif effect_magnitude == MAGNITUDES[0]:
            significant_but_negligible.append(reference)

    return {
        "baseline": baseline.name,
        "suites": [suite.name for suite in suites],
        "suite_sizes": {
            suite.name: {
                "project_count": suite.project_count,
                "api_test_count": len(suite.api_tests),
            }
            for suite in (baseline, *suites)
        },
        "method": {
            _PROPORTION: {
                "test": "fisher_exact",
                "effect_size": "cohens_h",
                "effect_sign": "positive when the baseline share is higher",
                "magnitude_thresholds": dict(zip(MAGNITUDES[1:], COHENS_H_THRESHOLDS)),
            },
            _DISTRIBUTION: {
                "test": "mann_whitney_u",
                "effect_size": "cliffs_delta",
                "effect_sign": (
                    "positive when baseline observations tend to be larger; the "
                    "means can order the other way"
                ),
                "magnitude_thresholds": dict(
                    zip(MAGNITUDES[1:], CLIFFS_DELTA_THRESHOLDS)
                ),
            },
            "alternative": "two-sided",
            "correction": "holm",
            "alpha": alpha,
            "untested": (
                "A comparison with an empty sample in either suite has no test or "
                "effect size and is left out of the correction; descriptive "
                "measures are single aggregates that no test applies to."
            ),
            "limitations": (
                "Observations are pooled over a suite's projects and treated as "
                "independent, although those of one test or project are related, "
                "so the p-values are optimistic. A p-value below the smallest "
                "positive float is reported as 0.0."
            ),
        },
        "summary": {
            "tested_comparison_count": len(tested),
            "significant_count": len(tested) - len(not_significant),
            "magnitude_counts": magnitude_counts,
            "not_significant": not_significant,
            "significant_but_negligible": significant_but_negligible,
        },
        "measures": measures,
    }
