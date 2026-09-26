"""Resource-interaction distributions over projects with endpoints and API tests:
per-(test, resource) CRUD lifecycle labels and per-production-resource operation
coverage, including the availability ceiling that bounds exercise. The
production-resource coverage is scoped (by the runner) to projects where at least
one dispatch resolved to an endpoint, so an untested resource reflects a genuine
gap rather than a project whose tests can never be mapped to its endpoints."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from gerbil.statistics.crud_combinations import CRUD_COMBINATIONS, combination_entries
from gerbil.statistics.distributions import share, summarize
from gerbil.statistics.records import (
    CRUD_OPERATIONS,
    CRUD_VERBS,
    HTTP_METHODS,
    LIFECYCLE_LABELS,
    WRITE_OPERATIONS,
    ResourceCrudRecord,
    TestRecord,
)

_READ = "read"
_FULL_CRUD: frozenset[str] = frozenset(CRUD_OPERATIONS)


def _canonical_subset(operations: Sequence[str]) -> tuple[str, ...]:
    """The given CRUD operations in canonical order, keyed like CRUD_COMBINATIONS."""
    present = set(operations)
    return tuple(operation for operation in CRUD_OPERATIONS if operation in present)


def _lifecycle_label_distribution(tests: Sequence[TestRecord]) -> dict[str, Any]:
    """Label counts and percentages over every (test, resource) pair.

    A pair is one resource-interaction sequence on a test; a test contributes one
    label per distinct resource it drives. Every label is keyed in canonical
    order so absent labels report a genuine 0.
    """
    counts = {label: 0 for label in LIFECYCLE_LABELS}
    total = 0
    for test in tests:
        for label in test.resource_lifecycle_labels:
            counts[label] += 1
            total += 1
    return {
        "scope": "test_resource_pairs",
        "pair_count": total,
        "labels": {
            label: {
                "count": counts[label],
                "pct": (100.0 * counts[label] / total) if total else None,
            }
            for label in LIFECYCLE_LABELS
        },
    }


def _per_test_lifecycle(tests: Sequence[TestRecord]) -> dict[str, Any]:
    """Lifecycle behavior folded per test rather than per (test, resource) pair.

    A test counts once under every distinct label its resource sequences carry,
    so unlike the pair view, multi-resource tests do not weigh more. Every label
    is keyed in canonical order so absent labels report a genuine 0.
    """
    tests_with_resources = [test for test in tests if test.resource_lifecycle_labels]
    total = len(tests_with_resources)
    label_counts = {
        label: sum(
            1
            for test in tests_with_resources
            if label in test.resource_lifecycle_labels
        )
        for label in LIFECYCLE_LABELS
    }
    return {
        "scope": "api_tests_with_resource_sequences",
        "test_count": total,
        "labels": {
            label: {
                "test_count": count,
                "pct_of_tests": (100.0 * count / total) if total else None,
            }
            for label, count in label_counts.items()
        },
        "multiple_distinct_label_tests": share(
            len(set(test.resource_lifecycle_labels)) >= 2
            for test in tests_with_resources
        ).to_dict(),
        "has_read_after_write": share(
            test.has_read_after_write for test in tests_with_resources
        ).to_dict(),
        "has_cleanup_delete": share(
            test.has_cleanup_delete for test in tests_with_resources
        ).to_dict(),
    }


def _http_method_distribution(tests: Sequence[TestRecord]) -> dict[str, Any]:
    """Counter of HTTP methods over every dispatched request event across the tests.

    One request event counts once under its method; non-standard or unresolved
    methods fold into the "UNKNOWN" bucket. Every method is keyed in canonical
    order so absent methods report a genuine 0.
    """
    counts = {method: 0 for method in HTTP_METHODS}
    for test in tests:
        for index, method in enumerate(HTTP_METHODS):
            counts[method] += test.http_method_counts[index]
    total = sum(counts.values())
    return {
        "scope": "http_dispatch_events",
        "request_count": total,
        "methods": {
            method: {
                "count": counts[method],
                "pct": (100.0 * counts[method] / total) if total else None,
            }
            for method in HTTP_METHODS
        },
    }


def _crud_operation_distribution(tests: Sequence[TestRecord]) -> dict[str, Any]:
    """Counter of CRUD operations over every dispatched request event across the tests.

    Each request maps to one operation by its HTTP method; requests whose method
    has no CRUD mapping are excluded from the total.
    """
    counts = {operation: 0 for operation in CRUD_OPERATIONS}
    for test in tests:
        for index, operation in enumerate(CRUD_OPERATIONS):
            counts[operation] += test.crud_operation_counts[index]
    total = sum(counts.values())
    return {
        "scope": "crud_mapped_http_dispatch_events",
        "request_count": total,
        "operations": {
            operation: {
                "count": counts[operation],
                "pct": (100.0 * counts[operation] / total) if total else None,
            }
            for operation in CRUD_OPERATIONS
        },
    }


def _resource_count_per_test(tests: Sequence[TestRecord]) -> dict[str, Any]:
    counts = [len(test.resource_lifecycle_labels) for test in tests]
    return {
        # Unlike per_test, zero-resource API tests stay in this sample: a 0 here
        # is the signal, not a structural gap.
        "scope": "api_tests",
        "distribution": summarize(counts).to_dict(),
        "tests_with_multiple_resources": share(count > 1 for count in counts).to_dict(),
    }


def _exercised_completeness(resources: Sequence[ResourceCrudRecord]) -> dict[str, Any]:
    """Distribution of (|available| - |missing|) / |available| over tested resources.

    A resource enters the sample only with a nonempty exercise (so the metric
    describes tested resources) and a nonempty available set (the denominator).
    """
    values = [
        (
            len(resource.available_operations)
            - len(resource.missing_available_operations)
        )
        / len(resource.available_operations)
        for resource in resources
        if resource.exercised_operations and resource.available_operations
    ]
    return {"among_tested": summarize(values).to_dict()}


def _availability_profile_distribution(
    resources: Sequence[ResourceCrudRecord],
) -> dict[str, Any]:
    """Distribution of which CRUD subset each production resource's endpoints expose.

    The unit is one production resource and the share denominator is resources
    exposing at least one CRUD operation; a resource whose endpoints are all
    non-CRUD or method-wildcard has no profile and is tallied separately. This is
    the availability ceiling that bounds what any test could exercise, so the
    exercised-combination distribution can be read against it rather than in a
    vacuum.
    """
    counts = {combination: 0 for combination in CRUD_COMBINATIONS}
    no_crud_operation_count = 0
    for resource in resources:
        available = _canonical_subset(resource.available_operations)
        if not available:
            no_crud_operation_count += 1
            continue
        counts[available] += 1
    crud_capable_total = sum(counts.values())
    return {
        "scope": "production_resources",
        "resource_count": len(resources),
        "crud_capable_resource_count": crud_capable_total,
        "no_crud_operation_resource_count": no_crud_operation_count,
        "combinations": combination_entries(
            counts, CRUD_COMBINATIONS, crud_capable_total
        ),
    }


def _fully_exercised(resources: Sequence[ResourceCrudRecord]) -> dict[str, Any]:
    """Share of CRUD-capable resources whose every available operation is exercised.

    A resource is fully exercised when no available operation is missing. Reported
    over the CRUD-capable population (>=1 available operation) and, more strictly,
    over its tested subset (>=1 exercised operation), so a resource no test touches
    is kept distinct from one tested but left incomplete. The not-fully-exercised
    share is the direct coverage-gap measure; the availability profile above
    explains how much of that gap is small (read-only resources) versus wide
    (full-CRUD resources tested on one verb).
    """
    capable = [resource for resource in resources if resource.available_operations]
    tested = [resource for resource in capable if resource.exercised_operations]
    # A capable resource with nothing missing has exercised every available
    # operation, so fully is a subset of tested.
    fully = [
        resource for resource in capable if not resource.missing_available_operations
    ]
    not_fully = len(capable) - len(fully)
    return {
        "crud_capable_resource_count": len(capable),
        "tested_resource_count": len(tested),
        "fully_exercised_count": len(fully),
        "not_fully_exercised_count": not_fully,
        "pct_fully_exercised_of_capable": (
            100.0 * len(fully) / len(capable) if capable else None
        ),
        "pct_not_fully_exercised_of_capable": (
            100.0 * not_fully / len(capable) if capable else None
        ),
        "pct_fully_exercised_of_tested": (
            100.0 * len(fully) / len(tested) if tested else None
        ),
    }


def _exercise_by_availability_breadth(
    resources: Sequence[ResourceCrudRecord],
) -> dict[str, Any]:
    """Exercise split by how many CRUD operations a resource's endpoints expose.

    Per breadth bucket: the exercised rate (are multi-verb resources tested at a
    higher rate than single-verb ones?) and, of the resources in the bucket a test
    touched, the share fully exercised (the stage-2 completeness rate, conditioned
    on availability breadth so the single-verb majority cannot inflate it). Plus
    the composition of the touched population by breadth.
    """

    def _bucket(group: list[ResourceCrudRecord]) -> dict[str, Any]:
        exercised = [resource for resource in group if resource.exercised_operations]
        fully = [
            resource
            for resource in exercised
            if not resource.missing_available_operations
        ]
        return {
            "resource_count": len(group),
            "exercised_count": len(exercised),
            "exercised_rate": (len(exercised) / len(group)) if group else None,
            "fully_exercised_count": len(fully),
            "pct_fully_exercised_of_exercised": (
                100.0 * len(fully) / len(exercised) if exercised else None
            ),
        }

    single = [
        resource for resource in resources if len(resource.available_operations) == 1
    ]
    multi = [
        resource for resource in resources if len(resource.available_operations) > 1
    ]
    single_bucket = _bucket(single)
    multi_bucket = _bucket(multi)
    single_tested = single_bucket["exercised_count"]
    multi_tested = multi_bucket["exercised_count"]
    tested_total = sum(1 for resource in resources if resource.exercised_operations)
    # Tested resources whose endpoints expose no CRUD verb (so neither bucket); the
    # residual that keeps the two composition shares honest rather than summing to
    # 100 by construction.
    no_operation_tested = tested_total - single_tested - multi_tested
    return {
        "single_operation_available": single_bucket,
        "multi_operation_available": multi_bucket,
        "tested_composition": {
            "tested_resource_count": tested_total,
            "single_operation_available_count": single_tested,
            "multi_operation_available_count": multi_tested,
            "no_crud_operation_available_count": no_operation_tested,
            "pct_single_operation_available": (
                100.0 * single_tested / tested_total if tested_total else None
            ),
            "pct_multi_operation_available": (
                100.0 * multi_tested / tested_total if tested_total else None
            ),
        },
    }


def _exercised_combination_when_full_crud_capable(
    resources: Sequence[ResourceCrudRecord],
) -> dict[str, Any]:
    """Exercised-combination distribution among full-CRUD-capable resources.

    Conditioning on resources whose endpoints expose all four operations removes
    the availability ceiling — every combination is possible — so the exercised
    shape reflects test behavior alone, not the API surface. Resources no test
    exercised fall into none_exercised; the combination shares and that residual
    together cover the capable population.
    """
    capable = [
        resource
        for resource in resources
        if _FULL_CRUD <= set(resource.available_operations)
    ]
    counts = {combination: 0 for combination in CRUD_COMBINATIONS}
    none_exercised = 0
    fully_exercised = 0
    for resource in capable:
        exercised = _canonical_subset(resource.exercised_operations)
        if not exercised:
            none_exercised += 1
            continue
        counts[exercised] += 1
        # Full CRUD is available, so nothing missing means all four were exercised.
        if not resource.missing_available_operations:
            fully_exercised += 1
    capable_total = len(capable)
    tested = capable_total - none_exercised
    return {
        "scope": "full_crud_capable_production_resources",
        "capable_resource_count": capable_total,
        "none_exercised_count": none_exercised,
        "none_exercised_pct": (
            100.0 * none_exercised / capable_total if capable_total else None
        ),
        # Of the full-CRUD-capable resources a test targeted, the share driven
        # through all four verbs — thoroughness with the availability ceiling
        # removed and untargeted resources set aside.
        "tested_resource_count": tested,
        "fully_exercised_count": fully_exercised,
        "pct_fully_exercised_of_tested": (
            100.0 * fully_exercised / tested if tested else None
        ),
        "combinations": combination_entries(counts, CRUD_COMBINATIONS, capable_total),
    }


def _per_operation_exercise(resources: Sequence[ResourceCrudRecord]) -> dict[str, Any]:
    """For each CRUD operation, the share of resources offering it that exercise it."""
    per_operation: dict[str, Any] = {}
    for operation in CRUD_OPERATIONS:
        available_count = 0
        exercised_count = 0
        for resource in resources:
            if operation in resource.available_operations:
                available_count += 1
                if operation in resource.exercised_operations:
                    exercised_count += 1
        per_operation[operation] = {
            "available_resource_count": available_count,
            "exercised_resource_count": exercised_count,
            "rate": (exercised_count / available_count) if available_count else None,
        }
    return per_operation


def _read_only_when_writable(
    resources: Sequence[ResourceCrudRecord],
) -> dict[str, Any]:
    """Resources that offer a write but whose tests only read them.

    Reported against the tested-and-writable population so the denominator is
    explicit rather than assumed.
    """
    writable = [
        resource
        for resource in resources
        if WRITE_OPERATIONS & set(resource.available_operations)
    ]
    writable_tested = [
        resource for resource in writable if resource.exercised_operations
    ]
    read_only = [
        resource
        for resource in writable
        if set(resource.exercised_operations) == {_READ}
    ]
    return {
        "writable_resource_count": len(writable),
        "writable_tested_resource_count": len(writable_tested),
        "read_only_tested_count": len(read_only),
        "proportion_of_writable_tested": (
            len(read_only) / len(writable_tested) if writable_tested else None
        ),
    }


def _full_crud_tested(resources: Sequence[ResourceCrudRecord]) -> dict[str, Any]:
    """Resources with a full-CRUD test, against all resources and CRUD-capable ones."""
    full_crud_operations = set(CRUD_OPERATIONS)
    capable = [
        resource
        for resource in resources
        if full_crud_operations <= set(resource.available_operations)
    ]
    tested = [resource for resource in resources if resource.full_crud_test_count > 0]
    return {
        "full_crud_capable_resource_count": len(capable),
        "full_crud_tested_count": len(tested),
        "proportion_of_capable": (len(tested) / len(capable) if capable else None),
    }


def _verb_availability_profile(
    resources: Sequence[ResourceCrudRecord],
) -> dict[str, Any]:
    """Which HTTP verbs each production resource's endpoints expose: the
    availability ceiling at verb granularity. Single-verb profiles are keyed
    exhaustively (genuine zeros); the sparse multi-verb space is reported as
    observed combinations only, matching verb_combinations' choice."""
    capable = [resource for resource in resources if resource.available_verbs]
    single_counts = {verb: 0 for verb in CRUD_VERBS}
    multi_counts: Counter[tuple[str, ...]] = Counter()
    for resource in capable:
        if len(resource.available_verbs) == 1:
            single_counts[resource.available_verbs[0]] += 1
        else:
            multi_counts[resource.available_verbs] += 1
    total = len(capable)
    multi_total = sum(multi_counts.values())
    verb_rank = {verb: index for index, verb in enumerate(CRUD_VERBS)}
    multi_keys = sorted(
        multi_counts,
        key=lambda combination: (
            -multi_counts[combination],
            tuple(verb_rank[verb] for verb in combination),
        ),
    )
    return {
        "scope": "production_resources",
        "resource_count": len(resources),
        "verb_capable_resource_count": total,
        "no_verb_resource_count": len(resources) - total,
        "single_verb": {
            verb: {
                "count": single_counts[verb],
                "pct": (100.0 * single_counts[verb] / total) if total else None,
            }
            for verb in CRUD_VERBS
        },
        "expose_multiple_verbs": {
            "count": multi_total,
            "pct": (100.0 * multi_total / total) if total else None,
        },
        "multi_verb_profiles": {
            "-".join(combination): {
                "verbs": list(combination),
                "count": multi_counts[combination],
                "pct": (100.0 * multi_counts[combination] / total) if total else None,
            }
            for combination in multi_keys
        },
    }


