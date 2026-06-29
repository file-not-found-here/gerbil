"""End-to-end HTTP path extraction that resolves String constants in call arguments."""

from __future__ import annotations

from cldk.models.java.models import JField

from gerbil.analysis.http.classification import classify_http_on_grouping
from gerbil.analysis.runtime.call_sites import (
    CallSiteNode,
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.schema import HttpRequestRole, LifecyclePhase
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
    make_field,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis

_OWNER_CLASS = "example.ApiTest"
_REST_TEMPLATE = "org.springframework.web.client.RestTemplate"
_WEB_TEST_CLIENT = "org.springframework.test.web.reactive.server.WebTestClient"


def _classify_chain(call_sites: list, analysis: FakeJavaAnalysis) -> list[CallSiteNode]:
    """Classify an arbitrary receiver chain on the owner class, returning its nodes."""
    method = make_callable(signature="test()", call_sites=call_sites)
    grouping = build_call_site_grouping(method.call_sites)
    owner = MethodRef(
        defining_class_name=_OWNER_CLASS,
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
        analysis=analysis,
        get_static_import_index_for_class=lambda _: StaticImportIndex.EMPTY,
    )
    classify_http_on_grouping(
        grouping=grouping, owner=owner, receiver_resolver=resolver
    )
    return grouping.nodes


def _classify_get(argument_expr: str, analysis: FakeJavaAnalysis) -> CallSiteNode:
    """Classify a single RestTemplate.getForEntity call and return the request node."""
    method = make_callable(
        signature="test()",
        call_sites=[
            make_call_site(
                method_name="getForEntity",
                receiver_type=_REST_TEMPLATE,
                receiver_expr="restTemplate",
                argument_expr=[argument_expr, "String.class"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=80,
            )
        ],
    )
    grouping = build_call_site_grouping(method.call_sites)
    owner = MethodRef(
        defining_class_name=_OWNER_CLASS,
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
        analysis=analysis,
        get_static_import_index_for_class=lambda _: StaticImportIndex.EMPTY,
    )
    classify_http_on_grouping(
        grouping=grouping, owner=owner, receiver_resolver=resolver
    )
    return grouping.nodes[0]


def _string_constant(variable: str, raw_initializer: str) -> JField:
    return make_field(
        type_name="java.lang.String",
        variables=[variable],
        modifiers=["static", "final"],
        variable_initializers={variable: raw_initializer},
    )


# Same-class constants resolve into the request's endpoint candidate.


def test_same_class_constant_concat_resolves_full_path_including_colon_segment() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={
            _OWNER_CLASS: make_type(
                field_declarations=[_string_constant("QUOTES_PATH", '"/rest/quotes"')]
            )
        }
    )

    node = _classify_get('QUOTES_PATH + "/s:0"', analysis)

    assert node.http_classification is not None
    assert node.http_classification.request_role == HttpRequestRole.EVENT
    assert node.http_classification.path == "/rest/quotes/s:0"
    assert node.http_classification.path_truncated is False
    assert node.endpoint_candidate is not None
    assert node.endpoint_candidate.path == "/rest/quotes/s:0"
    assert node.endpoint_candidate.path_truncated is False


def test_constant_inherited_from_project_base_class_resolves() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.BaseTest": make_type(
                field_declarations=[_string_constant("BASE_PATH", '"/rest/quotes"')]
            ),
            _OWNER_CLASS: make_type(extends_list=["example.BaseTest"]),
        }
    )

    node = _classify_get('BASE_PATH + "/s:0"', analysis)

    assert node.endpoint_candidate is not None
    assert node.endpoint_candidate.path == "/rest/quotes/s:0"
    assert node.endpoint_candidate.path_truncated is False


def test_interface_declared_constant_resolves() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                is_interface=True,
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["QUOTES"],
                        modifiers=[],
                        variable_initializers={"QUOTES": '"/rest/quotes"'},
                    )
                ],
            ),
            _OWNER_CLASS: make_type(implements_list=["example.Paths"]),
        }
    )

    node = _classify_get('QUOTES + "/s:0"', analysis)

    assert node.endpoint_candidate is not None
    assert node.endpoint_candidate.path == "/rest/quotes/s:0"
    assert node.endpoint_candidate.path_truncated is False


# Expressions that do not fully resolve fall back to today's literal scan.


def test_unresolved_identifier_falls_back_to_truncated_literal_suffix() -> None:
    analysis = FakeJavaAnalysis(classes={_OWNER_CLASS: make_type()})

    node = _classify_get('"/products/" + id', analysis)

    assert node.endpoint_candidate is not None
    assert node.endpoint_candidate.path == "/products/"
    assert node.endpoint_candidate.path_truncated is True


def test_method_call_initializer_does_not_resolve_and_falls_back() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            _OWNER_CLASS: make_type(
                field_declarations=[
                    _string_constant("DYNAMIC", 'System.getProperty("path")')
                ]
            )
        }
    )

    node = _classify_get('DYNAMIC + "/products/" + id', analysis)

    assert node.endpoint_candidate is not None
    assert node.endpoint_candidate.path == "/products/"
    assert node.endpoint_candidate.path_truncated is True


def test_ternary_initializer_does_not_resolve_and_falls_back() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            _OWNER_CLASS: make_type(
                field_declarations=[_string_constant("CHOICE", 'flag ? "/a" : "/b"')]
            )
        }
    )

    node = _classify_get('"/fallback/" + CHOICE', analysis)

    assert node.endpoint_candidate is not None
    assert node.endpoint_candidate.path == "/fallback/"
    assert node.endpoint_candidate.path_truncated is True


