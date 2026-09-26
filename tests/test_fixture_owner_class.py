from __future__ import annotations

from gerbil.analysis.runtime import FixtureMethod
from gerbil.analysis.schema import TestingFramework as Framework
from gerbil.analysis.test_method import MethodAnalysisInfo
from tests.cldk_factories import make_call_site, make_callable, make_type
from tests.fake_java_analysis import FakeJavaAnalysis


def test_inherited_fixture_uses_fixture_class_for_helper_expansion() -> None:
    parent_class = "com.example.BaseTest"
    child_class = "com.example.ChildTest"

    analysis = FakeJavaAnalysis(
        classes={
            parent_class: make_type(),
            child_class: make_type(extends_list=[parent_class]),
        },
        methods_by_class={
            parent_class: {
                "setUp()": make_callable(
                    signature="setUp()",
                    annotations=["@BeforeEach"],
                    modifiers=["public"],
                    call_sites=[
                        make_call_site(
                            method_name="initHelper",
                            callee_signature="initHelper()",
                            receiver_expr="this",
                            start_line=1,
                        ),
                    ],
                ),
                "initHelper()": make_callable(
                    signature="initHelper()",
                    modifiers=["protected"],
                    call_sites=[
                        make_call_site(
                            method_name="ParentCtor",
                            is_constructor_call=True,
                            start_line=2,
                        )
                    ],
                ),
            },
            child_class: {
                "testSomething()": make_callable(
                    signature="testSomething()",
                    annotations=["@Test"],
                    modifiers=["public"],
                ),
                "initHelper()": make_callable(
                    signature="initHelper()",
                    modifiers=["protected"],
                ),
            },
        },
        java_files={
            parent_class: "src/test/java/BaseTest.java",
            child_class: "src/test/java/ChildTest.java",
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=1,
    ).get_test_method_analysis_info(
        qualified_class_name=child_class,
        method_signature="testSomething()",
        testing_frameworks=[Framework.JUNIT5],
        setup_methods=[
            FixtureMethod(
                defining_class_name=parent_class,
                method_signature="setUp()",
            )
        ],
        teardown_methods=[],
    )

    assert result.expanded_metrics.number_of_objects_created == 1
