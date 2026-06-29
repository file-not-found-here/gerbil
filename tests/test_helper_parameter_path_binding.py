"""Helper String parameters bind to caller-resolved arguments so dispatch paths
inside helper expansions resolve per call site."""

from __future__ import annotations

from gerbil.analysis.http.classification import classify_http_on_runtime_view
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import (
    CallSiteGrouping,
    HelperExpansion,
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.schema import LifecyclePhase
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
    make_callable_parameter,
    make_field,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis

_TEST_OWNER = MethodRef(
    defining_class_name="example.WidgetApiTest",
    method_signature="listsWidgets()",
)
_HELPER_OWNER = MethodRef(
    defining_class_name="example.RequestHelpers",
    method_signature="fetch(java.lang.String)",
)


def _rest_template_dispatch(*, line: int, path_expression: str = "url"):
    return make_call_site(
        method_name="getForEntity",
        receiver_type="org.springframework.web.client.RestTemplate",
        argument_expr=[path_expression, "String.class"],
        start_line=line,
        end_line=line,
        end_column=40,
    )


def _helper_call(*, line: int, argument_exprs: list[str], method_name: str = "fetch"):
    return make_call_site(
        method_name=method_name,
        argument_expr=argument_exprs,
        start_line=line,
        end_line=line,
        end_column=30,
    )


def _expanded_view(
    caller_grouping: CallSiteGrouping,
    *,
    analysis: FakeJavaAnalysis | None = None,
) -> TestRuntimeView:
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=_TEST_OWNER,
                context_class_name=_TEST_OWNER.defining_class_name,
                grouping=caller_grouping,
                method_details=make_callable(signature=_TEST_OWNER.method_signature),
            )
        ]
    )
    classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(
            runtime_view, analysis=analysis or _analysis_with_helper()
        ),
    )
    return runtime_view


def _analysis_with_helper(
    *,
    helper_parameters: list | None = None,
    classes: dict | None = None,
    helper_signature: str = _HELPER_OWNER.method_signature,
    helper_code: str = "{}",
) -> FakeJavaAnalysis:
    parameters = (
        helper_parameters
        if helper_parameters is not None
        else [make_callable_parameter(name="url", type_name="java.lang.String")]
    )
    return FakeJavaAnalysis(
        classes=classes or {},
        methods_by_class={
            _HELPER_OWNER.defining_class_name: {
                helper_signature: make_callable(
                    signature=helper_signature,
                    parameters=parameters,
                    code=helper_code,
                )
            }
        },
    )


def _expand(helper_node, helper_grouping, callee: MethodRef = _HELPER_OWNER) -> None:
    helper_node.resolved_helper = callee
    helper_node.helper_expansion = HelperExpansion(
        callee=callee, grouping=helper_grouping
    )


def test_helper_string_parameter_binds_caller_literal_to_dispatch_path() -> None:
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['"/api/widgets/list"'])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    _expanded_view(caller_grouping)

    dispatch_node = helper_grouping.nodes[0]
    assert dispatch_node.http_classification is not None
    assert dispatch_node.http_classification.path == "/api/widgets/list"
    assert dispatch_node.endpoint_candidate is not None
    assert dispatch_node.endpoint_candidate.path == "/api/widgets/list"
    assert dispatch_node.endpoint_candidate.path_truncated is False


def test_each_helper_call_site_binds_its_own_argument() -> None:
    first_helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    second_helper_grouping = build_call_site_grouping(
        [_rest_template_dispatch(line=10)]
    )
    caller_grouping = build_call_site_grouping(
        [
            _helper_call(line=1, argument_exprs=['"/api/widgets"']),
            _helper_call(line=2, argument_exprs=['"/api/orders"']),
        ]
    )
    nodes_by_line = {node.span.start.line: node for node in caller_grouping.nodes}
    _expand(nodes_by_line[1], first_helper_grouping)
    _expand(nodes_by_line[2], second_helper_grouping)

    _expanded_view(caller_grouping)

    assert first_helper_grouping.nodes[0].http_classification.path == "/api/widgets"
    assert second_helper_grouping.nodes[0].http_classification.path == "/api/orders"


