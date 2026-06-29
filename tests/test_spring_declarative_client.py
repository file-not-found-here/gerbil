from __future__ import annotations

import pytest
from cldk.models.java.models import JCallableParameter, JField

from gerbil.analysis.http.classification import classify_http_on_runtime_view
from gerbil.analysis.properties.request_dispatch import analyze_request_dispatch
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.properties.endpoint.parameter_analysis import (
    build_endpoint_parameter_coverage_summary,
)
from gerbil.analysis.schema import (
    ApplicationEndpoint,
    CallSiteOriginKind,
    EndpointCandidate,
    EndpointParameter,
    EndpointParameterSource,
    HttpAnalysis,
    HttpCallSite,
    HttpDispatchFramework,
    HttpRequestInteraction,
    HttpRequestRole,
    LifecyclePhase,
    MethodIdentity,
    OriginContext,
    TestClassAnalysis as ModelClassAnalysis,
    TestMethodAnalysis as ModelMethodAnalysis,
)
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
    make_callable_parameter,
    make_field,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis

_CLIENT_CLASS = "com.example.OrderClient"
_CLIENT_FILE = "OrderClient.java"
_SPRING_BIND_IMPORTS = [
    "org.springframework.web.bind.annotation.PathVariable",
    "org.springframework.web.bind.annotation.RequestBody",
    "org.springframework.web.bind.annotation.RequestParam",
    "org.springframework.web.bind.annotation.RequestHeader",
    "org.springframework.web.bind.annotation.RequestPart",
]
_FEIGN_IMPORTS = [
    "org.springframework.cloud.openfeign.FeignClient",
    "org.springframework.web.bind.annotation.GetMapping",
    "org.springframework.web.bind.annotation.PostMapping",
    "org.springframework.web.bind.annotation.RequestMapping",
    *_SPRING_BIND_IMPORTS,
]
_HTTP_EXCHANGE_IMPORTS = [
    "org.springframework.web.service.annotation.HttpExchange",
    "org.springframework.web.service.annotation.GetExchange",
    "org.springframework.web.service.annotation.PostExchange",
    "org.springframework.web.service.annotation.PutExchange",
    "org.springframework.web.service.annotation.DeleteExchange",
    "org.springframework.web.service.annotation.PatchExchange",
    *_SPRING_BIND_IMPORTS,
]


def _classify_declarative_call(
    *,
    class_annotations: list[str],
    method_signature: str,
    method_annotations: list[str],
    method_parameters: list[JCallableParameter] | None = None,
    client_in_analyzed_set: bool = True,
    imports: list[str] | None = None,
    class_field_declarations: list[JField] | None = None,
    client_is_interface: bool = False,
):
    call_site = make_call_site(
        method_name=method_signature.split("(", 1)[0],
        receiver_type=_CLIENT_CLASS,
        receiver_expr="orderClient",
        callee_signature=method_signature,
        argument_expr=["1L"],
        start_line=5,
        start_column=9,
        end_column=40,
    )
    test_method = make_callable(call_sites=[call_site])
    grouping = build_call_site_grouping(test_method.call_sites)
    owner = MethodRef(
        defining_class_name="example.OrderClientTest",
        method_signature=test_method.signature,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=grouping,
                method_details=test_method,
            )
        ]
    )

    classes = {}
    methods_by_class = {}
    java_files = {}
    import_declarations_by_file = {}
    if client_in_analyzed_set:
        classes[_CLIENT_CLASS] = make_type(
            annotations=class_annotations,
            field_declarations=class_field_declarations or [],
            is_interface=client_is_interface,
        )
        methods_by_class[_CLIENT_CLASS] = {
            method_signature: make_callable(
                signature=method_signature,
                annotations=method_annotations,
                parameters=method_parameters or [],
            )
        }
        java_files[_CLIENT_CLASS] = _CLIENT_FILE
        import_declarations_by_file[_CLIENT_FILE] = imports or _FEIGN_IMPORTS

    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class=methods_by_class,
        java_files=java_files,
        import_declarations_by_file=import_declarations_by_file,
    )
    resolver = build_runtime_receiver_resolver_for_testing(
        runtime_view, analysis=analysis
    )
    classify_http_on_runtime_view(runtime_view=runtime_view, receiver_resolver=resolver)

    return grouping.nodes[0].http_classification, runtime_view


