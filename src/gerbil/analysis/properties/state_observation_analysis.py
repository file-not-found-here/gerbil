"""Detect back-door state reads (DB/MQ/FS) tied to an assertion via lexical
containment (Tier 1) or local-variable binding (Tier 2), plus annotation-declared
DB postcondition assertions (e.g. ``@ExpectedDataSet``)."""

from __future__ import annotations

import re

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JImport
from cldk.models.java.models import JCallable, JVariableDeclaration

from gerbil.analysis.runtime import TestRuntimeView
from gerbil.analysis.runtime.call_sites import CallSiteNode, MethodRef
from gerbil.analysis.schema import (
    LifecyclePhase,
    StateObservation,
    StateObservationMedium,
    StateObservationSummary,
    StateObservationTier,
)
from gerbil.analysis.shared.annotations import annotation_matches_expected
from gerbil.analysis.shared.caching import get_receiver_hierarchy
from gerbil.analysis.shared.class_utils import ResolvedAnnotation
from gerbil.analysis.shared.constants import (
    DB_STATE_ASSERTION_ANNOTATIONS,
    OBSERVATION_MEDIUM_DB_QUERY_RECEIVER_METHODS,
    OBSERVATION_MEDIUM_FS_RECEIVER_METHODS,
    OBSERVATION_MEDIUM_MQ_RECEIVER_METHODS,
)
from gerbil.analysis.shared.receiver_resolution import RuntimeReceiverResolver
from gerbil.analysis.shared.static_imports import (
    matches_receiver_prefix as _matches_receiver_prefix,
)

_OBSERVATION_MAPS: list[tuple[StateObservationMedium, dict[str, set[str]]]] = [
    (StateObservationMedium.DB, OBSERVATION_MEDIUM_DB_QUERY_RECEIVER_METHODS),
    (StateObservationMedium.MQ, OBSERVATION_MEDIUM_MQ_RECEIVER_METHODS),
    (StateObservationMedium.FS, OBSERVATION_MEDIUM_FS_RECEIVER_METHODS),
]

_ALL_OBSERVATION_METHODS: frozenset[str] = frozenset(
    method
    for _, receiver_method_map in _OBSERVATION_MAPS
    for methods in receiver_method_map.values()
    for method in methods
)

_SPRING_DATA_RECEIVER_PREFIX: str = "org.springframework.data."
# Spring Data derived-query reads (findByEmail, existsByUsername, countByStatus):
# a read verb followed by a ``By<Property>`` clause. delete/remove are mutations,
# not observations, so they are excluded; the ``By[A-Z]`` anchor keeps the broad
# verb prefix from matching ordinary getters (getByteArray, findByteCount).
_SPRING_DATA_DERIVED_QUERY_RE: re.Pattern[str] = re.compile(
    r"^(?:find|read|get|query|search|stream|count|exists)[A-Za-z0-9]*By[A-Z]\w*$"
)

_OBSERVATION_REACH_PHASES: frozenset[LifecyclePhase] = frozenset(
    {LifecyclePhase.TEST, LifecyclePhase.TEARDOWN}
)


def _match_observation(
    receiver_hierarchy: tuple[str, ...],
    method_name: str,
) -> tuple[StateObservationMedium, str] | None:
    for medium, receiver_method_map in _OBSERVATION_MAPS:
        for prefix, methods in receiver_method_map.items():
            if method_name not in methods:
                continue
            for candidate in receiver_hierarchy:
                if _matches_receiver_prefix(candidate, prefix):
                    evidence = (
                        f"{prefix}{method_name}"
                        if prefix.endswith(".")
                        else f"{prefix}.{method_name}"
                    )
                    return medium, evidence
    # Derived-query reads are gated to the Spring Data receiver hierarchy so the
    # broad name pattern can only fire on an actual repository.
    if _SPRING_DATA_DERIVED_QUERY_RE.match(method_name):
        for candidate in receiver_hierarchy:
            if _matches_receiver_prefix(candidate, _SPRING_DATA_RECEIVER_PREFIX):
                return (
                    StateObservationMedium.DB,
                    f"{_SPRING_DATA_RECEIVER_PREFIX}{method_name}",
                )
    return None


def _ancestor_has_assertion(node: CallSiteNode) -> bool:
    current = node.parent
    while current is not None:
        if current.assertion_classification is not None:
            return True
        current = current.parent
    return False


def _find_binding_declaration(
    read_node: CallSiteNode,
    method_details: JCallable,
) -> JVariableDeclaration | None:
    read_line = read_node.call_site.start_line
    method_name = read_node.call_site.method_name or ""
    if not method_name:
        return None

    method_pattern = re.compile(r"\b" + re.escape(method_name) + r"\b")

    best: JVariableDeclaration | None = None
    best_width = 0
    for declaration in method_details.variable_declarations or []:
        decl_start = declaration.start_line
        decl_end = max(declaration.end_line, decl_start)
        if not (decl_start <= read_line <= decl_end):
            continue
        if not method_pattern.search(declaration.initializer or ""):
            continue
        width = decl_end - decl_start
        if best is None or width < best_width:
            best = declaration
            best_width = width
    return best


