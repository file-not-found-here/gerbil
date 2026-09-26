"""Schema-model builders for statistics tests (kept lean so each test states
only the fields it exercises)."""

from __future__ import annotations

from pathlib import Path

from gerbil.analysis.schema import (
    ApplicationEndpoint,
    AssertionAnalysis,
    AssertionRole,
    AssertionSummary,
    AuthHandlingDecision,
    BuilderCorrelationSource,
    CallSiteOriginKind,
    CrudOperation,
    DependencyAnalysis,
    DependencyStrategyDecision,
    EndpointAssertedStatusOutcomes,
    EndpointCandidate,
    EndpointCoverageEntry,
    EndpointCoverageSummary,
    EndpointParameter,
    EndpointParameterCoverageEntry,
    EndpointParameterCoverageSummary,
    EndpointParameterSource,
    ExpandedMetrics,
    FailureScenarioSignals,
    FixtureAnalysis,
    HttpAnalysis,
    HttpCallSite,
    HttpDispatchFramework,
    HttpMockedCallSite,
    HttpMockedInteraction,
    HttpRequestInteraction,
    HttpRequestRole,
    HttpResponseExtraction,
    HttpResponseRole,
    HttpSequenceSummary,
    HttpTestSequence,
    HttpVerificationInteraction,
    LifecyclePhase,
    MethodIdentity,
    MockingContext,
    MockingContextKind,
    OracleTypeDecision,
    OriginContext,
    ParameterizationSummary,
    Precondition,
    PreconditionSource,
    PreconditionSummary,
    PreconditionType,
    ProductionResourceCrudEntry,
    ProductionResourceCrudSummary,
    ProjectAnalysis,
    ProjectMetadata,
    RequestDispatchDecision,
    ResourceInteractionSequence,
    SourceSpan,
    StatusCodeDistribution,
    StateAnalysis,
    StateObservation,
    StateObservationMedium,
    StateObservationSummary,
    StateObservationTier,
    TestClassAnalysis,
    TestingFramework,
    TestMethodAnalysis,
)

_SPAN = SourceSpan(start_line=1, start_column=1, end_line=1, end_column=2)


_FIXTURE_KINDS = (CallSiteOriginKind.FIXTURE, CallSiteOriginKind.FIXTURE_HELPER)


def origin(
    kind: CallSiteOriginKind, *, phase: LifecyclePhase | None = None
) -> OriginContext:
    if phase is None:
        # Mirror the analysis invariant: fixture-kind origins are setup/teardown,
        # never the test phase. Default fixtures to setup unless told otherwise.
        phase = LifecyclePhase.SETUP if kind in _FIXTURE_KINDS else LifecyclePhase.TEST
    return OriginContext(phase=phase, kind=kind)


def request_interaction(
    kind: CallSiteOriginKind,
    role: HttpRequestRole,
    *,
    http_method: str = "GET",
    phase: LifecyclePhase | None = None,
    framework: HttpDispatchFramework = HttpDispatchFramework.MOCKMVC,
    header_names: list[str] | None = None,
    query_param_names: list[str] | None = None,
    path_param_names: list[str] | None = None,
    form_param_names: list[str] | None = None,
    has_body_payload: bool = False,
    contributed_properties: list[str] | None = None,
    builder_property_sets: list[list[str]] | None = None,
    candidate_path: str | None = None,
    path_truncated: bool = False,
) -> HttpRequestInteraction:
    if builder_property_sets is not None:
        correlated_builder_sources = [
            BuilderCorrelationSource(
                method_name="builder", contributed_properties=list(properties)
            )
            for properties in builder_property_sets
        ]
    elif contributed_properties is not None:
        correlated_builder_sources = [
            BuilderCorrelationSource(
                method_name="builder",
                contributed_properties=contributed_properties,
            )
        ]
    else:
        correlated_builder_sources = []
    return HttpRequestInteraction(
        origin=origin(kind, phase=phase),
        http_call=HttpCallSite(
            http_method=http_method,
            path=candidate_path or "/x",
            framework=framework,
            request_role=role,
            method_name="call",
            header_names=header_names or [],
            query_param_names=query_param_names or [],
            path_param_names=path_param_names or [],
            form_param_names=form_param_names or [],
            has_body_payload=has_body_payload,
            correlated_builder_sources=correlated_builder_sources,
        ),
        endpoint_candidate=(
            None
            if candidate_path is None
            else EndpointCandidate(
                http_method=http_method,
                path=candidate_path,
                source="sequence-step",
                path_truncated=path_truncated,
            )
        ),
    )


