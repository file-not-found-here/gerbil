from gerbil.analysis.shared.constants import (
    SETUP_ANNOTATION_PRIORITY,
    TEARDOWN_ANNOTATION_PRIORITY,
)
from gerbil.analysis.shared import CommonAnalysis, Reachability
from gerbil.analysis.runtime import FixtureMethod
from gerbil.analysis.schema import LifecyclePhase, TestingFramework as Framework
from gerbil.analysis.test_method.test_method_analysis_info import (
    MethodAnalysisInfo,
)
from tests.cldk_factories import make_callable, make_type
from tests.fake_java_analysis import FakeJavaAnalysis

_COMMON_TEST_IMPORTS: list[str] = [
    "org.junit.jupiter.api.BeforeAll",
    "org.junit.jupiter.api.BeforeEach",
    "org.junit.jupiter.api.AfterAll",
    "org.junit.jupiter.api.AfterEach",
    "org.testng.annotations.BeforeClass",
    "org.testng.annotations.BeforeMethod",
    "org.testng.annotations.AfterClass",
    "org.testng.annotations.AfterMethod",
    "org.testng.annotations.Test",
]


def _java_file_path(qualified_class_name: str) -> str:
    return f"src/test/java/{qualified_class_name.replace('.', '/')}.java"


def _build_analysis(classes: dict[str, object], methods_by_class: dict[str, dict]):
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


def test_setup_annotation_priority_maps_all_setup_annotations() -> None:
    assert SETUP_ANNOTATION_PRIORITY["@BeforeSuite"] == -2
    assert SETUP_ANNOTATION_PRIORITY["@BeforeTest"] == -1
    assert SETUP_ANNOTATION_PRIORITY["@BeforeAll"] == 0
    assert SETUP_ANNOTATION_PRIORITY["@BeforeClass"] == 0
    assert SETUP_ANNOTATION_PRIORITY["@BeforeGroups"] == 0
    assert SETUP_ANNOTATION_PRIORITY["@BeforeEach"] == 1
    assert SETUP_ANNOTATION_PRIORITY["@Before"] == 1
    assert SETUP_ANNOTATION_PRIORITY["@BeforeMethod"] == 1


def test_teardown_annotation_priority_maps_all_teardown_annotations() -> None:
    assert TEARDOWN_ANNOTATION_PRIORITY["@AfterEach"] == 0
    assert TEARDOWN_ANNOTATION_PRIORITY["@After"] == 0
    assert TEARDOWN_ANNOTATION_PRIORITY["@AfterMethod"] == 0
    assert TEARDOWN_ANNOTATION_PRIORITY["@AfterAll"] == 1
    assert TEARDOWN_ANNOTATION_PRIORITY["@AfterClass"] == 1
    assert TEARDOWN_ANNOTATION_PRIORITY["@AfterGroups"] == 1
    assert TEARDOWN_ANNOTATION_PRIORITY["@AfterTest"] == 2
    assert TEARDOWN_ANNOTATION_PRIORITY["@AfterSuite"] == 3


def test_setup_entries_ordered_parent_before_child_for_same_priority() -> None:
    analysis = _build_analysis(
        classes={
            "example.ChildTest": make_type(extends_list=["example.ParentTest"]),
            "example.ParentTest": make_type(extends_list=["example.GrandParentTest"]),
            "example.GrandParentTest": make_type(),
        },
        methods_by_class={
            "example.ChildTest": {
                "childSetup()": make_callable(
                    signature="childSetup()", annotations=["@BeforeEach"]
                ),
                "testSomething()": make_callable(signature="testSomething()"),
            },
            "example.ParentTest": {
                "parentSetup()": make_callable(
                    signature="parentSetup()", annotations=["@BeforeEach"]
                ),
            },
            "example.GrandParentTest": {
                "grandSetup()": make_callable(
                    signature="grandSetup()", annotations=["@BeforeEach"]
                ),
            },
        },
    )

    common_analysis = CommonAnalysis(analysis)
    reachability = Reachability(analysis)
    resolve_helper, load_call_sites = reachability.build_helper_resolver(
        qualified_class_name="example.ChildTest",
    )
    setup_methods = common_analysis.get_setup_methods("example.ChildTest")
    teardown_methods = common_analysis.get_teardown_methods("example.ChildTest")

    info = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    )
    runtime_view = info._build_test_runtime_view(
        qualified_class_name="example.ChildTest",
        method_signature="testSomething()",
        method_details=analysis.get_method("example.ChildTest", "testSomething()"),
        setup_methods=setup_methods,
        teardown_methods=teardown_methods,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
    )

    setup_entries = [
        entry for entry in runtime_view.entries if entry.phase == LifecyclePhase.SETUP
    ]
    setup_classes = [entry.method_ref.defining_class_name for entry in setup_entries]

    assert setup_classes == [
        "example.GrandParentTest",
        "example.ParentTest",
        "example.ChildTest",
    ]


