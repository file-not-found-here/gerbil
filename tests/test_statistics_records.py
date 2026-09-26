from __future__ import annotations

import pytest

from gerbil.analysis.schema import (
    ApiSequenceStep,
    ApplicationEndpoint,
    AssertionSummary,
    AuthHandling,
    CallSiteOriginKind,
    CrudLifecycleLabel,
    CrudOperation,
    EndpointAssertedStatusOutcomes,
    EndpointParameterSource,
    HttpDispatchFramework,
    HttpRequestRole,
    HttpResponseRole,
    HttpTestSequence,
    LifecyclePhase,
    OriginContext,
    ParameterizationSummary,
    ProductionResourceCrudEntry,
    ProductionResourceCrudSummary,
    ResourceCrudTestReference,
    ResourceInteractionSequence,
    ResourceInteractionStep,
    SequenceStepKind,
    SourceSpan,
    StatusCodeDistribution,
    TestingFramework,
    TestMethodReference,
)
from gerbil.statistics.records import (
    AUTH_HANDLING_LABELS,
    CRUD_OPERATIONS,
    FIXTURE_PHASE_BUCKETS,
    HTTP_DISPATCH_FRAMEWORKS,
    HTTP_METHODS,
    ORIGIN_BUCKETS,
    STATUS_RANGE_KEYS,
    VERIFICATION_RESPONSE_ROLE_BUCKETS,
    BuilderGroup,
    TestRecord,
    project_endpoint_parameter,
    project_project,
    project_test,
)
from tests.statistics_builders import (
    api_test,
    body_param,
    class_analysis,
    endpoint_entry,
    endpoint_parameter_entry,
    fixture,
    form_param,
    header_param,
    mocked_interaction,
    non_api_test,
    project,
    query_param,
    request_interaction,
    resource_crud_entry,
    resource_crud_summary,
    response_extraction,
    verification_interaction,
)


def test_origin_buckets_and_status_keys_have_expected_order() -> None:
    assert AUTH_HANDLING_LABELS == tuple(label.value for label in AuthHandling)
    assert ORIGIN_BUCKETS == ("test-method", "test-helper", "fixture")
    assert FIXTURE_PHASE_BUCKETS == ("setup", "teardown")
    assert STATUS_RANGE_KEYS == ("1xx", "2xx", "3xx", "4xx", "5xx", "unknown")


def test_request_interactions_split_builders_and_events_by_origin() -> None:
    # Exercise every (origin kind x unit role) combination so the fixture /
    # fixture-helper fold into one bucket is proven for builders, events, and
    # verifications independently rather than by implicit code coverage.
    test = api_test(
        request_interactions=[
            request_interaction(
                CallSiteOriginKind.TEST_METHOD, HttpRequestRole.BUILDER
            ),
            request_interaction(CallSiteOriginKind.TEST_METHOD, HttpRequestRole.EVENT),
            request_interaction(CallSiteOriginKind.TEST_HELPER, HttpRequestRole.EVENT),
            request_interaction(CallSiteOriginKind.FIXTURE, HttpRequestRole.BUILDER),
            request_interaction(CallSiteOriginKind.FIXTURE, HttpRequestRole.EVENT),
            request_interaction(
                CallSiteOriginKind.FIXTURE_HELPER, HttpRequestRole.BUILDER
            ),
            request_interaction(
                CallSiteOriginKind.FIXTURE_HELPER, HttpRequestRole.EVENT
            ),
        ],
        verification_interactions=[
            verification_interaction(CallSiteOriginKind.TEST_METHOD),
            verification_interaction(CallSiteOriginKind.TEST_HELPER),
            verification_interaction(CallSiteOriginKind.FIXTURE),
            verification_interaction(CallSiteOriginKind.FIXTURE_HELPER),
        ],
    )

    record = project_test(test)

    # Fixture bucket (index 2) sums FIXTURE and FIXTURE_HELPER for each unit type.
    assert record.builder_counts == (1, 0, 2)
    assert record.event_counts == (1, 1, 2)
    assert record.verification_counts == (1, 1, 2)


def test_fixture_bucket_splits_units_by_setup_and_teardown_phase() -> None:
    # The fixture bucket folds FIXTURE and FIXTURE_HELPER kinds, then splits them
    # by lifecycle phase (setup, teardown); test-phase origins never contribute.
    test = api_test(
        request_interactions=[
            request_interaction(
                CallSiteOriginKind.TEST_METHOD, HttpRequestRole.BUILDER
            ),
            request_interaction(
                CallSiteOriginKind.FIXTURE,
                HttpRequestRole.BUILDER,
                phase=LifecyclePhase.SETUP,
            ),
            request_interaction(
                CallSiteOriginKind.FIXTURE_HELPER,
                HttpRequestRole.EVENT,
                phase=LifecyclePhase.SETUP,
            ),
            request_interaction(
                CallSiteOriginKind.FIXTURE,
                HttpRequestRole.EVENT,
                phase=LifecyclePhase.TEARDOWN,
            ),
        ],
        verification_interactions=[
            verification_interaction(
                CallSiteOriginKind.FIXTURE, phase=LifecyclePhase.SETUP
            ),
            verification_interaction(
                CallSiteOriginKind.FIXTURE_HELPER, phase=LifecyclePhase.TEARDOWN
            ),
            verification_interaction(
                CallSiteOriginKind.FIXTURE, phase=LifecyclePhase.TEARDOWN
            ),
        ],
    )

    record = project_test(test)

    # Phase tuples are (setup, teardown) and sum to the fixture bucket entry.
    assert record.fixture_builder_phase_counts == (1, 0)
    assert record.fixture_event_phase_counts == (1, 1)
    assert record.fixture_verification_phase_counts == (1, 2)
    assert sum(record.fixture_builder_phase_counts) == record.builder_counts[2]
    assert sum(record.fixture_event_phase_counts) == record.event_counts[2]
    assert (
        sum(record.fixture_verification_phase_counts) == record.verification_counts[2]
    )


