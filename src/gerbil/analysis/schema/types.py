from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SchemaModel(BaseModel):
    """Base for serialized schema models; unknown fields signal schema drift."""

    model_config = ConfigDict(extra="forbid")


class TestingFramework(str, Enum):
    JUNIT3 = "junit3"
    JUNIT4 = "junit4"
    JUNIT5 = "junit5"
    TESTNG = "testng"
    ASSERTJ = "assertj"
    HAMCREST = "hamcrest"
    GOOGLE_TRUTH = "google-truth"
    MOCKITO = "mockito"
    EASYMOCK = "easymock"
    POWERMOCK = "powermock"
    JMOCKIT = "jmockit"
    JMOCK = "jmock"
    SPRING_TEST = "spring-test"
    REST_ASSURED = "rest-assured"
    KARATE = "karate"
    PACT = "pact"
    CITRUS = "citrus"


class HttpDispatchFramework(str, Enum):
    MOCKMVC = "mockmvc"
    WEBTESTCLIENT = "webtestclient"
    TEST_REST_TEMPLATE = "test-rest-template"
    REST_TEMPLATE = "rest-template"
    REST_CLIENT = "rest-client"
    WEBCLIENT = "webclient"
    REST_ASSURED = "rest-assured"
    OKHTTP = "okhttp"
    APACHE_HTTPCLIENT = "apache-httpclient"
    JAVA_HTTPCLIENT = "java-httpclient"
    MICRONAUT_CLIENT = "micronaut-client"
    JAX_RS = "jax-rs"
    FEIGN = "feign"
    HTTP_INTERFACE = "http-interface"
    KARATE = "karate"
    PACT = "pact"
    CITRUS = "citrus"


class RequestDispatch(str, Enum):
    IN_PROCESS = "in-process"
    LOCAL_NETWORK = "local-network"
    REMOTE_NETWORK = "remote-network"
    UNKNOWN = "unknown"


class DependencyStrategy(str, Enum):
    MOCKED = "mocked"
    VIRTUALIZED = "virtualized"
    CONTAINERIZED = "containerized"


class AssertionRole(str, Enum):
    STATUS = "status"
    BODY = "body"
    HEADER = "header"
    EXCEPTION = "exception"
    GENERAL = "general"


class AssertionNodeKind(str, Enum):
    WRAPPER = "wrapper"
    SUBJECT = "subject"
    VERIFIER = "verifier"
    DIRECT = "direct"


class AssertionSummary(SchemaModel):
    status_count: int = 0
    body_count: int = 0
    header_count: int = 0
    general_count: int = 0
    exception_count: int = 0

    @property
    def total_count(self) -> int:
        return (
            self.status_count
            + self.body_count
            + self.header_count
            + self.general_count
            + self.exception_count
        )


_RESPONSE_SURFACE_ROLES: tuple[AssertionRole, ...] = (
    AssertionRole.STATUS,
    AssertionRole.BODY,
    AssertionRole.HEADER,
)


def _response_surface_combination(labels: list[AssertionRole]) -> str:
    if not labels:
        return "none"
    if len(labels) == 1:
        return f"{labels[0].value}-only"
    return "+".join(label.value for label in labels)


@dataclass
class AssertionClassification:
    """Internal annotation attached to a CallSiteNode during assertion classification.

    Not serialized — used only within the analysis pipeline.
    """

    role: AssertionRole
    status_code: int | None = None
    status_range: str | None = None
    node_kind: AssertionNodeKind = AssertionNodeKind.DIRECT

    @property
    def is_countable(self) -> bool:
        return self.node_kind in {
            AssertionNodeKind.DIRECT,
            AssertionNodeKind.VERIFIER,
        }


class AuthHandling(str, Enum):
    BYPASSED = "bypassed"
    MOCKED = "mocked"
    TEST_TOKEN = "test-token"
    REAL_FLOW = "real-flow"
    # Auth machinery is present but cannot be categorized into a strategy above.
    UNKNOWN = "unknown"
    # No auth evidence of any kind in the test.
    NONE = "none"


class AuthHandlingDecision(SchemaModel):
    label: str = "none"
    signals: dict[str, list[str]] = Field(default_factory=dict)


class PreconditionType(str, Enum):
    DB_SEEDING = "db-seeding"
    CONTAINER_BOOTSTRAP = "container-bootstrap"
    MQ_SEEDING = "mq-seeding"
    FS_SEEDING = "fs-seeding"


class PreconditionSource(str, Enum):
    ANNOTATION = "annotation"
    PROGRAMMATIC = "programmatic"


class StateObservationMedium(str, Enum):
    DB = "db"
    MQ = "mq"
    FS = "fs"


class StateObservationTier(str, Enum):
    NESTED = "nested"
    BINDING = "binding"
    ANNOTATION = "annotation"


class SequenceStepKind(str, Enum):
    REQUEST_BUILD = "request-build"
    HTTP_REQUEST = "http-request"
    RESPONSE_CHECK = "response-check"


