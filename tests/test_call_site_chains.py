from __future__ import annotations

from gerbil.analysis.runtime.call_sites import (
    HelperExpansion,
    MethodRef,
    build_call_site_grouping,
    build_expanded_call_site_grouping,
    iter_expanded_evaluation_order,
    iter_resolved_helpers,
)
from tests.cldk_factories import make_call_site


def test_mockmvc_argument_nesting() -> None:
    """perform(get("/api")) nests get() inside perform()."""
    perform = make_call_site(
        method_name="perform",
        receiver_type="org.springframework.test.web.servlet.MockMvc",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=34,
    )
    get = make_call_site(
        method_name="get",
        receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
        argument_expr=['"/api/users"'],
        start_line=1,
        start_column=17,
        end_line=1,
        end_column=33,
    )

    grouping = build_call_site_grouping([perform, get])

    assert len(grouping.roots) == 1
    root = grouping.roots[0]
    assert root.call_site is perform
    assert len(root.children) == 1
    assert root.children[0].call_site is get
    assert root.argument_children() == [root.children[0]]
    assert root.receiver_children() == []


def test_webtestclient_receiver_chain() -> None:
    """webTestClient.get().uri("/api").exchange() shares one start and grows by end."""
    get = make_call_site(
        method_name="get",
        receiver_type="org.springframework.test.web.reactive.server.WebTestClient",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=19,
    )
    uri = make_call_site(
        method_name="uri",
        receiver_type="org.springframework.test.web.reactive.server.WebTestClient$RequestHeadersUriSpec",
        argument_expr=['"/api"'],
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=31,
    )
    exchange = make_call_site(
        method_name="exchange",
        receiver_type="org.springframework.test.web.reactive.server.WebTestClient$RequestHeadersSpec",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=42,
    )

    grouping = build_call_site_grouping([get, uri, exchange])

    chain_nodes = grouping.receiver_chain_for(line=1, col=1)
    assert chain_nodes
    assert [node.call_site.method_name for node in chain_nodes] == [
        "get",
        "uri",
        "exchange",
    ]


def test_okhttp_mixed_nesting() -> None:
    """client.newCall(Request.Builder().url('/api').get().build()).execute()."""
    new_call = make_call_site(
        method_name="newCall",
        receiver_type="okhttp3.OkHttpClient",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=63,
    )
    url = make_call_site(
        method_name="url",
        receiver_type="okhttp3.Request$Builder",
        argument_expr=['"/api"'],
        start_line=1,
        start_column=16,
        end_line=1,
        end_column=48,
    )
    get = make_call_site(
        method_name="get",
        receiver_type="okhttp3.Request$Builder",
        start_line=1,
        start_column=16,
        end_line=1,
        end_column=54,
    )
    build = make_call_site(
        method_name="build",
        receiver_type="okhttp3.Request$Builder",
        start_line=1,
        start_column=16,
        end_line=1,
        end_column=62,
    )
    execute = make_call_site(
        method_name="execute",
        receiver_type="okhttp3.OkHttpClient",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=73,
    )

    grouping = build_call_site_grouping([new_call, url, get, build, execute])

    outer_chain_nodes = grouping.receiver_chain_for(line=1, col=1)
    assert outer_chain_nodes
    assert [node.call_site.method_name for node in outer_chain_nodes] == [
        "newCall",
        "execute",
    ]

    inner_chain_nodes = grouping.receiver_chain_for(line=1, col=16)
    assert inner_chain_nodes
    assert [node.call_site.method_name for node in inner_chain_nodes] == [
        "url",
        "get",
        "build",
    ]


def test_multiline_chain() -> None:
    get = make_call_site(
        method_name="get",
        start_line=1,
        start_column=1,
        end_line=2,
        end_column=10,
    )
    uri = make_call_site(
        method_name="uri",
        argument_expr=['"/api"'],
        start_line=1,
        start_column=1,
        end_line=3,
        end_column=16,
    )
    exchange = make_call_site(
        method_name="exchange",
        start_line=1,
        start_column=1,
        end_line=4,
        end_column=15,
    )

    grouping = build_call_site_grouping([get, uri, exchange])

    chain_nodes = grouping.receiver_chain_for(line=1, col=1)
    assert chain_nodes
    assert [node.call_site.method_name for node in chain_nodes] == [
        "get",
        "uri",
        "exchange",
    ]


