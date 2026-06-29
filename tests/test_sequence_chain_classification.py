from __future__ import annotations

import pytest

from gerbil.analysis.runtime.call_sites import (
    MethodRef,
    build_call_site_grouping,
    build_expanded_call_site_grouping,
)
from gerbil.analysis.schema import (
    ApiSequenceStep,
    AssertionRole,
    CallSiteOriginKind,
    HttpInteractionKind,
    HttpRequestRole,
    HttpResponseRole,
    LifecyclePhase,
    OriginContext,
    SequenceStepKind,
    SourceSpan,
)
from gerbil.analysis.properties.sequence_analysis import (
    build_api_call_sequence,
    build_http_interaction_views,
    build_http_sequence_summary,
    build_http_test_sequences,
    build_http_verification_interactions,
)
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from tests.cldk_factories import (
    annotate_node_http,
    classify_runtime_view_for_testing,
    make_call_site,
    make_callable,
)


def _api_sequence_step(
    order: int,
    kind: SequenceStepKind,
    *,
    http_method: str | None = None,
    http_path: str | None = None,
    assertion_role: AssertionRole | None = None,
    status_code: int | None = None,
    status_range: str | None = None,
    phase: LifecyclePhase = LifecyclePhase.TEST,
) -> ApiSequenceStep:
    return ApiSequenceStep(
        order=order,
        kind=kind,
        phase=phase,
        origin=OriginContext(phase=phase, kind=CallSiteOriginKind.TEST_METHOD),
        method_name=kind.value,
        source_span=SourceSpan(
            start_line=order,
            start_column=1,
            end_line=order,
            end_column=10,
        ),
        http_method=http_method,
        http_path=http_path,
        assertion_role=assertion_role,
        status_code=status_code,
        status_range=status_range,
    )


def test_mockmvc_chain_emits_builder_and_request() -> None:
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
                receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
                argument_expr=['"/api/users"'],
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=33,
            ),
            make_call_site(
                method_name="andExpect",
                receiver_type="org.springframework.test.web.servlet.ResultActions",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=70,
            ),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testFoo()",
                ),
                context_class_name="example.Test",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)
    steps = build_api_call_sequence(runtime_view)

    assert [(s.kind, s.method_name, s.http_method, s.http_path) for s in steps] == [
        (SequenceStepKind.REQUEST_BUILD, "get", "GET", "/api/users"),
        (SequenceStepKind.HTTP_REQUEST, "perform", "GET", "/api/users"),
    ]
    assert all(s.phase == LifecyclePhase.TEST for s in steps)


def test_mockmvc_chain_with_status_assertion_emits_response_check() -> None:
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
                receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
                argument_expr=['"/api/items"'],
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=33,
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
                method_name="isOk",
                receiver_type="org.springframework.test.web.servlet.result.StatusResultMatchers",
                start_line=1,
                start_column=60,
                end_line=1,
                end_column=65,
            ),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testItems()",
                ),
                context_class_name="example.Test",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)
    steps = build_api_call_sequence(runtime_view)

    assert [(s.kind, s.method_name) for s in steps] == [
        (SequenceStepKind.REQUEST_BUILD, "get"),
        (SequenceStepKind.HTTP_REQUEST, "perform"),
        (SequenceStepKind.RESPONSE_CHECK, "isOk"),
    ]
    response_check = steps[2]
    assert response_check.origin.kind == CallSiteOriginKind.TEST_METHOD
    assert response_check.origin.method_signature == "testItems()"
    assert response_check.assertion_role == AssertionRole.STATUS
    assert response_check.status_code == 200
    assert response_check.status_range == "2xx"
    verification_interactions = build_http_verification_interactions(runtime_view)
    assert len(verification_interactions) == 1
    assert verification_interactions[0].origin.kind == CallSiteOriginKind.TEST_METHOD
    assert verification_interactions[0].assertion_role == AssertionRole.STATUS
    (
        request_interactions,
        ordered_verification_interactions,
        response_extractions,
        http_interactions,
    ) = build_http_interaction_views(runtime_view)
    assert response_extractions == []
    assert [interaction.kind for interaction in http_interactions] == [
        HttpInteractionKind.REQUEST,
        HttpInteractionKind.REQUEST,
        HttpInteractionKind.VERIFICATION,
    ]
    assert [
        interaction.request_interaction
        for interaction in http_interactions
        if interaction.kind == HttpInteractionKind.REQUEST
    ] == request_interactions
    assert ordered_verification_interactions == verification_interactions