def _verb_fully_exercised(
    resources: Sequence[ResourceCrudRecord],
) -> dict[str, Any]:
    """Share of verb-capable resources whose every available verb is exercised.
    Verb counterpart of _fully_exercised; fully is stricter here because hitting
    a CRUD class no longer implies hitting every verb in it (e.g. PUT but not
    PATCH)."""
    capable = [resource for resource in resources if resource.available_verbs]
    tested = [resource for resource in capable if resource.exercised_verbs]
    fully = [resource for resource in capable if not resource.missing_available_verbs]
    not_fully = len(capable) - len(fully)
    return {
        "verb_capable_resource_count": len(capable),
        "tested_resource_count": len(tested),
        "fully_exercised_count": len(fully),
        "not_fully_exercised_count": not_fully,
        "pct_fully_exercised_of_capable": (
            100.0 * len(fully) / len(capable) if capable else None
        ),
        "pct_not_fully_exercised_of_capable": (
            100.0 * not_fully / len(capable) if capable else None
        ),
        "pct_fully_exercised_of_tested": (
            100.0 * len(fully) / len(tested) if tested else None
        ),
    }


def _verb_exercise_by_availability_breadth(
    resources: Sequence[ResourceCrudRecord],
) -> dict[str, Any]:
    """Exercise split by how many HTTP verbs a resource's endpoints expose, the
    verb counterpart of _exercise_by_availability_breadth. The multi-verb bucket's
    fully-of-exercised share is the verb-level 'full lifecycle' completeness rate.

    The CRUD peer's tested_composition block (the single/multi/no-op split of the
    tested population) is intentionally omitted: it backs no verb-level claim, and
    the verb availability profile already reports the capable composition.
    """

    def _bucket(group: list[ResourceCrudRecord]) -> dict[str, Any]:
        exercised = [resource for resource in group if resource.exercised_verbs]
        fully = [
            resource for resource in exercised if not resource.missing_available_verbs
        ]
        return {
            "resource_count": len(group),
            "exercised_count": len(exercised),
            "exercised_rate": (len(exercised) / len(group)) if group else None,
            "fully_exercised_count": len(fully),
            "pct_fully_exercised_of_exercised": (
                100.0 * len(fully) / len(exercised) if exercised else None
            ),
        }

    single = [resource for resource in resources if len(resource.available_verbs) == 1]
    multi = [resource for resource in resources if len(resource.available_verbs) > 1]
    return {
        "single_verb_available": _bucket(single),
        "multi_verb_available": _bucket(multi),
    }


