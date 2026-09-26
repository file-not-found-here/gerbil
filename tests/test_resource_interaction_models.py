from __future__ import annotations

import pytest
from pydantic import ValidationError

from gerbil.analysis import schema as model_package
from gerbil.analysis.schema import (
    ApiSequenceStep,
    AssertionAnalysis,
    AssertionRole,
    CallSiteOriginKind,
    CrudLifecycleLabel,
    CrudOperation,
    HttpCallSite,
    HttpDispatchFramework,
    HttpAnalysis,
    HttpInteraction,
    HttpInteractionKind,
    HttpRequestInteraction,
    HttpRequestRole,
    HttpTestSequence,
    HttpVerificationInteraction,
    LifecyclePhase,
    MethodIdentity,
    OriginContext,
    ProductionResourceCrudEntry,
    ProductionResourceCrudSummary,
    ResourceInteractionSequence,
    ResourceInteractionStep,
    ResourceCrudTestReference,
    SequenceStepKind,
    SourceSpan,
    TestMethodReference as MethodReferenceModel,
    TestMethodAnalysis as MethodAnalysisModel,
)
from gerbil.analysis.schema import types as model_definitions


def _make_step(
    http_method: str = "POST",
    path: str = "/users/1",
    normalized_path: str = "/users/{id}",
    event_order: int = 1,
    phase: LifecyclePhase = LifecyclePhase.TEST,
    crud_operation: CrudOperation | None = CrudOperation.CREATE,
) -> ResourceInteractionStep:
    return ResourceInteractionStep(
        http_method=http_method,
        path=path,
        normalized_path=normalized_path,
        event_order=event_order,
        phase=phase,
        crud_operation=crud_operation,
    )


def _make_sequence() -> ResourceInteractionSequence:
    return ResourceInteractionSequence(
        resource_key="/users",
        steps=[
            _make_step("POST", "/users/1", "/users/{id}", 1),
            _make_step(
                "GET",
                "/users/2",
                "/users/{id}",
                2,
                crud_operation=CrudOperation.READ,
            ),
        ],
        exercised_operations=[CrudOperation.CREATE, CrudOperation.READ],
        lifecycle_label=CrudLifecycleLabel.CREATE_VERIFY,
        has_read_after_write=True,
        has_cleanup_delete=False,
    )


def _make_api_sequence_step() -> ApiSequenceStep:
    return ApiSequenceStep(
        order=1,
        kind=SequenceStepKind.HTTP_REQUEST,
        phase=LifecyclePhase.TEST,
        origin=OriginContext(
            phase=LifecyclePhase.TEST,
            kind=CallSiteOriginKind.TEST_METHOD,
        ),
        method_name="get",
        source_span=SourceSpan(start_line=1, start_column=1, end_line=1, end_column=10),
        http_method="GET",
        http_path="/users/1",
    )


def _make_origin() -> OriginContext:
    return OriginContext(
        phase=LifecyclePhase.TEST,
        kind=CallSiteOriginKind.TEST_METHOD,
    )


def _make_source_span() -> SourceSpan:
    return SourceSpan(start_line=1, start_column=1, end_line=1, end_column=10)


@pytest.mark.parametrize(
    "missing_field",
    ["http_method", "path", "normalized_path", "event_order", "phase"],
)
def test_resource_interaction_step_requires_all_fields(missing_field: str) -> None:
    payload: dict[str, object] = {
        "http_method": "POST",
        "path": "/users/1",
        "normalized_path": "/users/{id}",
        "event_order": 1,
        "phase": "test",
    }
    payload.pop(missing_field)
    with pytest.raises(ValidationError) as exc_info:
        ResourceInteractionStep.model_validate(payload)
    assert any(
        error["type"] == "missing" and error["loc"] == (missing_field,)
        for error in exc_info.value.errors()
    )


def test_resource_interaction_sequence_requires_resource_key() -> None:
    payload: dict[str, object] = {"steps": []}
    with pytest.raises(ValidationError) as exc_info:
        ResourceInteractionSequence.model_validate(payload)
    assert any(
        error["type"] == "missing" and error["loc"] == ("resource_key",)
        for error in exc_info.value.errors()
    )