def _assertion_references_name(node: CallSiteNode, pattern: re.Pattern[str]) -> bool:
    def _matches(check_node: CallSiteNode) -> bool:
        call_site = check_node.call_site
        receiver_expr = call_site.receiver_expr or ""
        if receiver_expr and pattern.search(receiver_expr):
            return True
        for argument in call_site.argument_expr or []:
            if argument and pattern.search(str(argument)):
                return True
        return False

    if _matches(node):
        return True
    return any(_matches(descendant) for descendant in node.all_descendants())


def _iter_assertion_nodes_after(
    read_node: CallSiteNode,
    candidate_nodes: list[CallSiteNode],
) -> list[CallSiteNode]:
    gate = read_node.span.end
    return [
        candidate
        for candidate in candidate_nodes
        if candidate.assertion_classification is not None
        and candidate.span.start > gate
    ]


def detect_db_state_assertion_annotations(
    class_annotations: list[ResolvedAnnotation],
    method_annotations: list[str],
    class_annotation_imports_by_class: dict[str, list[JImport]],
    method_imports: list[JImport],
) -> list[str]:
    """Annotation-declared DB postconditions (e.g. ``@ExpectedDataSet``): the
    framework compares database state after the test, acting as the oracle."""

    matched: set[str] = set()
    for resolved in class_annotations:
        for expected in DB_STATE_ASSERTION_ANNOTATIONS:
            if annotation_matches_expected(
                annotation=resolved.annotation,
                expected_annotation=expected,
                class_imports=class_annotation_imports_by_class.get(
                    resolved.declaring_class_name, []
                ),
            ):
                matched.add(expected)
                break

    for annotation in method_annotations:
        for expected in DB_STATE_ASSERTION_ANNOTATIONS:
            if annotation_matches_expected(
                annotation=annotation,
                expected_annotation=expected,
                class_imports=method_imports,
            ):
                matched.add(expected)
                break

    return sorted(matched)


def db_state_assertion_observations(
    class_annotations: list[ResolvedAnnotation],
    method_annotations: list[str],
    class_annotation_imports_by_class: dict[str, list[JImport]],
    method_imports: list[JImport],
) -> list[StateObservation]:
    """Annotation-declared DB postconditions as observation entries."""

    return [
        StateObservation(
            medium=StateObservationMedium.DB,
            tier=StateObservationTier.ANNOTATION,
            receiver_type="",
            method_name="",
            evidence=annotation_name,
        )
        for annotation_name in detect_db_state_assertion_annotations(
            class_annotations,
            method_annotations,
            class_annotation_imports_by_class,
            method_imports,
        )
    ]


def analyze_state_observations(
    runtime_view: TestRuntimeView,
    analysis: JavaAnalysis | None,
    receiver_resolver: RuntimeReceiverResolver,
) -> StateObservationSummary:
    """Classify back-door state observations across the runtime view.

    Assumes assertion classification has already annotated CallSiteNodes.
    """

    emitted: dict[tuple[StateObservationMedium, str, str, int], StateObservation] = {}

    # Tier 2 needs JCallable.variable_declarations, only available at depth 0.
    top_level_method_details: dict[MethodRef, JCallable] = {}
    top_level_grouping_nodes: dict[MethodRef, list[CallSiteNode]] = {}
    for entry in runtime_view.entries:
        if entry.method_details is None:
            continue
        top_level_method_details[entry.method_ref] = entry.method_details
        top_level_grouping_nodes[entry.method_ref] = entry.grouping.nodes

    for event in runtime_view.iter_events():
        if event.phase not in _OBSERVATION_REACH_PHASES:
            continue

        call_site = event.call_site
        method_name = call_site.method_name or ""
        if (
            method_name not in _ALL_OBSERVATION_METHODS
            and not _SPRING_DATA_DERIVED_QUERY_RE.match(method_name)
        ):
            continue

        resolved = receiver_resolver.resolve_for_event(event.owner, call_site)
        receiver_type = resolved.receiver_type
        if not receiver_type:
            continue

        if analysis is None:
            hierarchy: tuple[str, ...] = (receiver_type,)
        else:
            hierarchy = get_receiver_hierarchy(receiver_type, analysis)

        match = _match_observation(hierarchy, method_name)
        if match is None:
            continue
        medium, evidence = match

        tier: StateObservationTier | None = None

        if _ancestor_has_assertion(event.node):
            tier = StateObservationTier.NESTED
        elif event.depth == 0:
            method_details = top_level_method_details.get(event.owner)
            grouping_nodes = top_level_grouping_nodes.get(event.owner)
            if method_details is not None and grouping_nodes is not None:
                declaration = _find_binding_declaration(event.node, method_details)
                if declaration is not None and declaration.name:
                    name_pattern = re.compile(
                        r"\b" + re.escape(declaration.name) + r"\b"
                    )
                    for assertion_node in _iter_assertion_nodes_after(
                        event.node, grouping_nodes
                    ):
                        if _assertion_references_name(assertion_node, name_pattern):
                            tier = StateObservationTier.BINDING
                            break

        if tier is None:
            continue

        start_line = call_site.start_line
        key_tuple = (medium, receiver_type, method_name, start_line)
        if key_tuple in emitted:
            continue
        emitted[key_tuple] = StateObservation(
            medium=medium,
            tier=tier,
            receiver_type=receiver_type,
            method_name=method_name,
            evidence=evidence,
            start_line=start_line,
        )

    observations = sorted(
        emitted.values(),
        key=lambda o: (o.medium.value, o.start_line, o.method_name, o.evidence),
    )
    return StateObservationSummary(observations=observations)
