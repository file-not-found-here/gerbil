"""Annotation-driven classification of Spring declarative HTTP client calls
(@FeignClient and @HttpExchange interfaces), where the verb and path live on the
callee interface's mapping annotations rather than at the call site (so
receiver-prefix matching cannot identify them)."""

from __future__ import annotations

from collections.abc import Callable

from cldk.models.java.models import JCallSite

from gerbil.analysis.schema import (
    EndpointParameterSource,
    HttpClassification,
    HttpDispatchFramework,
    HttpRequestRole,
)
from gerbil.analysis.schema.types import _template_path_variable_names
from gerbil.analysis.shared.http_mapping_annotations import (
    deduplicate_strings,
    extract_http_exchange_type_method,
    extract_spring_client_base_path,
    extract_spring_method_mapping,
    join_request_paths,
    spring_client_interface_kind,
)
from gerbil.analysis.shared.parameter_binding import extract_request_parameter_bindings
from gerbil.analysis.shared.receiver_resolution import ResolvedCallee

_CLIENT_KIND_FRAMEWORK: dict[str, HttpDispatchFramework] = {
    "feign": HttpDispatchFramework.FEIGN,
    "http-interface": HttpDispatchFramework.HTTP_INTERFACE,
}
_CLIENT_KIND_OWNER_FAMILY: dict[str, str] = {
    "feign": "feign.client_interface",
    "http-interface": "http-interface.client_interface",
}


def _project_request_parameter_evidence(
    callee: ResolvedCallee,
    constant_resolver: Callable[[str], str | None] | None = None,
) -> tuple[list[str], list[str], list[str], bool]:
    """Project the callee method's parameter bindings into event evidence.

    Returns ``(query names, header names, form names, has body)``. Both
    @FeignClient (SpringMvcContract) and @HttpExchange bind parameters with the
    standard Spring web annotations, so the "spring" binding rules apply to each.
    PATH bindings are omitted because the composed route template is the source
    of truth for path variables; an aggregate open query surface (an unnamed
    @RequestParam Map) carries no concrete key, so it contributes no query name.
    """
    query_names: list[str] = []
    header_names: list[str] = []
    form_names: list[str] = []
    has_body = False
    for binding in extract_request_parameter_bindings(
        callee.method_parameters,
        framework="spring",
        class_imports=callee.class_imports,
        constant_resolver=constant_resolver,
    ):
        if binding.source == EndpointParameterSource.BODY:
            has_body = True
        elif binding.source == EndpointParameterSource.QUERY:
            if not binding.is_aggregate:
                query_names.append(binding.name)
        elif binding.source == EndpointParameterSource.HEADER:
            header_names.append(binding.name)
        elif binding.source == EndpointParameterSource.FORM:
            form_names.append(binding.name)
    return (
        deduplicate_strings(query_names),
        deduplicate_strings(header_names),
        deduplicate_strings(form_names),
        has_body,
    )


def classify_spring_declarative_client_call_site(
    call_site: JCallSite,
    callee: ResolvedCallee,
    resolve_constant_expression: Callable[[str, str], str | None] | None = None,
) -> HttpClassification | None:
    """Classify a call to a Spring declarative HTTP client interface method.

    Returns ``None`` unless the receiver is a @FeignClient/@HttpExchange
    interface whose called method carries a recognized mapping annotation
    (an MVC @*Mapping for Feign, or the @HttpExchange family for HTTP Interface).
    """
    kind = spring_client_interface_kind(
        callee.class_annotations,
        class_imports=callee.class_imports,
    )
    if kind is None:
        return None

    constant_resolver = (
        (
            lambda expression: resolve_constant_expression(
                callee.declaring_class_name, expression
            )
        )
        if resolve_constant_expression is not None
        else None
    )

    mapping = extract_spring_method_mapping(
        callee.method_annotations,
        class_imports=callee.class_imports,
        kind=kind,
        constant_resolver=constant_resolver,
    )
    if mapping is None:
        return None
    http_method, is_method_wildcard, method_path = mapping

    # Spring inherits a type-level @HttpExchange `method` to methods that do not
    # declare their own verb (a bare method-level @HttpExchange without `method`).
    if kind == "http-interface" and is_method_wildcard:
        type_method_spec = extract_http_exchange_type_method(
            callee.class_annotations,
            class_imports=callee.class_imports,
        )
        if type_method_spec is not None and not type_method_spec.is_method_wildcard:
            http_method = type_method_spec.http_method
            is_method_wildcard = False

    base_path = extract_spring_client_base_path(
        callee.class_annotations,
        class_imports=callee.class_imports,
        kind=kind,
        constant_resolver=constant_resolver,
    )
    path = join_request_paths(base_path, method_path)

    query_param_names, header_names, form_param_names, has_body_payload = (
        _project_request_parameter_evidence(callee, constant_resolver=constant_resolver)
    )

    return HttpClassification(
        http_method="UNKNOWN" if is_method_wildcard else http_method,
        path=path,
        framework=_CLIENT_KIND_FRAMEWORK[kind],
        receiver_type=call_site.receiver_type or "",
        owner_family=_CLIENT_KIND_OWNER_FAMILY[kind],
        request_role=HttpRequestRole.EVENT,
        path_param_names=list(_template_path_variable_names(path)) if path else [],
        query_param_names=query_param_names,
        header_names=header_names,
        form_param_names=form_param_names,
        has_body_payload=has_body_payload,
    )
