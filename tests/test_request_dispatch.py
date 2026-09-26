from __future__ import annotations

import pytest

from gerbil.analysis.runtime.call_sites import (
    HelperExpansion,
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.schema import (
    HttpRequestRole,
    LifecyclePhase,
)
from gerbil.analysis.properties.request_dispatch import (
    analyze_request_dispatch,
    classify_request_dispatch,
)
from tests.cldk_factories import (
    annotate_node_http,
    make_call_site,
    make_callable,
)


def _make_runtime_view(
    *,
    paths: list[str],
    http_methods: list[str] | None = None,
    frameworks: list[str] | None = None,
    receiver_types: list[str] | None = None,
    request_roles: list[HttpRequestRole] | None = None,
    extra_entries: list[PhaseEntry] | None = None,
) -> TestRuntimeView:
    count = len(paths)
    resolved_http_methods = http_methods or ["GET"] * count
    resolved_frameworks = frameworks or ["unknown"] * count
    resolved_receiver_types = receiver_types or [""] * count
    resolved_request_roles = request_roles or [HttpRequestRole.EVENT] * count

    call_sites = [
        make_call_site(method_name="call", start_line=i + 1) for i in range(count)
    ]
    method = make_callable(call_sites=call_sites)
    grouping = build_call_site_grouping(list(method.call_sites))
    nodes = list(grouping.nodes)

    for i, node in enumerate(nodes):
        annotate_node_http(
            node,
            http_method=resolved_http_methods[i],
            path=paths[i],
            framework=resolved_frameworks[i],
            receiver_type=resolved_receiver_types[i],
            request_role=resolved_request_roles[i],
        )

    entries = [
        PhaseEntry(
            phase=LifecyclePhase.TEST,
            method_ref=MethodRef(
                defining_class_name="example.Test",
                method_signature="testMethod()",
            ),
            context_class_name="example.Test",
            grouping=grouping,
            method_details=method,
        )
    ]
    if extra_entries:
        entries.extend(extra_entries)

    return TestRuntimeView(entries=entries)


def _make_entry_with_method(
    method_name: str,
    start_line: int = 1,
) -> PhaseEntry:
    call_sites = [make_call_site(method_name=method_name, start_line=start_line)]
    method = make_callable(call_sites=call_sites)
    grouping = build_call_site_grouping(list(method.call_sites))
    return PhaseEntry(
        phase=LifecyclePhase.SETUP,
        method_ref=MethodRef(
            defining_class_name="example.Test",
            method_signature="setup()",
        ),
        context_class_name="example.Test",
        grouping=grouping,
        method_details=method,
    )


def test_no_runtime_returns_unknown() -> None:
    result = analyze_request_dispatch(runtime_view=None)
    assert result.labels == ["unknown"]
    assert result.signals == {"unknown": ["no-runtime"]}


def test_empty_runtime_view_returns_unknown() -> None:
    method = make_callable(call_sites=[])
    grouping = build_call_site_grouping([])
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testMethod()",
                ),
                context_class_name="example.Test",
                grouping=grouping,
                method_details=method,
            )
        ]
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["unknown"]
    assert result.signals == {"unknown": ["no-events"]}


