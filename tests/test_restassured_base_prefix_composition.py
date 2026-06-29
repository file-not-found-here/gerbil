"""REST Assured baseUri/basePath chain setters compose onto event request paths,
mirroring RequestSpecificationImpl's baseUri + basePath + path target assembly."""

from __future__ import annotations

from cldk.models.java import JImport

from gerbil.analysis.http import classification as http_classification
from gerbil.analysis.properties.endpoint.coverage import build_endpoint_coverage_summary
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.schema import (
    ApplicationEndpoint,
    HttpAnalysis,
    HttpClassification,
    HttpDispatchFramework,
    HttpRequestRole,
    LifecyclePhase,
    MethodIdentity,
    TestClassAnalysis as ModelClassAnalysis,
    TestMethodAnalysis as ModelMethodAnalysis,
)
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    classify_runtime_view_for_testing,
    make_call_site,
    make_callable,
    make_field,
    make_type,
    make_variable_declaration,
)
from tests.fake_java_analysis import FakeJavaAnalysis

_REQUEST_SPEC = "io.restassured.specification.RequestSpecification"


def _chain_call_sites(links, *, start_line=1):
    """Build one fluent chain: given().<links...> sharing a root span."""
    call_sites = [
        make_call_site(
            method_name="given",
            receiver_type="io.restassured.RestAssured",
            start_line=start_line,
            start_column=5,
            end_line=start_line,
            end_column=12,
        )
    ]
    end_column = 12
    for method_name, argument_exprs in links:
        end_column += 25
        call_sites.append(
            make_call_site(
                method_name=method_name,
                receiver_type=_REQUEST_SPEC,
                argument_expr=argument_exprs or [],
                start_line=start_line,
                start_column=5,
                end_line=start_line,
                end_column=end_column,
            )
        )
    return call_sites


def _classified_runtime_view(
    call_sites,
    *,
    variable_declarations=None,
    static_import_index: StaticImportIndex | None = None,
    analysis: FakeJavaAnalysis | None = None,
):
    owner = MethodRef(
        defining_class_name="example.ApiTest",
        method_signature="testOwner()",
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=build_call_site_grouping(call_sites),
                method_details=make_callable(
                    call_sites=call_sites,
                    variable_declarations=variable_declarations,
                ),
            )
        ]
    )
    if static_import_index is None and analysis is None:
        classify_runtime_view_for_testing(runtime_view)
    else:
        http_classification.classify_http_on_runtime_view(
            runtime_view=runtime_view,
            receiver_resolver=build_runtime_receiver_resolver_for_testing(
                runtime_view,
                analysis=analysis,
                get_static_import_index_for_class=(
                    lambda _class_name: static_import_index or StaticImportIndex.EMPTY
                ),
            ),
        )
    return runtime_view


def _events_sorted(runtime_view):
    return sorted(
        (
            node
            for entry in runtime_view.entries
            for node in entry.grouping.nodes
            if node.http_classification is not None
            and node.http_classification.request_role == HttpRequestRole.EVENT
        ),
        key=lambda node: node.span.start,
    )


def _single_event(runtime_view):
    events = _events_sorted(runtime_view)
    assert len(events) == 1
    return events[0]


# ---------------------------------------------------------------------------
# Base prefixes compose onto the request path
# ---------------------------------------------------------------------------


def test_base_path_prepends_to_request_path() -> None:
    runtime_view = _classified_runtime_view(
        _chain_call_sites(
            [
                ("basePath", ['"/api"']),
                ("when", None),
                ("get", ['"/owners/1"']),
            ]
        )
    )

    event = _single_event(runtime_view)
    classification = event.http_classification
    assert classification.http_method == "GET"
    assert classification.path == "/api/owners/1"
    assert classification.path_truncated is False
    assert event.endpoint_candidate is not None
    assert event.endpoint_candidate.path == "/api/owners/1"
    assert any(
        source.method_name == "basePath" and source.contributed_properties == ["path"]
        for source in classification.correlated_builder_sources
    )


def test_base_uri_path_component_precedes_base_path() -> None:
    runtime_view = _classified_runtime_view(
        _chain_call_sites(
            [
                ("baseUri", ['"http://localhost:8080/rest"']),
                ("basePath", ['"/api"']),
                ("get", ['"/owners/1"']),
            ]
        )
    )

    event = _single_event(runtime_view)
    assert event.http_classification.path == "http://localhost:8080/rest/api/owners/1"