def test_resource_interaction_sequence_defaults_steps_to_empty() -> None:
    seq = ResourceInteractionSequence(resource_key="/users")
    assert seq.steps == []
    assert seq.exercised_operations == []
    assert seq.available_operations == []
    assert seq.missing_available_operations == []
    assert seq.lifecycle_label == CrudLifecycleLabel.OTHER
    assert seq.has_read_after_write is False
    assert seq.has_cleanup_delete is False


def test_resource_interaction_step_serialization_round_trip() -> None:
    step = _make_step()
    dumped = step.model_dump(mode="json")
    assert dumped["http_method"] == "POST"
    assert dumped["crud_operation"] == "create"
    assert dumped["event_order"] == 1
    assert dumped["phase"] == "test"

    round_tripped = ResourceInteractionStep.model_validate(dumped)
    assert round_tripped == step


def test_resource_interaction_sequence_serialization_round_trip() -> None:
    seq = _make_sequence()
    dumped = seq.model_dump(mode="json")
    assert dumped["resource_key"] == "/users"
    assert len(dumped["steps"]) == 2
    assert dumped["exercised_operations"] == ["create", "read"]
    assert dumped["lifecycle_label"] == "create-verify"
    assert dumped["has_read_after_write"] is True
    assert dumped["has_cleanup_delete"] is False

    round_tripped_from_dict = ResourceInteractionSequence.model_validate(dumped)
    round_tripped_from_json = ResourceInteractionSequence.model_validate_json(
        seq.model_dump_json()
    )
    assert round_tripped_from_dict == seq
    assert round_tripped_from_json == seq


def test_http_analysis_resource_sequences_default_is_instance_safe() -> None:
    first = HttpAnalysis()
    second = HttpAnalysis()

    assert first.resource_interaction_sequences == []
    assert second.resource_interaction_sequences == []

    first.resource_interaction_sequences.append(_make_sequence())

    assert len(first.resource_interaction_sequences) == 1
    assert second.resource_interaction_sequences == []


def test_http_analysis_resource_sequences_accept_typed_entries() -> None:
    seq = _make_sequence()
    http = HttpAnalysis(resource_interaction_sequences=[seq])

    assert http.resource_interaction_sequences == [seq]
    dumped = http.model_dump(mode="json")
    assert dumped["resource_interaction_sequences"][0]["resource_key"] == "/users"
    assert dumped["resource_interaction_sequences"][0]["lifecycle_label"] == (
        "create-verify"
    )


def test_http_test_sequence_populates_length_from_steps() -> None:
    step = _make_api_sequence_step()
    sequence = HttpTestSequence(
        order=1,
        steps=[step],
        length=99,
        fingerprint="test-sequence",
    )

    assert sequence.length == 1
    assert sequence.model_dump(mode="json")["length"] == 1


def test_http_analysis_test_sequences_default_is_instance_safe() -> None:
    first = HttpAnalysis()
    second = HttpAnalysis()

    assert first.test_sequences == []
    assert second.test_sequences == []
    assert first.sequence_summary is not second.sequence_summary

    first.test_sequences.append(
        HttpTestSequence(
            order=1,
            steps=[_make_api_sequence_step()],
            fingerprint="test-sequence",
        )
    )
    first.sequence_summary.sequence_count = 1

    assert len(first.test_sequences) == 1
    assert second.test_sequences == []
    assert second.sequence_summary.sequence_count == 0


def test_http_analysis_http_interactions_default_is_instance_safe() -> None:
    first = HttpAnalysis()
    second = HttpAnalysis()

    assert first.http_interactions == []
    assert second.http_interactions == []

    request_interaction = HttpRequestInteraction(
        origin=_make_origin(),
        http_call=HttpCallSite(
            http_method="GET",
            path="/users/1",
            framework=HttpDispatchFramework.MOCKMVC,
            request_role=HttpRequestRole.EVENT,
            method_name="perform",
        ),
    )
    first.http_interactions.append(
        HttpInteraction(
            kind=HttpInteractionKind.REQUEST,
            origin=request_interaction.origin,
            source_span=_make_source_span(),
            request_interaction=request_interaction,
        )
    )

    assert len(first.http_interactions) == 1
    assert second.http_interactions == []


