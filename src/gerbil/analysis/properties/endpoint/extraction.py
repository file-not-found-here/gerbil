from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JCallable, JImport, JType
from cldk.models.java.models import JCallableParameter

from gerbil.analysis.schema import (
    ApplicationEndpoint,
    EndpointParameter,
    EndpointParameterSource,
)
from gerbil.analysis.shared.constant_resolution import ConstantResolver
from gerbil.analysis.schema.types import _template_path_variable_names
from gerbil.analysis.shared.annotations import (
    annotation_matches_expected,
    annotation_token,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    ATTRIBUTE_VALUE_PATTERN as _ATTRIBUTE_VALUE_PATTERN,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    QUOTED_STRING_RE as _QUOTED_STRING_RE,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    SPRING_ANNOTATION_IMPORT_ROOTS as _SPRING_ANNOTATION_IMPORT_ROOTS,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    SPRING_CLIENT_CLASS_ANNOTATIONS as _SPRING_CLIENT_CLASS_ANNOTATIONS,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    SPRING_DIRECT_METHOD_ANNOTATIONS as _SPRING_DIRECT_METHOD_ANNOTATIONS,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    SPRING_METHOD_ANNOTATION_NAMES as _SPRING_METHOD_ANNOTATION_NAMES,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    ProductionMethodSpec as _ProductionMethodSpec,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    annotation_body as _annotation_body,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    annotation_short_name as _annotation_short_name,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    deduplicate_strings as _deduplicate_strings,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    extract_annotation_paths as _extract_annotation_paths,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    extract_rfc6570_query_parameter_names as _extract_rfc6570_query_parameter_names,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    extract_request_mapping_method_specs as _extract_request_mapping_method_specs,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    join_paths as _join_paths,
)
from gerbil.analysis.shared.http_mapping_annotations import (
    normalize_path,
    paths_or_root as _paths_or_root,
)
from gerbil.analysis.shared.class_utils import (
    ClassAnnotationResolutionConfig,
    ResolvedAnnotation,
    resolve_effective_class_annotations,
)
from gerbil.analysis.shared.class_utils import (
    resolve_known_class_name as _resolve_known_class_name,
)
from gerbil.analysis.shared.imports import get_class_import_declarations
from gerbil.analysis.shared.parameter_binding import (
    PARAMETER_ANNOTATION_IMPORT_ROOTS_BY_FRAMEWORK,
    PARAMETER_ANNOTATION_SOURCES,
    FrameworkName,
    annotation_explicit_name as _annotation_explicit_name,
    classify_annotation_parameter_binding,
    is_jax_rs_optionality_sibling_annotation as _is_jax_rs_optionality_sibling_annotation,
    parameter_is_required as _parameter_is_required,
    simple_type_name as _simple_type_name,
)
from gerbil.analysis.shared.reachability import Reachability
from gerbil.analysis.shared.url_utils import classify_request_target, safe_urlparse

_REQUEST_TARGET_EXTERNAL: str = "external"
_REQUEST_TARGET_UNKNOWN: str = "unknown"

# An expression resolver bound to a single declaring class.
ClassBoundConstantResolver = Callable[[str], str | None]


@dataclass(frozen=True)
class EndpointExtractionResult:
    """Application endpoints plus the @ApplicationPath prefixes mounted ahead of them."""

    endpoints: list[ApplicationEndpoint]
    application_path_prefixes: tuple[str, ...] = ()


def _class_bound_resolver(
    constant_resolver: ConstantResolver | None,
    declaring_class_name: str | None,
) -> ClassBoundConstantResolver | None:
    if constant_resolver is None or declaring_class_name is None:
        return None
    return lambda expression: constant_resolver.resolve_expression(
        declaring_class_name, expression
    )


_JAX_RS_METHOD_ANNOTATIONS: dict[str, str] = {
    "@GET": "GET",
    "@POST": "POST",
    "@PUT": "PUT",
    "@DELETE": "DELETE",
    "@PATCH": "PATCH",
    "@HEAD": "HEAD",
    "@OPTIONS": "OPTIONS",
}

_MICRONAUT_METHOD_ANNOTATIONS: dict[str, str] = {
    "@Get": "GET",
    "@Post": "POST",
    "@Put": "PUT",
    "@Delete": "DELETE",
    "@Patch": "PATCH",
    "@Head": "HEAD",
    "@Options": "OPTIONS",
}
_MICRONAUT_CLIENT_CLASS_ANNOTATIONS: set[str] = {"@Client"}

_ENDPOINT_ANNOTATION_RESOLUTION_CONFIG = ClassAnnotationResolutionConfig(
    include_superclasses=True,
    include_interfaces=True,
    require_inherited_annotations_from_parents=False,
)

_JAX_RS_ANNOTATION_IMPORT_ROOTS: dict[str, set[str]] = {
    "@Path": {"javax.ws.rs", "jakarta.ws.rs"},
    "@ApplicationPath": {"javax.ws.rs", "jakarta.ws.rs"},
    "@GET": {"javax.ws.rs", "jakarta.ws.rs"},
    "@POST": {"javax.ws.rs", "jakarta.ws.rs"},
    "@PUT": {"javax.ws.rs", "jakarta.ws.rs"},
    "@DELETE": {"javax.ws.rs", "jakarta.ws.rs"},
    "@PATCH": {"javax.ws.rs", "jakarta.ws.rs"},
    "@HEAD": {"javax.ws.rs", "jakarta.ws.rs"},
    "@OPTIONS": {"javax.ws.rs", "jakarta.ws.rs"},
}

_MICRONAUT_ANNOTATION_IMPORT_ROOTS: dict[str, set[str]] = {
    "@Controller": {"io.micronaut.http.annotation"},
    # @Client lives in the client package, not io.micronaut.http.annotation.
    "@Client": {"io.micronaut.http.client.annotation", "io.micronaut.http.client"},
    "@Get": {"io.micronaut.http.annotation"},
    "@Post": {"io.micronaut.http.annotation"},
    "@Put": {"io.micronaut.http.annotation"},
    "@Delete": {"io.micronaut.http.annotation"},
    "@Patch": {"io.micronaut.http.annotation"},
    "@Head": {"io.micronaut.http.annotation"},
    "@Options": {"io.micronaut.http.annotation"},
}

_ANNOTATION_IMPORT_ROOTS_BY_FRAMEWORK: dict[FrameworkName, dict[str, set[str]]] = {
    "spring": _SPRING_ANNOTATION_IMPORT_ROOTS,
    "jax-rs": _JAX_RS_ANNOTATION_IMPORT_ROOTS,
    "micronaut": _MICRONAUT_ANNOTATION_IMPORT_ROOTS,
}

# Spring mapping-level constraints, e.g. `@GetMapping(params = "name")` or
# `@RequestMapping(headers = "X-API-Version")`.
_REQUEST_MAPPING_PARAMS_ATTRIBUTE_RE: re.Pattern[str] = re.compile(
    r"\bparams\s*=\s*" + _ATTRIBUTE_VALUE_PATTERN
)
_REQUEST_MAPPING_HEADERS_ATTRIBUTE_RE: re.Pattern[str] = re.compile(
    r"\bheaders\s*=\s*" + _ATTRIBUTE_VALUE_PATTERN
)

# JAX-RS request body (entity parameter) synthesis. JAX-RS binds the single
# method parameter that carries no injection/binding annotation as the request
# entity, and the spec permits at most one such parameter; unlike Spring's
# @RequestBody it is unannotated, so it has no entry in the source map above.
_JAX_RS_BODY_CAPABLE_HTTP_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH"})
# Verbs that never carry a JAX-RS request entity, so a body is not synthesized
# for them even when @Consumes is declared (e.g. inherited at the class level).
_JAX_RS_BODY_INCAPABLE_HTTP_METHODS: frozenset[str] = frozenset(
    {"GET", "HEAD", "OPTIONS"}
)
_JAX_RS_CONSUMES_IMPORT_ROOTS: dict[str, set[str]] = {
    "@Consumes": {"javax.ws.rs", "jakarta.ws.rs"},
}
_JAX_RS_PACKAGE_ROOTS: set[str] = {"javax.ws.rs", "jakarta.ws.rs"}
# JAX-RS spec annotations whose presence on an override (on the method or its
# parameters) defeats spec-§3.6 inheritance. Mirrors Jersey's AnnotatedMethod
# surface: verb annotations plus Path/Produces/Consumes and the spec parameter
# annotations; extension annotations (e.g. Jersey @FormDataParam) are excluded.
_JAX_RS_BARE_OVERRIDE_ANNOTATION_IMPORT_ROOTS: dict[str, set[str]] = {
    **_JAX_RS_ANNOTATION_IMPORT_ROOTS,
    **_JAX_RS_CONSUMES_IMPORT_ROOTS,
    **{
        annotation: _JAX_RS_PACKAGE_ROOTS
        for annotation in (
            "@Produces",
            "@PathParam",
            "@QueryParam",
            "@HeaderParam",
            "@FormParam",
            "@CookieParam",
            "@MatrixParam",
            "@BeanParam",
            "@DefaultValue",
            "@Encoded",
            "@Context",
            "@Suspended",
        )
    },
}
# Parameter annotations that inject a framework value or bind a non-body source
# that has no tracked EndpointParameterSource (cookie/matrix) or comes from a
# JAX-RS extension. A parameter carrying any of these is never the entity body.
# Source-mapped bindings (path/query/header/form) are unioned in below so this
# set and _PARAMETER_ANNOTATION_SOURCES["jax-rs"] cannot drift apart.
_JAX_RS_INJECTION_PARAMETER_ANNOTATIONS: frozenset[str] = frozenset(
    {
        "@CookieParam",
        "@MatrixParam",
        "@Context",
        "@Suspended",
        "@Auth",  # Dropwizard: injects the authenticated principal
        "@MultipartForm",  # RESTEasy: aggregates form parts onto a bean
        "@BeanParam",  # aggregates field-level bindings onto a bean, never the entity
    }
)
# A parameter is the entity body only when it carries no recognized
# binding/injection annotation. Unrecognized annotations (bean-validation such
# as @Valid/@NotNull, or project-specific markers) do not disqualify a body, so
# a custom binding annotation outside these curated sets is the known
# limitation: it would still be treated as body-compatible.
_JAX_RS_NON_BODY_PARAMETER_ANNOTATIONS: frozenset[str] = (
    frozenset(PARAMETER_ANNOTATION_SOURCES["jax-rs"])
    | _JAX_RS_INJECTION_PARAMETER_ANNOTATIONS
)
# Distinctive simple type names of framework-injected parameters that are never
# the entity body even when unannotated (JAX-RS context/async types and servlet
# plumbing). Generic context types (Request, HttpHeaders) are omitted because
# they reach handlers via @Context and their names can collide with real DTOs.
_JAX_RS_NON_BODY_PARAMETER_TYPES: frozenset[str] = frozenset(
    {
        "AsyncResponse",
        "UriInfo",
        "SecurityContext",
        "ContainerRequestContext",
        "ContainerResponseContext",
        "ResourceContext",
        "SseEventSink",
        "Sse",
        "Providers",
        "HttpServletRequest",
        "HttpServletResponse",
        "ServletContext",
        "ServletConfig",
    }
)


