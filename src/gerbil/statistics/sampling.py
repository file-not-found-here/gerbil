"""Sample 'interesting' API-test projects for targeted LLM analysis, ranking
projects by their API test count (the number of HTTP-driving tests they carry)."""

from __future__ import annotations

import json
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gerbil.statistics.records import ProjectStatsRecord

SAMPLE_OUTPUT_FILENAME = "interesting_projects.json"

# Identifies the ranking criterion in the output payload: projects are ranked by
# how many API tests they carry, over every project that has at least one.
SAMPLING_STRATEGY = "api_test_count"

# Datasets excluded from the candidate universe regardless of their metrics.
# rest-assured and resteasy are HTTP/JAX-RS libraries, not applications under
# test. thingsboard is excluded because its MockMvc tests do not resolve to
# endpoints, so its remaining resolved tests are not a meaningful sample. quarkus
# is excluded because its tests define endpoints test-side (in the test sources
# themselves) rather than exercising an application under test, so its resolved
# endpoints do not reflect a real API surface. quarkus-quickstarts is a monorepo
# of many independent quickstart demo apps that reuse identical demo path
# templates (e.g. GET /hello is declared by 10 resource classes, GET /fruits by
# 12), so a test in one quickstart cross-attributes to same-path endpoints
# declared in sibling quickstarts and its coverage metrics are confounded.
# spring-cloud-gateway is a gateway
# framework whose tests proxy requests through it to downstream stubs (/get,
# /post, ...) rather than exercising its own declared endpoints. springdoc-openapi
# is a doc-generation library whose endpoint universe is sample controllers
# defined in the test sources, exercised to verify generated OpenAPI docs. cxf is
# a JAX-RS/JAX-WS framework (peer to rest-assured/resteasy) whose endpoint
# universe is its own system-test and demo services, and whose tests use
# programmatic clients with runtime-built addresses that never resolve to a path.
# spring-cloud-function exposes functions as HTTP endpoints routed by function
# name (POST /<fn>) defined test-side, while its declared endpoints are unrelated
# demo controllers, so resolved test paths never attribute.
#
# The next six are excluded because endpoint attribution is unreliable, so their
# coverage-derived metrics are not comparable across test suites:
# - openrouteservice: the route base is set via a RestAssured basePath we do not
#   trace through dataflow, so observed paths miss the `/v2/<service>` prefix and
#   never attribute to an endpoint.
# - camunda: tests prepend a test-harness base path (`/rest-test`) that the
#   endpoint templates lack, confounding attribution.
# - openbas: another base-constant dataflow tracing gap; paths are built from a
#   base URI constant we do not trace, leaving unattributable fragments.
# - elide: degenerate endpoint surface for coverage, with catch-all `/{path:.*}`
#   dispatchers plus unresolved Spring `${...}` placeholder endpoints.
# - yas: controllers declare bare mappings (`/storefront/...`) while the served
#   path adds `server.servlet.context-path` (`/v1` in tests, `/<service>` in
#   prod) plus a gateway prefix we do not fold into templates, so integration
#   tests that use the served path do not attribute. The matched fraction depends
#   on a project's MockMvc-vs-RestAssured test mix, making coverage non-comparable
#   across suites.
# - strongbox: only ~30% of API tests resolve a request path because paths are
#   declared at runtime (built from base-URI constants we do not trace), so its
#   coverage reflects path-resolution recall rather than test thoroughness.
EXCLUDED_DATASETS = frozenset(
    {
        "rest-assured_rest-assured",
        "resteasy_resteasy",
        "apache_cxf",
        "thingsboard_thingsboard",
        "quarkusio_quarkus",
        "quarkusio_quarkus-quickstarts",
        "spring-cloud_spring-cloud-gateway",
        "spring-cloud_spring-cloud-function",
        "springdoc_springdoc-openapi",
        "GIScience_openrouteservice",
        "camunda_camunda-bpm-platform",
        "OpenBAS-Platform_openbas",
        "yahoo_elide",
        "nashtech-garage_yas",
        "strongbox_strongbox",
    }
)


