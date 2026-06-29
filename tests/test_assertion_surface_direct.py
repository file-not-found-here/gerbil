from __future__ import annotations

from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.schema import (
    AssertionAnalysis,
    AssertionRole,
    AssertionSummary,
    HttpResponseRole,
    LifecyclePhase,
)
from gerbil.analysis.properties.assertion.surface import build_assertion_summary
from gerbil.analysis.assertion import classify_assertions_on_runtime_view
from tests.cldk_factories import (
    annotate_node_http,
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
)


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


def _classify(
    runtime_view: TestRuntimeView, *, annotate_http_fn=None
) -> AssertionSummary:
    if annotate_http_fn is not None:
        annotate_http_fn(runtime_view.entries[0].grouping.nodes)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )
    return build_assertion_summary(runtime_view=runtime_view)


def test_classify_assertion_surface_status() -> None:
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

    result = _classify(
        _runtime_view_for_method(method),
        annotate_http_fn=lambda ns: annotate_node_http(
            next(n for n in ns if n.call_site.method_name == "statusCode"),
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        ),
    )

    assert result.status_count == 1
    assert result.body_count == 0
    assert result.header_count == 0


def test_classify_assertion_surface_response_body() -> None:
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

    result = _classify(
        _runtime_view_for_method(method),
        annotate_http_fn=lambda ns: annotate_node_http(
            next(n for n in ns if n.call_site.method_name == "containsString"),
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.BODY_ASSERTION,
        ),
    )

    assert result.body_count == 1
    assert result.status_count == 0


def test_classify_assertion_surface_body_full() -> None:
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

    result = _classify(
        _runtime_view_for_method(method),
        annotate_http_fn=lambda ns: annotate_node_http(
            next(n for n in ns if n.call_site.method_name == "body"),
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.BODY_ASSERTION,
        ),
    )

    assert result.body_count == 1
    assert result.status_count == 0


def test_classify_assertion_surface_mixed() -> None:
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

    def _annotate(ns):
        annotate_node_http(
            next(n for n in ns if n.call_site.method_name == "isOk"),
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        )
        annotate_node_http(
            next(n for n in ns if n.call_site.method_name == "jsonPath"),
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.BODY_ASSERTION,
        )

    runtime_view = _runtime_view_for_method(method)
    result = _classify(runtime_view, annotate_http_fn=_annotate)

    assert result.status_count >= 1
    assert result.body_count >= 1


def test_classify_assertion_surface_response_header() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="contentType",
                argument_expr=['"application/json"'],
                start_line=10,
                start_column=5,
                end_line=10,
                end_column=30,
            ),
        ]
    )

    result = _classify(
        _runtime_view_for_method(method),
        annotate_http_fn=lambda ns: annotate_node_http(
            ns[0],
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.HEADER_ASSERTION,
        ),
    )

    assert result.header_count == 1
    assert result.status_count == 0
    assert result.body_count == 0


def test_classify_assertion_surface_mixed_status_header() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="isOk",
                start_line=10,
                start_column=5,
                end_line=10,
                end_column=15,
            ),
            make_call_site(
                method_name="contentType",
                argument_expr=['"application/json"'],
                start_line=12,
                start_column=5,
                end_line=12,
                end_column=30,
            ),
        ]
    )

    def _annotate(ns):
        annotate_node_http(
            next(n for n in ns if n.call_site.method_name == "isOk"),
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        )
        annotate_node_http(
            next(n for n in ns if n.call_site.method_name == "contentType"),
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.HEADER_ASSERTION,
        )

    runtime_view = _runtime_view_for_method(method)
    result = _classify(runtime_view, annotate_http_fn=_annotate)

    assert result.status_count >= 1
    assert result.header_count >= 1


def test_assertthat_response_subject_chain_counts_only_terminal_verifier() -> None:
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
                argument_expr=["404"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=45,
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
                argument_expr=['"ok"'],
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=44,
            ),
            make_call_site(
                method_name="assertThat",
                start_line=14,
                start_column=1,
                end_line=14,
                end_column=30,
            ),
            make_call_site(
                method_name="getHeader",
                start_line=14,
                start_column=12,
                end_line=14,
                end_column=25,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=['"application/json"'],
                start_line=14,
                start_column=1,
                end_line=14,
                end_column=60,
            ),
        ]
    )

    result = _classify(_runtime_view_for_method(method))

    assert result.status_count == 1
    assert result.body_count == 1
    assert result.header_count == 1
    assert result.general_count == 0


def test_asserttrue_response_body_predicate_counts_once() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertTrue",
                argument_expr=['response.getBody().contains("ok")'],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=60,
            ),
            make_call_site(
                method_name="contains",
                argument_expr=['"ok"'],
                start_line=10,
                start_column=12,
                end_line=10,
                end_column=50,
            ),
            make_call_site(
                method_name="getBody",
                start_line=10,
                start_column=12,
                end_line=10,
                end_column=27,
            ),
        ]
    )

    result = _classify(_runtime_view_for_method(method))

    assert result.body_count == 1
    assert result.general_count == 0
    assert result.total_count == 1