def test_composed_parts_join_without_double_slashes() -> None:
    runtime_view = _classified_runtime_view(
        _chain_call_sites(
            [
                ("basePath", ['"/api/"']),
                ("get", ['"/owners/1"']),
            ]
        )
    )

    assert _single_event(runtime_view).http_classification.path == "/api/owners/1"


def test_repeated_base_path_setter_replaces_previous_value() -> None:
    runtime_view = _classified_runtime_view(
        _chain_call_sites(
            [
                ("basePath", ['"/v1"']),
                ("basePath", ['"/v2"']),
                ("get", ['"/owners/1"']),
            ]
        )
    )

    assert _single_event(runtime_view).http_classification.path == "/v2/owners/1"


def test_verb_only_event_takes_base_path_as_full_path() -> None:
    runtime_view = _classified_runtime_view(
        _chain_call_sites(
            [
                ("basePath", ['"/api"']),
                ("when", None),
                ("get", None),
            ]
        )
    )

    event = _single_event(runtime_view)
    assert event.http_classification.http_method == "GET"
    assert event.http_classification.path == "/api"
    assert event.endpoint_candidate is not None
    assert event.endpoint_candidate.path == "/api"


# ---------------------------------------------------------------------------
# Fully qualified request paths bypass the base components
# ---------------------------------------------------------------------------


def test_absolute_request_path_ignores_base_prefixes() -> None:
    runtime_view = _classified_runtime_view(
        _chain_call_sites(
            [
                ("basePath", ['"/api"']),
                ("get", ['"http://localhost:8080/owners/1"']),
            ]
        )
    )

    event = _single_event(runtime_view)
    assert event.http_classification.path == "http://localhost:8080/owners/1"


# ---------------------------------------------------------------------------
# Concatenation-truncated prefixes only compose as the final component
# ---------------------------------------------------------------------------


def test_truncated_base_uri_before_request_path_skips_composition() -> None:
    # The appended value is statically unknown, so joining the truncated
    # prefix directly onto the request path would fabricate adjacency.
    runtime_view = _classified_runtime_view(
        _chain_call_sites(
            [
                ("baseUri", ['"http://localhost:8080/svc/" + tenant']),
                ("get", ['"/owners/1"']),
            ]
        )
    )

    event = _single_event(runtime_view)
    assert event.http_classification.path == "/owners/1"
    assert event.http_classification.path_truncated is False


def test_truncated_base_path_as_final_component_composes_with_flag() -> None:
    runtime_view = _classified_runtime_view(
        _chain_call_sites(
            [
                ("basePath", ['"/api/" + tenant']),
                ("get", None),
            ]
        )
    )

    event = _single_event(runtime_view)
    assert event.http_classification.path == "/api/"
    assert event.http_classification.path_truncated is True
    assert event.endpoint_candidate is not None
    assert event.endpoint_candidate.path_truncated is True


# ---------------------------------------------------------------------------
# Bare single-segment basePath literals (no leading slash)
# ---------------------------------------------------------------------------


def _extract(argument_exprs, method_name):
    return http_classification._extract_path(argument_exprs, method_name)


def test_base_path_accepts_bare_single_segment_literal() -> None:
    assert _extract(['"api"'], "basePath") == ("/api", False)
    assert _extract(['"v2"'], "basePath") == ("/v2", False)
    # A leading slash or multi-segment relative value already worked.
    assert _extract(['"/api"'], "basePath") == ("/api", False)
    assert _extract(['"api/v2"'], "basePath") == ("/api/v2", False)


def test_base_uri_bare_token_is_not_extracted_as_path() -> None:
    # baseUri's bare token is typically a host, not a path segment, so the
    # single-segment relaxation is deliberately scoped out for baseUri.
    assert _extract(['"localhost"'], "baseUri") == ("", False)


def test_generic_verb_bare_token_still_requires_separator() -> None:
    # The relaxation is scoped to basePath; normal verbs keep the slash
    # requirement so arbitrary tokens are not treated as paths.
    assert _extract(['"api"'], "get") == ("", False)
    assert _extract(['"api"'], "post") == ("", False)


