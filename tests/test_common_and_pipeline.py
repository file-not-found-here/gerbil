from __future__ import annotations

from typing import Any

import pytest
from cldk.models.java import JImport

from gerbil.analysis.shared import (
    CommonAnalysis,
    Reachability,
    MethodRef,
    build_expanded_call_site_grouping,
    iter_resolved_helpers,
)
from gerbil.analysis.shared.metrics_helpers import (
    get_call_sites_sorted,
    get_non_comment_lines,
)
from gerbil.analysis.runtime import FixtureMethod
from gerbil.analysis.schema import (
    AssertionAnalysis,
    AssertionRole,
    AssertionSummary,
    AuthHandling,
    CallSiteOriginKind,
    DependencyAnalysis,
    HttpAnalysis,
    HttpInteractionKind,
    HttpRequestRole,
    HttpResponseRole,
    LifecyclePhase,
    StateAnalysis,
    TestingFramework as Framework,
)
from gerbil.analysis.project import ProjectAnalysisInfo
from gerbil.analysis.test_class import TestClassAnalysisInfo as ClassAnalysisInfo
from gerbil.analysis.test_method import MethodAnalysisInfo
from tests.cldk_factories import (
    make_call_site,
    make_callable,
    make_import_declarations_by_file,
    make_type,
    make_variable_declaration,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def _all_http_interactions(result):
    return result.http.request_interactions


def _all_http_calls(result):
    return [
        interaction.http_call
        for interaction in result.http.request_interactions
        if interaction.http_call is not None
    ]


def _all_endpoint_candidates(result):
    return [
        interaction.endpoint_candidate
        for interaction in result.http.request_interactions
        if interaction.endpoint_candidate is not None
    ]


def _fixtures_for_phase(result, phase: LifecyclePhase):
    return [fixture for fixture in result.fixtures if fixture.phase == phase]


def _fixture_methods(
    methods_by_class: dict[str, list[str]],
    *,
    ambiguous_methods_by_class: dict[str, list[str]] | None = None,
) -> list[FixtureMethod]:
    ambiguous_lookup: set[tuple[str, str]] = {
        (class_name, method_signature)
        for class_name, method_signatures in (ambiguous_methods_by_class or {}).items()
        for method_signature in method_signatures
    }
    fixture_methods: list[FixtureMethod] = []
    for class_name, method_signatures in methods_by_class.items():
        for method_signature in method_signatures:
            fixture_methods.append(
                FixtureMethod(
                    defining_class_name=class_name,
                    method_signature=method_signature,
                    is_ambiguous=(class_name, method_signature) in ambiguous_lookup,
                )
            )
    return fixture_methods


def _resolved_helper_map(
    *,
    analysis: FakeJavaAnalysis,
    qualified_class_name: str,
    method_signature: str,
    add_extended_class: bool = True,
    test_utility_classes: list[str] | None = None,
    max_depth: int = 1,
    include_declaring_class_helpers: bool = False,
) -> dict[str, list[str]]:
    reachability = Reachability(analysis)
    resolve_helper, load_call_sites = reachability.build_helper_resolver(
        qualified_class_name=qualified_class_name,
        add_extended_class=add_extended_class,
        test_utility_classes=test_utility_classes,
    )
    method_details = analysis.get_method(qualified_class_name, method_signature)
    assert method_details is not None

    grouping = build_expanded_call_site_grouping(
        call_sites=list(method_details.call_sites),
        owner=MethodRef(
            defining_class_name=qualified_class_name,
            method_signature=method_signature,
        ),
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        max_helper_depth=max_depth,
    )
    helper_map: dict[str, list[str]] = {}
    for helper_ref in iter_resolved_helpers(grouping):
        if (
            not include_declaring_class_helpers
            and helper_ref.defining_class_name == qualified_class_name
        ):
            continue
        helper_map.setdefault(helper_ref.defining_class_name, []).append(
            helper_ref.method_signature
        )
    return helper_map


def test_common_analysis_ncloc() -> None:
    analysis = CommonAnalysis(FakeJavaAnalysis())

    assert analysis.get_ncloc("void test()", "{}") == 0

    ncloc = analysis.get_ncloc(
        "void run()\n",
        "{\n// ignore\nint count = 1;\nreturn;\n}",
    )

    assert ncloc == 5


def test_common_analysis_non_comment_lines_handles_literals_and_inline_blocks() -> None:
    body = '''{
String url = "http://example.com"; // trailing comment
int first = 1; /* block */ int second = 2;
String payload = """
line // not comment
line /* also not comment */
""";
/* full line block comment */
}
'''
    non_comment_lines = get_non_comment_lines(body)

    assert 'String url = "http://example.com";' in non_comment_lines
    assert "int first = 1;  int second = 2;" in non_comment_lines
    assert "line // not comment" in non_comment_lines
    assert "line /* also not comment */" in non_comment_lines

    analysis = CommonAnalysis(FakeJavaAnalysis())
    assert analysis.get_ncloc("void run()\n", body) == 9


def test_common_analysis_detects_frameworks_from_imports_and_annotations() -> None:
    classes = {
        "example.WebTest": make_type(annotations=["@WebMvcTest"]),
    }
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class={"example.WebTest": {}},
        java_files={"example.WebTest": "src/test/java/example/WebTest.java"},
        import_declarations_by_file={
            "src/test/java/example/WebTest.java": [
                "org.junit.jupiter.api.Test",
                "org.springframework.test.web.servlet.MockMvc",
            ]
        },
    )

    frameworks = CommonAnalysis(analysis).get_testing_frameworks_for_class(
        "example.WebTest"
    )

    assert frameworks == [Framework.JUNIT5, Framework.SPRING_TEST]


def test_common_analysis_is_test_method_supports_junit3_and_testng() -> None:
    classes = {
        "example.LegacyTest": make_type(extends_list=["junit.framework.TestCase"]),
        "example.TestNgSuite": make_type(annotations=["@org.testng.annotations.Test"]),
        "example.Junit5Suite": make_type(),
    }
    methods_by_class = {
        "example.LegacyTest": {
            "testLegacy()": make_callable(
                signature="testLegacy()",
                modifiers=["public"],
                return_type="void",
            )
        },
        "example.TestNgSuite": {
            "verifyContract()": make_callable(
                signature="verifyContract()",
                modifiers=["public"],
                annotations=[],
            )
        },
        "example.Junit5Suite": {
            "runsWithQualifiedAnnotation()": make_callable(
                signature="runsWithQualifiedAnnotation()",
                annotations=["@org.junit.jupiter.api.Test"],
            )
        },
    }
    analysis = FakeJavaAnalysis(classes=classes, methods_by_class=methods_by_class)
    common = CommonAnalysis(analysis)

    assert common.is_test_method(
        "testLegacy()", "example.LegacyTest", [Framework.JUNIT3]
    )
    assert common.is_test_method(
        "verifyContract()", "example.TestNgSuite", [Framework.TESTNG]
    )
    assert common.is_test_method(
        "runsWithQualifiedAnnotation()",
        "example.Junit5Suite",
        [Framework.JUNIT5],
    )


def test_common_analysis_detects_frameworks_from_superclass_imports() -> None:
    classes = {
        "example.ChildNgSuite": make_type(extends_list=["example.BaseNgSuite"]),
        "example.BaseNgSuite": make_type(annotations=["@Test"]),
        "org.testng.annotations.Test": make_type(annotations=["@Inherited"]),
    }
    methods_by_class = {
        "example.ChildNgSuite": {
            "verifyContract()": make_callable(
                signature="verifyContract()",
                modifiers=["public"],
            )
        },
        "example.BaseNgSuite": {},
    }
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class=methods_by_class,
        java_files={
            "example.ChildNgSuite": "src/test/java/example/ChildNgSuite.java",
            "example.BaseNgSuite": "src/test/java/example/BaseNgSuite.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ChildNgSuite.java": [],
            "src/test/java/example/BaseNgSuite.java": ["org.testng.annotations.Test"],
        },
    )
    common = CommonAnalysis(analysis)

    frameworks = common.get_testing_frameworks_for_class("example.ChildNgSuite")

    assert frameworks == [Framework.TESTNG]
    assert common.is_test_method("verifyContract()", "example.ChildNgSuite", frameworks)


def test_get_superclass_chain_resolves_shortname_imported_base() -> None:
    child_file = "src/test/java/example/ChildTest.java"
    analysis = FakeJavaAnalysis(
        classes={
            "example.ChildTest": make_type(extends_list=["BaseApiTest"]),
            "com.example.BaseApiTest": make_type(),
        },
        java_files={"example.ChildTest": child_file},
        import_declarations_by_file={child_file: ["com.example.BaseApiTest"]},
    )
    common = CommonAnalysis(analysis)

    assert common.get_superclass_chain("example.ChildTest") == [
        "com.example.BaseApiTest"
    ]


