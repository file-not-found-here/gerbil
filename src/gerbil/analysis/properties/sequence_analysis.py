"""Project the classified TestRuntimeView into a flat HTTP API skeleton."""

from __future__ import annotations

from collections import Counter

from gerbil.analysis.properties.resource_interaction.path_normalization import (
    normalize_request_path,
    resource_key,
)
from gerbil.analysis.http.classification import (
    build_http_request_interaction_for_event,
)
from gerbil.analysis.runtime.call_sites import CallSiteNode
from gerbil.analysis.schema import (
    ApiSequenceStep,
    AssertionRole,
    HttpInteraction,
    HttpInteractionKind,
    HttpRequestInteraction,
    HttpRequestRole,
    HttpResponseExtraction,
    HttpResponseRole,
    HttpSequenceSummary,
    HttpTestSequence,
    HttpVerificationInteraction,
    SequenceStepKind,
    SourceSpan,
)
from gerbil.analysis.runtime import RuntimeEvent, TestRuntimeView

_RESPONSE_CHECK_ROLES: frozenset[AssertionRole] = frozenset(
    {AssertionRole.STATUS, AssertionRole.BODY, AssertionRole.HEADER}
)


def _source_span_from_node(node: CallSiteNode) -> SourceSpan:
    return SourceSpan(
        start_line=node.span.start.line,
        start_column=node.span.start.col,
        end_line=node.span.end.line,
        end_column=node.span.end.col,
    )


def build_api_call_sequence(
    runtime_view: TestRuntimeView,
) -> list[ApiSequenceStep]:
    """Build an ordered HTTP API skeleton from pre-classified runtime events."""

    steps: list[ApiSequenceStep] = []

    for event in runtime_view.iter_events():
        node = event.node
        http = node.http_classification
        assertion = node.assertion_classification

        if http is not None and http.request_role in (
            HttpRequestRole.BUILDER,
            HttpRequestRole.EVENT,
        ):
            kind = (
                SequenceStepKind.REQUEST_BUILD
                if http.request_role == HttpRequestRole.BUILDER
                else SequenceStepKind.HTTP_REQUEST
            )
            steps.append(
                ApiSequenceStep(
                    order=len(steps) + 1,
                    kind=kind,
                    phase=event.phase,
                    origin=event.origin_context,
                    method_name=node.call_site.method_name or "",
                    source_span=_source_span_from_node(node),
                    framework=http.framework,
                    http_method=(
                        http.http_method if http.http_method != "UNKNOWN" else None
                    ),
                    http_path=http.path or None,
                    path_truncated=http.path_truncated,
                )
            )
        elif (
            assertion is not None
            and assertion.is_countable
            and assertion.role in _RESPONSE_CHECK_ROLES
        ):
            steps.append(
                ApiSequenceStep(
                    order=len(steps) + 1,
                    kind=SequenceStepKind.RESPONSE_CHECK,
                    phase=event.phase,
                    origin=event.origin_context,
                    method_name=node.call_site.method_name or "",
                    source_span=_source_span_from_node(node),
                    framework=http.framework if http is not None else None,
                    assertion_role=assertion.role,
                    status_code=assertion.status_code,
                    status_range=assertion.status_range,
                )
            )

    return steps


def _request_step_fingerprint(step: ApiSequenceStep) -> str:
    method = (step.http_method or "*").upper()
    path = normalize_request_path(step.http_path) or "*"
    return f"{step.kind.value}:{method}:{path}"


def _response_check_fingerprint(step: ApiSequenceStep) -> str:
    role = step.assertion_role.value if step.assertion_role is not None else "*"
    return f"{step.kind.value}:{role}"


def _sequence_fingerprint(steps: list[ApiSequenceStep]) -> str:
    parts: list[str] = []
    for step in steps:
        if step.kind in (SequenceStepKind.REQUEST_BUILD, SequenceStepKind.HTTP_REQUEST):
            parts.append(_request_step_fingerprint(step))
        elif step.kind == SequenceStepKind.RESPONSE_CHECK:
            parts.append(_response_check_fingerprint(step))
    return "|".join(parts)


def build_http_test_sequences(
    call_sequence: list[ApiSequenceStep],
) -> list[HttpTestSequence]:
    sequences: list[HttpTestSequence] = []
    current_steps: list[ApiSequenceStep] = []
    current_has_http_request = False
    current_has_response_check = False

    def close_current() -> None:
        nonlocal current_has_http_request, current_has_response_check, current_steps
        if current_has_http_request:
            sequences.append(
                HttpTestSequence(
                    order=len(sequences) + 1,
                    steps=list(current_steps),
                    length=len(current_steps),
                    fingerprint=_sequence_fingerprint(current_steps),
                )
            )
        current_steps = []
        current_has_http_request = False
        current_has_response_check = False

    for step in call_sequence:
        if step.kind == SequenceStepKind.REQUEST_BUILD:
            if current_has_http_request or current_has_response_check:
                close_current()
            current_steps.append(step)
        elif step.kind == SequenceStepKind.HTTP_REQUEST:
            if current_has_http_request or current_has_response_check:
                close_current()
            current_steps.append(step)
            current_has_http_request = True
        elif step.kind == SequenceStepKind.RESPONSE_CHECK:
            if current_has_http_request:
                current_steps.append(step)
                current_has_response_check = True

    close_current()
    return sequences