def test_verification_interactions_include_direct_status_body_and_header() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="isOk",
                receiver_type="org.springframework.test.web.servlet.result.StatusResultMatchers",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=6,
            ),
            make_call_site(
                method_name="string",
                receiver_type="org.springframework.test.web.servlet.result.ContentResultMatchers",
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=8,
            ),
            make_call_site(
                method_name="exists",
                receiver_type="org.springframework.test.web.servlet.result.HeaderResultMatchers",
                start_line=3,
                start_column=1,
                end_line=3,
                end_column=8,
            ),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testResponseSurfaces()",
                ),
                context_class_name="example.Test",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)

    verification_interactions = build_http_verification_interactions(runtime_view)
    assert [
        interaction.assertion_role for interaction in verification_interactions
    ] == [
        AssertionRole.STATUS,
        AssertionRole.BODY,
        AssertionRole.HEADER,
    ]
    assert {interaction.origin.kind for interaction in verification_interactions} == {
        CallSiteOriginKind.TEST_METHOD,
    }
    assert [
        step.assertion_role
        for step in build_api_call_sequence(runtime_view)
        if step.kind == SequenceStepKind.RESPONSE_CHECK
    ] == [AssertionRole.STATUS, AssertionRole.BODY, AssertionRole.HEADER]


def test_assertthat_status_chain_emits_one_response_check_and_verification() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="get",
                argument_expr=['"/users/1"'],
                start_line=9,
                start_column=1,
                end_line=9,
                end_column=20,
            ),
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
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="testCase()",
                ),
                context_class_name="example.ApiTest",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )
    annotate_node_http(
        runtime_view.entries[0].grouping.nodes[0],
        http_method="GET",
        path="/users/1",
        request_role=HttpRequestRole.EVENT,
        response_role=None,
    )
    classify_runtime_view_for_testing(runtime_view)

    response_checks = [
        step
        for step in build_api_call_sequence(runtime_view)
        if step.kind == SequenceStepKind.RESPONSE_CHECK
    ]
    verification_interactions = build_http_verification_interactions(runtime_view)

    assert [
        (step.method_name, step.assertion_role, step.status_code)
        for step in response_checks
    ] == [("isEqualTo", AssertionRole.STATUS, 404)]
    assert [
        (interaction.method_name, interaction.assertion_role, interaction.status_code)
        for interaction in verification_interactions
    ] == [("isEqualTo", AssertionRole.STATUS, 404)]


def test_fixture_phase_is_tagged_on_steps() -> None:
    setup_method = make_callable(
        call_sites=[
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=40,
            ),
            make_call_site(
                method_name="post",
                receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
                argument_expr=['"/seed"'],
                start_line=2,
                start_column=17,
                end_line=2,
                end_column=30,
            ),
        ]
    )
    test_method = make_callable(
        call_sites=[
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=5,
                start_column=1,
                end_line=5,
                end_column=50,
            ),
            make_call_site(
                method_name="get",
                receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
                argument_expr=['"/api/users"'],
                start_line=5,
                start_column=17,
                end_line=5,
                end_column=33,
            ),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.SETUP,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="setUp()",
                ),
                context_class_name="example.Test",
                grouping=build_call_site_grouping(list(setup_method.call_sites)),
                method_details=setup_method,
            ),
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testFoo()",
                ),
                context_class_name="example.Test",
                grouping=build_call_site_grouping(list(test_method.call_sites)),
                method_details=test_method,
            ),
        ]
    )

    classify_runtime_view_for_testing(runtime_view)
    steps = build_api_call_sequence(runtime_view)

    assert [(s.phase, s.kind, s.method_name) for s in steps] == [
        (LifecyclePhase.SETUP, SequenceStepKind.REQUEST_BUILD, "post"),
        (LifecyclePhase.SETUP, SequenceStepKind.HTTP_REQUEST, "perform"),
        (LifecyclePhase.TEST, SequenceStepKind.REQUEST_BUILD, "get"),
        (LifecyclePhase.TEST, SequenceStepKind.HTTP_REQUEST, "perform"),
    ]
    assert steps[0].http_method == "POST"
    assert steps[0].http_path == "/seed"
    assert steps[2].http_method == "GET"
    assert steps[2].http_path == "/api/users"


