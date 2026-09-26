from __future__ import annotations

from gerbil.analysis.shared import CommonAnalysis
from gerbil.analysis.schema import TestingFramework as Framework
from tests.cldk_factories import make_callable, make_type
from tests.fake_java_analysis import FakeJavaAnalysis


class TestTestNGClassLevelClassification:
    def _build_common_analysis(self) -> CommonAnalysis:
        test_class = "com.example.NgTest"
        analysis = FakeJavaAnalysis(
            classes={
                test_class: make_type(annotations=["@org.testng.annotations.Test"]),
            },
            methods_by_class={
                test_class: {
                    "testSomething()": make_callable(
                        signature="testSomething()",
                        modifiers=["public"],
                    ),
                    "setUp()": make_callable(
                        signature="setUp()",
                        annotations=["@org.testng.annotations.BeforeMethod"],
                        modifiers=["public"],
                    ),
                    "tearDown()": make_callable(
                        signature="tearDown()",
                        annotations=["@org.testng.annotations.AfterMethod"],
                        modifiers=["public"],
                    ),
                    "initClass()": make_callable(
                        signature="initClass()",
                        annotations=["@org.testng.annotations.BeforeClass"],
                        modifiers=["public"],
                    ),
                },
            },
        )
        return CommonAnalysis(analysis)

    def test_plain_public_method_is_test(self) -> None:
        common = self._build_common_analysis()
        assert common.is_test_method(
            "testSomething()", "com.example.NgTest", [Framework.TESTNG]
        )

    def test_before_method_is_not_test(self) -> None:
        common = self._build_common_analysis()
        assert not common.is_test_method(
            "setUp()", "com.example.NgTest", [Framework.TESTNG]
        )

    def test_after_method_is_not_test(self) -> None:
        common = self._build_common_analysis()
        assert not common.is_test_method(
            "tearDown()", "com.example.NgTest", [Framework.TESTNG]
        )

    def test_before_class_is_not_test(self) -> None:
        common = self._build_common_analysis()
        assert not common.is_test_method(
            "initClass()", "com.example.NgTest", [Framework.TESTNG]
        )


class TestTestNGBaseClassLevelClassification:
    def _build_common_analysis(self, base_imports: list[str]) -> CommonAnalysis:
        analysis = FakeJavaAnalysis(
            classes={
                "com.example.AbstractNgTest": make_type(annotations=["@Test"]),
                "com.example.FooTest": make_type(
                    extends_list=["com.example.AbstractNgTest"]
                ),
            },
            methods_by_class={
                "com.example.FooTest": {
                    "verifiesSomething()": make_callable(
                        signature="verifiesSomething()",
                        modifiers=["public"],
                    ),
                },
            },
            java_files={
                "com.example.AbstractNgTest": (
                    "src/test/java/com/example/AbstractNgTest.java"
                ),
                "com.example.FooTest": "src/test/java/com/example/FooTest.java",
            },
            import_declarations_by_file={
                "src/test/java/com/example/AbstractNgTest.java": base_imports,
                "src/test/java/com/example/FooTest.java": [],
            },
        )
        return CommonAnalysis(analysis)

    def test_subclass_public_method_is_test_with_testng_base_annotation(self) -> None:
        common = self._build_common_analysis(["org.testng.annotations.Test"])
        assert common.is_test_method(
            "verifiesSomething()", "com.example.FooTest", [Framework.TESTNG]
        )

    def test_subclass_method_is_not_test_with_junit_base_import(self) -> None:
        common = self._build_common_analysis(["org.junit.jupiter.api.Test"])
        assert not common.is_test_method(
            "verifiesSomething()", "com.example.FooTest", [Framework.TESTNG]
        )
