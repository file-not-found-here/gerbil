from __future__ import annotations

from pathlib import Path

import pytest
from cldk import CLDK
from cldk.analysis import AnalysisLevel
from cldk.analysis.java import JavaAnalysis

from gerbil.analysis.schema import HttpDispatchFramework, ProjectAnalysis
from gerbil.analysis.project import ProjectAnalysisInfo


def collect_dispatch_frameworks(
    project_analysis: ProjectAnalysis,
) -> set[HttpDispatchFramework]:
    """Collect all HTTP dispatch frameworks from HttpCallSite data."""
    return {
        interaction.http_call.framework
        for test_class in project_analysis.test_class_analyses
        for test_method in test_class.test_method_analyses
        for interaction in test_method.http.request_interactions
        if interaction.http_call is not None
    }


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_ROOT = _REPO_ROOT / "tests"
_RESOURCES_ROOT = _TESTS_ROOT / "resources"
_OUTPUTS_ROOT = _TESTS_ROOT / "analysis_outputs"
_TEST_RUNTIME_HELPER_DEPTH = 5


def _require_repo_checkout(repo_name: str) -> Path:
    repo_path = _RESOURCES_ROOT / repo_name
    if not repo_path.exists():
        pytest.skip(f"missing repository fixture: {repo_path}")

    try:
        next(repo_path.rglob("*.java"))
    except StopIteration:
        pytest.skip(
            f"repository fixture has no Java sources (submodule not initialized?): {repo_path}"
        )

    return repo_path


def _build_java_analysis(
    *,
    repo_name: str,
    analysis_backend_path: str | None,
) -> JavaAnalysis:
    repo_path = _require_repo_checkout(repo_name)
    repo_output_dir = _OUTPUTS_ROOT / repo_name
    cldk_cache_dir = repo_output_dir / "cldk"
    cldk_cache_dir.mkdir(parents=True, exist_ok=True)

    return CLDK(language="java").analysis(
        project_path=str(repo_path),
        analysis_level=AnalysisLevel.symbol_table,
        analysis_json_path=str(cldk_cache_dir),
        analysis_backend_path=analysis_backend_path,
        eager=True,
    )


def _build_project_analysis(
    *, repo_name: str, analysis: JavaAnalysis
) -> ProjectAnalysis:
    repo_path = _require_repo_checkout(repo_name)
    project_analysis = ProjectAnalysisInfo(
        analysis=analysis,
        dataset_name=repo_name,
        project_path=str(repo_path),
        expanded_helper_depth=_TEST_RUNTIME_HELPER_DEPTH,
    ).gather_project_analysis_info()

    repo_output_dir = _OUTPUTS_ROOT / repo_name
    repo_output_dir.mkdir(parents=True, exist_ok=True)
    (repo_output_dir / "gerbil.json").write_text(
        project_analysis.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return project_analysis


@pytest.fixture(scope="session")
def analysis_backend_path(request: pytest.FixtureRequest) -> str | None:
    return request.config.getoption("--analysis-backend-path")


@pytest.fixture(scope="session")
def spring_data_rest_java_analysis(analysis_backend_path: str | None) -> JavaAnalysis:
    """CLDK JavaAnalysis for spring-projects/spring-data-rest."""
    return _build_java_analysis(
        repo_name="spring-data-rest",
        analysis_backend_path=analysis_backend_path,
    )


@pytest.fixture(scope="session")
def spring_boot_admin_java_analysis(analysis_backend_path: str | None) -> JavaAnalysis:
    """CLDK JavaAnalysis for codecentric/spring-boot-admin."""
    return _build_java_analysis(
        repo_name="spring-boot-admin",
        analysis_backend_path=analysis_backend_path,
    )


@pytest.fixture(scope="session")
def marquez_java_analysis(analysis_backend_path: str | None) -> JavaAnalysis:
    """CLDK JavaAnalysis for MarquezProject/marquez."""
    return _build_java_analysis(
        repo_name="marquez",
        analysis_backend_path=analysis_backend_path,
    )


@pytest.fixture(scope="session")
def state_interaction_smoke_java_analysis(
    analysis_backend_path: str | None,
) -> JavaAnalysis:
    """CLDK JavaAnalysis for local state-interaction smoke fixture."""
    return _build_java_analysis(
        repo_name="state-interaction-smoke",
        analysis_backend_path=analysis_backend_path,
    )


@pytest.fixture(scope="session")
def spring_data_rest_project_analysis(
    spring_data_rest_java_analysis: JavaAnalysis,
) -> ProjectAnalysis:
    """Full Gerbil ProjectAnalysis for spring-projects/spring-data-rest."""
    return _build_project_analysis(
        repo_name="spring-data-rest",
        analysis=spring_data_rest_java_analysis,
    )


@pytest.fixture(scope="session")
def spring_boot_admin_project_analysis(
    spring_boot_admin_java_analysis: JavaAnalysis,
) -> ProjectAnalysis:
    """Full Gerbil ProjectAnalysis for codecentric/spring-boot-admin."""
    return _build_project_analysis(
        repo_name="spring-boot-admin",
        analysis=spring_boot_admin_java_analysis,
    )


@pytest.fixture(scope="session")
def marquez_project_analysis(
    marquez_java_analysis: JavaAnalysis,
) -> ProjectAnalysis:
    """Full Gerbil ProjectAnalysis for MarquezProject/marquez."""
    return _build_project_analysis(repo_name="marquez", analysis=marquez_java_analysis)


@pytest.fixture(scope="session")
def state_interaction_smoke_project_analysis(
    state_interaction_smoke_java_analysis: JavaAnalysis,
) -> ProjectAnalysis:
    """Full Gerbil ProjectAnalysis for local state-interaction smoke fixture."""
    return _build_project_analysis(
        repo_name="state-interaction-smoke",
        analysis=state_interaction_smoke_java_analysis,
    )


@pytest.fixture(scope="session")
def request_path_recovery_smoke_java_analysis(
    analysis_backend_path: str | None,
) -> JavaAnalysis:
    """CLDK JavaAnalysis for local request-path recovery smoke fixture."""
    return _build_java_analysis(
        repo_name="request-path-recovery-smoke",
        analysis_backend_path=analysis_backend_path,
    )


@pytest.fixture(scope="session")
def request_path_recovery_smoke_project_analysis(
    request_path_recovery_smoke_java_analysis: JavaAnalysis,
) -> ProjectAnalysis:
    """Full Gerbil ProjectAnalysis for local request-path recovery smoke fixture."""
    return _build_project_analysis(
        repo_name="request-path-recovery-smoke",
        analysis=request_path_recovery_smoke_java_analysis,
    )
