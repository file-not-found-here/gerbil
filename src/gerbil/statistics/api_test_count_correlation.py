"""Spearman correlations between each project's API test count and its per-project
testing-practice rates, bounding how selecting projects by API test count can bias
the developer baseline of a comparison; meaningful over a full project corpus."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from gerbil.statistics.http_sequences import count_consecutive_transitions
from gerbil.statistics.records import (
    STATUS_RANGE_KEYS,
    ProjectStatsRecord,
    TestRecord,
    api_test_count,
    request_event_total,
)

# Scope: projects with >= 10 API tests and >= 1 identified application endpoint.
# Below 10 API tests a per-project rate swings by 10+ points on a single test, and
# such a project could never serve as a developer baseline; projects without
# endpoints (mostly libraries and frameworks) have no surface for the coverage
# measures. Admitting the small projects inflates every correlation, because
# near-empty API suites score ~0 on most measures. The scope also guarantees a
# non-zero denominator for the API-test rates and endpoint coverage.
MIN_API_TEST_COUNT = 10
MIN_ENDPOINT_COUNT = 1
SCOPE = "projects_with_min_api_tests_and_endpoints"
SCOPE_DESCRIPTION = (
    f"Projects with at least {MIN_API_TEST_COUNT} API tests and at least "
    f"{MIN_ENDPOINT_COUNT} identified application endpoint."
)

_ERROR_STATUS_INDICES: tuple[int, ...] = (
    STATUS_RANGE_KEYS.index("4xx"),
    STATUS_RANGE_KEYS.index("5xx"),
)

# Spearman's p-value uses a t-distribution with n - 2 degrees of freedom.
_MIN_CORRELATION_SAMPLE = 3


def _api_tests(record: ProjectStatsRecord) -> list[TestRecord]:
    return [test for test in record.tests if test.is_api_test]


def _api_test_rate(
    predicate: Callable[[TestRecord], bool],
) -> Callable[[ProjectStatsRecord], float]:
    def rate(record: ProjectStatsRecord) -> float:
        api_tests = _api_tests(record)
        return sum(1 for test in api_tests if predicate(test)) / len(api_tests)

    return rate


def _http_verb_repetition_rate(record: ProjectStatsRecord) -> float | None:
    _, mapped_pairs, transitions = count_consecutive_transitions(
        _api_tests(record), lambda test: test.http_sequence_verb_operations
    )
    if mapped_pairs == 0:
        return None
    repeats = sum(
        count for (source, target), count in transitions.items() if source == target
    )
    return repeats / mapped_pairs


def _endpoint_coverage(record: ProjectStatsRecord) -> float:
    covered = sum(
        1 for endpoint in record.endpoints if endpoint.covering_test_count > 0
    )
    return covered / len(record.endpoints)


def _simple_1_way_optional_coverage(record: ProjectStatsRecord) -> float | None:
    values = [
        entry.simple_1_way_optional_coverage
        for entry in record.endpoint_parameters
        if entry.route_covering_test_count > 0
        and entry.simple_1_way_optional_coverage is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


@dataclass(frozen=True)
class CorrelationMeasure:
    """One per-project measure correlated against API test count."""

    name: str
    description: str
    value: Callable[[ProjectStatsRecord], float | None]


MEASURES: tuple[CorrelationMeasure, ...] = (
    CorrelationMeasure(
        "header_check_rate",
        "Share of API tests asserting on at least one response header.",
        _api_test_rate(lambda test: test.assertion_header_count > 0),
    ),
    CorrelationMeasure(
        "body_verification_rate",
        "Share of API tests asserting on the response body.",
        _api_test_rate(lambda test: test.assertion_body_count > 0),
    ),
    CorrelationMeasure(
        "multi_request_rate",
        "Share of API tests dispatching at least two HTTP request events.",
        _api_test_rate(lambda test: request_event_total(test) >= 2),
    ),
    CorrelationMeasure(
        "error_status_assertion_rate",
        "Share of API tests asserting at least one 4xx or 5xx status.",
        _api_test_rate(
            lambda test: any(
                test.status_range_counts[index] > 0 for index in _ERROR_STATUS_INDICES
            )
        ),
    ),
    CorrelationMeasure(
        "state_verification_rate",
        "Share of API tests that write a resource through the API and then read "
        "the same resource back.",
        _api_test_rate(lambda test: test.has_read_after_write),
    ),
    CorrelationMeasure(
        "http_verb_repetition_rate",
        "Share of consecutive HTTP test-sequence pairs (within a test) that repeat "
        "the same HTTP verb; undefined for projects without such pairs.",
        _http_verb_repetition_rate,
    ),
    CorrelationMeasure(
        "endpoint_coverage",
        "Share of application endpoints covered by at least one test.",
        _endpoint_coverage,
    ),
    CorrelationMeasure(
        "simple_1_way_optional_coverage",
        "Mean simple 1-way optional-parameter coverage over covered endpoints; "
        "undefined for projects without a covered endpoint declaring an optional "
        "parameter.",
        _simple_1_way_optional_coverage,
    ),
)


def is_in_scope(record: ProjectStatsRecord) -> bool:
    return (
        api_test_count(record) >= MIN_API_TEST_COUNT
        and len(record.endpoints) >= MIN_ENDPOINT_COUNT
    )


def spearman_correlation(
    xs: Sequence[float], ys: Sequence[float]
) -> tuple[float | None, float | None]:
    """Two-sided Spearman (rho, p), or (None, None) when undefined: fewer than
    three samples, or a constant input (no ranking to correlate)."""
    # Imported lazily: scipy.stats takes ~1s to import, and the batch commands
    # re-import the CLI (and with it this package) in every spawned worker.
    from scipy.stats import spearmanr

    if len(xs) < _MIN_CORRELATION_SAMPLE or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None, None
    result = spearmanr(xs, ys)
    return float(result.statistic), float(result.pvalue)


def _project_row(record: ProjectStatsRecord) -> dict[str, Any]:
    return {
        "dataset_name": record.dataset_name,
        "api_test_count": api_test_count(record),
        "endpoint_count": len(record.endpoints),
        "measures": {measure.name: measure.value(record) for measure in MEASURES},
    }


def compute(records: Sequence[ProjectStatsRecord]) -> dict[str, Any]:
    """Serializable payload: the scope, the per-measure correlations against API
    test count, and the in-scope per-project values they were computed from."""
    rows = sorted(
        (_project_row(record) for record in records if is_in_scope(record)),
        key=lambda row: (-row["api_test_count"], row["dataset_name"]),
    )

    correlations: dict[str, Any] = {}
    for measure in MEASURES:
        defined = [row for row in rows if row["measures"][measure.name] is not None]
        rho, p_value = spearman_correlation(
            [row["api_test_count"] for row in defined],
            [row["measures"][measure.name] for row in defined],
        )
        correlations[measure.name] = {
            "description": measure.description,
            "project_count": len(defined),
            "rho": rho,
            "p_value": p_value,
        }

    return {
        "scope": SCOPE,
        "scope_description": SCOPE_DESCRIPTION,
        "min_api_test_count": MIN_API_TEST_COUNT,
        "min_endpoint_count": MIN_ENDPOINT_COUNT,
        "analyzed_project_count": len(records),
        "project_count": len(rows),
        "method": {
            "statistic": "spearman_rank_correlation",
            "x": "api_test_count",
            "y": "per-project measure value",
            "alternative": "two-sided",
            "implementation": "scipy.stats.spearmanr",
            "undefined_values": (
                "A project whose measure is undefined is omitted from that "
                "measure's correlation; project_count is the n for each measure."
            ),
        },
        "correlations": correlations,
        "projects": rows,
    }