def test_source_span_populated_from_node_positions() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="get",
                receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
                argument_expr=['"/api"'],
                start_line=3,
                start_column=10,
                end_line=3,
                end_column=25,
            ),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testFoo()",
                ),
                context_class_name="example.Test",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)
    steps = build_api_call_sequence(runtime_view)

    assert len(steps) == 1
    span = steps[0].source_span
    assert span.start_line == 3
    assert span.start_column == 10
    assert span.end_line == 3
    assert span.end_column == 25


def test_empty_runtime_view_returns_empty_sequence() -> None:
    steps = build_api_call_sequence(TestRuntimeView(entries=[]))
    assert steps == []


def test_http_test_sequences_group_build_event_and_response_checks() -> None:
    call_sequence = [
        _api_sequence_step(
            1,
            SequenceStepKind.REQUEST_BUILD,
            http_method="GET",
            http_path="/users/1",
        ),
        _api_sequence_step(
            2,
            SequenceStepKind.HTTP_REQUEST,
            http_method="GET",
            http_path="/users/1",
        ),
        _api_sequence_step(
            3,
            SequenceStepKind.RESPONSE_CHECK,
            assertion_role=AssertionRole.STATUS,
            status_code=200,
            status_range="2xx",
        ),
        _api_sequence_step(
            4,
            SequenceStepKind.RESPONSE_CHECK,
            assertion_role=AssertionRole.BODY,
        ),
    ]

    test_sequences = build_http_test_sequences(call_sequence)
    summary = build_http_sequence_summary(test_sequences)

    assert len(test_sequences) == 1
    assert test_sequences[0].order == 1
    assert test_sequences[0].length == 4
    assert test_sequences[0].steps == call_sequence
    assert test_sequences[0].fingerprint == (
        "request-build:GET:/users/{id}|"
        "http-request:GET:/users/{id}|"
        "response-check:status|"
        "response-check:body"
    )
    assert summary.sequence_count == 1
    assert summary.sequence_lengths == [4]
    assert summary.request_build_step_count == 1
    assert summary.http_request_step_count == 1
    assert summary.response_check_step_count == 2
    assert summary.distinct_http_method_count == 1
    assert summary.distinct_resource_count == 1
    assert summary.distinct_endpoint_count == 1
    assert not summary.has_repeated_sequence


def test_http_test_sequences_segment_on_next_builder_or_event() -> None:
    call_sequence = [
        _api_sequence_step(
            1,
            SequenceStepKind.REQUEST_BUILD,
            http_method="POST",
            http_path="/users",
        ),
        _api_sequence_step(
            2,
            SequenceStepKind.HTTP_REQUEST,
            http_method="POST",
            http_path="/users",
        ),
        _api_sequence_step(
            3,
            SequenceStepKind.REQUEST_BUILD,
            http_method="GET",
            http_path="/users/1",
        ),
        _api_sequence_step(
            4,
            SequenceStepKind.HTTP_REQUEST,
            http_method="GET",
            http_path="/users/1",
        ),
        _api_sequence_step(
            5,
            SequenceStepKind.RESPONSE_CHECK,
            assertion_role=AssertionRole.STATUS,
            status_code=200,
            status_range="2xx",
        ),
    ]

    test_sequences = build_http_test_sequences(call_sequence)
    summary = build_http_sequence_summary(test_sequences)

    assert [sequence.length for sequence in test_sequences] == [2, 3]
    assert [step.kind for step in test_sequences[0].steps] == [
        SequenceStepKind.REQUEST_BUILD,
        SequenceStepKind.HTTP_REQUEST,
    ]
    assert [step.kind for step in test_sequences[1].steps] == [
        SequenceStepKind.REQUEST_BUILD,
        SequenceStepKind.HTTP_REQUEST,
        SequenceStepKind.RESPONSE_CHECK,
    ]
    assert summary.has_multiple_sequences
    assert summary.distinct_http_method_count == 2
    assert summary.distinct_resource_count == 1
    assert summary.distinct_endpoint_count == 2


