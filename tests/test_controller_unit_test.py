from __future__ import annotations

from cldk.models.java.models import JCallSite

from gerbil.analysis.properties import (
    build_controller_unit_test_summary,
    build_endpoint_handler_index,
    detect_controller_unit_test_targets,
)
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.schema import (
    ApplicationEndpoint,
    ControllerHandlerTarget,
    LifecyclePhase,
    MethodIdentity,
    TestClassAnalysis as ClassAnalysisModel,
    TestingFramework as Framework,
    TestMethodAnalysis as MethodAnalysisModel,
    TestMethodReference as MethodReferenceModel,
)
from gerbil.analysis.project import ProjectAnalysisInfo
from gerbil.analysis.shared.caching import reset_class_resolution_cache
from gerbil.analysis.test_method import MethodAnalysisInfo
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def _owner_endpoint() -> ApplicationEndpoint:
    return ApplicationEndpoint(
        http_method="GET",
        path_template="/owners/{id}",
        framework="spring",
        declaring_class_name="com.example.OwnerController",
        declaring_method_signature="getOwner(int)",
    )


def _phase_entry(
    *,
    phase: LifecyclePhase,
    class_name: str,
    method_signature: str,
    call_sites: list[JCallSite],
) -> PhaseEntry:
    return PhaseEntry(
        phase=phase,
        method_ref=MethodRef(
            defining_class_name=class_name,
            method_signature=method_signature,
        ),
        context_class_name=class_name,
        grouping=build_call_site_grouping(call_sites),
        method_details=make_callable(signature=method_signature),
        is_group_ambiguous=False,
    )


def _detect(
    *,
    view: TestRuntimeView,
    endpoints: list[ApplicationEndpoint],
    analysis: FakeJavaAnalysis,
) -> list[ControllerHandlerTarget]:
    reset_class_resolution_cache()
    resolver = build_runtime_receiver_resolver_for_testing(view, analysis=analysis)
    return detect_controller_unit_test_targets(
        runtime_view=view,
        handler_index=build_endpoint_handler_index(endpoints),
        receiver_resolver=resolver,
        analysis=analysis,
    )


# --- build_endpoint_handler_index ------------------------------------------


def test_handler_index_skips_endpoints_without_class_or_signature() -> None:
    endpoints = [
        _owner_endpoint(),
        ApplicationEndpoint(
            http_method="GET",
            path_template="/anon",
            framework="spring",
            declaring_class_name="com.example.AnonController",
            declaring_method_signature=None,
        ),
        ApplicationEndpoint(
            http_method="GET",
            path_template="/blank",
            framework="spring",
            declaring_class_name="",
            declaring_method_signature="handle()",
        ),
    ]

    index = build_endpoint_handler_index(endpoints)

    assert index.handler_keys == frozenset(
        {("com.example.OwnerController", "getOwner(int)")}
    )
    assert index.handler_signatures == frozenset({"getOwner(int)"})


def test_empty_handler_index_detects_nothing() -> None:
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="com.example.OwnerTest",
                method_signature="testGetOwner()",
                call_sites=[
                    make_call_site(
                        method_name="getOwner",
                        receiver_type="com.example.OwnerController",
                        callee_signature="getOwner(int)",
                    )
                ],
            )
        ]
    )

    assert _detect(view=view, endpoints=[], analysis=FakeJavaAnalysis()) == []


# --- detect_controller_unit_test_targets -----------------------------------


def test_detects_direct_handler_call_on_concrete_controller() -> None:
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="com.example.OwnerTest",
                method_signature="testGetOwner()",
                call_sites=[
                    make_call_site(
                        method_name="getOwner",
                        receiver_type="com.example.OwnerController",
                        callee_signature="getOwner(int)",
                    )
                ],
            )
        ]
    )

    targets = _detect(
        view=view, endpoints=[_owner_endpoint()], analysis=FakeJavaAnalysis()
    )

    assert targets == [
        ControllerHandlerTarget(
            declaring_class_name="com.example.OwnerController",
            declaring_method_signature="getOwner(int)",
        )
    ]


def test_detects_handler_inherited_from_superclass() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "com.example.PetController": make_type(
                extends_list=["com.example.AbstractCrudController"]
            ),
            "com.example.AbstractCrudController": make_type(),
        }
    )
    endpoint = ApplicationEndpoint(
        http_method="GET",
        path_template="/pets/{id}",
        framework="spring",
        declaring_class_name="com.example.AbstractCrudController",
        declaring_method_signature="getOne(java.lang.Long)",
    )
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="com.example.PetControllerTest",
                method_signature="testGetOne()",
                call_sites=[
                    make_call_site(
                        method_name="getOne",
                        receiver_type="com.example.PetController",
                        callee_signature="getOne(java.lang.Long)",
                    )
                ],
            )
        ]
    )

    targets = _detect(view=view, endpoints=[endpoint], analysis=analysis)

    assert targets == [
        ControllerHandlerTarget(
            declaring_class_name="com.example.AbstractCrudController",
            declaring_method_signature="getOne(java.lang.Long)",
        )
    ]


