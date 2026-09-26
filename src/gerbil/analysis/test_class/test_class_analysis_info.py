from __future__ import annotations

from cldk.analysis.java import JavaAnalysis

from gerbil.analysis.shared import CommonAnalysis
from gerbil.analysis.properties import (
    EndpointHandlerIndex,
    build_endpoint_handler_index,
)
from gerbil.analysis.schema import FixtureAnalysis, TestClassAnalysis
from gerbil.analysis.test_method import MethodAnalysisInfo


class TestClassAnalysisInfo:
    def __init__(
        self,
        analysis: JavaAnalysis,
        application_classes: list[str],
        test_utility_classes: list[str] | None = None,
        expanded_helper_depth: int = 1,
        endpoint_handler_index: EndpointHandlerIndex | None = None,
    ) -> None:
        if expanded_helper_depth < 0:
            raise ValueError("expanded_helper_depth must be non-negative")

        self.analysis: JavaAnalysis = analysis
        self.application_classes: list[str] = application_classes
        self.test_utility_classes: list[str] = test_utility_classes or []
        self.expanded_helper_depth: int = expanded_helper_depth
        self.endpoint_handler_index: EndpointHandlerIndex = (
            endpoint_handler_index
            if endpoint_handler_index is not None
            else build_endpoint_handler_index([])
        )

    def get_test_class_analysis(
        self,
        qualified_class_name: str,
        test_methods: list[str],
    ) -> TestClassAnalysis:
        if not test_methods:
            raise ValueError("test_methods cannot be empty for get_test_class_analysis")

        common_analysis = CommonAnalysis(self.analysis)
        reachability = common_analysis.get_reachability()
        frameworks = common_analysis.get_testing_frameworks_for_class(
            qualified_class_name
        )

        setup_methods = common_analysis.get_setup_methods(qualified_class_name)
        teardown_methods = common_analysis.get_teardown_methods(qualified_class_name)

        method_analyzer = MethodAnalysisInfo(
            analysis=self.analysis,
            application_classes=self.application_classes,
            test_utility_classes=self.test_utility_classes,
            expanded_helper_depth=self.expanded_helper_depth,
            common_analysis=common_analysis,
            reachability=reachability,
            endpoint_handler_index=self.endpoint_handler_index,
        )

        test_method_analyses = []
        for method_signature in test_methods:
            effective_setup_methods = common_analysis.get_effective_setup_methods(
                qualified_class_name=qualified_class_name,
                test_method_signature=method_signature,
                setup_methods=setup_methods,
            )
            effective_teardown_methods = common_analysis.get_effective_teardown_methods(
                qualified_class_name=qualified_class_name,
                test_method_signature=method_signature,
                teardown_methods=teardown_methods,
            )

            test_method_analyses.append(
                method_analyzer.get_test_method_analysis_info(
                    testing_frameworks=frameworks,
                    qualified_class_name=qualified_class_name,
                    method_signature=method_signature,
                    setup_methods=effective_setup_methods,
                    teardown_methods=effective_teardown_methods,
                )
            )

        seen_fixture_keys: set[tuple[str, str, str]] = set()
        fixtures: list[FixtureAnalysis] = []
        for test_method_analysis in test_method_analyses:
            for fixture in test_method_analysis.fixtures:
                fixture_key = (
                    fixture.phase.value,
                    fixture.defining_class_name,
                    fixture.method_signature,
                )
                if fixture_key in seen_fixture_keys:
                    continue
                seen_fixture_keys.add(fixture_key)
                fixtures.append(fixture)

        return TestClassAnalysis(
            qualified_class_name=qualified_class_name,
            testing_frameworks=frameworks,
            fixtures=fixtures,
            test_method_analyses=test_method_analyses,
        )