def test_assertthat_terminal_description_counts_zero() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertThat",
                argument_expr=["response"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=22,
            ),
            make_call_site(
                method_name="as",
                argument_expr=['"response"'],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=38,
            ),
        ]
    )

    result = _classify(_runtime_view_for_method(method))

    assert result.total_count == 0


def test_assertthat_hamcrest_body_matcher_counts_once() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertThat",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=55,
            ),
            make_call_site(
                method_name="getBody",
                start_line=10,
                start_column=12,
                end_line=10,
                end_column=28,
            ),
            make_call_site(
                method_name="containsString",
                argument_expr=['"ok"'],
                start_line=10,
                start_column=31,
                end_line=10,
                end_column=52,
            ),
        ]
    )

    result = _classify(_runtime_view_for_method(method))

    assert result.body_count == 1
    assert result.total_count == 1


def test_assertthat_hamcrest_header_matcher_counts_once() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertThat",
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=70,
            ),
            make_call_site(
                method_name="getHeader",
                start_line=10,
                start_column=12,
                end_line=10,
                end_column=35,
            ),
            make_call_site(
                method_name="equalTo",
                argument_expr=['"application/json"'],
                start_line=10,
                start_column=38,
                end_line=10,
                end_column=68,
            ),
        ]
    )

    result = _classify(_runtime_view_for_method(method))

    assert result.header_count == 1
    assert result.total_count == 1


def test_empty_runtime_view_returns_zero_counts() -> None:
    result = build_assertion_summary(runtime_view=TestRuntimeView())
    assert result == AssertionSummary()


def test_multiple_status_assertions_counted() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="statusCode1",
                argument_expr=["200"],
                start_line=10,
                start_column=5,
                end_line=10,
                end_column=30,
            ),
            make_call_site(
                method_name="statusCode2",
                argument_expr=["201"],
                start_line=12,
                start_column=5,
                end_line=12,
                end_column=30,
            ),
        ]
    )

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
            response_role=HttpResponseRole.STATUS_ASSERTION,
        )

    runtime_view = _runtime_view_for_method(method)
    result = _classify(runtime_view, annotate_http_fn=_annotate)

    assert result.status_count == 2


def test_assertion_analysis_derives_m7_response_surface_fields() -> None:
    cases = [
        (
            AssertionSummary(status_count=1),
            [AssertionRole.STATUS],
            "status-only",
        ),
        (
            AssertionSummary(body_count=1),
            [AssertionRole.BODY],
            "body-only",
        ),
        (
            AssertionSummary(header_count=1),
            [AssertionRole.HEADER],
            "header-only",
        ),
        (
            AssertionSummary(status_count=1, body_count=1),
            [AssertionRole.STATUS, AssertionRole.BODY],
            "status+body",
        ),
        (
            AssertionSummary(status_count=1, body_count=1, header_count=1),
            [AssertionRole.STATUS, AssertionRole.BODY, AssertionRole.HEADER],
            "status+body+header",
        ),
        (
            AssertionSummary(),
            [],
            "none",
        ),
    ]

    for summary, labels, combination in cases:
        assertions = AssertionAnalysis(summary=summary)
        assert assertions.response_surface_labels == labels
        assert assertions.response_surface_combination == combination
        assert assertions.has_status_check is (summary.status_count > 0)
        assert assertions.has_body_check is (summary.body_count > 0)
        assert assertions.has_header_check is (summary.header_count > 0)


def test_assertion_analysis_derives_exception_presence_flag() -> None:
    assertions = AssertionAnalysis(summary=AssertionSummary(exception_count=1))

    assert assertions.has_exception_check is True
    assert assertions.response_surface_labels == []
    assert assertions.response_surface_combination == "none"


def test_raw_client_response_accessors_count_in_surface() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertEquals",
                argument_expr=[
                    "HttpURLConnection.HTTP_OK",
                    "connection.getResponseCode()",
                ],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=70,
            ),
            make_call_site(
                method_name="getResponseCode",
                start_line=10,
                start_column=50,
                end_line=10,
                end_column=68,
            ),
            make_call_site(
                method_name="assertThat",
                start_line=11,
                start_column=1,
                end_line=11,
                end_column=40,
            ),
            make_call_site(
                method_name="readEntity",
                start_line=11,
                start_column=12,
                end_line=11,
                end_column=25,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=['"payload"'],
                start_line=11,
                start_column=1,
                end_line=11,
                end_column=55,
            ),
            make_call_site(
                method_name="assertThat",
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=45,
            ),
            make_call_site(
                method_name="getHeaderString",
                start_line=12,
                start_column=12,
                end_line=12,
                end_column=30,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=['"application/json"'],
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=60,
            ),
            make_call_site(
                method_name="assertThat",
                start_line=13,
                start_column=1,
                end_line=13,
                end_column=45,
            ),
            make_call_site(
                method_name="getFirstHeader",
                start_line=13,
                start_column=12,
                end_line=13,
                end_column=30,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=['"application/json"'],
                start_line=13,
                start_column=1,
                end_line=13,
                end_column=60,
            ),
        ]
    )

    result = _classify(_runtime_view_for_method(method))

    assert result.status_count == 1
    assert result.body_count == 1
    assert result.header_count == 2
    assert result.general_count == 0