def test_http_method_and_crud_counts_tally_dispatched_events() -> None:
    # Only dispatched events count (the BUILDER method is ignored), method
    # matching is case-insensitive, and a non-standard method folds into UNKNOWN
    # with no CRUD operation.
    test = api_test(
        request_interactions=[
            request_interaction(
                CallSiteOriginKind.TEST_METHOD,
                HttpRequestRole.BUILDER,
                http_method="DELETE",
            ),
            request_interaction(
                CallSiteOriginKind.TEST_METHOD,
                HttpRequestRole.EVENT,
                http_method="POST",
            ),
            request_interaction(
                CallSiteOriginKind.TEST_HELPER,
                HttpRequestRole.EVENT,
                http_method="get",
            ),
            request_interaction(
                CallSiteOriginKind.FIXTURE,
                HttpRequestRole.EVENT,
                http_method="PUT",
            ),
            request_interaction(
                CallSiteOriginKind.FIXTURE,
                HttpRequestRole.EVENT,
                http_method="TROLL",
            ),
        ],
    )

    record = project_test(test)

    methods = dict(zip(HTTP_METHODS, record.http_method_counts))
    assert methods["POST"] == 1
    assert methods["GET"] == 1
    assert methods["PUT"] == 1
    assert methods["DELETE"] == 0  # builder role is not a dispatched event
    assert methods["UNKNOWN"] == 1  # non-standard method folds in
    assert sum(record.http_method_counts) == 4

    operations = dict(zip(CRUD_OPERATIONS, record.crud_operation_counts))
    assert operations == {"create": 1, "read": 1, "update": 1, "delete": 0}


def test_http_call_framework_counts_tally_builders_and_events() -> None:
    test = api_test(
        request_interactions=[
            request_interaction(CallSiteOriginKind.TEST_METHOD, HttpRequestRole.EVENT),
            request_interaction(
                CallSiteOriginKind.TEST_METHOD, HttpRequestRole.BUILDER
            ),
            request_interaction(
                CallSiteOriginKind.FIXTURE,
                HttpRequestRole.EVENT,
                framework=HttpDispatchFramework.REST_ASSURED,
            ),
        ],
    )

    record = project_test(test)

    frameworks = dict(zip(HTTP_DISPATCH_FRAMEWORKS, record.http_call_framework_counts))
    assert frameworks["mockmvc"] == 2  # builder and event call sites both tally
    assert frameworks["rest-assured"] == 1
    assert sum(record.http_call_framework_counts) == 3

    events = dict(zip(HTTP_DISPATCH_FRAMEWORKS, record.http_event_framework_counts))
    assert events["mockmvc"] == 1  # the builder call site is not an event
    assert events["rest-assured"] == 1
    assert sum(record.http_event_framework_counts) == 2


def test_runtime_header_name_counts_fold_occurrences_across_roles() -> None:
    # Header vocabulary spans builder and event roles and every origin (including
    # fixtures), folds case-insensitively, drops blanks, and accumulates counts:
    # content-type appears on both a builder and an event, so it counts twice.
    test = api_test(
        request_interactions=[
            request_interaction(
                CallSiteOriginKind.TEST_METHOD,
                HttpRequestRole.BUILDER,
                header_names=["Content-Type", "Authorization"],
            ),
            request_interaction(
                CallSiteOriginKind.TEST_METHOD,
                HttpRequestRole.EVENT,
                header_names=["content-type", "X-Forwarded-For", " "],
            ),
            request_interaction(
                CallSiteOriginKind.FIXTURE,
                HttpRequestRole.EVENT,
                header_names=["Host"],
            ),
        ],
    )

    record = project_test(test)

    assert record.runtime_header_name_counts == (
        ("authorization", 1),
        ("content-type", 2),
        ("host", 1),
        ("x-forwarded-for", 1),
    )


def test_framework_count_defaults_stay_aligned_with_dispatch_frameworks() -> None:
    # Hand-built records rely on the defaults; a short tuple would silently
    # vanish from consumers that zip against HTTP_DISPATCH_FRAMEWORKS.
    fields = TestRecord.__dataclass_fields__
    zero_counts = (0,) * len(HTTP_DISPATCH_FRAMEWORKS)
    assert fields["http_call_framework_counts"].default == zero_counts
    assert fields["http_event_framework_counts"].default == zero_counts


