from __future__ import annotations

from gerbil.analysis.shared.class_utils import categorize_classes
from gerbil.analysis.shared.constants import SETUP_ANNOTATIONS
from gerbil.analysis.shared.fixture_discovery import (
    SETUP_CLASS_SCOPE_ANNOTATIONS,
    find_fixture_methods,
    get_effective_fixture_methods,
)
from gerbil.analysis.shared.framework_inference import (
    infer_spring_subframeworks,
    infer_testing_frameworks,
    matches_package_prefix,
)
from gerbil.analysis.schema import HttpDispatchFramework
from gerbil.analysis.shared.metrics_helpers import (
    get_application_method_metrics,
    get_call_sites_sorted,
    get_test_utility_method_count,
)
from gerbil.analysis.runtime import FixtureMethod
from gerbil.analysis.schema import TestingFramework as Framework
from tests.cldk_factories import (
    make_call_site,
    make_callable,
    make_import_declaration,
    make_import_declarations,
    make_resolved_annotation,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def _fixture_tuples(
    fixture_methods: list[FixtureMethod],
) -> list[tuple[str, str, bool]]:
    return [
        (
            fixture.defining_class_name,
            fixture.method_signature,
            fixture.is_ambiguous,
        )
        for fixture in fixture_methods
    ]


def test_framework_inference_matches_package_prefix_is_boundary_safe() -> None:
    assert matches_package_prefix(
        "org.springframework.test.web.servlet.MockMvc",
        "org.springframework.test.web.servlet",
    )
    assert matches_package_prefix(
        "org.springframework.test.web.servlet",
        "org.springframework.test.web.servlet.",
    )
    assert not matches_package_prefix(
        "org.springframework.test.web.servletx.MockMvc",
        "org.springframework.test.web.servlet",
    )


def test_framework_inference_detects_frameworks_from_imports_and_annotations() -> None:
    class_imports = make_import_declarations(
        "org.junit.jupiter.api.Test",
        "org.springframework.test.web.servlet.MockMvc",
    )
    class_annotations = [
        make_resolved_annotation(
            "@org.springframework.boot.test.autoconfigure.web.reactive.WebFluxTest"
        )
    ]
    frameworks = infer_testing_frameworks(
        class_imports=class_imports,
        class_annotations=class_annotations,
        class_annotation_imports_by_class={},
    )

    assert Framework.JUNIT5 in frameworks
    assert Framework.SPRING_TEST in frameworks

    dispatch_frameworks = infer_spring_subframeworks(
        class_imports=class_imports,
        class_annotations=class_annotations,
        class_annotation_imports_by_class={},
    )
    assert HttpDispatchFramework.MOCKMVC in dispatch_frameworks
    assert HttpDispatchFramework.WEBTESTCLIENT in dispatch_frameworks


def test_framework_inference_normalizes_fully_qualified_annotations() -> None:
    frameworks = infer_testing_frameworks(
        class_imports=[],
        class_annotations=[
            make_resolved_annotation(
                "@org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest"
            )
        ],
        class_annotation_imports_by_class={},
    )

    assert Framework.SPRING_TEST in frameworks


def test_framework_inference_detects_rest_assured_under_both_roots() -> None:
    """RestAssured 2.x imports live under com.jayway.restassured; 3.0+ under io.restassured."""
    for import_path in (
        "io.restassured.RestAssured",
        "com.jayway.restassured.RestAssured",
    ):
        frameworks = infer_testing_frameworks(
            class_imports=make_import_declarations(import_path),
            class_annotations=[],
            class_annotation_imports_by_class={},
        )
        assert Framework.REST_ASSURED in frameworks, import_path


def test_framework_inference_detects_karate_under_both_roots() -> None:
    """Karate 1.x imports live under com.intuit.karate; 2.x under io.karatelabs."""
    for import_path in (
        "com.intuit.karate.Http",
        "io.karatelabs.http.Http",
    ):
        frameworks = infer_testing_frameworks(
            class_imports=make_import_declarations(import_path),
            class_annotations=[],
            class_annotation_imports_by_class={},
        )
        assert frameworks == [Framework.KARATE], import_path


def test_framework_inference_detects_frameworks_from_wildcard_imports() -> None:
    # CLDK emits wildcard imports as the bare package path without ".*".
    for import_path, expected_framework in (
        ("org.junit.*", Framework.JUNIT4),
        ("junit.framework.*", Framework.JUNIT3),
        ("org.testng.*", Framework.TESTNG),
    ):
        frameworks = infer_testing_frameworks(
            class_imports=make_import_declarations(import_path),
            class_annotations=[],
            class_annotation_imports_by_class={},
        )
        assert frameworks == [expected_framework], import_path


def test_framework_inference_detects_jmockit_from_mockit_package_import() -> None:
    """JMockit's groupId is org.jmockit but its Java package is mockit."""
    frameworks = infer_testing_frameworks(
        class_imports=make_import_declarations("mockit.Mocked"),
        class_annotations=[],
        class_annotation_imports_by_class={},
    )
    assert Framework.JMOCKIT in frameworks


def test_framework_inference_detects_frameworks_from_static_only_imports() -> None:
    class_imports = [
        make_import_declaration(
            "org.junit.jupiter.api.Assertions.assertEquals",
            is_static=True,
        ),
        make_import_declaration(
            "org.assertj.core.api.Assertions.assertThat",
            is_static=True,
        ),
        make_import_declaration(
            "org.mockito.Mockito.verify",
            is_static=True,
        ),
        make_import_declaration(
            "org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get",
            is_static=True,
        ),
    ]
    frameworks = infer_testing_frameworks(
        class_imports=class_imports,
        class_annotations=[],
        class_annotation_imports_by_class={},
    )

    assert Framework.JUNIT5 in frameworks
    assert Framework.ASSERTJ in frameworks
    assert Framework.MOCKITO in frameworks
    assert Framework.SPRING_TEST in frameworks

    dispatch_frameworks = infer_spring_subframeworks(
        class_imports=class_imports,
        class_annotations=[],
        class_annotation_imports_by_class={},
    )
    assert HttpDispatchFramework.MOCKMVC in dispatch_frameworks


def test_class_categorization_smoke_classifies_test_and_non_test_classes() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(),
            "example.TestSupport": make_type(),
            "example.ProductionService": make_type(),
        },
        methods_by_class={
            "example.ApiTest": {
                "testEndpoint()": make_callable(signature="testEndpoint()"),
                "helper()": make_callable(signature="helper()"),
            },
            "example.TestSupport": {
                "buildFixture()": make_callable(signature="buildFixture()"),
            },
            "example.ProductionService": {
                "run()": make_callable(signature="run()"),
            },
        },
        java_files={
            "example.ApiTest": "src/test/java/example/ApiTest.java",
            "example.TestSupport": "src/test/java/example/TestSupport.java",
            "example.ProductionService": "src/main/java/example/ProductionService.java",
        },
    )

    def _frameworks_for_class(_: str) -> list[Framework]:
        return [Framework.JUNIT5]

    def _is_test_method(
        method_signature: str,
        qualified_class_name: str,
        _: list[Framework],
    ) -> bool:
        return (
            qualified_class_name == "example.ApiTest"
            and method_signature == "testEndpoint()"
        )

    test_classes_methods, application_classes, test_utility_classes = (
        categorize_classes(
            analysis=analysis,
            qualified_class_names=[
                "example.ApiTest",
                "example.TestSupport",
                "example.ProductionService",
            ],
            test_dirs=("src/test/java",),
            get_testing_frameworks_for_class=_frameworks_for_class,
            is_test_method_for_class=_is_test_method,
        )
    )

    assert test_classes_methods == {"example.ApiTest": ["testEndpoint()"]}
    assert application_classes == ["example.ProductionService"]
    assert test_utility_classes == ["example.TestSupport"]


