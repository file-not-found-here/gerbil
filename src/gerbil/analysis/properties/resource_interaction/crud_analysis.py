from __future__ import annotations

from dataclasses import dataclass, field

from gerbil.analysis.properties.resource_interaction.path_normalization import (
    normalize_production_resource_key,
    strip_application_path_prefix,
)
from gerbil.analysis.schema import (
    ApplicationEndpoint,
    CrudLifecycleLabel,
    CrudOperation,
    ProductionResourceCrudEntry,
    ProductionResourceCrudSummary,
    ResourceCrudTestReference,
    ResourceInteractionSequence,
    ResourceInteractionStep,
    TestClassAnalysis,
    TestMethodReference,
)

CRUD_OPERATION_ORDER: tuple[CrudOperation, ...] = (
    CrudOperation.CREATE,
    CrudOperation.READ,
    CrudOperation.UPDATE,
    CrudOperation.DELETE,
)

_HTTP_METHOD_CRUD_OPERATION: dict[str, CrudOperation] = {
    "POST": CrudOperation.CREATE,
    "GET": CrudOperation.READ,
    "HEAD": CrudOperation.READ,
    "PUT": CrudOperation.UPDATE,
    "PATCH": CrudOperation.UPDATE,
    "DELETE": CrudOperation.DELETE,
}
_WRITE_OPERATIONS: frozenset[CrudOperation] = frozenset(
    {CrudOperation.CREATE, CrudOperation.UPDATE, CrudOperation.DELETE}
)


@dataclass
class _ProductionResourceAvailability:
    endpoints: list[ApplicationEndpoint] = field(default_factory=list)
    operations: set[CrudOperation] = field(default_factory=set)


def crud_operation_for_http_method(http_method: str | None) -> CrudOperation | None:
    return _HTTP_METHOD_CRUD_OPERATION.get((http_method or "").upper())


def _ordered_operations(operations: set[CrudOperation]) -> list[CrudOperation]:
    return [operation for operation in CRUD_OPERATION_ORDER if operation in operations]


def _step_operations(steps: list[ResourceInteractionStep]) -> list[CrudOperation]:
    operations: list[CrudOperation] = []
    for step in steps:
        operation = crud_operation_for_http_method(step.http_method)
        step.crud_operation = operation
        if operation is not None:
            operations.append(operation)
    return operations


def _has_ordered_pair(
    operations: list[CrudOperation],
    first: CrudOperation,
    second: CrudOperation,
) -> bool:
    has_first = False
    for operation in operations:
        if operation == first:
            has_first = True
        elif has_first and operation == second:
            return True
    return False


def _has_ordered_triple(
    operations: list[CrudOperation],
    first: CrudOperation,
    second: CrudOperation,
    third: CrudOperation,
) -> bool:
    stage = 0
    for operation in operations:
        if stage == 0 and operation == first:
            stage = 1
        elif stage == 1 and operation == second:
            stage = 2
        elif stage == 2 and operation == third:
            return True
    return False


def has_read_after_write(operations: list[CrudOperation]) -> bool:
    saw_write = False
    for operation in operations:
        if operation in _WRITE_OPERATIONS:
            saw_write = True
        elif saw_write and operation == CrudOperation.READ:
            return True
    return False


def has_cleanup_delete(operations: list[CrudOperation]) -> bool:
    return _has_ordered_pair(operations, CrudOperation.CREATE, CrudOperation.DELETE)


def _resource_supports_full_crud(
    available_operations: set[CrudOperation] | None,
) -> bool:
    # ``None`` means availability is not yet known (detection runs before
    # production endpoints are joined); the exercised shape already proves all
    # four operations are reachable, so treat it as full-CRUD capable.
    if available_operations is None:
        return True
    return set(CRUD_OPERATION_ORDER) <= available_operations


def _resource_supports_writes(
    available_operations: set[CrudOperation] | None,
) -> bool:
    if available_operations is None:
        return True
    return bool(available_operations & _WRITE_OPERATIONS)


