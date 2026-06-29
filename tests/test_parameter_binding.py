from __future__ import annotations

from gerbil.analysis.schema import EndpointParameterSource
from gerbil.analysis.shared import CommonAnalysis
from gerbil.analysis.shared.constant_resolution import ConstantResolver
from gerbil.analysis.shared.parameter_binding import (
    annotation_explicit_name,
    classify_annotation_parameter_binding,
    extract_request_parameter_bindings,
    is_aggregate_query_surface,
    parameter_is_required,
    simple_type_name,
)
from tests.cldk_factories import (
    make_callable_parameter,
    make_field,
    make_import_declaration,
    make_import_declarations,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis

_SPRING_IMPORTS = make_import_declarations(
    "org.springframework.web.bind.annotation.PathVariable",
    "org.springframework.web.bind.annotation.RequestParam",
    "org.springframework.web.bind.annotation.RequestBody",
    "org.springframework.web.bind.annotation.RequestHeader",
    "org.springframework.web.bind.annotation.RequestPart",
)
_JAX_RS_IMPORTS = make_import_declarations(
    "jakarta.ws.rs.QueryParam",
    "jakarta.ws.rs.HeaderParam",
)
_MICRONAUT_IMPORTS = make_import_declarations(
    "io.micronaut.http.annotation.QueryValue",
)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_simple_type_name_strips_package_generics_and_array() -> None:
    assert simple_type_name("java.util.List<java.lang.String>") == "List"
    assert simple_type_name("com.example.Order[]") == "Order"
    assert simple_type_name("String") == "String"


def test_annotation_explicit_name_prefers_attribute_then_positional() -> None:
    # Inputs are annotation bodies (the parens are already stripped upstream).
    assert annotation_explicit_name('"q"') == "q"
    assert annotation_explicit_name('name = "q"') == "q"
    assert annotation_explicit_name('value = "q"') == "q"
    # A defaultValue literal must not be mistaken for the parameter name.
    assert annotation_explicit_name('defaultValue = "100"') is None
    # An empty positional literal falls back to the Java parameter name.
    assert annotation_explicit_name('""') is None
    assert annotation_explicit_name("") is None


def test_annotation_explicit_name_ignores_positional_compound_literal() -> None:
    # A positional string literal that does not span the whole argument is a
    # compound expression; we cannot resolve the name, so fall back rather than
    # emit a truncated prefix.
    assert annotation_explicit_name('"foo" + SUFFIX') is None
    assert annotation_explicit_name('"foo" + "Id"') is None
    assert annotation_explicit_name('"foo".concat(suffix)') is None
    # A plain literal still resolves, and a trailing attribute argument
    # (separated by a comma) does not block the leading positional name.
    assert annotation_explicit_name('"foo"') == "foo"
    assert annotation_explicit_name('"foo", required = false') == "foo"


def test_parameter_is_required_signals() -> None:
    # PATH is always required regardless of optionality signals.
    assert (
        parameter_is_required(
            source=EndpointParameterSource.PATH,
            body="(required = false)",
            framework="spring",
            sibling_annotation_short_names=set(),
            simple_type_name="String",
        )
        is True
    )
    assert (
        parameter_is_required(
            source=EndpointParameterSource.QUERY,
            body="(required = false)",
            framework="spring",
            sibling_annotation_short_names=set(),
            simple_type_name="String",
        )
        is False
    )
    # Optional<> wrapper marks the parameter optional.
    assert (
        parameter_is_required(
            source=EndpointParameterSource.QUERY,
            body="",
            framework="spring",
            sibling_annotation_short_names=set(),
            simple_type_name="Optional",
        )
        is False
    )


def test_is_aggregate_query_surface_only_for_unnamed_map_query() -> None:
    assert (
        is_aggregate_query_surface(
            framework="spring",
            source=EndpointParameterSource.QUERY,
            explicit_name=None,
            simple_type_name="Map",
        )
        is True
    )
    # A named map is a single fixed parameter, not an open surface.
    assert (
        is_aggregate_query_surface(
            framework="spring",
            source=EndpointParameterSource.QUERY,
            explicit_name="filters",
            simple_type_name="Map",
        )
        is False
    )
    # JAX-RS has no bind-all map form.
    assert (
        is_aggregate_query_surface(
            framework="jax-rs",
            source=EndpointParameterSource.QUERY,
            explicit_name=None,
            simple_type_name="Map",
        )
        is False
    )


# --------------------------------------------------------------------------- #
# classify_annotation_parameter_binding
# --------------------------------------------------------------------------- #


def test_classify_binding_maps_each_spring_source() -> None:
    cases = {
        '@PathVariable("id")': EndpointParameterSource.PATH,
        '@RequestParam("q")': EndpointParameterSource.QUERY,
        "@RequestBody": EndpointParameterSource.BODY,
        '@RequestHeader("X-Tenant")': EndpointParameterSource.HEADER,
        '@RequestPart("file")': EndpointParameterSource.FORM,
    }
    for annotation, expected_source in cases.items():
        param = make_callable_parameter(
            name="value",
            type_name="java.lang.String",
            annotations=[annotation],
        )
        binding = classify_annotation_parameter_binding(
            param=param,
            short_name=annotation.split("(", 1)[0],
            annotation=annotation,
            framework="spring",
            class_imports=_SPRING_IMPORTS,
            sibling_annotation_short_names={annotation.split("(", 1)[0]},
            simple_type_name="String",
        )
        assert binding is not None
        assert binding.source == expected_source


def test_classify_binding_returns_none_for_unmapped_or_unvalidated() -> None:
    param = make_callable_parameter(
        name="value", type_name="java.lang.String", annotations=["@Valid"]
    )
    # Unmapped annotation.
    assert (
        classify_annotation_parameter_binding(
            param=param,
            short_name="@Valid",
            annotation="@Valid",
            framework="spring",
            class_imports=_SPRING_IMPORTS,
            sibling_annotation_short_names={"@Valid"},
            simple_type_name="String",
        )
        is None
    )
    # Source-mapped but import not present -> not validated.
    assert (
        classify_annotation_parameter_binding(
            param=param,
            short_name="@RequestParam",
            annotation='@RequestParam("q")',
            framework="spring",
            class_imports=make_import_declarations("com.example.Other"),
            sibling_annotation_short_names={"@RequestParam"},
            simple_type_name="String",
        )
        is None
    )


def test_classify_binding_aggregate_map_is_open_surface() -> None:
    param = make_callable_parameter(
        name="filters",
        type_name="java.util.Map<java.lang.String, java.lang.String>",
        annotations=["@RequestParam"],
    )
    binding = classify_annotation_parameter_binding(
        param=param,
        short_name="@RequestParam",
        annotation="@RequestParam",
        framework="spring",
        class_imports=_SPRING_IMPORTS,
        sibling_annotation_short_names={"@RequestParam"},
        simple_type_name="Map",
    )
    assert binding is not None
    assert binding.is_aggregate is True
    assert binding.required is False


# --------------------------------------------------------------------------- #
# extract_request_parameter_bindings
# --------------------------------------------------------------------------- #


def test_extract_bindings_one_per_parameter_first_match_wins() -> None:
    params = [
        make_callable_parameter(
            name="id", type_name="java.lang.Long", annotations=['@PathVariable("id")']
        ),
        make_callable_parameter(
            name="body", type_name="com.example.Order", annotations=["@RequestBody"]
        ),
        # An unannotated parameter contributes no binding (no Spring body synthesis).
        make_callable_parameter(
            name="ignored", type_name="java.lang.String", annotations=[]
        ),
    ]
    bindings = extract_request_parameter_bindings(
        params, framework="spring", class_imports=_SPRING_IMPORTS
    )
    assert [(b.name, b.source) for b in bindings] == [
        ("id", EndpointParameterSource.PATH),
        ("body", EndpointParameterSource.BODY),
    ]


def test_extract_bindings_is_framework_generic_jax_rs() -> None:
    params = [
        make_callable_parameter(
            name="q", type_name="java.lang.String", annotations=['@QueryParam("q")']
        ),
        make_callable_parameter(
            name="h",
            type_name="java.lang.String",
            annotations=['@HeaderParam("X-Trace")'],
        ),
    ]
    bindings = extract_request_parameter_bindings(
        params, framework="jax-rs", class_imports=_JAX_RS_IMPORTS
    )
    assert [(b.name, b.source) for b in bindings] == [
        ("q", EndpointParameterSource.QUERY),
        ("X-Trace", EndpointParameterSource.HEADER),
    ]


def test_extract_bindings_micronaut_query_value() -> None:
    params = [
        make_callable_parameter(
            name="status",
            type_name="java.lang.String",
            annotations=["@QueryValue"],
        ),
    ]
    bindings = extract_request_parameter_bindings(
        params, framework="micronaut", class_imports=_MICRONAUT_IMPORTS
    )
    assert [(b.name, b.source) for b in bindings] == [
        ("status", EndpointParameterSource.QUERY),
    ]


# --------------------------------------------------------------------------- #
# Constant references in annotation names
# --------------------------------------------------------------------------- #


def test_annotation_explicit_name_resolves_same_class_string_constant() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Api": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["USER_ID"],
                        modifiers=["static", "final"],
                        variable_initializers={"USER_ID": '"userId"'},
                    )
                ]
            )
        }
    )
    resolver = ConstantResolver(
        analysis=analysis,
        get_class_imports_for_class=lambda _: [],
        get_class_resolution_order=lambda class_name, _: [class_name],
    )

    assert (
        annotation_explicit_name(
            "value = USER_ID",
            constant_resolver=lambda expr: resolver.resolve_expression(
                "example.Api", expr
            ),
        )
        == "userId"
    )


