"""Per-request status-outcome attribution onto application endpoints."""

from __future__ import annotations

from gerbil.analysis.properties.endpoint.coverage import (
    build_endpoint_coverage_summary,
)
from gerbil.analysis.properties.sequence_analysis import (
    build_api_call_sequence,
    build_http_test_sequences,
)
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.schema import (
    ApiSequenceStep,
    ApplicationEndpoint,
    AssertionRole,
    CallSiteOriginKind,
    EndpointCoverageSummary,
    HttpAnalysis,
    HttpTestSequence,
    LifecyclePhase,
    MethodIdentity,
    OriginContext,
    SequenceStepKind,
    SourceSpan,
)
from gerbil.analysis.schema import TestClassAnalysis as ModelClassAnalysis
from gerbil.analysis.schema import TestMethodAnalysis as ModelMethodAnalysis
from tests.cldk_factories import (
    classify_runtime_view_for_testing,
    make_call_site,
    make_callable,
)


def _endpoint(http_method: str, path_template: str) -> ApplicationEndpoint:
    return ApplicationEndpoint(
        http_method=http_method,
        path_template=path_template,
        framework="spring",
        declaring_class_name="example.Controller",
    )


def _origin(
    phase: LifecyclePhase = LifecyclePhase.TEST,
    kind: CallSiteOriginKind = CallSiteOriginKind.TEST_METHOD,
) -> OriginContext:
    return OriginContext(phase=phase, kind=kind)


def _request_step(
    order: int,
    http_method: str | None,
    http_path: str | None,
    *,
    path_truncated: bool = False,
    phase: LifecyclePhase = LifecyclePhase.TEST,
    kind: CallSiteOriginKind = CallSiteOriginKind.TEST_METHOD,
) -> ApiSequenceStep:
    return ApiSequenceStep(
        order=order,
        kind=SequenceStepKind.HTTP_REQUEST,
        phase=phase,
        origin=_origin(phase, kind),
        method_name="exchange",
        source_span=SourceSpan(
            start_line=order, start_column=1, end_line=order, end_column=20
        ),
        http_method=http_method,
        http_path=http_path,
        path_truncated=path_truncated,
    )


def _response_check(
    order: int,
    *,
    assertion_role: AssertionRole = AssertionRole.STATUS,
    status_code: int | None = None,
    status_range: str | None = None,
    phase: LifecyclePhase = LifecyclePhase.TEST,
) -> ApiSequenceStep:
    return ApiSequenceStep(
        order=order,
        kind=SequenceStepKind.RESPONSE_CHECK,
        phase=phase,
        origin=_origin(phase),
        method_name="check",
        source_span=SourceSpan(
            start_line=order, start_column=1, end_line=order, end_column=20
        ),
        assertion_role=assertion_role,
        status_code=status_code,
        status_range=status_range,
    )


def _sequences(*step_groups: list[ApiSequenceStep]) -> list[HttpTestSequence]:
    return [
        HttpTestSequence(
            order=index + 1,
            steps=steps,
            length=len(steps),
            fingerprint=f"sequence-{index + 1}",
        )
        for index, steps in enumerate(step_groups)
    ]


def _method_analysis(
    method_signature: str, sequences: list[HttpTestSequence]
) -> ModelMethodAnalysis:
    return ModelMethodAnalysis(
        identity=MethodIdentity(
            defining_class_name="example.ApiTest",
            method_signature=method_signature,
            method_declaration=f"void {method_signature}",
        ),
        http=HttpAnalysis(test_sequences=sequences),
    )


def _coverage(
    endpoints: list[ApplicationEndpoint],
    method_analyses: list[ModelMethodAnalysis],
) -> EndpointCoverageSummary:
    return build_endpoint_coverage_summary(
        application_endpoints=endpoints,
        test_class_analyses=[
            ModelClassAnalysis(
                qualified_class_name="example.ApiTest",
                test_method_analyses=method_analyses,
            )
        ],
    )


# ── Status assertions attribute to the matched endpoint ─────────────


def test_status_assertion_attributes_to_matched_endpoint() -> None:
    coverage = _coverage(
        [_endpoint("GET", "/api/users/{id}")],
        [
            _method_analysis(
                "testMissingUser()",
                _sequences(
                    [
                        _request_step(1, "GET", "/api/users/42"),
                        _response_check(2, status_code=404, status_range="4xx"),
                    ]
                ),
            )
        ],
    )

    outcomes = coverage.endpoints[0].asserted_outcomes
    assert outcomes.attributed_request_count == 1
    assert outcomes.status_asserted_request_count == 1
    assert outcomes.asserting_test_method_count == 1
    assert outcomes.status_range_counts == {"4xx": 1}
    assert outcomes.status_code_counts == {"404": 1}


