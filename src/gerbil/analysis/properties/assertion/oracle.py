"""Oracle type classification.

Classifies a test method's verification strategy into one of four categories
using an ``OracleTypeDecision`` (label + signals):

- **property-based** -- generative testing with invariants
- **contract** -- validates against a formal schema or consumer-driven contract
- **example-based** -- asserts specific expected values
- **implicit** -- no explicit assertions; oracle is "doesn't crash"

The algorithm runs in three phases:

1. **Collect** -- single pass over runtime events to gather assertion counts,
   contract signals (receiver prefixes + method hints), and property signals
   (receiver prefixes + strong method names).
2. **Evaluate** -- determine which categories have positive signals, including
   import-validated ``@Property`` annotation matching.
3. **Decide** -- apply precedence (property-based > contract > example-based)
   to produce an ``OracleTypeDecision``.
"""

from __future__ import annotations

from cldk.models.java import JCallable, JImport

from gerbil.analysis.shared.annotations import annotation_matches_expected
from gerbil.analysis.shared.constants import (
    CONTRACT_METHOD_HINTS,
    CONTRACT_RECEIVER_PREFIXES,
    PROPERTY_RECEIVER_PREFIXES,
    STRONG_PROPERTY_METHODS,
)
from gerbil.analysis.properties.assertion.failure import (
    has_expected_exception_annotation,
)
from gerbil.analysis.runtime import TestRuntimeView
from gerbil.analysis.shared.receiver_resolution import RuntimeReceiverResolver
from gerbil.analysis.schema import OracleTypeDecision


def classify_oracle_type(
    *,
    runtime_view: TestRuntimeView,
    method_details: JCallable | None,
    class_imports: list[JImport],
    receiver_resolver: RuntimeReceiverResolver,
    method_annotations: list[str] | None = None,
) -> OracleTypeDecision:
    # ── Phase 1: Collect ─────────────────────────────────────────────
    assertion_count = 0
    contract_method_name: str | None = None
    contract_receiver_type: str | None = None
    property_receiver_type: str | None = None
    strong_property_method_name: str | None = None

    for event in runtime_view.iter_events():
        call_site = event.call_site
        method_name = call_site.method_name or ""

        if (
            event.node.assertion_classification is not None
            and event.node.assertion_classification.is_countable
        ):
            assertion_count += 1

        if contract_method_name is None and method_name in CONTRACT_METHOD_HINTS:
            contract_method_name = method_name

        if (
            strong_property_method_name is None
            and method_name in STRONG_PROPERTY_METHODS
        ):
            strong_property_method_name = method_name

        if contract_receiver_type is None or property_receiver_type is None:
            resolved = receiver_resolver.resolve_for_event(
                event.owner, call_site
            ).receiver_type
            resolved_lower = resolved.lower()
            callee_lower = (call_site.callee_signature or "").lower()

            if contract_receiver_type is None:
                for prefix in CONTRACT_RECEIVER_PREFIXES:
                    if resolved_lower.startswith(prefix) or callee_lower.startswith(
                        prefix
                    ):
                        contract_receiver_type = resolved
                        break

            if property_receiver_type is None:
                for prefix in PROPERTY_RECEIVER_PREFIXES:
                    if resolved_lower.startswith(prefix) or callee_lower.startswith(
                        prefix
                    ):
                        property_receiver_type = resolved
                        break

    # ── Phase 2: Evaluate ────────────────────────────────────────────
    contract_detected = (
        contract_method_name is not None or contract_receiver_type is not None
    )

    has_property_annotation = False
    if method_details is not None:
        has_property_annotation = any(
            annotation_matches_expected(
                annotation,
                "@Property",
                class_imports=class_imports,
            )
            for annotation in (method_details.annotations or [])
        )

    property_detected = (
        has_property_annotation
        or strong_property_method_name is not None
        or property_receiver_type is not None
    )

    expected_exception_annotation_detected = has_expected_exception_annotation(
        (
            method_annotations
            if method_annotations is not None
            else (method_details.annotations if method_details is not None else None)
        ),
        class_imports,
    )

    example_detected = assertion_count > 0 or expected_exception_annotation_detected

    # ── Build signals ────────────────────────────────────────────────
    signals: dict[str, list[str]] = {}

    if contract_detected:
        contract_signals: list[str] = []
        if contract_method_name is not None:
            contract_signals.append(f"method:{contract_method_name}")
        if contract_receiver_type is not None:
            contract_signals.append(f"receiver:{contract_receiver_type}")
        signals["contract"] = contract_signals

    if property_detected:
        property_signals: list[str] = []
        if property_receiver_type is not None:
            property_signals.append(f"receiver:{property_receiver_type}")
        if strong_property_method_name is not None:
            property_signals.append(f"method:{strong_property_method_name}")
        if has_property_annotation:
            property_signals.append("annotation:@Property")
        signals["property-based"] = property_signals

    if example_detected:
        example_signals: list[str] = [f"assertion-count:{assertion_count}"]
        if expected_exception_annotation_detected:
            example_signals.append("expected-exception-annotation")
        signals["example-based"] = example_signals

    # ── Phase 3: Decide ──────────────────────────────────────────────
    candidates: list[str] = []
    if property_detected:
        candidates.append("property-based")
    if contract_detected:
        candidates.append("contract")
    if example_detected:
        candidates.append("example-based")

    if not candidates:
        return OracleTypeDecision(label="implicit", signals=signals)

    return OracleTypeDecision(label=candidates[0], signals=signals)


__all__ = ["classify_oracle_type"]