# Spring mapping constraint attribute -> the parameter source it constrains.
_MAPPING_CONSTRAINT_ATTRIBUTE_SOURCES: tuple[
    tuple[re.Pattern[str], EndpointParameterSource], ...
] = (
    (_REQUEST_MAPPING_PARAMS_ATTRIBUTE_RE, EndpointParameterSource.QUERY),
    (_REQUEST_MAPPING_HEADERS_ATTRIBUTE_RE, EndpointParameterSource.HEADER),
)


def _mapping_constraint_param_name(entry: str) -> str | None:
    """Return the name a Spring mapping constraint entry requires present.

    Mirrors Spring's ``AbstractNameValueExpression`` parsing: the first ``=`` is
    the name/value separator, and the expression is negated when a ``!``
    immediately precedes it. Only presence (``"x"``) and equality (``"x=v"``)
    constraints require ``x`` and return its name. A negation — absence
    (``"!x"``) or inequality (``"x!=v"``) — does not require ``x`` because an
    absent parameter satisfies it, so ``None`` is returned to skip the entry.
    """
    normalized = entry.strip()
    if not normalized:
        return None
    separator = normalized.find("=")
    if separator == -1:
        return None if normalized.startswith("!") else normalized
    if separator > 0 and normalized[separator - 1] == "!":
        return None
    return normalized[:separator].strip() or None


def _extract_mapping_constraint_parameters(
    annotations: list[str],
    *,
    existing_parameters: list[EndpointParameter],
) -> list[EndpointParameter]:
    """Extract query/header parameters declared by Spring mapping constraints.

    Models ``params=`` and ``headers=`` presence/equality constraints as
    required query and header parameters respectively (see
    :func:`_mapping_constraint_param_name` for the per-entry rules). The bound
    Java type is unknown, so synthesized parameters carry ``is_synthetic=True``
    and no ``type``. Results are deduplicated against parameters already derived
    from method arguments and across ``annotations`` (by source + lowercase
    name).
    """
    seen_names_by_source: dict[EndpointParameterSource, set[str]] = {
        source: {
            parameter.name.lower()
            for parameter in existing_parameters
            if parameter.source == source
        }
        for _, source in _MAPPING_CONSTRAINT_ATTRIBUTE_SOURCES
    }

    constraint_parameters: list[EndpointParameter] = []
    for annotation in annotations:
        body = _annotation_body(annotation)
        if not body:
            continue
        annotation_short = _annotation_short_name(annotation)
        for attribute_re, source in _MAPPING_CONSTRAINT_ATTRIBUTE_SOURCES:
            attribute_match = attribute_re.search(body)
            if not attribute_match:
                continue
            for entry in _QUOTED_STRING_RE.findall(attribute_match.group(1)):
                name = _mapping_constraint_param_name(entry)
                if name is None:
                    continue
                normalized_name = name.lower()
                if normalized_name in seen_names_by_source[source]:
                    continue
                seen_names_by_source[source].add(normalized_name)
                constraint_parameters.append(
                    EndpointParameter(
                        name=name,
                        source=source,
                        required=True,
                        annotation=annotation_short,
                        is_synthetic=True,
                    )
                )
    return constraint_parameters


def _annotation_matches_framework(
    annotation: str,
    expected_annotation_name: str,
    *,
    framework: FrameworkName,
    class_imports: list[JImport],
) -> bool:
    import_roots_by_annotation = _ANNOTATION_IMPORT_ROOTS_BY_FRAMEWORK[framework]
    if expected_annotation_name not in import_roots_by_annotation:
        return False

    return annotation_matches_expected(
        annotation=annotation,
        expected_annotation=expected_annotation_name,
        class_imports=class_imports,
        import_roots_by_annotation=import_roots_by_annotation,
    )


def _annotation_name_set(
    annotations: list[str],
    *,
    framework: FrameworkName,
    class_imports: list[JImport],
) -> set[str]:
    annotation_import_roots = _ANNOTATION_IMPORT_ROOTS_BY_FRAMEWORK[framework]
    annotation_names: set[str] = set()
    for annotation in annotations:
        annotation_name = _annotation_short_name(annotation)
        if annotation_name not in annotation_import_roots:
            continue
        if not _annotation_matches_framework(
            annotation,
            annotation_name,
            framework=framework,
            class_imports=class_imports,
        ):
            continue
        annotation_names.add(annotation_name)
    return annotation_names


def _is_server_class(
    *,
    effective_class_annotations: list[ResolvedAnnotation],
    direct_class_annotations: list[str],
    direct_class_imports: list[JImport],
    framework: FrameworkName,
    excluded_annotation_names: set[str],
) -> bool:
    direct_annotation_names = _annotation_name_set(
        direct_class_annotations,
        framework=framework,
        class_imports=direct_class_imports,
    )

    if direct_annotation_names.intersection(excluded_annotation_names):
        return False
    return any(
        "Controller" in _annotation_short_name(resolved_annotation.annotation)
        for resolved_annotation in effective_class_annotations
    )


def _is_spring_server_class(
    effective_class_annotations: list[ResolvedAnnotation],
    direct_class_annotations: list[str],
    direct_class_imports: list[JImport],
) -> bool:
    return _is_server_class(
        effective_class_annotations=effective_class_annotations,
        direct_class_annotations=direct_class_annotations,
        direct_class_imports=direct_class_imports,
        framework="spring",
        excluded_annotation_names=_SPRING_CLIENT_CLASS_ANNOTATIONS,
    )


def _is_micronaut_server_class(
    effective_class_annotations: list[ResolvedAnnotation],
    direct_class_annotations: list[str],
    direct_class_imports: list[JImport],
) -> bool:
    return _is_server_class(
        effective_class_annotations=effective_class_annotations,
        direct_class_annotations=direct_class_annotations,
        direct_class_imports=direct_class_imports,
        framework="micronaut",
        excluded_annotation_names=_MICRONAUT_CLIENT_CLASS_ANNOTATIONS,
    )


def normalize_observed_path_with_context(path: str) -> tuple[str | None, str]:
    """Normalize an observed request path and classify its target context.

    A None path means the literal is not a usable request path (bracket-broken
    authority); callers must not match it against endpoint templates.
    """
    if safe_urlparse(path.strip()) is None:
        return None, _REQUEST_TARGET_UNKNOWN
    request_target_context = classify_request_target(path, bare_token_is_local=True)
    return normalize_path(path), request_target_context


def _extract_class_paths(
    class_annotations: list[str],
    annotation_name: str,
    *,
    framework: FrameworkName,
    class_imports: list[JImport],
    constant_resolver: ClassBoundConstantResolver | None = None,
) -> list[str]:
    class_paths: list[str] = []
    for annotation in class_annotations:
        if not _annotation_matches_framework(
            annotation,
            annotation_name,
            framework=framework,
            class_imports=class_imports,
        ):
            continue
        class_paths.extend(_extract_annotation_paths(annotation, constant_resolver))
    return _paths_or_root(_deduplicate_strings(class_paths))


def _extract_class_paths_from_resolved(
    class_annotations: list[ResolvedAnnotation],
    annotation_name: str,
    *,
    framework: FrameworkName,
    class_imports_by_class: dict[str, list[JImport]],
    constant_resolver: ConstantResolver | None = None,
) -> list[str]:
    class_paths: list[str] = []
    for resolved_annotation in class_annotations:
        if not _annotation_matches_framework(
            resolved_annotation.annotation,
            annotation_name,
            framework=framework,
            class_imports=class_imports_by_class.get(
                resolved_annotation.declaring_class_name,
                [],
            ),
        ):
            continue
        class_paths.extend(
            _extract_annotation_paths(
                resolved_annotation.annotation,
                _class_bound_resolver(
                    constant_resolver,
                    resolved_annotation.declaring_class_name,
                ),
            )
        )
    return _paths_or_root(_deduplicate_strings(class_paths))


def _resolved_spring_request_mapping_annotations(
    class_annotations: list[ResolvedAnnotation],
    *,
    class_imports_by_class: dict[str, list[JImport]],
) -> list[str]:
    """Return class-level Spring ``@RequestMapping`` annotation literals."""
    return [
        resolved_annotation.annotation
        for resolved_annotation in class_annotations
        if _annotation_matches_framework(
            resolved_annotation.annotation,
            "@RequestMapping",
            framework="spring",
            class_imports=class_imports_by_class.get(
                resolved_annotation.declaring_class_name,
                [],
            ),
        )
    ]


def _has_annotation(
    annotations: list[str],
    annotation_name: str,
    *,
    framework: FrameworkName,
    class_imports: list[JImport],
) -> bool:
    return any(
        _annotation_matches_framework(
            annotation,
            annotation_name,
            framework=framework,
            class_imports=class_imports,
        )
        for annotation in annotations
    )


def _extract_jax_rs_locator_paths(
    method_annotations: list[str],
    *,
    class_imports: list[JImport],
    constant_resolver: ClassBoundConstantResolver | None = None,
) -> list[str]:
    locator_paths: list[str] = []
    for annotation in method_annotations:
        if not _annotation_matches_framework(
            annotation,
            "@Path",
            framework="jax-rs",
            class_imports=class_imports,
        ):
            continue
        locator_paths.extend(_extract_annotation_paths(annotation, constant_resolver))
    return _paths_or_root(_deduplicate_strings(locator_paths))


def _is_jax_rs_locator_method(
    method_annotations: list[str],
    *,
    class_imports: list[JImport],
) -> bool:
    has_path_annotation = _has_annotation(
        method_annotations,
        "@Path",
        framework="jax-rs",
        class_imports=class_imports,
    )
    method_annotation_names = _annotation_name_set(
        method_annotations,
        framework="jax-rs",
        class_imports=class_imports,
    )
    has_http_method_annotation = bool(
        method_annotation_names.intersection(_JAX_RS_METHOD_ANNOTATIONS)
    )
    return has_path_annotation and not has_http_method_annotation


def _resolve_application_class_name(
    *,
    return_type: str,
    declaring_class_name: str,
    application_class_set: set[str],
) -> str | None:
    return _resolve_known_class_name(
        type_reference=return_type,
        declaring_class_name=declaring_class_name,
        known_class_names=application_class_set,
    )