def test_http_interaction_requires_payload_matching_kind() -> None:
    request_interaction = HttpRequestInteraction(
        origin=_make_origin(),
        http_call=HttpCallSite(
            http_method="GET",
            path="/users/1",
            framework=HttpDispatchFramework.MOCKMVC,
            request_role=HttpRequestRole.EVENT,
            method_name="perform",
        ),
    )
    verification_interaction = HttpVerificationInteraction(
        origin=_make_origin(),
        assertion_role=AssertionRole.STATUS,
        method_name="isOk",
        source_span=_make_source_span(),
        status_code=200,
        status_range="2xx",
    )

    assert (
        HttpInteraction(
            kind=HttpInteractionKind.REQUEST,
            origin=_make_origin(),
            source_span=_make_source_span(),
            request_interaction=request_interaction,
        ).request_interaction
        == request_interaction
    )
    assert (
        HttpInteraction(
            kind=HttpInteractionKind.VERIFICATION,
            origin=_make_origin(),
            source_span=_make_source_span(),
            verification_interaction=verification_interaction,
        ).verification_interaction
        == verification_interaction
    )

    with pytest.raises(ValidationError):
        HttpInteraction(
            kind=HttpInteractionKind.REQUEST,
            origin=_make_origin(),
            source_span=_make_source_span(),
            verification_interaction=verification_interaction,
        )


def test_production_resource_crud_summary_serialization_round_trip() -> None:
    test_ref = ResourceCrudTestReference(
        test_method=MethodReferenceModel(
            qualified_class_name="example.UserResourceTest",
            method_signature="createsAndReads()",
        ),
        resource_key="/users",
        lifecycle_label=CrudLifecycleLabel.CREATE_VERIFY,
    )
    entry = ProductionResourceCrudEntry(
        resource_key="/users",
        available_operations=[CrudOperation.CREATE, CrudOperation.READ],
        exercised_operations=[CrudOperation.CREATE, CrudOperation.READ],
        missing_available_operations=[],
        exercising_test_resources_by_operation={
            CrudOperation.CREATE: [test_ref],
            CrudOperation.READ: [test_ref],
        },
        full_crud_test_count=0,
        read_only_test_count=0,
    )
    summary = ProductionResourceCrudSummary(
        total_resource_count=1,
        resources_with_any_test_count=1,
        resources_with_full_crud_test_count=0,
        resources=[entry],
    )

    dumped = summary.model_dump(mode="json")

    assert dumped["resources"][0]["resource_key"] == "/users"
    assert dumped["resources"][0]["available_operations"] == ["create", "read"]
    assert dumped["resources"][0]["missing_available_operations"] == []
    assert dumped["resources"][0]["exercising_test_resources_by_operation"] == {
        "create": [
            {
                "test_method": {
                    "qualified_class_name": "example.UserResourceTest",
                    "method_signature": "createsAndReads()",
                },
                "resource_key": "/users",
                "lifecycle_label": "create-verify",
            }
        ],
        "read": [
            {
                "test_method": {
                    "qualified_class_name": "example.UserResourceTest",
                    "method_signature": "createsAndReads()",
                },
                "resource_key": "/users",
                "lifecycle_label": "create-verify",
            }
        ],
    }

    assert ProductionResourceCrudSummary.model_validate(dumped) == summary


