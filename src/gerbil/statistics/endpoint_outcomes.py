"""Asserted status outcomes per endpoint, attributed request-by-request from
test sequences: which endpoints get happy-path, error-path, and auth-denial
status assertions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from gerbil.statistics.distributions import (
    count_share_entries,
    share,
    status_code_sort_key,
    summarize,
)
from gerbil.statistics.records import STATUS_RANGE_KEYS, EndpointRecord

_SUCCESS_RANGE = "2xx"
_ERROR_RANGES: tuple[str, ...] = ("4xx", "5xx")
_AUTH_DENIAL_CODES: tuple[str, ...] = ("401", "403")

# Outcome-mix buckets partitioning status-asserted endpoints, in output order.
_OUTCOME_MIX_BUCKETS: tuple[str, ...] = (
    "success_only",
    "error_without_success",
    "success_and_error",
    "neither_success_nor_error",
)


def _outcome_mix_bucket(record: EndpointRecord) -> str:
    ranges = record.asserted_status_range_counts
    has_success = ranges.get(_SUCCESS_RANGE, 0) > 0
    has_error = any(ranges.get(range_key, 0) > 0 for range_key in _ERROR_RANGES)
    if has_success and has_error:
        return "success_and_error"
    if has_success:
        return "success_only"
    if has_error:
        return "error_without_success"
    # Only 1xx/3xx/unknown ranges were asserted.
    return "neither_success_nor_error"


def _has_auth_denial_assertion(record: EndpointRecord) -> bool:
    codes = record.asserted_status_code_counts
    return any(codes.get(code, 0) > 0 for code in _AUTH_DENIAL_CODES)


def _outcome_mix(asserted: Sequence[EndpointRecord]) -> dict[str, Any]:
    total = len(asserted)
    counts = Counter(_outcome_mix_bucket(record) for record in asserted)
    return {
        "scope": "status_asserted_endpoints",
        "endpoint_count": total,
        "by_mix": count_share_entries(counts, _OUTCOME_MIX_BUCKETS, total),
        "has_auth_denial_assertion": share(
            _has_auth_denial_assertion(record) for record in asserted
        ).to_dict(),
    }


# The pooled counts sum per-endpoint attributions, so one written assertion
# counts once per endpoint it attributes to (duplicate extractions of the same
# route — interface plus implementation, consumes/produces variants — are each
# credited); the keys say "attributed" to distinguish from assertions written.


def _asserted_status_ranges(
    endpoints: Sequence[EndpointRecord], asserted: Sequence[EndpointRecord]
) -> dict[str, Any]:
    attribution_counts: Counter[str] = Counter()
    endpoint_counts: Counter[str] = Counter()
    for record in asserted:
        for range_key, count in record.asserted_status_range_counts.items():
            attribution_counts[range_key] += count
            if count > 0:
                endpoint_counts[range_key] += 1
    attribution_total = sum(attribution_counts.values())
    return {
        "attributed_assertion_counts": count_share_entries(
            attribution_counts, STATUS_RANGE_KEYS, attribution_total
        ),
        "endpoints_with_range": count_share_entries(
            endpoint_counts, STATUS_RANGE_KEYS, len(asserted)
        ),
        "endpoints_with_range_among_all_endpoints": count_share_entries(
            endpoint_counts, STATUS_RANGE_KEYS, len(endpoints)
        ),
    }


def _asserted_status_codes(asserted: Sequence[EndpointRecord]) -> dict[str, Any]:
    attribution_counts: Counter[str] = Counter()
    endpoint_counts: Counter[str] = Counter()
    for record in asserted:
        for code, count in record.asserted_status_code_counts.items():
            attribution_counts[code] += count
            if count > 0:
                endpoint_counts[code] += 1
    attribution_total = sum(attribution_counts.values())
    code_keys = sorted(attribution_counts, key=status_code_sort_key)
    return {
        "attributed_assertion_counts": count_share_entries(
            attribution_counts, code_keys, attribution_total
        ),
        "endpoints_with_code": count_share_entries(
            endpoint_counts, code_keys, len(asserted)
        ),
    }


def compute(endpoints: Sequence[EndpointRecord]) -> dict[str, Any]:
    attributed = [record for record in endpoints if record.attributed_request_count > 0]
    asserted = [
        record for record in endpoints if record.status_asserted_request_count > 0
    ]
    return {
        "scope": "endpoints_api_tests_and_resolved_endpoint_events",
        "endpoint_count": len(endpoints),
        "request_attributed": share(
            record.attributed_request_count > 0 for record in endpoints
        ).to_dict(),
        "status_asserted": share(
            record.status_asserted_request_count > 0 for record in endpoints
        ).to_dict(),
        "outcome_mix": _outcome_mix(asserted),
        "asserted_status_ranges": _asserted_status_ranges(endpoints, asserted),
        "asserted_status_codes": _asserted_status_codes(asserted),
        "per_endpoint": {
            "attributed_request_count_among_attributed": summarize(
                record.attributed_request_count for record in attributed
            ).to_dict(),
            "status_asserted_request_count_among_asserted": summarize(
                record.status_asserted_request_count for record in asserted
            ).to_dict(),
            "asserting_test_count_among_asserted": summarize(
                record.asserting_test_count for record in asserted
            ).to_dict(),
        },
    }
