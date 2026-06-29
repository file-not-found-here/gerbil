from __future__ import annotations

import json
from pathlib import Path

from gerbil.analysis.schema import CrudOperation
from gerbil.statistics.records import project_project
from gerbil.statistics.runner import compute_all_statistics, write_statistics
from tests.statistics_builders import (
    api_test,
    endpoint_entry,
    endpoint_parameter_entry,
    non_api_test,
    project,
    resolved_sequence_summary,
    resource_crud_entry,
    resource_crud_summary,
)

_EXPECTED_STEMS = {
    "assertion_verification_distribution",
    "assertion_clustering_distribution",
    "auth_handling_distribution",
    "dependency_strategy_distribution",
    "test_metric_comparison",
    "testing_framework_distribution",
    "http_dispatch_framework_distribution",
    "http_dispatch_framework_event_distribution",
    "request_dispatch_distribution",
    "http_behavior_location",
    "http_test_sequence_distribution",
    "crud_combination_distribution",
    "verb_combination_distribution",
    "endpoint_distribution",
    "endpoint_outcome_distribution",
    "saint_comparison_distribution",
    "production_resource_sequence_distribution",
    "parameter_exercise_distribution",
    "parameterized_test_distribution",
    "verification_response_role_distribution",
    "request_construction_distribution",
    "resource_interaction_distribution",
    "state_condition_distribution",
    "test_scope_distribution",
    "project_composition",
}


def _records() -> list:
    # Project A: endpoints + API tests, with an API test that resolved an event to
    # an endpoint and method (passes the coverage gate).
    project_a = project_project(
        project(
            dataset_name="a-both",
            tests=[
                api_test(
                    dispatch_labels=["in-process"],
                    dependency_labels=["mocked"],
                    sequence_summary=resolved_sequence_summary(),
                ),
                non_api_test(),
            ],
            endpoints=[endpoint_entry(covering_test_count=2)],
            endpoint_parameters=[
                endpoint_parameter_entry(
                    route_covering_test_count=1, optional_exercise_rate=0.5
                )
            ],
            resource_crud=resource_crud_summary(
                [
                    resource_crud_entry(
                        resource_key="items",
                        available=[CrudOperation.CREATE, CrudOperation.READ],
                        exercised=[CrudOperation.READ],
                    )
                ]
            ),
        )
    )
    # Project B: endpoints and tests, but none are API tests (fails the gate).
    project_b = project_project(
        project(
            dataset_name="b-endpoints-no-api",
            tests=[non_api_test(), non_api_test()],
            endpoints=[
                endpoint_entry(covering_test_count=0),
                endpoint_entry(covering_test_count=0),
            ],
            endpoint_parameters=[
                endpoint_parameter_entry(route_covering_test_count=0),
                endpoint_parameter_entry(route_covering_test_count=0),
            ],
            resource_crud=resource_crud_summary(
                [
                    resource_crud_entry(
                        resource_key="orders",
                        available=[CrudOperation.READ],
                        exercised=[],
                    )
                ]
            ),
        )
    )
    # Project C: API tests but no endpoints (fails the gate).
    project_c = project_project(
        project(
            dataset_name="c-api-no-endpoints",
            tests=[api_test(dispatch_labels=["local-network"])],
        )
    )
    return [project_a, project_b, project_c]


def test_compute_all_statistics_returns_every_payload() -> None:
    statistics = compute_all_statistics(_records())

    assert set(statistics) == _EXPECTED_STEMS
    assert all(isinstance(payload, dict) for payload in statistics.values())
    # Test-side stats see every test across A+B+C (2 + 2 + 1 = 5).
    assert statistics["test_metric_comparison"]["test_counts"]["total"] == 5
    assert (
        statistics["dependency_strategy_distribution"]["strategy_split"]["mocked"][
            "test_count"
        ]
        == 1
    )
    composition = statistics["project_composition"]
    assert composition["project_count"] == 3
    assert composition["quadrants"]["endpoints_and_api_tests"]["project_count"] == 1
    # One test class per project; no test declares request interactions.
    assert statistics["testing_framework_distribution"]["test_class_count"] == 3
    assert statistics["http_dispatch_framework_distribution"]["call_site_count"] == 0
    assert statistics["http_dispatch_framework_event_distribution"]["event_count"] == 0


def test_coverage_family_stats_require_resolved_event() -> None:
    statistics = compute_all_statistics(_records())

    # endpoint_universe spans every project with endpoints (A: 1, B: 2).
    endpoints = statistics["endpoint_distribution"]
    assert endpoints["endpoint_universe"]["endpoint_count"] == 3
    # endpoint_coverage is gated to A: endpoints + API tests + a resolved event.
    assert endpoints["endpoint_coverage"]["endpoint_count"] == 1
    # all_universe scores coverage over every project's endpoints, ungated (3).
    assert endpoints["all_universe"]["scope"] == "all_projects"
    assert endpoints["all_universe"]["endpoint_count"] == 3

    # parameter_exercise counts only A's endpoint-parameter entries.
    parameters = statistics["parameter_exercise_distribution"]
    assert parameters["endpoint_count"] == 1
    # all_universe widens the parameter denominator to every project (A: 1, B: 2).
    assert parameters["all_universe"]["endpoint_count"] == 3

    # saint_comparison scores the full ungated universe (3 endpoints); with no
    # SAINT context-path prefix present, the stripped view mirrors the baseline.
    saint = statistics["saint_comparison_distribution"]
    assert saint["scope"] == "saint_comparison"
    assert saint["baseline"]["endpoint_count"] == 3
    assert saint["context_path_stripped"]["endpoint_count"] == 3
    assert saint["context_path_stripped"]["coverage"] == saint["baseline"]["coverage"]

    # resource_interaction's production-resource coverage shares the resolved-event
    # gate; A passes it, so its one resource is counted. The test-side lifecycle
    # distribution stays on the broader gate (A contributes no pairs here).
    resource = statistics["resource_interaction_distribution"]
    assert resource["resource_count"] == 1
    assert resource["lifecycle_label_distribution"]["pair_count"] == 0


