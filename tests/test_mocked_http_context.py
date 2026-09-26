from __future__ import annotations

from gerbil.analysis.http.classification import (
    build_output_http_mocked_interactions,
    build_output_http_request_interactions,
    classify_http_on_runtime_view,
)
from gerbil.analysis.properties.request_dispatch import analyze_request_dispatch
from gerbil.analysis.properties.sequence_analysis import build_api_call_sequence
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import (
    CallSiteNode,
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.schema import (
    HttpDispatchFramework,
    HttpRequestInteraction,
    HttpRequestRole,
    LifecyclePhase,
    MockingContextKind,
)
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
)


def _runtime_view_for_call_sites(call_sites) -> TestRuntimeView:
    owner = MethodRef(
        defining_class_name="example.HttpClientTest",
        method_signature="testMethod()",
    )
    method = make_callable(call_sites=list(call_sites))
    return TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )


def _classify(runtime_view: TestRuntimeView) -> None:
    classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )


def _test_nodes(runtime_view: TestRuntimeView) -> list[CallSiteNode]:
    test_entry = runtime_view.test_entry()
    assert test_entry is not None
    return test_entry.grouping.nodes


def _http_call_method_names(
    request_interactions: list[HttpRequestInteraction],
) -> list[str]:
    method_names: list[str] = []
    for interaction in request_interactions:
        assert interaction.http_call is not None
        method_names.append(interaction.http_call.method_name)
    return method_names


def test_mockito_when_suppresses_apache_httpclient_dispatch_event() -> None:
    runtime_view = _runtime_view_for_call_sites(
        [
            make_call_site(
                method_name="when",
                receiver_type="org.mockito.Mockito",
                callee_signature="org.mockito.Mockito.when",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=60,
            ),
            make_call_site(
                method_name="execute",
                receiver_type="org.apache.http.client.HttpClient",
                argument_expr=["any(HttpPost.class)"],
                argument_types=["org.apache.http.client.methods.HttpPost"],
                start_line=10,
                start_column=6,
                end_line=10,
                end_column=45,
            ),
        ]
    )

    _classify(runtime_view)

    execute_node = next(
        node
        for node in _test_nodes(runtime_view)
        if node.call_site.method_name == "execute"
    )
    classification = execute_node.http_classification

    assert classification is not None
    assert classification.framework == HttpDispatchFramework.APACHE_HTTPCLIENT
    assert classification.http_method == "POST"
    assert classification.request_role is None
    assert classification.response_role is None
    assert classification.mocking_context is not None
    assert classification.mocking_context.kind == MockingContextKind.STUBBING
    assert classification.mocking_context.wrapper_method == "when"
    assert execute_node.endpoint_candidate is None
    assert build_output_http_request_interactions(runtime_view) == []
    assert build_api_call_sequence(runtime_view) == []
    assert analyze_request_dispatch(runtime_view).signals == {"unknown": ["no-events"]}

    mocked = build_output_http_mocked_interactions(runtime_view)
    assert len(mocked) == 1
    assert mocked[0].http_call.method_name == "execute"
    assert mocked[0].http_call.mocking_context.kind == MockingContextKind.STUBBING


def test_mockito_verify_suppresses_webclient_exchange_dispatch_event() -> None:
    runtime_view = _runtime_view_for_call_sites(
        [
            make_call_site(
                method_name="exchange",
                receiver_type=(
                    "org.springframework.web.reactive.function.client.ExchangeFunction"
                ),
                argument_expr=["requestCaptor.capture()"],
                start_line=20,
                start_column=1,
                end_line=20,
                end_column=52,
            ),
            make_call_site(
                method_name="verify",
                receiver_type="org.mockito.Mockito",
                callee_signature="org.mockito.Mockito.verify",
                start_line=20,
                start_column=1,
                end_line=20,
                end_column=24,
            ),
        ]
    )

    _classify(runtime_view)

    exchange_node = next(
        node
        for node in _test_nodes(runtime_view)
        if node.call_site.method_name == "exchange"
    )
    classification = exchange_node.http_classification

    assert classification is not None
    assert classification.framework == HttpDispatchFramework.WEBCLIENT
    assert classification.request_role is None
    assert classification.response_role is None
    assert classification.mocking_context is not None
    assert classification.mocking_context.kind == MockingContextKind.VERIFICATION
    assert classification.mocking_context.wrapper_method == "verify"
    assert build_output_http_request_interactions(runtime_view) == []

    mocked = build_output_http_mocked_interactions(runtime_view)
    assert len(mocked) == 1
    assert mocked[0].http_call.method_name == "exchange"
    assert mocked[0].http_call.mocking_context.kind == MockingContextKind.VERIFICATION


