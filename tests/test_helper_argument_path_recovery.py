"""Request events adopt or compose the path argument of a chain helper whose
return type is a registered request-construction receiver."""

from __future__ import annotations

from gerbil.analysis.http.classification import classify_http_on_runtime_view
from gerbil.analysis.http.framework_registry import is_request_builder_receiver_type
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import (
    HelperExpansion,
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.schema import HttpRequestRole, LifecyclePhase
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
    method_signature="getsWidgetCount()",
)
_WEBTARGET_HELPER = MethodRef(
    defining_class_name="example.WidgetApiTest",
    method_signature="newRequest(java.lang.String)",
)
_SPEC_HELPER = MethodRef(
    defining_class_name="example.WidgetApiTest",
    method_signature="given(java.lang.String)",
)


def _classify(call_sites, *, helper_call_site, helper_ref, helper_return_type):
    grouping = build_call_site_grouping(call_sites)
    helper_node = grouping.node_for_call_site(helper_call_site)
    helper_node.resolved_helper = helper_ref
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=_TEST_OWNER,
                context_class_name=_TEST_OWNER.defining_class_name,
                grouping=grouping,
                method_details=make_callable(signature=_TEST_OWNER.method_signature),
            )
        ]
    )
    analysis = FakeJavaAnalysis(
        classes={
            _TEST_OWNER.defining_class_name: make_type(),
            "example.DatasourceConstants": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["BASE", "QUERY"],
                        modifiers=["static", "final"],
                        variable_initializers={
                            "BASE": '"/v1/metadata/datasource"',
                            "QUERY": '"?type=UserDatasource"',
                        },
                    )
                ]
            ),
        },
        methods_by_class={
            helper_ref.defining_class_name: {
                helper_ref.method_signature: make_callable(
                    signature=helper_ref.method_signature,
                    return_type=helper_return_type,
                )
            }
        },
    )
    classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(
            runtime_view, analysis=analysis
        ),
    )
    return grouping


def _event_classification(grouping):
    events = [
        node.http_classification
        for node in grouping.nodes
        if node.http_classification is not None
        and node.http_classification.request_role == HttpRequestRole.EVENT
    ]
    assert len(events) == 1
    return events[0]


def _webtarget_chain(helper_argument: str):
    helper_call = make_call_site(
        method_name="newRequest",
        argument_expr=[helper_argument],
        start_line=1,
        end_line=1,
        end_column=40,
    )
    request_call = make_call_site(
        method_name="request",
        receiver_type="jakarta.ws.rs.client.WebTarget",
        start_line=1,
        end_line=1,
        end_column=50,
    )
    get_call = make_call_site(
        method_name="get",
        receiver_type="jakarta.ws.rs.client.Invocation.Builder",
        start_line=1,
        end_line=1,
        end_column=56,
    )
    return helper_call, request_call, get_call


def test_webtarget_helper_argument_becomes_event_path() -> None:
    helper_call, request_call, get_call = _webtarget_chain(
        '"/v2/widget/count?isActive=true"'
    )
    grouping = _classify(
        [get_call, helper_call, request_call],
        helper_call_site=helper_call,
        helper_ref=_WEBTARGET_HELPER,
        helper_return_type="jakarta.ws.rs.client.WebTarget",
    )

    event = _event_classification(grouping)
    assert event.http_method == "GET"
    assert event.path == "/v2/widget/count?isActive=true"
    assert event.path_truncated is False
    assert [source.method_name for source in event.correlated_builder_sources] == [
        "newRequest"
    ]

    event_node = next(node for node in grouping.nodes if node.call_site is get_call)
    assert event_node.endpoint_candidate is not None
    assert event_node.endpoint_candidate.path == "/v2/widget/count?isActive=true"
    assert event_node.endpoint_candidate.http_method == "GET"


def test_truncated_helper_argument_keeps_truncation_and_skips_query() -> None:
    helper_call, request_call, get_call = _webtarget_chain('"/v1/ca/" + caName')
    get_call.argument_expr = ['"?verbose=true"']
    grouping = _classify(
        [get_call, helper_call, request_call],
        helper_call_site=helper_call,
        helper_ref=_WEBTARGET_HELPER,
        helper_return_type="jakarta.ws.rs.client.WebTarget",
    )

    event = _event_classification(grouping)
    assert event.path == "/v1/ca/"
    assert event.path_truncated is True