def test_mocked_and_dependency_and_lifecycle_projection() -> None:
    test = api_test(
        mocked_interactions=[
            mocked_interaction(CallSiteOriginKind.TEST_METHOD),
            mocked_interaction(CallSiteOriginKind.FIXTURE),
        ],
        dependency_labels=["mocked", "containerized"],
        auth_handling_label="mocked",
        resource_sequences=[
            ResourceInteractionSequence(resource_key="a", has_read_after_write=False),
            ResourceInteractionSequence(
                resource_key="b", has_read_after_write=True, has_cleanup_delete=True
            ),
        ],
    )

    record = project_test(test)

    assert record.mocked_interaction_count == 2
    assert record.dependency_strategy_label_count == 2
    assert record.dependency_strategy_labels == ("mocked", "containerized")
    assert record.auth_handling_label == "mocked"
    assert record.has_read_after_write is True
    assert record.has_cleanup_delete is True


def test_status_range_counts_align_with_keys_and_expanded_assertion_count() -> None:
    test = api_test(
        status_distribution=StatusCodeDistribution(range_2xx=3, range_4xx=1),
        assertion_summary=AssertionSummary(
            status_count=4, body_count=2, header_count=1, general_count=5
        ),
        oracle_type_label="example-based",
        status_code_counts={"200": 3, "404": 1},
    )

    record = project_test(test)

    assert record.status_range_counts == (0, 3, 0, 1, 0, 0)
    assert record.expanded_assertion_count == 12
    assert record.assertion_status_count == 4
    assert record.assertion_body_count == 2
    assert record.assertion_header_count == 1
    assert record.assertion_general_count == 5
    assert record.response_surface_combination == "status+body+header"
    assert record.oracle_type_label == "example-based"
    assert record.status_code_counts == {"200": 3, "404": 1}


def test_non_api_test_keeps_metrics_without_http_units() -> None:
    test = non_api_test(
        is_controller_unit_test=True,
        expanded_ncloc=12,
        expanded_cc=4,
        helper_method_count=1,
        objects_created=2,
    )

    record = project_test(test)

    assert record.is_api_test is False
    assert record.is_controller_unit_test is True
    assert record.expanded_ncloc == 12
    assert record.expanded_cyclomatic_complexity == 4
    assert record.expanded_helper_method_count == 1
    assert record.expanded_objects_created == 2
    assert record.dispatch_labels == ()
    assert record.builder_counts == (0, 0, 0)
    assert record.status_range_counts == (0, 0, 0, 0, 0, 0)


def test_endpoint_surface_projection_reads_template_and_bindings() -> None:
    entry = endpoint_entry(
        covering_test_count=2,
        path_template="/api/users/{id}/orders",
        parameters=[
            query_param("page", required=True),
            query_param("size", required=False),
            header_param("X-Token"),
            form_param("file"),
            body_param("payload"),
        ],
    )

    record = project_project(project(tests=[], endpoints=[entry])).endpoints[0]

    assert record.covering_test_count == 2
    assert record.route_depth == 4
    assert record.path_variable_count == 1
    assert record.has_body is True
    assert record.parameter_count_by_source["query"] == 2
    assert record.parameter_count_by_source["header"] == 1
    assert record.parameter_count_by_source["form"] == 1
    assert record.parameter_count_by_source["body"] == 1
    assert record.required_count_by_source["query"] == 1
    assert record.optional_count_by_source["query"] == 1
    # The surface keys every present source in all three dicts; header params
    # default to required, so the optional count for header is a genuine 0.
    assert record.required_count_by_source["header"] == 1
    assert record.optional_count_by_source["header"] == 0


def test_endpoint_without_body_reports_has_body_false() -> None:
    entry = endpoint_entry(
        covering_test_count=0,
        path_template="/api/health",
        parameters=[query_param("page", required=True)],
    )

    record = project_project(project(tests=[], endpoints=[entry])).endpoints[0]

    assert record.has_body is False


def test_endpoint_method_and_asserted_outcomes_projected() -> None:
    entry = endpoint_entry(
        covering_test_count=1,
        http_method="delete",
        asserted_outcomes=EndpointAssertedStatusOutcomes(
            attributed_request_count=3,
            status_asserted_request_count=2,
            asserting_test_method_count=2,
            status_range_counts={"2xx": 1, "4xx": 1},
            status_code_counts={"204": 1, "404": 1},
        ),
    )

    record = project_project(project(tests=[], endpoints=[entry])).endpoints[0]

    assert record.http_method == "DELETE"
    assert record.is_method_wildcard is False
    assert record.attributed_request_count == 3
    assert record.status_asserted_request_count == 2
    assert record.asserting_test_count == 2
    assert record.asserted_status_range_counts == {"2xx": 1, "4xx": 1}
    assert record.asserted_status_code_counts == {"204": 1, "404": 1}


def test_wildcard_endpoint_projects_unknown_method_with_flag() -> None:
    entry = endpoint_entry(
        covering_test_count=0,
        http_method="UNKNOWN",
        is_method_wildcard=True,
    )

    record = project_project(project(tests=[], endpoints=[entry])).endpoints[0]

    assert record.http_method == "UNKNOWN"
    assert record.is_method_wildcard is True
    assert record.attributed_request_count == 0
    assert record.asserted_status_range_counts == {}


def test_parameterization_projected_with_source_kinds() -> None:
    parameterized = api_test(
        parameterization=ParameterizationSummary(
            signals={
                "static": ["CsvSource", "ValueSource"],
                "dynamic": ["MethodSource"],
            }
        )
    )
    plain = api_test()

    parameterized_record = project_test(parameterized)
    plain_record = project_test(plain)

    assert parameterized_record.is_parameterized is True
    assert parameterized_record.parameterization_static_sources == (
        "CsvSource",
        "ValueSource",
    )
    assert parameterized_record.parameterization_dynamic_sources == ("MethodSource",)
    assert plain_record.is_parameterized is False
    assert plain_record.parameterization_static_sources == ()
    assert plain_record.parameterization_dynamic_sources == ()


