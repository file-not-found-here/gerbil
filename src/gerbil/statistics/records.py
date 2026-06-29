"""Compact, picklable per-project records projected from a full ProjectAnalysis.

Projecting each analysis down to these records in the loader worker keeps peak
memory and cross-process IPC small when pooling hundreds of large gerbil.json
outputs, while exposing exactly the fields the statistics modules consume.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from gerbil.analysis.http.classification import BUILDER_CONTRIBUTED_PROPERTY_NAMES
from gerbil.analysis.properties.endpoint.coverage import (
    build_endpoint_candidate_matcher,
    build_endpoint_coverage_summary,
)
from gerbil.analysis.properties.endpoint.parameter_analysis import (
    build_endpoint_parameter_coverage_summary,
)
from gerbil.analysis.properties.resource_interaction.crud_analysis import (
    crud_operation_for_http_method,
    enrich_resource_interaction_sequence,
)
from gerbil.analysis.properties.resource_interaction.path_normalization import (
    normalize_production_resource_key,
    normalize_request_path,
)
from gerbil.analysis.schema import (
    ApiSequenceStep,
    AssertionRole,
    AuthHandling,
    CallSiteOriginKind,
    CrudLifecycleLabel,
    CrudOperation,
    EndpointCandidate,
    EndpointCoverageEntry,
    EndpointParameterCoverageEntry,
    EndpointParameterSource,
    HttpDispatchFramework,
    HttpRequestRole,
    HttpResponseRole,
    HttpTestSequence,
    LifecyclePhase,
    PreconditionType,
    ProductionResourceCrudEntry,
    ProjectAnalysis,
    ResourceInteractionSequence,
    ResourceInteractionStep,
    SequenceStepKind,
    StateObservationMedium,
    TestClassAnalysis,
    TestMethodAnalysis,
)

# Auth-handling labels in schema declaration order, for stable distribution output.
AUTH_HANDLING_LABELS: tuple[str, ...] = tuple(label.value for label in AuthHandling)

# Origin buckets after folding fixture-helper into fixture, in display order.
ORIGIN_BUCKETS: tuple[str, ...] = ("test-method", "test-helper", "fixture")

# Lifecycle phases the fixture origin bucket splits into. A fixture-kind origin
# is, by construction, only ever a setup or teardown phase (the test phase maps
# to the test-method/test-helper buckets), so these two phases partition it.
FIXTURE_PHASE_BUCKETS: tuple[str, ...] = ("setup", "teardown")

# Status-range buckets, aligned with StatusCodeDistribution.to_bucket_counts().
STATUS_RANGE_KEYS: tuple[str, ...] = ("1xx", "2xx", "3xx", "4xx", "5xx", "unknown")

# CRUD operations in canonical create/read/update/delete order.
CRUD_OPERATIONS: tuple[str, ...] = tuple(operation.value for operation in CrudOperation)

# HTTP request methods in canonical order; any other or unresolved method (the
# pipeline emits "UNKNOWN" when it cannot determine one) folds into "UNKNOWN".
HTTP_METHODS: tuple[str, ...] = (
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "TRACE",
    "CONNECT",
    "UNKNOWN",
)

# The write operations (everything but read); gates the "writable resource" stats.
WRITE_OPERATIONS: frozenset[str] = frozenset(
    operation.value
    for operation in CrudOperation
    if operation is not CrudOperation.READ
)

# The CRUD-mappable HTTP methods kept distinct (CRUD merges GET/HEAD and
# PUT/PATCH). Derived from the CRUD mapping, so the verb- and CRUD-resolved
# populations coincide and only granularity differs.
CRUD_VERBS: tuple[str, ...] = tuple(
    method
    for method in HTTP_METHODS
    if crud_operation_for_http_method(method) is not None
)


def verb_for_http_method(http_method: str | None) -> str | None:
    """Uppercased method if CRUD maps it (PUT/PATCH stay distinct), else None."""
    if not http_method:
        return None
    verb = http_method.upper()
    return verb if crud_operation_for_http_method(verb) is not None else None


# CRUD lifecycle labels in declaration order, for stable label-distribution output.
LIFECYCLE_LABELS: tuple[str, ...] = tuple(label.value for label in CrudLifecycleLabel)

# HTTP dispatch frameworks in schema declaration order, for stable
# distribution output and aligned per-test call-site tallies.
HTTP_DISPATCH_FRAMEWORKS: tuple[str, ...] = tuple(
    framework.value for framework in HttpDispatchFramework
)

# State precondition/postcondition labels in schema declaration order.
PRECONDITION_TYPES: tuple[str, ...] = tuple(label.value for label in PreconditionType)
POSTCONDITION_TYPES: tuple[str, ...] = tuple(
    label.value for label in StateObservationMedium
)

# Verification response roles in schema declaration order, plus the "none"
# bucket for verifications with no HTTP response role (plain assertions on
# previously extracted values).
VERIFICATION_RESPONSE_ROLE_BUCKETS: tuple[str, ...] = (
    *(role.value for role in HttpResponseRole),
    "none",
)

# Builder-contributed property names in merge-pass emission order, sourced
# from the merge pass itself so the two layers cannot drift.
BUILDER_CONTRIBUTED_PROPERTIES: tuple[str, ...] = BUILDER_CONTRIBUTED_PROPERTY_NAMES

_HTTP_METHOD_INDEX: dict[str, int] = {
    method: index for index, method in enumerate(HTTP_METHODS)
}
_UNKNOWN_METHOD_INDEX: int = _HTTP_METHOD_INDEX["UNKNOWN"]
_CRUD_OPERATION_INDEX: dict[str, int] = {
    operation: index for index, operation in enumerate(CRUD_OPERATIONS)
}
_HTTP_DISPATCH_FRAMEWORK_INDEX: dict[str, int] = {
    framework: index for index, framework in enumerate(HTTP_DISPATCH_FRAMEWORKS)
}
_VERIFICATION_RESPONSE_ROLE_INDEX: dict[str, int] = {
    role: index for index, role in enumerate(VERIFICATION_RESPONSE_ROLE_BUCKETS)
}
_NONE_RESPONSE_ROLE_INDEX: int = _VERIFICATION_RESPONSE_ROLE_INDEX["none"]
_ZERO_RESPONSE_ROLE_COUNTS: tuple[int, ...] = (0,) * len(
    VERIFICATION_RESPONSE_ROLE_BUCKETS
)
# Default for the per-test framework tallies; full-length so consumers zipping
# against HTTP_DISPATCH_FRAMEWORKS never silently truncate.
_ZERO_FRAMEWORK_COUNTS: tuple[int, ...] = (0,) * len(HTTP_DISPATCH_FRAMEWORKS)
_FIXTURE_ORIGIN_BUCKET_INDEX: int = ORIGIN_BUCKETS.index("fixture")


def _http_method_index(http_method: str) -> int:
    return _HTTP_METHOD_INDEX.get(http_method.upper(), _UNKNOWN_METHOD_INDEX)


def _fixture_phase_index(phase: LifecyclePhase) -> int:
    # Reached only for fixture-kind origins, which are always setup or teardown.
    return 0 if phase == LifecyclePhase.SETUP else 1


@dataclass(slots=True, frozen=True)
class BuilderGroup:
    """Builders correlated to one dispatched request event (a per-dispatch builder
    chain); each entry is one builder's contributed property names."""

    builders: tuple[tuple[str, ...], ...]


setattr(BuilderGroup, "__test__", False)