def test_spec_helper_constant_argument_with_query_only_event_literal() -> None:
    helper_call = make_call_site(
        method_name="given",
        argument_expr=["DatasourceConstants.BASE"],
        start_line=1,
        end_line=1,
        end_column=30,
    )
    when_call = make_call_site(
        method_name="when",
        receiver_type="io.restassured.specification.RequestSpecification",
        start_line=1,
        end_line=1,
        end_column=38,
    )
    get_call = make_call_site(
        method_name="get",
        receiver_type="io.restassured.specification.RequestSpecification",
        argument_expr=['"?type=UserDatasource"'],
        start_line=1,
        end_line=1,
        end_column=64,
    )
    grouping = _classify(
        [get_call, helper_call, when_call],
        helper_call_site=helper_call,
        helper_ref=_SPEC_HELPER,
        helper_return_type="io.restassured.specification.RequestSpecification",
    )

    event = _event_classification(grouping)
    assert event.http_method == "GET"
    assert event.path == "/v1/metadata/datasource?type=UserDatasource"


def test_helper_returning_non_builder_type_recovers_nothing() -> None:
    helper_call, request_call, get_call = _webtarget_chain('"/v2/widget/count"')
    grouping = _classify(
        [get_call, helper_call, request_call],
        helper_call_site=helper_call,
        helper_ref=_WEBTARGET_HELPER,
        helper_return_type="java.lang.String",
    )

    event = _event_classification(grouping)
    assert event.path == ""


# Helper names are user-chosen, so they carry no argument-position semantics;
# a name colliding with the framework whitelist must not relax extraction.

_MOCKMVC_BUILDER_HELPER = MethodRef(
    defining_class_name="example.WidgetApiTest",
    method_signature="request(java.lang.String)",
)


def _perform_with_argument_helper(helper_argument: str) -> tuple:
    helper_call = make_call_site(
        method_name="request",
        argument_expr=[helper_argument],
        start_line=1,
        start_column=20,
        end_line=1,
        end_column=40,
    )
    perform_call = make_call_site(
        method_name="perform",
        receiver_type="org.springframework.test.web.servlet.MockMvc",
        argument_expr=[f"request({helper_argument})"],
        start_line=1,
        end_line=1,
        end_column=58,
    )
    return helper_call, perform_call


def test_media_type_argument_to_path_named_helper_is_not_adopted() -> None:
    helper_call, perform_call = _perform_with_argument_helper('"application/json"')
    grouping = _classify(
        [perform_call, helper_call],
        helper_call_site=helper_call,
        helper_ref=_MOCKMVC_BUILDER_HELPER,
        helper_return_type=(
            "org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder"
        ),
    )

    event = _event_classification(grouping)
    assert event.path == ""
    assert not event.correlated_builder_sources


def test_path_named_helper_with_slash_led_argument_still_recovers() -> None:
    helper_call, perform_call = _perform_with_argument_helper('"/v2/widget/count"')
    grouping = _classify(
        [perform_call, helper_call],
        helper_call_site=helper_call,
        helper_ref=_MOCKMVC_BUILDER_HELPER,
        helper_return_type=(
            "org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder"
        ),
    )

    event = _event_classification(grouping)
    assert event.path == "/v2/widget/count"
    assert [source.method_name for source in event.correlated_builder_sources] == [
        "request"
    ]


def test_path_shaped_relative_helper_argument_still_recovers() -> None:
    helper_call, request_call, get_call = _webtarget_chain('"v2/widget/count"')
    grouping = _classify(
        [get_call, helper_call, request_call],
        helper_call_site=helper_call,
        helper_ref=_WEBTARGET_HELPER,
        helper_return_type="jakarta.ws.rs.client.WebTarget",
    )

    event = _event_classification(grouping)
    assert event.path == "/v2/widget/count"


# A chain helper's base composes in front of an event's own relative path,
# mirroring how these chains append at runtime (spec base + verb path).


def _spec_helper_with_event_path(
    helper_argument: str, event_path_argument: str
) -> tuple:
    helper_call = make_call_site(
        method_name="given",
        argument_expr=[helper_argument],
        start_line=1,
        end_line=1,
        end_column=30,
    )
    get_call = make_call_site(
        method_name="get",
        receiver_type="io.restassured.specification.RequestSpecification",
        argument_expr=[event_path_argument],
        start_line=1,
        end_line=1,
        end_column=58,
    )
    return helper_call, get_call


