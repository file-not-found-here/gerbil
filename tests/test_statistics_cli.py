from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from gerbil import cli
from gerbil.analysis.schema import (
    CrudLifecycleLabel,
    CrudOperation,
    EndpointParameterSource,
    LifecyclePhase,
    ProjectAnalysis,
    ResourceInteractionSequence,
)
from gerbil.statistics.loading import discover_gerbil_files, load_project_records
from tests.statistics_builders import (
    api_test,
    body_param,
    endpoint_entry,
    endpoint_parameter_entry,
    fixture,
    non_api_test,
    project,
    query_param,
    resolved_sequence_summary,
    resource_crud_entry,
    resource_crud_summary,
)

_STAT_FILES = (
    "assertion_clustering_distribution.json",
    "assertion_verification_distribution.json",
    "auth_handling_distribution.json",
    "dependency_strategy_distribution.json",
    "test_metric_comparison.json",
    "testing_framework_distribution.json",
    "http_dispatch_framework_distribution.json",
    "http_dispatch_framework_event_distribution.json",
    "request_dispatch_distribution.json",
    "http_behavior_location.json",
    "http_test_sequence_distribution.json",
    "crud_combination_distribution.json",
    "verb_combination_distribution.json",
    "endpoint_distribution.json",
    "endpoint_outcome_distribution.json",
    "saint_comparison_distribution.json",
    "production_resource_sequence_distribution.json",
    "parameter_exercise_distribution.json",
    "parameterized_test_distribution.json",
    "verification_response_role_distribution.json",
    "request_construction_distribution.json",
    "resource_interaction_distribution.json",
    "state_condition_distribution.json",
    "test_scope_distribution.json",
    "project_composition.json",
)


def _write_project(input_root: Path, name: str, analysis: ProjectAnalysis) -> Path:
    project_dir = input_root / name
    project_dir.mkdir(parents=True)
    output_file = project_dir / "gerbil.json"
    output_file.write_text(analysis.model_dump_json(indent=2), encoding="utf-8")
    return output_file