@dataclass(slots=True)
class TestRecord:
    is_api_test: bool
    is_controller_unit_test: bool
    expanded_ncloc: int
    expanded_cyclomatic_complexity: int
    expanded_helper_method_count: int
    # Distinct test-body helper methods (fixture helpers excluded, no double count).
    test_helper_method_count: int
    expanded_objects_created: int
    expanded_assertion_count: int
    mocked_interaction_count: int
    dependency_strategy_label_count: int
    dispatch_labels: tuple[str, ...]
    has_read_after_write: bool
    has_cleanup_delete: bool
    # CRUD lifecycle label of every (test, resource) sequence this test drives.
    resource_lifecycle_labels: tuple[str, ...]
    # Setup/teardown fixture methods attached to the test (the test phase itself
    # is never a fixture, so only setup and teardown are counted).
    setup_fixture_count: int
    teardown_fixture_count: int
    # Counts aligned with STATUS_RANGE_KEYS / ORIGIN_BUCKETS respectively.
    status_range_counts: tuple[int, ...]
    builder_counts: tuple[int, ...]
    event_counts: tuple[int, ...]
    verification_counts: tuple[int, ...]
    # Fixture-bucket counts split by lifecycle phase, each aligned with
    # FIXTURE_PHASE_BUCKETS (setup, teardown); they sum to the folded fixture
    # entry of builder_counts / event_counts / verification_counts respectively.
    fixture_builder_phase_counts: tuple[int, ...]
    fixture_event_phase_counts: tuple[int, ...]
    fixture_verification_phase_counts: tuple[int, ...]
    # Per-test tallies of dispatched request events, aligned with HTTP_METHODS /
    # CRUD_OPERATIONS respectively; the CRUD tally is derived from each request's
    # HTTP method, so methods with no CRUD mapping contribute to neither bucket.
    http_method_counts: tuple[int, ...]
    crud_operation_counts: tuple[int, ...]
    # Per-test tallies by dispatch framework, aligned with
    # HTTP_DISPATCH_FRAMEWORKS: every HTTP call site (builders and events),
    # and dispatched request events only.
    http_call_framework_counts: tuple[int, ...] = _ZERO_FRAMEWORK_COUNTS
    http_event_framework_counts: tuple[int, ...] = _ZERO_FRAMEWORK_COUNTS
    dependency_strategy_labels: tuple[str, ...] = ()
    http_sequence_count: int = 0
    http_sequence_lengths: tuple[int, ...] = ()
    http_sequence_request_build_counts: tuple[int, ...] = ()
    http_sequence_http_request_counts: tuple[int, ...] = ()
    http_sequence_request_side_lengths: tuple[int, ...] = ()
    http_sequence_response_check_lengths: tuple[int, ...] = ()
    # Per-sequence response checks resolved by assertion role, each aligned with
    # http_sequence_response_check_lengths. They count only sequenced checks (a
    # response-check step inside a dispatch chain), so they sum to at most the
    # test's assertion summary count for that role; assertions not tied to a
    # dispatch (e.g. checks on previously extracted values) are excluded.
    http_sequence_status_check_counts: tuple[int, ...] = ()
    http_sequence_body_check_counts: tuple[int, ...] = ()
    http_sequence_header_check_counts: tuple[int, ...] = ()
    # Resolved status range of every sequenced STATUS response-check step, grouped
    # per sequence (aligned with http_sequence_status_check_counts); 'unknown' when
    # the asserted code/range could not be recovered. Sequenced checks only, so it
    # excludes status assertions not tied to a dispatch.
    http_sequence_status_ranges: tuple[tuple[str, ...], ...] = ()
    # Distinct CRUD operations of each sequence's dispatched requests, aligned
    # with http_sequence_response_check_lengths; empty when no request maps to one.
    http_sequence_crud_operations: tuple[tuple[str, ...], ...] = ()
    # Same, at HTTP-verb granularity (PUT/PATCH and GET/HEAD kept distinct).
    http_sequence_verb_operations: tuple[tuple[str, ...], ...] = ()
    # Counts aligned by sequenced request dispatch event: request builders before
    # the dispatch, and response checks after it within the same sequence.
    dispatch_event_request_builder_counts: tuple[int, ...] = ()
    dispatch_event_response_check_counts: tuple[int, ...] = ()
    http_sequence_response_check_count: int = 0
    has_multiple_http_sequences: bool = False
    # A sequence fingerprint repeated within this test's own sequences.
    has_repeated_http_sequence: bool = False
    # A sequence fingerprint this test shares with another API test in the same
    # project (cross-test duplication, e.g. copy-pasted request/assert chains).
    has_shared_http_sequence: bool = False
    distinct_endpoint_count: int = 0
    # True when one method+path endpoint is dispatched by more than one of the
    # test's sequences (an endpoint re-dispatch); keyed exactly like
    # distinct_endpoint_count, so re-dispatch implies dispatches > distinct.
    re_dispatches_endpoint: bool = False
    # True when the test dispatches at least one request and every dispatched
    # request resolves to a method and path; gates the duplication analysis so
    # unresolved dispatches (which fingerprint to "*") cannot inflate it.
    all_dispatch_events_resolved: bool = False
    # Distinct HTTP verbs across the test's dispatched request events, folded over
    # all sequences and phases; deduplicated, so a repeated verb counts once and
    # unresolved (UNKNOWN) verbs contribute nothing.
    distinct_http_method_count: int = 0
    # Distinct normalized resources the test's request events target across all
    # lifecycle phases (fixture requests exercise the same system under test),
    # and the subset reached by at least one test-phase request.
    distinct_resource_count: int = 0
    test_phase_distinct_resource_count: int = 0
    # Scope-Sankey variants of the two counts above: a resource is counted only
    # when at least one request on it resolved an HTTP method (no-method events
    # are treated as path-less), so every counted resource carries a method+path
    # endpoint. Consumed solely by the test_scope distribution.
    method_resolved_distinct_resource_count: int = 0
    method_resolved_test_phase_distinct_resource_count: int = 0
    # Distinct CRUD operations (canonical order) of every fully CRUD-resolved
    # (test, resource) sequence this test drives: one tuple per sequence whose
    # every request mapped to a CRUD operation. Drives the CRUD-combination
    # distribution; sequences with an unmapped method are excluded here and
    # tallied in unresolved_resource_crud_sequence_count instead.
    resource_crud_combinations: tuple[tuple[str, ...], ...] = ()
    unresolved_resource_crud_sequence_count: int = 0
    # HTTP-verb analog of the two fields above: one verb-combination tuple per
    # (test, resource) sequence whose every request resolved to a verb, and the
    # count of sequences excluded by the shared resolution rule. Because that rule
    # is identical to CRUD's, the resolved/unresolved split matches exactly.
    resource_verb_combinations: tuple[tuple[str, ...], ...] = ()
    unresolved_resource_verb_sequence_count: int = 0
    assertion_status_count: int = 0
    assertion_body_count: int = 0
    assertion_header_count: int = 0
    assertion_general_count: int = 0
    assertion_exception_count: int = 0
    response_surface_combination: str = "none"
    oracle_type_label: str = "implicit"
    has_exception_assertion: bool = False
    status_code_counts: dict[str, int] = field(default_factory=dict)
    precondition_types: tuple[str, ...] = ()
    postcondition_types: tuple[str, ...] = ()
    auth_handling_label: str = AuthHandling.NONE.value
    is_parameterized: bool = False
    # Parameterized-source annotation short names, split by source kind.
    parameterization_static_sources: tuple[str, ...] = ()
    parameterization_dynamic_sources: tuple[str, ...] = ()
    # Verification tallies aligned with VERIFICATION_RESPONSE_ROLE_BUCKETS.
    verification_response_role_counts: tuple[int, ...] = _ZERO_RESPONSE_ROLE_COUNTS
    response_extraction_count: int = 0
    # Construction surface over dispatched request events (post-builder-merge).
    request_events_with_body: int = 0
    request_events_with_headers: int = 0
    request_events_with_query_params: int = 0
    request_events_with_path_params: int = 0
    request_events_with_form_params: int = 0
    request_events_with_builder_correlation: int = 0
    # Per-event resolved-name counts; "with headers" can exceed nonzero entries
    # here when only raw header expressions (no names) were recovered.
    event_query_param_counts: tuple[int, ...] = ()
    event_header_name_counts: tuple[int, ...] = ()
    # Resolved request-header names and their occurrence counts across the test's
    # full runtime view (every request interaction and fixture, builder and event
    # roles), as (name, count) pairs sorted by name. Header names are
    # case-insensitive per HTTP, so they fold to lower case; only names the
    # pipeline resolved appear, making the vocabulary a lower-bound estimate. The
    # keys are the test's distinct vocabulary; the counts give occurrence weight.
    runtime_header_name_counts: tuple[tuple[str, int], ...] = ()
    # One group per dispatched event that has correlated builders; conditions
    # builder-type stats on what the rest of the same event's chain contributed.
    builder_groups: tuple[BuilderGroup, ...] = ()


