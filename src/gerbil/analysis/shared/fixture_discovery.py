from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JImport

from gerbil.analysis.shared.class_utils import ResolvedAnnotation
from gerbil.analysis.shared.annotations import (
    annotation_body,
    annotation_matches_expected,
)
from gerbil.analysis.shared.imports import get_class_import_declarations
from gerbil.analysis.runtime.fixtures import FixtureMethod


@dataclass(frozen=True)
class GroupParsingResult:
    groups: set[str]
    has_group_filter: bool
    ambiguous: bool
    always_run: bool = False


GROUP_ATTRIBUTE_RE: re.Pattern[str] = re.compile(
    r"(?P<key>groups|onlyForGroups|value)\s*=\s*(?P<value>\{[^}]*\}|\"[^\"]*\"|[^,)]+)"
)
GROUP_LITERAL_RE: re.Pattern[str] = re.compile(r'"([^"]+)"')
ALWAYS_RUN_RE: re.Pattern[str] = re.compile(r"\balwaysRun\s*=\s*true\b")
SETUP_CLASS_SCOPE_ANNOTATIONS: set[str] = {"@BeforeAll", "@BeforeClass"}
TEARDOWN_CLASS_SCOPE_ANNOTATIONS: set[str] = {"@AfterAll", "@AfterClass"}
GROUP_SCOPED_ANNOTATIONS: set[str] = {"@BeforeGroups", "@AfterGroups"}


def find_fixture_methods(
    *,
    analysis: JavaAnalysis,
    reachable_methods: dict[str, list[str]],
    fixture_annotations: set[str],
) -> list[FixtureMethod]:
    fixture_methods: list[FixtureMethod] = []
    class_imports_by_name: dict[str, list[JImport]] = {}
    shadowed_methods = _get_shadowed_supertype_methods(analysis, reachable_methods)

    for class_name, method_signatures in reachable_methods.items():
        class_imports = class_imports_by_name.get(class_name)
        if class_imports is None:
            class_imports = _get_class_imports(analysis, class_name)
            class_imports_by_name[class_name] = class_imports

        for method_signature in method_signatures:
            method = analysis.get_method(class_name, method_signature)
            if not method:
                continue

            if (class_name, method_signature) in shadowed_methods:
                continue

            if any(
                annotation_matches_expected(
                    annotation=annotation,
                    expected_annotation=expected_annotation,
                    class_imports=class_imports,
                )
                for annotation in (method.annotations or [])
                for expected_annotation in fixture_annotations
            ):
                fixture_methods.append(
                    FixtureMethod(
                        defining_class_name=class_name,
                        method_signature=method_signature,
                    )
                )

    return fixture_methods


def _get_supertype_closure(analysis: JavaAnalysis, class_name: str) -> set[str]:
    closure: set[str] = set()
    class_details = analysis.get_class(class_name)
    if class_details is None:
        return closure

    queue: deque[str] = deque(class_details.extends_list or [])
    queue.extend(class_details.implements_list or [])
    while queue:
        supertype = queue.popleft()
        if supertype in closure:
            continue
        closure.add(supertype)
        supertype_details = analysis.get_class(supertype)
        if supertype_details is None:
            continue
        queue.extend(supertype_details.extends_list or [])
        queue.extend(supertype_details.implements_list or [])

    return closure


def _get_shadowed_supertype_methods(
    analysis: JavaAnalysis,
    reachable_methods: dict[str, list[str]],
) -> set[tuple[str, str]]:
    # Java override semantics: a declaration hides the same signature in
    # supertypes only. JUnit 5 @Nested enclosing classes are not supertypes,
    # so their same-signature fixtures stay visible and run alongside.
    shadowed: set[tuple[str, str]] = set()
    for class_name, method_signatures in reachable_methods.items():
        declared_signatures = set(method_signatures)
        if not declared_signatures:
            continue
        for supertype in _get_supertype_closure(analysis, class_name):
            supertype_signatures = reachable_methods.get(supertype)
            if not supertype_signatures:
                continue
            for method_signature in supertype_signatures:
                if method_signature in declared_signatures:
                    shadowed.add((supertype, method_signature))
    return shadowed