def test_get_superclass_chain_preserves_fqn_extends_behavior() -> None:
    child_file = "src/test/java/example/Child.java"
    parent_file = "src/test/java/example/Parent.java"
    grandparent_file = "src/test/java/example/GrandParent.java"
    analysis = FakeJavaAnalysis(
        classes={
            "example.Child": make_type(extends_list=["example.Parent"]),
            "example.Parent": make_type(extends_list=["example.GrandParent"]),
            "example.GrandParent": make_type(),
        },
        java_files={
            "example.Child": child_file,
            "example.Parent": parent_file,
            "example.GrandParent": grandparent_file,
        },
        import_declarations_by_file={
            child_file: [],
            parent_file: [],
            grandparent_file: [],
        },
    )
    common = CommonAnalysis(analysis)

    assert common.get_superclass_chain("example.Child") == [
        "example.Parent",
        "example.GrandParent",
    ]


def test_common_analysis_is_test_method_uses_direct_imports_for_method_annotations() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={
            "example.ChildSuite": make_type(extends_list=["example.BaseSuite"]),
            "example.BaseSuite": make_type(),
        },
        methods_by_class={
            "example.ChildSuite": {
                "looksLikeTest()": make_callable(
                    signature="looksLikeTest()",
                    annotations=["@Test"],
                )
            },
            "example.BaseSuite": {},
        },
        java_files={
            "example.ChildSuite": "src/test/java/example/ChildSuite.java",
            "example.BaseSuite": "src/test/java/example/BaseSuite.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ChildSuite.java": ["com.example.Test"],
            "src/test/java/example/BaseSuite.java": ["org.junit.jupiter.api.Test"],
        },
    )
    common = CommonAnalysis(analysis)

    assert not common.is_test_method(
        "looksLikeTest()",
        "example.ChildSuite",
        [Framework.JUNIT5],
    )


def test_common_analysis_caches_effective_inheritance_resolution() -> None:
    class CountingFakeJavaAnalysis(FakeJavaAnalysis):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.get_class_calls: int = 0
            self.get_java_compilation_unit_calls: int = 0

        def get_class(self, qualified_class_name: str):  # type: ignore[override]
            self.get_class_calls += 1
            return super().get_class(qualified_class_name)

        def get_java_compilation_unit(self, java_file: str):  # type: ignore[override]
            self.get_java_compilation_unit_calls += 1
            return super().get_java_compilation_unit(java_file)

    analysis = CountingFakeJavaAnalysis(
        classes={
            "example.ChildApiTest": make_type(extends_list=["example.BaseApiTest"]),
            "example.BaseApiTest": make_type(annotations=["@WebMvcTest"]),
            "org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest": make_type(
                annotations=["@Inherited"]
            ),
        },
        methods_by_class={
            "example.ChildApiTest": {},
            "example.BaseApiTest": {},
        },
        java_files={
            "example.ChildApiTest": "src/test/java/example/ChildApiTest.java",
            "example.BaseApiTest": "src/test/java/example/BaseApiTest.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ChildApiTest.java": [],
            "src/test/java/example/BaseApiTest.java": [
                "org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest"
            ],
        },
    )
    common = CommonAnalysis(analysis)

    common.resolve_effective_class_annotations("example.ChildApiTest")
    common.get_effective_class_imports("example.ChildApiTest")
    first_get_class_calls = analysis.get_class_calls
    first_compilation_unit_calls = analysis.get_java_compilation_unit_calls

    common.resolve_effective_class_annotations("example.ChildApiTest")
    common.get_effective_class_imports("example.ChildApiTest")

    assert analysis.get_class_calls == first_get_class_calls
    assert analysis.get_java_compilation_unit_calls == first_compilation_unit_calls


def test_common_analysis_categorize_classes_and_metrics() -> None:
    classes = {
        "example.ApiTest": make_type(),
        "example.TestUtility": make_type(),
        "example.Service": make_type(),
    }
    methods_by_class = {
        "example.ApiTest": {
            "shouldFetchUser()": make_callable(
                signature="shouldFetchUser()",
                annotations=["@Test"],
                cyclomatic_complexity=1,
            )
        },
        "example.TestUtility": {
            "createFixture()": make_callable(
                signature="createFixture()",
                cyclomatic_complexity=1,
            )
        },
        "example.Service": {
            "run()": make_callable(signature="run()", cyclomatic_complexity=2),
            "calculate()": make_callable(
                signature="calculate()", cyclomatic_complexity=3
            ),
        },
    }
    java_files = {
        "example.ApiTest": "src/test/java/example/ApiTest.java",
        "example.TestUtility": "src/test/java/example/TestUtility.java",
        "example.Service": "src/main/java/example/Service.java",
    }
    import_declarations_by_file = {
        "src/test/java/example/ApiTest.java": ["org.junit.jupiter.api.Test"],
        "src/test/java/example/TestUtility.java": [],
        "src/main/java/example/Service.java": [],
    }
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class=methods_by_class,
        java_files=java_files,
        import_declarations_by_file=import_declarations_by_file,
    )
    common = CommonAnalysis(analysis)

    test_classes, application_classes, utility_classes = common.categorize_classes()

    assert test_classes == {"example.ApiTest": ["shouldFetchUser()"]}
    assert application_classes == ["example.Service"]
    assert utility_classes == ["example.TestUtility"]

    app_method_count, app_complexity = common.get_application_method_metrics(
        application_classes
    )

    assert app_method_count == 2
    assert app_complexity == 5
    assert common.get_test_utility_method_count(utility_classes) == 1


def test_common_analysis_supports_configurable_test_dirs() -> None:
    classes = {
        "example.IntegrationUtility": make_type(),
        "example.Service": make_type(),
    }
    methods_by_class = {
        "example.IntegrationUtility": {
            "seedData()": make_callable(signature="seedData()"),
        },
        "example.Service": {
            "run()": make_callable(signature="run()"),
        },
    }
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class=methods_by_class,
        java_files={
            "example.IntegrationUtility": (
                "src/integrationTest/java/example/IntegrationUtility.java"
            ),
            "example.Service": "src/main/java/example/Service.java",
        },
        import_declarations_by_file={
            "src/integrationTest/java/example/IntegrationUtility.java": [],
            "src/main/java/example/Service.java": [],
        },
    )

    default_common = CommonAnalysis(analysis)
    _, default_application_classes, default_utility_classes = (
        default_common.categorize_classes()
    )

    assert "example.IntegrationUtility" not in default_application_classes
    assert "example.IntegrationUtility" in default_utility_classes

    configured_common = CommonAnalysis(
        analysis,
        test_dirs=("src/test/java",),
    )
    _, configured_application_classes, configured_utility_classes = (
        configured_common.categorize_classes()
    )

    assert "example.IntegrationUtility" in configured_application_classes
    assert "example.IntegrationUtility" not in configured_utility_classes


def test_common_analysis_counts_objects_and_sorts_call_sites() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(method_name="b", start_line=3, start_column=5),
            make_call_site(method_name="a", start_line=3, start_column=1),
            make_call_site(method_name="Ctor", start_line=2, is_constructor_call=True),
        ],
        variable_declarations=[
            make_variable_declaration(initializer="new User()"),
            make_variable_declaration(initializer="existingValue"),
        ],
    )

    assert CommonAnalysis.count_objects_created(method) == 1
    assert [call_site.method_name for call_site in get_call_sites_sorted(method)] == [
        "Ctor",
        "a",
        "b",
    ]


def test_reachability_helper_method_resolution_traverses_valid_classes() -> None:
    classes = {
        "example.ChildTest": make_type(extends_list=["example.BaseTest"]),
        "example.BaseTest": make_type(),
        "example.Util": make_type(),
    }
    methods_by_class = {
        "example.ChildTest": {
            "testFlow()": make_callable(
                signature="testFlow()",
                call_sites=[
                    make_call_site(
                        method_name="helper",
                        callee_signature="helper()",
                        receiver_expr="this",
                    ),
                    make_call_site(
                        method_name="sharedUtil",
                        receiver_type="example.Util",
                        callee_signature="sharedUtil()",
                    ),
                ],
            ),
            "helper()": make_callable(
                signature="helper()",
                call_sites=[
                    make_call_site(
                        method_name="parentHelper",
                        receiver_type="example.BaseTest",
                        callee_signature="parentHelper()",
                    )
                ],
            ),
        },
        "example.BaseTest": {
            "parentHelper()": make_callable(signature="parentHelper()"),
        },
        "example.Util": {
            "sharedUtil()": make_callable(
                signature="sharedUtil()",
                call_sites=[
                    make_call_site(method_name="leaf", callee_signature="leaf()")
                ],
            ),
            "leaf()": make_callable(signature="leaf()"),
        },
    }
    analysis = FakeJavaAnalysis(classes=classes, methods_by_class=methods_by_class)

    helper_methods = _resolved_helper_map(
        analysis=analysis,
        qualified_class_name="example.ChildTest",
        method_signature="testFlow()",
        add_extended_class=True,
        test_utility_classes=["example.Util"],
    )

    assert set(helper_methods) == {"example.BaseTest", "example.Util"}
    assert helper_methods["example.BaseTest"] == ["parentHelper()"]
    assert set(helper_methods["example.Util"]) == {"sharedUtil()", "leaf()"}


