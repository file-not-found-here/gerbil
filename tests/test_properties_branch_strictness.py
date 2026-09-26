from __future__ import annotations

from cldk.models.java import JImport

from gerbil.analysis.shared.class_utils import ResolvedAnnotation
from gerbil.analysis.runtime.call_sites import (
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.schema import (
    AuthHandling,
    LifecyclePhase,
)
from gerbil.analysis.properties.auth_analysis import (
    classify_auth_handling as _classify_auth_handling,
)
from gerbil.analysis.properties.dependency_strategy import (
    classify_dependency_strategy as _classify_dependency_strategy,
)
from gerbil.analysis.properties.request_dispatch import analyze_request_dispatch
from gerbil.analysis.properties.precondition_analysis import (
    analyze_preconditions,
)
from gerbil.analysis.properties.sequence_analysis import build_api_call_sequence
from gerbil.analysis.shared.static_imports import StaticImportIndex
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    classify_runtime_view_for_testing,
    make_call_site,
    make_callable,
    make_field,
    make_import_declarations,
    make_import_lookup,
    make_resolved_annotation,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def _resolved_class_annotations(
    annotation_literals: list[str],
    *,
    declaring_class_name: str = "example.TestClass",
) -> list[ResolvedAnnotation]:
    return [
        make_resolved_annotation(
            annotation=annotation,
            declaring_class_name=declaring_class_name,
        )
        for annotation in annotation_literals
    ]


def _entry(
    *,
    phase: LifecyclePhase,
    qualified_class_name: str,
    method_signature: str,
    method_details,
    grouping=None,
) -> PhaseEntry:
    return PhaseEntry(
        phase=phase,
        method_ref=MethodRef(
            defining_class_name=qualified_class_name,
            method_signature=method_signature,
        ),
        context_class_name=qualified_class_name,
        grouping=grouping or build_call_site_grouping(list(method_details.call_sites)),
        method_details=method_details,
    )


def _runtime_view_for_method(
    method_details,
    *,
    qualified_class_name: str = "example.TestClass",
    method_signature: str = "testCase()",
) -> TestRuntimeView:
    return TestRuntimeView(
        entries=[
            _entry(
                phase=LifecyclePhase.TEST,
                qualified_class_name=qualified_class_name,
                method_signature=method_signature,
                method_details=method_details,
            )
        ]
    )


def _classify_runtime_view(runtime_view: TestRuntimeView) -> None:
    classify_runtime_view_for_testing(runtime_view)


def _build_api_call_sequence(runtime_view: TestRuntimeView):
    return build_api_call_sequence(runtime_view=runtime_view)


def classify_auth_handling(
    *,
    class_annotations: list[ResolvedAnnotation],
    method_annotations: list[str],
    class_annotation_imports_by_class,
    method_imports,
    runtime_view: TestRuntimeView,
    receiver_resolver=None,
    get_static_import_index_for_class=(lambda _class_name: StaticImportIndex.EMPTY),
):
    resolved_receiver_resolver = (
        receiver_resolver
        or build_runtime_receiver_resolver_for_testing(
            runtime_view,
            get_static_import_index_for_class=get_static_import_index_for_class,
        )
    )
    return _classify_auth_handling(
        class_annotations=class_annotations,
        method_annotations=method_annotations,
        class_annotation_imports_by_class=class_annotation_imports_by_class,
        method_imports=method_imports,
        runtime_view=runtime_view,
        receiver_resolver=resolved_receiver_resolver,
    )


def classify_dependency_strategy(
    *,
    class_details,
    method_details,
    class_annotations,
    runtime_view: TestRuntimeView,
    class_annotation_imports_by_class,
    method_imports,
    declaring_class_imports,
    analysis,
    receiver_resolver=None,
    get_static_import_index_for_class=(lambda _class_name: StaticImportIndex.EMPTY),
):
    resolved_receiver_resolver = (
        receiver_resolver
        or build_runtime_receiver_resolver_for_testing(
            runtime_view,
            analysis=analysis,
            get_static_import_index_for_class=get_static_import_index_for_class,
        )
    )
    return _classify_dependency_strategy(
        class_details=class_details,
        method_details=method_details,
        class_annotations=class_annotations,
        runtime_view=runtime_view,
        class_annotation_imports_by_class=class_annotation_imports_by_class,
        method_imports=method_imports,
        declaring_class_imports=declaring_class_imports,
        analysis=analysis,
        receiver_resolver=resolved_receiver_resolver,
    )


def test_auth_handling_precedence_uses_real_flow_over_mocked_and_token() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(method_name="withToken", argument_expr=['"authorization"']),
            make_call_site(
                method_name="authenticate",
                receiver_type="org.springframework.security.authentication.AuthenticationManager",
                callee_signature="AuthenticationManager.authenticate(Authentication)",
            ),
        ]
    )
    decision = classify_auth_handling(
        class_annotations=_resolved_class_annotations(["@WithMockUser"]),
        method_annotations=["@Test"],
        class_annotation_imports_by_class=make_import_lookup(
            {
                "example.TestClass": [
                    "org.springframework.security.test.context.support.WithMockUser"
                ]
            }
        ),
        method_imports=[],
        runtime_view=_runtime_view_for_method(method),
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.label == AuthHandling.REAL_FLOW.value


def test_auth_handling_detects_bypassed_annotations() -> None:
    method = make_callable()

    decision = classify_auth_handling(
        class_annotations=_resolved_class_annotations(["@WithAnonymousUser"]),
        method_annotations=[],
        class_annotation_imports_by_class=make_import_lookup(
            {
                "example.TestClass": [
                    "org.springframework.security.test.context.support.WithAnonymousUser"
                ]
            }
        ),
        method_imports=[],
        runtime_view=_runtime_view_for_method(method),
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.label == AuthHandling.BYPASSED.value


def test_dependency_strategy_mockbean_field_and_mockserver_call() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="when",
                receiver_type="org.mockserver.client.MockServerClient",
                start_line=40,
            )
        ]
    )
    klass = make_type(field_declarations=[make_field(annotations=["@MockBean"])])

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=make_import_declarations(
            "org.springframework.boot.test.mock.mockito.MockBean"
        ),
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked", "virtualized"]


