from __future__ import annotations

import inspect

import pytest
from cldk.models.java import JImport

from gerbil.analysis.shared import CommonAnalysis
from gerbil.analysis.runtime.call_sites import (
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.schema import (
    AuthHandling,
    AuthHandlingDecision,
    LifecyclePhase,
)
from gerbil.analysis.properties.auth_analysis import classify_auth_handling
from gerbil.analysis.shared.receiver_resolution import RuntimeReceiverResolver
from gerbil.analysis.shared.static_imports import StaticImportIndex
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from tests.cldk_factories import (
    annotate_node_http,
    make_call_site,
    make_callable,
    make_resolved_annotation,
    make_type,
    make_variable_declaration,
)
from tests.fake_java_analysis import FakeJavaAnalysis


_SPRING_SECURITY_POST_PROCESSORS = (
    "org.springframework.security.test.web.servlet.request."
    "SecurityMockMvcRequestPostProcessors"
)


def _runtime_view_for_method(
    method, *, class_name: str = "example.TestClass"
) -> TestRuntimeView:
    return TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name=class_name,
                    method_signature="testCase()",
                ),
                context_class_name=class_name,
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )


def _resolved_class_annotations(
    annotation_literals: list[str],
    *,
    declaring_class_name: str = "example.TestClass",
) -> list:
    return [
        make_resolved_annotation(
            annotation=annotation,
            declaring_class_name=declaring_class_name,
        )
        for annotation in annotation_literals
    ]


def _runtime_receiver_resolver(
    *,
    runtime_view: TestRuntimeView,
    analysis: FakeJavaAnalysis | None = None,
    static_import_index: StaticImportIndex = StaticImportIndex.EMPTY,
) -> RuntimeReceiverResolver:
    resolved_analysis = analysis or FakeJavaAnalysis()
    common_analysis = CommonAnalysis(resolved_analysis)
    method_details_by_owner = {
        entry.method_ref: entry.method_details for entry in runtime_view.entries
    }
    return RuntimeReceiverResolver(
        analysis=resolved_analysis,
        load_method_details=lambda owner: method_details_by_owner.get(owner),
        get_static_import_index_for_class=lambda _class_name: static_import_index,
        get_class_imports_for_class=common_analysis.get_class_imports,
        get_superclass_chain_for_class=common_analysis.get_superclass_chain,
        constant_resolver=common_analysis.get_constant_resolver(),
    )


def _classify_spring_security_post_processor(
    method_name: str,
    *,
    is_wildcard: bool = True,
) -> AuthHandlingDecision:
    method = make_callable(call_sites=[make_call_site(method_name=method_name)])
    runtime_view = _runtime_view_for_method(method)
    import_path = _SPRING_SECURITY_POST_PROCESSORS
    if not is_wildcard:
        import_path = f"{_SPRING_SECURITY_POST_PROCESSORS}.{method_name}"
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path=import_path,
                is_static=True,
                is_wildcard=is_wildcard,
            )
        ]
    )

    return classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view=runtime_view,
            static_import_index=static_import_index,
        ),
    )


def test_legacy_rest_assured_oauth2_call_site_counts_as_test_token() -> None:
    """RestAssured 2.x auth lives under com.jayway.restassured; oauth2 supplies a test token."""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="oauth2",
                receiver_type=(
                    "com.jayway.restassured.specification.RequestSpecification"
                ),
                receiver_expr="spec",
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.TEST_TOKEN.value
    assert any(
        "com.jayway.restassured" in signal and "oauth2" in signal
        for signal in decision.signals.get(AuthHandling.TEST_TOKEN.value, [])
    )


