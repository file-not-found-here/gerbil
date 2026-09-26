from __future__ import annotations

import gerbil.analysis.shared.caching as receiver_hierarchy_cache_module

from gerbil.analysis.runtime.call_sites import (
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.schema import (
    LifecyclePhase,
)
from gerbil.analysis.properties.dependency_strategy import (
    classify_dependency_strategy,
)
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def _runtime_receiver_resolver(runtime_view: TestRuntimeView):
    return build_runtime_receiver_resolver_for_testing(runtime_view)


def test_dependency_label_includes_teardown_fixture_hints() -> None:
    teardown_method = make_callable(
        signature="afterEach()",
        call_sites=[
            make_call_site(
                method_name="when",
                receiver_type="org.mockserver.client.MockServerClient",
            )
        ],
    )
    test_method = make_callable(call_sites=[])
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.TestClass",
                    method_signature="testCase()",
                ),
                context_class_name="example.TestClass",
                grouping=build_call_site_grouping(list(test_method.call_sites)),
                method_details=test_method,
            ),
            PhaseEntry(
                phase=LifecyclePhase.TEARDOWN,
                method_ref=MethodRef(
                    defining_class_name="example.TestClass",
                    method_signature="afterEach()",
                ),
                context_class_name="example.TestClass",
                grouping=build_call_site_grouping(list(teardown_method.call_sites)),
                method_details=teardown_method,
            ),
        ],
    )
    dep = classify_dependency_strategy(
        class_details=make_type(),
        method_details=test_method,
        class_annotations=[],
        runtime_view=runtime_view,
        class_annotation_imports_by_class={},
        method_imports=[],
        declaring_class_imports=[],
        analysis=None,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    assert "virtualized" in dep.labels


def test_class_resolution_cache_is_bounded() -> None:
    receiver_hierarchy_cache_module.reset_class_resolution_cache()

    try:
        analyses = [
            FakeJavaAnalysis(classes={f"example.Repository{index}": make_type()})
            for index in range(
                receiver_hierarchy_cache_module.CLASS_RESOLUTION_CACHE_MAX_ENTRIES + 10
            )
        ]
        for index, analysis in enumerate(analyses):
            receiver_hierarchy_cache_module.get_receiver_hierarchy(
                receiver_type=f"example.Repository{index}",
                analysis=analysis,
            )

        assert (
            0
            < len(receiver_hierarchy_cache_module.CLASS_RESOLUTION_CACHE)
            <= (receiver_hierarchy_cache_module.CLASS_RESOLUTION_CACHE_MAX_ENTRIES)
        )
    finally:
        receiver_hierarchy_cache_module.reset_class_resolution_cache()