def test_dependency_strategy_annotations_use_split_import_contexts() -> None:
    method = make_callable(annotations=["@MockBean"])
    klass = make_type(
        annotations=["@MockBean"],
        field_declarations=[make_field(annotations=["@MockBean"])],
    )

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        runtime_view=_runtime_view_for_method(method),
        class_annotations=_resolved_class_annotations(["@MockBean"]),
        class_annotation_imports_by_class=make_import_lookup(
            {"example.TestClass": ["com.example.MockBean"]}
        ),
        method_imports=make_import_declarations(
            "org.springframework.boot.test.mock.mockito.MockBean"
        ),
        declaring_class_imports=make_import_declarations("com.example.MockBean"),
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked"]


def test_dependency_strategy_field_annotations_use_declaring_import_context() -> None:
    method = make_callable()
    klass = make_type(field_declarations=[make_field(annotations=["@MockBean"])])

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class=make_import_lookup(
            {"example.TestClass": ["com.example.MockBean"]}
        ),
        method_imports=make_import_declarations("com.example.MockBean"),
        declaring_class_imports=make_import_declarations(
            "org.springframework.boot.test.mock.mockito.MockBean"
        ),
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked"]


def test_dependency_strategy_uses_class_annotation_import_context_independently() -> (
    None
):
    method = make_callable(annotations=["@MockBean"])
    klass = make_type(annotations=["@MockBean"])

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        runtime_view=_runtime_view_for_method(method),
        class_annotations=_resolved_class_annotations(["@MockBean"]),
        class_annotation_imports_by_class=make_import_lookup(
            {
                "example.TestClass": [
                    "org.springframework.boot.test.mock.mockito.MockBean"
                ]
            }
        ),
        method_imports=make_import_declarations("com.example.MockBean"),
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked"]


