from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JCallable, JImport

from gerbil.analysis.shared.annotations import (
    annotation_matches_expected,
    annotation_token as _annotation_token,
)
from gerbil.analysis.shared.constants import (
    SETUP_ANNOTATIONS,
    TEARDOWN_ANNOTATIONS,
    TEST_ANNOTATIONS,
)
from gerbil.analysis.shared.imports import get_class_import_declarations
from gerbil.analysis.schema import TestingFramework

# ---------------------------------------------------------------------------
# class_name_resolution
# ---------------------------------------------------------------------------


def normalize_type_reference(type_reference: str) -> str:
    """Normalize a Java type reference into a comparable class-name token.

    Args:
        type_reference: Raw Java type reference, possibly with generics, array
            markers, or modifiers.

    Returns:
        Normalized type token suitable for class-name resolution. Returns an
        empty string when no usable token is present.
    """

    normalized = (type_reference or "").strip()
    if not normalized:
        return ""

    normalized = re.sub(r"<.*>", "", normalized)
    normalized = normalized.replace("[]", "").strip()
    if " " in normalized:
        normalized = normalized.split()[-1]
    return normalized


def resolve_known_class_name(
    *,
    type_reference: str,
    declaring_class_name: str,
    known_class_names: set[str],
) -> str | None:
    """Resolve a type reference to a known fully qualified class name.

    Resolution attempts, in order:
    1. Exact known-class match.
    2. Same-package short-name match.
    3. Unique suffix match among known classes.

    Args:
        type_reference: Raw class type reference.
        declaring_class_name: Fully qualified class that declares the reference.
        known_class_names: Candidate classes available for resolution.

    Returns:
        Fully qualified class name when deterministically resolved, otherwise
        ``None``.
    """

    normalized_type_reference = normalize_type_reference(type_reference)
    if not normalized_type_reference:
        return None

    if normalized_type_reference in known_class_names:
        return normalized_type_reference

    if "." in normalized_type_reference:
        return None

    package_name = declaring_class_name.rpartition(".")[0]
    if package_name:
        same_package_candidate = f"{package_name}.{normalized_type_reference}"
        if same_package_candidate in known_class_names:
            return same_package_candidate

    suffix_matches = [
        class_name
        for class_name in known_class_names
        if class_name.endswith(f".{normalized_type_reference}")
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]

    return None


# ---------------------------------------------------------------------------
# class_annotation_hierarchy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedAnnotation:
    """Annotation with provenance to its declaring class."""

    annotation: str
    declaring_class_name: str


@dataclass(frozen=True)
class ClassAnnotationResolutionConfig:
    """Controls hierarchy traversal and inherited-annotation filtering.

    Attributes:
        include_superclasses: Traverse superclass edges from each visited class.
        include_interfaces: Traverse implemented/extended interface edges.
        require_inherited_annotations_from_parents: When true, include parent
            annotations only if the supplied inherited-annotation filter accepts
            the annotation for that parent type.
    """

    include_superclasses: bool = True
    include_interfaces: bool = True
    require_inherited_annotations_from_parents: bool = False


_NESTED_ANNOTATION_ROOTS: dict[str, set[str]] = {
    "@Nested": {"org.junit.jupiter.api"},
}


def _class_has_nested_annotation(
    analysis: JavaAnalysis,
    qualified_class_name: str,
) -> bool:
    class_details = analysis.get_class(qualified_class_name)
    if class_details is None:
        return False
    class_imports = get_class_import_declarations(analysis, qualified_class_name)
    return any(
        annotation_matches_expected(
            annotation=annotation,
            expected_annotation="@Nested",
            class_imports=class_imports,
            import_roots_by_annotation=_NESTED_ANNOTATION_ROOTS,
        )
        for annotation in class_details.annotations or []
    )


def get_nested_enclosing_chain(
    analysis: JavaAnalysis,
    qualified_class_name: str,
) -> list[str]:
    """Return the enclosing-class chain for a JUnit 5 @Nested class.

    Each link is included only when the immediately nested class carries
    ``@Nested`` from ``org.junit.jupiter.api``. Plain static inner helper
    classes therefore contribute no enclosing context.
    """

    class_details = analysis.get_class(qualified_class_name)
    if class_details is None:
        return []
    if not class_details.is_nested_type or not class_details.parent_type:
        return []
    if not _class_has_nested_annotation(analysis, qualified_class_name):
        return []

    enclosing_chain: list[str] = []
    current_class = class_details.parent_type
    while current_class:
        if current_class in enclosing_chain:
            break
        enclosing_chain.append(current_class)
        parent_details = analysis.get_class(current_class)
        if parent_details is None:
            break
        if not parent_details.is_nested_type or not parent_details.parent_type:
            break
        if not _class_has_nested_annotation(analysis, current_class):
            break
        current_class = parent_details.parent_type

    return enclosing_chain