def test_verification_response_roles_and_extractions_projected() -> None:
    test = api_test(
        verification_interactions=[
            verification_interaction(
                CallSiteOriginKind.TEST_METHOD,
                response_role=HttpResponseRole.STATUS_ASSERTION,
            ),
            verification_interaction(
                CallSiteOriginKind.TEST_METHOD,
                response_role=HttpResponseRole.BODY_ASSERTION,
            ),
            verification_interaction(CallSiteOriginKind.TEST_METHOD),
        ],
        response_extractions=[
            response_extraction(CallSiteOriginKind.TEST_METHOD),
            response_extraction(CallSiteOriginKind.TEST_HELPER),
        ],
    )

    record = project_test(test)

    role_counts = dict(
        zip(
            VERIFICATION_RESPONSE_ROLE_BUCKETS, record.verification_response_role_counts
        )
    )
    assert role_counts["status-assertion"] == 1
    assert role_counts["body-assertion"] == 1
    assert role_counts["none"] == 1
    assert sum(record.verification_response_role_counts) == 3
    assert record.response_extraction_count == 2


def test_request_construction_surface_projected_for_events_only() -> None:
    test = api_test(
        request_interactions=[
            request_interaction(
                CallSiteOriginKind.TEST_METHOD,
                HttpRequestRole.EVENT,
                header_names=["Content-Type", "X-Token"],
                query_param_names=["page"],
                has_body_payload=True,
                contributed_properties=["path", "has_body_payload"],
            ),
            request_interaction(
                CallSiteOriginKind.TEST_METHOD,
                HttpRequestRole.EVENT,
                path_param_names=["id"],
                form_param_names=["file"],
            ),
            # Builders never contribute to the event construction surface.
            request_interaction(
                CallSiteOriginKind.TEST_METHOD,
                HttpRequestRole.BUILDER,
                header_names=["Accept"],
                has_body_payload=True,
            ),
        ]
    )

    record = project_test(test)

    assert record.request_events_with_body == 1
    assert record.request_events_with_headers == 1
    assert record.request_events_with_query_params == 1
    assert record.request_events_with_path_params == 1
    assert record.request_events_with_form_params == 1
    assert record.request_events_with_builder_correlation == 1
    assert record.event_query_param_counts == (1, 0)
    assert record.event_header_name_counts == (2, 0)
    # One group per dispatched event with correlated builders: only the first
    # event has any, so the second event and the builder-role call yield none.
    assert record.builder_groups == (
        BuilderGroup(builders=(("path", "has_body_payload"),)),
    )


def test_builder_groups_keep_each_event_chain_separate() -> None:
    test = api_test(
        request_interactions=[
            request_interaction(
                CallSiteOriginKind.TEST_METHOD,
                HttpRequestRole.EVENT,
                has_body_payload=True,
                builder_property_sets=[["has_body_payload"], ["header_names"]],
            ),
            request_interaction(
                CallSiteOriginKind.TEST_METHOD,
                HttpRequestRole.EVENT,
                builder_property_sets=[["query_param_names"]],
            ),
        ]
    )

    record = project_test(test)

    assert record.builder_groups == (
        BuilderGroup(builders=(("has_body_payload",), ("header_names",))),
        BuilderGroup(builders=(("query_param_names",),)),
    )


def test_resource_lifecycle_labels_and_fixture_counts_projected() -> None:
    test = api_test(
        test_helper_method_count=2,
        resource_sequences=[
            ResourceInteractionSequence(
                resource_key="a", lifecycle_label=CrudLifecycleLabel.READ_ONLY
            ),
            ResourceInteractionSequence(
                resource_key="b", lifecycle_label=CrudLifecycleLabel.FULL_CRUD
            ),
        ],
        fixtures=[
            fixture(LifecyclePhase.SETUP),
            fixture(LifecyclePhase.SETUP, method_signature="before2()"),
            fixture(LifecyclePhase.TEARDOWN),
        ],
    )

    record = project_test(test)

    assert record.resource_lifecycle_labels == ("read-only", "full-crud")
    assert record.setup_fixture_count == 2
    assert record.teardown_fixture_count == 1
    assert record.test_helper_method_count == 2


def test_distinct_resource_counts_split_by_test_phase_participation() -> None:
    def step(phase: LifecyclePhase, order: int) -> ResourceInteractionStep:
        return ResourceInteractionStep(
            http_method="GET",
            path="/a",
            normalized_path="/a",
            event_order=order,
            phase=phase,
        )

    test = api_test(
        resource_sequences=[
            ResourceInteractionSequence(
                resource_key="a",
                steps=[step(LifecyclePhase.SETUP, 1), step(LifecyclePhase.TEST, 2)],
            ),
            ResourceInteractionSequence(
                resource_key="b",
                steps=[step(LifecyclePhase.SETUP, 3), step(LifecyclePhase.TEARDOWN, 4)],
            ),
        ],
    )

    record = project_test(test)

    assert record.distinct_resource_count == 2
    assert record.test_phase_distinct_resource_count == 1


