"""Failure scenario classification.

Reads ``node.assertion_classification`` set by the assertion classification
pass and produces a ``FailureScenarioSignals`` indicating whether the test
verifies client-error (4xx), server-error (5xx), or exception-throwing
failure paths.
"""

from __future__ import annotations

import re

from cldk.models.java import JImport

from gerbil.analysis.assertion.classification import status_range_from_code
from gerbil.analysis.runtime import TestRuntimeView
from gerbil.analysis.schema import AssertionRole, FailureScenarioSignals
from gerbil.analysis.shared.annotations import (
    annotation_body,
    annotation_matches_expected,
)

_EXPECTED_EXCEPTION_ATTRIBUTE_RE = re.compile(r"\b(expected|expectedExceptions)\s*=")


def _annotation_declares_expected_exception(annotation: str) -> bool:
    """Return True when a ``@Test`` annotation body assigns expected/expectedExceptions."""

    body = annotation_body(annotation)
    if not body:
        return False
    return bool(_EXPECTED_EXCEPTION_ATTRIBUTE_RE.search(body))


def has_expected_exception_annotation(
    method_annotations: list[str] | None,
    class_imports: list[JImport],
) -> bool:
    """Detect JUnit4/TestNG expected-exception declarations on the test method."""

    return any(
        annotation_matches_expected(
            annotation,
            "@Test",
            class_imports=class_imports,
        )
        and _annotation_declares_expected_exception(annotation)
        for annotation in (method_annotations or [])
    )


def classify_failure_scenarios(
    *,
    runtime_view: TestRuntimeView,
    method_annotations: list[str] | None = None,
    class_imports: list[JImport] | None = None,
) -> FailureScenarioSignals:
    has_client_error = False
    has_server_error = False
    has_exception = has_expected_exception_annotation(
        method_annotations,
        class_imports or [],
    )

    for event in runtime_view.iter_events():
        ac = event.node.assertion_classification
        if ac is None or not ac.is_countable:
            continue
        if ac.role == AssertionRole.STATUS:
            status_range = ac.status_range
            if status_range is None and ac.status_code is not None:
                status_range = status_range_from_code(ac.status_code)
            if status_range == "4xx":
                has_client_error = True
            elif status_range == "5xx":
                has_server_error = True
        elif ac.role == AssertionRole.EXCEPTION:
            has_exception = True
        if has_client_error and has_server_error and has_exception:
            break

    return FailureScenarioSignals(
        has_client_error_assertion=has_client_error,
        has_server_error_assertion=has_server_error,
        has_exception_assertion=has_exception,
    )


__all__ = ["classify_failure_scenarios", "has_expected_exception_annotation"]