def test_dependency_strategy_ignores_restassured_given_when_chain() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="given",
                receiver_type="io.restassured.specification.RequestSpecification",
                start_line=12,
            ),
            make_call_site(
                method_name="when",
                receiver_type="io.restassured.specification.RequestSenderOptions",
                start_line=13,
            ),
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == []


def test_dependency_strategy_classifies_mockito_when_as_mocked() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="when",
                receiver_type="org.mockito.Mockito",
                start_line=14,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked"]


def test_dependency_strategy_classifies_static_imported_mockito_when_as_mocked() -> (
    None
):
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="when",
                start_line=14,
            )
        ]
    )
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="org.mockito.Mockito",
                is_static=True,
                is_wildcard=True,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: static_import_index,
    )

    assert decision.labels == ["mocked"]


def test_dependency_strategy_classifies_static_imported_mockito_spy_as_mocked() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="spy",
                start_line=14,
            )
        ]
    )
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="org.mockito.Mockito",
                is_static=True,
                is_wildcard=True,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: static_import_index,
    )

    assert decision.labels == ["mocked"]


def test_dependency_strategy_classifies_wiremock_given_as_virtualized() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="given",
                receiver_type="com.github.tomakehurst.wiremock.client.WireMock",
                start_line=15,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["virtualized"]


def test_dependency_strategy_classifies_wildcard_static_imported_wiremock_stubfor() -> (
    None
):
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="stubFor",
                start_line=15,
            )
        ]
    )
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="com.github.tomakehurst.wiremock.client.WireMock",
                is_static=True,
                is_wildcard=True,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: static_import_index,
    )

    assert decision.labels == ["virtualized"]


def test_dependency_strategy_classifies_wildcard_static_imported_wiremock_post_requested_for() -> (
    None
):
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="postRequestedFor",
                start_line=15,
            )
        ]
    )
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="com.github.tomakehurst.wiremock.client.WireMock",
                is_static=True,
                is_wildcard=True,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: static_import_index,
    )

    assert decision.labels == ["virtualized"]


def test_dependency_strategy_requires_runtime_mock_calls() -> None:
    method = make_callable(call_sites=[])

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == []


def test_dependency_strategy_external_http_with_mock_calls_only_detects_mocked() -> (
    None
):
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="when",
                receiver_type="org.mockito.Mockito",
                start_line=16,
            ),
            make_call_site(
                method_name="send",
                receiver_type="java.net.http.HttpClient",
                argument_expr=['"https://api.example.com/v1/orders"'],
                start_line=17,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=runtime_view,
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked"]


def test_dependency_strategy_external_url_literals_no_longer_detected() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="request",
                argument_expr=['URI.create("https://api.example.com/v1/orders")'],
                start_line=22,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == []


def test_dependency_strategy_resolves_application_receiver_inheritance() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "com.example.MyPostgresContainer": make_type(
                extends_list=[
                    "org.testcontainers.containers.PostgreSQLContainer<com.example.MyPostgresContainer>"
                ]
            ),
        }
    )
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="start",
                receiver_type="com.example.MyPostgresContainer",
                start_line=22,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=analysis,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["containerized"]


def test_dependency_strategy_receiver_hints_require_boundary_for_containerized() -> (
    None
):
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="start",
                receiver_type="org.testcontainersx.containers.PostgreSQLContainer",
                start_line=23,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == []


def test_dependency_strategy_receiver_hints_allow_segment_boundary_for_containerized() -> (
    None
):
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="start",
                receiver_type="org.testcontainers.containers.PostgreSQLContainer",
                start_line=24,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["containerized"]


def test_dependency_strategy_receiver_hints_require_boundary_for_mountebank() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="when",
                receiver_type="com.mbtest.mountebanking.Client",
                start_line=25,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == []


def test_dependency_strategy_receiver_hints_allow_segment_boundary_for_mountebank() -> (
    None
):
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="when",
                receiver_type="com.mbtest.mountebank.Client",
                start_line=26,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["virtualized"]


def test_dependency_strategy_mock_field_correlated_via_receiver_expr() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="findAll",
                receiver_expr="paymentClient",
                start_line=10,
            )
        ]
    )
    klass = make_type(
        field_declarations=[
            make_field(annotations=["@Mock"], variables=["paymentClient"])
        ]
    )

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=make_import_declarations("org.mockito.Mock"),
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked"]
    assert any(
        "field-correlated" in s and "paymentClient" in s
        for s in decision.signals.get("mocked", [])
    )


