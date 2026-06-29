"""Resolves Java String constants from CLDK field initializers for path/URL reconstruction."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JImport
from cldk.models.java.models import JField, JType

# Excluded by design: static imports, ternary/method-call/text-block initializers,
# non-(static final | interface) fields, non-String types, and constants defined only
# in dependency JARs (unresolvable classes). Any token in a concatenation that fails to
# resolve makes the whole expression unresolvable.

_SIMPLE_ESCAPES: dict[str, str] = {
    "b": "\b",
    "t": "\t",
    "n": "\n",
    "f": "\f",
    "r": "\r",
    '"': '"',
    "'": "'",
    "\\": "\\",
}


def parse_java_string_literal(token: str) -> str | None:
    stripped = token.strip()
    if stripped.startswith('"""'):
        return None
    if len(stripped) < 2 or stripped[0] != '"' or stripped[-1] != '"':
        return None

    body = stripped[1:-1]
    result: list[str] = []
    index = 0
    length = len(body)
    while index < length:
        character = body[index]
        if character == '"':
            # An unescaped double-quote means this is not a single literal.
            return None
        if character != "\\":
            result.append(character)
            index += 1
            continue

        index += 1
        if index >= length:
            return None
        escape = body[index]
        if escape in _SIMPLE_ESCAPES:
            result.append(_SIMPLE_ESCAPES[escape])
            index += 1
            continue
        if escape == "u":
            hex_digits = body[index + 1 : index + 5]
            if len(hex_digits) != 4 or any(
                digit not in "0123456789abcdefABCDEF" for digit in hex_digits
            ):
                return None
            result.append(chr(int(hex_digits, 16)))
            index += 5
            continue
        return None

    return "".join(result)


def split_top_level_concat(expression: str) -> list[str] | None:
    tokens: list[str] = []
    current: list[str] = []
    in_double_quotes = False
    in_single_quotes = False
    is_escaping = False
    nesting_depth = 0

    for character in expression:
        if is_escaping:
            current.append(character)
            is_escaping = False
            continue

        if character == "\\" and (in_double_quotes or in_single_quotes):
            current.append(character)
            is_escaping = True
            continue

        if character == '"' and not in_single_quotes:
            current.append(character)
            in_double_quotes = not in_double_quotes
            continue

        if character == "'" and not in_double_quotes:
            current.append(character)
            in_single_quotes = not in_single_quotes
            continue

        if in_double_quotes or in_single_quotes:
            current.append(character)
            continue

        if character in "([{":
            nesting_depth += 1
            current.append(character)
            continue

        if character in ")]}":
            if nesting_depth > 0:
                nesting_depth -= 1
            current.append(character)
            continue

        if character == "+" and nesting_depth == 0:
            tokens.append("".join(current))
            current = []
            continue

        current.append(character)

    tokens.append("".join(current))

    stripped_tokens = [token.strip() for token in tokens]
    if any(not token for token in stripped_tokens):
        return None
    return stripped_tokens