def test_caller_constant_argument_resolves_before_binding() -> None:
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['BASE + "/items"'])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    analysis = _analysis_with_helper(
        classes={
            _TEST_OWNER.defining_class_name: make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["BASE"],
                        modifiers=["static", "final"],
                        variable_initializers={"BASE": '"/api/v2"'},
                    )
                ]
            )
        }
    )
    _expanded_view(caller_grouping, analysis=analysis)

    assert helper_grouping.nodes[0].http_classification.path == "/api/v2/items"


def test_binding_propagates_through_nested_helper_expansion() -> None:
    inner_owner = MethodRef(
        defining_class_name="example.RequestHelpers",
        method_signature="dispatch(java.lang.String)",
    )
    inner_grouping = build_call_site_grouping([_rest_template_dispatch(line=20)])
    # The outer helper forwards its own parameter to the inner helper.
    outer_grouping = build_call_site_grouping(
        [_helper_call(line=10, argument_exprs=["url"], method_name="dispatch")]
    )
    _expand(outer_grouping.nodes[0], inner_grouping, callee=inner_owner)
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['"/api/nested"'])]
    )
    _expand(caller_grouping.nodes[0], outer_grouping)

    analysis = FakeJavaAnalysis(
        methods_by_class={
            "example.RequestHelpers": {
                _HELPER_OWNER.method_signature: make_callable(
                    signature=_HELPER_OWNER.method_signature,
                    parameters=[
                        make_callable_parameter(
                            name="url", type_name="java.lang.String"
                        )
                    ],
                ),
                inner_owner.method_signature: make_callable(
                    signature=inner_owner.method_signature,
                    parameters=[
                        make_callable_parameter(
                            name="url", type_name="java.lang.String"
                        )
                    ],
                ),
            }
        },
    )
    _expanded_view(caller_grouping, analysis=analysis)

    assert inner_grouping.nodes[0].http_classification.path == "/api/nested"


def test_argument_parameter_arity_mismatch_binds_nothing() -> None:
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['"/api/widgets"', "headers"])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    _expanded_view(caller_grouping)

    dispatch_node = helper_grouping.nodes[0]
    assert dispatch_node.http_classification is not None
    assert dispatch_node.http_classification.path == ""


def test_unresolvable_argument_leaves_parameter_unbound() -> None:
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['baseUrl + "/list"'])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    _expanded_view(caller_grouping)

    assert helper_grouping.nodes[0].http_classification.path == ""


def test_non_string_parameter_is_ignored_while_string_parameter_binds() -> None:
    helper_signature = "fetch(java.lang.String, int)"
    helper_owner = MethodRef(
        defining_class_name=_HELPER_OWNER.defining_class_name,
        method_signature=helper_signature,
    )
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['"/api/widgets"', "42"])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping, callee=helper_owner)

    analysis = _analysis_with_helper(
        helper_parameters=[
            make_callable_parameter(name="url", type_name="java.lang.String"),
            make_callable_parameter(name="retries", type_name="int"),
        ],
        helper_signature=helper_signature,
    )
    _expanded_view(caller_grouping, analysis=analysis)

    assert helper_grouping.nodes[0].http_classification.path == "/api/widgets"


def test_bound_parameter_shadows_helper_class_constant() -> None:
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['"/from-argument"'])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    analysis = _analysis_with_helper(
        classes={
            _HELPER_OWNER.defining_class_name: make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["url"],
                        modifiers=["static", "final"],
                        variable_initializers={"url": '"/from-field"'},
                    )
                ]
            )
        }
    )
    _expanded_view(caller_grouping, analysis=analysis)

    assert helper_grouping.nodes[0].http_classification.path == "/from-argument"


