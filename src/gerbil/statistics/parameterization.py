"""Parameterized (data-driven) test distributions, compared across API and
non-API cohorts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from gerbil.statistics.distributions import count_share_entries, share
from gerbil.statistics.records import TestRecord

# Source-kind buckets partitioning parameterized tests, in output order. A
# parameterized test with no recognized source annotation still runs with an
# argument provider; it just uses one outside the curated mapping.
_SOURCE_KIND_BUCKETS: tuple[str, ...] = (
    "static_only",
    "dynamic_only",
    "mixed",
    "no_recognized_source",
)


def _source_kind_bucket(test: TestRecord) -> str:
    has_static = bool(test.parameterization_static_sources)
    has_dynamic = bool(test.parameterization_dynamic_sources)
    if has_static and has_dynamic:
        return "mixed"
    if has_static:
        return "static_only"
    if has_dynamic:
        return "dynamic_only"
    return "no_recognized_source"


def _cohort_payload(tests: Sequence[TestRecord]) -> dict[str, Any]:
    parameterized = [test for test in tests if test.is_parameterized]
    source_kind_counts = Counter(_source_kind_bucket(test) for test in parameterized)
    annotation_counts = Counter(
        annotation
        for test in parameterized
        for annotation in (
            *test.parameterization_static_sources,
            *test.parameterization_dynamic_sources,
        )
    )
    annotation_total = sum(annotation_counts.values())
    return {
        "test_count": len(tests),
        "parameterized": share(test.is_parameterized for test in tests).to_dict(),
        "source_kinds": {
            "scope": "parameterized_tests",
            "test_count": len(parameterized),
            "by_kind": count_share_entries(
                source_kind_counts, _SOURCE_KIND_BUCKETS, len(parameterized)
            ),
        },
        "source_annotations": {
            "total": annotation_total,
            "by_annotation": count_share_entries(
                annotation_counts, sorted(annotation_counts), annotation_total
            ),
        },
    }


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    return {
        "scope": "all_tests",
        "api": _cohort_payload([test for test in tests if test.is_api_test]),
        "non_api": _cohort_payload([test for test in tests if not test.is_api_test]),
    }