def get_effective_fixture_methods(
    *,
    analysis: JavaAnalysis,
    qualified_class_name: str,
    test_method_signature: str,
    class_annotations: list[ResolvedAnnotation],
    fixture_methods: list[FixtureMethod],
    fixture_annotations: set[str],
    class_scope_annotations: set[str],
) -> list[FixtureMethod]:
    if not fixture_methods:
        return []

    test_method_details = analysis.get_method(
        qualified_class_name, test_method_signature
    )
    test_class_imports = _get_class_imports(analysis, qualified_class_name)
    test_method_annotations: list[str] = (
        list(test_method_details.annotations or []) if test_method_details else []
    )
    class_annotation_imports_by_class = {
        class_name: _get_class_imports(analysis, class_name)
        for class_name in {
            annotation.declaring_class_name for annotation in class_annotations
        }
    }
    test_group_context = get_test_group_context(
        class_annotations=class_annotations,
        method_annotations=test_method_annotations,
        class_annotation_imports_by_class=class_annotation_imports_by_class,
        class_imports=test_class_imports,
    )

    effective_methods: list[FixtureMethod] = []
    for fixture in fixture_methods:
        class_name: str = fixture.defining_class_name
        method_signature: str = fixture.method_signature
        fixture_method = analysis.get_method(class_name, method_signature)
        fixture_class_imports = _get_class_imports(analysis, class_name)
        if fixture_method is None:
            effective_methods.append(
                FixtureMethod(
                    defining_class_name=class_name,
                    method_signature=method_signature,
                    is_ambiguous=True,
                )
            )
            continue

        fixture_annotation_prefixes = set()
        for annotation in fixture_method.annotations or []:
            for expected_annotation in fixture_annotations:
                if annotation_matches_expected(
                    annotation=annotation,
                    expected_annotation=expected_annotation,
                    class_imports=fixture_class_imports,
                ):
                    fixture_annotation_prefixes.add(expected_annotation)
        if not fixture_annotation_prefixes:
            effective_methods.append(fixture)
            continue

        fixture_group_context = get_fixture_group_context(
            annotations=list(fixture_method.annotations or []),
            fixture_annotations=fixture_annotations,
            class_imports=fixture_class_imports,
        )
        if is_fixture_effective_for_test(
            fixture_annotation_prefixes=fixture_annotation_prefixes,
            fixture_group_context=fixture_group_context,
            test_group_context=test_group_context,
            class_scope_annotations=class_scope_annotations,
            group_scope_annotations=GROUP_SCOPED_ANNOTATIONS,
        ):
            effective_methods.append(
                FixtureMethod(
                    defining_class_name=class_name,
                    method_signature=method_signature,
                    is_ambiguous=fixture.is_ambiguous
                    or (
                        (
                            fixture_group_context.ambiguous
                            or test_group_context.ambiguous
                        )
                        and bool(fixture_group_context.has_group_filter)
                    ),
                )
            )

    return effective_methods


def is_fixture_effective_for_test(
    *,
    fixture_annotation_prefixes: set[str],
    fixture_group_context: GroupParsingResult,
    test_group_context: GroupParsingResult,
    class_scope_annotations: set[str],
    group_scope_annotations: set[str],
) -> bool:
    if fixture_annotation_prefixes & class_scope_annotations:
        return True

    # TestNG's alwaysRun=true bypasses group filtering for configuration methods,
    # but @BeforeGroups/@AfterGroups are inherently group-scoped and alwaysRun
    # does not override their group requirement.
    if fixture_group_context.always_run and not (
        fixture_annotation_prefixes & group_scope_annotations
    ):
        return True

    if not fixture_group_context.has_group_filter:
        return True

    # Ambiguous group expressions are treated as effective to avoid
    # excluding fixtures that may run at runtime.
    if fixture_group_context.ambiguous or test_group_context.ambiguous:
        return True

    if not fixture_group_context.groups:
        return True

    if not test_group_context.has_group_filter:
        return False

    return bool(fixture_group_context.groups & test_group_context.groups)