def test_multi_endpoint_test_attributes_outcomes_per_request() -> None:
    coverage = _coverage(
        [_endpoint("POST", "/api/users"), _endpoint("GET", "/api/users/{id}")],
        [
            _method_analysis(
                "testCreateThenMiss()",
                _sequences(
                    [
                        _request_step(1, "POST", "/api/users"),
                        _response_check(2, status_code=201, status_range="2xx"),
                    ],
                    [
                        _request_step(3, "GET", "/api/users/999"),
                        _response_check(4, status_code=404, status_range="4xx"),
                    ],
                ),
            )
        ],
    )

    post_outcomes = coverage.endpoints[0].asserted_outcomes
    get_outcomes = coverage.endpoints[1].asserted_outcomes
    assert post_outcomes.status_code_counts == {"201": 1}
    assert post_outcomes.status_range_counts == {"2xx": 1}
    assert get_outcomes.status_code_counts == {"404": 1}
    assert get_outcomes.status_range_counts == {"4xx": 1}


def test_range_only_assertion_counts_range_without_code() -> None:
    coverage = _coverage(
        [_endpoint("GET", "/api/users")],
        [
            _method_analysis(
                "testClientError()",
                _sequences(
                    [
                        _request_step(1, "GET", "/api/users"),
                        _response_check(2, status_range="4xx"),
                    ]
                ),
            )
        ],
    )

    outcomes = coverage.endpoints[0].asserted_outcomes
    assert outcomes.status_range_counts == {"4xx": 1}
    assert outcomes.status_code_counts == {}


def test_unresolved_status_assertion_counts_unknown_range() -> None:
    # A status assertion whose expected code never resolved (e.g. a variable
    # passed to status().is(...)) still marks the request status-asserted.
    coverage = _coverage(
        [_endpoint("GET", "/api/users")],
        [
            _method_analysis(
                "testVariableStatus()",
                _sequences(
                    [
                        _request_step(1, "GET", "/api/users"),
                        _response_check(2),
                    ]
                ),
            )
        ],
    )

    outcomes = coverage.endpoints[0].asserted_outcomes
    assert outcomes.attributed_request_count == 1
    assert outcomes.status_asserted_request_count == 1
    assert outcomes.asserting_test_method_count == 1
    assert outcomes.status_range_counts == {"unknown": 1}
    assert outcomes.status_code_counts == {}


def test_request_without_status_checks_counts_attribution_only() -> None:
    coverage = _coverage(
        [_endpoint("GET", "/api/users")],
        [
            _method_analysis(
                "testBodyOnly()",
                _sequences(
                    [
                        _request_step(1, "GET", "/api/users"),
                        _response_check(2, assertion_role=AssertionRole.BODY),
                    ]
                ),
            )
        ],
    )

    outcomes = coverage.endpoints[0].asserted_outcomes
    assert outcomes.attributed_request_count == 1
    assert outcomes.status_asserted_request_count == 0
    assert outcomes.asserting_test_method_count == 0
    assert outcomes.status_range_counts == {}
    assert outcomes.status_code_counts == {}


def test_asserting_tests_counted_distinctly_per_endpoint() -> None:
    repeated_sequences = _sequences(
        [
            _request_step(1, "GET", "/api/users"),
            _response_check(2, status_code=200, status_range="2xx"),
        ],
        [
            _request_step(3, "GET", "/api/users"),
            _response_check(4, status_code=200, status_range="2xx"),
        ],
    )
    coverage = _coverage(
        [_endpoint("GET", "/api/users")],
        [
            _method_analysis("testTwice()", repeated_sequences),
            _method_analysis(
                "testOnce()",
                _sequences(
                    [
                        _request_step(1, "GET", "/api/users"),
                        _response_check(2, status_code=200, status_range="2xx"),
                    ]
                ),
            ),
        ],
    )

    outcomes = coverage.endpoints[0].asserted_outcomes
    assert outcomes.attributed_request_count == 3
    assert outcomes.status_asserted_request_count == 3
    assert outcomes.asserting_test_method_count == 2
    assert outcomes.status_range_counts == {"2xx": 3}


def test_fixture_phase_requests_attribute() -> None:
    coverage = _coverage(
        [_endpoint("POST", "/api/users")],
        [
            _method_analysis(
                "testWithSeededUser()",
                _sequences(
                    [
                        _request_step(
                            1,
                            "POST",
                            "/api/users",
                            phase=LifecyclePhase.SETUP,
                            kind=CallSiteOriginKind.FIXTURE,
                        ),
                        _response_check(
                            2,
                            status_code=201,
                            status_range="2xx",
                            phase=LifecyclePhase.SETUP,
                        ),
                    ]
                ),
            )
        ],
    )

    outcomes = coverage.endpoints[0].asserted_outcomes
    assert outcomes.attributed_request_count == 1
    assert outcomes.status_code_counts == {"201": 1}


def test_truncated_path_attributes_against_variable_tail_template() -> None:
    coverage = _coverage(
        [_endpoint("GET", "/api/users/{id}")],
        [
            _method_analysis(
                "testTruncatedConcat()",
                _sequences(
                    [
                        _request_step(1, "GET", "/api/users/", path_truncated=True),
                        _response_check(2, status_code=200, status_range="2xx"),
                    ]
                ),
            )
        ],
    )

    outcomes = coverage.endpoints[0].asserted_outcomes
    assert outcomes.attributed_request_count == 1
    assert outcomes.status_code_counts == {"200": 1}