def _verb_full_traversal_within_single_sequence(
    resources: Sequence[ResourceCrudRecord],
) -> dict[str, Any]:
    """Among multi-verb resources a test targets, the share where one single
    (test, resource) sequence already covers every available verb.

    The strict, single-sequence reading of 'full lifecycle': not that the union of
    all targeting tests eventually reaches every verb (that is
    exercise_by_availability_breadth's multi-verb fully-of-exercised rate), but
    that one sequence walks the resource's whole verb surface end to end. The gap
    between the two measures how much full coverage is assembled across separate
    tests rather than traversed in a single flow. Scoped to resources exposing
    more than one verb with at least one exercising sequence, so single-verb
    resources (trivially covered) and untargeted ones do not dilute it.
    """
    multi_verb = [
        resource
        for resource in resources
        if len(resource.available_verbs) > 1 and resource.exercising_sequence_verb_sets
    ]
    fully_traversed = [
        resource
        for resource in multi_verb
        if any(
            set(resource.available_verbs) <= set(sequence_verbs)
            for sequence_verbs in resource.exercising_sequence_verb_sets
        )
    ]
    return {
        "scope": "multi_verb_resources_with_exercising_sequence",
        "resource_count": len(multi_verb),
        "fully_traversed_count": len(fully_traversed),
        "pct_fully_traversed_in_single_sequence": (
            100.0 * len(fully_traversed) / len(multi_verb) if multi_verb else None
        ),
    }


