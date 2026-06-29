from __future__ import annotations

import gerbil.analysis.shared.caching as receiver_hierarchy_cache_module

from gerbil.analysis.runtime.call_sites import (
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.schema import (
    AssertionClassification,
    AssertionRole,
    AssertionSummary,
    OracleTypeDecision,
    LifecyclePhase,
)
from gerbil.analysis.properties.assertion import (
    build_assertion_summary as _build_assertion_summary,
    classify_oracle_type as _classify_oracle_type,
)
from gerbil.analysis.assertion import classify_assertions_on_runtime_view
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    annotate_node_http,
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
    make_type,
    make_variable_declaration,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def _runtime_view_for_method(
    method_details,
    *,
    class_name: str = "example.ApiTest",
    method_signature: str = "testCase()",
) -> TestRuntimeView:
    if method_details is None:
        return TestRuntimeView()

    return TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name=class_name,
                    method_signature=method_signature,
                ),
                context_class_name=class_name,
                grouping=build_call_site_grouping(list(method_details.call_sites)),
                method_details=method_details,
            )
        ]
    )


def _classify_surface(
    runtime_view: TestRuntimeView,
    *,
    analysis=None,
    get_static_import_index_for_class=(lambda _class_name: StaticImportIndex.EMPTY),
) -> AssertionSummary:
    _classify_assertions(
        runtime_view,
        analysis=analysis,
        get_static_import_index_for_class=get_static_import_index_for_class,
    )
    return _build_assertion_summary(runtime_view=runtime_view)


def _classify_assertions(
    runtime_view: TestRuntimeView,
    *,
    analysis=None,
    get_static_import_index_for_class=(lambda _class_name: StaticImportIndex.EMPTY),
):
    resolver = build_runtime_receiver_resolver_for_testing(
        runtime_view,
        analysis=analysis,
        get_static_import_index_for_class=get_static_import_index_for_class,
    )
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=resolver,
    )
    return resolver


def _classify_oracle(
    runtime_view: TestRuntimeView,
    method_details,
    *,
    analysis=None,
    get_static_import_index_for_class=(lambda _class_name: StaticImportIndex.EMPTY),
) -> OracleTypeDecision:
    resolver = _classify_assertions(
        runtime_view,
        analysis=analysis,
        get_static_import_index_for_class=get_static_import_index_for_class,
    )
    return _classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method_details,
        class_imports=[],
        receiver_resolver=resolver,
    )


# ── Assertion classification detection tests ──────────────────────────


