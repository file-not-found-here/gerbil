"""Status code distribution builder.

Reads ``node.assertion_classification`` set by the assertion classification
pass and produces a ``StatusCodeDistribution`` tallying STATUS-role assertions
into HTTP range buckets (1xx, 2xx, 3xx, 4xx, 5xx, unknown).
"""

from __future__ import annotations

from collections import Counter

from gerbil.analysis.assertion.classification import status_range_from_code
from gerbil.analysis.runtime import TestRuntimeView
from gerbil.analysis.schema import AssertionRole, StatusCodeDistribution

_RANGE_TO_FIELD: dict[str, str] = {
    "1xx": "range_1xx",
    "2xx": "range_2xx",
    "3xx": "range_3xx",
    "4xx": "range_4xx",
    "5xx": "range_5xx",
}


def build_status_code_distribution(
    *,
    runtime_view: TestRuntimeView,
) -> StatusCodeDistribution:
    counts: dict[str, int] = {field: 0 for field in _RANGE_TO_FIELD.values()}
    counts["unknown"] = 0

    for event in runtime_view.iter_events():
        ac = event.node.assertion_classification
        if ac is None or not ac.is_countable or ac.role != AssertionRole.STATUS:
            continue

        status_range = ac.status_range
        if status_range is None and ac.status_code is not None:
            status_range = status_range_from_code(ac.status_code)

        field = _RANGE_TO_FIELD.get(status_range or "", "unknown")
        counts[field] += 1

    return StatusCodeDistribution(**counts)


def build_status_code_counts(
    *,
    runtime_view: TestRuntimeView,
) -> dict[str, int]:
    counts: Counter[int] = Counter()

    for event in runtime_view.iter_events():
        ac = event.node.assertion_classification
        if ac is None or not ac.is_countable or ac.role != AssertionRole.STATUS:
            continue
        if ac.status_code is not None:
            counts[ac.status_code] += 1

    return {str(code): counts[code] for code in sorted(counts)}


__all__ = ["build_status_code_counts", "build_status_code_distribution"]