def _analysis_with_helper_and_shadowed_field(**kwargs) -> FakeJavaAnalysis:
    return _analysis_with_helper(
        classes={
            _HELPER_OWNER.defining_class_name: make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["url"],
                        modifiers=["static", "final"],
                        variable_initializers={"url": '"/from-field"'},
                    )
                ]
            )
        },
        **kwargs,
    )


def test_unbound_parameter_still_shadows_helper_class_constant() -> None:
    # An unresolvable argument must poison the dispatch path, not fall through
    # to a same-named field the parameter shadows.
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=["dynamicValue()"])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    _expanded_view(caller_grouping, analysis=_analysis_with_helper_and_shadowed_field())

    assert helper_grouping.nodes[0].http_classification.path == ""


def test_arity_mismatch_still_shadows_helper_class_constant() -> None:
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['"/api/widgets"', "headers"])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    _expanded_view(caller_grouping, analysis=_analysis_with_helper_and_shadowed_field())

    assert helper_grouping.nodes[0].http_classification.path == ""


def test_non_string_parameter_still_shadows_helper_class_constant() -> None:
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=["request"])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    analysis = _analysis_with_helper_and_shadowed_field(
        helper_parameters=[
            make_callable_parameter(name="url", type_name="java.lang.Object")
        ],
    )
    _expanded_view(caller_grouping, analysis=analysis)

    assert helper_grouping.nodes[0].http_classification.path == ""


def test_reassigned_parameter_binds_nothing() -> None:
    # The helper body rewrites the parameter before dispatch, so the call-site
    # binding no longer holds at the dispatch.
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['"/api/items"'])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    analysis = _analysis_with_helper(
        helper_code='{ url = url + "/" + ID; return restTemplate.getForEntity(url, String.class); }'
    )
    _expanded_view(caller_grouping, analysis=analysis)

    assert helper_grouping.nodes[0].http_classification.path == ""


def test_compound_reassignment_binds_nothing() -> None:
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['"/api/items"'])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    analysis = _analysis_with_helper(
        helper_code="{ url += suffix; return restTemplate.getForEntity(url, String.class); }"
    )
    _expanded_view(caller_grouping, analysis=analysis)

    assert helper_grouping.nodes[0].http_classification.path == ""


def test_comparison_of_parameter_is_not_a_reassignment() -> None:
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['"/api/items"'])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    analysis = _analysis_with_helper(
        helper_code="{ if (url == null || url != cached) { log(url); } "
        "return restTemplate.getForEntity(url, String.class); }"
    )
    _expanded_view(caller_grouping, analysis=analysis)

    assert helper_grouping.nodes[0].http_classification.path == "/api/items"


def test_field_write_of_same_name_is_not_a_reassignment() -> None:
    # `this.url = url` writes the field; the parameter keeps its call-site
    # binding for the whole body.
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['"/api/items"'])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    analysis = _analysis_with_helper(
        helper_code="{ this.url = url; "
        "return restTemplate.getForEntity(url, String.class); }"
    )
    _expanded_view(caller_grouping, analysis=analysis)

    assert helper_grouping.nodes[0].http_classification.path == "/api/items"


def test_assignment_text_inside_string_literal_is_not_a_reassignment() -> None:
    caller_grouping = build_call_site_grouping(
        [_helper_call(line=1, argument_exprs=['"/api/items"'])]
    )
    helper_grouping = build_call_site_grouping([_rest_template_dispatch(line=10)])
    _expand(caller_grouping.nodes[0], helper_grouping)

    analysis = _analysis_with_helper(
        helper_code='{ log("?url=" + token); '
        "return restTemplate.getForEntity(url, String.class); }"
    )
    _expanded_view(caller_grouping, analysis=analysis)

    assert helper_grouping.nodes[0].http_classification.path == "/api/items"