def test_assertion_classification_detects_assertthat_with_status_subject_chain() -> (
    None
):
    """assertThat(response.getStatusCode()).isEqualTo(expected) classifies nodes."""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertThat",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=30,
            ),
            make_call_site(
                method_name="getStatusCode",
                start_line=10,
                start_column=12,
                end_line=10,
                end_column=27,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=["expected"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=45,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    _classify_assertions(runtime_view)

    assert_that_node = [
        n
        for n in runtime_view.entries[0].grouping.nodes
        if n.call_site.method_name == "assertThat"
    ][0]
    # The assertThat root with a status subject hint should be classified
    assert assert_that_node.assertion_classification is not None
    assert assert_that_node.assertion_classification.role == AssertionRole.STATUS


def test_assertion_classification_ignores_standalone_status_predicate_outside_assertion_context() -> (
    None
):
    """is4xxClientError without an assertion context root should not be classified."""
    method = make_callable(
        call_sites=[
            make_call_site(method_name="is4xxClientError", start_line=10),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    _classify_assertions(runtime_view)

    nodes = list(runtime_view.entries[0].grouping.nodes)
    assert nodes[0].assertion_classification is None


def test_assertion_classification_classifies_status_methods_under_assertion_context() -> (
    None
):
    """isFound under assertTrue should be classified with STATUS role."""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertTrue",
                argument_expr=["status.isFound()"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=30,
            ),
            make_call_site(
                method_name="isFound",
                start_line=10,
                start_column=15,
                end_line=10,
                end_column=24,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    _classify_assertions(runtime_view)

    nodes = list(runtime_view.entries[0].grouping.nodes)
    is_found_node = [n for n in nodes if n.call_site.method_name == "isFound"][0]
    assert is_found_node.assertion_classification is not None
    assert is_found_node.assertion_classification.role == AssertionRole.STATUS
    assert is_found_node.assertion_classification.status_code == 302


def test_assertion_classification_status_code_from_isOk_under_andExpect() -> None:
    """isOk under andExpect yields STATUS role with status_code 200."""
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="andExpect",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=50,
            ),
            make_call_site(
                method_name="isOk",
                start_line=10,
                start_column=5,
                end_line=10,
                end_column=15,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[1],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )
    _classify_assertions(runtime_view)

    is_ok_node = [
        n
        for n in runtime_view.entries[0].grouping.nodes
        if n.call_site.method_name == "isOk"
    ][0]
    assert is_ok_node.assertion_classification is not None
    assert is_ok_node.assertion_classification.role == AssertionRole.STATUS
    assert is_ok_node.assertion_classification.status_code == 200


def test_assertion_classification_status_codes_in_assertion_context() -> None:
    """Status predicates under assertTrue need an HTTP status subject."""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertTrue",
                argument_expr=["response.getStatus().isFound()"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=46,
            ),
            make_call_site(
                method_name="isFound",
                start_line=10,
                start_column=15,
                end_line=10,
                end_column=43,
            ),
            make_call_site(
                method_name="getStatus",
                start_line=10,
                start_column=15,
                end_line=10,
                end_column=35,
            ),
            make_call_site(
                method_name="assertTrue",
                argument_expr=["response.getStatus().isUnprocessableEntity()"],
                start_line=11,
                start_column=1,
                end_line=11,
                end_column=59,
            ),
            make_call_site(
                method_name="isUnprocessableEntity",
                start_line=11,
                start_column=15,
                end_line=11,
                end_column=57,
            ),
            make_call_site(
                method_name="getStatus",
                start_line=11,
                start_column=15,
                end_line=11,
                end_column=35,
            ),
            make_call_site(
                method_name="assertTrue",
                argument_expr=["response.getStatus().isInternalServerError()"],
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=58,
            ),
            make_call_site(
                method_name="isInternalServerError",
                start_line=12,
                start_column=15,
                end_line=12,
                end_column=56,
            ),
            make_call_site(
                method_name="getStatus",
                start_line=12,
                start_column=15,
                end_line=12,
                end_column=35,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    _classify_assertions(runtime_view)

    status_codes = sorted(
        n.assertion_classification.status_code
        for n in runtime_view.entries[0].grouping.nodes
        if n.assertion_classification is not None
        and n.assertion_classification.is_countable
        and n.assertion_classification.status_code is not None
    )
    assert status_codes == [302, 422, 500]


def test_assertion_surface_does_not_count_domain_status_method_predicate() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertTrue",
                argument_expr=["domain.status().isNotFound()"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=45,
            ),
            make_call_site(
                method_name="status",
                receiver_expr="domain",
                start_line=10,
                start_column=12,
                end_line=10,
                end_column=27,
            ),
            make_call_site(
                method_name="isNotFound",
                receiver_expr="domain.status()",
                start_line=10,
                start_column=12,
                end_line=10,
                end_column=40,
            ),
        ],
        variable_declarations=[
            make_variable_declaration(
                name="domain",
                type_name="example.DomainObject",
                start_line=5,
            )
        ],
    )

    surface = _classify_surface(_runtime_view_for_method(method))

    assert surface.status_count == 0


def test_assertion_classification_range_status_methods_have_no_status_code() -> None:
    """Range methods like is2xxSuccessful are classified but have no specific status_code."""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertTrue",
                argument_expr=["status.is2xxSuccessful()"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=35,
            ),
            make_call_site(
                method_name="is2xxSuccessful",
                start_line=10,
                start_column=15,
                end_line=10,
                end_column=33,
            ),
            make_call_site(
                method_name="assertTrue",
                argument_expr=["status.is4xxClientError()"],
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=35,
            ),
            make_call_site(
                method_name="is4xxClientError",
                start_line=12,
                start_column=15,
                end_line=12,
                end_column=33,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    _classify_assertions(runtime_view)

    classified = [
        n
        for n in runtime_view.entries[0].grouping.nodes
        if n.assertion_classification is not None
        and n.assertion_classification.role == AssertionRole.STATUS
    ]
    assert len(classified) >= 2
    # Range methods do not map to a specific code but carry status_range
    for node in classified:
        assertion = node.assertion_classification
        assert assertion is not None
        if node.call_site.method_name == "is2xxSuccessful":
            assert assertion.status_code is None
            assert assertion.status_range == "2xx"
        elif node.call_site.method_name == "is4xxClientError":
            assert assertion.status_code is None
            assert assertion.status_range == "4xx"


def test_assertion_classification_assertThat_with_status_subject() -> None:
    """assertThat(response.getStatusCode()).isEqualTo(...) classifies as STATUS."""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertThat",
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=30,
            ),
            make_call_site(
                method_name="getStatusCode",
                start_line=12,
                start_column=12,
                end_line=12,
                end_column=27,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=["HttpStatus.OK"],
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=52,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    _classify_assertions(runtime_view)

    assert_that_node = [
        n
        for n in runtime_view.entries[0].grouping.nodes
        if n.call_site.method_name == "assertThat"
    ][0]
    assert assert_that_node.assertion_classification is not None
    assert assert_that_node.assertion_classification.role == AssertionRole.STATUS


def test_assertion_classification_assertThat_with_body_subject() -> None:
    """assertThat(response.getBody()).isEqualTo(...) classifies as BODY."""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertThat",
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=25,
            ),
            make_call_site(
                method_name="getBody",
                start_line=12,
                start_column=12,
                end_line=12,
                end_column=20,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=['"expected-body"'],
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=44,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    _classify_assertions(runtime_view)

    assert_that_node = [
        n
        for n in runtime_view.entries[0].grouping.nodes
        if n.call_site.method_name == "assertThat"
    ][0]
    assert assert_that_node.assertion_classification is not None
    assert assert_that_node.assertion_classification.role == AssertionRole.BODY


def test_assertion_classification_assertThrows_is_exception() -> None:
    """assertThrows should be classified as EXCEPTION role."""
    method = make_callable(
        call_sites=[
            make_call_site(method_name="assertThrows", start_line=10),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    _classify_assertions(runtime_view)

    nodes = list(runtime_view.entries[0].grouping.nodes)
    assert nodes[0].assertion_classification is not None
    assert nodes[0].assertion_classification.role == AssertionRole.EXCEPTION


def test_assertion_classification_do_not_leak_from_equal_span_sibling_descendants() -> (
    None
):
    """isOk under verifyStatus (not an assertion context root) should not be classified.

    When assertTrue and verifyStatus have identical spans, they become siblings
    in the call-site tree. isOk nests under verifyStatus (not assertTrue),
    so it is NOT in an assertion context and should not be classified.
    This prevents false positives from equal-span collisions.
    """
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertTrue",
                argument_expr=["true"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=50,
            ),
            make_call_site(
                method_name="verifyStatus",
                argument_expr=["status"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=50,
            ),
            make_call_site(
                method_name="isOk",
                start_line=10,
                start_column=20,
                end_line=10,
                end_column=32,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    _classify_assertions(runtime_view)

    is_ok_nodes = [
        n
        for n in runtime_view.entries[0].grouping.nodes
        if n.call_site.method_name == "isOk"
    ]
    # isOk is under verifyStatus (equal-span sibling to assertTrue),
    # not under an assertion context root, so it should NOT be classified
    assert len(is_ok_nodes) == 1
    assert is_ok_nodes[0].assertion_classification is None


def test_assertion_classification_isOk_with_status_receiver_fallback() -> None:
    """isOk with StatusAssertions receiver type classified even without whitelisted root."""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="verifyStatus",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=30,
            ),
            make_call_site(
                method_name="isOk",
                receiver_type="org.springframework.test.web.reactive.server.StatusAssertions",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=39,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    # Annotate isOk node with HTTP response role to trigger Tier 1 classification
    nodes = list(runtime_view.entries[0].grouping.nodes)
    from gerbil.analysis.schema import HttpResponseRole

    annotate_node_http(
        nodes[1],
        http_method="UNKNOWN",
        path="",
        framework="webtestclient",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )

    _classify_assertions(runtime_view)

    is_ok_node = [
        n
        for n in runtime_view.entries[0].grouping.nodes
        if n.call_site.method_name == "isOk"
    ][0]
    assert is_ok_node.assertion_classification is not None
    assert is_ok_node.assertion_classification.role == AssertionRole.STATUS
    assert is_ok_node.assertion_classification.status_code == 200


# ── Surface classification tests ──────────────────────────────────────


def test_assertion_surface_resolves_status_receiver_expr_via_runtime_resolver() -> None:
    """isNotFound with StatusAssertions receiver yields status when annotated via HTTP response role."""
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.specification.RequestSpecification",
                start_line=5,
            ),
            make_call_site(
                method_name="isNotFound",
                receiver_expr="statusAssertions",
                receiver_type="org.springframework.test.web.reactive.server.StatusAssertions",
                start_line=10,
            ),
        ],
        variable_declarations=[
            make_variable_declaration(
                name="statusAssertions",
                type_name="org.springframework.test.web.reactive.server.StatusAssertions",
                start_line=8,
            )
        ],
    )
    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[0],
        http_method="GET",
        path="/users/404",
        framework="rest-assured",
    )
    # The isNotFound node is a status assertion from the HTTP classification
    annotate_node_http(
        nodes[1],
        http_method="UNKNOWN",
        path="",
        framework="webtestclient",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )

    surface = _classify_surface(runtime_view)

    assert surface.status_count >= 1


def test_assertion_surface_detects_status_from_andExpect_statusCode() -> None:
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="andExpect",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=40,
            ),
            make_call_site(
                method_name="statusCode",
                argument_expr=["200"],
                start_line=10,
                start_column=5,
                end_line=10,
                end_column=30,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[1],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )
    surface = _classify_surface(runtime_view)

    assert surface.status_count == 1 and surface.body_count == 0


def test_assertion_surface_detects_response_body_from_containsString_under_andExpect() -> (
    None
):
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="andExpect",
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=40,
            ),
            make_call_site(
                method_name="containsString",
                argument_expr=['"ok"'],
                start_line=12,
                start_column=5,
                end_line=12,
                end_column=30,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[1],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.BODY_ASSERTION,
    )
    surface = _classify_surface(runtime_view)

    assert surface.body_count == 1 and surface.status_count == 0


def test_assertion_surface_detects_response_body_from_body_under_andExpect() -> None:
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="andExpect",
                start_line=15,
                start_column=1,
                end_line=15,
                end_column=50,
            ),
            make_call_site(
                method_name="body",
                argument_expr=["equalTo(expectedPayload)"],
                start_line=15,
                start_column=5,
                end_line=15,
                end_column=40,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[1],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.BODY_ASSERTION,
    )
    surface = _classify_surface(runtime_view)

    assert surface.body_count == 1 and surface.status_count == 0


def test_assertion_surface_mixed_status_and_body() -> None:
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="andExpect",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=50,
            ),
            make_call_site(
                method_name="isOk",
                start_line=10,
                start_column=5,
                end_line=10,
                end_column=15,
            ),
            make_call_site(
                method_name="jsonPath",
                argument_expr=["$.name"],
                start_line=10,
                start_column=20,
                end_line=10,
                end_column=40,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[1],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )
    annotate_node_http(
        nodes[2],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.BODY_ASSERTION,
    )
    surface = _classify_surface(runtime_view)

    assert surface.status_count >= 1
    assert surface.body_count >= 1


