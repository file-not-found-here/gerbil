from __future__ import annotations

from cldk.models.java import JCallable, JImport, JType
from cldk.models.java.models import (
    JCallSite,
    JCallableParameter,
    JField,
    JVariableDeclaration,
)

from gerbil.analysis.shared import CommonAnalysis
from gerbil.analysis.runtime.call_sites import CallSiteNode
from gerbil.analysis.shared.class_utils import ResolvedAnnotation
from gerbil.analysis.shared.receiver_resolution import RuntimeReceiverResolver
from gerbil.analysis.schema import (
    AssertionClassification,
    AssertionRole,
    AssertionNodeKind,
    HttpClassification,
    HttpDispatchFramework,
    HttpRequestRole,
    HttpResponseRole,
)
from gerbil.analysis.http.framework_registry import (
    HttpOwnerFamilyRule,
    classify_owner_family,
)
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.fake_java_analysis import FakeJavaAnalysis


def classify_http_roles(
    rule: HttpOwnerFamilyRule,
    *,
    receiver_type: str,
    method_name: str,
    is_constructor_call: bool = False,
) -> tuple[HttpRequestRole | None, HttpResponseRole | None]:
    request_role, response_role, _ = classify_owner_family(
        rule,
        receiver_type=receiver_type,
        method_name=method_name,
        is_constructor_call=is_constructor_call,
    )
    return request_role, response_role


def infer_owner_family_http_method(
    rule: HttpOwnerFamilyRule,
    *,
    receiver_type: str,
    method_name: str,
    is_constructor_call: bool = False,
) -> str:
    request_role, _, http_method = classify_owner_family(
        rule,
        receiver_type=receiver_type,
        method_name=method_name,
        is_constructor_call=is_constructor_call,
    )
    if request_role is None:
        return "UNKNOWN"
    return http_method


def make_import_declaration(
    path: str,
    *,
    is_static: bool = False,
    is_wildcard: bool = False,
) -> JImport:
    """Build a structured import declaration for tests.

    Args:
        path: Fully-qualified import path without trailing ``.*``.
        is_static: Whether the import is static.
        is_wildcard: Whether the import is wildcarded.

    Returns:
        A normalized ``JImport`` instance.
    """

    return JImport(
        path=path.strip(),
        is_static=is_static,
        is_wildcard=is_wildcard,
    )


def make_import_declarations(*paths: str) -> list[JImport]:
    """Build non-static import declarations from fully-qualified paths.

    Args:
        *paths: Import paths. A trailing ``.*`` is converted to
            ``is_wildcard=True``.

    Returns:
        Structured import declarations suitable for test fixtures.
    """

    import_declarations: list[JImport] = []
    for path in paths:
        normalized_path = path.strip()
        if not normalized_path:
            continue
        is_wildcard = normalized_path.endswith(".*")
        if is_wildcard:
            normalized_path = normalized_path.removesuffix(".*").strip()
        import_declarations.append(
            make_import_declaration(
                normalized_path,
                is_static=False,
                is_wildcard=is_wildcard,
            )
        )
    return import_declarations


def make_import_declarations_by_file(
    imports_by_file: dict[str, list[str]],
) -> dict[str, list[JImport]]:
    """Build structured import declarations keyed by Java file path.

    Args:
        imports_by_file: Mapping from Java file path to flat import paths.

    Returns:
        Structured import declarations for each Java file.
    """

    return {
        java_file: make_import_declarations(*import_paths)
        for java_file, import_paths in imports_by_file.items()
    }


def make_import_lookup(
    imports_by_class: dict[str, list[str]],
) -> dict[str, list[JImport]]:
    """Build structured imports keyed by declaring class name."""

    return {
        class_name: make_import_declarations(*import_paths)
        for class_name, import_paths in imports_by_class.items()
    }


