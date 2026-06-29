from __future__ import annotations

import pytest

from gerbil.analysis.properties.resource_interaction.crud_analysis import (
    build_resource_crud_analysis,
    classify_crud_lifecycle,
)
from gerbil.analysis.schema import (
    ApplicationEndpoint,
    CrudLifecycleLabel,
    CrudOperation,
    HttpAnalysis,
    LifecyclePhase,
    MethodIdentity,
    ResourceInteractionSequence,
    ResourceInteractionStep,
    TestClassAnalysis as ClassAnalysisModel,
    TestMethodAnalysis as MethodAnalysisModel,
)


def _endpoint(method: str, path: str) -> ApplicationEndpoint:
    return ApplicationEndpoint(
        http_method=method,
        path_template=path,
        framework="spring",
        declaring_class_name="example.UserController",
        declaring_method_signature=f"{method.lower()}User()",
    )


def _test_method(
    method_name: str,
    resource_key: str,
    methods: list[str],
) -> MethodAnalysisModel:
    steps = [
        ResourceInteractionStep(
            http_method=method,
            path=f"{resource_key}/1",
            normalized_path=f"{resource_key}/{{id}}",
            event_order=index + 1,
            phase=LifecyclePhase.TEST,
        )
        for index, method in enumerate(methods)
    ]
    return MethodAnalysisModel(
        identity=MethodIdentity(
            defining_class_name="example.UserResourceTest",
            method_signature=f"{method_name}()",
            method_declaration=f"void {method_name}()",
        ),
        is_api_test=True,
        http=HttpAnalysis(
            resource_interaction_sequences=[
                ResourceInteractionSequence(resource_key=resource_key, steps=steps)
            ]
        ),
    )


def _test_class(*methods: MethodAnalysisModel) -> ClassAnalysisModel:
    return ClassAnalysisModel(
        qualified_class_name="example.UserResourceTest",
        test_method_analyses=list(methods),
    )


def test_build_resource_crud_analysis_enriches_sequences_with_availability() -> None:
    create_verify = _test_method("createsAndReads", "/users", ["POST", "GET"])
    read_only = _test_method("readsOnly", "/users", ["GET", "HEAD"])
    endpoints = [
        _endpoint("GET", "/users/{userId}"),
        _endpoint("POST", "/users"),
        _endpoint("DELETE", "/users/{userId}"),
    ]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(create_verify, read_only)],
    )

    resource_entry = summary.resources[0]
    assert resource_entry.resource_key == "/users"
    assert resource_entry.available_operations == [
        CrudOperation.CREATE,
        CrudOperation.READ,
        CrudOperation.DELETE,
    ]
    assert resource_entry.exercised_operations == [
        CrudOperation.CREATE,
        CrudOperation.READ,
    ]
    assert resource_entry.missing_available_operations == [CrudOperation.DELETE]
    assert resource_entry.read_only_test_count == 1
    assert resource_entry.full_crud_test_count == 0

    sequence = create_verify.http.resource_interaction_sequences[0]
    assert sequence.exercised_operations == [CrudOperation.CREATE, CrudOperation.READ]
    assert sequence.available_operations == [
        CrudOperation.CREATE,
        CrudOperation.READ,
        CrudOperation.DELETE,
    ]
    assert sequence.missing_available_operations == [CrudOperation.DELETE]
    assert sequence.lifecycle_label == CrudLifecycleLabel.CREATE_VERIFY
    assert sequence.has_read_after_write is True
    assert sequence.has_cleanup_delete is False


def test_build_resource_crud_analysis_uses_production_availability_not_observed_ops() -> (
    None
):
    read_delete_test = _test_method("deleteThenVerify", "/accounts", ["DELETE", "GET"])
    endpoints = [
        _endpoint("GET", "/accounts/{accountId}"),
        _endpoint("DELETE", "/accounts/{accountId}"),
    ]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(read_delete_test)],
    )

    resource_entry = summary.resources[0]
    assert resource_entry.available_operations == [
        CrudOperation.READ,
        CrudOperation.DELETE,
    ]
    assert resource_entry.exercised_operations == [
        CrudOperation.READ,
        CrudOperation.DELETE,
    ]
    assert resource_entry.missing_available_operations == []
    sequence = read_delete_test.http.resource_interaction_sequences[0]
    assert sequence.lifecycle_label == CrudLifecycleLabel.DELETE_VERIFY
    assert sequence.available_operations == [CrudOperation.READ, CrudOperation.DELETE]
    assert sequence.missing_available_operations == []