def test_request_body_call_not_classified_as_body_assertion() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="given",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=7,
            ),
            make_call_site(
                method_name="body",
                argument_expr=["payload"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=21,
            ),
            make_call_site(
                method_name="post",
                argument_expr=['"/api/users"'],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=40,
            ),
            make_call_site(
                method_name="then",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=47,
            ),
            make_call_site(
                method_name="statusCode",
                argument_expr=["201"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=63,
            ),
        ],
    )
    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[2], http_method="POST", path="/api/users", framework="rest-assured"
    )

    surface = _classify_surface(runtime_view)

    # The body() call here is a request body setter, not a response body assertion
    assert surface.body_count == 0


def test_assertion_surface_detects_status_from_mockmvc_isFound_chain() -> None:
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=30,
            ),
            make_call_site(
                method_name="andExpect",
                receiver_type="org.springframework.test.web.servlet.ResultActions",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=59,
            ),
            make_call_site(
                method_name="status",
                receiver_type="org.springframework.test.web.servlet.result.MockMvcResultMatchers",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=39,
            ),
            make_call_site(
                method_name="isFound",
                receiver_type="org.springframework.test.web.servlet.result.StatusResultMatchers",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=49,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(nodes[0], http_method="UNKNOWN", path="", framework="mockmvc")
    annotate_node_http(
        nodes[3],
        http_method="UNKNOWN",
        path="",
        framework="mockmvc",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )

    surface = _classify_surface(runtime_view)

    assert surface.status_count >= 1


def test_assertion_surface_detects_status_from_mockmvc_andExpectAll_chain() -> None:
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=30,
            ),
            make_call_site(
                method_name="andExpectAll",
                receiver_type="org.springframework.test.web.servlet.ResultActions",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=70,
            ),
            make_call_site(
                method_name="status",
                receiver_type="org.springframework.test.web.servlet.result.MockMvcResultMatchers",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=40,
            ),
            make_call_site(
                method_name="isOk",
                receiver_type="org.springframework.test.web.servlet.result.StatusResultMatchers",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=48,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(nodes[0], http_method="UNKNOWN", path="", framework="mockmvc")
    annotate_node_http(
        nodes[3],
        http_method="UNKNOWN",
        path="",
        framework="mockmvc",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )

    surface = _classify_surface(runtime_view)

    assert surface.status_count >= 1


def test_assertion_surface_detects_status_from_webtestclient_expectStatus_chain() -> (
    None
):
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="exchange",
                receiver_type="org.springframework.test.web.reactive.server.WebTestClient$RequestHeadersSpec",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=24,
            ),
            make_call_site(
                method_name="expectStatus",
                receiver_type="org.springframework.test.web.reactive.server.WebTestClient$ResponseSpec",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=38,
            ),
            make_call_site(
                method_name="isOk",
                receiver_type="org.springframework.test.web.reactive.server.StatusAssertions",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=46,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[0], http_method="GET", path="/api/users/1", framework="webtestclient"
    )
    annotate_node_http(
        nodes[2],
        http_method="GET",
        path="/api/users/1",
        framework="webtestclient",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )

    surface = _classify_surface(runtime_view)

    assert surface.status_count >= 1


