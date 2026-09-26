from __future__ import annotations

from gerbil.analysis.schema import LifecyclePhase
from gerbil.analysis.test_class import TestClassAnalysisInfo as ClassAnalysisInfo
from tests.cldk_factories import make_call_site, make_callable, make_type
from tests.fake_java_analysis import FakeJavaAnalysis


def _api_request_call(start_line: int):
    return make_call_site(
        method_name="getForEntity",
        receiver_type="org.springframework.boot.test.web.client.TestRestTemplate",
        argument_expr=['"/api/users"'],
        start_line=start_line,
    )


def test_class_fixtures_union_across_test_methods() -> None:
    test_class = "com.example.UnionTest"
    analysis = FakeJavaAnalysis(
        classes={
            test_class: make_type(annotations=["@Test"]),
        },
        methods_by_class={
            test_class: {
                "testA()": make_callable(
                    signature="testA()",
                    annotations=['@Test(groups = {"smoke"})'],
                    modifiers=["public"],
                    call_sites=[_api_request_call(10)],
                ),
                "testB()": make_callable(
                    signature="testB()",
                    annotations=['@Test(groups = {"regression"})'],
                    modifiers=["public"],
                    call_sites=[_api_request_call(20)],
                ),
                "setUpSmoke()": make_callable(
                    signature="setUpSmoke()",
                    annotations=['@BeforeMethod(onlyForGroups = {"smoke"})'],
                    modifiers=["public"],
                ),
                "setUpRegression()": make_callable(
                    signature="setUpRegression()",
                    annotations=['@BeforeMethod(onlyForGroups = {"regression"})'],
                    modifiers=["public"],
                ),
                "initAll()": make_callable(
                    signature="initAll()",
                    annotations=["@BeforeClass"],
                    modifiers=["public", "static"],
                ),
            },
        },
        java_files={test_class: "src/test/java/UnionTest.java"},
        import_declarations_by_file={
            "src/test/java/UnionTest.java": ["org.testng.annotations.Test"]
        },
    )

    result = ClassAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    ).get_test_class_analysis(
        qualified_class_name=test_class,
        test_methods=["testA()", "testB()"],
    )

    fixture_keys = {
        (fixture.defining_class_name, fixture.method_signature)
        for fixture in result.fixtures
    }
    assert (test_class, "setUpSmoke()") in fixture_keys
    assert (test_class, "setUpRegression()") in fixture_keys
    assert (test_class, "initAll()") in fixture_keys
    assert len(result.fixtures) == len(fixture_keys)


def test_class_fixtures_preserve_setup_and_teardown_for_same_signature() -> None:
    test_class = "com.example.PhaseAwareFixtureTest"
    analysis = FakeJavaAnalysis(
        classes={
            test_class: make_type(annotations=["@Test"]),
        },
        methods_by_class={
            test_class: {
                "testA()": make_callable(
                    signature="testA()",
                    annotations=["@Test"],
                    modifiers=["public"],
                    call_sites=[_api_request_call(10)],
                ),
                "sharedFixture()": make_callable(
                    signature="sharedFixture()",
                    annotations=["@BeforeMethod", "@AfterMethod"],
                    modifiers=["public"],
                ),
            },
        },
        java_files={test_class: "src/test/java/PhaseAwareFixtureTest.java"},
        import_declarations_by_file={
            "src/test/java/PhaseAwareFixtureTest.java": ["org.testng.annotations.Test"]
        },
    )

    result = ClassAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    ).get_test_class_analysis(
        qualified_class_name=test_class,
        test_methods=["testA()"],
    )

    shared_fixtures = [
        fixture
        for fixture in result.fixtures
        if fixture.defining_class_name == test_class
        and fixture.method_signature == "sharedFixture()"
    ]

    assert len(shared_fixtures) == 2
    assert {fixture.phase for fixture in shared_fixtures} == {
        LifecyclePhase.SETUP,
        LifecyclePhase.TEARDOWN,
    }


def test_class_fixtures_collapse_same_phase_duplicates_across_test_methods() -> None:
    test_class = "com.example.DedupFixtureTest"
    analysis = FakeJavaAnalysis(
        classes={
            test_class: make_type(annotations=["@Test"]),
        },
        methods_by_class={
            test_class: {
                "testA()": make_callable(
                    signature="testA()",
                    annotations=['@Test(groups = {"smoke"})'],
                    modifiers=["public"],
                    call_sites=[_api_request_call(10)],
                ),
                "testB()": make_callable(
                    signature="testB()",
                    annotations=['@Test(groups = {"regression"})'],
                    modifiers=["public"],
                    call_sites=[_api_request_call(20)],
                ),
                "initAll()": make_callable(
                    signature="initAll()",
                    annotations=["@BeforeClass"],
                    modifiers=["public", "static"],
                ),
            },
        },
        java_files={test_class: "src/test/java/DedupFixtureTest.java"},
        import_declarations_by_file={
            "src/test/java/DedupFixtureTest.java": ["org.testng.annotations.Test"]
        },
    )

    result = ClassAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    ).get_test_class_analysis(
        qualified_class_name=test_class,
        test_methods=["testA()", "testB()"],
    )

    init_all_setup_fixtures = [
        fixture
        for fixture in result.fixtures
        if fixture.defining_class_name == test_class
        and fixture.method_signature == "initAll()"
        and fixture.phase == LifecyclePhase.SETUP
    ]

    assert len(init_all_setup_fixtures) == 1


def test_nested_class_runtime_view_includes_outer_fixture_http_call() -> None:
    test_class = "example.OuterTest.InnerTest"
    nested_test_file = "src/test/java/example/OuterTest.java"
    analysis = FakeJavaAnalysis(
        classes={
            "example.OuterTest": make_type(),
            test_class: make_type(
                parent_type="example.OuterTest",
                annotations=["@Nested"],
            ),
        },
        methods_by_class={
            test_class: {
                "innerTest()": make_callable(
                    signature="innerTest()",
                    annotations=["@Test"],
                    modifiers=["public"],
                ),
            },
            "example.OuterTest": {
                "outerSetUp()": make_callable(
                    signature="outerSetUp()",
                    annotations=["@BeforeEach"],
                    modifiers=["public"],
                    call_sites=[_api_request_call(10)],
                ),
            },
        },
        java_files={
            "example.OuterTest": nested_test_file,
            test_class: nested_test_file,
        },
        import_declarations_by_file={
            nested_test_file: [
                "org.junit.jupiter.api.Test",
                "org.junit.jupiter.api.BeforeEach",
                "org.junit.jupiter.api.Nested",
                "org.springframework.boot.test.web.client.TestRestTemplate",
            ],
        },
    )

    result = ClassAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    ).get_test_class_analysis(
        qualified_class_name=test_class,
        test_methods=["innerTest()"],
    )

    outer_fixture = next(
        (
            fixture
            for fixture in result.fixtures
            if fixture.defining_class_name == "example.OuterTest"
            and fixture.method_signature == "outerSetUp()"
        ),
        None,
    )
    assert outer_fixture is not None
    assert outer_fixture.phase == LifecyclePhase.SETUP
    assert outer_fixture.request_interaction_count == 1
