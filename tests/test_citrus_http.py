from __future__ import annotations

from gerbil.analysis.assertion.classification import (
    classify_assertions_on_runtime_view,
)
from gerbil.analysis.http.classification import classify_http_on_runtime_view
from gerbil.analysis.properties.assertion.status_distribution import (
    build_status_code_counts,
)
from gerbil.analysis.properties.request_dispatch import analyze_request_dispatch
from gerbil.analysis.properties.sequence_analysis import (
    build_http_verification_interactions,
)
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.schema import (
    AssertionRole,
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


def _send_chain(start_line: int) -> list:
    """http().client(httpClient).send().post("/orders").payload(body) — request."""
    column = 9
    return [
        make_call_site(
            method_name="http",
            receiver_expr="",
            start_line=start_line,
            start_column=column,
            end_column=column + 5,
        ),
        make_call_site(
            method_name="client",
            argument_expr=["httpClient"],
            start_line=start_line,
            start_column=column,
            end_column=column + 25,
        ),
        make_call_site(
            method_name="send",
            start_line=start_line,
            start_column=column,
            end_column=column + 32,
        ),
        make_call_site(
            method_name="post",
            argument_expr=['"/orders"'],
            start_line=start_line,
            start_column=column,
            end_column=column + 50,
        ),
        make_call_site(
            method_name="payload",
            argument_expr=['"{\\"id\\": 1}"'],
            start_line=start_line,
            start_column=column,
            end_column=column + 70,
        ),
    ]


def _receive_chain(start_line: int) -> list:
    """http().client(httpClient).receive().response(HttpStatus.OK)
    .message().body(...).header(...) — response validation."""
    column = 9
    return [
        make_call_site(
            method_name="http",
            receiver_expr="",
            start_line=start_line,
            start_column=column,
            end_column=column + 5,
        ),
        make_call_site(
            method_name="client",
            argument_expr=["httpClient"],
            start_line=start_line,
            start_column=column,
            end_column=column + 25,
        ),
        make_call_site(
            method_name="receive",
            start_line=start_line,
            start_column=column,
            end_column=column + 35,
        ),
        make_call_site(
            method_name="response",
            argument_expr=["HttpStatus.OK"],
            start_line=start_line,
            start_column=column,
            end_column=column + 55,
        ),
        make_call_site(
            method_name="message",
            start_line=start_line,
            start_column=column,
            end_column=column + 65,
        ),
        make_call_site(
            method_name="body",
            argument_expr=['"{\\"id\\": 1}"'],
            start_line=start_line,
            start_column=column,
            end_column=column + 85,
        ),
        make_call_site(
            method_name="header",
            argument_expr=['"Content-Type"', '"application/json"'],
            start_line=start_line,
            start_column=column,
            end_column=column + 110,
        ),
    ]


def _classified_runtime_view(call_sites: list) -> TestRuntimeView:
    method = make_callable(call_sites=call_sites)
    grouping = build_call_site_grouping(method.call_sites)
    owner = MethodRef(
        defining_class_name="example.CitrusOrderTest",
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
    classify_http_on_runtime_view(runtime_view=runtime_view, receiver_resolver=resolver)
    classify_assertions_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=resolver,
    )
    return runtime_view


def _http_by_method(runtime_view: TestRuntimeView) -> dict:
    result: dict = {}
    for entry in runtime_view.entries:
        for node in entry.grouping.nodes:
            if node.http_classification is not None:
                result[node.call_site.method_name] = node.http_classification
    return result


def test_citrus_send_chain_yields_request_event() -> None:
    runtime_view = _classified_runtime_view(_send_chain(start_line=5))
    http = _http_by_method(runtime_view)

    assert "post" in http
    event = http["post"]
    assert event.framework == HttpDispatchFramework.CITRUS
    assert event.request_role == HttpRequestRole.EVENT
    assert event.http_method == "POST"
    assert event.path == "/orders"
    assert event.has_body_payload is True


def test_citrus_receive_chain_yields_response_roles() -> None:
    runtime_view = _classified_runtime_view(_receive_chain(start_line=5))
    http = _http_by_method(runtime_view)

    assert http["response"].response_role == HttpResponseRole.STATUS_ASSERTION
    assert http["body"].response_role == HttpResponseRole.BODY_ASSERTION
    assert http["header"].response_role == HttpResponseRole.HEADER_ASSERTION
    assert http["response"].framework == HttpDispatchFramework.CITRUS


def test_citrus_response_status_code_is_extracted() -> None:
    runtime_view = _classified_runtime_view(_receive_chain(start_line=5))
    counts = build_status_code_counts(runtime_view=runtime_view)
    assert counts == {"200": 1}


def test_citrus_response_roles_become_verification_interactions() -> None:
    runtime_view = _classified_runtime_view(_receive_chain(start_line=5))
    interactions = build_http_verification_interactions(runtime_view)
    roles = {interaction.assertion_role for interaction in interactions}
    assert AssertionRole.STATUS in roles
    assert AssertionRole.BODY in roles
    assert AssertionRole.HEADER in roles
    assert all(
        interaction.framework == HttpDispatchFramework.CITRUS
        for interaction in interactions
    )


def test_citrus_full_test_is_dispatch_classified() -> None:
    runtime_view = _classified_runtime_view(
        [*_send_chain(start_line=5), *_receive_chain(start_line=7)]
    )
    decision = analyze_request_dispatch(runtime_view=runtime_view)
    # "/orders" is a local path → local-network dispatch.
    assert decision.labels == ["local-network"]


def test_citrus_get_verb_path_on_argument() -> None:
    runtime_view = _classified_runtime_view(
        [
            make_call_site(
                method_name="http",
                receiver_expr="",
                start_line=5,
                start_column=9,
                end_column=14,
            ),
            make_call_site(
                method_name="client",
                argument_expr=["httpClient"],
                start_line=5,
                start_column=9,
                end_column=30,
            ),
            make_call_site(
                method_name="send",
                start_line=5,
                start_column=9,
                end_column=37,
            ),
            make_call_site(
                method_name="get",
                argument_expr=['"/orders/1"'],
                start_line=5,
                start_column=9,
                end_column=55,
            ),
        ]
    )
    http = _http_by_method(runtime_view)
    assert http["get"].http_method == "GET"
    assert http["get"].path == "/orders/1"
    assert http["get"].request_role == HttpRequestRole.EVENT


def test_citrus_single_segment_path_builder_supplies_event_path() -> None:
    # http().client(httpClient).send().post().path("orders") — path() fixes its
    # argument as the request path, so a bare segment is kept.
    runtime_view = _classified_runtime_view(
        [
            make_call_site(
                method_name="http",
                receiver_expr="",
                start_line=5,
                start_column=9,
                end_column=14,
            ),
            make_call_site(
                method_name="client",
                argument_expr=["httpClient"],
                start_line=5,
                start_column=9,
                end_column=30,
            ),
            make_call_site(
                method_name="send",
                start_line=5,
                start_column=9,
                end_column=37,
            ),
            make_call_site(
                method_name="post",
                start_line=5,
                start_column=9,
                end_column=44,
            ),
            make_call_site(
                method_name="path",
                argument_expr=['"orders"'],
                start_line=5,
                start_column=9,
                end_column=60,
            ),
        ]
    )
    http = _http_by_method(runtime_view)
    assert http["post"].http_method == "POST"
    assert http["post"].path == "/orders"
    assert http["post"].request_role == HttpRequestRole.EVENT


def test_citrus_uri_bare_host_token_is_not_a_path() -> None:
    # uri() takes a full URI whose bare token is usually a host, so a
    # separator-less literal must not become a path.
    runtime_view = _classified_runtime_view(
        [
            make_call_site(
                method_name="http",
                receiver_expr="",
                start_line=5,
                start_column=9,
                end_column=14,
            ),
            make_call_site(
                method_name="client",
                argument_expr=["httpClient"],
                start_line=5,
                start_column=9,
                end_column=30,
            ),
            make_call_site(
                method_name="send",
                start_line=5,
                start_column=9,
                end_column=37,
            ),
            make_call_site(
                method_name="get",
                start_line=5,
                start_column=9,
                end_column=44,
            ),
            make_call_site(
                method_name="uri",
                argument_expr=['"localhost"'],
                start_line=5,
                start_column=9,
                end_column=60,
            ),
        ]
    )
    http = _http_by_method(runtime_view)
    assert http["get"].http_method == "GET"
    assert http["get"].path == ""


def test_non_citrus_http_chain_is_not_misclassified() -> None:
    # http().get("/x") with no client() — not a Citrus HTTP action chain.
    runtime_view = _classified_runtime_view(
        [
            make_call_site(
                method_name="http",
                receiver_expr="",
                start_line=5,
                start_column=9,
                end_column=14,
            ),
            make_call_site(
                method_name="get",
                argument_expr=['"/x"'],
                start_line=5,
                start_column=9,
                end_column=30,
            ),
        ]
    )
    http = _http_by_method(runtime_view)
    assert "get" not in http
    assert "http" not in http


def test_citrus_send_without_verb_uses_send_event() -> None:
    # http().client(ep).send().message().body(...) — verb expressed via message.
    runtime_view = _classified_runtime_view(
        [
            make_call_site(
                method_name="http",
                receiver_expr="",
                start_line=5,
                start_column=9,
                end_column=14,
            ),
            make_call_site(
                method_name="client",
                argument_expr=["httpClient"],
                start_line=5,
                start_column=9,
                end_column=30,
            ),
            make_call_site(
                method_name="send",
                start_line=5,
                start_column=9,
                end_column=37,
            ),
            make_call_site(
                method_name="message",
                start_line=5,
                start_column=9,
                end_column=50,
            ),
        ]
    )
    http = _http_by_method(runtime_view)
    assert http["send"].request_role == HttpRequestRole.EVENT
    assert http["send"].framework == HttpDispatchFramework.CITRUS
    assert http["send"].http_method == "UNKNOWN"


def test_citrus_send_chain_collects_query_params_and_headers() -> None:
    runtime_view = _classified_runtime_view(
        [
            make_call_site(
                method_name="http",
                receiver_expr="",
                start_line=5,
                start_column=9,
                end_column=14,
            ),
            make_call_site(
                method_name="client",
                argument_expr=["httpClient"],
                start_line=5,
                start_column=9,
                end_column=30,
            ),
            make_call_site(
                method_name="send",
                start_line=5,
                start_column=9,
                end_column=37,
            ),
            make_call_site(
                method_name="get",
                argument_expr=['"/orders"'],
                start_line=5,
                start_column=9,
                end_column=55,
            ),
            make_call_site(
                method_name="queryParam",
                argument_expr=['"status"', '"open"'],
                start_line=5,
                start_column=9,
                end_column=80,
            ),
            make_call_site(
                method_name="header",
                argument_expr=['"X-Token"', '"token"'],
                start_line=5,
                start_column=9,
                end_column=105,
            ),
            make_call_site(
                method_name="contentType",
                argument_expr=['"application/json"'],
                start_line=5,
                start_column=9,
                end_column=130,
            ),
        ]
    )
    http = _http_by_method(runtime_view)
    event = http["get"]
    assert event.http_method == "GET"
    assert event.path == "/orders"
    assert event.query_param_names == ["status"]
    assert event.header_names == ["x-token", "content-type"]