@pytest.mark.parametrize(
    "method_name", ["basic", "digest", "ntlm", "form", "oauth", "certificate"]
)
def test_rest_assured_auth_chain_credentials_count_as_test_token(
    method_name: str,
) -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="auth",
                receiver_type="io.restassured.specification.RequestSpecification",
                receiver_expr="given()",
            ),
            make_call_site(
                method_name=method_name,
                receiver_type=(
                    "io.restassured.specification.AuthenticationSpecification"
                ),
                receiver_expr="given().auth()",
                argument_expr=['"u"', '"p"'],
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.TEST_TOKEN.value
    assert any(
        "AuthenticationSpecification" in signal and method_name in signal
        for signal in decision.signals.get(AuthHandling.TEST_TOKEN.value, [])
    )


def test_test_rest_template_with_basic_auth_counts_as_test_token() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="withBasicAuth",
                receiver_type=(
                    "org.springframework.boot.test.web.client.TestRestTemplate"
                ),
                receiver_expr="restTemplate",
                argument_expr=['"u"', '"p"'],
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.TEST_TOKEN.value
    assert any(
        "TestRestTemplate" in signal and "withBasicAuth" in signal
        for signal in decision.signals.get(AuthHandling.TEST_TOKEN.value, [])
    )


def test_mocked_auth_hints_do_not_map_to_real_flow() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(method_name="withMockUser"),
            make_call_site(method_name="oauth2Login"),
            make_call_site(method_name="jwt"),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.MOCKED.value
    assert "mocked" in decision.signals


def test_mockmvc_security_user_static_import_counts_as_mocked_auth() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="with",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockHttpServletRequestBuilder"
                ),
                argument_expr=['user("alice").roles("ADMIN")'],
            ),
            make_call_site(
                method_name="user",
                argument_expr=['"alice"'],
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path=_SPRING_SECURITY_POST_PROCESSORS,
                is_static=True,
                is_wildcard=True,
            )
        ]
    )

    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view=runtime_view,
            static_import_index=static_import_index,
        ),
    )

    assert decision.label == AuthHandling.MOCKED.value
    assert decision.signals == {
        "mocked": ["call-site:receiver:" f"{_SPRING_SECURITY_POST_PROCESSORS}.user"]
    }


def test_mockmvc_security_named_user_static_import_counts_as_mocked_auth() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="user",
                argument_expr=['"alice"'],
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path=f"{_SPRING_SECURITY_POST_PROCESSORS}.user",
                is_static=True,
                is_wildcard=False,
            )
        ]
    )

    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view=runtime_view,
            static_import_index=static_import_index,
        ),
    )

    assert decision.label == AuthHandling.MOCKED.value
    assert "mocked" in decision.signals


@pytest.mark.parametrize("method_name", ["opaqueToken", "testSecurityContext"])
def test_mockmvc_security_mocked_post_processor_static_import_counts_as_mocked_auth(
    method_name: str,
) -> None:
    decision = _classify_spring_security_post_processor(method_name)

    assert decision.label == AuthHandling.MOCKED.value
    assert decision.signals == {
        "mocked": [
            "call-site:receiver:" f"{_SPRING_SECURITY_POST_PROCESSORS}.{method_name}"
        ]
    }


def test_mockmvc_security_named_opaque_token_import_counts_as_mocked_auth() -> None:
    decision = _classify_spring_security_post_processor(
        "opaqueToken",
        is_wildcard=False,
    )

    assert decision.label == AuthHandling.MOCKED.value
    assert "mocked" in decision.signals


def test_mockmvc_security_anonymous_static_import_counts_as_bypassed_auth() -> None:
    decision = _classify_spring_security_post_processor("anonymous")

    assert decision.label == AuthHandling.BYPASSED.value
    assert "bypassed" in decision.signals


def test_mockmvc_security_http_basic_static_import_counts_as_test_token() -> None:
    decision = _classify_spring_security_post_processor("httpBasic")

    assert decision.label == AuthHandling.TEST_TOKEN.value
    assert "test-token" in decision.signals


def test_mockmvc_security_digest_static_import_counts_as_test_token() -> None:
    decision = _classify_spring_security_post_processor("digest")

    assert decision.label == AuthHandling.TEST_TOKEN.value
    assert decision.signals == {
        "test-token": [f"call-site:receiver:{_SPRING_SECURITY_POST_PROCESSORS}.digest"]
    }


