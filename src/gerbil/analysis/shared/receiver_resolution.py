from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JCallable, JImport
from cldk.models.java.models import (
    JCallableParameter,
    JCallSite,
    JField,
    JVariableDeclaration,
)

from gerbil.analysis.runtime.call_sites import MethodRef
from gerbil.analysis.shared.class_utils import normalize_type_reference
from gerbil.analysis.shared.constant_resolution import ConstantResolver
from gerbil.analysis.shared.imports import get_class_import_declarations
from gerbil.analysis.shared.static_imports import StaticImportIndex

_IDENTIFIER_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_QUALIFIED_IDENTIFIER_RE: re.Pattern[str] = re.compile(
    r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+$"
)
_THIS_OR_SUPER_MEMBER_RE: re.Pattern[str] = re.compile(
    r"^(this|super)\.([A-Za-z_$][A-Za-z0-9_$]*)$"
)


@dataclass(frozen=True)
class ResolvedReceiver:
    receiver_type: str
    source: str


@dataclass(frozen=True)
class ResolvedCallee:
    """Declaring-type + method annotations for a call site's resolved callee.

    Enables annotation-driven classification (e.g. Spring declarative HTTP client
    interfaces), where the dispatch semantics live on the callee's annotations
    rather than at the call site itself.
    """

    declaring_class_name: str
    class_annotations: list[str]
    method_annotations: list[str]
    method_parameters: list[JCallableParameter]
    class_imports: list[JImport]


def _int_value(value: object, default: int = -1) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _position_at_or_before(
    *,
    candidate_line: int,
    candidate_col: int,
    target_line: int,
    target_col: int,
) -> bool:
    if target_line < 0:
        return True
    if candidate_line < 0:
        return True
    if candidate_line < target_line:
        return True
    if candidate_line > target_line:
        return False
    if target_col < 0 or candidate_col < 0:
        return True
    return candidate_col <= target_col


def _parameter_name(parameter: object) -> str:
    if isinstance(parameter, Mapping):
        return str(parameter.get("name") or "").strip()
    return str(getattr(parameter, "name", "") or "").strip()


def _parameter_type(parameter: object) -> str:
    if isinstance(parameter, Mapping):
        return str(parameter.get("type") or "").strip()
    return str(getattr(parameter, "type", "") or "").strip()


def _class_imports(
    *,
    class_name: str,
    analysis: JavaAnalysis,
    get_class_imports_for_class: Callable[[str], list[JImport]] | None,
) -> list[JImport]:
    if get_class_imports_for_class is not None:
        return list(get_class_imports_for_class(class_name))
    return get_class_import_declarations(analysis, class_name)


def _superclass_chain(
    *,
    owner_class_name: str,
    analysis: JavaAnalysis,
    get_superclass_chain_for_class: Callable[[str], list[str]] | None,
) -> list[str]:
    if get_superclass_chain_for_class is not None:
        return list(get_superclass_chain_for_class(owner_class_name))

    owner_details = analysis.get_class(owner_class_name)
    if owner_details is None:
        return []

    super_queue: deque[str] = deque(owner_details.extends_list or [])
    seen: set[str] = set(owner_details.extends_list or [])
    superclasses: list[str] = []
    while super_queue:
        superclass = super_queue.popleft()
        superclasses.append(superclass)
        superclass_details = analysis.get_class(superclass)
        if superclass_details is None:
            continue
        for next_super in superclass_details.extends_list or []:
            if next_super in seen:
                continue
            seen.add(next_super)
            super_queue.append(next_super)
    return superclasses