# --------------------------------------------------------------------------- #
# Spring Cloud OpenFeign (@FeignClient)
# --------------------------------------------------------------------------- #


def test_feign_get_mapping_composes_class_and_method_path() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=[
            '@FeignClient(name = "orders")',
            '@RequestMapping("/api")',
        ],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
    )
    assert classification is not None
    assert classification.framework == HttpDispatchFramework.FEIGN
    assert classification.request_role == HttpRequestRole.EVENT
    assert classification.http_method == "GET"
    assert classification.path == "/api/orders/{id}"
    assert classification.path_param_names == ["id"]
    assert classification.owner_family == "feign.client_interface"


def test_feign_absolute_url_authority_is_preserved() -> None:
    # A literal absolute http(s) @FeignClient(url=...) is the real target, so its
    # authority survives composition (instead of being dropped to a local route).
    classification, runtime_view = _classify_declarative_call(
        class_annotations=[
            '@FeignClient(name = "orders", url = "https://api.external.com", '
            'path = "/svc")',
        ],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
    )
    assert classification is not None
    assert classification.path == "https://api.external.com/svc/orders/{id}"
    assert classification.path_param_names == ["id"]
    decision = analyze_request_dispatch(runtime_view=runtime_view)
    assert decision.labels == ["remote-network"]


def test_feign_service_name_url_is_not_a_path() -> None:
    # A bare service name / property placeholder is not an absolute target, so the
    # base path stays the @FeignClient(path=...) prefix with no fabricated authority.
    for url_literal in (
        '"orders"',
        '"${orders.url}"',
        # A scheme with a property-placeholder host is not a concrete authority.
        '"https://${orders.host}"',
    ):
        classification, _ = _classify_declarative_call(
            class_annotations=[
                f'@FeignClient(name = "orders", url = {url_literal}, path = "/svc")',
            ],
            method_signature="getOrder(java.lang.Long)",
            method_annotations=['@GetMapping("/orders/{id}")'],
        )
        assert classification is not None
        assert classification.path == "/svc/orders/{id}"


def test_feign_partial_concat_url_does_not_fabricate_authority() -> None:
    # A concat whose host is statically unknown must NOT contribute its literal
    # head ("https://api.") as a fabricated authority — the base stays the path.
    classification, _ = _classify_declarative_call(
        class_annotations=[
            '@FeignClient(name = "orders", url = "https://api." + region '
            '+ ".example.com", path = "/svc")',
        ],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
    )
    assert classification is not None
    assert classification.path == "/svc/orders/{id}"


def test_feign_post_mapping_without_class_request_mapping() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="createOrder(com.example.Order)",
        method_annotations=['@PostMapping("/orders")'],
    )
    assert classification is not None
    assert classification.http_method == "POST"
    assert classification.path == "/orders"


def test_feign_request_mapping_method_attribute_resolves_verb() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="findOrder(java.lang.Long)",
        method_annotations=[
            '@RequestMapping(path = "/orders/{id}", method = RequestMethod.GET)'
        ],
    )
    assert classification is not None
    assert classification.http_method == "GET"
    assert classification.path == "/orders/{id}"


def test_feign_client_path_attribute_is_base_path() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders", path = "/svc")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
    )
    assert classification is not None
    # The FeignClient name ("orders") must not be mistaken for a base path.
    assert classification.path == "/svc/orders/{id}"


def test_feign_dispatch_is_real_http() -> None:
    _, runtime_view = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")', '@RequestMapping("/api")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
    )
    decision = analyze_request_dispatch(runtime_view=runtime_view)
    assert decision.labels == ["local-network"]


