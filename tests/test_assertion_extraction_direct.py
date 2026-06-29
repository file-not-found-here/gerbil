from __future__ import annotations

from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.schema import (
    AssertionRole,
    HttpResponseRole,
    LifecyclePhase,
)
from gerbil.analysis.assertion import classify_assertions_on_runtime_view
from tests.cldk_factories import (
    annotate_node_http,
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
)


def _classify_and_get_nodes(call_sites):
    method = make_callable(call_sites=call_sites)
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="testCase()",
                ),
                context_class_name="example.ApiTest",
                grouping=build_call_site_grouping(call_sites),
                method_details=method,
            )
        ]
    )
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )
    return runtime_view.entries[0].grouping.nodes


def test_wrapper_chain_both_nodes_classified() -> None:
    # assertThat(getStatusCode()).isEqualTo(200):
    # - getStatusCode is an argument child of assertThat, providing status subject hint
    # - assertThat is the inner receiver call, isEqualTo wraps it
    status_subject = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=17,
    )
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=18,
    )
    matcher = make_call_site(
        method_name="isEqualTo",
        argument_expr=["expected"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=35,
    )

    nodes = _classify_and_get_nodes([status_subject, assert_that, matcher])
    classified_names = [
        node.call_site.method_name
        for node in nodes
        if node.assertion_classification is not None
    ]
    assert "assertThat" in classified_names
    assert "isEqualTo" in classified_names


def test_standalone_matcher_not_classified() -> None:
    standalone_matcher = make_call_site(
        method_name="isEqualTo",
        argument_expr=["domainValue"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=15,
    )
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["actualValue"],
        start_line=10,
        start_column=20,
        end_line=10,
        end_column=50,
    )
    wrapped_matcher = make_call_site(
        method_name="isEqualTo",
        argument_expr=["expectedValue"],
        start_line=10,
        start_column=20,
        end_line=10,
        end_column=32,
    )

    nodes = _classify_and_get_nodes([standalone_matcher, assert_that, wrapped_matcher])
    standalone_node = next(
        node for node in nodes if node.call_site is standalone_matcher
    )
    assert standalone_node.assertion_classification is None


def test_assertion_context_root_andExpect() -> None:
    and_expect = make_call_site(
        method_name="andExpect",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )
    status = make_call_site(
        method_name="status",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=20,
    )
    is_ok = make_call_site(
        method_name="isOk",
        start_line=10,
        start_column=22,
        end_line=10,
        end_column=30,
    )

    method = make_callable(call_sites=[and_expect, status, is_ok])
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="testCase()",
                ),
                context_class_name="example.ApiTest",
                grouping=build_call_site_grouping([and_expect, status, is_ok]),
                method_details=method,
            )
        ]
    )
    # Add HTTP annotation for the isOk node (Tier 1)
    is_ok_node = next(
        n
        for n in runtime_view.entries[0].grouping.nodes
        if n.call_site.method_name == "isOk"
    )
    annotate_node_http(
        is_ok_node,
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )

    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )
    nodes = runtime_view.entries[0].grouping.nodes

    is_ok_result = next(n for n in nodes if n.call_site.method_name == "isOk")
    assert is_ok_result.assertion_classification is not None
    assert is_ok_result.assertion_classification.role == AssertionRole.STATUS


def test_exception_assertion_classified() -> None:
    assert_throws = make_call_site(
        method_name="assertThrows",
        argument_expr=["RuntimeException.class"],
        start_line=10,
    )

    nodes = _classify_and_get_nodes([assert_throws])
    node = nodes[0]
    assert node.assertion_classification is not None
    assert node.assertion_classification.role == AssertionRole.EXCEPTION