def test_mockmvc_security_csrf_static_import_does_not_classify_auth_by_itself() -> None:
    # csrf is a security post-processor but not authentication; with no auth
    # evidence the test resolves to `none`, not the auth-but-unclassified `unknown`.
    decision = _classify_spring_security_post_processor("csrf")

    assert decision.label == AuthHandling.NONE.value
    assert decision.signals == {}


def test_real_flow_requires_structural_auth_context() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(method_name="login"),
            make_call_site(method_name="authenticate"),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    # Bare login()/authenticate() with no security receiver is not auth evidence.
    assert decision.label == AuthHandling.NONE.value


def test_real_flow_detected_with_structural_context() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="authenticate",
                receiver_type="org.springframework.security.authentication.AuthenticationManager",
                callee_signature="AuthenticationManager.authenticate(Authentication)",
            )
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.REAL_FLOW.value
    assert "real-flow" in decision.signals
    assert any("receiver:" in s for s in decision.signals["real-flow"])


def test_auth_route_http_call_counts_as_real_flow() -> None:
    method = make_callable(
        call_sites=[make_call_site(method_name="post")],
    )
    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[0],
        http_method="POST",
        path="/oauth/token",
        framework="rest-assured",
    )

    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.REAL_FLOW.value
    assert "real-flow" in decision.signals
    assert any("route:" in s for s in decision.signals["real-flow"])


def test_auth_route_substring_inside_word_does_not_count_as_real_flow() -> None:
    method = make_callable(
        call_sites=[make_call_site(method_name="get")],
    )
    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[0],
        http_method="GET",
        path="/api/authors",
        framework="rest-assured",
    )

    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    # A non-auth path with no other auth evidence is `none`, not `unknown`.
    assert decision.label == AuthHandling.NONE.value


def test_auth_route_standalone_segments_still_count_as_real_flow() -> None:
    for path in ["/login", "/auth/callback", "/api/v1/auth/session"]:
        method = make_callable(
            call_sites=[make_call_site(method_name="get")],
        )
        runtime_view = _runtime_view_for_method(method)
        nodes = list(runtime_view.entries[0].grouping.nodes)
        annotate_node_http(
            nodes[0],
            http_method="GET",
            path=path,
            framework="rest-assured",
        )

        decision = classify_auth_handling(
            class_annotations=[],
            method_annotations=["@Test"],
            class_annotation_imports_by_class={},
            method_imports=[],
            runtime_view=runtime_view,
            receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
        )

        assert decision.label == AuthHandling.REAL_FLOW.value, path


def test_teardown_only_auth_hints_change_label() -> None:
    teardown_method = make_callable(
        signature="afterEach()",
        call_sites=[
            make_call_site(
                method_name="authenticate",
                receiver_type="org.springframework.security.authentication.AuthenticationManager",
                callee_signature="AuthenticationManager.authenticate(Authentication)",
            )
        ],
    )
    test_method = make_callable(call_sites=[])
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.TestClass",
                    method_signature="testCase()",
                ),
                context_class_name="example.TestClass",
                grouping=build_call_site_grouping([]),
                method_details=test_method,
            ),
            PhaseEntry(
                phase=LifecyclePhase.TEARDOWN,
                method_ref=MethodRef(
                    defining_class_name="example.TestClass",
                    method_signature="afterEach()",
                ),
                context_class_name="example.TestClass",
                grouping=build_call_site_grouping(list(teardown_method.call_sites)),
                method_details=teardown_method,
            ),
        ],
    )
    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.REAL_FLOW.value


def test_classify_auth_handling_signature_excludes_method_details() -> None:
    parameter_names = list(inspect.signature(classify_auth_handling).parameters)

    assert parameter_names == [
        "class_annotations",
        "method_annotations",
        "class_annotation_imports_by_class",
        "method_imports",
        "runtime_view",
        "receiver_resolver",
    ]