def test_annotation_explicit_name_resolves_positional_qualified_constant() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "com.app.PathParams": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["USER_ID"],
                        modifiers=["static", "final"],
                        variable_initializers={"USER_ID": '"userId"'},
                    )
                ]
            ),
            "example.Api": make_type(),
        },
        java_files={"example.Api": "example/Api.java"},
        import_declarations_by_file={"example/Api.java": ["com.app.PathParams"]},
    )
    common = CommonAnalysis(analysis)
    resolver = common.get_constant_resolver()

    assert (
        annotation_explicit_name(
            "PathParams.USER_ID",
            constant_resolver=lambda expr: resolver.resolve_expression(
                "example.Api", expr
            ),
            class_imports=common.get_class_imports("example.Api"),
        )
        == "userId"
    )


def test_classify_binding_resolves_imported_constant_parameter_name() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "com.app.PathParams": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["USER_ID"],
                        modifiers=["static", "final"],
                        variable_initializers={"USER_ID": '"userId"'},
                    )
                ]
            ),
            "example.Api": make_type(),
        },
        java_files={"example.Api": "example/Api.java"},
        import_declarations_by_file={"example/Api.java": ["com.app.PathParams"]},
    )
    common = CommonAnalysis(analysis)
    resolver = common.get_constant_resolver()
    param = make_callable_parameter(
        name="id",
        type_name="java.lang.Long",
        annotations=["@PathVariable(PathParams.USER_ID)"],
    )

    binding = classify_annotation_parameter_binding(
        param=param,
        short_name="@PathVariable",
        annotation="@PathVariable(PathParams.USER_ID)",
        framework="spring",
        class_imports=_SPRING_IMPORTS + make_import_declarations("com.app.PathParams"),
        sibling_annotation_short_names={"@PathVariable"},
        simple_type_name="Long",
        constant_resolver=lambda expr: resolver.resolve_expression("example.Api", expr),
    )

    assert binding is not None
    assert binding.name == "userId"


