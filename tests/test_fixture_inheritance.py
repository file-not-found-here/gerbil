from __future__ import annotations

from cldk.models.java import JCallable

from gerbil.analysis.schema import LifecyclePhase
from gerbil.analysis.shared import CommonAnalysis, Reachability
from gerbil.analysis.runtime import FixtureMethod
from gerbil.analysis.test_class import TestClassAnalysisInfo as ClassAnalysisInfo
from tests.cldk_factories import make_call_site, make_callable, make_type
from tests.fake_java_analysis import FakeJavaAnalysis

_COMMON_TEST_IMPORTS: list[str] = [
    "org.junit.jupiter.api.BeforeAll",
    "org.junit.jupiter.api.BeforeEach",
    "org.junit.jupiter.api.AfterAll",
    "org.junit.jupiter.api.AfterEach",
    "org.testng.annotations.BeforeSuite",
    "org.testng.annotations.BeforeTest",
    "org.testng.annotations.BeforeGroups",
    "org.testng.annotations.BeforeClass",
    "org.testng.annotations.BeforeMethod",
    "org.testng.annotations.AfterSuite",
    "org.testng.annotations.AfterTest",
    "org.testng.annotations.AfterGroups",
    "org.testng.annotations.AfterClass",
    "org.testng.annotations.AfterMethod",
    "org.testng.annotations.Test",
]


def _java_file_path(qualified_class_name: str) -> str:
    return f"src/test/java/{qualified_class_name.replace('.', '/')}.java"


def _build_hierarchy_analysis(
    methods_by_class: dict[str, dict[str, JCallable]],
) -> FakeJavaAnalysis:
    classes = {
        "example.ChildTest": make_type(
            extends_list=["example.ParentTest"],
            implements_list=["example.ChildFixture"],
        ),
        "example.ParentTest": make_type(
            extends_list=["example.GrandParentTest"],
            implements_list=["example.ParentFixture"],
        ),
        "example.GrandParentTest": make_type(),
        "example.ChildFixture": make_type(),
        "example.ParentFixture": make_type(extends_list=["example.BaseFixture"]),
        "example.BaseFixture": make_type(),
    }
    java_files = {
        qualified_class_name: _java_file_path(qualified_class_name)
        for qualified_class_name in classes
    }
    import_declarations_by_file = {
        java_file: list(_COMMON_TEST_IMPORTS) for java_file in java_files.values()
    }
    return FakeJavaAnalysis(
        classes=classes,
        methods_by_class=methods_by_class,
        java_files=java_files,
        import_declarations_by_file=import_declarations_by_file,
    )


_NESTED_TEST_FILE: str = "src/test/java/example/OuterTest.java"


def _build_nested_analysis(
    methods_by_class: dict[str, dict[str, JCallable]],
) -> FakeJavaAnalysis:
    classes = {
        "example.OuterTest": make_type(),
        "example.OuterTest.InnerTest": make_type(
            parent_type="example.OuterTest",
            annotations=["@Nested"],
        ),
    }
    java_files = {
        "example.OuterTest": _NESTED_TEST_FILE,
        "example.OuterTest.InnerTest": _NESTED_TEST_FILE,
    }
    import_declarations_by_file = {
        _NESTED_TEST_FILE: [
            "org.junit.jupiter.api.BeforeEach",
            "org.junit.jupiter.api.AfterEach",
            "org.junit.jupiter.api.Test",
            "org.junit.jupiter.api.Nested",
        ],
    }
    return FakeJavaAnalysis(
        classes=classes,
        methods_by_class=methods_by_class,
        java_files=java_files,
        import_declarations_by_file=import_declarations_by_file,
    )


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


def test_reachability_class_resolution_order_walks_full_hierarchy() -> None:
    analysis = _build_hierarchy_analysis(methods_by_class={})

    class_order = Reachability(analysis).get_class_resolution_order("example.ChildTest")

    assert class_order == [
        "example.ChildTest",
        "example.ParentTest",
        "example.GrandParentTest",
        "example.ChildFixture",
        "example.ParentFixture",
        "example.BaseFixture",
    ]