class LifecyclePhase(str, Enum):
    SETUP = "setup"
    TEST = "test"
    TEARDOWN = "teardown"


class CrudOperation(str, Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"


class CrudLifecycleLabel(str, Enum):
    READ_ONLY = "read-only"
    CREATE_AND_TRUST = "create-and-trust"
    CREATE_VERIFY = "create-verify"
    CREATE_UPDATE_VERIFY = "create-update-verify"
    CREATE_VERIFY_CLEANUP = "create-verify-cleanup"
    DELETE_VERIFY = "delete-verify"
    FULL_CRUD = "full-crud"
    WRITE_ONLY = "write-only"
    OTHER = "other"


class CallSiteOriginKind(str, Enum):
    TEST_METHOD = "test-method"
    TEST_HELPER = "test-helper"
    FIXTURE = "fixture"
    FIXTURE_HELPER = "fixture-helper"


class HttpRequestRole(str, Enum):
    BUILDER = "builder"
    EVENT = "event"


class HttpResponseRole(str, Enum):
    INSPECTOR = "inspector"
    MATCHER = "matcher"
    EXTRACTOR = "extractor"
    BODY_ASSERTION = "body-assertion"
    HEADER_ASSERTION = "header-assertion"
    STATUS_ASSERTION = "status-assertion"


class MockingContextKind(str, Enum):
    STUBBING = "stubbing"
    VERIFICATION = "verification"


class MockingContext(SchemaModel):
    framework: str = "mockito"
    kind: MockingContextKind
    wrapper_method: str


class HttpInteractionKind(str, Enum):
    REQUEST = "request"
    VERIFICATION = "verification"


class BuilderCorrelationSource(SchemaModel):
    """Records which builder call site contributed properties to a request event."""

    method_name: str
    start_line: int = -1
    framework: HttpDispatchFramework | None = None
    contributed_properties: list[str] = Field(default_factory=list)


@dataclass
class HttpClassification:
    """Internal annotation attached to a CallSiteNode during HTTP classification.

    Not serialized — used only within the analysis pipeline.
    """

    http_method: str
    path: str
    framework: HttpDispatchFramework
    # True when the path is the leading literal of a concatenated expression
    # cut at a trailing slash ("/users/" + id), so a final segment is missing.
    path_truncated: bool = False
    receiver_type: str = ""
    # Normalized framework-specific API family used to distinguish sub-APIs
    # inside one framework, such as request factories, request builders,
    # response inspectors, matcher roots, and assertion objects.
    owner_family: str | None = None
    request_role: HttpRequestRole | None = None
    response_role: HttpResponseRole | None = None
    headers: list[str] = field(default_factory=list)
    header_names: list[str] = field(default_factory=list)
    query_param_names: list[str] = field(default_factory=list)
    path_param_names: list[str] = field(default_factory=list)
    form_param_names: list[str] = field(default_factory=list)
    rest_assured_ambiguous_param_names: list[str] = field(default_factory=list)
    has_body_payload: bool = False
    auth_hints: list[str] = field(default_factory=list)
    correlated_builder_sources: list[BuilderCorrelationSource] = field(
        default_factory=list
    )
    mocking_context: MockingContext | None = None


class EndpointCandidate(SchemaModel):
    http_method: str
    path: str
    source: str
    start_line: int = -1
    path_truncated: bool = False


class HttpCallSite(SchemaModel):
    http_method: str
    path: str
    framework: HttpDispatchFramework
    request_role: HttpRequestRole = HttpRequestRole.EVENT
    method_name: str
    receiver_type: str = ""
    callee_signature: str = ""
    start_line: int = -1
    headers: list[str] = Field(default_factory=list)
    header_names: list[str] = Field(default_factory=list)
    query_param_names: list[str] = Field(default_factory=list)
    path_param_names: list[str] = Field(default_factory=list)
    form_param_names: list[str] = Field(default_factory=list)
    has_body_payload: bool = False
    auth_hints: list[str] = Field(default_factory=list)
    correlated_builder_sources: list[BuilderCorrelationSource] = Field(
        default_factory=list
    )


class StatusCodeDistribution(SchemaModel):
    range_1xx: int = 0
    range_2xx: int = 0
    range_3xx: int = 0
    range_4xx: int = 0
    range_5xx: int = 0
    unknown: int = 0

    def to_bucket_counts(self) -> dict[str, int]:
        return {
            "1xx": self.range_1xx,
            "2xx": self.range_2xx,
            "3xx": self.range_3xx,
            "4xx": self.range_4xx,
            "5xx": self.range_5xx,
            "unknown": self.unknown,
        }


def _default_status_range_counts() -> dict[str, int]:
    return StatusCodeDistribution().to_bucket_counts()


class FailureScenarioSignals(SchemaModel):
    has_client_error_assertion: bool = False
    has_server_error_assertion: bool = False
    has_exception_assertion: bool = False


class Precondition(SchemaModel):
    type: PreconditionType
    source: PreconditionSource
    evidence: str


class PreconditionSummary(SchemaModel):
    preconditions: list[Precondition] = Field(default_factory=list)


class StateObservation(SchemaModel):
    medium: StateObservationMedium
    tier: StateObservationTier
    receiver_type: str
    method_name: str
    evidence: str
    start_line: int = -1


class StateObservationSummary(SchemaModel):
    observations: list[StateObservation] = Field(default_factory=list)

    @property
    def counts_by_medium(self) -> dict[StateObservationMedium, int]:
        counts: dict[StateObservationMedium, int] = {
            m: 0 for m in StateObservationMedium
        }
        for obs in self.observations:
            counts[obs.medium] += 1
        return counts

    @property
    def total_count(self) -> int:
        return len(self.observations)

    @property
    def has_any(self) -> bool:
        return bool(self.observations)


class SourceSpan(SchemaModel):
    start_line: int
    start_column: int
    end_line: int
    end_column: int


class OriginContext(SchemaModel):
    phase: LifecyclePhase
    kind: CallSiteOriginKind
    defining_class_name: str | None = None
    method_signature: str | None = None
    entry_defining_class_name: str | None = None
    entry_method_signature: str | None = None
    depth: int = 0
    is_group_ambiguous: bool = False


class ApiSequenceStep(SchemaModel):
    order: int
    kind: SequenceStepKind
    phase: LifecyclePhase
    origin: OriginContext
    method_name: str
    source_span: SourceSpan
    framework: HttpDispatchFramework | None = None
    http_method: str | None = None
    http_path: str | None = None
    path_truncated: bool = False
    assertion_role: AssertionRole | None = None
    status_code: int | None = None
    status_range: str | None = None


class HttpTestSequence(SchemaModel):
    order: int
    steps: list[ApiSequenceStep] = Field(default_factory=list)
    length: int = 0
    fingerprint: str

    @model_validator(mode="before")
    @classmethod
    def _populate_length(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        sequence_data = dict(data)
        raw_steps = sequence_data.get("steps") or []
        sequence_data["length"] = len(raw_steps) if isinstance(raw_steps, list) else 0
        return sequence_data


class HttpSequenceSummary(SchemaModel):
    sequence_count: int = 0
    sequence_lengths: list[int] = Field(default_factory=list)
    request_build_step_count: int = 0
    http_request_step_count: int = 0
    response_check_step_count: int = 0
    has_multiple_sequences: bool = False
    distinct_http_method_count: int = 0
    distinct_resource_count: int = 0
    distinct_endpoint_count: int = 0
    distinct_sequence_fingerprint_count: int = 0
    repeated_sequence_fingerprint_count: int = 0
    has_repeated_sequence: bool = False


class ResourceInteractionStep(SchemaModel):
    http_method: str
    path: str
    normalized_path: str
    event_order: int
    phase: LifecyclePhase
    crud_operation: CrudOperation | None = None


class ResourceInteractionSequence(SchemaModel):
    resource_key: str
    steps: list[ResourceInteractionStep] = Field(default_factory=list)
    exercised_operations: list[CrudOperation] = Field(default_factory=list)
    available_operations: list[CrudOperation] = Field(default_factory=list)
    missing_available_operations: list[CrudOperation] = Field(default_factory=list)
    lifecycle_label: CrudLifecycleLabel = CrudLifecycleLabel.OTHER
    has_read_after_write: bool = False
    has_cleanup_delete: bool = False


class ParameterizationSummary(SchemaModel):
    signals: dict[str, list[str]] = Field(default_factory=dict)


class MethodIdentity(SchemaModel):
    defining_class_name: str
    method_signature: str
    method_declaration: str
    annotations: list[str] = Field(default_factory=list)
    thrown_exceptions: list[str] = Field(default_factory=list)
    parameterization: ParameterizationSummary | None = None


class MethodMetrics(SchemaModel):
    ncloc: int = 0
    cyclomatic_complexity: int = 0
    number_of_objects_created: int = 0
    assertion_count: int = 0


class ExpandedMetrics(SchemaModel):
    ncloc: int = 0
    cyclomatic_complexity: int = 0
    # Helper invocation events across all phases: a helper called twice counts twice,
    # and fixture helpers are included. Distinct test-body helpers are counted
    # separately by test_helper_method_count.
    helper_method_count: int = 0
    helper_method_ncloc: int = 0
    # Distinct helper methods reachable from the test body only (fixture helpers
    # excluded), so a helper called twice counts once.
    test_helper_method_count: int = 0
    number_of_objects_created: int = 0


class HttpRequestInteraction(SchemaModel):
    origin: OriginContext
    http_call: HttpCallSite | None = None
    endpoint_candidate: EndpointCandidate | None = None


class HttpMockedCallSite(SchemaModel):
    http_method: str
    path: str
    framework: HttpDispatchFramework
    method_name: str
    receiver_type: str = ""
    callee_signature: str = ""
    start_line: int = -1
    mocking_context: MockingContext


class HttpMockedInteraction(SchemaModel):
    origin: OriginContext
    http_call: HttpMockedCallSite


class HttpVerificationInteraction(SchemaModel):
    origin: OriginContext
    assertion_role: AssertionRole
    method_name: str
    source_span: SourceSpan
    framework: HttpDispatchFramework | None = None
    response_role: HttpResponseRole | None = None
    status_code: int | None = None
    status_range: str | None = None


class HttpInteraction(SchemaModel):
    kind: HttpInteractionKind
    origin: OriginContext
    source_span: SourceSpan
    request_interaction: HttpRequestInteraction | None = None
    verification_interaction: HttpVerificationInteraction | None = None

    @model_validator(mode="after")
    def _validate_payload_matches_kind(self) -> HttpInteraction:
        if self.kind == HttpInteractionKind.REQUEST:
            if (
                self.request_interaction is None
                or self.verification_interaction is not None
            ):
                raise ValueError(
                    "request HTTP interactions require request_interaction only"
                )
        elif self.kind == HttpInteractionKind.VERIFICATION and (
            self.verification_interaction is None
            or self.request_interaction is not None
        ):
            raise ValueError(
                "verification HTTP interactions require verification_interaction only"
            )
        return self


class FixtureAnalysis(SchemaModel):
    phase: LifecyclePhase
    defining_class_name: str
    method_signature: str
    annotations: list[str] = Field(default_factory=list)
    ncloc: int = 0
    request_interaction_count: int = 0
    request_interactions: list[HttpRequestInteraction] = Field(default_factory=list)
    verification_interaction_count: int = 0
    verification_interactions: list[HttpVerificationInteraction] = Field(
        default_factory=list
    )
    http_interaction_count: int = 0
    http_interactions: list[HttpInteraction] = Field(default_factory=list)


class FixtureAmbiguityNote(SchemaModel):
    phase: LifecyclePhase
    defining_class_name: str
    method_signature: str
    reason: str = "ambiguous-group-filter"


class RequestDispatchDecision(SchemaModel):
    labels: list[str] = Field(default_factory=list)
    local_request_count: int = 0
    external_request_count: int = 0
    unresolved_request_count: int = 0
    signals: dict[str, list[str]] = Field(default_factory=dict)


class OracleTypeDecision(SchemaModel):
    label: str = "implicit"
    signals: dict[str, list[str]] = Field(default_factory=dict)


class DependencyStrategyDecision(SchemaModel):
    labels: list[str] = Field(default_factory=list)
    signals: dict[str, list[str]] = Field(default_factory=dict)


class HttpResponseExtraction(SchemaModel):
    """Response-data usage (extract/inspect) that is not itself an assertion."""

    origin: OriginContext
    response_role: HttpResponseRole
    method_name: str
    source_span: SourceSpan
    framework: HttpDispatchFramework | None = None


class HttpAnalysis(SchemaModel):
    request_interactions: list[HttpRequestInteraction] = Field(default_factory=list)
    mocked_interactions: list[HttpMockedInteraction] = Field(default_factory=list)
    response_extractions: list[HttpResponseExtraction] = Field(default_factory=list)
    verification_interactions: list[HttpVerificationInteraction] = Field(
        default_factory=list
    )
    http_interactions: list[HttpInteraction] = Field(default_factory=list)
    call_sequence: list[ApiSequenceStep] = Field(default_factory=list)
    test_sequences: list[HttpTestSequence] = Field(default_factory=list)
    sequence_summary: HttpSequenceSummary = Field(default_factory=HttpSequenceSummary)
    resource_interaction_sequences: list[ResourceInteractionSequence] = Field(
        default_factory=list
    )
    request_dispatch: RequestDispatchDecision = Field(
        default_factory=RequestDispatchDecision
    )
    auth_handling: AuthHandlingDecision = Field(default_factory=AuthHandlingDecision)


class AssertionAnalysis(SchemaModel):
    summary: AssertionSummary = Field(default_factory=AssertionSummary)
    oracle_type: OracleTypeDecision = Field(default_factory=OracleTypeDecision)
    failure_scenarios: FailureScenarioSignals = Field(
        default_factory=FailureScenarioSignals
    )
    status_code_distribution: StatusCodeDistribution = Field(
        default_factory=StatusCodeDistribution
    )
    response_surface_labels: list[AssertionRole] = Field(default_factory=list)
    response_surface_combination: str = "none"
    status_code_counts: dict[str, int] = Field(default_factory=dict)
    status_range_counts: dict[str, int] = Field(
        default_factory=_default_status_range_counts
    )
    has_status_check: bool = False
    has_body_check: bool = False
    has_header_check: bool = False
    has_exception_check: bool = False

    @model_validator(mode="after")
    def _populate_m7_summaries(self) -> AssertionAnalysis:
        labels = [
            role
            for role in _RESPONSE_SURFACE_ROLES
            if getattr(self.summary, f"{role.value}_count") > 0
        ]
        self.response_surface_labels = labels
        self.response_surface_combination = _response_surface_combination(labels)
        self.status_range_counts = self.status_code_distribution.to_bucket_counts()
        self.has_status_check = self.summary.status_count > 0
        self.has_body_check = self.summary.body_count > 0
        self.has_header_check = self.summary.header_count > 0
        self.has_exception_check = self.summary.exception_count > 0
        return self


class DependencyAnalysis(SchemaModel):
    strategy: DependencyStrategyDecision = Field(
        default_factory=DependencyStrategyDecision
    )


class StateAnalysis(SchemaModel):
    preconditions: PreconditionSummary = Field(default_factory=PreconditionSummary)
    observations: StateObservationSummary = Field(
        default_factory=StateObservationSummary
    )


class ControllerHandlerTarget(SchemaModel):
    """An endpoint handler method a controller unit test invokes directly."""

    declaring_class_name: str
    declaring_method_signature: str


class TestMethodAnalysis(SchemaModel):
    identity: MethodIdentity
    is_api_test: bool = False
    # A non-API test that directly invokes an endpoint handler method in-process
    # (bypassing HTTP). Mutually exclusive with is_api_test: a request EVENT
    # always classifies the test as API and never as a controller unit test.
    is_controller_unit_test: bool = False
    controller_unit_test_targets: list[ControllerHandlerTarget] = Field(
        default_factory=list
    )
    local_metrics: MethodMetrics = Field(default_factory=MethodMetrics)
    expanded_metrics: ExpandedMetrics = Field(default_factory=ExpandedMetrics)
    http: HttpAnalysis = Field(default_factory=HttpAnalysis)
    assertions: AssertionAnalysis = Field(default_factory=AssertionAnalysis)
    dependencies: DependencyAnalysis = Field(default_factory=DependencyAnalysis)
    state: StateAnalysis = Field(default_factory=StateAnalysis)
    fixtures: list[FixtureAnalysis] = Field(default_factory=list)
    ambiguous_fixture_group_methods: list[FixtureAmbiguityNote] = Field(
        default_factory=list
    )


class TestClassAnalysis(SchemaModel):
    qualified_class_name: str
    testing_frameworks: list[TestingFramework] = Field(default_factory=list)
    fixtures: list[FixtureAnalysis] = Field(default_factory=list)
    test_method_analyses: list[TestMethodAnalysis] = Field(default_factory=list)


class TestMethodReference(SchemaModel):
    qualified_class_name: str
    method_signature: str


class ResourceCrudTestReference(SchemaModel):
    test_method: TestMethodReference
    resource_key: str
    lifecycle_label: CrudLifecycleLabel


class EndpointParameterSource(str, Enum):
    PATH = "path"
    QUERY = "query"
    HEADER = "header"
    BODY = "body"
    FORM = "form"
    UNKNOWN = "unknown"


class EndpointParameter(SchemaModel):
    name: str
    # None for synthesized parameters whose Java type is unknown (see
    # is_synthetic); otherwise the declared parameter type.
    type: str | None = None
    source: EndpointParameterSource
    required: bool = True
    annotation: str | None = None
    # True for aggregate "open" query surfaces (e.g. an unnamed
    # @RequestParam MultiValueMap/Map) that accept arbitrary query keys
    # rather than a single fixed parameter name.
    is_aggregate: bool = False
    # True for parameters synthesized without a backing typed method argument,
    # so the type is unknown: mapping-level params=/headers= constraints, and
    # path variables present in the route template with no recognized binding
    # annotation.
    is_synthetic: bool = False
    # True for structured aggregate bindings (Spring @ModelAttribute, an
    # unresolved JAX-RS @BeanParam) whose individual request names cannot be
    # enumerated; recorded for surface inventory but excluded from coverage.
    is_unscorable: bool = False


class EndpointSurfaceSummary(SchemaModel):
    route_depth: int
    path_variable_count: int
    parameter_sources: list[EndpointParameterSource]
    parameter_count_by_source: dict[EndpointParameterSource, int]
    required_parameter_count_by_source: dict[EndpointParameterSource, int]
    optional_parameter_count_by_source: dict[EndpointParameterSource, int]
    total_required_parameter_count: int
    total_optional_parameter_count: int


def _unset_endpoint_surface_summary() -> EndpointSurfaceSummary:
    # The sentinel keeps computed root surfaces visible with exclude_defaults=True.
    return EndpointSurfaceSummary(
        route_depth=-1,
        path_variable_count=-1,
        parameter_sources=[],
        parameter_count_by_source={},
        required_parameter_count_by_source={},
        optional_parameter_count_by_source={},
        total_required_parameter_count=-1,
        total_optional_parameter_count=-1,
    )


_ENDPOINT_PARAMETER_SOURCE_ORDER: tuple[EndpointParameterSource, ...] = tuple(
    EndpointParameterSource
)


def _endpoint_route_depth(path_template: str) -> int:
    path_without_suffix = (path_template or "").split("?", 1)[0].split("#", 1)[0]
    segments = [
        segment for segment in path_without_suffix.strip("/").split("/") if segment
    ]
    return len(segments)


def _template_path_variable_names(path_template: str) -> list[str]:
    """Ordered, de-duplicated path-variable names declared in a route template.

    Scans balanced ``{...}`` groups rather than matching a flat token regex, so
    a regex constraint with a brace quantifier (e.g. ``{id:[0-9]{4}}``) is read
    as a single ``id`` variable, and a Spring ``${...}``/``#{...}`` configuration
    placeholder — which is not a path variable — is skipped.
    """
    template = (path_template or "").split("?", 1)[0].split("#", 1)[0]
    names: list[str] = []
    seen: set[str] = set()
    index = 0
    length = len(template)
    while index < length:
        if template[index] != "{":
            index += 1
            continue
        depth = 1
        cursor = index + 1
        while cursor < length and depth:
            if template[cursor] == "{":
                depth += 1
            elif template[cursor] == "}":
                depth -= 1
            cursor += 1
        is_placeholder = index > 0 and template[index - 1] in "$#"
        if depth == 0 and not is_placeholder:
            content = template[index + 1 : cursor - 1]
            name = content.lstrip("*").lstrip("/").split(":", 1)[0].strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                names.append(name)
        index = cursor
    return names


def _endpoint_path_variable_count(path_template: str) -> int:
    return len(_template_path_variable_names(path_template))


def _endpoint_parameter_source(
    parameter: EndpointParameter | dict[str, object],
) -> EndpointParameterSource:
    if isinstance(parameter, EndpointParameter):
        return parameter.source

    raw_source = parameter.get("source", EndpointParameterSource.UNKNOWN)
    if isinstance(raw_source, EndpointParameterSource):
        return raw_source
    try:
        return EndpointParameterSource(str(raw_source))
    except ValueError:
        return EndpointParameterSource.UNKNOWN


def _endpoint_parameter_required(
    parameter: EndpointParameter | dict[str, object],
) -> bool:
    if isinstance(parameter, EndpointParameter):
        return parameter.required

    raw_required = parameter.get("required", True)
    if raw_required is None:
        return True
    return bool(raw_required)


def _build_endpoint_surface_summary(
    path_template: str,
    parameters: list[EndpointParameter | dict[str, object]],
) -> EndpointSurfaceSummary:
    parameter_count_by_source: dict[EndpointParameterSource, int] = {}
    required_parameter_count_by_source: dict[EndpointParameterSource, int] = {}
    optional_parameter_count_by_source: dict[EndpointParameterSource, int] = {}

    for parameter in parameters:
        source = _endpoint_parameter_source(parameter)
        parameter_count_by_source[source] = parameter_count_by_source.get(source, 0) + 1
        if _endpoint_parameter_required(parameter):
            required_parameter_count_by_source[source] = (
                required_parameter_count_by_source.get(source, 0) + 1
            )
        else:
            optional_parameter_count_by_source[source] = (
                optional_parameter_count_by_source.get(source, 0) + 1
            )

    parameter_sources = [
        source
        for source in _ENDPOINT_PARAMETER_SOURCE_ORDER
        if source in parameter_count_by_source
    ]

    return EndpointSurfaceSummary(
        route_depth=_endpoint_route_depth(path_template),
        path_variable_count=_endpoint_path_variable_count(path_template),
        parameter_sources=parameter_sources,
        parameter_count_by_source={
            source: parameter_count_by_source.get(source, 0)
            for source in parameter_sources
        },
        required_parameter_count_by_source={
            source: required_parameter_count_by_source.get(source, 0)
            for source in parameter_sources
        },
        optional_parameter_count_by_source={
            source: optional_parameter_count_by_source.get(source, 0)
            for source in parameter_sources
        },
        total_required_parameter_count=sum(required_parameter_count_by_source.values()),
        total_optional_parameter_count=sum(optional_parameter_count_by_source.values()),
    )


class ApplicationEndpoint(SchemaModel):
    http_method: str
    is_method_wildcard: bool = False
    path_template: str
    framework: str
    declaring_class_name: str
    declaring_method_signature: str | None = None
    parameters: list[EndpointParameter] = Field(default_factory=list)
    surface: EndpointSurfaceSummary = Field(
        default_factory=_unset_endpoint_surface_summary
    )

    @model_validator(mode="before")
    @classmethod
    def _populate_surface_summary(cls, data: object) -> object:
        if not isinstance(data, dict) or data.get("surface") is not None:
            return data

        endpoint_data = dict(data)
        raw_parameters = endpoint_data.get("parameters") or []
        parameters = raw_parameters if isinstance(raw_parameters, list) else []
        endpoint_data["surface"] = _build_endpoint_surface_summary(
            str(endpoint_data.get("path_template") or ""),
            parameters,
        )
        return endpoint_data


class ProductionResourceCrudEntry(SchemaModel):
    resource_key: str
    endpoints: list[ApplicationEndpoint] = Field(default_factory=list)
    available_operations: list[CrudOperation] = Field(default_factory=list)
    exercised_operations: list[CrudOperation] = Field(default_factory=list)
    missing_available_operations: list[CrudOperation] = Field(default_factory=list)
    exercising_test_resources_by_operation: dict[
        CrudOperation, list[ResourceCrudTestReference]
    ] = Field(default_factory=dict)
    full_crud_test_count: int = 0
    read_only_test_count: int = 0


class ProductionResourceCrudSummary(SchemaModel):
    total_resource_count: int = 0
    resources_with_any_test_count: int = 0
    resources_with_full_crud_test_count: int = 0
    resources: list[ProductionResourceCrudEntry] = Field(default_factory=list)


class EndpointAssertedStatusOutcomes(SchemaModel):
    """Status assertions attributed per request to this endpoint.

    Attribution follows test-sequence structure: every response check belongs
    to its sequence's single request, so deferred batch assertions (capture two
    responses, assert both afterwards) attach to the later request.
    """

    attributed_request_count: int = 0
    status_asserted_request_count: int = 0
    asserting_test_method_count: int = 0
    status_range_counts: dict[str, int] = Field(default_factory=dict)
    status_code_counts: dict[str, int] = Field(default_factory=dict)


class EndpointCoverageEntry(SchemaModel):
    endpoint: ApplicationEndpoint
    covering_test_methods: list[TestMethodReference] = Field(default_factory=list)
    covering_test_method_count: int = 0
    is_covered: bool = False
    asserted_outcomes: EndpointAssertedStatusOutcomes = Field(
        default_factory=EndpointAssertedStatusOutcomes
    )


class EndpointCoverageSummary(SchemaModel):
    total_application_endpoints: int = 0
    covered_endpoint_count: int = 0
    untested_endpoint_count: int = 0
    coverage_ratio: float = 0.0
    endpoints: list[EndpointCoverageEntry] = Field(default_factory=list)
    discovered_application_paths: list[str] = Field(default_factory=list)


class ControllerUnitTestEndpointEntry(SchemaModel):
    """An endpoint whose handler method is invoked directly by controller unit tests."""

    endpoint: ApplicationEndpoint
    exercising_test_methods: list[TestMethodReference] = Field(default_factory=list)
    exercising_test_method_count: int = 0


class ControllerUnitTestSummary(SchemaModel):
    controller_unit_test_count: int = 0
    targeted_endpoint_count: int = 0
    # Only endpoints with at least one exercising controller unit test are listed.
    endpoints: list[ControllerUnitTestEndpointEntry] = Field(default_factory=list)


class ParameterExerciseEvidence(SchemaModel):
    test_method: TestMethodReference
    exercised_parameters: list[EndpointParameter] = Field(default_factory=list)


class ParameterCoverageEntry(SchemaModel):
    parameter: EndpointParameter
    is_exercised: bool = False
    exercising_test_count: int = 0


class ObservedOptionalParameterSet(SchemaModel):
    parameter_keys: list[str] = Field(default_factory=list)
    test_count: int = 0


class EndpointParameterCoverageEntry(SchemaModel):
    endpoint: ApplicationEndpoint
    parameter_evidence: list[ParameterExerciseEvidence] = Field(default_factory=list)
    parameter_entries: list[ParameterCoverageEntry] = Field(default_factory=list)
    exercised_parameter_count: int = 0
    total_parameter_count: int = 0
    # Every rate is None when its denominator is 0 (no parameters of that kind to
    # exercise), kept distinct from a genuine 0.0 (a real denominator, none
    # exercised) so downstream distribution analysis can tell N/A from 0%.
    exercise_rate: float | None = None
    exercise_rate_by_source: dict[EndpointParameterSource, float] = Field(
        default_factory=dict
    )
    required_parameter_count: int = 0
    required_exercised_count: int = 0
    required_exercise_rate: float | None = None
    optional_parameter_count: int = 0
    optional_exercised_count: int = 0
    optional_exercise_rate: float | None = None
    # Per-source required/optional stats, restricted to sources where we perform
    # named parameter analysis and optionality is meaningful (query/header/form).
    # Every source present on the endpoint is keyed in all of these dicts; counts
    # are genuine (0 allowed) and a rate is None when its denominator is 0, so a
    # count of 0 pairs with a rate of None.
    required_parameter_count_by_source: dict[EndpointParameterSource, int] = Field(
        default_factory=dict
    )
    required_exercised_count_by_source: dict[EndpointParameterSource, int] = Field(
        default_factory=dict
    )
    required_exercise_rate_by_source: dict[EndpointParameterSource, float | None] = (
        Field(default_factory=dict)
    )
    optional_parameter_count_by_source: dict[EndpointParameterSource, int] = Field(
        default_factory=dict
    )
    optional_exercised_count_by_source: dict[EndpointParameterSource, int] = Field(
        default_factory=dict
    )
    optional_exercise_rate_by_source: dict[EndpointParameterSource, float | None] = (
        Field(default_factory=dict)
    )
    route_covering_test_count: int = 0
    observed_optional_parameter_set_limit: int = 256
    observed_optional_parameter_sets_truncated: bool = False
    # Holistic count of distinct optional-parameter present-sets observed across
    # covering requests (mixes every source into one combinatorial space).
    distinct_observed_optional_parameter_set_count: int = 0
    # Same count projected onto a single source's optional parameters.
    distinct_observed_optional_set_count_by_source: dict[
        EndpointParameterSource, int
    ] = Field(default_factory=dict)
    observed_optional_parameter_sets: list[ObservedOptionalParameterSet] = Field(
        default_factory=list
    )
    # NIST simple 1-way coverage (each-choice): an optional parameter is covered
    # when it was observed both present and absent across covering requests.
    # Holistic spans every optional parameter; the per-source dicts project onto
    # query/header/form.
    simple_1_way_optional_covered_count: int = 0
    simple_1_way_optional_coverage: float | None = None
    simple_1_way_optional_covered_count_by_source: dict[
        EndpointParameterSource, int
    ] = Field(default_factory=dict)
    simple_1_way_optional_coverage_by_source: dict[
        EndpointParameterSource, float | None
    ] = Field(default_factory=dict)
    # 2-way coverage over optional-parameter pairs. Simple (NIST simple 2-way):
    # a pair counts only when all four present/absent combinations were observed.
    # Total (NIST total variable-value configuration coverage): the proportion of
    # the 4*pair_count configurations observed at all, giving partial credit to
    # suites that vary parameters one at a time. Holistic pairs span sources;
    # per-source pairs stay within one source.
    optional_pair_count: int = 0
    simple_2_way_optional_covered_count: int = 0
    simple_2_way_optional_coverage: float | None = None
    total_2_way_optional_covered_config_count: int = 0
    total_2_way_optional_coverage: float | None = None
    optional_pair_count_by_source: dict[EndpointParameterSource, int] = Field(
        default_factory=dict
    )
    simple_2_way_optional_covered_count_by_source: dict[
        EndpointParameterSource, int
    ] = Field(default_factory=dict)
    simple_2_way_optional_coverage_by_source: dict[
        EndpointParameterSource, float | None
    ] = Field(default_factory=dict)
    total_2_way_optional_covered_config_count_by_source: dict[
        EndpointParameterSource, int
    ] = Field(default_factory=dict)
    total_2_way_optional_coverage_by_source: dict[
        EndpointParameterSource, float | None
    ] = Field(default_factory=dict)


class EndpointParameterCoverageSummary(SchemaModel):
    total_endpoints_with_parameters: int = 0
    fully_exercised_endpoint_count: int = 0
    partially_exercised_endpoint_count: int = 0
    unexercised_endpoint_count: int = 0
    # Endpoints whose every parameter is unscorable (@ModelAttribute, unresolved
    # @BeanParam): they have a binding surface we cannot enumerate, so they are
    # counted here instead of fully/partial/unexercised.
    unscorable_endpoint_count: int = 0
    endpoints: list[EndpointParameterCoverageEntry] = Field(default_factory=list)


class ProjectSummary(SchemaModel):
    api_test_count: int = 0
    non_api_test_count: int = 0
    # Subset of non_api_test_count: non-API tests that drive an endpoint handler
    # method directly in-process.
    controller_unit_test_count: int = 0
    total_http_interactions: int = 0
    coverage_ratio: float = 0.0


class ProjectMetadata(SchemaModel):
    project_path: str
    git_commit_hash: str | None = None
    git_remote_host: str | None = None
    git_repository: str | None = None
    expanded_helper_depth: int = 1
    analysis_level: Literal["symbol_table"] = "symbol_table"


class ProjectAnalysis(SchemaModel):
    dataset_name: str
    metadata: ProjectMetadata
    summary: ProjectSummary = Field(default_factory=ProjectSummary)
    application_class_count: int = 0
    application_method_count: int = 0
    application_cyclomatic_complexity: int = 0
    test_class_count: int = 0
    test_method_count: int = 0
    test_utility_class_count: int = 0
    test_utility_method_count: int = 0
    endpoint_coverage: EndpointCoverageSummary = Field(
        default_factory=EndpointCoverageSummary
    )
    endpoint_parameter_coverage: EndpointParameterCoverageSummary = Field(
        default_factory=EndpointParameterCoverageSummary
    )
    resource_crud: ProductionResourceCrudSummary = Field(
        default_factory=ProductionResourceCrudSummary
    )
    controller_unit_tests: ControllerUnitTestSummary = Field(
        default_factory=ControllerUnitTestSummary
    )
    test_class_analyses: list[TestClassAnalysis] = Field(default_factory=list)