def _extract_jax_rs_direct_class_paths(
    class_annotations: list[str],
    *,
    class_imports: list[JImport],
    constant_resolver: ClassBoundConstantResolver | None = None,
) -> list[str]:
    if not _has_annotation(
        class_annotations,
        "@Path",
        framework="jax-rs",
        class_imports=class_imports,
    ):
        return []
    return _extract_class_paths(
        class_annotations,
        "@Path",
        framework="jax-rs",
        class_imports=class_imports,
        constant_resolver=constant_resolver,
    )


def _get_class_imports(
    analysis: JavaAnalysis,
    qualified_class_name: str,
) -> list[JImport]:
    return get_class_import_declarations(analysis, qualified_class_name)


def _build_jax_rs_inherited_class_paths(
    *,
    analysis: JavaAnalysis,
    application_classes: list[str],
    class_annotations_by_class: dict[str, list[str]],
    class_imports_by_class: dict[str, list[JImport]],
    constant_resolver: ConstantResolver | None = None,
) -> dict[str, set[str]]:
    known_class_names = set(application_classes)
    known_class_names.update(analysis.get_classes().keys())

    class_annotations_cache: dict[str, list[str]] = dict(class_annotations_by_class)
    class_imports_cache: dict[str, list[JImport]] = dict(class_imports_by_class)
    parent_classes_cache: dict[str, list[str]] = {}
    resolved_paths_cache: dict[str, set[str]] = {}
    visiting: set[str] = set()

    def _class_annotations_for(class_name: str) -> list[str]:
        cached_annotations = class_annotations_cache.get(class_name)
        if cached_annotations is not None:
            return cached_annotations

        class_details = analysis.get_class(class_name)
        annotations = list(class_details.annotations or []) if class_details else []
        class_annotations_cache[class_name] = annotations
        return annotations

    def _parent_classes_for(class_name: str) -> list[str]:
        cached_parents = parent_classes_cache.get(class_name)
        if cached_parents is not None:
            return cached_parents

        class_details = analysis.get_class(class_name)
        if not class_details:
            parent_classes_cache[class_name] = []
            return []

        parent_classes: list[str] = []
        seen_parent_classes: set[str] = set()
        raw_parent_types = [
            *(class_details.extends_list or []),
            *(class_details.implements_list or []),
        ]
        for parent_type in raw_parent_types:
            resolved_parent_class = _resolve_known_class_name(
                type_reference=parent_type,
                declaring_class_name=class_name,
                known_class_names=known_class_names,
            )
            if not resolved_parent_class:
                continue
            if resolved_parent_class in seen_parent_classes:
                continue
            seen_parent_classes.add(resolved_parent_class)
            parent_classes.append(resolved_parent_class)

        parent_classes_cache[class_name] = parent_classes
        return parent_classes

    def _class_imports_for(class_name: str) -> list[JImport]:
        cached_imports = class_imports_cache.get(class_name)
        if cached_imports is not None:
            return cached_imports

        imports = _get_class_imports(analysis, class_name)
        class_imports_cache[class_name] = imports
        return imports

    def _resolve_paths(class_name: str) -> set[str]:
        cached_paths = resolved_paths_cache.get(class_name)
        if cached_paths is not None:
            return cached_paths

        if class_name in visiting:
            return set()
        visiting.add(class_name)

        inherited_paths: set[str] = set()
        for parent_class_name in _parent_classes_for(class_name):
            inherited_paths.update(_resolve_paths(parent_class_name))

        direct_paths = set(
            _extract_jax_rs_direct_class_paths(
                _class_annotations_for(class_name),
                class_imports=_class_imports_for(class_name),
                constant_resolver=_class_bound_resolver(constant_resolver, class_name),
            )
        )

        # JAX-RS does not compose class-level @Path across inheritance: a class
        # carrying its own @Path mounts there alone (Jersey resolves the nearest
        # annotated class); superclass paths apply only when the class has none.
        if direct_paths:
            resolved_paths = set(direct_paths)
        else:
            resolved_paths = set(inherited_paths)

        visiting.remove(class_name)
        resolved_paths_cache[class_name] = resolved_paths
        return resolved_paths

    inherited_paths_by_class: dict[str, set[str]] = {}
    for class_name in application_classes:
        class_paths = _resolve_paths(class_name)
        if class_paths:
            inherited_paths_by_class[class_name] = class_paths

    return inherited_paths_by_class


def _build_jax_rs_class_mount_paths(
    *,
    analysis: JavaAnalysis,
    application_classes: list[str],
    class_annotations_by_class: dict[str, list[str]],
    class_imports_by_class: dict[str, list[JImport]],
    methods_by_class: dict[str, dict[str, JCallable]],
    constant_resolver: ConstantResolver | None = None,
) -> dict[str, list[str]]:
    application_class_set = set(application_classes)
    class_mount_paths = _build_jax_rs_inherited_class_paths(
        analysis=analysis,
        application_classes=application_classes,
        class_annotations_by_class=class_annotations_by_class,
        class_imports_by_class=class_imports_by_class,
        constant_resolver=constant_resolver,
    )

    locator_edges: dict[str, list[tuple[str, list[str]]]] = {}
    for source_class_name in application_classes:
        methods = methods_by_class.get(source_class_name, {})
        class_imports = class_imports_by_class.get(source_class_name, [])
        for method_details in methods.values():
            method_annotations = list(method_details.annotations or [])
            if not _is_jax_rs_locator_method(
                method_annotations,
                class_imports=class_imports,
            ):
                continue

            target_class_name = _resolve_application_class_name(
                return_type=method_details.return_type or "",
                declaring_class_name=source_class_name,
                application_class_set=application_class_set,
            )
            if target_class_name is None:
                continue

            locator_edges.setdefault(source_class_name, []).append(
                (
                    target_class_name,
                    _extract_jax_rs_locator_paths(
                        method_annotations,
                        class_imports=class_imports,
                        constant_resolver=_class_bound_resolver(
                            constant_resolver, source_class_name
                        ),
                    ),
                )
            )

    queue: deque[tuple[str, str, tuple[str, ...]]] = deque()
    queued_states: set[tuple[str, str, tuple[str, ...]]] = set()

    def _enqueue_state(
        class_name: str,
        mount_path: str,
        traversed_classes: tuple[str, ...],
    ) -> None:
        state = (class_name, mount_path, traversed_classes)
        if state in queued_states:
            return
        queued_states.add(state)
        queue.append(state)

    for class_name, mount_paths in class_mount_paths.items():
        for mount_path in mount_paths:
            _enqueue_state(class_name, mount_path, (class_name,))

    while queue:
        source_class_name, source_mount_path, traversed_classes = queue.popleft()
        traversed_class_set = set(traversed_classes)

        for target_class_name, locator_paths in locator_edges.get(
            source_class_name, []
        ):
            if target_class_name in traversed_class_set:
                continue

            next_traversed_classes = (*traversed_classes, target_class_name)
            target_mount_paths = class_mount_paths.setdefault(target_class_name, set())
            for locator_path in locator_paths:
                mounted_path = _join_paths(source_mount_path, locator_path)
                if mounted_path not in target_mount_paths:
                    target_mount_paths.add(mounted_path)
                _enqueue_state(target_class_name, mounted_path, next_traversed_classes)

    # Class/interface @Path is not inherited (Jakarta REST §3.6); an interface
    # or abstract @Path supertype is a contract, not a registered root resource,
    # so its mount is dropped and the concrete own-@Path descendant becomes the
    # mount; a CONCRETE @Path supertype remains its own registered root resource
    # and keeps its mount.
    known_class_names = set(application_classes)
    known_class_names.update(analysis.get_classes().keys())

    def _class_annotations_for_shadow(class_name: str) -> list[str]:
        cached_annotations = class_annotations_by_class.get(class_name)
        if cached_annotations is not None:
            return cached_annotations
        class_details = analysis.get_class(class_name)
        return list(class_details.annotations or []) if class_details else []

    def _class_imports_for_shadow(class_name: str) -> list[JImport]:
        cached_imports = class_imports_by_class.get(class_name)
        if cached_imports is not None:
            return cached_imports
        return _get_class_imports(analysis, class_name)

    def _has_own_direct_path(class_name: str) -> bool:
        return bool(
            _extract_jax_rs_direct_class_paths(
                _class_annotations_for_shadow(class_name),
                class_imports=_class_imports_for_shadow(class_name),
                constant_resolver=_class_bound_resolver(constant_resolver, class_name),
            )
        )

    def _resolve_parent_class(class_name: str, parent_type: str) -> str | None:
        return _resolve_known_class_name(
            type_reference=parent_type,
            declaring_class_name=class_name,
            known_class_names=known_class_names,
        )

    def _collect_shadowed_supertypes(class_name: str, visited: set[str]) -> set[str]:
        if class_name in visited:
            return set()
        visited.add(class_name)

        shadowed: set[str] = set()
        class_details = analysis.get_class(class_name)
        if not class_details:
            return shadowed

        for parent_type in [
            *(class_details.extends_list or []),
            *(class_details.implements_list or []),
        ]:
            parent_class_name = _resolve_parent_class(class_name, parent_type)
            if not parent_class_name:
                continue
            if parent_class_name not in application_class_set:
                continue
            if _has_own_direct_path(parent_class_name):
                parent_details = analysis.get_class(parent_class_name)
                if parent_details is not None and (
                    parent_details.is_interface
                    or "abstract" in (parent_details.modifiers or [])
                ):
                    shadowed.add(parent_class_name)
            shadowed.update(_collect_shadowed_supertypes(parent_class_name, visited))
        return shadowed

    shadowed_classes: set[str] = set()
    for class_name in application_classes:
        if _has_own_direct_path(class_name):
            shadowed_classes.update(_collect_shadowed_supertypes(class_name, set()))

    return {
        class_name: sorted(mount_paths)
        for class_name, mount_paths in class_mount_paths.items()
        if mount_paths and class_name not in shadowed_classes
    }


def _reconcile_path_parameters(
    path_template: str,
    parameters: list[EndpointParameter],
) -> list[EndpointParameter]:
    """Scope an endpoint's PATH parameters to its own route template.

    A ``{var}`` token is a path parameter by definition — the framework must
    bind it for the route to match — so the template is the source of truth for
    path-variable existence and requiredness; a recognized binding annotation
    only enriches the entry with its declared Java type. Declared path
    parameters absent from this template are dropped (so a multi-template
    handler yields per-template lists), and any template variable lacking a
    recognized annotation is synthesized.
    """
    declared_path_by_name = {
        parameter.name.lower(): parameter
        for parameter in parameters
        if parameter.source == EndpointParameterSource.PATH
    }
    non_path_parameters = [
        parameter
        for parameter in parameters
        if parameter.source != EndpointParameterSource.PATH
    ]

    reconciled_path_parameters: list[EndpointParameter] = []
    for variable_name in _template_path_variable_names(path_template):
        declared = declared_path_by_name.get(variable_name.lower())
        if declared is not None:
            reconciled_path_parameters.append(declared)
        else:
            reconciled_path_parameters.append(
                EndpointParameter(
                    name=variable_name,
                    source=EndpointParameterSource.PATH,
                    required=True,
                    is_synthetic=True,
                )
            )

    return [*reconciled_path_parameters, *non_path_parameters]