def test_fixture_discovery_find_fixture_methods_hides_shadowed_parent_signatures() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={
            "example.ChildTest": make_type(extends_list=["example.ParentTest"]),
            "example.ParentTest": make_type(),
        },
        methods_by_class={
            "example.ChildTest": {
                "setUp()": make_callable(signature="setUp()", annotations=[]),
            },
            "example.ParentTest": {
                "setUp()": make_callable(
                    signature="setUp()",
                    annotations=["@org.junit.jupiter.api.BeforeEach"],
                ),
                "parentOnly()": make_callable(
                    signature="parentOnly()",
                    annotations=["@org.junit.jupiter.api.BeforeEach"],
                ),
            },
        },
    )

    fixture_methods = find_fixture_methods(
        analysis=analysis,
        reachable_methods={
            "example.ChildTest": ["setUp()"],
            "example.ParentTest": ["setUp()", "parentOnly()"],
        },
        fixture_annotations=SETUP_ANNOTATIONS,
    )

    assert _fixture_tuples(fixture_methods) == [
        ("example.ParentTest", "parentOnly()", False)
    ]


def test_fixture_discovery_find_fixture_methods_keeps_same_signature_enclosing_fixtures() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={
            "example.OuterTest": make_type(),
            "example.OuterTest.InnerTest": make_type(
                parent_type="example.OuterTest",
                annotations=["@org.junit.jupiter.api.Nested"],
            ),
        },
        methods_by_class={
            "example.OuterTest.InnerTest": {
                "setUp()": make_callable(
                    signature="setUp()",
                    annotations=["@org.junit.jupiter.api.BeforeEach"],
                ),
            },
            "example.OuterTest": {
                "setUp()": make_callable(
                    signature="setUp()",
                    annotations=["@org.junit.jupiter.api.BeforeEach"],
                ),
            },
        },
    )

    fixture_methods = find_fixture_methods(
        analysis=analysis,
        reachable_methods={
            "example.OuterTest.InnerTest": ["setUp()"],
            "example.OuterTest": ["setUp()"],
        },
        fixture_annotations=SETUP_ANNOTATIONS,
    )

    assert _fixture_tuples(fixture_methods) == [
        ("example.OuterTest.InnerTest", "setUp()", False),
        ("example.OuterTest", "setUp()", False),
    ]


