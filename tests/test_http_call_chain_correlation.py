from __future__ import annotations

import pytest

from gerbil.analysis.runtime.call_sites import (
    HelperExpansion,
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.schema import (
    AssertionRole,
    CallSiteOriginKind,
    EndpointCandidate,
    HttpClassification,
    HttpDispatchFramework,
    HttpRequestRole,
    HttpResponseRole,
    LifecyclePhase,
)
from gerbil.analysis.http import classification as http_request_interaction_builder
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
    make_field,
    make_import_declaration,
    make_type,
    make_variable_declaration,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def _runtime_view_for_setup_call_sites(call_sites):
    setup_owner = MethodRef(
        defining_class_name="example.Test",
        method_signature="setUp()",
    )
    setup_method = make_callable(call_sites=call_sites)
    return TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.SETUP,
                method_ref=setup_owner,
                context_class_name=setup_owner.defining_class_name,
                grouping=build_call_site_grouping(list(setup_method.call_sites)),
                method_details=setup_method,
            )
        ]
    )


def test_http_request_interaction_builder_only_emits_annotated_nodes(
    monkeypatch,
) -> None:
    """When classify_http_on_grouping annotates only 2 of 3 nodes,
    build_output_http_request_interactions should emit exactly 2 interactions."""
    runtime_view = _runtime_view_for_setup_call_sites(
        [
            make_call_site(
                method_name="exchange",
                start_line=12,
                start_column=1,
                end_line=12,
                end_column=8,
            ),
            make_call_site(
                method_name="exchange",
                start_line=12,
                start_column=15,
                end_line=12,
                end_column=22,
            ),
            make_call_site(
                method_name="exchange",
                start_line=12,
                start_column=30,
                end_line=12,
                end_column=37,
            ),
        ]
    )

    def fake_classify_http_on_grouping(grouping, **_kwargs):
        nodes = list(grouping.nodes)
        # Annotate only the first 2 of 3 nodes; the 3rd is left unannotated.
        nodes[0].http_classification = HttpClassification(
            http_method="GET",
            path="/first",
            framework=HttpDispatchFramework.WEBTESTCLIENT,
            request_role=HttpRequestRole.EVENT,
        )
        nodes[0].endpoint_candidate = EndpointCandidate(
            http_method="GET",
            path="/first-endpoint",
            source="call-site",
            start_line=12,
        )
        nodes[1].http_classification = HttpClassification(
            http_method="POST",
            path="/second",
            framework=HttpDispatchFramework.WEBTESTCLIENT,
            request_role=HttpRequestRole.EVENT,
        )
        nodes[1].endpoint_candidate = EndpointCandidate(
            http_method="POST",
            path="/second-endpoint",
            source="call-site",
            start_line=12,
        )

    monkeypatch.setattr(
        http_request_interaction_builder,
        "classify_http_on_grouping",
        fake_classify_http_on_grouping,
    )

    http_request_interaction_builder.classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )
    interactions = (
        http_request_interaction_builder.build_output_http_request_interactions(
            runtime_view=runtime_view,
        )
    )

    assert [
        (
            interaction.http_call.path if interaction.http_call is not None else None,
            (
                interaction.endpoint_candidate.path
                if interaction.endpoint_candidate is not None
                else None
            ),
        )
        for interaction in interactions
    ] == [
        ("/first", "/first-endpoint"),
        ("/second", "/second-endpoint"),
    ]
    assert all(
        interaction.origin.phase == LifecyclePhase.SETUP for interaction in interactions
    )


def test_http_request_interaction_builder_preserves_annotation_order(
    monkeypatch,
) -> None:
    """Interactions are emitted in the order nodes are annotated."""
    runtime_view = _runtime_view_for_setup_call_sites(
        [
            make_call_site(
                method_name="exchange",
                start_line=20,
                start_column=1,
                end_line=20,
                end_column=8,
            ),
            make_call_site(
                method_name="exchange",
                start_line=20,
                start_column=15,
                end_line=20,
                end_column=22,
            ),
        ]
    )

    def fake_classify_http_on_grouping(grouping, **_kwargs):
        nodes = list(grouping.nodes)
        nodes[0].http_classification = HttpClassification(
            http_method="POST",
            path="/alpha",
            framework=HttpDispatchFramework.WEBTESTCLIENT,
            request_role=HttpRequestRole.EVENT,
        )
        nodes[1].http_classification = HttpClassification(
            http_method="GET",
            path="/beta",
            framework=HttpDispatchFramework.WEBTESTCLIENT,
            request_role=HttpRequestRole.EVENT,
        )

    monkeypatch.setattr(
        http_request_interaction_builder,
        "classify_http_on_grouping",
        fake_classify_http_on_grouping,
    )

    http_request_interaction_builder.classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )
    interactions = (
        http_request_interaction_builder.build_output_http_request_interactions(
            runtime_view=runtime_view,
        )
    )

    assert [
        interaction.http_call.path
        for interaction in interactions
        if interaction.http_call is not None
    ] == ["/alpha", "/beta"]


def test_runtime_http_classifies_each_helper_expansion_instance_even_with_same_owner(
    monkeypatch,
) -> None:
    """Same helper MethodRef expanded in setup and test must be annotated twice."""

    helper_owner = MethodRef(
        defining_class_name="example.Helper",
        method_signature="shared()",
    )
    setup_owner = MethodRef(
        defining_class_name="example.Test",
        method_signature="setUp()",
    )
    test_owner = MethodRef(
        defining_class_name="example.Test",
        method_signature="testBody()",
    )

    setup_grouping = build_call_site_grouping(
        [
            make_call_site(
                method_name="setupCall",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=10,
            )
        ]
    )
    test_grouping = build_call_site_grouping(
        [
            make_call_site(
                method_name="testCall",
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=9,
            )
        ]
    )
    setup_helper_grouping = build_call_site_grouping(
        [
            make_call_site(
                method_name="exchange",
                start_line=11,
                start_column=1,
                end_line=11,
                end_column=9,
            )
        ]
    )
    test_helper_grouping = build_call_site_grouping(
        [
            make_call_site(
                method_name="exchange",
                start_line=21,
                start_column=1,
                end_line=21,
                end_column=9,
            )
        ]
    )

    setup_grouping.nodes[0].helper_expansion = HelperExpansion(
        callee=helper_owner,
        grouping=setup_helper_grouping,
    )
    test_grouping.nodes[0].helper_expansion = HelperExpansion(
        callee=helper_owner,
        grouping=test_helper_grouping,
    )

    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.SETUP,
                method_ref=setup_owner,
                context_class_name=setup_owner.defining_class_name,
                grouping=setup_grouping,
                method_details=make_callable(signature=setup_owner.method_signature),
            ),
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=test_owner,
                context_class_name=test_owner.defining_class_name,
                grouping=test_grouping,
                method_details=make_callable(signature=test_owner.method_signature),
            ),
        ]
    )

    helper_path_by_grouping_id = {
        id(setup_helper_grouping): "/helper-setup",
        id(test_helper_grouping): "/helper-test",
    }

    def fake_classify_http_on_grouping(grouping, **_kwargs):
        helper_path = helper_path_by_grouping_id.get(id(grouping), "/entry")
        for node in grouping.nodes:
            node.http_classification = HttpClassification(
                http_method="GET",
                path=helper_path,
                framework=HttpDispatchFramework.MOCKMVC,
                request_role=HttpRequestRole.EVENT,
            )

    monkeypatch.setattr(
        http_request_interaction_builder,
        "classify_http_on_grouping",
        fake_classify_http_on_grouping,
    )

    http_request_interaction_builder.classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )
    interactions = (
        http_request_interaction_builder.build_output_http_request_interactions(
            runtime_view=runtime_view,
        )
    )

    helper_nodes = setup_helper_grouping.nodes + test_helper_grouping.nodes
    assert all(node.http_classification is not None for node in helper_nodes)
    assert {
        interaction.http_call.path
        for interaction in interactions
        if interaction.http_call is not None
    } >= {"/helper-setup", "/helper-test"}
    assert [
        (
            interaction.origin.phase,
            interaction.origin.kind,
            interaction.origin.method_signature,
            interaction.origin.entry_method_signature,
            interaction.origin.depth,
            interaction.http_call.path if interaction.http_call is not None else None,
        )
        for interaction in interactions
    ] == [
        (
            LifecyclePhase.SETUP,
            CallSiteOriginKind.FIXTURE,
            "setUp()",
            "setUp()",
            0,
            "/entry",
        ),
        (
            LifecyclePhase.SETUP,
            CallSiteOriginKind.FIXTURE_HELPER,
            "shared()",
            "setUp()",
            1,
            "/helper-setup",
        ),
        (
            LifecyclePhase.TEST,
            CallSiteOriginKind.TEST_METHOD,
            "testBody()",
            "testBody()",
            0,
            "/entry",
        ),
        (
            LifecyclePhase.TEST,
            CallSiteOriginKind.TEST_HELPER,
            "shared()",
            "testBody()",
            1,
            "/helper-test",
        ),
    ]


# ---------------------------------------------------------------------------
# Cross-chain builder-event correlation tests
# ---------------------------------------------------------------------------


def _classify_and_get_grouping(call_sites):
    """Build a runtime view, run classification, return the grouping."""
    from tests.cldk_factories import classify_runtime_view_for_testing

    runtime_view = _runtime_view_for_setup_call_sites(call_sites)
    classify_runtime_view_for_testing(runtime_view)
    return runtime_view.entries[0].grouping


def _event_nodes(grouping):
    """Return nodes annotated as EVENT."""
    return [
        n
        for n in grouping.nodes
        if n.http_classification is not None
        and n.http_classification.request_role == HttpRequestRole.EVENT
    ]


def test_okhttp_response_header_accessor_does_not_supply_event_path() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="header",
                receiver_type="okhttp3.Response",
                receiver_expr="response",
                argument_expr=['"Location"', '"/users"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="execute",
                receiver_type="okhttp3.Call",
                receiver_expr="call",
                start_line=2,
                start_column=5,
                end_line=2,
                end_column=20,
            ),
        ]
    )

    accessor = grouping.nodes[0]
    assert accessor.http_classification is None

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.path == ""
    assert event.header_names == []
    assert event.correlated_builder_sources == []


def test_helper_return_type_infers_mockmvc_receiver_for_next_chain_node() -> None:
    helper_ref = MethodRef(
        defining_class_name="example.MockMvcHelpers",
        method_signature="mvc()",
    )
    mvc = make_call_site(
        method_name="mvc",
        callee_signature="mvc()",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=6,
    )
    perform = make_call_site(
        method_name="perform",
        receiver_expr="mvc()",
        argument_expr=['request(GET, "/users")'],
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=45,
    )
    request = make_call_site(
        method_name="request",
        receiver_type=(
            "org.springframework.test.web.servlet.request.MockMvcRequestBuilders"
        ),
        argument_expr=["GET", '"/users"'],
        start_line=1,
        start_column=15,
        end_line=1,
        end_column=44,
    )
    runtime_view = _runtime_view_for_setup_call_sites([perform, mvc, request])
    grouping = runtime_view.entries[0].grouping
    mvc_node = grouping.node_for_call_site(mvc)
    assert mvc_node is not None
    mvc_node.resolved_helper = helper_ref

    helper_file = "src/test/java/example/MockMvcHelpers.java"
    analysis = FakeJavaAnalysis(
        classes={"example.MockMvcHelpers": make_type()},
        methods_by_class={
            "example.MockMvcHelpers": {
                "mvc()": make_callable(
                    signature="mvc()",
                    declaration="MockMvc mvc()",
                    return_type="MockMvc",
                )
            }
        },
        java_files={"example.MockMvcHelpers": helper_file},
        import_declarations_by_file={
            helper_file: [
                make_import_declaration("org.springframework.test.web.servlet.MockMvc")
            ]
        },
    )

    http_request_interaction_builder.classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(
            runtime_view,
            analysis=analysis,
        ),
    )

    events = _event_nodes(grouping)
    assert [event.call_site for event in events] == [perform]
    event = events[0].http_classification
    assert event is not None
    assert event.receiver_type == "org.springframework.test.web.servlet.MockMvc"
    assert event.http_method == "GET"
    assert event.path == "/users"


def test_helper_return_type_infers_webtestclient_receiver_for_next_chain_node() -> None:
    helper_ref = MethodRef(
        defining_class_name="example.WebTestClientHelpers",
        method_signature="webTestClient()",
    )
    web_test_client = make_call_site(
        method_name="webTestClient",
        callee_signature="webTestClient()",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=16,
    )
    get = make_call_site(
        method_name="get",
        receiver_expr="webTestClient()",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=22,
    )
    runtime_view = _runtime_view_for_setup_call_sites([get, web_test_client])
    grouping = runtime_view.entries[0].grouping
    helper_node = grouping.node_for_call_site(web_test_client)
    assert helper_node is not None
    helper_node.resolved_helper = helper_ref

    helper_file = "src/test/java/example/WebTestClientHelpers.java"
    analysis = FakeJavaAnalysis(
        classes={"example.WebTestClientHelpers": make_type()},
        methods_by_class={
            "example.WebTestClientHelpers": {
                "webTestClient()": make_callable(
                    signature="webTestClient()",
                    declaration="WebTestClient webTestClient()",
                    return_type="WebTestClient",
                )
            }
        },
        java_files={"example.WebTestClientHelpers": helper_file},
        import_declarations_by_file={
            helper_file: [
                make_import_declaration(
                    "org.springframework.test.web.reactive.server.WebTestClient"
                )
            ]
        },
    )

    http_request_interaction_builder.classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(
            runtime_view,
            analysis=analysis,
        ),
    )

    builder_nodes = [
        node
        for node in grouping.nodes
        if node.http_classification is not None
        and node.http_classification.request_role == HttpRequestRole.BUILDER
    ]
    assert [node.call_site for node in builder_nodes] == [get]
    builder = builder_nodes[0].http_classification
    assert builder is not None
    assert (
        builder.receiver_type
        == "org.springframework.test.web.reactive.server.WebTestClient"
    )
    assert builder.framework == HttpDispatchFramework.WEBTESTCLIENT
    assert builder.http_method == "GET"