def test_reachability_helper_method_resolution_supports_configurable_depth() -> None:
    classes = {
        "example.ChildTest": make_type(),
        "example.Util": make_type(),
    }
    methods_by_class = {
        "example.ChildTest": {
            "testFlow()": make_callable(
                signature="testFlow()",
                call_sites=[
                    make_call_site(
                        method_name="sharedUtil",
                        receiver_type="example.Util",
                        callee_signature="sharedUtil()",
                    )
                ],
            ),
        },
        "example.Util": {
            "sharedUtil()": make_callable(
                signature="sharedUtil()",
                call_sites=[
                    make_call_site(method_name="leaf", callee_signature="leaf()")
                ],
            ),
            "leaf()": make_callable(signature="leaf()"),
        },
    }
    analysis = FakeJavaAnalysis(classes=classes, methods_by_class=methods_by_class)

    depth_zero = _resolved_helper_map(
        analysis=analysis,
        qualified_class_name="example.ChildTest",
        method_signature="testFlow()",
        test_utility_classes=["example.Util"],
        max_depth=0,
    )
    depth_one = _resolved_helper_map(
        analysis=analysis,
        qualified_class_name="example.ChildTest",
        method_signature="testFlow()",
        test_utility_classes=["example.Util"],
        max_depth=1,
    )
    depth_two = _resolved_helper_map(
        analysis=analysis,
        qualified_class_name="example.ChildTest",
        method_signature="testFlow()",
        test_utility_classes=["example.Util"],
        max_depth=2,
    )

    assert depth_zero == {"example.Util": ["sharedUtil()"]}
    assert set(depth_one["example.Util"]) == {"sharedUtil()", "leaf()"}
    assert set(depth_two["example.Util"]) == {"sharedUtil()", "leaf()"}


def test_reachability_resolves_unqualified_calls_to_inherited_helpers() -> None:
    classes = {
        "example.ChildIntegrationTest": make_type(
            extends_list=["example.BaseIntegrationTest"]
        ),
        "example.BaseIntegrationTest": make_type(),
    }
    methods_by_class = {
        "example.ChildIntegrationTest": {
            "testFlow()": make_callable(
                signature="testFlow()",
                call_sites=[
                    make_call_site(
                        method_name="sendLineage",
                        callee_signature="sendLineage()",
                        receiver_expr="this",
                    )
                ],
            )
        },
        "example.BaseIntegrationTest": {
            "sendLineage()": make_callable(signature="sendLineage()"),
        },
    }
    analysis = FakeJavaAnalysis(classes=classes, methods_by_class=methods_by_class)

    helper_methods = _resolved_helper_map(
        analysis=analysis,
        qualified_class_name="example.ChildIntegrationTest",
        method_signature="testFlow()",
        add_extended_class=True,
        max_depth=1,
    )

    assert helper_methods == {"example.BaseIntegrationTest": ["sendLineage()"]}


def test_reachability_avoids_reexpanding_helper_cycles() -> None:
    classes = {
        "example.ChildTest": make_type(),
        "example.Util": make_type(),
    }
    methods_by_class = {
        "example.ChildTest": {
            "testFlow()": make_callable(
                signature="testFlow()",
                call_sites=[
                    make_call_site(
                        method_name="a",
                        receiver_type="example.Util",
                        callee_signature="a()",
                    )
                ],
            )
        },
        "example.Util": {
            "a()": make_callable(
                signature="a()",
                call_sites=[make_call_site(method_name="b", callee_signature="b()")],
            ),
            "b()": make_callable(
                signature="b()",
                call_sites=[make_call_site(method_name="a", callee_signature="a()")],
            ),
        },
    }
    analysis = FakeJavaAnalysis(classes=classes, methods_by_class=methods_by_class)

    helper_methods = _resolved_helper_map(
        analysis=analysis,
        qualified_class_name="example.ChildTest",
        method_signature="testFlow()",
        test_utility_classes=["example.Util"],
        max_depth=6,
    )

    assert helper_methods == {"example.Util": ["a()", "b()"]}


def test_reachability_helper_method_resolution_deduplicates_repeated_helpers() -> None:
    classes = {
        "example.ChildTest": make_type(),
        "example.Util": make_type(),
    }
    methods_by_class = {
        "example.ChildTest": {
            "testFlow()": make_callable(
                signature="testFlow()",
                call_sites=[
                    make_call_site(
                        method_name="sharedUtil",
                        receiver_type="example.Util",
                        callee_signature="sharedUtil()",
                        start_line=10,
                    ),
                    make_call_site(
                        method_name="sharedUtil",
                        receiver_type="example.Util",
                        callee_signature="sharedUtil()",
                        start_line=20,
                    ),
                ],
            ),
        },
        "example.Util": {
            "sharedUtil()": make_callable(signature="sharedUtil()"),
        },
    }
    analysis = FakeJavaAnalysis(classes=classes, methods_by_class=methods_by_class)

    helper_methods = _resolved_helper_map(
        analysis=analysis,
        qualified_class_name="example.ChildTest",
        method_signature="testFlow()",
        test_utility_classes=["example.Util"],
        max_depth=1,
    )

    assert helper_methods == {"example.Util": ["sharedUtil()"]}


def test_test_method_analysis_info_aggregates_metrics_and_labels() -> None:
    classes = {
        "example.ApiTest": make_type(
            annotations=[
                "@org.springframework.boot.test.context.SpringBootTest",
            ]
        ),
        "example.TestUtil": make_type(),
    }
    methods_by_class = {
        "example.ApiTest": {
            "beforeEach()": make_callable(
                signature="beforeEach()",
                annotations=["@BeforeEach"],
                call_sites=[
                    make_call_site(
                        method_name="postForEntity",
                        receiver_type="org.springframework.boot.test.web.client.TestRestTemplate",
                        argument_expr=['"/seed/users"'],
                        start_line=3,
                    )
                ],
            ),
            "afterEach()": make_callable(
                signature="afterEach()",
                annotations=["@AfterEach"],
                call_sites=[
                    make_call_site(
                        method_name="delete",
                        receiver_type="org.springframework.boot.test.web.client.TestRestTemplate",
                        argument_expr=['"/seed/users/1"'],
                        start_line=4,
                    )
                ],
            ),
            "testGetUser()": make_callable(
                signature="testGetUser()",
                annotations=[
                    "@Test",
                ],
                declaration="void testGetUser()",
                code="{\nassertEquals(200, 200);\n}",
                cyclomatic_complexity=2,
                call_sites=[
                    make_call_site(
                        method_name="Ctor",
                        is_constructor_call=True,
                        start_line=1,
                    ),
                    make_call_site(
                        method_name="getForEntity",
                        receiver_type="org.springframework.boot.test.web.client.TestRestTemplate",
                        argument_expr=['"/api/users"'],
                        start_line=10,
                    ),
                    make_call_site(
                        method_name="assertEquals",
                        argument_expr=["200", "200"],
                        start_line=11,
                    ),
                    make_call_site(
                        method_name="invokeHelper",
                        receiver_type="example.TestUtil",
                        callee_signature="preparePayload()",
                        start_line=12,
                    ),
                ],
                variable_declarations=[
                    make_variable_declaration(initializer="new Request()")
                ],
            ),
        },
        "example.TestUtil": {
            "preparePayload()": make_callable(
                signature="preparePayload()",
                declaration="void preparePayload()",
                code="{\nint value = 1;\n}",
                cyclomatic_complexity=3,
                variable_declarations=[
                    make_variable_declaration(initializer="new Payload()")
                ],
            )
        },
    }
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class=methods_by_class,
        java_files={
            "example.ApiTest": "src/test/java/example/ApiTest.java",
            "example.TestUtil": "src/test/java/example/TestUtil.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": [
                "org.junit.jupiter.api.Test",
                "org.springframework.boot.test.web.client.TestRestTemplate",
            ],
            "src/test/java/example/TestUtil.java": [],
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        test_utility_classes=["example.TestUtil"],
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5, Framework.SPRING_TEST],
        qualified_class_name="example.ApiTest",
        method_signature="testGetUser()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=_fixture_methods({"example.ApiTest": ["afterEach()"]}),
    )

    assert result.is_api_test is True
    assert result.local_metrics.assertion_count == 1
    assert result.expanded_metrics.helper_method_count == 1
    assert result.expanded_metrics.ncloc > result.local_metrics.ncloc
    assert result.expanded_metrics.cyclomatic_complexity == 5
    assert result.expanded_metrics.helper_method_ncloc > 0
    assert result.local_metrics.number_of_objects_created == 1
    assert result.expanded_metrics.number_of_objects_created == 1
    assert {endpoint.source for endpoint in _all_endpoint_candidates(result)} == {
        "call-site",
    }
    assert (
        len(
            [
                call
                for call in _all_http_calls(result)
                if call.request_role == HttpRequestRole.EVENT
                and call.path == "/api/users"
            ]
        )
        == 1
    )
    assert [
        interaction.origin.phase for interaction in _all_http_interactions(result)
    ] == [
        LifecyclePhase.SETUP,
        LifecyclePhase.TEST,
        LifecyclePhase.TEARDOWN,
    ]
    assert [
        interaction.origin.kind for interaction in _all_http_interactions(result)
    ] == [
        CallSiteOriginKind.FIXTURE,
        CallSiteOriginKind.TEST_METHOD,
        CallSiteOriginKind.FIXTURE,
    ]
    assert [
        interaction.origin.entry_method_signature
        for interaction in _all_http_interactions(result)
    ] == [
        "beforeEach()",
        "testGetUser()",
        "afterEach()",
    ]
    assert [
        interaction.http_call.path
        for interaction in _all_http_interactions(result)
        if interaction.http_call is not None
    ] == [
        "/seed/users",
        "/api/users",
        "/seed/users/1",
    ]
    assert [interaction.kind for interaction in result.http.http_interactions] == [
        HttpInteractionKind.REQUEST,
        HttpInteractionKind.REQUEST,
        HttpInteractionKind.REQUEST,
    ]
    assert [
        interaction.request_interaction
        for interaction in result.http.http_interactions
        if interaction.kind == HttpInteractionKind.REQUEST
    ] == result.http.request_interactions
    assert [
        interaction.verification_interaction
        for interaction in result.http.http_interactions
        if interaction.kind == HttpInteractionKind.VERIFICATION
    ] == result.http.verification_interactions
    endpoint_phases = [
        interaction.origin.phase
        for interaction in _all_http_interactions(result)
        if interaction.endpoint_candidate is not None
    ]
    assert endpoint_phases.count(LifecyclePhase.TEST) == 1
    assert LifecyclePhase.SETUP in endpoint_phases
    assert LifecyclePhase.TEARDOWN in endpoint_phases
    assert result.assertions.summary.total_count == 1
    assert result.state.preconditions is not None
    assert result.http.call_sequence
    assert [
        analysis.method_signature
        for analysis in _fixtures_for_phase(result, LifecyclePhase.SETUP)
    ] == ["beforeEach()"]
    assert [
        analysis.method_signature
        for analysis in _fixtures_for_phase(result, LifecyclePhase.TEARDOWN)
    ] == ["afterEach()"]
    assert result.identity.parameterization is None
    assert result.ambiguous_fixture_group_methods == []
    assert result.http.request_dispatch.labels == ["local-network"]
    assert result.http.request_dispatch.signals == {
        "local-network": ["real-http-local"]
    }
    assert result.http.request_dispatch.local_request_count == 3
    assert result.http.request_dispatch.external_request_count == 0