def _resolve_type_reference(
    *,
    type_reference: str,
    declaring_class_name: str,
    analysis: JavaAnalysis,
    get_class_imports_for_class: Callable[[str], list[JImport]] | None,
) -> str:
    normalized_type = normalize_type_reference(type_reference)
    if not normalized_type:
        return ""
    if "." in normalized_type:
        return normalized_type

    imports = _class_imports(
        class_name=declaring_class_name,
        analysis=analysis,
        get_class_imports_for_class=get_class_imports_for_class,
    )

    explicit_import_matches = {
        import_entry.path.strip()
        for import_entry in imports
        if (
            not import_entry.is_static
            and not import_entry.is_wildcard
            and import_entry.path.strip().endswith(f".{normalized_type}")
        )
    }
    if len(explicit_import_matches) == 1:
        return next(iter(explicit_import_matches))
    if len(explicit_import_matches) > 1:
        return ""

    package_name = declaring_class_name.rpartition(".")[0]
    if package_name:
        same_package_candidate = f"{package_name}.{normalized_type}"
        if analysis.get_class(same_package_candidate) is not None:
            return same_package_candidate

    wildcard_matches: set[str] = set()
    for import_entry in imports:
        if import_entry.is_static or not import_entry.is_wildcard:
            continue
        candidate = f"{import_entry.path.strip()}.{normalized_type}".strip(".")
        if analysis.get_class(candidate) is None:
            continue
        wildcard_matches.add(candidate)
    if len(wildcard_matches) == 1:
        return next(iter(wildcard_matches))
    if len(wildcard_matches) > 1:
        return ""

    java_lang_candidate = f"java.lang.{normalized_type}"
    if analysis.get_class(java_lang_candidate) is not None:
        return java_lang_candidate
    return ""


def _resolve_local_symbol(
    *,
    symbol_name: str,
    call_site: JCallSite,
    owner_method_details: JCallable,
    owner_class_name: str,
    analysis: JavaAnalysis,
    get_class_imports_for_class: Callable[[str], list[JImport]] | None,
) -> tuple[str | None, bool]:
    call_line = _int_value(call_site.start_line)
    call_col = _int_value(call_site.start_column)
    candidate_declarations: list[JVariableDeclaration] = []
    for declaration in owner_method_details.variable_declarations or []:
        if declaration.name != symbol_name:
            continue
        declaration_line = _int_value(declaration.start_line)
        declaration_col = _int_value(declaration.start_column)
        if not _position_at_or_before(
            candidate_line=declaration_line,
            candidate_col=declaration_col,
            target_line=call_line,
            target_col=call_col,
        ):
            continue
        candidate_declarations.append(declaration)

    if not candidate_declarations:
        return None, False

    candidate_declarations.sort(
        key=lambda declaration: (
            _int_value(declaration.start_line),
            _int_value(declaration.start_column),
        )
    )
    nearest_declaration = candidate_declarations[-1]
    resolved_type = _resolve_type_reference(
        type_reference=nearest_declaration.type,
        declaring_class_name=owner_class_name,
        analysis=analysis,
        get_class_imports_for_class=get_class_imports_for_class,
    )
    return resolved_type or "", True


def _resolve_parameter_symbol(
    *,
    symbol_name: str,
    owner_method_details: JCallable,
    owner_class_name: str,
    analysis: JavaAnalysis,
    get_class_imports_for_class: Callable[[str], list[JImport]] | None,
) -> tuple[str | None, bool]:
    for parameter in owner_method_details.parameters or []:
        if _parameter_name(parameter) != symbol_name:
            continue
        resolved_type = _resolve_type_reference(
            type_reference=_parameter_type(parameter),
            declaring_class_name=owner_class_name,
            analysis=analysis,
            get_class_imports_for_class=get_class_imports_for_class,
        )
        return resolved_type or "", True
    return None, False


def _field_type_for_symbol(
    *,
    class_name: str,
    symbol_name: str,
    analysis: JavaAnalysis,
) -> str | None:
    class_details = analysis.get_class(class_name)
    if class_details is None:
        return None
    for field in class_details.field_declarations or []:
        if _field_declares_symbol(field, symbol_name):
            return field.type
    return None


def _field_declares_symbol(field: JField, symbol_name: str) -> bool:
    return any(variable_name == symbol_name for variable_name in field.variables)