def test_repeated_normalized_sequences_ignore_literals_phase_and_status_code() -> None:
    call_sequence = [
        _api_sequence_step(
            1,
            SequenceStepKind.REQUEST_BUILD,
            http_method="GET",
            http_path="/users/1",
        ),
        _api_sequence_step(
            2,
            SequenceStepKind.HTTP_REQUEST,
            http_method="GET",
            http_path="/users/1",
        ),
        _api_sequence_step(
            3,
            SequenceStepKind.RESPONSE_CHECK,
            assertion_role=AssertionRole.STATUS,
            status_code=200,
            status_range="2xx",
        ),
        _api_sequence_step(
            4,
            SequenceStepKind.REQUEST_BUILD,
            http_method="GET",
            http_path="/users/{userId}",
            phase=LifecyclePhase.SETUP,
        ),
        _api_sequence_step(
            5,
            SequenceStepKind.HTTP_REQUEST,
            http_method="GET",
            http_path="/users/{userId}",
            phase=LifecyclePhase.SETUP,
        ),
        _api_sequence_step(
            6,
            SequenceStepKind.RESPONSE_CHECK,
            assertion_role=AssertionRole.STATUS,
            status_code=201,
            status_range="2xx",
            phase=LifecyclePhase.SETUP,
        ),
    ]

    test_sequences = build_http_test_sequences(call_sequence)
    summary = build_http_sequence_summary(test_sequences)

    assert len(test_sequences) == 2
    assert test_sequences[0].fingerprint == test_sequences[1].fingerprint
    assert summary.distinct_sequence_fingerprint_count == 1
    assert summary.repeated_sequence_fingerprint_count == 1
    assert summary.has_repeated_sequence
    assert summary.distinct_resource_count == 1
    assert summary.distinct_endpoint_count == 1


def test_different_method_or_response_surface_changes_fingerprint() -> None:
    get_status_sequence = build_http_test_sequences(
        [
            _api_sequence_step(
                1,
                SequenceStepKind.HTTP_REQUEST,
                http_method="GET",
                http_path="/users/1",
            ),
            _api_sequence_step(
                2,
                SequenceStepKind.RESPONSE_CHECK,
                assertion_role=AssertionRole.STATUS,
                status_code=200,
                status_range="2xx",
            ),
        ]
    )[0]
    post_status_sequence = build_http_test_sequences(
        [
            _api_sequence_step(
                1,
                SequenceStepKind.HTTP_REQUEST,
                http_method="POST",
                http_path="/users/1",
            ),
            _api_sequence_step(
                2,
                SequenceStepKind.RESPONSE_CHECK,
                assertion_role=AssertionRole.STATUS,
                status_code=200,
                status_range="2xx",
            ),
        ]
    )[0]
    get_body_sequence = build_http_test_sequences(
        [
            _api_sequence_step(
                1,
                SequenceStepKind.HTTP_REQUEST,
                http_method="GET",
                http_path="/users/1",
            ),
            _api_sequence_step(
                2,
                SequenceStepKind.RESPONSE_CHECK,
                assertion_role=AssertionRole.BODY,
            ),
        ]
    )[0]

    assert get_status_sequence.fingerprint != post_status_sequence.fingerprint
    assert get_status_sequence.fingerprint != get_body_sequence.fingerprint