@dataclass(frozen=True)
class ProjectComplexity:
    """Per-project inter-sequence complexity metrics and the source analysis path."""

    dataset_name: str
    gerbil_path: Path
    api_test_count: int
    # API tests that resolved a method+path endpoint for at least one request event.
    resolved_endpoint_test_count: int
    multi_sequence_test_count: int
    total_sequence_count: int
    max_sequence_count: int
    mean_sequence_count_per_test: float | None
    endpoint_count: int


def project_complexity(
    record: ProjectStatsRecord, gerbil_path: Path
) -> ProjectComplexity:
    """Project one loaded record to its inter-sequence complexity metrics."""
    sequence_counts = [
        test.http_sequence_count for test in record.tests if test.is_api_test
    ]
    multi_sequence_test_count = sum(
        1
        for test in record.tests
        if test.is_api_test and test.has_multiple_http_sequences
    )
    resolved_endpoint_test_count = sum(
        1
        for test in record.tests
        if test.is_api_test and test.distinct_endpoint_count > 0
    )
    return ProjectComplexity(
        dataset_name=record.dataset_name,
        gerbil_path=gerbil_path,
        api_test_count=len(sequence_counts),
        resolved_endpoint_test_count=resolved_endpoint_test_count,
        multi_sequence_test_count=multi_sequence_test_count,
        total_sequence_count=sum(sequence_counts),
        max_sequence_count=max(sequence_counts, default=0),
        mean_sequence_count_per_test=(
            sum(sequence_counts) / len(sequence_counts) if sequence_counts else None
        ),
        endpoint_count=len(record.endpoints),
    )


def _rank_key(project: ProjectComplexity) -> tuple[int, int, int, str]:
    # Most API tests first; total then peak sequence volume break ties, and
    # dataset_name keeps the order deterministic for equal-volume projects.
    return (
        -project.api_test_count,
        -project.total_sequence_count,
        -project.max_sequence_count,
        project.dataset_name,
    )


def rank_interesting_projects(
    records: Sequence[ProjectStatsRecord], gerbil_paths: Sequence[Path]
) -> list[ProjectComplexity]:
    """Projects carrying resolvable API tests against endpoints, ranked by count.

    The candidate universe is the API-test-and-endpoints universe: a project
    qualifies only if it has at least one endpoint and at least one API test for
    which a method+path endpoint was resolved (so projects whose HTTP calls never
    resolve to an endpoint, e.g. raw HttpClient usage, are excluded). Curated
    datasets (libraries and projects with systematically unresolvable tests, see
    EXCLUDED_DATASETS) are also excluded. Records and gerbil_paths must align
    positionally.
    """
    complexities = [
        project_complexity(record, gerbil_path)
        for record, gerbil_path in zip(records, gerbil_paths, strict=True)
    ]
    interesting = [
        project
        for project in complexities
        if project.resolved_endpoint_test_count > 0
        and project.endpoint_count > 0
        and project.dataset_name not in EXCLUDED_DATASETS
    ]
    return sorted(interesting, key=_rank_key)


def _sample_size(
    candidate_count: int, *, count: int | None, percentile: float | None
) -> int:
    """How many projects to take from a candidate set of `candidate_count`.

    Exactly one of count or percentile selects the size; percentile keeps at least
    one project when the set is non-empty. The result never exceeds candidate_count.
    """
    if (count is None) == (percentile is None):
        raise ValueError("exactly one of count or percentile must be provided")
    if percentile is not None:
        if not 0 < percentile <= 100:
            raise ValueError("percentile must be in the range (0, 100]")
        if candidate_count == 0:
            return 0
        return min(
            candidate_count, max(1, math.ceil(candidate_count * percentile / 100.0))
        )
    assert count is not None
    if count < 1:
        raise ValueError("count must be >= 1")
    return min(candidate_count, count)


