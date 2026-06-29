from __future__ import annotations

import pytest

from cldk.models.java.models import JCallSite

from gerbil.analysis.runtime.call_sites import build_call_site_grouping, MethodRef
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.schema import (
    CrudLifecycleLabel,
    CrudOperation,
    HttpRequestRole,
    LifecyclePhase,
    ResourceInteractionSequence,
)
from gerbil.analysis.properties.resource_interaction.detection import (
    detect_resource_interaction_sequences,
)
from tests.cldk_factories import (
    annotate_node_http,
    make_call_site,
)

_OWNER = MethodRef(
    defining_class_name="example.ResourceInteractionTest",
    method_signature="test()",
)


def _test_runtime_view(
    call_sites: list[JCallSite],
) -> TestRuntimeView:
    return TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=_OWNER,
                context_class_name=_OWNER.defining_class_name,
                grouping=build_call_site_grouping(call_sites),
                method_details=None,
            )
        ]
    )


def _detect(
    *,
    call_sites: list[JCallSite],
    http_calls: list[tuple[JCallSite, str, str]],
) -> list[ResourceInteractionSequence]:
    runtime_view = _test_runtime_view(call_sites)
    grouping = runtime_view.entries[0].grouping
    for call_site, http_method, http_path in http_calls:
        node = grouping.node_for_call_site(call_site)
        assert node is not None
        annotate_node_http(
            node,
            http_method=http_method,
            path=http_path,
            framework="rest-assured",
            request_role=HttpRequestRole.EVENT,
        )
    return detect_resource_interaction_sequences(runtime_view=runtime_view)


def test_post_then_get_same_resource_produces_single_sequence() -> None:
    post_call = make_call_site(method_name="post", start_line=10)
    get_call = make_call_site(method_name="get", start_line=20)
    sequences = _detect(
        call_sites=[post_call, get_call],
        http_calls=[
            (post_call, "POST", "/users/1"),
            (get_call, "GET", "/users/2"),
        ],
    )

    assert len(sequences) == 1
    seq = sequences[0]
    assert seq.resource_key == "/users"
    assert len(seq.steps) == 2
    assert seq.steps[0].http_method == "POST"
    assert seq.steps[0].crud_operation == CrudOperation.CREATE
    assert seq.steps[0].normalized_path == "/users/{id}"
    assert seq.steps[0].phase == LifecyclePhase.TEST
    assert seq.steps[1].http_method == "GET"
    assert seq.steps[1].crud_operation == CrudOperation.READ
    assert seq.steps[1].normalized_path == "/users/{id}"
    assert seq.steps[1].phase == LifecyclePhase.TEST
    assert seq.exercised_operations == [CrudOperation.CREATE, CrudOperation.READ]
    assert seq.lifecycle_label == CrudLifecycleLabel.CREATE_VERIFY
    assert seq.has_read_after_write is True
    assert seq.has_cleanup_delete is False


def test_collection_post_then_instance_get_groups_together() -> None:
    post_call = make_call_site(method_name="post", start_line=10)
    get_call = make_call_site(method_name="get", start_line=20)
    sequences = _detect(
        call_sites=[post_call, get_call],
        http_calls=[
            (post_call, "POST", "/users"),
            (get_call, "GET", "/users/42"),
        ],
    )

    assert len(sequences) == 1
    seq = sequences[0]
    assert seq.resource_key == "/users"
    assert seq.steps[0].http_method == "POST"
    assert seq.steps[0].normalized_path == "/users"
    assert seq.steps[1].http_method == "GET"
    assert seq.steps[1].normalized_path == "/users/{id}"
    assert seq.lifecycle_label == CrudLifecycleLabel.CREATE_VERIFY


def test_different_resources_produce_separate_sequences() -> None:
    post_users = make_call_site(method_name="post", start_line=10)
    get_orders = make_call_site(method_name="get", start_line=20)
    sequences = _detect(
        call_sites=[post_users, get_orders],
        http_calls=[
            (post_users, "POST", "/users/1"),
            (get_orders, "GET", "/orders/1"),
        ],
    )

    assert len(sequences) == 2
    keys = {seq.resource_key for seq in sequences}
    assert keys == {"/users", "/orders"}


def test_dynamic_path_events_are_skipped() -> None:
    post_call = make_call_site(method_name="post", start_line=10)
    get_call = make_call_site(method_name="get", start_line=20)
    sequences = _detect(
        call_sites=[post_call, get_call],
        http_calls=[
            (post_call, "POST", "/users/${id}"),
            (get_call, "GET", "/users/1"),
        ],
    )

    assert len(sequences) == 1
    assert sequences[0].steps[0].http_method == "GET"