def test_method_resolved_resource_counts_drop_unresolved_method_events() -> None:
    def step(
        phase: LifecyclePhase, order: int, *, http_method: str
    ) -> ResourceInteractionStep:
        return ResourceInteractionStep(
            http_method=http_method,
            path="/a",
            normalized_path="/a",
            event_order=order,
            phase=phase,
        )

    test = api_test(
        resource_sequences=[
            # Resolved verb in the test phase: counts everywhere.
            ResourceInteractionSequence(
                resource_key="a",
                steps=[step(LifecyclePhase.TEST, 1, http_method="GET")],
            ),
            # Verb never resolves: treated as path-less, so it anchors no resource
            # even though it has a normalized path and a test-phase event.
            ResourceInteractionSequence(
                resource_key="b",
                steps=[step(LifecyclePhase.TEST, 2, http_method="UNKNOWN")],
            ),
            # Resolved verb, but only in a fixture phase: a focal resource without a
            # test-phase resolution.
            ResourceInteractionSequence(
                resource_key="c",
                steps=[step(LifecyclePhase.SETUP, 3, http_method="DELETE")],
            ),
        ],
    )

    record = project_test(test)

    # Path-only counts (used by the other statistics) keep every resource.
    assert record.distinct_resource_count == 3
    assert record.test_phase_distinct_resource_count == 2
    # Scope-Sankey counts drop the unresolved-verb resource.
    assert record.method_resolved_distinct_resource_count == 2
    assert record.method_resolved_test_phase_distinct_resource_count == 1


def test_resource_crud_combinations_projected_for_fully_mapped_sequences() -> None:
    def step(order: int, *, http_method: str) -> ResourceInteractionStep:
        return ResourceInteractionStep(
            http_method=http_method,
            path="/a",
            normalized_path="/a",
            event_order=order,
            phase=LifecyclePhase.TEST,
        )

    test = api_test(
        resource_sequences=[
            # POST then GET on one resource -> {create, read} in canonical order.
            ResourceInteractionSequence(
                resource_key="a",
                steps=[step(1, http_method="POST"), step(2, http_method="GET")],
            ),
            # A lone GET -> {read}.
            ResourceInteractionSequence(
                resource_key="b", steps=[step(3, http_method="GET")]
            ),
            # OPTIONS has no CRUD mapping, so the whole sequence is unresolved and
            # contributes no partial combination.
            ResourceInteractionSequence(
                resource_key="c",
                steps=[step(4, http_method="GET"), step(5, http_method="OPTIONS")],
            ),
        ],
    )

    record = project_test(test)

    assert record.resource_crud_combinations == (("create", "read"), ("read",))
    assert record.unresolved_resource_crud_sequence_count == 1


def test_resource_crud_combination_is_canonical_regardless_of_event_order() -> None:
    def step(order: int, *, http_method: str) -> ResourceInteractionStep:
        return ResourceInteractionStep(
            http_method=http_method,
            path="/a",
            normalized_path="/a",
            event_order=order,
            phase=LifecyclePhase.TEST,
        )

    # DELETE (delete) then GET (read) in event order still folds to canonical
    # read-before-delete, so the combination is set- not order-of-arrival-based.
    test = api_test(
        resource_sequences=[
            ResourceInteractionSequence(
                resource_key="a",
                steps=[step(1, http_method="DELETE"), step(2, http_method="GET")],
            ),
        ],
    )

    record = project_test(test)

    assert record.resource_crud_combinations == (("read", "delete"),)


def test_resource_crud_combination_treats_empty_step_sequence_as_unresolved() -> None:
    test = api_test(
        resource_sequences=[ResourceInteractionSequence(resource_key="a", steps=[])],
    )

    record = project_test(test)

    assert record.resource_crud_combinations == ()
    assert record.unresolved_resource_crud_sequence_count == 1


def test_resource_verb_combinations_keep_put_and_patch_distinct() -> None:
    def step(order: int, *, http_method: str) -> ResourceInteractionStep:
        return ResourceInteractionStep(
            http_method=http_method,
            path="/a",
            normalized_path="/a",
            event_order=order,
            phase=LifecyclePhase.TEST,
        )

    test = api_test(
        resource_sequences=[
            # PUT then PATCH: one CRUD class (update) but two distinct verbs.
            ResourceInteractionSequence(
                resource_key="a",
                steps=[step(1, http_method="PUT"), step(2, http_method="PATCH")],
            ),
            # GET then POST -> {GET, POST} in canonical order.
            ResourceInteractionSequence(
                resource_key="b",
                steps=[step(3, http_method="GET"), step(4, http_method="POST")],
            ),
            # OPTIONS does not map, so the sequence is unresolved under both views.
            ResourceInteractionSequence(
                resource_key="c",
                steps=[step(5, http_method="OPTIONS")],
            ),
        ],
    )

    record = project_test(test)

    # CRUD folds PUT/PATCH into a single update-only sequence; verbs keep them apart.
    assert record.resource_crud_combinations == (("update",), ("create", "read"))
    assert record.resource_verb_combinations == (("PUT", "PATCH"), ("GET", "POST"))
    # The resolution rule is shared, so the unresolved counts match exactly.
    assert record.unresolved_resource_crud_sequence_count == 1
    assert record.unresolved_resource_verb_sequence_count == 1