def _resolve_field_symbol(
    *,
    symbol_name: str,
    owner_class_name: str,
    analysis: JavaAnalysis,
    include_owner_class: bool,
    get_class_imports_for_class: Callable[[str], list[JImport]] | None,
    get_superclass_chain_for_class: Callable[[str], list[str]] | None,
) -> tuple[str | None, str | None, bool]:
    candidate_classes: list[str] = []
    if include_owner_class:
        candidate_classes.append(owner_class_name)
    candidate_classes.extend(
        _superclass_chain(
            owner_class_name=owner_class_name,
            analysis=analysis,
            get_superclass_chain_for_class=get_superclass_chain_for_class,
        )
    )

    for candidate_class in candidate_classes:
        field_type_reference = _field_type_for_symbol(
            class_name=candidate_class,
            symbol_name=symbol_name,
            analysis=analysis,
        )
        if field_type_reference is None:
            continue
        resolved_type = _resolve_type_reference(
            type_reference=field_type_reference,
            declaring_class_name=candidate_class,
            analysis=analysis,
            get_class_imports_for_class=get_class_imports_for_class,
        )
        return resolved_type or "", candidate_class, True
    return None, None, False


def _looks_like_class_literal_identifier(identifier: str) -> bool:
    return bool(identifier) and identifier[0].isupper()


def _looks_like_class_literal_reference(reference: str) -> bool:
    segments = reference.split(".")
    return bool(segments) and _looks_like_class_literal_identifier(segments[-1])


def _explicit_import_match_for_qualified_reference(
    *,
    qualified_reference: str,
    owner_class_name: str,
    analysis: JavaAnalysis,
    get_class_imports_for_class: Callable[[str], list[JImport]] | None,
) -> str:
    imports = _class_imports(
        class_name=owner_class_name,
        analysis=analysis,
        get_class_imports_for_class=get_class_imports_for_class,
    )
    explicit_import_matches = {
        import_entry.path.strip()
        for import_entry in imports
        if (
            not import_entry.is_static
            and not import_entry.is_wildcard
            and import_entry.path.strip().endswith(f".{qualified_reference}")
        )
    }
    if len(explicit_import_matches) == 1:
        return next(iter(explicit_import_matches))
    return ""


def _resolve_class_literal_reference(
    *,
    reference: str,
    owner_class_name: str,
    analysis: JavaAnalysis,
    get_class_imports_for_class: Callable[[str], list[JImport]] | None,
) -> ResolvedReceiver | None:
    if not _looks_like_class_literal_reference(reference):
        return None

    explicit_match = _explicit_import_match_for_qualified_reference(
        qualified_reference=reference,
        owner_class_name=owner_class_name,
        analysis=analysis,
        get_class_imports_for_class=get_class_imports_for_class,
    )
    if explicit_match:
        return ResolvedReceiver(
            receiver_type=explicit_match,
            source="class_literal_receiver",
        )

    if "." in reference:
        first_segment, nested_suffix = reference.split(".", 1)
        resolved_first_segment = _resolve_type_reference(
            type_reference=first_segment,
            declaring_class_name=owner_class_name,
            analysis=analysis,
            get_class_imports_for_class=get_class_imports_for_class,
        )
        if resolved_first_segment:
            return ResolvedReceiver(
                receiver_type=f"{resolved_first_segment}.{nested_suffix}",
                source="class_literal_receiver",
            )
        if _looks_like_class_literal_identifier(first_segment):
            return None
        return ResolvedReceiver(
            receiver_type=reference,
            source="class_literal_receiver",
        )

    resolved_type = _resolve_type_reference(
        type_reference=reference,
        declaring_class_name=owner_class_name,
        analysis=analysis,
        get_class_imports_for_class=get_class_imports_for_class,
    )
    if not resolved_type:
        return None

    return ResolvedReceiver(
        receiver_type=resolved_type,
        source="class_literal_receiver",
    )


