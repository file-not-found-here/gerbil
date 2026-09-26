from __future__ import annotations

from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.schema import (
    AssertionAnalysis,
    HttpResponseRole,
    LifecyclePhase,
    StatusCodeDistribution,
)
from gerbil.analysis.assertion import classify_assertions_on_runtime_view
from gerbil.analysis.properties.assertion.status_distribution import (
    build_status_code_counts,
    build_status_code_distribution,
)
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
) -> StatusCodeDistribution:
    if annotate_http_fn is not None:
        annotate_http_fn(runtime_view.entries[0].grouping.nodes)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )
    return build_status_code_distribution(runtime_view=runtime_view)


def test_empty_runtime_view_returns_zero_counts() -> None:
    result = build_status_code_distribution(runtime_view=TestRuntimeView())
    assert result == StatusCodeDistribution()


def test_single_2xx_status_assertion() -> None:
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
                method_name="isOk",
                start_line=10,
                start_column=5,
                end_line=10,
                end_column=15,
            ),
        ]
    )
    result = _classify(
        _runtime_view_for_method(method),
        annotate_http_fn=lambda ns: annotate_node_http(
            next(n for n in ns if n.call_site.method_name == "isOk"),
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        ),
    )
    assert result.range_2xx == 1
    assert result.range_4xx == 0
    assert result.range_5xx == 0
    assert result.unknown == 0


def test_mixed_status_buckets() -> None:
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
    result = _classify(_runtime_view_for_method(method))
    assert result.range_3xx >= 1
    assert result.range_4xx >= 1
    assert result.range_5xx >= 1
    assert result.range_2xx == 0


def test_domain_status_method_predicate_not_in_status_distribution() -> None:
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
        ]
    )

    result = _classify(_runtime_view_for_method(method))

    assert result == StatusCodeDistribution()


def test_range_only_assertions_tally_from_status_range() -> None:
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
    result = _classify(_runtime_view_for_method(method))
    assert result.range_2xx >= 1
    assert result.range_4xx >= 1


def test_status_assertion_without_code_or_range_is_unknown() -> None:
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
                argument_expr=["someVariable"],
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
    assert result.unknown == 1
    assert result.range_2xx == 0


def test_assertthat_status_subject_chain_counts_exact_status_without_unknown() -> None:
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
        ]
    )

    result = _classify(_runtime_view_for_method(method))

    assert result.range_4xx == 1
    assert result.unknown == 0


def test_assertall_nested_status_assertion_counts_once() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertAll",
                argument_expr=["() -> assertEquals(404, resp.getStatusCode())"],
                start_line=10,
                start_column=1,
                end_line=12,
                end_column=2,
            ),
            make_call_site(
                method_name="assertEquals",
                argument_expr=["404", "resp.getStatusCode()"],
                start_line=11,
                start_column=9,
                end_line=11,
                end_column=60,
            ),
            make_call_site(
                method_name="getStatusCode",
                start_line=11,
                start_column=30,
                end_line=11,
                end_column=55,
            ),
        ]
    )

    result = _classify(_runtime_view_for_method(method))

    assert result.range_4xx == 1
    assert result.range_2xx == 0
    assert result.unknown == 0


def test_negated_status_equality_does_not_tally_rejected_code_bucket() -> None:
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
                method_name="isNotEqualTo",
                argument_expr=["404"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=48,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    result = build_status_code_distribution(runtime_view=runtime_view)
    assert result.range_4xx == 0
    assert result.unknown == 1
    assert build_status_code_counts(runtime_view=runtime_view) == {}


def test_assertthat_hamcrest_status_matcher_counts_exact_status() -> None:
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
                method_name="getStatusCode",
                start_line=10,
                start_column=12,
                end_line=10,
                end_column=28,
            ),
            make_call_site(
                method_name="equalTo",
                argument_expr=["404"],
                start_line=10,
                start_column=31,
                end_line=10,
                end_column=52,
            ),
        ]
    )

    result = _classify(_runtime_view_for_method(method))

    assert result.range_4xx == 1
    assert result.unknown == 0


def test_non_status_assertions_ignored() -> None:
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
                method_name="jsonPath",
                argument_expr=["$.name"],
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
            next(n for n in ns if n.call_site.method_name == "jsonPath"),
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.BODY_ASSERTION,
        ),
    )
    assert result == StatusCodeDistribution()


def test_multiple_2xx_assertions_accumulated() -> None:
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
                method_name="isCreated",
                start_line=12,
                start_column=5,
                end_line=12,
                end_column=20,
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
            next(n for n in ns if n.call_site.method_name == "isCreated"),
            http_method="POST",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        )

    result = _classify(_runtime_view_for_method(method), annotate_http_fn=_annotate)
    assert result.range_2xx == 2


def test_exact_status_code_counts_accumulate_known_codes() -> None:
    status_codes = [200, 201, 204, 400, 401, 404, 500, 200]
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="statusCode",
                argument_expr=[str(status_code)],
                start_line=index + 10,
                start_column=5,
                end_line=index + 10,
                end_column=30,
            )
            for index, status_code in enumerate(status_codes)
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    for node in runtime_view.entries[0].grouping.nodes:
        annotate_node_http(
            node,
            http_method="GET",
            path="/api",
            request_role=None,
            response_role=HttpResponseRole.STATUS_ASSERTION,
        )
    build_runtime_receiver_resolver_for_testing(runtime_view)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    assert build_status_code_counts(runtime_view=runtime_view) == {
        "200": 2,
        "201": 1,
        "204": 1,
        "400": 1,
        "401": 1,
        "404": 1,
        "500": 1,
    }


def test_range_only_status_assertions_do_not_invent_exact_codes() -> None:
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
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    build_runtime_receiver_resolver_for_testing(runtime_view)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    assert build_status_code_counts(runtime_view=runtime_view) == {}


def test_assertion_analysis_derives_status_range_counts_map() -> None:
    distribution = StatusCodeDistribution(range_2xx=2, range_4xx=1, unknown=1)

    assertions = AssertionAnalysis(
        status_code_distribution=distribution,
        status_code_counts={"200": 2, "404": 1},
    )

    assert assertions.status_code_counts == {"200": 2, "404": 1}
    assert assertions.status_range_counts == {
        "1xx": 0,
        "2xx": 2,
        "3xx": 0,
        "4xx": 1,
        "5xx": 0,
        "unknown": 1,
    }


def test_getResponseCode_httpurlconnection_constant_counts_exact_status() -> None:
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
        ]
    )

    result = _classify(_runtime_view_for_method(method))
    assert result.range_2xx == 1
    assert result.unknown == 0


def test_negated_assertion_root_does_not_tally_rejected_code() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertNotEquals",
                argument_expr=[
                    "HttpStatus.INTERNAL_SERVER_ERROR",
                    "response.getStatusCode()",
                ],
                argument_types=["org.springframework.http.HttpStatus", "int"],
                start_line=10,
                start_column=1,
                end_line=10,
                end_column=80,
            ),
            make_call_site(
                method_name="getStatusCode",
                start_line=10,
                start_column=60,
                end_line=10,
                end_column=78,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    result = _classify(runtime_view)
    assert result.range_5xx == 0
    assert result.unknown == 1
    assert build_status_code_counts(runtime_view=runtime_view) == {}