def test_setup_entries_ordered_super_interface_class_for_same_priority() -> None:
    analysis = _build_analysis(
        classes={
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
            "example.ParentFixture": make_type(),
        },
        methods_by_class={
            "example.ChildTest": {
                "childSetup()": make_callable(
                    signature="childSetup()",
                    annotations=["@BeforeEach"],
                ),
                "testSomething()": make_callable(signature="testSomething()"),
            },
            "example.ParentTest": {
                "parentSetup()": make_callable(
                    signature="parentSetup()",
                    annotations=["@BeforeEach"],
                ),
            },
            "example.GrandParentTest": {
                "grandSetup()": make_callable(
                    signature="grandSetup()",
                    annotations=["@BeforeEach"],
                ),
            },
            "example.ChildFixture": {
                "childInterfaceSetup()": make_callable(
                    signature="childInterfaceSetup()",
                    annotations=["@BeforeEach"],
                ),
            },
            "example.ParentFixture": {
                "parentInterfaceSetup()": make_callable(
                    signature="parentInterfaceSetup()",
                    annotations=["@BeforeEach"],
                ),
            },
        },
    )
    common_analysis = CommonAnalysis(analysis)
    reachability = Reachability(analysis)
    resolve_helper, load_call_sites = reachability.build_helper_resolver(
        qualified_class_name="example.ChildTest",
    )
    setup_methods = common_analysis.get_setup_methods("example.ChildTest")

    info = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    )
    runtime_view = info._build_test_runtime_view(
        qualified_class_name="example.ChildTest",
        method_signature="testSomething()",
        method_details=analysis.get_method("example.ChildTest", "testSomething()"),
        setup_methods=setup_methods,
        teardown_methods=[],
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        testing_frameworks=[Framework.JUNIT5],
    )

    setup_entries = [
        entry for entry in runtime_view.entries if entry.phase == LifecyclePhase.SETUP
    ]
    setup_classes = [entry.method_ref.defining_class_name for entry in setup_entries]

    assert setup_classes == [
        "example.GrandParentTest",
        "example.ParentTest",
        "example.ParentFixture",
        "example.ChildFixture",
        "example.ChildTest",
    ]


def test_teardown_entries_ordered_class_interface_super_for_same_priority() -> None:
    analysis = _build_analysis(
        classes={
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
            "example.ParentFixture": make_type(),
        },
        methods_by_class={
            "example.ChildTest": {
                "childTeardown()": make_callable(
                    signature="childTeardown()",
                    annotations=["@AfterEach"],
                ),
                "testSomething()": make_callable(signature="testSomething()"),
            },
            "example.ParentTest": {
                "parentTeardown()": make_callable(
                    signature="parentTeardown()",
                    annotations=["@AfterEach"],
                ),
            },
            "example.GrandParentTest": {
                "grandTeardown()": make_callable(
                    signature="grandTeardown()",
                    annotations=["@AfterEach"],
                ),
            },
            "example.ChildFixture": {
                "childInterfaceTeardown()": make_callable(
                    signature="childInterfaceTeardown()",
                    annotations=["@AfterEach"],
                ),
            },
            "example.ParentFixture": {
                "parentInterfaceTeardown()": make_callable(
                    signature="parentInterfaceTeardown()",
                    annotations=["@AfterEach"],
                ),
            },
        },
    )
    common_analysis = CommonAnalysis(analysis)
    reachability = Reachability(analysis)
    resolve_helper, load_call_sites = reachability.build_helper_resolver(
        qualified_class_name="example.ChildTest",
    )
    teardown_methods = common_analysis.get_teardown_methods("example.ChildTest")

    info = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    )
    runtime_view = info._build_test_runtime_view(
        qualified_class_name="example.ChildTest",
        method_signature="testSomething()",
        method_details=analysis.get_method("example.ChildTest", "testSomething()"),
        setup_methods=[],
        teardown_methods=teardown_methods,
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        testing_frameworks=[Framework.JUNIT5],
    )

    teardown_entries = [
        entry
        for entry in runtime_view.entries
        if entry.phase == LifecyclePhase.TEARDOWN
    ]
    teardown_classes = [
        entry.method_ref.defining_class_name for entry in teardown_entries
    ]

    assert teardown_classes == [
        "example.ChildTest",
        "example.ChildFixture",
        "example.ParentFixture",
        "example.ParentTest",
        "example.GrandParentTest",
    ]