def test_restassured_full_chain() -> None:
    given = make_call_site(
        method_name="given",
        receiver_type="io.restassured.RestAssured",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=7,
    )
    header = make_call_site(
        method_name="header",
        receiver_type="io.restassured.specification.RequestSpecification",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=36,
    )
    when = make_call_site(
        method_name="when",
        receiver_type="io.restassured.specification.RequestSpecification",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=43,
    )
    get = make_call_site(
        method_name="get",
        receiver_type="io.restassured.specification.RequestSpecification",
        argument_expr=['"/api"'],
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=55,
    )
    then = make_call_site(
        method_name="then",
        receiver_type="io.restassured.response.Response",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=62,
    )
    status_code = make_call_site(
        method_name="statusCode",
        receiver_type="io.restassured.response.ValidatableResponse",
        argument_expr=["200"],
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=78,
    )

    grouping = build_call_site_grouping([given, header, when, get, then, status_code])

    chain_nodes = grouping.receiver_chain_for(line=1, col=1)
    assert chain_nodes
    assert [node.call_site.method_name for node in chain_nodes] == [
        "given",
        "header",
        "when",
        "get",
        "then",
        "statusCode",
    ]


def test_missing_end_positions_no_crash() -> None:
    a = make_call_site(method_name="a", start_line=1, start_column=1)
    b = make_call_site(method_name="b", start_line=1, start_column=10)

    grouping = build_call_site_grouping([a, b])

    assert len(grouping.roots) == 2


def test_equal_span_calls_are_siblings_not_parent_child() -> None:
    first = make_call_site(
        method_name="assertThat",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )
    second = make_call_site(
        method_name="verify",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=40,
    )

    grouping = build_call_site_grouping([first, second])

    assert len(grouping.roots) == 2

    first_node = grouping.node_for_call_site(first)
    second_node = grouping.node_for_call_site(second)
    assert first_node is not None
    assert second_node is not None
    assert first_node.parent is None
    assert second_node.parent is None
    assert first_node.children == []
    assert second_node.children == []


def test_equal_span_siblings_preserve_surrounding_topology() -> None:
    outer = make_call_site(
        method_name="outer",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=100,
    )
    sibling_a = make_call_site(
        method_name="branchA",
        start_line=1,
        start_column=10,
        end_line=1,
        end_column=60,
    )
    sibling_b = make_call_site(
        method_name="branchB",
        start_line=1,
        start_column=10,
        end_line=1,
        end_column=60,
    )
    nested = make_call_site(
        method_name="inner",
        start_line=1,
        start_column=20,
        end_line=1,
        end_column=30,
    )

    grouping = build_call_site_grouping([outer, sibling_a, sibling_b, nested])

    outer_node = grouping.node_for_call_site(outer)
    sibling_a_node = grouping.node_for_call_site(sibling_a)
    sibling_b_node = grouping.node_for_call_site(sibling_b)
    nested_node = grouping.node_for_call_site(nested)
    assert outer_node is not None
    assert sibling_a_node is not None
    assert sibling_b_node is not None
    assert nested_node is not None

    assert outer_node.parent is None
    assert sibling_a_node.parent is outer_node
    assert sibling_b_node.parent is outer_node
    assert sibling_b_node not in sibling_a_node.children
    assert sibling_a_node not in sibling_b_node.children

    assert nested_node.parent in (sibling_a_node, sibling_b_node)
    assert nested_node.parent is not outer_node
    assert nested_node in outer_node.all_descendants()


def test_call_site_node_helper_expansion_defaults_to_none() -> None:
    call_site = make_call_site(method_name="foo", start_line=1, start_column=1)
    grouping = build_call_site_grouping([call_site])

    assert grouping.roots[0].helper_expansion is None
    assert grouping.roots[0].resolved_helper is None


def test_independent_calls_on_different_lines() -> None:
    call_a = make_call_site(
        method_name="getForEntity",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=40,
    )
    call_b = make_call_site(
        method_name="assertEquals",
        start_line=6,
        start_column=1,
        end_line=6,
        end_column=30,
    )

    grouping = build_call_site_grouping([call_a, call_b])

    assert len(grouping.roots) == 2
    assert grouping.receiver_chain_for(line=5, col=1)
    assert grouping.receiver_chain_for(line=6, col=1)


def test_iter_expanded_evaluation_order_no_expansion() -> None:
    a_call = make_call_site(
        method_name="a",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=10,
    )
    b_call = make_call_site(
        method_name="b",
        start_line=2,
        start_column=1,
        end_line=2,
        end_column=10,
    )

    grouping = build_call_site_grouping([a_call, b_call])
    owner = MethodRef(
        defining_class_name="com.example.Test", method_signature="testFoo()"
    )

    events = list(iter_expanded_evaluation_order(grouping, owner=owner))

    assert len(events) == 2
    assert events[0].node.call_site.method_name == "a"
    assert events[0].owner == owner
    assert events[0].depth == 0
    assert events[1].node.call_site.method_name == "b"