def test_setup_methods_include_transitive_fixtures_with_name_overrides() -> None:
    analysis = _build_hierarchy_analysis(
        methods_by_class={
            "example.ChildTest": {
                "childSetup()": make_callable(
                    signature="childSetup()", annotations=["@BeforeEach"]
                ),
                "shared()": make_callable(
                    signature="shared()", annotations=["@BeforeEach"]
                ),
                "shadowedByChildNonFixture()": make_callable(
                    signature="shadowedByChildNonFixture()", annotations=[]
                ),
            },
            "example.ParentTest": {
                "parentSetup()": make_callable(
                    signature="parentSetup()", annotations=["@BeforeEach"]
                ),
                "shared()": make_callable(
                    signature="shared()", annotations=["@BeforeEach"]
                ),
                "shadowedByChildNonFixture(java.lang.String)": make_callable(
                    signature="shadowedByChildNonFixture(java.lang.String)",
                    annotations=["@BeforeEach"],
                ),
            },
            "example.GrandParentTest": {
                "grandSetup()": make_callable(
                    signature="grandSetup()", annotations=["@BeforeEach"]
                )
            },
            "example.ChildFixture": {
                "childInterfaceSetup()": make_callable(
                    signature="childInterfaceSetup()", annotations=["@BeforeEach"]
                )
            },
            "example.ParentFixture": {
                "interfaceSetup()": make_callable(
                    signature="interfaceSetup()", annotations=["@BeforeEach"]
                )
            },
            "example.BaseFixture": {
                "interfaceSetup()": make_callable(
                    signature="interfaceSetup()", annotations=["@BeforeEach"]
                )
            },
        }
    )

    setup_methods = CommonAnalysis(analysis).get_setup_methods("example.ChildTest")

    assert _fixture_tuples(setup_methods) == [
        ("example.ChildTest", "childSetup()", False),
        ("example.ChildTest", "shared()", False),
        ("example.ParentTest", "parentSetup()", False),
        (
            "example.ParentTest",
            "shadowedByChildNonFixture(java.lang.String)",
            False,
        ),
        ("example.GrandParentTest", "grandSetup()", False),
        ("example.ChildFixture", "childInterfaceSetup()", False),
        ("example.ParentFixture", "interfaceSetup()", False),
    ]


def test_teardown_methods_include_transitive_fixtures_with_name_overrides() -> None:
    analysis = _build_hierarchy_analysis(
        methods_by_class={
            "example.ChildTest": {
                "cleanup()": make_callable(
                    signature="cleanup()", annotations=["@AfterEach"]
                ),
                "shadowedByChildNonFixture()": make_callable(
                    signature="shadowedByChildNonFixture()", annotations=[]
                ),
            },
            "example.ParentTest": {
                "cleanup()": make_callable(
                    signature="cleanup()", annotations=["@AfterEach"]
                ),
                "parentCleanup()": make_callable(
                    signature="parentCleanup()", annotations=["@AfterEach"]
                ),
                "shadowedByChildNonFixture(java.lang.String)": make_callable(
                    signature="shadowedByChildNonFixture(java.lang.String)",
                    annotations=["@AfterEach"],
                ),
            },
            "example.GrandParentTest": {
                "grandCleanup()": make_callable(
                    signature="grandCleanup()", annotations=["@AfterEach"]
                )
            },
            "example.ParentFixture": {
                "interfaceCleanup()": make_callable(
                    signature="interfaceCleanup()", annotations=["@AfterEach"]
                )
            },
            "example.BaseFixture": {
                "interfaceCleanup()": make_callable(
                    signature="interfaceCleanup()", annotations=["@AfterEach"]
                )
            },
        }
    )

    teardown_methods = CommonAnalysis(analysis).get_teardown_methods(
        "example.ChildTest"
    )

    assert _fixture_tuples(teardown_methods) == [
        ("example.ChildTest", "cleanup()", False),
        ("example.ParentTest", "parentCleanup()", False),
        (
            "example.ParentTest",
            "shadowedByChildNonFixture(java.lang.String)",
            False,
        ),
        ("example.GrandParentTest", "grandCleanup()", False),
        ("example.ParentFixture", "interfaceCleanup()", False),
    ]