def test_orphan_response_checks_do_not_create_http_test_sequences() -> None:
    call_sequence = [
        _api_sequence_step(
            1,
            SequenceStepKind.RESPONSE_CHECK,
            assertion_role=AssertionRole.STATUS,
            status_code=200,
            status_range="2xx",
        ),
        _api_sequence_step(
            2,
            SequenceStepKind.HTTP_REQUEST,
            http_method="GET",
            http_path="/health",
        ),
        _api_sequence_step(
            3,
            SequenceStepKind.RESPONSE_CHECK,
            assertion_role=AssertionRole.STATUS,
            status_code=200,
            status_range="2xx",
        ),
    ]

    test_sequences = build_http_test_sequences(call_sequence)

    assert len(test_sequences) == 1
    assert [step.order for step in test_sequences[0].steps] == [2, 3]


def test_order_is_sequential_starting_at_one() -> None:
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
                receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
                argument_expr=['"/api"'],
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=30,
            ),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testFoo()",
                ),
                context_class_name="example.Test",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)
    steps = build_api_call_sequence(runtime_view)

    assert [s.order for s in steps] == [1, 2]


def test_helper_expanded_http_calls_appear_in_skeleton() -> None:
    helper_call = make_call_site(
        method_name="invokeHelper",
        callee_signature="invokeHelper()",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=16,
    )
    test_method = make_callable(call_sites=[helper_call])

    helper_inner = make_call_site(
        method_name="getForEntity",
        receiver_type="org.springframework.web.client.RestTemplate",
        callee_signature="getForEntity()",
        argument_expr=['"/api/data"'],
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=30,
    )
    helper_owner = MethodRef(
        defining_class_name="example.Test",
        method_signature="invokeHelper()",
    )

    def resolve_helper(owner: MethodRef, call_site):
        if call_site.callee_signature == "invokeHelper()":
            return helper_owner
        return None

    def load_call_sites(method_ref: MethodRef):
        if method_ref == helper_owner:
            return [helper_inner]
        return None

    owner = MethodRef(
        defining_class_name="example.Test",
        method_signature="testFoo()",
    )
    grouping = build_expanded_call_site_grouping(
        call_sites=list(test_method.call_sites),
        owner=owner,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=1,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=grouping,
                method_details=test_method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)
    steps = build_api_call_sequence(runtime_view)

    assert len(steps) == 1
    assert steps[0].kind == SequenceStepKind.HTTP_REQUEST
    assert steps[0].method_name == "getForEntity"
    assert steps[0].http_method == "GET"
    assert steps[0].http_path == "/api/data"


def test_status_predicate_in_assert_helper_emits_response_check() -> None:
    helper_bridge_call = make_call_site(
        method_name="helperBridge",
        callee_signature="helperBridge()",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=18,
    )
    test_method = make_callable(call_sites=[helper_bridge_call])

    helper_owner = MethodRef(
        defining_class_name="example.Test",
        method_signature="helperBridge()",
    )
    helper_assert_call = make_call_site(
        method_name="assertStatus",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=46,
    )
    helper_status_predicate = make_call_site(
        method_name="is4xxClientError",
        start_line=5,
        start_column=21,
        end_line=5,
        end_column=39,
    )

    def resolve_helper(owner: MethodRef, call_site):
        if call_site.callee_signature == "helperBridge()":
            return helper_owner
        return None

    def load_call_sites(method_ref: MethodRef):
        if method_ref == helper_owner:
            return [helper_assert_call, helper_status_predicate]
        return None

    owner = MethodRef(
        defining_class_name="example.Test",
        method_signature="testFoo()",
    )
    grouping = build_expanded_call_site_grouping(
        call_sites=list(test_method.call_sites),
        owner=owner,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=1,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=grouping,
                method_details=test_method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)
    steps = build_api_call_sequence(runtime_view)

    status_steps = [s for s in steps if s.kind == SequenceStepKind.RESPONSE_CHECK]
    assert len(status_steps) == 1
    assert status_steps[0].method_name == "is4xxClientError"
    assert status_steps[0].origin.kind == CallSiteOriginKind.TEST_HELPER
    assert status_steps[0].origin.method_signature == "helperBridge()"
    assert status_steps[0].origin.entry_method_signature == "testFoo()"
    assert status_steps[0].assertion_role == AssertionRole.STATUS
    assert status_steps[0].status_range == "4xx"
    verification_interactions = build_http_verification_interactions(runtime_view)
    assert len(verification_interactions) == 1
    assert verification_interactions[0].origin.kind == CallSiteOriginKind.TEST_HELPER
    assert verification_interactions[0].origin.method_signature == "helperBridge()"