def test_classify_auth_handling_normalizes_fully_qualified_annotations() -> None:
    method = make_callable()
    runtime_view = _runtime_view_for_method(method)

    decision = classify_auth_handling(
        class_annotations=_resolved_class_annotations(
            ["@org.springframework.security.test.context.support.WithMockUser"]
        ),
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.MOCKED.value
    assert "mocked" in decision.signals
    assert any("annotation:" in s for s in decision.signals["mocked"])


def test_auth_handling_uses_runtime_resolver_for_receiver_expr_context() -> None:
    auth_call = make_call_site(
        method_name="authenticate",
        receiver_expr="authManager",
        receiver_type="",
        callee_signature="AuthenticationManager.authenticate(Authentication)",
        start_line=12,
    )
    method = make_callable(
        call_sites=[auth_call],
        variable_declarations=[
            make_variable_declaration(
                name="authManager",
                type_name="org.springframework.security.authentication.AuthenticationManager",
                start_line=8,
            )
        ],
    )
    runtime_view = _runtime_view_for_method(method)
    analysis = FakeJavaAnalysis(
        classes={"example.TestClass": make_type()},
        methods_by_class={"example.TestClass": {"testCase()": method}},
    )

    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view=runtime_view,
            analysis=analysis,
        ),
    )

    assert decision.label == AuthHandling.REAL_FLOW.value


def _classify_request_with_auth_hints(auth_hints: list[str]) -> AuthHandlingDecision:
    method = make_callable(call_sites=[make_call_site(method_name="get")])
    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[0],
        http_method="GET",
        path="/api/things",
        framework="rest-assured",
        auth_hints=auth_hints,
    )
    return classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )


# --- test-token: any request credential, including Basic / X-API-Key ---


@pytest.mark.parametrize("hint", ["Authorization", "Bearer", "Basic", "X-API-Key"])
def test_request_credential_hints_count_as_test_token(hint: str) -> None:
    decision = _classify_request_with_auth_hints([hint])

    assert decision.label == AuthHandling.TEST_TOKEN.value
    assert "test-token" in decision.signals


# --- none vs unknown: clear separation ---


def test_no_auth_evidence_resolves_to_none() -> None:
    method = make_callable(call_sites=[make_call_site(method_name="get")])
    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[0], http_method="GET", path="/api/things", framework="rest-assured"
    )

    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.NONE.value
    assert decision.signals == {}


def test_custom_with_security_annotation_resolves_to_unknown() -> None:
    method = make_callable(call_sites=[])
    runtime_view = _runtime_view_for_method(method)

    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test", "@WithMockCustomUser"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.UNKNOWN.value
    assert any("weak:annotation:" in s for s in decision.signals["unknown"])


def test_unrelated_with_annotation_does_not_resolve_to_unknown() -> None:
    method = make_callable(call_sites=[])
    runtime_view = _runtime_view_for_method(method)

    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test", "@WithEnvironmentVariable"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.NONE.value


def test_unrecognized_method_on_auth_receiver_resolves_to_unknown() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="refreshToken",
                receiver_type="org.keycloak.admin.client.Keycloak",
                callee_signature="Keycloak.refreshToken()",
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)

    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.UNKNOWN.value
    assert any("weak:auth-receiver:" in s for s in decision.signals["unknown"])


@pytest.mark.parametrize("method_name", ["setBearerAuth", "setBasicAuth"])
def test_spring_http_headers_auth_methods_count_as_test_token(method_name: str) -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name=method_name,
                receiver_type="org.springframework.http.HttpHeaders",
                receiver_expr="headers",
                argument_expr=['"token"'],
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.TEST_TOKEN.value
    assert any(
        "HttpHeaders" in signal and method_name in signal
        for signal in decision.signals.get(AuthHandling.TEST_TOKEN.value, [])
    )


def test_recognized_method_on_auth_receiver_stays_real_flow_not_weak() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="authenticate",
                receiver_type="org.springframework.security.authentication.AuthenticationManager",
                callee_signature="AuthenticationManager.authenticate(Authentication)",
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)

    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=["@Test"],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view=runtime_view),
    )

    assert decision.label == AuthHandling.REAL_FLOW.value
    assert "unknown" not in decision.signals