def test_chain_helper_base_composes_with_event_path() -> None:
    helper_call, get_call = _spec_helper_with_event_path(
        '"/from-helper"', '"/explicit/path"'
    )
    grouping = _classify(
        [get_call, helper_call],
        helper_call_site=helper_call,
        helper_ref=_SPEC_HELPER,
        helper_return_type="io.restassured.specification.RequestSpecification",
    )

    event = _event_classification(grouping)
    assert event.path == "/from-helper/explicit/path"
    assert event.path_truncated is False
    assert [source.method_name for source in event.correlated_builder_sources] == [
        "given"
    ]


def test_truncated_helper_base_does_not_compose() -> None:
    # Composing across a truncated base would fabricate adjacency across the
    # statically unknown appended value.
    helper_call, get_call = _spec_helper_with_event_path(
        '"/v1/ca/" + caName', '"/explicit/path"'
    )
    grouping = _classify(
        [get_call, helper_call],
        helper_call_site=helper_call,
        helper_ref=_SPEC_HELPER,
        helper_return_type="io.restassured.specification.RequestSpecification",
    )

    event = _event_classification(grouping)
    assert event.path == "/explicit/path"
    assert not event.correlated_builder_sources


def test_absolute_event_path_is_untouched() -> None:
    helper_call, get_call = _spec_helper_with_event_path(
        '"/from-helper"', '"http://api.example.com/explicit"'
    )
    grouping = _classify(
        [get_call, helper_call],
        helper_call_site=helper_call,
        helper_ref=_SPEC_HELPER,
        helper_return_type="io.restassured.specification.RequestSpecification",
    )

    event = _event_classification(grouping)
    assert event.path == "http://api.example.com/explicit"
    assert not event.correlated_builder_sources


def test_argument_helper_does_not_compose_with_event_path() -> None:
    # Only a chain receiver's base precedes the event's own path; a helper in
    # the event's arguments has no append relationship with it.
    helper_call = make_call_site(
        method_name="given",
        argument_expr=['"/from-helper"'],
        start_line=1,
        start_column=20,
        end_line=1,
        end_column=40,
    )
    get_call = make_call_site(
        method_name="get",
        receiver_type="io.restassured.specification.RequestSpecification",
        argument_expr=['"/explicit/path"', "given()"],
        start_line=1,
        end_line=1,
        end_column=58,
    )
    grouping = _classify(
        [get_call, helper_call],
        helper_call_site=helper_call,
        helper_ref=_SPEC_HELPER,
        helper_return_type="io.restassured.specification.RequestSpecification",
    )

    event = _event_classification(grouping)
    assert event.path == "/explicit/path"
    assert not event.correlated_builder_sources


def test_unresolvable_helper_argument_recovers_nothing() -> None:
    helper_call, request_call, get_call = _webtarget_chain("uriPath")
    grouping = _classify(
        [get_call, helper_call, request_call],
        helper_call_site=helper_call,
        helper_ref=_WEBTARGET_HELPER,
        helper_return_type="jakarta.ws.rs.client.WebTarget",
    )

    event = _event_classification(grouping)
    assert event.path == ""


# A path-taking builder between the helper and the event whose argument failed
# extraction extends the real request path, so the adopted base is a prefix.


def test_intervening_path_builder_with_unresolved_argument_marks_truncation() -> None:
    helper_call = make_call_site(
        method_name="newRequest",
        argument_expr=['"/v1/ca"'],
        start_line=1,
        end_line=1,
        end_column=30,
    )
    path_call = make_call_site(
        method_name="path",
        receiver_type="jakarta.ws.rs.client.WebTarget",
        argument_expr=["caName"],
        start_line=1,
        end_line=1,
        end_column=44,
    )
    request_call = make_call_site(
        method_name="request",
        receiver_type="jakarta.ws.rs.client.WebTarget",
        start_line=1,
        end_line=1,
        end_column=54,
    )
    get_call = make_call_site(
        method_name="get",
        receiver_type="jakarta.ws.rs.client.Invocation.Builder",
        argument_expr=['"?verbose=true"'],
        start_line=1,
        end_line=1,
        end_column=60,
    )
    grouping = _classify(
        [get_call, helper_call, path_call, request_call],
        helper_call_site=helper_call,
        helper_ref=_WEBTARGET_HELPER,
        helper_return_type="jakarta.ws.rs.client.WebTarget",
    )

    event = _event_classification(grouping)
    assert event.path == "/v1/ca"
    assert event.path_truncated is True
    # The query ride-along must not pretend the incomplete path is complete.
    assert "?" not in event.path


# Query evidence rides along only when it is the entire event argument.