def test_fixture_methods_hide_parent_only_on_exact_signature_match() -> None:
    analysis = _build_hierarchy_analysis(
        methods_by_class={
            "example.ChildTest": {
                "setup(java.lang.String)": make_callable(
                    signature="setup(java.lang.String)",
                    annotations=[],
                ),
            },
            "example.ParentTest": {
                "setup(java.lang.String)": make_callable(
                    signature="setup(java.lang.String)",
                    annotations=["@BeforeEach"],
                ),
                "setup()": make_callable(
                    signature="setup()",
                    annotations=["@BeforeEach"],
                ),
            },
        }
    )

    setup_methods = CommonAnalysis(analysis).get_setup_methods("example.ChildTest")

    assert _fixture_tuples(setup_methods) == [
        ("example.ParentTest", "setup()", False),
    ]


def test_effective_fixture_methods_apply_group_filtering_with_fallback() -> None:
    analysis = _build_hierarchy_analysis(
        methods_by_class={
            "example.ChildTest": {
                "beforeAllShared()": make_callable(
                    signature="beforeAllShared()",
                    annotations=["@BeforeAll"],
                ),
                "beforeEachShared()": make_callable(
                    signature="beforeEachShared()",
                    annotations=["@BeforeEach"],
                ),
                "beforeSmokeOnly()": make_callable(
                    signature="beforeSmokeOnly()",
                    annotations=['@BeforeMethod(onlyForGroups = {"smoke"})'],
                ),
                "beforeAmbiguous()": make_callable(
                    signature="beforeAmbiguous()",
                    annotations=["@BeforeMethod(onlyForGroups = SOME_GROUPS)"],
                ),
                "afterAllShared()": make_callable(
                    signature="afterAllShared()",
                    annotations=["@AfterAll"],
                ),
                "afterSmokeOnly()": make_callable(
                    signature="afterSmokeOnly()",
                    annotations=['@AfterMethod(onlyForGroups = {"smoke"})'],
                ),
                "afterAmbiguous()": make_callable(
                    signature="afterAmbiguous()",
                    annotations=["@AfterMethod(onlyForGroups = GROUPS)"],
                ),
                "testSmoke()": make_callable(
                    signature="testSmoke()",
                    annotations=['@Test(groups = {"smoke"})'],
                ),
                "testRegression()": make_callable(
                    signature="testRegression()",
                    annotations=['@Test(groups = {"regression"})'],
                ),
            }
        }
    )
    common_analysis = CommonAnalysis(analysis)
    setup_methods = common_analysis.get_setup_methods("example.ChildTest")
    teardown_methods = common_analysis.get_teardown_methods("example.ChildTest")

    smoke_setup_methods = common_analysis.get_effective_setup_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testSmoke()",
        setup_methods=setup_methods,
    )
    smoke_teardown_methods = common_analysis.get_effective_teardown_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testSmoke()",
        teardown_methods=teardown_methods,
    )

    regression_setup_methods = common_analysis.get_effective_setup_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testRegression()",
        setup_methods=setup_methods,
    )
    regression_teardown_methods = common_analysis.get_effective_teardown_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testRegression()",
        teardown_methods=teardown_methods,
    )

    assert _fixture_tuples(smoke_setup_methods) == [
        ("example.ChildTest", "beforeAllShared()", False),
        ("example.ChildTest", "beforeEachShared()", False),
        ("example.ChildTest", "beforeSmokeOnly()", False),
        ("example.ChildTest", "beforeAmbiguous()", True),
    ]
    assert _fixture_tuples(regression_setup_methods) == [
        ("example.ChildTest", "beforeAllShared()", False),
        ("example.ChildTest", "beforeEachShared()", False),
        ("example.ChildTest", "beforeAmbiguous()", True),
    ]

    assert _fixture_tuples(smoke_teardown_methods) == [
        ("example.ChildTest", "afterAllShared()", False),
        ("example.ChildTest", "afterSmokeOnly()", False),
        ("example.ChildTest", "afterAmbiguous()", True),
    ]
    assert _fixture_tuples(regression_teardown_methods) == [
        ("example.ChildTest", "afterAllShared()", False),
        ("example.ChildTest", "afterAmbiguous()", True),
    ]