def test_build_resource_crud_analysis_skips_unknown_wildcard_methods_for_availability() -> (
    None
):
    read_test = _test_method("readsOnly", "/files", ["GET"])
    wildcard_endpoint = ApplicationEndpoint(
        http_method="UNKNOWN",
        is_method_wildcard=True,
        path_template="/files/{fileId}",
        framework="spring",
        declaring_class_name="example.FileController",
    )
    endpoints = [
        wildcard_endpoint,
        _endpoint("GET", "/files/{fileId}"),
    ]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(read_test)],
    )

    assert summary.resources[0].available_operations == [CrudOperation.READ]
    assert summary.resources[0].endpoints is not None
    assert wildcard_endpoint in summary.resources[0].endpoints
    assert read_test.http.resource_interaction_sequences[0].available_operations == [
        CrudOperation.READ
    ]


def test_build_resource_crud_analysis_keeps_unknown_only_resource_inventory() -> None:
    summary = build_resource_crud_analysis(
        application_endpoints=[
            ApplicationEndpoint(
                http_method="UNKNOWN",
                is_method_wildcard=True,
                path_template="/reports/{reportId}",
                framework="spring",
                declaring_class_name="example.ReportController",
            )
        ],
        test_class_analyses=[],
    )

    assert summary.total_resource_count == 1
    assert summary.resources[0].resource_key == "/reports"
    assert summary.resources[0].available_operations == []
    assert summary.resources[0].exercised_operations == []
    assert summary.resources[0].missing_available_operations == []


def test_build_resource_crud_analysis_orders_resources_endpoints_and_test_refs() -> (
    None
):
    z_read = _test_method("zReads", "/accounts", ["GET"])
    a_read = _test_method("aReads", "/accounts", ["GET"])

    summary = build_resource_crud_analysis(
        application_endpoints=[
            _endpoint("POST", "/users"),
            _endpoint("GET", "/accounts/{accountId}"),
            _endpoint("DELETE", "/accounts/{accountId}"),
        ],
        test_class_analyses=[_test_class(z_read, a_read)],
    )

    assert [resource.resource_key for resource in summary.resources] == [
        "/accounts",
        "/users",
    ]
    assert summary.resources[0].endpoints is not None
    assert [endpoint.http_method for endpoint in summary.resources[0].endpoints] == [
        "DELETE",
        "GET",
    ]
    read_refs = summary.resources[0].exercising_test_resources_by_operation[
        CrudOperation.READ
    ]
    assert [ref.test_method.method_signature for ref in read_refs] == [
        "aReads()",
        "zReads()",
    ]


# @ApplicationPath-mounted request paths join to unmounted production resources


def test_application_path_prefixed_sequence_exercises_production_resource() -> None:
    create_verify = _test_method("createsAndReads", "/rest/quotes", ["POST", "GET"])
    endpoints = [
        _endpoint("GET", "/quotes/{symbol}"),
        _endpoint("POST", "/quotes"),
    ]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(create_verify)],
        application_path_prefixes=("/rest",),
    )

    resource_entry = summary.resources[0]
    assert resource_entry.resource_key == "/quotes"
    assert resource_entry.exercised_operations == [
        CrudOperation.CREATE,
        CrudOperation.READ,
    ]
    sequence = create_verify.http.resource_interaction_sequences[0]
    assert sequence.available_operations == [CrudOperation.CREATE, CrudOperation.READ]
    assert sequence.lifecycle_label == CrudLifecycleLabel.CREATE_VERIFY
    read_refs = resource_entry.exercising_test_resources_by_operation[
        CrudOperation.READ
    ]
    assert [ref.resource_key for ref in read_refs] == ["/rest/quotes"]


def test_unprefixed_sequence_leaves_production_resource_unexercised() -> None:
    create_verify = _test_method("createsAndReads", "/rest/quotes", ["POST", "GET"])
    endpoints = [
        _endpoint("GET", "/quotes/{symbol}"),
        _endpoint("POST", "/quotes"),
    ]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(create_verify)],
    )

    resource_entry = summary.resources[0]
    assert resource_entry.resource_key == "/quotes"
    assert resource_entry.exercised_operations == []
    sequence = create_verify.http.resource_interaction_sequences[0]
    assert sequence.available_operations == []