def test_iter_expanded_evaluation_order_with_helper() -> None:
    setup = make_call_site(
        method_name="setup",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=10,
    )
    helper_call = make_call_site(
        method_name="doHelper",
        start_line=2,
        start_column=1,
        end_line=2,
        end_column=15,
    )
    verify = make_call_site(
        method_name="verify",
        start_line=3,
        start_column=1,
        end_line=3,
        end_column=10,
    )
    grouping = build_call_site_grouping([setup, helper_call, verify])

    helper_inner_a = make_call_site(
        method_name="innerA",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=10,
    )
    helper_inner_b = make_call_site(
        method_name="innerB",
        start_line=6,
        start_column=1,
        end_line=6,
        end_column=10,
    )
    helper_grouping = build_call_site_grouping([helper_inner_a, helper_inner_b])
    helper_ref = MethodRef(
        defining_class_name="com.example.Test",
        method_signature="doHelper()",
    )

    helper_node = grouping.node_for_call_site(helper_call)
    assert helper_node is not None
    helper_node.helper_expansion = HelperExpansion(
        callee=helper_ref, grouping=helper_grouping
    )

    owner = MethodRef(
        defining_class_name="com.example.Test", method_signature="testFoo()"
    )
    events = list(iter_expanded_evaluation_order(grouping, owner=owner))

    names = [event.node.call_site.method_name for event in events]
    assert names == ["setup", "doHelper", "innerA", "innerB", "verify"]

    depths = [event.depth for event in events]
    assert depths == [0, 0, 1, 1, 0]

    assert events[2].owner == helper_ref
    assert events[3].owner == helper_ref


def test_build_expanded_grouping_with_resolver() -> None:
    setup = make_call_site(
        method_name="setup",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=10,
    )
    helper_call = make_call_site(
        method_name="doHelper",
        start_line=2,
        start_column=1,
        end_line=2,
        end_column=15,
        callee_signature="doHelper()",
    )
    verify = make_call_site(
        method_name="verify",
        start_line=3,
        start_column=1,
        end_line=3,
        end_column=10,
    )

    inner_a = make_call_site(
        method_name="innerA",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=10,
    )
    inner_b = make_call_site(
        method_name="innerB",
        start_line=6,
        start_column=1,
        end_line=6,
        end_column=10,
    )

    helper_ref = MethodRef(
        defining_class_name="com.example.Test",
        method_signature="doHelper()",
    )

    def resolve_helper(owner: MethodRef, call_site):
        if call_site.callee_signature == "doHelper()":
            return helper_ref
        return None

    def load_call_sites(method_ref: MethodRef):
        if method_ref == helper_ref:
            return [inner_a, inner_b]
        return None

    owner = MethodRef(
        defining_class_name="com.example.Test", method_signature="testFoo()"
    )
    grouping = build_expanded_call_site_grouping(
        call_sites=[setup, helper_call, verify],
        owner=owner,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=1,
    )

    events = list(iter_expanded_evaluation_order(grouping, owner=owner))
    names = [event.node.call_site.method_name for event in events]
    assert names == ["setup", "doHelper", "innerA", "innerB", "verify"]


def test_build_expanded_grouping_depth_limit() -> None:
    call_a = make_call_site(
        method_name="helperA",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=10,
        callee_signature="helperA()",
    )
    call_b = make_call_site(
        method_name="helperB",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=10,
        callee_signature="helperB()",
    )
    call_c = make_call_site(
        method_name="leaf",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=10,
    )

    ref_a = MethodRef(defining_class_name="T", method_signature="helperA()")
    ref_b = MethodRef(defining_class_name="T", method_signature="helperB()")

    def resolve_helper(owner: MethodRef, call_site):
        if call_site.callee_signature == "helperA()":
            return ref_a
        if call_site.callee_signature == "helperB()":
            return ref_b
        return None

    def load_call_sites(method_ref: MethodRef):
        if method_ref == ref_a:
            return [call_b]
        if method_ref == ref_b:
            return [call_c]
        return None

    owner = MethodRef(defining_class_name="T", method_signature="test()")

    grouping = build_expanded_call_site_grouping(
        call_sites=[call_a],
        owner=owner,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=1,
    )
    events = list(iter_expanded_evaluation_order(grouping, owner=owner))
    names = [event.node.call_site.method_name for event in events]
    assert names == ["helperA", "helperB"]
    assert events[0].node.resolved_helper == ref_a
    assert events[0].node.helper_expansion is not None
    assert events[1].node.resolved_helper == ref_b
    assert events[1].node.helper_expansion is None

    grouping_two = build_expanded_call_site_grouping(
        call_sites=[call_a],
        owner=owner,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=2,
    )
    events_two = list(iter_expanded_evaluation_order(grouping_two, owner=owner))
    names_two = [event.node.call_site.method_name for event in events_two]
    assert names_two == ["helperA", "helperB", "leaf"]


