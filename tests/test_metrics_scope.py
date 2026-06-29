from __future__ import annotations

from gerbil.analysis.runtime import FixtureMethod
from gerbil.analysis.schema import TestingFramework as Framework
from gerbil.analysis.test_method import MethodAnalysisInfo
from tests.cldk_factories import make_call_site, make_callable, make_type
from tests.fake_java_analysis import FakeJavaAnalysis


def test_objects_created_local_vs_expanded() -> None:
    test_class = "com.example.MyTest"

    analysis = FakeJavaAnalysis(
        classes={
            test_class: make_type(),
        },
        methods_by_class={
            test_class: {
                "testFoo()": make_callable(
                    signature="testFoo()",
                    annotations=["@Test"],
                    modifiers=["public"],
                    call_sites=[
                        make_call_site(method_name="Foo", is_constructor_call=True),
                    ],
                ),
                "setUp()": make_callable(
                    signature="setUp()",
                    annotations=["@BeforeEach"],
                    modifiers=["public"],
                    call_sites=[
                        make_call_site(method_name="Bar", is_constructor_call=True),
                        make_call_site(method_name="Baz", is_constructor_call=True),
                    ],
                ),
            },
        },
        java_files={test_class: "src/test/java/MyTest.java"},
    )

    info = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    )

    result = info.get_test_method_analysis_info(
        qualified_class_name=test_class,
        method_signature="testFoo()",
        testing_frameworks=[Framework.JUNIT5],
        setup_methods=[
            FixtureMethod(
                defining_class_name=test_class,
                method_signature="setUp()",
            )
        ],
        teardown_methods=[],
    )

    assert result.local_metrics.number_of_objects_created == 1
    assert result.expanded_metrics.number_of_objects_created == 3