def make_call_site(
    method_name: str,
    receiver_type: str = "",
    argument_expr: list[str] | None = None,
    callee_signature: str = "",
    start_line: int = 1,
    start_column: int = 1,
    end_line: int | None = None,
    end_column: int = 1,
    is_static_call: bool = False,
    is_private: bool = False,
    is_public: bool = True,
    is_protected: bool = False,
    is_unspecified: bool = False,
    is_constructor_call: bool = False,
    receiver_expr: str = "",
    argument_types: list[str] | None = None,
    return_type: str = "",
) -> JCallSite:
    """Build a minimal JCallSite for unit tests.

    Args:
        method_name: Name of the invoked method.
        receiver_type: Fully qualified receiver type, if present.
        argument_expr: Argument expressions passed at the call site.
        callee_signature: Resolved callee signature.
        start_line: One-based call-site start line.
        start_column: One-based call-site start column.
        end_line: One-based call-site end line.
        end_column: One-based call-site end column.
        is_static_call: Whether this call is static dispatch.
        is_private: Whether the callee is private.
        is_public: Whether the callee is public.
        is_protected: Whether the callee is protected.
        is_unspecified: Whether callee visibility is unspecified.
        is_constructor_call: Whether this call represents constructor invocation.
        receiver_expr: Receiver expression text.
        argument_types: Resolved argument type names.
        return_type: Resolved return type name.

    Returns:
        A JCallSite instance populated with defaults suitable for tests.
    """

    resolved_end_line: int = end_line if end_line is not None else start_line
    return JCallSite(
        comment=None,
        method_name=method_name,
        receiver_expr=receiver_expr,
        receiver_type=receiver_type,
        argument_types=list(argument_types or []),
        argument_expr=list(argument_expr or []),
        return_type=return_type,
        callee_signature=callee_signature,
        is_static_call=is_static_call,
        is_private=is_private,
        is_public=is_public,
        is_protected=is_protected,
        is_unspecified=is_unspecified,
        is_constructor_call=is_constructor_call,
        crud_operation=None,
        crud_query=None,
        start_line=start_line,
        start_column=start_column,
        end_line=resolved_end_line,
        end_column=end_column,
    )


def make_variable_declaration(
    name: str = "value",
    type_name: str = "java.lang.Object",
    initializer: str = "",
    start_line: int = 1,
    start_column: int = 1,
    end_line: int | None = None,
    end_column: int = 1,
) -> JVariableDeclaration:
    """Build a minimal JVariableDeclaration for unit tests.

    Args:
        name: Local variable name.
        type_name: Resolved variable type.
        initializer: Initializer expression.
        start_line: One-based declaration start line.
        start_column: One-based declaration start column.
        end_line: One-based declaration end line.
        end_column: One-based declaration end column.

    Returns:
        A JVariableDeclaration instance for test fixtures.
    """

    resolved_end_line: int = end_line if end_line is not None else start_line
    return JVariableDeclaration(
        comment=None,
        name=name,
        type=type_name,
        initializer=initializer,
        start_line=start_line,
        start_column=start_column,
        end_line=resolved_end_line,
        end_column=end_column,
    )


def make_callable_parameter(
    name: str = "param",
    type_name: str = "java.lang.String",
    annotations: list[str] | None = None,
    modifiers: list[str] | None = None,
    start_line: int = 1,
    end_line: int | None = None,
    start_column: int = 1,
    end_column: int = 1,
) -> JCallableParameter:
    resolved_end_line: int = end_line if end_line is not None else start_line
    return JCallableParameter(
        name=name,
        type=type_name,
        annotations=list(annotations or []),
        modifiers=list(modifiers or []),
        start_line=start_line,
        end_line=resolved_end_line,
        start_column=start_column,
        end_column=end_column,
    )