# Prevent pytest from treating the "Test"-prefixed record as a test class.
setattr(TestRecord, "__test__", False)


def request_event_total(test: TestRecord) -> int:
    """Total dispatched request events across all ORIGIN_BUCKETS."""
    return sum(test.event_counts)


@dataclass(slots=True)
class TestClassRecord:
    # TestingFramework values detected on the class, deduplicated upstream.
    testing_frameworks: tuple[str, ...]
    # API test methods declared on the class; >0 means the class has API tests.
    api_test_count: int = 0
    # Setup/teardown fixture methods declared on the class.
    fixture_count: int = 0


setattr(TestClassRecord, "__test__", False)


@dataclass(slots=True)
class EndpointRecord:
    """Per-endpoint binding surface and asserted outcomes projected from one
    coverage entry.

    The by-source dicts are keyed by EndpointParameterSource value and only hold
    sources present on the endpoint; a missing source means a count of 0.
    """

    covering_test_count: int
    route_depth: int
    path_variable_count: int
    has_body: bool
    parameter_count_by_source: dict[str, int]
    required_count_by_source: dict[str, int]
    optional_count_by_source: dict[str, int]
    # Wildcard-method endpoints carry an "UNKNOWN" method with the flag set.
    http_method: str = "UNKNOWN"
    is_method_wildcard: bool = False
    attributed_request_count: int = 0
    status_asserted_request_count: int = 0
    asserting_test_count: int = 0
    asserted_status_range_counts: dict[str, int] = field(default_factory=dict)
    asserted_status_code_counts: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class EndpointParameterRecord:
    """Per-endpoint parameter-exercise rates projected from one coverage entry.

    Every rate is None when its denominator is 0 (no parameters of that kind to
    exercise), kept distinct from a genuine 0.0 so distribution analysis can drop
    N/A without conflating it with 0%. The by-source dicts are keyed by parameter
    source value and a source maps to None for the same reason.
    """

    # Distinct tests whose request matched this endpoint's route; >0 means at
    # least one test targets the endpoint.
    route_covering_test_count: int
    # Overall exercise rate across every parameter on the endpoint.
    exercise_rate: float | None
    optional_exercise_rate: float | None
    required_exercise_rate: float | None
    simple_1_way_optional_coverage: float | None
    simple_2_way_optional_coverage: float | None
    total_2_way_optional_coverage: float | None
    exercise_rate_by_source: dict[str, float | None]
    optional_exercise_rate_by_source: dict[str, float | None]
    simple_1_way_optional_coverage_by_source: dict[str, float | None]
    simple_2_way_optional_coverage_by_source: dict[str, float | None]
    total_2_way_optional_coverage_by_source: dict[str, float | None]
    # Raw per-source optional-parameter counts, retained so distributions can
    # pool a parameter-level (micro) exercise rate, not just average per-endpoint
    # rates. Keyed by parameter source value; a missing source means 0.
    optional_count_by_source: dict[str, int]
    optional_exercised_count_by_source: dict[str, int]


@dataclass(slots=True)
class ResourceCrudRecord:
    """Per-production-resource CRUD coverage projected from one resource entry.

    Operation tuples hold CrudOperation values; missing_available_operations are
    the available operations no test exercised, so available - missing is the
    set of available operations that were exercised.
    """

    available_operations: tuple[str, ...]
    exercised_operations: tuple[str, ...]
    missing_available_operations: tuple[str, ...]
    full_crud_test_count: int
    # HTTP-verb analog of the operation tuples above. available_verbs come from
    # the resource's endpoint methods (the same non-wildcard, CRUD-mappable
    # endpoints that back available_operations); exercised_verbs are the verbs the
    # exercising tests actually dispatched against it. available - missing is the
    # exercised-and-available set, so a resource is fully exercised at verb
    # granularity when missing_available_verbs is empty.
    available_verbs: tuple[str, ...] = ()
    exercised_verbs: tuple[str, ...] = ()
    missing_available_verbs: tuple[str, ...] = ()
    # The verbs each distinct (test, resource) sequence dispatched against the
    # resource (canonical order, one tuple per sequence). exercised_verbs is their
    # union; these stay unmerged so a caller can tell full coverage reached within
    # one sequence apart from coverage only assembled across all targeting tests.
    exercising_sequence_verb_sets: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class GroupedResourceSequenceRecord:
    """One (test, resource) sequence's CRUD/verb shape and lifecycle flags.

    ``crud_combination``/``verb_combination`` are empty when the group is
    unresolved (a step's HTTP method does not map to a CRUD verb).
    ``resolved_to_production`` is True when every step matched a production
    endpoint so the group is keyed by its production resource key.
    """

    crud_combination: tuple[str, ...]
    verb_combination: tuple[str, ...]
    has_read_after_write: bool
    has_cleanup_delete: bool
    resolved_to_production: bool = True


@dataclass(slots=True)
class ProjectStatsRecord:
    dataset_name: str
    tests: tuple[TestRecord, ...]
    test_classes: tuple[TestClassRecord, ...]
    endpoints: tuple[EndpointRecord, ...]
    endpoint_parameters: tuple[EndpointParameterRecord, ...]
    resources: tuple[ResourceCrudRecord, ...]
    # Production (non-test) class and method counts for the whole project.
    application_class_count: int = 0
    application_method_count: int = 0
    # Endpoint coverage recomputed with known SAINT deploy-time context-path
    # prefixes stripped; mirrors `endpoints` for projects without such a prefix.
    # SAINT-comparison only — see saint_comparison statistics.
    saint_comparison_endpoints: tuple[EndpointRecord, ...] = ()
    # Endpoint-parameter coverage recomputed with SAINT context-path prefixes, so
    # the covered subpopulation includes endpoints the baseline attribution drops;
    # mirrors `endpoint_parameters` for projects without such a prefix.
    # SAINT-comparison only — see the parameter_exercise saint_comparison block.
    saint_comparison_endpoint_parameters: tuple[EndpointParameterRecord, ...] = ()
    # Resource sequences grouped two ways: by observed request path (the baseline,
    # matching resource_interaction) and by production resource key (each request
    # resolved to its endpoint, folding an instance write and its collection read
    # into one group). SAINT-comparison only — see production_resource_sequences.
    observed_resource_sequences: tuple[GroupedResourceSequenceRecord, ...] = ()
    production_resource_sequences: tuple[GroupedResourceSequenceRecord, ...] = ()