def test_base_path_single_segment_rejection_guards_preserved() -> None:
    # Whitespace, brace/bracket starts, and scheme-like tokens are still
    # rejected with the slash requirement relaxed.
    assert _extract(['"api v2"'], "basePath") == ("", False)
    assert _extract(['"{tenant}"'], "basePath") == ("", False)
    assert _extract(['"mailto:x"'], "basePath") == ("", False)


def test_bare_base_path_composes_onto_request_path() -> None:
    runtime_view = _classified_runtime_view(
        _chain_call_sites(
            [
                ("basePath", ['"api"']),
                ("when", None),
                ("get", ['"/owners/1"']),
            ]
        )
    )

    event = _single_event(runtime_view)
    assert event.http_classification.path == "/api/owners/1"
    assert event.endpoint_candidate is not None
    assert event.endpoint_candidate.path == "/api/owners/1"


# ---------------------------------------------------------------------------
# Composition is REST Assured-specific
# ---------------------------------------------------------------------------


def test_non_rest_assured_builder_records_no_base_prefix() -> None:
    base_prefixes: dict = {}
    http_classification._record_base_prefix(
        base_prefixes,
        "basePath",
        3,
        HttpClassification(
            http_method="UNKNOWN",
            path="/app",
            framework=HttpDispatchFramework.MOCKMVC,
            request_role=HttpRequestRole.BUILDER,
        ),
        overwrite=True,
    )

    assert base_prefixes == {}


def test_composition_requires_rest_assured_event() -> None:
    event = HttpClassification(
        http_method="GET",
        path="/owners/1",
        framework=HttpDispatchFramework.MOCKMVC,
        request_role=HttpRequestRole.EVENT,
    )
    http_classification._compose_base_prefixes_into_event(
        event,
        {
            "basepath": http_classification._BasePrefixEvidence(
                path="/api",
                truncated=False,
                method_name="basePath",
                start_line=1,
            )
        },
    )

    assert event.path == "/owners/1"


# ---------------------------------------------------------------------------
# Cross-chain correlation keeps prefix semantics
# ---------------------------------------------------------------------------


def test_queued_base_path_builder_composes_into_pathless_event() -> None:
    spec_chain = _chain_call_sites([("basePath", ['"/api"'])], start_line=1)
    event_chain = _chain_call_sites([("get", None)], start_line=2)
    runtime_view = _classified_runtime_view(spec_chain + event_chain)

    event = _single_event(runtime_view)
    assert event.http_classification.http_method == "GET"
    assert event.http_classification.path == "/api"


def test_queued_base_path_composes_into_explicit_path_event() -> None:
    # The split-spec pattern:
    #   RequestSpecification spec = given().basePath("/api");
    #   spec.get("/owners/1");
    # The queued prefix composes onto the event's own path.
    spec_chain = _chain_call_sites([("basePath", ['"/api"'])], start_line=1)
    event_chain = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec",
            argument_expr=['"/owners/1"'],
            start_line=2,
            start_column=5,
            end_line=2,
            end_column=30,
        )
    ]
    runtime_view = _classified_runtime_view(spec_chain + event_chain)

    event = _single_event(runtime_view)
    assert event.http_classification.http_method == "GET"
    assert event.http_classification.path == "/api/owners/1"


def test_same_chain_base_path_wins_over_queued_builder_chain() -> None:
    queued_chain = _chain_call_sites([("basePath", ['"/v1"'])], start_line=1)
    event_chain = _chain_call_sites(
        [("basePath", ['"/v2"']), ("get", None)], start_line=2
    )
    runtime_view = _classified_runtime_view(queued_chain + event_chain)

    event = _single_event(runtime_view)
    assert event.http_classification.path == "/v2"


def test_same_chain_base_path_wins_over_queued_prefix_with_explicit_path() -> None:
    queued_chain = _chain_call_sites([("basePath", ['"/v1"'])], start_line=1)
    event_chain = _chain_call_sites(
        [("basePath", ['"/v2"']), ("get", ['"/owners/1"'])], start_line=2
    )
    runtime_view = _classified_runtime_view(queued_chain + event_chain)

    event = _single_event(runtime_view)
    assert event.http_classification.path == "/v2/owners/1"