def test_duplicate_route_extractions_each_credited() -> None:
    # Duplicate extractions of the same route (interface plus implementation)
    # are each attributed the request and its assertion, mirroring direct
    # matching; pooled stats label these counts as attributions for this reason.
    coverage = _coverage(
        [_endpoint("GET", "/api/users/{id}"), _endpoint("GET", "/api/users/{id}")],
        [
            _method_analysis(
                "testMissingUser()",
                _sequences(
                    [
                        _request_step(1, "GET", "/api/users/42"),
                        _response_check(2, status_code=404, status_range="4xx"),
                    ]
                ),
            )
        ],
    )

    for entry in coverage.endpoints:
        outcomes = entry.asserted_outcomes
        assert outcomes.attributed_request_count == 1
        assert outcomes.status_asserted_request_count == 1
        assert outcomes.status_code_counts == {"404": 1}


# ── Requests that resolve to no endpoint do not attribute ────────────


def test_external_requests_do_not_attribute() -> None:
    coverage = _coverage(
        [_endpoint("GET", "/api/users")],
        [
            _method_analysis(
                "testExternalCall()",
                _sequences(
                    [
                        _request_step(1, "GET", "https://other.example.com/api/users"),
                        _response_check(2, status_code=200, status_range="2xx"),
                    ]
                ),
            )
        ],
    )

    outcomes = coverage.endpoints[0].asserted_outcomes
    assert outcomes.attributed_request_count == 0
    assert outcomes.status_asserted_request_count == 0


def test_unresolved_method_does_not_match_typed_endpoint() -> None:
    coverage = _coverage(
        [_endpoint("GET", "/api/users")],
        [
            _method_analysis(
                "testUnknownVerb()",
                _sequences(
                    [
                        _request_step(1, None, "/api/users"),
                        _response_check(2, status_code=200, status_range="2xx"),
                    ]
                ),
            )
        ],
    )

    outcomes = coverage.endpoints[0].asserted_outcomes
    assert outcomes.attributed_request_count == 0


# ── Batch-capture assertions attach to the nearest preceding request ─


def test_batch_capture_assertions_attach_to_last_request() -> None:
    # Capture-two-responses-then-assert-both: sequence segmentation binds every
    # response check to the nearest preceding request, so both assertions land
    # on the second request. Pins the documented attribution limitation.
    flat_steps = [
        _request_step(1, "GET", "/api/first"),
        _request_step(2, "GET", "/api/second"),
        _response_check(3, status_code=200, status_range="2xx"),
        _response_check(4, status_code=404, status_range="4xx"),
    ]
    coverage = _coverage(
        [_endpoint("GET", "/api/first"), _endpoint("GET", "/api/second")],
        [_method_analysis("testBatchCapture()", build_http_test_sequences(flat_steps))],
    )

    first_outcomes = coverage.endpoints[0].asserted_outcomes
    second_outcomes = coverage.endpoints[1].asserted_outcomes
    assert first_outcomes.attributed_request_count == 1
    assert first_outcomes.status_asserted_request_count == 0
    assert first_outcomes.status_code_counts == {}
    assert second_outcomes.status_asserted_request_count == 1
    assert second_outcomes.status_code_counts == {"200": 1, "404": 1}


# ── Serialization and end-to-end classification ─────────────────────


def test_asserted_outcomes_serialized_on_coverage_entry() -> None:
    coverage = _coverage(
        [_endpoint("GET", "/api/users")],
        [
            _method_analysis(
                "testOk()",
                _sequences(
                    [
                        _request_step(1, "GET", "/api/users"),
                        _response_check(2, status_code=200, status_range="2xx"),
                    ]
                ),
            )
        ],
    )

    dumped = coverage.endpoints[0].model_dump()["asserted_outcomes"]
    assert dumped == {
        "attributed_request_count": 1,
        "status_asserted_request_count": 1,
        "asserting_test_method_count": 1,
        "status_range_counts": {"2xx": 1},
        "status_code_counts": {"200": 1},
    }


def test_mockmvc_chain_attributes_not_found_to_endpoint() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.servlet.request"
                    ".MockMvcRequestBuilders"
                ),
                argument_expr=['"/api/items/99"'],
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="andExpect",
                receiver_type="org.springframework.test.web.servlet.ResultActions",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=90,
            ),
            make_call_site(
                method_name="isNotFound",
                receiver_type=(
                    "org.springframework.test.web.servlet.result"
                    ".StatusResultMatchers"
                ),
                start_line=1,
                start_column=60,
                end_line=1,
                end_column=72,
            ),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="testMissingItem()",
                ),
                context_class_name="example.ApiTest",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )
    classify_runtime_view_for_testing(runtime_view)
    sequences = build_http_test_sequences(build_api_call_sequence(runtime_view))

    coverage = _coverage(
        [_endpoint("GET", "/api/items/{id}")],
        [_method_analysis("testMissingItem()", sequences)],
    )

    outcomes = coverage.endpoints[0].asserted_outcomes
    assert outcomes.attributed_request_count == 1
    assert outcomes.status_asserted_request_count == 1
    assert outcomes.status_code_counts == {"404": 1}
    assert outcomes.status_range_counts == {"4xx": 1}
