from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from cldk.models.java import JCallable, JImport, JType


@dataclass
class FakeCompilationUnit:
    import_declarations: list[JImport] = field(default_factory=list)


def _coerce_import_declarations(
    import_declarations: Sequence[JImport | str],
) -> list[JImport]:
    normalized_imports: list[JImport] = []
    for import_entry in import_declarations:
        if isinstance(import_entry, JImport):
            normalized_imports.append(import_entry)
            continue

        normalized_path = import_entry.strip()
        if not normalized_path:
            continue
        is_wildcard = normalized_path.endswith(".*")
        if is_wildcard:
            normalized_path = normalized_path.removesuffix(".*").strip()
        normalized_imports.append(
            JImport(
                path=normalized_path,
                is_static=False,
                is_wildcard=is_wildcard,
            )
        )
    return normalized_imports


class FakeJavaAnalysis:
    def __init__(
        self,
        classes: dict[str, JType] | None = None,
        methods_by_class: dict[str, dict[str, JCallable]] | None = None,
        java_files: dict[str, str] | None = None,
        import_declarations_by_file: (
            Mapping[str, Sequence[JImport | str]] | None
        ) = None,
        extended_classes: dict[str, list[str]] | None = None,
    ) -> None:
        self._classes: dict[str, JType] = classes or {}
        self._methods_by_class: dict[str, dict[str, JCallable]] = methods_by_class or {}
        self._java_files: dict[str, str] = java_files or {}
        self._import_declarations_by_file: dict[str, list[JImport]] = {
            java_file: _coerce_import_declarations(import_declarations)
            for java_file, import_declarations in (
                import_declarations_by_file or {}
            ).items()
        }
        self._extended_classes: dict[str, list[str]] = extended_classes or {}

    def get_class(self, qualified_class_name: str) -> JType | None:
        return self._classes.get(qualified_class_name)

    def get_classes(self) -> dict[str, JType]:
        return dict(self._classes)

    def get_methods_in_class(self, qualified_class_name: str) -> dict[str, JCallable]:
        return dict(self._methods_by_class.get(qualified_class_name, {}))

    def get_method(
        self, qualified_class_name: str, method_signature: str
    ) -> JCallable | None:
        return self._methods_by_class.get(qualified_class_name, {}).get(
            method_signature
        )

    def get_extended_classes(self, qualified_class_name: str) -> list[str]:
        if qualified_class_name in self._extended_classes:
            return list(self._extended_classes[qualified_class_name])

        class_details = self._classes.get(qualified_class_name)
        if not class_details:
            return []
        return list(class_details.extends_list or [])

    def get_java_file(self, qualified_class_name: str) -> str | None:
        return self._java_files.get(qualified_class_name)

    def get_java_compilation_unit(self, java_file: str) -> FakeCompilationUnit | None:
        import_declarations = self._import_declarations_by_file.get(java_file)
        if import_declarations is None:
            return None
        return FakeCompilationUnit(import_declarations=list(import_declarations))