def test_detects_handler_declared_on_implemented_interface() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "com.example.PetApiImpl": make_type(implements_list=["com.example.PetApi"]),
            "com.example.PetApi": make_type(),
        }
    )
    endpoint = ApplicationEndpoint(
        http_method="GET",
        path_template="/pets/{id}",
        framework="spring",
        declaring_class_name="com.example.PetApi",
        declaring_method_signature="getPet(java.lang.Long)",
    )
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="com.example.PetApiTest",
                method_signature="testGetPet()",
                call_sites=[
                    make_call_site(
                        method_name="getPet",
                        receiver_type="com.example.PetApiImpl",
                        callee_signature="getPet(java.lang.Long)",
                    )
                ],
            )
        ]
    )

    targets = _detect(view=view, endpoints=[endpoint], analysis=analysis)

    assert targets == [
        ControllerHandlerTarget(
            declaring_class_name="com.example.PetApi",
            declaring_method_signature="getPet(java.lang.Long)",
        )
    ]


def test_same_signature_on_unrelated_class_is_not_a_handler() -> None:
    analysis = FakeJavaAnalysis(
        classes={"com.example.OwnerService": make_type()},
    )
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="com.example.OwnerServiceTest",
                method_signature="testGetOwner()",
                call_sites=[
                    make_call_site(
                        method_name="getOwner",
                        receiver_type="com.example.OwnerService",
                        callee_signature="getOwner(int)",
                    )
                ],
            )
        ]
    )

    assert _detect(view=view, endpoints=[_owner_endpoint()], analysis=analysis) == []


def test_unresolved_receiver_does_not_match() -> None:
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="com.example.OwnerTest",
                method_signature="testGetOwner()",
                call_sites=[
                    make_call_site(
                        method_name="getOwner",
                        callee_signature="getOwner(int)",
                    )
                ],
            )
        ]
    )

    assert (
        _detect(view=view, endpoints=[_owner_endpoint()], analysis=FakeJavaAnalysis())
        == []
    )


def test_constructor_call_is_ignored() -> None:
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="com.example.OwnerTest",
                method_signature="testGetOwner()",
                call_sites=[
                    make_call_site(
                        method_name="getOwner",
                        receiver_type="com.example.OwnerController",
                        callee_signature="getOwner(int)",
                        is_constructor_call=True,
                    )
                ],
            )
        ]
    )

    assert (
        _detect(view=view, endpoints=[_owner_endpoint()], analysis=FakeJavaAnalysis())
        == []
    )


def test_repeated_handler_call_yields_single_target() -> None:
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="com.example.OwnerTest",
                method_signature="testGetOwner()",
                call_sites=[
                    make_call_site(
                        method_name="getOwner",
                        receiver_type="com.example.OwnerController",
                        callee_signature="getOwner(int)",
                        start_line=10,
                    ),
                    make_call_site(
                        method_name="getOwner",
                        receiver_type="com.example.OwnerController",
                        callee_signature="getOwner(int)",
                        start_line=20,
                    ),
                ],
            )
        ]
    )

    targets = _detect(
        view=view, endpoints=[_owner_endpoint()], analysis=FakeJavaAnalysis()
    )

    assert targets == [
        ControllerHandlerTarget(
            declaring_class_name="com.example.OwnerController",
            declaring_method_signature="getOwner(int)",
        )
    ]


def test_handler_call_only_in_fixture_phase_is_not_a_controller_unit_test() -> None:
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.SETUP,
                class_name="com.example.OwnerTest",
                method_signature="setUp()",
                call_sites=[
                    make_call_site(
                        method_name="getOwner",
                        receiver_type="com.example.OwnerController",
                        callee_signature="getOwner(int)",
                    )
                ],
            ),
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="com.example.OwnerTest",
                method_signature="testSomethingElse()",
                call_sites=[
                    make_call_site(
                        method_name="doWork",
                        receiver_type="com.example.OwnerService",
                        callee_signature="doWork()",
                    )
                ],
            ),
        ]
    )

    assert (
        _detect(view=view, endpoints=[_owner_endpoint()], analysis=FakeJavaAnalysis())
        == []
    )


# --- pipeline (get_test_method_analysis_info) ------------------------------


