"""Endpoint-surface distributions: per-source parameter counts and body
presence over all endpoints, plus surface metrics bucketed by how many tests
target each application endpoint."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from gerbil.analysis.schema import EndpointParameterSource
from gerbil.statistics.distributions import share, summarize
from gerbil.statistics.records import HTTP_METHODS, EndpointRecord

# Canonical source order for stable per-source output.
_SOURCE_ORDER: tuple[str, ...] = tuple(
    source.value for source in EndpointParameterSource
)

_QUERY = EndpointParameterSource.QUERY.value

# (bucket name, predicate over covering_test_count), in output order.
_COVERAGE_BUCKETS: tuple[tuple[str, Callable[[int], bool]], ...] = (
    ("no_test", lambda count: count == 0),
    ("one_to_three", lambda count: 1 <= count <= 3),
    ("more_than_three", lambda count: count > 3),
)

_SURFACE_ACCESSORS: tuple[tuple[str, Callable[[EndpointRecord], int]], ...] = (
    ("path_variable_count", lambda endpoint: endpoint.path_variable_count),
    (
        "query_variable_count",
        lambda endpoint: endpoint.parameter_count_by_source.get(_QUERY, 0),
    ),
    (
        "header_variable_count",
        lambda endpoint: endpoint.parameter_count_by_source.get(
            EndpointParameterSource.HEADER.value, 0
        ),
    ),
    (
        "form_variable_count",
        lambda endpoint: endpoint.parameter_count_by_source.get(
            EndpointParameterSource.FORM.value, 0
        ),
    ),
    (
        "required_query_variable_count",
        lambda endpoint: endpoint.required_count_by_source.get(_QUERY, 0),
    ),
    (
        "optional_query_variable_count",
        lambda endpoint: endpoint.optional_count_by_source.get(_QUERY, 0),
    ),
    ("route_depth", lambda endpoint: endpoint.route_depth),
)


def _surface_distributions(endpoints: Sequence[EndpointRecord]) -> dict[str, Any]:
    return {
        name: summarize(accessor(endpoint) for endpoint in endpoints).to_dict()
        for name, accessor in _SURFACE_ACCESSORS
    }


def _by_source_count_distributions(
    endpoints: Sequence[EndpointRecord],
    accessor: Callable[[EndpointRecord], dict[str, int]],
) -> dict[str, Any]:
    """Per-source count distributions over all endpoints (0 for endpoints lacking
    the source); only sources observed somewhere in the dataset are emitted."""
    observed = [
        source
        for source in _SOURCE_ORDER
        if any(accessor(endpoint).get(source, 0) > 0 for endpoint in endpoints)
    ]
    return {
        source: summarize(
            accessor(endpoint).get(source, 0) for endpoint in endpoints
        ).to_dict()
        for source in observed
    }


def _parameter_surface(endpoints: Sequence[EndpointRecord]) -> dict[str, Any]:
    return {
        "endpoints_with_body": share(
            endpoint.has_body for endpoint in endpoints
        ).to_dict(),
        "required_by_source": _by_source_count_distributions(
            endpoints, lambda endpoint: endpoint.required_count_by_source
        ),
        "optional_by_source": _by_source_count_distributions(
            endpoints, lambda endpoint: endpoint.optional_count_by_source
        ),
    }


# Wildcard-method endpoints (mappings without a method constraint) bucket
# separately so they never inflate a concrete method's coverage.
_WILDCARD_METHOD_BUCKET = "wildcard"


def _coverage_by_http_method(endpoints: Sequence[EndpointRecord]) -> dict[str, Any]:
    def bucket(endpoint: EndpointRecord) -> str:
        if endpoint.is_method_wildcard:
            return _WILDCARD_METHOD_BUCKET
        return endpoint.http_method

    by_method: dict[str, Any] = {}
    for method in (*HTTP_METHODS, _WILDCARD_METHOD_BUCKET):
        method_endpoints = [
            endpoint for endpoint in endpoints if bucket(endpoint) == method
        ]
        by_method[method] = {
            "endpoint_count": len(method_endpoints),
            "coverage": share(
                endpoint.covering_test_count > 0 for endpoint in method_endpoints
            ).to_dict(),
            "tests_per_endpoint_among_covered": summarize(
                endpoint.covering_test_count
                for endpoint in method_endpoints
                if endpoint.covering_test_count > 0
            ).to_dict(),
        }
    return by_method


def _coverage_buckets(
    endpoints: Sequence[EndpointRecord],
) -> dict[str, Any]:
    coverage_buckets: dict[str, Any] = {}
    for bucket_name, predicate in _COVERAGE_BUCKETS:
        bucket_endpoints = [
            endpoint
            for endpoint in endpoints
            if predicate(endpoint.covering_test_count)
        ]
        coverage_buckets[bucket_name] = {
            "endpoint_count": len(bucket_endpoints),
            **_surface_distributions(bucket_endpoints),
        }
    return coverage_buckets


def coverage_payload(scope: str, endpoints: Sequence[EndpointRecord]) -> dict[str, Any]:
    return {
        "scope": scope,
        "endpoint_count": len(endpoints),
        "parameter_surface": _parameter_surface(endpoints),
        "coverage": share(
            endpoint.covering_test_count > 0 for endpoint in endpoints
        ).to_dict(),
        "tests_per_endpoint_among_covered": summarize(
            endpoint.covering_test_count
            for endpoint in endpoints
            if endpoint.covering_test_count > 0
        ).to_dict(),
        "coverage_buckets": _coverage_buckets(endpoints),
        "coverage_by_http_method": _coverage_by_http_method(endpoints),
    }


def compute(
    endpoints: Sequence[EndpointRecord],
    gated_endpoints: Sequence[EndpointRecord],
) -> dict[str, Any]:
    """Endpoint-universe parameter surface over all projects with endpoints, plus
    two coverage decompositions: ``all_universe`` over every project's endpoints
    (no gating) and ``endpoint_coverage`` gated to projects with API tests that
    resolved at least one endpoint+method event. The ungated ``all_universe`` view
    is intended for cross-suite comparison, where each suite is scored over the
    same full endpoint surface rather than only the projects it happens to test."""
    return {
        "endpoint_universe": {
            "scope": "projects_with_endpoints",
            "endpoint_count": len(endpoints),
            "parameter_surface": _parameter_surface(endpoints),
        },
        "all_universe": coverage_payload("all_projects", endpoints),
        "endpoint_coverage": coverage_payload(
            "endpoints_api_tests_and_resolved_endpoint_events", gated_endpoints
        ),
    }