def test_query_literal_inside_unresolved_concat_does_not_ride_along() -> None:
    helper_call = make_call_site(
        method_name="given",
        argument_expr=["DatasourceConstants.BASE"],
        start_line=1,
        end_line=1,
        end_column=30,
    )
    get_call = make_call_site(
        method_name="get",
        receiver_type="io.restassured.specification.RequestSpecification",
        argument_expr=['datasourceId + "?full=true"'],
        start_line=1,
        end_line=1,
        end_column=64,
    )
    grouping = _classify(
        [get_call, helper_call],
        helper_call_site=helper_call,
        helper_ref=_SPEC_HELPER,
        helper_return_type="io.restassured.specification.RequestSpecification",
    )

    event = _event_classification(grouping)
    assert event.path == "/v1/metadata/datasource"
    assert "full" not in event.query_param_names


def test_query_constant_event_argument_rides_along() -> None:
    helper_call = make_call_site(
        method_name="given",
        argument_expr=["DatasourceConstants.BASE"],
        start_line=1,
        end_line=1,
        end_column=30,
    )
    get_call = make_call_site(
        method_name="get",
        receiver_type="io.restassured.specification.RequestSpecification",
        argument_expr=["DatasourceConstants.QUERY"],
        start_line=1,
        end_line=1,
        end_column=64,
    )
    grouping = _classify(
        [get_call, helper_call],
        helper_call_site=helper_call,
        helper_ref=_SPEC_HELPER,
        helper_return_type="io.restassured.specification.RequestSpecification",
    )

    event = _event_classification(grouping)
    assert event.path == "/v1/metadata/datasource?type=UserDatasource"


# A recovered path's embedded query/template names mirror onto the
# classification, keeping the serialized call site internally consistent.


def test_recovered_query_bearing_path_refreshes_query_param_names() -> None:
    helper_call, request_call, get_call = _webtarget_chain(
        '"/v2/widget/count?isActive=true"'
    )
    grouping = _classify(
        [get_call, helper_call, request_call],
        helper_call_site=helper_call,
        helper_ref=_WEBTARGET_HELPER,
        helper_return_type="jakarta.ws.rs.client.WebTarget",
    )

    event = _event_classification(grouping)
    assert event.query_param_names == ["isActive"]


def test_recovered_template_path_refreshes_path_param_names() -> None:
    helper_call, request_call, get_call = _webtarget_chain('"/v2/widget/{name}"')
    grouping = _classify(
        [get_call, helper_call, request_call],
        helper_call_site=helper_call,
        helper_ref=_WEBTARGET_HELPER,
        helper_return_type="jakarta.ws.rs.client.WebTarget",
    )

    event = _event_classification(grouping)
    assert event.path == "/v2/widget/{name}"
    assert event.path_param_names == ["name"]


# When the helper's expansion resolves a fuller builder path (helper-internal
# constants + bound parameters), the event upgrades its adopted base to it.


def _expanded_webtarget_setup(
    expansion_target_argument: str | None = None,
    *,
    expansion_call_sites: list | None = None,
):
    helper_call, request_call, get_call = _webtarget_chain('"/v3/search"')
    grouping = build_call_site_grouping([get_call, helper_call, request_call])
    helper_node = grouping.node_for_call_site(helper_call)
    helper_node.resolved_helper = _WEBTARGET_HELPER

    if expansion_call_sites is None:
        expansion_call_sites = [
            make_call_site(
                method_name="target",
                receiver_type="jakarta.ws.rs.client.Client",
                argument_expr=[expansion_target_argument],
                start_line=10,
                end_line=10,
                end_column=40,
            )
        ]
    expansion_grouping = build_call_site_grouping(expansion_call_sites)
    helper_node.helper_expansion = HelperExpansion(
        callee=_WEBTARGET_HELPER, grouping=expansion_grouping
    )

    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=_TEST_OWNER,
                context_class_name=_TEST_OWNER.defining_class_name,
                grouping=grouping,
                method_details=make_callable(signature=_TEST_OWNER.method_signature),
            )
        ]
    )
    analysis = FakeJavaAnalysis(
        classes={
            _TEST_OWNER.defining_class_name: make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["API_BASE"],
                        modifiers=["static", "final"],
                        variable_initializers={"API_BASE": '"/apis/registry"'},
                    )
                ]
            ),
        },
        methods_by_class={
            _WEBTARGET_HELPER.defining_class_name: {
                _WEBTARGET_HELPER.method_signature: make_callable(
                    signature=_WEBTARGET_HELPER.method_signature,
                    return_type="jakarta.ws.rs.client.WebTarget",
                    parameters=[
                        make_callable_parameter(
                            name="uriPath", type_name="java.lang.String"
                        )
                    ],
                )
            }
        },
    )
    classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(
            runtime_view, analysis=analysis
        ),
    )
    return grouping