def classify_crud_lifecycle(
    operations: list[CrudOperation],
    *,
    available_operations: set[CrudOperation] | None = None,
) -> CrudLifecycleLabel:
    operation_set = set(operations)
    if not operation_set:
        return CrudLifecycleLabel.OTHER
    if operation_set == set(CRUD_OPERATION_ORDER) and _resource_supports_full_crud(
        available_operations
    ):
        return CrudLifecycleLabel.FULL_CRUD
    if operation_set <= {CrudOperation.READ} and _resource_supports_writes(
        available_operations
    ):
        return CrudLifecycleLabel.READ_ONLY
    if operation_set == {CrudOperation.CREATE}:
        return CrudLifecycleLabel.CREATE_AND_TRUST
    if operation_set == {
        CrudOperation.CREATE,
        CrudOperation.READ,
        CrudOperation.UPDATE,
    } and _has_ordered_triple(
        operations,
        CrudOperation.CREATE,
        CrudOperation.UPDATE,
        CrudOperation.READ,
    ):
        return CrudLifecycleLabel.CREATE_UPDATE_VERIFY
    if operation_set == {
        CrudOperation.CREATE,
        CrudOperation.READ,
        CrudOperation.DELETE,
    } and _has_ordered_triple(
        operations,
        CrudOperation.CREATE,
        CrudOperation.READ,
        CrudOperation.DELETE,
    ):
        return CrudLifecycleLabel.CREATE_VERIFY_CLEANUP
    if operation_set == {
        CrudOperation.CREATE,
        CrudOperation.READ,
    } and _has_ordered_pair(operations, CrudOperation.CREATE, CrudOperation.READ):
        return CrudLifecycleLabel.CREATE_VERIFY
    if operation_set == {
        CrudOperation.DELETE,
        CrudOperation.READ,
    } and _has_ordered_pair(operations, CrudOperation.DELETE, CrudOperation.READ):
        return CrudLifecycleLabel.DELETE_VERIFY
    if operation_set & _WRITE_OPERATIONS and CrudOperation.READ not in operation_set:
        return CrudLifecycleLabel.WRITE_ONLY
    return CrudLifecycleLabel.OTHER


def enrich_resource_interaction_sequence(
    sequence: ResourceInteractionSequence,
    *,
    available_operations: set[CrudOperation] | None = None,
) -> ResourceInteractionSequence:
    ordered_step_operations = _step_operations(sequence.steps)
    exercised_operations = set(ordered_step_operations)
    sequence.exercised_operations = _ordered_operations(exercised_operations)
    sequence.lifecycle_label = classify_crud_lifecycle(
        ordered_step_operations,
        available_operations=available_operations,
    )
    sequence.has_read_after_write = has_read_after_write(ordered_step_operations)
    sequence.has_cleanup_delete = has_cleanup_delete(ordered_step_operations)

    if available_operations is not None:
        sequence.available_operations = _ordered_operations(available_operations)
        sequence.missing_available_operations = _ordered_operations(
            available_operations - exercised_operations
        )

    return sequence


def _build_resource_availability(
    application_endpoints: list[ApplicationEndpoint],
) -> dict[str, _ProductionResourceAvailability]:
    availability_by_resource_key: dict[str, _ProductionResourceAvailability] = {}
    for endpoint in application_endpoints:
        resource_key = normalize_production_resource_key(endpoint.path_template)
        if resource_key is None:
            continue

        availability = availability_by_resource_key.setdefault(
            resource_key,
            _ProductionResourceAvailability(),
        )
        availability.endpoints.append(endpoint)
        operation = crud_operation_for_http_method(endpoint.http_method)
        if operation is not None and not endpoint.is_method_wildcard:
            availability.operations.add(operation)
    return availability_by_resource_key


def _sorted_endpoints(
    endpoints: list[ApplicationEndpoint],
) -> list[ApplicationEndpoint]:
    return sorted(
        endpoints,
        key=lambda endpoint: (
            endpoint.path_template,
            endpoint.http_method,
            endpoint.declaring_class_name,
            endpoint.declaring_method_signature or "",
        ),
    )


def _build_test_reference(
    *,
    test_class_analysis: TestClassAnalysis,
    test_method_index: int,
    sequence: ResourceInteractionSequence,
) -> ResourceCrudTestReference:
    test_method_analysis = test_class_analysis.test_method_analyses[test_method_index]
    return ResourceCrudTestReference(
        test_method=TestMethodReference(
            qualified_class_name=test_method_analysis.identity.defining_class_name,
            method_signature=test_method_analysis.identity.method_signature,
        ),
        resource_key=sequence.resource_key,
        lifecycle_label=sequence.lifecycle_label,
    )


def _resolve_production_resource_key(
    sequence_key: str,
    availability_by_resource_key: dict[str, _ProductionResourceAvailability],
    application_path_prefixes: tuple[str, ...],
) -> str | None:
    """Map an observed sequence key onto a production resource key.

    A direct hit wins unconditionally. Otherwise each discovered
    @ApplicationPath prefix is stripped segment-wise and the remainder is
    accepted only when it lands on exactly one production resource; zero or
    ambiguous strips stay unmatched to avoid cross-application false positives.
    """
    if sequence_key in availability_by_resource_key:
        return sequence_key
    stripped_keys = {
        stripped_key
        for prefix in application_path_prefixes
        if (stripped_key := strip_application_path_prefix(sequence_key, prefix))
        is not None
        and stripped_key in availability_by_resource_key
    }
    if len(stripped_keys) == 1:
        return next(iter(stripped_keys))
    return None