def verification_interaction(
    kind: CallSiteOriginKind,
    *,
    phase: LifecyclePhase | None = None,
    response_role: HttpResponseRole | None = None,
) -> HttpVerificationInteraction:
    return HttpVerificationInteraction(
        origin=origin(kind, phase=phase),
        assertion_role=AssertionRole.STATUS,
        method_name="isOk",
        source_span=_SPAN,
        response_role=response_role,
    )


def response_extraction(
    kind: CallSiteOriginKind, *, phase: LifecyclePhase | None = None
) -> HttpResponseExtraction:
    return HttpResponseExtraction(
        origin=origin(kind, phase=phase),
        response_role=HttpResponseRole.EXTRACTOR,
        method_name="extract",
        source_span=_SPAN,
    )


def mocked_interaction(kind: CallSiteOriginKind) -> HttpMockedInteraction:
    return HttpMockedInteraction(
        origin=origin(kind),
        http_call=HttpMockedCallSite(
            http_method="GET",
            path="/x",
            framework=HttpDispatchFramework.OKHTTP,
            method_name="enqueue",
            mocking_context=MockingContext(
                kind=MockingContextKind.STUBBING, wrapper_method="when"
            ),
        ),
    )


def resolved_sequence_summary(
    *, distinct_endpoint_count: int = 1, distinct_http_method_count: int = 1
) -> HttpSequenceSummary:
    """A summary with at least one event resolved to both an endpoint and a method,
    so the owning project passes the endpoint/parameter coverage gate."""
    return HttpSequenceSummary(
        sequence_count=1,
        distinct_endpoint_count=distinct_endpoint_count,
        distinct_http_method_count=distinct_http_method_count,
    )


def fixture(
    phase: LifecyclePhase,
    *,
    defining_class_name: str = "C",
    method_signature: str = "fixture()",
) -> FixtureAnalysis:
    return FixtureAnalysis(
        phase=phase,
        defining_class_name=defining_class_name,
        method_signature=method_signature,
    )


def precondition(
    precondition_type: PreconditionType,
    *,
    source: PreconditionSource = PreconditionSource.PROGRAMMATIC,
    evidence: str = "evidence",
) -> Precondition:
    return Precondition(type=precondition_type, source=source, evidence=evidence)


def postcondition(
    medium: StateObservationMedium,
    *,
    tier: StateObservationTier = StateObservationTier.NESTED,
) -> StateObservation:
    return StateObservation(
        medium=medium,
        tier=tier,
        receiver_type="Receiver",
        method_name="observe",
        evidence="Receiver.observe",
        start_line=1,
    )


def api_test(
    *,
    expanded_ncloc: int = 0,
    expanded_cc: int = 0,
    helper_method_count: int = 0,
    test_helper_method_count: int = 0,
    objects_created: int = 0,
    dispatch_labels: list[str] | None = None,
    dependency_labels: list[str] | None = None,
    request_interactions: list[HttpRequestInteraction] | None = None,
    verification_interactions: list[HttpVerificationInteraction] | None = None,
    mocked_interactions: list[HttpMockedInteraction] | None = None,
    resource_sequences: list[ResourceInteractionSequence] | None = None,
    sequence_summary: HttpSequenceSummary | None = None,
    test_sequences: list[HttpTestSequence] | None = None,
    fixtures: list[FixtureAnalysis] | None = None,
    status_distribution: StatusCodeDistribution | None = None,
    assertion_summary: AssertionSummary | None = None,
    oracle_type_label: str = "implicit",
    has_client_error_assertion: bool = False,
    has_server_error_assertion: bool = False,
    has_exception_assertion: bool = False,
    status_code_counts: dict[str, int] | None = None,
    preconditions: list[Precondition] | None = None,
    postconditions: list[StateObservation] | None = None,
    auth_handling_label: str = "none",
    parameterization: ParameterizationSummary | None = None,
    response_extractions: list[HttpResponseExtraction] | None = None,
) -> TestMethodAnalysis:
    return TestMethodAnalysis(
        identity=MethodIdentity(
            defining_class_name="C",
            method_signature="t()",
            method_declaration="void t()",
            parameterization=parameterization,
        ),
        is_api_test=True,
        expanded_metrics=ExpandedMetrics(
            ncloc=expanded_ncloc,
            cyclomatic_complexity=expanded_cc,
            helper_method_count=helper_method_count,
            test_helper_method_count=test_helper_method_count,
            number_of_objects_created=objects_created,
        ),
        http=HttpAnalysis(
            request_interactions=request_interactions or [],
            verification_interactions=verification_interactions or [],
            mocked_interactions=mocked_interactions or [],
            response_extractions=response_extractions or [],
            resource_interaction_sequences=resource_sequences or [],
            test_sequences=test_sequences or [],
            sequence_summary=sequence_summary or HttpSequenceSummary(),
            request_dispatch=RequestDispatchDecision(
                labels=dispatch_labels if dispatch_labels is not None else ["unknown"]
            ),
            auth_handling=AuthHandlingDecision(label=auth_handling_label),
        ),
        assertions=AssertionAnalysis(
            summary=assertion_summary or AssertionSummary(),
            oracle_type=OracleTypeDecision(label=oracle_type_label),
            failure_scenarios=FailureScenarioSignals(
                has_client_error_assertion=has_client_error_assertion,
                has_server_error_assertion=has_server_error_assertion,
                has_exception_assertion=has_exception_assertion,
            ),
            status_code_distribution=status_distribution or StatusCodeDistribution(),
            status_code_counts=status_code_counts or {},
        ),
        dependencies=DependencyAnalysis(
            strategy=DependencyStrategyDecision(labels=dependency_labels or [])
        ),
        state=StateAnalysis(
            preconditions=PreconditionSummary(preconditions=preconditions or []),
            observations=StateObservationSummary(observations=postconditions or []),
        ),
        fixtures=fixtures or [],
    )