def test_feign_field_receiver_with_empty_call_site_type_classifies() -> None:
    # CLDK commonly leaves call_site.receiver_type empty for an injected field
    # receiver (`@Autowired OrderClient orderClient; orderClient.getOrder(id)`);
    # the resolved field type must still drive callee/declarative classification.
    client_class = "com.example.OrderClient"
    test_class = "example.OrderClientTest"
    method_signature = "getOrder(java.lang.Long)"

    call_site = make_call_site(
        method_name="getOrder",
        receiver_type="",
        receiver_expr="orderClient",
        callee_signature=method_signature,
        argument_expr=["1L"],
        start_line=5,
        start_column=9,
        end_column=40,
    )
    test_method = make_callable(call_sites=[call_site])
    grouping = build_call_site_grouping(test_method.call_sites)
    owner = MethodRef(
        defining_class_name=test_class,
        method_signature=test_method.signature,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=grouping,
                method_details=test_method,
            )
        ]
    )
    analysis = FakeJavaAnalysis(
        classes={
            client_class: make_type(
                annotations=['@FeignClient(name = "orders")'],
                is_interface=True,
            ),
            test_class: make_type(
                field_declarations=[
                    make_field(type_name=client_class, variables=["orderClient"]),
                ],
            ),
        },
        methods_by_class={
            client_class: {
                method_signature: make_callable(
                    signature=method_signature,
                    annotations=['@GetMapping("/orders/{id}")'],
                ),
            },
        },
        java_files={
            client_class: _CLIENT_FILE,
            test_class: "OrderClientTest.java",
        },
        import_declarations_by_file={_CLIENT_FILE: _FEIGN_IMPORTS},
    )
    resolver = build_runtime_receiver_resolver_for_testing(
        runtime_view, analysis=analysis
    )
    classify_http_on_runtime_view(runtime_view=runtime_view, receiver_resolver=resolver)

    classification = grouping.nodes[0].http_classification
    assert classification is not None
    assert classification.framework == HttpDispatchFramework.FEIGN
    assert classification.http_method == "GET"
    assert classification.path == "/orders/{id}"


def test_non_feign_interface_is_not_classified() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=["@Component"],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
    )
    assert classification is None


def test_feign_method_without_mapping_annotation_is_not_classified() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="helper(java.lang.Long)",
        method_annotations=[],
    )
    assert classification is None


def test_feign_client_outside_analyzed_set_degrades_gracefully() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
        client_in_analyzed_set=False,
    )
    assert classification is None


def test_feign_annotation_without_matching_import_is_not_classified() -> None:
    # @FeignClient present but no org.springframework.cloud.openfeign import —
    # the import-validated matcher must reject it to avoid false positives.
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
        imports=["com.example.NotFeign"],
    )
    assert classification is None


# --------------------------------------------------------------------------- #
# Spring HTTP Interface (@HttpExchange)
# --------------------------------------------------------------------------- #


def test_http_exchange_composes_class_url_and_method_path() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@HttpExchange(url = "/api")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetExchange("/orders/{id}")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.framework == HttpDispatchFramework.HTTP_INTERFACE
    assert classification.request_role == HttpRequestRole.EVENT
    assert classification.http_method == "GET"
    assert classification.path == "/api/orders/{id}"
    assert classification.path_param_names == ["id"]
    assert classification.owner_family == "http-interface.client_interface"