def build_http_sequence_summary(
    test_sequences: list[HttpTestSequence],
) -> HttpSequenceSummary:
    steps = [step for sequence in test_sequences for step in sequence.steps]
    methods: set[str] = set()
    resources: set[str] = set()
    endpoints: set[str] = set()

    for step in steps:
        if step.kind != SequenceStepKind.HTTP_REQUEST:
            continue
        method = (step.http_method or "").upper()
        normalized_path = normalize_request_path(step.http_path)
        if method:
            methods.add(method)
        if normalized_path is not None:
            resources.add(resource_key(normalized_path))
        if method and normalized_path is not None:
            endpoints.add(f"{method} {normalized_path}")

    fingerprint_counts = Counter(sequence.fingerprint for sequence in test_sequences)
    repeated_fingerprint_count = sum(
        1 for count in fingerprint_counts.values() if count > 1
    )

    return HttpSequenceSummary(
        sequence_count=len(test_sequences),
        sequence_lengths=[sequence.length for sequence in test_sequences],
        request_build_step_count=sum(
            1 for step in steps if step.kind == SequenceStepKind.REQUEST_BUILD
        ),
        http_request_step_count=sum(
            1 for step in steps if step.kind == SequenceStepKind.HTTP_REQUEST
        ),
        response_check_step_count=sum(
            1 for step in steps if step.kind == SequenceStepKind.RESPONSE_CHECK
        ),
        has_multiple_sequences=len(test_sequences) > 1,
        distinct_http_method_count=len(methods),
        distinct_resource_count=len(resources),
        distinct_endpoint_count=len(endpoints),
        distinct_sequence_fingerprint_count=len(fingerprint_counts),
        repeated_sequence_fingerprint_count=repeated_fingerprint_count,
        has_repeated_sequence=repeated_fingerprint_count > 0,
    )


def build_http_verification_interactions(
    runtime_view: TestRuntimeView,
) -> list[HttpVerificationInteraction]:
    """Build origin-tagged HTTP response verification events."""

    interactions: list[HttpVerificationInteraction] = []

    for event in runtime_view.iter_events():
        interaction = build_http_verification_interaction_for_event(event)
        if interaction is not None:
            interactions.append(interaction)

    return interactions


def build_http_verification_interaction_for_event(
    event: RuntimeEvent,
) -> HttpVerificationInteraction | None:
    node = event.node
    http = node.http_classification
    assertion = node.assertion_classification

    if (
        assertion is None
        or not assertion.is_countable
        or assertion.role not in _RESPONSE_CHECK_ROLES
    ):
        return None

    return HttpVerificationInteraction(
        origin=event.origin_context,
        assertion_role=assertion.role,
        method_name=node.call_site.method_name or "",
        source_span=_source_span_from_node(node),
        framework=http.framework if http is not None else None,
        response_role=http.response_role if http is not None else None,
        status_code=assertion.status_code,
        status_range=assertion.status_range,
    )


def build_http_interaction_views(
    runtime_view: TestRuntimeView,
) -> tuple[
    list[HttpRequestInteraction],
    list[HttpVerificationInteraction],
    list[HttpResponseExtraction],
    list[HttpInteraction],
]:
    """Classify each runtime event once into request, verification, and
    response-extraction views, keeping the buckets disjoint by construction."""
    request_interactions: list[HttpRequestInteraction] = []
    verification_interactions: list[HttpVerificationInteraction] = []
    response_extractions: list[HttpResponseExtraction] = []
    http_interactions: list[HttpInteraction] = []

    for event in runtime_view.iter_events():
        request_interaction = build_http_request_interaction_for_event(event)
        if request_interaction is not None:
            request_interactions.append(request_interaction)
            http_interactions.append(
                HttpInteraction(
                    kind=HttpInteractionKind.REQUEST,
                    origin=request_interaction.origin,
                    source_span=_source_span_from_node(event.node),
                    request_interaction=request_interaction,
                )
            )

        verification_interaction = build_http_verification_interaction_for_event(event)
        if verification_interaction is not None:
            verification_interactions.append(verification_interaction)
            http_interactions.append(
                HttpInteraction(
                    kind=HttpInteractionKind.VERIFICATION,
                    origin=verification_interaction.origin,
                    source_span=verification_interaction.source_span,
                    verification_interaction=verification_interaction,
                )
            )

        # Extractors only: inspector-role nodes include chain plumbing
        # (andExpect, then) whose presence says nothing about response-data
        # reuse, and a node counted as a response-check assertion never also
        # counts as an extraction.
        http = event.node.http_classification
        if (
            verification_interaction is None
            and http is not None
            and http.response_role is HttpResponseRole.EXTRACTOR
        ):
            response_extractions.append(
                HttpResponseExtraction(
                    origin=event.origin_context,
                    response_role=http.response_role,
                    method_name=event.node.call_site.method_name or "",
                    source_span=_source_span_from_node(event.node),
                    framework=http.framework,
                )
            )

    return (
        request_interactions,
        verification_interactions,
        response_extractions,
        http_interactions,
    )
