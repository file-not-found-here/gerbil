from __future__ import annotations

from cldk.models.java import JImport

from gerbil.analysis.shared.class_utils import ResolvedAnnotation
from gerbil.analysis.shared.annotations import annotation_matches_expected
from gerbil.analysis.shared.constants import (
    SORTED_FRAMEWORK_PREFIXES,
    SORTED_SPRING_DECOMPOSITION_PREFIXES,
    SPRING_DECOMPOSITION_ANNOTATION_HINTS,
)
from gerbil.analysis.schema import HttpDispatchFramework, TestingFramework


def matches_package_prefix(qualified_name: str, prefix: str) -> bool:
    if not qualified_name or not prefix:
        return False

    normalized_prefix = prefix[:-1] if prefix.endswith(".") else prefix
    return qualified_name == normalized_prefix or qualified_name.startswith(
        f"{normalized_prefix}."
    )


def infer_spring_subframeworks(
    class_imports: list[JImport],
    class_annotations: list[ResolvedAnnotation],
    class_annotation_imports_by_class: dict[str, list[JImport]],
) -> set[HttpDispatchFramework]:
    frameworks: set[HttpDispatchFramework] = set()

    for import_entry in class_imports:
        class_import = import_entry.path
        for prefix, framework in SORTED_SPRING_DECOMPOSITION_PREFIXES:
            if matches_package_prefix(class_import, prefix):
                frameworks.add(framework)
                break

    for resolved_annotation in class_annotations:
        annotation_imports = class_annotation_imports_by_class.get(
            resolved_annotation.declaring_class_name,
            [],
        )
        for (
            expected_annotation,
            annotation_framework,
        ) in SPRING_DECOMPOSITION_ANNOTATION_HINTS.items():
            if annotation_matches_expected(
                annotation=resolved_annotation.annotation,
                expected_annotation=expected_annotation,
                class_imports=annotation_imports,
            ):
                frameworks.add(annotation_framework)
                break

    return frameworks


def infer_testing_frameworks(
    class_imports: list[JImport],
    class_annotations: list[ResolvedAnnotation],
    class_annotation_imports_by_class: dict[str, list[JImport]],
) -> list[TestingFramework]:
    frameworks: set[TestingFramework] = set()

    for import_entry in class_imports:
        class_import = import_entry.path
        for prefix, framework in SORTED_FRAMEWORK_PREFIXES:
            # CLDK wildcard imports carry the package path without ".*", so a
            # bare startswith would miss `import org.junit.*;`.
            if matches_package_prefix(class_import, prefix):
                frameworks.add(framework)
                break

    for resolved_annotation in class_annotations:
        annotation_imports = class_annotation_imports_by_class.get(
            resolved_annotation.declaring_class_name,
            [],
        )
        if any(
            annotation_matches_expected(
                annotation=resolved_annotation.annotation,
                expected_annotation=expected_annotation,
                class_imports=annotation_imports,
            )
            for expected_annotation in {
                "@SpringBootTest",
                "@WebMvcTest",
                "@WebFluxTest",
            }
        ):
            frameworks.add(TestingFramework.SPRING_TEST)

    return sorted(frameworks, key=lambda item: item.value)


__all__ = [
    "infer_spring_subframeworks",
    "infer_testing_frameworks",
    "matches_package_prefix",
]