def resolve_effective_class_annotations(
    *,
    analysis: JavaAnalysis,
    qualified_class_name: str,
    known_class_names: set[str] | None = None,
    config: ClassAnnotationResolutionConfig | None = None,
    inherited_annotation_filter: Callable[[str, str], bool] | None = None,
) -> list[ResolvedAnnotation]:
    """Resolve class annotations across a configurable class hierarchy.

    Annotation entries are deduplicated by annotation name and preserve nearest
    declaration precedence in traversal order. Traversal is depth-first with
    interfaces searched before the superclass at each level, matching Spring's
    find semantics (AnnotationsScanner.processClassHierarchy).

    Args:
        analysis: Java static-analysis facade.
        qualified_class_name: Fully qualified class name to inspect.
        known_class_names: Optional known class names used to resolve short type
            references. When omitted, all classes from the analysis are used.
        config: Optional resolver configuration.
        inherited_annotation_filter: Predicate used only when
            `config.require_inherited_annotations_from_parents` is true. Receives
            `(annotation_name, declaring_class_name)` and returns whether the
            parent annotation should be included.

    Returns:
        Ordered, deduplicated class annotations with declaring-class provenance.

    Raises:
        ValueError: If inherited-only mode is enabled without a filter callback.
    """

    resolved_config = config or ClassAnnotationResolutionConfig()
    if (
        resolved_config.require_inherited_annotations_from_parents
        and inherited_annotation_filter is None
    ):
        raise ValueError(
            "inherited_annotation_filter is required when "
            "require_inherited_annotations_from_parents is enabled"
        )

    resolved_known_class_names: set[str] = set(analysis.get_classes().keys())
    if known_class_names:
        resolved_known_class_names.update(known_class_names)
    resolved_known_class_names.add(qualified_class_name)

    visited: set[str] = {qualified_class_name}
    hierarchy_order: list[str] = [qualified_class_name]
    enclosing_chain_classes: set[str] = set()

    def _visit(class_name: str) -> None:
        class_details = analysis.get_class(class_name)
        if class_details is None:
            return

        parent_type_references: list[str] = []
        if resolved_config.include_interfaces:
            parent_type_references.extend(class_details.implements_list or [])
        if resolved_config.include_superclasses:
            parent_type_references.extend(class_details.extends_list or [])

        for parent_type_reference in parent_type_references:
            resolved_parent_class = resolve_known_class_name(
                type_reference=parent_type_reference,
                declaring_class_name=class_name,
                known_class_names=resolved_known_class_names,
            )
            if not resolved_parent_class or resolved_parent_class in visited:
                continue
            visited.add(resolved_parent_class)
            hierarchy_order.append(resolved_parent_class)
            _visit(resolved_parent_class)

    _visit(qualified_class_name)

    # Spring's @NestedTestConfiguration default (INHERIT) propagates the
    # enclosing class's full test-context configuration, so Java @Inherited is
    # irrelevant for the enclosing classes themselves. Their supertypes are
    # still real inheritance and keep the inherited-annotation filter.
    for enclosing_class in get_nested_enclosing_chain(analysis, qualified_class_name):
        if enclosing_class in visited:
            continue
        visited.add(enclosing_class)
        hierarchy_order.append(enclosing_class)
        enclosing_chain_classes.add(enclosing_class)
        _visit(enclosing_class)

    effective_annotations: list[ResolvedAnnotation] = []
    seen_annotation_names: set[str] = set()

    for class_index, class_name in enumerate(hierarchy_order):
        class_details = analysis.get_class(class_name)
        if class_details is None:
            continue
        for annotation in class_details.annotations or []:
            annotation_name = _annotation_token(annotation)
            if not annotation_name or annotation_name in seen_annotation_names:
                continue

            if (
                class_index > 0
                and resolved_config.require_inherited_annotations_from_parents
                and inherited_annotation_filter is not None
                and class_name not in enclosing_chain_classes
                and not inherited_annotation_filter(annotation_name, class_name)
            ):
                continue

            seen_annotation_names.add(annotation_name)
            effective_annotations.append(
                ResolvedAnnotation(
                    annotation=annotation,
                    declaring_class_name=class_name,
                )
            )

    return effective_annotations