def get_test_group_context(
    *,
    class_annotations: list[ResolvedAnnotation],
    method_annotations: list[str],
    class_annotation_imports_by_class: dict[str, list[JImport]],
    class_imports: list[JImport],
) -> GroupParsingResult:
    groups: set[str] = set()
    has_group_filter: bool = False
    ambiguous: bool = False

    for resolved_annotation in class_annotations:
        if not annotation_matches_expected(
            annotation=resolved_annotation.annotation,
            expected_annotation="@Test",
            class_imports=class_annotation_imports_by_class.get(
                resolved_annotation.declaring_class_name,
                [],
            ),
        ):
            continue

        parsed = parse_annotation_groups(resolved_annotation.annotation)
        if not parsed.has_group_filter:
            continue

        has_group_filter = True
        groups.update(parsed.groups)
        ambiguous = ambiguous or parsed.ambiguous

    for annotation in method_annotations:
        if not annotation_matches_expected(
            annotation=annotation,
            expected_annotation="@Test",
            class_imports=class_imports,
        ):
            continue

        parsed = parse_annotation_groups(annotation)
        if not parsed.has_group_filter:
            continue

        has_group_filter = True
        groups.update(parsed.groups)
        ambiguous = ambiguous or parsed.ambiguous

    return GroupParsingResult(
        groups=groups,
        has_group_filter=has_group_filter,
        ambiguous=ambiguous,
    )


def _annotation_has_always_run(annotation: str) -> bool:
    body = annotation_body(annotation)
    return bool(body and ALWAYS_RUN_RE.search(body))


def get_fixture_group_context(
    *,
    annotations: list[str],
    fixture_annotations: set[str],
    class_imports: list[JImport],
) -> GroupParsingResult:
    groups: set[str] = set()
    has_group_filter: bool = False
    ambiguous: bool = False
    always_run: bool = False

    for annotation in annotations:
        if not any(
            annotation_matches_expected(
                annotation=annotation,
                expected_annotation=expected_annotation,
                class_imports=class_imports,
            )
            for expected_annotation in fixture_annotations
        ):
            continue

        if _annotation_has_always_run(annotation):
            always_run = True

        parsed = parse_annotation_groups(annotation)
        if not parsed.has_group_filter:
            continue

        has_group_filter = True
        groups.update(parsed.groups)
        ambiguous = ambiguous or parsed.ambiguous

    return GroupParsingResult(
        groups=groups,
        has_group_filter=has_group_filter,
        ambiguous=ambiguous,
        always_run=always_run,
    )


def parse_annotation_groups(annotation: str) -> GroupParsingResult:
    body = annotation_body(annotation)
    if not body:
        return GroupParsingResult(groups=set(), has_group_filter=False, ambiguous=False)

    groups: set[str] = set()
    has_group_filter: bool = False
    ambiguous: bool = False

    for match in GROUP_ATTRIBUTE_RE.finditer(body):
        has_group_filter = True
        raw_value: str = match.group("value").strip()
        literal_groups: list[str] = [
            literal.strip()
            for literal in GROUP_LITERAL_RE.findall(raw_value)
            if literal.strip()
        ]

        if literal_groups:
            groups.update(literal_groups)

        residual: str = GROUP_LITERAL_RE.sub("", raw_value)
        residual = residual.replace("{", "").replace("}", "").replace(",", "").strip()
        if residual:
            ambiguous = True
        elif not literal_groups:
            ambiguous = True

    return GroupParsingResult(
        groups=groups,
        has_group_filter=has_group_filter,
        ambiguous=ambiguous,
    )


def _get_class_imports(analysis: JavaAnalysis, class_name: str) -> list[JImport]:
    return get_class_import_declarations(analysis, class_name)


__all__ = [
    "GroupParsingResult",
    "GROUP_SCOPED_ANNOTATIONS",
    "SETUP_CLASS_SCOPE_ANNOTATIONS",
    "TEARDOWN_CLASS_SCOPE_ANNOTATIONS",
    "find_fixture_methods",
    "get_effective_fixture_methods",
    "get_fixture_group_context",
    "get_test_group_context",
    "is_fixture_effective_for_test",
    "parse_annotation_groups",
]
