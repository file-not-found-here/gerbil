from __future__ import annotations

import re

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JCallable, JImport, JType
from cldk.models.java.models import JField

from gerbil.analysis.shared.class_utils import ResolvedAnnotation
from gerbil.analysis.shared.annotations import (
    annotation_matches_expected,
)
from gerbil.analysis.shared.constants import (
    CONTAINERIZED_ENVIRONMENT_ANNOTATIONS,
    CONTAINERIZED_FIELD_ANNOTATIONS,
    CONTAINERIZED_RECEIVER_HINTS,
    MOCKED_CALL_NAMES,
    MOCKED_ENVIRONMENT_ANNOTATIONS,
    MOCKED_FIELD_ANNOTATIONS,
    MOCKED_RECEIVER_HINTS,
    VIRTUALIZED_ENVIRONMENT_ANNOTATIONS,
    VIRTUALIZED_STRATEGY_RECEIVER_HINTS,
)
from gerbil.analysis.shared.caching import (
    get_receiver_hierarchy,
)
from gerbil.analysis.shared.receiver_resolution import RuntimeReceiverResolver
from gerbil.analysis.shared.static_imports import (
    matches_receiver_prefix as _matches_receiver_prefix,
)
from gerbil.analysis.schema import (
    DependencyStrategy,
    DependencyStrategyDecision,
)
from gerbil.analysis.runtime import TestRuntimeView


def _matching_annotation(
    annotations: list[str],
    expected: set[str],
    class_imports: list[JImport],
) -> str | None:
    for annotation in annotations:
        for expected_annotation in expected:
            if annotation_matches_expected(
                annotation=annotation,
                expected_annotation=expected_annotation,
                class_imports=class_imports,
            ):
                return annotation
    return None


def _matching_resolved_annotation(
    annotations: list[ResolvedAnnotation],
    expected: set[str],
    class_annotation_imports_by_class: dict[str, list[JImport]],
) -> str | None:
    for resolved_annotation in annotations:
        for expected_annotation in expected:
            if annotation_matches_expected(
                annotation=resolved_annotation.annotation,
                expected_annotation=expected_annotation,
                class_imports=class_annotation_imports_by_class.get(
                    resolved_annotation.declaring_class_name,
                    [],
                ),
            ):
                return resolved_annotation.annotation
    return None


def _receiver_matches_hints(
    receiver_type: str,
    hints: set[str],
    analysis: JavaAnalysis | None,
) -> bool:
    if not receiver_type:
        return False

    receiver_hierarchy: tuple[str, ...]
    if analysis is None:
        receiver_hierarchy = (receiver_type,)
    else:
        receiver_hierarchy = get_receiver_hierarchy(receiver_type, analysis)

    return any(
        _matches_receiver_prefix(receiver_candidate, prefix)
        for receiver_candidate in receiver_hierarchy
        for prefix in hints
    )


def _add_signal(
    signals: dict[str, list[str]],
    label: str,
    signal: str,
) -> None:
    signals.setdefault(label, []).append(signal)


def _has_mocked_call_context(
    receiver_type: str,
    callee_signature: str,
    analysis: JavaAnalysis | None,
) -> bool:
    if _receiver_matches_hints(
        receiver_type=receiver_type,
        hints=MOCKED_RECEIVER_HINTS,
        analysis=analysis,
    ):
        return True

    if not callee_signature:
        return False

    return any(
        _matches_receiver_prefix(callee_signature, prefix)
        for prefix in MOCKED_RECEIVER_HINTS
    )