def test_classify_binding_curates_http_headers_constant_with_import_evidence() -> None:
    param = make_callable_parameter(
        name="auth",
        type_name="java.lang.String",
        annotations=["@RequestHeader(HttpHeaders.AUTHORIZATION)"],
    )

    binding = classify_annotation_parameter_binding(
        param=param,
        short_name="@RequestHeader",
        annotation="@RequestHeader(HttpHeaders.AUTHORIZATION)",
        framework="spring",
        class_imports=_SPRING_IMPORTS
        + make_import_declarations("org.springframework.http.HttpHeaders"),
        sibling_annotation_short_names={"@RequestHeader"},
        simple_type_name="String",
    )

    assert binding is not None
    assert binding.name == "Authorization"


def test_classify_binding_http_headers_constant_without_import_evidence_falls_back() -> (
    None
):
    param = make_callable_parameter(
        name="auth",
        type_name="java.lang.String",
        annotations=["@RequestHeader(HttpHeaders.AUTHORIZATION)"],
    )

    binding = classify_annotation_parameter_binding(
        param=param,
        short_name="@RequestHeader",
        annotation="@RequestHeader(HttpHeaders.AUTHORIZATION)",
        framework="spring",
        class_imports=_SPRING_IMPORTS,
        sibling_annotation_short_names={"@RequestHeader"},
        simple_type_name="String",
    )

    assert binding is not None
    assert binding.name == "auth"


def test_classify_binding_unresolvable_constant_falls_back_to_java_name() -> None:
    param = make_callable_parameter(
        name="id",
        type_name="java.lang.Long",
        annotations=["@PathVariable(PathParams.USER_ID)"],
    )

    binding = classify_annotation_parameter_binding(
        param=param,
        short_name="@PathVariable",
        annotation="@PathVariable(PathParams.USER_ID)",
        framework="spring",
        class_imports=_SPRING_IMPORTS + make_import_declarations("com.app.PathParams"),
        sibling_annotation_short_names={"@PathVariable"},
        simple_type_name="Long",
        constant_resolver=lambda _expr: None,
    )

    assert binding is not None
    assert binding.name == "id"


