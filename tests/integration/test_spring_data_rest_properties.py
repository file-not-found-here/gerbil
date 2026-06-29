from __future__ import annotations

import pytest

from gerbil.analysis.schema import (
    AuthHandling,
    HttpDispatchFramework,
    RequestDispatch,
    LifecyclePhase,
    ProjectAnalysis,
    TestingFramework as AnalysisFramework,
)
from tests.integration.conftest import collect_dispatch_frameworks

pytestmark = pytest.mark.integration

_MIN_SPRING_DATA_REST_HTTP_CALLS = 1
_MIN_SPRING_DATA_REST_INHERITED_SETUP_CLASSES = 5
_MIN_SPRING_DATA_REST_TEST_METHODS = 100
_MIN_SPRING_DATA_REST_TEST_CLASSES = 30
_MIN_SPRING_DATA_REST_API_TESTS = 8
_MIN_SPRING_DATA_REST_ASSERTIONS = 300


def _all_frameworks(project_analysis: ProjectAnalysis) -> set[AnalysisFramework]:
    return {
        framework
        for test_class in project_analysis.test_class_analyses
        for framework in test_class.testing_frameworks
    }


def test_spring_data_rest_detects_mockmvc_frameworks(
    spring_data_rest_project_analysis: ProjectAnalysis,
) -> None:
    all_frameworks = _all_frameworks(spring_data_rest_project_analysis)
    assert AnalysisFramework.JUNIT5 in all_frameworks
    assert AnalysisFramework.SPRING_TEST in all_frameworks

    dispatch = collect_dispatch_frameworks(spring_data_rest_project_analysis)
    assert HttpDispatchFramework.MOCKMVC in dispatch


def test_spring_data_rest_extracts_http_calls(
    spring_data_rest_project_analysis: ProjectAnalysis,
) -> None:
    total_http_calls = sum(
        1
        for test_class in spring_data_rest_project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
        for interaction in test_method.http.request_interactions
        if interaction.http_call is not None
    )
    assert total_http_calls >= _MIN_SPRING_DATA_REST_HTTP_CALLS


def test_spring_data_rest_detects_status_code_assertions(
    spring_data_rest_project_analysis: ProjectAnalysis,
) -> None:
    has_any_status_assertion = False
    for test_class in spring_data_rest_project_analysis.test_class_analyses:
        for test_method in test_class.test_method_analyses:
            fs = test_method.assertions.failure_scenarios
            if fs.has_client_error_assertion or fs.has_server_error_assertion:
                has_any_status_assertion = True
            if test_method.assertions.summary.status_count > 0:
                has_any_status_assertion = True

    assert has_any_status_assertion


def test_spring_data_rest_detects_fixture_inheritance(
    spring_data_rest_project_analysis: ProjectAnalysis,
) -> None:
    classes_with_inherited_setup = [
        test_class
        for test_class in spring_data_rest_project_analysis.test_class_analyses
        if any(
            fixture.defining_class_name != test_class.qualified_class_name
            for fixture in test_class.fixtures
            if fixture.phase == LifecyclePhase.SETUP
        )
    ]
    assert (
        len(classes_with_inherited_setup)
        >= _MIN_SPRING_DATA_REST_INHERITED_SETUP_CLASSES
    )


def test_spring_data_rest_mockmvc_tests_are_in_process(
    spring_data_rest_project_analysis: ProjectAnalysis,
) -> None:
    mockmvc_classes = [
        test_class
        for test_class in spring_data_rest_project_analysis.test_class_analyses
        if any(
            interaction.http_call is not None
            and interaction.http_call.framework == HttpDispatchFramework.MOCKMVC
            for test_method in test_class.test_method_analyses
            for interaction in test_method.http.request_interactions
        )
    ]
    assert mockmvc_classes

    for test_class in mockmvc_classes:
        for test_method in test_class.test_method_analyses:
            if not test_method.http.request_dispatch.labels:
                continue
            assert (
                RequestDispatch.REMOTE_NETWORK.value
                not in test_method.http.request_dispatch.labels
            )


def test_spring_data_rest_discovers_sufficient_test_methods(
    spring_data_rest_project_analysis: ProjectAnalysis,
) -> None:
    assert (
        spring_data_rest_project_analysis.test_method_count
        >= _MIN_SPRING_DATA_REST_TEST_METHODS
    )
    assert (
        spring_data_rest_project_analysis.test_class_count
        >= _MIN_SPRING_DATA_REST_TEST_CLASSES
    )


def test_spring_data_rest_classifies_api_tests(
    spring_data_rest_project_analysis: ProjectAnalysis,
) -> None:
    api_test_count = sum(
        1
        for test_class in spring_data_rest_project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
        if test_method.is_api_test
    )
    assert api_test_count >= _MIN_SPRING_DATA_REST_API_TESTS


def test_spring_data_rest_detects_assertion_patterns(
    spring_data_rest_project_analysis: ProjectAnalysis,
) -> None:
    total_assertions = sum(
        test_method.local_metrics.assertion_count
        for test_class in spring_data_rest_project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
    )
    assert total_assertions >= _MIN_SPRING_DATA_REST_ASSERTIONS


def test_spring_data_rest_has_no_real_auth_handling(
    spring_data_rest_project_analysis: ProjectAnalysis,
) -> None:
    auth_labels = {
        test_method.http.auth_handling.label
        for test_class in spring_data_rest_project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
        if test_method.http.auth_handling.label not in {"unknown", "none"}
    }
    assert AuthHandling.REAL_FLOW.value not in auth_labels