def test_dependency_strategy_mock_field_not_correlated_when_unused() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="findAll",
                receiver_expr="orderService",
                start_line=10,
            )
        ]
    )
    klass = make_type(
        field_declarations=[
            make_field(annotations=["@Mock"], variables=["paymentClient"])
        ]
    )

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=make_import_declarations("org.mockito.Mock"),
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == []


def test_dependency_strategy_mock_field_correlated_via_argument_expr() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="when",
                receiver_type="org.mockito.Mockito",
                argument_expr=["paymentClient.findAll()"],
                start_line=10,
            )
        ]
    )
    klass = make_type(
        field_declarations=[
            make_field(annotations=["@Mock"], variables=["paymentClient"])
        ]
    )

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=make_import_declarations("org.mockito.Mock"),
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked"]
    assert any("field-correlated" in s for s in decision.signals.get("mocked", []))


def test_dependency_strategy_short_mock_field_does_not_match_inside_identifier() -> (
    None
):
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="serialize",
                receiver_expr="customMapper",
                argument_expr=["customPayload"],
                start_line=10,
            )
        ]
    )
    klass = make_type(
        field_declarations=[make_field(annotations=["@Mock"], variables=["om"])]
    )

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=make_import_declarations("org.mockito.Mock"),
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == []


def test_dependency_strategy_short_mock_field_matches_on_word_boundary() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="when",
                receiver_type="org.mockito.Mockito",
                argument_expr=["om.readValue(json, User.class)"],
                start_line=10,
            )
        ]
    )
    klass = make_type(
        field_declarations=[make_field(annotations=["@Mock"], variables=["om"])]
    )

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=make_import_declarations("org.mockito.Mock"),
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked"]
    assert any(
        "field-correlated" in s and s.endswith(":om")
        for s in decision.signals.get("mocked", [])
    )


def test_dependency_strategy_spy_field_follows_correlation_rule() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="doReturn",
                receiver_expr="spyService",
                receiver_type="org.mockito.Mockito",
                start_line=10,
            )
        ]
    )
    klass = make_type(
        field_declarations=[make_field(annotations=["@Spy"], variables=["spyService"])]
    )

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=make_import_declarations("org.mockito.Spy"),
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked"]


def test_dependency_strategy_multi_tier_combination() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="stubFor",
                receiver_type="com.github.tomakehurst.wiremock.client.WireMock",
                start_line=10,
            )
        ]
    )
    klass = make_type(field_declarations=[make_field(annotations=["@MockBean"])])

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        class_annotations=_resolved_class_annotations(["@Testcontainers"]),
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class=make_import_lookup(
            {"example.TestClass": ["org.testcontainers.junit.jupiter.Testcontainers"]}
        ),
        method_imports=[],
        declaring_class_imports=make_import_declarations(
            "org.springframework.boot.test.mock.mockito.MockBean"
        ),
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["containerized", "mocked", "virtualized"]


def test_dependency_strategy_signal_format() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="when",
                receiver_type="org.mockito.Mockito",
                start_line=14,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked"]
    mocked_signals = decision.signals["mocked"]
    assert any(
        "call-site:receiver:org.mockito.Mockito.when" in s for s in mocked_signals
    )


def test_request_dispatch_rest_assured_external_returns_remote_for_multiple_hosts() -> (
    None
):
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.specification.RequestSenderOptions",
                argument_expr=['"https://api.one.example/users"'],
                start_line=1,
            ),
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.specification.RequestSenderOptions",
                argument_expr=['"https://api.two.example/users"'],
                start_line=2,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    _classify_runtime_view(runtime_view)

    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["remote-network"]


def test_request_dispatch_rest_assured_external_returns_remote_for_single_host() -> (
    None
):
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.specification.RequestSenderOptions",
                argument_expr=['"https://api.one.example/users"'],
                start_line=1,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    _classify_runtime_view(runtime_view)

    result = analyze_request_dispatch(runtime_view=runtime_view)
    assert result.labels == ["remote-network"]


