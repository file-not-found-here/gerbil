"""Concern #2 validation: Apache HTTPClient constructor-path propagation.

Tests whether the cross-chain builder queue propagates path from a verb-typed
constructor (e.g. ``new HttpGet("/api/data")``) into a separate-statement
``execute()`` call.

Pattern under test (FlowIntegrationTest.getRunResponse style):

    HttpGet request = new HttpGet("/api/v1/jobs/runs/" + runId);   // line 1
    request.addHeader(ACCEPT, APPLICATION_JSON.toString());        // line 2
    HttpResponse response = http.execute(request);                 // line 3
"""

from __future__ import annotations

import pytest

from gerbil.analysis.http.classification import classify_http_on_grouping
from gerbil.analysis.runtime.call_sites import build_call_site_grouping, MethodRef
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.schema import HttpRequestRole, LifecyclePhase
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
)


def _classify(method_details):
    """Classify HTTP on a grouping built from method_details and return
    (grouping, {method_name: http_classification})."""
    grouping = build_call_site_grouping(method_details.call_sites)
    owner = MethodRef(
        defining_class_name="example.ApiTest",
        method_signature=method_details.signature,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=grouping,
                method_details=method_details,
            )
        ]
    )
    resolver = build_runtime_receiver_resolver_for_testing(
        runtime_view,
        get_static_import_index_for_class=lambda _: StaticImportIndex.EMPTY,
    )
    classify_http_on_grouping(
        grouping=grouping, owner=owner, receiver_resolver=resolver
    )
    return grouping


def _classify_single_call(call_site):
    grouping = _classify(make_callable(call_sites=[call_site]))
    return grouping.nodes[0].http_classification


# ── Multi-statement: constructor on line 1, execute on line 3 ──


def test_multi_statement_constructor_path_propagates_to_execute() -> None:
    """The exact pattern from FlowIntegrationTest.getRunResponse:

        HttpGet request = new HttpGet("/api/v1/jobs/runs/" + runId);
        request.addHeader(ACCEPT, APPLICATION_JSON.toString());
        HttpResponse response = http.execute(request);

    The path from the constructor should propagate to the execute event
    via the cross-chain builder queue.
    """
    method = make_callable(
        call_sites=[
            # new HttpGet("/api/v1/jobs/runs/...")  — line 1
            make_call_site(
                method_name="<init>",
                receiver_type="org.apache.http.client.methods.HttpGet",
                argument_expr=['"/api/v1/jobs/runs/" + runId'],
                is_constructor_call=True,
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=60,
            ),
            # request.addHeader(...)  — line 2
            make_call_site(
                method_name="addHeader",
                receiver_type="org.apache.http.client.methods.HttpGet",
                receiver_expr="request",
                argument_expr=['"Accept"', '"application/json"'],
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=55,
            ),
            # http.execute(request)  — line 3
            make_call_site(
                method_name="execute",
                receiver_type="org.apache.http.impl.client.CloseableHttpClient",
                receiver_expr="http",
                argument_expr=["request"],
                argument_types=["org.apache.http.client.methods.HttpGet"],
                start_line=3,
                start_column=1,
                end_line=3,
                end_column=25,
            ),
        ]
    )

    grouping = _classify(method)

    classified = {
        node.call_site.method_name: node.http_classification
        for node in grouping.nodes
        if node.http_classification is not None
    }

    # Constructor should be classified as BUILDER with path and verb.
    init = classified["<init>"]
    assert init.request_role == HttpRequestRole.BUILDER
    assert init.http_method == "GET"
    assert init.path == "/api/v1/jobs/runs/"

    # Execute should be classified as EVENT.
    execute = classified["execute"]
    assert execute.request_role == HttpRequestRole.EVENT
    assert execute.http_method == "GET"

    # This is the key assertion from Concern #2:
    # Does the constructor's path propagate to the execute event?
    assert (
        execute.path == "/api/v1/jobs/runs/"
    ), f"Expected constructor path to propagate to execute(), got: {execute.path!r}"

    # Endpoint candidate should also have the path.
    execute_node = next(
        n for n in grouping.nodes if n.call_site.method_name == "execute"
    )
    assert execute_node.endpoint_candidate is not None
    assert execute_node.endpoint_candidate.path == "/api/v1/jobs/runs/"


# ── Simpler two-statement variant (no intermediate addHeader) ──


def test_two_statement_constructor_path_propagates() -> None:
    """Constructor on line 1, execute on line 2, no intermediate builder."""
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="<init>",
                receiver_type="org.apache.http.client.methods.HttpPost",
                argument_expr=['"/api/orders"'],
                is_constructor_call=True,
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="execute",
                receiver_type="org.apache.http.impl.client.CloseableHttpClient",
                argument_expr=["request"],
                argument_types=["org.apache.http.client.methods.HttpPost"],
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=25,
            ),
        ]
    )

    grouping = _classify(method)

    execute_cls = next(
        n.http_classification
        for n in grouping.nodes
        if n.call_site.method_name == "execute"
    )

    assert execute_cls is not None
    assert execute_cls.http_method == "POST"
    assert execute_cls.path == "/api/orders"


