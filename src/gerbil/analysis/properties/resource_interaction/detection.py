from __future__ import annotations

from gerbil.analysis.schema import (
    HttpRequestRole,
    ResourceInteractionSequence,
    ResourceInteractionStep,
)
from gerbil.analysis.properties.resource_interaction.crud_analysis import (
    crud_operation_for_http_method,
    enrich_resource_interaction_sequence,
)
from gerbil.analysis.properties.resource_interaction.path_normalization import (
    normalize_request_path,
    resource_key,
)
from gerbil.analysis.runtime import TestRuntimeView


def detect_resource_interaction_sequences(
    *,
    runtime_view: TestRuntimeView,
) -> list[ResourceInteractionSequence]:
    """Group HTTP events across all lifecycle phases by normalized resource path.

    Algorithm:
      1. Walk runtime events in evaluation order.
      2. Keep only HTTP EVENT nodes with a normalizable path.
      3. Compute a resource key for each (strips trailing ``/{id}``).
      4. Group steps by resource key, preserving event order.
      5. Return one ``ResourceInteractionSequence`` per group.
    """
    groups: dict[str, list[ResourceInteractionStep]] = {}

    for order, runtime_event in enumerate(runtime_view.iter_events(), start=1):
        classification = runtime_event.node.http_classification
        if classification is None:
            continue
        if classification.request_role != HttpRequestRole.EVENT:
            continue

        raw_path = classification.path
        normalized = normalize_request_path(raw_path)
        if normalized is None:
            continue

        key = resource_key(normalized)
        step = ResourceInteractionStep(
            http_method=classification.http_method.upper(),
            path=raw_path,
            normalized_path=normalized,
            event_order=order,
            phase=runtime_event.phase,
            crud_operation=crud_operation_for_http_method(classification.http_method),
        )
        groups.setdefault(key, []).append(step)

    sequences = [
        enrich_resource_interaction_sequence(
            ResourceInteractionSequence(resource_key=key, steps=steps)
        )
        for key, steps in groups.items()
    ]
    return sorted(sequences, key=lambda seq: seq.steps[0].event_order)


__all__ = ["detect_resource_interaction_sequences"]
