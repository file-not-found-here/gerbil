"""OkHttp verb-less Request.Builder chains default to GET, matching the
runtime default okhttp3.Request.Builder initializes its method field with."""

from __future__ import annotations

from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.schema import (
    HttpDispatchFramework,
    HttpRequestRole,
    LifecyclePhase,
)
from tests.cldk_factories import (
    classify_runtime_view_for_testing,
    make_call_site,
    make_callable,
)


def _classify_and_get_grouping(call_sites):
    owner = MethodRef(
        defining_class_name="example.ApiTest",
        method_signature="testRequest()",
    )
    method = make_callable(call_sites=call_sites)
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            )
        ]
    )
    classify_runtime_view_for_testing(runtime_view)
    return runtime_view.entries[0].grouping


def _single_event(grouping):
    events = [
        node
        for node in grouping.nodes
        if node.http_classification is not None
        and node.http_classification.request_role == HttpRequestRole.EVENT
    ]
    assert len(events) == 1
    return events[0]


def _builder_chain_call_sites(*, verb_call_site=None, dispatch_method="execute"):
    """Split-statement chain: builder on line 1, dispatch on line 3."""
    call_sites = [
        make_call_site(
            method_name="url",
            receiver_type="okhttp3.Request$Builder",
            argument_expr=['"/api/data"'],
            start_line=1,
            start_column=5,
            end_line=1,
            end_column=30,
        ),
    ]
    if verb_call_site is not None:
        call_sites.append(verb_call_site)
    call_sites.append(
        make_call_site(
            method_name=dispatch_method,
            receiver_type="okhttp3.Call",
            start_line=3,
            start_column=5,
            end_line=3,
            end_column=25,
        )
    )
    return call_sites


# ── Verb-less visible builder chains dispatch GET ──


def test_verbless_builder_chain_defaults_execute_to_get() -> None:
    grouping = _classify_and_get_grouping(_builder_chain_call_sites())

    event = _single_event(grouping)
    assert event.http_classification.framework == HttpDispatchFramework.OKHTTP
    assert event.http_classification.http_method == "GET"
    assert event.http_classification.path == "/api/data"
    assert event.endpoint_candidate is not None
    assert event.endpoint_candidate.http_method == "GET"
    assert event.endpoint_candidate.path == "/api/data"


def test_verbless_builder_chain_defaults_enqueue_to_get() -> None:
    grouping = _classify_and_get_grouping(
        _builder_chain_call_sites(dispatch_method="enqueue")
    )

    event = _single_event(grouping)
    assert event.http_classification.http_method == "GET"
    assert event.http_classification.path == "/api/data"


def test_inline_verbless_builder_defaults_to_get() -> None:
    """client.newCall(new Request.Builder().url("/api/data").build()).execute()"""
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

    event = _single_event(grouping)
    assert event.http_classification.http_method == "GET"
    assert event.http_classification.path == "/api/data"