def test_http_sequence_verb_operations_projected_per_sequence() -> None:
    span = SourceSpan(start_line=1, start_column=1, end_line=1, end_column=2)
    origin = OriginContext(
        phase=LifecyclePhase.TEST, kind=CallSiteOriginKind.TEST_METHOD
    )

    def request_step(order: int, *, http_method: str) -> ApiSequenceStep:
        return ApiSequenceStep(
            order=order,
            kind=SequenceStepKind.HTTP_REQUEST,
            phase=LifecyclePhase.TEST,
            origin=origin,
            method_name="exchange",
            source_span=span,
            http_method=http_method,
            http_path="/items",
        )

    test = api_test(
        test_sequences=[
            HttpTestSequence(
                order=1, steps=[request_step(1, http_method="PUT")], fingerprint="put"
            ),
            HttpTestSequence(
                order=2,
                steps=[request_step(2, http_method="PATCH")],
                fingerprint="patch",
            ),
            # OPTIONS does not map, so this sequence carries no verb.
            HttpTestSequence(
                order=3,
                steps=[request_step(3, http_method="OPTIONS")],
                fingerprint="options",
            ),
        ],
    )

    record = project_test(test)

    assert record.http_sequence_crud_operations == (("update",), ("update",), ())
    assert record.http_sequence_verb_operations == (("PUT",), ("PATCH",), ())


def test_project_resource_crud_derives_available_and_exercised_verbs() -> None:
    def step(order: int, *, http_method: str) -> ResourceInteractionStep:
        return ResourceInteractionStep(
            http_method=http_method,
            path="/items",
            normalized_path="/items",
            event_order=order,
            phase=LifecyclePhase.TEST,
        )

    # The test dispatches GET and PUT against "items"; the resource also exposes
    # POST and PATCH, so PATCH is available-but-unexercised at verb granularity.
    test = api_test(
        resource_sequences=[
            ResourceInteractionSequence(
                resource_key="items",
                steps=[step(1, http_method="GET"), step(2, http_method="PUT")],
            ),
        ],
    )
    entry = ProductionResourceCrudEntry(
        resource_key="items",
        endpoints=[
            ApplicationEndpoint(
                http_method=method,
                path_template="/items",
                framework="spring",
                declaring_class_name="ItemController",
            )
            for method in ("GET", "POST", "PUT", "PATCH")
        ],
        available_operations=[
            CrudOperation.CREATE,
            CrudOperation.READ,
            CrudOperation.UPDATE,
        ],
        exercised_operations=[CrudOperation.READ, CrudOperation.UPDATE],
        missing_available_operations=[CrudOperation.CREATE],
        exercising_test_resources_by_operation={
            CrudOperation.READ: [
                ResourceCrudTestReference(
                    test_method=TestMethodReference(
                        qualified_class_name="C", method_signature="t()"
                    ),
                    resource_key="items",
                    lifecycle_label=CrudLifecycleLabel.READ_ONLY,
                )
            ],
            CrudOperation.UPDATE: [
                ResourceCrudTestReference(
                    test_method=TestMethodReference(
                        qualified_class_name="C", method_signature="t()"
                    ),
                    resource_key="items",
                    lifecycle_label=CrudLifecycleLabel.OTHER,
                )
            ],
        },
    )

    record = project_project(
        project(
            tests=[test], resource_crud=ProductionResourceCrudSummary(resources=[entry])
        )
    )

    resource = record.resources[0]
    # Available verbs come from the endpoint methods in canonical order; the CRUD
    # availability folds PUT/PATCH into the single update class.
    assert resource.available_verbs == ("GET", "POST", "PUT", "PATCH")
    assert resource.available_operations == ("create", "read", "update")
    # Exercised verbs are recovered from the resource sequence the refs point to.
    assert resource.exercised_verbs == ("GET", "PUT")
    # POST and PATCH are available but never dispatched -> missing at verb level.
    assert resource.missing_available_verbs == ("POST", "PATCH")
    # Both operation refs point to the one sequence, deduped to a single verb set.
    assert resource.exercising_sequence_verb_sets == (("GET", "PUT"),)


def test_exercised_verbs_union_across_sequences_on_one_resource() -> None:
    def step(order: int, *, http_method: str) -> ResourceInteractionStep:
        return ResourceInteractionStep(
            http_method=http_method,
            path="/items",
            normalized_path="/items",
            event_order=order,
            phase=LifecyclePhase.TEST,
        )

    # Two distinct tests each drive one sequence on the same production resource,
    # contributing different verbs; the resource's exercised_verbs is their union.
    read_test = api_test(
        resource_sequences=[
            ResourceInteractionSequence(
                resource_key="items", steps=[step(1, http_method="GET")]
            )
        ],
    )
    read_test.identity.method_signature = "read()"
    delete_test = api_test(
        resource_sequences=[
            ResourceInteractionSequence(
                resource_key="items", steps=[step(1, http_method="DELETE")]
            )
        ],
    )
    delete_test.identity.method_signature = "delete()"

    def reference(signature: str) -> ResourceCrudTestReference:
        return ResourceCrudTestReference(
            test_method=TestMethodReference(
                qualified_class_name="C", method_signature=signature
            ),
            resource_key="items",
            lifecycle_label=CrudLifecycleLabel.OTHER,
        )

    entry = ProductionResourceCrudEntry(
        resource_key="items",
        endpoints=[
            ApplicationEndpoint(
                http_method=method,
                path_template="/items",
                framework="spring",
                declaring_class_name="ItemController",
            )
            for method in ("GET", "DELETE")
        ],
        available_operations=[CrudOperation.READ, CrudOperation.DELETE],
        exercised_operations=[CrudOperation.READ, CrudOperation.DELETE],
        missing_available_operations=[],
        exercising_test_resources_by_operation={
            CrudOperation.READ: [reference("read()")],
            CrudOperation.DELETE: [reference("delete()")],
        },
    )

    record = project_project(
        project(
            test_classes=[class_analysis(tests=[read_test, delete_test])],
            resource_crud=ProductionResourceCrudSummary(resources=[entry]),
        )
    )

    resource = record.resources[0]
    # GET from one sequence, DELETE from the other -> union, in canonical order.
    assert resource.exercised_verbs == ("GET", "DELETE")
    assert resource.available_verbs == ("GET", "DELETE")
    assert resource.missing_available_verbs == ()
    # The two sequences stay unmerged, so neither alone covers both verbs: the
    # resource is fully exercised by union but not within a single sequence.
    assert resource.exercising_sequence_verb_sets == (("GET",), ("DELETE",))