def test_assertion_surface_detects_wrapper_status_matcher_with_status_subject() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="get",
                argument_expr=['"/api/users/1"'],
                start_line=5,
                start_column=1,
                end_line=5,
                end_column=18,
            ),
            make_call_site(
                method_name="assertThat",
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=30,
            ),
            make_call_site(
                method_name="getStatusCode",
                start_line=12,
                start_column=12,
                end_line=12,
                end_column=28,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=["HttpStatus.OK"],
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=52,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[0], http_method="GET", path="/api/users/1", framework="rest-assured"
    )

    surface = _classify_surface(runtime_view)

    assert surface.status_count >= 1


def test_assertion_surface_detects_wrapper_body_matcher_with_body_subject() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="get",
                argument_expr=['"/api/users/1"'],
                start_line=5,
                start_column=1,
                end_line=5,
                end_column=18,
            ),
            make_call_site(
                method_name="assertThat",
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=25,
            ),
            make_call_site(
                method_name="getBody",
                start_line=12,
                start_column=12,
                end_line=12,
                end_column=20,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=['"expected-body"'],
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=44,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[0], http_method="GET", path="/api/users/1", framework="rest-assured"
    )

    surface = _classify_surface(runtime_view)

    assert surface.body_count >= 1


def test_assertion_surface_wrapper_matchers_without_subject_hints_not_classified() -> (
    None
):
    """assertThat(namespace).isEqualTo(expected) without status/body subject hints produces no labels."""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="get",
                argument_expr=['"/api/users/1"'],
                start_line=5,
                start_column=1,
                end_line=5,
                end_column=18,
            ),
            make_call_site(
                method_name="assertThat",
                argument_expr=["namespace"],
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=22,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=["expectedNamespace"],
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=43,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[0], http_method="GET", path="/api/users/1", framework="rest-assured"
    )

    surface = _classify_surface(runtime_view)

    assert surface.status_count == 0
    assert surface.body_count == 0
    assert surface.header_count == 0


