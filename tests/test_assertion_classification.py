"""Tests for the node-level assertion classification pass."""

from __future__ import annotations

from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.schema import (
    AssertionNodeKind,
    AssertionRole,
    HttpResponseRole,
    LifecyclePhase,
)
from gerbil.analysis.assertion import classify_assertions_on_runtime_view
from gerbil.analysis.assertion.classification import (
    _HTTPSTATUS_CONSTANT_CODES,
    _extract_status_code_from_arguments,
)
from tests.cldk_factories import (
    annotate_node_http,
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
    make_variable_declaration,
)


def _classify_nodes(call_sites, *, annotate_http=None, variable_declarations=None):
    method = make_callable(
        call_sites=call_sites,
        variable_declarations=variable_declarations,
    )
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
    if annotate_http is not None:
        annotate_http(runtime_view.entries[0].grouping.nodes)

    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )
    return runtime_view.entries[0].grouping.nodes


# ── Tier 1: HttpResponseRole present ──────────────────────────────


def test_tier1_status_assertion_role() -> None:
    call_site = make_call_site(method_name="isOk", start_line=10)
    nodes = _classify_nodes(
        [call_site],
        annotate_http=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        ),
    )
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.STATUS
    assert ac.status_code == 200
    assert ac.status_range == "2xx"


def test_tier1_body_assertion_role() -> None:
    call_site = make_call_site(method_name="body", start_line=10)
    nodes = _classify_nodes(
        [call_site],
        annotate_http=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.BODY_ASSERTION,
        ),
    )
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.BODY


def test_tier1_matcher_role_is_not_classified() -> None:
    call_site = make_call_site(method_name="jsonPath", start_line=10)
    nodes = _classify_nodes(
        [call_site],
        annotate_http=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.MATCHER,
        ),
    )
    assert nodes[0].assertion_classification is None


def test_tier1_matcher_does_not_inflate_assertion_count() -> None:
    """A MATCHER node alongside a STATUS_ASSERTION should not get classified."""
    status_site = make_call_site(method_name="isOk", start_line=10)
    matcher_site = make_call_site(method_name="equalTo", start_line=11)

    def _annotate(ns):
        annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        )
        annotate_node_http(
            ns[1],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.MATCHER,
        )

    nodes = _classify_nodes([status_site, matcher_site], annotate_http=_annotate)
    status_node = next(n for n in nodes if n.call_site.method_name == "isOk")
    matcher_node = next(n for n in nodes if n.call_site.method_name == "equalTo")
    assert status_node.assertion_classification is not None
    assert status_node.assertion_classification.role == AssertionRole.STATUS
    assert matcher_node.assertion_classification is None


def test_tier1_inspector_not_classified() -> None:
    call_site = make_call_site(method_name="getStatus", start_line=10)
    nodes = _classify_nodes(
        [call_site],
        annotate_http=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.INSPECTOR,
        ),
    )
    assert nodes[0].assertion_classification is None


def test_tier1_extractor_not_classified() -> None:
    call_site = make_call_site(method_name="extract", start_line=10)
    nodes = _classify_nodes(
        [call_site],
        annotate_http=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.EXTRACTOR,
        ),
    )
    assert nodes[0].assertion_classification is None


# ── Tier 2: No HttpResponseRole ───────────────────────────────────


def test_tier2_assertThrows_exception() -> None:
    call_site = make_call_site(
        method_name="assertThrows",
        argument_expr=["RuntimeException.class"],
        start_line=10,
    )
    nodes = _classify_nodes([call_site])
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.EXCEPTION


def test_tier2_assertThrowsExactly_exception() -> None:
    call_site = make_call_site(
        method_name="assertThrowsExactly",
        argument_expr=["RuntimeException.class"],
        start_line=10,
    )
    nodes = _classify_nodes([call_site])
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.EXCEPTION


def test_tier2_expectThrows_exception() -> None:
    call_site = make_call_site(
        method_name="expectThrows",
        argument_expr=["RuntimeException.class"],
        start_line=10,
    )
    nodes = _classify_nodes([call_site])
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.EXCEPTION


def test_tier2_assertThatThrownBy_exception() -> None:
    call_site = make_call_site(
        method_name="assertThatThrownBy",
        start_line=10,
    )
    nodes = _classify_nodes([call_site])
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.EXCEPTION


def test_tier2_assertThatExceptionOfType_exception() -> None:
    call_site = make_call_site(
        method_name="assertThatExceptionOfType",
        start_line=10,
    )
    nodes = _classify_nodes([call_site])
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.EXCEPTION


def test_tier2_assertThat_with_status_subject() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )
    get_status_code = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=30,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=["200"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=55,
    )

    nodes = _classify_nodes([assert_that, get_status_code, is_equal_to])
    matcher_node = next(n for n in nodes if n.call_site.method_name == "isEqualTo")
    assert matcher_node.assertion_classification is not None
    assert matcher_node.assertion_classification.role == AssertionRole.STATUS


def test_tier2_assertThat_status_chain_marks_wrapper_subject_and_verifier() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=30,
    )
    get_status_code = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=27,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=["404"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=45,
    )

    nodes = _classify_nodes([assert_that, get_status_code, is_equal_to])
    assert_that_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    subject_node = next(n for n in nodes if n.call_site.method_name == "getStatusCode")
    verifier_node = next(n for n in nodes if n.call_site.method_name == "isEqualTo")

    assert assert_that_node.assertion_classification is not None
    assert assert_that_node.assertion_classification.role == AssertionRole.STATUS
    assert (
        assert_that_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    )
    assert assert_that_node.assertion_classification.is_countable is False

    assert subject_node.assertion_classification is not None
    assert subject_node.assertion_classification.node_kind == AssertionNodeKind.SUBJECT
    assert subject_node.assertion_classification.is_countable is False

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert (
        verifier_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    )
    assert verifier_node.assertion_classification.is_countable is True
    assert verifier_node.assertion_classification.status_code == 404
    assert verifier_node.assertion_classification.status_range == "4xx"