def test_request_dispatch_no_runtime_returns_unknown() -> None:
    result = analyze_request_dispatch(runtime_view=None)
    assert result.labels == ["unknown"]


def _empty_runtime_kwargs() -> dict:
    runtime_view = TestRuntimeView()
    return {
        "runtime_view": runtime_view,
        "analysis": None,
        "receiver_resolver": build_runtime_receiver_resolver_for_testing(runtime_view),
    }


def test_precondition_analysis_detects_both_labels_from_class_annotations() -> None:
    summary = analyze_preconditions(
        class_annotations=_resolved_class_annotations(["@Testcontainers", "@Sql"]),
        method_annotations=[],
        class_annotation_imports_by_class=make_import_lookup(
            {
                "example.TestClass": [
                    "org.testcontainers.junit.jupiter.Testcontainers",
                    "org.springframework.test.context.jdbc.Sql",
                ]
            }
        ),
        method_imports=[],
        **_empty_runtime_kwargs(),
    )

    types = sorted({p.type.value for p in summary.preconditions})
    assert types == ["container-bootstrap", "db-seeding"]
    evidence_by_type = {
        p.type.value: p.evidence
        for p in summary.preconditions
        if p.source.value == "annotation"
    }
    assert evidence_by_type["container-bootstrap"] == "@Testcontainers"
    assert evidence_by_type["db-seeding"] == "@Sql"


def test_precondition_analysis_empty_when_no_matching_annotations() -> None:
    summary = analyze_preconditions(
        class_annotations=[],
        method_annotations=[],
        class_annotation_imports_by_class={},
        method_imports=[],
        **_empty_runtime_kwargs(),
    )

    assert summary.preconditions == []


def test_precondition_analysis_uses_split_import_contexts() -> None:
    summary = analyze_preconditions(
        class_annotations=_resolved_class_annotations(["@Sql"]),
        method_annotations=["@Container"],
        class_annotation_imports_by_class=make_import_lookup(
            {"example.TestClass": ["com.example.Sql"]}
        ),
        method_imports=make_import_declarations(
            "org.testcontainers.junit.jupiter.Container"
        ),
        **_empty_runtime_kwargs(),
    )

    assert [p.type.value for p in summary.preconditions] == ["container-bootstrap"]
    assert summary.preconditions[0].evidence == "@Container"
    assert summary.preconditions[0].source.value == "annotation"


def test_precondition_analysis_uses_class_import_context_independently() -> None:
    summary = analyze_preconditions(
        class_annotations=_resolved_class_annotations(["@Sql"]),
        method_annotations=["@Container"],
        class_annotation_imports_by_class=make_import_lookup(
            {"example.TestClass": ["org.springframework.test.context.jdbc.Sql"]}
        ),
        method_imports=make_import_declarations("com.example.Container"),
        **_empty_runtime_kwargs(),
    )

    assert [p.type.value for p in summary.preconditions] == ["db-seeding"]
    assert summary.preconditions[0].evidence == "@Sql"
    assert summary.preconditions[0].source.value == "annotation"


def test_sequence_skeleton_only_emits_http_classified_steps() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(method_name="save", start_line=5),
            make_call_site(
                method_name="getForEntity",
                receiver_type="org.springframework.web.client.RestTemplate",
                callee_signature="getForEntity()",
                argument_expr=['"/api/users/1"'],
                start_line=10,
            ),
            make_call_site(method_name="assertEquals", start_line=15),
            make_call_site(method_name="verify", start_line=25),
            make_call_site(method_name="log", start_line=30),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            _entry(
                phase=LifecyclePhase.TEST,
                qualified_class_name="example.TestClass",
                method_signature="testCase()",
                method_details=method,
            )
        ]
    )

    _classify_runtime_view(runtime_view)
    steps = _build_api_call_sequence(runtime_view)

    assert [step.kind.value for step in steps] == ["http-request"]
    assert steps[0].http_method == "GET"
    assert steps[0].http_path == "/api/users/1"
    assert steps[0].order == 1


