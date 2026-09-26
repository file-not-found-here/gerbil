from __future__ import annotations

from cldk.models.java.models import JCallSite

from gerbil.analysis.runtime.call_sites import (
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.schema import CallSiteOriginKind, LifecyclePhase
from gerbil.analysis.runtime import FixtureMethod, PhaseEntry, TestRuntimeView
from gerbil.analysis.test_method.test_method_analysis_info import (
    MethodAnalysisInfo,
)
from tests.cldk_factories import make_call_site, make_callable
from tests.fake_java_analysis import FakeJavaAnalysis


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


def test_test_runtime_view_iter_events_concatenates_phases_in_order() -> None:
    setup_cs = make_call_site(method_name="setupCall", start_line=1, start_column=1)
    test_cs = make_call_site(method_name="testCall", start_line=2, start_column=1)
    teardown_cs = make_call_site(
        method_name="teardownCall", start_line=3, start_column=1
    )

    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.SETUP,
                class_name="T",
                method_signature="setUp()",
                call_sites=[setup_cs],
            ),
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="T",
                method_signature="test()",
                call_sites=[test_cs],
            ),
            _phase_entry(
                phase=LifecyclePhase.TEARDOWN,
                class_name="T",
                method_signature="tearDown()",
                call_sites=[teardown_cs],
            ),
        ],
    )

    method_names = [event.call_site.method_name for event in view.iter_events()]
    assert method_names == ["setupCall", "testCall", "teardownCall"]


def test_test_runtime_view_events_expose_phase_owner_and_depth() -> None:
    setup_cs = make_call_site(method_name="setupCall", start_line=1, start_column=1)
    test_cs = make_call_site(method_name="testCall", start_line=2, start_column=1)

    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.SETUP,
                class_name="T",
                method_signature="setUp()",
                call_sites=[setup_cs],
            ),
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="T",
                method_signature="test()",
                call_sites=[test_cs],
            ),
        ],
    )

    events = view.iter_events()
    assert events[0].phase == LifecyclePhase.SETUP
    assert events[0].owner == MethodRef(
        defining_class_name="T", method_signature="setUp()"
    )
    assert events[0].depth == 0
    assert events[0].origin_kind == CallSiteOriginKind.FIXTURE

    assert events[1].phase == LifecyclePhase.TEST
    assert events[1].owner == MethodRef(
        defining_class_name="T", method_signature="test()"
    )
    assert events[1].depth == 0
    assert events[1].origin_kind == CallSiteOriginKind.TEST_METHOD


def test_test_runtime_view_phase_events_filters_correctly() -> None:
    setup_cs = make_call_site(method_name="setupCall", start_line=1, start_column=1)
    test_cs = make_call_site(method_name="testCall", start_line=2, start_column=1)

    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.SETUP,
                class_name="T",
                method_signature="setUp()",
                call_sites=[setup_cs],
            ),
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="T",
                method_signature="test()",
                call_sites=[test_cs],
            ),
        ],
    )

    setup_names = [
        event.call_site.method_name for event in view.phase_events(LifecyclePhase.SETUP)
    ]
    test_names = [
        event.call_site.method_name for event in view.phase_events(LifecyclePhase.TEST)
    ]
    teardown_names = [
        event.call_site.method_name
        for event in view.phase_events(LifecyclePhase.TEARDOWN)
    ]
    assert setup_names == ["setupCall"]
    assert test_names == ["testCall"]
    assert teardown_names == []


def test_test_runtime_view_test_events_filters_test_phase() -> None:
    setup_cs = make_call_site(method_name="setupCall", start_line=1, start_column=1)
    test_cs = make_call_site(method_name="testCall", start_line=2, start_column=1)

    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.SETUP,
                class_name="T",
                method_signature="setUp()",
                call_sites=[setup_cs],
            ),
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="T",
                method_signature="test()",
                call_sites=[test_cs],
            ),
        ],
    )

    test_names = [event.call_site.method_name for event in view.test_events()]
    assert test_names == ["testCall"]


