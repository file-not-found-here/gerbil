from __future__ import annotations

import pytest

from gerbil.analysis.schema import (
    AuthHandling,
    HttpDispatchFramework,
    LifecyclePhase,
    ProjectAnalysis,
    TestingFramework as AnalysisFramework,
)
from tests.integration.conftest import collect_dispatch_frameworks

pytestmark = pytest.mark.integration

_MIN_MARQUEZ_INHERITED_SETUP_CLASSES = 8
_MIN_MARQUEZ_PARAMETERIZED_TESTS = 15
_MIN_MARQUEZ_TEST_METHODS = 400
_MIN_MARQUEZ_TEST_CLASSES = 60
_MIN_MARQUEZ_ASSERTIONS = 1500


def _all_frameworks(project_analysis: ProjectAnalysis) -> set[AnalysisFramework]:
    return {
        framework
        for test_class in project_analysis.test_class_analyses
        for framework in test_class.testing_frameworks
    }


def test_marquez_detects_java_httpclient(
    marquez_project_analysis: ProjectAnalysis,
) -> None:
    all_frameworks = _all_frameworks(marquez_project_analysis)
    assert AnalysisFramework.JUNIT5 in all_frameworks

    dispatch = collect_dispatch_frameworks(marquez_project_analysis)
    assert HttpDispatchFramework.JAVA_HTTPCLIENT in dispatch


def test_marquez_detects_apache_httpclient(
    marquez_project_analysis: ProjectAnalysis,
) -> None:
    dispatch = collect_dispatch_frameworks(marquez_project_analysis)
    assert HttpDispatchFramework.APACHE_HTTPCLIENT in dispatch


def test_marquez_extracts_http_calls_from_jdk_client(
    marquez_project_analysis: ProjectAnalysis,
) -> None:
    all_http_calls = [
        interaction.http_call
        for test_class in marquez_project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
        for interaction in test_method.http.request_interactions
        if interaction.http_call is not None
    ]
    assert all_http_calls


def test_marquez_detects_base_integration_test_inheritance(
    marquez_project_analysis: ProjectAnalysis,
) -> None:
    classes_with_inherited_setup = [
        test_class
        for test_class in marquez_project_analysis.test_class_analyses
        if any(
            fixture.defining_class_name != test_class.qualified_class_name
            for fixture in test_class.fixtures
            if fixture.phase == LifecyclePhase.SETUP
        )
    ]
    assert len(classes_with_inherited_setup) >= _MIN_MARQUEZ_INHERITED_SETUP_CLASSES


def test_marquez_detects_parameterized_tests(
    marquez_project_analysis: ProjectAnalysis,
) -> None:
    parameterized_count = sum(
        1
        for test_class in marquez_project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
        if test_method.identity.parameterization is not None
    )
    assert parameterized_count >= _MIN_MARQUEZ_PARAMETERIZED_TESTS


def test_marquez_discovers_tests(marquez_project_analysis: ProjectAnalysis) -> None:
    assert marquez_project_analysis.test_method_count >= _MIN_MARQUEZ_TEST_METHODS
    assert marquez_project_analysis.test_class_count >= _MIN_MARQUEZ_TEST_CLASSES


def test_marquez_has_no_real_auth_handling(
    marquez_project_analysis: ProjectAnalysis,
) -> None:
    auth_labels = {
        test_method.http.auth_handling.label
        for test_class in marquez_project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
        if test_method.http.auth_handling.label not in {"unknown", "none"}
    }
    assert AuthHandling.REAL_FLOW.value not in auth_labels


def test_marquez_detects_assertion_patterns(
    marquez_project_analysis: ProjectAnalysis,
) -> None:
    total_assertions = sum(
        test_method.local_metrics.assertion_count
        for test_class in marquez_project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
    )
    assert total_assertions >= _MIN_MARQUEZ_ASSERTIONS