def test_sequence_skeleton_excludes_unrecognized_header_call() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(method_name="header", start_line=5),
            make_call_site(
                method_name="getForEntity",
                receiver_type="org.springframework.web.client.RestTemplate",
                argument_expr=['"/api/users/1"'],
                start_line=10,
            ),
        ]
    )

    runtime_view = TestRuntimeView(
        entries=[
            _entry(
                phase=LifecyclePhase.TEST,
                qualified_class_name="example.TestClass",
                method_signature="testCase()",
                method_details=method,
            )
        ]
    )

    _classify_runtime_view(runtime_view)
    steps = _build_api_call_sequence(runtime_view)

    assert len(steps) == 1
    assert steps[0].kind.value == "http-request"
    assert steps[0].http_method == "GET"


def test_sequence_skeleton_spans_fixture_and_test_phases() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="getForEntity",
                receiver_type="org.springframework.web.client.RestTemplate",
                callee_signature="getForEntity()",
                argument_expr=['"/api/users/1"'],
                start_line=10,
            ),
            make_call_site(method_name="assertEquals", start_line=11),
        ]
    )
    setup_fixture_method = make_callable(
        call_sites=[
            make_call_site(
                method_name="postForEntity",
                receiver_type="org.springframework.web.client.RestTemplate",
                callee_signature="postForEntity()",
                argument_expr=['"/seed/users"'],
                start_line=2,
            ),
            make_call_site(method_name="debugLog", start_line=3),
        ]
    )
    teardown_fixture_method = make_callable(
        call_sites=[
            make_call_site(method_name="verify", start_line=4),
            make_call_site(method_name="debugLog", start_line=5),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            _entry(
                phase=LifecyclePhase.SETUP,
                qualified_class_name="example.TestClass",
                method_signature="beforeEach()",
                method_details=setup_fixture_method,
            ),
            _entry(
                phase=LifecyclePhase.TEST,
                qualified_class_name="example.TestClass",
                method_signature="testCase()",
                method_details=method,
            ),
            _entry(
                phase=LifecyclePhase.TEARDOWN,
                qualified_class_name="example.TestClass",
                method_signature="afterEach()",
                method_details=teardown_fixture_method,
            ),
        ]
    )

    _classify_runtime_view(runtime_view)
    steps = _build_api_call_sequence(runtime_view)

    assert [(s.phase.value, s.kind.value, s.method_name) for s in steps] == [
        ("setup", "http-request", "postForEntity"),
        ("test", "http-request", "getForEntity"),
    ]
    assert steps[0].http_method == "POST"
    assert steps[1].http_method == "GET"
    assert all(s.method_name != "debugLog" for s in steps)
    assert all(s.order == i for i, s in enumerate(steps, start=1))


def test_sequence_skeleton_excludes_general_assertions_in_fixtures() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="getForEntity",
                receiver_type="org.springframework.web.client.RestTemplate",
                callee_signature="getForEntity()",
                argument_expr=['"/api/users/1"'],
                start_line=10,
            )
        ]
    )
    setup_fixture_method = make_callable(
        call_sites=[make_call_site(method_name="assertEquals", start_line=2)]
    )
    teardown_fixture_method = make_callable(
        call_sites=[make_call_site(method_name="assertTimeout", start_line=20)]
    )
    runtime_view = TestRuntimeView(
        entries=[
            _entry(
                phase=LifecyclePhase.SETUP,
                qualified_class_name="example.TestClass",
                method_signature="beforeEach()",
                method_details=setup_fixture_method,
            ),
            _entry(
                phase=LifecyclePhase.TEST,
                qualified_class_name="example.TestClass",
                method_signature="testCase()",
                method_details=method,
            ),
            _entry(
                phase=LifecyclePhase.TEARDOWN,
                qualified_class_name="example.TestClass",
                method_signature="afterEach()",
                method_details=teardown_fixture_method,
            ),
        ]
    )

    _classify_runtime_view(runtime_view)
    steps = _build_api_call_sequence(runtime_view)

    assert [(s.kind.value, s.method_name) for s in steps] == [
        ("http-request", "getForEntity"),
    ]


