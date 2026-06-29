"""Request-side construction surface over dispatched request events: payloads,
headers, parameter kinds, and builder-pattern usage."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from gerbil.statistics.distributions import (
    count_share,
    count_share_entries,
    share,
    summarize,
)
from gerbil.statistics.records import (
    BUILDER_CONTRIBUTED_PROPERTIES,
    BuilderGroup,
    TestRecord,
    request_event_total,
)

# Builder-contributed property marking a builder that set the request body; kept
# within the canonical vocabulary so the conditional cohort and the property
# table cannot drift apart if the merge pass renames or drops it.
_BODY_PAYLOAD_PROPERTY = "has_body_payload"
assert _BODY_PAYLOAD_PROPERTY in BUILDER_CONTRIBUTED_PROPERTIES

# The two content-negotiation header names the request-construction narrative
# singles out; the occurrence share over these isolates plain content negotiation
# from scenario-specific header use.
_CONTENT_NEGOTIATION_HEADERS: frozenset[str] = frozenset({"content-type", "accept"})

# (surface name, per-test count accessor), in output order.
_SURFACE_ACCESSORS: tuple[tuple[str, Callable[[TestRecord], int]], ...] = (
    ("body_payload", lambda test: test.request_events_with_body),
    ("headers", lambda test: test.request_events_with_headers),
    ("query_params", lambda test: test.request_events_with_query_params),
    ("path_params", lambda test: test.request_events_with_path_params),
    ("form_params", lambda test: test.request_events_with_form_params),
)


def _event_surfaces(
    api_tests: Sequence[TestRecord], event_total: int
) -> dict[str, Any]:
    surfaces: dict[str, Any] = {}
    for name, accessor in _SURFACE_ACCESSORS:
        event_count = sum(accessor(test) for test in api_tests)
        surfaces[name] = {
            "events": count_share(event_count, event_total).to_dict(),
            "tests_with_any": share(
                accessor(test) > 0
                for test in api_tests
                if request_event_total(test) > 0
            ).to_dict(),
        }
    return surfaces


def _per_event_distributions(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    return {
        "query_param_names_per_event": summarize(
            count for test in api_tests for count in test.event_query_param_counts
        ).to_dict(),
        "header_names_per_event": summarize(
            count for test in api_tests for count in test.event_header_name_counts
        ).to_dict(),
    }


def _ordered_property_keys(counts: Mapping[str, int]) -> tuple[str, ...]:
    """Canonical contributed-property names first, then any observed extras."""
    observed_extra = sorted(set(counts) - set(BUILDER_CONTRIBUTED_PROPERTIES))
    return (*BUILDER_CONTRIBUTED_PROPERTIES, *observed_extra)


def _group_has_body(group: BuilderGroup) -> bool:
    return any(_BODY_PAYLOAD_PROPERTY in builder for builder in group.builders)


def _builder_usage(api_tests: Sequence[TestRecord], event_total: int) -> dict[str, Any]:
    correlated_events = sum(
        test.request_events_with_builder_correlation for test in api_tests
    )
    builder_groups = [group for test in api_tests for group in test.builder_groups]
    builder_count_total = sum(len(group.builders) for group in builder_groups)
    property_counts = Counter(
        property_name
        for group in builder_groups
        for builder in group.builders
        for property_name in builder
    )
    property_total = sum(property_counts.values())
    return {
        "events_with_builder_correlation": count_share(
            correlated_events, event_total
        ).to_dict(),
        "tests_with_builder_correlation": share(
            test.request_events_with_builder_correlation > 0
            for test in api_tests
            if request_event_total(test) > 0
        ).to_dict(),
        "builder_group_count": len(builder_groups),
        "builder_count_total": builder_count_total,
        "contributed_properties": {
            "total": property_total,
            "by_property": count_share_entries(
                property_counts, _ordered_property_keys(property_counts), property_total
            ),
        },
    }


def _header_vocabulary(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    """Distinct resolved request-header vocabulary across the api-test runtime
    view. The test-frequency map counts tests (a test contributes each name it
    sets at most once); the occurrence breakdown weights every use. Only header
    names the pipeline resolved are counted, so both are lower-bound estimates of
    the vocabulary actually exercised."""
    name_test_frequency: Counter[str] = Counter()
    occurrence_total = 0
    content_negotiation_occurrences = 0
    for test in api_tests:
        for name, count in test.runtime_header_name_counts:
            name_test_frequency[name] += 1
            occurrence_total += count
            if name in _CONTENT_NEGOTIATION_HEADERS:
                content_negotiation_occurrences += count
    ordered_names = sorted(
        name_test_frequency, key=lambda name: (-name_test_frequency[name], name)
    )
    return {
        "distinct_header_name_count": len(name_test_frequency),
        "tests_with_any_header": share(
            bool(test.runtime_header_name_counts)
            for test in api_tests
            if request_event_total(test) > 0
        ).to_dict(),
        "distinct_header_names_per_test": summarize(
            len(test.runtime_header_name_counts) for test in api_tests
        ).to_dict(),
        "header_name_occurrences": {
            "total": occurrence_total,
            "content_negotiation": count_share(
                content_negotiation_occurrences, occurrence_total
            ).to_dict(),
        },
        "header_name_test_frequency": {
            name: name_test_frequency[name] for name in ordered_names
        },
    }


def _builder_types_given_body(api_tests: Sequence[TestRecord]) -> dict[str, Any]:
    """Among per-dispatch builder groups that include a body-payload builder, the
    share of the request builders contributing each property type. A builder is
    counted once per distinct property it contributed, so types are not mutually
    exclusive and the shares need not sum to one."""
    groups = [group for test in api_tests for group in test.builder_groups]
    body_groups = [group for group in groups if _group_has_body(group)]
    builders = [builder for group in body_groups for builder in group.builders]
    builder_total = len(builders)
    type_counts = Counter(
        property_name for builder in builders for property_name in set(builder)
    )
    return {
        "scope": "per_dispatch_builder_groups_with_body_payload",
        "builder_group_count": len(groups),
        "body_builder_group_count": len(body_groups),
        "share_of_builder_groups_with_body_payload": share(
            _group_has_body(group) for group in groups
        ).to_dict(),
        "builder_count": builder_total,
        "builder_types": {
            "total": builder_total,
            "by_type": count_share_entries(
                type_counts, _ordered_property_keys(type_counts), builder_total
            ),
        },
    }


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    api_tests = [test for test in tests if test.is_api_test]
    event_total = sum(request_event_total(test) for test in api_tests)
    return {
        "scope": "api_tests",
        "api_test_count": len(api_tests),
        "request_event_count": event_total,
        "event_surfaces": _event_surfaces(api_tests, event_total),
        "per_event": _per_event_distributions(api_tests),
        "builder_usage": _builder_usage(api_tests, event_total),
        "header_vocabulary": _header_vocabulary(api_tests),
        "builder_types_given_body": _builder_types_given_body(api_tests),
    }