def test_rest_assured_builder_input_evidence_merges_into_request_event() -> None:
    runtime_view = _runtime_view_for_setup_call_sites(
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
                argument_expr=['"q"', '"abc"'],
                start_line=1,
                start_column=5,
                end_line=2,
                end_column=33,
            ),
            make_call_site(
                method_name="header",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"X-Token"', "token"],
                start_line=1,
                start_column=5,
                end_line=3,
                end_column=38,
            ),
            make_call_site(
                method_name="formParam",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"file"', "file"],
                start_line=1,
                start_column=5,
                end_line=4,
                end_column=36,
            ),
            make_call_site(
                method_name="body",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=["payload"],
                start_line=1,
                start_column=5,
                end_line=5,
                end_column=25,
            ),
            make_call_site(
                method_name="post",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/upload/{id}"'],
                start_line=1,
                start_column=5,
                end_line=6,
                end_column=32,
            ),
        ]
    )
    from tests.cldk_factories import classify_runtime_view_for_testing

    classify_runtime_view_for_testing(runtime_view)
    interactions = (
        http_request_interaction_builder.build_output_http_request_interactions(
            runtime_view=runtime_view
        )
    )

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
    assert event_call.form_param_names == ["file"]
    assert event_call.path_param_names == ["id"]
    assert event_call.has_body_payload is True


def _rest_assured_event_classification(call_sites):
    grouping = _classify_and_get_grouping(call_sites)
    events = _event_nodes(grouping)
    assert len(events) == 1
    return events[0].http_classification


def test_rest_assured_untyped_param_on_post_is_form_param() -> None:
    event = _rest_assured_event_classification(
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
                method_name="param",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"username"', '"u"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="post",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/login"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=60,
            ),
        ]
    )
    assert event.http_method == "POST"
    assert event.path == "/login"
    assert event.query_param_names == []
    assert event.form_param_names == ["username"]


def test_rest_assured_untyped_params_on_post_are_form_params() -> None:
    event = _rest_assured_event_classification(
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
                method_name="params",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"username"', '"u"', '"password"', '"p"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=70,
            ),
            make_call_site(
                method_name="post",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/login"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=90,
            ),
        ]
    )
    assert event.http_method == "POST"
    assert event.form_param_names == ["username", "password"]
    assert event.query_param_names == []


def test_rest_assured_untyped_param_on_get_remains_query_param() -> None:
    event = _rest_assured_event_classification(
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
                method_name="param",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"q"', '"v"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/search"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=60,
            ),
        ]
    )
    assert event.http_method == "GET"
    assert event.query_param_names == ["q"]
    assert event.form_param_names == []


@pytest.mark.parametrize("verb", ["GET", "PUT", "PATCH", "DELETE"])
def test_rest_assured_untyped_param_on_non_post_verbs_is_query_param(verb) -> None:
    event = _rest_assured_event_classification(
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
                method_name="param",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"q"', '"v"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name=verb.lower(),
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/resource"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=60,
            ),
        ]
    )
    assert event.http_method == verb
    assert event.query_param_names == ["q"]
    assert event.form_param_names == []


def test_rest_assured_query_param_on_post_stays_query_param() -> None:
    event = _rest_assured_event_classification(
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
                argument_expr=['"q"', '"v"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=45,
            ),
            make_call_site(
                method_name="post",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/search"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=65,
            ),
        ]
    )
    assert event.http_method == "POST"
    assert event.query_param_names == ["q"]
    assert event.form_param_names == []


def test_rest_assured_form_param_on_post_stays_form_param() -> None:
    event = _rest_assured_event_classification(
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
                method_name="formParam",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"username"', '"u"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="post",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/login"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=70,
            ),
        ]
    )
    assert event.http_method == "POST"
    assert event.query_param_names == []
    assert event.form_param_names == ["username"]


def test_rest_assured_param_query_param_and_form_param_on_post_stay_separate() -> None:
    event = _rest_assured_event_classification(
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
                method_name="param",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"username"', '"u"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="queryParam",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"q"', '"v"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=65,
            ),
            make_call_site(
                method_name="formParam",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"file"', '"f"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=90,
            ),
            make_call_site(
                method_name="post",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/upload"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=110,
            ),
        ]
    )
    assert event.http_method == "POST"
    assert event.query_param_names == ["q"]
    assert event.form_param_names == ["file", "username"]


def test_rest_assured_param_after_when_before_post_is_form_param() -> None:
    # Chain order variant: param sits after `when()` but still before the event.
    event = _rest_assured_event_classification(
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
                method_name="when",
                receiver_type="io.restassured.specification.RequestSpecification",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="param",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"username"', '"u"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=60,
            ),
            make_call_site(
                method_name="post",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/login"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=80,
            ),
        ]
    )
    assert event.http_method == "POST"
    assert event.query_param_names == []
    assert event.form_param_names == ["username"]


def test_rest_assured_receiverless_param_on_post_is_form_param() -> None:
    # Receiverless recovered REST Assured chains flow through the same merge and
    # normalization path as typed chains.
    event = _rest_assured_event_classification(
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
                method_name="param",
                argument_expr=['"username"', '"u"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="post",
                argument_expr=['"/login"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=60,
            ),
        ]
    )
    assert event.http_method == "POST"
    assert event.path == "/login"
    assert event.query_param_names == []
    assert event.form_param_names == ["username"]


def test_rest_assured_param_on_post_records_form_param_builder_source() -> None:
    event = _rest_assured_event_classification(
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
                method_name="param",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"username"', '"u"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="post",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/login"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=60,
            ),
        ]
    )
    assert event.http_method == "POST"
    assert event.rest_assured_ambiguous_param_names == []
    assert [source.method_name for source in event.correlated_builder_sources] == [
        "param"
    ]
    assert [
        source.contributed_properties for source in event.correlated_builder_sources
    ] == [["form_param_names"]]


def test_rest_assured_param_on_get_records_query_param_builder_source() -> None:
    event = _rest_assured_event_classification(
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
                method_name="param",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"q"', '"v"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/search"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=60,
            ),
        ]
    )
    assert event.http_method == "GET"
    assert event.rest_assured_ambiguous_param_names == []
    assert [source.method_name for source in event.correlated_builder_sources] == [
        "param"
    ]
    assert [
        source.contributed_properties for source in event.correlated_builder_sources
    ] == [["query_param_names"]]


def test_rest_assured_ambiguous_param_with_unknown_verb_is_dropped() -> None:
    # `request(methodVar, ...)` leaves the verb UNKNOWN, so the overloaded
    # `param()` cannot be statically bucketed as query or form. Guessing query
    # (the prior behavior) is a false positive whenever the runtime verb is POST,
    # so the ambiguous name is dropped from both buckets and its provisional
    # builder contribution is removed rather than leaking into stats.
    event = _rest_assured_event_classification(
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
                method_name="param",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"x"', '"y"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="request",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=["methodVar", '"/path"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=60,
            ),
        ]
    )
    assert event.http_method == "UNKNOWN"
    assert event.path == "/path"
    assert event.query_param_names == []
    assert event.form_param_names == []
    assert event.rest_assured_ambiguous_param_names == []
    assert event.correlated_builder_sources == []


def test_rest_assured_param_after_post_in_separate_chain_is_ignored() -> None:
    # A builder that follows the event on a separate chain is not correlated,
    # so its name is not recovered. This matches existing cross-chain semantics.
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="post",
                receiver_type="io.restassured.specification.RequestSpecification",
                receiver_expr="when",
                argument_expr=['"/login"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=25,
            ),
            make_call_site(
                method_name="param",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"username"', '"u"'],
                start_line=2,
                start_column=5,
                end_line=2,
                end_column=35,
            ),
        ]
    )
    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.query_param_names == []
    assert event.form_param_names == []


def test_mockmvc_builder_input_evidence_merges_into_perform_event() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=100,
            ),
            make_call_site(
                method_name="post",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockMvcRequestBuilders"
                ),
                argument_expr=['"/users"'],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="param",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockHttpServletRequestBuilder"
                ),
                argument_expr=['"source"', '"web"'],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=55,
            ),
            make_call_site(
                method_name="header",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockHttpServletRequestBuilder"
                ),
                argument_expr=['"X-Token"', "token"],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=70,
            ),
            make_call_site(
                method_name="content",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockHttpServletRequestBuilder"
                ),
                argument_expr=["json"],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=85,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == "/users"
    assert event.query_param_names == ["source"]
    assert event.header_names == ["x-token"]
    assert event.has_body_payload is True


def test_webtestclient_exchange_inferred_from_classified_chain_builders() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type="org.springframework.test.web.reactive.server.WebTestClient",
                return_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient.RequestHeadersUriSpec<?>"
                ),
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="uri",
                receiver_type="",
                argument_expr=['"/users"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=38,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type="",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=49,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "GET"
    assert event.path == "/users"
    assert event.framework == HttpDispatchFramework.WEBTESTCLIENT
    assert event.owner_family == "webtestclient.request_executor"


def test_mockmvc_perform_inferred_from_classified_argument_builder() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="perform",
                receiver_type="",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=80,
            ),
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockMvcRequestBuilders"
                ),
                argument_expr=['"/orders"'],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=38,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "GET"
    assert event.path == "/orders"
    assert event.framework == HttpDispatchFramework.MOCKMVC
    assert event.owner_family == "mockmvc.request_executor"


def test_mockmvc_request_builder_resolves_ordinary_imported_class_literal() -> None:
    perform_call = make_call_site(
        method_name="perform",
        receiver_expr="mockMvc",
        receiver_type="",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=80,
    )
    get_call = make_call_site(
        method_name="get",
        receiver_expr="MockMvcRequestBuilders",
        receiver_type="",
        argument_expr=['"/orders"'],
        start_line=1,
        start_column=20,
        end_line=1,
        end_column=55,
    )
    test_method = make_callable(call_sites=[perform_call, get_call])
    owner = MethodRef(
        defining_class_name="example.MockMvcTest",
        method_signature=test_method.signature,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=build_call_site_grouping(list(test_method.call_sites)),
                method_details=test_method,
            )
        ]
    )
    analysis = FakeJavaAnalysis(
        classes={
            owner.defining_class_name: make_type(
                field_declarations=[
                    make_field(type_name="MockMvc", variables=["mockMvc"])
                ]
            )
        },
        methods_by_class={
            owner.defining_class_name: {owner.method_signature: test_method}
        },
        java_files={owner.defining_class_name: "MockMvcTest.java"},
        import_declarations_by_file={
            "MockMvcTest.java": [
                make_import_declaration("org.springframework.test.web.servlet.MockMvc"),
                make_import_declaration(
                    "org.springframework.test.web.servlet.request."
                    "MockMvcRequestBuilders"
                ),
            ]
        },
    )
    resolver = build_runtime_receiver_resolver_for_testing(
        runtime_view,
        analysis=analysis,
    )

    http_request_interaction_builder.classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=resolver,
    )

    events = _event_nodes(runtime_view.entries[0].grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "GET"
    assert event.path == "/orders"
    assert event.framework == HttpDispatchFramework.MOCKMVC
    assert event.owner_family == "mockmvc.request_executor"


def test_terminal_event_is_not_inferred_from_method_names_alone() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type="",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=10,
            ),
            make_call_site(
                method_name="uri",
                receiver_type="",
                argument_expr=['"/users"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type="",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=35,
            ),
        ]
    )

    assert _event_nodes(grouping) == []


def test_terminal_event_is_not_inferred_from_ambiguous_builder_frameworks() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="given",
                receiver_type="io.restassured.RestAssured",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=8,
            ),
            make_call_site(
                method_name="uri",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient.RequestHeadersUriSpec<?>"
                ),
                argument_expr=['"/users"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=22,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type="",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=33,
            ),
        ]
    )

    assert _event_nodes(grouping) == []


def test_rest_assured_body_after_then_is_recovered_as_response_assertion() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="given",
                receiver_type="io.restassured.RestAssured",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=8,
            ),
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/users"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=22,
            ),
            make_call_site(
                method_name="then",
                receiver_type="",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=29,
            ),
            make_call_site(
                method_name="body",
                receiver_type="",
                argument_expr=['"id"', "equalTo(1)"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=52,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert events[0].call_site.method_name == "get"
    assert event.http_method == "GET"
    assert event.path == "/users"
    assert event.framework == HttpDispatchFramework.REST_ASSURED

    body_nodes = [
        node for node in grouping.nodes if node.call_site.method_name == "body"
    ]
    assert len(body_nodes) == 1
    body_classification = body_nodes[0].http_classification
    assert body_classification is not None
    assert body_classification.request_role is None
    assert body_classification.response_role == HttpResponseRole.BODY_ASSERTION
    assert body_nodes[0].assertion_classification is not None
    assert body_nodes[0].assertion_classification.role == AssertionRole.BODY


def test_rest_assured_receiverless_status_without_then_is_not_recovered() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="given",
                receiver_type="io.restassured.RestAssured",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=8,
            ),
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/users"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=22,
            ),
            make_call_site(
                method_name="statusCode",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=35,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    status_node = next(
        node for node in grouping.nodes if node.call_site.method_name == "statusCode"
    )
    assert status_node.http_classification is None
    assert status_node.assertion_classification is None


def test_rest_assured_internal_typed_status_after_then_uses_registry() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="given",
                receiver_type="io.restassured.RestAssured",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=8,
            ),
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/users"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=22,
            ),
            make_call_site(
                method_name="then",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=29,
            ),
            make_call_site(
                method_name="statusCode",
                receiver_type="io.restassured.internal.ValidatableResponseImpl",
                argument_expr=["200"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=45,
            ),
        ]
    )

    then_node = next(
        node for node in grouping.nodes if node.call_site.method_name == "then"
    )
    status_node = next(
        node for node in grouping.nodes if node.call_site.method_name == "statusCode"
    )
    assert then_node.http_classification is not None
    assert then_node.http_classification.response_role == HttpResponseRole.INSPECTOR
    assert status_node.http_classification is not None
    assert (
        status_node.http_classification.response_role
        == HttpResponseRole.STATUS_ASSERTION
    )
    assert (
        status_node.http_classification.receiver_type
        == "io.restassured.internal.ValidatableResponseImpl"
    )
    assert status_node.http_classification.owner_family == (
        "rest-assured.status_assertion"
    )
    assert status_node.assertion_classification is not None
    assert status_node.assertion_classification.role == AssertionRole.STATUS
    assert status_node.assertion_classification.status_code == 200