def _origin_bucket_index(kind: CallSiteOriginKind) -> int:
    if kind == CallSiteOriginKind.TEST_METHOD:
        return 0
    if kind == CallSiteOriginKind.TEST_HELPER:
        return 1
    # FIXTURE and FIXTURE_HELPER fold into a single "fixture" bucket.
    return 2


def _source_value_counts(
    by_source: Mapping[EndpointParameterSource, int],
) -> dict[str, int]:
    return {source.value: count for source, count in by_source.items()}


def _sequence_kind_count(steps: list[ApiSequenceStep], *kinds: SequenceStepKind) -> int:
    kind_set = set(kinds)
    return sum(1 for step in steps if step.kind in kind_set)


def _sequence_role_check_count(
    steps: list[ApiSequenceStep], role: AssertionRole
) -> int:
    """Response-check steps in a sequence carrying a given assertion role."""
    return sum(
        1
        for step in steps
        if step.kind == SequenceStepKind.RESPONSE_CHECK and step.assertion_role == role
    )


def _resolved_status_range(step: ApiSequenceStep) -> str:
    """Status range of a status response-check step; 'unknown' when the asserted
    code/range could not be recovered. Mirrors StatusCodeDistribution bucketing."""
    if step.status_range:
        return step.status_range
    if step.status_code is not None:
        return f"{step.status_code // 100}xx"
    return "unknown"


def _sequence_status_ranges(steps: list[ApiSequenceStep]) -> tuple[str, ...]:
    """Resolved status range of each STATUS response-check step in a sequence, in
    step order; one entry per status check (aligned with its status check count)."""
    return tuple(
        _resolved_status_range(step)
        for step in steps
        if step.kind == SequenceStepKind.RESPONSE_CHECK
        and step.assertion_role == AssertionRole.STATUS
    )


def _crud_value(http_method: str | None) -> str | None:
    operation = crud_operation_for_http_method(http_method)
    return operation.value if operation is not None else None


def _sequence_grouped_operations(
    steps: list[ApiSequenceStep],
    mapper: Callable[[str | None], str | None],
    order: tuple[str, ...],
) -> tuple[str, ...]:
    """Distinct mapped values of a sequence's dispatched requests, in `order`.
    Methods the mapper does not resolve (e.g. OPTIONS, or an unresolved verb)
    contribute nothing, so a sequence whose only request is one of those is empty."""
    values = {
        value
        for step in steps
        if step.kind == SequenceStepKind.HTTP_REQUEST
        and (value := mapper(step.http_method)) is not None
    }
    return tuple(value for value in order if value in values)


def _sequence_crud_operations(steps: list[ApiSequenceStep]) -> tuple[str, ...]:
    return _sequence_grouped_operations(steps, _crud_value, CRUD_OPERATIONS)


def _sequence_verb_operations(steps: list[ApiSequenceStep]) -> tuple[str, ...]:
    return _sequence_grouped_operations(steps, verb_for_http_method, CRUD_VERBS)


# Scope-Sankey resolution rule: a request event with no resolved HTTP method is
# treated as if it had no path, so it anchors neither a focal resource nor a
# focal endpoint. A resource the test_scope splits count therefore always carries
# at least one method+path endpoint, keeping the resource and endpoint stages
# consistent. The path-only resource definition used by every other statistic is
# left untouched (distinct_resource_count).
_UNRESOLVED_HTTP_METHOD = "UNKNOWN"


def _resource_step_method_resolved(step: ResourceInteractionStep) -> bool:
    return bool(step.http_method) and step.http_method != _UNRESOLVED_HTTP_METHOD