def make_callable(
    signature: str = "testMethod()",
    annotations: list[str] | None = None,
    call_sites: list[JCallSite] | None = None,
    variable_declarations: list[JVariableDeclaration] | None = None,
    parameters: list[JCallableParameter] | None = None,
    declaration: str = "void testMethod()",
    code: str = "{}",
    cyclomatic_complexity: int | None = 0,
    modifiers: list[str] | None = None,
    return_type: str | None = "void",
    thrown_exceptions: list[str] | None = None,
    is_implicit: bool = False,
    is_constructor: bool = False,
    is_entrypoint: bool = False,
) -> JCallable:
    """Build a minimal JCallable for unit tests.

    Args:
        signature: Method signature identifier.
        annotations: Method annotations.
        call_sites: Call-site graph edges belonging to this method.
        variable_declarations: Local variable declarations.
        declaration: Method declaration text.
        code: Method body text.
        cyclomatic_complexity: Cyclomatic complexity value.
        modifiers: Method modifiers.
        return_type: Resolved return type.
        thrown_exceptions: Declared thrown exception types.
        is_implicit: Whether the callable is implicit/synthetic.
        is_constructor: Whether the callable is a constructor.
        is_entrypoint: Whether the callable is marked as an entrypoint.

    Returns:
        A JCallable instance populated with defaults suitable for tests.
    """

    resolved_call_sites: list[JCallSite] = list(call_sites or [])
    resolved_variables: list[JVariableDeclaration] = list(variable_declarations or [])
    resolved_parameters: list[JCallableParameter] = list(parameters or [])
    return JCallable(
        signature=signature,
        is_implicit=is_implicit,
        is_constructor=is_constructor,
        comments=[],
        annotations=list(annotations or []),
        modifiers=list(modifiers or []),
        thrown_exceptions=list(thrown_exceptions or []),
        declaration=declaration,
        parameters=resolved_parameters,
        return_type=return_type,
        code=code,
        start_line=1,
        end_line=1,
        code_start_line=1,
        referenced_types=[],
        accessed_fields=[],
        call_sites=resolved_call_sites,
        is_entrypoint=is_entrypoint,
        variable_declarations=resolved_variables,
        crud_operations=None,
        crud_queries=None,
        cyclomatic_complexity=cyclomatic_complexity,
    )


def make_field(
    annotations: list[str] | None = None,
    type_name: str = "java.lang.Object",
    variables: list[str] | None = None,
    modifiers: list[str] | None = None,
    start_line: int = 1,
    end_line: int | None = None,
    variable_initializers: dict[str, str] | None = None,
) -> JField:
    """Build a minimal JField for unit tests.

    Args:
        annotations: Field annotations.
        type_name: Field type.
        variables: Declared variable names for the field.
        modifiers: Field modifiers.
        start_line: One-based field start line.
        end_line: One-based field end line.
        variable_initializers: Raw initializer expression text keyed per variable.

    Returns:
        A JField instance for test fixtures.
    """

    resolved_end_line: int = end_line if end_line is not None else start_line
    return JField(
        comment=None,
        type=type_name,
        start_line=start_line,
        end_line=resolved_end_line,
        variables=list(variables or ["field"]),
        modifiers=list(modifiers or []),
        annotations=list(annotations or []),
        variable_initializers=variable_initializers,
    )


def make_type(
    parent_type: str = "",
    annotations: list[str] | None = None,
    extends_list: list[str] | None = None,
    implements_list: list[str] | None = None,
    field_declarations: list[JField] | None = None,
    callable_declarations: dict[str, JCallable] | None = None,
    is_interface: bool = False,
) -> JType:
    """Build a minimal JType for unit tests.

    Args:
        parent_type: Fully qualified declaring type, if nested.
        annotations: Type annotations.
        extends_list: Direct superclass or interface extensions.
        implements_list: Implemented interfaces.
        field_declarations: Declared fields on the type.
        callable_declarations: Constructors and methods keyed by signature.
        is_interface: Whether the type is an interface declaration.

    Returns:
        A JType instance with the requested hierarchy and members.
    """

    return JType(
        parent_type=parent_type,
        is_nested_type=bool(parent_type),
        annotations=list(annotations or []),
        extends_list=list(extends_list or []),
        implements_list=list(implements_list or []),
        field_declarations=list(field_declarations or []),
        callable_declarations=dict(callable_declarations or {}),
        is_interface=is_interface,
    )