def test_consumed_queued_prefix_does_not_leak_to_later_event() -> None:
    # The queued prefix is single-shot: the first REST Assured event consumes
    # it; a later unrelated event must not inherit it.
    spec_chain = _chain_call_sites([("basePath", ['"/api"'])], start_line=1)
    first_event = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec",
            argument_expr=['"/first"'],
            start_line=2,
            start_column=5,
            end_line=2,
            end_column=30,
        )
    ]
    second_event = _chain_call_sites([("get", ['"/second"'])], start_line=3)
    runtime_view = _classified_runtime_view(spec_chain + first_event + second_event)

    events = _events_sorted(runtime_view)
    assert [event.http_classification.path for event in events] == [
        "/api/first",
        "/second",
    ]


def test_queued_prefix_with_other_builders_claims_whole_spec_no_leak() -> None:
    # A queued spec carrying both a prefix and a header is claimed atomically by
    # the consuming event; neither the prefix nor the header strands to leak
    # into a later unrelated event.
    spec_chain = _chain_call_sites(
        [("basePath", ['"/api"']), ("header", ['"X-Token"', "token"])],
        start_line=1,
    )
    first_event = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec",
            argument_expr=['"/owners/1"'],
            start_line=5,
            start_column=5,
            end_line=5,
            end_column=30,
        )
    ]
    second_event = _chain_call_sites([("get", None)], start_line=6)
    runtime_view = _classified_runtime_view(spec_chain + first_event + second_event)

    events = _events_sorted(runtime_view)
    assert [
        (event.http_classification.path, event.http_classification.header_names)
        for event in events
    ] == [("/api/owners/1", ["x-token"]), ("", [])]


def test_foreign_framework_queued_builder_does_not_leak_into_event() -> None:
    # A java.net.http builder queued ahead of a REST Assured spec belongs to its
    # own framework; claiming the prefix must not merge its header into the REST
    # Assured event.
    foreign_chain = [
        make_call_site(
            method_name="newBuilder",
            receiver_type="java.net.http.HttpRequest",
            start_line=1,
            start_column=5,
            end_line=1,
            end_column=30,
        ),
        make_call_site(
            method_name="header",
            receiver_type="java.net.http.HttpRequest.Builder",
            argument_expr=['"X-Orphan"', "value"],
            start_line=1,
            start_column=5,
            end_line=1,
            end_column=55,
        ),
    ]
    spec_chain = _chain_call_sites([("basePath", ['"/api"'])], start_line=2)
    event_chain = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec",
            argument_expr=['"/owners/1"'],
            start_line=3,
            start_column=5,
            end_line=3,
            end_column=30,
        )
    ]
    runtime_view = _classified_runtime_view(foreign_chain + spec_chain + event_chain)

    event = _single_event(runtime_view)
    assert event.http_classification.path == "/api/owners/1"
    assert event.http_classification.header_names == []


def test_multiple_queued_specs_match_variable_backed_spec_names() -> None:
    spec1_chain = _chain_call_sites(
        [("header", ['"X-One"', "one"])],
        start_line=1,
    )
    spec2_chain = _chain_call_sites([("basePath", ['"/api"'])], start_line=2)
    spec1_event = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec1",
            argument_expr=['"/one"'],
            start_line=4,
            start_column=5,
            end_line=4,
            end_column=25,
        )
    ]
    spec2_event = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec2",
            argument_expr=['"/two"'],
            start_line=5,
            start_column=5,
            end_line=5,
            end_column=25,
        )
    ]
    runtime_view = _classified_runtime_view(
        spec1_chain + spec2_chain + spec1_event + spec2_event,
        variable_declarations=[
            make_variable_declaration(
                name="spec1",
                type_name=_REQUEST_SPEC,
                initializer='given().header("X-One", one)',
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=80,
            ),
            make_variable_declaration(
                name="spec2",
                type_name=_REQUEST_SPEC,
                initializer='given().basePath("/api")',
                start_line=2,
                start_column=1,
                end_line=2,
                end_column=80,
            ),
        ],
    )

    events = _events_sorted(runtime_view)
    assert [
        (event.http_classification.path, event.http_classification.header_names)
        for event in events
    ] == [("/one", ["x-one"]), ("/api/two", [])]