def test_test_method_analysis_info_uses_inherited_class_annotations_for_labels() -> (
    None
):
    classes = {
        "example.ChildApiTest": make_type(extends_list=["example.BaseApiTest"]),
        "example.BaseApiTest": make_type(annotations=["@WebMvcTest"]),
        "org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest": make_type(
            annotations=["@Inherited"]
        ),
    }
    methods_by_class = {
        "example.ChildApiTest": {
            "testEndpoint()": make_callable(
                signature="testEndpoint()",
                annotations=["@Test"],
            )
        },
        "example.BaseApiTest": {},
    }
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class=methods_by_class,
        java_files={
            "example.ChildApiTest": "src/test/java/example/ChildApiTest.java",
            "example.BaseApiTest": "src/test/java/example/BaseApiTest.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ChildApiTest.java": ["org.junit.jupiter.api.Test"],
            "src/test/java/example/BaseApiTest.java": [
                "org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest"
            ],
        },
    )
    common = CommonAnalysis(analysis)
    frameworks = common.get_testing_frameworks_for_class("example.ChildApiTest")

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=frameworks,
        qualified_class_name="example.ChildApiTest",
        method_signature="testEndpoint()",
        setup_methods=[],
        teardown_methods=[],
    )

    assert result.is_api_test is False
    assert result.http == HttpAnalysis()
    assert result.assertions == AssertionAnalysis()
    assert result.dependencies == DependencyAnalysis()
    assert result.state == StateAnalysis()
    assert result.fixtures == []
    assert result.ambiguous_fixture_group_methods == []


def test_test_method_analysis_info_returns_minimal_shape_for_missing_method() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.Missing": make_type()}, methods_by_class={}
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=[],
        qualified_class_name="example.Missing",
        method_signature="doesNotExist()",
        setup_methods=[],
        teardown_methods=[],
    )

    assert result.identity.method_signature == "doesNotExist()"
    assert result.identity.method_declaration == ""
    assert result.is_api_test is False


def test_test_method_analysis_info_precondition_summary_is_annotation_only() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(annotations=["@Sql"]),
        },
        methods_by_class={
            "example.ApiTest": {
                "beforeEach()": make_callable(
                    signature="beforeEach()",
                    annotations=["@BeforeEach"],
                    call_sites=[make_call_site(method_name="save", start_line=10)],
                ),
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/api/users"'],
                            start_line=20,
                        )
                    ],
                ),
            }
        },
        java_files={
            "example.ApiTest": "src/test/java/example/ApiTest.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": [
                "org.springframework.test.context.jdbc.Sql",
            ],
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=[],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=[],
    )

    types = {p.type.value for p in result.state.preconditions.preconditions}
    assert "db-seeding" in types
    annotation_evidence = {
        p.evidence
        for p in result.state.preconditions.preconditions
        if p.type.value == "db-seeding" and p.source.value == "annotation"
    }
    assert "@Sql" in annotation_evidence


def test_test_method_analysis_info_includes_ambiguous_and_teardown_in_runtime_labels() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "beforeAmbiguous()": make_callable(
                    signature="beforeAmbiguous()",
                    annotations=["@BeforeMethod(onlyForGroups = SOME_GROUPS)"],
                    call_sites=[
                        make_call_site(
                            method_name="when",
                            receiver_type="org.mockserver.client.MockServerClient",
                            start_line=5,
                        )
                    ],
                ),
                "afterEach()": make_callable(
                    signature="afterEach()",
                    annotations=["@AfterEach"],
                    call_sites=[
                        make_call_site(
                            method_name="authenticate",
                            receiver_type="org.springframework.security.authentication.AuthenticationManager",
                            callee_signature="AuthenticationManager.authenticate(Authentication)",
                            start_line=20,
                        )
                    ],
                ),
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/api/users"'],
                            start_line=40,
                        )
                    ],
                ),
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=[],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=_fixture_methods(
            {"example.ApiTest": ["beforeAmbiguous()"]},
            ambiguous_methods_by_class={"example.ApiTest": ["beforeAmbiguous()"]},
        ),
        teardown_methods=_fixture_methods({"example.ApiTest": ["afterEach()"]}),
    )

    assert "virtualized" in result.dependencies.strategy.labels
    assert result.http.auth_handling.label == AuthHandling.REAL_FLOW.value
    assert [
        analysis.method_signature
        for analysis in _fixtures_for_phase(result, LifecyclePhase.SETUP)
    ] == ["beforeAmbiguous()"]
    assert [
        analysis.method_signature
        for analysis in _fixtures_for_phase(result, LifecyclePhase.TEARDOWN)
    ] == ["afterEach()"]
    assert result.http.call_sequence
    assert [
        (
            note.phase.value,
            note.defining_class_name,
            note.method_signature,
            note.reason,
        )
        for note in result.ambiguous_fixture_group_methods
    ] == [
        (
            "setup",
            "example.ApiTest",
            "beforeAmbiguous()",
            "ambiguous-group-filter",
        )
    ]


def test_test_method_analysis_info_includes_ambiguous_teardown_in_runtime_labels() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "afterAmbiguous()": make_callable(
                    signature="afterAmbiguous()",
                    annotations=["@AfterMethod(onlyForGroups = GROUPS)"],
                    call_sites=[
                        make_call_site(
                            method_name="authenticate",
                            receiver_type="org.springframework.security.authentication.AuthenticationManager",
                            callee_signature="AuthenticationManager.authenticate(Authentication)",
                            start_line=30,
                        )
                    ],
                ),
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/api/users"'],
                            start_line=40,
                        )
                    ],
                ),
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=[],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=[],
        teardown_methods=_fixture_methods(
            {"example.ApiTest": ["afterAmbiguous()"]},
            ambiguous_methods_by_class={"example.ApiTest": ["afterAmbiguous()"]},
        ),
    )

    assert result.http.auth_handling.label == AuthHandling.REAL_FLOW.value
    assert [
        (
            note.phase.value,
            note.defining_class_name,
            note.method_signature,
            note.reason,
        )
        for note in result.ambiguous_fixture_group_methods
    ] == [
        (
            "teardown",
            "example.ApiTest",
            "afterAmbiguous()",
            "ambiguous-group-filter",
        )
    ]


def test_test_method_analysis_info_detects_api_from_teardown_http_runtime() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "afterEach()": make_callable(
                    signature="afterEach()",
                    annotations=["@AfterEach"],
                    call_sites=[
                        make_call_site(
                            method_name="postForEntity",
                            receiver_type="org.springframework.boot.test.web.client.TestRestTemplate",
                            argument_expr=['"/cleanup/users"'],
                            start_line=12,
                        )
                    ],
                ),
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                ),
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=[],
        teardown_methods=_fixture_methods({"example.ApiTest": ["afterEach()"]}),
    )

    assert result.is_api_test is True
    assert [
        interaction
        for interaction in _all_http_interactions(result)
        if interaction.origin.kind == CallSiteOriginKind.TEST_METHOD
    ] == []
    assert len(_all_http_calls(result)) == 1
    assert _all_http_interactions(result)[0].origin.phase == LifecyclePhase.TEARDOWN
    assert _all_http_calls(result)[0].path == "/cleanup/users"