def test_execute_host_request_overload_uses_request_argument_type_for_method() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="execute",
                receiver_type="org.apache.http.impl.client.CloseableHttpClient",
                argument_expr=["host", "request"],
                argument_types=[
                    "org.apache.http.HttpHost",
                    "org.apache.http.client.methods.HttpPost",
                ],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=30,
            )
        ]
    )

    grouping = _classify(method)

    execute_cls = next(
        n.http_classification
        for n in grouping.nodes
        if n.call_site.method_name == "execute"
    )

    assert execute_cls is not None
    assert execute_cls.http_method == "POST"


def test_fluent_request_factory_path_propagates_to_execute() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="Get",
                receiver_type="org.apache.http.client.fluent.Request",
                argument_expr=['"/api/data"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="execute",
                receiver_type="org.apache.http.client.fluent.Request",
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=20,
            ),
        ]
    )

    grouping = _classify(method)

    execute_cls = next(
        n.http_classification
        for n in grouping.nodes
        if n.call_site.method_name == "execute"
    )

    assert execute_cls is not None
    assert execute_cls.http_method == "GET"
    assert execute_cls.path == "/api/data"


@pytest.mark.parametrize(
    ("body_method_name", "argument_exprs"),
    [
        ("bodyByteArray", ["bytes"]),
        ("bodyFile", ["file", "ContentType.APPLICATION_OCTET_STREAM"]),
        ("bodyForm", ["formParams"]),
        ("bodyStream", ["stream"]),
        ("bodyString", ['"{}"', "ContentType.APPLICATION_JSON"]),
    ],
)
def test_fluent_body_builder_payload_propagates_to_execute(
    body_method_name: str,
    argument_exprs: list[str],
) -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="Post",
                receiver_type="org.apache.http.client.fluent.Request",
                argument_expr=['"/api/data"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name=body_method_name,
                receiver_type="org.apache.http.client.fluent.Request",
                argument_expr=argument_exprs,
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=60,
            ),
            make_call_site(
                method_name="execute",
                receiver_type="org.apache.http.client.fluent.Request",
                start_line=3,
                start_column=1,
                end_line=3,
                end_column=20,
            ),
        ]
    )

    grouping = _classify(method)

    body_cls = next(
        n.http_classification
        for n in grouping.nodes
        if n.call_site.method_name == body_method_name
    )
    execute_cls = next(
        n.http_classification
        for n in grouping.nodes
        if n.call_site.method_name == "execute"
    )

    assert body_cls is not None
    assert body_cls.request_role == HttpRequestRole.BUILDER
    assert body_cls.has_body_payload is True
    assert execute_cls is not None
    assert execute_cls.http_method == "POST"
    assert execute_cls.path == "/api/data"
    assert execute_cls.has_body_payload is True


def test_simple_http_request_set_body_propagates_to_execute() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="create",
                receiver_type="org.apache.hc.client5.http.async.methods.SimpleHttpRequest",
                argument_expr=['"POST"', '"/api/data"'],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="setBody",
                receiver_type="org.apache.hc.client5.http.async.methods.SimpleHttpRequest",
                receiver_expr="request",
                argument_expr=["body"],
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=30,
            ),
            make_call_site(
                method_name="execute",
                receiver_type=(
                    "org.apache.hc.client5.http.impl.async.CloseableHttpAsyncClient"
                ),
                receiver_expr="client",
                argument_expr=["request"],
                argument_types=[
                    "org.apache.hc.client5.http.async.methods.SimpleHttpRequest"
                ],
                start_line=3,
                start_column=1,
                end_line=3,
                end_column=30,
            ),
        ]
    )

    grouping = _classify(method)

    execute_cls = next(
        n.http_classification
        for n in grouping.nodes
        if n.call_site.method_name == "execute"
    )

    assert execute_cls is not None
    assert execute_cls.http_method == "POST"
    assert execute_cls.path == "/api/data"
    assert execute_cls.has_body_payload is True


@pytest.mark.parametrize(
    "argument_exprs",
    [
        ['"POST"', '"/api/data"'],
        ["Method.POST", "host", '"/api/data"'],
        ['"POST"', '"https"', "authority", '"/api/data"'],
    ],
)
def test_simple_http_request_constructor_extracts_method_and_path(
    argument_exprs: list[str],
) -> None:
    classification = _classify_single_call(
        make_call_site(
            method_name="<init>",
            receiver_type="org.apache.hc.client5.http.async.methods.SimpleHttpRequest",
            argument_expr=argument_exprs,
            is_constructor_call=True,
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=70,
        )
    )

    assert classification is not None
    assert classification.request_role == HttpRequestRole.BUILDER
    assert classification.http_method == "POST"
    assert classification.path == "/api/data"