def test_fixture_discovery_effective_methods_respect_testng_group_filters() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.TestClass": make_type()},
        methods_by_class={
            "example.TestClass": {
                "testSmoke()": make_callable(
                    signature="testSmoke()",
                    annotations=['@org.testng.annotations.Test(groups = {"smoke"})'],
                ),
                "beforeShared()": make_callable(
                    signature="beforeShared()",
                    annotations=["@org.junit.jupiter.api.BeforeEach"],
                ),
                "beforeSmokeOnly()": make_callable(
                    signature="beforeSmokeOnly()",
                    annotations=[
                        '@org.testng.annotations.BeforeMethod(onlyForGroups = {"smoke"})'
                    ],
                ),
                "beforeRegressionOnly()": make_callable(
                    signature="beforeRegressionOnly()",
                    annotations=[
                        '@org.testng.annotations.BeforeMethod(onlyForGroups = {"regression"})'
                    ],
                ),
            }
        },
    )

    fixture_selection = get_effective_fixture_methods(
        analysis=analysis,
        qualified_class_name="example.TestClass",
        test_method_signature="testSmoke()",
        class_annotations=[],
        fixture_methods=[
            FixtureMethod(
                defining_class_name="example.TestClass",
                method_signature="beforeShared()",
            ),
            FixtureMethod(
                defining_class_name="example.TestClass",
                method_signature="beforeSmokeOnly()",
            ),
            FixtureMethod(
                defining_class_name="example.TestClass",
                method_signature="beforeRegressionOnly()",
            ),
        ],
        fixture_annotations=SETUP_ANNOTATIONS,
        class_scope_annotations=SETUP_CLASS_SCOPE_ANNOTATIONS,
    )

    assert _fixture_tuples(fixture_selection) == [
        ("example.TestClass", "beforeShared()", False),
        ("example.TestClass", "beforeSmokeOnly()", False),
    ]


def test_fixture_discovery_normalizes_fully_qualified_fixture_annotations() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.TestClass": make_type()},
        methods_by_class={
            "example.TestClass": {
                "setUp()": make_callable(
                    signature="setUp()",
                    annotations=["@org.junit.jupiter.api.BeforeEach"],
                )
            }
        },
    )

    fixture_methods = find_fixture_methods(
        analysis=analysis,
        reachable_methods={"example.TestClass": ["setUp()"]},
        fixture_annotations=SETUP_ANNOTATIONS,
    )

    assert _fixture_tuples(fixture_methods) == [("example.TestClass", "setUp()", False)]


def test_metrics_helpers_smoke_counts_and_sorts() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Service": make_type(),
            "example.TestSupport": make_type(),
        },
        methods_by_class={
            "example.Service": {
                "run()": make_callable(signature="run()", cyclomatic_complexity=3),
                "retry()": make_callable(signature="retry()", cyclomatic_complexity=2),
            },
            "example.TestSupport": {
                "fixture()": make_callable(
                    signature="fixture()", cyclomatic_complexity=1
                ),
            },
        },
    )

    method_count, cyclomatic_complexity = get_application_method_metrics(
        analysis,
        ["example.Service"],
    )

    assert method_count == 2
    assert cyclomatic_complexity == 5
    assert get_test_utility_method_count(analysis, ["example.TestSupport"]) == 1

    method_details = make_callable(
        signature="ordered()",
        call_sites=[
            make_call_site(method_name="late", start_line=3, start_column=1),
            make_call_site(method_name="early_col", start_line=1, start_column=2),
            make_call_site(method_name="early", start_line=1, start_column=10),
        ],
    )

    assert [
        call_site.method_name for call_site in get_call_sites_sorted(method_details)
    ] == ["early_col", "early", "late"]