def test_mockmvc_always_in_process() -> None:
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=["mockmvc"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["in-process"]
    assert result.signals == {"in-process": ["mockmvc-in-process"]}


def test_mockmvc_unresolved_path_still_in_process() -> None:
    runtime_view = _make_runtime_view(
        paths=[""],
        frameworks=["mockmvc"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["in-process"]
    assert result.signals == {"in-process": ["mockmvc-in-process"]}


def test_webtestclient_default_in_process() -> None:
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=["webtestclient"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["in-process"]
    assert result.signals == {"in-process": ["webtestclient-mock-mode"]}


def test_webtestclient_bind_to_server_local_is_local_network() -> None:
    bind_entry = _make_entry_with_method("bindToServer")
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=["webtestclient"],
        extra_entries=[bind_entry],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["local-network"]
    assert result.signals == {"local-network": ["real-http-local"]}


def test_webtestclient_bind_to_server_inside_helper_is_real_http() -> None:
    # The bindToServer call sits inside a resolved helper expansion, not in a
    # top-level grouping; the event traversal still flips WebTestClient to
    # real-HTTP dispatch.
    helper_call_sites = [make_call_site(method_name="initClient", start_line=1)]
    helper_method = make_callable(call_sites=helper_call_sites)
    grouping = build_call_site_grouping(list(helper_method.call_sites))
    grouping.nodes[0].helper_expansion = HelperExpansion(
        callee=MethodRef(
            defining_class_name="example.Test",
            method_signature="initClient()",
        ),
        grouping=build_call_site_grouping(
            [make_call_site(method_name="bindToServer", start_line=1)]
        ),
    )
    bind_entry = PhaseEntry(
        phase=LifecyclePhase.SETUP,
        method_ref=MethodRef(
            defining_class_name="example.Test",
            method_signature="setup()",
        ),
        context_class_name="example.Test",
        grouping=grouping,
        method_details=helper_method,
    )
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=["webtestclient"],
        extra_entries=[bind_entry],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["local-network"]
    assert result.signals == {"local-network": ["real-http-local"]}


def test_webtestclient_bind_to_server_external_is_remote_network() -> None:
    bind_entry = _make_entry_with_method("bindToServer")
    runtime_view = _make_runtime_view(
        paths=["https://api.external.com/v1"],
        frameworks=["webtestclient"],
        extra_entries=[bind_entry],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["remote-network"]
    assert result.signals == {"remote-network": ["real-http-remote"]}


def test_real_http_localhost_is_local_network() -> None:
    runtime_view = _make_runtime_view(
        paths=["http://localhost:8080/api"],
        frameworks=["java-httpclient"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["local-network"]
    assert result.signals == {"local-network": ["real-http-local"]}


def test_real_http_local_path_is_local_network() -> None:
    runtime_view = _make_runtime_view(
        paths=["/api/users"],
        frameworks=["rest-template"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["local-network"]


def test_micronaut_client_relative_path_is_local_network() -> None:
    # Micronaut client tests hit the @MicronautTest embedded server over real HTTP.
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=["micronaut-client"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["local-network"]
    assert result.signals == {"local-network": ["real-http-local"]}


def test_real_http_external_is_remote_network() -> None:
    runtime_view = _make_runtime_view(
        paths=["https://api.external.com/v1"],
        frameworks=["java-httpclient"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["remote-network"]


def test_real_http_unresolved_is_unknown() -> None:
    runtime_view = _make_runtime_view(
        paths=[""],
        frameworks=["rest-template"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["unknown"]
    assert result.signals == {"unknown": ["real-http-unresolved"]}


def test_real_http_bracket_invalid_url_is_unknown() -> None:
    runtime_view = _make_runtime_view(
        paths=["http://["],
        frameworks=["rest-template"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["unknown"]
    assert result.signals == {"unknown": ["real-http-unresolved"]}
    assert result.unresolved_request_count == 1


def test_real_http_xpath_literal_path_is_unknown() -> None:
    # A '//'-leading selector literal must not count as a local request.
    runtime_view = _make_runtime_view(
        paths=[r"//iframe[@id=\"OverlayIFrame\"]"],
        frameworks=["rest-template"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["unknown"]
    assert result.unresolved_request_count == 1


def test_builder_nodes_ignored() -> None:
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=["rest-template"],
        request_roles=[HttpRequestRole.BUILDER],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["unknown"]
    assert result.signals == {"unknown": ["no-events"]}


def test_mockmvc_and_real_http_produce_multi_labels() -> None:
    call_sites = [
        make_call_site(method_name="call", start_line=1),
        make_call_site(method_name="call", start_line=2),
    ]
    method = make_callable(call_sites=call_sites)
    grouping = build_call_site_grouping(list(method.call_sites))
    nodes = list(grouping.nodes)
    annotate_node_http(
        nodes[0],
        http_method="GET",
        path="/api/orders",
        framework="mockmvc",
        request_role=HttpRequestRole.EVENT,
    )
    annotate_node_http(
        nodes[1],
        http_method="GET",
        path="/api/users",
        framework="rest-template",
        request_role=HttpRequestRole.EVENT,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testMethod()",
                ),
                context_class_name="example.Test",
                grouping=grouping,
                method_details=method,
            )
        ]
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert set(result.labels) == {"in-process", "local-network"}
    assert "in-process" in result.signals
    assert "local-network" in result.signals


def test_local_and_remote_paths_produce_multi_labels() -> None:
    call_sites = [
        make_call_site(method_name="call", start_line=1),
        make_call_site(method_name="call", start_line=2),
    ]
    method = make_callable(call_sites=call_sites)
    grouping = build_call_site_grouping(list(method.call_sites))
    nodes = list(grouping.nodes)
    annotate_node_http(
        nodes[0],
        http_method="GET",
        path="/api/orders",
        framework="java-httpclient",
        request_role=HttpRequestRole.EVENT,
    )
    annotate_node_http(
        nodes[1],
        http_method="GET",
        path="https://api.external.com/v1",
        framework="java-httpclient",
        request_role=HttpRequestRole.EVENT,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testMethod()",
                ),
                context_class_name="example.Test",
                grouping=grouping,
                method_details=method,
            )
        ]
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert set(result.labels) == {"remote-network", "local-network"}
    assert "remote-network" in result.signals
    assert "local-network" in result.signals


def test_classify_wrapper_returns_decision_only() -> None:
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=["mockmvc"],
    )
    decision = classify_request_dispatch(runtime_view=runtime_view)
    assert decision == ["in-process"]


def test_apache_httpclient_local_is_local_network() -> None:
    runtime_view = _make_runtime_view(
        paths=["/api/test"],
        frameworks=["apache-httpclient"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["local-network"]


def test_okhttp_external_is_remote_network() -> None:
    runtime_view = _make_runtime_view(
        paths=["https://remote.service.io/data"],
        frameworks=["okhttp"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["remote-network"]


def test_test_rest_template_local_is_local_network() -> None:
    runtime_view = _make_runtime_view(
        paths=["/api/users"],
        frameworks=["test-rest-template"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["local-network"]


def test_rest_assured_local_is_local_network() -> None:
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=["rest-assured"],
        receiver_types=["io.restassured.RestAssured"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["local-network"]
    assert result.signals == {"local-network": ["real-http-local"]}


def test_rest_assured_external_is_remote_network() -> None:
    runtime_view = _make_runtime_view(
        paths=["https://api.external.com/v1"],
        frameworks=["rest-assured"],
        receiver_types=["io.restassured.RestAssured"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["remote-network"]
    assert result.signals == {"remote-network": ["real-http-remote"]}


def test_rest_assured_mockmvc_module_is_in_process() -> None:
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=["rest-assured"],
        receiver_types=["io.restassured.module.mockmvc.RestAssuredMockMvc"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["in-process"]
    assert result.signals == {"in-process": ["rest-assured-module-in-process"]}


def test_rest_assured_webtestclient_module_is_in_process() -> None:
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=["rest-assured"],
        receiver_types=["io.restassured.module.webtestclient.RestAssuredWebTestClient"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["in-process"]
    assert result.signals == {"in-process": ["rest-assured-module-in-process"]}


def test_legacy_rest_assured_mockmvc_module_is_in_process() -> None:
    # RestAssured 2.x shipped the spring-mock-mvc module under com.jayway.restassured.
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=["rest-assured"],
        receiver_types=["com.jayway.restassured.module.mockmvc.RestAssuredMockMvc"],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["in-process"]
    assert result.signals == {"in-process": ["rest-assured-module-in-process"]}


# Tier 2 real-HTTP dispatch frameworks: WebClient, RestClient, JAX-RS, Feign,
# HTTP Interface, Karate, Pact (consumer tests hit a local mock server over
# real HTTP), Citrus.
_TIER2_REAL_HTTP_FRAMEWORKS = [
    "webclient",
    "rest-client",
    "jax-rs",
    "feign",
    "http-interface",
    "karate",
    "pact",
    "citrus",
]


@pytest.mark.parametrize("framework", _TIER2_REAL_HTTP_FRAMEWORKS)
def test_tier2_framework_local_path_is_local_network(framework: str) -> None:
    runtime_view = _make_runtime_view(
        paths=["/api/orders"],
        frameworks=[framework],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["local-network"]
    assert result.signals == {"local-network": ["real-http-local"]}


@pytest.mark.parametrize("framework", _TIER2_REAL_HTTP_FRAMEWORKS)
def test_tier2_framework_external_path_is_remote_network(framework: str) -> None:
    runtime_view = _make_runtime_view(
        paths=["https://api.external.com/v1"],
        frameworks=[framework],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["remote-network"]
    assert result.signals == {"remote-network": ["real-http-remote"]}


@pytest.mark.parametrize("framework", _TIER2_REAL_HTTP_FRAMEWORKS)
def test_tier2_framework_unresolved_path_is_unknown(framework: str) -> None:
    runtime_view = _make_runtime_view(
        paths=[""],
        frameworks=[framework],
    )
    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["unknown"]
    assert result.signals == {"unknown": ["real-http-unresolved"]}
