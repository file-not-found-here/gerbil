"""Detect controller unit tests: non-API tests that invoke an endpoint handler
method directly in-process, and summarize which endpoints they target."""

from __future__ import annotations

from dataclasses import dataclass

from cldk.analysis.java import JavaAnalysis

from gerbil.analysis.runtime import TestRuntimeView
from gerbil.analysis.schema import (
    ApplicationEndpoint,
    ControllerHandlerTarget,
    ControllerUnitTestEndpointEntry,
    ControllerUnitTestSummary,
    TestClassAnalysis,
    TestMethodReference,
)
from gerbil.analysis.shared.caching import get_receiver_hierarchy
from gerbil.analysis.shared.receiver_resolution import RuntimeReceiverResolver


@dataclass(frozen=True)
class EndpointHandlerIndex:
    """Lookup structures for matching call sites to endpoint handler methods.

    ``handler_signatures`` is a cheap pre-filter (a call site whose callee
    signature is absent here cannot be a handler invocation); ``handler_keys``
    pins the match to the declaring class once the receiver hierarchy is known.
    """

    handler_keys: frozenset[tuple[str, str]]
    handler_signatures: frozenset[str]

    def is_empty(self) -> bool:
        return not self.handler_keys


def build_endpoint_handler_index(
    application_endpoints: list[ApplicationEndpoint],
) -> EndpointHandlerIndex:
    handler_keys: set[tuple[str, str]] = set()
    handler_signatures: set[str] = set()
    for endpoint in application_endpoints:
        declaring_class_name = (endpoint.declaring_class_name or "").strip()
        declaring_method_signature = (endpoint.declaring_method_signature or "").strip()
        if not declaring_class_name or not declaring_method_signature:
            continue
        handler_keys.add((declaring_class_name, declaring_method_signature))
        handler_signatures.add(declaring_method_signature)
    return EndpointHandlerIndex(
        handler_keys=frozenset(handler_keys),
        handler_signatures=frozenset(handler_signatures),
    )


def detect_controller_unit_test_targets(
    *,
    runtime_view: TestRuntimeView,
    handler_index: EndpointHandlerIndex,
    receiver_resolver: RuntimeReceiverResolver,
    analysis: JavaAnalysis,
) -> list[ControllerHandlerTarget]:
    """Return the endpoint handler methods this test invokes directly.

    Scoped to TEST-phase events (the test method body plus its expanded helper
    call tree): a handler call only in a fixture is state setup, not the test
    exercising the endpoint. ``declaring_method_signature`` and the call site's
    ``callee_signature`` share CLDK's signature namespace, so they compare
    directly; the receiver's resolution order (superclasses + interfaces) is
    walked so a handler inherited or declared on a supertype still matches.
    """

    if handler_index.is_empty():
        return []

    matched_handlers: set[tuple[str, str]] = set()
    for event in runtime_view.test_events():
        call_site = event.call_site
        if call_site.is_constructor_call:
            continue
        callee_signature = (call_site.callee_signature or "").strip()
        if callee_signature not in handler_index.handler_signatures:
            continue

        receiver_type = receiver_resolver.resolve_for_event(
            event.owner, call_site
        ).receiver_type
        if not receiver_type:
            continue

        for candidate_class in get_receiver_hierarchy(receiver_type, analysis):
            handler_key = (candidate_class, callee_signature)
            if handler_key in handler_index.handler_keys:
                matched_handlers.add(handler_key)
                break

    return [
        ControllerHandlerTarget(
            declaring_class_name=declaring_class_name,
            declaring_method_signature=declaring_method_signature,
        )
        for declaring_class_name, declaring_method_signature in sorted(matched_handlers)
    ]


def build_controller_unit_test_summary(
    application_endpoints: list[ApplicationEndpoint],
    test_class_analyses: list[TestClassAnalysis],
) -> ControllerUnitTestSummary:
    endpoint_indices_by_handler: dict[tuple[str, str], list[int]] = {}
    for index, endpoint in enumerate(application_endpoints):
        declaring_class_name = (endpoint.declaring_class_name or "").strip()
        declaring_method_signature = (endpoint.declaring_method_signature or "").strip()
        if not declaring_class_name or not declaring_method_signature:
            continue
        endpoint_indices_by_handler.setdefault(
            (declaring_class_name, declaring_method_signature), []
        ).append(index)

    exercising_tests_by_endpoint_index: dict[int, set[tuple[str, str]]] = {}
    controller_unit_test_count = 0
    for test_class_analysis in test_class_analyses:
        for test_method_analysis in test_class_analysis.test_method_analyses:
            if not test_method_analysis.is_controller_unit_test:
                continue
            controller_unit_test_count += 1
            test_method_reference = (
                test_method_analysis.identity.defining_class_name,
                test_method_analysis.identity.method_signature,
            )
            for target in test_method_analysis.controller_unit_test_targets:
                handler_key = (
                    target.declaring_class_name,
                    target.declaring_method_signature,
                )
                for endpoint_index in endpoint_indices_by_handler.get(handler_key, []):
                    exercising_tests_by_endpoint_index.setdefault(
                        endpoint_index, set()
                    ).add(test_method_reference)

    endpoint_entries: list[ControllerUnitTestEndpointEntry] = []
    for endpoint_index in sorted(exercising_tests_by_endpoint_index):
        test_method_refs = sorted(exercising_tests_by_endpoint_index[endpoint_index])
        endpoint_entries.append(
            ControllerUnitTestEndpointEntry(
                endpoint=application_endpoints[endpoint_index],
                exercising_test_methods=[
                    TestMethodReference(
                        qualified_class_name=qualified_class_name,
                        method_signature=method_signature,
                    )
                    for qualified_class_name, method_signature in test_method_refs
                ],
                exercising_test_method_count=len(test_method_refs),
            )
        )

    return ControllerUnitTestSummary(
        controller_unit_test_count=controller_unit_test_count,
        targeted_endpoint_count=len(endpoint_entries),
        endpoints=endpoint_entries,
    )
