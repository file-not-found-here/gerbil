from __future__ import annotations

import pytest

from gerbil.analysis.properties.assertion.surface import build_assertion_summary
from gerbil.analysis.properties.assertion.status_distribution import (
    build_status_code_distribution,
)
from gerbil.analysis.schema.types import (
    AssertionSummary,
    StatusCodeDistribution,
)
from gerbil.analysis.properties.assertion.oracle import classify_oracle_type
from gerbil.analysis.properties.auth_analysis import classify_auth_handling
from gerbil.analysis.properties.dependency_strategy import (
    classify_dependency_strategy,
)
from gerbil.analysis.properties.assertion.failure import classify_failure_scenarios
from gerbil.analysis.properties.precondition_analysis import (
    analyze_preconditions,
)
from gerbil.analysis.properties.sequence_analysis import build_api_call_sequence
from gerbil.analysis.runtime import TestRuntimeView
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
)


def test_assertion_classification_with_none_method_details() -> None:
    runtime_view = TestRuntimeView()
    assertion_surface = build_assertion_summary(
        runtime_view=runtime_view,
    )
    oracle_type = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=None,
        class_imports=[],
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    assert assertion_surface == AssertionSummary()
    assert oracle_type.label == "implicit"


def test_dependency_strategy_with_missing_details() -> None:
    runtime_view = TestRuntimeView()
    decision = classify_dependency_strategy(
        class_details=None,
        method_details=None,
        class_annotations=[],
        runtime_view=runtime_view,
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    assert decision.labels == []


def test_auth_handling_with_none_method_details() -> None:
    runtime_view = TestRuntimeView()
    decision = classify_auth_handling(
        class_annotations=[],
        method_annotations=[],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    assert decision.label == "none"
    assert decision.signals == {}


def test_status_code_distribution_with_none_method_details() -> None:
    runtime_view = TestRuntimeView()
    distribution = build_status_code_distribution(
        runtime_view=runtime_view,
    )

    assert distribution == StatusCodeDistribution()


def test_failure_scenarios_with_none_method_details() -> None:
    runtime_view = TestRuntimeView()
    signals = classify_failure_scenarios(
        runtime_view=runtime_view,
    )

    assert signals.has_client_error_assertion is False
    assert signals.has_server_error_assertion is False
    assert signals.has_exception_assertion is False


def test_precondition_analysis_with_no_annotations() -> None:
    runtime_view = TestRuntimeView()
    summary = analyze_preconditions(
        class_annotations=[],
        method_annotations=[],
        class_annotation_imports_by_class={},
        method_imports=[],
        runtime_view=runtime_view,
        analysis=None,
        receiver_resolver=build_runtime_receiver_resolver_for_testing(runtime_view),
    )

    assert summary.preconditions == []


def test_sequence_builder_with_none_method_details() -> None:
    sequence = build_api_call_sequence(
        runtime_view=TestRuntimeView(entries=[]),
    )

    assert sequence == []


def test_oracle_classification_requires_runtime_receiver_resolver() -> None:
    with pytest.raises(TypeError, match="receiver_resolver"):
        classify_oracle_type(  # type: ignore[call-arg]
            runtime_view=TestRuntimeView(),
            method_details=None,
            class_imports=[],
        )


def test_dependency_classification_requires_runtime_receiver_resolver() -> None:
    with pytest.raises(TypeError, match="receiver_resolver"):
        classify_dependency_strategy(  # type: ignore[call-arg]
            class_details=None,
            method_details=None,
            class_annotations=[],
            runtime_view=TestRuntimeView(),
            class_annotation_imports_by_class={},
            method_imports=[],
            declaring_class_imports=[],
            analysis=None,
        )


def test_auth_classification_requires_runtime_receiver_resolver() -> None:
    with pytest.raises(TypeError, match="receiver_resolver"):
        classify_auth_handling(  # type: ignore[call-arg]
            class_annotations=[],
            method_annotations=[],
            class_annotation_imports_by_class={},
            method_imports=[],
            runtime_view=TestRuntimeView(),
        )