def test_test_method_analysis_info_preserves_resolved_static_import_receivers() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "beforeEach()": make_callable(
                    signature="beforeEach()",
                    annotations=["@BeforeEach"],
                    call_sites=[
                        make_call_site(
                            method_name="get",
                            argument_expr=['"/setup/users"'],
                            is_static_call=True,
                            start_line=12,
                        )
                    ],
                ),
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/api/users"'],
                            start_line=30,
                        )
                    ],
                ),
            }
        },
        java_files={"example.ApiTest": "src/test/java/example/ApiTest.java"},
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": [
                JImport(
                    path="org.junit.jupiter.api.BeforeEach",
                    is_static=False,
                    is_wildcard=False,
                ),
                JImport(
                    path="org.junit.jupiter.api.Test",
                    is_static=False,
                    is_wildcard=False,
                ),
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
            ]
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=[],
    )

    resolved_receiver = (
        "org.springframework.test.web.servlet.request.MockMvcRequestBuilders"
    )
    builder_calls = [
        call
        for call in _all_http_calls(result)
        if call.request_role == HttpRequestRole.BUILDER
    ]

    assert len(builder_calls) == 1
    assert builder_calls[0].receiver_type == resolved_receiver
    assert len(_fixtures_for_phase(result, LifecyclePhase.SETUP)) == 1
    assert (
        _fixtures_for_phase(result, LifecyclePhase.SETUP)[0]
        .request_interactions[0]
        .http_call.receiver_type
        == resolved_receiver
    )


def test_test_method_analysis_info_expands_static_imported_test_utility_helper() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(),
            "example.TestUtil": make_type(),
        },
        methods_by_class={
            "example.ApiTest": {
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="seedRequest",
                            callee_signature="seedRequest()",
                            is_static_call=True,
                            start_line=20,
                        ),
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/api/users"'],
                            start_line=30,
                        ),
                    ],
                ),
            },
            "example.TestUtil": {
                "seedRequest()": make_callable(
                    signature="seedRequest()",
                    call_sites=[
                        make_call_site(
                            method_name="get",
                            argument_expr=['"/seed/users"'],
                            is_static_call=True,
                            start_line=8,
                        )
                    ],
                ),
            },
        },
        java_files={
            "example.ApiTest": "src/test/java/example/ApiTest.java",
            "example.TestUtil": "src/test/java/example/TestUtil.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": [
                JImport(
                    path="org.junit.jupiter.api.Test",
                    is_static=False,
                    is_wildcard=False,
                ),
                JImport(
                    path="example.TestUtil.seedRequest",
                    is_static=True,
                    is_wildcard=False,
                ),
            ],
            "src/test/java/example/TestUtil.java": [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
            ],
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        test_utility_classes=["example.TestUtil"],
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=[],
        teardown_methods=[],
    )

    http_calls = _all_http_calls(result)
    seed_calls = [call for call in http_calls if call.path == "/seed/users"]

    assert len(seed_calls) == 1
    assert (
        seed_calls[0].receiver_type
        == "org.springframework.test.web.servlet.request.MockMvcRequestBuilders"
    )
    assert result.expanded_metrics.helper_method_count == 1
    assert result.http.call_sequence


def test_test_method_analysis_info_ignores_builder_calls_for_api_count() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.BuilderOnlyTest": make_type()},
        methods_by_class={
            "example.BuilderOnlyTest": {
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="header",
                            receiver_type="io.restassured.specification.RequestSpecification",
                            argument_expr=['"Authorization"', '"Bearer test-token"'],
                            start_line=10,
                        )
                    ],
                ),
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.BuilderOnlyTest",
        method_signature="testEndpoint()",
        setup_methods=[],
        teardown_methods=[],
    )

    assert result.is_api_test is False
    assert result.http == HttpAnalysis()
    assert result.assertions == AssertionAnalysis()
    assert result.dependencies == DependencyAnalysis()
    assert result.state == StateAnalysis()
    assert result.fixtures == []


def test_test_method_analysis_info_uses_runtime_assertions_for_labels() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "afterEach()": make_callable(
                    signature="afterEach()",
                    annotations=["@AfterEach"],
                    call_sites=[
                        make_call_site(
                            method_name="statusCode",
                            argument_expr=["500"],
                            start_line=12,
                        ),
                        make_call_site(
                            method_name="assertTimeout",
                            argument_expr=["Duration.ofMillis(100)"],
                            start_line=13,
                        ),
                    ],
                ),
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/api/users"'],
                            start_line=30,
                        )
                    ],
                ),
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=[],
        teardown_methods=_fixture_methods({"example.ApiTest": ["afterEach()"]}),
    )

    assert result.local_metrics.assertion_count == 0
    assert result.assertions.summary.total_count == 1
    assert result.assertions.oracle_type.label == "example-based"
    assert result.assertions.failure_scenarios.has_server_error_assertion is False


def test_test_method_analysis_info_populates_parameterization_metadata() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ParamTest": make_type()},
        methods_by_class={
            "example.ParamTest": {
                "testData(String)": make_callable(
                    signature="testData(String)",
                    annotations=[
                        "@org.junit.jupiter.params.ParameterizedTest",
                        '@org.junit.jupiter.params.provider.ValueSource(strings = {"alpha", "beta"})',
                    ],
                )
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ParamTest",
        method_signature="testData(String)",
        setup_methods=[],
        teardown_methods=[],
    )

    assert result.identity.parameterization is not None
    assert result.identity.parameterization.signals == {"static": ["@ValueSource"]}


def test_test_class_analysis_info_requires_non_empty_test_methods() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={"example.ApiTest": {}},
    )

    with pytest.raises(ValueError):
        ClassAnalysisInfo(
            analysis=analysis,
            application_classes=[],
        ).get_test_class_analysis("example.ApiTest", [])


def test_analysis_info_rejects_negative_expanded_helper_depth() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()}, methods_by_class={}
    )

    with pytest.raises(ValueError):
        MethodAnalysisInfo(
            analysis=analysis,
            application_classes=[],
            expanded_helper_depth=-1,
        )

    with pytest.raises(ValueError):
        ClassAnalysisInfo(
            analysis=analysis,
            application_classes=[],
            expanded_helper_depth=-1,
        )

    with pytest.raises(ValueError):
        ProjectAnalysisInfo(
            analysis=analysis,
            dataset_name="example",
            project_path="/tmp/example",
            expanded_helper_depth=-1,
        )


def test_test_class_and_project_analysis_info_integration() -> None:
    classes = {
        "example.ApiTest": make_type(annotations=["@SpringBootTest"]),
        "example.TestUtil": make_type(),
        "example.Service": make_type(),
    }
    methods_by_class = {
        "example.ApiTest": {
            "beforeEach()": make_callable(
                signature="beforeEach()",
                annotations=["@BeforeEach"],
            ),
            "afterEach()": make_callable(
                signature="afterEach()",
                annotations=["@AfterEach"],
            ),
            "testEndpoint()": make_callable(
                signature="testEndpoint()",
                annotations=["@Test"],
                call_sites=[
                    make_call_site(
                        method_name="getForEntity",
                        receiver_type="org.springframework.boot.test.web.client.TestRestTemplate",
                        argument_expr=['"/api/orders"'],
                        start_line=20,
                    ),
                    make_call_site(
                        method_name="assertEquals",
                        argument_expr=["200", "200"],
                        start_line=21,
                    ),
                ],
                cyclomatic_complexity=1,
            ),
        },
        "example.TestUtil": {
            "helper()": make_callable(signature="helper()", cyclomatic_complexity=1)
        },
        "example.Service": {
            "run()": make_callable(signature="run()", cyclomatic_complexity=2)
        },
    }
    java_files = {
        "example.ApiTest": "src/test/java/example/ApiTest.java",
        "example.TestUtil": "src/test/java/example/TestUtil.java",
        "example.Service": "src/main/java/example/Service.java",
    }
    import_declarations_by_file = {
        "src/test/java/example/ApiTest.java": [
            "org.junit.jupiter.api.Test",
            "org.springframework.boot.test.web.client.TestRestTemplate",
        ],
        "src/test/java/example/TestUtil.java": [],
        "src/main/java/example/Service.java": [],
    }
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class=methods_by_class,
        java_files=java_files,
        import_declarations_by_file=import_declarations_by_file,
    )

    test_class_analysis = ClassAnalysisInfo(
        analysis=analysis,
        application_classes=["example.Service"],
        test_utility_classes=["example.TestUtil"],
    ).get_test_class_analysis(
        qualified_class_name="example.ApiTest",
        test_methods=["testEndpoint()"],
    )

    assert test_class_analysis.qualified_class_name == "example.ApiTest"
    assert len(_fixtures_for_phase(test_class_analysis, LifecyclePhase.SETUP)) == 1
    assert len(_fixtures_for_phase(test_class_analysis, LifecyclePhase.TEARDOWN)) == 1
    assert len(test_class_analysis.test_method_analyses) == 1
    method_analysis = test_class_analysis.test_method_analyses[0]
    assert len(_fixtures_for_phase(method_analysis, LifecyclePhase.SETUP)) == 1
    assert len(_fixtures_for_phase(method_analysis, LifecyclePhase.TEARDOWN)) == 1

    project_analysis = ProjectAnalysisInfo(
        analysis=analysis,
        dataset_name="example",
        project_path="/tmp/example",
    ).gather_project_analysis_info()

    assert project_analysis.dataset_name == "example"
    assert project_analysis.metadata.expanded_helper_depth == 1
    assert project_analysis.metadata.git_commit_hash is None
    assert project_analysis.metadata.git_remote_host is None
    assert project_analysis.metadata.git_repository is None
    assert project_analysis.application_class_count == 1
    assert project_analysis.application_method_count == 1
    assert project_analysis.application_cyclomatic_complexity == 2
    assert project_analysis.test_class_count == 1
    assert project_analysis.test_method_count == 1
    assert project_analysis.test_utility_class_count == 1
    assert project_analysis.test_utility_method_count == 1
    assert len(project_analysis.test_class_analyses) == 1