def _iter_resource_sequences(
    test_class_analyses: list[TestClassAnalysis],
) -> list[tuple[TestClassAnalysis, int, ResourceInteractionSequence]]:
    sequences: list[tuple[TestClassAnalysis, int, ResourceInteractionSequence]] = []
    for test_class_analysis in test_class_analyses:
        for test_method_index, test_method_analysis in enumerate(
            test_class_analysis.test_method_analyses
        ):
            for sequence in test_method_analysis.http.resource_interaction_sequences:
                sequences.append((test_class_analysis, test_method_index, sequence))
    return sequences


def build_resource_crud_analysis(
    *,
    application_endpoints: list[ApplicationEndpoint],
    test_class_analyses: list[TestClassAnalysis],
    application_path_prefixes: tuple[str, ...] = (),
) -> ProductionResourceCrudSummary:
    availability_by_resource_key = _build_resource_availability(application_endpoints)
    sequence_entries = _iter_resource_sequences(test_class_analyses)

    production_key_by_sequence_key = {
        sequence.resource_key: _resolve_production_resource_key(
            sequence.resource_key,
            availability_by_resource_key,
            application_path_prefixes,
        )
        for _, _, sequence in sequence_entries
    }

    for _test_class_analysis, _test_method_index, sequence in sequence_entries:
        production_key = production_key_by_sequence_key[sequence.resource_key]
        availability = (
            availability_by_resource_key[production_key]
            if production_key is not None
            else None
        )
        enrich_resource_interaction_sequence(
            sequence,
            available_operations=availability.operations if availability else None,
        )

    resources: list[ProductionResourceCrudEntry] = []
    for resource_key in sorted(availability_by_resource_key):
        availability = availability_by_resource_key[resource_key]
        exercising_refs_by_operation: dict[
            CrudOperation, dict[tuple[str, str, str], ResourceCrudTestReference]
        ] = {}
        full_crud_tests: set[tuple[str, str]] = set()
        read_only_tests: set[tuple[str, str]] = set()

        for test_class_analysis, test_method_index, sequence in sequence_entries:
            if production_key_by_sequence_key[sequence.resource_key] != resource_key:
                continue

            test_method_analysis = test_class_analysis.test_method_analyses[
                test_method_index
            ]
            test_key = (
                test_method_analysis.identity.defining_class_name,
                test_method_analysis.identity.method_signature,
            )
            if sequence.lifecycle_label == CrudLifecycleLabel.FULL_CRUD:
                full_crud_tests.add(test_key)
            elif sequence.lifecycle_label == CrudLifecycleLabel.READ_ONLY:
                read_only_tests.add(test_key)

            test_ref = _build_test_reference(
                test_class_analysis=test_class_analysis,
                test_method_index=test_method_index,
                sequence=sequence,
            )
            for operation in sequence.exercised_operations:
                operation_refs = exercising_refs_by_operation.setdefault(
                    operation,
                    {},
                )
                operation_refs[(test_key[0], test_key[1], sequence.resource_key)] = (
                    test_ref
                )

        exercised_operation_set = set(exercising_refs_by_operation)
        exercising_test_resources_by_operation = {
            operation: [
                refs[key]
                for key in sorted(
                    refs,
                    key=lambda ref_key: (ref_key[0], ref_key[1], ref_key[2]),
                )
            ]
            for operation, refs in sorted(
                exercising_refs_by_operation.items(),
                key=lambda item: CRUD_OPERATION_ORDER.index(item[0]),
            )
        }
        resources.append(
            ProductionResourceCrudEntry(
                resource_key=resource_key,
                endpoints=_sorted_endpoints(availability.endpoints),
                available_operations=_ordered_operations(availability.operations),
                exercised_operations=_ordered_operations(exercised_operation_set),
                missing_available_operations=_ordered_operations(
                    availability.operations - exercised_operation_set
                ),
                exercising_test_resources_by_operation=(
                    exercising_test_resources_by_operation
                ),
                full_crud_test_count=len(full_crud_tests),
                read_only_test_count=len(read_only_tests),
            )
        )

    return ProductionResourceCrudSummary(
        total_resource_count=len(resources),
        resources_with_any_test_count=sum(
            1 for resource in resources if resource.exercised_operations
        ),
        resources_with_full_crud_test_count=sum(
            1 for resource in resources if resource.full_crud_test_count > 0
        ),
        resources=resources,
    )


__all__ = [
    "CRUD_OPERATION_ORDER",
    "build_resource_crud_analysis",
    "classify_crud_lifecycle",
    "crud_operation_for_http_method",
    "enrich_resource_interaction_sequence",
    "has_cleanup_delete",
    "has_read_after_write",
]