def test_sequence_skeleton_http_steps_across_fixture_and_test() -> None:
    method = make_callable(
        signature="testEndpoint()",
        call_sites=[
            make_call_site(
                method_name="getForEntity",
                receiver_type="org.springframework.web.client.RestTemplate",
                callee_signature="getForEntity()",
                argument_expr=['"/api/users/1"'],
                start_line=10,
            )
        ],
    )
    setup_fixture_method = make_callable(
        signature="beforeEach()",
        call_sites=[
            make_call_site(
                method_name="postForEntity",
                receiver_type="org.springframework.web.client.RestTemplate",
                callee_signature="postForEntity()",
                argument_expr=['"/seed/users"'],
                start_line=2,
            )
        ],
    )
    runtime_view = TestRuntimeView(
        entries=[
            _entry(
                phase=LifecyclePhase.SETUP,
                qualified_class_name="example.TestClass",
                method_signature="beforeEach()",
                method_details=setup_fixture_method,
            ),
            _entry(
                phase=LifecyclePhase.TEST,
                qualified_class_name="example.TestClass",
                method_signature="testEndpoint()",
                method_details=method,
            ),
        ]
    )

    _classify_runtime_view(runtime_view)
    steps = _build_api_call_sequence(runtime_view)

    http_steps = [s for s in steps if s.kind.value == "http-request"]
    assert [s.http_method for s in http_steps] == ["POST", "GET"]
    assert [s.http_path for s in http_steps] == ["/seed/users", "/api/users/1"]


def test_dependency_strategy_mockito_bean_annotation_counts_as_mocked() -> None:
    method = make_callable()
    klass = make_type(annotations=["@MockitoBean"])

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        class_annotations=_resolved_class_annotations(["@MockitoBean"]),
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class=make_import_lookup(
            {
                "example.TestClass": [
                    "org.springframework.test.context.bean.override.mockito.MockitoBean"
                ]
            }
        ),
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["mocked"]


def test_dependency_strategy_autoconfigure_wiremock_counts_as_virtualized() -> None:
    method = make_callable()
    klass = make_type(annotations=["@AutoConfigureWireMock"])

    decision = classify_dependency_strategy(
        class_details=klass,
        method_details=method,
        class_annotations=_resolved_class_annotations(["@AutoConfigureWireMock"]),
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class=make_import_lookup(
            {
                "example.TestClass": [
                    "org.springframework.cloud.contract.wiremock.AutoConfigureWireMock"
                ]
            }
        ),
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["virtualized"]


def test_dependency_strategy_testcontainers_module_package_counts_as_containerized() -> (
    None
):
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="start",
                receiver_type="org.testcontainers.kafka.KafkaContainer",
                start_line=10,
            )
        ]
    )

    decision = classify_dependency_strategy(
        class_details=make_type(),
        method_details=method,
        class_annotations=[],
        runtime_view=_runtime_view_for_method(method),
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
    )

    assert decision.labels == ["containerized"]


def test_container_bootstrap_detects_testcontainers_module_package_start_in_fixture() -> (
    None
):
    setup_method = make_callable(
        signature="beforeAll()",
        call_sites=[
            make_call_site(
                method_name="start",
                receiver_type="org.testcontainers.kafka.KafkaContainer",
                start_line=5,
            )
        ],
    )
    test_method = make_callable(signature="testSomething()")
    runtime_view = TestRuntimeView(
        entries=[
            _entry(
                phase=LifecyclePhase.SETUP,
                qualified_class_name="example.TestClass",
                method_signature="beforeAll()",
                method_details=setup_method,
            ),
            _entry(
                phase=LifecyclePhase.TEST,
                qualified_class_name="example.TestClass",
                method_signature="testSomething()",
                method_details=test_method,
            ),
        ]
    )

    summary = analyze_preconditions(
        class_annotations=[],
        method_annotations=[],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        analysis=None,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    assert any(
        p.type.value == "container-bootstrap"
        and "org.testcontainers" in p.evidence
        and "start" in p.evidence
        for p in summary.preconditions
    )