# ---------------------------------------------------------------------------
# class_categorization
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def _matches_test_dir(java_file: str, test_dir: str) -> bool:
    normalized_path = _normalize_path(java_file)
    normalized_test_dir = _normalize_path(test_dir).strip("/")
    if not normalized_test_dir:
        return False

    return (
        normalized_path == normalized_test_dir
        or normalized_path.startswith(f"{normalized_test_dir}/")
        or f"/{normalized_test_dir}/" in normalized_path
        or normalized_path.endswith(f"/{normalized_test_dir}")
    )


def is_in_test_directory(java_file: str, test_dirs: Sequence[str]) -> bool:
    return any(_matches_test_dir(java_file, test_dir) for test_dir in test_dirs)


def is_test_method(
    *,
    method_signature: str,
    method_details: JCallable | None,
    class_extends_list: list[str],
    class_annotations: list[ResolvedAnnotation],
    testing_frameworks: list[TestingFramework],
    method_imports: list[JImport],
    class_annotation_imports_by_class: dict[str, list[JImport]],
) -> bool:
    if method_details is None:
        return False

    is_public: bool = "public" in (method_details.modifiers or [])
    method_annotation_literals: list[str] = list(method_details.annotations or [])
    has_test_annotation: bool = any(
        annotation_matches_expected(
            annotation=annotation,
            expected_annotation=expected_annotation,
            class_imports=method_imports,
        )
        for annotation in method_annotation_literals
        for expected_annotation in TEST_ANNOTATIONS
    )

    is_junit3_style: bool = (
        TestingFramework.JUNIT3 in testing_frameworks
        and any(
            extended_class.endswith("TestCase") for extended_class in class_extends_list
        )
        and method_signature.startswith("test")
        and is_public
        and method_details.return_type == "void"
        and len(method_details.parameters or []) == 0
    )

    has_setup_or_teardown_annotation = any(
        annotation_matches_expected(
            annotation=annotation,
            expected_annotation=expected_annotation,
            class_imports=method_imports,
        )
        for annotation in method_annotation_literals
        for expected_annotation in (SETUP_ANNOTATIONS | TEARDOWN_ANNOTATIONS)
    )

    is_testng_class_level: bool = (
        TestingFramework.TESTNG in testing_frameworks
        and any(
            annotation_matches_expected(
                annotation=resolved_annotation.annotation,
                expected_annotation="@Test",
                class_imports=class_annotation_imports_by_class.get(
                    resolved_annotation.declaring_class_name, []
                ),
            )
            for resolved_annotation in class_annotations
        )
        and is_public
        and not has_setup_or_teardown_annotation
    )

    return has_test_annotation or is_junit3_style or is_testng_class_level


def categorize_classes(
    *,
    analysis: JavaAnalysis,
    qualified_class_names: list[str],
    test_dirs: Sequence[str],
    get_testing_frameworks_for_class: Callable[[str], list[TestingFramework]],
    is_test_method_for_class: Callable[[str, str, list[TestingFramework]], bool],
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    test_classes_methods: dict[str, list[str]] = {}
    application_classes: list[str] = []
    test_utility_classes: list[str] = []

    for qualified_class_name in qualified_class_names:
        java_file: str | None = analysis.get_java_file(qualified_class_name)
        if not java_file:
            continue

        in_test_dir = is_in_test_directory(java_file, test_dirs)
        frameworks = get_testing_frameworks_for_class(qualified_class_name)

        methods_in_class: list[str] = list(
            analysis.get_methods_in_class(qualified_class_name).keys()
        )
        test_methods: list[str] = [
            method_signature
            for method_signature in methods_in_class
            if is_test_method_for_class(
                method_signature, qualified_class_name, frameworks
            )
        ]

        if test_methods:
            test_classes_methods[qualified_class_name] = test_methods
            continue

        if in_test_dir:
            test_utility_classes.append(qualified_class_name)
        else:
            application_classes.append(qualified_class_name)

    return test_classes_methods, application_classes, test_utility_classes


__all__ = [
    "ClassAnnotationResolutionConfig",
    "ResolvedAnnotation",
    "categorize_classes",
    "is_in_test_directory",
    "is_test_method",
    "normalize_type_reference",
    "resolve_effective_class_annotations",
    "resolve_known_class_name",
]