def _resolve_from_identifier(
    *,
    symbol_name: str,
    call_site: JCallSite,
    owner_class_name: str,
    owner_method_details: JCallable,
    analysis: JavaAnalysis,
    get_class_imports_for_class: Callable[[str], list[JImport]] | None,
    get_superclass_chain_for_class: Callable[[str], list[str]] | None,
) -> tuple[ResolvedReceiver | None, bool]:
    local_type, local_found = _resolve_local_symbol(
        symbol_name=symbol_name,
        call_site=call_site,
        owner_method_details=owner_method_details,
        owner_class_name=owner_class_name,
        analysis=analysis,
        get_class_imports_for_class=get_class_imports_for_class,
    )
    if local_found:
        if local_type:
            return (
                ResolvedReceiver(
                    receiver_type=local_type,
                    source="local_symbol",
                ),
                True,
            )
        return None, True

    parameter_type, parameter_found = _resolve_parameter_symbol(
        symbol_name=symbol_name,
        owner_method_details=owner_method_details,
        owner_class_name=owner_class_name,
        analysis=analysis,
        get_class_imports_for_class=get_class_imports_for_class,
    )
    if parameter_found:
        if parameter_type:
            return (
                ResolvedReceiver(
                    receiver_type=parameter_type,
                    source="parameter_symbol",
                ),
                True,
            )
        return None, True

    field_type, declaring_class_name, field_found = _resolve_field_symbol(
        symbol_name=symbol_name,
        owner_class_name=owner_class_name,
        analysis=analysis,
        include_owner_class=True,
        get_class_imports_for_class=get_class_imports_for_class,
        get_superclass_chain_for_class=get_superclass_chain_for_class,
    )
    if not field_found or not field_type or declaring_class_name is None:
        return None, field_found

    source = (
        "field_symbol"
        if declaring_class_name == owner_class_name
        else "inherited_field_symbol"
    )

    return (
        ResolvedReceiver(
            receiver_type=field_type,
            source=source,
        ),
        True,
    )


def _resolve_receiver_expr(
    *,
    receiver_expr: str,
    call_site: JCallSite,
    owner_class_name: str,
    owner_method_details: JCallable,
    analysis: JavaAnalysis,
    get_class_imports_for_class: Callable[[str], list[JImport]] | None,
    get_superclass_chain_for_class: Callable[[str], list[str]] | None,
) -> ResolvedReceiver | None:
    normalized_receiver_expr = receiver_expr.strip()
    if not normalized_receiver_expr:
        return None

    if normalized_receiver_expr == "this":
        return ResolvedReceiver(
            receiver_type=owner_class_name,
            source="this_receiver",
        )

    if normalized_receiver_expr == "super":
        superclass_chain = _superclass_chain(
            owner_class_name=owner_class_name,
            analysis=analysis,
            get_superclass_chain_for_class=get_superclass_chain_for_class,
        )
        if not superclass_chain:
            return None
        nearest_superclass = superclass_chain[0]
        return ResolvedReceiver(
            receiver_type=nearest_superclass,
            source="super_receiver",
        )

    if _IDENTIFIER_RE.match(normalized_receiver_expr):
        resolved_symbol, symbol_found = _resolve_from_identifier(
            symbol_name=normalized_receiver_expr,
            call_site=call_site,
            owner_class_name=owner_class_name,
            owner_method_details=owner_method_details,
            analysis=analysis,
            get_class_imports_for_class=get_class_imports_for_class,
            get_superclass_chain_for_class=get_superclass_chain_for_class,
        )
        if resolved_symbol is not None or symbol_found:
            return resolved_symbol
        return _resolve_class_literal_reference(
            reference=normalized_receiver_expr,
            owner_class_name=owner_class_name,
            analysis=analysis,
            get_class_imports_for_class=get_class_imports_for_class,
        )

    member_match = _THIS_OR_SUPER_MEMBER_RE.match(normalized_receiver_expr)
    if member_match is not None:
        receiver_base = member_match.group(1)
        symbol_name = member_match.group(2)
        include_owner = receiver_base == "this"
        field_type, declaring_class_name, field_found = _resolve_field_symbol(
            symbol_name=symbol_name,
            owner_class_name=owner_class_name,
            analysis=analysis,
            include_owner_class=include_owner,
            get_class_imports_for_class=get_class_imports_for_class,
            get_superclass_chain_for_class=get_superclass_chain_for_class,
        )
        if not field_found or not field_type or declaring_class_name is None:
            return None

        source = (
            "field_symbol"
            if declaring_class_name == owner_class_name
            else "inherited_field_symbol"
        )

        return ResolvedReceiver(
            receiver_type=field_type,
            source=source,
        )

    if _QUALIFIED_IDENTIFIER_RE.match(normalized_receiver_expr):
        if normalized_receiver_expr.startswith(("this.", "super.")):
            return None
        if not _looks_like_class_literal_reference(normalized_receiver_expr):
            return None
        first_segment = normalized_receiver_expr.split(".", 1)[0]
        _, symbol_found = _resolve_from_identifier(
            symbol_name=first_segment,
            call_site=call_site,
            owner_class_name=owner_class_name,
            owner_method_details=owner_method_details,
            analysis=analysis,
            get_class_imports_for_class=get_class_imports_for_class,
            get_superclass_chain_for_class=get_superclass_chain_for_class,
        )
        if symbol_found:
            return None
        return _resolve_class_literal_reference(
            reference=normalized_receiver_expr,
            owner_class_name=owner_class_name,
            analysis=analysis,
            get_class_imports_for_class=get_class_imports_for_class,
        )

    return None