def _sample_projects(input_root: Path) -> None:
    _write_project(
        input_root,
        "service-a",
        project(
            dataset_name="service-a",
            tests=[
                api_test(
                    dispatch_labels=["in-process"],
                    dependency_labels=["mocked", "containerized"],
                    auth_handling_label="mocked",
                    expanded_ncloc=20,
                    test_helper_method_count=2,
                    sequence_summary=resolved_sequence_summary(),
                    fixtures=[
                        fixture(LifecyclePhase.SETUP),
                        fixture(LifecyclePhase.TEARDOWN),
                    ],
                    resource_sequences=[
                        ResourceInteractionSequence(
                            resource_key="items",
                            lifecycle_label=CrudLifecycleLabel.READ_ONLY,
                        )
                    ],
                ),
                non_api_test(is_controller_unit_test=True, expanded_ncloc=8),
            ],
            endpoints=[
                endpoint_entry(
                    covering_test_count=2,
                    path_template="/api/items/{id}",
                    parameters=[query_param("page", required=True), body_param()],
                ),
                endpoint_entry(covering_test_count=0, path_template="/api/health"),
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
            endpoint_parameters=[
                endpoint_parameter_entry(
                    route_covering_test_count=2,
                    optional_exercise_rate=0.5,
                    required_exercise_rate=1.0,
                    optional_exercise_rate_by_source={
                        EndpointParameterSource.QUERY: 0.5
                    },
                ),
                # Untargeted endpoint: excluded from the among_covered distributions.
                endpoint_parameter_entry(
                    route_covering_test_count=0,
                    optional_exercise_rate=None,
                    required_exercise_rate=None,
                ),
            ],
        ),
    )
    _write_project(
        input_root,
        "service-b",
        project(
            dataset_name="service-b",
            tests=[
                api_test(
                    dispatch_labels=["local-network"],
                    dependency_labels=["virtualized"],
                    auth_handling_label="test-token",
                    expanded_ncloc=40,
                    sequence_summary=resolved_sequence_summary(),
                )
            ],
            endpoints=[endpoint_entry(covering_test_count=5, path_template="/a/b/c/d")],
            endpoint_parameters=[
                endpoint_parameter_entry(
                    route_covering_test_count=4,
                    optional_exercise_rate=1.0,
                    optional_exercise_rate_by_source={
                        EndpointParameterSource.QUERY: 1.0
                    },
                )
            ],
        ),
    )


def test_discover_finds_nested_gerbil_outputs(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    _sample_projects(input_root)

    discovered = discover_gerbil_files(input_root)

    assert [path.parent.name for path in discovered] == ["service-a", "service-b"]


def test_load_project_records_reports_corrupt_outputs(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    _sample_projects(input_root)
    (input_root / "broken").mkdir()
    broken = input_root / "broken" / "gerbil.json"
    broken.write_text("{not valid json", encoding="utf-8")

    paths = discover_gerbil_files(input_root)
    records, failures = load_project_records(paths, jobs=1)

    assert {record.dataset_name for record in records} == {"service-a", "service-b"}
    assert [failure.path for failure in failures] == [broken]


def test_statistics_command_writes_one_file_per_type(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "stats"
    _sample_projects(input_root)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "statistics",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["analyses"] == 2
    assert summary["loaded"] == 2
    assert summary["failed"] == 0
    assert summary["tests"] == 3
    assert summary["endpoints"] == 3
    assert summary["endpoint_parameters"] == 3
    assert summary["resources"] == 1

    for file_name in _STAT_FILES:
        assert (output_dir / file_name).is_file()

    # Reported outputs are the stem-sorted statistics files.
    assert [Path(path).name for path in summary["outputs"]] == sorted(_STAT_FILES)

    metrics = json.loads((output_dir / "test_metric_comparison.json").read_text())
    assert metrics["test_counts"] == {
        "total": 3,
        "api": 2,
        "non_api": 1,
        "controller_unit_test": 1,
    }
    assert metrics["comparisons"]["expanded_ncloc"]["api"]["mean"] == 30.0

    assertion_verification = json.loads(
        (output_dir / "assertion_verification_distribution.json").read_text()
    )
    assert assertion_verification["api_test_count"] == 2
    assert assertion_verification["response_surface_combinations"]["total"] == 2
    assert assertion_verification["assertion_targets"]["countable_assertion_count"] == 0

    dependency = json.loads(
        (output_dir / "dependency_strategy_distribution.json").read_text()
    )
    assert dependency["scope"] == "api_tests"
    assert dependency["test_count"] == 2
    assert dependency["strategy_split"]["mocked"]["test_count"] == 1
    assert dependency["strategy_split"]["containerized"]["test_count"] == 1
    assert dependency["strategy_split"]["virtualized"]["test_count"] == 1
    assert dependency["multiple_strategy_tests"]["count"] == 1

    auth = json.loads((output_dir / "auth_handling_distribution.json").read_text())
    assert auth["scope"] == "api_tests"
    assert auth["test_count"] == 2
    assert auth["label_split"]["mocked"]["test_count"] == 1
    assert auth["label_split"]["mocked"]["pct_of_tests"] == 50.0
    assert auth["label_split"]["test-token"]["test_count"] == 1
    assert auth["label_split"]["none"]["test_count"] == 0

    dispatch = json.loads(
        (output_dir / "request_dispatch_distribution.json").read_text()
    )
    assert dispatch["labeled_test_count"] == 2
    assert set(dispatch["label_split"]) == {"in-process", "local-network"}

    endpoints = json.loads((output_dir / "endpoint_distribution.json").read_text())
    universe = endpoints["endpoint_universe"]
    assert universe["scope"] == "projects_with_endpoints"
    assert universe["endpoint_count"] == 3
    coverage = endpoints["endpoint_coverage"]
    assert coverage["scope"] == "endpoints_api_tests_and_resolved_endpoint_events"
    # Both sample projects pass the gate, so all three endpoints are covered.
    assert coverage["endpoint_count"] == 3
    assert coverage["coverage_buckets"]["no_test"]["endpoint_count"] == 1
    assert coverage["coverage_buckets"]["one_to_three"]["endpoint_count"] == 1
    # The /api/items/{id}?page endpoint survives the JSON round-trip with its
    # path/query surface counts intact (enum dict keys preserved on load).
    one_to_three = coverage["coverage_buckets"]["one_to_three"]
    assert one_to_three["path_variable_count"]["mean"] == 1.0
    assert one_to_three["query_variable_count"]["mean"] == 1.0
    assert one_to_three["required_query_variable_count"]["mean"] == 1.0
    assert coverage["coverage_buckets"]["more_than_three"]["endpoint_count"] == 1

    parameters = json.loads(
        (output_dir / "parameter_exercise_distribution.json").read_text()
    )
    assert parameters["scope"] == "endpoints_api_tests_and_resolved_endpoint_events"
    assert parameters["endpoint_count"] == 3
    # Two of the three gated entries carry a covering test.
    assert parameters["coverage"]["count"] == 2
    assert parameters["coverage"]["total"] == 3
    # among_covered drops the untargeted endpoint entirely (rates 0.5 and 1.0).
    among_covered = parameters["among_covered"]
    assert among_covered["endpoint_count"] == 2
    assert among_covered["holistic"]["optional_exercise_rate"]["count"] == 2
    assert among_covered["holistic"]["optional_exercise_rate"]["mean"] == 0.75
    # Per-source query rates survive the enum-keyed JSON round-trip.
    query = among_covered["by_source"]["optional_exercise_rate"]["query"]
    assert query["count"] == 2
    assert query["mean"] == 0.75

    # The body parameter on /api/items/{id} survives the round-trip as a
    # has-body endpoint (1 of 3 endpoints carries a body).
    surface = universe["parameter_surface"]
    assert surface["endpoints_with_body"]["count"] == 1
    assert surface["endpoints_with_body"]["total"] == 3
    assert surface["required_by_source"]["query"]["max"] == 1.0

    # Fixture and helper distributions over API tests (service-a: 1 setup, 1
    # teardown, 2 test-helpers; service-b: none).
    behavior = json.loads((output_dir / "http_behavior_location.json").read_text())
    assert behavior["fixtures"]["setup_method_count"]["max"] == 1.0
    assert behavior["fixtures"]["setup_method_count"]["mean"] == 0.5
    assert behavior["fixtures"]["teardown_method_count"]["max"] == 1.0
    assert behavior["test_helper_method_count"]["max"] == 2.0

    # Resource-interaction stats survive the resource_crud JSON round-trip.
    resource = json.loads(
        (output_dir / "resource_interaction_distribution.json").read_text()
    )
    assert resource["scope"] == "endpoints_and_api_tests"
    assert resource["resource_count"] == 1
    assert resource["tested"]["count"] == 1
    assert resource["tested"]["total"] == 1
    labels = resource["lifecycle_label_distribution"]
    assert labels["pair_count"] == 1
    assert labels["labels"]["read-only"]["count"] == 1
    assert labels["labels"]["read-only"]["pct"] == 100.0
    per_test = resource["per_test"]
    assert per_test["scope"] == "api_tests_with_resource_sequences"
    assert per_test["test_count"] == 1
    assert per_test["labels"]["read-only"]["test_count"] == 1
    assert per_test["labels"]["read-only"]["pct_of_tests"] == 100.0
    assert per_test["has_read_after_write"]["count"] == 0
    assert per_test["has_cleanup_delete"]["count"] == 0
    # items offers create+read, only read is exercised -> completeness 0.5.
    assert resource["exercised_completeness"]["among_tested"]["mean"] == 0.5
    assert resource["per_operation_exercise"]["read"]["rate"] == 1.0
    assert resource["per_operation_exercise"]["create"]["rate"] == 0.0
    assert resource["per_operation_exercise"]["update"]["rate"] is None
    read_only = resource["read_only_when_writable"]
    assert read_only["read_only_tested_count"] == 1
    assert read_only["proportion_of_writable_tested"] == 1.0

    # Both sample projects carry endpoints and API tests, so the composition
    # quadrant holds their combined test corpus (service-a: 2, service-b: 1).
    composition = json.loads((output_dir / "project_composition.json").read_text())
    assert composition["project_count"] == 2
    both = composition["quadrants"]["endpoints_and_api_tests"]
    assert both["project_count"] == 2
    assert both["test_count"] == 3
    assert both["api_test_count"] == 2
    assert composition["quadrants"]["endpoints_no_api_tests"]["project_count"] == 0
    assert composition["quadrants"]["api_tests_no_endpoints"]["project_count"] == 0
    assert composition["quadrants"]["no_endpoints_no_api_tests"]["project_count"] == 0

    # The API-test universe spans both projects; NCLOC is per API test
    # (service-a: 20, service-b: 40).
    universe = composition["api_test_universe"]
    assert universe["project_count"] == 2
    assert universe["api_test_count"] == 2
    assert universe["api_test_class_count"] == 2
    assert universe["api_test_ncloc"]["min"] == 20
    assert universe["api_test_ncloc"]["max"] == 40
    assert universe["api_test_ncloc"]["mean"] == 30
    # Endpoints per API-test project (service-a: 2, service-b: 1).
    assert universe["endpoint_count"] == 3
    assert universe["endpoints_per_project"]["min"] == 1
    assert universe["endpoints_per_project"]["max"] == 2
    assert universe["endpoints_per_project"]["mean"] == 1.5


def test_statistics_command_runs_in_parallel_worker_processes(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "stats"
    _sample_projects(input_root)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "statistics",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "2",
        ],
    )

    assert result.exit_code == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["loaded"] == 2
    assert (output_dir / "http_behavior_location.json").is_file()


def test_statistics_command_exits_nonzero_on_load_failure(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "stats"
    _sample_projects(input_root)
    (input_root / "broken").mkdir()
    (input_root / "broken" / "gerbil.json").write_text("nope", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "statistics",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 1
    summary = json.loads(result.stdout)
    assert summary["failed"] == 1
    # Statistics over the loadable projects are still written.
    assert (output_dir / "endpoint_distribution.json").is_file()


def test_statistics_command_rejects_missing_input_root(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "statistics",
            "--input-root",
            str(tmp_path / "missing"),
            "--output-dir",
            str(tmp_path / "stats"),
        ],
    )

    assert result.exit_code != 0
    assert "input_root does not exist" in result.stderr


def test_statistics_command_rejects_input_root_without_outputs(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "statistics",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(tmp_path / "stats"),
        ],
    )

    assert result.exit_code != 0
    assert "does not contain any gerbil.json outputs" in result.stderr