def test_production_resource_crud_models_use_concrete_empty_defaults() -> None:
    entry = ProductionResourceCrudEntry(resource_key="/users")
    summary = ProductionResourceCrudSummary()

    assert entry.endpoints == []
    assert entry.available_operations == []
    assert entry.exercised_operations == []
    assert entry.missing_available_operations == []
    assert entry.exercising_test_resources_by_operation == {}
    assert entry.full_crud_test_count == 0
    assert entry.read_only_test_count == 0

    assert summary.total_resource_count == 0
    assert summary.resources_with_any_test_count == 0
    assert summary.resources_with_full_crud_test_count == 0
    assert summary.resources == []

    dumped = ProductionResourceCrudSummary(resources=[entry]).model_dump(mode="json")
    assert dumped["total_resource_count"] == 0
    assert dumped["resources"][0]["available_operations"] == []
    assert dumped["resources"][0]["exercising_test_resources_by_operation"] == {}


def test_assertion_analysis_oracle_type_default_is_instance_safe() -> None:
    first = AssertionAnalysis()
    second = AssertionAnalysis()

    assert first.oracle_type.label == second.oracle_type.label == "implicit"
    assert first.oracle_type is not second.oracle_type
    assert first.response_surface_labels == second.response_surface_labels == []
    assert first.status_code_counts == second.status_code_counts == {}
    assert first.status_range_counts == second.status_range_counts
    assert first.response_surface_labels is not second.response_surface_labels
    assert first.status_code_counts is not second.status_code_counts
    assert first.status_range_counts is not second.status_range_counts


def test_resource_interaction_fields_integrate_with_test_method_analysis_defaults() -> (
    None
):
    analysis = MethodAnalysisModel(
        identity=MethodIdentity(
            defining_class_name="example.TestClass",
            method_signature="testExample()",
            method_declaration="void testExample()",
        )
    )

    assert analysis.http.resource_interaction_sequences == []
    assert analysis.assertions.oracle_type.label == "implicit"


def test_test_method_analysis_rejects_removed_top_level_groups() -> None:
    payload = {
        "identity": {
            "defining_class_name": "example.TestClass",
            "method_signature": "testExample()",
            "method_declaration": "void testExample()",
        },
        "evidence": {},
        "classification": {},
        "precondition_summary": {},
    }

    with pytest.raises(ValidationError) as exc_info:
        MethodAnalysisModel.model_validate(payload)

    error_locations = {error["loc"] for error in exc_info.value.errors()}
    assert ("evidence",) in error_locations
    assert ("classification",) in error_locations
    assert ("precondition_summary",) in error_locations


def test_model_package_exports_new_resource_interaction_types() -> None:
    expected_exports = {
        "CrudLifecycleLabel",
        "CrudOperation",
        "HttpInteraction",
        "HttpInteractionKind",
        "ProductionResourceCrudEntry",
        "ProductionResourceCrudSummary",
        "ResourceCrudTestReference",
        "ResourceInteractionStep",
        "ResourceInteractionSequence",
    }
    assert expected_exports.issubset(set(model_package.__all__))

    assert model_package.CrudOperation is model_definitions.CrudOperation
    assert model_package.CrudLifecycleLabel is model_definitions.CrudLifecycleLabel
    assert (
        model_package.ResourceCrudTestReference
        is model_definitions.ResourceCrudTestReference
    )
    assert (
        model_package.ProductionResourceCrudEntry
        is model_definitions.ProductionResourceCrudEntry
    )
    assert (
        model_package.ProductionResourceCrudSummary
        is model_definitions.ProductionResourceCrudSummary
    )
    assert (
        model_package.ResourceInteractionStep
        is model_definitions.ResourceInteractionStep
    )
    assert (
        model_package.ResourceInteractionSequence
        is model_definitions.ResourceInteractionSequence
    )


def test_old_state_interaction_types_are_removed_from_exports() -> None:
    assert "StateInteractionPattern" not in set(model_package.__all__)
    assert "ObservationMedium" not in set(model_package.__all__)
    assert "VerificationIntent" not in set(model_package.__all__)


def test_old_method_analysis_groups_are_removed_from_exports() -> None:
    assert "ApiEvidence" not in set(model_package.__all__)
    assert "ClassificationLabels" not in set(model_package.__all__)