def resolve_receiver(
    *,
    call_site: JCallSite,
    static_import_index: StaticImportIndex,
    owner_class_name: str,
    owner_method_details: JCallable | None,
    analysis: JavaAnalysis,
    get_class_imports_for_class: Callable[[str], list[JImport]] | None = None,
    get_superclass_chain_for_class: Callable[[str], list[str]] | None = None,
) -> ResolvedReceiver:
    receiver_type = (call_site.receiver_type or "").strip()
    if receiver_type:
        return ResolvedReceiver(
            receiver_type=receiver_type,
            source="explicit_receiver_type",
        )

    receiver_expr = (call_site.receiver_expr or "").strip()
    if receiver_expr:
        if owner_method_details is not None:
            resolved_from_expr = _resolve_receiver_expr(
                receiver_expr=receiver_expr,
                call_site=call_site,
                owner_class_name=owner_class_name,
                owner_method_details=owner_method_details,
                analysis=analysis,
                get_class_imports_for_class=get_class_imports_for_class,
                get_superclass_chain_for_class=get_superclass_chain_for_class,
            )
            if resolved_from_expr is not None:
                return resolved_from_expr

        return ResolvedReceiver(
            receiver_type="",
            source="unresolved_receiver_expr",
        )

    method_name = (call_site.method_name or "").strip()
    if method_name:
        static_receiver = static_import_index.resolve(method_name)
        if static_receiver:
            return ResolvedReceiver(
                receiver_type=static_receiver,
                source="static_import_method",
            )

    return ResolvedReceiver(
        receiver_type="",
        source="unresolved_receiver",
    )