def test_tier2_assertThat_status_chain_negated_equality_drops_code() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=30,
    )
    get_status_code = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=27,
    )
    is_not_equal_to = make_call_site(
        method_name="isNotEqualTo",
        argument_expr=["404"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=48,
    )

    nodes = _classify_nodes([assert_that, get_status_code, is_not_equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    verifier_node = next(n for n in nodes if n.call_site.method_name == "isNotEqualTo")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.STATUS
    assert root_node.assertion_classification.status_code is None
    assert root_node.assertion_classification.status_range is None

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert (
        verifier_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    )
    assert verifier_node.assertion_classification.is_countable is True
    assert verifier_node.assertion_classification.status_code is None
    assert verifier_node.assertion_classification.status_range is None


def test_tier2_assertThat_status_chain_marks_intermediate_links_as_wrappers() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=30,
    )
    get_status_code = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=27,
    )
    description = make_call_site(
        method_name="as",
        argument_expr=['"status code"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=48,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=["404"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=63,
    )

    nodes = _classify_nodes([assert_that, get_status_code, description, is_equal_to])
    description_node = next(n for n in nodes if n.call_site.method_name == "as")
    verifier_node = next(n for n in nodes if n.call_site.method_name == "isEqualTo")

    assert description_node.assertion_classification is not None
    assert description_node.assertion_classification.role == AssertionRole.STATUS
    assert (
        description_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    )
    assert description_node.assertion_classification.is_countable is False

    assert verifier_node.assertion_classification is not None
    assert (
        verifier_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    )
    assert verifier_node.assertion_classification.status_code == 404


def test_tier2_assertj_has_status_ok_counts_as_status_verifier() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=['mvc.perform(get("/"))'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=35,
    )
    has_status_ok = make_call_site(
        method_name="hasStatusOk",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=49,
    )

    nodes = _classify_nodes([assert_that, has_status_ok])
    verifier_node = next(n for n in nodes if n.call_site.method_name == "hasStatusOk")

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert (
        verifier_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    )
    assert verifier_node.assertion_classification.status_code == 200
    assert verifier_node.assertion_classification.status_range == "2xx"


def test_tier2_assertj_has_status_parses_http_status_argument() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["mvc.perform(patch(uri))"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=35,
    )
    has_status = make_call_site(
        method_name="hasStatus",
        argument_expr=["HttpStatus.PRECONDITION_FAILED"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=72,
    )

    nodes = _classify_nodes([assert_that, has_status])
    verifier_node = next(n for n in nodes if n.call_site.method_name == "hasStatus")

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert verifier_node.assertion_classification.status_code == 412
    assert verifier_node.assertion_classification.status_range == "4xx"


def test_tier2_assertj_has_status_range_counts_as_status_verifier() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["result"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=20,
    )
    has_status_4xx = make_call_site(
        method_name="hasStatus4xxClientError",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=47,
    )

    nodes = _classify_nodes([assert_that, has_status_4xx])
    verifier_node = next(
        n for n in nodes if n.call_site.method_name == "hasStatus4xxClientError"
    )

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert verifier_node.assertion_classification.status_code is None
    assert verifier_node.assertion_classification.status_range == "4xx"


def test_tier2_assertj_has_status_1xx_range_counts_as_status_verifier() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["result"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=20,
    )
    has_status_1xx = make_call_site(
        method_name="hasStatus1xxInformational",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=52,
    )

    nodes = _classify_nodes([assert_that, has_status_1xx])
    verifier_node = next(
        n for n in nodes if n.call_site.method_name == "hasStatus1xxInformational"
    )

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert verifier_node.assertion_classification.status_code is None
    assert verifier_node.assertion_classification.status_range == "1xx"


def test_tier2_assertj_does_not_infer_generated_named_status_aliases() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["result"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=20,
    )
    has_status_created = make_call_site(
        method_name="hasStatusCreated",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=45,
    )

    nodes = _classify_nodes([assert_that, has_status_created])
    verifier_node = next(
        n for n in nodes if n.call_site.method_name == "hasStatusCreated"
    )

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role != AssertionRole.STATUS


def test_tier2_assertThat_terminal_description_remains_wrapper() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["response"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=22,
    )
    description = make_call_site(
        method_name="as",
        argument_expr=['"response"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=38,
    )

    nodes = _classify_nodes([assert_that, description])
    assert_that_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    description_node = next(n for n in nodes if n.call_site.method_name == "as")

    assert assert_that_node.assertion_classification is not None
    assert (
        assert_that_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    )
    assert assert_that_node.assertion_classification.is_countable is False

    assert description_node.assertion_classification is not None
    assert (
        description_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    )
    assert description_node.assertion_classification.is_countable is False


def test_tier2_assertWithMessage_chain_counts_single_terminal_verifier() -> None:
    assert_with_message = make_call_site(
        method_name="assertWithMessage",
        argument_expr=['"m"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=25,
    )
    that = make_call_site(
        method_name="that",
        argument_expr=["user"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=37,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=["expected"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=58,
    )

    nodes = _classify_nodes([assert_with_message, that, is_equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertWithMessage")
    countable_nodes = [
        n
        for n in nodes
        if n.assertion_classification is not None
        and n.assertion_classification.is_countable
    ]

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert root_node.assertion_classification.is_countable is False

    assert len(countable_nodes) == 1
    assert countable_nodes[0].call_site.method_name == "isEqualTo"


def test_tier2_assertThatCode_chain_counts_single_terminal_verifier() -> None:
    assert_that_code = make_call_site(
        method_name="assertThatCode",
        argument_expr=["() -> service.run()"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=35,
    )
    does_not_throw = make_call_site(
        method_name="doesNotThrowAnyException",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=62,
    )

    nodes = _classify_nodes([assert_that_code, does_not_throw])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThatCode")
    countable_nodes = [
        n
        for n in nodes
        if n.assertion_classification is not None
        and n.assertion_classification.is_countable
    ]

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.is_countable is False

    assert len(countable_nodes) == 1
    assert countable_nodes[0].call_site.method_name == "doesNotThrowAnyException"


def test_assertj_bdd_then_value_chain_counts_terminal_verifier() -> None:
    # AssertJ BDD `then(x).isEqualTo(y)` (org.assertj.core.api.BDDAssertions) is
    # the verb-shifted `assertThat(x).isEqualTo(y)` and must count identically.
    then = make_call_site(
        method_name="then",
        receiver_type="org.assertj.core.api.BDDAssertions",
        argument_expr=["value"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=25,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=["expected"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=45,
    )

    nodes = _classify_nodes([then, is_equal_to])
    then_node = next(n for n in nodes if n.call_site.method_name == "then")
    countable_nodes = [
        n
        for n in nodes
        if n.assertion_classification is not None
        and n.assertion_classification.is_countable
    ]

    assert then_node.assertion_classification is not None
    assert then_node.assertion_classification.is_countable is False
    assert len(countable_nodes) == 1
    assert countable_nodes[0].call_site.method_name == "isEqualTo"


def test_assertj_bdd_then_thrown_by_is_exception_root() -> None:
    then_thrown_by = make_call_site(
        method_name="thenThrownBy",
        receiver_type="org.assertj.core.api.BDDAssertions",
        argument_expr=["() -> client.get()"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=45,
    )

    nodes = _classify_nodes([then_thrown_by])
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.EXCEPTION


def test_bdd_then_with_non_assertj_receiver_is_not_an_assertion() -> None:
    # Mockito's BDDMockito.then(mock).should() is a verification, not an oracle;
    # the receiver gate keeps the overloaded `then` name from being counted.
    then = make_call_site(
        method_name="then",
        receiver_type="org.mockito.BDDMockito",
        argument_expr=["mock"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=20,
    )
    should = make_call_site(
        method_name="should",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=32,
    )

    nodes = _classify_nodes([then, should])
    assert all(n.assertion_classification is None for n in nodes)


def test_tier2_mockmvc_status_chain_classifies_verifier_without_receiver_type() -> None:
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
        start_column=16,
        end_line=10,
        end_column=23,
    )
    is_ok = make_call_site(
        method_name="isOk",
        receiver_expr="status()",
        start_line=10,
        start_column=16,
        end_line=10,
        end_column=30,
    )

    nodes = _classify_nodes([and_expect, status, is_ok])
    status_node = next(n for n in nodes if n.call_site.method_name == "status")
    verifier_node = next(n for n in nodes if n.call_site.method_name == "isOk")

    assert status_node.assertion_classification is not None
    assert status_node.assertion_classification.node_kind == AssertionNodeKind.SUBJECT
    assert status_node.assertion_classification.is_countable is False

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert (
        verifier_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    )
    assert verifier_node.assertion_classification.status_code == 200
    assert verifier_node.assertion_classification.status_range == "2xx"


def test_tier2_mockmvc_status_chain_supports_expanded_named_statuses() -> None:
    and_expect = make_call_site(
        method_name="andExpect",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=50,
    )
    status = make_call_site(
        method_name="status",
        start_line=10,
        start_column=16,
        end_line=10,
        end_column=23,
    )
    is_precondition_failed = make_call_site(
        method_name="isPreconditionFailed",
        receiver_expr="status()",
        start_line=10,
        start_column=16,
        end_line=10,
        end_column=43,
    )

    nodes = _classify_nodes([and_expect, status, is_precondition_failed])
    verifier_node = next(
        n for n in nodes if n.call_site.method_name == "isPreconditionFailed"
    )

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert verifier_node.assertion_classification.status_code == 412
    assert verifier_node.assertion_classification.status_range == "4xx"


def test_tier2_mockmvc_status_chain_supports_too_many_requests() -> None:
    and_expect = make_call_site(
        method_name="andExpect",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=50,
    )
    status = make_call_site(
        method_name="status",
        start_line=10,
        start_column=16,
        end_line=10,
        end_column=23,
    )
    is_too_many_requests = make_call_site(
        method_name="isTooManyRequests",
        receiver_expr="status()",
        start_line=10,
        start_column=16,
        end_line=10,
        end_column=43,
    )

    nodes = _classify_nodes([and_expect, status, is_too_many_requests])
    verifier_node = next(
        n for n in nodes if n.call_site.method_name == "isTooManyRequests"
    )

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert verifier_node.assertion_classification.status_code == 429
    assert verifier_node.assertion_classification.status_range == "4xx"


def test_tier2_webtestclient_expect_status_chain_classifies_named_status() -> None:
    expect_status = make_call_site(
        method_name="expectStatus",
        start_line=10,
        start_column=9,
        end_line=10,
        end_column=31,
    )
    is_bad_request = make_call_site(
        method_name="isBadRequest",
        receiver_expr="exchange().expectStatus()",
        start_line=10,
        start_column=9,
        end_line=10,
        end_column=46,
    )

    nodes = _classify_nodes([expect_status, is_bad_request])
    subject_node = next(n for n in nodes if n.call_site.method_name == "expectStatus")
    verifier_node = next(n for n in nodes if n.call_site.method_name == "isBadRequest")

    assert subject_node.assertion_classification is not None
    assert subject_node.assertion_classification.node_kind == AssertionNodeKind.SUBJECT
    assert subject_node.assertion_classification.is_countable is False

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert verifier_node.assertion_classification.status_code == 400
    assert verifier_node.assertion_classification.status_range == "4xx"


def test_tier2_webtestclient_expect_status_chain_parses_http_status_argument() -> None:
    expect_status = make_call_site(
        method_name="expectStatus",
        start_line=10,
        start_column=9,
        end_line=10,
        end_column=31,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=["HttpStatus.CONFLICT"],
        receiver_expr="exchange().expectStatus()",
        start_line=10,
        start_column=9,
        end_line=10,
        end_column=62,
    )
    expect_header = make_call_site(
        method_name="expectHeader",
        receiver_expr="exchange().expectStatus().isEqualTo(HttpStatus.CONFLICT)",
        start_line=10,
        start_column=9,
        end_line=10,
        end_column=77,
    )

    nodes = _classify_nodes([expect_status, is_equal_to, expect_header])
    verifier_node = next(n for n in nodes if n.call_site.method_name == "isEqualTo")
    header_node = next(n for n in nodes if n.call_site.method_name == "expectHeader")

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert verifier_node.assertion_classification.status_code == 409
    assert verifier_node.assertion_classification.status_range == "4xx"
    assert header_node.assertion_classification is None


def test_tier2_webtestclient_expect_status_does_not_leak_into_body_matcher() -> None:
    expect_status = make_call_site(
        method_name="expectStatus",
        start_line=10,
        start_column=9,
        end_line=10,
        end_column=31,
    )
    expect_body = make_call_site(
        method_name="expectBody",
        receiver_expr="exchange().expectStatus()",
        start_line=10,
        start_column=9,
        end_line=10,
        end_column=44,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=['"body"'],
        receiver_expr="exchange().expectStatus().expectBody()",
        start_line=10,
        start_column=9,
        end_line=10,
        end_column=63,
    )

    nodes = _classify_nodes([expect_status, expect_body, is_equal_to])
    body_matcher_node = next(n for n in nodes if n.call_site.method_name == "isEqualTo")

    assert body_matcher_node.assertion_classification is None


def test_tier2_assertTrue_status_predicate_counts_child_not_root() -> None:
    assert_true = make_call_site(
        method_name="assertTrue",
        argument_expr=["response.getStatus().isFound()"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=46,
    )
    is_found = make_call_site(
        method_name="isFound",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=43,
    )
    get_status = make_call_site(
        method_name="getStatus",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=32,
    )

    nodes = _classify_nodes([assert_true, is_found, get_status])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertTrue")
    predicate_node = next(n for n in nodes if n.call_site.method_name == "isFound")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.STATUS
    assert root_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert root_node.assertion_classification.is_countable is False
    assert root_node.assertion_classification.status_code == 302
    assert root_node.assertion_classification.status_range == "3xx"

    assert predicate_node.assertion_classification is not None
    assert predicate_node.assertion_classification.role == AssertionRole.STATUS
    assert (
        predicate_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    )
    assert predicate_node.assertion_classification.is_countable is True
    assert predicate_node.assertion_classification.status_code == 302


def test_tier2_assertTrue_status_predicate_without_status_subject_is_general() -> None:
    assert_true = make_call_site(
        method_name="assertTrue",
        argument_expr=["result.isFound()"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=34,
    )
    is_found = make_call_site(
        method_name="isFound",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=30,
    )

    nodes = _classify_nodes([assert_true, is_found])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertTrue")
    predicate_node = next(n for n in nodes if n.call_site.method_name == "isFound")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.GENERAL

    assert predicate_node.assertion_classification is not None
    assert predicate_node.assertion_classification.is_countable is False


def test_tier2_assertTrue_domain_status_method_chain_is_not_countable_status() -> None:
    assert_true = make_call_site(
        method_name="assertTrue",
        argument_expr=["domain.status().isNotFound()"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=45,
    )
    status = make_call_site(
        method_name="status",
        receiver_expr="domain",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=27,
    )
    is_not_found = make_call_site(
        method_name="isNotFound",
        receiver_expr="domain.status()",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=40,
    )

    nodes = _classify_nodes([assert_true, status, is_not_found])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertTrue")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.GENERAL
    assert not any(
        node.assertion_classification is not None
        and node.assertion_classification.role == AssertionRole.STATUS
        and node.assertion_classification.is_countable
        for node in nodes
    )


def test_tier2_assertTrue_typed_status_receiver_counts_child_not_root() -> None:
    assert_true = make_call_site(
        method_name="assertTrue",
        argument_expr=["status.isFound()"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=34,
    )
    is_found = make_call_site(
        method_name="isFound",
        receiver_expr="status",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=30,
    )

    nodes = _classify_nodes(
        [assert_true, is_found],
        variable_declarations=[
            make_variable_declaration(
                name="status",
                type_name=(
                    "org.springframework.test.web.reactive.server.StatusAssertions"
                ),
                start_line=5,
            )
        ],
    )
    root_node = next(n for n in nodes if n.call_site.method_name == "assertTrue")
    predicate_node = next(n for n in nodes if n.call_site.method_name == "isFound")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.STATUS
    assert root_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert root_node.assertion_classification.is_countable is False

    assert predicate_node.assertion_classification is not None
    assert predicate_node.assertion_classification.role == AssertionRole.STATUS
    assert (
        predicate_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    )
    assert predicate_node.assertion_classification.status_code == 302


def test_tier2_assertTrue_domain_typed_status_receiver_is_general() -> None:
    assert_true = make_call_site(
        method_name="assertTrue",
        argument_expr=["status.isFound()"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=34,
    )
    is_found = make_call_site(
        method_name="isFound",
        receiver_expr="status",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=30,
    )

    nodes = _classify_nodes(
        [assert_true, is_found],
        variable_declarations=[
            make_variable_declaration(
                name="status",
                type_name="example.OrderStatus",
                start_line=5,
            )
        ],
    )
    root_node = next(n for n in nodes if n.call_site.method_name == "assertTrue")
    predicate_node = next(n for n in nodes if n.call_site.method_name == "isFound")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.GENERAL

    assert predicate_node.assertion_classification is not None
    assert predicate_node.assertion_classification.is_countable is False


def test_tier2_assertTrue_body_predicate_counts_child_not_root() -> None:
    assert_true = make_call_site(
        method_name="assertTrue",
        argument_expr=['response.getBody().contains("ok")'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=60,
    )
    contains = make_call_site(
        method_name="contains",
        argument_expr=['"ok"'],
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=50,
    )
    get_body = make_call_site(
        method_name="getBody",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=27,
    )

    nodes = _classify_nodes([assert_true, contains, get_body])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertTrue")
    predicate_node = next(n for n in nodes if n.call_site.method_name == "contains")
    subject_node = next(n for n in nodes if n.call_site.method_name == "getBody")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.BODY
    assert root_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert root_node.assertion_classification.is_countable is False

    assert predicate_node.assertion_classification is not None
    assert predicate_node.assertion_classification.role == AssertionRole.BODY
    assert (
        predicate_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    )
    assert predicate_node.assertion_classification.is_countable is True

    assert subject_node.assertion_classification is not None
    assert subject_node.assertion_classification.node_kind == AssertionNodeKind.SUBJECT
    assert subject_node.assertion_classification.is_countable is False


def test_tier2_assertThat_hamcrest_body_matcher_counts_matcher_argument() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=55,
    )
    get_body = make_call_site(
        method_name="getBody",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=28,
    )
    contains_string = make_call_site(
        method_name="containsString",
        argument_expr=['"ok"'],
        start_line=10,
        start_column=31,
        end_line=10,
        end_column=52,
    )

    nodes = _classify_nodes([assert_that, get_body, contains_string])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    matcher_node = next(n for n in nodes if n.call_site.method_name == "containsString")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.BODY
    assert root_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert root_node.assertion_classification.is_countable is False

    assert matcher_node.assertion_classification is not None
    assert matcher_node.assertion_classification.role == AssertionRole.BODY
    assert matcher_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    assert matcher_node.assertion_classification.is_countable is True


def test_tier2_assertThat_hamcrest_header_matcher_counts_generic_matcher() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=70,
    )
    get_header = make_call_site(
        method_name="getHeader",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=35,
    )
    equal_to = make_call_site(
        method_name="equalTo",
        argument_expr=['"application/json"'],
        start_line=10,
        start_column=38,
        end_line=10,
        end_column=68,
    )

    nodes = _classify_nodes([assert_that, get_header, equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    matcher_node = next(n for n in nodes if n.call_site.method_name == "equalTo")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.HEADER
    assert root_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert root_node.assertion_classification.is_countable is False

    assert matcher_node.assertion_classification is not None
    assert matcher_node.assertion_classification.role == AssertionRole.HEADER
    assert matcher_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    assert matcher_node.assertion_classification.is_countable is True


def test_tier2_assertThat_hamcrest_status_matcher_counts_generic_matcher() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=55,
    )
    get_status_code = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=28,
    )
    equal_to = make_call_site(
        method_name="equalTo",
        argument_expr=["404"],
        start_line=10,
        start_column=31,
        end_line=10,
        end_column=52,
    )

    nodes = _classify_nodes([assert_that, get_status_code, equal_to])
    matcher_node = next(n for n in nodes if n.call_site.method_name == "equalTo")

    assert matcher_node.assertion_classification is not None
    assert matcher_node.assertion_classification.role == AssertionRole.STATUS
    assert matcher_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    assert matcher_node.assertion_classification.is_countable is True
    assert matcher_node.assertion_classification.status_code == 404
    assert matcher_node.assertion_classification.status_range == "4xx"


def test_tier2_assertThat_hamcrest_negated_status_matcher_drops_code() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=60,
    )
    get_status = make_call_site(
        method_name="getStatus",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=28,
    )
    not_matcher = make_call_site(
        method_name="not",
        start_line=10,
        start_column=31,
        end_line=10,
        end_column=58,
    )
    equal_to = make_call_site(
        method_name="equalTo",
        argument_expr=["404"],
        start_line=10,
        start_column=35,
        end_line=10,
        end_column=56,
    )

    nodes = _classify_nodes([assert_that, get_status, not_matcher, equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    promoted_node = next(n for n in nodes if n.call_site.method_name == "equalTo")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.STATUS
    assert root_node.assertion_classification.status_code is None

    assert promoted_node.assertion_classification is not None
    assert promoted_node.assertion_classification.role == AssertionRole.STATUS
    assert (
        promoted_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    )
    assert promoted_node.assertion_classification.is_countable is True
    assert promoted_node.assertion_classification.status_code is None
    assert promoted_node.assertion_classification.status_range is None


def test_tier2_assertThat_with_body_subject() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )
    get_body = make_call_site(
        method_name="getBody",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=25,
    )
    contains = make_call_site(
        method_name="contains",
        argument_expr=['"data"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=55,
    )

    nodes = _classify_nodes([assert_that, get_body, contains])
    matcher_node = next(n for n in nodes if n.call_site.method_name == "contains")
    assert matcher_node.assertion_classification is not None
    assert matcher_node.assertion_classification.role == AssertionRole.BODY


def test_tier2_assertEquals_without_subject_gets_general() -> None:
    call_site = make_call_site(
        method_name="assertEquals",
        argument_expr=["expected", "actual"],
        start_line=10,
    )
    nodes = _classify_nodes([call_site])
    # assertEquals is an assertion root; with no status/body subject hints it gets GENERAL
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.GENERAL


def test_tier2_statusCode_in_assertion_context() -> None:
    and_expect = make_call_site(
        method_name="andExpect",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )
    status_code = make_call_site(
        method_name="statusCode",
        argument_expr=["200"],
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=30,
    )

    nodes = _classify_nodes(
        [and_expect, status_code],
        annotate_http=lambda ns: annotate_node_http(
            ns[1],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        ),
    )
    sc_node = next(n for n in nodes if n.call_site.method_name == "statusCode")
    assert sc_node.assertion_classification is not None
    assert sc_node.assertion_classification.role == AssertionRole.STATUS


def test_tier1_isNotFound_status_code() -> None:
    call_site = make_call_site(method_name="isNotFound", start_line=10)
    nodes = _classify_nodes(
        [call_site],
        annotate_http=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        ),
    )
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.status_code == 404
    assert ac.status_range == "4xx"


def test_tier1_isGone_status_code() -> None:
    call_site = make_call_site(method_name="isGone", start_line=10)
    nodes = _classify_nodes(
        [call_site],
        annotate_http=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        ),
    )
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.status_code == 410
    assert ac.status_range == "4xx"


def test_tier1_isBadGateway_status_code() -> None:
    call_site = make_call_site(method_name="isBadGateway", start_line=10)
    nodes = _classify_nodes(
        [call_site],
        annotate_http=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        ),
    )
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.status_code == 502
    assert ac.status_range == "5xx"


def test_helper_expansion_classification_recurses() -> None:
    """Classification recurses into helper expansions."""
    # Use the public API with a runtime view that has helper expansion
    is_ok = make_call_site(method_name="isOk", start_line=5)
    helper_ref = MethodRef(
        defining_class_name="example.Helper",
        method_signature="checkStatus()",
    )

    # Build the test entry with a helper call that expands
    helper_call = make_call_site(method_name="checkStatus", start_line=10)
    test_method = make_callable(call_sites=[helper_call])

    from gerbil.analysis.runtime.call_sites import HelperExpansion

    grouping = build_call_site_grouping([helper_call])
    helper_grouping = build_call_site_grouping([is_ok])

    # Manually attach helper expansion
    grouping.nodes[0].resolved_helper = helper_ref
    grouping.nodes[0].helper_expansion = HelperExpansion(
        callee=helper_ref,
        grouping=helper_grouping,
    )

    # Annotate the isOk node with HTTP response role
    annotate_node_http(
        helper_grouping.nodes[0],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )

    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="testCase()",
                ),
                context_class_name="example.ApiTest",
                grouping=grouping,
                method_details=test_method,
            )
        ]
    )

    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    # The isOk node inside the helper expansion should be classified
    is_ok_node = helper_grouping.nodes[0]
    assert is_ok_node.assertion_classification is not None
    assert is_ok_node.assertion_classification.role == AssertionRole.STATUS
    assert is_ok_node.assertion_classification.status_code == 200
    assert is_ok_node.assertion_classification.status_range == "2xx"


# ── New Tier 2 tests ────────────────────────────────────────────────


def test_tier2_assertEquals_with_status_subject() -> None:
    """assertEquals(getStatusCode(), 200) → root gets STATUS."""
    assert_eq = make_call_site(
        method_name="assertEquals",
        argument_expr=["getStatusCode()", "200"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )
    get_status_code = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=14,
        end_line=10,
        end_column=30,
    )

    nodes = _classify_nodes([assert_eq, get_status_code])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertEquals")
    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.STATUS
    # getStatusCode argument child gets GENERAL (category-aware)
    arg_node = next(n for n in nodes if n.call_site.method_name == "getStatusCode")
    assert arg_node.assertion_classification is not None
    assert arg_node.assertion_classification.role == AssertionRole.GENERAL


def test_tier2_assertEquals_with_body_subject() -> None:
    """assertEquals(getBody(), expected) → root gets BODY."""
    assert_eq = make_call_site(
        method_name="assertEquals",
        argument_expr=["getBody()", "expected"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )
    get_body = make_call_site(
        method_name="getBody",
        start_line=10,
        start_column=14,
        end_line=10,
        end_column=25,
    )

    nodes = _classify_nodes([assert_eq, get_body])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertEquals")
    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.BODY


def test_tier2_chain_intermediaries_get_classified() -> None:
    """assertThat(x).extracting("name").isEqualTo("y") → all get GENERAL."""
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["x"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=15,
    )
    extracting = make_call_site(
        method_name="extracting",
        argument_expr=['"name"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=30,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=['"y"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=45,
    )

    nodes = _classify_nodes([assert_that, extracting, is_equal_to])
    for node in nodes:
        assert node.assertion_classification is not None
        assert node.assertion_classification.role == AssertionRole.GENERAL


def test_tier2_hamcrest_status_value_matcher_extracts_code() -> None:
    """assertThat(getStatusCode(), is(equalTo(200))) extracts equalTo status code."""
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=50,
    )
    get_status_code = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=28,
    )
    is_matcher = make_call_site(
        method_name="is",
        start_line=10,
        start_column=30,
        end_line=10,
        end_column=48,
    )
    equal_to = make_call_site(
        method_name="equalTo",
        argument_expr=["200"],
        start_line=10,
        start_column=33,
        end_line=10,
        end_column=47,
    )

    nodes = _classify_nodes([assert_that, get_status_code, is_matcher, equal_to])
    root = next(n for n in nodes if n.call_site.method_name == "assertThat")
    assert root.assertion_classification is not None
    assert root.assertion_classification.role == AssertionRole.STATUS
    is_node = next(n for n in nodes if n.call_site.method_name == "is")
    assert is_node.assertion_classification is not None
    assert is_node.assertion_classification.role == AssertionRole.GENERAL
    equal_to_node = next(n for n in nodes if n.call_site.method_name == "equalTo")
    assert equal_to_node.assertion_classification is not None
    assert equal_to_node.assertion_classification.role == AssertionRole.STATUS
    assert equal_to_node.assertion_classification.status_code == 200
    assert equal_to_node.assertion_classification.status_range == "2xx"


def test_tier2_resolved_helper_not_classified() -> None:
    """Nodes with resolved_helper are skipped by Tier 2."""
    from gerbil.analysis.runtime.call_sites import HelperExpansion, MethodRef

    helper_call = make_call_site(method_name="assertHelper", start_line=10)
    # Manually set resolved_helper before classification would have run
    # We need to test the mechanism by building the view, setting the helper, then classifying
    method = make_callable(call_sites=[helper_call])
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="testCase()",
                ),
                context_class_name="example.ApiTest",
                grouping=build_call_site_grouping([helper_call]),
                method_details=method,
            )
        ]
    )
    helper_ref = MethodRef(
        defining_class_name="example.Helper",
        method_signature="assertHelper()",
    )
    runtime_view.entries[0].grouping.nodes[0].resolved_helper = helper_ref
    helper_grouping = build_call_site_grouping([])
    runtime_view.entries[0].grouping.nodes[0].helper_expansion = HelperExpansion(
        callee=helper_ref, grouping=helper_grouping
    )

    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    assert runtime_view.entries[0].grouping.nodes[0].assertion_classification is None


def test_tier2_exception_root_marks_descendants_general() -> None:
    """assertThatThrownBy().hasMessage("x") → root EXCEPTION, hasMessage GENERAL."""
    root = make_call_site(
        method_name="assertThatThrownBy",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=25,
    )
    has_message = make_call_site(
        method_name="hasMessage",
        argument_expr=['"x"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )

    nodes = _classify_nodes([root, has_message])
    root_node = next(
        n for n in nodes if n.call_site.method_name == "assertThatThrownBy"
    )
    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.EXCEPTION

    msg_node = next(n for n in nodes if n.call_site.method_name == "hasMessage")
    assert msg_node.assertion_classification is not None
    assert msg_node.assertion_classification.role == AssertionRole.GENERAL


# ── Tier 1: HEADER_ASSERTION ────────────────────────────────────────


def test_tier1_header_assertion_role() -> None:
    call_site = make_call_site(method_name="contentType", start_line=10)
    nodes = _classify_nodes(
        [call_site],
        annotate_http=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.HEADER_ASSERTION,
        ),
    )
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.HEADER


# ── Tier 2: HEADER subject/category ─────────────────────────────────


def test_tier2_assertThat_with_header_subject() -> None:
    """assertThat(response.getHeader(...)).isEqualTo(...) → root gets HEADER."""
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=50,
    )
    get_header = make_call_site(
        method_name="getHeader",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=30,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=['"application/json"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=60,
    )

    nodes = _classify_nodes([assert_that, get_header, is_equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.HEADER


def test_tier2_header_category_after_assertion_root_without_subject_is_general() -> (
    None
):
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["resp"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=20,
    )
    header_call = make_call_site(
        method_name="header",
        argument_expr=['"X-Custom"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )

    nodes = _classify_nodes([assert_that, header_call])
    header_node = next(n for n in nodes if n.call_site.method_name == "header")
    assert header_node.assertion_classification is not None
    assert header_node.assertion_classification.role == AssertionRole.GENERAL


def test_tier2_assertthat_generic_contains_without_body_subject_is_general() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["items"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=20,
    )
    contains = make_call_site(
        method_name="contains",
        argument_expr=["item"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=36,
    )

    nodes = _classify_nodes([assert_that, contains])
    verifier_node = next(n for n in nodes if n.call_site.method_name == "contains")

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.GENERAL
    assert verifier_node.assertion_classification.is_countable is True


def test_tier2_assertthat_generic_matches_without_body_subject_is_general() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["pattern"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=22,
    )
    matches = make_call_site(
        method_name="matches",
        argument_expr=["candidate"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=42,
    )

    nodes = _classify_nodes([assert_that, matches])
    verifier_node = next(n for n in nodes if n.call_site.method_name == "matches")

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.GENERAL
    assert verifier_node.assertion_classification.is_countable is True


def test_tier2_assertthat_content_type_without_header_subject_is_general() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["document"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=23,
    )
    content_type = make_call_site(
        method_name="contentType",
        argument_expr=['"application/json"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=55,
    )

    nodes = _classify_nodes([assert_that, content_type])
    verifier_node = next(n for n in nodes if n.call_site.method_name == "contentType")

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.GENERAL
    assert verifier_node.assertion_classification.is_countable is True


def test_tier2_assertthat_status_code_remains_confident_status() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["responseAssert"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=30,
    )
    status_code = make_call_site(
        method_name="statusCode",
        argument_expr=["404"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=46,
    )

    nodes = _classify_nodes([assert_that, status_code])
    verifier_node = next(n for n in nodes if n.call_site.method_name == "statusCode")

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert verifier_node.assertion_classification.status_code == 404
    assert verifier_node.assertion_classification.status_range == "4xx"


def test_tier2_domain_status_chain_without_http_context_is_not_status() -> None:
    status = make_call_site(
        method_name="status",
        receiver_expr="domain",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=16,
    )
    is_not_found = make_call_site(
        method_name="isNotFound",
        receiver_expr="domain.status()",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=29,
    )

    nodes = _classify_nodes([status, is_not_found])

    assert all(node.assertion_classification is None for node in nodes)


def test_tier2_mixed_header_body_subject_gets_general() -> None:
    """Both header and body hints in arguments → root gets GENERAL."""
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=50,
    )
    get_header = make_call_site(
        method_name="getHeader",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=25,
    )
    get_body = make_call_site(
        method_name="getBody",
        start_line=10,
        start_column=27,
        end_line=10,
        end_column=38,
    )

    nodes = _classify_nodes([assert_that, get_header, get_body])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.GENERAL


def test_tier2_mixed_header_status_subject_gets_general() -> None:
    """Both header and status hints in arguments → root gets GENERAL."""
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=50,
    )
    get_header = make_call_site(
        method_name="getHeader",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=25,
    )
    get_status = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=27,
        end_line=10,
        end_column=42,
    )

    nodes = _classify_nodes([assert_that, get_header, get_status])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.GENERAL


# ── Tier 1: status_range and argument parsing ───────────────────────


def test_tier1_statusCode_with_integer_argument() -> None:
    """statusCode(404) in tier 1 → status_code=404, status_range='4xx'."""
    call_site = make_call_site(
        method_name="statusCode",
        argument_expr=["404"],
        start_line=10,
    )
    nodes = _classify_nodes(
        [call_site],
        annotate_http=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        ),
    )
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.STATUS
    assert ac.status_code == 404
    assert ac.status_range == "4xx"


def test_tier1_statusCode_with_httpstatus_constant() -> None:
    """statusCode(HttpStatus.NOT_FOUND) → status_code=404, status_range='4xx'."""
    call_site = make_call_site(
        method_name="statusCode",
        argument_expr=["HttpStatus.NOT_FOUND"],
        start_line=10,
    )
    nodes = _classify_nodes(
        [call_site],
        annotate_http=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        ),
    )
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.status_code == 404
    assert ac.status_range == "4xx"


def test_tier2_is2xxSuccessful_via_category() -> None:
    """is2xxSuccessful via _classify_by_category → status_code=None, status_range='2xx'."""
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["resp"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=20,
    )
    is2xx = make_call_site(
        method_name="is2xxSuccessful",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )

    nodes = _classify_nodes([assert_that, is2xx])
    node = next(n for n in nodes if n.call_site.method_name == "is2xxSuccessful")
    assert node.assertion_classification is not None
    assert node.assertion_classification.role == AssertionRole.STATUS
    assert node.assertion_classification.status_code is None
    assert node.assertion_classification.status_range == "2xx"


def test_tier2_is5xxServerError_via_category() -> None:
    """is5xxServerError via _classify_by_category → status_code=None, status_range='5xx'."""
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["resp"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=20,
    )
    is5xx = make_call_site(
        method_name="is5xxServerError",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )

    nodes = _classify_nodes([assert_that, is5xx])
    node = next(n for n in nodes if n.call_site.method_name == "is5xxServerError")
    assert node.assertion_classification is not None
    assert node.assertion_classification.role == AssertionRole.STATUS
    assert node.assertion_classification.status_code is None
    assert node.assertion_classification.status_range == "5xx"


# ── assertAll groups count nested assertions individually ────────────


def _countable_nodes(nodes):
    return [
        n
        for n in nodes
        if n.assertion_classification is not None
        and n.assertion_classification.is_countable
    ]


def test_tier2_assertAll_counts_each_nested_assertion() -> None:
    assert_all = make_call_site(
        method_name="assertAll",
        argument_expr=["() -> assertEquals(1, a)", "() -> assertEquals(2, b)"],
        start_line=10,
        start_column=1,
        end_line=13,
        end_column=2,
    )
    nested_one = make_call_site(
        method_name="assertEquals",
        argument_expr=["1", "a"],
        start_line=11,
        start_column=13,
        end_line=11,
        end_column=40,
    )
    nested_two = make_call_site(
        method_name="assertEquals",
        argument_expr=["2", "b"],
        start_line=12,
        start_column=13,
        end_line=12,
        end_column=40,
    )

    nodes = _classify_nodes([assert_all, nested_one, nested_two])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertAll")
    nested_nodes = [n for n in nodes if n.call_site.method_name == "assertEquals"]

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert root_node.assertion_classification.is_countable is False
    for nested in nested_nodes:
        assert nested.assertion_classification is not None
        assert nested.assertion_classification.role == AssertionRole.GENERAL
        assert nested.assertion_classification.node_kind == AssertionNodeKind.DIRECT
        assert nested.assertion_classification.is_countable is True
    assert len(_countable_nodes(nodes)) == 2


def test_assertAll_nested_assertj_bdd_then_chain_counts_verifier() -> None:
    # `assertAll(() -> then(x).isEqualTo(y))`: the nested AssertJ BDD chain must be
    # detected as its own root so assertAll demotes to a wrapper and the terminal
    # verifier counts — same as the nested `assertThat` case.
    assert_all = make_call_site(
        method_name="assertAll",
        argument_expr=["() -> then(actual).isEqualTo(expected)"],
        start_line=10,
        start_column=1,
        end_line=12,
        end_column=2,
    )
    then = make_call_site(
        method_name="then",
        receiver_type="org.assertj.core.api.BDDAssertions",
        argument_expr=["actual"],
        start_line=11,
        start_column=13,
        end_line=11,
        end_column=27,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=["expected"],
        start_line=11,
        start_column=13,
        end_line=11,
        end_column=50,
    )

    nodes = _classify_nodes([assert_all, then, is_equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertAll")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.is_countable is False
    countable = _countable_nodes(nodes)
    assert len(countable) == 1
    assert countable[0].call_site.method_name == "isEqualTo"


def test_tier2_assertAll_nested_status_assertion_surfaces_code() -> None:
    assert_all = make_call_site(
        method_name="assertAll",
        argument_expr=["() -> assertEquals(404, resp.getStatusCode())"],
        start_line=10,
        start_column=1,
        end_line=12,
        end_column=2,
    )
    nested_assert = make_call_site(
        method_name="assertEquals",
        argument_expr=["404", "resp.getStatusCode()"],
        start_line=11,
        start_column=9,
        end_line=11,
        end_column=60,
    )
    get_status_code = make_call_site(
        method_name="getStatusCode",
        start_line=11,
        start_column=30,
        end_line=11,
        end_column=55,
    )

    nodes = _classify_nodes([assert_all, nested_assert, get_status_code])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertAll")
    nested_node = next(n for n in nodes if n.call_site.method_name == "assertEquals")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert root_node.assertion_classification.is_countable is False

    assert nested_node.assertion_classification is not None
    assert nested_node.assertion_classification.role == AssertionRole.STATUS
    assert nested_node.assertion_classification.status_code == 404
    assert nested_node.assertion_classification.status_range == "4xx"
    assert nested_node.assertion_classification.node_kind == AssertionNodeKind.DIRECT
    assert nested_node.assertion_classification.is_countable is True
    assert len(_countable_nodes(nodes)) == 1


def test_tier2_assertAll_without_nested_call_sites_stays_direct() -> None:
    assert_all = make_call_site(
        method_name="assertAll",
        argument_expr=["() -> checkInvariants", "() -> verifyTotals"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=60,
    )

    nodes = _classify_nodes([assert_all])
    ac = nodes[0].assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.GENERAL
    assert ac.node_kind == AssertionNodeKind.DIRECT
    assert ac.is_countable is True


def test_tier2_assertAll_nested_exception_root_counts_child() -> None:
    assert_all = make_call_site(
        method_name="assertAll",
        argument_expr=["() -> assertThrows(RuntimeException.class, () -> svc.run())"],
        start_line=10,
        start_column=1,
        end_line=12,
        end_column=2,
    )
    nested_throws = make_call_site(
        method_name="assertThrows",
        argument_expr=["RuntimeException.class", "() -> svc.run()"],
        start_line=11,
        start_column=9,
        end_line=11,
        end_column=64,
    )

    nodes = _classify_nodes([assert_all, nested_throws])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertAll")
    nested_node = next(n for n in nodes if n.call_site.method_name == "assertThrows")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert root_node.assertion_classification.is_countable is False

    assert nested_node.assertion_classification is not None
    assert nested_node.assertion_classification.role == AssertionRole.EXCEPTION
    assert nested_node.assertion_classification.is_countable is True
    assert len(_countable_nodes(nodes)) == 1


def test_tier2_assertAll_nested_assertThat_chain_counts_terminal_verifier() -> None:
    assert_all = make_call_site(
        method_name="assertAll",
        argument_expr=["() -> assertThat(value).isEqualTo(2)"],
        start_line=10,
        start_column=1,
        end_line=12,
        end_column=2,
    )
    nested_assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["value"],
        start_line=11,
        start_column=9,
        end_line=11,
        end_column=28,
    )
    nested_is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=["2"],
        start_line=11,
        start_column=9,
        end_line=11,
        end_column=42,
    )

    nodes = _classify_nodes([assert_all, nested_assert_that, nested_is_equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertAll")
    wrapper_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    verifier_node = next(n for n in nodes if n.call_site.method_name == "isEqualTo")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert wrapper_node.assertion_classification is not None
    assert wrapper_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert verifier_node.assertion_classification is not None
    assert (
        verifier_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    )
    assert len(_countable_nodes(nodes)) == 1


# ── Unit tests for _extract_status_code_from_arguments ───────────────


def test_extract_status_code_bare_integer() -> None:
    assert _extract_status_code_from_arguments(["200"]) == 200


def test_extract_status_code_httpstatus_constant() -> None:
    assert _extract_status_code_from_arguments(["HttpStatus.NOT_FOUND"]) == 404


def test_extract_status_code_see_other_constant() -> None:
    assert _extract_status_code_from_arguments(["HttpStatus.SEE_OTHER"]) == 303


def test_extract_status_code_request_timeout_constant() -> None:
    assert _extract_status_code_from_arguments(["HttpStatus.REQUEST_TIMEOUT"]) == 408


def test_extract_status_code_rejects_two_digit() -> None:
    assert _extract_status_code_from_arguments(["42"]) is None


def test_extract_status_code_rejects_four_digit() -> None:
    assert _extract_status_code_from_arguments(["1000"]) is None


def test_extract_status_code_rejects_variable_name() -> None:
    assert _extract_status_code_from_arguments(["someVar"]) is None


def test_extract_status_code_rejects_unknown_httpstatus() -> None:
    assert _extract_status_code_from_arguments(["HttpStatus.UNKNOWN_THING"]) is None


def test_extract_status_code_picks_right_arg_from_multiple() -> None:
    assert _extract_status_code_from_arguments(["response", "200"]) == 200


def test_extract_status_code_jaxrs_qualified_response_status() -> None:
    assert (
        _extract_status_code_from_arguments(
            ["Response.Status.NOT_FOUND.getStatusCode()"]
        )
        == 404
    )


def test_extract_status_code_jaxrs_short_status_qualifier_needs_type_evidence() -> None:
    expression = ["Status.REQUEST_ENTITY_TOO_LARGE.getStatusCode()"]
    assert _extract_status_code_from_arguments(expression) is None
    assert (
        _extract_status_code_from_arguments(
            expression, ["javax.ws.rs.core.Response.Status"]
        )
        == 413
    )


def test_extract_status_code_jaxrs_request_uri_too_long_with_jakarta_type() -> None:
    assert (
        _extract_status_code_from_arguments(
            ["Status.REQUEST_URI_TOO_LONG"], ["jakarta.ws.rs.core.Response.Status"]
        )
        == 414
    )


def test_extract_status_code_jaxrs_bare_status_accepts_binary_type_name() -> None:
    assert (
        _extract_status_code_from_arguments(
            ["Status.NOT_FOUND"], ["javax.ws.rs.core.Response$Status"]
        )
        == 404
    )


def test_extract_status_code_rejects_domain_status_enum_without_jaxrs_type() -> None:
    assert (
        _extract_status_code_from_arguments(
            ["Status.NOT_FOUND"], ["com.acme.order.Status"]
        )
        is None
    )


def test_extract_status_code_rejects_domain_status_enum() -> None:
    assert _extract_status_code_from_arguments(["OrderStatus.NOT_FOUND"]) is None


def test_extract_status_code_rejects_unknown_jaxrs_constant() -> None:
    assert _extract_status_code_from_arguments(["Status.SOMETHING_ELSE"]) is None


def test_extract_status_code_apache_sc_constant_routes_via_sc_map() -> None:
    assert "SC_NO_CONTENT" not in _HTTPSTATUS_CONSTANT_CODES
    assert _extract_status_code_from_arguments(["HttpStatus.SC_NO_CONTENT"]) == 204


def test_extract_status_code_apache_request_too_long_constant() -> None:
    assert (
        _extract_status_code_from_arguments(["HttpStatus.SC_REQUEST_TOO_LONG"]) == 413
    )


def test_extract_status_code_apache_static_imported_sc_constant() -> None:
    assert _extract_status_code_from_arguments(["SC_NOT_FOUND"]) == 404


def test_extract_status_code_httpurlconnection_forbidden() -> None:
    assert _extract_status_code_from_arguments(["HTTP_FORBIDDEN"]) == 403


def test_extract_status_code_httpurlconnection_qualified() -> None:
    assert (
        _extract_status_code_from_arguments(["HttpURLConnection.HTTP_CLIENT_TIMEOUT"])
        == 408
    )


def test_extract_status_code_spring_constant_still_resolves() -> None:
    assert _extract_status_code_from_arguments(["HttpStatus.PAYLOAD_TOO_LARGE"]) == 413


def test_extract_status_code_rejects_sc_lookalike_qualifier() -> None:
    assert _extract_status_code_from_arguments(["MyStatus.SC_ACCEPTED"]) is None


def test_extract_status_code_rejects_http_lookalike_qualifier() -> None:
    assert _extract_status_code_from_arguments(["MyConstants.HTTP_OK"]) is None


def test_extract_status_code_rejects_constant_name_inside_string_literal() -> None:
    assert _extract_status_code_from_arguments(['"SC_OK"']) is None
    assert _extract_status_code_from_arguments(['"HTTP_OK"']) is None


def test_extract_status_code_fully_qualified_apache_constant_resolves() -> None:
    assert (
        _extract_status_code_from_arguments(["org.apache.http.HttpStatus.SC_OK"]) == 200
    )


def test_tier2_assertEquals_with_jaxrs_status_constant() -> None:
    assert_equals = make_call_site(
        method_name="assertEquals",
        argument_expr=[
            "Response.Status.NOT_FOUND.getStatusCode()",
            "response.getStatus()",
        ],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=90,
    )
    enum_accessor = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=14,
        end_line=10,
        end_column=55,
    )
    get_status = make_call_site(
        method_name="getStatus",
        start_line=10,
        start_column=58,
        end_line=10,
        end_column=78,
    )

    nodes = _classify_nodes([assert_equals, enum_accessor, get_status])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertEquals")
    ac = root_node.assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.STATUS
    assert ac.status_code == 404
    assert ac.status_range == "4xx"
    assert ac.is_countable is True


def test_tier2_assertEquals_with_domain_status_enum_records_no_code() -> None:
    assert_equals = make_call_site(
        method_name="assertEquals",
        argument_expr=["Status.NOT_FOUND", "order.getStatus()"],
        argument_types=["com.acme.order.Status", "com.acme.order.Status"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=60,
    )
    get_status = make_call_site(
        method_name="getStatus",
        start_line=10,
        start_column=30,
        end_line=10,
        end_column=48,
    )

    nodes = _classify_nodes([assert_equals, get_status])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertEquals")
    ac = root_node.assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.GENERAL
    assert ac.status_code is None
    assert ac.status_range is None


def test_tier2_assertEquals_with_typed_bare_jaxrs_status_constant() -> None:
    assert_equals = make_call_site(
        method_name="assertEquals",
        argument_expr=["Status.NOT_FOUND", "response.getStatus()"],
        argument_types=["javax.ws.rs.core.Response.Status", "int"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=60,
    )
    get_status = make_call_site(
        method_name="getStatus",
        start_line=10,
        start_column=30,
        end_line=10,
        end_column=52,
    )

    nodes = _classify_nodes([assert_equals, get_status])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertEquals")
    ac = root_node.assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.STATUS
    assert ac.status_code == 404
    assert ac.status_range == "4xx"


# ── Edge cases for negated assertion roots ──────────────────────────


def test_tier2_assertNotEquals_with_literal_status_code_drops_rejected_code() -> None:
    assert_not_equals = make_call_site(
        method_name="assertNotEquals",
        argument_expr=["500", "response.getStatusCode()"],
        argument_types=["int", "int"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=50,
    )
    get_status_code = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=30,
        end_line=10,
        end_column=50,
    )

    nodes = _classify_nodes([assert_not_equals, get_status_code])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertNotEquals")
    ac = root_node.assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.STATUS
    assert ac.status_code is None
    assert ac.status_range is None
    assert ac.node_kind == AssertionNodeKind.DIRECT


def test_tier2_assertNotEquals_with_httpstatus_constant_drops_rejected_code() -> None:
    assert_not_equals = make_call_site(
        method_name="assertNotEquals",
        argument_expr=["HttpStatus.INTERNAL_SERVER_ERROR", "response.getStatusCode()"],
        argument_types=["org.springframework.http.HttpStatus", "int"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=70,
    )
    get_status_code = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=50,
        end_line=10,
        end_column=70,
    )

    nodes = _classify_nodes([assert_not_equals, get_status_code])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertNotEquals")
    ac = root_node.assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.STATUS
    assert ac.status_code is None
    assert ac.status_range is None


def test_tier2_assertFalse_with_status_range_verifier_drops_rejected_range() -> None:
    assert_false = make_call_site(
        method_name="assertFalse",
        argument_expr=["response.getStatusCode().is2xxSuccessful()"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=50,
    )
    is2xx = make_call_site(
        method_name="is2xxSuccessful",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=48,
    )
    get_status_code = make_call_site(
        method_name="getStatusCode",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=28,
    )

    nodes = _classify_nodes([assert_false, is2xx, get_status_code])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertFalse")
    verifier_node = next(
        n for n in nodes if n.call_site.method_name == "is2xxSuccessful"
    )

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.STATUS
    assert root_node.assertion_classification.node_kind == AssertionNodeKind.WRAPPER
    assert root_node.assertion_classification.status_code is None
    assert root_node.assertion_classification.status_range is None

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert (
        verifier_node.assertion_classification.node_kind == AssertionNodeKind.VERIFIER
    )
    assert verifier_node.assertion_classification.status_code is None
    assert verifier_node.assertion_classification.status_range is None


# ── Edge cases for raw-client response accessors ────────────────────


def test_tier2_assertThat_getResponseCode_extracts_httpurlconnection_constant() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )
    get_response_code = make_call_site(
        method_name="getResponseCode",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=30,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=["HttpURLConnection.HTTP_OK"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=65,
    )

    nodes = _classify_nodes([assert_that, get_response_code, is_equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    verifier_node = next(n for n in nodes if n.call_site.method_name == "isEqualTo")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.STATUS

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.STATUS
    assert verifier_node.assertion_classification.status_code == 200
    assert verifier_node.assertion_classification.status_range == "2xx"


def test_tier2_assertThat_readEntity_counts_body_assertion() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )
    read_entity = make_call_site(
        method_name="readEntity",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=25,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=['"payload"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=55,
    )

    nodes = _classify_nodes([assert_that, read_entity, is_equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    verifier_node = next(n for n in nodes if n.call_site.method_name == "isEqualTo")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.BODY

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.BODY


def test_tier2_assertThat_getHeaderString_counts_header_assertion() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=45,
    )
    get_header_string = make_call_site(
        method_name="getHeaderString",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=30,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=['"application/json"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=60,
    )

    nodes = _classify_nodes([assert_that, get_header_string, is_equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    verifier_node = next(n for n in nodes if n.call_site.method_name == "isEqualTo")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.HEADER

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.HEADER


def test_tier2_assertThat_getFirstHeader_counts_header_assertion() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=45,
    )
    get_first_header = make_call_site(
        method_name="getFirstHeader",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=30,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=['"application/json"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=60,
    )

    nodes = _classify_nodes([assert_that, get_first_header, is_equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    verifier_node = next(n for n in nodes if n.call_site.method_name == "isEqualTo")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.HEADER

    assert verifier_node.assertion_classification is not None
    assert verifier_node.assertion_classification.role == AssertionRole.HEADER


# ── Edge cases for type-evidence-gated getStatus downgrade ──────────


def test_tier2_assertEquals_getStatus_with_int_type_keeps_status() -> None:
    assert_equals = make_call_site(
        method_name="assertEquals",
        argument_expr=["200", "response.getStatus()"],
        argument_types=["int", "int"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=50,
    )
    get_status = make_call_site(
        method_name="getStatus",
        start_line=10,
        start_column=30,
        end_line=10,
        end_column=48,
    )

    nodes = _classify_nodes([assert_equals, get_status])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertEquals")
    ac = root_node.assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.STATUS
    assert ac.status_code == 200
    assert ac.status_range == "2xx"


def test_tier2_assertEquals_getStatus_with_httpstatus_type_keeps_status() -> None:
    assert_equals = make_call_site(
        method_name="assertEquals",
        argument_expr=["HttpStatus.OK", "response.getStatus()"],
        argument_types=[
            "org.springframework.http.HttpStatus",
            "org.springframework.http.HttpStatus",
        ],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=60,
    )
    get_status = make_call_site(
        method_name="getStatus",
        start_line=10,
        start_column=40,
        end_line=10,
        end_column=58,
    )

    nodes = _classify_nodes([assert_equals, get_status])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertEquals")
    ac = root_node.assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.STATUS
    assert ac.status_code == 200
    assert ac.status_range == "2xx"


def test_tier2_assertEquals_getStatus_without_type_info_keeps_status() -> None:
    assert_equals = make_call_site(
        method_name="assertEquals",
        argument_expr=["200", "response.getStatus()"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=50,
    )
    get_status = make_call_site(
        method_name="getStatus",
        start_line=10,
        start_column=30,
        end_line=10,
        end_column=48,
    )

    nodes = _classify_nodes([assert_equals, get_status])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertEquals")
    ac = root_node.assertion_classification
    assert ac is not None
    assert ac.role == AssertionRole.STATUS


def test_tier2_assertThat_getStatus_with_domain_enum_is_general() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        argument_expr=["user.getStatus()"],
        argument_types=["com.acme.UserStatus"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=35,
    )
    get_status = make_call_site(
        method_name="getStatus",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=25,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=["UserStatus.ACTIVE"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=60,
    )

    nodes = _classify_nodes([assert_that, get_status, is_equal_to])
    root_node = next(n for n in nodes if n.call_site.method_name == "assertThat")
    matcher_node = next(n for n in nodes if n.call_site.method_name == "isEqualTo")

    assert root_node.assertion_classification is not None
    assert root_node.assertion_classification.role == AssertionRole.GENERAL

    assert matcher_node.assertion_classification is not None
    assert matcher_node.assertion_classification.role == AssertionRole.GENERAL