def test_mockito_do_return_value_http_call_remains_request_event() -> None:
    runtime_view = _runtime_view_for_call_sites(
        [
            make_call_site(
                method_name="execute",
                receiver_type="org.apache.http.client.HttpClient",
                argument_expr=["any(HttpPost.class)"],
                argument_types=["org.apache.http.client.methods.HttpPost"],
                start_line=30,
                start_column=1,
                end_line=30,
                end_column=68,
            ),
            make_call_site(
                method_name="when",
                receiver_type="org.mockito.stubbing.Stubber",
                callee_signature="org.mockito.stubbing.Stubber.when",
                start_line=30,
                start_column=1,
                end_line=30,
                end_column=48,
            ),
            make_call_site(
                method_name="doReturn",
                receiver_type="org.mockito.Mockito",
                callee_signature="org.mockito.Mockito.doReturn",
                start_line=30,
                start_column=1,
                end_line=30,
                end_column=36,
            ),
            make_call_site(
                method_name="send",
                receiver_type="java.net.http.HttpClient",
                argument_expr=["req"],
                start_line=30,
                start_column=10,
                end_line=30,
                end_column=32,
            ),
        ]
    )

    _classify(runtime_view)

    nodes_by_method = {
        node.call_site.method_name: node for node in _test_nodes(runtime_view)
    }
    send_classification = nodes_by_method["send"].http_classification
    execute_classification = nodes_by_method["execute"].http_classification

    assert send_classification is not None
    assert send_classification.request_role == HttpRequestRole.EVENT
    assert send_classification.mocking_context is None
    assert execute_classification is not None
    assert execute_classification.request_role is None
    assert execute_classification.mocking_context is not None
    assert execute_classification.mocking_context.wrapper_method == "when"

    request_interactions = build_output_http_request_interactions(runtime_view)
    assert _http_call_method_names(request_interactions) == ["send"]
    mocked = build_output_http_mocked_interactions(runtime_view)
    assert [item.http_call.method_name for item in mocked] == ["execute"]


def test_mockito_when_nested_http_argument_remains_request_event() -> None:
    runtime_view = _runtime_view_for_call_sites(
        [
            make_call_site(
                method_name="when",
                receiver_type="org.mockito.Mockito",
                callee_signature="org.mockito.Mockito.when",
                start_line=40,
                start_column=1,
                end_line=40,
                end_column=42,
            ),
            make_call_site(
                method_name="call",
                receiver_type="example.Service",
                start_line=40,
                start_column=6,
                end_line=40,
                end_column=41,
            ),
            make_call_site(
                method_name="send",
                receiver_type="java.net.http.HttpClient",
                argument_expr=["req"],
                start_line=40,
                start_column=16,
                end_line=40,
                end_column=36,
            ),
        ]
    )

    _classify(runtime_view)

    send_node = next(
        node
        for node in _test_nodes(runtime_view)
        if node.call_site.method_name == "send"
    )
    classification = send_node.http_classification

    assert classification is not None
    assert classification.request_role == HttpRequestRole.EVENT
    assert classification.mocking_context is None
    assert _http_call_method_names(
        build_output_http_request_interactions(runtime_view)
    ) == ["send"]
    assert build_output_http_mocked_interactions(runtime_view) == []


def test_non_mocked_http_call_remains_request_event() -> None:
    runtime_view = _runtime_view_for_call_sites(
        [
            make_call_site(
                method_name="execute",
                receiver_type="org.apache.http.client.HttpClient",
                argument_expr=['new HttpGet("/api")'],
                argument_types=["org.apache.http.client.methods.HttpGet"],
                start_line=50,
                start_column=1,
                end_line=50,
                end_column=36,
            ),
        ]
    )

    _classify(runtime_view)

    execute_node = _test_nodes(runtime_view)[0]
    classification = execute_node.http_classification

    assert classification is not None
    assert classification.request_role == HttpRequestRole.EVENT
    assert classification.mocking_context is None
    assert len(build_output_http_request_interactions(runtime_view)) == 1
    assert build_output_http_mocked_interactions(runtime_view) == []