def test_literal_dynamic_segment_is_not_treated_as_unresolved() -> None:
    # A real route ending in a literal "dynamic" segment (e.g. HertzBeat's
    # GET /api/apps/{monitorId}/define/dynamic) must resolve to a sequence
    # rather than being dropped as an unresolved path.
    get_call = make_call_site(method_name="get", start_line=10)
    sequences = _detect(
        call_sites=[get_call],
        http_calls=[(get_call, "GET", "/api/apps/{monitorId}/define/dynamic")],
    )

    assert len(sequences) == 1
    seq = sequences[0]
    assert seq.steps[0].normalized_path == "/api/apps/{id}/define/dynamic"
    assert seq.resource_key == "/api/apps/{id}/define/dynamic"


def test_template_path_events_group_with_literal_paths() -> None:
    post = make_call_site(method_name="post", start_line=10)
    get = make_call_site(method_name="get", start_line=20)
    delete = make_call_site(method_name="delete", start_line=30)
    sequences = _detect(
        call_sites=[post, get, delete],
        http_calls=[
            (post, "POST", "/products/{name}"),
            (get, "GET", "/products"),
            (delete, "DELETE", "/products/{name}"),
        ],
    )

    assert len(sequences) == 1
    seq = sequences[0]
    assert seq.resource_key == "/products"
    assert seq.steps[0].normalized_path == "/products/{id}"
    assert seq.lifecycle_label == CrudLifecycleLabel.CREATE_VERIFY_CLEANUP
    assert seq.has_cleanup_delete is True


def test_full_crud_sequence() -> None:
    post = make_call_site(method_name="post", start_line=10)
    get1 = make_call_site(method_name="get", start_line=20)
    put = make_call_site(method_name="put", start_line=30)
    get2 = make_call_site(method_name="get", start_line=40)
    delete = make_call_site(method_name="delete", start_line=50)
    sequences = _detect(
        call_sites=[post, get1, put, get2, delete],
        http_calls=[
            (post, "POST", "/items/1"),
            (get1, "GET", "/items/1"),
            (put, "PUT", "/items/1"),
            (get2, "GET", "/items/1"),
            (delete, "DELETE", "/items/1"),
        ],
    )

    assert len(sequences) == 1
    methods = [step.http_method for step in sequences[0].steps]
    assert methods == ["POST", "GET", "PUT", "GET", "DELETE"]
    assert sequences[0].exercised_operations == [
        CrudOperation.CREATE,
        CrudOperation.READ,
        CrudOperation.UPDATE,
        CrudOperation.DELETE,
    ]
    assert sequences[0].lifecycle_label == CrudLifecycleLabel.FULL_CRUD


@pytest.mark.parametrize(
    ("methods", "expected_label"),
    [
        (["POST"], CrudLifecycleLabel.CREATE_AND_TRUST),
        (["POST", "GET"], CrudLifecycleLabel.CREATE_VERIFY),
        (["POST", "PUT", "GET"], CrudLifecycleLabel.CREATE_UPDATE_VERIFY),
        (["POST", "PATCH", "GET"], CrudLifecycleLabel.CREATE_UPDATE_VERIFY),
        (["POST", "GET", "DELETE"], CrudLifecycleLabel.CREATE_VERIFY_CLEANUP),
        (["DELETE", "GET"], CrudLifecycleLabel.DELETE_VERIFY),
        (["POST", "PATCH", "DELETE"], CrudLifecycleLabel.WRITE_ONLY),
        (["GET", "HEAD"], CrudLifecycleLabel.READ_ONLY),
        (["PUT", "POST", "GET"], CrudLifecycleLabel.OTHER),
        (["GET", "PATCH", "POST", "GET"], CrudLifecycleLabel.OTHER),
    ],
)
def test_crud_lifecycle_labels_are_derived_from_ordered_steps(
    methods: list[str],
    expected_label: CrudLifecycleLabel,
) -> None:
    call_sites = [
        make_call_site(method_name=method.lower(), start_line=(index + 1) * 10)
        for index, method in enumerate(methods)
    ]
    sequences = _detect(
        call_sites=call_sites,
        http_calls=[
            (call_site, method, "/items/1")
            for call_site, method in zip(call_sites, methods, strict=True)
        ],
    )

    assert len(sequences) == 1
    assert sequences[0].lifecycle_label == expected_label


def test_cleanup_delete_is_true_for_delete_after_create() -> None:
    post = make_call_site(method_name="post", start_line=10)
    get = make_call_site(method_name="get", start_line=20)
    delete = make_call_site(method_name="delete", start_line=30)
    sequences = _detect(
        call_sites=[post, get, delete],
        http_calls=[
            (post, "POST", "/items/1"),
            (get, "GET", "/items/1"),
            (delete, "DELETE", "/items/1"),
        ],
    )

    assert sequences[0].has_cleanup_delete is True


def test_no_http_events_returns_empty() -> None:
    call = make_call_site(method_name="assertEquals", start_line=10)
    runtime_view = _test_runtime_view([call])
    sequences = detect_resource_interaction_sequences(runtime_view=runtime_view)
    assert sequences == []