def _resolved_resource_combination(
    sequence: ResourceInteractionSequence,
    mapper: Callable[[str | None], str | None],
    order: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Distinct mapped values (in `order`) of a (test, resource) sequence, or None
    when it has no steps or any request's method does not map.

    None marks the sequence unresolved: with an unmapped method (e.g. OPTIONS or
    an unresolved verb) the value set is incomplete, so reporting a partial
    combination would understate it. Only fully mapped sequences are characterized.
    The CRUD and verb views share one resolution rule, so they include and exclude
    exactly the same sequences and differ only in granularity.
    """
    if not sequence.steps:
        return None
    values: set[str] = set()
    for step in sequence.steps:
        value = mapper(step.http_method)
        if value is None:
            return None
        values.add(value)
    return tuple(value for value in order if value in values)


def _resolved_resource_crud_combination(
    sequence: ResourceInteractionSequence,
) -> tuple[str, ...] | None:
    return _resolved_resource_combination(sequence, _crud_value, CRUD_OPERATIONS)


def _resolved_resource_verb_combination(
    sequence: ResourceInteractionSequence,
) -> tuple[str, ...] | None:
    return _resolved_resource_combination(sequence, verb_for_http_method, CRUD_VERBS)


def _re_dispatches_endpoint(test_sequences: list[HttpTestSequence]) -> bool:
    """True when one method+path endpoint is dispatched by more than one sequence.

    Endpoints are keyed exactly as build_http_sequence_summary keys
    distinct_endpoint_count (uppercased method, normalized path), so an event with
    no resolvable method or path is not counted.
    """
    endpoint_counts: Counter[str] = Counter()
    for sequence in test_sequences:
        for step in sequence.steps:
            if step.kind != SequenceStepKind.HTTP_REQUEST:
                continue
            method = (step.http_method or "").upper()
            normalized_path = normalize_request_path(step.http_path)
            if method and normalized_path is not None:
                endpoint_counts[f"{method} {normalized_path}"] += 1
    return any(count > 1 for count in endpoint_counts.values())


def _all_dispatch_events_resolved(test_sequences: list[HttpTestSequence]) -> bool:
    """True when the test dispatches at least one request and every dispatched
    request resolves to both an HTTP method and a path.

    Sequence fingerprints fall back to "*" for an unresolved method or path, so two
    sequences of otherwise-unresolved dispatches collapse to the same fingerprint
    and read as duplicates even when their endpoints are unknown. Gating the
    duplication analysis on this flag keeps that collapse out of the numbers.
    """
    saw_dispatch = False
    for sequence in test_sequences:
        for step in sequence.steps:
            if step.kind != SequenceStepKind.HTTP_REQUEST:
                continue
            saw_dispatch = True
            method = (step.http_method or "").upper()
            if not method or normalize_request_path(step.http_path) is None:
                return False
    return saw_dispatch


def _sequence_dispatch_fan_counts(
    steps: list[ApiSequenceStep],
) -> tuple[list[int], list[int]]:
    builder_counts: list[int] = []
    response_check_counts: list[int] = []
    for index, step in enumerate(steps):
        if step.kind != SequenceStepKind.HTTP_REQUEST:
            continue
        builder_counts.append(
            _sequence_kind_count(steps[:index], SequenceStepKind.REQUEST_BUILD)
        )
        response_check_counts.append(
            _sequence_kind_count(steps[index + 1 :], SequenceStepKind.RESPONSE_CHECK)
        )
    return builder_counts, response_check_counts


def project_test(
    test: TestMethodAnalysis, *, has_shared_http_sequence: bool = False
) -> TestRecord:
    http = test.http
    expanded = test.expanded_metrics
    assertions = test.assertions
    assertion_summary = assertions.summary

    builder_counts = [0, 0, 0]
    event_counts = [0, 0, 0]
    fixture_builder_phase_counts = [0, 0]
    fixture_event_phase_counts = [0, 0]
    http_method_counts = [0] * len(HTTP_METHODS)
    crud_operation_counts = [0] * len(CRUD_OPERATIONS)
    http_call_framework_counts = [0] * len(HTTP_DISPATCH_FRAMEWORKS)
    http_event_framework_counts = [0] * len(HTTP_DISPATCH_FRAMEWORKS)
    request_events_with_body = 0
    request_events_with_headers = 0
    request_events_with_query_params = 0
    request_events_with_path_params = 0
    request_events_with_form_params = 0
    request_events_with_builder_correlation = 0
    event_query_param_counts: list[int] = []
    event_header_name_counts: list[int] = []
    runtime_header_name_counts: Counter[str] = Counter()
    builder_groups: list[BuilderGroup] = []
    for interaction in http.request_interactions:
        call = interaction.http_call
        if call is None:
            continue
        # Header vocabulary spans every call role: a header set on a builder chain
        # configures the request just as one named on the dispatched event does.
        runtime_header_name_counts.update(
            name for raw in call.header_names if (name := raw.strip().lower())
        )
        framework_index = _HTTP_DISPATCH_FRAMEWORK_INDEX[call.framework.value]
        http_call_framework_counts[framework_index] += 1
        bucket = _origin_bucket_index(interaction.origin.kind)
        is_fixture = bucket == _FIXTURE_ORIGIN_BUCKET_INDEX
        if call.request_role == HttpRequestRole.BUILDER:
            builder_counts[bucket] += 1
            if is_fixture:
                fixture_builder_phase_counts[
                    _fixture_phase_index(interaction.origin.phase)
                ] += 1
        elif call.request_role == HttpRequestRole.EVENT:
            event_counts[bucket] += 1
            http_event_framework_counts[framework_index] += 1
            if is_fixture:
                fixture_event_phase_counts[
                    _fixture_phase_index(interaction.origin.phase)
                ] += 1
            http_method_counts[_http_method_index(call.http_method)] += 1
            operation = crud_operation_for_http_method(call.http_method)
            if operation is not None:
                crud_operation_counts[_CRUD_OPERATION_INDEX[operation.value]] += 1
            if call.has_body_payload:
                request_events_with_body += 1
            if call.header_names or call.headers:
                request_events_with_headers += 1
            if call.query_param_names:
                request_events_with_query_params += 1
            if call.path_param_names:
                request_events_with_path_params += 1
            if call.form_param_names:
                request_events_with_form_params += 1
            if call.correlated_builder_sources:
                request_events_with_builder_correlation += 1
            event_query_param_counts.append(len(call.query_param_names))
            event_header_name_counts.append(len(call.header_names))
            if call.correlated_builder_sources:
                builder_groups.append(
                    BuilderGroup(
                        builders=tuple(
                            tuple(source.contributed_properties)
                            for source in call.correlated_builder_sources
                        )
                    )
                )

    verification_counts = [0, 0, 0]
    fixture_verification_phase_counts = [0, 0]
    verification_response_role_counts = [0] * len(VERIFICATION_RESPONSE_ROLE_BUCKETS)
    for verification in http.verification_interactions:
        bucket = _origin_bucket_index(verification.origin.kind)
        verification_counts[bucket] += 1
        if bucket == _FIXTURE_ORIGIN_BUCKET_INDEX:
            fixture_verification_phase_counts[
                _fixture_phase_index(verification.origin.phase)
            ] += 1
        role_index = (
            _VERIFICATION_RESPONSE_ROLE_INDEX[verification.response_role.value]
            if verification.response_role is not None
            else _NONE_RESPONSE_ROLE_INDEX
        )
        verification_response_role_counts[role_index] += 1

    parameterization = test.identity.parameterization

    status_range = assertions.status_range_counts
    status_range_counts = tuple(status_range.get(key, 0) for key in STATUS_RANGE_KEYS)

    setup_fixture_count = sum(
        1 for fixture in test.fixtures if fixture.phase == LifecyclePhase.SETUP
    )
    teardown_fixture_count = sum(
        1 for fixture in test.fixtures if fixture.phase == LifecyclePhase.TEARDOWN
    )
    dispatch_event_request_builder_counts: list[int] = []
    dispatch_event_response_check_counts: list[int] = []
    for sequence in http.test_sequences:
        builder_fan_in, response_check_fan_out = _sequence_dispatch_fan_counts(
            sequence.steps
        )
        dispatch_event_request_builder_counts.extend(builder_fan_in)
        dispatch_event_response_check_counts.extend(response_check_fan_out)

    # Scope-Sankey resource counts: drop events with no resolved HTTP method
    # (treated as path-less), so a resource survives only with a method+path event.
    method_resolved_resource_sequences = [
        sequence
        for sequence in http.resource_interaction_sequences
        if any(_resource_step_method_resolved(step) for step in sequence.steps)
    ]

    resource_crud_combinations: list[tuple[str, ...]] = []
    unresolved_resource_crud_sequence_count = 0
    resource_verb_combinations: list[tuple[str, ...]] = []
    unresolved_resource_verb_sequence_count = 0
    for resource_sequence in http.resource_interaction_sequences:
        combination = _resolved_resource_crud_combination(resource_sequence)
        if combination is None:
            unresolved_resource_crud_sequence_count += 1
        else:
            resource_crud_combinations.append(combination)
        verb_combination = _resolved_resource_verb_combination(resource_sequence)
        if verb_combination is None:
            unresolved_resource_verb_sequence_count += 1
        else:
            resource_verb_combinations.append(verb_combination)

    return TestRecord(
        is_api_test=test.is_api_test,
        is_controller_unit_test=test.is_controller_unit_test,
        expanded_ncloc=expanded.ncloc,
        expanded_cyclomatic_complexity=expanded.cyclomatic_complexity,
        expanded_helper_method_count=expanded.helper_method_count,
        test_helper_method_count=expanded.test_helper_method_count,
        expanded_objects_created=expanded.number_of_objects_created,
        expanded_assertion_count=assertion_summary.total_count,
        assertion_status_count=assertion_summary.status_count,
        assertion_body_count=assertion_summary.body_count,
        assertion_header_count=assertion_summary.header_count,
        assertion_general_count=assertion_summary.general_count,
        assertion_exception_count=assertion_summary.exception_count,
        response_surface_combination=assertions.response_surface_combination,
        oracle_type_label=assertions.oracle_type.label,
        has_exception_assertion=assertions.failure_scenarios.has_exception_assertion,
        status_code_counts=dict(assertions.status_code_counts),
        mocked_interaction_count=len(http.mocked_interactions),
        dependency_strategy_label_count=len(test.dependencies.strategy.labels),
        dependency_strategy_labels=tuple(test.dependencies.strategy.labels),
        dispatch_labels=tuple(http.request_dispatch.labels),
        has_read_after_write=any(
            sequence.has_read_after_write
            for sequence in http.resource_interaction_sequences
        ),
        has_cleanup_delete=any(
            sequence.has_cleanup_delete
            for sequence in http.resource_interaction_sequences
        ),
        resource_lifecycle_labels=tuple(
            sequence.lifecycle_label.value
            for sequence in http.resource_interaction_sequences
        ),
        setup_fixture_count=setup_fixture_count,
        teardown_fixture_count=teardown_fixture_count,
        status_range_counts=status_range_counts,
        builder_counts=tuple(builder_counts),
        event_counts=tuple(event_counts),
        verification_counts=tuple(verification_counts),
        fixture_builder_phase_counts=tuple(fixture_builder_phase_counts),
        fixture_event_phase_counts=tuple(fixture_event_phase_counts),
        fixture_verification_phase_counts=tuple(fixture_verification_phase_counts),
        http_method_counts=tuple(http_method_counts),
        crud_operation_counts=tuple(crud_operation_counts),
        http_call_framework_counts=tuple(http_call_framework_counts),
        http_event_framework_counts=tuple(http_event_framework_counts),
        http_sequence_count=http.sequence_summary.sequence_count,
        http_sequence_lengths=tuple(http.sequence_summary.sequence_lengths),
        http_sequence_request_build_counts=tuple(
            _sequence_kind_count(sequence.steps, SequenceStepKind.REQUEST_BUILD)
            for sequence in http.test_sequences
        ),
        http_sequence_http_request_counts=tuple(
            _sequence_kind_count(sequence.steps, SequenceStepKind.HTTP_REQUEST)
            for sequence in http.test_sequences
        ),
        http_sequence_request_side_lengths=tuple(
            _sequence_kind_count(
                sequence.steps,
                SequenceStepKind.REQUEST_BUILD,
                SequenceStepKind.HTTP_REQUEST,
            )
            for sequence in http.test_sequences
        ),
        http_sequence_response_check_lengths=tuple(
            _sequence_kind_count(sequence.steps, SequenceStepKind.RESPONSE_CHECK)
            for sequence in http.test_sequences
        ),
        http_sequence_status_check_counts=tuple(
            _sequence_role_check_count(sequence.steps, AssertionRole.STATUS)
            for sequence in http.test_sequences
        ),
        http_sequence_body_check_counts=tuple(
            _sequence_role_check_count(sequence.steps, AssertionRole.BODY)
            for sequence in http.test_sequences
        ),
        http_sequence_header_check_counts=tuple(
            _sequence_role_check_count(sequence.steps, AssertionRole.HEADER)
            for sequence in http.test_sequences
        ),
        http_sequence_status_ranges=tuple(
            _sequence_status_ranges(sequence.steps) for sequence in http.test_sequences
        ),
        http_sequence_crud_operations=tuple(
            _sequence_crud_operations(sequence.steps)
            for sequence in http.test_sequences
        ),
        http_sequence_verb_operations=tuple(
            _sequence_verb_operations(sequence.steps)
            for sequence in http.test_sequences
        ),
        dispatch_event_request_builder_counts=tuple(
            dispatch_event_request_builder_counts
        ),
        dispatch_event_response_check_counts=tuple(
            dispatch_event_response_check_counts
        ),
        http_sequence_response_check_count=http.sequence_summary.response_check_step_count,
        has_multiple_http_sequences=http.sequence_summary.has_multiple_sequences,
        has_repeated_http_sequence=http.sequence_summary.has_repeated_sequence,
        has_shared_http_sequence=has_shared_http_sequence,
        distinct_endpoint_count=http.sequence_summary.distinct_endpoint_count,
        re_dispatches_endpoint=_re_dispatches_endpoint(http.test_sequences),
        all_dispatch_events_resolved=_all_dispatch_events_resolved(http.test_sequences),
        distinct_http_method_count=http.sequence_summary.distinct_http_method_count,
        distinct_resource_count=len(http.resource_interaction_sequences),
        test_phase_distinct_resource_count=sum(
            1
            for sequence in http.resource_interaction_sequences
            if any(step.phase == LifecyclePhase.TEST for step in sequence.steps)
        ),
        method_resolved_distinct_resource_count=len(method_resolved_resource_sequences),
        method_resolved_test_phase_distinct_resource_count=sum(
            1
            for sequence in method_resolved_resource_sequences
            if any(
                step.phase == LifecyclePhase.TEST
                and _resource_step_method_resolved(step)
                for step in sequence.steps
            )
        ),
        resource_crud_combinations=tuple(resource_crud_combinations),
        unresolved_resource_crud_sequence_count=(
            unresolved_resource_crud_sequence_count
        ),
        resource_verb_combinations=tuple(resource_verb_combinations),
        unresolved_resource_verb_sequence_count=(
            unresolved_resource_verb_sequence_count
        ),
        precondition_types=tuple(
            precondition.type.value
            for precondition in test.state.preconditions.preconditions
        ),
        postcondition_types=tuple(
            observation.medium.value
            for observation in test.state.observations.observations
        ),
        auth_handling_label=http.auth_handling.label,
        is_parameterized=parameterization is not None,
        parameterization_static_sources=tuple(
            parameterization.signals.get("static", ())
            if parameterization is not None
            else ()
        ),
        parameterization_dynamic_sources=tuple(
            parameterization.signals.get("dynamic", ())
            if parameterization is not None
            else ()
        ),
        verification_response_role_counts=tuple(verification_response_role_counts),
        response_extraction_count=len(http.response_extractions),
        request_events_with_body=request_events_with_body,
        request_events_with_headers=request_events_with_headers,
        request_events_with_query_params=request_events_with_query_params,
        request_events_with_path_params=request_events_with_path_params,
        request_events_with_form_params=request_events_with_form_params,
        request_events_with_builder_correlation=request_events_with_builder_correlation,
        event_query_param_counts=tuple(event_query_param_counts),
        event_header_name_counts=tuple(event_header_name_counts),
        runtime_header_name_counts=tuple(sorted(runtime_header_name_counts.items())),
        builder_groups=tuple(builder_groups),
    )


# Deploy-time webapp context-path prefixes baked into the generated request URLs
# of the SAINT comparison corpus (the WAR/servlet mount, plus a JAX-RS
# @ApplicationPath where the mount sits in front of it and blocks the normal
# strip). They are absent from the source-derived endpoint templates, so SAINT's
# observed paths never attribute without removing them. SAINT-comparison only:
# these literals are specific to that corpus's deployments and must not be folded
# into general endpoint coverage.
SAINT_CONTEXT_PATH_PREFIXES: tuple[str, ...] = (
    "/web-1.1.49-SNAPSHOT",
    "/restcountries-2.0.6-SNAPSHOT/rest",
    "/daytrader",
)

_SAINT_CONTEXT_PATH_LEAD_SEGMENTS: tuple[str, ...] = tuple(
    prefix.strip("/").split("/")[0] for prefix in SAINT_CONTEXT_PATH_PREFIXES
)


def _analysis_has_saint_context_prefix(analysis: ProjectAnalysis) -> bool:
    """True when any observed request path carries a known SAINT context-path lead
    segment, gating the (costlier) coverage recomputation to projects it can move."""
    return any(
        candidate is not None
        and candidate.path
        and any(seg in candidate.path for seg in _SAINT_CONTEXT_PATH_LEAD_SEGMENTS)
        for test_class in analysis.test_class_analyses
        for test in test_class.test_method_analyses
        for interaction in test.http.request_interactions
        for candidate in (interaction.endpoint_candidate,)
    )


def _saint_comparison_endpoints(
    analysis: ProjectAnalysis,
    baseline_endpoints: tuple[EndpointRecord, ...],
) -> tuple[EndpointRecord, ...]:
    """Endpoint coverage recomputed with the known SAINT deploy-time context-path
    prefixes appended to the discovered @ApplicationPath prefixes, so SAINT's
    context-path-prefixed request URLs attribute to their endpoints. Projects with
    no such prefix reuse the baseline coverage. SAINT-comparison only."""
    if not _analysis_has_saint_context_prefix(analysis):
        return baseline_endpoints
    application_endpoints = [
        entry.endpoint for entry in analysis.endpoint_coverage.endpoints
    ]
    application_path_prefixes = (
        tuple(analysis.endpoint_coverage.discovered_application_paths)
        + SAINT_CONTEXT_PATH_PREFIXES
    )
    summary = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=analysis.test_class_analyses,
        application_path_prefixes=application_path_prefixes,
    )
    return tuple(project_endpoint(entry) for entry in summary.endpoints)


def _saint_comparison_endpoint_parameters(
    analysis: ProjectAnalysis,
    baseline_endpoint_parameters: tuple[EndpointParameterRecord, ...],
) -> tuple[EndpointParameterRecord, ...]:
    """Endpoint-parameter coverage recomputed with the known SAINT deploy-time
    context-path prefixes appended, so a context-path-prefixed request attributes
    to its endpoint and the covered subpopulation (which conditions the exercise
    and t-way metrics) includes the query-parameter-rich endpoints the baseline
    attribution drops. Projects with no such prefix reuse the baseline. The mirror
    of `_saint_comparison_endpoints` for parameter coverage. SAINT-comparison only."""
    if not _analysis_has_saint_context_prefix(analysis):
        return baseline_endpoint_parameters
    application_endpoints = [
        entry.endpoint for entry in analysis.endpoint_coverage.endpoints
    ]
    application_path_prefixes = (
        tuple(analysis.endpoint_coverage.discovered_application_paths)
        + SAINT_CONTEXT_PATH_PREFIXES
    )
    summary = build_endpoint_parameter_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=analysis.test_class_analyses,
        application_path_prefixes=application_path_prefixes,
    )
    return tuple(project_endpoint_parameter(entry) for entry in summary.endpoints)


def _grouped_record_from_sequence(
    sequence: ResourceInteractionSequence,
    *,
    resolved_to_production: bool,
) -> GroupedResourceSequenceRecord:
    return GroupedResourceSequenceRecord(
        crud_combination=_resolved_resource_crud_combination(sequence) or (),
        verb_combination=_resolved_resource_verb_combination(sequence) or (),
        has_read_after_write=sequence.has_read_after_write,
        has_cleanup_delete=sequence.has_cleanup_delete,
        resolved_to_production=resolved_to_production,
    )


def _production_resource_key_for_step(
    step: ResourceInteractionStep,
    matched_indices_for: Callable[[EndpointCandidate], set[int]],
    production_key_by_endpoint_index: Mapping[int, str | None],
    observed_fallback_key: str,
) -> tuple[str, bool]:
    """Resolve a step to its production resource key, or the observed key.

    Returns the production key with True only when the request matches endpoints
    that share exactly one production resource key; zero matches or a split
    across keys is ambiguous, so the step keeps its observed resource key so it
    is never merged on a guess."""
    candidate = EndpointCandidate(
        http_method=(step.http_method or _UNRESOLVED_HTTP_METHOD).upper(),
        path=step.path,
        source="resource-step",
    )
    production_keys = {
        production_key_by_endpoint_index[index]
        for index in matched_indices_for(candidate)
    }
    production_keys.discard(None)
    if len(production_keys) == 1:
        production_key = next(iter(production_keys))
        assert production_key is not None
        return production_key, True
    return observed_fallback_key, False


def _grouped_resource_sequences(
    analysis: ProjectAnalysis,
) -> tuple[
    tuple[GroupedResourceSequenceRecord, ...],
    tuple[GroupedResourceSequenceRecord, ...],
]:
    """Resource sequences grouped by observed path (baseline) and by production
    resource key. Production grouping re-keys every request onto its endpoint's
    resource key (context-path prefixes included for SAINT projects) and rebuilds
    each group with the same enrichment detection uses, so an instance write and
    its collection read-back land in one group. SAINT-comparison only."""
    application_endpoints = [
        entry.endpoint for entry in analysis.endpoint_coverage.endpoints
    ]
    application_path_prefixes = tuple(
        analysis.endpoint_coverage.discovered_application_paths
    )
    if _analysis_has_saint_context_prefix(analysis):
        application_path_prefixes += SAINT_CONTEXT_PATH_PREFIXES
    coverage_endpoints, matched_indices_for = build_endpoint_candidate_matcher(
        application_endpoints, application_path_prefixes
    )
    production_key_by_endpoint_index = {
        index: normalize_production_resource_key(endpoint.path_template)
        for index, endpoint in enumerate(coverage_endpoints)
    }

    observed: list[GroupedResourceSequenceRecord] = []
    production: list[GroupedResourceSequenceRecord] = []
    for test_class in analysis.test_class_analyses:
        for test in test_class.test_method_analyses:
            sequences = test.http.resource_interaction_sequences
            observed.extend(
                _grouped_record_from_sequence(sequence, resolved_to_production=True)
                for sequence in sequences
            )

            steps_by_production_key: dict[str, list[ResourceInteractionStep]] = {}
            resolved_by_production_key: dict[str, bool] = {}
            for sequence in sequences:
                for step in sequence.steps:
                    production_key, resolved = _production_resource_key_for_step(
                        step,
                        matched_indices_for,
                        production_key_by_endpoint_index,
                        sequence.resource_key,
                    )
                    steps_by_production_key.setdefault(production_key, []).append(step)
                    resolved_by_production_key[production_key] = (
                        resolved_by_production_key.get(production_key, True)
                        and resolved
                    )

            for production_key, steps in steps_by_production_key.items():
                grouped = ResourceInteractionSequence(
                    resource_key=production_key,
                    steps=sorted(steps, key=lambda step: step.event_order),
                )
                enrich_resource_interaction_sequence(grouped)
                production.append(
                    _grouped_record_from_sequence(
                        grouped,
                        resolved_to_production=resolved_by_production_key[
                            production_key
                        ],
                    )
                )

    return tuple(observed), tuple(production)


def project_endpoint(entry: EndpointCoverageEntry) -> EndpointRecord:
    surface = entry.endpoint.surface
    parameter_count_by_source = _source_value_counts(surface.parameter_count_by_source)
    outcomes = entry.asserted_outcomes
    return EndpointRecord(
        covering_test_count=entry.covering_test_method_count,
        route_depth=surface.route_depth,
        path_variable_count=surface.path_variable_count,
        has_body=parameter_count_by_source.get(EndpointParameterSource.BODY.value, 0)
        > 0,
        parameter_count_by_source=parameter_count_by_source,
        required_count_by_source=_source_value_counts(
            surface.required_parameter_count_by_source
        ),
        optional_count_by_source=_source_value_counts(
            surface.optional_parameter_count_by_source
        ),
        http_method=(entry.endpoint.http_method or "UNKNOWN").upper(),
        is_method_wildcard=entry.endpoint.is_method_wildcard,
        attributed_request_count=outcomes.attributed_request_count,
        status_asserted_request_count=outcomes.status_asserted_request_count,
        asserting_test_count=outcomes.asserting_test_method_count,
        asserted_status_range_counts=dict(outcomes.status_range_counts),
        asserted_status_code_counts=dict(outcomes.status_code_counts),
    )


def _rate_by_source(
    rates: Mapping[EndpointParameterSource, float | None],
) -> dict[str, float | None]:
    return {source.value: rate for source, rate in rates.items()}


def _count_by_source(
    counts: Mapping[EndpointParameterSource, int],
) -> dict[str, int]:
    return {source.value: count for source, count in counts.items()}


def project_endpoint_parameter(
    entry: EndpointParameterCoverageEntry,
) -> EndpointParameterRecord:
    return EndpointParameterRecord(
        route_covering_test_count=entry.route_covering_test_count,
        exercise_rate=entry.exercise_rate,
        optional_exercise_rate=entry.optional_exercise_rate,
        required_exercise_rate=entry.required_exercise_rate,
        simple_1_way_optional_coverage=entry.simple_1_way_optional_coverage,
        simple_2_way_optional_coverage=entry.simple_2_way_optional_coverage,
        total_2_way_optional_coverage=entry.total_2_way_optional_coverage,
        exercise_rate_by_source=_rate_by_source(entry.exercise_rate_by_source),
        optional_exercise_rate_by_source=_rate_by_source(
            entry.optional_exercise_rate_by_source
        ),
        simple_1_way_optional_coverage_by_source=_rate_by_source(
            entry.simple_1_way_optional_coverage_by_source
        ),
        simple_2_way_optional_coverage_by_source=_rate_by_source(
            entry.simple_2_way_optional_coverage_by_source
        ),
        total_2_way_optional_coverage_by_source=_rate_by_source(
            entry.total_2_way_optional_coverage_by_source
        ),
        optional_count_by_source=_count_by_source(
            entry.optional_parameter_count_by_source
        ),
        optional_exercised_count_by_source=_count_by_source(
            entry.optional_exercised_count_by_source
        ),
    )


def project_test_class(test_class: TestClassAnalysis) -> TestClassRecord:
    return TestClassRecord(
        testing_frameworks=tuple(
            framework.value for framework in test_class.testing_frameworks
        ),
        api_test_count=sum(
            1 for test in test_class.test_method_analyses if test.is_api_test
        ),
        fixture_count=len(test_class.fixtures),
    )


def _canonical_verbs(verbs: Iterable[str]) -> tuple[str, ...]:
    """The given verbs in canonical HTTP_METHODS order, keyed like CRUD_VERBS."""
    present = set(verbs)
    return tuple(verb for verb in CRUD_VERBS if verb in present)


def _exercising_sequence_verbs_by_resource(
    method_analyses: Sequence[TestMethodAnalysis],
    resources: Sequence[ProductionResourceCrudEntry],
) -> dict[str, tuple[frozenset[str], ...]]:
    """Per production resource, the verb set of each distinct (test, resource)
    sequence that exercises it, keyed by resource_key.

    The resource-CRUD analysis already resolved which (test, resource-key)
    sequences exercise each resource (exercising_test_resources_by_operation); this
    re-reads those sequences' request verbs, one set per distinct sequence, so it
    inherits the analysis's production-resource matching rather than re-deriving it.
    The union of a resource's sets is its exercised verbs; keeping them unmerged
    lets callers separate coverage reached within one sequence from coverage only
    assembled across the union of all targeting tests.
    """
    sequence_by_reference: dict[tuple[str, str, str], ResourceInteractionSequence] = {}
    for test in method_analyses:
        identity = test.identity
        for sequence in test.http.resource_interaction_sequences:
            sequence_by_reference[
                (
                    identity.defining_class_name,
                    identity.method_signature,
                    sequence.resource_key,
                )
            ] = sequence
    by_resource: dict[str, tuple[frozenset[str], ...]] = {}
    for entry in resources:
        sequence_verbs: dict[tuple[str, str, str], frozenset[str]] = {}
        for references in entry.exercising_test_resources_by_operation.values():
            for reference in references:
                key = (
                    reference.test_method.qualified_class_name,
                    reference.test_method.method_signature,
                    reference.resource_key,
                )
                matched_sequence = sequence_by_reference.get(key)
                if matched_sequence is None:
                    continue
                sequence_verbs[key] = frozenset(
                    verb
                    for step in matched_sequence.steps
                    if (verb := verb_for_http_method(step.http_method)) is not None
                )
        by_resource[entry.resource_key] = tuple(sequence_verbs.values())
    return by_resource


def project_resource_crud(
    entry: ProductionResourceCrudEntry,
    exercising_sequence_verb_sets: tuple[frozenset[str], ...] = (),
) -> ResourceCrudRecord:
    available_verbs = _canonical_verbs(
        endpoint.http_method.upper()
        for endpoint in entry.endpoints
        if not endpoint.is_method_wildcard
        and verb_for_http_method(endpoint.http_method) is not None
    )
    exercised_verbs = frozenset().union(*exercising_sequence_verb_sets)
    return ResourceCrudRecord(
        available_operations=tuple(
            operation.value for operation in entry.available_operations
        ),
        exercised_operations=tuple(
            operation.value for operation in entry.exercised_operations
        ),
        missing_available_operations=tuple(
            operation.value for operation in entry.missing_available_operations
        ),
        full_crud_test_count=entry.full_crud_test_count,
        available_verbs=available_verbs,
        exercised_verbs=_canonical_verbs(exercised_verbs),
        missing_available_verbs=_canonical_verbs(
            set(available_verbs) - exercised_verbs
        ),
        exercising_sequence_verb_sets=tuple(
            _canonical_verbs(verbs) for verbs in exercising_sequence_verb_sets
        ),
    )


def api_test_count(record: ProjectStatsRecord) -> int:
    return sum(1 for test in record.tests if test.is_api_test)


def has_resolved_endpoint_method_event(record: ProjectStatsRecord) -> bool:
    """True when an API test resolved at least one dispatched event to both an
    endpoint route and an HTTP method. distinct_endpoint_count counts only
    method+path-resolved events, so this separates projects where a test->endpoint
    mapping is possible from those where it never resolves (some frameworks)."""
    return any(
        test.is_api_test and test.distinct_endpoint_count > 0 for test in record.tests
    )


def _cross_test_duplicated_flags(
    method_analyses: Sequence[TestMethodAnalysis],
) -> list[bool]:
    """Per-test flags marking API tests that share a sequence fingerprint with
    another API test in the same project (cross-test sequence duplication)."""
    per_test_fingerprints = [
        (
            frozenset(sequence.fingerprint for sequence in test.http.test_sequences)
            if test.is_api_test
            else frozenset()
        )
        for test in method_analyses
    ]
    test_counts_by_fingerprint = Counter(
        fingerprint
        for fingerprints in per_test_fingerprints
        for fingerprint in fingerprints
    )
    return [
        any(test_counts_by_fingerprint[fingerprint] > 1 for fingerprint in fingerprints)
        for fingerprints in per_test_fingerprints
    ]


def project_project(analysis: ProjectAnalysis) -> ProjectStatsRecord:
    method_analyses = [
        test
        for test_class in analysis.test_class_analyses
        for test in test_class.test_method_analyses
    ]
    tests = tuple(
        project_test(test, has_shared_http_sequence=shared)
        for test, shared in zip(
            method_analyses, _cross_test_duplicated_flags(method_analyses)
        )
    )
    test_classes = tuple(
        project_test_class(test_class) for test_class in analysis.test_class_analyses
    )
    endpoints = tuple(
        project_endpoint(entry) for entry in analysis.endpoint_coverage.endpoints
    )
    endpoint_parameters = tuple(
        project_endpoint_parameter(entry)
        for entry in analysis.endpoint_parameter_coverage.endpoints
    )
    exercising_sequence_verbs_by_resource = _exercising_sequence_verbs_by_resource(
        method_analyses, analysis.resource_crud.resources
    )
    resources = tuple(
        project_resource_crud(
            entry,
            exercising_sequence_verbs_by_resource.get(entry.resource_key, ()),
        )
        for entry in analysis.resource_crud.resources
    )
    observed_resource_sequences, production_resource_sequences = (
        _grouped_resource_sequences(analysis)
    )
    return ProjectStatsRecord(
        dataset_name=analysis.dataset_name,
        tests=tests,
        test_classes=test_classes,
        endpoints=endpoints,
        endpoint_parameters=endpoint_parameters,
        resources=resources,
        application_class_count=analysis.application_class_count,
        application_method_count=analysis.application_method_count,
        saint_comparison_endpoints=_saint_comparison_endpoints(analysis, endpoints),
        saint_comparison_endpoint_parameters=_saint_comparison_endpoint_parameters(
            analysis, endpoint_parameters
        ),
        observed_resource_sequences=observed_resource_sequences,
        production_resource_sequences=production_resource_sequences,
    )