def test_test_runtime_view_iter_events_uses_cached_list() -> None:
    test_cs = make_call_site(method_name="testCall", start_line=1, start_column=1)
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="T",
                method_signature="test()",
                call_sites=[test_cs],
            )
        ]
    )

    assert view.iter_events() is view.iter_events()


def test_test_runtime_view_iter_events_refreshes_after_entries_append() -> None:
    test_cs = make_call_site(method_name="testCall", start_line=1, start_column=1)
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="T",
                method_signature="test()",
                call_sites=[test_cs],
            )
        ]
    )

    assert [event.call_site.method_name for event in view.iter_events()] == ["testCall"]

    teardown_cs = make_call_site(
        method_name="teardownCall", start_line=2, start_column=1
    )
    view.entries.append(
        _phase_entry(
            phase=LifecyclePhase.TEARDOWN,
            class_name="T",
            method_signature="tearDown()",
            call_sites=[teardown_cs],
        )
    )

    assert [event.call_site.method_name for event in view.iter_events()] == [
        "testCall",
        "teardownCall",
    ]


def test_test_runtime_view_iter_events_refreshes_after_entry_grouping_mutation() -> (
    None
):
    initial_cs = make_call_site(method_name="initialCall", start_line=1, start_column=1)
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="T",
                method_signature="test()",
                call_sites=[initial_cs],
            )
        ]
    )

    assert [event.call_site.method_name for event in view.iter_events()] == [
        "initialCall"
    ]

    replacement_cs = make_call_site(
        method_name="replacementCall", start_line=2, start_column=1
    )
    view.entries[0].grouping = build_call_site_grouping([replacement_cs])

    assert [event.call_site.method_name for event in view.iter_events()] == [
        "replacementCall"
    ]


def test_test_runtime_view_iter_events_refreshes_after_entries_replace_and_remove() -> (
    None
):
    setup_cs = make_call_site(method_name="setupCall", start_line=1, start_column=1)
    test_cs = make_call_site(method_name="testCall", start_line=2, start_column=1)
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.SETUP,
                class_name="T",
                method_signature="setUp()",
                call_sites=[setup_cs],
            ),
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="T",
                method_signature="test()",
                call_sites=[test_cs],
            ),
        ]
    )

    assert [event.call_site.method_name for event in view.iter_events()] == [
        "setupCall",
        "testCall",
    ]

    replacement_cs = make_call_site(
        method_name="replacementCall", start_line=3, start_column=1
    )
    view.entries[1] = _phase_entry(
        phase=LifecyclePhase.TEST,
        class_name="T",
        method_signature="replacementTest()",
        call_sites=[replacement_cs],
    )
    view.entries.pop(0)

    assert [event.call_site.method_name for event in view.iter_events()] == [
        "replacementCall"
    ]


def test_test_runtime_view_event_views_stay_coherent_after_interleaved_mutations() -> (
    None
):
    setup_cs = make_call_site(method_name="setupCall", start_line=1, start_column=1)
    test_cs = make_call_site(method_name="testCall", start_line=2, start_column=1)
    view = TestRuntimeView(
        entries=[
            _phase_entry(
                phase=LifecyclePhase.SETUP,
                class_name="T",
                method_signature="setUp()",
                call_sites=[setup_cs],
            ),
            _phase_entry(
                phase=LifecyclePhase.TEST,
                class_name="T",
                method_signature="test()",
                call_sites=[test_cs],
            ),
        ]
    )

    _ = view.iter_events()

    teardown_cs = make_call_site(
        method_name="teardownCall", start_line=3, start_column=1
    )
    view.entries.append(
        _phase_entry(
            phase=LifecyclePhase.TEARDOWN,
            class_name="T",
            method_signature="tearDown()",
            call_sites=[teardown_cs],
        )
    )

    assert [event.call_site.method_name for event in view.test_events()] == ["testCall"]
    assert [event.call_site.method_name for event in view.iter_events()] == [
        "setupCall",
        "testCall",
        "teardownCall",
    ]
    assert [
        event.call_site.method_name
        for event in view.phase_events(LifecyclePhase.TEARDOWN)
    ] == ["teardownCall"]

    updated_test_cs = make_call_site(
        method_name="updatedTestCall", start_line=4, start_column=1
    )
    view.entries[1] = _phase_entry(
        phase=LifecyclePhase.TEST,
        class_name="T",
        method_signature="updatedTest()",
        call_sites=[updated_test_cs],
    )

    test_phase_snapshot = [
        event.call_site.method_name for event in view.phase_events(LifecyclePhase.TEST)
    ]
    full_snapshot = [event.call_site.method_name for event in view.iter_events()]
    interleaved_test_snapshot = [
        event.call_site.method_name for event in view.test_events()
    ]

    assert test_phase_snapshot == ["updatedTestCall"]
    assert interleaved_test_snapshot == test_phase_snapshot
    assert full_snapshot == ["setupCall", "updatedTestCall", "teardownCall"]