def classify_dependency_strategy(
    class_details: JType | None,
    method_details: JCallable | None,
    class_annotations: list[ResolvedAnnotation],
    runtime_view: TestRuntimeView,
    class_annotation_imports_by_class: dict[str, list[JImport]],
    method_imports: list[JImport],
    declaring_class_imports: list[JImport],
    analysis: JavaAnalysis | None,
    receiver_resolver: RuntimeReceiverResolver,
) -> DependencyStrategyDecision:
    signals: dict[str, list[str]] = {}

    effective_class_annotations: list[ResolvedAnnotation] = list(class_annotations)
    runtime_test_entry = runtime_view.test_entry()
    effective_method_details: JCallable | None = (
        runtime_test_entry.method_details
        if runtime_test_entry is not None
        else method_details
    )
    method_annotations: list[str] = (
        list(effective_method_details.annotations or [])
        if effective_method_details
        else []
    )

    # ------------------------------------------------------------------
    # Tier 1: Environment signals — always active for all tests in class
    # ------------------------------------------------------------------

    _CLASS_ANNOTATION_CHECKS: list[tuple[set[str], str]] = [
        (VIRTUALIZED_ENVIRONMENT_ANNOTATIONS, DependencyStrategy.VIRTUALIZED),
        (CONTAINERIZED_ENVIRONMENT_ANNOTATIONS, DependencyStrategy.CONTAINERIZED),
        (MOCKED_ENVIRONMENT_ANNOTATIONS, DependencyStrategy.MOCKED),
    ]
    for expected_set, label in _CLASS_ANNOTATION_CHECKS:
        match = _matching_resolved_annotation(
            effective_class_annotations,
            expected_set,
            class_annotation_imports_by_class,
        )
        if match is not None:
            _add_signal(signals, label, f"environment:annotation:{match}")

    _METHOD_ANNOTATION_CHECKS: list[tuple[set[str], str]] = [
        (MOCKED_ENVIRONMENT_ANNOTATIONS, DependencyStrategy.MOCKED),
        (CONTAINERIZED_ENVIRONMENT_ANNOTATIONS, DependencyStrategy.CONTAINERIZED),
    ]
    for expected_set, label in _METHOD_ANNOTATION_CHECKS:
        match = _matching_annotation(
            method_annotations,
            expected_set,
            method_imports,
        )
        if match is not None:
            _add_signal(signals, label, f"environment:annotation:{match}")

    # Field annotations → environment signals + Tier 3 candidates
    field_declarations: list[JField] = (
        list(class_details.field_declarations or []) if class_details else []
    )
    correlated_field_names: dict[str, str] = {}  # variable_name → annotation

    _FIELD_ANNOTATION_CHECKS: list[tuple[set[str], str]] = [
        (MOCKED_ENVIRONMENT_ANNOTATIONS, DependencyStrategy.MOCKED),
        (CONTAINERIZED_FIELD_ANNOTATIONS, DependencyStrategy.CONTAINERIZED),
    ]
    for field in field_declarations:
        field_name = field.variables[0] if field.variables else "unknown"
        for annotation in field.annotations:
            for expected_set, label in _FIELD_ANNOTATION_CHECKS:
                if any(
                    annotation_matches_expected(
                        annotation=annotation,
                        expected_annotation=ea,
                        class_imports=declaring_class_imports,
                    )
                    for ea in expected_set
                ):
                    _add_signal(
                        signals,
                        label,
                        f"environment:field-annotation:{annotation}:{field_name}",
                    )

            # Collect Tier 3 candidates
            if any(
                annotation_matches_expected(
                    annotation=annotation,
                    expected_annotation=ea,
                    class_imports=declaring_class_imports,
                )
                for ea in MOCKED_FIELD_ANNOTATIONS
            ):
                for var_name in field.variables:
                    correlated_field_names[var_name] = annotation

    # ------------------------------------------------------------------
    # Tier 2: Call-site evidence — method-scoped
    # ------------------------------------------------------------------

    # Only collect expressions for Tier 3 if there are correlated field candidates.
    collect_exprs = bool(correlated_field_names)
    all_exprs: set[str] = set()

    for runtime_event in runtime_view.iter_events():
        call_site = runtime_event.call_site
        method_name: str = call_site.method_name or ""
        receiver_type = receiver_resolver.resolve_for_event(
            runtime_event.owner,
            call_site,
        ).receiver_type
        callee_signature: str = call_site.callee_signature or ""

        if collect_exprs:
            if call_site.receiver_expr:
                all_exprs.add(str(call_site.receiver_expr))
            all_exprs.update(str(item) for item in call_site.argument_expr)

        # Mocked call-site: receiver matches MOCKED hints + method in MOCKED_CALL_NAMES
        if method_name in MOCKED_CALL_NAMES and _has_mocked_call_context(
            receiver_type=receiver_type,
            callee_signature=callee_signature,
            analysis=analysis,
        ):
            signal_receiver = receiver_type or callee_signature
            _add_signal(
                signals,
                DependencyStrategy.MOCKED,
                f"call-site:receiver:{signal_receiver}.{method_name}",
            )

        # Virtualized call-site
        if _receiver_matches_hints(
            receiver_type=receiver_type,
            hints=VIRTUALIZED_STRATEGY_RECEIVER_HINTS,
            analysis=analysis,
        ):
            _add_signal(
                signals,
                DependencyStrategy.VIRTUALIZED,
                f"call-site:receiver:{receiver_type}.{method_name}",
            )

        # Containerized call-site
        if _receiver_matches_hints(
            receiver_type=receiver_type,
            hints=CONTAINERIZED_RECEIVER_HINTS,
            analysis=analysis,
        ):
            _add_signal(
                signals,
                DependencyStrategy.CONTAINERIZED,
                f"call-site:receiver:{receiver_type}.{method_name}",
            )

    # ------------------------------------------------------------------
    # Tier 3: Correlated field annotations — only if test uses the field
    # ------------------------------------------------------------------

    for var_name, annotation in correlated_field_names.items():
        # Word-boundary match: a raw substring check would let short field
        # names (e.g. `om`, `db`) fire inside unrelated identifiers.
        var_pattern = re.compile(r"\b" + re.escape(var_name) + r"\b")
        if any(var_pattern.search(token) for token in all_exprs):
            _add_signal(
                signals,
                DependencyStrategy.MOCKED,
                f"field-correlated:annotation:{annotation}:{var_name}",
            )

    labels = sorted(signals.keys())
    return DependencyStrategyDecision(labels=labels, signals=signals)