def test_rest_assured_unrelated_typed_status_after_then_is_not_recovered() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="given",
                receiver_type="io.restassured.RestAssured",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=8,
            ),
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/users"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=22,
            ),
            make_call_site(
                method_name="then",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=29,
            ),
            make_call_site(
                method_name="statusCode",
                receiver_type="com.example.NotRestAssured",
                argument_expr=["200"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=45,
            ),
        ]
    )

    status_node = next(
        node for node in grouping.nodes if node.call_site.method_name == "statusCode"
    )
    assert status_node.http_classification is None
    assert status_node.assertion_classification is None


def test_mockmvc_multipart_factory_does_not_extract_path_as_form_param() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=100,
            ),
            make_call_site(
                method_name="multipart",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockMvcRequestBuilders"
                ),
                argument_expr=['"/upload"'],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=45,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == "/upload"
    assert event.form_param_names == []


def _mockmvc_multipart_file_grouping(file_argument_expr: list[str]):
    return _classify_and_get_grouping(
        [
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=100,
            ),
            make_call_site(
                method_name="multipart",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockMvcRequestBuilders"
                ),
                argument_expr=['"/upload"'],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=45,
            ),
            make_call_site(
                method_name="file",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockMultipartHttpServletRequestBuilder"
                ),
                argument_expr=file_argument_expr,
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=80,
            ),
        ]
    )


def test_mockmvc_multipart_file_merges_form_name_into_perform_event() -> None:
    grouping = _mockmvc_multipart_file_grouping(['"payload1"', "new byte[]{0x1}"])

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == "/upload"
    assert event.form_param_names == ["payload1"]


def test_mockmvc_multipart_file_constructor_extracts_part_name() -> None:
    grouping = _mockmvc_multipart_file_grouping(
        ['new MockMultipartFile("payload1", "original.png", "image/png", bytes)']
    )

    file_node = next(
        node for node in grouping.nodes if node.call_site.method_name == "file"
    )
    assert file_node.http_classification is not None
    assert file_node.http_classification.form_param_names == ["payload1"]


def test_mockmvc_multipart_file_constructor_dynamic_name_ignores_filename() -> None:
    grouping = _mockmvc_multipart_file_grouping(
        ['new MockMultipartFile(partName, "avatar.png", "image/png", bytes)']
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.form_param_names == []
    file_node = next(
        node for node in grouping.nodes if node.call_site.method_name == "file"
    )
    assert file_node.http_classification is not None
    assert file_node.http_classification.form_param_names == []


def test_mockmvc_multipart_file_constructor_dynamic_literal_prefix_ignored() -> None:
    grouping = _mockmvc_multipart_file_grouping(
        ['new MockMultipartFile("payload" + suffix, "avatar.png", "image/png", bytes)']
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.form_param_names == []
    file_node = next(
        node for node in grouping.nodes if node.call_site.method_name == "file"
    )
    assert file_node.http_classification is not None
    assert file_node.http_classification.form_param_names == []


def test_mockmvc_multipart_file_variable_extracts_no_form_name() -> None:
    # A pre-built file argument carries no literal part name; without dataflow the
    # name is invisible, so no form evidence is recorded (documented limitation).
    grouping = _mockmvc_multipart_file_grouping(["uploadedFile"])

    file_node = next(
        node for node in grouping.nodes if node.call_site.method_name == "file"
    )
    assert file_node.http_classification is not None
    assert file_node.http_classification.form_param_names == []


def test_rest_assured_multipart_builder_extracts_form_field_name() -> None:
    runtime_view = _runtime_view_for_setup_call_sites(
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
                method_name="multiPart",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"file"', "uploadedFile"],
                start_line=1,
                start_column=5,
                end_line=2,
                end_column=40,
            ),
            make_call_site(
                method_name="post",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"/upload"'],
                start_line=1,
                start_column=5,
                end_line=3,
                end_column=28,
            ),
        ]
    )
    from tests.cldk_factories import classify_runtime_view_for_testing

    classify_runtime_view_for_testing(runtime_view)
    interactions = (
        http_request_interaction_builder.build_output_http_request_interactions(
            runtime_view=runtime_view
        )
    )

    event_calls = [
        interaction.http_call
        for interaction in interactions
        if interaction.http_call is not None
        and interaction.http_call.request_role == HttpRequestRole.EVENT
    ]
    assert len(event_calls) == 1
    event_call = event_calls[0]
    assert event_call.http_method == "POST"
    assert event_call.path == "/upload"
    assert event_call.form_param_names == ["file"]


def test_webtestclient_uri_lambda_input_evidence_merges_into_exchange_event() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="post",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestBodyUriSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=18,
            ),
            make_call_site(
                method_name="uri",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestBodyUriSpec"
                ),
                argument_expr=['b -> b.path("/search").queryParam("q", "abc").build()'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=70,
            ),
            make_call_site(
                method_name="header",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersSpec"
                ),
                argument_expr=['"X-Token"', "token"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=88,
            ),
            make_call_site(
                method_name="bodyValue",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestBodySpec"
                ),
                argument_expr=["dto"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=104,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=116,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == "/search"
    assert event.query_param_names == ["q"]
    assert event.header_names == ["x-token"]
    assert event.has_body_payload is True


def _webtestclient_body_grouping(body_argument_expr: list[str]):
    return _classify_and_get_grouping(
        [
            make_call_site(
                method_name="post",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestBodyUriSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=18,
            ),
            make_call_site(
                method_name="uri",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestBodyUriSpec"
                ),
                argument_expr=['"/users"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="body",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestBodySpec"
                ),
                argument_expr=body_argument_expr,
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=90,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=104,
            ),
        ]
    )