def _has_spring_mapping_annotation(
    method_annotations: list[str],
    *,
    class_imports: list[JImport],
) -> bool:
    for annotation in method_annotations:
        annotation_name = _annotation_short_name(annotation)
        if annotation_name not in _SPRING_METHOD_ANNOTATION_NAMES:
            continue
        if _annotation_matches_framework(
            annotation,
            annotation_name,
            framework="spring",
            class_imports=class_imports,
        ):
            return True
    return False


def _supertype_method_search_order(
    analysis: JavaAnalysis,
    qualified_class_name: str,
    *,
    known_class_names: set[str],
) -> list[str]:
    """Supertypes in Java override search order.

    Mirrors Spring's AnnotationsScanner.processMethodHierarchy (spring-core):
    at each level interfaces are searched depth-first before the superclass.
    JAX-RS and Micronaut inherit method-level annotations along the same shape.
    """
    order: list[str] = []
    visited: set[str] = {qualified_class_name}

    def _visit(class_name: str) -> None:
        class_details = analysis.get_class(class_name)
        if class_details is None:
            return
        parent_references = [
            *(class_details.implements_list or []),
            *(class_details.extends_list or []),
        ]
        for parent_reference in parent_references:
            resolved_parent = _resolve_known_class_name(
                type_reference=parent_reference,
                declaring_class_name=class_name,
                known_class_names=known_class_names,
            )
            if not resolved_parent or resolved_parent in visited:
                continue
            visited.add(resolved_parent)
            order.append(resolved_parent)
            _visit(resolved_parent)

    _visit(qualified_class_name)
    return order


def _jax_rs_supertype_method_search_order(
    analysis: JavaAnalysis,
    qualified_class_name: str,
    *,
    known_class_names: set[str],
) -> list[str]:
    """JAX-RS supertype search order: superclass before implemented interfaces.

    Jakarta REST 3.6: annotations on a super-class take precedence over those on
    an implemented interface, so the nearest-first walk visits ``extends`` before
    ``implements`` (the reverse of Spring's interface-first order).
    """
    order: list[str] = []
    visited: set[str] = {qualified_class_name}

    def _visit(class_name: str) -> None:
        class_details = analysis.get_class(class_name)
        if class_details is None:
            return
        parent_references = [
            *(class_details.extends_list or []),
            *(class_details.implements_list or []),
        ]
        for parent_reference in parent_references:
            resolved_parent = _resolve_known_class_name(
                type_reference=parent_reference,
                declaring_class_name=class_name,
                known_class_names=known_class_names,
            )
            if not resolved_parent or resolved_parent in visited:
                continue
            visited.add(resolved_parent)
            order.append(resolved_parent)
            _visit(resolved_parent)

    _visit(qualified_class_name)
    return order


def _superclass_search_order(
    analysis: JavaAnalysis,
    qualified_class_name: str,
    *,
    known_class_names: set[str],
) -> list[str]:
    """Superclass-only walk for inherited non-overridden methods.

    Only concrete/abstract superclasses contribute methods that a subclass
    inherits without re-declaring. Interfaces are excluded because an
    "unimplemented" interface method would not be a valid inherited method on a
    concrete server class, and CLDK signatures for generic interfaces often do
    not line up with the implementing method's signature.
    """
    order: list[str] = []
    visited: set[str] = {qualified_class_name}

    def _visit(class_name: str) -> None:
        class_details = analysis.get_class(class_name)
        if class_details is None:
            return
        for parent_reference in class_details.extends_list or []:
            resolved_parent = _resolve_known_class_name(
                type_reference=parent_reference,
                declaring_class_name=class_name,
                known_class_names=known_class_names,
            )
            if not resolved_parent or resolved_parent in visited:
                continue
            visited.add(resolved_parent)
            order.append(resolved_parent)
            _visit(resolved_parent)

    _visit(qualified_class_name)
    return order


class _MappingPredicate(Protocol):
    def __call__(
        self,
        method_annotations: list[str],
        *,
        class_imports: list[JImport],
    ) -> bool: ...


def _resolve_inherited_mapping_method(
    analysis: JavaAnalysis,
    *,
    supertype_order: list[str],
    method_signature: str,
    has_mapping_annotation: _MappingPredicate,
) -> tuple[str, JCallable] | None:
    """Nearest supertype declaration of the signature with a validated mapping.

    Matches on exact CLDK signature equality only, so a generic supertype whose
    erased parameter spelling differs from the override finds nothing (fail
    closed). Private supertype methods are never overrides. Mapping annotations
    validate against the supertype's own imports.
    """
    for supertype_name in supertype_order:
        supertype_method = cast(
            dict[str, JCallable],
            analysis.get_methods_in_class(supertype_name),
        ).get(method_signature)
        if supertype_method is None:
            continue
        if "private" in (supertype_method.modifiers or []):
            continue
        supertype_imports = _get_class_imports(analysis, supertype_name)
        if has_mapping_annotation(
            list(supertype_method.annotations or []),
            class_imports=supertype_imports,
        ):
            return supertype_name, supertype_method
    return None


def _resolve_inherited_spring_mapping_method(
    analysis: JavaAnalysis,
    *,
    supertype_order: list[str],
    method_signature: str,
) -> tuple[str, JCallable] | None:
    """Nearest supertype declaration of the signature with a validated Spring mapping."""
    return _resolve_inherited_mapping_method(
        analysis,
        supertype_order=supertype_order,
        method_signature=method_signature,
        has_mapping_annotation=_has_spring_mapping_annotation,
    )


def _has_jax_rs_mapping_annotation(
    method_annotations: list[str],
    *,
    class_imports: list[JImport],
) -> bool:
    for annotation in method_annotations:
        annotation_name = _annotation_short_name(annotation)
        if annotation_name not in _JAX_RS_METHOD_ANNOTATIONS:
            continue
        if _annotation_matches_framework(
            annotation,
            annotation_name,
            framework="jax-rs",
            class_imports=class_imports,
        ):
            return True
    return False


def _has_micronaut_mapping_annotation(
    method_annotations: list[str],
    *,
    class_imports: list[JImport],
) -> bool:
    for annotation in method_annotations:
        annotation_name = _annotation_short_name(annotation)
        if annotation_name not in _MICRONAUT_METHOD_ANNOTATIONS:
            continue
        if _annotation_matches_framework(
            annotation,
            annotation_name,
            framework="micronaut",
            class_imports=class_imports,
        ):
            return True
    return False


def _resolve_inherited_jax_rs_mapping_method(
    analysis: JavaAnalysis,
    *,
    supertype_order: list[str],
    method_signature: str,
) -> tuple[str, JCallable] | None:
    """Nearest supertype declaration of the signature with a validated JAX-RS verb."""
    return _resolve_inherited_mapping_method(
        analysis,
        supertype_order=supertype_order,
        method_signature=method_signature,
        has_mapping_annotation=_has_jax_rs_mapping_annotation,
    )


def _is_jax_rs_annotation(
    annotation: str,
    class_imports: list[JImport],
) -> bool:
    """Whether an annotation literal resolves to a JAX-RS annotation."""
    token = annotation_token(annotation)
    if not token:
        return False
    qualified_name = token.removeprefix("@").strip()
    if "." in qualified_name:
        package_name = qualified_name.rsplit(".", 1)[0]
        return any(
            package_name == root or package_name.startswith(f"{root}.")
            for root in _JAX_RS_PACKAGE_ROOTS
        )
    short_name = _annotation_short_name(annotation)
    if short_name not in _JAX_RS_BARE_OVERRIDE_ANNOTATION_IMPORT_ROOTS:
        return False
    return annotation_matches_expected(
        annotation=annotation,
        expected_annotation=short_name,
        class_imports=class_imports,
        import_roots_by_annotation=_JAX_RS_BARE_OVERRIDE_ANNOTATION_IMPORT_ROOTS,
    )


def _is_jax_rs_bare_override(
    method_details: JCallable,
    *,
    class_imports: list[JImport],
) -> bool:
    """Whether a method carries no JAX-RS annotations of its own.

    JAX-RS spec §3.6: method-level annotations are inherited from a supertype
    method only when the override declares no JAX-RS annotations itself, on the
    method or on any of its parameters.
    """
    for annotation in method_details.annotations or []:
        if _is_jax_rs_annotation(annotation, class_imports):
            return False
    for parameter in method_details.parameters:
        for annotation in parameter.annotations:
            if _is_jax_rs_annotation(annotation, class_imports):
                return False
    return True


def _is_jax_rs_parameter_annotation(
    annotation: str,
    class_imports: list[JImport],
) -> bool:
    annotation_name = _annotation_short_name(annotation)
    import_roots = PARAMETER_ANNOTATION_IMPORT_ROOTS_BY_FRAMEWORK["jax-rs"]
    if annotation_name in import_roots and annotation_matches_expected(
        annotation=annotation,
        expected_annotation=annotation_name,
        class_imports=class_imports,
        import_roots_by_annotation=import_roots,
    ):
        return True
    # Retain sibling annotations that make an inherited param optional
    # (@DefaultValue/@Nullable), matching the direct-path optionality signals so
    # an inherited optional param does not become "required".
    return _is_jax_rs_optionality_sibling_annotation(annotation)


def _inherited_parameter_annotations(
    analysis: JavaAnalysis,
    *,
    supertype_order: list[str],
    method_signature: str,
    parameter_count: int,
    annotation_filter: Callable[[str, list[JImport]], bool] | None = None,
) -> list[list[tuple[str, list[JImport], str]]]:
    """Per-index parameter annotations Spring merges from supertype overrides.

    Mirrors AnnotatedMethod.getInheritedParameterAnnotations (spring-core):
    every non-private supertype declaration of the signature contributes its
    parameter annotations, even when the concrete override carries its own
    mapping. Entries are nearest-first; the caller keeps the first declaration
    of each annotation type per parameter, with the concrete method winning.
    The declaring class name is included so constant resolution can bind to the
    supertype's package and imports.
    """
    inherited: list[list[tuple[str, list[JImport], str]]] = [
        [] for _ in range(parameter_count)
    ]
    for supertype_name in supertype_order:
        supertype_method = cast(
            dict[str, JCallable],
            analysis.get_methods_in_class(supertype_name),
        ).get(method_signature)
        if supertype_method is None:
            continue
        if "private" in (supertype_method.modifiers or []):
            continue
        if not any(param.annotations for param in supertype_method.parameters):
            continue
        supertype_imports = _get_class_imports(analysis, supertype_name)
        for index, param in enumerate(supertype_method.parameters):
            annotations = param.annotations
            if annotation_filter is not None:
                annotations = [
                    annotation
                    for annotation in annotations
                    if annotation_filter(annotation, supertype_imports)
                ]
            inherited[index].extend(
                (annotation, supertype_imports, supertype_name)
                for annotation in annotations
            )
    return inherited