def test_effective_fixture_methods_mark_ambiguous_group_filtered_methods() -> None:
    analysis = _build_hierarchy_analysis(
        methods_by_class={
            "example.ChildTest": {
                "beforeAmbiguous()": make_callable(
                    signature="beforeAmbiguous()",
                    annotations=["@BeforeMethod(onlyForGroups = SOME_GROUPS)"],
                ),
                "beforeSmokeOnly()": make_callable(
                    signature="beforeSmokeOnly()",
                    annotations=['@BeforeMethod(onlyForGroups = {"smoke"})'],
                ),
                "afterAmbiguous()": make_callable(
                    signature="afterAmbiguous()",
                    annotations=["@AfterMethod(onlyForGroups = GROUPS)"],
                ),
                "testSmoke()": make_callable(
                    signature="testSmoke()",
                    annotations=['@Test(groups = {"smoke"})'],
                ),
            }
        }
    )

    common_analysis = CommonAnalysis(analysis)
    setup_methods = common_analysis.get_setup_methods("example.ChildTest")
    teardown_methods = common_analysis.get_teardown_methods("example.ChildTest")

    setup_methods = common_analysis.get_effective_setup_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testSmoke()",
        setup_methods=setup_methods,
    )
    teardown_methods = common_analysis.get_effective_teardown_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testSmoke()",
        teardown_methods=teardown_methods,
    )

    assert _fixture_tuples(setup_methods) == [
        ("example.ChildTest", "beforeAmbiguous()", True),
        ("example.ChildTest", "beforeSmokeOnly()", False),
    ]
    assert _fixture_tuples(teardown_methods) == [
        ("example.ChildTest", "afterAmbiguous()", True),
    ]


def test_base_class_test_groups_feed_subclass_fixture_group_context() -> None:
    classes = {
        "example.NgChildTest": make_type(extends_list=["example.NgBaseTest"]),
        "example.NgBaseTest": make_type(annotations=['@Test(groups = {"smoke"})']),
    }
    java_files = {
        qualified_class_name: _java_file_path(qualified_class_name)
        for qualified_class_name in classes
    }
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class={
            "example.NgChildTest": {
                "beforeSmokeOnly()": make_callable(
                    signature="beforeSmokeOnly()",
                    annotations=['@BeforeMethod(onlyForGroups = {"smoke"})'],
                ),
                "beforeRegressionOnly()": make_callable(
                    signature="beforeRegressionOnly()",
                    annotations=['@BeforeMethod(onlyForGroups = {"regression"})'],
                ),
                "verifiesSomething()": make_callable(
                    signature="verifiesSomething()",
                    modifiers=["public"],
                ),
            },
        },
        java_files=java_files,
        import_declarations_by_file={
            java_file: list(_COMMON_TEST_IMPORTS) for java_file in java_files.values()
        },
    )
    common_analysis = CommonAnalysis(analysis)
    setup_methods = common_analysis.get_setup_methods("example.NgChildTest")

    effective_setup_methods = common_analysis.get_effective_setup_methods(
        qualified_class_name="example.NgChildTest",
        test_method_signature="verifiesSomething()",
        setup_methods=setup_methods,
    )

    assert _fixture_tuples(effective_setup_methods) == [
        ("example.NgChildTest", "beforeSmokeOnly()", False),
    ]


def test_effective_fixture_methods_mark_unresolved_fixture_methods_ambiguous() -> None:
    analysis = _build_hierarchy_analysis(
        methods_by_class={
            "example.ChildTest": {
                "testSmoke()": make_callable(
                    signature="testSmoke()",
                    annotations=['@Test(groups = {"smoke"})'],
                ),
            }
        }
    )
    common_analysis = CommonAnalysis(analysis)

    setup_methods = common_analysis.get_effective_setup_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testSmoke()",
        setup_methods=[
            FixtureMethod(
                defining_class_name="example.ChildTest",
                method_signature="beforeMissing()",
            )
        ],
    )

    assert _fixture_tuples(setup_methods) == [
        ("example.ChildTest", "beforeMissing()", True),
    ]