def _verb_exercise_when_full_crud_capable(
    resources: Sequence[ResourceCrudRecord],
) -> dict[str, Any]:
    """Among resources whose endpoints expose all four CRUD classes, the share
    fully exercised at verb granularity. Conditioning on the richest CRUD surface
    removes the availability ceiling; requiring every verb (not just every class)
    is the stricter verb-level reading of 'full lifecycle'."""
    capable = [
        resource
        for resource in resources
        if _FULL_CRUD <= set(resource.available_operations)
    ]
    tested = [resource for resource in capable if resource.exercised_verbs]
    fully = [resource for resource in tested if not resource.missing_available_verbs]
    return {
        "scope": "full_crud_capable_production_resources",
        "capable_resource_count": len(capable),
        "tested_resource_count": len(tested),
        "fully_exercised_count": len(fully),
        "pct_fully_exercised_of_tested": (
            100.0 * len(fully) / len(tested) if tested else None
        ),
    }


def compute(
    tests: Sequence[TestRecord],
    resources: Sequence[ResourceCrudRecord],
) -> dict[str, Any]:
    # Resource interaction is only analyzed for API tests; non-API tests would
    # dilute the per-test distributions with structural zeros. The test-side
    # lifecycle/method distributions run over the endpoints+API-tests gate, while
    # resources are the narrower dispatch-resolved population (see runner), so an
    # untested resource here is a genuine gap rather than an unmappable project.
    api_tests = [test for test in tests if test.is_api_test]
    return {
        "scope": "endpoints_and_api_tests",
        "api_test_count": len(api_tests),
        "resource_count": len(resources),
        "tested": share(
            bool(resource.exercised_operations) for resource in resources
        ).to_dict(),
        "lifecycle_label_distribution": _lifecycle_label_distribution(api_tests),
        "per_test": _per_test_lifecycle(api_tests),
        "resource_count_per_test": _resource_count_per_test(api_tests),
        "http_method_distribution": _http_method_distribution(api_tests),
        "crud_operation_distribution": _crud_operation_distribution(api_tests),
        "exercised_completeness": _exercised_completeness(resources),
        "availability_profile_distribution": _availability_profile_distribution(
            resources
        ),
        "fully_exercised": _fully_exercised(resources),
        "exercise_by_availability_breadth": _exercise_by_availability_breadth(
            resources
        ),
        "exercised_combination_when_full_crud_capable": (
            _exercised_combination_when_full_crud_capable(resources)
        ),
        "per_operation_exercise": _per_operation_exercise(resources),
        "read_only_when_writable": _read_only_when_writable(resources),
        "full_crud_tested": _full_crud_tested(resources),
        # HTTP-verb view of the same production-resource population: availability,
        # completeness, and breadth at verb granularity (PUT/PATCH and GET/HEAD
        # kept distinct), so it reads against the CRUD coverage above.
        "verb_resource_exercise": {
            "scope": "production_resources_http_verb_view",
            "availability_profile": _verb_availability_profile(resources),
            "fully_exercised": _verb_fully_exercised(resources),
            "exercise_by_availability_breadth": (
                _verb_exercise_by_availability_breadth(resources)
            ),
            "full_traversal_within_single_sequence": (
                _verb_full_traversal_within_single_sequence(resources)
            ),
            "exercise_when_full_crud_capable": (
                _verb_exercise_when_full_crud_capable(resources)
            ),
        },
    }