def test_simple_http_request_constructor_path_propagates_to_execute() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="<init>",
                receiver_type="org.apache.hc.client5.http.async.methods.SimpleHttpRequest",
                argument_expr=['"POST"', '"/api/data"'],
                is_constructor_call=True,
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="execute",
                receiver_type=(
                    "org.apache.hc.client5.http.impl.async.CloseableHttpAsyncClient"
                ),
                receiver_expr="client",
                argument_expr=["request"],
                argument_types=[
                    "org.apache.hc.client5.http.async.methods.SimpleHttpRequest"
                ],
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=30,
            ),
        ]
    )

    grouping = _classify(method)

    execute_cls = next(
        n.http_classification
        for n in grouping.nodes
        if n.call_site.method_name == "execute"
    )

    assert execute_cls is not None
    assert execute_cls.http_method == "POST"
    assert execute_cls.path == "/api/data"


def test_simple_http_request_create_uri_overload_extracts_path() -> None:
    classification = _classify_single_call(
        make_call_site(
            method_name="create",
            receiver_type="org.apache.hc.client5.http.async.methods.SimpleHttpRequest",
            argument_expr=['"POST"', '"/api/data"'],
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=50,
        )
    )

    assert classification is not None
    assert classification.http_method == "POST"
    assert classification.path == "/api/data"


def test_apache_request_builder_create_method_only_does_not_extract_path() -> None:
    classification = _classify_single_call(
        make_call_site(
            method_name="create",
            receiver_type="org.apache.hc.core5.http.io.support.ClassicRequestBuilder",
            argument_expr=['"POST"'],
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=30,
        )
    )

    assert classification is not None
    assert classification.http_method == "POST"
    assert classification.path == ""


def test_simple_http_request_create_host_overload_extracts_path_after_host() -> None:
    classification = _classify_single_call(
        make_call_site(
            method_name="create",
            receiver_type="org.apache.hc.client5.http.async.methods.SimpleHttpRequest",
            argument_expr=['"POST"', "host", '"/api/data"'],
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=60,
        )
    )

    assert classification is not None
    assert classification.http_method == "POST"
    assert classification.path == "/api/data"


def test_simple_http_request_create_authority_overload_extracts_path_after_authority() -> (
    None
):
    classification = _classify_single_call(
        make_call_site(
            method_name="create",
            receiver_type="org.apache.hc.client5.http.async.methods.SimpleHttpRequest",
            argument_expr=['"POST"', '"https"', "authority", '"/api/data"'],
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=70,
        )
    )

    assert classification is not None
    assert classification.http_method == "POST"
    assert classification.path == "/api/data"


# ── MarquezHttp.put style: setURI instead of constructor arg ──


def test_setURI_path_propagates_to_execute() -> None:
    """MarquezHttp internal pattern:

        HttpPut request = new HttpPut();
        request.setURI(url.toURI());
        HttpResponse response = http.execute(request);

    setURI's argument contains no literal path, but the constructor is
    verb-typed. This tests whether setURI-based paths work when the
    argument contains a literal.
    """
    method = make_callable(
        call_sites=[
            # new HttpPut()  — no path arg
            make_call_site(
                method_name="<init>",
                receiver_type="org.apache.http.client.methods.HttpPut",
                argument_expr=[],
                is_constructor_call=True,
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=20,
            ),
            # request.setURI(...)  — path in argument as literal
            make_call_site(
                method_name="setURI",
                receiver_type="org.apache.http.client.methods.HttpPut",
                receiver_expr="request",
                argument_expr=['"http://localhost:8080/api/v1/namespaces/test"'],
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=40,
            ),
            # http.execute(request)
            make_call_site(
                method_name="execute",
                receiver_type="org.apache.http.impl.client.CloseableHttpClient",
                receiver_expr="http",
                argument_expr=["request"],
                argument_types=["org.apache.http.client.methods.HttpPut"],
                start_line=3,
                start_column=1,
                end_line=3,
                end_column=25,
            ),
        ]
    )

    grouping = _classify(method)

    execute_cls = next(
        n.http_classification
        for n in grouping.nodes
        if n.call_site.method_name == "execute"
    )

    assert execute_cls is not None
    assert execute_cls.http_method == "PUT"
    # setURI builder has a literal URL — does it propagate?
    assert execute_cls.path == "http://localhost:8080/api/v1/namespaces/test"


# ── Inline constructor: execute(new HttpGet("/api/data")) ──


def test_inline_constructor_path_propagates() -> None:
    """Inline pattern where constructor is nested inside execute() args.

    This should work via same-chain descendant correlation.
    """
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="execute",
                receiver_type="org.apache.http.impl.client.CloseableHttpClient",
                argument_expr=['new HttpGet("/api/data")'],
                argument_types=["org.apache.http.client.methods.HttpGet"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=50,
            ),
            make_call_site(
                method_name="<init>",
                receiver_type="org.apache.http.client.methods.HttpGet",
                argument_expr=['"/api/data"'],
                is_constructor_call=True,
                start_line=1,
                start_column=17,
                end_line=1,
                end_column=49,
            ),
        ]
    )

    grouping = _classify(method)

    execute_cls = next(
        n.http_classification
        for n in grouping.nodes
        if n.call_site.method_name == "execute"
    )

    assert execute_cls is not None
    assert execute_cls.http_method == "GET"
    assert execute_cls.path == "/api/data"