def _spring_inherited_parameter_annotations(
    analysis: JavaAnalysis,
    *,
    supertype_order: list[str],
    method_signature: str,
    parameter_count: int,
) -> list[list[tuple[str, list[JImport], str]]]:
    """Per-index parameter annotations Spring merges from supertype overrides.

    Mirrors AnnotatedMethod.getInheritedParameterAnnotations (spring-core):
    every non-private supertype declaration of the signature contributes its
    parameter annotations, even when the concrete override carries its own
    mapping. Entries are nearest-first; the caller keeps the first declaration
    of each annotation type per parameter, with the concrete method winning.
    """
    return _inherited_parameter_annotations(
        analysis,
        supertype_order=supertype_order,
        method_signature=method_signature,
        parameter_count=parameter_count,
    )


def _extract_spring_endpoints(
    qualified_class_name: str,
    method_signature: str,
    class_paths: list[str],
    method_annotations: list[str],
    class_imports: list[JImport],
    endpoint_parameters: list[EndpointParameter],
    class_mapping_annotations: list[str] | None = None,
    constant_resolver: ClassBoundConstantResolver | None = None,
) -> list[ApplicationEndpoint]:
    class_paths = _paths_or_root(class_paths)

    # Class-level mapping constraints (e.g. @RequestMapping(params = "tenant"))
    # apply to every endpoint declared in the class.
    base_parameters = [
        *endpoint_parameters,
        *_extract_mapping_constraint_parameters(
            class_mapping_annotations or [],
            existing_parameters=endpoint_parameters,
        ),
    ]

    endpoints: list[ApplicationEndpoint] = []
    for annotation in method_annotations:
        annotation_name = _annotation_short_name(annotation)
        if annotation_name not in _SPRING_METHOD_ANNOTATION_NAMES:
            continue
        if not _annotation_matches_framework(
            annotation,
            annotation_name,
            framework="spring",
            class_imports=class_imports,
        ):
            continue

        method_paths = _paths_or_root(
            _extract_annotation_paths(annotation, constant_resolver)
        )

        # Method-level mapping constraints (e.g. @GetMapping(params = "name"))
        # contribute required query/header parameters without a method argument.
        effective_parameters = [
            *base_parameters,
            *_extract_mapping_constraint_parameters(
                [annotation],
                existing_parameters=base_parameters,
            ),
        ]

        # Handle RequestMapping for potential wild cards
        if annotation_name == "@RequestMapping":
            method_specs = _extract_request_mapping_method_specs(annotation)
        else:
            method_specs = [
                _ProductionMethodSpec(
                    _SPRING_DIRECT_METHOD_ANNOTATIONS[annotation_name],
                    False,
                )
            ]

        for method_spec in method_specs:
            for class_path in class_paths:
                for method_path in method_paths:
                    path_template = _join_paths(class_path, method_path)
                    endpoints.append(
                        ApplicationEndpoint(
                            http_method=method_spec.http_method,
                            is_method_wildcard=method_spec.is_method_wildcard,
                            path_template=path_template,
                            framework="spring",
                            declaring_class_name=qualified_class_name,
                            declaring_method_signature=method_signature,
                            parameters=_reconcile_path_parameters(
                                path_template, effective_parameters
                            ),
                        )
                    )

    return endpoints


def _extract_jax_rs_endpoints(
    qualified_class_name: str,
    method_signature: str,
    class_paths: list[str],
    method_annotations: list[str],
    class_imports: list[JImport],
    endpoint_parameters: list[EndpointParameter],
    constant_resolver: ClassBoundConstantResolver | None = None,
) -> list[ApplicationEndpoint]:
    class_paths = _paths_or_root(class_paths)

    method_paths: list[str] = []
    for annotation in method_annotations:
        if not _annotation_matches_framework(
            annotation,
            "@Path",
            framework="jax-rs",
            class_imports=class_imports,
        ):
            continue
        method_paths.extend(_extract_annotation_paths(annotation, constant_resolver))

    method_paths = _paths_or_root(method_paths)

    http_methods: list[str] = []
    for annotation in method_annotations:
        annotation_name = _annotation_short_name(annotation)
        method = _JAX_RS_METHOD_ANNOTATIONS.get(annotation_name)
        if method and _annotation_matches_framework(
            annotation,
            annotation_name,
            framework="jax-rs",
            class_imports=class_imports,
        ):
            http_methods.append(method)

    http_methods = _deduplicate_strings(http_methods)
    if not http_methods:
        return []

    endpoints: list[ApplicationEndpoint] = []
    for http_method in http_methods:
        for class_path in class_paths:
            for method_path in method_paths:
                path_template = _join_paths(class_path, method_path)
                endpoints.append(
                    ApplicationEndpoint(
                        http_method=http_method,
                        path_template=path_template,
                        framework="jax-rs",
                        declaring_class_name=qualified_class_name,
                        declaring_method_signature=method_signature,
                        parameters=_reconcile_path_parameters(
                            path_template, endpoint_parameters
                        ),
                    )
                )

    return endpoints


def _extract_micronaut_endpoints(
    qualified_class_name: str,
    method_signature: str,
    class_paths: list[str],
    method_annotations: list[str],
    class_imports: list[JImport],
    endpoint_parameters: list[EndpointParameter],
    constant_resolver: ClassBoundConstantResolver | None = None,
) -> list[ApplicationEndpoint]:
    class_paths = _paths_or_root(class_paths)

    endpoints: list[ApplicationEndpoint] = []
    for annotation in method_annotations:
        annotation_name = _annotation_short_name(annotation)
        http_method = _MICRONAUT_METHOD_ANNOTATIONS.get(annotation_name)
        if http_method is None:
            continue
        if not _annotation_matches_framework(
            annotation,
            annotation_name,
            framework="micronaut",
            class_imports=class_imports,
        ):
            continue

        method_paths = _paths_or_root(
            _extract_annotation_paths(annotation, constant_resolver)
        )

        for class_path in class_paths:
            for method_path in method_paths:
                path_template = _join_paths(class_path, method_path)
                # `{?max,offset}` in the route template statically declares optional
                # query parameters that join_paths strips from the path; synthesize
                # them so the query surface is not lost.
                template_parameters = [
                    *endpoint_parameters,
                    *_synthesize_micronaut_query_parameters(
                        [class_path, method_path], endpoint_parameters
                    ),
                ]
                endpoints.append(
                    ApplicationEndpoint(
                        http_method=http_method,
                        path_template=path_template,
                        framework="micronaut",
                        declaring_class_name=qualified_class_name,
                        declaring_method_signature=method_signature,
                        parameters=_reconcile_path_parameters(
                            path_template, template_parameters
                        ),
                    )
                )

    return endpoints


def _synthesize_micronaut_query_parameters(
    raw_paths: list[str],
    existing_parameters: list[EndpointParameter],
) -> list[EndpointParameter]:
    """Optional QUERY params declared by RFC 6570 ``{?...}`` groups in the raw
    route templates, minus any query name already bound by a recognized
    annotation (matched case-insensitively)."""
    existing_query_names = {
        parameter.name.lower()
        for parameter in existing_parameters
        if parameter.source == EndpointParameterSource.QUERY
    }
    synthesized: list[EndpointParameter] = []
    for raw_path in raw_paths:
        for name in _extract_rfc6570_query_parameter_names(raw_path):
            if name.lower() in existing_query_names:
                continue
            existing_query_names.add(name.lower())
            synthesized.append(
                EndpointParameter(
                    name=name,
                    source=EndpointParameterSource.QUERY,
                    required=False,
                    is_synthetic=True,
                )
            )
    return synthesized


def _deduplicate_application_endpoints(
    endpoints: list[ApplicationEndpoint],
) -> list[ApplicationEndpoint]:
    deduplicated: dict[
        tuple[str, bool, str, str, str, str | None],
        ApplicationEndpoint,
    ] = {}
    for endpoint in endpoints:
        key = (
            endpoint.http_method,
            endpoint.is_method_wildcard,
            endpoint.path_template,
            endpoint.framework,
            endpoint.declaring_class_name,
            endpoint.declaring_method_signature,
        )
        deduplicated[key] = endpoint
    return list(deduplicated.values())


def _declares_jax_rs_consumes(
    annotations: list[str],
    *,
    class_imports: list[JImport],
) -> bool:
    return any(
        _annotation_short_name(annotation) == "@Consumes"
        and annotation_matches_expected(
            annotation=annotation,
            expected_annotation="@Consumes",
            class_imports=class_imports,
            import_roots_by_annotation=_JAX_RS_CONSUMES_IMPORT_ROOTS,
        )
        for annotation in annotations
    )


def _jax_rs_method_accepts_body(
    method_annotations: list[str],
    class_annotations: list[str],
    *,
    class_imports: list[JImport],
) -> bool:
    """Whether a JAX-RS resource method can carry a request entity body.

    True for a body-capable verb (POST/PUT/PATCH). For other verbs an explicit
    ``@Consumes`` — declared on the method or inherited from the class — signals
    a body (e.g. a ``@Consumes`` DELETE). GET/HEAD/OPTIONS never carry an entity,
    so they are excluded even when the class declares ``@Consumes``.
    """
    verbs = {
        _JAX_RS_METHOD_ANNOTATIONS[annotation_name]
        for annotation in method_annotations
        if (annotation_name := _annotation_short_name(annotation))
        in _JAX_RS_METHOD_ANNOTATIONS
        and _annotation_matches_framework(
            annotation,
            annotation_name,
            framework="jax-rs",
            class_imports=class_imports,
        )
    }
    if verbs & _JAX_RS_BODY_CAPABLE_HTTP_METHODS:
        return True
    if not verbs or verbs <= _JAX_RS_BODY_INCAPABLE_HTTP_METHODS:
        return False
    return _declares_jax_rs_consumes(
        method_annotations, class_imports=class_imports
    ) or _declares_jax_rs_consumes(class_annotations, class_imports=class_imports)