def test_resource_crud_projection_maps_operations_and_counts() -> None:
    record = project_project(
        project(
            resource_crud=resource_crud_summary(
                [
                    resource_crud_entry(
                        resource_key="items",
                        available=[
                            CrudOperation.CREATE,
                            CrudOperation.READ,
                            CrudOperation.UPDATE,
                            CrudOperation.DELETE,
                        ],
                        exercised=[CrudOperation.CREATE, CrudOperation.READ],
                    )
                ]
            )
        )
    ).resources[0]

    assert record.available_operations == ("create", "read", "update", "delete")
    assert record.exercised_operations == ("create", "read")
    assert record.missing_available_operations == ("update", "delete")
    assert record.full_crud_test_count == 0


def test_endpoint_parameter_projection_keeps_rates_and_keys_sources_by_value() -> None:
    entry = endpoint_parameter_entry(
        route_covering_test_count=3,
        exercise_rate=0.25,
        optional_exercise_rate=0.5,
        required_exercise_rate=None,
        simple_1_way_optional_coverage=1.0,
        simple_2_way_optional_coverage=0.0,
        total_2_way_optional_coverage=0.75,
        exercise_rate_by_source={
            EndpointParameterSource.QUERY: 1.0,
            EndpointParameterSource.HEADER: 0.0,
        },
        optional_exercise_rate_by_source={EndpointParameterSource.QUERY: None},
        total_2_way_optional_coverage_by_source={EndpointParameterSource.QUERY: 0.75},
    )

    record = project_endpoint_parameter(entry)

    assert record.route_covering_test_count == 3
    assert record.exercise_rate == 0.25
    assert record.optional_exercise_rate == 0.5
    assert record.required_exercise_rate is None
    assert record.simple_1_way_optional_coverage == 1.0
    assert record.simple_2_way_optional_coverage == 0.0
    assert record.total_2_way_optional_coverage == 0.75
    # Enum keys are projected to their string values; None values are preserved.
    assert record.exercise_rate_by_source == {"query": 1.0, "header": 0.0}
    assert record.optional_exercise_rate_by_source == {"query": None}
    assert record.total_2_way_optional_coverage_by_source == {"query": 0.75}


def test_project_projection_flattens_tests_across_classes() -> None:
    record = project_project(
        project(
            dataset_name="svc",
            tests=[api_test(), non_api_test()],
            endpoints=[endpoint_entry(covering_test_count=0)],
            endpoint_parameters=[endpoint_parameter_entry(route_covering_test_count=1)],
        )
    )

    assert record.dataset_name == "svc"
    assert len(record.tests) == 2
    assert len(record.endpoints) == 1
    assert len(record.endpoint_parameters) == 1


def test_project_projection_keeps_per_class_testing_frameworks() -> None:
    record = project_project(
        project(
            test_classes=[
                class_analysis(
                    qualified_class_name="A",
                    testing_frameworks=[
                        TestingFramework.JUNIT5,
                        TestingFramework.MOCKITO,
                    ],
                ),
                class_analysis(qualified_class_name="B"),
            ]
        )
    )

    assert [test_class.testing_frameworks for test_class in record.test_classes] == [
        ("junit5", "mockito"),
        (),
    ]


def test_project_projection_counts_api_tests_and_fixtures_per_class() -> None:
    record = project_project(
        project(
            test_classes=[
                class_analysis(
                    qualified_class_name="A",
                    tests=[api_test(), api_test(), non_api_test()],
                    fixtures=[
                        fixture(LifecyclePhase.SETUP),
                        fixture(LifecyclePhase.TEARDOWN),
                    ],
                ),
                class_analysis(
                    qualified_class_name="B",
                    tests=[non_api_test()],
                    fixtures=[fixture(LifecyclePhase.SETUP)],
                ),
            ]
        )
    )

    assert [test_class.api_test_count for test_class in record.test_classes] == [2, 0]
    assert [test_class.fixture_count for test_class in record.test_classes] == [2, 1]


def test_project_projection_keeps_application_class_and_method_counts() -> None:
    record = project_project(
        project(
            tests=[api_test()],
            application_class_count=7,
            application_method_count=42,
        )
    )

    assert record.application_class_count == 7
    assert record.application_method_count == 42


def test_project_builder_rejects_tests_combined_with_test_classes() -> None:
    with pytest.raises(ValueError, match="not both"):
        project(tests=[api_test()], test_classes=[class_analysis()])