def test_assertion_surface_empty_runtime_view_produces_empty_labels() -> None:
    surface = _classify_surface(TestRuntimeView())

    assert surface == AssertionSummary()


def test_cross_owner_chain_role_map_does_not_merge_setup_and_test_calls() -> None:
    """Regression: per-owner grouping prevents cross-owner chain contamination.

    Setup has a standalone ``body("payload")`` at line 10 col 1.
    Test has a fluent chain ``given().body(payload).post("/api").then().statusCode(201)``
    all sharing start position line 10 col 1.

    With per-owner grouping each entry is processed independently.
    """
    setup_body_call_site = make_call_site(
        method_name="body",
        argument_expr=["payload"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=21,
    )
    setup_method = make_callable(
        signature="beforeEach()",
        call_sites=[setup_body_call_site],
    )

    test_given_call_site = make_call_site(
        method_name="given",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=7,
    )
    test_body_call_site = make_call_site(
        method_name="body",
        argument_expr=["payload"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=21,
    )
    test_post_call_site = make_call_site(
        method_name="post",
        argument_expr=['"/api"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )
    test_then_call_site = make_call_site(
        method_name="then",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=47,
    )
    test_status_call_site = make_call_site(
        method_name="statusCode",
        argument_expr=["201"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=63,
    )
    test_method = make_callable(
        call_sites=[
            test_given_call_site,
            test_body_call_site,
            test_post_call_site,
            test_then_call_site,
            test_status_call_site,
        ],
    )

    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.SETUP,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="beforeEach()",
                ),
                context_class_name="example.ApiTest",
                grouping=build_call_site_grouping(list(setup_method.call_sites)),
                method_details=setup_method,
            ),
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="testCase()",
                ),
                context_class_name="example.ApiTest",
                grouping=build_call_site_grouping(list(test_method.call_sites)),
                method_details=test_method,
            ),
        ],
    )

    test_nodes = list(runtime_view.entries[1].grouping.nodes)
    annotate_node_http(
        test_nodes[2], http_method="POST", path="/api", framework="rest-assured"
    )

    surface = _classify_surface(runtime_view)

    # The setup's body("payload") is not response-body; the test's statusCode is status
    assert surface.body_count == 0