def make_resolved_annotation(
    annotation: str,
    declaring_class_name: str = "example.TestClass",
) -> ResolvedAnnotation:
    """Build a resolved class annotation for unit tests.

    Args:
        annotation: Raw annotation literal.
        declaring_class_name: Class where the annotation is declared.

    Returns:
        A ResolvedAnnotation instance.
    """

    return ResolvedAnnotation(
        annotation=annotation,
        declaring_class_name=declaring_class_name,
    )


def annotate_node_http(
    node: CallSiteNode,
    *,
    http_method: str,
    path: str,
    framework: str | HttpDispatchFramework = HttpDispatchFramework.MOCKMVC,
    receiver_type: str = "",
    request_role: HttpRequestRole | None = HttpRequestRole.EVENT,
    owner_family: str | None = None,
    response_role: HttpResponseRole | None = None,
    headers: list[str] | None = None,
    header_names: list[str] | None = None,
    query_param_names: list[str] | None = None,
    path_param_names: list[str] | None = None,
    form_param_names: list[str] | None = None,
    has_body_payload: bool = False,
    auth_hints: list[str] | None = None,
) -> None:
    """Annotate a CallSiteNode with an HttpClassification for testing."""

    if request_role is None and response_role is None:
        request_role = HttpRequestRole.EVENT

    node.http_classification = HttpClassification(
        http_method=http_method,
        path=path,
        framework=HttpDispatchFramework(framework),
        receiver_type=receiver_type,
        owner_family=owner_family,
        request_role=request_role,
        response_role=response_role,
        headers=headers or [],
        header_names=header_names or [],
        query_param_names=query_param_names or [],
        path_param_names=path_param_names or [],
        form_param_names=form_param_names or [],
        has_body_payload=has_body_payload,
        auth_hints=auth_hints or [],
    )


def annotate_node_assertion(
    node: CallSiteNode,
    *,
    role: AssertionRole,
    status_code: int | None = None,
    node_kind: AssertionNodeKind = AssertionNodeKind.DIRECT,
) -> None:
    """Annotate a CallSiteNode with an AssertionClassification for testing."""
    node.assertion_classification = AssertionClassification(
        role=role,
        status_code=status_code,
        node_kind=node_kind,
    )


def classify_runtime_view_for_testing(runtime_view) -> None:
    """Run the HTTP and assertion classification mutation passes using entry method_details."""
    from gerbil.analysis.http.classification import (
        classify_http_on_runtime_view,
    )
    from gerbil.analysis.assertion import classify_assertions_on_runtime_view

    receiver_resolver = build_runtime_receiver_resolver_for_testing(runtime_view)
    classify_http_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=receiver_resolver,
    )
    classify_assertions_on_runtime_view(
        runtime_view=runtime_view,
        receiver_resolver=receiver_resolver,
    )


def build_runtime_receiver_resolver_for_testing(
    runtime_view,
    *,
    analysis=None,
    get_static_import_index_for_class=(lambda _class_name: StaticImportIndex.EMPTY),
) -> RuntimeReceiverResolver:
    resolved_analysis = analysis or FakeJavaAnalysis()
    common_analysis = CommonAnalysis(resolved_analysis)
    method_details_by_owner = {
        entry.method_ref: entry.method_details for entry in runtime_view.entries
    }

    return RuntimeReceiverResolver(
        analysis=resolved_analysis,
        load_method_details=(
            lambda owner: method_details_by_owner.get(owner)
            or resolved_analysis.get_method(
                owner.defining_class_name,
                owner.method_signature,
            )
        ),
        get_static_import_index_for_class=get_static_import_index_for_class,
        get_class_imports_for_class=common_analysis.get_class_imports,
        get_superclass_chain_for_class=common_analysis.get_superclass_chain,
        constant_resolver=common_analysis.get_constant_resolver(),
    )
