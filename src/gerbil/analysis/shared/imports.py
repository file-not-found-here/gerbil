from __future__ import annotations

from collections.abc import Sequence

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JImport


def clone_imports(imports: Sequence[JImport]) -> list[JImport]:
    """Return cloned structured imports.

    Args:
        imports: Structured import declarations.

    Returns:
        New ``JImport`` instances preserving the original field values.
    """

    return [
        JImport(
            path=import_entry.path,
            is_static=bool(import_entry.is_static),
            is_wildcard=bool(import_entry.is_wildcard),
        )
        for import_entry in imports
    ]


def deduplicate_imports(imports: Sequence[JImport]) -> list[JImport]:
    """Return ordered, normalized import declarations without duplicates.

    Args:
        imports: Raw import declarations from CLDK.

    Returns:
        Ordered import declarations deduplicated by ``(path, is_static,
        is_wildcard)``. Blank paths are ignored.
    """

    deduplicated: list[JImport] = []
    seen: set[tuple[str, bool, bool]] = set()
    for import_entry in imports:
        normalized_path = import_entry.path.strip()
        if not normalized_path:
            continue

        key = (
            normalized_path,
            bool(import_entry.is_static),
            bool(import_entry.is_wildcard),
        )
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(
            JImport(
                path=normalized_path,
                is_static=key[1],
                is_wildcard=key[2],
            )
        )
    return deduplicated


def get_java_file_import_declarations(
    analysis: JavaAnalysis,
    java_file: str,
) -> list[JImport]:
    """Load normalized structured imports for a Java source file.

    Args:
        analysis: Java static-analysis facade.
        java_file: Source file path recognized by CLDK.

    Returns:
        Ordered, deduplicated import declarations or an empty list when the
        compilation unit is unavailable.
    """

    compilation_unit = analysis.get_java_compilation_unit(java_file)
    if compilation_unit is None:
        return []
    return deduplicate_imports(list(compilation_unit.import_declarations or []))


def get_class_import_declarations(
    analysis: JavaAnalysis,
    qualified_class_name: str,
) -> list[JImport]:
    """Load normalized structured imports for a fully-qualified class name.

    Args:
        analysis: Java static-analysis facade.
        qualified_class_name: Fully-qualified class name.

    Returns:
        Ordered, deduplicated import declarations or an empty list when the
        class file cannot be resolved.
    """

    java_file = analysis.get_java_file(qualified_class_name)
    if not java_file:
        return []
    return get_java_file_import_declarations(analysis, java_file)


def non_static_imports(imports: Sequence[JImport]) -> list[JImport]:
    """Return only non-static import declarations with non-empty paths."""

    return [import_entry for import_entry in imports if not import_entry.is_static]


def matches_import_root(import_entry: JImport, import_root: str) -> bool:
    """Return True when an import belongs to the expected package root."""

    if import_entry.is_static:
        return False

    normalized_path = import_entry.path.strip()
    normalized_root = import_root.rstrip(".")
    if not normalized_path or not normalized_root:
        return False

    return normalized_path == normalized_root or normalized_path.startswith(
        f"{normalized_root}."
    )


def has_import_root_signal(
    imports: Sequence[JImport],
    allowed_import_roots: set[str],
) -> bool:
    """Return True when imports contain an allowed package-root signal."""

    visible_imports = non_static_imports(imports)
    return any(
        matches_import_root(import_entry=import_entry, import_root=import_root)
        for import_entry in visible_imports
        for import_root in allowed_import_roots
    )


def has_conflicting_explicit_import(
    imports: Sequence[JImport],
    short_name: str,
    allowed_roots: set[str],
) -> bool:
    """Return True when an explicit import shadows the expected short name."""

    for import_entry in non_static_imports(imports):
        if import_entry.is_wildcard:
            continue
        normalized_path = import_entry.path.strip()
        if not normalized_path:
            continue
        if normalized_path.rsplit(".", 1)[-1] != short_name:
            continue
        if any(
            matches_import_root(import_entry=import_entry, import_root=import_root)
            for import_root in allowed_roots
        ):
            continue
        return True
    return False


__all__ = [
    "clone_imports",
    "deduplicate_imports",
    "get_class_import_declarations",
    "get_java_file_import_declarations",
    "has_conflicting_explicit_import",
    "has_import_root_signal",
    "matches_import_root",
    "non_static_imports",
]
