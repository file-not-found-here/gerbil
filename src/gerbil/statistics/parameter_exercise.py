"""Coverage of endpoint parameter exercise: the share of endpoints with a
covering test, plus holistic and per-source exercise-rate distributions over
the covered subpopulation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from collections.abc import Iterable

from gerbil.analysis.schema import EndpointParameterSource
from gerbil.statistics.distributions import share, summarize
from gerbil.statistics.records import EndpointParameterRecord

# Canonical source order for stable per-source output; sources with no observed
# (non-None) value are omitted from each metric rather than emitted as empty.
_SOURCE_ORDER: tuple[str, ...] = tuple(
    source.value for source in EndpointParameterSource
)

# Per-endpoint rates spanning the whole binding surface.
_HOLISTIC_METRICS: tuple[
    tuple[str, Callable[[EndpointParameterRecord], float | None]], ...
] = (
    ("exercise_rate", lambda record: record.exercise_rate),
    ("optional_exercise_rate", lambda record: record.optional_exercise_rate),
    ("required_exercise_rate", lambda record: record.required_exercise_rate),
    (
        "simple_1_way_optional_coverage",
        lambda record: record.simple_1_way_optional_coverage,
    ),
    (
        "simple_2_way_optional_coverage",
        lambda record: record.simple_2_way_optional_coverage,
    ),
    (
        "total_2_way_optional_coverage",
        lambda record: record.total_2_way_optional_coverage,
    ),
)

# Rates projected onto a single parameter source.
_BY_SOURCE_METRICS: tuple[
    tuple[str, Callable[[EndpointParameterRecord], dict[str, float | None]]], ...
] = (
    ("exercise_rate", lambda record: record.exercise_rate_by_source),
    ("optional_exercise_rate", lambda record: record.optional_exercise_rate_by_source),
    (
        "simple_1_way_optional_coverage",
        lambda record: record.simple_1_way_optional_coverage_by_source,
    ),
    (
        "simple_2_way_optional_coverage",
        lambda record: record.simple_2_way_optional_coverage_by_source,
    ),
    (
        "total_2_way_optional_coverage",
        lambda record: record.total_2_way_optional_coverage_by_source,
    ),
)


def _holistic_distributions(
    records: Sequence[EndpointParameterRecord],
) -> dict[str, Any]:
    distributions: dict[str, Any] = {}
    for name, accessor in _HOLISTIC_METRICS:
        # None denominators are N/A, not 0%, so they never enter the sample.
        values = [
            rate
            for rate in (accessor(record) for record in records)
            if rate is not None
        ]
        distributions[name] = summarize(values).to_dict()
    return distributions


def _by_source_distributions(
    records: Sequence[EndpointParameterRecord],
) -> dict[str, Any]:
    distributions: dict[str, Any] = {}
    for name, accessor in _BY_SOURCE_METRICS:
        per_source: dict[str, Any] = {}
        for source in _SOURCE_ORDER:
            values = [
                rate
                for rate in (accessor(record).get(source) for record in records)
                if rate is not None
            ]
            if values:
                per_source[source] = summarize(values).to_dict()
        distributions[name] = per_source
    return distributions


def _pooled_optional_exercise_by_source(
    records: Sequence[EndpointParameterRecord],
) -> dict[str, Any]:
    """Parameter-level (micro) optional exercise rate per source.

    Pools every optional parameter across the covered endpoints—summing
    exercised and total counts before dividing—so each parameter weighs equally,
    in contrast to the per-endpoint (macro) mean of `optional_exercise_rate`,
    where every endpoint weighs equally regardless of how many optional
    parameters it declares.
    """
    pooled: dict[str, Any] = {}
    for source in _SOURCE_ORDER:
        exercised = sum(
            record.optional_exercised_count_by_source.get(source, 0)
            for record in records
        )
        total = sum(
            record.optional_count_by_source.get(source, 0) for record in records
        )
        if total == 0:
            continue
        pooled[source] = {
            "exercised_parameter_count": exercised,
            "total_parameter_count": total,
            "exercise_rate": exercised / total,
        }
    return pooled


def _optional_exercise_extremes(
    records: Sequence[EndpointParameterRecord],
) -> dict[str, Any]:
    """Share of endpoints exercising all vs. none of their optional parameters.

    The per-endpoint optional exercise rate is bimodal at 0 and 1, which the
    mean/percentile summaries obscure; each share is taken over endpoints that
    declare at least one optional parameter (of the given source), i.e. the same
    non-None denominator as the matching `optional_exercise_rate` distribution.
    """

    def extremes(rates: Iterable[float | None]) -> dict[str, Any]:
        present = [rate for rate in rates if rate is not None]
        return {
            "fully_exercised": share(rate >= 1.0 for rate in present).to_dict(),
            "none_exercised": share(rate <= 0.0 for rate in present).to_dict(),
        }

    by_source: dict[str, Any] = {}
    for source in _SOURCE_ORDER:
        rates = [
            record.optional_exercise_rate_by_source.get(source) for record in records
        ]
        if any(rate is not None for rate in rates):
            by_source[source] = extremes(rates)
    return {
        "holistic": extremes(record.optional_exercise_rate for record in records),
        "by_source": by_source,
    }


def _condition_payload(records: Sequence[EndpointParameterRecord]) -> dict[str, Any]:
    return {
        "endpoint_count": len(records),
        "holistic": _holistic_distributions(records),
        "by_source": _by_source_distributions(records),
        "pooled_optional_exercise_by_source": _pooled_optional_exercise_by_source(
            records
        ),
        "optional_exercise_extremes": _optional_exercise_extremes(records),
    }


def _scope_payload(
    scope: str, endpoint_parameters: Sequence[EndpointParameterRecord]
) -> dict[str, Any]:
    covered = [
        record for record in endpoint_parameters if record.route_covering_test_count > 0
    ]
    return {
        "scope": scope,
        "endpoint_count": len(endpoint_parameters),
        "coverage": share(
            record.route_covering_test_count > 0 for record in endpoint_parameters
        ).to_dict(),
        "among_covered": _condition_payload(covered),
    }


def compute(
    endpoint_parameters: Sequence[EndpointParameterRecord],
    all_endpoint_parameters: Sequence[EndpointParameterRecord] | None = None,
    saint_comparison_endpoint_parameters: (
        Sequence[EndpointParameterRecord] | None
    ) = None,
) -> dict[str, Any]:
    """Parameter-exercise coverage over the gated endpoint-parameter set, plus an
    ``all_universe`` view over every project's endpoint parameters (no gating) for
    cross-suite comparison. Both views condition the exercise/combinatorial metrics
    on covered endpoints only, so widening the denominator changes the coverage
    proportion but leaves the ``among_covered`` distributions unchanged. When
    ``all_endpoint_parameters`` is omitted the ungated view mirrors the gated set.

    ``saint_comparison_endpoint_parameters`` (SAINT-comparison only), when given,
    adds a ``saint_comparison`` view over the full universe with the known SAINT
    deploy-time context-path prefixes stripped, so the covered subpopulation
    admits the query-parameter-rich endpoints the baseline attribution drops and
    the t-way metrics are not suppressed by that artifact."""
    if all_endpoint_parameters is None:
        all_endpoint_parameters = endpoint_parameters
    payload = _scope_payload(
        "endpoints_api_tests_and_resolved_endpoint_events", endpoint_parameters
    )
    payload["all_universe"] = _scope_payload("all_projects", all_endpoint_parameters)
    if saint_comparison_endpoint_parameters is not None:
        payload["saint_comparison"] = _scope_payload(
            "all_projects_context_path_stripped",
            saint_comparison_endpoint_parameters,
        )
    return payload