def select_sample(
    ranked: Sequence[ProjectComplexity],
    *,
    count: int | None = None,
    percentile: float | None = None,
) -> list[ProjectComplexity]:
    """Top-ranked slice of `ranked` limited by exactly one of count or percentile."""
    size = _sample_size(len(ranked), count=count, percentile=percentile)
    return list(ranked[:size])


def build_candidate_pool(
    ranked: Sequence[ProjectComplexity], pool_percent: float
) -> list[ProjectComplexity]:
    """The top pool_percent% of `ranked` (at least one when `ranked` is non-empty)."""
    if not 0 < pool_percent <= 100:
        raise ValueError("pool_percent must be in the range (0, 100]")
    if not ranked:
        return []
    top_n = max(1, math.ceil(len(ranked) * pool_percent / 100.0))
    return list(ranked[:top_n])


def draw_random_sample(
    pool: Sequence[ProjectComplexity],
    *,
    count: int | None = None,
    percentile: float | None = None,
    seed: int = 0,
) -> list[ProjectComplexity]:
    """A seeded uniform draw of count (or percentile%) projects from `pool`.

    The draw is reproducible for a given seed and returned in ranked order.
    """
    size = _sample_size(len(pool), count=count, percentile=percentile)
    drawn = random.Random(seed).sample(list(pool), size)
    return sorted(drawn, key=_rank_key)


def _summarize_sample(selected: Sequence[ProjectComplexity]) -> dict[str, int]:
    """Corpus-wide totals over the selected projects."""
    return {
        "project_count": len(selected),
        "api_test_count": sum(project.api_test_count for project in selected),
        "resolved_endpoint_test_count": sum(
            project.resolved_endpoint_test_count for project in selected
        ),
        "multi_sequence_test_count": sum(
            project.multi_sequence_test_count for project in selected
        ),
        "total_sequence_count": sum(
            project.total_sequence_count for project in selected
        ),
        "endpoint_count": sum(project.endpoint_count for project in selected),
    }


def build_sample_payload(
    ranked: Sequence[ProjectComplexity],
    selected: Sequence[ProjectComplexity],
    *,
    count: int | None,
    percentile: float | None,
    mode: str = "top",
    seed: int | None = None,
    pool_percent: float | None = None,
    pool_project_count: int | None = None,
) -> dict[str, Any]:
    """Serializable payload: the selection parameters and the chosen projects.

    `mode` is "top" (deterministic, most-tests-first) or "random" (a seeded draw
    from the top pool_percent% of interesting projects); the seed/pool fields are
    null in "top" mode.
    """
    return {
        "selection": {
            "strategy": SAMPLING_STRATEGY,
            "mode": mode,
            "count": count,
            "percentile": percentile,
            "seed": seed,
            "pool_percent": pool_percent,
            "interesting_project_count": len(ranked),
            "pool_project_count": pool_project_count,
            "selected_project_count": len(selected),
        },
        "summary": _summarize_sample(selected),
        "projects": [
            {
                "rank": index + 1,
                "dataset_name": project.dataset_name,
                "gerbil_path": str(project.gerbil_path),
                "project_dir": str(project.gerbil_path.parent),
                "api_test_count": project.api_test_count,
                "resolved_endpoint_test_count": project.resolved_endpoint_test_count,
                "multi_sequence_test_count": project.multi_sequence_test_count,
                "total_sequence_count": project.total_sequence_count,
                "max_sequence_count": project.max_sequence_count,
                "mean_sequence_count_per_test": project.mean_sequence_count_per_test,
                "endpoint_count": project.endpoint_count,
            }
            for index, project in enumerate(selected)
        ],
    }


def write_sample(payload: dict[str, Any], output_dir: Path) -> Path:
    """Write the sample payload to <output_dir>/interesting_projects.json."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / SAMPLE_OUTPUT_FILENAME
    output_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return output_file