@pytest.mark.parametrize(
    ("phase", "entry_signature"),
    [
        (LifecyclePhase.SETUP, "setUp()"),
        (LifecyclePhase.TEARDOWN, "tearDown()"),
    ],
)
def test_status_predicate_in_fixture_helper_emits_response_check_origin(
    phase: LifecyclePhase,
    entry_signature: str,
) -> None:
    helper_bridge_call = make_call_site(
        method_name="fixtureHelperBridge",
        callee_signature="fixtureHelperBridge()",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=25,
    )
    fixture_method = make_callable(call_sites=[helper_bridge_call])

    helper_owner = MethodRef(
        defining_class_name="example.Test",
        method_signature="fixtureHelperBridge()",
    )
    helper_assert_call = make_call_site(
        method_name="assertStatus",
        start_line=8,
        start_column=1,
        end_line=8,
        end_column=46,
    )
    helper_status_predicate = make_call_site(
        method_name="is5xxServerError",
        start_line=8,
        start_column=21,
        end_line=8,
        end_column=39,
    )

    def resolve_helper(owner: MethodRef, call_site):
        if call_site.callee_signature == "fixtureHelperBridge()":
            return helper_owner
        return None

    def load_call_sites(method_ref: MethodRef):
        if method_ref == helper_owner:
            return [helper_assert_call, helper_status_predicate]
        return None

    owner = MethodRef(
        defining_class_name="example.Test",
        method_signature=entry_signature,
    )
    grouping = build_expanded_call_site_grouping(
        call_sites=list(fixture_method.call_sites),
        owner=owner,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=1,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=phase,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=grouping,
                method_details=fixture_method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)

    status_steps = [
        step
        for step in build_api_call_sequence(runtime_view)
        if step.kind == SequenceStepKind.RESPONSE_CHECK
    ]
    assert len(status_steps) == 1
    assert status_steps[0].origin.phase == phase
    assert status_steps[0].origin.kind == CallSiteOriginKind.FIXTURE_HELPER
    assert status_steps[0].origin.method_signature == "fixtureHelperBridge()"
    assert status_steps[0].origin.entry_method_signature == entry_signature
    assert status_steps[0].status_range == "5xx"

    verification_interactions = build_http_verification_interactions(runtime_view)
    assert len(verification_interactions) == 1
    assert verification_interactions[0].origin.phase == phase
    assert verification_interactions[0].origin.kind == CallSiteOriginKind.FIXTURE_HELPER
    assert verification_interactions[0].origin.entry_method_signature == entry_signature