def test_record_roundtrips_through_pickle() -> None:
    import pickle

    record = project_project(project(tests=[api_test()], endpoints=[]))
    restored = pickle.loads(pickle.dumps(record))

    assert restored.tests[0].dispatch_labels == record.tests[0].dispatch_labels


def test_saint_comparison_endpoints_strip_known_context_path_prefix() -> None:
    analysis = project(
        dataset_name="saint-genome",
        tests=[
            api_test(
                request_interactions=[
                    request_interaction(
                        CallSiteOriginKind.TEST_METHOD,
                        HttpRequestRole.EVENT,
                        http_method="GET",
                        candidate_path=(
                            "http://localhost:9080"
                            "/web-1.1.49-SNAPSHOT//signal/mutation"
                        ),
                    )
                ],
            )
        ],
        endpoints=[
            endpoint_entry(
                path_template="/signal/mutation",
                http_method="GET",
                covering_test_count=0,
            )
        ],
    )

    record = project_project(analysis)

    # Baseline: the context-path-prefixed URL attributes to nothing.
    assert record.endpoints[0].covering_test_count == 0
    # SAINT comparison: stripping /web-1.1.49-SNAPSHOT recovers the match.
    assert record.saint_comparison_endpoints[0].covering_test_count == 1


def test_saint_comparison_endpoint_parameters_strip_known_context_path_prefix() -> None:
    analysis = project(
        dataset_name="saint-genome",
        tests=[
            api_test(
                request_interactions=[
                    request_interaction(
                        CallSiteOriginKind.TEST_METHOD,
                        HttpRequestRole.EVENT,
                        http_method="GET",
                        candidate_path=(
                            "http://localhost:9080"
                            "/web-1.1.49-SNAPSHOT//signal/mutation"
                        ),
                        query_param_names=["fields"],
                    )
                ],
            )
        ],
        endpoints=[
            endpoint_entry(
                path_template="/signal/mutation",
                http_method="GET",
                covering_test_count=0,
                parameters=[query_param("fields", required=False)],
            )
        ],
        # Baseline parameter coverage: the prefixed URL attributes to nothing.
        endpoint_parameters=[endpoint_parameter_entry(route_covering_test_count=0)],
    )

    record = project_project(analysis)

    assert record.endpoint_parameters[0].route_covering_test_count == 0
    # SAINT comparison recomputes parameter coverage from the endpoints with the
    # prefix stripped, so the request attributes and the endpoint is covered.
    assert record.saint_comparison_endpoint_parameters[0].route_covering_test_count == 1


def test_saint_comparison_endpoints_reuse_baseline_without_a_known_prefix() -> None:
    analysis = project(
        dataset_name="dev",
        tests=[
            api_test(
                request_interactions=[
                    request_interaction(
                        CallSiteOriginKind.TEST_METHOD,
                        HttpRequestRole.EVENT,
                        http_method="GET",
                        candidate_path="/signal/mutation",
                    )
                ],
            )
        ],
        endpoints=[
            endpoint_entry(
                path_template="/signal/mutation",
                http_method="GET",
                covering_test_count=1,
            )
        ],
    )

    record = project_project(analysis)

    # No known SAINT context prefix present, so the gate reuses the baseline
    # endpoints object untouched rather than re-running coverage matching.
    assert record.saint_comparison_endpoints is record.endpoints


def test_production_grouping_folds_instance_write_and_collection_read() -> None:
    def step(order: int, *, http_method: str, path: str) -> ResourceInteractionStep:
        return ResourceInteractionStep(
            http_method=http_method,
            path=path,
            normalized_path=path,
            event_order=order,
            phase=LifecyclePhase.TEST,
        )

    # A write to an instance path and a read of its collection: distinct observed
    # resource keys, but both resolve to the same production resource key.
    test = api_test(
        resource_sequences=[
            ResourceInteractionSequence(
                resource_key="/products/ProductA/features/Feature1",
                steps=[
                    step(
                        1,
                        http_method="POST",
                        path="/products/ProductA/features/Feature1",
                    )
                ],
            ),
            ResourceInteractionSequence(
                resource_key="/products/ProductA/features",
                steps=[step(2, http_method="GET", path="/products/ProductA/features")],
            ),
        ],
    )
    analysis = project(
        tests=[test],
        endpoints=[
            endpoint_entry(
                path_template="/products/{name}/features/{feature}",
                http_method="POST",
                covering_test_count=1,
            ),
            endpoint_entry(
                path_template="/products/{name}/features",
                http_method="GET",
                covering_test_count=1,
            ),
        ],
    )

    record = project_project(analysis)

    # Observed grouping keeps the write and read-back apart: two single-verb
    # sequences, neither exhibiting verify-after-mutate.
    observed = record.observed_resource_sequences
    assert len(observed) == 2
    assert {sequence.crud_combination for sequence in observed} == {
        ("create",),
        ("read",),
    }
    assert not any(sequence.has_read_after_write for sequence in observed)

    # Production grouping folds both requests onto /products/{id}/features, so the
    # single group is a create-read that reads after the write.
    production = record.production_resource_sequences
    assert len(production) == 1
    grouped = production[0]
    assert grouped.crud_combination == ("create", "read")
    assert grouped.has_read_after_write is True
    assert grouped.resolved_to_production is True