def _detect_multi_phase(
    *,
    phases: list[tuple[LifecyclePhase, list[JCallSite]]],
    http_calls: list[tuple[int, JCallSite, str, str]],
) -> list[ResourceInteractionSequence]:
    entries = []
    for phase, call_sites in phases:
        entries.append(
            PhaseEntry(
                phase=phase,
                method_ref=_OWNER,
                context_class_name=_OWNER.defining_class_name,
                grouping=build_call_site_grouping(call_sites),
                method_details=None,
            )
        )
    runtime_view = TestRuntimeView(entries=entries)
    for phase_idx, call_site, http_method, http_path in http_calls:
        node = runtime_view.entries[phase_idx].grouping.node_for_call_site(call_site)
        assert node is not None
        annotate_node_http(
            node,
            http_method=http_method,
            path=http_path,
            framework="rest-assured",
            request_role=HttpRequestRole.EVENT,
        )
    return detect_resource_interaction_sequences(runtime_view=runtime_view)


def test_setup_phase_events_are_included() -> None:
    setup_call = make_call_site(method_name="post", start_line=5)
    test_call = make_call_site(method_name="get", start_line=15)

    sequences = _detect_multi_phase(
        phases=[
            (LifecyclePhase.SETUP, [setup_call]),
            (LifecyclePhase.TEST, [test_call]),
        ],
        http_calls=[
            (0, setup_call, "POST", "/users/1"),
            (1, test_call, "GET", "/users/1"),
        ],
    )

    assert len(sequences) == 1
    assert len(sequences[0].steps) == 2
    assert sequences[0].steps[0].http_method == "POST"
    assert sequences[0].steps[0].phase == LifecyclePhase.SETUP
    assert sequences[0].steps[1].http_method == "GET"
    assert sequences[0].steps[1].phase == LifecyclePhase.TEST


def test_teardown_phase_events_are_included() -> None:
    test_call = make_call_site(method_name="post", start_line=10)
    teardown_call = make_call_site(method_name="delete", start_line=20)

    sequences = _detect_multi_phase(
        phases=[
            (LifecyclePhase.TEST, [test_call]),
            (LifecyclePhase.TEARDOWN, [teardown_call]),
        ],
        http_calls=[
            (0, test_call, "POST", "/items/1"),
            (1, teardown_call, "DELETE", "/items/1"),
        ],
    )

    assert len(sequences) == 1
    assert len(sequences[0].steps) == 2
    assert sequences[0].steps[0].http_method == "POST"
    assert sequences[0].steps[0].phase == LifecyclePhase.TEST
    assert sequences[0].steps[1].http_method == "DELETE"
    assert sequences[0].steps[1].phase == LifecyclePhase.TEARDOWN


def test_template_path_cleanup_delete_in_teardown_is_detected() -> None:
    setup_call = make_call_site(method_name="post", start_line=5)
    test_call = make_call_site(method_name="get", start_line=15)
    teardown_call = make_call_site(method_name="delete", start_line=25)

    sequences = _detect_multi_phase(
        phases=[
            (LifecyclePhase.SETUP, [setup_call]),
            (LifecyclePhase.TEST, [test_call]),
            (LifecyclePhase.TEARDOWN, [teardown_call]),
        ],
        http_calls=[
            (0, setup_call, "POST", "/products/{name}"),
            (1, test_call, "GET", "/products/{name}"),
            (2, teardown_call, "DELETE", "/products/{name}"),
        ],
    )

    assert len(sequences) == 1
    seq = sequences[0]
    assert seq.resource_key == "/products"
    assert seq.lifecycle_label == CrudLifecycleLabel.CREATE_VERIFY_CLEANUP
    assert seq.has_cleanup_delete is True
    assert seq.steps[2].phase == LifecyclePhase.TEARDOWN


def test_builder_role_events_are_excluded() -> None:
    builder_call = make_call_site(method_name="given", start_line=10)
    event_call = make_call_site(method_name="post", start_line=20)
    runtime_view = _test_runtime_view([builder_call, event_call])
    grouping = runtime_view.entries[0].grouping

    builder_node = grouping.node_for_call_site(builder_call)
    assert builder_node is not None
    annotate_node_http(
        builder_node,
        http_method="POST",
        path="/users",
        framework="rest-assured",
        request_role=HttpRequestRole.BUILDER,
    )
    event_node = grouping.node_for_call_site(event_call)
    assert event_node is not None
    annotate_node_http(
        event_node,
        http_method="POST",
        path="/users",
        framework="rest-assured",
        request_role=HttpRequestRole.EVENT,
    )

    sequences = detect_resource_interaction_sequences(runtime_view=runtime_view)
    assert len(sequences) == 1
    assert len(sequences[0].steps) == 1
    assert sequences[0].steps[0].event_order == 2


def test_sequences_sorted_by_first_event_order() -> None:
    get_orders = make_call_site(method_name="get", start_line=10)
    post_users = make_call_site(method_name="post", start_line=20)
    sequences = _detect(
        call_sites=[get_orders, post_users],
        http_calls=[
            (get_orders, "GET", "/orders/1"),
            (post_users, "POST", "/users/1"),
        ],
    )

    assert len(sequences) == 2
    assert sequences[0].resource_key == "/orders"
    assert sequences[1].resource_key == "/users"
