"""SAINT-comparison-only endpoint coverage: the full-universe coverage decomposition
recomputed after stripping the known deploy-time context-path prefixes that keep
SAINT's generated request URLs from attributing to their endpoints."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from gerbil.statistics.endpoints import coverage_payload
from gerbil.statistics.records import SAINT_CONTEXT_PATH_PREFIXES, EndpointRecord


def compute(
    endpoints: Sequence[EndpointRecord],
    saint_comparison_endpoints: Sequence[EndpointRecord],
) -> dict[str, Any]:
    """SAINT comparison only. Endpoint coverage over the full endpoint universe,
    before (`baseline`) and after (`context_path_stripped`) removing the known
    SAINT deploy-time context-path prefixes from observed request paths. SAINT
    deploys under a webapp/servlet context path absent from the source-derived
    templates, so its requests otherwise attribute to nothing; the two payloads
    isolate that measurement artifact from any real coverage difference."""
    return {
        "scope": "saint_comparison",
        "purpose": (
            "SAINT comparison only: endpoint coverage recomputed after stripping "
            "known deploy-time context-path prefixes from observed request paths, "
            "so SAINT's context-path-prefixed URLs attribute to their endpoints."
        ),
        "stripped_context_path_prefixes": list(SAINT_CONTEXT_PATH_PREFIXES),
        "baseline": coverage_payload("all_projects_unstripped", endpoints),
        "context_path_stripped": coverage_payload(
            "all_projects_context_path_stripped", saint_comparison_endpoints
        ),
    }
