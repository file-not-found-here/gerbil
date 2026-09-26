from __future__ import annotations

import pytest

from gerbil.analysis.schema import ProjectAnalysis

pytestmark = pytest.mark.integration


def _all_resource_sequences(
    project_analysis: ProjectAnalysis,
) -> list[dict[str, object]]:
    return [
        seq.model_dump(mode="json")
        for test_class in project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
        for seq in test_method.http.resource_interaction_sequences
    ]


def test_resource_interaction_sequences_have_expected_schema_across_fixtures(
    spring_data_rest_project_analysis: ProjectAnalysis,
    spring_boot_admin_project_analysis: ProjectAnalysis,
    marquez_project_analysis: ProjectAnalysis,
    state_interaction_smoke_project_analysis: ProjectAnalysis,
) -> None:
    sequences = [
        *_all_resource_sequences(spring_data_rest_project_analysis),
        *_all_resource_sequences(spring_boot_admin_project_analysis),
        *_all_resource_sequences(marquez_project_analysis),
        *_all_resource_sequences(state_interaction_smoke_project_analysis),
    ]

    for seq in sequences:
        assert "resource_key" in seq
        assert isinstance(seq["resource_key"], str)
        assert "steps" in seq
        assert isinstance(seq["steps"], list)
        for step in seq["steps"]:
            assert "http_method" in step
            assert "path" in step
            assert "normalized_path" in step
            assert "event_order" in step
            assert "phase" in step


def test_resource_interaction_sequences_steps_are_ordered(
    spring_data_rest_project_analysis: ProjectAnalysis,
    spring_boot_admin_project_analysis: ProjectAnalysis,
    marquez_project_analysis: ProjectAnalysis,
    state_interaction_smoke_project_analysis: ProjectAnalysis,
) -> None:
    sequences = [
        *_all_resource_sequences(spring_data_rest_project_analysis),
        *_all_resource_sequences(spring_boot_admin_project_analysis),
        *_all_resource_sequences(marquez_project_analysis),
        *_all_resource_sequences(state_interaction_smoke_project_analysis),
    ]

    for seq in sequences:
        steps = seq["steps"]
        assert isinstance(steps, list)
        if len(steps) > 1:
            orders = [step["event_order"] for step in steps]
            assert orders == sorted(
                orders
            ), f"Steps not ordered for resource_key={seq['resource_key']}"