def test_webtestclient_form_data_inserter_merges_form_name_into_exchange_event() -> (
    None
):
    grouping = _webtestclient_body_grouping(
        ['BodyInserters.fromFormData("username", "Tester")']
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == "/users"
    assert event.form_param_names == ["username"]
    assert event.has_body_payload is True


def test_webtestclient_form_data_inserter_dynamic_literal_prefix_ignored() -> None:
    grouping = _webtestclient_body_grouping(
        ['BodyInserters.fromFormData("user" + suffix, "Tester")']
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == "/users"
    assert event.form_param_names == []
    assert event.has_body_payload is True


def test_webtestclient_multipart_data_inserter_extracts_part_name() -> None:
    grouping = _webtestclient_body_grouping(
        ['BodyInserters.fromMultipartData("file", "example".getBytes())']
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.form_param_names == ["file"]
    assert event.has_body_payload is True


def test_webtestclient_form_data_variable_without_population_keeps_body_only() -> None:
    # A map variable with no in-method literal population (built elsewhere, or
    # populated dynamically) leaves the body payload recorded but no form names.
    grouping = _webtestclient_body_grouping(["BodyInserters.fromFormData(formData)"])

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.form_param_names == []
    assert event.has_body_payload is True


def _multivaluemap_add(receiver_expr: str, key_expr: str, *, line: int):
    return make_call_site(
        method_name="add",
        receiver_type="org.springframework.util.LinkedMultiValueMap",
        receiver_expr=receiver_expr,
        argument_expr=[key_expr, "value"],
        start_line=line,
        start_column=5,
        end_line=line,
        end_column=40,
    )


def _webtestclient_map_request_chain(
    body_argument_expr, *, line: int, path: str = "/users"
):
    return [
        make_call_site(
            method_name="post",
            receiver_type=(
                "org.springframework.test.web.reactive.server."
                "WebTestClient$RequestBodyUriSpec"
            ),
            start_line=line,
            start_column=5,
            end_line=line,
            end_column=18,
        ),
        make_call_site(
            method_name="uri",
            receiver_type=(
                "org.springframework.test.web.reactive.server."
                "WebTestClient$RequestBodyUriSpec"
            ),
            argument_expr=[f'"{path}"'],
            start_line=line,
            start_column=5,
            end_line=line,
            end_column=40,
        ),
        make_call_site(
            method_name="body",
            receiver_type=(
                "org.springframework.test.web.reactive.server."
                "WebTestClient$RequestBodySpec"
            ),
            argument_expr=body_argument_expr,
            start_line=line,
            start_column=5,
            end_line=line,
            end_column=90,
        ),
        make_call_site(
            method_name="exchange",
            receiver_type=(
                "org.springframework.test.web.reactive.server."
                "WebTestClient$RequestHeadersSpec"
            ),
            start_line=line,
            start_column=5,
            end_line=line,
            end_column=104,
        ),
    ]


def _webtestclient_map_body_grouping(body_argument_expr, population):
    # Map population precedes a `post().uri().body(<inserter>).exchange()` chain.
    chain = _webtestclient_map_request_chain(body_argument_expr, line=9)
    return _classify_and_get_grouping(population + chain)


def test_webtestclient_form_data_map_variable_resolves_form_names() -> None:
    grouping = _webtestclient_map_body_grouping(
        ["BodyInserters.fromFormData(formData)"],
        [
            _multivaluemap_add("formData", '"username"', line=1),
            _multivaluemap_add("formData", '"email"', line=2),
        ],
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == "/users"
    assert event.form_param_names == ["username", "email"]
    assert event.has_body_payload is True


def test_webtestclient_form_data_map_variable_ignores_later_population() -> None:
    grouping = _webtestclient_map_body_grouping(
        ["BodyInserters.fromFormData(formData)"],
        [
            _multivaluemap_add("formData", '"username"', line=1),
            _multivaluemap_add("formData", '"later"', line=20),
        ],
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.form_param_names == ["username"]
    assert event.has_body_payload is True


def test_webtestclient_reused_form_data_map_does_not_backfill_later_fields() -> None:
    grouping = _classify_and_get_grouping(
        [
            _multivaluemap_add("formData", '"first"', line=1),
            *_webtestclient_map_request_chain(
                ["BodyInserters.fromFormData(formData)"],
                line=5,
                path="/first",
            ),
            _multivaluemap_add("formData", '"second"', line=10),
            *_webtestclient_map_request_chain(
                ["BodyInserters.fromFormData(formData)"],
                line=15,
                path="/second",
            ),
        ]
    )

    events = sorted(_event_nodes(grouping), key=lambda event: event.span.start)
    assert len(events) == 2
    first_event = events[0].http_classification
    second_event = events[1].http_classification
    assert first_event.path == "/first"
    assert first_event.form_param_names == ["first"]
    assert second_event.path == "/second"
    assert second_event.form_param_names == ["first", "second"]


def test_webtestclient_multipart_data_map_variable_resolves_part_names() -> None:
    grouping = _webtestclient_map_body_grouping(
        ["BodyInserters.fromMultipartData(multipartData)"],
        [_multivaluemap_add("multipartData", '"file"', line=1)],
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.form_param_names == ["file"]
    assert event.has_body_payload is True


def test_webtestclient_form_data_map_variable_with_dynamic_key_resolves_nothing() -> (
    None
):
    # A non-literal key cannot be named statically; the body payload is still
    # recorded but no form names are claimed.
    grouping = _webtestclient_map_body_grouping(
        ["BodyInserters.fromFormData(formData)"],
        [_multivaluemap_add("formData", "dynamicKey", line=1)],
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.form_param_names == []
    assert event.has_body_payload is True


def test_webtestclient_form_data_map_variable_dynamic_key_fragment_resolves_nothing() -> (
    None
):
    grouping = _webtestclient_map_body_grouping(
        ["BodyInserters.fromFormData(formData)"],
        [_multivaluemap_add("formData", 'prefix + "name"', line=1)],
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.form_param_names == []
    assert event.has_body_payload is True


def test_webtestclient_form_data_map_variable_ignores_other_variable_population() -> (
    None
):
    # Only the variable actually passed to the inserter contributes names; an
    # unrelated map populated in the same method must not leak its keys.
    grouping = _webtestclient_map_body_grouping(
        ["BodyInserters.fromFormData(formData)"],
        [
            _multivaluemap_add("formData", '"username"', line=1),
            _multivaluemap_add("otherMap", '"leaked"', line=2),
        ],
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.form_param_names == ["username"]


def _webclient_body_chain(body_argument_expr, *, line: int = 1):
    return [
        make_call_site(
            method_name="post",
            receiver_type=(
                "org.springframework.web.reactive.function.client."
                "WebClient$RequestBodyUriSpec"
            ),
            start_line=line,
            start_column=5,
            end_line=line,
            end_column=18,
        ),
        make_call_site(
            method_name="uri",
            receiver_type=(
                "org.springframework.web.reactive.function.client."
                "WebClient$RequestBodyUriSpec"
            ),
            argument_expr=['"/users"'],
            start_line=line,
            start_column=5,
            end_line=line,
            end_column=40,
        ),
        make_call_site(
            method_name="body",
            receiver_type=(
                "org.springframework.web.reactive.function.client."
                "WebClient$RequestBodySpec"
            ),
            argument_expr=body_argument_expr,
            start_line=line,
            start_column=5,
            end_line=line,
            end_column=90,
        ),
        make_call_site(
            method_name="retrieve",
            receiver_type=(
                "org.springframework.web.reactive.function.client."
                "WebClient$RequestHeadersSpec"
            ),
            start_line=line,
            start_column=5,
            end_line=line,
            end_column=104,
        ),
    ]


def test_webclient_form_data_inserter_merges_form_name_into_retrieve_event() -> None:
    grouping = _classify_and_get_grouping(
        _webclient_body_chain(['BodyInserters.fromFormData("user", password)'])
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == "/users"
    assert event.form_param_names == ["user"]
    assert event.has_body_payload is True


def test_webclient_form_data_map_variable_resolves_form_names() -> None:
    grouping = _classify_and_get_grouping(
        [
            _multivaluemap_add("formData", '"username"', line=1),
            *_webclient_body_chain(["BodyInserters.fromFormData(formData)"], line=9),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.form_param_names == ["username"]
    assert event.has_body_payload is True


# ---------------------------------------------------------------------------
# Header constant decoding and container-variable recovery
# ---------------------------------------------------------------------------


def _decode_header(expression: str) -> str:
    return http_request_interaction_builder._header_name_from_argument([expression], 0)


def test_header_constant_decoding_rules() -> None:
    # Qualified by a recognized header-constant class: any constant decodes via
    # SCREAMING_SNAKE -> kebab, lowercased.
    assert _decode_header("HttpHeaders.IF_MATCH") == "if-match"
    assert _decode_header("HttpHeaders.WWW_AUTHENTICATE") == "www-authenticate"
    assert _decode_header("HttpHeaderNames.ACCEPT_ENCODING") == "accept-encoding"
    assert (
        _decode_header("org.springframework.http.HttpHeaders.IF_NONE_MATCH")
        == "if-none-match"
    )
    # Bare/static-imported tokens decode only when they are standard headers.
    assert _decode_header("CONTENT_TYPE") == "content-type"
    assert _decode_header("MAX_RETRIES") == ""
    # Non-header constants on a header class, and standard names qualified by a
    # foreign class, are left undecoded (their value is unknown).
    assert _decode_header("HttpHeaders.EMPTY") == ""
    assert _decode_header("MediaType.APPLICATION_JSON_VALUE") == ""
    assert _decode_header("AppConstants.CONTENT_TYPE") == ""
    # A standard-header token glued to a lowercase camelCase prefix is not a bare
    # constant; the token boundary keeps it from decoding.
    assert _decode_header("requestDATE") == ""
    assert _decode_header("myACCEPT") == ""
    assert _decode_header("requestIF_MATCH") == ""
    # String literals keep their existing behavior.
    assert _decode_header('"X-Custom"') == "x-custom"


def test_header_name_concatenation_and_wrapper_rules() -> None:
    # A `+` outside string literals builds the name dynamically, so a single literal
    # or constant in the expression is only a fragment and is not taken as the name.
    assert _decode_header('"X-" + suffix') == ""
    assert _decode_header('prefix + "X-Token"') == ""
    assert _decode_header('"a" + "b"') == ""
    assert _decode_header("prefix + HttpHeaders.AUTHORIZATION") == ""
    # A `+` inside the literal itself is content, not concatenation.
    assert _decode_header('"X-Custom+Header"') == "x-custom+header"


def test_header_name_recovered_only_from_name_position() -> None:
    # The name is the whole argument, or the first argument of a recognized header
    # wrapper -- never just the first quoted string found anywhere.
    assert _decode_header('new Header("X-Token", "v")') == "x-token"
    assert _decode_header('new BasicHeader("X-Token", "v")') == "x-token"
    assert (
        _decode_header('new org.apache.http.message.BasicHeader("X-Token", "v")')
        == "x-token"
    )
    # A clean literal name with a concatenated value still resolves; the value `+`
    # does not suppress the name.
    assert _decode_header('new Header("X-Token", "Bearer " + token)') == "x-token"
    # A literal in a non-name position must not be taken as the name.
    assert _decode_header('new Header(name, "v")') == ""  # "v" is the value
    assert _decode_header('String.format("X-%s", suffix)') == ""  # format template
    assert _decode_header('new Header("X-" + suffix, "v")') == ""  # dynamic name
    # An unrecognized `...Header(...)` class is not a known wrapper.
    assert _decode_header('new SomeCustomHeader("X-Token", "v")') == ""


# ---------------------------------------------------------------------------
# Query/form/path param names are recovered only from a whole-literal argument
# ---------------------------------------------------------------------------


def _query_param_names(
    argument_exprs: list[str],
    method_name: str = "queryParam",
    owner_family: str | None = None,
):
    return http_request_interaction_builder._extract_query_param_names(
        method_name, argument_exprs, "", owner_family=owner_family
    )


def _form_param_names(argument_exprs: list[str], method_name: str = "formParam"):
    return http_request_interaction_builder._extract_form_param_names(
        framework=HttpDispatchFramework.REST_ASSURED,
        method_name=method_name,
        argument_exprs=argument_exprs,
    )


def _path_param_names(argument_exprs: list[str], method_name: str = "pathParam"):
    return http_request_interaction_builder._extract_request_path_param_names(
        method_name, argument_exprs, ""
    )


def test_param_names_recovered_only_from_whole_literal_argument() -> None:
    # A clean whole-literal in the name position is the param name.
    assert _query_param_names(['"page"', "value"]) == ["page"]
    assert _form_param_names(['"username"', "value"]) == ["username"]
    assert _path_param_names(['"id"', "value"]) == ["id"]
    # Param names are case-sensitive: the literal is preserved verbatim.
    assert _query_param_names(['"PageSize"', "value"]) == ["PageSize"]

    # A literal embedded in a larger expression is not the name -- concatenation
    # fragments, format templates, and value/wrapper literals all yield nothing.
    assert _query_param_names(['"p" + suffix', "value"]) == []
    assert _query_param_names(['prefix + "page"', "value"]) == []
    assert _query_param_names(['String.format("p%s", x)', "value"]) == []
    assert _query_param_names(['new Param("page", v)', "value"]) == []
    assert _form_param_names(['"f" + idx', "value"]) == []
    assert _path_param_names(['base + "id"', "value"]) == []


def test_alternating_param_names_recovered_only_from_whole_literals() -> None:
    # Even-index name positions that are clean literals are recovered; complex
    # name expressions in those positions are dropped (values stay at odd indices).
    assert _query_param_names(
        ['"a"', '"1"', '"b"', '"2"'], method_name="queryParams"
    ) == ["a", "b"]
    assert _query_param_names(
        ['"a" + x', '"1"', '"b"', '"2"'], method_name="queryParams"
    ) == ["b"]
    assert _form_param_names(
        ['"user"', '"u"', 'role + "Id"', '"r"'], method_name="formParams"
    ) == ["user"]
    assert _path_param_names(
        ['"id"', '"1"', 'String.format("p%s", n)', '"2"'], method_name="pathParams"
    ) == ["id"]


def test_pact_query_literal_is_parsed_as_query_string() -> None:
    # PactDslRequestWithPath.query(String)/encodedQuery(String) take the whole
    # query string, not a parameter name, so names are parsed out of it.
    assert _query_param_names(
        ['"status=open&page=1"'], method_name="query", owner_family="pact.request"
    ) == ["status", "page"]
    assert _query_param_names(
        ['"status=open&page=1"'],
        method_name="encodedQuery",
        owner_family="pact.request",
    ) == ["status", "page"]
    assert _query_param_names(
        ['"status=open"'], method_name="query", owner_family="pact.request"
    ) == ["status"]


def test_non_pact_query_literal_keeps_first_argument_as_name() -> None:
    # Feign's RequestTemplate.query(name, ...) and REST Assured's queryParam(name, ...)
    # treat the first literal as the parameter name, not a query string.
    assert _query_param_names(
        ['"status=open&page=1"'], method_name="query", owner_family="feign.request"
    ) == ["status=open&page=1"]
    assert _query_param_names(
        ['"status=open&page=1"'],
        method_name="queryParam",
        owner_family="rest-assured.request_builder",
    ) == ["status=open&page=1"]


def _webclient_single_header_chain(header_argument_expr: list[str]):
    return _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.web.reactive.function.client.WebClient"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=20,
            ),
            make_call_site(
                method_name="uri",
                argument_expr=['"/users"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=48,
            ),
            make_call_site(
                method_name="header",
                argument_expr=header_argument_expr,
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=78,
            ),
            make_call_site(
                method_name="retrieve",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=89,
            ),
        ]
    )


def test_qualified_header_constant_decoded_onto_event() -> None:
    grouping = _webclient_single_header_chain(["HttpHeaders.IF_MATCH", '"etag"'])

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.header_names == ["if-match"]


def test_static_imported_header_constant_decoded_onto_event() -> None:
    grouping = _webclient_single_header_chain(["CONTENT_TYPE", '"json"'])

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.header_names == ["content-type"]


def test_project_constant_header_not_decoded_onto_event() -> None:
    # A bare project constant cannot be tied to a value and is not a standard
    # header name, so it contributes no header evidence.
    grouping = _webclient_single_header_chain(["CUSTOM_HEADER", '"v"'])

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.header_names == []


def _header_mutator(
    receiver_expr: str, method_name: str, args: list[str], *, line: int
):
    return make_call_site(
        method_name=method_name,
        receiver_type="org.springframework.http.HttpHeaders",
        receiver_expr=receiver_expr,
        argument_expr=args,
        start_line=line,
        start_column=5,
        end_line=line,
        end_column=50,
    )


def _rest_assured_header_container_grouping(
    population,
    *,
    container_argument_expr=None,
    chain_line: int = 9,
    path: str = "/users",
):
    chain = [
        make_call_site(
            method_name="given",
            receiver_type="io.restassured.RestAssured",
            start_line=chain_line,
            start_column=5,
            end_line=chain_line,
            end_column=12,
        ),
        make_call_site(
            method_name="headers",
            argument_expr=(
                ["httpHeaders"]
                if container_argument_expr is None
                else container_argument_expr
            ),
            start_line=chain_line,
            start_column=5,
            end_line=chain_line,
            end_column=40,
        ),
        make_call_site(
            method_name="get",
            argument_expr=[f'"{path}"'],
            start_line=chain_line,
            start_column=5,
            end_line=chain_line,
            end_column=60,
        ),
    ]
    return _classify_and_get_grouping(population + chain)


def _query_map_mutator(receiver_expr: str, key_expr: str, *, line: int):
    return make_call_site(
        method_name="put",
        receiver_type="java.util.HashMap",
        receiver_expr=receiver_expr,
        argument_expr=[key_expr, "value"],
        start_line=line,
        start_column=5,
        end_line=line,
        end_column=40,
    )


def test_rest_assured_query_params_map_variable_resolves_query_names() -> None:
    # `given().queryParams(params).get(...)` where the map is populated with
    # `params.put("status", v)` earlier — the keys back the event's query names.
    chain = [
        make_call_site(
            method_name="given",
            receiver_type="io.restassured.RestAssured",
            start_line=5,
            start_column=5,
            end_line=5,
            end_column=12,
        ),
        make_call_site(
            method_name="queryParams",
            receiver_expr="given()",
            argument_expr=["params"],
            start_line=5,
            start_column=5,
            end_line=5,
            end_column=40,
        ),
        make_call_site(
            method_name="get",
            argument_expr=['"/orders"'],
            start_line=5,
            start_column=5,
            end_line=5,
            end_column=60,
        ),
    ]
    population = [
        _query_map_mutator("params", '"status"', line=1),
        _query_map_mutator("params", '"page"', line=2),
    ]
    grouping = _classify_and_get_grouping(population + chain)

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "GET"
    assert event.path == "/orders"
    assert event.query_param_names == ["status", "page"]


def test_rest_assured_params_map_variable_resolves_by_verb() -> None:
    # `given().params(mapVar).<verb>(...)` is query for GET and form for POST; the
    # recovered map keys flow through the same verb-driven ambiguous normalization.
    for verb, expected_query, expected_form in (
        ("get", ["status", "page"], []),
        ("post", [], ["status", "page"]),
    ):
        chain = [
            make_call_site(
                method_name="given",
                receiver_type="io.restassured.RestAssured",
                start_line=5,
                start_column=5,
                end_line=5,
                end_column=12,
            ),
            make_call_site(
                method_name="params",
                receiver_expr="given()",
                argument_expr=["params"],
                start_line=5,
                start_column=5,
                end_line=5,
                end_column=40,
            ),
            make_call_site(
                method_name=verb,
                argument_expr=['"/orders"'],
                start_line=5,
                start_column=5,
                end_line=5,
                end_column=60,
            ),
        ]
        population = [
            _query_map_mutator("params", '"status"', line=1),
            _query_map_mutator("params", '"page"', line=2),
        ]
        grouping = _classify_and_get_grouping(population + chain)
        event = _event_nodes(grouping)[0].http_classification
        assert event.query_param_names == expected_query, verb
        assert event.form_param_names == expected_form, verb


def test_header_container_variable_resolves_literal_and_constant_names() -> None:
    grouping = _rest_assured_header_container_grouping(
        [
            _header_mutator(
                "httpHeaders", "add", ["HttpHeaders.AUTHORIZATION", '"tok"'], line=1
            ),
            _header_mutator("httpHeaders", "set", ['"X-Token"', '"v"'], line=2),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.header_names == ["authorization", "x-token"]


def test_header_container_variable_resolves_typed_setters() -> None:
    grouping = _rest_assured_header_container_grouping(
        [
            _header_mutator("httpHeaders", "setContentType", ["mediaType"], line=1),
            _header_mutator("httpHeaders", "setIfMatch", ['"etag"'], line=2),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.header_names == ["content-type", "if-match"]


def test_header_container_dynamic_argument_resolves_nothing() -> None:
    # A non-identifier `.headers(...)` argument (a builder call) cannot be tied to
    # in-method population, so no header names are recovered.
    grouping = _rest_assured_header_container_grouping(
        [_header_mutator("httpHeaders", "add", ['"X-Token"', '"v"'], line=1)],
        container_argument_expr=["buildHeaders()"],
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.header_names == []


def test_header_container_concatenated_key_is_not_recovered() -> None:
    # A concatenated key (`"X-" + suffix`) names the header dynamically; the literal
    # fragment must not be recovered, while a plain literal key still is.
    grouping = _rest_assured_header_container_grouping(
        [
            _header_mutator("httpHeaders", "add", ['"X-" + suffix', '"v"'], line=1),
            _header_mutator("httpHeaders", "add", ['"X-Token"', '"v"'], line=2),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.header_names == ["x-token"]


def test_header_container_dynamic_key_does_not_recover_value() -> None:
    # When the key is a variable, the literal value in the next argument must not be
    # mistaken for the header name.
    grouping = _rest_assured_header_container_grouping(
        [_header_mutator("httpHeaders", "add", ["headerName", '"some-value"'], line=1)]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.header_names == []


def test_header_container_ignores_population_after_dispatch() -> None:
    grouping = _rest_assured_header_container_grouping(
        [
            _header_mutator("httpHeaders", "add", ['"X-Token"', '"v"'], line=1),
            _header_mutator("httpHeaders", "add", ['"X-Later"', '"v"'], line=20),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.header_names == ["x-token"]


def test_header_container_ignores_other_variable_population() -> None:
    grouping = _rest_assured_header_container_grouping(
        [
            _header_mutator("httpHeaders", "add", ['"X-Token"', '"v"'], line=1),
            _header_mutator("otherHeaders", "add", ['"X-Leaked"', '"v"'], line=2),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.header_names == ["x-token"]


def test_response_side_headers_assertion_does_not_recover_request_names() -> None:
    # `.then().headers(expected)` is a RestAssured response header assertion, not a
    # request builder; its container population must not surface as request headers.
    grouping = _classify_and_get_grouping(
        [
            _header_mutator("expected", "add", ['"X-Leaked"', '"v"'], line=1),
            make_call_site(
                method_name="given",
                receiver_type="io.restassured.RestAssured",
                start_line=9,
                start_column=5,
                end_line=9,
                end_column=12,
            ),
            make_call_site(
                method_name="get",
                argument_expr=['"/users"'],
                start_line=9,
                start_column=5,
                end_line=9,
                end_column=30,
            ),
            make_call_site(
                method_name="then",
                start_line=9,
                start_column=5,
                end_line=9,
                end_column=40,
            ),
            make_call_site(
                method_name="headers",
                argument_expr=["expected"],
                start_line=9,
                start_column=5,
                end_line=9,
                end_column=60,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.header_names == []
    headers_node = next(
        node
        for node in grouping.nodes
        if (node.call_site.method_name or "").lower() == "headers"
    )
    assert (
        headers_node.http_classification.response_role
        == HttpResponseRole.HEADER_ASSERTION
    )
    assert headers_node.http_classification.header_names == []


def test_webclient_receiverless_retrieve_after_builder_chain_is_recovered() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.web.reactive.function.client.WebClient"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=20,
            ),
            make_call_site(
                method_name="uri",
                argument_expr=['"/users/{id}"', "userId"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=48,
            ),
            make_call_site(
                method_name="header",
                argument_expr=['"Authorization"', "token"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=78,
            ),
            make_call_site(
                method_name="retrieve",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=89,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "GET"
    assert event.path == "/users/{id}"
    assert event.framework == HttpDispatchFramework.WEBCLIENT
    assert event.owner_family == "webclient.request"
    assert event.header_names == ["authorization"]
    assert event.path_param_names == ["id"]


def test_rest_client_retrieve_after_builder_chain_is_recovered() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="post",
                receiver_type="org.springframework.web.client.RestClient",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=22,
            ),
            make_call_site(
                method_name="uri",
                argument_expr=['"/instances"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=42,
            ),
            make_call_site(
                method_name="contentType",
                argument_expr=["MediaType.APPLICATION_JSON"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=80,
            ),
            make_call_site(
                method_name="body",
                argument_expr=["application"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=98,
            ),
            make_call_site(
                method_name="retrieve",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=109,
            ),
            make_call_site(
                method_name="body",
                argument_expr=["RESPONSE_TYPE"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=129,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == "/instances"
    assert event.framework == HttpDispatchFramework.REST_CLIENT
    assert event.owner_family == "rest-client.request"
    assert event.header_names == ["content-type"]
    assert event.has_body_payload is True

    response_body = grouping.nodes[-1]
    assert response_body.http_classification is None


def test_rest_client_delete_retrieve_chain_is_recovered() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="delete",
                receiver_type="org.springframework.web.client.RestClient",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="uri",
                argument_expr=['"/instances/{id}"', "id"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=55,
            ),
            make_call_site(
                method_name="retrieve",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=66,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "DELETE"
    assert event.path == "/instances/{id}"
    assert event.framework == HttpDispatchFramework.REST_CLIENT
    assert event.path_param_names == ["id"]


def test_rest_assured_receiverless_response_roles_recovered_after_event() -> None:
    grouping = _classify_and_get_grouping(
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
                method_name="header",
                argument_expr=['"X-Token"', "token"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=38,
            ),
            make_call_site(
                method_name="get",
                argument_expr=['"/users"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=52,
            ),
            make_call_site(
                method_name="then",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=59,
            ),
            make_call_site(
                method_name="statusCode",
                argument_expr=["201"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=75,
            ),
            make_call_site(
                method_name="body",
                argument_expr=['"id"', "equalTo(1)"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=99,
            ),
        ]
    )

    nodes_by_name = {node.call_site.method_name: node for node in grouping.nodes}

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "GET"
    assert event.path == "/users"
    assert event.header_names == ["x-token"]

    then_http = nodes_by_name["then"].http_classification
    status_http = nodes_by_name["statusCode"].http_classification
    body_http = nodes_by_name["body"].http_classification

    assert then_http is not None
    assert then_http.response_role == HttpResponseRole.INSPECTOR
    assert status_http is not None
    assert status_http.response_role == HttpResponseRole.STATUS_ASSERTION
    assert body_http is not None
    assert body_http.request_role is None
    assert body_http.response_role == HttpResponseRole.BODY_ASSERTION

    status_assertion = nodes_by_name["statusCode"].assertion_classification
    body_assertion = nodes_by_name["body"].assertion_classification
    assert status_assertion is not None
    assert status_assertion.role == AssertionRole.STATUS
    assert status_assertion.status_code == 201
    assert body_assertion is not None
    assert body_assertion.role == AssertionRole.BODY


def test_rest_assured_receiverless_extract_followers_are_extractors() -> None:
    grouping = _classify_and_get_grouping(
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
                method_name="get",
                argument_expr=['"/users"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=52,
            ),
            make_call_site(
                method_name="then",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=59,
            ),
            make_call_site(
                method_name="extract",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=68,
            ),
            make_call_site(
                method_name="statusCode",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=80,
            ),
            make_call_site(
                method_name="header",
                argument_expr=['"Location"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=95,
            ),
            make_call_site(
                method_name="body",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=102,
            ),
            make_call_site(
                method_name="asString",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=113,
            ),
        ]
    )

    nodes_by_name = {node.call_site.method_name: node for node in grouping.nodes}
    assert nodes_by_name["statusCode"].http_classification is not None
    assert (
        nodes_by_name["statusCode"].http_classification.response_role
        == HttpResponseRole.EXTRACTOR
    )
    assert nodes_by_name["header"].http_classification is not None
    assert (
        nodes_by_name["header"].http_classification.response_role
        == HttpResponseRole.EXTRACTOR
    )
    assert nodes_by_name["body"].http_classification is not None
    assert (
        nodes_by_name["body"].http_classification.response_role
        == HttpResponseRole.EXTRACTOR
    )
    assert nodes_by_name["asString"].http_classification is not None
    assert (
        nodes_by_name["asString"].http_classification.response_role
        == HttpResponseRole.EXTRACTOR
    )


def test_mockmvc_receiverless_andexpect_arguments_recovered_after_perform() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=42,
            ),
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockMvcRequestBuilders"
                ),
                argument_expr=['"/users"'],
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=32,
            ),
            make_call_site(
                method_name="andExpect",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=75,
            ),
            make_call_site(
                method_name="status",
                start_line=1,
                start_column=54,
                end_line=1,
                end_column=62,
            ),
            make_call_site(
                method_name="isOk",
                start_line=1,
                start_column=54,
                end_line=1,
                end_column=69,
            ),
            make_call_site(
                method_name="andExpect",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=120,
            ),
            make_call_site(
                method_name="jsonPath",
                argument_expr=['"$.id"'],
                start_line=1,
                start_column=88,
                end_line=1,
                end_column=104,
            ),
            make_call_site(
                method_name="value",
                argument_expr=["1"],
                start_line=1,
                start_column=88,
                end_line=1,
                end_column=113,
            ),
        ]
    )

    and_expect_nodes = [
        node for node in grouping.nodes if node.call_site.method_name == "andExpect"
    ]
    nodes_by_name = {node.call_site.method_name: node for node in grouping.nodes}

    assert len(and_expect_nodes) == 2
    assert all(
        node.http_classification is not None
        and node.http_classification.response_role == HttpResponseRole.INSPECTOR
        for node in and_expect_nodes
    )

    status_root_http = nodes_by_name["status"].http_classification
    status_http = nodes_by_name["isOk"].http_classification
    body_root_http = nodes_by_name["jsonPath"].http_classification
    body_http = nodes_by_name["value"].http_classification

    assert status_root_http is not None
    assert status_root_http.response_role == HttpResponseRole.MATCHER
    assert status_http is not None
    assert status_http.response_role == HttpResponseRole.STATUS_ASSERTION
    assert body_root_http is not None
    assert body_root_http.response_role == HttpResponseRole.MATCHER
    assert body_http is not None
    assert body_http.response_role == HttpResponseRole.BODY_ASSERTION

    status_assertion = nodes_by_name["isOk"].assertion_classification
    body_assertion = nodes_by_name["value"].assertion_classification
    assert status_assertion is not None
    assert status_assertion.role == AssertionRole.STATUS
    assert status_assertion.status_code == 200
    assert body_assertion is not None
    assert body_assertion.role == AssertionRole.BODY


def test_mockmvc_argument_recovery_ignores_nested_expected_value_calls() -> None:
    runtime_view = _runtime_view_for_setup_call_sites(
        [
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=42,
            ),
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockMvcRequestBuilders"
                ),
                argument_expr=['"/users"'],
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=32,
            ),
            make_call_site(
                method_name="andExpect",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=150,
            ),
            make_call_site(
                method_name="jsonPath",
                argument_expr=['"$.state"'],
                start_line=1,
                start_column=54,
                end_line=1,
                end_column=80,
            ),
            make_call_site(
                method_name="value",
                argument_expr=["order.status().name()"],
                start_line=1,
                start_column=54,
                end_line=1,
                end_column=145,
            ),
            make_call_site(
                method_name="status",
                start_line=1,
                start_column=112,
                end_line=1,
                end_column=126,
            ),
            make_call_site(
                method_name="name",
                start_line=1,
                start_column=112,
                end_line=1,
                end_column=133,
            ),
        ]
    )
    http_request_interaction_builder.classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )
    grouping = runtime_view.entries[0].grouping

    nodes_by_name = {node.call_site.method_name: node for node in grouping.nodes}

    body_root_http = nodes_by_name["jsonPath"].http_classification
    body_http = nodes_by_name["value"].http_classification
    nested_status_node = nodes_by_name["status"]
    nested_name_node = nodes_by_name["name"]

    assert body_root_http is not None
    assert body_root_http.response_role == HttpResponseRole.MATCHER
    assert body_http is not None
    assert body_http.response_role == HttpResponseRole.BODY_ASSERTION
    assert body_http.request_role is None

    assert nested_status_node.http_classification is None
    assert nested_name_node.http_classification is None


def test_mockmvc_unrelated_typed_andexpect_is_not_recovered() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=42,
            ),
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockMvcRequestBuilders"
                ),
                argument_expr=['"/users"'],
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=32,
            ),
            make_call_site(
                method_name="andExpect",
                receiver_type="com.example.NotResultActions",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=75,
            ),
            make_call_site(
                method_name="status",
                receiver_type="com.example.NotMockMvcResultMatchers",
                start_line=1,
                start_column=54,
                end_line=1,
                end_column=62,
            ),
            make_call_site(
                method_name="isOk",
                start_line=1,
                start_column=54,
                end_line=1,
                end_column=69,
            ),
        ]
    )

    nodes_by_name = {node.call_site.method_name: node for node in grouping.nodes}
    assert nodes_by_name["andExpect"].http_classification is None
    assert nodes_by_name["status"].http_classification is None
    assert nodes_by_name["isOk"].http_classification is None


def test_webtestclient_receiverless_response_roles_recovered_after_exchange() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.reactive.server.WebTestClient"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="uri",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersUriSpec"
                ),
                argument_expr=['"/problem"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=51,
            ),
            make_call_site(
                method_name="expectStatus",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=66,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=["HttpStatus.CONFLICT"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=98,
            ),
            make_call_site(
                method_name="expectHeader",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=113,
            ),
            make_call_site(
                method_name="contentType",
                argument_expr=["MediaTypes.PROBLEM"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=142,
            ),
            make_call_site(
                method_name="expectBody",
                argument_expr=["Problem.class"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=168,
            ),
        ]
    )

    nodes_by_name = {node.call_site.method_name: node for node in grouping.nodes}

    status_http = nodes_by_name["isEqualTo"].http_classification
    status_root_http = nodes_by_name["expectStatus"].http_classification
    header_root_http = nodes_by_name["expectHeader"].http_classification
    header_http = nodes_by_name["contentType"].http_classification
    body_http = nodes_by_name["expectBody"].http_classification

    assert status_root_http is not None
    assert status_root_http.response_role == HttpResponseRole.MATCHER
    assert status_http is not None
    assert status_http.response_role == HttpResponseRole.STATUS_ASSERTION
    assert header_root_http is not None
    assert header_root_http.response_role == HttpResponseRole.MATCHER
    assert header_http is not None
    assert header_http.response_role == HttpResponseRole.HEADER_ASSERTION
    assert body_http is not None
    assert body_http.response_role == HttpResponseRole.BODY_ASSERTION

    status_assertion = nodes_by_name["isEqualTo"].assertion_classification
    header_assertion = nodes_by_name["contentType"].assertion_classification
    body_assertion = nodes_by_name["expectBody"].assertion_classification

    assert status_assertion is not None
    assert status_assertion.role == AssertionRole.STATUS
    assert status_assertion.status_code == 409
    assert status_assertion.status_range == "4xx"
    assert nodes_by_name["expectStatus"].assertion_classification is not None
    assert nodes_by_name["expectStatus"].assertion_classification.is_countable is False
    assert nodes_by_name["expectHeader"].assertion_classification is None
    assert header_assertion is not None
    assert header_assertion.role == AssertionRole.HEADER
    assert body_assertion is not None
    assert body_assertion.role == AssertionRole.BODY


def test_webtestclient_unrelated_typed_response_subject_is_not_recovered() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.reactive.server.WebTestClient"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="uri",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersUriSpec"
                ),
                argument_expr=['"/problem"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=51,
            ),
            make_call_site(
                method_name="expectStatus",
                receiver_type="com.example.NotWebTestClientResponseSpec",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=66,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=["HttpStatus.CONFLICT"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=98,
            ),
        ]
    )

    nodes_by_name = {node.call_site.method_name: node for node in grouping.nodes}
    assert nodes_by_name["expectStatus"].http_classification is None
    assert nodes_by_name["isEqualTo"].http_classification is None


def test_webtestclient_resolved_receiver_subject_is_not_recovered() -> None:
    owner = MethodRef(
        defining_class_name="example.Test",
        method_signature="setUp()",
    )
    call_sites = [
        make_call_site(
            method_name="get",
            receiver_type="org.springframework.test.web.reactive.server.WebTestClient",
            start_line=2,
            start_column=5,
            end_line=2,
            end_column=24,
        ),
        make_call_site(
            method_name="uri",
            receiver_type=(
                "org.springframework.test.web.reactive.server."
                "WebTestClient$RequestHeadersUriSpec"
            ),
            argument_expr=['"/problem"'],
            start_line=2,
            start_column=5,
            end_line=2,
            end_column=40,
        ),
        make_call_site(
            method_name="exchange",
            receiver_type=(
                "org.springframework.test.web.reactive.server."
                "WebTestClient$RequestHeadersSpec"
            ),
            start_line=2,
            start_column=5,
            end_line=2,
            end_column=51,
        ),
        make_call_site(
            method_name="expectStatus",
            receiver_expr="notResponse",
            start_line=2,
            start_column=5,
            end_line=2,
            end_column=66,
        ),
        make_call_site(
            method_name="isEqualTo",
            argument_expr=["HttpStatus.CONFLICT"],
            start_line=2,
            start_column=5,
            end_line=2,
            end_column=98,
        ),
    ]
    method = make_callable(
        signature=owner.method_signature,
        call_sites=call_sites,
        variable_declarations=[
            make_variable_declaration(
                name="notResponse",
                type_name="NotWebTestClientResponseSpec",
                start_line=1,
                start_column=9,
            )
        ],
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.SETUP,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )
    analysis = FakeJavaAnalysis(
        classes={"example.NotWebTestClientResponseSpec": make_type()}
    )

    http_request_interaction_builder.classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(
            runtime_view,
            analysis=analysis,
        ),
    )

    grouping = runtime_view.entries[0].grouping
    nodes_by_name = {node.call_site.method_name: node for node in grouping.nodes}
    assert nodes_by_name["expectStatus"].http_classification is None
    assert nodes_by_name["isEqualTo"].http_classification is None


def test_webtestclient_receiverless_body_matcher_root_keeps_body_context() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.reactive.server.WebTestClient"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="uri",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersUriSpec"
                ),
                argument_expr=['"/instances"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=42,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=53,
            ),
            make_call_site(
                method_name="expectBody",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=66,
            ),
            make_call_site(
                method_name="jsonPath",
                argument_expr=['"$.registration.name"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=100,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=['"Test-Instance"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=127,
            ),
        ]
    )

    nodes_by_name = {node.call_site.method_name: node for node in grouping.nodes}

    body_root_http = nodes_by_name["expectBody"].http_classification
    json_path_http = nodes_by_name["jsonPath"].http_classification
    verifier_http = nodes_by_name["isEqualTo"].http_classification

    assert body_root_http is not None
    assert body_root_http.response_role == HttpResponseRole.MATCHER
    assert json_path_http is not None
    assert json_path_http.response_role == HttpResponseRole.MATCHER
    assert verifier_http is not None
    assert verifier_http.response_role == HttpResponseRole.BODY_ASSERTION

    assert nodes_by_name["expectBody"].assertion_classification is None
    assert nodes_by_name["jsonPath"].assertion_classification is None
    verifier_assertion = nodes_by_name["isEqualTo"].assertion_classification
    assert verifier_assertion is not None
    assert verifier_assertion.role == AssertionRole.BODY


def test_webtestclient_typed_expect_body_argument_is_body_assertion() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.reactive.server.WebTestClient"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="uri",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersUriSpec"
                ),
                argument_expr=['"/problem"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=51,
            ),
            make_call_site(
                method_name="expectBody",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$ResponseSpec"
                ),
                argument_expr=["Problem.class"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=77,
            ),
        ]
    )

    body_node = next(
        node for node in grouping.nodes if node.call_site.method_name == "expectBody"
    )

    assert body_node.http_classification is not None
    assert (
        body_node.http_classification.response_role == HttpResponseRole.BODY_ASSERTION
    )
    assert body_node.assertion_classification is not None
    assert body_node.assertion_classification.role == AssertionRole.BODY


def test_webtestclient_typed_expect_body_void_argument_is_not_body_assertion() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.reactive.server.WebTestClient"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="uri",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersUriSpec"
                ),
                argument_expr=['"/missing"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=51,
            ),
            make_call_site(
                method_name="expectBody",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$ResponseSpec"
                ),
                argument_expr=["Void.class"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=74,
            ),
        ]
    )

    body_node = next(
        node for node in grouping.nodes if node.call_site.method_name == "expectBody"
    )

    assert body_node.http_classification is not None
    assert body_node.http_classification.response_role == HttpResponseRole.MATCHER
    assert body_node.assertion_classification is None


def test_webtestclient_typed_expect_header_subject_is_matcher() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.reactive.server.WebTestClient"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="uri",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersUriSpec"
                ),
                argument_expr=['"/problem"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=51,
            ),
            make_call_site(
                method_name="expectHeader",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$ResponseSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=66,
            ),
            make_call_site(
                method_name="contentType",
                receiver_type=(
                    "org.springframework.test.web.reactive.server.HeaderAssertions"
                ),
                argument_expr=["MediaTypes.PROBLEM"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=95,
            ),
        ]
    )

    nodes_by_name = {node.call_site.method_name: node for node in grouping.nodes}

    header_root_http = nodes_by_name["expectHeader"].http_classification
    header_http = nodes_by_name["contentType"].http_classification

    assert header_root_http is not None
    assert header_root_http.response_role == HttpResponseRole.MATCHER
    assert nodes_by_name["expectHeader"].assertion_classification is None
    assert header_http is not None
    assert header_http.response_role == HttpResponseRole.HEADER_ASSERTION
    assert nodes_by_name["contentType"].assertion_classification is not None
    assert nodes_by_name["contentType"].assertion_classification.role == (
        AssertionRole.HEADER
    )


def test_receiverless_expect_body_is_not_recovered_without_webtestclient_event() -> (
    None
):
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="exchange",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=16,
            ),
            make_call_site(
                method_name="expectBody",
                argument_expr=["Problem.class"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=42,
            ),
        ]
    )

    body_node = next(
        node for node in grouping.nodes if node.call_site.method_name == "expectBody"
    )

    assert body_node.http_classification is None
    assert body_node.assertion_classification is None


def test_webtestclient_header_value_path_does_not_supply_event_path() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.test.web.reactive.server.WebTestClient"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="header",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersUriSpec"
                ),
                argument_expr=['"Location"', '"/users"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=52,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=63,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "GET"
    assert event.path == ""
    assert event.header_names == ["location"]
    assert [source.method_name for source in event.correlated_builder_sources] == [
        "get",
        "header",
    ]
    assert all(
        "path" not in source.contributed_properties
        for source in event.correlated_builder_sources
    )


def test_restassured_query_value_path_does_not_supply_event_path() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="queryParam",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"redirect"', '"/users"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.specification.RequestSpecification",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=46,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "GET"
    assert event.path == ""
    assert event.query_param_names == ["redirect"]
    assert all(
        "path" not in source.contributed_properties
        for source in event.correlated_builder_sources
    )


def test_webclient_body_value_path_does_not_supply_event_path() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="post",
                receiver_type=(
                    "org.springframework.web.reactive.function.client.WebClient"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="bodyValue",
                receiver_type=(
                    "org.springframework.web.reactive.function.client."
                    "WebClient$RequestBodySpec"
                ),
                argument_expr=['"/users"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=45,
            ),
            make_call_site(
                method_name="retrieve",
                receiver_type=(
                    "org.springframework.web.reactive.function.client."
                    "WebClient$RequestHeadersSpec"
                ),
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=56,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == ""
    assert event.has_body_payload
    assert all(
        "path" not in source.contributed_properties
        for source in event.correlated_builder_sources
    )


def test_okhttp_header_value_method_does_not_supply_event_method() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="newCall",
                receiver_type="okhttp3.OkHttpClient",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=80,
            ),
            make_call_site(
                method_name="header",
                receiver_type="okhttp3.Request$Builder",
                argument_expr=['"X-Method"', '"POST"'],
                start_line=1,
                start_column=24,
                end_line=1,
                end_column=55,
            ),
            make_call_site(
                method_name="build",
                receiver_type="okhttp3.Request$Builder",
                start_line=1,
                start_column=24,
                end_line=1,
                end_column=63,
            ),
            make_call_site(
                method_name="execute",
                receiver_type="okhttp3.Call",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=90,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "UNKNOWN"
    assert event.path == ""
    assert event.header_names == ["x-method"]
    assert all(
        "http_method" not in source.contributed_properties
        for source in event.correlated_builder_sources
    )


def test_java_httpclient_header_value_method_does_not_supply_event_method() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="header",
                receiver_type="java.net.http.HttpRequest$Builder",
                argument_expr=['"X-Method"', '"DELETE"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=42,
            ),
            make_call_site(
                method_name="send",
                receiver_type="java.net.http.HttpClient",
                start_line=3,
                start_column=5,
                end_line=3,
                end_column=40,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "UNKNOWN"
    assert event.path == ""
    assert event.header_names == ["x-method"]
    assert all(
        "http_method" not in source.contributed_properties
        for source in event.correlated_builder_sources
    )


def test_dynamic_method_arguments_still_supply_methods_when_positioned() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="url",
                receiver_type="okhttp3.Request$Builder",
                argument_expr=['"/api/data"'],
                start_line=1,
                start_column=24,
                end_line=1,
                end_column=45,
            ),
            make_call_site(
                method_name="method",
                receiver_type="okhttp3.Request$Builder",
                argument_expr=['"PATCH"', "body"],
                start_line=1,
                start_column=24,
                end_line=1,
                end_column=68,
            ),
            make_call_site(
                method_name="execute",
                receiver_type="okhttp3.Call",
                start_line=3,
                start_column=5,
                end_line=3,
                end_column=25,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "PATCH"
    assert event.path == "/api/data"


def test_mockmvc_multipart_explicit_method_uses_url_argument() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=80,
            ),
            make_call_site(
                method_name="multipart",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockMvcRequestBuilders"
                ),
                argument_expr=["HttpMethod.PUT", '"/doc"'],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=55,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "PUT"
    assert event.path == "/doc"


def test_karate_body_method_argument_does_not_supply_event_path() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="post",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"/not-a-path-body"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=35,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == ""


def test_cross_chain_java_httpclient_send_resolves_from_preceding_builder() -> None:
    """Builder chain on one line, send event on another — cross-chain merges."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="uri",
                receiver_type="java.net.http.HttpRequest$Builder",
                argument_expr=['"/api/lineage"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="GET",
                receiver_type="java.net.http.HttpRequest$Builder",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=36,
            ),
            make_call_site(
                method_name="send",
                receiver_type="java.net.http.HttpClient",
                start_line=3,
                start_column=5,
                end_line=3,
                end_column=40,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.http_method == "GET"
    assert events[0].http_classification.path == "/api/lineage"
    assert len(events[0].http_classification.correlated_builder_sources) >= 1


def test_cross_chain_scattered_builders_drain_into_event() -> None:
    """Builders scattered across separate statements all merge into the event."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="uri",
                receiver_type="java.net.http.HttpRequest$Builder",
                argument_expr=['"/api/v1/jobs"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="POST",
                receiver_type="java.net.http.HttpRequest$Builder",
                start_line=2,
                start_column=5,
                end_line=2,
                end_column=20,
            ),
            make_call_site(
                method_name="send",
                receiver_type="java.net.http.HttpClient",
                start_line=4,
                start_column=5,
                end_line=4,
                end_column=40,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.http_method == "POST"
    assert events[0].http_classification.path == "/api/v1/jobs"


def test_cross_chain_okhttp_execute_resolves_from_builder() -> None:
    """OkHttp builder chain on line 1, execute on line 3 — cross-chain merges."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="url",
                receiver_type="okhttp3.Request$Builder",
                argument_expr=['"/api/data"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="get",
                receiver_type="okhttp3.Request$Builder",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=36,
            ),
            make_call_site(
                method_name="execute",
                receiver_type="okhttp3.Call",
                start_line=3,
                start_column=5,
                end_line=3,
                end_column=25,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.http_method == "GET"
    assert events[0].http_classification.path == "/api/data"


def test_okhttp_receiverless_execute_resolves_from_new_call_builder() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="newCall",
                receiver_type="okhttp3.OkHttpClient",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=70,
            ),
            make_call_site(
                method_name="url",
                receiver_type="okhttp3.Request$Builder",
                argument_expr=['"/api/data"'],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=42,
            ),
            make_call_site(
                method_name="get",
                receiver_type="okhttp3.Request$Builder",
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=48,
            ),
            make_call_site(
                method_name="execute",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=80,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.framework == HttpDispatchFramework.OKHTTP
    assert event.owner_family == "okhttp.request"
    assert event.http_method == "GET"
    assert event.path == "/api/data"


def test_java_httpclient_receiverless_send_resolves_from_inline_request_builder() -> (
    None
):
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="send",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=100,
            ),
            make_call_site(
                method_name="newBuilder",
                receiver_expr="java.net.http.HttpRequest",
                receiver_type="",
                start_line=1,
                start_column=18,
                end_line=1,
                end_column=42,
            ),
            make_call_site(
                method_name="uri",
                argument_expr=['"/api/data"'],
                start_line=1,
                start_column=18,
                end_line=1,
                end_column=60,
            ),
            make_call_site(
                method_name="GET",
                start_line=1,
                start_column=18,
                end_line=1,
                end_column=66,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.framework == HttpDispatchFramework.JAVA_HTTPCLIENT
    assert event.owner_family == "java-httpclient.request"
    assert event.http_method == "GET"
    assert event.path == "/api/data"


def test_java_httpclient_receiverless_head_is_recovered() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="send",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=100,
            ),
            make_call_site(
                method_name="newBuilder",
                receiver_expr="java.net.http.HttpRequest",
                receiver_type="",
                start_line=1,
                start_column=18,
                end_line=1,
                end_column=42,
            ),
            make_call_site(
                method_name="uri",
                argument_expr=['"/api/data"'],
                start_line=1,
                start_column=18,
                end_line=1,
                end_column=60,
            ),
            make_call_site(
                method_name="HEAD",
                start_line=1,
                start_column=18,
                end_line=1,
                end_column=67,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.framework == HttpDispatchFramework.JAVA_HTTPCLIENT
    assert event.http_method == "HEAD"
    assert event.path == "/api/data"


def test_cross_chain_skips_when_builder_follows_event() -> None:
    """Builders after the event should not merge backward."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="send",
                receiver_type="java.net.http.HttpClient",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="uri",
                receiver_type="java.net.http.HttpRequest$Builder",
                argument_expr=['"/api/should-not-merge"'],
                start_line=3,
                start_column=5,
                end_line=3,
                end_column=40,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.path == ""


def test_cross_chain_multiple_pairs_fifo_order() -> None:
    """Two builder chains + two events pair up in FIFO source order."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="uri",
                receiver_type="java.net.http.HttpRequest$Builder",
                argument_expr=['"/first"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=25,
            ),
            make_call_site(
                method_name="GET",
                receiver_type="java.net.http.HttpRequest$Builder",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=31,
            ),
            make_call_site(
                method_name="send",
                receiver_type="java.net.http.HttpClient",
                start_line=2,
                start_column=5,
                end_line=2,
                end_column=30,
            ),
            make_call_site(
                method_name="uri",
                receiver_type="java.net.http.HttpRequest$Builder",
                argument_expr=['"/second"'],
                start_line=4,
                start_column=5,
                end_line=4,
                end_column=25,
            ),
            make_call_site(
                method_name="POST",
                receiver_type="java.net.http.HttpRequest$Builder",
                start_line=4,
                start_column=5,
                end_line=4,
                end_column=31,
            ),
            make_call_site(
                method_name="send",
                receiver_type="java.net.http.HttpClient",
                start_line=5,
                start_column=5,
                end_line=5,
                end_column=30,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 2
    assert events[0].http_classification.path == "/first"
    assert events[0].http_classification.http_method == "GET"
    assert events[1].http_classification.path == "/second"
    assert events[1].http_classification.http_method == "POST"


def test_cross_chain_already_satisfied_event_not_touched() -> None:
    """An event enriched via same-chain should not drain the queue."""
    grouping = _classify_and_get_grouping(
        [
            # Orphan builder on line 1
            make_call_site(
                method_name="uri",
                receiver_type="java.net.http.HttpRequest$Builder",
                argument_expr=['"/orphan"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=25,
            ),
            # Same-chain fluent: get("/known").perform() on line 3
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=3,
                start_column=1,
                end_line=3,
                end_column=34,
            ),
            make_call_site(
                method_name="get",
                receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
                argument_expr=['"/known"'],
                start_line=3,
                start_column=17,
                end_line=3,
                end_column=33,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.path == "/known"
    assert events[0].http_classification.http_method == "GET"


def test_restdocs_mockmvc_request_builder_correlates_method_to_perform() -> None:
    """Spring REST Docs builders behave like MockMvc request builders."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=52,
            ),
            make_call_site(
                method_name="get",
                receiver_type=(
                    "org.springframework.restdocs.mockmvc."
                    "RestDocumentationRequestBuilders"
                ),
                argument_expr=['"/people"'],
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=31,
            ),
            make_call_site(
                method_name="header",
                receiver_type=(
                    "org.springframework.test.web.servlet.request."
                    "MockHttpServletRequestBuilder"
                ),
                argument_expr=['"Authorization"', '"Basic token"'],
                start_line=1,
                start_column=31,
                end_line=1,
                end_column=51,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "GET"
    assert event.path == "/people"
    assert event.header_names == ["authorization"]


def test_cross_chain_split_mockmvc_resolves() -> None:
    """MockMvc used in split style: get() on one line, perform() on another."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
                argument_expr=['"/api/split"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="perform",
                receiver_type="org.springframework.test.web.servlet.MockMvc",
                start_line=3,
                start_column=5,
                end_line=3,
                end_column=30,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    assert events[0].http_classification.http_method == "GET"
    assert events[0].http_classification.path == "/api/split"


# ---------------------------------------------------------------------------
# Appended path segments compose across a builder chain in chain order
# ---------------------------------------------------------------------------


def _webtarget_chain_call_sites(
    *, target_argument: str, first_path: str, second_path: str, event_line: int = 1
):
    return [
        make_call_site(
            method_name="target",
            receiver_type="jakarta.ws.rs.client.Client",
            argument_expr=[target_argument],
            start_line=1,
            end_line=1,
            end_column=20,
        ),
        make_call_site(
            method_name="path",
            receiver_type="jakarta.ws.rs.client.WebTarget",
            argument_expr=[first_path],
            start_line=1,
            end_line=1,
            end_column=33,
        ),
        make_call_site(
            method_name="path",
            receiver_type="jakarta.ws.rs.client.WebTarget",
            argument_expr=[second_path],
            start_line=1,
            end_line=1,
            end_column=48,
        ),
        make_call_site(
            method_name="request",
            receiver_type="jakarta.ws.rs.client.WebTarget",
            start_line=1,
            end_line=1,
            end_column=58,
        ),
        make_call_site(
            method_name="get",
            receiver_type="jakarta.ws.rs.client.Invocation.Builder",
            receiver_expr="builder" if event_line != 1 else "",
            start_line=event_line,
            end_line=event_line,
            end_column=70,
        ),
    ]


def test_webtarget_appended_paths_compose_into_event() -> None:
    grouping = _classify_and_get_grouping(
        _webtarget_chain_call_sites(
            target_argument="base", first_path='"/api"', second_path='"/users"'
        )
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.path == "/api/users"
    assert event.path_truncated is False
    assert [
        source.method_name
        for source in event.correlated_builder_sources
        if "path" in source.contributed_properties
    ] == ["path", "path"]


def test_webtarget_builder_only_chain_composes_when_drained_into_event() -> None:
    # WebTarget t = client.target(base).path("/api").path("/users");
    # t.request().get();  -- the queued chain composes the same way.
    grouping = _classify_and_get_grouping(
        _webtarget_chain_call_sites(
            target_argument="base",
            first_path='"/api"',
            second_path='"/users"',
            event_line=3,
        )
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.path == "/api/users"
    assert event.path_truncated is False


def test_truncated_non_final_appender_falls_back_to_first_path() -> None:
    # Composing across "/a/" + x would fabricate adjacency across the
    # statically unknown appended value.
    grouping = _classify_and_get_grouping(
        _webtarget_chain_call_sites(
            target_argument="base", first_path='"/a/" + x', second_path='"/users"'
        )
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.path == "/a/"
    assert event.path_truncated is True


def test_truncated_final_appender_composes_truncated() -> None:
    grouping = _classify_and_get_grouping(
        _webtarget_chain_call_sites(
            target_argument="base", first_path='"/api"', second_path='"/users/" + id'
        )
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.path == "/api/users/"
    assert event.path_truncated is True


def test_webtarget_single_segment_paths_compose_into_event() -> None:
    # client.target(base).path("users").path("active").request().get() — the
    # canonical WebTarget idiom appends bare segments.
    grouping = _classify_and_get_grouping(
        _webtarget_chain_call_sites(
            target_argument="base", first_path='"users"', second_path='"active"'
        )
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.path == "/users/active"
    assert event.path_truncated is False


def test_karate_url_base_composes_with_appended_paths() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="url",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"/v1"'],
                start_line=1,
                end_line=1,
                end_column=20,
            ),
            make_call_site(
                method_name="path",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"/cats"'],
                start_line=1,
                end_line=1,
                end_column=33,
            ),
            make_call_site(
                method_name="path",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"/billie"'],
                start_line=1,
                end_line=1,
                end_column=48,
            ),
            make_call_site(
                method_name="get",
                receiver_type="com.intuit.karate.Http",
                start_line=1,
                end_line=1,
                end_column=58,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.path == "/v1/cats/billie"
    assert event.path_truncated is False


def test_karate_to_factory_with_variable_base_takes_single_segment_path() -> None:
    # Http.to(url).path("cats").get() — the unresolved base contributes nothing
    # and the bare path segment becomes the event path.
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="to",
                receiver_type="com.intuit.karate.Http",
                is_static_call=True,
                argument_expr=["url"],
                start_line=1,
                end_line=1,
                end_column=20,
            ),
            make_call_site(
                method_name="path",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"cats"'],
                start_line=1,
                end_line=1,
                end_column=33,
            ),
            make_call_site(
                method_name="get",
                receiver_type="com.intuit.karate.Http",
                start_line=1,
                end_line=1,
                end_column=42,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.http_method == "GET"
    assert event.path == "/cats"
    assert event.path_truncated is False


def test_karate_to_base_literal_composes_with_appended_path() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="to",
                receiver_type="com.intuit.karate.Http",
                is_static_call=True,
                argument_expr=['"http://localhost:8080/v1"'],
                start_line=1,
                end_line=1,
                end_column=38,
            ),
            make_call_site(
                method_name="path",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"cats"'],
                start_line=1,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="get",
                receiver_type="com.intuit.karate.Http",
                start_line=1,
                end_line=1,
                end_column=58,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.path == "http://localhost:8080/v1/cats"
    assert event.path_truncated is False


def test_karate_post_body_argument_marks_body_payload() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="to",
                receiver_type="com.intuit.karate.Http",
                is_static_call=True,
                argument_expr=["url"],
                start_line=1,
                end_line=1,
                end_column=20,
            ),
            make_call_site(
                method_name="post",
                receiver_type="com.intuit.karate.Http",
                argument_expr=["requestBody"],
                start_line=1,
                end_line=1,
                end_column=38,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.http_method == "POST"
    assert event.has_body_payload is True


def test_karate_delete_argument_marks_body_payload() -> None:
    # karate 2.x Http.delete(Object) is its only argument-taking overload.
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="delete",
                receiver_type="com.intuit.karate.Http",
                argument_expr=["payload"],
                start_line=1,
                end_line=1,
                end_column=25,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.http_method == "DELETE"
    assert event.has_body_payload is True


def test_karate_bare_delete_has_no_body_payload() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="delete",
                receiver_type="com.intuit.karate.Http",
                start_line=1,
                end_line=1,
                end_column=25,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.http_method == "DELETE"
    assert event.has_body_payload is False


def test_karate_2x_receiver_classifies_chain() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="to",
                receiver_type="io.karatelabs.http.Http",
                is_static_call=True,
                argument_expr=['"http://localhost:8080/v1"'],
                start_line=1,
                end_line=1,
                end_column=38,
            ),
            make_call_site(
                method_name="path",
                receiver_type="io.karatelabs.http.Http",
                argument_expr=['"cats"'],
                start_line=1,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="get",
                receiver_type="io.karatelabs.http.Http",
                start_line=1,
                end_line=1,
                end_column=58,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.framework == HttpDispatchFramework.KARATE
    assert event.http_method == "GET"
    assert event.path == "http://localhost:8080/v1/cats"


def test_karate_patch_body_argument_marks_body_payload() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="patch",
                receiver_type="io.karatelabs.http.Http",
                argument_expr=["requestBody"],
                start_line=1,
                end_line=1,
                end_column=30,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.http_method == "PATCH"
    assert event.has_body_payload is True


def test_karate_patch_json_marks_body_payload() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="patchJson",
                receiver_type="io.karatelabs.http.Http",
                argument_expr=['"{}"'],
                start_line=1,
                end_line=1,
                end_column=30,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.http_method == "PATCH"
    assert event.has_body_payload is True


def test_karate_single_argument_method_has_no_body_payload() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="method",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"POST"'],
                start_line=1,
                end_line=1,
                end_column=30,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.http_method == "POST"
    assert event.has_body_payload is False


def test_karate_two_argument_method_marks_body_payload() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="method",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"POST"', "requestBody"],
                start_line=1,
                end_line=1,
                end_column=42,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.http_method == "POST"
    assert event.has_body_payload is True


def test_karate_varargs_path_composes_literal_segments() -> None:
    # Http.to(url).path("cats", "42").get() appends every vararg segment.
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="to",
                receiver_type="com.intuit.karate.Http",
                is_static_call=True,
                argument_expr=["url"],
                start_line=1,
                end_line=1,
                end_column=20,
            ),
            make_call_site(
                method_name="path",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"cats"', '"42"'],
                start_line=1,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="get",
                receiver_type="com.intuit.karate.Http",
                start_line=1,
                end_line=1,
                end_column=48,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.http_method == "GET"
    assert event.path == "/cats/42"
    assert event.path_truncated is False


def test_karate_varargs_path_truncates_at_dynamic_segment() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="path",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"cats"', "id"],
                start_line=1,
                end_line=1,
                end_column=36,
            ),
            make_call_site(
                method_name="get",
                receiver_type="com.intuit.karate.Http",
                start_line=1,
                end_line=1,
                end_column=44,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.path == "/cats"
    assert event.path_truncated is True


def test_karate_varargs_path_does_not_fabricate_adjacency_across_dynamic_segment() -> (
    None
):
    # path("cats", id, "tails") must not compose /cats/tails.
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="path",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"cats"', "id", '"tails"'],
                start_line=1,
                end_line=1,
                end_column=48,
            ),
            make_call_site(
                method_name="get",
                receiver_type="com.intuit.karate.Http",
                start_line=1,
                end_line=1,
                end_column=56,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.path == "/cats"
    assert event.path_truncated is True


def test_karate_varargs_path_with_leading_dynamic_segment_yields_no_path() -> None:
    # A later literal segment must not become the path on its own.
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="path",
                receiver_type="com.intuit.karate.Http",
                argument_expr=["id", '"cats"'],
                start_line=1,
                end_line=1,
                end_column=36,
            ),
            make_call_site(
                method_name="get",
                receiver_type="com.intuit.karate.Http",
                start_line=1,
                end_line=1,
                end_column=44,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.path == ""
    path_builder = next(n for n in grouping.nodes if n.call_site.method_name == "path")
    assert path_builder.http_classification is not None
    assert path_builder.http_classification.path == ""
    assert path_builder.http_classification.path_truncated is True


def test_rest_assured_extract_path_does_not_supply_request_path() -> None:
    # .extract().path("id") reads a response body value; its owner family
    # registers no path-argument positions, so no request path is extracted.
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="get",
                receiver_type="io.restassured.RestAssured",
                is_static_call=True,
                start_line=1,
                end_line=1,
                end_column=15,
            ),
            make_call_site(
                method_name="extract",
                receiver_type="io.restassured.response.ValidatableResponse",
                start_line=1,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="path",
                receiver_type="io.restassured.response.ExtractableResponse",
                argument_expr=['"id"'],
                start_line=1,
                end_line=1,
                end_column=45,
            ),
        ]
    )

    extractor_nodes = [
        node
        for node in grouping.nodes
        if (node.call_site.method_name or "") == "path"
        and node.http_classification is not None
    ]
    assert len(extractor_nodes) == 1
    extractor = extractor_nodes[0].http_classification
    assert extractor.response_role == HttpResponseRole.EXTRACTOR
    assert extractor.request_role is None
    assert extractor.path == ""

    event = _event_nodes(grouping)[0].http_classification
    assert event.path == ""


def test_rest_assured_base_uri_bare_host_stays_excluded() -> None:
    # baseUri("localhost") is a host token, not a path; basePath("api") is a
    # path by definition and keeps its bare segment.
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="baseUri",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"localhost"'],
                start_line=1,
                end_line=1,
                end_column=25,
            ),
            make_call_site(
                method_name="basePath",
                receiver_type="io.restassured.specification.RequestSpecification",
                argument_expr=['"api"'],
                start_line=1,
                end_line=1,
                end_column=40,
            ),
        ]
    )

    classifications = {
        node.call_site.method_name: node.http_classification
        for node in grouping.nodes
        if node.http_classification is not None
    }
    assert classifications["baseUri"].path == ""
    assert classifications["basePath"].path == "/api"


def test_replace_semantics_setters_do_not_compose() -> None:
    # karate url() replaces the base; chaining two must not fabricate "/v1/v2".
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="url",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"/v1"'],
                start_line=1,
                end_line=1,
                end_column=20,
            ),
            make_call_site(
                method_name="url",
                receiver_type="com.intuit.karate.Http",
                argument_expr=['"/v2"'],
                start_line=1,
                end_line=1,
                end_column=33,
            ),
            make_call_site(
                method_name="get",
                receiver_type="com.intuit.karate.Http",
                start_line=1,
                end_line=1,
                end_column=44,
            ),
        ]
    )

    event = _event_nodes(grouping)[0].http_classification
    assert event.path == "/v1"
    assert event.path_truncated is False


def test_micronaut_to_blocking_exchange_resolves_from_inline_request_factory() -> None:
    """client.toBlocking().exchange(HttpRequest.GET("/users/1")) yields GET /users/1."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="toBlocking",
                receiver_type="io.micronaut.http.client.HttpClient",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=28,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type="io.micronaut.http.client.BlockingHttpClient",
                argument_expr=['HttpRequest.GET("/users/1")'],
                argument_types=["io.micronaut.http.MutableHttpRequest"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=70,
            ),
            make_call_site(
                method_name="GET",
                receiver_type="io.micronaut.http.HttpRequest",
                argument_expr=['"/users/1"'],
                start_line=1,
                start_column=38,
                end_line=1,
                end_column=64,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.framework == HttpDispatchFramework.MICRONAUT_CLIENT
    assert event.owner_family == "micronaut-client.request"
    assert event.http_method == "GET"
    assert event.path == "/users/1"
    assert len(event.correlated_builder_sources) >= 1


def test_micronaut_blocking_retrieve_string_uri_defaults_to_get() -> None:
    """retrieve(String) delegates to HttpRequest.GET(uri), so the verb is GET."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="retrieve",
                receiver_type="io.micronaut.http.client.BlockingHttpClient",
                argument_expr=['"/health"'],
                argument_types=["java.lang.String"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.framework == HttpDispatchFramework.MICRONAUT_CLIENT
    assert event.http_method == "GET"
    assert event.path == "/health"


def test_micronaut_retrieve_without_argument_types_keeps_unknown_verb() -> None:
    """Without resolved argument types the String-overload GET default stays off."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="retrieve",
                receiver_type="io.micronaut.http.client.BlockingHttpClient",
                argument_expr=['"/health"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "UNKNOWN"
    assert event.path == "/health"


def test_micronaut_exchange_request_variable_extracts_nothing() -> None:
    """A non-literal HttpRequest argument supplies neither verb nor path."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="exchange",
                receiver_type="io.micronaut.http.client.BlockingHttpClient",
                argument_expr=["request"],
                argument_types=["io.micronaut.http.MutableHttpRequest"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "UNKNOWN"
    assert event.path == ""


def test_cross_chain_micronaut_post_factory_drains_into_exchange() -> None:
    """HttpRequest.POST built on one line, exchange on another — cross-chain merges."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="POST",
                receiver_type="io.micronaut.http.HttpRequest",
                argument_expr=['"/users"', "userJson"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=45,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type="io.micronaut.http.client.HttpClient",
                start_line=3,
                start_column=5,
                end_line=3,
                end_column=40,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == "/users"
    assert event.has_body_payload is True


def test_micronaut_delete_factory_without_body_argument_has_no_body() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="DELETE",
                receiver_type="io.micronaut.http.HttpRequest",
                argument_expr=['"/users/1"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type="io.micronaut.http.client.HttpClient",
                start_line=3,
                start_column=5,
                end_line=3,
                end_column=40,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "DELETE"
    assert event.path == "/users/1"
    assert event.has_body_payload is False


def test_micronaut_request_create_carries_verb_from_http_method_argument() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="create",
                receiver_type="io.micronaut.http.HttpRequest",
                argument_expr=["HttpMethod.POST", '"/items"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type="io.micronaut.http.client.HttpClient",
                start_line=3,
                start_column=5,
                end_line=3,
                end_column=40,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "POST"
    assert event.path == "/items"


def test_micronaut_receiverless_exchange_resolves_from_inline_request_factory() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="exchange",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=100,
            ),
            make_call_site(
                method_name="GET",
                receiver_type="io.micronaut.http.HttpRequest",
                argument_expr=['"/api/data"'],
                start_line=1,
                start_column=18,
                end_line=1,
                end_column=60,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.framework == HttpDispatchFramework.MICRONAUT_CLIENT
    assert event.owner_family == "micronaut-client.request"
    assert event.http_method == "GET"
    assert event.path == "/api/data"


# ---------------------------------------------------------------------------
# Spring RequestEntity builder correlation
# ---------------------------------------------------------------------------

_REQUEST_ENTITY = "org.springframework.http.RequestEntity"
_BODY_BUILDER = "org.springframework.http.RequestEntity$BodyBuilder"
_HEADERS_BUILDER = "org.springframework.http.RequestEntity$HeadersBuilder"
_REST_TEMPLATE = "org.springframework.web.client.RestTemplate"
_TEST_REST_TEMPLATE = "org.springframework.boot.test.web.client.TestRestTemplate"


def test_request_entity_inline_chain_merges_into_rest_template_exchange() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="exchange",
                receiver_type=_REST_TEMPLATE,
                argument_expr=[
                    'RequestEntity.post("/api/users").contentType(MediaType.APPLICATION_JSON).body(dto)',
                    "String.class",
                ],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=200,
            ),
            make_call_site(
                method_name="post",
                receiver_type=_REQUEST_ENTITY,
                argument_expr=['"/api/users"'],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="contentType",
                receiver_type=_BODY_BUILDER,
                argument_expr=["MediaType.APPLICATION_JSON"],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=95,
            ),
            make_call_site(
                method_name="body",
                receiver_type=_BODY_BUILDER,
                argument_expr=["dto"],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=110,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.framework == HttpDispatchFramework.REST_TEMPLATE
    assert event.http_method == "POST"
    assert event.path == "/api/users"
    assert event.has_body_payload is True
    assert event.header_names == ["content-type"]


def test_request_entity_inline_chain_merges_into_test_rest_template_exchange() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="exchange",
                receiver_type=_TEST_REST_TEMPLATE,
                argument_expr=[
                    'RequestEntity.post(URI.create("/api/users")).contentType(MediaType.APPLICATION_JSON).body(dto)',
                    "String.class",
                ],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=220,
            ),
            make_call_site(
                method_name="post",
                receiver_type=_REQUEST_ENTITY,
                argument_expr=['URI.create("/api/users")'],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=60,
            ),
            make_call_site(
                method_name="contentType",
                receiver_type=_BODY_BUILDER,
                argument_expr=["MediaType.APPLICATION_JSON"],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=105,
            ),
            make_call_site(
                method_name="body",
                receiver_type=_BODY_BUILDER,
                argument_expr=["dto"],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=120,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.framework == HttpDispatchFramework.TEST_REST_TEMPLATE
    assert event.http_method == "POST"
    assert event.path == "/api/users"
    assert event.has_body_payload is True
    assert event.header_names == ["content-type"]


def test_request_entity_split_variable_adopts_cross_chain_evidence() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="post",
                receiver_type=_REQUEST_ENTITY,
                argument_expr=['"/api/users"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="contentType",
                receiver_type=_BODY_BUILDER,
                argument_expr=["MediaType.APPLICATION_JSON"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=85,
            ),
            make_call_site(
                method_name="body",
                receiver_type=_BODY_BUILDER,
                argument_expr=["dto"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=100,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type=_TEST_REST_TEMPLATE,
                argument_expr=["req", "String.class"],
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=60,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.framework == HttpDispatchFramework.TEST_REST_TEMPLATE
    assert event.http_method == "POST"
    assert event.path == "/api/users"
    assert event.has_body_payload is True
    assert event.header_names == ["content-type"]


def test_request_entity_get_with_build_yields_verb_and_path() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="exchange",
                receiver_type=_TEST_REST_TEMPLATE,
                argument_expr=[
                    'RequestEntity.get("/x").build()',
                    "String.class",
                ],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=120,
            ),
            make_call_site(
                method_name="get",
                receiver_type=_REQUEST_ENTITY,
                argument_expr=['"/x"'],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="build",
                receiver_type=_HEADERS_BUILDER,
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=50,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "GET"
    assert event.path == "/x"
    assert event.has_body_payload is False


def test_request_entity_method_factory_extracts_verb_and_path() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="exchange",
                receiver_type=_REST_TEMPLATE,
                argument_expr=[
                    'RequestEntity.method(HttpMethod.PATCH, "/api/users").body(dto)',
                    "String.class",
                ],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=200,
            ),
            make_call_site(
                method_name="method",
                receiver_type=_REQUEST_ENTITY,
                argument_expr=["HttpMethod.PATCH", '"/api/users"'],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=70,
            ),
            make_call_site(
                method_name="body",
                receiver_type=_BODY_BUILDER,
                argument_expr=["dto"],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=90,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "PATCH"
    assert event.path == "/api/users"
    assert event.has_body_payload is True


def test_non_request_entity_first_arg_keeps_unknown_verb() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="exchange",
                receiver_type=_TEST_REST_TEMPLATE,
                argument_expr=["req", "String.class"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=50,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "UNKNOWN"
    assert event.path == ""
    assert event.has_body_payload is False


def test_request_entity_put_with_uri_variable_and_build_has_no_body() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="exchange",
                receiver_type=_REST_TEMPLATE,
                argument_expr=[
                    'RequestEntity.put("/api/{id}", id).build()',
                    "String.class",
                ],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=120,
            ),
            make_call_site(
                method_name="put",
                receiver_type=_REQUEST_ENTITY,
                argument_expr=['"/api/{id}"', "id"],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="build",
                receiver_type=_BODY_BUILDER,
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=60,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "PUT"
    assert event.path == "/api/{id}"
    assert event.path_param_names == ["id"]
    assert event.has_body_payload is False


def test_request_entity_put_with_uri_variable_split_variable_has_no_body() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="put",
                receiver_type=_REQUEST_ENTITY,
                argument_expr=['"/api/{id}"', "id"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="build",
                receiver_type=_BODY_BUILDER,
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="exchange",
                receiver_type=_TEST_REST_TEMPLATE,
                argument_expr=["req", "String.class"],
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=60,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "PUT"
    assert event.path == "/api/{id}"
    assert event.has_body_payload is False


def test_request_entity_put_with_explicit_body_has_body() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="exchange",
                receiver_type=_TEST_REST_TEMPLATE,
                argument_expr=[
                    'RequestEntity.put("/api/{id}", id).body(dto)',
                    "String.class",
                ],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=120,
            ),
            make_call_site(
                method_name="put",
                receiver_type=_REQUEST_ENTITY,
                argument_expr=['"/api/{id}"', "id"],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="body",
                receiver_type=_BODY_BUILDER,
                argument_expr=["dto"],
                start_line=1,
                start_column=20,
                end_line=1,
                end_column=65,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "PUT"
    assert event.path == "/api/{id}"
    assert event.has_body_payload is True


def test_plain_rest_template_put_with_second_arg_still_has_body() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="put",
                receiver_type=_REST_TEMPLATE,
                argument_expr=['"/api/x"', "dto"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=40,
            ),
        ]
    )

    events = _event_nodes(grouping)
    assert len(events) == 1
    event = events[0].http_classification
    assert event.http_method == "PUT"
    assert event.path == "/api/x"
    assert event.has_body_payload is True