def test_http_exchange_post_exchange_url_attribute_is_path() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="createOrder(com.example.Order)",
        method_annotations=['@PostExchange(url = "/orders")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.http_method == "POST"
    assert classification.path == "/orders"


@pytest.mark.parametrize(
    ("annotation", "expected_method"),
    [
        ('@GetExchange("/orders")', "GET"),
        ('@PostExchange("/orders")', "POST"),
        ('@PutExchange("/orders")', "PUT"),
        ('@DeleteExchange("/orders")', "DELETE"),
        ('@PatchExchange("/orders")', "PATCH"),
    ],
)
def test_http_exchange_verb_shortcuts_resolve_method(
    annotation: str, expected_method: str
) -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="callOrder(java.lang.Long)",
        method_annotations=[annotation],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.framework == HttpDispatchFramework.HTTP_INTERFACE
    assert classification.http_method == expected_method
    assert classification.path == "/orders"


def test_http_exchange_method_level_method_attribute_resolves_verb() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="findOrder(java.lang.Long)",
        method_annotations=['@HttpExchange(method = "GET", url = "/orders/{id}")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.http_method == "GET"
    assert classification.path == "/orders/{id}"


def test_http_exchange_method_level_without_method_is_unknown() -> None:
    # A bare method-level @HttpExchange with no `method` attribute leaves the verb
    # undetermined; mirror @RequestMapping wildcard handling and emit UNKNOWN.
    classification, _ = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="anyOrder(java.lang.Long)",
        method_annotations=['@HttpExchange(url = "/orders")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.http_method == "UNKNOWN"
    assert classification.path == "/orders"


def test_http_exchange_is_labeled_http_interface_not_feign() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetExchange("/orders/{id}")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.framework == HttpDispatchFramework.HTTP_INTERFACE
    assert classification.framework != HttpDispatchFramework.FEIGN


def test_http_exchange_dispatch_is_real_http() -> None:
    _, runtime_view = _classify_declarative_call(
        class_annotations=['@HttpExchange(url = "/api")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetExchange("/orders/{id}")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    decision = analyze_request_dispatch(runtime_view=runtime_view)
    assert decision.labels == ["local-network"]


def test_http_exchange_method_without_mapping_annotation_is_not_classified() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="helper(java.lang.Long)",
        method_annotations=[],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is None


def test_http_exchange_annotation_without_matching_import_is_not_classified() -> None:
    # @HttpExchange present but no org.springframework.web.service.annotation
    # import — the import-validated matcher must reject it.
    classification, _ = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetExchange("/orders/{id}")'],
        imports=["com.example.NotHttpExchange"],
    )
    assert classification is None


# --------------------------------------------------------------------------- #
# Absolute urls preserve their authority for request dispatch
# --------------------------------------------------------------------------- #


def test_http_exchange_absolute_method_url_is_remote_network() -> None:
    classification, runtime_view = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetExchange("https://api.external.com/orders/{id}")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    # The authority must survive composition so dispatch can see it is remote.
    assert classification.path == "https://api.external.com/orders/{id}"
    assert classification.path_param_names == ["id"]
    decision = analyze_request_dispatch(runtime_view=runtime_view)
    assert decision.labels == ["remote-network"]


def test_http_exchange_absolute_class_url_is_remote_network() -> None:
    classification, runtime_view = _classify_declarative_call(
        class_annotations=['@HttpExchange(url = "https://svc.external.com")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetExchange("/orders")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.path == "https://svc.external.com/orders"
    decision = analyze_request_dispatch(runtime_view=runtime_view)
    assert decision.labels == ["remote-network"]


def test_http_exchange_absolute_local_url_stays_local_network() -> None:
    classification, runtime_view = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetExchange("http://localhost:8080/orders")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.path == "http://localhost:8080/orders"
    decision = analyze_request_dispatch(runtime_view=runtime_view)
    assert decision.labels == ["local-network"]


def test_feign_absolute_method_url_is_remote_network() -> None:
    # The host-preserving composition is shared with Feign.
    classification, runtime_view = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("https://api.external.com/orders")'],
    )
    assert classification is not None
    assert classification.path == "https://api.external.com/orders"
    decision = analyze_request_dispatch(runtime_view=runtime_view)
    assert decision.labels == ["remote-network"]


# --------------------------------------------------------------------------- #
# Annotation families are not mixed across client kinds
# --------------------------------------------------------------------------- #


def test_http_exchange_does_not_match_mvc_mapping_annotation() -> None:
    # An @HttpExchange interface whose method carries an MVC @GetMapping is not a
    # valid HTTP Interface declaration; imports for both families are present, so
    # rejection is family enforcement, not missing imports.
    classification, _ = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
        imports=_FEIGN_IMPORTS + _HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is None


def test_feign_does_not_match_exchange_annotation() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetExchange("/orders/{id}")'],
        imports=_FEIGN_IMPORTS + _HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is None


# --------------------------------------------------------------------------- #
# Type-level @HttpExchange(method) is inherited
# --------------------------------------------------------------------------- #


def test_http_exchange_inherits_type_level_method() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@HttpExchange(method = "GET", url = "/api")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@HttpExchange(url = "/orders/{id}")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.http_method == "GET"
    assert classification.path == "/api/orders/{id}"


def test_http_exchange_method_level_verb_overrides_type_level() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@HttpExchange(method = "GET")'],
        method_signature="createOrder(com.example.Order)",
        method_annotations=['@PostExchange("/orders")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.http_method == "POST"
    assert classification.path == "/orders"


def test_http_exchange_without_type_level_method_stays_unknown() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@HttpExchange(url = "/api")'],
        method_signature="anyOrder(java.lang.Long)",
        method_annotations=['@HttpExchange(url = "/orders")'],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.http_method == "UNKNOWN"
    assert classification.path == "/api/orders"


# --------------------------------------------------------------------------- #
# Mapping paths declared as String constants resolve at call-site classification
# --------------------------------------------------------------------------- #


def test_http_exchange_method_path_constant_resolves() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=["@GetExchange(ORDERS_PATH)"],
        imports=_HTTP_EXCHANGE_IMPORTS,
        client_is_interface=True,
        class_field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["ORDERS_PATH"],
                variable_initializers={"ORDERS_PATH": '"/orders/{id}"'},
            )
        ],
    )
    assert classification is not None
    assert classification.framework == HttpDispatchFramework.HTTP_INTERFACE
    assert classification.http_method == "GET"
    # The mapping path is a bare interface constant, so it only becomes a path
    # when the call-site classifier routes it through the callee-bound resolver.
    assert classification.path == "/orders/{id}"
    assert classification.path_param_names == ["id"]


def test_feign_class_and_method_path_constants_resolve() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")', "@RequestMapping(BASE)"],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=["@GetMapping(DETAIL)"],
        class_field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["BASE"],
                modifiers=["static", "final"],
                variable_initializers={"BASE": '"/api"'},
            ),
            make_field(
                type_name="java.lang.String",
                variables=["DETAIL"],
                modifiers=["static", "final"],
                variable_initializers={"DETAIL": '"/orders/{id}"'},
            ),
        ],
    )
    assert classification is not None
    assert classification.framework == HttpDispatchFramework.FEIGN
    assert classification.http_method == "GET"
    assert classification.path == "/api/orders/{id}"
    assert classification.path_param_names == ["id"]