def test_named_queued_prefix_waits_for_matching_spec_event() -> None:
    spec2_chain = _chain_call_sites([("basePath", ['"/api"'])], start_line=1)
    spec1_event = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec1",
            argument_expr=['"/one"'],
            start_line=2,
            start_column=5,
            end_line=2,
            end_column=25,
        )
    ]
    spec2_event = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec2",
            argument_expr=['"/two"'],
            start_line=3,
            start_column=5,
            end_line=3,
            end_column=25,
        )
    ]
    runtime_view = _classified_runtime_view(
        spec2_chain + spec1_event + spec2_event,
        variable_declarations=[
            make_variable_declaration(
                name="spec2",
                type_name=_REQUEST_SPEC,
                initializer='given().basePath("/api")',
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=80,
            )
        ],
    )

    events = _events_sorted(runtime_view)
    assert [event.http_classification.path for event in events] == [
        "/one",
        "/api/two",
    ]


def test_named_queued_prefix_is_not_claimed_by_static_event() -> None:
    spec_chain = _chain_call_sites([("basePath", ['"/api"'])], start_line=1)
    static_event = [
        make_call_site(
            method_name="get",
            argument_expr=['"/health"'],
            start_line=3,
            start_column=5,
            end_line=3,
            end_column=20,
        )
    ]
    spec_event = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec",
            argument_expr=['"/owners"'],
            start_line=4,
            start_column=5,
            end_line=4,
            end_column=25,
        )
    ]
    runtime_view = _classified_runtime_view(
        spec_chain + static_event + spec_event,
        variable_declarations=[
            make_variable_declaration(
                name="spec",
                type_name=_REQUEST_SPEC,
                initializer='given().basePath("/api")',
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=80,
            )
        ],
        static_import_index=StaticImportIndex.from_import_entries(
            [
                JImport(
                    path="io.restassured.RestAssured.get",
                    is_static=True,
                    is_wildcard=False,
                )
            ]
        ),
    )

    events = _events_sorted(runtime_view)
    assert [event.http_classification.path for event in events] == [
        "/health",
        "/api/owners",
    ]


def test_multiple_queued_specs_without_names_fall_back_to_one_group_fifo() -> None:
    spec1_chain = _chain_call_sites(
        [("header", ['"X-One"', "one"])],
        start_line=1,
    )
    spec2_chain = _chain_call_sites([("basePath", ['"/api"'])], start_line=2)
    spec1_event = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec1",
            argument_expr=['"/one"'],
            start_line=4,
            start_column=5,
            end_line=4,
            end_column=25,
        )
    ]
    spec2_event = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec2",
            argument_expr=['"/two"'],
            start_line=5,
            start_column=5,
            end_line=5,
            end_column=25,
        )
    ]
    runtime_view = _classified_runtime_view(
        spec1_chain + spec2_chain + spec1_event + spec2_event
    )

    events = _events_sorted(runtime_view)
    assert [
        (event.http_classification.path, event.http_classification.header_names)
        for event in events
    ] == [("/one", ["x-one"]), ("/api/two", [])]


def test_queued_spec_mutator_statements_match_event_receiver_name() -> None:
    spec_prefix = [
        make_call_site(
            method_name="basePath",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec",
            argument_expr=['"/api"'],
            start_line=1,
            start_column=5,
            end_line=1,
            end_column=28,
        )
    ]
    spec_header = [
        make_call_site(
            method_name="header",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec",
            argument_expr=['"X-Token"', "token"],
            start_line=2,
            start_column=5,
            end_line=2,
            end_column=40,
        )
    ]
    spec_event = [
        make_call_site(
            method_name="get",
            receiver_type=_REQUEST_SPEC,
            receiver_expr="spec",
            argument_expr=['"/owners/1"'],
            start_line=3,
            start_column=5,
            end_line=3,
            end_column=30,
        )
    ]
    runtime_view = _classified_runtime_view(spec_prefix + spec_header + spec_event)

    event = _single_event(runtime_view)
    assert event.http_classification.path == "/api/owners/1"
    assert event.http_classification.header_names == ["x-token"]


# ---------------------------------------------------------------------------
# Composed paths cover prefixed endpoint templates
# ---------------------------------------------------------------------------


