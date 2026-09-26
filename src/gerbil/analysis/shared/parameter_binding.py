"""Framework-generic HTTP parameter-binding heuristics shared by server-endpoint
extraction and declarative-client event projection (given a Java method parameter
and its framework, derive the bound request source and name)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

from cldk.models.java import JImport
from cldk.models.java.models import JCallableParameter

from gerbil.analysis.schema import EndpointParameter, EndpointParameterSource
from gerbil.analysis.shared.annotations import annotation_matches_expected
from gerbil.analysis.shared.class_utils import normalize_type_reference
from gerbil.analysis.shared.http_mapping_annotations import (
    QUOTED_STRING_RE,
    annotation_body,
    annotation_short_name,
)

ConstantExpressionResolver = Callable[[str], str | None]

FrameworkName = Literal["spring", "jax-rs", "micronaut"]

# Parameter binding annotation -> the request source it binds. @ModelAttribute
# (Spring) and @BeanParam (JAX-RS) are intentionally absent: they aggregate a
# whole object and are handled as structured bindings by their callers.
PARAMETER_ANNOTATION_SOURCES: dict[
    FrameworkName, dict[str, EndpointParameterSource]
] = {
    "spring": {
        "@PathVariable": EndpointParameterSource.PATH,
        "@RequestParam": EndpointParameterSource.QUERY,
        "@RequestBody": EndpointParameterSource.BODY,
        "@RequestHeader": EndpointParameterSource.HEADER,
        "@RequestPart": EndpointParameterSource.FORM,
    },
    "jax-rs": {
        "@PathParam": EndpointParameterSource.PATH,
        "@QueryParam": EndpointParameterSource.QUERY,
        "@HeaderParam": EndpointParameterSource.HEADER,
        "@FormParam": EndpointParameterSource.FORM,
        # Jersey multipart binds a named form-data part, not the request entity.
        "@FormDataParam": EndpointParameterSource.FORM,
    },
    "micronaut": {
        "@PathVariable": EndpointParameterSource.PATH,
        "@QueryValue": EndpointParameterSource.QUERY,
        "@Header": EndpointParameterSource.HEADER,
        "@Body": EndpointParameterSource.BODY,
        "@Part": EndpointParameterSource.FORM,
    },
}

_SPRING_PARAMETER_ANNOTATION_IMPORT_ROOTS: dict[str, set[str]] = {
    "@PathVariable": {"org.springframework.web.bind.annotation"},
    "@RequestParam": {"org.springframework.web.bind.annotation"},
    "@RequestBody": {"org.springframework.web.bind.annotation"},
    "@RequestHeader": {"org.springframework.web.bind.annotation"},
    "@RequestPart": {"org.springframework.web.bind.annotation"},
    "@ModelAttribute": {"org.springframework.web.bind.annotation"},
}

_JAX_RS_PARAMETER_ANNOTATION_IMPORT_ROOTS: dict[str, set[str]] = {
    "@PathParam": {"javax.ws.rs", "jakarta.ws.rs"},
    "@QueryParam": {"javax.ws.rs", "jakarta.ws.rs"},
    "@HeaderParam": {"javax.ws.rs", "jakarta.ws.rs"},
    "@FormParam": {"javax.ws.rs", "jakarta.ws.rs"},
    "@BeanParam": {"javax.ws.rs", "jakarta.ws.rs"},
    "@FormDataParam": {"org.glassfish.jersey.media.multipart"},
}

_MICRONAUT_PARAMETER_ANNOTATION_IMPORT_ROOTS: dict[str, set[str]] = {
    "@PathVariable": {"io.micronaut.http.annotation"},
    "@QueryValue": {"io.micronaut.http.annotation"},
    "@Header": {"io.micronaut.http.annotation"},
    "@Body": {"io.micronaut.http.annotation"},
    "@Part": {"io.micronaut.http.annotation"},
}

PARAMETER_ANNOTATION_IMPORT_ROOTS_BY_FRAMEWORK: dict[
    FrameworkName, dict[str, set[str]]
] = {
    "spring": _SPRING_PARAMETER_ANNOTATION_IMPORT_ROOTS,
    "jax-rs": _JAX_RS_PARAMETER_ANNOTATION_IMPORT_ROOTS,
    "micronaut": _MICRONAUT_PARAMETER_ANNOTATION_IMPORT_ROOTS,
}

_REQUIRED_FALSE_RE: re.Pattern[str] = re.compile(
    r"\brequired\s*=\s*false\b", re.IGNORECASE
)
# A `defaultValue = "..."` attribute (Spring `@RequestParam`, Micronaut
# `@QueryValue`/`@Header`) implies an optional parameter.
_DEFAULT_VALUE_ATTRIBUTE_RE: re.Pattern[str] = re.compile(r"\bdefaultValue\s*=")

# Sibling annotation short names that mark a parameter optional.
_NULLABLE_ANNOTATION_SHORT_NAME: str = "@Nullable"
_JAX_RS_DEFAULT_VALUE_ANNOTATION_SHORT_NAME: str = "@DefaultValue"


def is_jax_rs_optionality_sibling_annotation(annotation: str) -> bool:
    """Whether an annotation is a JAX-RS sibling that marks a param optional.

    Mirrors the short-name sibling signals parameter_is_required reads:
    @Nullable and JAX-RS @DefaultValue, both matched by short name.
    """
    return annotation_short_name(annotation) in {
        _NULLABLE_ANNOTATION_SHORT_NAME,
        _JAX_RS_DEFAULT_VALUE_ANNOTATION_SHORT_NAME,
    }


# Wrapper types that make a parameter inherently optional.
_OPTIONAL_TYPE_SIMPLE_NAMES: frozenset[str] = frozenset(
    {"Optional", "OptionalInt", "OptionalLong", "OptionalDouble"}
)
# Map-like types that denote an aggregate "open" query surface when bound to a
# query annotation without an explicit name.
_AGGREGATE_QUERY_TYPE_SIMPLE_NAMES: frozenset[str] = frozenset(
    {"Map", "MultiValueMap", "HashMap", "LinkedHashMap", "LinkedMultiValueMap"}
)
# Frameworks whose query binding maps an unnamed Map parameter to the entire
# query string. JAX-RS @QueryParam binds a single named value and has no such
# "bind all" Map form, so it is excluded to avoid false-positive open surfaces.
_AGGREGATE_QUERY_SURFACE_FRAMEWORKS: frozenset[FrameworkName] = frozenset(
    {"spring", "micronaut"}
)

# Well-known header constants defined in framework JARs (Spring/jakarta/javax
# HttpHeaders). They are curated because the source class is never in the
# analyzed set, so field-resolution cannot see them.
_HTTP_HEADERS_CLASSES: frozenset[str] = frozenset(
    {
        "org.springframework.http.HttpHeaders",
        "jakarta.ws.rs.core.HttpHeaders",
        "javax.ws.rs.core.HttpHeaders",
    }
)
_HTTP_HEADERS_CURATED_NAMES: dict[str, str] = {
    "ACCEPT": "Accept",
    "ACCEPT_ENCODING": "Accept-Encoding",
    "ACCEPT_LANGUAGE": "Accept-Language",
    "AUTHORIZATION": "Authorization",
    "CACHE_CONTROL": "Cache-Control",
    "CONNECTION": "Connection",
    "CONTENT_ENCODING": "Content-Encoding",
    "CONTENT_LANGUAGE": "Content-Language",
    "CONTENT_LENGTH": "Content-Length",
    "CONTENT_TYPE": "Content-Type",
    "COOKIE": "Cookie",
    "HOST": "Host",
    "IF_MATCH": "If-Match",
    "IF_NONE_MATCH": "If-None-Match",
    "LOCATION": "Location",
    "ORIGIN": "Origin",
    "RANGE": "Range",
    "REFERER": "Referer",
    "SET_COOKIE": "Set-Cookie",
    "USER_AGENT": "User-Agent",
    "WWW_AUTHENTICATE": "WWW-Authenticate",
}

# A Java identifier or qualified identifier.
_IDENTIFIER_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_QUALIFIED_IDENTIFIER_RE: re.Pattern[str] = re.compile(
    r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+$"
)


def _has_http_headers_import_evidence(
    class_imports: list[JImport],
    *,
    require_static: bool,
    constant_name: str | None = None,
) -> bool:
    for import_entry in class_imports:
        if require_static and not import_entry.is_static:
            continue
        path = import_entry.path.strip()
        for known_class in _HTTP_HEADERS_CLASSES:
            if require_static:
                # A bare constant is only in scope via a static import of that
                # exact member or a static wildcard on the holder class.
                if import_entry.is_wildcard and path == known_class:
                    return True
                if constant_name and path == f"{known_class}.{constant_name}":
                    return True
                continue
            if path == known_class or path.startswith(f"{known_class}."):
                return True
            if import_entry.is_wildcard:
                parent_package = known_class.rpartition(".")[0]
                if path == parent_package:
                    return True
    return False


def _curated_header_constant_name(
    identifier: str,
    class_imports: list[JImport],
) -> str | None:
    """Map well-known HttpHeaders constants to their header names.

    Gated on the ``HttpHeaders`` qualifier and import evidence so unrelated
    project constants named ``AUTHORIZATION`` are not misidentified. The bare
    form (e.g. ``AUTHORIZATION``) requires static-import evidence because a
    plain type import of ``HttpHeaders`` does not make the constant available
    without qualification in Java.
    """
    if "." in identifier:
        qualifier, _, constant_name = identifier.rpartition(".")
        if qualifier != "HttpHeaders":
            return None
        require_static = False
    else:
        constant_name = identifier
        require_static = True

    curated_name = _HTTP_HEADERS_CURATED_NAMES.get(constant_name)
    if curated_name is None:
        return None
    if not _has_http_headers_import_evidence(
        class_imports,
        require_static=require_static,
        constant_name=constant_name if require_static else None,
    ):
        return None
    return curated_name


def _resolve_annotation_name_identifier(
    identifier: str,
    *,
    constant_resolver: ConstantExpressionResolver | None,
    class_imports: list[JImport],
) -> str | None:
    if constant_resolver is not None:
        resolved = constant_resolver(identifier)
        if resolved is not None:
            return resolved
    return _curated_header_constant_name(identifier, class_imports)


_NAME_VALUE_ATTRIBUTE_RE: re.Pattern[str] = re.compile(r"\b(?:name|value)\s*=")


def _extract_name_value_attribute(body: str) -> str | None:
    """Return the raw token of the first ``name=``/``value=`` attribute.

    The token must be a single quoted literal (returned with quotes) or a
    single identifier/qualified identifier spanning the whole attribute value
    (trimmed). Compound expressions such as ``"a" + SUFFIX`` or
    ``methodCall()`` return ``None`` so callers fall back to the Java parameter
    name instead of emitting a partial or wrong name.
    """
    for match in _NAME_VALUE_ATTRIBUTE_RE.finditer(body):
        index = match.end()
        while index < len(body) and body[index].isspace():
            index += 1
        if index >= len(body):
            return None
        token: str
        if body[index] == '"':
            literal_match = QUOTED_STRING_RE.match(body[index:])
            if literal_match is None:
                return None
            token = literal_match.group(0)
            index += len(token)
        else:
            ident_match = re.match(
                r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*",
                body[index:],
            )
            if ident_match is None:
                return None
            token = ident_match.group(0)
            index += len(token)
        while index < len(body) and body[index].isspace():
            index += 1
        if index < len(body) and body[index] not in ",)":
            return None
        return token
    return None


def _leading_positional_identifier(body: str) -> str | None:
    body = body.lstrip()
    if not body or not (body[0].isalpha() or body[0] in "_$"):
        return None
    match = _QUALIFIED_IDENTIFIER_RE.match(body)
    if match is not None:
        return match.group(0)
    match = _IDENTIFIER_RE.match(body)
    if match is not None:
        return match.group(0)
    return None


def simple_type_name(type_reference: str) -> str:
    """Return the unqualified, un-generic, un-arrayed simple type name."""
    return normalize_type_reference(type_reference).rsplit(".", 1)[-1]


def annotation_explicit_name(
    body: str,
    *,
    constant_resolver: ConstantExpressionResolver | None = None,
    class_imports: list[JImport] | None = None,
) -> str | None:
    """Return the explicit parameter name declared by an annotation body.

    Prefers a `name=`/`value=` attribute, then a leading positional string
    literal or constant identifier (e.g. ``@RequestParam("foo")`` or
    ``@RequestHeader(ACCEPT_HEADER)``). Returns ``None`` when no explicit name
    is present so the caller can fall back to the Java parameter name.

    This deliberately does not return the first quoted literal anywhere in the
    body: attributes such as ``defaultValue = "100"`` must not be mistaken for
    the parameter name. An empty literal (``@RequestParam("")``) is treated as
    no explicit name so the caller falls back to the Java parameter name.
    """
    if not body:
        return None

    imports = class_imports or []
    attribute_token = _extract_name_value_attribute(body)
    if attribute_token is not None:
        if attribute_token.startswith('"'):
            parsed = QUOTED_STRING_RE.match(attribute_token)
            return parsed.group(1) or None if parsed is not None else None
        return _resolve_annotation_name_identifier(
            attribute_token,
            constant_resolver=constant_resolver,
            class_imports=imports,
        )

    stripped_body = body.lstrip()
    if stripped_body.startswith('"'):
        # The literal must span the whole positional argument; a trailing
        # operator (e.g. ``"foo" + SUFFIX``) means a compound expression whose
        # name we cannot resolve, so fall back to the Java parameter name.
        literal_match = QUOTED_STRING_RE.match(stripped_body)
        if literal_match is None:
            return None
        index = literal_match.end()
        while index < len(stripped_body) and stripped_body[index].isspace():
            index += 1
        if index < len(stripped_body) and stripped_body[index] not in ",)":
            return None
        return literal_match.group(1) or None

    identifier = _leading_positional_identifier(body)
    if identifier is not None:
        return _resolve_annotation_name_identifier(
            identifier,
            constant_resolver=constant_resolver,
            class_imports=imports,
        )

    return None


def parameter_is_required(
    *,
    source: EndpointParameterSource,
    body: str,
    framework: FrameworkName,
    sibling_annotation_short_names: set[str],
    simple_type_name: str,
) -> bool:
    """Determine whether a bound parameter is required.

    A path variable is part of the URI template; each optional path form is
    already modeled as a separate endpoint, so a PATH parameter is always
    required for the endpoint that contains it and optionality signals
    (``Optional<>``, ``@Nullable``, ``defaultValue``) do not apply.

    For other sources, any one of these signals makes the parameter optional:
      * ``required = false`` on the binding annotation (any source/framework).
      * A ``defaultValue = ...`` attribute (Spring `@RequestParam`, Micronaut
        `@QueryValue`/`@Header`) on the binding annotation.
      * A sibling ``@Nullable`` annotation (any framework/package).
      * A sibling JAX-RS ``@DefaultValue`` annotation.
      * An ``Optional<...>`` wrapper parameter type.
    """
    if source == EndpointParameterSource.PATH:
        return True
    if body and _REQUIRED_FALSE_RE.search(body):
        return False
    if body and _DEFAULT_VALUE_ATTRIBUTE_RE.search(body):
        return False
    if _NULLABLE_ANNOTATION_SHORT_NAME in sibling_annotation_short_names:
        return False
    if (
        framework == "jax-rs"
        and _JAX_RS_DEFAULT_VALUE_ANNOTATION_SHORT_NAME
        in sibling_annotation_short_names
    ):
        return False
    if simple_type_name in _OPTIONAL_TYPE_SIMPLE_NAMES:
        return False
    return True


def is_aggregate_query_surface(
    *,
    framework: FrameworkName,
    source: EndpointParameterSource,
    explicit_name: str | None,
    simple_type_name: str,
) -> bool:
    """Return True for an unnamed map-typed query parameter (open surface)."""
    if framework not in _AGGREGATE_QUERY_SURFACE_FRAMEWORKS:
        return False
    if source != EndpointParameterSource.QUERY:
        return False
    if explicit_name is not None:
        return False
    return simple_type_name in _AGGREGATE_QUERY_TYPE_SIMPLE_NAMES


def classify_annotation_parameter_binding(
    *,
    param: JCallableParameter,
    short_name: str,
    annotation: str,
    framework: FrameworkName,
    class_imports: list[JImport],
    sibling_annotation_short_names: set[str],
    simple_type_name: str,
    constant_resolver: ConstantExpressionResolver | None = None,
) -> EndpointParameter | None:
    """Classify one binding annotation on a parameter into an EndpointParameter.

    Returns ``None`` when ``short_name`` is not a source-mapped binding for the
    framework or fails import validation, so callers can fall through to the
    parameter's next annotation. Aggregate-object bindings (@ModelAttribute,
    @BeanParam) are not handled here.
    """
    source = PARAMETER_ANNOTATION_SOURCES[framework].get(short_name)
    if source is None:
        return None

    param_import_roots = PARAMETER_ANNOTATION_IMPORT_ROOTS_BY_FRAMEWORK[framework]
    if short_name not in param_import_roots:
        return None
    if not annotation_matches_expected(
        annotation=annotation,
        expected_annotation=short_name,
        class_imports=class_imports,
        import_roots_by_annotation=param_import_roots,
    ):
        return None

    body = annotation_body(annotation)
    explicit_name = annotation_explicit_name(
        body,
        constant_resolver=constant_resolver,
        class_imports=class_imports,
    )
    param_name = (
        explicit_name if explicit_name is not None else (param.name or "unknown")
    )

    is_aggregate = is_aggregate_query_surface(
        framework=framework,
        source=source,
        explicit_name=explicit_name,
        simple_type_name=simple_type_name,
    )
    # An open query surface accepts arbitrary keys; it is never a single
    # required parameter, overriding the per-signal requiredness.
    required = (
        False
        if is_aggregate
        else parameter_is_required(
            source=source,
            body=body,
            framework=framework,
            sibling_annotation_short_names=sibling_annotation_short_names,
            simple_type_name=simple_type_name,
        )
    )
    return EndpointParameter(
        name=param_name,
        type=param.type,
        source=source,
        required=required,
        annotation=short_name,
        is_aggregate=is_aggregate,
    )


def extract_request_parameter_bindings(
    parameters: list[JCallableParameter],
    *,
    framework: FrameworkName,
    class_imports: list[JImport],
    constant_resolver: ConstantExpressionResolver | None = None,
) -> list[EndpointParameter]:
    """Bind a method's parameters to their request sources, one per parameter.

    Each parameter contributes at most one binding (its first source-mapped,
    import-validated annotation). Aggregate-object bindings (@ModelAttribute,
    @BeanParam) are intentionally not expanded here: declarative HTTP clients do
    not use them, and an unexpanded one simply yields no binding rather than a
    misnamed one.
    """
    bindings: list[EndpointParameter] = []
    for param in parameters:
        sibling_annotation_short_names = {
            annotation_short_name(annotation) for annotation in param.annotations
        }
        param_simple_type_name = simple_type_name(param.type)
        for annotation in param.annotations:
            binding = classify_annotation_parameter_binding(
                param=param,
                short_name=annotation_short_name(annotation),
                annotation=annotation,
                framework=framework,
                class_imports=class_imports,
                sibling_annotation_short_names=sibling_annotation_short_names,
                simple_type_name=param_simple_type_name,
                constant_resolver=constant_resolver,
            )
            if binding is not None:
                bindings.append(binding)
                break
    return bindings