def test_expansion_builder_path_upgrades_adopted_base() -> None:
    grouping = _expanded_webtarget_setup("API_BASE + uriPath")

    event = _event_classification(grouping)
    assert event.path == "/apis/registry/v3/search"
    assert event.path_truncated is False


def test_expansion_path_without_suffix_relationship_keeps_adopted_base() -> None:
    # The expansion's path must end with the adopted base to evidence that the
    # helper argument flowed into the builder.
    grouping = _expanded_webtarget_setup('API_BASE + "/other"')

    event = _event_classification(grouping)
    assert event.path == "/v3/search"


def _expansion_chain_call_sites(first_path: str, second_path: str) -> list:
    # client.target(base).path(<first>).path(<second>) inside the helper body.
    return [
        make_call_site(
            method_name="target",
            receiver_type="jakarta.ws.rs.client.Client",
            argument_expr=["base"],
            start_line=10,
            end_line=10,
            end_column=25,
        ),
        make_call_site(
            method_name="path",
            receiver_type="jakarta.ws.rs.client.WebTarget",
            argument_expr=[first_path],
            start_line=10,
            end_line=10,
            end_column=40,
        ),
        make_call_site(
            method_name="path",
            receiver_type="jakarta.ws.rs.client.WebTarget",
            argument_expr=[second_path],
            start_line=10,
            end_line=10,
            end_column=52,
        ),
    ]


def test_expansion_appended_chain_paths_upgrade_adopted_base() -> None:
    grouping = _expanded_webtarget_setup(
        expansion_call_sites=_expansion_chain_call_sites('"/api"', "uriPath")
    )

    event = _event_classification(grouping)
    assert event.path == "/api/v3/search"
    assert event.path_truncated is False


def test_expansion_with_multiple_path_bearing_chains_keeps_adopted_base() -> None:
    # A second chain carrying a path makes it ambiguous which chain the helper
    # returns.
    call_sites = _expansion_chain_call_sites('"/api"', "uriPath")
    call_sites.append(
        make_call_site(
            method_name="target",
            receiver_type="jakarta.ws.rs.client.Client",
            argument_expr=['"/other"'],
            start_line=12,
            end_line=12,
            end_column=30,
        )
    )
    grouping = _expanded_webtarget_setup(expansion_call_sites=call_sites)

    event = _event_classification(grouping)
    assert event.path == "/v3/search"


def test_expansion_chain_with_truncated_non_final_member_keeps_adopted_base() -> None:
    # Composing across "/api/" + version would fabricate adjacency across the
    # statically unknown appended value.
    grouping = _expanded_webtarget_setup(
        expansion_call_sites=_expansion_chain_call_sites('"/api/" + version', "uriPath")
    )

    event = _event_classification(grouping)
    assert event.path == "/v3/search"
    assert event.path_truncated is False


# The registry-derived predicate spans frameworks without hand-curated lists.


def test_request_builder_receiver_predicate_breadth() -> None:
    assert is_request_builder_receiver_type("jakarta.ws.rs.client.WebTarget")
    assert is_request_builder_receiver_type("javax.ws.rs.client.WebTarget")
    assert is_request_builder_receiver_type("jakarta.ws.rs.client.Invocation.Builder")
    assert is_request_builder_receiver_type(
        "io.restassured.specification.RequestSpecification"
    )
    assert is_request_builder_receiver_type(
        "org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder"
    )
    assert is_request_builder_receiver_type("okhttp3.Request$Builder")
    assert is_request_builder_receiver_type("com.intuit.karate.Http")
    assert not is_request_builder_receiver_type("java.lang.String")
    assert not is_request_builder_receiver_type("")
    # Response-side receivers never build requests.
    assert not is_request_builder_receiver_type("io.restassured.response.Response")
    assert not is_request_builder_receiver_type(
        "org.springframework.test.web.reactive.server.WebTestClient$ResponseSpec"
    )
    # The karate exact type must not leak prefix semantics onto its package.
    assert not is_request_builder_receiver_type("com.intuit.karate.http.Response")
    # Client factories take base URLs or config, not request paths.
    assert not is_request_builder_receiver_type("okhttp3.OkHttpClient")
    assert not is_request_builder_receiver_type("jakarta.ws.rs.client.Client")
    assert not is_request_builder_receiver_type(
        "org.springframework.web.reactive.function.client.WebClient"
    )