def test_feign_client_path_attribute_constant_resolves() -> None:
    # @FeignClient(path = CONST) is routed through the same callee-bound resolver
    # as the other mapping attributes, so the base prefix is recovered.
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders", path = BASE)'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
        class_field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["BASE"],
                modifiers=["static", "final"],
                variable_initializers={"BASE": '"/svc"'},
            ),
        ],
    )
    assert classification is not None
    assert classification.path == "/svc/orders/{id}"


def test_declarative_client_without_resolver_leaves_constant_path_unresolved() -> None:
    # The constant lives on the callee interface, not the owner test class; without
    # the callee-bound resolver the bare identifier cannot become a path.
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=["@GetMapping(DETAIL)"],
        class_field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["DETAIL"],
                modifiers=["static", "final"],
                variable_initializers={"DETAIL": 'System.getProperty("detail")'},
            )
        ],
    )
    assert classification is not None
    assert classification.http_method == "GET"
    assert classification.path == ""


# --------------------------------------------------------------------------- #
# Callee parameter bindings project into event evidence
# --------------------------------------------------------------------------- #


def test_feign_request_body_param_sets_has_body_payload() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="createOrder(com.example.Order)",
        method_annotations=['@PostMapping("/orders")'],
        method_parameters=[
            make_callable_parameter(
                name="order",
                type_name="com.example.Order",
                annotations=["@RequestBody"],
            ),
        ],
    )
    assert classification is not None
    assert classification.has_body_payload is True
    assert classification.query_param_names == []
    assert classification.header_names == []
    assert classification.form_param_names == []


