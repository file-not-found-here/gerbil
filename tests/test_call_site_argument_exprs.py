from __future__ import annotations

import pytest

from gerbil.analysis.assertion import classify_assertions_on_runtime_view
from gerbil.analysis.http.classification import build_output_http_request_interactions
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import (
    MethodRef,
    build_call_site_grouping,
    call_site_argument_exprs,
)
from gerbil.analysis.schema import (
    AssertionRole,
    HttpRequestRole,
    HttpResponseRole,
    LifecyclePhase,
)
from tests.cldk_factories import (
    annotate_node_http,
    build_runtime_receiver_resolver_for_testing,
    classify_runtime_view_for_testing,
    make_call_site,
    make_callable,
)


def _runtime_view(call_sites, phase: LifecyclePhase) -> TestRuntimeView:
    method_ref = MethodRef(
        defining_class_name="example.ApiTest", method_signature="testCase()"
    )
    return TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=phase,
                method_ref=method_ref,
                context_class_name=method_ref.defining_class_name,
                grouping=build_call_site_grouping(call_sites),
                method_details=make_callable(call_sites=call_sites),
            )
        ]
    )


@pytest.mark.parametrize(
    "argument",
    [
        "500",
        '"https://storage.example.com/a.vsix"',
        '"/api/**"',
        '"/*"',
        "'/'",
        '"""\n    // text block\n    """',
        'baseUrlOfSut + "/api/alert"',
        "user -> {\n    // inner\n    return user;\n}",
        '"a " + //\n"b"',
    ],
)
def test_arguments_without_a_leading_comment_are_unchanged(argument: str) -> None:
    call_site = make_call_site(method_name="m", argument_expr=[argument])

    assert call_site_argument_exprs(call_site) == [argument]


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        ("// org/x/Foo_58_bar\n500", "500"),
        ('// admin\n"super_user"', '"super_user"'),
        ("//\nisEqualTo(project)", "isEqualTo(project)"),
        ("//   trailing spaces\n4", "4"),
        ("// first\n// second\nHttpStatus.OK", "HttpStatus.OK"),
        ("/* ApplicationType */\nnull", "null"),
        ("/* a\n       b */\ny", "y"),
        ("/**\n * doc\n */\n404", "404"),
        ("/**/\nw", "w"),
        ("/* q **/\nv", "v"),
        ("/* see http://x */\n200", "200"),
        ("// ends with */\n201", "201"),
        ("// c\r\n204", "204"),
        ('// c\n"a " + //\n"b"', '"a " + //\n"b"'),
    ],
)
def test_leading_comments_are_stripped(argument: str, expected: str) -> None:
    call_site = make_call_site(method_name="m", argument_expr=[argument])

    assert call_site_argument_exprs(call_site) == [expected]


def test_each_argument_is_stripped_independently() -> None:
    call_site = make_call_site(
        method_name="header",
        argument_expr=['"Authorization"', "// admin\nauth_admin"],
    )

    assert call_site_argument_exprs(call_site) == ['"Authorization"', "auth_admin"]


def test_status_code_with_trailing_source_comment_resolves() -> None:
    call_sites = [
        make_call_site(
            method_name="andExpect",
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=40,
        ),
        make_call_site(
            method_name="statusCode",
            argument_expr=[
                "// org/apache/hertzbeat/manager/service/ai/factory/"
                "AiServiceFactoryImpl_58_getAiServiceImplBean\n500"
            ],
            start_line=1,
            start_column=5,
            end_line=1,
            end_column=20,
        ),
    ]
    runtime_view = _runtime_view(call_sites, LifecyclePhase.TEST)
    for node in runtime_view.entries[0].grouping.nodes:
        if node.call_site.method_name == "statusCode":
            annotate_node_http(
                node,
                http_method="GET",
                path="/api",
                request_role=None,
                response_role=HttpResponseRole.STATUS_ASSERTION,
            )

    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    statuses = [
        (
            node.assertion_classification.status_code,
            node.assertion_classification.status_range,
        )
        for node in runtime_view.entries[0].grouping.nodes
        if node.assertion_classification is not None
        and node.assertion_classification.role == AssertionRole.STATUS
    ]
    assert statuses == [(500, "5xx")]


def test_rest_assured_request_inputs_with_comments_resolve() -> None:
    runtime_view = _runtime_view(
        [
            make_call_site(
                method_name="given",
                receiver_type="io.restassured.RestAssured",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=12,
            ),
            make_call_site(
                method_name="queryParam",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['// filter\n"q"', '"abc"'],
                start_line=1,
                start_column=5,
                end_line=2,
                end_column=33,
            ),
            make_call_site(
                method_name="header",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['/* scenario */\n"X-Token"', "// admin\ntoken"],
                start_line=1,
                start_column=5,
                end_line=3,
                end_column=38,
            ),
            make_call_site(
                method_name="post",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['// target\n"/upload/{id}"'],
                start_line=1,
                start_column=5,
                end_line=4,
                end_column=32,
            ),
        ],
        LifecyclePhase.SETUP,
    )

    classify_runtime_view_for_testing(runtime_view)
    interactions = build_output_http_request_interactions(runtime_view=runtime_view)

    event_calls = [
        interaction.http_call
        for interaction in interactions
        if interaction.http_call is not None
        and interaction.http_call.request_role == HttpRequestRole.EVENT
    ]
    assert len(event_calls) == 1
    event_call = event_calls[0]
    assert event_call.http_method == "POST"
    assert event_call.path == "/upload/{id}"
    assert event_call.query_param_names == ["q"]
    assert event_call.header_names == ["x-token"]
    assert event_call.path_param_names == ["id"]