def test_composed_base_path_covers_prefixed_endpoint_template() -> None:
    """A test dispatching basePath("/api") + get("/owners/1") exercises the
    endpoint extracted as /api/owners/{id}."""
    runtime_view = _classified_runtime_view(
        _chain_call_sites(
            [
                ("basePath", ['"/api"']),
                ("when", None),
                ("get", ['"/owners/1"']),
            ]
        )
    )
    interactions = http_classification.build_output_http_request_interactions(
        runtime_view=runtime_view
    )

    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="spring",
                http_method="GET",
                path_template="/api/owners/{id}",
                declaring_class_name="example.OwnerController",
                declaring_method_signature="getOwner()",
            )
        ],
        test_class_analyses=[
            ModelClassAnalysis(
                qualified_class_name="example.ApiTest",
                test_method_analyses=[
                    ModelMethodAnalysis(
                        identity=MethodIdentity(
                            defining_class_name="example.ApiTest",
                            method_signature="testOwner()",
                            method_declaration="void testOwner()",
                        ),
                        http=HttpAnalysis(request_interactions=interactions),
                    )
                ],
            )
        ],
    )

    assert coverage.covered_endpoint_count == 1
    entry = coverage.endpoints[0]
    assert entry.is_covered
    assert [
        reference.method_signature for reference in entry.covering_test_methods
    ] == ["testOwner()"]


# ---------------------------------------------------------------------------
# Static RestAssured.baseURI/basePath field assignments seed default prefixes
# ---------------------------------------------------------------------------

_REST_ASSURED_IMPORT = JImport(
    path="io.restassured.RestAssured", is_static=False, is_wildcard=False
)
_REST_ASSURED_STATIC_WILDCARD_IMPORT = JImport(
    path="io.restassured.RestAssured.*", is_static=True, is_wildcard=True
)


def _static_config_analysis(
    code_by_method: dict[str, str],
    *,
    class_name: str = "example.ApiTest",
    imports: list[JImport] | None = None,
    extra_code_by_class: dict[str, dict[str, str]] | None = None,
    classes: dict | None = None,
) -> FakeJavaAnalysis:
    methods_by_class = {
        class_name: {
            signature: make_callable(signature=signature, code=code)
            for signature, code in code_by_method.items()
        }
    }
    for other_class, code_by_signature in (extra_code_by_class or {}).items():
        methods_by_class[other_class] = {
            signature: make_callable(signature=signature, code=code)
            for signature, code in code_by_signature.items()
        }
    resolved_imports = [_REST_ASSURED_IMPORT] if imports is None else imports
    return FakeJavaAnalysis(
        classes=classes or {},
        methods_by_class=methods_by_class,
        java_files={name: f"{name}.java" for name in methods_by_class},
        import_declarations_by_file={
            f"{name}.java": list(resolved_imports) for name in methods_by_class
        },
    )


def _event_chain(path: str | None = '"/owners/1"'):
    return _chain_call_sites(
        [("when", None), ("get", [path] if path is not None else None)]
    )


def test_static_base_path_assignment_prepends_to_request_path() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {"setUp()": '{ RestAssured.basePath = "/api"; }'}
        ),
    )

    event = _single_event(runtime_view)
    assert event.http_classification.path == "/api/owners/1"
    assert event.endpoint_candidate is not None
    assert event.endpoint_candidate.path == "/api/owners/1"
    assert any(
        source.method_name == "RestAssured.basePath"
        and source.contributed_properties == ["path"]
        for source in event.http_classification.correlated_builder_sources
    )


def test_static_base_uri_path_component_precedes_static_base_path() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {
                "setUp()": (
                    '{ RestAssured.baseURI = "http://localhost:8080/rest"; '
                    'RestAssured.basePath = "/api"; }'
                )
            }
        ),
    )

    event = _single_event(runtime_view)
    assert event.http_classification.path == "http://localhost:8080/rest/api/owners/1"


def test_static_config_discovered_in_superclass_fixture() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {"testOwner()": "{}"},
            extra_code_by_class={
                "example.ServiceBase": {"setUp()": '{ RestAssured.basePath = "/api"; }'}
            },
            classes={
                "example.ApiTest": make_type(extends_list=["example.ServiceBase"])
            },
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/api/owners/1"