def test_cross_owner_chain_role_map_avoids_legacy_get_collision_without_owner_metadata() -> (
    None
):
    setup_body_call_site = make_call_site(
        method_name="body",
        argument_expr=["payload"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=21,
    )
    setup_non_http_get_call_site = make_call_site(
        method_name="get",
        receiver_type="java.util.List",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=35,
    )
    setup_method = make_callable(
        signature="beforeEach()",
        call_sites=[setup_body_call_site, setup_non_http_get_call_site],
    )

    test_http_get_call_site = make_call_site(
        method_name="get",
        receiver_type="io.restassured.specification.RequestSpecification",
        argument_expr=['"/api/users/1"'],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=18,
    )
    test_method = make_callable(
        call_sites=[test_http_get_call_site],
    )

    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.SETUP,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="beforeEach()",
                ),
                context_class_name="example.ApiTest",
                grouping=build_call_site_grouping(list(setup_method.call_sites)),
                method_details=setup_method,
            ),
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="testCase()",
                ),
                context_class_name="example.ApiTest",
                grouping=build_call_site_grouping(list(test_method.call_sites)),
                method_details=test_method,
            ),
        ],
    )

    test_nodes = list(runtime_view.entries[1].grouping.nodes)
    annotate_node_http(
        test_nodes[0], http_method="GET", path="/api/users/1", framework="rest-assured"
    )

    surface = _classify_surface(runtime_view)

    # No assertion nodes in this view, so no labels
    assert surface == AssertionSummary()