class RuntimeReceiverResolver:
    """Resolve call-site receivers with full runtime context and shared caches."""

    def __init__(
        self,
        *,
        analysis: JavaAnalysis,
        load_method_details: Callable[[MethodRef], JCallable | None],
        get_static_import_index_for_class: Callable[[str], StaticImportIndex],
        get_class_imports_for_class: Callable[[str], list[JImport]],
        get_superclass_chain_for_class: Callable[[str], list[str]],
        constant_resolver: ConstantResolver,
    ) -> None:
        self.analysis = analysis
        self._load_method_details = load_method_details
        self._get_static_import_index_for_class = get_static_import_index_for_class
        self._get_class_imports_for_class = get_class_imports_for_class
        self._get_superclass_chain_for_class = get_superclass_chain_for_class
        self._constant_resolver = constant_resolver
        self._owner_method_details_by_owner: dict[MethodRef, JCallable | None] = {}
        self._static_import_index_by_class_name: dict[str, StaticImportIndex] = {}
        self._helper_return_receiver_by_method: dict[MethodRef, ResolvedReceiver] = {}
        self._callee_class_info_by_name: dict[
            str, tuple[list[str], list[JImport]] | None
        ] = {}

    def _owner_method_details(self, owner: MethodRef) -> JCallable | None:
        if owner not in self._owner_method_details_by_owner:
            self._owner_method_details_by_owner[owner] = self._load_method_details(
                owner
            )
        return self._owner_method_details_by_owner[owner]

    def _static_import_index(self, class_name: str) -> StaticImportIndex:
        if class_name not in self._static_import_index_by_class_name:
            self._static_import_index_by_class_name[class_name] = (
                self._get_static_import_index_for_class(class_name)
            )
        return self._static_import_index_by_class_name[class_name]

    def method_details_for_owner(self, owner: MethodRef) -> JCallable | None:
        return self._owner_method_details(owner)

    def superclass_chain(self, class_name: str) -> list[str]:
        return list(self._get_superclass_chain_for_class(class_name))

    def methods_in_class(self, class_name: str) -> dict[str, JCallable]:
        return dict(self.analysis.get_methods_in_class(class_name) or {})

    def class_imports(self, class_name: str) -> list[JImport]:
        return list(self._get_class_imports_for_class(class_name))

    def resolve_for_event(
        self, owner: MethodRef, call_site: JCallSite
    ) -> ResolvedReceiver:
        owner_method_details = self._owner_method_details(owner)
        return resolve_receiver(
            call_site=call_site,
            static_import_index=self._static_import_index(owner.defining_class_name),
            owner_class_name=owner.defining_class_name,
            owner_method_details=owner_method_details,
            analysis=self.analysis,
            get_class_imports_for_class=self._get_class_imports_for_class,
            get_superclass_chain_for_class=self._get_superclass_chain_for_class,
        )

    def resolve_callee(
        self,
        call_site: JCallSite,
        *,
        resolved_receiver_type: str | None = None,
    ) -> ResolvedCallee | None:
        """Resolve the call site's callee declaring-type + method annotations.

        Returns ``None`` when the receiver type is unresolved or the declaring
        class is not in the analyzed set (e.g. a client interface defined only in
        a dependency JAR), so callers degrade gracefully to unclassified.

        ``resolved_receiver_type`` lets a caller supply the receiver type its
        own :meth:`resolve_for_event` recovered. CLDK commonly leaves
        ``call_site.receiver_type`` empty for field/local receivers (an injected
        ``@Autowired`` Feign/@HttpExchange client is the typical case), so the
        raw type alone would miss those declarative-client calls entirely.
        """
        receiver_type = (resolved_receiver_type or "").strip() or (
            call_site.receiver_type or ""
        ).strip()
        if not receiver_type:
            return None

        class_info = self._callee_class_info(receiver_type)
        if class_info is None:
            return None
        class_annotations, class_imports = class_info

        methods = self.analysis.get_methods_in_class(receiver_type) or {}
        callee_method = methods.get(call_site.callee_signature or "")
        method_annotations = (
            list(callee_method.annotations or []) if callee_method is not None else []
        )
        method_parameters = (
            list(callee_method.parameters or []) if callee_method is not None else []
        )
        return ResolvedCallee(
            declaring_class_name=receiver_type,
            class_annotations=class_annotations,
            method_annotations=method_annotations,
            method_parameters=method_parameters,
            class_imports=class_imports,
        )

    def _callee_class_info(
        self, class_name: str
    ) -> tuple[list[str], list[JImport]] | None:
        if class_name not in self._callee_class_info_by_name:
            class_details = self.analysis.get_class(class_name)
            if class_details is None:
                self._callee_class_info_by_name[class_name] = None
            else:
                self._callee_class_info_by_name[class_name] = (
                    list(class_details.annotations or []),
                    list(self._get_class_imports_for_class(class_name)),
                )
        return self._callee_class_info_by_name[class_name]

    def resolve_helper_return_receiver(self, helper: MethodRef) -> ResolvedReceiver:
        if helper in self._helper_return_receiver_by_method:
            return self._helper_return_receiver_by_method[helper]

        helper_method_details = self._load_method_details(helper)
        if helper_method_details is None:
            resolved_receiver = ResolvedReceiver(
                receiver_type="",
                source="unresolved_helper_return_type",
            )
        else:
            receiver_type = _resolve_type_reference(
                type_reference=helper_method_details.return_type or "",
                declaring_class_name=helper.defining_class_name,
                analysis=self.analysis,
                get_class_imports_for_class=self._get_class_imports_for_class,
            )
            resolved_receiver = ResolvedReceiver(
                receiver_type=receiver_type,
                source=(
                    "helper_return_type"
                    if receiver_type
                    else "unresolved_helper_return_type"
                ),
            )

        self._helper_return_receiver_by_method[helper] = resolved_receiver
        return resolved_receiver

    def resolve_constant_expression(
        self,
        owner_class_name: str,
        expression: str,
        local_values: Mapping[str, str | None] | None = None,
    ) -> str | None:
        return self._constant_resolver.resolve_expression(
            owner_class_name, expression, local_values=local_values
        )


__all__ = ["ResolvedReceiver", "RuntimeReceiverResolver", "resolve_receiver"]
