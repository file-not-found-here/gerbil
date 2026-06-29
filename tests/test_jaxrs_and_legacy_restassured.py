"""Classification coverage for the JAX-RS client family and the legacy
``com.jayway.restassured`` (RestAssured 2.x) package additions to the registry.

Receiver types use the fully-qualified forms that project-mode CLDK emits;
source-only snippets do not resolve library types, so synthetic call sites are
the correct test vehicle here.
"""

from __future__ import annotations

from cldk.models.java import JImport

from gerbil.analysis.http.framework_registry import (
    classify_owner_family,
    resolve_http_owner_family,
)
from gerbil.analysis.http.classification import classify_http_on_grouping
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.schema import (
    HttpDispatchFramework,
    HttpRequestRole,
    HttpResponseRole,
    LifecyclePhase,
)
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
)


def _classify(method, static_import_index: StaticImportIndex | None = None):
    """Classify a method's call sites and return {method_name: HttpClassification}."""
    grouping = build_call_site_grouping(method.call_sites)
    owner = MethodRef(
        defining_class_name="example.ApiTest",
        method_signature=method.signature,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=grouping,
                method_details=method,
            )
        ]
    )
    resolver = build_runtime_receiver_resolver_for_testing(
        runtime_view,
        get_static_import_index_for_class=(
            lambda _class_name: static_import_index or StaticImportIndex.EMPTY
        ),
    )
    classify_http_on_grouping(
        grouping=grouping, owner=owner, receiver_resolver=resolver
    )
    return {
        node.call_site.method_name: node.http_classification
        for node in grouping.nodes
        if node.http_classification is not None
    }


# --------------------------------------------------------------------------- #
# JAX-RS client family
# --------------------------------------------------------------------------- #


def _jaxrs_fluent_chain(namespace: str):
    """client.target(uri).path(p).request(JSON).get() — one fluent statement."""
    return make_callable(
        call_sites=[
            make_call_site(
                method_name="target",
                receiver_type=f"{namespace}.ws.rs.client.Client",
                receiver_expr="client",
                argument_expr=['"http://localhost:8080"'],
                start_line=4,
                start_column=9,
                end_line=4,
                end_column=40,
            ),
            make_call_site(
                method_name="path",
                receiver_type=f"{namespace}.ws.rs.client.WebTarget",
                argument_expr=['"/users"'],
                start_line=4,
                start_column=9,
                end_line=4,
                end_column=55,
            ),
            make_call_site(
                method_name="request",
                receiver_type=f"{namespace}.ws.rs.client.WebTarget",
                argument_expr=["MediaType.APPLICATION_JSON"],
                start_line=4,
                start_column=9,
                end_line=4,
                end_column=80,
            ),
            make_call_site(
                method_name="get",
                receiver_type=f"{namespace}.ws.rs.client.Invocation.Builder",
                start_line=4,
                start_column=9,
                end_line=4,
                end_column=86,
            ),
        ]
    )


def test_jaxrs_fluent_get_event_is_classified() -> None:
    classified = _classify(_jaxrs_fluent_chain("javax"))

    get = classified["get"]
    assert get.request_role == HttpRequestRole.EVENT
    assert get.http_method == "GET"
    assert get.framework == HttpDispatchFramework.JAX_RS

    # The chain links are builders, not spurious events.
    assert classified["target"].request_role == HttpRequestRole.BUILDER
    assert classified["request"].request_role == HttpRequestRole.BUILDER


def test_jaxrs_jakarta_namespace_is_classified() -> None:
    classified = _classify(_jaxrs_fluent_chain("jakarta"))
    get = classified["get"]
    assert get.request_role == HttpRequestRole.EVENT
    assert get.http_method == "GET"
    assert get.framework == HttpDispatchFramework.JAX_RS


def test_jaxrs_method_string_argument_resolves_verb() -> None:
    """`.method("POST")` carries its verb as a string-literal argument."""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="method",
                receiver_type="javax.ws.rs.client.Invocation.Builder",
                receiver_expr="builder",
                argument_expr=['"POST"', "Entity.json(body)"],
                start_line=3,
                start_column=9,
                end_line=3,
                end_column=45,
            ),
        ]
    )
    classified = _classify(method)
    event = classified["method"]
    assert event.request_role == HttpRequestRole.EVENT
    assert event.http_method == "POST"
    assert event.framework == HttpDispatchFramework.JAX_RS
    # The second argument is an Entity factory, so the request carries a body.
    assert event.has_body_payload is True


def _jaxrs_invocation_call(method_name: str, argument_expr: list[str]):
    return make_callable(
        call_sites=[
            make_call_site(
                method_name=method_name,
                receiver_type="javax.ws.rs.client.Invocation.Builder",
                receiver_expr="builder",
                argument_expr=argument_expr,
                start_line=3,
                start_column=9,
                end_line=3,
                end_column=45,
            ),
        ]
    )


def test_jaxrs_post_entity_has_body() -> None:
    classified = _classify(_jaxrs_invocation_call("post", ["Entity.json(dto)"]))
    event = classified["post"]
    assert event.http_method == "POST"
    assert event.has_body_payload is True