def _is_jax_rs_body_candidate(param: JCallableParameter) -> bool:
    """Whether a parameter could be the JAX-RS entity body.

    Excludes parameters carrying a recognized binding/injection annotation and
    framework-injected types; bean-validation annotations (``@Valid``,
    ``@NotNull``) and other unrecognized annotations do not disqualify a body.
    """
    annotation_short_names = {
        _annotation_short_name(annotation) for annotation in param.annotations
    }
    if annotation_short_names & _JAX_RS_NON_BODY_PARAMETER_ANNOTATIONS:
        return False
    return _simple_type_name(param.type) not in _JAX_RS_NON_BODY_PARAMETER_TYPES


def _synthesize_jax_rs_body_parameter(
    method_details: JCallable,
    *,
    class_annotations: list[str],
    class_imports: list[JImport],
) -> EndpointParameter | None:
    """Synthesize a BODY parameter for a JAX-RS entity argument.

    JAX-RS binds the single parameter with no injection/binding annotation as
    the request entity (the spec permits at most one). On a body-capable method
    the lone such non-framework parameter is that entity body. Returns ``None``
    when the method cannot carry a body or the entity is ambiguous (zero or more
    than one candidate), keeping synthesis conservative.
    """
    if not _jax_rs_method_accepts_body(
        list(method_details.annotations or []),
        class_annotations,
        class_imports=class_imports,
    ):
        return None

    candidates = [
        param for param in method_details.parameters if _is_jax_rs_body_candidate(param)
    ]
    if len(candidates) != 1:
        return None

    entity = candidates[0]
    required = _parameter_is_required(
        source=EndpointParameterSource.BODY,
        body="",
        framework="jax-rs",
        sibling_annotation_short_names={
            _annotation_short_name(annotation) for annotation in entity.annotations
        },
        simple_type_name=_simple_type_name(entity.type),
    )
    return EndpointParameter(
        name=entity.name or "body",
        type=entity.type,
        source=EndpointParameterSource.BODY,
        required=required,
    )


# Aggregate command/bean bindings handled outside the single-name source map.
# Spring @ModelAttribute is always unscorable (command-object property names are
# not enumerable); JAX-RS @BeanParam is expanded into its field bindings when the
# bean type resolves, otherwise it falls back to an unscorable marker.
_SPRING_MODEL_ATTRIBUTE_ANNOTATION: str = "@ModelAttribute"
_JAX_RS_BEAN_PARAM_ANNOTATION: str = "@BeanParam"


def _unscorable_structured_binding(
    *,
    name: str | None,
    type_name: str | None,
    annotation: str,
) -> EndpointParameter:
    """A structured aggregate binding recorded for inventory but not scored.

    The submitted request names are bean properties/fields we cannot enumerate
    statically, so a single placeholder carries ``is_unscorable``. The request
    sources it spans (query/header/path/form) are likewise unknown, so it is
    recorded as UNKNOWN rather than guessing FORM.
    """
    return EndpointParameter(
        name=name or "unknown",
        type=type_name,
        source=EndpointParameterSource.UNKNOWN,
        required=False,
        annotation=annotation,
        is_unscorable=True,
    )


def _bean_property_name(method_name: str) -> str:
    """Best-effort JavaBeans property name for a getter/setter method name.

    Only used as a fallback: JAX-RS parameter annotations carry a mandatory
    explicit name, so the derived name is rarely the one actually bound.
    """
    for prefix in ("set", "get"):
        if method_name.startswith(prefix) and len(method_name) > len(prefix):
            remainder = method_name[len(prefix) :]
            return remainder[:1].lower() + remainder[1:]
    if method_name.startswith("is") and len(method_name) > len("is"):
        remainder = method_name[len("is") :]
        return remainder[:1].lower() + remainder[1:]
    return method_name


def _bean_binding_sites(
    class_details: JType,
) -> list[tuple[list[str], str, str]]:
    """Yield ``(annotations, default_name, type)`` for each tangible binding site.

    JAX-RS injects ``@BeanParam`` members through fields, bean properties
    (setter/getter), and constructor parameters; scanning is scoped to those
    accessors so unrelated methods that happen to carry a binding annotation are
    ignored. A setter contributes both its method-level annotations (the common
    property-injection form) and its parameter's annotations.
    """
    sites: list[tuple[list[str], str, str]] = []
    for field in class_details.field_declarations or []:
        default_name = field.variables[0] if field.variables else "unknown"
        sites.append((field.annotations, default_name, field.type))

    for method in class_details.callable_declarations.values():
        parameters = method.parameters or []
        if method.is_constructor:
            for parameter in parameters:
                sites.append(
                    (
                        parameter.annotations,
                        parameter.name or "unknown",
                        parameter.type,
                    )
                )
            continue

        method_name = (method.signature or "").split("(", 1)[0].strip()
        is_setter = method_name.startswith("set") and len(parameters) == 1
        is_getter = (
            method_name.startswith("get") or method_name.startswith("is")
        ) and len(parameters) == 0
        if is_setter:
            property_name = _bean_property_name(method_name)
            sites.append((method.annotations, property_name, parameters[0].type))
            sites.append(
                (
                    parameters[0].annotations,
                    parameters[0].name or property_name,
                    parameters[0].type,
                )
            )
        elif is_getter:
            sites.append(
                (
                    method.annotations,
                    _bean_property_name(method_name),
                    method.return_type or "",
                )
            )

    return sites


def _map_bean_binding_site(
    *,
    annotations: list[str],
    default_name: str,
    type_name: str,
    declaring_class_name: str,
    class_imports: list[JImport],
    analysis: JavaAnalysis,
    known_class_names: set[str],
    visited_classes: frozenset[str],
    constant_resolver: ConstantResolver | None = None,
) -> list[EndpointParameter]:
    """Map a single bean binding site to its endpoint parameter(s).

    A nested ``@BeanParam`` recurses; an unresolved nested bean yields an
    unscorable marker rather than vanishing. Unrecognized annotations and
    cookie/matrix bindings (no tracked source) contribute nothing.
    """
    import_roots = PARAMETER_ANNOTATION_IMPORT_ROOTS_BY_FRAMEWORK["jax-rs"]
    field_sources = PARAMETER_ANNOTATION_SOURCES["jax-rs"]
    sibling_short_names = {
        _annotation_short_name(annotation) for annotation in annotations
    }
    simple_type_name = _simple_type_name(type_name)
    site_constant_resolver = _class_bound_resolver(
        constant_resolver, declaring_class_name
    )
    for annotation in annotations:
        short_name = _annotation_short_name(annotation)
        if short_name == _JAX_RS_BEAN_PARAM_ANNOTATION:
            if not annotation_matches_expected(
                annotation=annotation,
                expected_annotation=short_name,
                class_imports=class_imports,
                import_roots_by_annotation=import_roots,
            ):
                continue
            nested = _expand_jax_rs_bean_param(
                bean_type=type_name,
                declaring_class_name=declaring_class_name,
                analysis=analysis,
                known_class_names=known_class_names,
                visited_classes=visited_classes,
                constant_resolver=constant_resolver,
            )
            if nested is None:
                return [
                    _unscorable_structured_binding(
                        name=default_name,
                        type_name=type_name,
                        annotation=short_name,
                    )
                ]
            return nested
        source = field_sources.get(short_name)
        if source is None:
            continue
        if not annotation_matches_expected(
            annotation=annotation,
            expected_annotation=short_name,
            class_imports=class_imports,
            import_roots_by_annotation=import_roots,
        ):
            continue
        body = _annotation_body(annotation)
        explicit_name = _annotation_explicit_name(
            body,
            constant_resolver=site_constant_resolver,
            class_imports=class_imports,
        )
        return [
            EndpointParameter(
                name=explicit_name if explicit_name is not None else default_name,
                type=type_name,
                source=source,
                required=_parameter_is_required(
                    source=source,
                    body=body,
                    framework="jax-rs",
                    sibling_annotation_short_names=sibling_short_names,
                    simple_type_name=simple_type_name,
                ),
                annotation=short_name,
            )
        ]
    return []


