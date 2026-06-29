from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from cldk.analysis.java import JavaAnalysis

from gerbil.analysis.shared import CommonAnalysis
from gerbil.analysis.shared.caching import (
    reset_class_resolution_cache,
)
from gerbil.analysis.shared.git_info import read_git_info
from gerbil.analysis.schema import (
    ProjectAnalysis,
    ProjectMetadata,
    ProjectSummary,
)
from gerbil.analysis.properties import (
    build_controller_unit_test_summary,
    build_endpoint_coverage_summary,
    build_endpoint_handler_index,
    build_endpoint_parameter_coverage_summary,
    build_resource_crud_analysis,
    extract_application_endpoints,
)
from gerbil.analysis.test_class import TestClassAnalysisInfo


class ProjectAnalysisInfo:
    def __init__(
        self,
        analysis: JavaAnalysis,
        dataset_name: str,
        project_path: str,
        expanded_helper_depth: int = 1,
        test_dirs: Sequence[str] | None = None,
    ) -> None:
        if expanded_helper_depth < 0:
            raise ValueError("expanded_helper_depth must be non-negative")
        resolved_test_dirs = tuple(test_dirs or ())
        if test_dirs is not None and not resolved_test_dirs:
            raise ValueError("test_dirs must contain at least one path pattern")

        self.analysis: JavaAnalysis = analysis
        self.dataset_name: str = dataset_name
        self.project_path: str = project_path
        self.expanded_helper_depth: int = expanded_helper_depth
        self.test_dirs: tuple[str, ...] | None = (
            resolved_test_dirs if test_dirs is not None else None
        )

    def gather_project_analysis_info(self) -> ProjectAnalysis:
        reset_class_resolution_cache()

        common_analysis = CommonAnalysis(
            self.analysis,
            test_dirs=self.test_dirs,
        )
        test_class_methods, application_classes, test_utility_classes = (
            common_analysis.categorize_classes()
        )

        # Endpoints are extracted up front so the handler index is available
        # while each test method is analyzed (controller unit test detection).
        endpoint_extraction = extract_application_endpoints(
            analysis=self.analysis,
            application_classes=application_classes,
            constant_resolver=common_analysis.get_constant_resolver(),
        )
        application_endpoints = endpoint_extraction.endpoints
        endpoint_handler_index = build_endpoint_handler_index(application_endpoints)

        test_class_analyzer = TestClassAnalysisInfo(
            analysis=self.analysis,
            application_classes=application_classes,
            test_utility_classes=test_utility_classes,
            expanded_helper_depth=self.expanded_helper_depth,
            endpoint_handler_index=endpoint_handler_index,
        )

        test_class_analyses = [
            test_class_analyzer.get_test_class_analysis(
                qualified_class_name=test_class,
                test_methods=test_methods,
            )
            for test_class, test_methods in test_class_methods.items()
        ]

        endpoint_coverage = build_endpoint_coverage_summary(
            application_endpoints=application_endpoints,
            test_class_analyses=test_class_analyses,
            application_path_prefixes=endpoint_extraction.application_path_prefixes,
        )
        endpoint_parameter_coverage = build_endpoint_parameter_coverage_summary(
            application_endpoints=application_endpoints,
            test_class_analyses=test_class_analyses,
            application_path_prefixes=endpoint_extraction.application_path_prefixes,
        )
        resource_crud = build_resource_crud_analysis(
            application_endpoints=application_endpoints,
            test_class_analyses=test_class_analyses,
            application_path_prefixes=endpoint_extraction.application_path_prefixes,
        )
        controller_unit_tests = build_controller_unit_test_summary(
            application_endpoints=application_endpoints,
            test_class_analyses=test_class_analyses,
        )

        api_test_count = 0
        non_api_test_count = 0
        controller_unit_test_count = 0
        total_http_interactions = 0
        for test_class_analysis in test_class_analyses:
            for test_method_analysis in test_class_analysis.test_method_analyses:
                if test_method_analysis.is_api_test:
                    api_test_count += 1
                else:
                    non_api_test_count += 1
                    if test_method_analysis.is_controller_unit_test:
                        controller_unit_test_count += 1
                total_http_interactions += len(
                    test_method_analysis.http.http_interactions
                )

        total_endpoints = endpoint_coverage.total_application_endpoints
        coverage_ratio = (
            endpoint_coverage.covered_endpoint_count / total_endpoints
            if total_endpoints > 0
            else 0.0
        )
        summary = ProjectSummary(
            api_test_count=api_test_count,
            non_api_test_count=non_api_test_count,
            controller_unit_test_count=controller_unit_test_count,
            total_http_interactions=total_http_interactions,
            coverage_ratio=round(coverage_ratio, 4),
        )

        (
            application_method_count,
            application_cyclomatic_complexity,
        ) = common_analysis.get_application_method_metrics(application_classes)

        test_method_count = sum(
            len(test_methods) for test_methods in test_class_methods.values()
        )

        git_info = read_git_info(Path(self.project_path))

        return ProjectAnalysis(
            dataset_name=self.dataset_name,
            metadata=ProjectMetadata(
                project_path=self.project_path,
                git_commit_hash=git_info.commit_hash,
                git_remote_host=git_info.remote_host,
                git_repository=git_info.repository,
                expanded_helper_depth=self.expanded_helper_depth,
            ),
            summary=summary,
            application_class_count=len(application_classes),
            application_method_count=application_method_count,
            application_cyclomatic_complexity=application_cyclomatic_complexity,
            test_class_count=len(test_class_methods),
            test_method_count=test_method_count,
            test_utility_class_count=len(test_utility_classes),
            test_utility_method_count=common_analysis.get_test_utility_method_count(
                test_utility_classes
            ),
            endpoint_coverage=endpoint_coverage,
            endpoint_parameter_coverage=endpoint_parameter_coverage,
            resource_crud=resource_crud,
            controller_unit_tests=controller_unit_tests,
            test_class_analyses=test_class_analyses,
        )