def test_project_analysis_info_applies_custom_test_dirs() -> None:
    classes = {
        "example.IntegrationUtility": make_type(),
        "example.Service": make_type(),
    }
    methods_by_class = {
        "example.IntegrationUtility": {
            "seedData()": make_callable(signature="seedData()")
        },
        "example.Service": {"run()": make_callable(signature="run()")},
    }
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class=methods_by_class,
        java_files={
            "example.IntegrationUtility": (
                "src/integrationTest/java/example/IntegrationUtility.java"
            ),
            "example.Service": "src/main/java/example/Service.java",
        },
        import_declarations_by_file={
            "src/integrationTest/java/example/IntegrationUtility.java": [],
            "src/main/java/example/Service.java": [],
        },
    )

    default_result = ProjectAnalysisInfo(
        analysis=analysis,
        dataset_name="default",
        project_path="/tmp/default",
    ).gather_project_analysis_info()

    custom_result = ProjectAnalysisInfo(
        analysis=analysis,
        dataset_name="custom",
        project_path="/tmp/custom",
        test_dirs=("src/test/java",),
    ).gather_project_analysis_info()

    assert default_result.test_utility_class_count == 1
    assert default_result.application_class_count == 1
    assert custom_result.test_utility_class_count == 0
    assert custom_result.application_class_count == 2


def test_runtime_helper_depth_applies_to_test_and_fixture_expansion() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "beforeEach()": make_callable(
                    signature="beforeEach()",
                    annotations=["@BeforeEach"],
                    call_sites=[
                        make_call_site(
                            method_name="seedUsers",
                            receiver_expr="this",
                            callee_signature="seedUsers()",
                            start_line=4,
                        )
                    ],
                ),
                "seedUsers()": make_callable(
                    signature="seedUsers()",
                    call_sites=[
                        make_call_site(
                            method_name="postForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client.TestRestTemplate"
                            ),
                            argument_expr=['"/seed/users"'],
                            start_line=9,
                        )
                    ],
                ),
                "seedViaApi()": make_callable(
                    signature="seedViaApi()",
                    call_sites=[
                        make_call_site(
                            method_name="postForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client.TestRestTemplate"
                            ),
                            argument_expr=['"/api/seed"'],
                            start_line=15,
                        )
                    ],
                ),
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="seedViaApi",
                            receiver_expr="this",
                            callee_signature="seedViaApi()",
                            start_line=20,
                        )
                    ],
                ),
            }
        },
    )

    depth_zero = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=[],
    )

    depth_one = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=1,
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=[],
    )

    assert _all_http_calls(depth_zero) == []
    assert [call.path for call in _all_http_calls(depth_one)] == [
        "/seed/users",
        "/api/seed",
    ]
    assert [
        (
            interaction.origin.kind,
            interaction.origin.method_signature,
            interaction.origin.entry_method_signature,
            interaction.origin.depth,
        )
        for interaction in _all_http_interactions(depth_one)
    ] == [
        (
            CallSiteOriginKind.FIXTURE_HELPER,
            "seedUsers()",
            "beforeEach()",
            1,
        ),
        (
            CallSiteOriginKind.TEST_HELPER,
            "seedViaApi()",
            "testEndpoint()",
            1,
        ),
    ]
    setup_fixture = _fixtures_for_phase(depth_one, LifecyclePhase.SETUP)[0]
    assert [
        interaction.request_interaction.http_call.path
        for interaction in setup_fixture.http_interactions
        if interaction.request_interaction is not None
        and interaction.request_interaction.http_call is not None
    ] == ["/seed/users"]
    assert [
        interaction.http_call.path
        for interaction in setup_fixture.request_interactions
        if interaction.http_call is not None
    ] == ["/seed/users"]
    assert (
        setup_fixture.http_interactions[0].origin.kind
        == CallSiteOriginKind.FIXTURE_HELPER
    )
    assert setup_fixture.http_interactions[0].origin.method_signature == "seedUsers()"
    assert (
        setup_fixture.http_interactions[0].origin.entry_method_signature
        == "beforeEach()"
    )
    assert depth_zero.http == HttpAnalysis()
    assert depth_one.http.request_dispatch.labels == ["local-network"]
    assert depth_one.http.request_dispatch.signals == {
        "local-network": ["real-http-local"]
    }
    assert depth_one.http.request_dispatch.local_request_count == 2
    assert depth_one.expanded_metrics.helper_method_count == 2
    # The fixture helper (seedUsers) is excluded; only the test-body helper
    # (seedViaApi) counts as a distinct test-helper method.
    assert depth_one.expanded_metrics.test_helper_method_count == 1


def test_fixture_analysis_records_response_role_events_from_fixture_helpers() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "beforeEach()": make_callable(
                    signature="beforeEach()",
                    annotations=["@BeforeEach"],
                    call_sites=[
                        make_call_site(
                            method_name="verifySeedResponse",
                            receiver_expr="this",
                            callee_signature="verifySeedResponse()",
                            start_line=4,
                        )
                    ],
                ),
                "verifySeedResponse()": make_callable(
                    signature="verifySeedResponse()",
                    call_sites=[
                        make_call_site(
                            method_name="isOk",
                            receiver_type=(
                                "org.springframework.test.web.servlet.result."
                                "StatusResultMatchers"
                            ),
                            start_line=9,
                            start_column=17,
                            end_line=9,
                            end_column=23,
                        )
                    ],
                ),
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/api/users"'],
                            start_line=30,
                        )
                    ],
                ),
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=1,
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=[],
    )

    setup_fixture = _fixtures_for_phase(result, LifecyclePhase.SETUP)[0]

    assert setup_fixture.request_interactions == []
    assert setup_fixture.verification_interaction_count == 1
    assert setup_fixture.http_interaction_count == 1
    assert (
        setup_fixture.verification_interactions[0].response_role
        == HttpResponseRole.STATUS_ASSERTION
    )
    assert setup_fixture.verification_interactions[0].method_name == "isOk"
    assert (
        setup_fixture.http_interactions[0].verification_interaction
        == setup_fixture.verification_interactions[0]
    )
    assert (
        setup_fixture.http_interactions[0].origin.kind
        == CallSiteOriginKind.FIXTURE_HELPER
    )
    assert (
        setup_fixture.http_interactions[0].origin.method_signature
        == "verifySeedResponse()"
    )
    assert (
        setup_fixture.http_interactions[0].origin.entry_method_signature
        == "beforeEach()"
    )


def test_fixture_analysis_records_assertion_only_verification_from_fixture_helpers() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "beforeEach()": make_callable(
                    signature="beforeEach()",
                    annotations=["@BeforeEach"],
                    call_sites=[
                        make_call_site(
                            method_name="verifySeedResponse",
                            receiver_expr="this",
                            callee_signature="verifySeedResponse()",
                            start_line=4,
                        )
                    ],
                ),
                "verifySeedResponse()": make_callable(
                    signature="verifySeedResponse()",
                    call_sites=[
                        make_call_site(
                            method_name="getStatusCode",
                            start_line=9,
                            start_column=12,
                            end_line=9,
                            end_column=27,
                        ),
                        make_call_site(
                            method_name="assertThat",
                            start_line=9,
                            start_column=1,
                            end_line=9,
                            end_column=30,
                        ),
                        make_call_site(
                            method_name="isEqualTo",
                            argument_expr=["200"],
                            start_line=9,
                            start_column=1,
                            end_line=9,
                            end_column=45,
                        ),
                    ],
                ),
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/api/users"'],
                            start_line=30,
                        )
                    ],
                ),
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=1,
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=[],
    )

    setup_fixture = _fixtures_for_phase(result, LifecyclePhase.SETUP)[0]

    assert setup_fixture.request_interactions == []
    assert setup_fixture.verification_interaction_count == 1
    assert setup_fixture.http_interaction_count == 1
    assert setup_fixture.verification_interactions[0].response_role is None
    assert (
        setup_fixture.verification_interactions[0].assertion_role
        == AssertionRole.STATUS
    )
    assert setup_fixture.verification_interactions[0].method_name == "isEqualTo"
    assert (
        setup_fixture.http_interactions[0].verification_interaction
        == setup_fixture.verification_interactions[0]
    )
    assert (
        setup_fixture.http_interactions[0].origin.kind
        == CallSiteOriginKind.FIXTURE_HELPER
    )
    assert (
        setup_fixture.http_interactions[0].origin.method_signature
        == "verifySeedResponse()"
    )


