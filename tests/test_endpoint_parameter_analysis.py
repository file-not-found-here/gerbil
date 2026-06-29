from __future__ import annotations

from cldk.models.java import JCallable, JImport, JType

from gerbil.analysis.schema import (
    CallSiteOriginKind,
    HttpAnalysis,
    ApplicationEndpoint,
    EndpointCandidate,
    EndpointParameter,
    EndpointParameterSource,
    HttpCallSite,
    HttpDispatchFramework,
    HttpRequestInteraction,
    HttpRequestRole,
    LifecyclePhase,
    MethodIdentity,
    OriginContext,
    TestClassAnalysis,
    TestMethodAnalysis,
)
from gerbil.analysis.properties.endpoint.extraction import (
    _extract_jax_rs_endpoints,
    _extract_method_parameters,
    _extract_spring_endpoints,
    _reconcile_path_parameters,
    _template_path_variable_names,
    extract_application_endpoints,
)
from gerbil.analysis.properties.endpoint.parameter_analysis import (
    build_endpoint_parameter_coverage_summary,
)
from gerbil.analysis.shared.common_analysis import CommonAnalysis
from gerbil.analysis.shared.parameter_binding import (
    is_jax_rs_optionality_sibling_annotation,
)
from tests.cldk_factories import (
    make_callable,
    make_callable_parameter,
    make_field,
    make_import_declarations,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def _java_file(class_name: str) -> str:
    return f"src/main/java/{class_name.replace('.', '/')}.java"


# ---------------------------------------------------------------------------
# Spring parameter extraction
# ---------------------------------------------------------------------------


def test_spring_path_variable() -> None:
    method = make_callable(
        signature="getUser(java.lang.String)",
        annotations=['@GetMapping("/users/{id}")'],
        parameters=[
            make_callable_parameter(
                name="id",
                type_name="java.lang.String",
                annotations=['@PathVariable("id")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.PathVariable",
        "org.springframework.web.bind.annotation.GetMapping",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "id"
    assert result[0].source == EndpointParameterSource.PATH


def test_spring_request_param() -> None:
    method = make_callable(
        signature="search(java.lang.String)",
        annotations=['@GetMapping("/search")'],
        parameters=[
            make_callable_parameter(
                name="q",
                type_name="java.lang.String",
                annotations=['@RequestParam("name")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestParam",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "name"
    assert result[0].source == EndpointParameterSource.QUERY


def test_spring_request_body() -> None:
    method = make_callable(
        signature="create(example.UserDto)",
        annotations=['@PostMapping("/users")'],
        parameters=[
            make_callable_parameter(
                name="body",
                type_name="example.UserDto",
                annotations=["@RequestBody"],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestBody",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "body"
    assert result[0].source == EndpointParameterSource.BODY


def test_spring_request_header() -> None:
    method = make_callable(
        signature="doSomething(java.lang.String)",
        annotations=['@GetMapping("/action")'],
        parameters=[
            make_callable_parameter(
                name="token",
                type_name="java.lang.String",
                annotations=['@RequestHeader("X-Token")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestHeader",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "X-Token"
    assert result[0].source == EndpointParameterSource.HEADER


# ---------------------------------------------------------------------------
# JAX-RS parameter extraction
# ---------------------------------------------------------------------------


def test_jaxrs_path_param() -> None:
    method = make_callable(
        signature="getNamespace(java.lang.String)",
        annotations=["@GET", '@Path("/ns/{namespace}")'],
        parameters=[
            make_callable_parameter(
                name="ns",
                type_name="java.lang.String",
                annotations=['@PathParam("namespace")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.PathParam",
        "javax.ws.rs.GET",
        "javax.ws.rs.Path",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "namespace"
    assert result[0].source == EndpointParameterSource.PATH


def test_jaxrs_query_param() -> None:
    method = make_callable(
        signature="list(int)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="limit",
                type_name="int",
                annotations=['@QueryParam("limit")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.QueryParam",
        "javax.ws.rs.GET",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "limit"
    assert result[0].source == EndpointParameterSource.QUERY


def test_jaxrs_header_param() -> None:
    method = make_callable(
        signature="doAction(java.lang.String)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="auth",
                type_name="java.lang.String",
                annotations=['@HeaderParam("Authorization")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "jakarta.ws.rs.HeaderParam",
        "jakarta.ws.rs.GET",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "Authorization"
    assert result[0].source == EndpointParameterSource.HEADER


def test_jaxrs_form_param() -> None:
    method = make_callable(
        signature="submit(java.lang.String)",
        annotations=["@POST"],
        parameters=[
            make_callable_parameter(
                name="field",
                type_name="java.lang.String",
                annotations=['@FormParam("field")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.FormParam",
        "javax.ws.rs.POST",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "field"
    assert result[0].source == EndpointParameterSource.FORM


# ---------------------------------------------------------------------------
# JAX-RS request body (unannotated entity) synthesis
# ---------------------------------------------------------------------------


def test_jaxrs_body_synthesized_for_consuming_post() -> None:
    method = make_callable(
        signature="create(example.Dto)",
        annotations=["@POST", "@Consumes(APPLICATION_JSON)"],
        parameters=[
            make_callable_parameter(
                name="dto",
                type_name="example.Dto",
                annotations=["@Valid"],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.POST",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "dto"
    assert result[0].type == "example.Dto"
    assert result[0].source == EndpointParameterSource.BODY
    assert result[0].required is True
    # The body is a real typed argument, not a path/constraint placeholder.
    assert result[0].is_synthetic is False


def test_jaxrs_body_synthesized_without_consumes_on_body_verb() -> None:
    method = make_callable(
        signature="update(example.Dto)",
        annotations=["@PUT"],
        parameters=[
            make_callable_parameter(name="dto", type_name="example.Dto"),
        ],
    )
    imports = make_import_declarations("javax.ws.rs.PUT")
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].source == EndpointParameterSource.BODY


def test_jaxrs_body_synthesized_alongside_path_param() -> None:
    method = make_callable(
        signature="createOrUpdate(example.SourceName,example.SourceMeta)",
        annotations=["@PUT", '@Path("{source}")', "@Consumes(APPLICATION_JSON)"],
        parameters=[
            make_callable_parameter(
                name="name",
                type_name="example.SourceName",
                annotations=['@PathParam("source")'],
            ),
            make_callable_parameter(
                name="meta",
                type_name="example.SourceMeta",
                annotations=["@Valid"],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.PUT",
        "javax.ws.rs.Path",
        "javax.ws.rs.PathParam",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    sources = {param.source: param for param in result}
    assert sources.keys() == {
        EndpointParameterSource.PATH,
        EndpointParameterSource.BODY,
    }
    assert sources[EndpointParameterSource.PATH].name == "source"
    assert sources[EndpointParameterSource.BODY].name == "meta"


def test_jaxrs_body_excludes_suspended_async_response() -> None:
    method = make_callable(
        signature="create(example.BaseEvent,javax.ws.rs.container.AsyncResponse)",
        annotations=["@POST", "@Consumes(APPLICATION_JSON)"],
        parameters=[
            make_callable_parameter(
                name="event",
                type_name="example.BaseEvent",
                annotations=["@Valid", "@NotNull"],
            ),
            make_callable_parameter(
                name="asyncResponse",
                type_name="javax.ws.rs.container.AsyncResponse",
                annotations=["@Suspended"],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.POST",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "event"
    assert result[0].source == EndpointParameterSource.BODY


def test_jaxrs_body_excludes_context_param() -> None:
    method = make_callable(
        signature="create(example.Dto,javax.ws.rs.core.UriInfo)",
        annotations=["@POST", "@Consumes(APPLICATION_JSON)"],
        parameters=[
            make_callable_parameter(name="dto", type_name="example.Dto"),
            make_callable_parameter(
                name="uriInfo",
                type_name="javax.ws.rs.core.UriInfo",
                annotations=["@Context"],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.POST",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "dto"
    assert result[0].source == EndpointParameterSource.BODY


def test_jaxrs_body_excludes_servlet_request_by_type() -> None:
    method = make_callable(
        signature="create(example.Dto,javax.servlet.http.HttpServletRequest)",
        annotations=["@POST", "@Consumes(APPLICATION_JSON)"],
        parameters=[
            make_callable_parameter(name="dto", type_name="example.Dto"),
            # Servlet plumbing reaches handlers without a binding annotation.
            make_callable_parameter(
                name="request",
                type_name="javax.servlet.http.HttpServletRequest",
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.POST",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "dto"
    assert result[0].source == EndpointParameterSource.BODY


def test_jaxrs_optional_body_is_not_required() -> None:
    method = make_callable(
        signature="create(java.util.Optional)",
        annotations=["@POST", "@Consumes(APPLICATION_JSON)"],
        parameters=[
            make_callable_parameter(
                name="dto",
                type_name="java.util.Optional<example.Dto>",
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.POST",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].source == EndpointParameterSource.BODY
    assert result[0].required is False


def test_jaxrs_no_body_for_get_without_consumes() -> None:
    method = make_callable(
        signature="list(example.Filter)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(name="filter", type_name="example.Filter"),
        ],
    )
    imports = make_import_declarations("javax.ws.rs.GET")
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result == []


def test_jaxrs_no_body_when_multiple_entity_candidates() -> None:
    method = make_callable(
        signature="create(example.A,example.B)",
        annotations=["@POST", "@Consumes(APPLICATION_JSON)"],
        parameters=[
            make_callable_parameter(name="a", type_name="example.A"),
            make_callable_parameter(name="b", type_name="example.B"),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.POST",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result == []


# JAX-RS extension bindings are not the entity body


def test_jaxrs_form_data_param_is_form_not_body() -> None:
    method = make_callable(
        signature="upload(java.io.InputStream)",
        annotations=["@POST", "@Consumes(MULTIPART_FORM_DATA)"],
        parameters=[
            make_callable_parameter(
                name="file",
                type_name="java.io.InputStream",
                annotations=['@FormDataParam("file")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.POST",
        "javax.ws.rs.Consumes",
        "org.glassfish.jersey.media.multipart.FormDataParam",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "file"
    assert result[0].source == EndpointParameterSource.FORM


def test_jaxrs_dropwizard_auth_principal_is_not_body() -> None:
    method = make_callable(
        signature="delete(example.User)",
        annotations=["@POST", "@Consumes(APPLICATION_JSON)"],
        parameters=[
            make_callable_parameter(
                name="user",
                type_name="example.User",
                annotations=["@Auth"],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.POST",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result == []


def test_jaxrs_auth_principal_keeps_real_body_unambiguous() -> None:
    method = make_callable(
        signature="create(example.User,example.Dto)",
        annotations=["@POST", "@Consumes(APPLICATION_JSON)"],
        parameters=[
            make_callable_parameter(
                name="principal",
                type_name="example.User",
                annotations=["@Auth"],
            ),
            make_callable_parameter(
                name="dto",
                type_name="example.Dto",
                annotations=["@Valid"],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.POST",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "dto"
    assert result[0].source == EndpointParameterSource.BODY


def test_jaxrs_resteasy_multipart_form_aggregate_is_not_body() -> None:
    method = make_callable(
        signature="upload(example.UploadForm)",
        annotations=["@POST", "@Consumes(MULTIPART_FORM_DATA)"],
        parameters=[
            make_callable_parameter(
                name="form",
                type_name="example.UploadForm",
                annotations=["@MultipartForm"],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.POST",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result == []


# Class-level @Consumes enables bodies on non-standard verbs


def test_jaxrs_class_level_consumes_enables_delete_body() -> None:
    method = make_callable(
        signature="delete(example.Dto)",
        annotations=["@DELETE"],
        parameters=[
            make_callable_parameter(name="dto", type_name="example.Dto"),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.DELETE",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method,
        framework="jax-rs",
        class_imports=imports,
        class_annotations=["@Consumes(APPLICATION_JSON)"],
    )
    assert len(result) == 1
    assert result[0].name == "dto"
    assert result[0].source == EndpointParameterSource.BODY


def test_jaxrs_delete_without_consumes_has_no_body() -> None:
    method = make_callable(
        signature="delete(example.Dto)",
        annotations=["@DELETE"],
        parameters=[
            make_callable_parameter(name="dto", type_name="example.Dto"),
        ],
    )
    imports = make_import_declarations("javax.ws.rs.DELETE")
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result == []


def test_jaxrs_get_with_class_consumes_has_no_body() -> None:
    method = make_callable(
        signature="list(example.Filter)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(name="filter", type_name="example.Filter"),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.GET",
        "javax.ws.rs.Consumes",
    )
    result = _extract_method_parameters(
        method,
        framework="jax-rs",
        class_imports=imports,
        class_annotations=["@Consumes(APPLICATION_JSON)"],
    )
    assert result == []


def test_extract_application_endpoints_synthesizes_jaxrs_body() -> None:
    class_name = "example.SourceResource"
    java_file = _java_file(class_name)
    analysis = FakeJavaAnalysis(
        classes={
            class_name: make_type(annotations=['@Path("/api/v1/sources")']),
        },
        methods_by_class={
            class_name: {
                "createOrUpdate(example.SourceName,example.SourceMeta)": make_callable(
                    signature="createOrUpdate(example.SourceName,example.SourceMeta)",
                    annotations=[
                        "@PUT",
                        '@Path("{source}")',
                        "@Consumes(APPLICATION_JSON)",
                    ],
                    parameters=[
                        make_callable_parameter(
                            name="name",
                            type_name="example.SourceName",
                            annotations=['@PathParam("source")'],
                        ),
                        make_callable_parameter(
                            name="meta",
                            type_name="example.SourceMeta",
                            annotations=["@Valid"],
                        ),
                    ],
                ),
            },
        },
        java_files={class_name: java_file},
        import_declarations_by_file={
            java_file: [
                JImport(path=path, is_static=False, is_wildcard=False)
                for path in (
                    "javax.ws.rs.Path",
                    "javax.ws.rs.PUT",
                    "javax.ws.rs.PathParam",
                    "javax.ws.rs.Consumes",
                )
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[class_name],
    ).endpoints
    assert len(endpoints) == 1
    sources = {param.source for param in endpoints[0].parameters}
    assert sources == {EndpointParameterSource.PATH, EndpointParameterSource.BODY}
    assert endpoints[0].surface.parameter_count_by_source == {
        EndpointParameterSource.PATH: 1,
        EndpointParameterSource.BODY: 1,
    }


# ---------------------------------------------------------------------------
# Micronaut parameter extraction
# ---------------------------------------------------------------------------


def test_micronaut_path_variable() -> None:
    method = make_callable(
        signature="get(java.lang.String)",
        annotations=['@Get("/{id}")'],
        parameters=[
            make_callable_parameter(
                name="id",
                type_name="java.lang.String",
                annotations=['@PathVariable("id")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "io.micronaut.http.annotation.PathVariable",
        "io.micronaut.http.annotation.Get",
    )
    result = _extract_method_parameters(
        method, framework="micronaut", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "id"
    assert result[0].source == EndpointParameterSource.PATH


def test_micronaut_query_value() -> None:
    method = make_callable(
        signature="search(java.lang.String)",
        annotations=['@Get("/search")'],
        parameters=[
            make_callable_parameter(
                name="q",
                type_name="java.lang.String",
                annotations=['@QueryValue("q")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "io.micronaut.http.annotation.QueryValue",
        "io.micronaut.http.annotation.Get",
    )
    result = _extract_method_parameters(
        method, framework="micronaut", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "q"
    assert result[0].source == EndpointParameterSource.QUERY


def test_micronaut_header() -> None:
    method = make_callable(
        signature="action(java.lang.String)",
        annotations=['@Get("/action")'],
        parameters=[
            make_callable_parameter(
                name="accept",
                type_name="java.lang.String",
                annotations=['@Header("Accept")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "io.micronaut.http.annotation.Header",
        "io.micronaut.http.annotation.Get",
    )
    result = _extract_method_parameters(
        method, framework="micronaut", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "Accept"
    assert result[0].source == EndpointParameterSource.HEADER


def test_micronaut_body() -> None:
    method = make_callable(
        signature="create(example.Dto)",
        annotations=['@Post("/items")'],
        parameters=[
            make_callable_parameter(
                name="dto",
                type_name="example.Dto",
                annotations=["@Body"],
            ),
        ],
    )
    imports = make_import_declarations(
        "io.micronaut.http.annotation.Body",
        "io.micronaut.http.annotation.Post",
    )
    result = _extract_method_parameters(
        method, framework="micronaut", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "dto"
    assert result[0].source == EndpointParameterSource.BODY


# ---------------------------------------------------------------------------
# Annotation body name extraction vs fallback
# ---------------------------------------------------------------------------


def test_annotation_body_name_overrides_java_param_name() -> None:
    method = make_callable(
        signature="get(java.lang.String)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="id",
                type_name="java.lang.String",
                annotations=['@PathParam("userId")'],
            ),
        ],
    )
    imports = make_import_declarations("javax.ws.rs.PathParam", "javax.ws.rs.GET")
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result[0].name == "userId"


def test_fallback_to_param_name_when_annotation_body_empty() -> None:
    method = make_callable(
        signature="search(java.lang.String)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="payload",
                type_name="java.lang.String",
                annotations=["@QueryParam"],
            ),
        ],
    )
    imports = make_import_declarations("javax.ws.rs.QueryParam", "javax.ws.rs.GET")
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result[0].name == "payload"
    assert result[0].source == EndpointParameterSource.QUERY


# ---------------------------------------------------------------------------
# JAX-RS @BeanParam expansion and Spring @ModelAttribute unscorable handling
# ---------------------------------------------------------------------------


def test_jaxrs_bean_param_expands_resolvable_bean_fields() -> None:
    bean = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["query"],
                annotations=['@QueryParam("q")'],
            ),
            make_field(
                type_name="java.lang.String",
                variables=["token"],
                annotations=['@HeaderParam("X-Token")'],
            ),
            make_field(
                type_name="java.lang.String",
                variables=["upload"],
                annotations=['@FormParam("file")'],
            ),
            make_field(
                type_name="long",
                variables=["id"],
                annotations=['@PathParam("id")'],
            ),
            # Cookie/matrix bindings have no tracked source and are dropped.
            make_field(
                type_name="java.lang.String",
                variables=["session"],
                annotations=['@CookieParam("JSESSIONID")'],
            ),
        ],
    )
    java_file = "src/main/java/example/SearchBean.java"
    analysis = FakeJavaAnalysis(
        classes={"example.SearchBean": bean},
        java_files={"example.SearchBean": java_file},
        import_declarations_by_file={
            java_file: [
                "javax.ws.rs.QueryParam",
                "javax.ws.rs.HeaderParam",
                "javax.ws.rs.FormParam",
                "javax.ws.rs.PathParam",
                "javax.ws.rs.CookieParam",
            ]
        },
    )
    method = make_callable(
        signature="search(example.SearchBean)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="bean",
                type_name="example.SearchBean",
                annotations=["@BeanParam"],
            ),
        ],
    )
    imports = make_import_declarations("javax.ws.rs.BeanParam", "javax.ws.rs.GET")
    result = _extract_method_parameters(
        method,
        framework="jax-rs",
        class_imports=imports,
        declaring_class_name="example.SearchResource",
        analysis=analysis,
        known_class_names={"example.SearchBean"},
    )
    by_name = {param.name: param for param in result}
    assert by_name["q"].source == EndpointParameterSource.QUERY
    assert by_name["X-Token"].source == EndpointParameterSource.HEADER
    assert by_name["file"].source == EndpointParameterSource.FORM
    assert by_name["id"].source == EndpointParameterSource.PATH
    assert by_name["id"].required is True
    assert "JSESSIONID" not in by_name
    assert all(not param.is_unscorable for param in result)


def test_jaxrs_bean_param_includes_superclass_and_nested_bean_fields() -> None:
    base = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["auth"],
                annotations=['@HeaderParam("Authorization")'],
            ),
        ],
    )
    nested = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["page"],
                annotations=['@QueryParam("page")'],
            ),
        ],
    )
    bean = make_type(
        extends_list=["example.BaseBean"],
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["q"],
                annotations=['@QueryParam("q")'],
            ),
            make_field(
                type_name="example.NestedBean",
                variables=["nested"],
                annotations=["@BeanParam"],
            ),
        ],
    )

    def java_file(class_name: str) -> str:
        return f"src/main/java/{class_name.replace('.', '/')}.java"

    jaxrs_imports = [
        "javax.ws.rs.QueryParam",
        "javax.ws.rs.HeaderParam",
        "javax.ws.rs.BeanParam",
    ]
    bean_classes = ["example.SearchBean", "example.BaseBean", "example.NestedBean"]
    analysis = FakeJavaAnalysis(
        classes={
            "example.SearchBean": bean,
            "example.BaseBean": base,
            "example.NestedBean": nested,
        },
        java_files={class_name: java_file(class_name) for class_name in bean_classes},
        import_declarations_by_file={
            java_file(class_name): jaxrs_imports for class_name in bean_classes
        },
    )
    method = make_callable(
        signature="search(example.SearchBean)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="bean",
                type_name="example.SearchBean",
                annotations=["@BeanParam"],
            ),
        ],
    )
    imports = make_import_declarations("javax.ws.rs.BeanParam", "javax.ws.rs.GET")
    result = _extract_method_parameters(
        method,
        framework="jax-rs",
        class_imports=imports,
        declaring_class_name="example.SearchResource",
        analysis=analysis,
        known_class_names=set(bean_classes),
    )
    assert {param.name for param in result} == {"q", "Authorization", "page"}


def test_jaxrs_bean_param_nested_bean_constant_name_resolves() -> None:
    constants = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["PAGE"],
                modifiers=["static", "final"],
                variable_initializers={"PAGE": '"page"'},
            ),
        ],
    )
    nested = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["pageField"],
                annotations=["@QueryParam(Params.PAGE)"],
            ),
        ],
    )
    bean = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["q"],
                annotations=['@QueryParam("q")'],
            ),
            make_field(
                type_name="example.NestedBean",
                variables=["nested"],
                annotations=["@BeanParam"],
            ),
        ],
    )
    bean_classes = ["example.SearchBean", "example.NestedBean", "example.Params"]
    analysis = FakeJavaAnalysis(
        classes={
            "example.SearchBean": bean,
            "example.NestedBean": nested,
            "example.Params": constants,
        },
        java_files={class_name: _java_file(class_name) for class_name in bean_classes},
        import_declarations_by_file={
            _java_file(class_name): ["javax.ws.rs.QueryParam", "javax.ws.rs.BeanParam"]
            for class_name in bean_classes
        },
    )
    method = make_callable(
        signature="search(example.SearchBean)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="bean",
                type_name="example.SearchBean",
                annotations=["@BeanParam"],
            ),
        ],
    )
    result = _extract_method_parameters(
        method,
        framework="jax-rs",
        class_imports=make_import_declarations(
            "javax.ws.rs.BeanParam", "javax.ws.rs.GET"
        ),
        declaring_class_name="example.SearchResource",
        analysis=analysis,
        known_class_names=set(bean_classes),
        constant_resolver=CommonAnalysis(analysis).get_constant_resolver(),
    )
    assert {param.name for param in result} == {"q", "page"}


def test_jaxrs_bean_param_self_reference_does_not_recurse_infinitely() -> None:
    bean = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["q"],
                annotations=['@QueryParam("q")'],
            ),
            make_field(
                type_name="example.SearchBean",
                variables=["self"],
                annotations=["@BeanParam"],
            ),
        ],
    )
    java_file = "src/main/java/example/SearchBean.java"
    analysis = FakeJavaAnalysis(
        classes={"example.SearchBean": bean},
        java_files={"example.SearchBean": java_file},
        import_declarations_by_file={
            java_file: ["javax.ws.rs.QueryParam", "javax.ws.rs.BeanParam"]
        },
    )
    method = make_callable(
        signature="search(example.SearchBean)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="bean",
                type_name="example.SearchBean",
                annotations=["@BeanParam"],
            ),
        ],
    )
    imports = make_import_declarations("javax.ws.rs.BeanParam", "javax.ws.rs.GET")
    result = _extract_method_parameters(
        method,
        framework="jax-rs",
        class_imports=imports,
        declaring_class_name="example.SearchResource",
        analysis=analysis,
        known_class_names={"example.SearchBean"},
    )
    assert [param.name for param in result] == ["q"]


def test_jaxrs_bean_param_unresolved_bean_is_unscorable() -> None:
    method = make_callable(
        signature="create(ext.UnknownBean)",
        annotations=["@POST"],
        parameters=[
            make_callable_parameter(
                name="bean",
                type_name="ext.UnknownBean",
                annotations=["@BeanParam"],
            ),
        ],
    )
    imports = make_import_declarations("javax.ws.rs.BeanParam", "javax.ws.rs.POST")
    # No analysis context: the bean type cannot be resolved.
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "bean"
    assert result[0].source == EndpointParameterSource.UNKNOWN
    assert result[0].is_unscorable is True
    assert result[0].required is False
    assert result[0].annotation == "@BeanParam"


def _bean_param_method() -> JCallable:
    return make_callable(
        signature="search(example.SearchBean)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="bean",
                type_name="example.SearchBean",
                annotations=["@BeanParam"],
            ),
        ],
    )


def _expand_bean(
    bean: JType, imports: list[str], known: set[str] | None = None
) -> list[EndpointParameter]:
    java_file = "src/main/java/example/SearchBean.java"
    analysis = FakeJavaAnalysis(
        classes={"example.SearchBean": bean},
        java_files={"example.SearchBean": java_file},
        import_declarations_by_file={java_file: imports},
    )
    return _extract_method_parameters(
        _bean_param_method(),
        framework="jax-rs",
        class_imports=make_import_declarations(
            "javax.ws.rs.BeanParam", "javax.ws.rs.GET"
        ),
        declaring_class_name="example.SearchResource",
        analysis=analysis,
        known_class_names=known or {"example.SearchBean"},
    )


def test_jaxrs_bean_param_expands_setter_property_bindings() -> None:
    bean = make_type(
        callable_declarations={
            "setQuery(java.lang.String)": make_callable(
                signature="setQuery(java.lang.String)",
                annotations=['@QueryParam("q")'],
                parameters=[
                    make_callable_parameter(name="query", type_name="java.lang.String")
                ],
                return_type="void",
            ),
        },
    )
    result = _expand_bean(bean, ["javax.ws.rs.QueryParam"])
    assert len(result) == 1
    assert result[0].name == "q"
    assert result[0].source == EndpointParameterSource.QUERY
    assert result[0].is_unscorable is False


def test_jaxrs_bean_param_expands_getter_property_bindings() -> None:
    bean = make_type(
        callable_declarations={
            "getToken()": make_callable(
                signature="getToken()",
                annotations=['@HeaderParam("X-Token")'],
                parameters=[],
                return_type="java.lang.String",
            ),
        },
    )
    result = _expand_bean(bean, ["javax.ws.rs.HeaderParam"])
    assert len(result) == 1
    assert result[0].name == "X-Token"
    assert result[0].source == EndpointParameterSource.HEADER


def test_jaxrs_bean_param_expands_constructor_parameter_bindings() -> None:
    bean = make_type(
        callable_declarations={
            "SearchBean(java.lang.String)": make_callable(
                signature="SearchBean(java.lang.String)",
                is_constructor=True,
                parameters=[
                    make_callable_parameter(
                        name="q",
                        type_name="java.lang.String",
                        annotations=['@QueryParam("q")'],
                    )
                ],
            ),
        },
    )
    result = _expand_bean(bean, ["javax.ws.rs.QueryParam"])
    assert len(result) == 1
    assert result[0].name == "q"
    assert result[0].source == EndpointParameterSource.QUERY


def test_jaxrs_bean_param_dedupes_field_and_setter_for_same_property() -> None:
    bean = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["query"],
                annotations=['@QueryParam("q")'],
            ),
        ],
        callable_declarations={
            "setQuery(java.lang.String)": make_callable(
                signature="setQuery(java.lang.String)",
                annotations=['@QueryParam("q")'],
                parameters=[
                    make_callable_parameter(name="query", type_name="java.lang.String")
                ],
                return_type="void",
            ),
        },
    )
    result = _expand_bean(bean, ["javax.ws.rs.QueryParam"])
    # The field and its setter describe one binding; it must not be counted twice.
    assert [param.name for param in result] == ["q"]


def test_jaxrs_bean_param_nested_unresolved_field_is_unscorable() -> None:
    bean = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["q"],
                annotations=['@QueryParam("q")'],
            ),
            make_field(
                type_name="ext.UnknownBean",
                variables=["nested"],
                annotations=["@BeanParam"],
            ),
        ],
    )
    result = _expand_bean(bean, ["javax.ws.rs.QueryParam", "javax.ws.rs.BeanParam"])
    by_name = {param.name: param for param in result}
    # The resolvable field is scored; the unresolvable nested bean is recorded as
    # unscorable rather than silently dropped.
    assert by_name["q"].source == EndpointParameterSource.QUERY
    assert by_name["q"].is_unscorable is False
    assert by_name["nested"].is_unscorable is True
    assert by_name["nested"].source == EndpointParameterSource.UNKNOWN


def test_jaxrs_bean_param_resolvable_but_unscorable_bean_is_marked() -> None:
    # A resolvable bean whose only binding has no tracked source (cookie) yields
    # no scorable params, so the aggregate is recorded as unscorable, not dropped.
    bean = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["session"],
                annotations=['@CookieParam("JSESSIONID")'],
            ),
        ],
    )
    result = _expand_bean(bean, ["javax.ws.rs.CookieParam"])
    assert len(result) == 1
    assert result[0].name == "bean"
    assert result[0].is_unscorable is True
    assert result[0].source == EndpointParameterSource.UNKNOWN


def test_spring_model_attribute_is_unscorable() -> None:
    method = make_callable(
        signature="process(example.Owner)",
        annotations=['@PostMapping("/owners")'],
        parameters=[
            make_callable_parameter(
                name="owner",
                type_name="example.Owner",
                annotations=["@ModelAttribute"],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.ModelAttribute",
        "org.springframework.web.bind.annotation.PostMapping",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "owner"
    assert result[0].source == EndpointParameterSource.UNKNOWN
    assert result[0].is_unscorable is True
    assert result[0].required is False


def test_spring_model_attribute_wrong_package_is_ignored() -> None:
    method = make_callable(
        signature="process(example.Owner)",
        annotations=['@PostMapping("/owners")'],
        parameters=[
            make_callable_parameter(
                name="owner",
                type_name="example.Owner",
                annotations=["@ModelAttribute"],
            ),
        ],
    )
    # @ModelAttribute imported from an unrelated package must not be treated as
    # the Spring binding (no import under the Spring web-bind root is present).
    imports = make_import_declarations("com.example.custom.ModelAttribute")
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert result == []


# ---------------------------------------------------------------------------
# Unannotated / wrong-package / Spring required=false
# ---------------------------------------------------------------------------


def test_unannotated_parameters_are_skipped() -> None:
    method = make_callable(
        signature="handle(javax.servlet.http.HttpServletRequest)",
        annotations=['@GetMapping("/foo")'],
        parameters=[
            make_callable_parameter(
                name="request",
                type_name="javax.servlet.http.HttpServletRequest",
                annotations=[],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.GetMapping",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert result == []


def test_wrong_package_annotation_rejected() -> None:
    method = make_callable(
        signature="get(java.lang.String)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="id",
                type_name="java.lang.String",
                annotations=['@com.custom.PathParam("id")'],
            ),
        ],
    )
    imports = make_import_declarations("com.custom.PathParam", "javax.ws.rs.GET")
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result == []


def test_spring_request_param_required_false() -> None:
    method = make_callable(
        signature="search(java.lang.String)",
        annotations=['@GetMapping("/search")'],
        parameters=[
            make_callable_parameter(
                name="filter",
                type_name="java.lang.String",
                annotations=['@RequestParam(value = "filter", required = false)'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestParam",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].name == "filter"
    assert result[0].required is False


def test_spring_request_param_required_true_by_default() -> None:
    method = make_callable(
        signature="search(java.lang.String)",
        annotations=['@GetMapping("/search")'],
        parameters=[
            make_callable_parameter(
                name="q",
                type_name="java.lang.String",
                annotations=['@RequestParam("q")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestParam",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert result[0].required is True


# ---------------------------------------------------------------------------
# Integration: endpoints carry parameters after extraction
# ---------------------------------------------------------------------------


def test_extract_application_endpoints_includes_parameters() -> None:
    class_name = "example.UserController"
    java_file = _java_file(class_name)
    analysis = FakeJavaAnalysis(
        classes={
            class_name: make_type(
                annotations=["@RestController"],
            ),
        },
        methods_by_class={
            class_name: {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/users/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="java.lang.String",
                            annotations=['@PathVariable("id")'],
                        ),
                    ],
                ),
            },
        },
        java_files={class_name: java_file},
        import_declarations_by_file={
            java_file: [
                JImport(
                    path="org.springframework.web.bind.annotation.RestController",
                    is_static=False,
                    is_wildcard=False,
                ),
                JImport(
                    path="org.springframework.web.bind.annotation.GetMapping",
                    is_static=False,
                    is_wildcard=False,
                ),
                JImport(
                    path="org.springframework.web.bind.annotation.PathVariable",
                    is_static=False,
                    is_wildcard=False,
                ),
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[class_name],
    ).endpoints
    assert len(endpoints) == 1
    assert len(endpoints[0].parameters) == 1
    assert endpoints[0].parameters[0].name == "id"
    assert endpoints[0].parameters[0].source == EndpointParameterSource.PATH
    assert endpoints[0].surface.parameter_sources == [EndpointParameterSource.PATH]
    assert endpoints[0].surface.parameter_count_by_source == {
        EndpointParameterSource.PATH: 1
    }
    assert endpoints[0].surface.required_parameter_count_by_source == {
        EndpointParameterSource.PATH: 1
    }
    assert endpoints[0].surface.optional_parameter_count_by_source == {
        EndpointParameterSource.PATH: 0
    }
    assert endpoints[0].surface.total_required_parameter_count == 1
    assert endpoints[0].surface.total_optional_parameter_count == 0

    dumped_endpoint = endpoints[0].model_dump(mode="json", exclude_defaults=True)
    assert dumped_endpoint["surface"]["parameter_sources"] == ["path"]
    assert dumped_endpoint["surface"]["parameter_count_by_source"] == {"path": 1}
    assert dumped_endpoint["surface"]["total_required_parameter_count"] == 1


# ---------------------------------------------------------------------------
# Parameter coverage analysis helpers
# ---------------------------------------------------------------------------


def _make_endpoint(
    http_method: str = "GET",
    path_template: str = "/api/users/{id}",
    parameters: list[EndpointParameter] | None = None,
    framework: str = "spring",
) -> ApplicationEndpoint:
    return ApplicationEndpoint(
        http_method=http_method,
        path_template=path_template,
        framework=framework,
        declaring_class_name="example.Controller",
        declaring_method_signature="method()",
        parameters=parameters or [],
    )


def _make_test_class_analysis(
    interactions: list[HttpRequestInteraction],
    class_name: str = "test.FooTest",
    method_sig: str = "testMethod()",
) -> TestClassAnalysis:
    return TestClassAnalysis(
        qualified_class_name=class_name,
        test_method_analyses=[
            TestMethodAnalysis(
                identity=MethodIdentity(
                    defining_class_name=class_name,
                    method_signature=method_sig,
                    method_declaration=f"void {method_sig}",
                ),
                is_api_test=True,
                http=HttpAnalysis(request_interactions=interactions),
            ),
        ],
    )


def _event_interaction(
    http_method: str = "GET",
    path: str = "/api/users/42",
    header_names: list[str] | None = None,
    query_param_names: list[str] | None = None,
    path_param_names: list[str] | None = None,
    form_param_names: list[str] | None = None,
    has_body_payload: bool = False,
    role: HttpRequestRole = HttpRequestRole.EVENT,
) -> HttpRequestInteraction:
    return HttpRequestInteraction(
        origin=OriginContext(
            phase=LifecyclePhase.TEST,
            kind=CallSiteOriginKind.TEST_METHOD,
        ),
        http_call=HttpCallSite(
            http_method=http_method,
            path=path,
            framework=HttpDispatchFramework.MOCKMVC,
            request_role=role,
            method_name="perform",
            header_names=header_names or [],
            query_param_names=query_param_names or [],
            path_param_names=path_param_names or [],
            form_param_names=form_param_names or [],
            has_body_payload=has_body_payload,
        ),
        endpoint_candidate=EndpointCandidate(
            http_method=http_method,
            path=path,
            source="test",
        ),
    )


# ---------------------------------------------------------------------------
# Parameter coverage tests
# ---------------------------------------------------------------------------


def test_endpoints_without_parameters_excluded() -> None:
    endpoints = [_make_endpoint(parameters=[])]
    result = build_endpoint_parameter_coverage_summary(endpoints, [])
    assert result.total_endpoints_with_parameters == 0


def test_path_params_always_exercised_on_template_match() -> None:
    endpoints = [
        _make_endpoint(
            parameters=[
                EndpointParameter(
                    name="id",
                    type="java.lang.String",
                    source=EndpointParameterSource.PATH,
                ),
            ],
        ),
    ]
    test_classes = [_make_test_class_analysis([_event_interaction()])]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.total_endpoints_with_parameters == 1
    assert result.fully_exercised_endpoint_count == 1
    entry = result.endpoints[0]
    assert entry.exercised_parameter_count == 1
    assert entry.parameter_entries[0].is_exercised is True


def test_application_path_prefixed_request_exercises_parameters() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/quotes/{symbol}",
            parameters=[
                EndpointParameter(
                    name="symbol",
                    type="java.lang.String",
                    source=EndpointParameterSource.PATH,
                ),
                EndpointParameter(
                    name="detail",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                ),
            ],
        ),
    ]
    interaction = _event_interaction(
        path="/rest/quotes/IBM",
        query_param_names=["detail"],
    )
    test_classes = [_make_test_class_analysis([interaction])]

    unprefixed = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert unprefixed.endpoints[0].exercised_parameter_count == 0

    result = build_endpoint_parameter_coverage_summary(
        endpoints,
        test_classes,
        application_path_prefixes=("/rest",),
    )
    entry = result.endpoints[0]
    assert entry.exercised_parameter_count == 2
    assert result.fully_exercised_endpoint_count == 1


def test_suffix_fallback_request_exercises_parameters() -> None:
    # Parameter coverage shares route coverage's matcher, so a request whose
    # mount prefix is invisible to extraction still exercises parameters.
    endpoints = [
        _make_endpoint(
            path_template="/apis/registry/v3/search/artifacts",
            parameters=[
                EndpointParameter(
                    name="name",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                ),
            ],
        ),
    ]
    interaction = _event_interaction(
        path="/registry/v3/search/artifacts",
        query_param_names=["name"],
    )
    test_classes = [_make_test_class_analysis([interaction])]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]
    assert entry.route_covering_test_count == 1
    assert entry.exercised_parameter_count == 1


def test_direct_match_suppresses_prefix_stripped_parameter_exercise() -> None:
    stripped_target = _make_endpoint(
        path_template="/users",
        parameters=[
            EndpointParameter(
                name="page",
                type="java.lang.String",
                source=EndpointParameterSource.QUERY,
            ),
        ],
    )
    direct_target = _make_endpoint(
        path_template="/rest/users",
        parameters=[
            EndpointParameter(
                name="page",
                type="java.lang.String",
                source=EndpointParameterSource.QUERY,
            ),
        ],
    )
    interaction = _event_interaction(
        path="/rest/users",
        query_param_names=["page"],
    )
    test_classes = [_make_test_class_analysis([interaction])]

    result = build_endpoint_parameter_coverage_summary(
        [stripped_target, direct_target],
        test_classes,
        application_path_prefixes=("/rest",),
    )

    by_template = {entry.endpoint.path_template: entry for entry in result.endpoints}
    assert by_template["/rest/users"].exercised_parameter_count == 1
    assert by_template["/users"].exercised_parameter_count == 0


def test_query_param_exercised_when_present_in_path() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis([_event_interaction(path="/search?q=hello")]),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is True


def test_query_param_exercised_when_present_on_request_event() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [_event_interaction(path="/search", query_param_names=["q"])]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is True


def test_query_param_not_exercised_when_absent() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
            ],
        ),
    ]
    test_classes = [_make_test_class_analysis([_event_interaction(path="/search")])]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is False


def test_query_param_not_exercised_when_different_name() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis([_event_interaction(path="/search?filter=abc")]),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is False


def test_header_param_exercised_when_present() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/api/users/{id}",
            parameters=[
                EndpointParameter(
                    name="X-Token",
                    type="java.lang.String",
                    source=EndpointParameterSource.HEADER,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis([_event_interaction(header_names=["x-token"])]),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is True


def test_body_param_exercised_when_payload_present() -> None:
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/api/users",
            parameters=[
                EndpointParameter(
                    name="body",
                    type="example.Dto",
                    source=EndpointParameterSource.BODY,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    http_method="POST",
                    path="/api/users",
                    has_body_payload=True,
                )
            ]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is True


def test_body_param_not_exercised_for_post_without_payload() -> None:
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/api/users",
            parameters=[
                EndpointParameter(
                    name="body",
                    type="example.Dto",
                    source=EndpointParameterSource.BODY,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [_event_interaction(http_method="POST", path="/api/users")]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is False


def test_body_param_not_exercised_for_get() -> None:
    endpoints = [
        _make_endpoint(
            http_method="GET",
            path_template="/api/users",
            parameters=[
                EndpointParameter(
                    name="body",
                    type="example.Dto",
                    source=EndpointParameterSource.BODY,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [_event_interaction(http_method="GET", path="/api/users")]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is False


def test_fully_vs_partially_vs_unexercised_classification() -> None:
    path_param = EndpointParameter(
        name="id", type="java.lang.String", source=EndpointParameterSource.PATH
    )
    query_param = EndpointParameter(
        name="q", type="java.lang.String", source=EndpointParameterSource.QUERY
    )

    fully = _make_endpoint(
        path_template="/api/items/{id}",
        parameters=[path_param],
    )
    partial = _make_endpoint(
        path_template="/api/search/{id}",
        parameters=[path_param, query_param],
    )
    unexercised = _make_endpoint(
        http_method="POST",
        path_template="/api/other",
        parameters=[
            EndpointParameter(
                name="body",
                type="example.Dto",
                source=EndpointParameterSource.BODY,
            )
        ],
    )

    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(path="/api/items/1"),
                _event_interaction(path="/api/search/2"),
            ]
        ),
    ]

    result = build_endpoint_parameter_coverage_summary(
        [fully, partial, unexercised], test_classes
    )
    assert result.fully_exercised_endpoint_count == 1
    assert result.partially_exercised_endpoint_count == 1
    assert result.unexercised_endpoint_count == 1


def test_parameter_evidence_traces_to_correct_test_method() -> None:
    endpoints = [
        _make_endpoint(
            parameters=[
                EndpointParameter(
                    name="id",
                    type="java.lang.String",
                    source=EndpointParameterSource.PATH,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [_event_interaction()],
            class_name="test.MyTest",
            method_sig="testGet()",
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    evidence = result.endpoints[0].parameter_evidence
    assert len(evidence) == 1
    assert evidence[0].test_method.qualified_class_name == "test.MyTest"
    assert evidence[0].test_method.method_signature == "testGet()"


def test_external_url_requests_excluded() -> None:
    endpoints = [
        _make_endpoint(
            parameters=[
                EndpointParameter(
                    name="id",
                    type="java.lang.String",
                    source=EndpointParameterSource.PATH,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [_event_interaction(path="https://external.example.com/api/users/42")]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].exercised_parameter_count == 0


def test_builder_interactions_excluded() -> None:
    endpoints = [
        _make_endpoint(
            parameters=[
                EndpointParameter(
                    name="id",
                    type="java.lang.String",
                    source=EndpointParameterSource.PATH,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis([_event_interaction(role=HttpRequestRole.BUILDER)]),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].exercised_parameter_count == 0


def test_multiple_tests_contribute_different_evidence() -> None:
    query_param = EndpointParameter(
        name="q", type="java.lang.String", source=EndpointParameterSource.QUERY
    )
    header_param = EndpointParameter(
        name="X-Token", type="java.lang.String", source=EndpointParameterSource.HEADER
    )
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[query_param, header_param],
        ),
    ]
    test1 = _make_test_class_analysis(
        [_event_interaction(path="/search?q=hello")],
        class_name="test.A",
        method_sig="t1()",
    )
    test2 = _make_test_class_analysis(
        [_event_interaction(path="/search", header_names=["x-token"])],
        class_name="test.B",
        method_sig="t2()",
    )
    result = build_endpoint_parameter_coverage_summary(endpoints, [test1, test2])
    entry = result.endpoints[0]
    assert entry.exercised_parameter_count == 2
    assert entry.total_parameter_count == 2
    assert result.fully_exercised_endpoint_count == 1


def test_form_param_exercised_when_name_present() -> None:
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/api/upload",
            parameters=[
                EndpointParameter(
                    name="file",
                    type="org.springframework.web.multipart.MultipartFile",
                    source=EndpointParameterSource.FORM,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    http_method="POST",
                    path="/api/upload",
                    form_param_names=["file"],
                )
            ]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is True


def test_request_part_form_params_partially_exercised_by_multipart_file_names() -> None:
    # Mirrors the @RequestPart("payload1")/@RequestPart("payload2") controller
    # exercised by a MockMvc `multipart(...).file("payload1", ...)` test that
    # only supplies the first part.
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/api/handler-multipart",
            parameters=[
                EndpointParameter(
                    name="payload1",
                    type="org.springframework.web.multipart.MultipartFile",
                    source=EndpointParameterSource.FORM,
                ),
                EndpointParameter(
                    name="payload2",
                    type="org.springframework.web.multipart.MultipartFile",
                    source=EndpointParameterSource.FORM,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    http_method="POST",
                    path="/api/handler-multipart",
                    form_param_names=["payload1"],
                )
            ]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]
    parameter_entries = {
        parameter_entry.parameter.name: parameter_entry
        for parameter_entry in entry.parameter_entries
    }
    assert parameter_entries["payload1"].is_exercised is True
    assert parameter_entries["payload2"].is_exercised is False
    assert entry.exercised_parameter_count == 1
    assert result.fully_exercised_endpoint_count == 0


def test_form_param_not_exercised_for_post_without_name() -> None:
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/api/upload",
            parameters=[
                EndpointParameter(
                    name="file",
                    type="org.springframework.web.multipart.MultipartFile",
                    source=EndpointParameterSource.FORM,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [_event_interaction(http_method="POST", path="/api/upload")]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is False


def test_form_param_not_exercised_for_get() -> None:
    endpoints = [
        _make_endpoint(
            http_method="GET",
            path_template="/api/upload",
            parameters=[
                EndpointParameter(
                    name="file",
                    type="org.springframework.web.multipart.MultipartFile",
                    source=EndpointParameterSource.FORM,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [_event_interaction(http_method="GET", path="/api/upload")]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is False


def test_spring_request_param_exercised_by_form_param() -> None:
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/login",
            parameters=[
                EndpointParameter(
                    name="username",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
                EndpointParameter(
                    name="password",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    http_method="POST",
                    path="/login",
                    form_param_names=["username", "password"],
                )
            ]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]
    assert all(
        parameter_entry.is_exercised for parameter_entry in entry.parameter_entries
    )
    assert entry.exercised_parameter_count == 2
    assert result.fully_exercised_endpoint_count == 1


def test_jax_rs_query_param_not_exercised_by_form_param() -> None:
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/login",
            framework="jax-rs",
            parameters=[
                EndpointParameter(
                    name="username",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
                EndpointParameter(
                    name="password",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    http_method="POST",
                    path="/login",
                    form_param_names=["username", "password"],
                )
            ]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]
    assert all(
        parameter_entry.is_exercised is False
        for parameter_entry in entry.parameter_entries
    )
    assert entry.exercised_parameter_count == 0
    assert result.unexercised_endpoint_count == 1


def test_spring_request_param_still_exercised_by_query_param() -> None:
    endpoints = [
        _make_endpoint(
            parameters=[
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    path="/api/users/42?q=alice",
                    query_param_names=["q"],
                )
            ]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]
    assert entry.parameter_entries[0].is_exercised is True
    assert entry.exercised_parameter_count == 1


def test_spring_request_param_mixed_form_and_query_exercise_distinct_params() -> None:
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/filter",
            parameters=[
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
                EndpointParameter(
                    name="f",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    http_method="POST",
                    path="/filter?q=alice",
                    query_param_names=["q"],
                    form_param_names=["f"],
                )
            ]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]
    assert all(
        parameter_entry.is_exercised for parameter_entry in entry.parameter_entries
    )
    assert entry.exercised_parameter_count == 2
    assert result.fully_exercised_endpoint_count == 1


def test_unknown_source_params_never_exercised() -> None:
    endpoints = [
        _make_endpoint(
            parameters=[
                EndpointParameter(
                    name="ctx",
                    type="javax.servlet.http.HttpServletRequest",
                    source=EndpointParameterSource.UNKNOWN,
                ),
            ],
        ),
    ]
    test_classes = [_make_test_class_analysis([_event_interaction()])]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is False
    assert result.unexercised_endpoint_count == 1


def test_mixed_sources_partial_coverage() -> None:
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/api/users/{id}",
            parameters=[
                EndpointParameter(
                    name="id",
                    type="java.lang.String",
                    source=EndpointParameterSource.PATH,
                ),
                EndpointParameter(
                    name="body",
                    type="example.Dto",
                    source=EndpointParameterSource.BODY,
                ),
                EndpointParameter(
                    name="X-Trace",
                    type="java.lang.String",
                    source=EndpointParameterSource.HEADER,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    http_method="POST",
                    path="/api/users/42",
                    has_body_payload=True,
                )
            ]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]
    assert entry.exercised_parameter_count == 2
    assert entry.total_parameter_count == 3
    assert entry.parameter_entries[0].is_exercised is True  # PATH
    assert entry.parameter_entries[1].is_exercised is True  # BODY
    assert entry.parameter_entries[2].is_exercised is False  # HEADER (not sent)


def test_parameter_coverage_rollups_include_required_optional_and_source_rates() -> (
    None
):
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/api/users/{id}",
            parameters=[
                EndpointParameter(
                    name="id",
                    type="java.lang.String",
                    source=EndpointParameterSource.PATH,
                ),
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
                EndpointParameter(
                    name="body",
                    type="example.Dto",
                    source=EndpointParameterSource.BODY,
                ),
                EndpointParameter(
                    name="X-Token",
                    type="java.lang.String",
                    source=EndpointParameterSource.HEADER,
                    required=False,
                ),
                EndpointParameter(
                    name="file",
                    type="org.springframework.web.multipart.MultipartFile",
                    source=EndpointParameterSource.FORM,
                    required=False,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    http_method="POST",
                    path="/api/users/42?q=alice",
                    header_names=["x-token"],
                )
            ]
        )
    ]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    assert entry.total_parameter_count == 5
    assert entry.exercised_parameter_count == 3
    assert entry.exercise_rate == 3 / 5
    assert entry.exercise_rate_by_source == {
        EndpointParameterSource.PATH: 1.0,
        EndpointParameterSource.QUERY: 1.0,
        EndpointParameterSource.HEADER: 1.0,
        EndpointParameterSource.BODY: 0.0,
        EndpointParameterSource.FORM: 0.0,
    }
    assert entry.required_parameter_count == 3
    assert entry.required_exercised_count == 2
    assert entry.required_exercise_rate == 2 / 3
    assert entry.optional_parameter_count == 2
    assert entry.optional_exercised_count == 1
    assert entry.optional_exercise_rate == 1 / 2
    assert entry.route_covering_test_count == 1
    assert entry.observed_optional_parameter_set_limit == 256
    assert entry.observed_optional_parameter_sets_truncated is False
    assert entry.distinct_observed_optional_parameter_set_count == 1
    assert len(entry.observed_optional_parameter_sets) == 1
    assert entry.observed_optional_parameter_sets[0].parameter_keys == [
        "header:x-token"
    ]
    assert entry.observed_optional_parameter_sets[0].test_count == 1


def test_route_covering_count_includes_hits_with_no_exercised_parameters() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                ),
            ],
        ),
    ]
    test_classes = [_make_test_class_analysis([_event_interaction(path="/search")])]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    assert entry.route_covering_test_count == 1
    assert entry.exercised_parameter_count == 0
    assert entry.parameter_evidence == []
    assert entry.distinct_observed_optional_parameter_set_count == 1
    assert entry.observed_optional_parameter_sets_truncated is False
    assert len(entry.observed_optional_parameter_sets) == 1
    assert entry.observed_optional_parameter_sets[0].parameter_keys == []
    assert entry.observed_optional_parameter_sets[0].test_count == 1


def test_observed_optional_parameter_sets_are_unique_counted_and_capped() -> None:
    optional_parameters = [
        EndpointParameter(
            name=f"p{i}",
            type="java.lang.String",
            source=EndpointParameterSource.QUERY,
            required=False,
        )
        for i in range(9)
    ]
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=optional_parameters,
        ),
    ]
    test_classes = []
    for mask in range(257):
        query_param_names = [
            f"p{i}" for i in range(len(optional_parameters)) if mask & (1 << i)
        ]
        test_classes.append(
            _make_test_class_analysis(
                [
                    _event_interaction(
                        path="/search", query_param_names=query_param_names
                    )
                ],
                method_sig=f"testCombination{mask}()",
            )
        )

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    assert entry.route_covering_test_count == 257
    assert entry.optional_parameter_count == 9
    assert entry.optional_exercised_count == 9
    assert entry.optional_exercise_rate == 1.0
    assert entry.distinct_observed_optional_parameter_set_count == 257
    assert entry.observed_optional_parameter_set_limit == 256
    assert entry.observed_optional_parameter_sets_truncated is True
    assert len(entry.observed_optional_parameter_sets) == 256
    assert all(
        observed_set.test_count == 1
        for observed_set in entry.observed_optional_parameter_sets
    )


def test_parameter_coverage_uses_source_qualified_parameter_identity() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/items/{id}",
            parameters=[
                EndpointParameter(
                    name="id",
                    type="java.lang.String",
                    source=EndpointParameterSource.PATH,
                ),
                EndpointParameter(
                    name="id",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
            ],
        ),
    ]
    test_classes = [_make_test_class_analysis([_event_interaction(path="/items/42")])]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    assert entry.parameter_entries[0].is_exercised is True
    assert entry.parameter_entries[1].is_exercised is False
    assert entry.exercised_parameter_count == 1
    assert entry.exercise_rate_by_source == {
        EndpointParameterSource.PATH: 1.0,
        EndpointParameterSource.QUERY: 0.0,
    }


# ---------------------------------------------------------------------------
# Per-source required/optional rollups and combinatorial optional coverage
# ---------------------------------------------------------------------------


def test_per_source_required_optional_counts_and_rates() -> None:
    endpoints = [
        _make_endpoint(
            http_method="GET",
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
                EndpointParameter(
                    name="filter",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                ),
                EndpointParameter(
                    name="X-Token",
                    type="java.lang.String",
                    source=EndpointParameterSource.HEADER,
                    required=False,
                ),
                EndpointParameter(
                    name="file",
                    type="org.springframework.web.multipart.MultipartFile",
                    source=EndpointParameterSource.FORM,
                    required=False,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    path="/search?q=alice&filter=active",
                    header_names=["x-token"],
                )
            ]
        )
    ]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    # Every present source (query/header/form) is keyed; header and form carry no
    # required parameter, so their required count is 0 and required rate is None.
    assert entry.required_parameter_count_by_source == {
        EndpointParameterSource.QUERY: 1,
        EndpointParameterSource.HEADER: 0,
        EndpointParameterSource.FORM: 0,
    }
    assert entry.required_exercised_count_by_source == {
        EndpointParameterSource.QUERY: 1,
        EndpointParameterSource.HEADER: 0,
        EndpointParameterSource.FORM: 0,
    }
    assert entry.required_exercise_rate_by_source == {
        EndpointParameterSource.QUERY: 1.0,
        EndpointParameterSource.HEADER: None,
        EndpointParameterSource.FORM: None,
    }
    assert entry.optional_parameter_count_by_source == {
        EndpointParameterSource.QUERY: 1,
        EndpointParameterSource.HEADER: 1,
        EndpointParameterSource.FORM: 1,
    }
    assert entry.optional_exercised_count_by_source == {
        EndpointParameterSource.QUERY: 1,
        EndpointParameterSource.HEADER: 1,
        EndpointParameterSource.FORM: 0,
    }
    assert entry.optional_exercise_rate_by_source == {
        EndpointParameterSource.QUERY: 1.0,
        EndpointParameterSource.HEADER: 1.0,
        EndpointParameterSource.FORM: 0.0,
    }


def test_per_source_metrics_exclude_path_and_body() -> None:
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/api/users/{id}",
            parameters=[
                EndpointParameter(
                    name="id",
                    type="java.lang.String",
                    source=EndpointParameterSource.PATH,
                ),
                EndpointParameter(
                    name="body",
                    type="example.Dto",
                    source=EndpointParameterSource.BODY,
                    required=False,
                ),
                EndpointParameter(
                    name="X-Token",
                    type="java.lang.String",
                    source=EndpointParameterSource.HEADER,
                    required=False,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    http_method="POST",
                    path="/api/users/42",
                    has_body_payload=True,
                    header_names=["x-token"],
                ),
                _event_interaction(http_method="POST", path="/api/users/42"),
            ]
        )
    ]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    # Path is always required and body is not name-enumerated: neither appears in
    # the per-source breakdowns, which cover query/header/form only. Header is the
    # one present source there; it has no required parameter, so it is keyed with a
    # required count of 0 and a required rate of None.
    assert entry.required_parameter_count_by_source == {
        EndpointParameterSource.HEADER: 0
    }
    assert entry.required_exercise_rate_by_source == {
        EndpointParameterSource.HEADER: None
    }
    assert entry.optional_parameter_count_by_source == {
        EndpointParameterSource.HEADER: 1
    }
    assert set(entry.optional_exercise_rate_by_source) == {
        EndpointParameterSource.HEADER
    }
    assert set(entry.distinct_observed_optional_set_count_by_source) == {
        EndpointParameterSource.HEADER
    }
    # Body optionality still flows into the holistic optional surface.
    assert entry.optional_parameter_count == 2
    assert entry.simple_1_way_optional_covered_count == 2


def test_distinct_observed_optional_set_count_by_source() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="a",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                ),
                EndpointParameter(
                    name="b",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                ),
                EndpointParameter(
                    name="X-H",
                    type="java.lang.String",
                    source=EndpointParameterSource.HEADER,
                    required=False,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(path="/search", query_param_names=["a"]),
                _event_interaction(
                    path="/search",
                    query_param_names=["a", "b"],
                    header_names=["x-h"],
                ),
                _event_interaction(path="/search"),
            ]
        )
    ]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    assert entry.distinct_observed_optional_parameter_set_count == 3
    assert entry.distinct_observed_optional_set_count_by_source == {
        EndpointParameterSource.QUERY: 3,
        EndpointParameterSource.HEADER: 2,
    }


def test_simple_1_way_optional_coverage_per_source_and_holistic() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="a",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                ),
                EndpointParameter(
                    name="b",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(path="/search", query_param_names=["a", "b"]),
                _event_interaction(path="/search", query_param_names=["b"]),
            ]
        )
    ]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    # "a" is observed present (request 1) and absent (request 2); "b" is always
    # present, so only "a" achieves simple 1-way (each-choice) coverage.
    assert entry.simple_1_way_optional_covered_count == 1
    assert entry.simple_1_way_optional_coverage == 0.5
    assert entry.simple_1_way_optional_covered_count_by_source == {
        EndpointParameterSource.QUERY: 1
    }
    assert entry.simple_1_way_optional_coverage_by_source == {
        EndpointParameterSource.QUERY: 0.5
    }
    # The pair (a, b) is only ever seen as (present, present) and (absent,
    # present): two of four combinations, so simple 2-way leaves it uncovered
    # while total 2-way credits the two observed configurations.
    assert entry.optional_pair_count == 1
    assert entry.simple_2_way_optional_covered_count == 0
    assert entry.simple_2_way_optional_coverage == 0.0
    assert entry.total_2_way_optional_covered_config_count == 2
    assert entry.total_2_way_optional_coverage == 0.5
    assert entry.optional_pair_count_by_source == {EndpointParameterSource.QUERY: 1}
    assert entry.simple_2_way_optional_covered_count_by_source == {
        EndpointParameterSource.QUERY: 0
    }
    assert entry.simple_2_way_optional_coverage_by_source == {
        EndpointParameterSource.QUERY: 0.0
    }
    assert entry.total_2_way_optional_covered_config_count_by_source == {
        EndpointParameterSource.QUERY: 2
    }
    assert entry.total_2_way_optional_coverage_by_source == {
        EndpointParameterSource.QUERY: 0.5
    }


def test_two_way_optional_coverage_full() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="a",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                ),
                EndpointParameter(
                    name="b",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(path="/search", query_param_names=["a", "b"]),
                _event_interaction(path="/search", query_param_names=["a"]),
                _event_interaction(path="/search", query_param_names=["b"]),
                _event_interaction(path="/search"),
            ]
        )
    ]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    # All four present/absent combinations of (a, b) are observed.
    assert entry.optional_pair_count == 1
    assert entry.simple_2_way_optional_covered_count == 1
    assert entry.simple_2_way_optional_coverage == 1.0
    assert entry.total_2_way_optional_covered_config_count == 4
    assert entry.total_2_way_optional_coverage == 1.0
    assert entry.simple_2_way_optional_coverage_by_source == {
        EndpointParameterSource.QUERY: 1.0
    }
    assert entry.total_2_way_optional_coverage_by_source == {
        EndpointParameterSource.QUERY: 1.0
    }
    # Both parameters are observed present and absent.
    assert entry.simple_1_way_optional_covered_count == 2
    assert entry.simple_1_way_optional_coverage == 1.0


def test_one_at_a_time_suite_scores_partial_total_but_zero_simple_2_way() -> None:
    # A suite that sends the bare request plus each optional parameter alone
    # (the dominant hand-written pattern) reaches 3 of 4 configurations on every
    # pair -- both-present is never observed. Simple 2-way stays 0 while total
    # 2-way credits the 9 of 12 observed configurations.
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name=name,
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                )
                for name in ("a", "b", "c")
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(path="/search"),
                _event_interaction(path="/search", query_param_names=["a"]),
                _event_interaction(path="/search", query_param_names=["b"]),
                _event_interaction(path="/search", query_param_names=["c"]),
            ]
        )
    ]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    assert entry.optional_pair_count == 3
    assert entry.simple_2_way_optional_covered_count == 0
    assert entry.simple_2_way_optional_coverage == 0.0
    assert entry.total_2_way_optional_covered_config_count == 9
    assert entry.total_2_way_optional_coverage == 0.75
    assert entry.total_2_way_optional_coverage_by_source == {
        EndpointParameterSource.QUERY: 0.75
    }
    # Every parameter is observed both present and absent.
    assert entry.simple_1_way_optional_coverage == 1.0


def test_two_way_optional_holistic_spans_sources_but_per_source_does_not() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="a",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                ),
                EndpointParameter(
                    name="h",
                    type="java.lang.String",
                    source=EndpointParameterSource.HEADER,
                    required=False,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [
                _event_interaction(
                    path="/search",
                    query_param_names=["a"],
                    header_names=["h"],
                ),
                _event_interaction(path="/search", query_param_names=["a"]),
                _event_interaction(path="/search", header_names=["h"]),
                _event_interaction(path="/search"),
            ]
        )
    ]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    # The cross-source pair (query:a, header:h) sees all four combinations, so the
    # holistic metrics cover it.
    assert entry.optional_pair_count == 1
    assert entry.simple_2_way_optional_covered_count == 1
    assert entry.simple_2_way_optional_coverage == 1.0
    assert entry.total_2_way_optional_covered_config_count == 4
    assert entry.total_2_way_optional_coverage == 1.0
    # Each source has a single optional parameter, so it has zero within-source
    # pairs: the pair count is 0 and the rates are None (no denominator), keyed
    # per present source rather than omitted.
    assert entry.optional_pair_count_by_source == {
        EndpointParameterSource.QUERY: 0,
        EndpointParameterSource.HEADER: 0,
    }
    assert entry.simple_2_way_optional_covered_count_by_source == {
        EndpointParameterSource.QUERY: 0,
        EndpointParameterSource.HEADER: 0,
    }
    assert entry.simple_2_way_optional_coverage_by_source == {
        EndpointParameterSource.QUERY: None,
        EndpointParameterSource.HEADER: None,
    }
    assert entry.total_2_way_optional_covered_config_count_by_source == {
        EndpointParameterSource.QUERY: 0,
        EndpointParameterSource.HEADER: 0,
    }
    assert entry.total_2_way_optional_coverage_by_source == {
        EndpointParameterSource.QUERY: None,
        EndpointParameterSource.HEADER: None,
    }
    # Simple 1-way covers both parameters, per source and holistically.
    assert entry.simple_1_way_optional_covered_count == 2
    assert entry.simple_1_way_optional_coverage == 1.0
    assert entry.simple_1_way_optional_coverage_by_source == {
        EndpointParameterSource.QUERY: 1.0,
        EndpointParameterSource.HEADER: 1.0,
    }
    assert entry.distinct_observed_optional_parameter_set_count == 4
    assert entry.distinct_observed_optional_set_count_by_source == {
        EndpointParameterSource.QUERY: 2,
        EndpointParameterSource.HEADER: 2,
    }


# ---------------------------------------------------------------------------
# N/A rates (None) are kept distinct from a genuine 0.0
# ---------------------------------------------------------------------------


def test_genuine_zero_rate_preserved_and_empty_category_is_none() -> None:
    # One required query parameter, route covered but the parameter never sent:
    # the exercise rate is a genuine 0.0, while the optional side (no optional
    # parameters) is None rather than 0.0.
    endpoints = [
        _make_endpoint(
            http_method="GET",
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
            ],
        ),
    ]
    test_classes = [_make_test_class_analysis([_event_interaction(path="/search")])]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    assert entry.route_covering_test_count == 1
    assert entry.total_parameter_count == 1
    assert entry.exercised_parameter_count == 0
    assert entry.exercise_rate == 0.0
    assert entry.required_exercise_rate == 0.0
    assert entry.optional_exercise_rate is None
    assert entry.simple_1_way_optional_coverage is None
    assert entry.simple_2_way_optional_coverage is None
    assert entry.total_2_way_optional_coverage is None
    # The present query source carries a genuine 0.0 required rate and a None
    # optional rate, paired with a 0 optional count.
    assert entry.required_exercise_rate_by_source == {
        EndpointParameterSource.QUERY: 0.0
    }
    assert entry.optional_parameter_count_by_source == {
        EndpointParameterSource.QUERY: 0
    }
    assert entry.optional_exercise_rate_by_source == {
        EndpointParameterSource.QUERY: None
    }


def test_present_source_without_optional_params_reports_zero_distinct_sets() -> None:
    # query carries only a required parameter; header carries an optional one, so
    # present-sets exist holistically. The query source must still report 0
    # distinct optional sets (not a degenerate 1 for the empty projection),
    # matching its 0 optional count and None simple 1-way coverage.
    endpoints = [
        _make_endpoint(
            http_method="GET",
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
                EndpointParameter(
                    name="h",
                    type="java.lang.String",
                    source=EndpointParameterSource.HEADER,
                    required=False,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [_event_interaction(path="/search?q=1", header_names=["h"])]
        )
    ]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    assert entry.optional_parameter_count_by_source == {
        EndpointParameterSource.QUERY: 0,
        EndpointParameterSource.HEADER: 1,
    }
    assert entry.distinct_observed_optional_set_count_by_source == {
        EndpointParameterSource.QUERY: 0,
        EndpointParameterSource.HEADER: 1,
    }
    assert entry.simple_1_way_optional_coverage_by_source == {
        EndpointParameterSource.QUERY: None,
        EndpointParameterSource.HEADER: 0.0,
    }


def test_all_unscorable_endpoint_has_none_rates() -> None:
    # Every parameter is an unscorable aggregate binding, so there is no scorable
    # surface: holistic rates are None (no denominator) and the per-source dicts
    # are empty because no source is present among the scorable parameters.
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/forms",
            parameters=[
                EndpointParameter(
                    name="form",
                    type="example.Form",
                    source=EndpointParameterSource.FORM,
                    is_unscorable=True,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [_event_interaction(http_method="POST", path="/forms")]
        )
    ]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]

    assert entry.route_covering_test_count == 1
    assert entry.total_parameter_count == 0
    assert entry.exercise_rate is None
    assert entry.required_exercise_rate is None
    assert entry.optional_exercise_rate is None
    assert entry.simple_1_way_optional_coverage is None
    assert entry.simple_2_way_optional_coverage is None
    assert entry.total_2_way_optional_coverage is None
    assert entry.required_exercise_rate_by_source == {}
    assert entry.optional_exercise_rate_by_source == {}
    assert entry.simple_2_way_optional_coverage_by_source == {}
    assert entry.total_2_way_optional_coverage_by_source == {}
    # No scorable surface: bucketed as unscorable rather than exercised.
    assert result.unscorable_endpoint_count == 1


def test_none_rates_survive_exclude_defaults_but_genuine_zero_serializes() -> None:
    # exclude_defaults drops a None rate (it equals the default) but keeps a
    # genuine 0.0, so absence in the dump means N/A and a present 0.0 means a real
    # zero -- exactly the distinction downstream distribution analysis needs.
    endpoints = [
        _make_endpoint(
            http_method="GET",
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="q",
                    type="java.lang.String",
                    source=EndpointParameterSource.QUERY,
                ),
            ],
        ),
    ]
    test_classes = [_make_test_class_analysis([_event_interaction(path="/search")])]

    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    dumped = result.endpoints[0].model_dump(mode="json", exclude_defaults=True)

    assert dumped["exercise_rate"] == 0.0
    assert dumped["required_exercise_rate"] == 0.0
    assert "optional_exercise_rate" not in dumped
    assert "simple_2_way_optional_coverage" not in dumped
    assert "total_2_way_optional_coverage" not in dumped


# ---------------------------------------------------------------------------
# Name extraction: defaultValue / other attributes must not become the name
# ---------------------------------------------------------------------------


def test_spring_request_param_default_value_not_used_as_name() -> None:
    method = make_callable(
        signature="list(int)",
        annotations=['@GetMapping("/items")'],
        parameters=[
            make_callable_parameter(
                name="limit",
                type_name="int",
                annotations=['@RequestParam(defaultValue = "100")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestParam",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert len(result) == 1
    # The Java parameter name, NOT the defaultValue literal "100".
    assert result[0].name == "limit"
    # defaultValue implies optional.
    assert result[0].required is False


def test_spring_request_param_value_attribute_preferred_over_default_value() -> None:
    method = make_callable(
        signature="list(int)",
        annotations=['@GetMapping("/items")'],
        parameters=[
            make_callable_parameter(
                name="size",
                type_name="int",
                annotations=['@RequestParam(value = "pageSize", defaultValue = "20")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestParam",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert result[0].name == "pageSize"
    assert result[0].required is False


# ---------------------------------------------------------------------------
# Requiredness signals (Q2)
# ---------------------------------------------------------------------------


def test_jaxrs_query_param_default_value_literal_is_optional() -> None:
    method = make_callable(
        signature="list(int)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="limit",
                type_name="int",
                annotations=['@QueryParam("limit")', '@DefaultValue("100")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.QueryParam",
        "javax.ws.rs.DefaultValue",
        "javax.ws.rs.GET",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result[0].name == "limit"
    assert result[0].required is False


def test_jaxrs_query_param_default_value_constant_is_optional() -> None:
    method = make_callable(
        signature="search(SearchSort)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="sort",
                type_name="example.SearchSort",
                annotations=['@QueryParam("sort")', "@DefaultValue(DEFAULT_SORT)"],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.QueryParam",
        "javax.ws.rs.DefaultValue",
        "javax.ws.rs.GET",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    # A constant default still marks the parameter optional; we never resolve it.
    assert result[0].name == "sort"
    assert result[0].required is False


def test_jaxrs_query_param_nullable_sibling_is_optional() -> None:
    method = make_callable(
        signature="search(java.lang.String)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="q",
                type_name="java.lang.String",
                annotations=["@Nullable", '@QueryParam("q")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.QueryParam",
        "javax.ws.rs.GET",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result[0].required is False


def test_jaxrs_query_param_without_signals_is_required() -> None:
    method = make_callable(
        signature="list(int)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="limit",
                type_name="int",
                annotations=['@QueryParam("limit")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.QueryParam",
        "javax.ws.rs.GET",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result[0].required is True


def test_is_jax_rs_optionality_sibling_annotation_default_value_javax_import() -> None:
    assert is_jax_rs_optionality_sibling_annotation('@DefaultValue("10")') is True


def test_is_jax_rs_optionality_sibling_annotation_default_value_matches_by_short_name() -> (
    None
):
    assert is_jax_rs_optionality_sibling_annotation('@DefaultValue("10")') is True


def test_is_jax_rs_optionality_sibling_annotation_nullable_any_package() -> None:
    assert is_jax_rs_optionality_sibling_annotation("@Nullable") is True


def test_is_jax_rs_optionality_sibling_annotation_query_param_is_false() -> None:
    assert is_jax_rs_optionality_sibling_annotation('@QueryParam("limit")') is False


def test_is_jax_rs_optionality_sibling_annotation_override_is_false() -> None:
    assert is_jax_rs_optionality_sibling_annotation("@Override") is False


def test_spring_request_header_required_false_is_optional() -> None:
    method = make_callable(
        signature="get(java.lang.String)",
        annotations=['@GetMapping("/x")'],
        parameters=[
            make_callable_parameter(
                name="acceptHeader",
                type_name="java.lang.String",
                annotations=['@RequestHeader(value = "Accept", required = false)'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestHeader",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert result[0].source == EndpointParameterSource.HEADER
    assert result[0].required is False


def test_spring_request_body_required_false_is_optional() -> None:
    method = make_callable(
        signature="put(example.Dto)",
        annotations=['@PutMapping("/x")'],
        parameters=[
            make_callable_parameter(
                name="incoming",
                type_name="example.Dto",
                annotations=["@Nullable", "@RequestBody(required = false)"],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestBody",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert result[0].source == EndpointParameterSource.BODY
    assert result[0].required is False


def test_spring_optional_wrapper_type_is_optional() -> None:
    method = make_callable(
        signature="search(java.util.Optional)",
        annotations=['@GetMapping("/x")'],
        parameters=[
            make_callable_parameter(
                name="q",
                type_name="java.util.Optional<java.lang.String>",
                annotations=['@RequestParam("q")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestParam",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert result[0].required is False


# ---------------------------------------------------------------------------
# Aggregate / open query surfaces (Q1)
# ---------------------------------------------------------------------------


def test_unnamed_multivaluemap_query_is_aggregate() -> None:
    method = make_callable(
        signature="search(org.springframework.util.MultiValueMap)",
        annotations=['@GetMapping("/search")'],
        parameters=[
            make_callable_parameter(
                name="parameters",
                type_name="org.springframework.util.MultiValueMap<java.lang.String, java.lang.Object>",
                annotations=["@RequestParam"],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestParam",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert len(result) == 1
    assert result[0].source == EndpointParameterSource.QUERY
    assert result[0].name == "parameters"
    assert result[0].is_aggregate is True
    assert result[0].required is False


def test_named_map_query_is_not_aggregate() -> None:
    method = make_callable(
        signature="search(java.util.Map)",
        annotations=['@GetMapping("/search")'],
        parameters=[
            make_callable_parameter(
                name="filters",
                type_name="java.util.Map<java.lang.String, java.lang.String>",
                annotations=['@RequestParam("filters")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestParam",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert result[0].name == "filters"
    assert result[0].is_aggregate is False


def test_aggregate_query_param_exercised_when_any_query_present() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="parameters",
                    type="org.springframework.util.MultiValueMap",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                    is_aggregate=True,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [_event_interaction(path="/search", query_param_names=["name"])]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is True


def test_aggregate_query_param_not_exercised_without_query() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/search",
            parameters=[
                EndpointParameter(
                    name="parameters",
                    type="org.springframework.util.MultiValueMap",
                    source=EndpointParameterSource.QUERY,
                    required=False,
                    is_aggregate=True,
                ),
            ],
        ),
    ]
    test_classes = [_make_test_class_analysis([_event_interaction(path="/search")])]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    assert result.endpoints[0].parameter_entries[0].is_exercised is False


# ---------------------------------------------------------------------------
# Spring mapping-level params= constraint (Q3)
# ---------------------------------------------------------------------------


def _spring_imports() -> list[JImport]:
    return make_import_declarations(
        "org.springframework.web.bind.annotation.GetMapping",
    )


def test_mapping_params_constraint_adds_query_param() -> None:
    endpoints = _extract_spring_endpoints(
        qualified_class_name="example.Controller",
        method_signature="list()",
        class_paths=[""],
        method_annotations=['@GetMapping(path = "/instances", params = "name")'],
        class_imports=_spring_imports(),
        endpoint_parameters=[],
    )
    assert len(endpoints) == 1
    params = endpoints[0].parameters
    assert len(params) == 1
    assert params[0].name == "name"
    assert params[0].source == EndpointParameterSource.QUERY
    assert params[0].required is True
    assert params[0].annotation == "@GetMapping"
    # Synthesized from a mapping constraint: no backing Java argument/type.
    assert params[0].is_synthetic is True
    assert params[0].type is None


def test_mapping_params_constraint_deduped_against_method_param() -> None:
    existing = [
        EndpointParameter(
            name="name",
            type="java.lang.String",
            source=EndpointParameterSource.QUERY,
        ),
    ]
    endpoints = _extract_spring_endpoints(
        qualified_class_name="example.Controller",
        method_signature="list(java.lang.String)",
        class_paths=[""],
        method_annotations=['@GetMapping(path = "/instances", params = "name")'],
        class_imports=_spring_imports(),
        endpoint_parameters=existing,
    )
    params = endpoints[0].parameters
    # No duplicate "name" query parameter introduced.
    query_names = [p.name for p in params if p.source == EndpointParameterSource.QUERY]
    assert query_names == ["name"]


def test_mapping_params_constraint_skips_negations_and_keeps_positive() -> None:
    endpoints = _extract_spring_endpoints(
        qualified_class_name="example.Controller",
        method_signature="list()",
        class_paths=[""],
        method_annotations=[
            '@GetMapping(path = "/x", params = {"active", "!trace", "mode=full",'
            ' "kind!=hidden"})'
        ],
        class_imports=_spring_imports(),
        endpoint_parameters=[],
    )
    names = sorted(
        p.name
        for p in endpoints[0].parameters
        if p.source == EndpointParameterSource.QUERY
    )
    # Presence/equality ("active", "mode=full") are kept. Both negation forms are
    # skipped: absence ("!trace") and inequality ("kind!=hidden") — Spring matches
    # an inequality constraint even when the parameter is absent, so it is not
    # required.
    assert names == ["active", "mode"]


def test_mapping_params_inequality_constraint_is_not_required() -> None:
    # "x!=v" is satisfied by an absent x (Spring negates the value match), so x
    # is not a required parameter and contributes no constraint.
    endpoints = _extract_spring_endpoints(
        qualified_class_name="example.Controller",
        method_signature="list()",
        class_paths=[""],
        method_annotations=['@GetMapping(path = "/x", params = "status!=archived")'],
        class_imports=_spring_imports(),
        endpoint_parameters=[],
    )
    query_names = [
        p.name
        for p in endpoints[0].parameters
        if p.source == EndpointParameterSource.QUERY
    ]
    assert query_names == []


def test_mapping_params_value_containing_bang_is_kept() -> None:
    # A '!' inside the constraint *value* is not a negation; "token" is required.
    endpoints = _extract_spring_endpoints(
        qualified_class_name="example.Controller",
        method_signature="list()",
        class_paths=[""],
        method_annotations=['@GetMapping(path = "/x", params = "token=a!b")'],
        class_imports=_spring_imports(),
        endpoint_parameters=[],
    )
    names = [
        p.name
        for p in endpoints[0].parameters
        if p.source == EndpointParameterSource.QUERY
    ]
    assert names == ["token"]


def test_mapping_headers_constraint_adds_required_header_param() -> None:
    endpoints = _extract_spring_endpoints(
        qualified_class_name="example.Controller",
        method_signature="get()",
        class_paths=[""],
        method_annotations=['@GetMapping(path = "/x", headers = "X-API-Version=2")'],
        class_imports=_spring_imports(),
        endpoint_parameters=[],
    )
    headers = [
        p for p in endpoints[0].parameters if p.source == EndpointParameterSource.HEADER
    ]
    assert len(headers) == 1
    assert headers[0].name == "X-API-Version"
    assert headers[0].required is True
    assert headers[0].is_synthetic is True


def test_class_level_mapping_params_constraint_adds_query_param() -> None:
    # A class-level @RequestMapping(params=...) applies to every method endpoint.
    endpoints = _extract_spring_endpoints(
        qualified_class_name="example.Controller",
        method_signature="list()",
        class_paths=["/api"],
        method_annotations=['@GetMapping("/instances")'],
        class_imports=make_import_declarations(
            "org.springframework.web.bind.annotation.GetMapping",
            "org.springframework.web.bind.annotation.RequestMapping",
        ),
        endpoint_parameters=[],
        class_mapping_annotations=['@RequestMapping(path = "/api", params = "tenant")'],
    )
    assert len(endpoints) == 1
    query_names = [
        p.name
        for p in endpoints[0].parameters
        if p.source == EndpointParameterSource.QUERY
    ]
    assert query_names == ["tenant"]


def test_class_level_mapping_params_constraint_deduped_against_method() -> None:
    # A method-level params= for the same name must not double-add it.
    endpoints = _extract_spring_endpoints(
        qualified_class_name="example.Controller",
        method_signature="list()",
        class_paths=["/api"],
        method_annotations=['@GetMapping(path = "/instances", params = "tenant")'],
        class_imports=make_import_declarations(
            "org.springframework.web.bind.annotation.GetMapping",
            "org.springframework.web.bind.annotation.RequestMapping",
        ),
        endpoint_parameters=[],
        class_mapping_annotations=['@RequestMapping(path = "/api", params = "tenant")'],
    )
    query_names = [
        p.name
        for p in endpoints[0].parameters
        if p.source == EndpointParameterSource.QUERY
    ]
    assert query_names == ["tenant"]


# ---------------------------------------------------------------------------
# Micronaut defaultValue optionality (Q3 follow-up)
# ---------------------------------------------------------------------------


def test_micronaut_query_value_default_value_is_optional() -> None:
    method = make_callable(
        signature="list(int)",
        annotations=['@Get("/list")'],
        parameters=[
            make_callable_parameter(
                name="size",
                type_name="int",
                annotations=['@QueryValue(defaultValue = "10")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "io.micronaut.http.annotation.QueryValue",
        "io.micronaut.http.annotation.Get",
    )
    result = _extract_method_parameters(
        method, framework="micronaut", class_imports=imports
    )
    assert result[0].name == "size"
    # defaultValue implies optional for Micronaut just as for Spring.
    assert result[0].required is False


# ---------------------------------------------------------------------------
# Empty explicit name falls back to the Java parameter name
# ---------------------------------------------------------------------------


def test_spring_request_param_empty_name_falls_back_to_java_name() -> None:
    method = make_callable(
        signature="search(java.lang.String)",
        annotations=['@GetMapping("/search")'],
        parameters=[
            make_callable_parameter(
                name="q",
                type_name="java.lang.String",
                annotations=['@RequestParam(name = "")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestParam",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    # Empty name is not a usable name; fall back to the Java parameter name.
    assert result[0].name == "q"


def test_spring_request_param_empty_name_map_is_aggregate() -> None:
    method = make_callable(
        signature="search(java.util.Map)",
        annotations=['@GetMapping("/search")'],
        parameters=[
            make_callable_parameter(
                name="filters",
                type_name="java.util.Map<java.lang.String, java.lang.String>",
                annotations=['@RequestParam(name = "")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.RequestParam",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    # An empty name does not pin the Map to a single key; it is an open surface.
    assert result[0].is_aggregate is True
    assert result[0].required is False


# ---------------------------------------------------------------------------
# Aggregate open-query surfaces are framework-specific (#4)
# ---------------------------------------------------------------------------


def test_micronaut_unnamed_map_query_is_aggregate() -> None:
    # Micronaut binds an unnamed @QueryValue Map to the whole query string.
    method = make_callable(
        signature="search(java.util.Map)",
        annotations=['@Get("/search")'],
        parameters=[
            make_callable_parameter(
                name="parameters",
                type_name="java.util.Map<java.lang.String, java.lang.Object>",
                annotations=["@QueryValue"],
            ),
        ],
    )
    imports = make_import_declarations(
        "io.micronaut.http.annotation.QueryValue",
        "io.micronaut.http.annotation.Get",
    )
    result = _extract_method_parameters(
        method, framework="micronaut", class_imports=imports
    )
    assert result[0].source == EndpointParameterSource.QUERY
    assert result[0].is_aggregate is True
    assert result[0].required is False


def test_jaxrs_unnamed_map_query_is_not_aggregate() -> None:
    # JAX-RS @QueryParam binds a single named value; there is no bind-all Map
    # form, so an unnamed Map-typed query param is NOT an open surface.
    method = make_callable(
        signature="search(java.util.Map)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="filters",
                type_name="java.util.Map<java.lang.String, java.lang.String>",
                annotations=["@QueryParam"],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.QueryParam",
        "javax.ws.rs.GET",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result[0].source == EndpointParameterSource.QUERY
    assert result[0].is_aggregate is False
    # Not an open surface, so the per-signal requiredness applies (required).
    assert result[0].required is True


# ---------------------------------------------------------------------------
# Path variables are always required regardless of optionality signals (#1)
# ---------------------------------------------------------------------------


def test_spring_path_variable_optional_wrapper_is_required() -> None:
    method = make_callable(
        signature="get(java.util.Optional)",
        annotations=['@GetMapping("/items/{id}")'],
        parameters=[
            make_callable_parameter(
                name="id",
                type_name="java.util.Optional<java.lang.Long>",
                annotations=['@PathVariable("id")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.PathVariable",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert result[0].source == EndpointParameterSource.PATH
    # A path segment is structurally part of the URI; Optional<> does not relax it.
    assert result[0].required is True


def test_spring_path_variable_nullable_sibling_is_required() -> None:
    method = make_callable(
        signature="get(java.lang.Long)",
        annotations=['@GetMapping("/items/{id}")'],
        parameters=[
            make_callable_parameter(
                name="id",
                type_name="java.lang.Long",
                annotations=["@Nullable", '@PathVariable("id")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "org.springframework.web.bind.annotation.PathVariable",
    )
    result = _extract_method_parameters(
        method, framework="spring", class_imports=imports
    )
    assert result[0].source == EndpointParameterSource.PATH
    assert result[0].required is True


def test_jaxrs_path_param_default_value_sibling_is_required() -> None:
    # A sibling @DefaultValue would mark a query/header param optional, but a
    # path param is always required for the endpoint that contains it.
    method = make_callable(
        signature="get(java.lang.String)",
        annotations=["@GET"],
        parameters=[
            make_callable_parameter(
                name="id",
                type_name="java.lang.String",
                annotations=['@PathParam("id")', '@DefaultValue("0")'],
            ),
        ],
    )
    imports = make_import_declarations(
        "javax.ws.rs.PathParam",
        "javax.ws.rs.DefaultValue",
        "javax.ws.rs.GET",
    )
    result = _extract_method_parameters(
        method, framework="jax-rs", class_imports=imports
    )
    assert result[0].source == EndpointParameterSource.PATH
    assert result[0].required is True


# ---------------------------------------------------------------------------
# Path-parameter reconciliation: the route template is the source of truth for
# path-variable existence + requiredness; a recognized annotation only enriches
# the declared type.
# ---------------------------------------------------------------------------


def test_template_path_variable_names_basic() -> None:
    assert _template_path_variable_names("/users/{id}") == ["id"]
    assert _template_path_variable_names(
        "/ns/{namespace}/datasets/{dataset}/fields/{field}/tags/{tag}"
    ) == ["namespace", "dataset", "field", "tag"]
    assert _template_path_variable_names("/users") == []


def test_template_path_variable_names_catch_all_and_regex() -> None:
    # The catch-all '*' marker and a ':regex' constraint reduce to the bare name.
    assert _template_path_variable_names("/files/{*rest}") == ["rest"]
    assert _template_path_variable_names(r"/items/{id:\d+}") == ["id"]
    # Multiple variables in a single segment are each recognized.
    assert _template_path_variable_names("/range/{from}-{to}") == ["from", "to"]


def test_template_path_variable_names_ignores_query_and_dedupes() -> None:
    # A '{}' in the query string is not a path variable.
    assert _template_path_variable_names("/a/{b}?x={y}") == ["b"]
    # A repeated name is reported once.
    assert _template_path_variable_names("/a/{id}/b/{id}") == ["id"]


def test_template_path_variable_names_strips_optional_slash_marker() -> None:
    # RFC 6570 ``{/var}`` declares the same variable as ``{var}``.
    assert _template_path_variable_names("/books{/id}") == ["id"]


def test_reconcile_enriches_recognized_path_param() -> None:
    declared = EndpointParameter(
        name="id",
        type="java.lang.Long",
        source=EndpointParameterSource.PATH,
        required=True,
        annotation="@PathVariable",
    )
    result = _reconcile_path_parameters("/users/{id}", [declared])
    assert len(result) == 1
    # The declared parameter is retained verbatim (real type + annotation).
    assert result[0] is declared
    assert result[0].is_synthetic is False
    assert result[0].type == "java.lang.Long"


def test_reconcile_synthesizes_template_var_without_annotation() -> None:
    # Problem A: a {var} bound by an unrecognized mechanism (e.g. @BackendId)
    # still yields a synthetic, required, type-less PATH parameter.
    body = EndpointParameter(
        name="payload",
        type="example.Dto",
        source=EndpointParameterSource.BODY,
    )
    result = _reconcile_path_parameters("/things/{id}", [body])
    path_params = [p for p in result if p.source == EndpointParameterSource.PATH]
    assert len(path_params) == 1
    assert path_params[0].name == "id"
    assert path_params[0].required is True
    assert path_params[0].is_synthetic is True
    assert path_params[0].type is None
    # Non-path parameters pass through unchanged.
    assert body in result


def test_reconcile_drops_path_param_absent_from_template() -> None:
    # Problem B: a declared path param whose {var} is not in THIS template is
    # dropped (the no-variable form of a multi-template handler).
    declared = EndpointParameter(
        name="id",
        type="java.lang.Long",
        source=EndpointParameterSource.PATH,
    )
    result = _reconcile_path_parameters("/users", [declared])
    assert [p for p in result if p.source == EndpointParameterSource.PATH] == []


def test_reconcile_orders_path_params_by_template() -> None:
    dataset = EndpointParameter(name="dataset", source=EndpointParameterSource.PATH)
    namespace = EndpointParameter(name="namespace", source=EndpointParameterSource.PATH)
    # Declared out of order; reconciliation follows template order.
    result = _reconcile_path_parameters(
        "/ns/{namespace}/datasets/{dataset}", [dataset, namespace]
    )
    assert [p.name for p in result] == ["namespace", "dataset"]


def test_spring_multi_template_scopes_path_params_per_template() -> None:
    # Problem B end-to-end: {"/users","/users/{id}"} expands into two endpoints;
    # only the {id} form carries the path parameter.
    declared = EndpointParameter(
        name="id",
        type="java.lang.Long",
        source=EndpointParameterSource.PATH,
        required=True,
        annotation="@PathVariable",
    )
    endpoints = _extract_spring_endpoints(
        qualified_class_name="example.Controller",
        method_signature="get(java.lang.Long)",
        class_paths=[""],
        method_annotations=['@GetMapping({"/users", "/users/{id}"})'],
        class_imports=_spring_imports(),
        endpoint_parameters=[declared],
    )
    by_template = {endpoint.path_template: endpoint for endpoint in endpoints}
    assert set(by_template) == {"/users", "/users/{id}"}
    with_var = by_template["/users/{id}"]
    assert [p.name for p in with_var.parameters] == ["id"]
    assert with_var.parameters[0].is_synthetic is False
    assert with_var.parameters[0].type == "java.lang.Long"
    assert by_template["/users"].parameters == []


def test_jaxrs_multi_var_template_orders_and_retains_params() -> None:
    namespace = EndpointParameter(
        name="namespace",
        type="example.NamespaceName",
        source=EndpointParameterSource.PATH,
        annotation="@PathParam",
    )
    dataset = EndpointParameter(
        name="dataset",
        type="example.DatasetName",
        source=EndpointParameterSource.PATH,
        annotation="@PathParam",
    )
    endpoints = _extract_jax_rs_endpoints(
        qualified_class_name="example.Resource",
        method_signature="get(java.lang.String,java.lang.String)",
        class_paths=[""],
        method_annotations=["@GET", '@Path("/ns/{namespace}/datasets/{dataset}")'],
        class_imports=make_import_declarations(
            "javax.ws.rs.GET",
            "javax.ws.rs.Path",
        ),
        # Declared out of order to prove template ordering wins.
        endpoint_parameters=[dataset, namespace],
    )
    assert len(endpoints) == 1
    params = endpoints[0].parameters
    assert [p.name for p in params] == ["namespace", "dataset"]
    assert all(not p.is_synthetic for p in params)
    # RQ4 (template tokens) and RQ5 (path parameters) agree by construction.
    assert endpoints[0].surface.path_variable_count == len(params)


def test_synthesized_path_param_is_exercised_on_route_match() -> None:
    # End-to-end: a {id} bound by an unrecognized annotation is synthesized and
    # then exercised by a test that hits the route.
    endpoints = _extract_spring_endpoints(
        qualified_class_name="example.Controller",
        method_signature="get(java.io.Serializable)",
        class_paths=["/things"],
        method_annotations=['@GetMapping("/{id}")'],
        class_imports=_spring_imports(),
        endpoint_parameters=[],  # an @BackendId-style binding is not recognized
    )
    assert len(endpoints) == 1
    endpoint = endpoints[0]
    assert endpoint.path_template == "/things/{id}"
    assert [p.name for p in endpoint.parameters] == ["id"]
    assert endpoint.parameters[0].is_synthetic is True
    assert endpoint.surface.path_variable_count == 1

    test_classes = [
        _make_test_class_analysis(
            [_event_interaction(http_method="GET", path="/things/42")]
        )
    ]
    result = build_endpoint_parameter_coverage_summary([endpoint], test_classes)
    entry = result.endpoints[0]
    assert entry.exercised_parameter_count == 1
    assert entry.total_parameter_count == 1
    assert result.fully_exercised_endpoint_count == 1


def test_extract_application_endpoints_synthesizes_custom_path_binding() -> None:
    # @BackendId is a real spring-data-rest custom binding annotation that is not
    # in the recognized source map; its {id} template variable must still be
    # modeled as a (synthetic) path parameter.
    class_name = "example.RepositoryController"
    java_file = _java_file(class_name)
    analysis = FakeJavaAnalysis(
        classes={class_name: make_type(annotations=["@RestController"])},
        methods_by_class={
            class_name: {
                "get(java.io.Serializable)": make_callable(
                    signature="get(java.io.Serializable)",
                    annotations=['@GetMapping("/things/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="java.io.Serializable",
                            annotations=["@BackendId"],
                        ),
                    ],
                ),
            },
        },
        java_files={class_name: java_file},
        import_declarations_by_file={
            java_file: make_import_declarations(
                "org.springframework.web.bind.annotation.RestController",
                "org.springframework.web.bind.annotation.GetMapping",
                "org.springframework.data.rest.webmvc.support.BackendId",
            ),
        },
    )
    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[class_name],
    ).endpoints
    assert len(endpoints) == 1
    path_params = [
        p for p in endpoints[0].parameters if p.source == EndpointParameterSource.PATH
    ]
    assert len(path_params) == 1
    assert path_params[0].name == "id"
    assert path_params[0].is_synthetic is True
    assert path_params[0].type is None
    assert endpoints[0].surface.path_variable_count == 1


def test_template_path_variable_names_skips_spring_property_placeholder() -> None:
    # ${...} is a Spring configuration placeholder resolved at startup, not a
    # path variable; only the real {id} variable is reported.
    assert _template_path_variable_names("/${api.base-path}/users/{id}") == ["id"]
    # A placeholder carrying a default value ( ${name:default} ) is still skipped.
    assert _template_path_variable_names("/${svc:default}/x/{key}") == ["key"]


def test_template_path_variable_names_handles_brace_quantifier_regex() -> None:
    # A regex constraint with a brace quantifier is one variable, not two.
    assert _template_path_variable_names("/items/{id:[0-9]{4}}") == ["id"]
    assert _template_path_variable_names("/items/{id:[0-9]{2,4}}/{rev}") == [
        "id",
        "rev",
    ]


def test_reconcile_enriches_brace_quantifier_regex_param() -> None:
    # The declared id must be retained (enriched), not dropped and replaced by a
    # spurious synthetic "4" parsed out of the {4} quantifier.
    declared = EndpointParameter(
        name="id",
        type="java.lang.String",
        source=EndpointParameterSource.PATH,
        annotation="@PathVariable",
    )
    result = _reconcile_path_parameters("/items/{id:[0-9]{4}}", [declared])
    assert len(result) == 1
    assert result[0] is declared
    assert result[0].is_synthetic is False


def test_reconcile_ignores_property_placeholder() -> None:
    # A ${...} placeholder must not become a synthetic path parameter.
    declared = EndpointParameter(
        name="id",
        type="java.lang.Long",
        source=EndpointParameterSource.PATH,
        annotation="@PathVariable",
    )
    result = _reconcile_path_parameters("/${api.base-path}/users/{id}", [declared])
    assert [p.name for p in result] == ["id"]
    assert result[0].is_synthetic is False


# ---------------------------------------------------------------------------
# Coverage exclusion of unscorable structured bindings
# ---------------------------------------------------------------------------


def test_coverage_excludes_unscorable_parameters_from_totals() -> None:
    endpoints = [
        _make_endpoint(
            path_template="/owners/{id}",
            parameters=[
                EndpointParameter(
                    name="id",
                    type="java.lang.String",
                    source=EndpointParameterSource.PATH,
                ),
                EndpointParameter(
                    name="owner",
                    source=EndpointParameterSource.UNKNOWN,
                    required=False,
                    annotation="@ModelAttribute",
                    is_unscorable=True,
                ),
            ],
        ),
    ]
    test_classes = [_make_test_class_analysis([_event_interaction(path="/owners/42")])]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]
    # Only the scorable PATH parameter is counted; the @ModelAttribute binding
    # is excluded from the denominator and from the parameter entries. The
    # endpoint still has a scorable parameter, so it is fully exercised, not
    # bucketed as unscorable.
    assert entry.total_parameter_count == 1
    assert entry.exercised_parameter_count == 1
    assert [pe.parameter.name for pe in entry.parameter_entries] == ["id"]
    assert result.fully_exercised_endpoint_count == 1
    assert result.unscorable_endpoint_count == 0


def test_all_unscorable_endpoint_bucketed_unscorable_when_route_covered() -> None:
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/owners",
            parameters=[
                EndpointParameter(
                    name="owner",
                    source=EndpointParameterSource.UNKNOWN,
                    required=False,
                    annotation="@ModelAttribute",
                    is_unscorable=True,
                ),
            ],
        ),
    ]
    test_classes = [
        _make_test_class_analysis(
            [_event_interaction(http_method="POST", path="/owners")]
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, test_classes)
    entry = result.endpoints[0]
    # Route coverage is still recorded on the entry, but an endpoint with no
    # measurable surface is bucketed unscorable rather than claimed as fully
    # exercised.
    assert result.total_endpoints_with_parameters == 1
    assert result.unscorable_endpoint_count == 1
    assert result.fully_exercised_endpoint_count == 0
    assert entry.total_parameter_count == 0
    assert entry.route_covering_test_count == 1


def test_all_unscorable_endpoint_bucketed_unscorable_when_no_test_targets_it() -> None:
    endpoints = [
        _make_endpoint(
            http_method="POST",
            path_template="/owners",
            parameters=[
                EndpointParameter(
                    name="owner",
                    source=EndpointParameterSource.UNKNOWN,
                    required=False,
                    annotation="@ModelAttribute",
                    is_unscorable=True,
                ),
            ],
        ),
    ]
    result = build_endpoint_parameter_coverage_summary(endpoints, [])
    # Route coverage does not move the bucket: an all-unscorable endpoint is
    # unscorable whether or not a test targets it.
    assert result.total_endpoints_with_parameters == 1
    assert result.unscorable_endpoint_count == 1
    assert result.unexercised_endpoint_count == 0
    assert result.fully_exercised_endpoint_count == 0