class ConstantResolver:
    """Resolves Java String constants from project field initializers across class hierarchies."""

    def __init__(
        self,
        *,
        analysis: JavaAnalysis,
        get_class_imports_for_class: Callable[[str], list[JImport]],
        get_class_resolution_order: Callable[[str, bool], list[str]],
    ) -> None:
        self.analysis = analysis
        self._get_class_imports_for_class = get_class_imports_for_class
        self._get_class_resolution_order = get_class_resolution_order
        self._initializer_tables_by_class: dict[str, dict[str, str]] = {}
        self._resolved_values: dict[tuple[str, str], str | None] = {}
        self._in_progress: set[tuple[str, str]] = set()

    def resolve_identifier(
        self, declaring_class_name: str, identifier: str
    ) -> str | None:
        qualifier, separator, name = identifier.rpartition(".")
        if not separator:
            return self._resolve_unqualified(declaring_class_name, name)

        target_class = self._resolve_qualifier_class(declaring_class_name, qualifier)
        if target_class is None:
            return None
        return self._resolve_unqualified(target_class, name)

    def resolve_expression(
        self,
        declaring_class_name: str,
        expression: str,
        local_values: Mapping[str, str | None] | None = None,
    ) -> str | None:
        tokens = split_top_level_concat(expression)
        if tokens is None:
            return None

        resolved_parts: list[str] = []
        for token in tokens:
            literal = parse_java_string_literal(token)
            if literal is not None:
                resolved_parts.append(literal)
                continue
            # A bound local (helper parameter) shadows any same-named field, per
            # JLS scoping; only bare identifiers can refer to locals. A None
            # binding marks a local whose value is statically unknown — the
            # shadowing still applies, so resolution fails rather than falling
            # through to the shadowed field.
            if local_values is not None and token in local_values:
                bound_value = local_values[token]
                if bound_value is None:
                    return None
                resolved_parts.append(bound_value)
                continue
            resolved_identifier = self.resolve_identifier(declaring_class_name, token)
            if resolved_identifier is None:
                return None
            resolved_parts.append(resolved_identifier)

        return "".join(resolved_parts)

    def _resolve_unqualified(self, qualified_class_name: str, name: str) -> str | None:
        superclass_order = self._get_class_resolution_order(qualified_class_name, False)
        for class_name in superclass_order:
            if name in self._initializer_table(class_name):
                return self._resolve_table_value(class_name, name)

        full_order = self._get_class_resolution_order(qualified_class_name, True)
        superclass_set = set(superclass_order)
        interface_values: set[str] = set()
        for class_name in full_order:
            if class_name in superclass_set:
                continue
            if name not in self._initializer_table(class_name):
                continue
            value = self._resolve_table_value(class_name, name)
            if value is None:
                return None
            interface_values.add(value)

        if len(interface_values) == 1:
            return next(iter(interface_values))
        return None

    def _resolve_qualifier_class(
        self,
        declaring_class_name: str,
        qualifier: str,
        *,
        allow_wildcard: bool = True,
    ) -> str | None:
        if self.analysis.get_class(qualifier) is not None:
            return qualifier

        imports = self._get_class_imports_for_class(declaring_class_name)

        suffix = f".{qualifier}"
        for import_entry in imports:
            if import_entry.is_static or import_entry.is_wildcard:
                continue
            if import_entry.path.endswith(suffix):
                if self.analysis.get_class(import_entry.path) is not None:
                    return import_entry.path
                return None

        package_name = declaring_class_name.rpartition(".")[0]
        if package_name:
            candidate = f"{package_name}.{qualifier}"
            if self.analysis.get_class(candidate) is not None:
                return candidate

        if "." in qualifier:
            first_segment, remainder = qualifier.split(".", 1)
            resolved_first_segment = self._resolve_qualifier_class(
                declaring_class_name, first_segment, allow_wildcard=False
            )
            if resolved_first_segment is not None:
                candidate = f"{resolved_first_segment}.{remainder}"
                if self.analysis.get_class(candidate) is not None:
                    return candidate

        if not allow_wildcard:
            return None

        wildcard_matches: set[str] = set()
        for import_entry in imports:
            if import_entry.is_static or not import_entry.is_wildcard:
                continue
            candidate = f"{import_entry.path.strip()}.{qualifier}".strip(".")
            if self.analysis.get_class(candidate) is not None:
                wildcard_matches.add(candidate)
        if len(wildcard_matches) == 1:
            return next(iter(wildcard_matches))
        if len(wildcard_matches) > 1:
            return None

        return None

    def _initializer_table(self, qualified_class_name: str) -> dict[str, str]:
        cached = self._initializer_tables_by_class.get(qualified_class_name)
        if cached is not None:
            return cached

        class_details: JType | None = self.analysis.get_class(qualified_class_name)
        table: dict[str, str] = {}
        if class_details is not None:
            is_interface = bool(class_details.is_interface)
            for field in class_details.field_declarations or []:
                if not self._is_eligible_field(field, is_interface):
                    continue
                initializers = field.variable_initializers or {}
                for variable_name in field.variables or []:
                    if variable_name in initializers:
                        table[variable_name] = initializers[variable_name]

        self._initializer_tables_by_class[qualified_class_name] = table
        return table

    def _resolve_table_value(
        self, declaring_class_name: str, variable_name: str
    ) -> str | None:
        key = (declaring_class_name, variable_name)
        if key in self._resolved_values:
            return self._resolved_values[key]
        if key in self._in_progress:
            # Reference cycle: this value is mid-resolution, so it cannot resolve.
            return None

        self._in_progress.add(key)
        try:
            initializer = self._initializer_table(declaring_class_name)[variable_name]
            resolved = self.resolve_expression(declaring_class_name, initializer)
        finally:
            self._in_progress.discard(key)
        self._resolved_values[key] = resolved
        return resolved

    @staticmethod
    def _is_eligible_field(field: JField, is_interface: bool) -> bool:
        if field.type != "java.lang.String":
            return False
        if is_interface:
            return True
        modifiers = field.modifiers or []
        return "static" in modifiers and "final" in modifiers


__all__ = [
    "ConstantResolver",
    "parse_java_string_literal",
    "split_top_level_concat",
]