def test_runtime_analysis_drops_repeated_helper_overflow_without_modulo_wrap() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="helper",
                            receiver_expr="this",
                            callee_signature="helper()",
                            start_line=10,
                        ),
                        make_call_site(
                            method_name="helper",
                            receiver_expr="this",
                            callee_signature="helper()",
                            start_line=20,
                        ),
                    ],
                ),
                "helper()": make_callable(
                    signature="helper()",
                    declaration="void helper()",
                    code="{\nint seed = 1;\n}",
                    call_sites=[
                        make_call_site(
                            method_name="postForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client.TestRestTemplate"
                            ),
                            argument_expr=['"/api/repeated"'],
                            start_line=5,
                        )
                    ],
                ),
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=1,
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=[],
        teardown_methods=[],
    )

    assert result.expanded_metrics.helper_method_count == 2
    # The same helper invoked twice is one distinct test-helper method.
    assert result.expanded_metrics.test_helper_method_count == 1
    assert result.expanded_metrics.helper_method_ncloc > 0
    assert [
        interaction.origin.method_signature
        for interaction in _all_http_interactions(result)
        if interaction.http_call is not None
    ] == [
        "helper()",
        "helper()",
    ]
    assert [call.path for call in _all_http_calls(result)] == [
        "/api/repeated",
        "/api/repeated",
    ]
    assert result.http.request_dispatch.local_request_count == 2


def test_runtime_helper_depth_two_includes_second_level_helpers() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "beforeEach()": make_callable(
                    signature="beforeEach()",
                    annotations=["@BeforeEach"],
                    call_sites=[
                        make_call_site(
                            method_name="seedUsers",
                            receiver_expr="this",
                            callee_signature="seedUsers()",
                        )
                    ],
                ),
                "seedUsers()": make_callable(
                    signature="seedUsers()",
                    call_sites=[
                        make_call_site(
                            method_name="doFixtureSeed",
                            receiver_expr="this",
                            callee_signature="doFixtureSeed()",
                        )
                    ],
                ),
                "doFixtureSeed()": make_callable(
                    signature="doFixtureSeed()",
                    call_sites=[
                        make_call_site(
                            method_name="postForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client.TestRestTemplate"
                            ),
                            argument_expr=['"/fixture/seed"'],
                            start_line=30,
                        )
                    ],
                ),
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="seedViaApi",
                            receiver_expr="this",
                            callee_signature="seedViaApi()",
                        )
                    ],
                ),
                "seedViaApi()": make_callable(
                    signature="seedViaApi()",
                    call_sites=[
                        make_call_site(
                            method_name="doTestSeed",
                            receiver_expr="this",
                            callee_signature="doTestSeed()",
                        )
                    ],
                ),
                "doTestSeed()": make_callable(
                    signature="doTestSeed()",
                    call_sites=[
                        make_call_site(
                            method_name="postForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client.TestRestTemplate"
                            ),
                            argument_expr=['"/test/seed"'],
                            start_line=40,
                        )
                    ],
                ),
            }
        },
    )

    depth_one = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=1,
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=[],
    )

    depth_two = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=2,
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=[],
    )

    assert _all_http_calls(depth_one) == []
    assert [call.path for call in _all_http_calls(depth_two)] == [
        "/fixture/seed",
        "/test/seed",
    ]


def test_assertion_evidence_uses_expanded_helper_execution_order() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/api/users"'],
                            start_line=5,
                        ),
                        make_call_site(
                            method_name="helperAssert",
                            receiver_expr="this",
                            callee_signature="helperAssert()",
                            start_line=10,
                        ),
                        make_call_site(
                            method_name="assertEquals",
                            argument_expr=["200", "200"],
                            start_line=20,
                        ),
                    ],
                ),
                "helperAssert()": make_callable(
                    signature="helperAssert()",
                    call_sites=[
                        make_call_site(
                            method_name="statusCode",
                            argument_expr=["200"],
                            start_line=5,
                        )
                    ],
                ),
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=1,
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=[],
        teardown_methods=[],
    )

    # assertEquals("200", "200") now gets GENERAL classification
    assert result.assertions.summary.total_count == 1


def test_api_call_sequence_marks_helper_receiver_expr_status_matcher_as_assertion() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/api/users"'],
                            start_line=5,
                        ),
                        make_call_site(
                            method_name="helperAssert",
                            receiver_expr="this",
                            callee_signature="helperAssert()",
                            start_line=10,
                        ),
                    ],
                ),
                "helperAssert()": make_callable(
                    signature="helperAssert()",
                    call_sites=[
                        make_call_site(
                            method_name="isNotFound",
                            receiver_expr="statusAssertions",
                            start_line=5,
                        )
                    ],
                    variable_declarations=[
                        make_variable_declaration(
                            name="statusAssertions",
                            type_name=(
                                "org.springframework.test.web.reactive.server.StatusAssertions"
                            ),
                            start_line=3,
                        )
                    ],
                ),
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=1,
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=[],
        teardown_methods=[],
    )

    helper_status_steps = [
        step for step in result.http.call_sequence if step.method_name == "isNotFound"
    ]
    assert len(helper_status_steps) == 1
    assert helper_status_steps[0].kind.value == "response-check"
    assert helper_status_steps[0].origin.kind == CallSiteOriginKind.TEST_HELPER
    assert helper_status_steps[0].origin.method_signature == "helperAssert()"
    assert helper_status_steps[0].origin.entry_method_signature == "testEndpoint()"
    helper_verifications = [
        interaction
        for interaction in result.http.verification_interactions
        if interaction.method_name == "isNotFound"
    ]
    assert len(helper_verifications) == 1
    assert helper_verifications[0].origin.kind == CallSiteOriginKind.TEST_HELPER
    assert helper_verifications[0].assertion_role.value == "status"


def test_pipeline_assertion_evidence_and_surface_share_runtime_status_resolution() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/api/users"'],
                            start_line=5,
                        ),
                        make_call_site(
                            method_name="helperAssert",
                            receiver_expr="this",
                            callee_signature="helperAssert()",
                            start_line=10,
                        ),
                    ],
                ),
                "helperAssert()": make_callable(
                    signature="helperAssert()",
                    call_sites=[
                        make_call_site(
                            method_name="isNotFound",
                            receiver_expr="statusAssertions",
                            start_line=5,
                        )
                    ],
                    variable_declarations=[
                        make_variable_declaration(
                            name="statusAssertions",
                            type_name=(
                                "org.springframework.test.web.reactive.server.StatusAssertions"
                            ),
                            start_line=3,
                        )
                    ],
                ),
            }
        },
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=1,
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testEndpoint()",
        setup_methods=[],
        teardown_methods=[],
    )

    assert result.assertions.summary.status_count >= 1
    assert result.assertions.response_surface_labels == [AssertionRole.STATUS]
    assert result.assertions.response_surface_combination == "status-only"
    assert result.assertions.status_code_counts == {"404": 1}
    assert result.assertions.status_range_counts["4xx"] == 1
    assert result.assertions.has_status_check is True
    assert result.assertions.has_body_check is False
    assert result.assertions.has_header_check is False


def test_runtime_view_builder_runs_per_method_analysis() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "beforeEach()": make_callable(
                    signature="beforeEach()",
                    annotations=["@BeforeEach"],
                    call_sites=[
                        make_call_site(
                            method_name="postForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client.TestRestTemplate"
                            ),
                            argument_expr=['"/seed/users"'],
                            start_line=2,
                        )
                    ],
                ),
                "testOne()": make_callable(
                    signature="testOne()",
                    annotations=["@Test"],
                ),
                "testTwo()": make_callable(
                    signature="testTwo()",
                    annotations=["@Test"],
                ),
            }
        },
    )
    analyzer = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    )

    runtime_view_build_count = 0
    original_runtime_view_builder = analyzer._build_test_runtime_view

    def counting_runtime_view_builder(*args: Any, **kwargs: Any):
        nonlocal runtime_view_build_count
        runtime_view_build_count += 1
        return original_runtime_view_builder(*args, **kwargs)

    analyzer._build_test_runtime_view = counting_runtime_view_builder  # type: ignore[assignment]

    first = analyzer.get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testOne()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=[],
    )
    second = analyzer.get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.ApiTest",
        method_signature="testTwo()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=[],
    )

    assert runtime_view_build_count == 2
    assert [
        analysis.method_signature
        for analysis in _fixtures_for_phase(first, LifecyclePhase.SETUP)
    ] == ["beforeEach()"]
    assert [
        analysis.method_signature
        for analysis in _fixtures_for_phase(second, LifecyclePhase.SETUP)
    ] == ["beforeEach()"]


