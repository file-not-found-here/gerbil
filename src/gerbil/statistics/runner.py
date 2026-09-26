"""Orchestrates statistics computation over loaded records and writes one
JSON file per statistics type."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from gerbil.statistics import (
    api_test_count_correlation as api_test_count_correlation_stats,
)
from gerbil.statistics import assertion_clustering as assertion_clustering_stats
from gerbil.statistics import assertion_verification as assertion_verification_stats
from gerbil.statistics import auth_handling as auth_handling_stats
from gerbil.statistics import crud_combinations as crud_combinations_stats
from gerbil.statistics import dependency_strategy as dependency_strategy_stats
from gerbil.statistics import endpoint_outcomes as endpoint_outcomes_stats
from gerbil.statistics import endpoints as endpoints_stats
from gerbil.statistics import frameworks as frameworks_stats
from gerbil.statistics import http_behavior as http_behavior_stats
from gerbil.statistics import http_sequences as http_sequences_stats
from gerbil.statistics import parameter_exercise as parameter_exercise_stats
from gerbil.statistics import parameterization as parameterization_stats
from gerbil.statistics import (
    production_resource_sequences as production_resource_sequences_stats,
)
from gerbil.statistics import project_composition as project_composition_stats
from gerbil.statistics import request_construction as request_construction_stats
from gerbil.statistics import request_dispatch as request_dispatch_stats
from gerbil.statistics import resource_interaction as resource_interaction_stats
from gerbil.statistics import response_roles as response_roles_stats
from gerbil.statistics import saint_comparison as saint_comparison_stats
from gerbil.statistics import state_conditions as state_conditions_stats
from gerbil.statistics import test_metrics as test_metrics_stats
from gerbil.statistics import test_scope as test_scope_stats
from gerbil.statistics import verb_combinations as verb_combinations_stats
from gerbil.statistics.records import (
    ProjectStatsRecord,
    api_test_count,
    has_resolved_endpoint_method_event,
)

# Output file stem -> nothing else; the runner derives "<stem>.json".
TEST_METRIC_COMPARISON = "test_metric_comparison"
REQUEST_DISPATCH_DISTRIBUTION = "request_dispatch_distribution"
HTTP_BEHAVIOR_LOCATION = "http_behavior_location"
HTTP_TEST_SEQUENCE_DISTRIBUTION = "http_test_sequence_distribution"
CRUD_COMBINATION_DISTRIBUTION = "crud_combination_distribution"
VERB_COMBINATION_DISTRIBUTION = "verb_combination_distribution"
ENDPOINT_DISTRIBUTION = "endpoint_distribution"
PARAMETER_EXERCISE_DISTRIBUTION = "parameter_exercise_distribution"
RESOURCE_INTERACTION_DISTRIBUTION = "resource_interaction_distribution"
STATE_CONDITION_DISTRIBUTION = "state_condition_distribution"
PROJECT_COMPOSITION = "project_composition"
ASSERTION_VERIFICATION_DISTRIBUTION = "assertion_verification_distribution"
ASSERTION_CLUSTERING_DISTRIBUTION = "assertion_clustering_distribution"
DEPENDENCY_STRATEGY_DISTRIBUTION = "dependency_strategy_distribution"
AUTH_HANDLING_DISTRIBUTION = "auth_handling_distribution"
TEST_SCOPE_DISTRIBUTION = "test_scope_distribution"
TESTING_FRAMEWORK_DISTRIBUTION = "testing_framework_distribution"
HTTP_DISPATCH_FRAMEWORK_DISTRIBUTION = "http_dispatch_framework_distribution"
HTTP_DISPATCH_FRAMEWORK_EVENT_DISTRIBUTION = (
    "http_dispatch_framework_event_distribution"
)
ENDPOINT_OUTCOME_DISTRIBUTION = "endpoint_outcome_distribution"
SAINT_COMPARISON_DISTRIBUTION = "saint_comparison_distribution"
PRODUCTION_RESOURCE_SEQUENCE_DISTRIBUTION = "production_resource_sequence_distribution"
PARAMETERIZED_TEST_DISTRIBUTION = "parameterized_test_distribution"
VERIFICATION_RESPONSE_ROLE_DISTRIBUTION = "verification_response_role_distribution"
REQUEST_CONSTRUCTION_DISTRIBUTION = "request_construction_distribution"
API_TEST_COUNT_CORRELATION = "api_test_count_correlation"


def compute_all_statistics(
    records: Sequence[ProjectStatsRecord],
    *,
    saint_comparison: bool = False,
    api_test_count_correlation: bool = False,
) -> dict[str, dict[str, Any]]:
    """Compute every statistics payload, keyed by output file stem. The SAINT
    comparison views and the API test count correlation are opt-in."""
    tests = [test for record in records for test in record.tests]
    test_classes = [
        test_class for record in records for test_class in record.test_classes
    ]
    endpoints = [endpoint for record in records for endpoint in record.endpoints]
    endpoint_parameters = [
        entry for record in records for entry in record.endpoint_parameters
    ]
    # Coverage-family stats gate to projects with both extracted endpoints and at
    # least one API test, so a zero inside the gate reads as a coverage gap rather
    # than a project that tests at a non-HTTP layer.
    gated = [
        record for record in records if record.endpoints and api_test_count(record) > 0
    ]
    gated_tests = [test for record in gated for test in record.tests]
    # Endpoint-, parameter-, and production-resource-coverage stats narrow the gate
    # further to projects where at least one API test resolved an event to both an
    # endpoint route and an HTTP method. Without such a resolved event a
    # test->endpoint mapping is impossible (some frameworks never resolve one), so a
    # coverage zero there would reflect that limitation rather than a genuine gap;
    # likewise a production resource in such a project can never be matched to a
    # test and would always read as untested. Excluding these projects keeps the
    # coverage denominators honest.
    coverage_gated = [
        record for record in gated if has_resolved_endpoint_method_event(record)
    ]
    coverage_endpoints = [
        endpoint for record in coverage_gated for endpoint in record.endpoints
    ]
    coverage_endpoint_parameters = [
        entry for record in coverage_gated for entry in record.endpoint_parameters
    ]
    coverage_resources = [
        resource for record in coverage_gated for resource in record.resources
    ]
    # SAINT-comparison-only parameters with deploy-time context-path prefixes
    # stripped; ungated, so they score over the same full endpoint universe.
    saint_comparison_endpoint_parameters = (
        [
            entry
            for record in records
            for entry in record.saint_comparison_endpoint_parameters
        ]
        if saint_comparison
        else None
    )
    statistics: dict[str, dict[str, Any]] = {
        ASSERTION_VERIFICATION_DISTRIBUTION: assertion_verification_stats.compute(
            tests
        ),
        ASSERTION_CLUSTERING_DISTRIBUTION: assertion_clustering_stats.compute(tests),
        AUTH_HANDLING_DISTRIBUTION: auth_handling_stats.compute(tests),
        DEPENDENCY_STRATEGY_DISTRIBUTION: dependency_strategy_stats.compute(tests),
        TESTING_FRAMEWORK_DISTRIBUTION: (
            frameworks_stats.compute_testing_framework_distribution(test_classes)
        ),
        HTTP_DISPATCH_FRAMEWORK_DISTRIBUTION: (
            frameworks_stats.compute_http_dispatch_framework_distribution(tests)
        ),
        HTTP_DISPATCH_FRAMEWORK_EVENT_DISTRIBUTION: (
            frameworks_stats.compute_http_dispatch_framework_event_distribution(tests)
        ),
        TEST_METRIC_COMPARISON: test_metrics_stats.compute(tests),
        REQUEST_DISPATCH_DISTRIBUTION: request_dispatch_stats.compute(tests),
        HTTP_BEHAVIOR_LOCATION: http_behavior_stats.compute(tests),
        HTTP_TEST_SEQUENCE_DISTRIBUTION: http_sequences_stats.compute(tests),
        CRUD_COMBINATION_DISTRIBUTION: crud_combinations_stats.compute(tests),
        VERB_COMBINATION_DISTRIBUTION: verb_combinations_stats.compute(tests),
        ENDPOINT_DISTRIBUTION: endpoints_stats.compute(endpoints, coverage_endpoints),
        ENDPOINT_OUTCOME_DISTRIBUTION: endpoint_outcomes_stats.compute(
            coverage_endpoints
        ),
        PARAMETERIZED_TEST_DISTRIBUTION: parameterization_stats.compute(tests),
        VERIFICATION_RESPONSE_ROLE_DISTRIBUTION: response_roles_stats.compute(tests),
        REQUEST_CONSTRUCTION_DISTRIBUTION: request_construction_stats.compute(tests),
        PARAMETER_EXERCISE_DISTRIBUTION: parameter_exercise_stats.compute(
            coverage_endpoint_parameters,
            endpoint_parameters,
            saint_comparison_endpoint_parameters,
        ),
        RESOURCE_INTERACTION_DISTRIBUTION: resource_interaction_stats.compute(
            gated_tests, coverage_resources
        ),
        STATE_CONDITION_DISTRIBUTION: state_conditions_stats.compute(tests),
        TEST_SCOPE_DISTRIBUTION: test_scope_stats.compute(tests),
        PROJECT_COMPOSITION: project_composition_stats.compute(records),
    }
    if saint_comparison:
        statistics[SAINT_COMPARISON_DISTRIBUTION] = saint_comparison_stats.compute(
            endpoints,
            [
                endpoint
                for record in records
                for endpoint in record.saint_comparison_endpoints
            ],
        )
        # Resource sequences grouped by observed path vs. by production resource
        # key; ungated to score over the same full corpus.
        statistics[PRODUCTION_RESOURCE_SEQUENCE_DISTRIBUTION] = (
            production_resource_sequences_stats.compute(
                [
                    sequence
                    for record in records
                    for sequence in record.observed_resource_sequences
                ],
                [
                    sequence
                    for record in records
                    for sequence in record.production_resource_sequences
                ],
            )
        )
    if api_test_count_correlation:
        # Applies its own scope (>= 10 API tests and >= 1 endpoint) to `records`;
        # only meaningful over the full project corpus.
        statistics[API_TEST_COUNT_CORRELATION] = (
            api_test_count_correlation_stats.compute(records)
        )
    return statistics


def write_statistics(
    statistics: dict[str, dict[str, Any]], output_dir: Path
) -> list[Path]:
    """Write each statistics payload to <output_dir>/<stem>.json, sorted by stem."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for stem in sorted(statistics):
        output_file = output_dir / f"{stem}.json"
        output_file.write_text(
            json.dumps(statistics[stem], indent=2) + "\n", encoding="utf-8"
        )
        written.append(output_file)
    return written
