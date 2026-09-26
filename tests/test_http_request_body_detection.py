"""Body-payload detection for request shapes that encode the body in arguments rather
than a body-named setter: RestTemplate ``exchange``, Java HttpClient ``method``/``POST``.

Receiver types use the fully-qualified forms project-mode CLDK emits, so synthetic call
sites are the correct test vehicle here.
"""

from __future__ import annotations

from gerbil.analysis.http.classification import classify_http_on_grouping
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.schema import HttpRequestRole, LifecyclePhase
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
)

_REST_TEMPLATE = "org.springframework.web.client.RestTemplate"
_TEST_REST_TEMPLATE = "org.springframework.boot.test.web.client.TestRestTemplate"
_JAVA_HTTPCLIENT_BUILDER = "java.net.http.HttpRequest.Builder"


def _classify(method):
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
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )
    classify_http_on_grouping(
        grouping=grouping, owner=owner, receiver_resolver=resolver
    )
    return {
        node.call_site.method_name: node.http_classification
        for node in grouping.nodes
        if node.http_classification is not None
    }


def _single_call(method_name: str, receiver_type: str, argument_expr: list[str]):
    return make_callable(
        call_sites=[
            make_call_site(
                method_name=method_name,
                receiver_type=receiver_type,
                receiver_expr="restTemplate",
                argument_expr=argument_expr,
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=40,
            )
        ]
    )


# --------------------------------------------------------------------------- #
# RestTemplate.exchange
# --------------------------------------------------------------------------- #


def test_resttemplate_exchange_post_with_entity_has_body() -> None:
    classified = _classify(
        _single_call(
            "exchange",
            _REST_TEMPLATE,
            ["url", "HttpMethod.POST", "new HttpEntity<>(dto)", "String.class"],
        )
    )
    event = classified["exchange"]
    assert event.request_role == HttpRequestRole.EVENT
    assert event.http_method == "POST"
    assert event.has_body_payload is True


def test_test_rest_template_exchange_put_with_entity_has_body() -> None:
    classified = _classify(
        _single_call(
            "exchange",
            _TEST_REST_TEMPLATE,
            ["url", "HttpMethod.PUT", "new HttpEntity<>(dto, headers)", "Void.class"],
        )
    )
    event = classified["exchange"]
    assert event.http_method == "PUT"
    assert event.has_body_payload is True


def test_resttemplate_exchange_get_with_headers_entity_has_no_body() -> None:
    # A GET that passes a headers-only entity carries no body; the verb gate excludes it.
    classified = _classify(
        _single_call(
            "exchange",
            _REST_TEMPLATE,
            ["url", "HttpMethod.GET", "new HttpEntity<>(headers)", "String.class"],
        )
    )
    event = classified["exchange"]
    assert event.http_method == "GET"
    assert event.has_body_payload is False


def test_resttemplate_exchange_post_with_empty_entity_has_no_body() -> None:
    classified = _classify(
        _single_call(
            "exchange",
            _REST_TEMPLATE,
            ["url", "HttpMethod.POST", "HttpEntity.EMPTY", "String.class"],
        )
    )
    assert classified["exchange"].has_body_payload is False


def test_resttemplate_exchange_post_with_null_entity_has_no_body() -> None:
    classified = _classify(
        _single_call(
            "exchange",
            _REST_TEMPLATE,
            ["url", "HttpMethod.POST", "null", "String.class"],
        )
    )
    assert classified["exchange"].has_body_payload is False


def test_resttemplate_exchange_post_with_null_body_headers_entity_has_no_body() -> None:
    # `new HttpEntity<>(null, headers)` is the headers-only-no-body idiom: the explicit
    # null body slot carries no payload even though a headers argument follows.
    classified = _classify(
        _single_call(
            "exchange",
            _REST_TEMPLATE,
            ["url", "HttpMethod.POST", "new HttpEntity<>(null, headers)", "Void.class"],
        )
    )
    assert classified["exchange"].has_body_payload is False