def test_chain_base_path_setter_replaces_static_default() -> None:
    runtime_view = _classified_runtime_view(
        _chain_call_sites([("basePath", ['"/v2"']), ("get", ['"/owners/1"'])]),
        analysis=_static_config_analysis(
            {"setUp()": '{ RestAssured.basePath = "/v1"; }'}
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/v2/owners/1"


def test_repeated_equal_static_assignments_still_apply() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {
                "setUp()": '{ RestAssured.basePath = "/api"; }',
                "reset()": '{ RestAssured.basePath = "/api"; }',
            }
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/api/owners/1"


def test_conflicting_static_assignments_exclude_the_field() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {
                "setUp()": '{ RestAssured.basePath = "/v1"; }',
                "other()": '{ RestAssured.basePath = "/v2"; }',
            }
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/owners/1"


def test_dynamic_assignment_excludes_only_that_field() -> None:
    # The openrouteservice shape: baseURI is dynamic while basePath is a
    # constant; each field contributes independently.
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {
                "setUp()": (
                    "{ RestAssured.baseURI = testRestTemplate.getRootUri(); "
                    'RestAssured.basePath = "/ors/v2"; }'
                )
            }
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/ors/v2/owners/1"


def test_host_only_static_base_uri_contributes_nothing() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {"setUp()": '{ RestAssured.baseURI = "http://localhost:8080"; }'}
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/owners/1"


def test_external_static_base_uri_keeps_authority() -> None:
    # A non-local base URI keeps its authority so coverage can classify the
    # request as external instead of crediting a local endpoint.
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {"setUp()": '{ RestAssured.baseURI = "https://api.example.com/v1"; }'}
        ),
    )

    assert (
        _single_event(runtime_view).http_classification.path
        == "https://api.example.com/v1/owners/1"
    )


def test_absolute_request_path_bypasses_static_config() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain('"http://localhost:8080/owners/1"'),
        analysis=_static_config_analysis(
            {"setUp()": '{ RestAssured.basePath = "/api"; }'}
        ),
    )

    assert (
        _single_event(runtime_view).http_classification.path
        == "http://localhost:8080/owners/1"
    )


def test_verb_only_event_takes_static_base_path_as_full_path() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(None),
        analysis=_static_config_analysis(
            {"setUp()": '{ RestAssured.basePath = "/api"; }'}
        ),
    )

    event = _single_event(runtime_view)
    assert event.http_classification.http_method == "GET"
    assert event.http_classification.path == "/api"


def test_bare_assignment_with_static_wildcard_import_applies() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {"setUp()": '{ basePath = "/api"; }'},
            imports=[_REST_ASSURED_STATIC_WILDCARD_IMPORT],
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/api/owners/1"


def test_bare_assignment_without_static_import_is_ignored() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis({"setUp()": '{ basePath = "/api"; }'}),
    )

    assert _single_event(runtime_view).http_classification.path == "/owners/1"


def test_local_variable_named_base_path_is_not_config() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {"setUp()": '{ String basePath = "/x"; use(basePath); }'},
            imports=[_REST_ASSURED_STATIC_WILDCARD_IMPORT],
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/owners/1"


def test_commented_out_assignment_is_ignored() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {
                "setUp()": (
                    '{\n// RestAssured.basePath = "/old";\n'
                    '/* RestAssured.basePath = "/block"; */\n}'
                )
            }
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/owners/1"


def test_simple_name_without_rest_assured_imports_is_ignored() -> None:
    # A same-package custom RestAssured helper class must not register; only
    # files importing real io.restassured types are credible.
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {"setUp()": '{ RestAssured.basePath = "/api"; }'},
            imports=[],
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/owners/1"


def test_fully_qualified_assignment_needs_no_import() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {"setUp()": '{ io.restassured.RestAssured.basePath = "/api"; }'},
            imports=[],
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/api/owners/1"


def test_constant_initializer_resolves_through_static_assignment() -> None:
    runtime_view = _classified_runtime_view(
        _event_chain(),
        analysis=_static_config_analysis(
            {"setUp()": "{ RestAssured.basePath = BASE; }"},
            classes={
                "example.ApiTest": make_type(
                    field_declarations=[
                        make_field(
                            type_name="java.lang.String",
                            variables=["BASE"],
                            modifiers=["static", "final"],
                            variable_initializers={"BASE": '"/api"'},
                        )
                    ]
                )
            },
        ),
    )

    assert _single_event(runtime_view).http_classification.path == "/api/owners/1"