def test_feign_request_param_explicit_name_is_query_evidence() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="findOrders(java.lang.String)",
        method_annotations=['@GetMapping("/orders")'],
        method_parameters=[
            make_callable_parameter(
                name="status",
                type_name="java.lang.String",
                annotations=['@RequestParam("q")'],
            ),
        ],
    )
    assert classification is not None
    # The annotation's explicit name wins over the Java parameter name.
    assert classification.query_param_names == ["q"]
    assert classification.has_body_payload is False


def test_feign_request_param_without_explicit_name_uses_java_name() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="findOrders(java.lang.String)",
        method_annotations=['@GetMapping("/orders")'],
        method_parameters=[
            make_callable_parameter(
                name="status",
                type_name="java.lang.String",
                annotations=["@RequestParam"],
            ),
        ],
    )
    assert classification is not None
    assert classification.query_param_names == ["status"]


def test_feign_request_header_is_header_evidence() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
        method_parameters=[
            make_callable_parameter(
                name="id",
                type_name="java.lang.Long",
                annotations=['@PathVariable("id")'],
            ),
            make_callable_parameter(
                name="tenant",
                type_name="java.lang.String",
                annotations=['@RequestHeader("X-Tenant")'],
            ),
        ],
    )
    assert classification is not None
    assert classification.header_names == ["X-Tenant"]
    assert classification.path_param_names == ["id"]


def test_feign_request_part_is_form_evidence() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="upload(org.springframework.web.multipart.MultipartFile)",
        method_annotations=['@PostMapping("/orders/upload")'],
        method_parameters=[
            make_callable_parameter(
                name="file",
                type_name="org.springframework.web.multipart.MultipartFile",
                annotations=['@RequestPart("payload")'],
            ),
        ],
    )
    assert classification is not None
    assert classification.form_param_names == ["payload"]


def test_feign_path_variable_does_not_pollute_other_surfaces() -> None:
    # Path variables come from the route template (the source of truth) and must
    # not appear as query/header/form evidence or imply a body payload.
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="getOrder(java.lang.Long)",
        method_annotations=['@GetMapping("/orders/{id}")'],
        method_parameters=[
            make_callable_parameter(
                name="id",
                type_name="java.lang.Long",
                annotations=['@PathVariable("id")'],
            ),
        ],
    )
    assert classification is not None
    assert classification.path_param_names == ["id"]
    assert classification.query_param_names == []
    assert classification.header_names == []
    assert classification.form_param_names == []
    assert classification.has_body_payload is False


def test_feign_aggregate_request_param_map_contributes_no_query_name() -> None:
    # An unnamed @RequestParam Map is an open query surface with no concrete key,
    # so it must not project the Java parameter name as a false query parameter.
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="search(java.util.Map)",
        method_annotations=['@GetMapping("/orders")'],
        method_parameters=[
            make_callable_parameter(
                name="filters",
                type_name="java.util.Map<java.lang.String, java.lang.String>",
                annotations=["@RequestParam"],
            ),
        ],
    )
    assert classification is not None
    assert classification.query_param_names == []


