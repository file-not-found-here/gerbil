from __future__ import annotations

import pytest

from gerbil.analysis.schema import (
    HttpDispatchFramework,
    ProjectAnalysis,
)
from tests.integration.conftest import collect_dispatch_frameworks

pytestmark = pytest.mark.integration


def test_repos_detect_different_framework_profiles(
    spring_data_rest_project_analysis: ProjectAnalysis,
    spring_boot_admin_project_analysis: ProjectAnalysis,
    marquez_project_analysis: ProjectAnalysis,
) -> None:
    spring_data_rest_dispatch = collect_dispatch_frameworks(
        spring_data_rest_project_analysis
    )
    spring_boot_admin_dispatch = collect_dispatch_frameworks(
        spring_boot_admin_project_analysis
    )
    marquez_dispatch = collect_dispatch_frameworks(marquez_project_analysis)

    assert HttpDispatchFramework.MOCKMVC in spring_data_rest_dispatch
    assert HttpDispatchFramework.MOCKMVC in spring_boot_admin_dispatch
    assert HttpDispatchFramework.WEBTESTCLIENT in spring_boot_admin_dispatch
    assert HttpDispatchFramework.WEBTESTCLIENT not in spring_data_rest_dispatch
    assert HttpDispatchFramework.MOCKMVC not in marquez_dispatch
    assert HttpDispatchFramework.JAVA_HTTPCLIENT in marquez_dispatch


def test_spring_boot_admin_has_more_auth_detection_than_spring_data_rest(
    spring_data_rest_project_analysis: ProjectAnalysis,
    spring_boot_admin_project_analysis: ProjectAnalysis,
) -> None:
    spring_data_rest_auth_labels = {
        test_method.http.auth_handling.label
        for test_class in spring_data_rest_project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
        if test_method.http.auth_handling.label not in {"unknown", "none"}
    }
    spring_boot_admin_auth_labels = {
        test_method.http.auth_handling.label
        for test_class in spring_boot_admin_project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
        if test_method.http.auth_handling.label not in {"unknown", "none"}
    }
    assert len(spring_boot_admin_auth_labels) > len(spring_data_rest_auth_labels)