def test_equal_priority_equal_depth_same_class_orders_by_signature() -> None:
    analysis = _build_analysis(
        classes={"example.Test": make_type()},
        methods_by_class={
            "example.Test": {
                "zzzSetup()": make_callable(
                    signature="zzzSetup()",
                    annotations=["@BeforeEach"],
                ),
                "aaaSetup()": make_callable(
                    signature="aaaSetup()",
                    annotations=["@BeforeEach"],
                ),
                "testSomething()": make_callable(signature="testSomething()"),
            }
        },
    )

    common_analysis = CommonAnalysis(analysis)
    reachability = Reachability(analysis)
    resolve_helper, load_call_sites = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
    )
    setup_methods = common_analysis.get_setup_methods("example.Test")

    info = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    )
    runtime_view = info._build_test_runtime_view(
        qualified_class_name="example.Test",
        method_signature="testSomething()",
        method_details=analysis.get_method("example.Test", "testSomething()"),
        setup_methods=setup_methods,
        teardown_methods=[],
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        testing_frameworks=[Framework.JUNIT5],
    )

    setup_entries = [
        entry for entry in runtime_view.entries if entry.phase == LifecyclePhase.SETUP
    ]
    setup_signatures = [entry.method_ref.method_signature for entry in setup_entries]

    assert setup_signatures == ["aaaSetup()", "zzzSetup()"]


def test_unresolved_fixture_owner_sorts_last_within_same_priority() -> None:
    analysis = _build_analysis(
        classes={"example.Test": make_type()},
        methods_by_class={
            "example.Test": {
                "localFixture()": make_callable(
                    signature="localFixture()",
                    annotations=[],
                ),
                "testSomething()": make_callable(signature="testSomething()"),
            }
        },
    )
    reachability = Reachability(analysis)
    resolve_helper, load_call_sites = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
    )
    info = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    )
    runtime_view = info._build_test_runtime_view(
        qualified_class_name="example.Test",
        method_signature="testSomething()",
        method_details=analysis.get_method("example.Test", "testSomething()"),
        setup_methods=[
            FixtureMethod(
                defining_class_name="z.missing.Owner",
                method_signature="zzzMissing()",
            ),
            FixtureMethod(
                defining_class_name="example.Test",
                method_signature="localFixture()",
            ),
            FixtureMethod(
                defining_class_name="a.missing.Owner",
                method_signature="aaaMissing()",
            ),
        ],
        teardown_methods=[],
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        testing_frameworks=[Framework.JUNIT5],
    )
    setup_entries = [
        entry for entry in runtime_view.entries if entry.phase == LifecyclePhase.SETUP
    ]
    setup_refs = [
        (
            entry.method_ref.defining_class_name,
            entry.method_ref.method_signature,
        )
        for entry in setup_entries
    ]

    assert setup_refs == [
        ("example.Test", "localFixture()"),
        ("a.missing.Owner", "aaaMissing()"),
        ("z.missing.Owner", "zzzMissing()"),
    ]


def test_testng_profile_uses_explicit_relation_ordering() -> None:
    analysis = _build_analysis(
        classes={
            "example.ChildTest": make_type(extends_list=["example.ParentTest"]),
            "example.ParentTest": make_type(),
        },
        methods_by_class={
            "example.ChildTest": {
                "childSetup()": make_callable(
                    signature="childSetup()",
                    annotations=["@BeforeMethod"],
                ),
                "testSomething()": make_callable(signature="testSomething()"),
            },
            "example.ParentTest": {
                "parentSetup()": make_callable(
                    signature="parentSetup()",
                    annotations=["@BeforeMethod"],
                ),
            },
        },
    )
    common_analysis = CommonAnalysis(analysis)
    reachability = Reachability(analysis)
    resolve_helper, load_call_sites = reachability.build_helper_resolver(
        qualified_class_name="example.ChildTest",
    )
    setup_methods = common_analysis.get_setup_methods("example.ChildTest")

    info = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        expanded_helper_depth=0,
    )
    runtime_view = info._build_test_runtime_view(
        qualified_class_name="example.ChildTest",
        method_signature="testSomething()",
        method_details=analysis.get_method("example.ChildTest", "testSomething()"),
        setup_methods=setup_methods,
        teardown_methods=[],
        resolve_helper=resolve_helper,
        load_call_sites=load_call_sites,
        testing_frameworks=[Framework.TESTNG],
    )
    setup_entries = [
        entry for entry in runtime_view.entries if entry.phase == LifecyclePhase.SETUP
    ]
    setup_classes = [entry.method_ref.defining_class_name for entry in setup_entries]

    assert setup_classes == [
        "example.ParentTest",
        "example.ChildTest",
    ]