def test_feign_cookie_value_param_is_not_projected() -> None:
    # @CookieValue is a Spring binding deliberately outside the tracked source
    # map (no cookie surface in coverage), so it contributes no event evidence.
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="findOrders(java.lang.String)",
        method_annotations=['@GetMapping("/orders")'],
        method_parameters=[
            make_callable_parameter(
                name="session",
                type_name="java.lang.String",
                annotations=['@CookieValue("SESSION")'],
            ),
        ],
    )
    assert classification is not None
    assert classification.query_param_names == []
    assert classification.header_names == []
    assert classification.form_param_names == []
    assert classification.has_body_payload is False


def test_feign_unannotated_parameter_is_not_treated_as_body() -> None:
    # Unlike JAX-RS, Spring requires an explicit @RequestBody; an unannotated
    # argument is not a synthesized body.
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="createOrder(com.example.Order)",
        method_annotations=['@PostMapping("/orders")'],
        method_parameters=[
            make_callable_parameter(
                name="order",
                type_name="com.example.Order",
                annotations=[],
            ),
        ],
    )
    assert classification is not None
    assert classification.has_body_payload is False
    assert classification.query_param_names == []


def test_feign_optional_request_param_still_projected() -> None:
    # Requiredness does not gate event evidence: a call still supplies the value.
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="findOrders(java.lang.String)",
        method_annotations=['@GetMapping("/orders")'],
        method_parameters=[
            make_callable_parameter(
                name="status",
                type_name="java.lang.String",
                annotations=['@RequestParam(value = "q", required = false)'],
            ),
        ],
    )
    assert classification is not None
    assert classification.query_param_names == ["q"]


def test_feign_mixed_parameters_projected_across_surfaces() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature=(
            "createOrder(java.lang.Long,com.example.Order,"
            "java.lang.String,java.lang.String)"
        ),
        method_annotations=['@PostMapping("/orders/{id}")'],
        method_parameters=[
            make_callable_parameter(
                name="id",
                type_name="java.lang.Long",
                annotations=['@PathVariable("id")'],
            ),
            make_callable_parameter(
                name="order",
                type_name="com.example.Order",
                annotations=["@RequestBody"],
            ),
            make_callable_parameter(
                name="status",
                type_name="java.lang.String",
                annotations=['@RequestParam("q")'],
            ),
            make_callable_parameter(
                name="tenant",
                type_name="java.lang.String",
                annotations=['@RequestHeader("X-Tenant")'],
            ),
        ],
    )
    assert classification is not None
    assert classification.has_body_payload is True
    assert classification.query_param_names == ["q"]
    assert classification.header_names == ["X-Tenant"]
    assert classification.path_param_names == ["id"]
    assert classification.form_param_names == []


def test_feign_duplicate_header_names_are_deduped() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature="getOrder(java.lang.String,java.lang.String)",
        method_annotations=['@GetMapping("/orders")'],
        method_parameters=[
            make_callable_parameter(
                name="tenant",
                type_name="java.lang.String",
                annotations=['@RequestHeader("X-Tenant")'],
            ),
            make_callable_parameter(
                name="tenantAlias",
                type_name="java.lang.String",
                annotations=['@RequestHeader("X-Tenant")'],
            ),
        ],
    )
    assert classification is not None
    assert classification.header_names == ["X-Tenant"]


def test_http_exchange_projects_parameters_like_feign() -> None:
    # The HTTP Interface client binds parameters with the same Spring web
    # annotations, so projection works identically to Feign.
    classification, _ = _classify_declarative_call(
        class_annotations=["@HttpExchange"],
        method_signature="createOrder(com.example.Order,java.lang.String)",
        method_annotations=['@PostExchange("/orders")'],
        method_parameters=[
            make_callable_parameter(
                name="order",
                type_name="com.example.Order",
                annotations=["@RequestBody"],
            ),
            make_callable_parameter(
                name="tenant",
                type_name="java.lang.String",
                annotations=['@RequestHeader("X-Tenant")'],
            ),
        ],
        imports=_HTTP_EXCHANGE_IMPORTS,
    )
    assert classification is not None
    assert classification.framework == HttpDispatchFramework.HTTP_INTERFACE
    assert classification.has_body_payload is True
    assert classification.header_names == ["X-Tenant"]


