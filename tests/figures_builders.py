"""Statistics-payload builders for figures tests, derived from the real
statistics pipeline so payload shapes always match the JSON outputs."""

from __future__ import annotations

import json
from typing import Any

from gerbil.analysis.schema import (
    AssertionSummary,
    CallSiteOriginKind,
    CrudLifecycleLabel,
    CrudOperation,
    HttpDispatchFramework,
    HttpRequestRole,
    LifecyclePhase,
    PreconditionType,
    ResourceInteractionSequence,
    StateObservationMedium,
    StatusCodeDistribution,
    TestingFramework,
)
from gerbil.statistics.records import project_project
from gerbil.statistics.runner import compute_all_statistics
from tests.statistics_builders import (
    api_test,
    body_param,
    class_analysis,
    endpoint_entry,
    endpoint_parameter_entry,
    fixture,
    non_api_test,
    postcondition,
    precondition,
    project,
    query_param,
    request_interaction,
    resource_crud_entry,
    resource_crud_summary,
    verification_interaction,
)


def _round_trip(statistics: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Mirror the on-disk JSON round-trip the figures loader performs."""
    return json.loads(json.dumps(statistics))


def _rich_api_test(**overrides: Any) -> Any:
    defaults: dict[str, Any] = dict(
        expanded_ncloc=30,
        expanded_cc=4,
        helper_method_count=2,
        test_helper_method_count=1,
        objects_created=3,
        dispatch_labels=["in-process"],
        dependency_labels=["mocked"],
        auth_handling_label="test-token",
        request_interactions=[
            request_interaction(
                CallSiteOriginKind.TEST_METHOD, HttpRequestRole.BUILDER
            ),
            request_interaction(
                CallSiteOriginKind.TEST_METHOD,
                HttpRequestRole.EVENT,
                http_method="POST",
            ),
            request_interaction(
                CallSiteOriginKind.FIXTURE,
                HttpRequestRole.EVENT,
                framework=HttpDispatchFramework.REST_ASSURED,
            ),
        ],
        verification_interactions=[
            verification_interaction(CallSiteOriginKind.TEST_METHOD)
        ],
        fixtures=[fixture(LifecyclePhase.SETUP), fixture(LifecyclePhase.TEARDOWN)],
        status_distribution=StatusCodeDistribution(range_2xx=1, range_4xx=1),
        status_code_counts={"200": 1, "404": 1},
        assertion_summary=AssertionSummary(
            status_count=2, body_count=1, general_count=1
        ),
        oracle_type_label="example-based",
        resource_sequences=[
            ResourceInteractionSequence(
                resource_key="items",
                lifecycle_label=CrudLifecycleLabel.CREATE_VERIFY,
                has_read_after_write=True,
            )
        ],
    )
    defaults.update(overrides)
    return api_test(**defaults)


def dev_statistics() -> dict[str, dict[str, Any]]:
    """Payloads over a human-corpus-like project mix (all quadrants populated)."""
    records = [
        project_project(
            project(
                dataset_name="dev-both",
                test_classes=[
                    class_analysis(
                        qualified_class_name="ItemsIT",
                        testing_frameworks=[
                            TestingFramework.JUNIT5,
                            TestingFramework.REST_ASSURED,
                        ],
                        tests=[
                            _rich_api_test(),
                            _rich_api_test(
                                dispatch_labels=["local-network"],
                                dependency_labels=["containerized"],
                                auth_handling_label="none",
                                oracle_type_label="implicit",
                                has_exception_assertion=True,
                                preconditions=[
                                    precondition(PreconditionType.DB_SEEDING)
                                ],
                                postconditions=[
                                    postcondition(StateObservationMedium.DB)
                                ],
                            ),
                        ],
                    ),
                    class_analysis(
                        qualified_class_name="ItemsUnitTest",
                        testing_frameworks=[
                            TestingFramework.JUNIT4,
                            TestingFramework.MOCKITO,
                        ],
                        tests=[
                            non_api_test(expanded_ncloc=8),
                            non_api_test(
                                is_controller_unit_test=True, expanded_ncloc=12
                            ),
                        ],
                    ),
                ],
                endpoints=[
                    endpoint_entry(
                        covering_test_count=2,
                        path_template="/api/items/{id}",
                        parameters=[query_param("page", required=True), body_param()],
                    ),
                    endpoint_entry(covering_test_count=5, path_template="/a/b/c/d"),
                    endpoint_entry(covering_test_count=0, path_template="/api/health"),
                ],
                endpoint_parameters=[
                    endpoint_parameter_entry(
                        route_covering_test_count=2,
                        exercise_rate=1.0,
                        required_exercise_rate=1.0,
                        optional_exercise_rate=0.5,
                    ),
                    endpoint_parameter_entry(route_covering_test_count=0),
                ],
                resource_crud=resource_crud_summary(
                    [
                        resource_crud_entry(
                            resource_key="items",
                            available=[CrudOperation.CREATE, CrudOperation.READ],
                            exercised=[CrudOperation.READ],
                        ),
                        resource_crud_entry(
                            resource_key="orders",
                            available=[CrudOperation.READ],
                        ),
                    ]
                ),
            )
        ),
        project_project(
            project(
                dataset_name="dev-endpoints-only",
                tests=[non_api_test()],
                endpoints=[endpoint_entry(covering_test_count=0)],
            )
        ),
        project_project(
            project(
                dataset_name="dev-api-only",
                tests=[_rich_api_test(dispatch_labels=["unknown"])],
            )
        ),
    ]
    return _round_trip(compute_all_statistics(records))


def tool_statistics() -> dict[str, dict[str, Any]]:
    """Payloads over a tool-generated corpus: API tests only, REST Assured only."""
    records = [
        project_project(
            project(
                dataset_name="tool-service",
                test_classes=[
                    class_analysis(
                        qualified_class_name="GeneratedIT",
                        testing_frameworks=[TestingFramework.REST_ASSURED],
                        tests=[
                            _rich_api_test(
                                dispatch_labels=["local-network"],
                                dependency_labels=[],
                                auth_handling_label="none",
                                request_interactions=[
                                    request_interaction(
                                        CallSiteOriginKind.TEST_METHOD,
                                        HttpRequestRole.EVENT,
                                        framework=HttpDispatchFramework.REST_ASSURED,
                                    )
                                ],
                            )
                        ],
                    )
                ],
                endpoints=[
                    endpoint_entry(covering_test_count=1, path_template="/api/items")
                ],
                endpoint_parameters=[
                    endpoint_parameter_entry(
                        route_covering_test_count=1, exercise_rate=1.0
                    )
                ],
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
    ]
    return _round_trip(compute_all_statistics(records))