def test_setup_and_teardown_share_visible_method_lookup_cache(
    monkeypatch,
) -> None:
    analysis = _build_hierarchy_analysis(
        methods_by_class={
            "example.ChildTest": {
                "setupFixture()": make_callable(
                    signature="setupFixture()",
                    annotations=["@BeforeEach"],
                ),
                "teardownFixture()": make_callable(
                    signature="teardownFixture()",
                    annotations=["@AfterEach"],
                ),
            }
        }
    )
    common_analysis = CommonAnalysis(analysis)

    visible_lookup_count = 0
    original_visible_lookup = Reachability.get_visible_class_methods

    def counting_visible_lookup(self, qualified_class_name: str):
        nonlocal visible_lookup_count
        visible_lookup_count += 1
        return original_visible_lookup(self, qualified_class_name)

    monkeypatch.setattr(
        Reachability,
        "get_visible_class_methods",
        counting_visible_lookup,
    )

    common_analysis.get_setup_methods("example.ChildTest")
    common_analysis.get_teardown_methods("example.ChildTest")

    assert visible_lookup_count == 1


def test_fake_java_analysis_get_extended_classes_precedence() -> None:
    analysis_with_override = FakeJavaAnalysis(
        classes={
            "example.ChildTest": make_type(extends_list=["example.ClassExtends"]),
        },
        methods_by_class={},
        extended_classes={"example.ChildTest": ["example.OverrideExtends"]},
    )

    analysis_without_override = FakeJavaAnalysis(
        classes={
            "example.ChildTest": make_type(extends_list=["example.ClassExtends"]),
        },
        methods_by_class={},
    )

    assert analysis_with_override.get_extended_classes("example.ChildTest") == [
        "example.OverrideExtends"
    ]
    assert analysis_without_override.get_extended_classes("example.ChildTest") == [
        "example.ClassExtends"
    ]
    assert analysis_without_override.get_extended_classes("example.Missing") == []


def test_alwaysrun_fixture_runs_for_test_without_matching_group() -> None:
    analysis = _build_hierarchy_analysis(
        methods_by_class={
            "example.ChildTest": {
                "beforeAlwaysRun()": make_callable(
                    signature="beforeAlwaysRun()",
                    annotations=[
                        """@org.testng.annotations.BeforeMethod(
                            groups = {"integration"}, alwaysRun = true
                        )"""
                    ],
                ),
                "beforeGroupOnly()": make_callable(
                    signature="beforeGroupOnly()",
                    annotations=[
                        """@org.testng.annotations.BeforeMethod(
                            onlyForGroups = {"integration"}
                        )"""
                    ],
                ),
                "testPlain()": make_callable(signature="testPlain()"),
            }
        }
    )
    common_analysis = CommonAnalysis(analysis)
    setup_methods = common_analysis.get_setup_methods("example.ChildTest")
    effective = common_analysis.get_effective_setup_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testPlain()",
        setup_methods=setup_methods,
    )

    assert _fixture_tuples(effective) == [
        ("example.ChildTest", "beforeAlwaysRun()", False),
    ]


def test_alwaysrun_does_not_bypass_groups_for_beforegroups() -> None:
    analysis = _build_hierarchy_analysis(
        methods_by_class={
            "example.ChildTest": {
                "beforeGroupsAlwaysRun()": make_callable(
                    signature="beforeGroupsAlwaysRun()",
                    annotations=[
                        """@org.testng.annotations.BeforeGroups(
                            groups = {"integration"}, alwaysRun = true
                        )"""
                    ],
                ),
                "testPlain()": make_callable(signature="testPlain()"),
                "testIntegration()": make_callable(
                    signature="testIntegration()",
                    annotations=['@Test(groups = {"integration"})'],
                ),
            }
        }
    )
    common_analysis = CommonAnalysis(analysis)
    setup_methods = common_analysis.get_setup_methods("example.ChildTest")

    plain_effective = common_analysis.get_effective_setup_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testPlain()",
        setup_methods=setup_methods,
    )
    integration_effective = common_analysis.get_effective_setup_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testIntegration()",
        setup_methods=setup_methods,
    )

    assert _fixture_tuples(plain_effective) == []
    assert _fixture_tuples(integration_effective) == [
        ("example.ChildTest", "beforeGroupsAlwaysRun()", False),
    ]