def test_cross_owner_status_context_does_not_leak_on_position_collision() -> None:
    setup_method = make_callable(
        signature="beforeEach()",
        call_sites=[
            make_call_site(
                method_name="isUnprocessableEntity",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=35,
            )
        ],
    )

    test_method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertTrue",
                argument_expr=["flag"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=20,
            )
        ]
    )

    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.SETUP,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="beforeEach()",
                ),
                context_class_name="example.ApiTest",
                grouping=build_call_site_grouping(list(setup_method.call_sites)),
                method_details=setup_method,
            ),
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="testCase()",
                ),
                context_class_name="example.ApiTest",
                grouping=build_call_site_grouping(list(test_method.call_sites)),
                method_details=test_method,
            ),
        ]
    )

    surface = _classify_surface(runtime_view)

    # Setup's isUnprocessableEntity should not leak status classification into the test
    assert surface.status_count == 0
    assert surface.body_count == 0
    assert surface.header_count == 0


def test_assertion_surface_generic_matchers_in_status_context() -> None:
    """andExpect().status().is(200) via HTTP annotation and assertThat(getStatusCode()).isEqualTo(404) yield status."""
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="andExpect",
                start_line=20,
                start_column=1,
                end_line=20,
                end_column=40,
            ),
            make_call_site(
                method_name="status",
                start_line=20,
                start_column=12,
                end_line=20,
                end_column=20,
            ),
            make_call_site(
                method_name="is",
                argument_expr=["200"],
                start_line=20,
                start_column=22,
                end_line=20,
                end_column=28,
            ),
            make_call_site(
                method_name="assertThat",
                start_line=30,
                start_column=1,
                end_line=30,
                end_column=30,
            ),
            make_call_site(
                method_name="getStatusCode",
                start_line=30,
                start_column=12,
                end_line=30,
                end_column=27,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=["404"],
                start_line=30,
                start_column=1,
                end_line=30,
                end_column=45,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    # Annotate the status/is nodes in the andExpect chain via HTTP
    annotate_node_http(
        nodes[1],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )
    annotate_node_http(
        nodes[2],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.MATCHER,
    )
    surface = _classify_surface(runtime_view)

    assert surface.status_count >= 1


def test_assertion_surface_generic_matchers_nested_under_statusCode() -> None:
    """statusCode().is(201) with HTTP annotation yields status."""
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="andExpect",
                start_line=40,
                start_column=1,
                end_line=40,
                end_column=40,
            ),
            make_call_site(
                method_name="statusCode",
                start_line=40,
                start_column=5,
                end_line=40,
                end_column=30,
            ),
            make_call_site(
                method_name="is",
                argument_expr=["201"],
                start_line=40,
                start_column=15,
                end_line=40,
                end_column=21,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[1],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )
    annotate_node_http(
        nodes[2],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.MATCHER,
    )
    surface = _classify_surface(runtime_view)

    assert surface.status_count >= 1


# ── Oracle type tests ─────────────────────────────────────────────────