def test_expression_with_non_string_constant_does_not_resolve_and_falls_back() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            _OWNER_CLASS: make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["CONTEXT"],
                        modifiers=["static", "final"],
                        variable_initializers={"CONTEXT": '"/ctx"'},
                    ),
                    make_field(
                        type_name="int",
                        variables=["PORT"],
                        modifiers=["static", "final"],
                        variable_initializers={"PORT": "8080"},
                    ),
                ]
            )
        }
    )

    node = _classify_get('CONTEXT + PORT + "/path/" + id', analysis)

    assert node.endpoint_candidate is not None
    assert node.endpoint_candidate.path == "/path/"
    assert node.endpoint_candidate.path_truncated is True


# Constants resolve through the inferred-request builder path.


def _event_node(nodes: list[CallSiteNode]) -> CallSiteNode | None:
    for node in nodes:
        classification = node.http_classification
        if (
            classification is not None
            and classification.request_role == HttpRequestRole.EVENT
        ):
            return node
    return None


def test_inferred_request_builder_resolves_same_class_constant_path() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            _OWNER_CLASS: make_type(
                field_declarations=[_string_constant("BASE", '"/users"')]
            )
        }
    )

    nodes = _classify_chain(
        [
            make_call_site(
                method_name="get",
                receiver_type=_WEB_TEST_CLIENT,
                return_type=f"{_WEB_TEST_CLIENT}.RequestHeadersUriSpec<?>",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="uri",
                receiver_type="",
                argument_expr=['BASE + "/x"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type="",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=52,
            ),
        ],
        analysis,
    )

    event = _event_node(nodes)
    assert event is not None
    assert event.endpoint_candidate is not None
    assert event.endpoint_candidate.path == "/users/x"
    assert event.endpoint_candidate.path_truncated is False


def test_inferred_request_builder_unresolved_identifier_falls_back_to_truncated() -> (
    None
):
    analysis = FakeJavaAnalysis(classes={_OWNER_CLASS: make_type()})

    nodes = _classify_chain(
        [
            make_call_site(
                method_name="get",
                receiver_type=_WEB_TEST_CLIENT,
                return_type=f"{_WEB_TEST_CLIENT}.RequestHeadersUriSpec<?>",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="uri",
                receiver_type="",
                argument_expr=['"/users/" + id'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type="",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=52,
            ),
        ],
        analysis,
    )

    event = _event_node(nodes)
    assert event is not None
    assert event.endpoint_candidate is not None
    assert event.endpoint_candidate.path == "/users/"
    assert event.endpoint_candidate.path_truncated is True


# Constants resolve through the Citrus send-chain recovery.


def _citrus_send_chain(verb_argument: str, path_builder_argument: str | None) -> list:
    column = 9
    chain = [
        make_call_site(
            method_name="http",
            receiver_expr="",
            start_line=1,
            start_column=column,
            end_column=column + 5,
        ),
        make_call_site(
            method_name="client",
            argument_expr=["httpClient"],
            start_line=1,
            start_column=column,
            end_column=column + 25,
        ),
        make_call_site(
            method_name="send",
            start_line=1,
            start_column=column,
            end_column=column + 32,
        ),
        make_call_site(
            method_name="get",
            argument_expr=[verb_argument] if verb_argument else [],
            start_line=1,
            start_column=column,
            end_column=column + 50,
        ),
    ]
    if path_builder_argument is not None:
        chain.append(
            make_call_site(
                method_name="path",
                argument_expr=[path_builder_argument],
                start_line=1,
                start_column=column,
                end_column=column + 70,
            )
        )
    return chain


def test_citrus_path_builder_resolves_same_class_constant() -> None:
    # The verb call carries no path; the constant arrives on the .path(...) builder.
    analysis = FakeJavaAnalysis(
        classes={
            _OWNER_CLASS: make_type(
                field_declarations=[_string_constant("QUOTES_PATH", '"/rest/quotes"')]
            )
        }
    )

    nodes = _classify_chain(
        _citrus_send_chain(verb_argument="", path_builder_argument="QUOTES_PATH"),
        analysis,
    )

    event = _event_node(nodes)
    assert event is not None
    assert event.http_classification is not None
    assert event.http_classification.http_method == "GET"
    assert event.endpoint_candidate is not None
    assert event.endpoint_candidate.path == "/rest/quotes"
    assert event.endpoint_candidate.path_truncated is False


def test_citrus_verb_path_resolves_same_class_constant() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            _OWNER_CLASS: make_type(
                field_declarations=[_string_constant("QUOTES_PATH", '"/rest/quotes"')]
            )
        }
    )

    nodes = _classify_chain(
        _citrus_send_chain(verb_argument="QUOTES_PATH", path_builder_argument=None),
        analysis,
    )

    event = _event_node(nodes)
    assert event is not None
    assert event.endpoint_candidate is not None
    assert event.endpoint_candidate.path == "/rest/quotes"
    assert event.endpoint_candidate.path_truncated is False


def test_citrus_path_builder_unresolved_identifier_falls_back_to_truncated() -> None:
    analysis = FakeJavaAnalysis(classes={_OWNER_CLASS: make_type()})

    nodes = _classify_chain(
        _citrus_send_chain(verb_argument="", path_builder_argument='"/quotes/" + id'),
        analysis,
    )

    event = _event_node(nodes)
    assert event is not None
    assert event.endpoint_candidate is not None
    assert event.endpoint_candidate.path == "/quotes/"
    assert event.endpoint_candidate.path_truncated is True