def test_jaxrs_post_prebuilt_entity_variable_has_body() -> None:
    # post/put have only entity-bearing overloads, so a pre-built entity counts too.
    classified = _classify(_jaxrs_invocation_call("post", ["entity"]))
    assert classified["post"].has_body_payload is True


def test_jaxrs_method_with_response_type_class_has_no_body() -> None:
    # `.method("GET", String.class)` is the response-type overload, not a body.
    classified = _classify(_jaxrs_invocation_call("method", ['"GET"', "String.class"]))
    event = classified["method"]
    assert event.http_method == "GET"
    assert event.has_body_payload is False


def test_jaxrs_get_without_entity_has_no_body() -> None:
    classified = _classify(_jaxrs_invocation_call("get", []))
    assert classified["get"].has_body_payload is False


def test_jaxrs_split_variable_get_event_is_classified() -> None:
    """Builder stored in a local: Response r = builder.get();"""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="get",
                receiver_type="javax.ws.rs.client.Invocation.Builder",
                receiver_expr="builder",
                start_line=5,
                start_column=20,
                end_line=5,
                end_column=32,
            ),
        ]
    )
    classified = _classify(method)
    assert classified["get"].request_role == HttpRequestRole.EVENT
    assert classified["get"].http_method == "GET"


def test_jaxrs_core_response_is_not_classified_as_request() -> None:
    """javax.ws.rs.core.Response is the response side — must not match the client family."""
    rule = resolve_http_owner_family("javax.ws.rs.core.Response", "getStatus")
    assert rule is None


def test_jaxrs_event_and_builder_roles_at_registry_level() -> None:
    for namespace in ("javax", "jakarta"):
        builder_recv = f"{namespace}.ws.rs.client.Invocation.Builder"
        for method_name, verb in (
            ("get", "GET"),
            ("post", "POST"),
            ("put", "PUT"),
            ("delete", "DELETE"),
            ("head", "HEAD"),
            ("options", "OPTIONS"),
            ("trace", "TRACE"),
        ):
            rule = resolve_http_owner_family(builder_recv, method_name)
            assert rule is not None and rule.family_id == "jaxrs-client.request"
            req, _resp, http_method = classify_owner_family(
                rule, receiver_type=builder_recv, method_name=method_name
            )
            assert req == HttpRequestRole.EVENT
            assert http_method == verb

        target_recv = f"{namespace}.ws.rs.client.WebTarget"
        rule = resolve_http_owner_family(target_recv, "request")
        assert rule is not None
        req, _resp, _ = classify_owner_family(
            rule, receiver_type=target_recv, method_name="request"
        )
        assert req == HttpRequestRole.BUILDER


# --------------------------------------------------------------------------- #
# Legacy com.jayway.restassured (RestAssured 2.x)
# --------------------------------------------------------------------------- #


def test_legacy_jayway_request_spec_get_is_event() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="get",
                receiver_type="com.jayway.restassured.specification.RequestSpecification",
                receiver_expr="spec",
                argument_expr=['"/products"'],
                start_line=2,
                start_column=9,
                end_line=2,
                end_column=40,
            ),
        ]
    )
    classified = _classify(method)
    event = classified["get"]
    assert event.request_role == HttpRequestRole.EVENT
    assert event.http_method == "GET"
    assert event.framework == HttpDispatchFramework.REST_ASSURED


def test_legacy_jayway_static_import_resolves_to_request_factory() -> None:
    """features-service pattern: `import static com.jayway.restassured.RestAssured.when;`"""
    index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="com.jayway.restassured.RestAssured.when",
                is_static=True,
                is_wildcard=False,
            )
        ]
    )
    resolved = index.resolve("when")
    assert resolved == "com.jayway.restassured.RestAssured"

    rule = resolve_http_owner_family(resolved, "when")
    assert rule is not None
    assert rule.family_id == "rest-assured.request_factory"
    assert rule.framework == HttpDispatchFramework.REST_ASSURED


def test_legacy_jayway_validatable_response_status_assertion() -> None:
    rule = resolve_http_owner_family(
        "com.jayway.restassured.response.ValidatableResponse", "statusCode"
    )
    assert rule is not None
    _req, resp, _ = classify_owner_family(
        rule,
        receiver_type="com.jayway.restassured.response.ValidatableResponse",
        method_name="statusCode",
    )
    assert resp == HttpResponseRole.STATUS_ASSERTION


def test_modern_io_restassured_still_classified() -> None:
    """Regression: adding the legacy root must not disturb io.restassured."""
    rule = resolve_http_owner_family(
        "io.restassured.specification.RequestSpecification", "get"
    )
    assert rule is not None
    req, _resp, http_method = classify_owner_family(
        rule,
        receiver_type="io.restassured.specification.RequestSpecification",
        method_name="get",
    )
    assert req == HttpRequestRole.EVENT
    assert http_method == "GET"
    assert rule.framework == HttpDispatchFramework.REST_ASSURED