def test_resttemplate_exchange_post_with_null_body_constructor_has_no_body() -> None:
    classified = _classify(
        _single_call(
            "exchange",
            _REST_TEMPLATE,
            ["url", "HttpMethod.POST", "new HttpEntity<>(null)", "String.class"],
        )
    )
    assert classified["exchange"].has_body_payload is False


def test_resttemplate_exchange_unresolved_verb_has_no_body() -> None:
    # A variable verb cannot be confirmed body-capable, so no body is recorded.
    classified = _classify(
        _single_call(
            "exchange",
            _REST_TEMPLATE,
            ["url", "method", "new HttpEntity<>(dto)", "String.class"],
        )
    )
    event = classified["exchange"]
    assert event.http_method == "UNKNOWN"
    assert event.has_body_payload is False


def test_resttemplate_exchange_accessor_get_call_is_not_a_verb_literal() -> None:
    # `verbs.get(i)` is a collection accessor, not the uppercase HttpMethod.GET
    # constant; the verb stays unresolved.
    classified = _classify(
        _single_call(
            "exchange",
            _REST_TEMPLATE,
            ["url", "verbs.get(i)", "new HttpEntity<>(dto)", "String.class"],
        )
    )
    event = classified["exchange"]
    assert event.http_method == "UNKNOWN"
    assert event.has_body_payload is False


def test_resttemplate_exchange_qualified_verb_constant_extracts_verb() -> None:
    classified = _classify(
        _single_call(
            "exchange",
            _REST_TEMPLATE,
            ["url", "HttpMethod.DELETE", "null", "Void.class"],
        )
    )
    assert classified["exchange"].http_method == "DELETE"


# --------------------------------------------------------------------------- #
# RestTemplate.postForLocation
# --------------------------------------------------------------------------- #


def test_resttemplate_post_for_location_with_request_has_body() -> None:
    classified = _classify(
        _single_call(
            "postForLocation",
            _REST_TEMPLATE,
            ["url", "request"],
        )
    )
    event = classified["postForLocation"]
    assert event.request_role == HttpRequestRole.EVENT
    assert event.http_method == "POST"
    assert event.has_body_payload is True


def test_test_rest_template_post_for_location_with_request_has_body() -> None:
    classified = _classify(
        _single_call(
            "postForLocation",
            _TEST_REST_TEMPLATE,
            ["url", "request"],
        )
    )
    assert classified["postForLocation"].has_body_payload is True


# --------------------------------------------------------------------------- #
# Java HttpClient builder
# --------------------------------------------------------------------------- #


def test_java_httpclient_method_with_body_publisher_has_body() -> None:
    classified = _classify(
        _single_call(
            "method",
            _JAVA_HTTPCLIENT_BUILDER,
            ['"POST"', "BodyPublishers.ofString(body)"],
        )
    )
    builder = classified["method"]
    assert builder.request_role == HttpRequestRole.BUILDER
    assert builder.http_method == "POST"
    assert builder.has_body_payload is True


def test_java_httpclient_method_with_no_body_publisher_has_no_body() -> None:
    classified = _classify(
        _single_call(
            "method",
            _JAVA_HTTPCLIENT_BUILDER,
            ['"POST"', "BodyPublishers.noBody()"],
        )
    )
    assert classified["method"].has_body_payload is False


def test_java_httpclient_post_with_body_publisher_has_body() -> None:
    classified = _classify(
        _single_call(
            "POST",
            _JAVA_HTTPCLIENT_BUILDER,
            ["BodyPublishers.ofString(body)"],
        )
    )
    assert classified["POST"].has_body_payload is True


def test_java_httpclient_post_with_no_body_publisher_has_no_body() -> None:
    classified = _classify(
        _single_call(
            "POST",
            _JAVA_HTTPCLIENT_BUILDER,
            ["BodyPublishers.noBody()"],
        )
    )
    assert classified["POST"].has_body_payload is False