def test_direct_production_match_suppresses_prefix_stripping() -> None:
    mounted_read = _test_method("readsMounted", "/rest/users", ["GET"])
    endpoints = [
        _endpoint("GET", "/users/{userId}"),
        _endpoint("GET", "/rest/users/{userId}"),
    ]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(mounted_read)],
        application_path_prefixes=("/rest",),
    )

    entries_by_key = {entry.resource_key: entry for entry in summary.resources}
    assert entries_by_key["/rest/users"].exercised_operations == [CrudOperation.READ]
    assert entries_by_key["/users"].exercised_operations == []


def test_prefix_strip_requires_whole_leading_segments() -> None:
    read_test = _test_method("readsItems", "/apiserver/items", ["GET"])
    endpoints = [_endpoint("GET", "/server/items/{itemId}")]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(read_test)],
        application_path_prefixes=("/api",),
    )

    assert summary.resources[0].exercised_operations == []


def test_ambiguous_multi_prefix_strip_stays_unmatched() -> None:
    # /rest/v1/items strips to /v1/items (under /rest) and /items (under
    # /rest/v1), both production resources, so the join is rejected; the
    # unambiguous /rest/v1/orders still maps to /orders.
    ambiguous_read = _test_method("readsItems", "/rest/v1/items", ["GET"])
    unambiguous_read = _test_method("readsOrders", "/rest/v1/orders", ["GET"])
    endpoints = [
        _endpoint("GET", "/v1/items/{itemId}"),
        _endpoint("GET", "/items/{itemId}"),
        _endpoint("GET", "/orders/{orderId}"),
    ]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(ambiguous_read, unambiguous_read)],
        application_path_prefixes=("/rest", "/rest/v1"),
    )

    entries_by_key = {entry.resource_key: entry for entry in summary.resources}
    assert entries_by_key["/v1/items"].exercised_operations == []
    assert entries_by_key["/items"].exercised_operations == []
    assert entries_by_key["/orders"].exercised_operations == [CrudOperation.READ]


def test_id_like_prefix_never_strips() -> None:
    # A numeric @ApplicationPath abstracts to {id} under resource-key
    # normalization, so stripping it would credit requests under ANY ID-led
    # root (here /2/quotes) to the resource mounted at /1.
    unrelated_read = _test_method("readsOtherRoot", "/{id}/quotes", ["GET"])
    endpoints = [_endpoint("GET", "/quotes/{symbol}")]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(unrelated_read)],
        application_path_prefixes=("/1",),
    )

    assert summary.resources[0].exercised_operations == []


def test_prefix_stripped_join_feeds_availability_into_lifecycle() -> None:
    # Production /catalog is read-only, so the mounted read sequence must not
    # be labeled READ_ONLY (which requires a writable resource); without the
    # joined availability it would fall back to the exercised-shape label.
    read_only = _test_method("readsCatalog", "/rest/catalog", ["GET"])
    endpoints = [_endpoint("GET", "/catalog/{itemId}")]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(read_only)],
        application_path_prefixes=("/rest",),
    )

    sequence = read_only.http.resource_interaction_sequences[0]
    assert sequence.available_operations == [CrudOperation.READ]
    assert sequence.lifecycle_label == CrudLifecycleLabel.OTHER
    assert summary.resources[0].read_only_test_count == 0


_CREATE = CrudOperation.CREATE
_READ = CrudOperation.READ
_UPDATE = CrudOperation.UPDATE
_DELETE = CrudOperation.DELETE
_ALL_CRUD = {_CREATE, _READ, _UPDATE, _DELETE}


# Read-only requires a writable resource


@pytest.mark.parametrize(
    ("exercised", "available", "expected_label"),
    [
        # Reading a writable resource is the read-only candidate.
        ([_READ], {_READ, _CREATE}, CrudLifecycleLabel.READ_ONLY),
        ([_READ, _READ], {_READ, _UPDATE}, CrudLifecycleLabel.READ_ONLY),
        ([_READ], {_READ, _DELETE}, CrudLifecycleLabel.READ_ONLY),
        # Reading a resource with no write operations is not read-only.
        ([_READ], {_READ}, CrudLifecycleLabel.OTHER),
        ([_READ, _READ], {_READ}, CrudLifecycleLabel.OTHER),
        ([_READ], set(), CrudLifecycleLabel.OTHER),
        # Unknown availability falls back to the exercised shape.
        ([_READ], None, CrudLifecycleLabel.READ_ONLY),
    ],
)
def test_classify_read_only_requires_resource_write_ability(
    exercised: list[CrudOperation],
    available: set[CrudOperation] | None,
    expected_label: CrudLifecycleLabel,
) -> None:
    assert (
        classify_crud_lifecycle(exercised, available_operations=available)
        == expected_label
    )