def test_build_expanded_grouping_cycle_safe() -> None:
    call_recurse = make_call_site(
        method_name="recurse",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=10,
        callee_signature="recurse()",
    )
    recurse_ref = MethodRef(defining_class_name="T", method_signature="recurse()")

    def resolve_helper(owner: MethodRef, call_site):
        if call_site.callee_signature == "recurse()":
            return recurse_ref
        return None

    def load_call_sites(method_ref: MethodRef):
        if method_ref == recurse_ref:
            return [
                make_call_site(
                    method_name="recurse",
                    start_line=1,
                    start_column=1,
                    end_line=1,
                    end_column=10,
                    callee_signature="recurse()",
                )
            ]
        return None

    owner = MethodRef(defining_class_name="T", method_signature="test()")
    grouping = build_expanded_call_site_grouping(
        call_sites=[call_recurse],
        owner=owner,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=5,
    )
    events = list(iter_expanded_evaluation_order(grouping, owner=owner))
    names = [event.node.call_site.method_name for event in events]
    assert names == ["recurse", "recurse"]
    assert events[0].depth == 0
    assert events[1].depth == 1
    assert events[0].node.resolved_helper == recurse_ref
    assert events[0].node.helper_expansion is not None
    assert events[1].node.resolved_helper == recurse_ref
    assert events[1].node.helper_expansion is None


def test_build_expanded_grouping_multiple_helpers_same_level() -> None:
    helper_one_call = make_call_site(
        method_name="helperOne",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=15,
        callee_signature="helperOne()",
    )
    helper_two_call = make_call_site(
        method_name="helperTwo",
        start_line=2,
        start_column=1,
        end_line=2,
        end_column=15,
        callee_signature="helperTwo()",
    )

    helper_one_ref = MethodRef(defining_class_name="T", method_signature="helperOne()")
    helper_two_ref = MethodRef(defining_class_name="T", method_signature="helperTwo()")

    inner_one = make_call_site(
        method_name="inner1",
        start_line=10,
        start_column=1,
        end_line=10,
        end_column=10,
    )
    inner_two = make_call_site(
        method_name="inner2",
        start_line=20,
        start_column=1,
        end_line=20,
        end_column=10,
    )

    def resolve_helper(owner: MethodRef, call_site):
        if call_site.callee_signature == "helperOne()":
            return helper_one_ref
        if call_site.callee_signature == "helperTwo()":
            return helper_two_ref
        return None

    def load_call_sites(method_ref: MethodRef):
        if method_ref == helper_one_ref:
            return [inner_one]
        if method_ref == helper_two_ref:
            return [inner_two]
        return None

    owner = MethodRef(defining_class_name="T", method_signature="test()")
    grouping = build_expanded_call_site_grouping(
        call_sites=[helper_one_call, helper_two_call],
        owner=owner,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=1,
    )
    events = list(iter_expanded_evaluation_order(grouping, owner=owner))
    names = [event.node.call_site.method_name for event in events]
    assert names == ["helperOne", "inner1", "helperTwo", "inner2"]


def test_iter_resolved_helpers_yields_expanded_and_depth_cutoff_helpers() -> None:
    """iter_resolved_helpers yields helpers with expansion AND those at depth cutoff."""
    root_call_sites = [
        make_call_site(
            method_name="helperA",
            callee_signature="helperA()",
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=10,
        ),
        make_call_site(
            method_name="externalCall",
            start_line=2,
            start_column=1,
            end_line=2,
            end_column=10,
        ),
    ]
    helper_a_call_sites = [
        make_call_site(
            method_name="helperB",
            callee_signature="helperB()",
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=10,
        ),
    ]

    owner = MethodRef(defining_class_name="Test", method_signature="test()")
    helper_a_ref = MethodRef(defining_class_name="Test", method_signature="helperA()")
    helper_b_ref = MethodRef(defining_class_name="Test", method_signature="helperB()")

    def resolve_helper(caller: MethodRef, call_site) -> MethodRef | None:
        sig = call_site.callee_signature or ""
        if sig == "helperA()":
            return helper_a_ref
        if sig == "helperB()":
            return helper_b_ref
        return None

    def load_call_sites(ref: MethodRef):
        if ref == helper_a_ref:
            return helper_a_call_sites
        return None

    grouping = build_expanded_call_site_grouping(
        call_sites=root_call_sites,
        owner=owner,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=1,
    )

    helpers = list(iter_resolved_helpers(grouping))
    assert helper_a_ref in helpers
    assert helper_b_ref in helpers
    assert len(helpers) == 2


def test_iter_resolved_helpers_empty_for_no_helpers() -> None:
    call_sites = [
        make_call_site(method_name="externalCall", start_line=1, start_column=1),
    ]
    grouping = build_call_site_grouping(call_sites)
    helpers = list(iter_resolved_helpers(grouping))
    assert helpers == []