def test_oracle_type_returns_implicit_without_any_signals() -> None:
    result = _classify_oracle(TestRuntimeView(), None)

    assert result.label == "implicit"


def test_oracle_type_example_based_from_classified_assertion() -> None:
    from gerbil.analysis.schema import HttpResponseRole

    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="andExpect",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=40,
            ),
            make_call_site(
                method_name="statusCode",
                argument_expr=["200"],
                start_line=10,
                start_column=5,
                end_line=10,
                end_column=30,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[1],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )
    result = _classify_oracle(runtime_view, method)

    assert result.label == "example-based"


def test_oracle_type_contract_from_matchesJsonSchemaInClasspath() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="andExpect",
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=50,
            ),
            make_call_site(
                method_name="matchesJsonSchemaInClasspath",
                start_line=12,
                start_column=5,
                end_line=12,
                end_column=40,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    # matchesJsonSchemaInClasspath needs manual annotation since it is a contract
    # hint but not in the standard assertion category methods
    for node in runtime_view.entries[0].grouping.nodes:
        if node.call_site.method_name == "matchesJsonSchemaInClasspath":
            node.assertion_classification = AssertionClassification(
                role=AssertionRole.BODY,
            )

    result = _classify_oracle(runtime_view, method)

    assert result.label == "contract"
    assert "example-based" in result.signals


def test_oracle_type_contract_from_pact_receiver() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="uponReceiving",
                receiver_type="au.com.dius.pact.consumer.dsl.PactDslWithProvider",
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)

    assert result.label == "contract"


def test_oracle_type_property_based_from_distinct_method() -> None:
    for method_name in ["forAll", "qt"]:
        method = make_callable(call_sites=[make_call_site(method_name=method_name)])
        result = _classify_oracle(_runtime_view_for_method(method), method)

        assert result.label == "property-based", method_name


def test_oracle_type_check_requires_property_framework_context() -> None:
    check_without_context = make_callable(
        call_sites=[make_call_site(method_name="check")]
    )

    result_without = _classify_oracle(
        _runtime_view_for_method(check_without_context),
        check_without_context,
    )

    assert result_without.label != "property-based"

    check_with_context = make_callable(
        call_sites=[
            make_call_site(
                method_name="check",
                receiver_type="net.jqwik.api.PropertyChecker",
                callee_signature="net.jqwik.api.PropertyChecker.check()",
            )
        ]
    )

    result_with = _classify_oracle(
        _runtime_view_for_method(check_with_context),
        check_with_context,
    )

    assert result_with.label == "property-based"


def test_oracle_type_does_not_mark_property_for_isEqualTo_only() -> None:
    method = make_callable(
        call_sites=[make_call_site(method_name="isEqualTo", start_line=10)]
    )

    result = _classify_oracle(_runtime_view_for_method(method), method)

    assert result.label != "property-based"


# ── Cache bounding tests ─────────────────────────────────────────────


def test_assertion_analysis_class_resolution_cache_is_bounded() -> None:
    receiver_hierarchy_cache_module.reset_class_resolution_cache()

    try:
        analyses = [
            FakeJavaAnalysis(classes={f"example.Repository{index}": make_type()})
            for index in range(
                receiver_hierarchy_cache_module.CLASS_RESOLUTION_CACHE_MAX_ENTRIES + 10
            )
        ]
        for index, analysis in enumerate(analyses):
            receiver_hierarchy_cache_module.get_receiver_hierarchy(
                receiver_type=f"example.Repository{index}",
                analysis=analysis,
            )

        assert (
            0
            < len(receiver_hierarchy_cache_module.CLASS_RESOLUTION_CACHE)
            <= (receiver_hierarchy_cache_module.CLASS_RESOLUTION_CACHE_MAX_ENTRIES)
        )
    finally:
        receiver_hierarchy_cache_module.reset_class_resolution_cache()


def test_assertion_analysis_cache_bounding_cleanup_isolates_neighboring_tests() -> None:
    assert len(receiver_hierarchy_cache_module.CLASS_RESOLUTION_CACHE) == 0