def test_mixed_direct_and_helper_response_checks_keep_distinct_origins() -> None:
    helper_bridge_call = make_call_site(
        method_name="helperBridge",
        callee_signature="helperBridge()",
        start_line=2,
        start_column=1,
        end_line=2,
        end_column=18,
    )
    test_method = make_callable(
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
                receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
                argument_expr=['"/api/items"'],
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=33,
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
                method_name="isOk",
                receiver_type="org.springframework.test.web.servlet.result.StatusResultMatchers",
                start_line=1,
                start_column=60,
                end_line=1,
                end_column=65,
            ),
            helper_bridge_call,
        ]
    )

    helper_owner = MethodRef(
        defining_class_name="example.Test",
        method_signature="helperBridge()",
    )
    helper_assert_call = make_call_site(
        method_name="assertStatus",
        argument_expr=["response.getStatus().isNotFound()"],
        start_line=6,
        start_column=1,
        end_line=6,
        end_column=60,
    )
    helper_status_predicate = make_call_site(
        method_name="isNotFound",
        start_line=6,
        start_column=21,
        end_line=6,
        end_column=58,
    )
    helper_status_subject = make_call_site(
        method_name="getStatus",
        start_line=6,
        start_column=21,
        end_line=6,
        end_column=41,
    )

    def resolve_helper(owner: MethodRef, call_site):
        if call_site.callee_signature == "helperBridge()":
            return helper_owner
        return None

    def load_call_sites(method_ref: MethodRef):
        if method_ref == helper_owner:
            return [helper_assert_call, helper_status_predicate, helper_status_subject]
        return None

    owner = MethodRef(
        defining_class_name="example.Test",
        method_signature="testMixedVerification()",
    )
    grouping = build_expanded_call_site_grouping(
        call_sites=list(test_method.call_sites),
        owner=owner,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=1,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=grouping,
                method_details=test_method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)

    response_step_origins = {
        step.method_name: step.origin.kind
        for step in build_api_call_sequence(runtime_view)
        if step.kind == SequenceStepKind.RESPONSE_CHECK
    }
    assert response_step_origins == {
        "isOk": CallSiteOriginKind.TEST_METHOD,
        "isNotFound": CallSiteOriginKind.TEST_HELPER,
    }

    verification_origins = {
        interaction.method_name: interaction.origin.kind
        for interaction in build_http_verification_interactions(runtime_view)
    }
    assert verification_origins == response_step_origins


def test_non_http_calls_excluded_from_skeleton() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(method_name="toString", start_line=1),
            make_call_site(method_name="debugLog", start_line=2),
            make_call_site(method_name="helperSetup", start_line=3),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testFoo()",
                ),
                context_class_name="example.Test",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)
    steps = build_api_call_sequence(runtime_view)

    assert steps == []


def test_truncated_request_path_propagates_to_request_step() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="getForEntity",
                receiver_type=(
                    "org.springframework.boot.test.web.client.TestRestTemplate"
                ),
                argument_expr=['"/api/users/" + id', "String.class"],
                start_line=1,
            ),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testUser()",
                ),
                context_class_name="example.Test",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)
    steps = build_api_call_sequence(runtime_view)

    request_steps = [s for s in steps if s.kind == SequenceStepKind.HTTP_REQUEST]
    assert [(s.http_method, s.http_path, s.path_truncated) for s in request_steps] == [
        ("GET", "/api/users/", True)
    ]


def test_mockmvc_and_return_emits_response_extraction_not_verification() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=60,
            ),
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.servlet.request"
                    ".MockMvcRequestBuilders"
                ),
                argument_expr=['"/api/items"'],
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=33,
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
                method_name="isOk",
                receiver_type=(
                    "org.springframework.test.web.servlet.result"
                    ".StatusResultMatchers"
                ),
                start_line=1,
                start_column=60,
                end_line=1,
                end_column=65,
            ),
            make_call_site(
                method_name="andReturn",
                receiver_type="org.springframework.test.web.servlet.ResultActions",
                start_line=1,
                start_column=91,
                end_line=1,
                end_column=101,
            ),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testExtract()",
                ),
                context_class_name="example.Test",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)
    _, _, extractions, _ = build_http_interaction_views(runtime_view)
    verifications = build_http_verification_interactions(runtime_view)

    assert [(e.method_name, e.response_role, e.origin.kind) for e in extractions] == [
        ("andReturn", HttpResponseRole.EXTRACTOR, CallSiteOriginKind.TEST_METHOD)
    ]
    # The status assertion stays a verification and never doubles as extraction.
    assert [v.method_name for v in verifications] == ["isOk"]


def test_response_extractions_empty_without_extractor_roles() -> None:
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
                argument_expr=['"/api/items"'],
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=33,
            ),
        ]
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.Test",
                    method_signature="testNoExtraction()",
                ),
                context_class_name="example.Test",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )

    classify_runtime_view_for_testing(runtime_view)

    _, _, extractions, _ = build_http_interaction_views(runtime_view)
    assert extractions == []