def test_alwaysrun_does_not_bypass_groups_for_aftergroups() -> None:
    analysis = _build_hierarchy_analysis(
        methods_by_class={
            "example.ChildTest": {
                "afterGroupsAlwaysRun()": make_callable(
                    signature="afterGroupsAlwaysRun()",
                    annotations=[
                        """@org.testng.annotations.AfterGroups(
                            groups = {"integration"}, alwaysRun = true
                        )"""
                    ],
                ),
                "testPlain()": make_callable(signature="testPlain()"),
                "testIntegration()": make_callable(
                    signature="testIntegration()",
                    annotations=['@Test(groups = {"integration"})'],
                ),
            }
        }
    )
    common_analysis = CommonAnalysis(analysis)
    teardown_methods = common_analysis.get_teardown_methods("example.ChildTest")

    plain_effective = common_analysis.get_effective_teardown_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testPlain()",
        teardown_methods=teardown_methods,
    )
    integration_effective = common_analysis.get_effective_teardown_methods(
        qualified_class_name="example.ChildTest",
        test_method_signature="testIntegration()",
        teardown_methods=teardown_methods,
    )

    assert _fixture_tuples(plain_effective) == []
    assert _fixture_tuples(integration_effective) == [
        ("example.ChildTest", "afterGroupsAlwaysRun()", False),
    ]


def test_testng_suite_and_test_fixtures_are_discovered_with_import_gating() -> None:
    analysis = _build_hierarchy_analysis(
        methods_by_class={
            "example.ChildTest": {
                "suiteSetup()": make_callable(
                    signature="suiteSetup()",
                    annotations=["@BeforeSuite"],
                ),
                "testSetup()": make_callable(
                    signature="testSetup()",
                    annotations=["@BeforeTest"],
                ),
                "groupsSetup()": make_callable(
                    signature="groupsSetup()",
                    annotations=["@BeforeGroups"],
                ),
                "testSomething()": make_callable(signature="testSomething()"),
            }
        }
    )

    setup_methods = CommonAnalysis(analysis).get_setup_methods("example.ChildTest")

    assert _fixture_tuples(setup_methods) == [
        ("example.ChildTest", "suiteSetup()", False),
        ("example.ChildTest", "testSetup()", False),
        ("example.ChildTest", "groupsSetup()", False),
    ]


def test_testng_suite_fixture_ignored_when_imported_from_wrong_package() -> None:
    classes = {"example.TestClass": make_type()}
    java_files = {
        "example.TestClass": "src/test/java/example/TestClass.java",
    }
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class={
            "example.TestClass": {
                "suiteSetup()": make_callable(
                    signature="suiteSetup()",
                    annotations=["@BeforeSuite"],
                ),
                "testSomething()": make_callable(signature="testSomething()"),
            }
        },
        java_files=java_files,
        import_declarations_by_file={
            java_files["example.TestClass"]: ["com.example.BeforeSuite"],
        },
    )

    setup_methods = CommonAnalysis(analysis).get_setup_methods("example.TestClass")

    assert _fixture_tuples(setup_methods) == []


def test_fake_java_analysis_exposes_shared_helper_apis() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ChildTest": make_type()},
        methods_by_class={},
        java_files={"example.ChildTest": "src/test/java/example/ChildTest.java"},
        import_declarations_by_file={
            "src/test/java/example/ChildTest.java": ["org.junit.jupiter.api.Test"],
        },
    )

    assert analysis.get_classes() == {
        "example.ChildTest": analysis.get_class("example.ChildTest")
    }
    assert (
        analysis.get_java_file("example.ChildTest")
        == "src/test/java/example/ChildTest.java"
    )
    compilation_unit = analysis.get_java_compilation_unit(
        "src/test/java/example/ChildTest.java"
    )
    assert compilation_unit is not None
    assert [
        import_entry.path for import_entry in compilation_unit.import_declarations
    ] == ["org.junit.jupiter.api.Test"]


def test_nested_class_resolution_order_includes_outer_class_after_super_chain() -> None:
    analysis = _build_nested_analysis(methods_by_class={})

    class_order = Reachability(analysis).get_class_resolution_order(
        "example.OuterTest.InnerTest"
    )

    assert class_order == [
        "example.OuterTest.InnerTest",
        "example.OuterTest",
    ]