def _run_pipeline(
    *,
    analysis: FakeJavaAnalysis,
    endpoints: list[ApplicationEndpoint],
    qualified_class_name: str,
    method_signature: str,
) -> MethodAnalysisModel:
    reset_class_resolution_cache()
    return MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
        endpoint_handler_index=build_endpoint_handler_index(endpoints),
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name=qualified_class_name,
        method_signature=method_signature,
        setup_methods=[],
        teardown_methods=[],
    )


def test_pipeline_flags_controller_unit_test() -> None:
    analysis = FakeJavaAnalysis(
        classes={"com.example.OwnerTest": make_type()},
        methods_by_class={
            "com.example.OwnerTest": {
                "testGetOwner()": make_callable(
                    signature="testGetOwner()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getOwner",
                            receiver_type="com.example.OwnerController",
                            callee_signature="getOwner(int)",
                            start_line=10,
                        )
                    ],
                )
            }
        },
    )

    result = _run_pipeline(
        analysis=analysis,
        endpoints=[_owner_endpoint()],
        qualified_class_name="com.example.OwnerTest",
        method_signature="testGetOwner()",
    )

    assert result.is_api_test is False
    assert result.is_controller_unit_test is True
    assert result.controller_unit_test_targets == [
        ControllerHandlerTarget(
            declaring_class_name="com.example.OwnerController",
            declaring_method_signature="getOwner(int)",
        )
    ]


def test_pipeline_api_test_takes_precedence_over_controller_unit_test() -> None:
    analysis = FakeJavaAnalysis(
        classes={"com.example.OwnerTest": make_type()},
        methods_by_class={
            "com.example.OwnerTest": {
                "testGetOwner()": make_callable(
                    signature="testGetOwner()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client."
                                "TestRestTemplate"
                            ),
                            argument_expr=['"/owners/1"'],
                            start_line=10,
                        ),
                        make_call_site(
                            method_name="getOwner",
                            receiver_type="com.example.OwnerController",
                            callee_signature="getOwner(int)",
                            start_line=11,
                        ),
                    ],
                )
            }
        },
    )

    result = _run_pipeline(
        analysis=analysis,
        endpoints=[_owner_endpoint()],
        qualified_class_name="com.example.OwnerTest",
        method_signature="testGetOwner()",
    )

    assert result.is_api_test is True
    assert result.is_controller_unit_test is False
    assert result.controller_unit_test_targets == []


def test_pipeline_non_controller_test_is_unflagged() -> None:
    analysis = FakeJavaAnalysis(
        classes={"com.example.OwnerTest": make_type()},
        methods_by_class={
            "com.example.OwnerTest": {
                "testWiring()": make_callable(
                    signature="testWiring()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="doWork",
                            receiver_type="com.example.OwnerService",
                            callee_signature="doWork()",
                            start_line=10,
                        )
                    ],
                )
            }
        },
    )

    result = _run_pipeline(
        analysis=analysis,
        endpoints=[_owner_endpoint()],
        qualified_class_name="com.example.OwnerTest",
        method_signature="testWiring()",
    )

    assert result.is_api_test is False
    assert result.is_controller_unit_test is False
    assert result.controller_unit_test_targets == []


# --- build_controller_unit_test_summary ------------------------------------


def _controller_unit_test_method(
    *,
    defining_class_name: str,
    method_signature: str,
    targets: list[ControllerHandlerTarget],
) -> MethodAnalysisModel:
    return MethodAnalysisModel(
        identity=MethodIdentity(
            defining_class_name=defining_class_name,
            method_signature=method_signature,
            method_declaration="",
        ),
        is_controller_unit_test=True,
        controller_unit_test_targets=targets,
    )


