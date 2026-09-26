from __future__ import annotations

from gerbil.analysis.shared.constants import (
    IN_PROCESS_DISPATCH_FRAMEWORKS,
    MODAL_DISPATCH_FRAMEWORK,
    REAL_HTTP_DISPATCH_FRAMEWORKS,
    REST_ASSURED_IN_PROCESS_RECEIVER_PREFIXES,
)
from gerbil.analysis.shared.url_utils import classify_request_target
from gerbil.analysis.schema import (
    HttpDispatchFramework,
    HttpRequestRole,
    RequestDispatchDecision,
)
from gerbil.analysis.runtime import TestRuntimeView


def analyze_request_dispatch(
    runtime_view: TestRuntimeView | None = None,
) -> RequestDispatchDecision:
    """Classify request dispatch from runtime HTTP event frameworks.

    Returns a :class:`RequestDispatchDecision` with labels
    (e.g. ``["in-process", "local-network"]``) and signals mapping each label
    to its contributing evidence.

    Algorithm:
      1. No runtime / no EVENT nodes → ``["unknown"]``
      2. Partition event frameworks into in-process vs real-HTTP sets
      3. Collect labels from each partition; real-HTTP frameworks trigger a
         path-classification pass to distinguish local vs remote
      4. Fallback → ``["unknown"]``
    """
    if runtime_view is None:
        return RequestDispatchDecision(
            labels=["unknown"],
            signals={"unknown": ["no-runtime"]},
        )

    # Discovery pass: collect event frameworks, check for bindToServer
    event_frameworks: set[HttpDispatchFramework] = set()
    has_events = False
    has_bind_to_server = False
    has_rest_assured_module_receiver = False

    for event in runtime_view.iter_events():
        # Scanned on the same traversal as events so a bindToServer inside a
        # helper expansion is seen too.
        if (event.node.call_site.method_name or "") == "bindToServer":
            has_bind_to_server = True
        classification = event.node.http_classification
        if classification is None:
            continue
        if classification.request_role != HttpRequestRole.EVENT:
            continue
        has_events = True
        event_frameworks.add(classification.framework)
        if (
            classification.framework == HttpDispatchFramework.REST_ASSURED
            and classification.receiver_type
            and classification.receiver_type.startswith(
                REST_ASSURED_IN_PROCESS_RECEIVER_PREFIXES
            )
        ):
            has_rest_assured_module_receiver = True

    if not has_events:
        return RequestDispatchDecision(
            labels=["unknown"],
            signals={"unknown": ["no-events"]},
        )

    # Partition frameworks into in-process vs real-HTTP
    in_process_frameworks: set[HttpDispatchFramework] = set()
    real_http_frameworks: set[HttpDispatchFramework] = set()

    for fw in event_frameworks:
        if fw in IN_PROCESS_DISPATCH_FRAMEWORKS:
            in_process_frameworks.add(fw)
        elif fw == HttpDispatchFramework.REST_ASSURED:
            if has_rest_assured_module_receiver:
                in_process_frameworks.add(fw)
            else:
                real_http_frameworks.add(fw)
        elif fw == MODAL_DISPATCH_FRAMEWORK:
            if has_bind_to_server:
                real_http_frameworks.add(fw)
            else:
                in_process_frameworks.add(fw)
        elif fw in REAL_HTTP_DISPATCH_FRAMEWORKS:
            real_http_frameworks.add(fw)

    # Collect labels
    labels: list[str] = []
    signals: dict[str, list[str]] = {}

    # In-process labels
    if in_process_frameworks:
        in_process_signals: list[str] = []
        for fw in sorted(in_process_frameworks):
            if fw in IN_PROCESS_DISPATCH_FRAMEWORKS:
                in_process_signals.append("mockmvc-in-process")
            elif fw == HttpDispatchFramework.REST_ASSURED:
                in_process_signals.append("rest-assured-module-in-process")
            elif fw == MODAL_DISPATCH_FRAMEWORK:
                in_process_signals.append("webtestclient-mock-mode")
        labels.append("in-process")
        signals["in-process"] = in_process_signals

    # Real-HTTP labels — classify paths
    if real_http_frameworks:
        local_count = 0
        external_count = 0
        unresolved_count = 0

        for event in runtime_view.iter_events():
            classification = event.node.http_classification
            if classification is None:
                continue
            if classification.request_role != HttpRequestRole.EVENT:
                continue
            if classification.framework not in real_http_frameworks:
                continue

            target_kind = classify_request_target(
                classification.path, bare_token_is_local=False
            )
            if target_kind == "local":
                local_count += 1
            elif target_kind == "external":
                external_count += 1
            else:
                unresolved_count += 1

        if external_count > 0:
            labels.append("remote-network")
            signals["remote-network"] = ["real-http-remote"]
        if local_count > 0:
            labels.append("local-network")
            signals["local-network"] = ["real-http-local"]

        if not labels:
            labels.append("unknown")
            signals["unknown"] = ["real-http-unresolved"]

        return RequestDispatchDecision(
            labels=labels,
            local_request_count=local_count,
            external_request_count=external_count,
            unresolved_request_count=unresolved_count,
            signals=signals,
        )

    # No recognized real-HTTP framework (but might have in-process labels already)
    if labels:
        return RequestDispatchDecision(
            labels=labels,
            signals=signals,
        )

    return RequestDispatchDecision(
        labels=["unknown"],
        signals={"unknown": ["unrecognized-framework"]},
    )


def classify_request_dispatch(
    runtime_view: TestRuntimeView | None = None,
) -> list[str]:
    """Classify request dispatch and return only the labels."""
    result = analyze_request_dispatch(runtime_view=runtime_view)
    return result.labels