def test_dot_separated_builder_receiver_also_defaults_to_get() -> None:
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="url",
                receiver_type="okhttp3.Request.Builder",
                argument_expr=['"/api/data"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
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

    event = _single_event(grouping)
    assert event.http_classification.http_method == "GET"


# ── Explicit verbs are preserved, not overridden ──


def test_post_builder_keeps_post() -> None:
    grouping = _classify_and_get_grouping(
        _builder_chain_call_sites(
            verb_call_site=make_call_site(
                method_name="post",
                receiver_type="okhttp3.Request$Builder",
                argument_expr=["body"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=42,
            )
        )
    )

    event = _single_event(grouping)
    assert event.http_classification.http_method == "POST"


def test_head_builder_resolves_head_not_get() -> None:
    grouping = _classify_and_get_grouping(
        _builder_chain_call_sites(
            verb_call_site=make_call_site(
                method_name="head",
                receiver_type="okhttp3.Request$Builder",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=38,
            )
        )
    )

    event = _single_event(grouping)
    assert event.http_classification.http_method == "HEAD"


def test_method_with_literal_verb_resolves_that_verb() -> None:
    grouping = _classify_and_get_grouping(
        _builder_chain_call_sites(
            verb_call_site=make_call_site(
                method_name="method",
                receiver_type="okhttp3.Request$Builder",
                argument_expr=['"DELETE"', "body"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=50,
            )
        )
    )

    event = _single_event(grouping)
    assert event.http_classification.http_method == "DELETE"


# ── Verb-ambiguous chains stay UNKNOWN ──


def test_dynamic_method_verb_suppresses_get_default() -> None:
    """method(verb, body) with a non-literal verb keeps the event UNKNOWN."""
    grouping = _classify_and_get_grouping(
        _builder_chain_call_sites(
            verb_call_site=make_call_site(
                method_name="method",
                receiver_type="okhttp3.Request$Builder",
                argument_expr=["verb", "body"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=48,
            )
        )
    )

    event = _single_event(grouping)
    assert event.http_classification.http_method == "UNKNOWN"
    assert event.http_classification.path == "/api/data"


def test_request_copy_builder_suppresses_get_default() -> None:
    """template.newBuilder().url(...) inherits the template's verb."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="newBuilder",
                receiver_type="okhttp3.Request",
                receiver_expr="template",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=28,
            ),
            make_call_site(
                method_name="url",
                receiver_type="okhttp3.Request$Builder",
                argument_expr=['"/api/data"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=50,
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

    event = _single_event(grouping)
    assert event.http_classification.http_method == "UNKNOWN"
    assert event.http_classification.path == "/api/data"


def test_receiverless_head_in_chain_resolves_head_not_get() -> None:
    """A receiverless .head() recovered via builder-evidence inference must
    carry HEAD; an unmapped verb link would let the GET default misfire."""
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
            # CLDK omitted the receiver type on the chained verb call.
            make_call_site(
                method_name="head",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=38,
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

    event = _single_event(grouping)
    assert event.http_classification.http_method == "HEAD"
    assert event.http_classification.path == "/api/data"


def test_receiverless_dynamic_method_suppresses_get_default() -> None:
    """A receiverless .method(verb, body) inherits the builder receiver from
    chain evidence, so it still suppresses the GET default."""
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
                method_name="method",
                argument_expr=["verb", "body"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=48,
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

    event = _single_event(grouping)
    assert event.http_classification.http_method == "UNKNOWN"
    assert event.http_classification.path == "/api/data"


def test_invisible_builder_stays_unknown() -> None:
    """A dispatch with no visible builder chain cannot assume GET."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="newCall",
                receiver_type="okhttp3.OkHttpClient",
                argument_expr=["request"],
                argument_types=["okhttp3.Request"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=28,
            ),
            make_call_site(
                method_name="execute",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=38,
            ),
        ]
    )

    event = _single_event(grouping)
    assert event.http_classification.http_method == "UNKNOWN"


def test_url_from_mockwebserver_does_not_trigger_get_default() -> None:
    """MockWebServer.url(...) builds a URL, not a Request.Builder chain; the
    request's verb may live in an invisible helper."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="url",
                receiver_type="okhttp3.mockwebserver.MockWebServer",
                receiver_expr="server",
                argument_expr=['"/api/data"'],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
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

    event = _single_event(grouping)
    assert event.http_classification.http_method == "UNKNOWN"
    assert event.http_classification.path == ""


def test_httpurl_copy_builder_does_not_suppress_get_default() -> None:
    """HttpUrl.newBuilder() manipulates the URL only; a verb-less
    Request.Builder chain alongside it still dispatches GET."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="newBuilder",
                receiver_type="okhttp3.HttpUrl",
                receiver_expr="baseUrl",
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=27,
            ),
            make_call_site(
                method_name="url",
                receiver_type="okhttp3.Request$Builder",
                argument_expr=['"/api/data"'],
                start_line=2,
                start_column=5,
                end_line=2,
                end_column=30,
            ),
            make_call_site(
                method_name="execute",
                receiver_type="okhttp3.Call",
                start_line=4,
                start_column=5,
                end_line=4,
                end_column=25,
            ),
        ]
    )

    event = _single_event(grouping)
    assert event.http_classification.http_method == "GET"
    assert event.http_classification.path == "/api/data"


# ── newBuilder registration is scoped to okhttp3.Request ──


def _classification_for_single_call_site(call_site):
    grouping = _classify_and_get_grouping([call_site])
    return grouping.nodes[0].http_classification


def test_request_newbuilder_classifies_as_builder() -> None:
    classification = _classification_for_single_call_site(
        make_call_site(
            method_name="newBuilder",
            receiver_type="okhttp3.Request",
            receiver_expr="template",
            start_line=1,
            start_column=5,
            end_line=1,
            end_column=28,
        )
    )
    assert classification is not None
    assert classification.request_role == HttpRequestRole.BUILDER
    assert classification.framework == HttpDispatchFramework.OKHTTP


def test_non_request_newbuilder_receivers_are_not_request_builders() -> None:
    """Client configuration, URL manipulation, and response copying are not
    request-building activity."""
    for receiver_type in (
        "okhttp3.OkHttpClient",
        "okhttp3.HttpUrl",
        "okhttp3.Response",
    ):
        classification = _classification_for_single_call_site(
            make_call_site(
                method_name="newBuilder",
                receiver_type=receiver_type,
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=28,
            )
        )
        assert classification is None, receiver_type


def test_requestbody_receiver_does_not_match_request_scoped_rule() -> None:
    """Segment-aware prefix matching keeps okhttp3.RequestBody out of the
    okhttp3.Request-scoped rule."""
    classification = _classification_for_single_call_site(
        make_call_site(
            method_name="newBuilder",
            receiver_type="okhttp3.RequestBody",
            start_line=1,
            start_column=5,
            end_line=1,
            end_column=28,
        )
    )
    assert classification is None


# ── The default never crosses frameworks ──


def test_apache_execute_with_unresolved_verb_stays_unknown() -> None:
    """An Apache execute without verb evidence is untouched by the OkHttp rule."""
    grouping = _classify_and_get_grouping(
        [
            make_call_site(
                method_name="execute",
                receiver_type="org.apache.http.impl.client.CloseableHttpClient",
                argument_expr=["request"],
                argument_types=["org.apache.http.client.methods.HttpUriRequest"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
            ),
        ]
    )

    event = _single_event(grouping)
    assert (
        event.http_classification.framework == HttpDispatchFramework.APACHE_HTTPCLIENT
    )
    assert event.http_classification.http_method == "UNKNOWN"