def non_api_test(
    *,
    is_controller_unit_test: bool = False,
    expanded_ncloc: int = 0,
    expanded_cc: int = 0,
    helper_method_count: int = 0,
    test_helper_method_count: int = 0,
    objects_created: int = 0,
    fixtures: list[FixtureAnalysis] | None = None,
    preconditions: list[Precondition] | None = None,
    postconditions: list[StateObservation] | None = None,
) -> TestMethodAnalysis:
    return TestMethodAnalysis(
        identity=MethodIdentity(
            defining_class_name="C",
            method_signature="u()",
            method_declaration="void u()",
        ),
        is_api_test=False,
        is_controller_unit_test=is_controller_unit_test,
        expanded_metrics=ExpandedMetrics(
            ncloc=expanded_ncloc,
            cyclomatic_complexity=expanded_cc,
            helper_method_count=helper_method_count,
            test_helper_method_count=test_helper_method_count,
            number_of_objects_created=objects_created,
        ),
        state=StateAnalysis(
            preconditions=PreconditionSummary(preconditions=preconditions or []),
            observations=StateObservationSummary(observations=postconditions or []),
        ),
        fixtures=fixtures or [],
    )


def endpoint_entry(
    *,
    covering_test_count: int,
    path_template: str = "/api/items",
    parameters: list[EndpointParameter] | None = None,
    http_method: str = "GET",
    is_method_wildcard: bool = False,
    asserted_outcomes: EndpointAssertedStatusOutcomes | None = None,
) -> EndpointCoverageEntry:
    endpoint = ApplicationEndpoint(
        http_method=http_method,
        is_method_wildcard=is_method_wildcard,
        path_template=path_template,
        framework="spring",
        declaring_class_name="Ctrl",
        parameters=parameters or [],
    )
    return EndpointCoverageEntry(
        endpoint=endpoint,
        covering_test_method_count=covering_test_count,
        covering_test_methods=[],
        is_covered=covering_test_count > 0,
        asserted_outcomes=asserted_outcomes or EndpointAssertedStatusOutcomes(),
    )


def endpoint_parameter_entry(
    *,
    route_covering_test_count: int = 0,
    path_template: str = "/api/items",
    exercise_rate: float | None = None,
    optional_exercise_rate: float | None = None,
    required_exercise_rate: float | None = None,
    simple_1_way_optional_coverage: float | None = None,
    simple_2_way_optional_coverage: float | None = None,
    total_2_way_optional_coverage: float | None = None,
    exercise_rate_by_source: dict[EndpointParameterSource, float] | None = None,
    optional_exercise_rate_by_source: (
        dict[EndpointParameterSource, float | None] | None
    ) = None,
    simple_1_way_optional_coverage_by_source: (
        dict[EndpointParameterSource, float | None] | None
    ) = None,
    simple_2_way_optional_coverage_by_source: (
        dict[EndpointParameterSource, float | None] | None
    ) = None,
    total_2_way_optional_coverage_by_source: (
        dict[EndpointParameterSource, float | None] | None
    ) = None,
) -> EndpointParameterCoverageEntry:
    endpoint = ApplicationEndpoint(
        http_method="GET",
        path_template=path_template,
        framework="spring",
        declaring_class_name="Ctrl",
    )
    return EndpointParameterCoverageEntry(
        endpoint=endpoint,
        route_covering_test_count=route_covering_test_count,
        exercise_rate=exercise_rate,
        optional_exercise_rate=optional_exercise_rate,
        required_exercise_rate=required_exercise_rate,
        simple_1_way_optional_coverage=simple_1_way_optional_coverage,
        simple_2_way_optional_coverage=simple_2_way_optional_coverage,
        total_2_way_optional_coverage=total_2_way_optional_coverage,
        exercise_rate_by_source=exercise_rate_by_source or {},
        optional_exercise_rate_by_source=optional_exercise_rate_by_source or {},
        simple_1_way_optional_coverage_by_source=(
            simple_1_way_optional_coverage_by_source or {}
        ),
        simple_2_way_optional_coverage_by_source=(
            simple_2_way_optional_coverage_by_source or {}
        ),
        total_2_way_optional_coverage_by_source=(
            total_2_way_optional_coverage_by_source or {}
        ),
    )