def test_classify_binding_ignores_compound_expression_name_attribute() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "com.app.Params": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["USER", "SUFFIX"],
                        modifiers=["static", "final"],
                        variable_initializers={
                            "USER": '"user"',
                            "SUFFIX": '"Id"',
                        },
                    )
                ]
            ),
            "example.Api": make_type(),
        },
        java_files={"example.Api": "example/Api.java"},
        import_declarations_by_file={"example/Api.java": ["com.app.Params"]},
    )
    common = CommonAnalysis(analysis)
    resolver = common.get_constant_resolver()
    param = make_callable_parameter(
        name="id",
        type_name="java.lang.Long",
        annotations=["@PathVariable(value = Params.USER + Params.SUFFIX)"],
    )

    binding = classify_annotation_parameter_binding(
        param=param,
        short_name="@PathVariable",
        annotation="@PathVariable(value = Params.USER + Params.SUFFIX)",
        framework="spring",
        class_imports=_SPRING_IMPORTS + make_import_declarations("com.app.Params"),
        sibling_annotation_short_names={"@PathVariable"},
        simple_type_name="Long",
        constant_resolver=lambda expr: resolver.resolve_expression("example.Api", expr),
    )

    assert binding is not None
    assert binding.name == "id"


def test_classify_binding_curates_http_headers_via_package_wildcard() -> None:
    param = make_callable_parameter(
        name="auth",
        type_name="java.lang.String",
        annotations=["@RequestHeader(HttpHeaders.AUTHORIZATION)"],
    )

    binding = classify_annotation_parameter_binding(
        param=param,
        short_name="@RequestHeader",
        annotation="@RequestHeader(HttpHeaders.AUTHORIZATION)",
        framework="spring",
        class_imports=_SPRING_IMPORTS
        + make_import_declarations("org.springframework.http.*"),
        sibling_annotation_short_names={"@RequestHeader"},
        simple_type_name="String",
    )

    assert binding is not None
    assert binding.name == "Authorization"


def test_classify_binding_bare_http_headers_constant_requires_static_import() -> None:
    param = make_callable_parameter(
        name="auth",
        type_name="java.lang.String",
        annotations=["@RequestHeader(AUTHORIZATION)"],
    )

    binding = classify_annotation_parameter_binding(
        param=param,
        short_name="@RequestHeader",
        annotation="@RequestHeader(AUTHORIZATION)",
        framework="spring",
        class_imports=_SPRING_IMPORTS
        + make_import_declarations("org.springframework.http.HttpHeaders"),
        sibling_annotation_short_names={"@RequestHeader"},
        simple_type_name="String",
    )

    assert binding is not None
    assert binding.name == "auth"


def test_classify_binding_bare_http_headers_constant_with_static_import_maps() -> None:
    param = make_callable_parameter(
        name="auth",
        type_name="java.lang.String",
        annotations=["@RequestHeader(AUTHORIZATION)"],
    )

    binding = classify_annotation_parameter_binding(
        param=param,
        short_name="@RequestHeader",
        annotation="@RequestHeader(AUTHORIZATION)",
        framework="spring",
        class_imports=_SPRING_IMPORTS
        + [
            make_import_declaration(
                "org.springframework.http.HttpHeaders.AUTHORIZATION",
                is_static=True,
            )
        ],
        sibling_annotation_short_names={"@RequestHeader"},
        simple_type_name="String",
    )

    assert binding is not None
    assert binding.name == "Authorization"


def test_classify_binding_bare_constant_with_other_member_static_import_does_not_map() -> (
    None
):
    param = make_callable_parameter(
        name="authHeader",
        type_name="java.lang.String",
        annotations=["@RequestHeader(AUTHORIZATION)"],
    )

    binding = classify_annotation_parameter_binding(
        param=param,
        short_name="@RequestHeader",
        annotation="@RequestHeader(AUTHORIZATION)",
        framework="spring",
        class_imports=_SPRING_IMPORTS
        + [
            make_import_declaration(
                "org.springframework.http.HttpHeaders.CONTENT_TYPE",
                is_static=True,
            )
        ],
        sibling_annotation_short_names={"@RequestHeader"},
        simple_type_name="String",
    )

    assert binding is not None
    assert binding.name == "authHeader"
