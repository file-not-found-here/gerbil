"""Scope, merge and repair the OpenAPI schema EvoMaster searches a service through."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

_HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)
_INLINE_TYPE_KEYS = ("type", "format", "items", "collectionFormat")
_SWAGGER2_SECTIONS = ("definitions", "parameters", "responses")


@dataclass(frozen=True)
class ScopedSchema:
    schema: dict[str, Any]
    total_operations: int
    kept_operations: int


def parse_served_schema(text: str) -> dict[str, Any]:
    """Read a served document; Data Flow's message converters base64-encode it."""
    document = json.loads(text)
    if isinstance(document, str):
        try:
            document = json.loads(base64.b64decode(document, validate=True))
        except binascii.Error as error:
            raise ValueError(f"served schema is a plain string: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
        raise ValueError(f"served schema has no paths: {str(document)[:200]}")
    return document


def scope_schema(schema: dict[str, Any], excluded: Sequence[str]) -> ScopedSchema:
    """Drop the paths fully matching an `excluded` pattern, counting operations.

    Ant-style wildcard paths such as `/files/**`, which springdoc and springfox copy from
    request mappings, are dropped too: they are no OpenAPI path templates, so a call can
    only send the wildcard itself, and EvoMaster 6.2.0 crashes reading them as regexes.
    """
    patterns = [re.compile(pattern) for pattern in excluded]
    paths = schema.get("paths", {})
    kept = {
        path: item
        for path, item in paths.items()
        if "*" not in path and not any(pattern.fullmatch(path) for pattern in patterns)
    }
    return ScopedSchema(
        schema={**schema, "paths": kept},
        total_operations=_count_operations(paths),
        kept_operations=_count_operations(kept),
    )


@dataclass(frozen=True)
class MergedSchema:
    schema: dict[str, Any]
    shadowed_operations: int


def merge_schemas(schemas: Sequence[dict[str, Any]]) -> MergedSchema:
    """Union documents of one service, earlier documents taking precedence.

    A later operation on a method and path an earlier document holds is shadowed, since
    one document cannot hold operations that differ only in media type. A later definition
    that disagrees with an earlier one of its name is renamed, with its references.
    """
    merged: dict[str, Any] = {
        key: value
        for key, value in schemas[0].items()
        if key not in ("paths", "components", *_SWAGGER2_SECTIONS)
    }
    merged["paths"] = {}
    kept: dict[str, dict[str, Any]] = {}
    shadowed = 0
    for schema in schemas:
        schema = _without_conflicting_names(kept, schema)
        for prefix, entries in _sections(schema).items():
            section = kept.setdefault(prefix, {})
            for name, definition in entries.items():
                section.setdefault(name, definition)
        for path, item in schema.get("paths", {}).items():
            merged_item = merged["paths"].setdefault(path, {})
            for key, value in item.items():
                if merged_item.setdefault(key, value) == value:
                    continue
                if key.lower() not in _HTTP_METHODS:
                    raise ValueError(f"conflicting definitions of {key} {path}")
                shadowed += 1
    return MergedSchema(_with_sections(merged, kept), shadowed)


def _sections(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each section of named definitions, keyed by the prefix its references use."""
    sections = {
        f"#/components/{name}/": entries
        for name, entries in schema.get("components", {}).items()
    }
    for name in _SWAGGER2_SECTIONS:
        if name in schema:
            sections[f"#/{name}/"] = schema[name]
    return sections


def _with_sections(
    schema: dict[str, Any], sections: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result = dict(schema)
    for prefix, entries in sections.items():
        if prefix.startswith("#/components/"):
            name = prefix.removeprefix("#/components/").removesuffix("/")
            result["components"] = {**result.get("components", {}), name: entries}
        else:
            result[prefix.removeprefix("#/").removesuffix("/")] = entries
    return result


def _without_conflicting_names(
    kept: dict[str, dict[str, Any]], schema: dict[str, Any]
) -> dict[str, Any]:
    # A renamed definition changes those referring to it, which may then conflict too.
    while True:
        renames: dict[str, str] = {}
        for prefix, entries in _sections(schema).items():
            existing = kept.get(prefix, {})
            taken = set(existing) | set(entries)
            for name, definition in entries.items():
                if name in existing and existing[name] != definition:
                    free = _free_name(name, taken)
                    taken.add(free)
                    renames[prefix + name] = prefix + free
        if not renames:
            return schema
        schema = _renamed(schema, renames)


def _free_name(name: str, taken: set[str]) -> str:
    suffix = 2
    while f"{name}_{suffix}" in taken:
        suffix += 1
    return f"{name}_{suffix}"


def _renamed(schema: dict[str, Any], renames: dict[str, str]) -> dict[str, Any]:
    """Apply `renames`, between full references, to references and definitions alike."""
    referenced = _with_references(schema, renames)
    sections = {
        prefix: {
            renames.get(prefix + name, prefix + name).removeprefix(prefix): definition
            for name, definition in entries.items()
        }
        for prefix, entries in _sections(referenced).items()
    }
    return _with_sections(referenced, sections)


def _with_references(value: Any, renames: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_with_references(entry, renames) for entry in value]
    if not isinstance(value, dict):
        return value
    return {
        key: (
            renames.get(entry, entry)
            if key == "$ref" and isinstance(entry, str)
            else _with_references(entry, renames)
        )
        for key, entry in value.items()
    }


def declare_path_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    """Declare the path variables an operation leaves out, as springdoc sometimes does.

    Each is typed as other operations first type its name, or else as a string.
    """
    dialect = _Dialect(inline="swagger" in schema)
    declared = _declared_path_variables(schema.get("paths", {}), dialect)
    return {
        **schema,
        "paths": {
            path: _with_complete_path_parameters(path, item, declared, dialect)
            for path, item in schema.get("paths", {}).items()
        },
    }


@dataclass(frozen=True)
class _Dialect:
    """Swagger 2 types a parameter on the parameter itself, OpenAPI 3 in its schema."""

    inline: bool

    def schema_of(self, parameter: dict[str, Any]) -> dict[str, Any]:
        if not self.inline:
            schema: dict[str, Any] = parameter.get("schema", {})
            return schema
        return {key: parameter[key] for key in _INLINE_TYPE_KEYS if key in parameter}

    def typed(
        self, parameter: dict[str, Any], schema: dict[str, Any]
    ) -> dict[str, Any]:
        if not self.inline:
            return {**parameter, "schema": schema}
        untyped = {
            key: value
            for key, value in parameter.items()
            if key not in _INLINE_TYPE_KEYS
        }
        return {**untyped, **schema}


def _declared_path_variables(
    paths: dict[str, Any], dialect: _Dialect
) -> dict[str, dict[str, Any]]:
    declared: dict[str, dict[str, Any]] = {}
    for item in paths.values():
        for parameter in _path_parameters(item):
            declared.setdefault(parameter["name"], dialect.schema_of(parameter))
    return declared


def _with_complete_path_parameters(
    path: str,
    item: dict[str, Any],
    declared: dict[str, dict[str, Any]],
    dialect: _Dialect,
) -> dict[str, Any]:
    variables = re.findall(r"\{([^{}]+)\}", path)
    shared = {parameter.get("name") for parameter in item.get("parameters", [])}
    rewritten = {}
    for method, operation in item.items():
        if method.lower() not in _HTTP_METHODS:
            rewritten[method] = operation
            continue
        parameters = operation.get("parameters", [])
        named = {p["name"] for p in parameters if p.get("in") == "path"} | shared
        missing = [
            dialect.typed(
                {"name": name, "in": "path", "required": True},
                declared.get(name) or {"type": "string"},
            )
            for name in variables
            if name not in named
        ]
        if parameters or missing:
            operation = {**operation, "parameters": parameters + missing}
        rewritten[method] = operation
    return rewritten


def _path_parameters(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        parameter
        for method, operation in item.items()
        if method.lower() in _HTTP_METHODS
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "path"
    ]


def _count_operations(paths: dict[str, Any]) -> int:
    return sum(
        1 for item in paths.values() for key in item if key.lower() in _HTTP_METHODS
    )