def query_param(name: str, *, required: bool) -> EndpointParameter:
    return EndpointParameter(
        name=name, source=EndpointParameterSource.QUERY, required=required
    )


def header_param(name: str) -> EndpointParameter:
    return EndpointParameter(name=name, source=EndpointParameterSource.HEADER)


def form_param(name: str) -> EndpointParameter:
    return EndpointParameter(name=name, source=EndpointParameterSource.FORM)


def body_param(name: str = "payload") -> EndpointParameter:
    return EndpointParameter(name=name, source=EndpointParameterSource.BODY)


def resource_crud_entry(
    *,
    resource_key: str = "items",
    available: list[CrudOperation] | None = None,
    exercised: list[CrudOperation] | None = None,
    full_crud_test_count: int = 0,
) -> ProductionResourceCrudEntry:
    available = available or []
    exercised = exercised or []
    missing = [operation for operation in available if operation not in exercised]
    return ProductionResourceCrudEntry(
        resource_key=resource_key,
        available_operations=available,
        exercised_operations=exercised,
        missing_available_operations=missing,
        full_crud_test_count=full_crud_test_count,
    )


def resource_crud_summary(
    entries: list[ProductionResourceCrudEntry] | None = None,
) -> ProductionResourceCrudSummary:
    entries = entries or []
    return ProductionResourceCrudSummary(
        total_resource_count=len(entries),
        resources_with_any_test_count=sum(
            1 for entry in entries if entry.exercised_operations
        ),
        resources_with_full_crud_test_count=sum(
            1 for entry in entries if entry.full_crud_test_count > 0
        ),
        resources=entries,
    )


def class_analysis(
    *,
    qualified_class_name: str = "C",
    testing_frameworks: list[TestingFramework] | None = None,
    tests: list[TestMethodAnalysis] | None = None,
    fixtures: list[FixtureAnalysis] | None = None,
) -> TestClassAnalysis:
    return TestClassAnalysis(
        qualified_class_name=qualified_class_name,
        testing_frameworks=testing_frameworks or [],
        test_method_analyses=tests or [],
        fixtures=fixtures or [],
    )


def project(
    *,
    dataset_name: str = "proj",
    tests: list[TestMethodAnalysis] | None = None,
    test_classes: list[TestClassAnalysis] | None = None,
    endpoints: list[EndpointCoverageEntry] | None = None,
    endpoint_parameters: list[EndpointParameterCoverageEntry] | None = None,
    resource_crud: ProductionResourceCrudSummary | None = None,
    application_class_count: int = 0,
    application_method_count: int = 0,
) -> ProjectAnalysis:
    if tests is not None and test_classes is not None:
        raise ValueError("pass either tests or test_classes, not both")
    return ProjectAnalysis(
        dataset_name=dataset_name,
        metadata=ProjectMetadata(project_path=f"/projects/{dataset_name}"),
        application_class_count=application_class_count,
        application_method_count=application_method_count,
        endpoint_coverage=EndpointCoverageSummary(endpoints=endpoints or []),
        endpoint_parameter_coverage=EndpointParameterCoverageSummary(
            endpoints=endpoint_parameters or []
        ),
        resource_crud=resource_crud or ProductionResourceCrudSummary(),
        test_class_analyses=(
            test_classes if test_classes is not None else [class_analysis(tests=tests)]
        ),
    )


def write_gerbil_output(input_root: Path, name: str, analysis: ProjectAnalysis) -> Path:
    """Write a project's analysis to <input_root>/<name>/gerbil.json."""
    project_dir = input_root / name
    project_dir.mkdir(parents=True)
    output_file = project_dir / "gerbil.json"
    output_file.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    return output_file