def test_nested_class_inherits_outer_before_each_fixture() -> None:
    analysis = _build_nested_analysis(
        methods_by_class={
            "example.OuterTest.InnerTest": {
                "innerTest()": make_callable(
                    signature="innerTest()", annotations=["@Test"]
                ),
            },
            "example.OuterTest": {
                "outerSetUp()": make_callable(
                    signature="outerSetUp()", annotations=["@BeforeEach"]
                ),
            },
        }
    )

    setup_methods = CommonAnalysis(analysis).get_setup_methods(
        "example.OuterTest.InnerTest"
    )

    assert _fixture_tuples(setup_methods) == [
        ("example.OuterTest", "outerSetUp()", False),
    ]


def test_nested_class_inherits_outer_after_each_fixture() -> None:
    analysis = _build_nested_analysis(
        methods_by_class={
            "example.OuterTest.InnerTest": {
                "innerTest()": make_callable(
                    signature="innerTest()", annotations=["@Test"]
                ),
            },
            "example.OuterTest": {
                "outerTearDown()": make_callable(
                    signature="outerTearDown()", annotations=["@AfterEach"]
                ),
            },
        }
    )

    teardown_methods = CommonAnalysis(analysis).get_teardown_methods(
        "example.OuterTest.InnerTest"
    )

    assert _fixture_tuples(teardown_methods) == [
        ("example.OuterTest", "outerTearDown()", False),
    ]


def test_nested_in_nested_inherits_all_outer_fixtures() -> None:
    classes = {
        "example.OuterTest": make_type(),
        "example.OuterTest.MiddleTest": make_type(
            parent_type="example.OuterTest",
            annotations=["@Nested"],
        ),
        "example.OuterTest.MiddleTest.InnerTest": make_type(
            parent_type="example.OuterTest.MiddleTest",
            annotations=["@Nested"],
        ),
    }
    java_files = {class_name: _NESTED_TEST_FILE for class_name in classes}
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class={
            "example.OuterTest.MiddleTest.InnerTest": {
                "innerTest()": make_callable(
                    signature="innerTest()", annotations=["@Test"]
                ),
            },
            "example.OuterTest.MiddleTest": {
                "middleSetUp()": make_callable(
                    signature="middleSetUp()", annotations=["@BeforeEach"]
                ),
            },
            "example.OuterTest": {
                "outerSetUp()": make_callable(
                    signature="outerSetUp()", annotations=["@BeforeEach"]
                ),
            },
        },
        java_files=java_files,
        import_declarations_by_file={
            _NESTED_TEST_FILE: [
                "org.junit.jupiter.api.BeforeEach",
                "org.junit.jupiter.api.Test",
                "org.junit.jupiter.api.Nested",
            ],
        },
    )

    setup_methods = CommonAnalysis(analysis).get_setup_methods(
        "example.OuterTest.MiddleTest.InnerTest"
    )

    assert _fixture_tuples(setup_methods) == [
        ("example.OuterTest.MiddleTest", "middleSetUp()", False),
        ("example.OuterTest", "outerSetUp()", False),
    ]


def test_static_inner_class_without_nested_inherits_no_fixtures() -> None:
    classes = {
        "example.OuterTest": make_type(),
        "example.OuterTest.Helper": make_type(
            parent_type="example.OuterTest",
        ),
    }
    java_files = {class_name: _NESTED_TEST_FILE for class_name in classes}
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class={
            "example.OuterTest.Helper": {
                "helperTest()": make_callable(
                    signature="helperTest()", annotations=["@Test"]
                ),
            },
            "example.OuterTest": {
                "outerSetUp()": make_callable(
                    signature="outerSetUp()", annotations=["@BeforeEach"]
                ),
            },
        },
        java_files=java_files,
        import_declarations_by_file={
            _NESTED_TEST_FILE: [
                "org.junit.jupiter.api.BeforeEach",
                "org.junit.jupiter.api.Test",
            ],
        },
    )

    setup_methods = CommonAnalysis(analysis).get_setup_methods(
        "example.OuterTest.Helper"
    )

    assert _fixture_tuples(setup_methods) == []