def test_runtime_view_marks_ambiguous_fixture_variants() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "beforeEach()": make_callable(
                    signature="beforeEach()",
                    annotations=["@BeforeMethod(onlyForGroups = SOME_GROUPS)"],
                    call_sites=[
                        make_call_site(
                            method_name="postForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client.TestRestTemplate"
                            ),
                            argument_expr=['"/seed/users"'],
                            start_line=2,
                        )
                    ],
                ),
                "testOne()": make_callable(
                    signature="testOne()",
                    annotations=["@Test"],
                ),
                "testTwo()": make_callable(
                    signature="testTwo()",
                    annotations=["@Test"],
                ),
                "testThree()": make_callable(
                    signature="testThree()",
                    annotations=["@Test"],
                ),
            }
        },
    )
    analyzer = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    )

    ambiguous_result = analyzer.get_test_method_analysis_info(
        testing_frameworks=[Framework.TESTNG],
        qualified_class_name="example.ApiTest",
        method_signature="testOne()",
        setup_methods=_fixture_methods(
            {"example.ApiTest": ["beforeEach()"]},
            ambiguous_methods_by_class={"example.ApiTest": ["beforeEach()"]},
        ),
        teardown_methods=[],
    )
    non_ambiguous_result = analyzer.get_test_method_analysis_info(
        testing_frameworks=[Framework.TESTNG],
        qualified_class_name="example.ApiTest",
        method_signature="testTwo()",
        setup_methods=_fixture_methods({"example.ApiTest": ["beforeEach()"]}),
        teardown_methods=[],
    )
    ambiguous_repeat_result = analyzer.get_test_method_analysis_info(
        testing_frameworks=[Framework.TESTNG],
        qualified_class_name="example.ApiTest",
        method_signature="testThree()",
        setup_methods=_fixture_methods(
            {"example.ApiTest": ["beforeEach()"]},
            ambiguous_methods_by_class={"example.ApiTest": ["beforeEach()"]},
        ),
        teardown_methods=[],
    )

    assert [
        (
            note.phase.value,
            note.method_signature,
            note.reason,
        )
        for note in ambiguous_result.ambiguous_fixture_group_methods
    ] == [("setup", "beforeEach()", "ambiguous-group-filter")]
    assert non_ambiguous_result.ambiguous_fixture_group_methods == []
    assert [
        (
            note.phase.value,
            note.method_signature,
            note.reason,
        )
        for note in ambiguous_repeat_result.ambiguous_fixture_group_methods
    ] == [("setup", "beforeEach()", "ambiguous-group-filter")]
    assert _all_http_interactions(ambiguous_result)[0].origin.is_group_ambiguous is True
    assert (
        _all_http_interactions(non_ambiguous_result)[0].origin.is_group_ambiguous
        is False
    )
    assert (
        _all_http_interactions(ambiguous_repeat_result)[0].origin.is_group_ambiguous
        is True
    )


def test_class_analysis_reuses_common_analysis_instance_across_method_analyses(
    monkeypatch,
) -> None:
    import gerbil.analysis.test_class.test_class_analysis_info as class_analysis_module
    import gerbil.analysis.test_method.test_method_analysis_info as method_analysis_module

    class CountingCommonAnalysis(CommonAnalysis):
        init_count = 0

        def __init__(self, analysis):
            CountingCommonAnalysis.init_count += 1
            super().__init__(analysis)

    monkeypatch.setattr(
        class_analysis_module,
        "CommonAnalysis",
        CountingCommonAnalysis,
    )
    monkeypatch.setattr(
        method_analysis_module,
        "CommonAnalysis",
        CountingCommonAnalysis,
    )

    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {
                "beforeEach()": make_callable(
                    signature="beforeEach()",
                    annotations=["@BeforeEach"],
                ),
                "testOne()": make_callable(
                    signature="testOne()",
                    annotations=["@Test"],
                ),
                "testTwo()": make_callable(
                    signature="testTwo()",
                    annotations=["@Test"],
                ),
            }
        },
        java_files={"example.ApiTest": "src/test/java/example/ApiTest.java"},
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": ["org.junit.jupiter.api.Test"]
        },
    )

    class_analysis = class_analysis_module.TestClassAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_class_analysis(
        qualified_class_name="example.ApiTest",
        test_methods=["testOne()", "testTwo()"],
    )

    assert len(class_analysis.test_method_analyses) == 2
    assert CountingCommonAnalysis.init_count == 1


def _load_project_cache_fixture(
    analysis: FakeJavaAnalysis,
    *,
    dependency_parent: str,
    side_effect_parent: str,
) -> None:
    classes = {
        "example.ApiTest": make_type(),
        "example.SharedDependencyReceiver": make_type(extends_list=[dependency_parent]),
        "example.SharedAssertionReceiver": make_type(extends_list=[side_effect_parent]),
        dependency_parent: make_type(),
        side_effect_parent: make_type(),
    }
    methods_by_class = {
        "example.ApiTest": {
            "testEndpoint()": make_callable(
                signature="testEndpoint()",
                annotations=["@Test"],
                call_sites=[
                    make_call_site(
                        method_name="postForEntity",
                        receiver_type=(
                            "org.springframework.boot.test.web.client.TestRestTemplate"
                        ),
                        argument_expr=['"/api/orders"'],
                        start_line=10,
                    ),
                    make_call_site(
                        method_name="execute",
                        receiver_type="example.SharedDependencyReceiver",
                        start_line=12,
                    ),
                    make_call_site(
                        method_name="count",
                        receiver_type="example.SharedAssertionReceiver",
                        start_line=20,
                    ),
                ],
            )
        }
    }
    analysis._classes = classes
    analysis._methods_by_class = methods_by_class
    analysis._java_files = {
        "example.ApiTest": "src/test/java/example/ApiTest.java",
    }
    analysis._import_declarations_by_file = make_import_declarations_by_file(
        {
            "src/test/java/example/ApiTest.java": [
                "org.junit.jupiter.api.Test",
                "org.springframework.boot.test.web.client.TestRestTemplate",
            ],
        }
    )
    analysis._extended_classes = {}


def _project_method_labels(
    analysis: FakeJavaAnalysis,
    *,
    dataset_name: str,
) -> tuple[list[str], AssertionSummary]:
    project_analysis = ProjectAnalysisInfo(
        analysis=analysis,
        dataset_name=dataset_name,
        project_path=f"/tmp/{dataset_name}",
    ).gather_project_analysis_info()

    method_analysis = project_analysis.test_class_analyses[0].test_method_analyses[0]
    return (
        method_analysis.dependencies.strategy.labels,
        method_analysis.assertions.summary,
    )


def test_project_analysis_is_order_independent_for_dependency_and_assertion_surface() -> (
    None
):
    shared_analysis = FakeJavaAnalysis()
    _load_project_cache_fixture(
        shared_analysis,
        dependency_parent="org.mockserver.client.MockServerClient",
        side_effect_parent="org.springframework.data.repository.CrudRepository",
    )
    _project_method_labels(shared_analysis, dataset_name="project-a")

    _load_project_cache_fixture(
        shared_analysis,
        dependency_parent="example.NonHintDependencyBase",
        side_effect_parent="example.NonHintAssertionBase",
    )
    project_b_after_a = _project_method_labels(
        shared_analysis,
        dataset_name="project-b-after-a",
    )

    isolated_project_b = FakeJavaAnalysis()
    _load_project_cache_fixture(
        isolated_project_b,
        dependency_parent="example.NonHintDependencyBase",
        side_effect_parent="example.NonHintAssertionBase",
    )
    project_b_alone = _project_method_labels(
        isolated_project_b,
        dataset_name="project-b-alone",
    )

    assert project_b_after_a == project_b_alone
    assert project_b_alone == ([], AssertionSummary())


def test_project_analysis_info_calls_property_cache_reset_hooks(monkeypatch) -> None:
    import gerbil.analysis.project.project_analysis_info as project_analysis_module

    reset_calls: list[str] = []

    monkeypatch.setattr(
        project_analysis_module,
        "reset_class_resolution_cache",
        lambda: reset_calls.append("hierarchy"),
    )

    ProjectAnalysisInfo(
        analysis=FakeJavaAnalysis(classes={}, methods_by_class={}),
        dataset_name="example",
        project_path="/tmp/example",
    ).gather_project_analysis_info()

    assert reset_calls == ["hierarchy"]


def test_property_module_reset_caches_clear_all_entries() -> None:
    import gerbil.analysis.shared.caching as hierarchy_cache_module

    hierarchy_cache_module.CLASS_RESOLUTION_CACHE[(object(), "example.Type")] = (
        "example.Type",
    )

    hierarchy_cache_module.reset_class_resolution_cache()

    assert not hierarchy_cache_module.CLASS_RESOLUTION_CACHE
