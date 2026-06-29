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


def _classify_and_get_status_codes(call_sites, *, annotate_http_fn=None) -> set[int]:
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
    if annotate_http_fn is not None:
        annotate_http_fn(runtime_view.entries[0].grouping.nodes)

    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    codes: set[int] = set()
    for node in runtime_view.entries[0].grouping.nodes:
        ac = node.assertion_classification
        if (
            ac is not None
            and ac.role == AssertionRole.STATUS
            and ac.status_code is not None
        ):
            codes.add(ac.status_code)
    return codes


def _annotate_status_nodes(nodes, method_names):
    """Annotate nodes matching method_names with HTTP STATUS_ASSERTION role."""
    for node in nodes:
        if node.call_site.method_name in method_names:
            annotate_node_http(
                node,
                http_method="GET",
                path="/api",
                request_role=None,
                response_role=HttpResponseRole.STATUS_ASSERTION,
            )


def test_convenience_method_status_codes() -> None:
    # Wrap status convenience methods with HTTP annotations (Tier 1)
    call_sites = [
        make_call_site(
            method_name="andExpect",
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=50,
        ),
        make_call_site(
            method_name="isOk",
            start_line=1,
            start_column=5,
            end_line=1,
            end_column=15,
        ),
        make_call_site(
            method_name="isNotFound",
            start_line=1,
            start_column=20,
            end_line=1,
            end_column=35,
        ),
    ]
    codes = _classify_and_get_status_codes(
        call_sites,
        annotate_http_fn=lambda ns: _annotate_status_nodes(ns, {"isOk", "isNotFound"}),
    )
    assert 200 in codes
    assert 404 in codes


def test_isCreated_status_code() -> None:
    call_sites = [
        make_call_site(
            method_name="andExpect",
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=40,
        ),
        make_call_site(
            method_name="isCreated",
            start_line=1,
            start_column=5,
            end_line=1,
            end_column=20,
        ),
    ]
    codes = _classify_and_get_status_codes(
        call_sites,
        annotate_http_fn=lambda ns: _annotate_status_nodes(ns, {"isCreated"}),
    )
    assert codes == {201}


def test_statusCode_method_classified_but_no_code_hint() -> None:
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
            argument_expr=["200"],
            start_line=1,
            start_column=5,
            end_line=1,
            end_column=20,
        ),
    ]
    codes = _classify_and_get_status_codes(
        call_sites,
        annotate_http_fn=lambda ns: _annotate_status_nodes(ns, {"statusCode"}),
    )
    # statusCode with argument_expr=["200"] now extracts the code via argument parsing
    assert codes == {200}


def test_statusCode_with_httpstatus_ok_argument() -> None:
    """statusCode(HttpStatus.OK) with tier 1 HTTP annotation → codes == {200}."""
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
            argument_expr=["HttpStatus.OK"],
            start_line=1,
            start_column=5,
            end_line=1,
            end_column=20,
        ),
    ]
    codes = _classify_and_get_status_codes(
        call_sites,
        annotate_http_fn=lambda ns: _annotate_status_nodes(ns, {"statusCode"}),
    )
    assert codes == {200}


def test_statusCode_with_hamcrest_equalto_matcher_extracts_code() -> None:
    """statusCode(equalTo(404)) / statusCode(is(404)) → the wrapped code resolves."""
    for matcher_expr, expected in (
        ("equalTo(404)", 404),
        ("is(201)", 201),
        ("equalTo(HttpStatus.SC_NOT_FOUND)", 404),
    ):
        call_sites = [
            make_call_site(
                method_name="then",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="statusCode",
                argument_expr=[matcher_expr],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
            ),
        ]
        codes = _classify_and_get_status_codes(
            call_sites,
            annotate_http_fn=lambda ns: _annotate_status_nodes(ns, {"statusCode"}),
        )
        assert codes == {expected}, matcher_expr


def test_statusCode_with_bare_httpstatus_constant_resolves_with_type() -> None:
    """A statically-imported HttpStatus.NOT_FOUND (bare NOT_FOUND) resolves only
    when CLDK types the argument as the status enum."""
    for argument_type, expect in (
        ("org.springframework.http.HttpStatus", {404}),
        ("jakarta.ws.rs.core.Response.Status", {404}),
        ("com.example.DomainEnum", set()),
    ):
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
                argument_expr=["NOT_FOUND"],
                argument_types=[argument_type],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=20,
            ),
        ]
        codes = _classify_and_get_status_codes(
            call_sites,
            annotate_http_fn=lambda ns: _annotate_status_nodes(ns, {"statusCode"}),
        )
        assert codes == expect, argument_type


def test_tier2_assertEquals_extracts_integer_in_status_context() -> None:
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
    codes = _classify_and_get_status_codes(
        [assert_eq, get_status_code],
    )
    assert codes == {200}


def test_tier2_assertThat_isEqualTo_extracts_integer_in_status_context() -> None:
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
        argument_expr=["404"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=55,
    )

    codes = _classify_and_get_status_codes(
        [assert_that, get_status_code, is_equal_to],
    )
    assert codes == {404}


def test_tier2_assertThat_isEqualTo_extracts_httpstatus_constant() -> None:
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
        argument_expr=["HttpStatus.NOT_FOUND"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=55,
    )

    codes = _classify_and_get_status_codes(
        [assert_that, get_status_code, is_equal_to],
    )
    assert codes == {404}


def test_tier2_hamcrest_equalTo_extracts_integer_in_status_context() -> None:
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
        argument_expr=["500"],
        start_line=10,
        start_column=33,
        end_line=10,
        end_column=47,
    )

    codes = _classify_and_get_status_codes(
        [assert_that, get_status_code, is_matcher, equal_to],
    )
    assert codes == {500}


def test_tier2_numeric_generic_assertion_without_status_subject_is_not_status() -> None:
    assert_that = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=30,
    )
    get_age = make_call_site(
        method_name="getAge",
        start_line=10,
        start_column=12,
        end_line=10,
        end_column=20,
    )
    is_equal_to = make_call_site(
        method_name="isEqualTo",
        argument_expr=["404"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=45,
    )

    codes = _classify_and_get_status_codes(
        [assert_that, get_age, is_equal_to],
    )
    assert codes == set()


def test_tier2_status_context_non_value_matcher_does_not_extract_integer() -> None:
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
    described_as = make_call_site(
        method_name="describedAs",
        argument_expr=["404"],
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=45,
    )

    codes = _classify_and_get_status_codes(
        [assert_that, get_status_code, described_as],
    )
    assert codes == set()