def test_nested_class_inherits_outer_superclass_fixtures() -> None:
    classes = {
        "example.BaseTest": make_type(),
        "example.OuterTest": make_type(extends_list=["example.BaseTest"]),
        "example.OuterTest.InnerTest": make_type(
            parent_type="example.OuterTest",
            annotations=["@Nested"],
        ),
    }
    java_files = {
        class_name: _java_file_path(class_name)
        for class_name in ["example.BaseTest", "example.OuterTest"]
    }
    java_files["example.OuterTest.InnerTest"] = _java_file_path("example.OuterTest")
    import_declarations_by_file = {
        _java_file_path("example.BaseTest"): [
            "org.junit.jupiter.api.BeforeEach",
        ],
        _java_file_path("example.OuterTest"): [
            "org.junit.jupiter.api.BeforeEach",
            "org.junit.jupiter.api.Test",
            "org.junit.jupiter.api.Nested",
        ],
    }
    analysis = FakeJavaAnalysis(
        classes=classes,
        methods_by_class={
            "example.OuterTest.InnerTest": {
                "innerTest()": make_callable(
                    signature="innerTest()", annotations=["@Test"]
                ),
            },
            "example.OuterTest": {
                "outerSetUp()": make_callable(
                    signature="outerSetUp()", annotations=["@BeforeEach"]
                ),
            },
            "example.BaseTest": {
                "baseSetUp()": make_callable(
                    signature="baseSetUp()", annotations=["@BeforeEach"]
                ),
            },
        },
        java_files=java_files,
        import_declarations_by_file=import_declarations_by_file,
    )

    setup_methods = CommonAnalysis(analysis).get_setup_methods(
        "example.OuterTest.InnerTest"
    )

    assert _fixture_tuples(setup_methods) == [
        ("example.OuterTest", "outerSetUp()", False),
        ("example.BaseTest", "baseSetUp()", False),
    ]


def test_nested_class_keeps_same_signature_fixture_from_outer_class() -> None:
    analysis = _build_nested_analysis(
        methods_by_class={
            "example.OuterTest.InnerTest": {
                "innerTest()": make_callable(
                    signature="innerTest()", annotations=["@Test"]
                ),
                "setUp()": make_callable(
                    signature="setUp()", annotations=["@BeforeEach"]
                ),
            },
            "example.OuterTest": {
                "setUp()": make_callable(
                    signature="setUp()", annotations=["@BeforeEach"]
                ),
            },
        }
    )

    setup_methods = CommonAnalysis(analysis).get_setup_methods(
        "example.OuterTest.InnerTest"
    )

    assert _fixture_tuples(setup_methods) == [
        ("example.OuterTest.InnerTest", "setUp()", False),
        ("example.OuterTest", "setUp()", False),
    ]


def test_outer_same_signature_fixture_runs_before_inner_for_setup() -> None:
    nested_test_file = "src/test/java/example/OuterTest.java"
    analysis = FakeJavaAnalysis(
        classes={
            "example.OuterTest": make_type(),
            "example.OuterTest.InnerTest": make_type(
                parent_type="example.OuterTest",
                annotations=["@Nested"],
            ),
        },
        methods_by_class={
            "example.OuterTest.InnerTest": {
                "innerTest()": make_callable(
                    signature="innerTest()",
                    annotations=["@Test"],
                    modifiers=["public"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type="org.springframework.boot.test.web.client.TestRestTemplate",
                            argument_expr=['"/api/users"'],
                            start_line=20,
                        )
                    ],
                ),
                "setUp()": make_callable(
                    signature="setUp()", annotations=["@BeforeEach"]
                ),
            },
            "example.OuterTest": {
                "setUp()": make_callable(
                    signature="setUp()", annotations=["@BeforeEach"]
                ),
            },
        },
        java_files={
            "example.OuterTest": nested_test_file,
            "example.OuterTest.InnerTest": nested_test_file,
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
        qualified_class_name="example.OuterTest.InnerTest",
        test_methods=["innerTest()"],
    )

    setup_fixture_order = [
        (fixture.defining_class_name, fixture.method_signature)
        for fixture in result.fixtures
        if fixture.phase == LifecyclePhase.SETUP
    ]

    assert setup_fixture_order == [
        ("example.OuterTest", "setUp()"),
        ("example.OuterTest.InnerTest", "setUp()"),
    ]