# Full CRUD requires the resource to expose all CRUD operations


@pytest.mark.parametrize(
    ("available", "expected_label"),
    [
        # All four operations available -> full CRUD.
        (_ALL_CRUD, CrudLifecycleLabel.FULL_CRUD),
        # Availability missing any operation -> not full CRUD.
        ({_CREATE, _READ, _UPDATE}, CrudLifecycleLabel.OTHER),
        ({_CREATE, _READ, _DELETE}, CrudLifecycleLabel.OTHER),
        (set(), CrudLifecycleLabel.OTHER),
        # Unknown availability falls back to the exercised shape.
        (None, CrudLifecycleLabel.FULL_CRUD),
    ],
)
def test_classify_full_crud_requires_all_operations_available(
    available: set[CrudOperation] | None,
    expected_label: CrudLifecycleLabel,
) -> None:
    exercised = [_CREATE, _READ, _UPDATE, _DELETE]
    assert (
        classify_crud_lifecycle(exercised, available_operations=available)
        == expected_label
    )


def test_read_only_count_ignores_resources_without_write_ability() -> None:
    read_only = _test_method("readsOnly", "/catalog", ["GET", "HEAD"])
    endpoints = [_endpoint("GET", "/catalog/{itemId}")]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(read_only)],
    )

    resource_entry = summary.resources[0]
    assert resource_entry.available_operations == [CrudOperation.READ]
    assert resource_entry.read_only_test_count == 0
    sequence = read_only.http.resource_interaction_sequences[0]
    assert sequence.lifecycle_label == CrudLifecycleLabel.OTHER


def test_read_only_count_includes_reads_of_writable_resources() -> None:
    read_only = _test_method("readsOnly", "/orders", ["GET"])
    endpoints = [
        _endpoint("GET", "/orders/{orderId}"),
        _endpoint("POST", "/orders"),
    ]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(read_only)],
    )

    resource_entry = summary.resources[0]
    assert resource_entry.available_operations == [
        CrudOperation.CREATE,
        CrudOperation.READ,
    ]
    assert resource_entry.read_only_test_count == 1
    sequence = read_only.http.resource_interaction_sequences[0]
    assert sequence.lifecycle_label == CrudLifecycleLabel.READ_ONLY


def test_full_crud_count_requires_all_operations_available() -> None:
    full_crud = _test_method(
        "exercisesEverything", "/widgets", ["POST", "GET", "PUT", "DELETE"]
    )
    endpoints = [
        _endpoint("POST", "/widgets"),
        _endpoint("GET", "/widgets/{widgetId}"),
        _endpoint("PUT", "/widgets/{widgetId}"),
        _endpoint("DELETE", "/widgets/{widgetId}"),
    ]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(full_crud)],
    )

    resource_entry = summary.resources[0]
    assert resource_entry.available_operations == [
        CrudOperation.CREATE,
        CrudOperation.READ,
        CrudOperation.UPDATE,
        CrudOperation.DELETE,
    ]
    assert resource_entry.full_crud_test_count == 1
    assert summary.resources_with_full_crud_test_count == 1
    sequence = full_crud.http.resource_interaction_sequences[0]
    assert sequence.lifecycle_label == CrudLifecycleLabel.FULL_CRUD


def test_full_crud_count_excludes_resources_missing_an_operation() -> None:
    # The test exercises a DELETE, but the resource exposes no DELETE endpoint,
    # so the resource is not full-CRUD capable.
    full_shape = _test_method(
        "exercisesEverything", "/widgets", ["POST", "GET", "PUT", "DELETE"]
    )
    endpoints = [
        _endpoint("POST", "/widgets"),
        _endpoint("GET", "/widgets/{widgetId}"),
        _endpoint("PUT", "/widgets/{widgetId}"),
    ]

    summary = build_resource_crud_analysis(
        application_endpoints=endpoints,
        test_class_analyses=[_test_class(full_shape)],
    )

    resource_entry = summary.resources[0]
    assert resource_entry.available_operations == [
        CrudOperation.CREATE,
        CrudOperation.READ,
        CrudOperation.UPDATE,
    ]
    assert resource_entry.full_crud_test_count == 0
    assert summary.resources_with_full_crud_test_count == 0
    sequence = full_shape.http.resource_interaction_sequences[0]
    assert sequence.lifecycle_label == CrudLifecycleLabel.OTHER