def test_summary_maps_targeted_endpoints_and_counts_tests() -> None:
    # One handler method serves two endpoints (e.g. a method handling two paths);
    # both should be reported as targeted when the handler is invoked directly.
    endpoints = [
        _owner_endpoint(),
        ApplicationEndpoint(
            http_method="GET",
            path_template="/owners",
            framework="spring",
            declaring_class_name="com.example.OwnerController",
            declaring_method_signature="getOwner(int)",
        ),
        ApplicationEndpoint(
            http_method="GET",
            path_template="/pets/{id}",
            framework="spring",
            declaring_class_name="com.example.PetController",
            declaring_method_signature="getPet(int)",
        ),
    ]
    owner_target = ControllerHandlerTarget(
        declaring_class_name="com.example.OwnerController",
        declaring_method_signature="getOwner(int)",
    )
    test_class_analyses = [
        ClassAnalysisModel(
            qualified_class_name="com.example.OwnerTest",
            test_method_analyses=[
                _controller_unit_test_method(
                    defining_class_name="com.example.OwnerTest",
                    method_signature="testGetOwner()",
                    targets=[owner_target],
                ),
                _controller_unit_test_method(
                    defining_class_name="com.example.OwnerTest",
                    method_signature="testGetOwnerAgain()",
                    targets=[owner_target],
                ),
                MethodAnalysisModel(
                    identity=MethodIdentity(
                        defining_class_name="com.example.OwnerTest",
                        method_signature="testApi()",
                        method_declaration="",
                    ),
                    is_api_test=True,
                ),
            ],
        )
    ]

    summary = build_controller_unit_test_summary(endpoints, test_class_analyses)

    assert summary.controller_unit_test_count == 2
    assert summary.targeted_endpoint_count == 2
    # Both endpoints backed by the invoked handler method are reported; the
    # unrelated PetController endpoint is not.
    assert [entry.endpoint.path_template for entry in summary.endpoints] == [
        "/owners/{id}",
        "/owners",
    ]
    for entry in summary.endpoints:
        assert entry.exercising_test_method_count == 2
        assert entry.exercising_test_methods == [
            MethodReferenceModel(
                qualified_class_name="com.example.OwnerTest",
                method_signature="testGetOwner()",
            ),
            MethodReferenceModel(
                qualified_class_name="com.example.OwnerTest",
                method_signature="testGetOwnerAgain()",
            ),
        ]


def test_summary_is_empty_without_controller_unit_tests() -> None:
    test_class_analyses = [
        ClassAnalysisModel(
            qualified_class_name="com.example.OwnerTest",
            test_method_analyses=[
                MethodAnalysisModel(
                    identity=MethodIdentity(
                        defining_class_name="com.example.OwnerTest",
                        method_signature="testApi()",
                        method_declaration="",
                    ),
                    is_api_test=True,
                )
            ],
        )
    ]

    summary = build_controller_unit_test_summary(
        [_owner_endpoint()], test_class_analyses
    )

    assert summary.controller_unit_test_count == 0
    assert summary.targeted_endpoint_count == 0
    assert summary.endpoints == []


# --- project pipeline (ProjectAnalysisInfo) --------------------------------


def test_project_analysis_reports_controller_unit_tests() -> None:
    # Fully-qualified Spring annotations let endpoint extraction resolve the
    # handler without import wiring; the test drives that handler in-process.
    analysis = FakeJavaAnalysis(
        classes={
            "example.OwnerController": make_type(
                annotations=[
                    "@org.springframework.web.bind.annotation.RestController",
                    '@org.springframework.web.bind.annotation.RequestMapping("/owners")',
                ]
            ),
            "example.OwnerControllerTest": make_type(),
        },
        methods_by_class={
            "example.OwnerController": {
                "getOwner(int)": make_callable(
                    signature="getOwner(int)",
                    annotations=[
                        '@org.springframework.web.bind.annotation.GetMapping("/{id}")'
                    ],
                )
            },
            "example.OwnerControllerTest": {
                "testGetOwner()": make_callable(
                    signature="testGetOwner()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getOwner",
                            receiver_type="example.OwnerController",
                            callee_signature="getOwner(int)",
                            start_line=10,
                        )
                    ],
                )
            },
        },
        java_files={
            "example.OwnerController": "src/main/java/example/OwnerController.java",
            "example.OwnerControllerTest": (
                "src/test/java/example/OwnerControllerTest.java"
            ),
        },
        import_declarations_by_file={
            "src/test/java/example/OwnerControllerTest.java": [
                "org.junit.jupiter.api.Test",
            ],
        },
    )

    project_analysis = ProjectAnalysisInfo(
        analysis=analysis,
        dataset_name="example",
        project_path="/tmp/example",
    ).gather_project_analysis_info()

    assert project_analysis.summary.api_test_count == 0
    assert project_analysis.summary.non_api_test_count == 1
    assert project_analysis.summary.controller_unit_test_count == 1

    controller_unit_tests = project_analysis.controller_unit_tests
    assert controller_unit_tests.controller_unit_test_count == 1
    assert controller_unit_tests.targeted_endpoint_count == 1
    entry = controller_unit_tests.endpoints[0]
    assert entry.endpoint.path_template == "/owners/{id}"
    assert entry.endpoint.declaring_method_signature == "getOwner(int)"
    assert entry.exercising_test_method_count == 1
    assert entry.exercising_test_methods[0].method_signature == "testGetOwner()"

    test_method = project_analysis.test_class_analyses[0].test_method_analyses[0]
    assert test_method.is_controller_unit_test is True
    assert test_method.controller_unit_test_targets == [
        ControllerHandlerTarget(
            declaring_class_name="example.OwnerController",
            declaring_method_signature="getOwner(int)",
        )
    ]