def test_endpoint_parameter_coverage_excludes_projects_without_resolved_events() -> (
    None
):
    # X resolves an endpoint+method event; Y has endpoints and an API test but no
    # event ever resolves to an endpoint and method (e.g. an unsupported client).
    project_x = project_project(
        project(
            dataset_name="x-resolved",
            tests=[api_test(sequence_summary=resolved_sequence_summary())],
            endpoints=[endpoint_entry(covering_test_count=1)],
            endpoint_parameters=[endpoint_parameter_entry(route_covering_test_count=1)],
            resource_crud=resource_crud_summary(
                [
                    resource_crud_entry(
                        resource_key="items",
                        available=[CrudOperation.READ],
                        exercised=[CrudOperation.READ],
                    )
                ]
            ),
        )
    )
    project_y = project_project(
        project(
            dataset_name="y-unresolved",
            tests=[api_test()],
            endpoints=[
                endpoint_entry(covering_test_count=0),
                endpoint_entry(covering_test_count=0),
            ],
            endpoint_parameters=[
                endpoint_parameter_entry(route_covering_test_count=0),
                endpoint_parameter_entry(route_covering_test_count=0),
            ],
            resource_crud=resource_crud_summary(
                [
                    resource_crud_entry(
                        resource_key="orders",
                        available=[CrudOperation.READ],
                        exercised=[],
                    )
                ]
            ),
        )
    )
    statistics = compute_all_statistics([project_x, project_y])

    endpoints = statistics["endpoint_distribution"]
    # endpoint_universe still spans both projects (X: 1, Y: 2).
    assert endpoints["endpoint_universe"]["endpoint_count"] == 3
    # Coverage stats keep only X, where an event resolved to an endpoint+method.
    assert endpoints["endpoint_coverage"]["endpoint_count"] == 1
    assert statistics["endpoint_outcome_distribution"]["endpoint_count"] == 1
    assert statistics["parameter_exercise_distribution"]["endpoint_count"] == 1
    # Y's "orders" resource can never be matched to a test (no dispatch resolves),
    # so production-resource coverage drops it and counts only X's resource.
    assert statistics["resource_interaction_distribution"]["resource_count"] == 1


def test_test_side_stats_see_every_project_regardless_of_the_gate() -> None:
    statistics = compute_all_statistics(_records())

    # A+B+C: 5 tests total, 2 of them API tests (A and C).
    assert statistics["test_metric_comparison"]["test_counts"]["total"] == 5
    assert statistics["test_metric_comparison"]["test_counts"]["api"] == 2
    dispatch = statistics["request_dispatch_distribution"]
    assert set(dispatch["label_split"]) == {"in-process", "local-network"}
    assert statistics["http_behavior_location"]["api_test_count"] == 2
    auth = statistics["auth_handling_distribution"]
    assert auth["scope"] == "api_tests"
    assert auth["test_count"] == 2


def test_compute_all_statistics_has_all_keys_even_when_empty() -> None:
    statistics = compute_all_statistics([])

    assert set(statistics) == _EXPECTED_STEMS
    endpoints = statistics["endpoint_distribution"]
    assert endpoints["endpoint_universe"]["endpoint_count"] == 0
    assert endpoints["endpoint_coverage"]["endpoint_count"] == 0


def test_write_statistics_writes_sorted_named_files(tmp_path: Path) -> None:
    statistics = compute_all_statistics(_records())
    output_dir = tmp_path / "nested" / "stats"

    written = write_statistics(statistics, output_dir)

    # Files are written and returned in stem-sorted order.
    assert [path.name for path in written] == [
        "assertion_clustering_distribution.json",
        "assertion_verification_distribution.json",
        "auth_handling_distribution.json",
        "crud_combination_distribution.json",
        "dependency_strategy_distribution.json",
        "endpoint_distribution.json",
        "endpoint_outcome_distribution.json",
        "http_behavior_location.json",
        "http_dispatch_framework_distribution.json",
        "http_dispatch_framework_event_distribution.json",
        "http_test_sequence_distribution.json",
        "parameter_exercise_distribution.json",
        "parameterized_test_distribution.json",
        "production_resource_sequence_distribution.json",
        "project_composition.json",
        "request_construction_distribution.json",
        "request_dispatch_distribution.json",
        "resource_interaction_distribution.json",
        "saint_comparison_distribution.json",
        "state_condition_distribution.json",
        "test_metric_comparison.json",
        "test_scope_distribution.json",
        "testing_framework_distribution.json",
        "verb_combination_distribution.json",
        "verification_response_role_distribution.json",
    ]
    assert output_dir.is_dir()
    for path in written:
        assert path.is_file()
        # Each file is valid JSON ending in a trailing newline.
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n")
        json.loads(text)


def test_write_statistics_empty_mapping_writes_nothing(tmp_path: Path) -> None:
    output_dir = tmp_path / "stats"

    written = write_statistics({}, output_dir)

    assert written == []
    assert output_dir.is_dir()
    assert list(output_dir.iterdir()) == []