def _dedupe_bean_params(
    parameters: list[EndpointParameter],
) -> list[EndpointParameter]:
    """Collapse params bound to the same source+name (field and accessor pair)."""
    seen: set[tuple[str, str]] = set()
    deduped: list[EndpointParameter] = []
    for parameter in parameters:
        key = (parameter.source.value, parameter.name.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(parameter)
    return deduped


def _expand_jax_rs_bean_param(
    *,
    bean_type: str,
    declaring_class_name: str,
    analysis: JavaAnalysis,
    known_class_names: set[str],
    visited_classes: frozenset[str],
    constant_resolver: ConstantResolver | None = None,
) -> list[EndpointParameter] | None:
    """Expand a JAX-RS ``@BeanParam`` bean into its bound parameters.

    Walks the bean and its superclasses in inheritance order and collects
    bindings from fields, bean properties (setter/getter), and constructor
    parameters; nested ``@BeanParam`` members recurse. Results are deduplicated
    by source+name (subclass declarations take precedence). Returns ``None`` when
    the bean type cannot be resolved (caller falls back to an unscorable marker)
    and an empty list for a self-referential cycle.
    """
    bean_class = _resolve_known_class_name(
        type_reference=bean_type,
        declaring_class_name=declaring_class_name,
        known_class_names=known_class_names,
    )
    if bean_class is None or analysis.get_class(bean_class) is None:
        return None
    if bean_class in visited_classes:
        return []

    next_visited = visited_classes | {bean_class}
    resolution_order = Reachability(analysis).get_class_resolution_order(
        bean_class,
        include_superclasses=True,
        include_interfaces=False,
    ) or [bean_class]

    collected: list[EndpointParameter] = []
    for class_name in resolution_order:
        class_details = analysis.get_class(class_name)
        if class_details is None:
            continue
        class_imports = _get_class_imports(analysis, class_name)
        for site_annotations, default_name, type_name in _bean_binding_sites(
            class_details
        ):
            collected.extend(
                _map_bean_binding_site(
                    annotations=site_annotations,
                    default_name=default_name,
                    type_name=type_name,
                    declaring_class_name=class_name,
                    class_imports=class_imports,
                    analysis=analysis,
                    known_class_names=known_class_names,
                    visited_classes=next_visited,
                    constant_resolver=constant_resolver,
                )
            )

    return _dedupe_bean_params(collected)


def _extract_method_parameters(
    method_details: JCallable,
    *,
    framework: FrameworkName,
    class_imports: list[JImport],
    class_annotations: list[str] | None = None,
    declaring_class_name: str | None = None,
    analysis: JavaAnalysis | None = None,
    known_class_names: set[str] | None = None,
    inherited_parameter_annotations: (
        list[list[tuple[str, list[JImport], str]]] | None
    ) = None,
    constant_resolver: ConstantResolver | None = None,
) -> list[EndpointParameter]:
    param_import_roots = PARAMETER_ANNOTATION_IMPORT_ROOTS_BY_FRAMEWORK[framework]

    parameters: list[EndpointParameter] = []
    for param_index, param in enumerate(method_details.parameters):
        annotation_entries: list[tuple[str, str, list[JImport], str | None]] = [
            (
                _annotation_short_name(annotation),
                annotation,
                class_imports,
                declaring_class_name,
            )
            for annotation in param.annotations
        ]
        if inherited_parameter_annotations is not None:
            # Inherited annotations validate against their declaring class's
            # imports. Entry order (concrete first, then nearest supertype)
            # plus the first-valid-binding break below realizes Spring's
            # concrete-wins-per-type merge without suppressing an inherited
            # annotation behind a concrete lookalike that fails validation.
            annotation_entries.extend(
                (
                    _annotation_short_name(annotation),
                    annotation,
                    declaring_imports,
                    supertype_name,
                )
                for annotation, declaring_imports, supertype_name in inherited_parameter_annotations[
                    param_index
                ]
            )
        sibling_annotation_short_names = {entry[0] for entry in annotation_entries}
        simple_type_name = _simple_type_name(param.type)
        for (
            short_name,
            annotation,
            annotation_imports,
            annotation_declaring_class,
        ) in annotation_entries:
            class_bound_resolver = _class_bound_resolver(
                constant_resolver, annotation_declaring_class
            )
            if (
                framework == "spring"
                and short_name == _SPRING_MODEL_ATTRIBUTE_ANNOTATION
            ):
                if not annotation_matches_expected(
                    annotation=annotation,
                    expected_annotation=short_name,
                    class_imports=annotation_imports,
                    import_roots_by_annotation=param_import_roots,
                ):
                    continue
                parameters.append(
                    _unscorable_structured_binding(
                        name=param.name,
                        type_name=param.type,
                        annotation=short_name,
                    )
                )
                break
            if framework == "jax-rs" and short_name == _JAX_RS_BEAN_PARAM_ANNOTATION:
                if not annotation_matches_expected(
                    annotation=annotation,
                    expected_annotation=short_name,
                    class_imports=annotation_imports,
                    import_roots_by_annotation=param_import_roots,
                ):
                    continue
                expanded: list[EndpointParameter] | None = None
                if analysis is not None and declaring_class_name is not None:
                    expanded = _expand_jax_rs_bean_param(
                        bean_type=param.type,
                        declaring_class_name=declaring_class_name,
                        analysis=analysis,
                        known_class_names=known_class_names or set(),
                        visited_classes=frozenset(),
                        constant_resolver=constant_resolver,
                    )
                # A resolved bean that yields no scorable bindings (only
                # cookie/matrix, constructor-only we cannot characterize, or an
                # unresolvable bean) is recorded as unscorable rather than dropped.
                if not expanded:
                    parameters.append(
                        _unscorable_structured_binding(
                            name=param.name,
                            type_name=param.type,
                            annotation=short_name,
                        )
                    )
                else:
                    parameters.extend(expanded)
                break

            binding = classify_annotation_parameter_binding(
                param=param,
                short_name=short_name,
                annotation=annotation,
                framework=framework,
                class_imports=annotation_imports,
                sibling_annotation_short_names=sibling_annotation_short_names,
                simple_type_name=simple_type_name,
                constant_resolver=class_bound_resolver,
            )
            if binding is not None:
                parameters.append(binding)
                break

    # JAX-RS request bodies are unannotated entity parameters, so the annotation
    # loop above never emits them; synthesize one when unambiguous.
    if framework == "jax-rs" and not any(
        param.source == EndpointParameterSource.BODY for param in parameters
    ):
        body_parameter = _synthesize_jax_rs_body_parameter(
            method_details,
            class_annotations=class_annotations or [],
            class_imports=class_imports,
        )
        if body_parameter is not None:
            parameters.append(body_parameter)

    return parameters


def _discover_application_path_prefixes(
    *,
    application_classes: list[str],
    class_annotations_by_class: dict[str, list[str]],
    class_imports_by_class: dict[str, list[JImport]],
    constant_resolver: ConstantResolver | None,
) -> set[str]:
    """Collect normalized JAX-RS @ApplicationPath prefixes mounted across the app.

    @ApplicationPath is not @Inherited and sits on concrete Application subclasses,
    so a flat scan over application_classes is sufficient. Root-equivalent values
    (``""``/``"/"``) contribute no prefix and are excluded.
    """
    prefixes: set[str] = set()
    for qualified_class_name in application_classes:
        class_annotations = class_annotations_by_class.get(qualified_class_name, [])
        class_imports = class_imports_by_class.get(qualified_class_name, [])
        for annotation in class_annotations:
            if not _annotation_matches_framework(
                annotation,
                "@ApplicationPath",
                framework="jax-rs",
                class_imports=class_imports,
            ):
                continue
            for raw_path in _extract_annotation_paths(
                annotation,
                _class_bound_resolver(constant_resolver, qualified_class_name),
            ):
                normalized = normalize_path(raw_path)
                if normalized != "/":
                    prefixes.add(normalized)
    return prefixes


def extract_application_endpoints(
    analysis: JavaAnalysis,
    application_classes: list[str],
    constant_resolver: ConstantResolver | None = None,
) -> EndpointExtractionResult:
    endpoints: list[ApplicationEndpoint] = []

    known_class_names = set(application_classes)
    known_class_names.update(analysis.get_classes().keys())

    class_annotations_by_class: dict[str, list[str]] = {}
    class_imports_by_class: dict[str, list[JImport]] = {}
    effective_class_annotations_by_class: dict[str, list[ResolvedAnnotation]] = {}
    methods_by_class: dict[str, dict[str, JCallable]] = {}
    for qualified_class_name in application_classes:
        class_details = analysis.get_class(qualified_class_name)
        class_annotations_by_class[qualified_class_name] = (
            list(class_details.annotations or []) if class_details else []
        )
        class_imports_by_class[qualified_class_name] = _get_class_imports(
            analysis=analysis,
            qualified_class_name=qualified_class_name,
        )
        effective_class_annotations_by_class[qualified_class_name] = (
            resolve_effective_class_annotations(
                analysis=analysis,
                qualified_class_name=qualified_class_name,
                known_class_names=known_class_names,
                config=_ENDPOINT_ANNOTATION_RESOLUTION_CONFIG,
            )
        )
        methods_by_class[qualified_class_name] = cast(
            dict[str, JCallable],
            analysis.get_methods_in_class(qualified_class_name),
        )

    # Resolve JAX-RS class mount paths (inheritance + sub-resource locator chains)
    # HTTP endpoints are determined by method-level annotations in the per-class loop.
    jax_rs_mount_paths_by_class = _build_jax_rs_class_mount_paths(
        analysis=analysis,
        application_classes=application_classes,
        class_annotations_by_class=class_annotations_by_class,
        class_imports_by_class=class_imports_by_class,
        methods_by_class=methods_by_class,
        constant_resolver=constant_resolver,
    )

    is_spring_server_class_by_class: dict[str, bool] = {}
    is_micronaut_server_class_by_class: dict[str, bool] = {}
    for qualified_class_name in application_classes:
        direct_class_annotations = class_annotations_by_class.get(
            qualified_class_name, []
        )
        direct_class_imports = class_imports_by_class.get(qualified_class_name, [])
        effective_class_annotations = effective_class_annotations_by_class.get(
            qualified_class_name, []
        )
        is_spring_server_class_by_class[qualified_class_name] = _is_spring_server_class(
            effective_class_annotations=effective_class_annotations,
            direct_class_annotations=direct_class_annotations,
            direct_class_imports=direct_class_imports,
        )
        is_micronaut_server_class_by_class[qualified_class_name] = (
            _is_micronaut_server_class(
                effective_class_annotations=effective_class_annotations,
                direct_class_annotations=direct_class_annotations,
                direct_class_imports=direct_class_imports,
            )
        )

    application_path_prefixes = _discover_application_path_prefixes(
        application_classes=application_classes,
        class_annotations_by_class=class_annotations_by_class,
        class_imports_by_class=class_imports_by_class,
        constant_resolver=constant_resolver,
    )

    for qualified_class_name in application_classes:
        direct_class_annotations = class_annotations_by_class.get(
            qualified_class_name, []
        )
        direct_class_imports = class_imports_by_class.get(qualified_class_name, [])
        effective_class_annotations = effective_class_annotations_by_class.get(
            qualified_class_name, []
        )
        effective_class_imports_by_class: dict[str, list[JImport]] = {
            class_name: class_imports_by_class.get(class_name)
            or _get_class_imports(analysis, class_name)
            for class_name in {
                resolved_annotation.declaring_class_name
                for resolved_annotation in effective_class_annotations
            }
        }
        is_spring_server_class = is_spring_server_class_by_class[qualified_class_name]
        spring_class_paths = (
            _extract_class_paths_from_resolved(
                effective_class_annotations,
                "@RequestMapping",
                framework="spring",
                class_imports_by_class=effective_class_imports_by_class,
                constant_resolver=constant_resolver,
            )
            if is_spring_server_class
            else []
        )
        spring_class_mapping_annotations = (
            _resolved_spring_request_mapping_annotations(
                effective_class_annotations,
                class_imports_by_class=effective_class_imports_by_class,
            )
            if is_spring_server_class
            else []
        )
        spring_supertype_order = (
            _supertype_method_search_order(
                analysis,
                qualified_class_name,
                known_class_names=known_class_names,
            )
            if is_spring_server_class
            else []
        )
        jax_rs_class_paths = jax_rs_mount_paths_by_class.get(qualified_class_name, [])
        jax_rs_supertype_order = (
            _jax_rs_supertype_method_search_order(
                analysis,
                qualified_class_name,
                known_class_names=known_class_names,
            )
            if jax_rs_class_paths
            else []
        )
        is_micronaut_server_class = is_micronaut_server_class_by_class[
            qualified_class_name
        ]
        micronaut_class_paths = (
            _extract_class_paths_from_resolved(
                effective_class_annotations,
                "@Controller",
                framework="micronaut",
                class_imports_by_class=effective_class_imports_by_class,
                constant_resolver=constant_resolver,
            )
            if is_micronaut_server_class
            else []
        )
        method_constant_resolver = _class_bound_resolver(
            constant_resolver, qualified_class_name
        )
        methods = methods_by_class.get(qualified_class_name, {})

        for method_signature, method_details in methods.items():
            method_annotations = list(method_details.annotations or [])

            if is_spring_server_class:
                # Contract-first controllers: a bare override picks up its
                # mapping from the nearest supertype declaration
                # (findMergedAnnotation), while parameter annotations merge
                # from all supertype overrides even when the override carries
                # its own mapping (AnnotatedMethod).
                mapping_class_name = qualified_class_name
                mapping_method_details = method_details
                mapping_class_imports = direct_class_imports
                if spring_supertype_order and not _has_spring_mapping_annotation(
                    method_annotations,
                    class_imports=direct_class_imports,
                ):
                    inherited_mapping = _resolve_inherited_spring_mapping_method(
                        analysis,
                        supertype_order=spring_supertype_order,
                        method_signature=method_signature,
                    )
                    if inherited_mapping is not None:
                        mapping_class_name, mapping_method_details = inherited_mapping
                        mapping_class_imports = _get_class_imports(
                            analysis, mapping_class_name
                        )
                inherited_parameter_annotations = (
                    _spring_inherited_parameter_annotations(
                        analysis,
                        supertype_order=spring_supertype_order,
                        method_signature=method_signature,
                        parameter_count=len(method_details.parameters),
                    )
                    if spring_supertype_order
                    else None
                )
                spring_params = _extract_method_parameters(
                    method_details,
                    framework="spring",
                    class_imports=direct_class_imports,
                    declaring_class_name=qualified_class_name,
                    analysis=analysis,
                    known_class_names=known_class_names,
                    inherited_parameter_annotations=inherited_parameter_annotations,
                    constant_resolver=constant_resolver,
                )
                spring_eps = _extract_spring_endpoints(
                    qualified_class_name=qualified_class_name,
                    method_signature=method_signature,
                    class_paths=spring_class_paths,
                    method_annotations=list(mapping_method_details.annotations or []),
                    class_imports=mapping_class_imports,
                    endpoint_parameters=spring_params,
                    class_mapping_annotations=spring_class_mapping_annotations,
                    constant_resolver=_class_bound_resolver(
                        constant_resolver, mapping_class_name
                    ),
                )
                endpoints.extend(spring_eps)
            if jax_rs_class_paths:
                jax_rs_mapping_method_details = method_details
                jax_rs_mapping_class_imports = direct_class_imports
                jax_rs_mapping_class_name = qualified_class_name
                jax_rs_method_annotations = method_annotations
                jax_rs_inherited_parameter_annotations = None
                if jax_rs_supertype_order and _is_jax_rs_bare_override(
                    method_details,
                    class_imports=direct_class_imports,
                ):
                    inherited_mapping = _resolve_inherited_jax_rs_mapping_method(
                        analysis,
                        supertype_order=jax_rs_supertype_order,
                        method_signature=method_signature,
                    )
                    if inherited_mapping is not None:
                        # A bare override inherits the supertype's method mapping
                        # (Jakarta REST §3.6) and exposes it under THIS class's own
                        # mount. This holds even when the supertype is itself a
                        # concrete @Path root resource keeping its own separate
                        # mount, so it is not gated on supertype mount membership
                        # (mirrors the Spring bare-override handling above).
                        jax_rs_mapping_class_name, jax_rs_mapping_method_details = (
                            inherited_mapping
                        )
                        jax_rs_mapping_class_imports = _get_class_imports(
                            analysis, jax_rs_mapping_class_name
                        )
                        jax_rs_method_annotations = list(
                            jax_rs_mapping_method_details.annotations or []
                        )
                        jax_rs_inherited_parameter_annotations = (
                            _inherited_parameter_annotations(
                                analysis,
                                supertype_order=jax_rs_supertype_order,
                                method_signature=method_signature,
                                parameter_count=len(method_details.parameters),
                                annotation_filter=_is_jax_rs_parameter_annotation,
                            )
                        )
                if jax_rs_mapping_method_details is method_details:
                    jax_rs_params = _extract_method_parameters(
                        method_details,
                        framework="jax-rs",
                        class_imports=direct_class_imports,
                        class_annotations=direct_class_annotations,
                        declaring_class_name=qualified_class_name,
                        analysis=analysis,
                        known_class_names=known_class_names,
                        constant_resolver=constant_resolver,
                    )
                else:
                    jax_rs_params = _extract_method_parameters(
                        jax_rs_mapping_method_details,
                        framework="jax-rs",
                        class_imports=jax_rs_mapping_class_imports,
                        class_annotations=direct_class_annotations,
                        declaring_class_name=jax_rs_mapping_class_name,
                        analysis=analysis,
                        known_class_names=known_class_names,
                        inherited_parameter_annotations=jax_rs_inherited_parameter_annotations,
                        constant_resolver=constant_resolver,
                    )
                jax_rs_eps = _extract_jax_rs_endpoints(
                    qualified_class_name=qualified_class_name,
                    method_signature=method_signature,
                    class_paths=jax_rs_class_paths,
                    method_annotations=jax_rs_method_annotations,
                    class_imports=jax_rs_mapping_class_imports,
                    endpoint_parameters=jax_rs_params,
                    constant_resolver=_class_bound_resolver(
                        constant_resolver, jax_rs_mapping_class_name
                    ),
                )
                endpoints.extend(jax_rs_eps)
            if is_micronaut_server_class:
                micronaut_params = _extract_method_parameters(
                    method_details,
                    framework="micronaut",
                    class_imports=direct_class_imports,
                    declaring_class_name=qualified_class_name,
                    constant_resolver=constant_resolver,
                )
                micronaut_eps = _extract_micronaut_endpoints(
                    qualified_class_name=qualified_class_name,
                    method_signature=method_signature,
                    class_paths=micronaut_class_paths,
                    method_annotations=method_annotations,
                    class_imports=direct_class_imports,
                    endpoint_parameters=micronaut_params,
                    constant_resolver=method_constant_resolver,
                )
                endpoints.extend(micronaut_eps)

        # Methods inherited from non-server supertypes without an override are
        # real routes on the concrete server class (generic base controller
        # pattern). Skip signatures declared locally and methods declared on
        # supertypes that are server classes themselves (those are extracted in
        # their own iteration).
        local_method_signatures = set(methods.keys())
        inherited_method_signatures: set[str] = set()

        if is_spring_server_class:
            for supertype_name in _superclass_search_order(
                analysis, qualified_class_name, known_class_names=known_class_names
            ):
                if is_spring_server_class_by_class.get(supertype_name, False):
                    continue
                supertype_methods = cast(
                    dict[str, JCallable],
                    analysis.get_methods_in_class(supertype_name),
                )
                supertype_imports = _get_class_imports(analysis, supertype_name)
                for method_signature, supertype_method in supertype_methods.items():
                    if method_signature in local_method_signatures:
                        continue
                    if method_signature in inherited_method_signatures:
                        continue
                    if "private" in (supertype_method.modifiers or []):
                        continue
                    if not _has_spring_mapping_annotation(
                        list(supertype_method.annotations or []),
                        class_imports=supertype_imports,
                    ):
                        continue
                    inherited_method_signatures.add(method_signature)
                    inherited_params = _extract_method_parameters(
                        supertype_method,
                        framework="spring",
                        class_imports=supertype_imports,
                        declaring_class_name=supertype_name,
                        analysis=analysis,
                        known_class_names=known_class_names,
                        constant_resolver=constant_resolver,
                    )
                    inherited_eps = _extract_spring_endpoints(
                        qualified_class_name=qualified_class_name,
                        method_signature=method_signature,
                        class_paths=spring_class_paths,
                        method_annotations=list(supertype_method.annotations or []),
                        class_imports=supertype_imports,
                        endpoint_parameters=inherited_params,
                        class_mapping_annotations=spring_class_mapping_annotations,
                        constant_resolver=_class_bound_resolver(
                            constant_resolver, supertype_name
                        ),
                    )
                    endpoints.extend(inherited_eps)

        if jax_rs_class_paths:
            for supertype_name in _superclass_search_order(
                analysis, qualified_class_name, known_class_names=known_class_names
            ):
                if supertype_name in jax_rs_mount_paths_by_class:
                    continue
                supertype_methods = cast(
                    dict[str, JCallable],
                    analysis.get_methods_in_class(supertype_name),
                )
                supertype_imports = _get_class_imports(analysis, supertype_name)
                for method_signature, supertype_method in supertype_methods.items():
                    if method_signature in local_method_signatures:
                        continue
                    if method_signature in inherited_method_signatures:
                        continue
                    if "private" in (supertype_method.modifiers or []):
                        continue
                    if not _has_jax_rs_mapping_annotation(
                        list(supertype_method.annotations or []),
                        class_imports=supertype_imports,
                    ):
                        continue
                    inherited_method_signatures.add(method_signature)
                    inherited_params = _extract_method_parameters(
                        supertype_method,
                        framework="jax-rs",
                        class_imports=supertype_imports,
                        class_annotations=direct_class_annotations,
                        declaring_class_name=supertype_name,
                        analysis=analysis,
                        known_class_names=known_class_names,
                        constant_resolver=constant_resolver,
                    )
                    inherited_eps = _extract_jax_rs_endpoints(
                        qualified_class_name=qualified_class_name,
                        method_signature=method_signature,
                        class_paths=jax_rs_class_paths,
                        method_annotations=list(supertype_method.annotations or []),
                        class_imports=supertype_imports,
                        endpoint_parameters=inherited_params,
                        constant_resolver=_class_bound_resolver(
                            constant_resolver, supertype_name
                        ),
                    )
                    endpoints.extend(inherited_eps)

        if is_micronaut_server_class:
            for supertype_name in _superclass_search_order(
                analysis, qualified_class_name, known_class_names=known_class_names
            ):
                if is_micronaut_server_class_by_class.get(supertype_name, False):
                    continue
                supertype_methods = cast(
                    dict[str, JCallable],
                    analysis.get_methods_in_class(supertype_name),
                )
                supertype_imports = _get_class_imports(analysis, supertype_name)
                for method_signature, supertype_method in supertype_methods.items():
                    if method_signature in local_method_signatures:
                        continue
                    if method_signature in inherited_method_signatures:
                        continue
                    if "private" in (supertype_method.modifiers or []):
                        continue
                    if not _has_micronaut_mapping_annotation(
                        list(supertype_method.annotations or []),
                        class_imports=supertype_imports,
                    ):
                        continue
                    inherited_method_signatures.add(method_signature)
                    inherited_params = _extract_method_parameters(
                        supertype_method,
                        framework="micronaut",
                        class_imports=supertype_imports,
                        declaring_class_name=supertype_name,
                        constant_resolver=constant_resolver,
                    )
                    inherited_eps = _extract_micronaut_endpoints(
                        qualified_class_name=qualified_class_name,
                        method_signature=method_signature,
                        class_paths=micronaut_class_paths,
                        method_annotations=list(supertype_method.annotations or []),
                        class_imports=supertype_imports,
                        endpoint_parameters=inherited_params,
                        constant_resolver=_class_bound_resolver(
                            constant_resolver, supertype_name
                        ),
                    )
                    endpoints.extend(inherited_eps)

    return EndpointExtractionResult(
        endpoints=_deduplicate_application_endpoints(endpoints),
        application_path_prefixes=tuple(sorted(application_path_prefixes)),
    )