def test_test_runtime_view_test_entry_returns_test_phase() -> None:
    test_cs = make_call_site(method_name="testCall", start_line=1, start_column=1)
    test_entry = PhaseEntry(
        phase=LifecyclePhase.TEST,
        method_ref=MethodRef(defining_class_name="T", method_signature="test()"),
        context_class_name="T",
        grouping=build_call_site_grouping([test_cs]),
        method_details=make_callable(signature="test()"),
        is_group_ambiguous=False,
    )
    view = TestRuntimeView(entries=[test_entry])
    assert view.test_entry() is test_entry


def test_build_test_runtime_view_orders_fixtures_by_annotation_priority() -> None:
    methods_by_class = {
        "example.Test": {
            "testCase()": make_callable(
                signature="testCase()",
                annotations=["@Test"],
                call_sites=[
                    make_call_site(
                        method_name="testCall", start_line=10, start_column=1
                    )
                ],
            ),
            "setUpEach()": make_callable(
                signature="setUpEach()",
                annotations=["@BeforeEach"],
                call_sites=[
                    make_call_site(
                        method_name="setupEach", start_line=1, start_column=1
                    )
                ],
            ),
            "setUpAll()": make_callable(
                signature="setUpAll()",
                annotations=["@BeforeAll"],
                call_sites=[
                    make_call_site(method_name="setupAll", start_line=2, start_column=1)
                ],
            ),
            "tearDownAll()": make_callable(
                signature="tearDownAll()",
                annotations=["@AfterAll"],
                call_sites=[
                    make_call_site(
                        method_name="teardownAll", start_line=3, start_column=1
                    )
                ],
            ),
            "tearDownEach()": make_callable(
                signature="tearDownEach()",
                annotations=["@AfterEach"],
                call_sites=[
                    make_call_site(
                        method_name="teardownEach", start_line=4, start_column=1
                    )
                ],
            ),
        }
    }
    analysis = FakeJavaAnalysis(methods_by_class=methods_by_class)
    analyzer = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    )

    runtime_view = analyzer._build_test_runtime_view(
        qualified_class_name="example.Test",
        method_signature="testCase()",
        method_details=methods_by_class["example.Test"]["testCase()"],
        setup_methods=[
            FixtureMethod(
                defining_class_name="example.Test",
                method_signature="setUpEach()",
            ),
            FixtureMethod(
                defining_class_name="example.Test",
                method_signature="setUpAll()",
            ),
        ],
        teardown_methods=[
            FixtureMethod(
                defining_class_name="example.Test",
                method_signature="tearDownAll()",
            ),
            FixtureMethod(
                defining_class_name="example.Test",
                method_signature="tearDownEach()",
            ),
        ],
        resolve_helper=lambda _owner, _call_site: None,
        load_call_sites=lambda _method_ref: None,
    )

    ordered_signatures = [
        entry.method_ref.method_signature for entry in runtime_view.entries
    ]
    assert ordered_signatures == [
        "setUpAll()",
        "setUpEach()",
        "testCase()",
        "tearDownEach()",
        "tearDownAll()",
    ]
    assert all(
        entry.context_class_name == "example.Test" for entry in runtime_view.entries
    )