# --------------------------------------------------------------------------- #
# Projected evidence exercises a mirror server endpoint in coverage
# --------------------------------------------------------------------------- #


def _interaction_from_classification(classification) -> HttpRequestInteraction:
    """Mirror the production HttpClassification -> HttpCallSite projection."""
    return HttpRequestInteraction(
        origin=OriginContext(
            phase=LifecyclePhase.TEST,
            kind=CallSiteOriginKind.TEST_METHOD,
        ),
        http_call=HttpCallSite(
            http_method=classification.http_method,
            path=classification.path,
            framework=classification.framework,
            request_role=classification.request_role or HttpRequestRole.EVENT,
            method_name="createOrder",
            header_names=list(classification.header_names),
            query_param_names=list(classification.query_param_names),
            path_param_names=list(classification.path_param_names),
            form_param_names=list(classification.form_param_names),
            has_body_payload=classification.has_body_payload,
        ),
        endpoint_candidate=EndpointCandidate(
            http_method=classification.http_method,
            path=classification.path,
            source="call-site",
        ),
    )


def test_feign_event_exercises_mirror_server_endpoint_parameters() -> None:
    classification, _ = _classify_declarative_call(
        class_annotations=['@FeignClient(name = "orders")'],
        method_signature=(
            "createOrder(java.lang.Long,com.example.Order,"
            "java.lang.String,java.lang.String)"
        ),
        method_annotations=['@PostMapping("/orders/{id}")'],
        method_parameters=[
            make_callable_parameter(
                name="id",
                type_name="java.lang.Long",
                annotations=['@PathVariable("id")'],
            ),
            make_callable_parameter(
                name="order",
                type_name="com.example.Order",
                annotations=["@RequestBody"],
            ),
            make_callable_parameter(
                name="status",
                type_name="java.lang.String",
                annotations=['@RequestParam("q")'],
            ),
            make_callable_parameter(
                name="tenant",
                type_name="java.lang.String",
                annotations=['@RequestHeader("X-Tenant")'],
            ),
        ],
    )
    assert classification is not None

    server_endpoint = ApplicationEndpoint(
        http_method="POST",
        path_template="/orders/{id}",
        framework="spring",
        declaring_class_name="example.OrderController",
        declaring_method_signature="createOrder(java.lang.Long,com.example.Order)",
        parameters=[
            EndpointParameter(
                name="id",
                type="java.lang.Long",
                source=EndpointParameterSource.PATH,
            ),
            EndpointParameter(
                name="order",
                type="com.example.Order",
                source=EndpointParameterSource.BODY,
            ),
            EndpointParameter(
                name="q",
                type="java.lang.String",
                source=EndpointParameterSource.QUERY,
            ),
            EndpointParameter(
                name="X-Tenant",
                type="java.lang.String",
                source=EndpointParameterSource.HEADER,
            ),
        ],
    )

    test_class = ModelClassAnalysis(
        qualified_class_name="example.OrderClientTest",
        test_method_analyses=[
            ModelMethodAnalysis(
                identity=MethodIdentity(
                    defining_class_name="example.OrderClientTest",
                    method_signature="testCreateOrder()",
                    method_declaration="void testCreateOrder()",
                ),
                is_api_test=True,
                http=HttpAnalysis(
                    request_interactions=[
                        _interaction_from_classification(classification)
                    ]
                ),
            )
        ],
    )

    summary = build_endpoint_parameter_coverage_summary([server_endpoint], [test_class])
    assert summary.total_endpoints_with_parameters == 1
    assert summary.fully_exercised_endpoint_count == 1
    entry = summary.endpoints[0]
    assert entry.exercised_parameter_count == 4
    assert entry.total_parameter_count == 4
