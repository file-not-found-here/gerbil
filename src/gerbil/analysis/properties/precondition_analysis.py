"""Two-tier precondition classification.

Tier 1 is annotation-driven (unchanged in spirit): class-level and method-level
annotations are matched against curated sets for DB seeding and container
bootstrap.

Tier 2 adds call-site evidence scoped to SETUP-phase runtime events only.
Test-body writes are exercised behavior, teardown writes are cleanup; neither
is a precondition. Matching is done on receiver-type prefix + method name
using curated maps in ``gerbil.analysis.shared.constants``.

Known limitations (static analysis cannot see):

- Static initializers and class-level field initializers are not surfaced by
  ``TestRuntimeView`` / fixture discovery; tests that bootstrap containers via
  ``static GenericContainer c = new GenericContainer(...)`` without a
  ``@Testcontainers``/``@Container`` annotation and without a ``@BeforeAll``
  call will be missed.
- Mock-stubbed writes (``when(repository.save(any()))...``) in SETUP can be
  false positives; accepted as low-volume.
- Dynamic receiver types (reflection, property-source-driven template choice)
  fall through to no-match.
"""

from __future__ import annotations

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JImport

from gerbil.analysis.runtime import TestRuntimeView
from gerbil.analysis.schema import (
    LifecyclePhase,
    Precondition,
    PreconditionSource,
    PreconditionSummary,
    PreconditionType,
)
from gerbil.analysis.shared.annotations import annotation_matches_expected
from gerbil.analysis.shared.caching import get_receiver_hierarchy
from gerbil.analysis.shared.class_utils import ResolvedAnnotation
from gerbil.analysis.shared.constants import (
    CONTAINER_BOOTSTRAP_ANNOTATIONS,
    CONTAINER_BOOTSTRAP_RECEIVER_METHODS,
    DB_SEEDING_ANNOTATIONS,
    DB_SEEDING_RECEIVER_METHODS,
    FS_SEEDING_RECEIVER_METHODS,
    MQ_SEEDING_RECEIVER_METHODS,
)
from gerbil.analysis.shared.receiver_resolution import RuntimeReceiverResolver
from gerbil.analysis.shared.static_imports import (
    matches_receiver_prefix as _matches_receiver_prefix,
)

_ANNOTATION_TO_PRECONDITION: dict[str, PreconditionType] = {
    annotation: PreconditionType.DB_SEEDING for annotation in DB_SEEDING_ANNOTATIONS
} | {
    annotation: PreconditionType.CONTAINER_BOOTSTRAP
    for annotation in CONTAINER_BOOTSTRAP_ANNOTATIONS
}

_PROGRAMMATIC_MAPS: list[tuple[PreconditionType, dict[str, set[str]]]] = [
    (PreconditionType.DB_SEEDING, DB_SEEDING_RECEIVER_METHODS),
    (PreconditionType.CONTAINER_BOOTSTRAP, CONTAINER_BOOTSTRAP_RECEIVER_METHODS),
    (PreconditionType.MQ_SEEDING, MQ_SEEDING_RECEIVER_METHODS),
    (PreconditionType.FS_SEEDING, FS_SEEDING_RECEIVER_METHODS),
]

# Union of every method name that appears in any programmatic map. Used as a
# pre-filter so non-seeding call sites skip the receiver-hierarchy lookup.
_ALL_PROGRAMMATIC_METHODS: frozenset[str] = frozenset(
    method
    for _, receiver_method_map in _PROGRAMMATIC_MAPS
    for methods in receiver_method_map.values()
    for method in methods
)


def _match_programmatic(
    receiver_hierarchy: tuple[str, ...],
    method_name: str,
) -> tuple[PreconditionType, str] | None:
    """First-match-wins dispatch over the programmatic maps.

    Returns ``(precondition_type, evidence)`` on hit, else ``None``. ``evidence``
    is the canonical ``<matched_prefix>.<method_name>`` string (with any
    trailing ``.`` on the prefix collapsed).
    """

    for precondition_type, receiver_method_map in _PROGRAMMATIC_MAPS:
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
                    return precondition_type, evidence
    return None


def analyze_preconditions(
    class_annotations: list[ResolvedAnnotation],
    method_annotations: list[str],
    class_annotation_imports_by_class: dict[str, list[JImport]],
    method_imports: list[JImport],
    runtime_view: TestRuntimeView,
    analysis: JavaAnalysis | None,
    receiver_resolver: RuntimeReceiverResolver,
) -> PreconditionSummary:
    """Classify preconditions from annotations (Tier 1) and SETUP-phase call
    sites (Tier 2)."""

    entries: dict[tuple[PreconditionType, PreconditionSource, str], Precondition] = {}

    def _emit(
        precondition_type: PreconditionType,
        source: PreconditionSource,
        evidence: str,
    ) -> None:
        key = (precondition_type, source, evidence)
        if key in entries:
            return
        entries[key] = Precondition(
            type=precondition_type,
            source=source,
            evidence=evidence,
        )

    # ------------------------------------------------------------------
    # Tier 1: Annotations
    # ------------------------------------------------------------------
    for resolved in class_annotations:
        for expected, ptype in _ANNOTATION_TO_PRECONDITION.items():
            if not annotation_matches_expected(
                annotation=resolved.annotation,
                expected_annotation=expected,
                class_imports=class_annotation_imports_by_class.get(
                    resolved.declaring_class_name, []
                ),
            ):
                continue
            _emit(ptype, PreconditionSource.ANNOTATION, expected)
            break

    for annotation in method_annotations:
        for expected, ptype in _ANNOTATION_TO_PRECONDITION.items():
            if not annotation_matches_expected(
                annotation=annotation,
                expected_annotation=expected,
                class_imports=method_imports,
            ):
                continue
            _emit(ptype, PreconditionSource.ANNOTATION, expected)
            break

    # ------------------------------------------------------------------
    # Tier 2: SETUP-phase programmatic call sites
    # ------------------------------------------------------------------
    for event in runtime_view.phase_events(LifecyclePhase.SETUP):
        call_site = event.call_site
        method_name = call_site.method_name or ""
        if method_name not in _ALL_PROGRAMMATIC_METHODS:
            continue

        receiver_type = receiver_resolver.resolve_for_event(
            event.owner,
            call_site,
        ).receiver_type
        if not receiver_type:
            continue

        if analysis is None:
            hierarchy: tuple[str, ...] = (receiver_type,)
        else:
            hierarchy = get_receiver_hierarchy(receiver_type, analysis)

        match = _match_programmatic(hierarchy, method_name)
        if match is None:
            continue

        precondition_type, evidence = match
        _emit(precondition_type, PreconditionSource.PROGRAMMATIC, evidence)

    preconditions = sorted(
        entries.values(),
        key=lambda p: (p.type.value, p.source.value, p.evidence),
    )
    return PreconditionSummary(preconditions=preconditions)
