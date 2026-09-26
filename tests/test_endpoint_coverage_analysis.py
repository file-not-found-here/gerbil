from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest
from cldk.models.java import JCallable, JImport, JType
from cldk.models.java.models import JField

import gerbil.analysis.properties.endpoint.extraction as endpoint_extraction_module
import gerbil.analysis.shared.http_mapping_annotations as http_mapping_annotations_module

from gerbil.analysis.schema import (
    CallSiteOriginKind,
    CrudLifecycleLabel,
    CrudOperation,
    HttpAnalysis,
    ApplicationEndpoint,
    EndpointCandidate,
    EndpointCoverageSummary,
    EndpointParameter,
    EndpointParameterSource,
    HttpCallSite,
    HttpDispatchFramework,
    HttpRequestInteraction,
    HttpRequestRole,
    LifecyclePhase,
    MethodIdentity,
    OriginContext,
    TestClassAnalysis as ModelClassAnalysis,
    TestMethodAnalysis as ModelMethodAnalysis,
)
from gerbil.analysis.project import ProjectAnalysisInfo
from gerbil.analysis.properties.request_dispatch import analyze_request_dispatch
from gerbil.analysis.properties.endpoint.coverage import (
    _compile_path_template_segment_matchers,
    _http_methods_match,
    _path_segments,
    _template_has_plain_variable_tail,
    _template_matches,
    build_endpoint_coverage_summary,
)
from gerbil.analysis.properties.endpoint.extraction import (
    ConstantResolver,
    EndpointExtractionResult,
    extract_application_endpoints,
)
from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.shared import CommonAnalysis
from tests.cldk_factories import (
    annotate_node_http,
    make_call_site,
    make_callable,
    make_callable_parameter,
    make_field,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def _java_file_for_class_name(class_name: str) -> str:
    return f"src/main/java/{class_name.replace('.', '/')}.java"


def _constant_resolver_for(analysis: FakeJavaAnalysis) -> ConstantResolver:
    return CommonAnalysis(analysis).get_constant_resolver()


def _string_constant_field(variable: str, raw_initializer: str) -> JField:
    return make_field(
        type_name="java.lang.String",
        variables=[variable],
        modifiers=["static", "final"],
        variable_initializers={variable: raw_initializer},
    )


def _test_origin(method_signature: str) -> OriginContext:
    return OriginContext(
        phase=LifecyclePhase.TEST,
        kind=CallSiteOriginKind.TEST_METHOD,
        defining_class_name="example.ApiTest",
        method_signature=method_signature,
        entry_defining_class_name="example.ApiTest",
        entry_method_signature=method_signature,
    )


def test_application_endpoint_surface_summarizes_route_shape() -> None:
    cases = [
        ("/", 0, 0),
        ("/api/users", 2, 0),
        ("/api/users/{id}", 3, 1),
        ("/api/users/{userId}/orders/{orderId}", 5, 2),
        ("/api/files/{*path}", 3, 1),
    ]

    for path_template, route_depth, path_variable_count in cases:
        endpoint = ApplicationEndpoint(
            http_method="GET",
            path_template=path_template,
            framework="spring",
            declaring_class_name="example.Controller",
        )

        assert endpoint.surface.route_depth == route_depth
        assert endpoint.surface.path_variable_count == path_variable_count
        dumped_surface = endpoint.model_dump(exclude_defaults=True)["surface"]
        assert dumped_surface["route_depth"] == route_depth
        assert dumped_surface["path_variable_count"] == path_variable_count


def test_application_endpoint_surface_summarizes_parameters_by_source() -> None:
    endpoint = ApplicationEndpoint(
        http_method="POST",
        path_template="/api/users/{id}",
        framework="spring",
        declaring_class_name="example.Controller",
        parameters=[
            EndpointParameter(
                name="id",
                type="String",
                source=EndpointParameterSource.PATH,
            ),
            EndpointParameter(
                name="includeInactive",
                type="boolean",
                source=EndpointParameterSource.QUERY,
                required=False,
            ),
            EndpointParameter(
                name="sort",
                type="String",
                source=EndpointParameterSource.QUERY,
            ),
            EndpointParameter(
                name="x-request-id",
                type="String",
                source=EndpointParameterSource.HEADER,
                required=False,
            ),
            EndpointParameter(
                name="body",
                type="UserRequest",
                source=EndpointParameterSource.BODY,
            ),
            EndpointParameter(
                name="avatar",
                type="MultipartFile",
                source=EndpointParameterSource.FORM,
                required=False,
            ),
        ],
    )

    assert endpoint.surface.parameter_sources == [
        EndpointParameterSource.PATH,
        EndpointParameterSource.QUERY,
        EndpointParameterSource.HEADER,
        EndpointParameterSource.BODY,
        EndpointParameterSource.FORM,
    ]
    assert endpoint.surface.parameter_count_by_source == {
        EndpointParameterSource.PATH: 1,
        EndpointParameterSource.QUERY: 2,
        EndpointParameterSource.HEADER: 1,
        EndpointParameterSource.BODY: 1,
        EndpointParameterSource.FORM: 1,
    }
    assert endpoint.surface.required_parameter_count_by_source == {
        EndpointParameterSource.PATH: 1,
        EndpointParameterSource.QUERY: 1,
        EndpointParameterSource.HEADER: 0,
        EndpointParameterSource.BODY: 1,
        EndpointParameterSource.FORM: 0,
    }
    assert endpoint.surface.optional_parameter_count_by_source == {
        EndpointParameterSource.PATH: 0,
        EndpointParameterSource.QUERY: 1,
        EndpointParameterSource.HEADER: 1,
        EndpointParameterSource.BODY: 0,
        EndpointParameterSource.FORM: 1,
    }
    assert endpoint.surface.total_required_parameter_count == 3
    assert endpoint.surface.total_optional_parameter_count == 3


def _class_annotations(
    classes: Mapping[str, JType],
    methods_by_class: Mapping[str, Mapping[str, JCallable]],
    class_name: str,
) -> list[str]:
    class_annotations = list(classes.get(class_name, make_type()).annotations or [])
    method_annotations = [
        annotation
        for method in methods_by_class.get(class_name, {}).values()
        for annotation in (method.annotations or [])
    ]
    return class_annotations + method_annotations


def _build_class_graph(
    classes: Mapping[str, JType],
    extended_classes: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {class_name: set() for class_name in classes}
    for class_name, class_details in classes.items():
        related_class_names = set(class_details.extends_list or [])
        related_class_names.update(class_details.implements_list or [])
        related_class_names.update((extended_classes or {}).get(class_name, []))
        for related_class_name in related_class_names:
            if related_class_name not in graph:
                continue
            graph[class_name].add(related_class_name)
            graph[related_class_name].add(class_name)
    return graph


def _framework_candidates_for_annotation(
    annotation: str,
) -> set[endpoint_extraction_module.FrameworkName]:
    annotation_name = endpoint_extraction_module._annotation_short_name(annotation)
    annotation_token = http_mapping_annotations_module.annotation_name_token(annotation)
    qualified_name = annotation_token.removeprefix("@").strip()

    framework_candidates: set[endpoint_extraction_module.FrameworkName] = set()
    for (
        framework_name,
        annotation_import_roots,
    ) in endpoint_extraction_module._ANNOTATION_IMPORT_ROOTS_BY_FRAMEWORK.items():
        allowed_roots = annotation_import_roots.get(annotation_name)
        if not allowed_roots:
            continue
        if "." not in qualified_name:
            framework_candidates.add(framework_name)
            continue

        package_name = qualified_name.rsplit(".", 1)[0]
        if any(
            package_name == import_root.rstrip(".")
            or package_name.startswith(f"{import_root.rstrip('.')}.")
            for import_root in allowed_roots
        ):
            framework_candidates.add(framework_name)

    return framework_candidates


def _framework_by_class(
    classes: Mapping[str, JType],
    methods_by_class: Mapping[str, Mapping[str, JCallable]],
    extended_classes: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, endpoint_extraction_module.FrameworkName | None]:
    class_graph = _build_class_graph(classes, extended_classes=extended_classes)
    frameworks_by_class: dict[str, endpoint_extraction_module.FrameworkName | None] = {}
    visited: set[str] = set()

    for class_name in classes:
        if class_name in visited:
            continue

        component: set[str] = set()
        pending = [class_name]
        while pending:
            current_class_name = pending.pop()
            if current_class_name in component:
                continue
            component.add(current_class_name)
            pending.extend(class_graph.get(current_class_name, ()))

        visited.update(component)
        candidate_counts: dict[endpoint_extraction_module.FrameworkName, int] = {}
        for component_class_name in component:
            for annotation in _class_annotations(
                classes,
                methods_by_class,
                component_class_name,
            ):
                for framework_name in _framework_candidates_for_annotation(annotation):
                    candidate_counts[framework_name] = (
                        candidate_counts.get(framework_name, 0) + 1
                    )

        resolved_framework: endpoint_extraction_module.FrameworkName | None = None
        if candidate_counts:
            max_count = max(candidate_counts.values())
            top_frameworks = [
                framework_name
                for framework_name, count in candidate_counts.items()
                if count == max_count
            ]
            if len(top_frameworks) == 1:
                resolved_framework = top_frameworks[0]

        for component_class_name in component:
            frameworks_by_class[component_class_name] = resolved_framework

    return frameworks_by_class


def _import_path_for_annotation(
    annotation: str,
    framework_name: endpoint_extraction_module.FrameworkName,
) -> str | None:
    annotation_token = http_mapping_annotations_module.annotation_name_token(annotation)
    qualified_name = annotation_token.removeprefix("@").strip()
    if "." in qualified_name:
        return None

    annotation_name = endpoint_extraction_module._annotation_short_name(annotation)
    allowed_roots = endpoint_extraction_module._ANNOTATION_IMPORT_ROOTS_BY_FRAMEWORK[
        framework_name
    ].get(annotation_name)
    if not allowed_roots:
        return None

    return f"{sorted(allowed_roots)[0]}.{annotation_name.removeprefix('@')}"


def _make_endpoint_analysis(
    *,
    classes: dict[str, JType],
    methods_by_class: dict[str, dict[str, JCallable]] | None = None,
    java_files: dict[str, str] | None = None,
    import_declarations_by_file: Mapping[str, Sequence[JImport | str]] | None = None,
    extended_classes: dict[str, list[str]] | None = None,
) -> FakeJavaAnalysis:
    resolved_methods_by_class = methods_by_class or {}
    resolved_java_files = {
        class_name: _java_file_for_class_name(class_name) for class_name in classes
    }
    resolved_java_files.update(java_files or {})

    resolved_frameworks = _framework_by_class(
        classes,
        resolved_methods_by_class,
        extended_classes=extended_classes,
    )
    resolved_imports_by_file: dict[str, list[JImport | str]] = {}
    for class_name in classes:
        java_file = resolved_java_files[class_name]
        resolved_imports_by_file[java_file] = []
        framework_name = resolved_frameworks.get(class_name)
        if not framework_name:
            continue

        seen_import_paths: set[str] = set()
        for annotation in _class_annotations(
            classes,
            resolved_methods_by_class,
            class_name,
        ):
            import_path = _import_path_for_annotation(annotation, framework_name)
            if not import_path or import_path in seen_import_paths:
                continue
            seen_import_paths.add(import_path)
            resolved_imports_by_file[java_file].append(import_path)

    for java_file, import_entries in (import_declarations_by_file or {}).items():
        resolved_imports_by_file.setdefault(java_file, [])
        resolved_imports_by_file[java_file].extend(import_entries)

    return FakeJavaAnalysis(
        classes=classes,
        methods_by_class=resolved_methods_by_class,
        java_files=resolved_java_files,
        import_declarations_by_file=resolved_imports_by_file,
        extended_classes=extended_classes,
    )


def test_extract_application_endpoints_supports_spring_jaxrs_and_micronaut() -> None:
    analysis = _make_endpoint_analysis(
        classes={
            "example.SpringController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")']
            ),
            "example.JaxRsResource": make_type(annotations=['@Path("/api/orders")']),
            "example.MicronautController": make_type(
                annotations=['@Controller("/items")']
            ),
        },
        methods_by_class={
            "example.SpringController": {
                "getUser()": make_callable(
                    signature="getUser()",
                    annotations=['@GetMapping("/{id}")'],
                ),
                "createUser()": make_callable(
                    signature="createUser()",
                    annotations=[
                        '@RequestMapping(value = "", method = RequestMethod.POST)'
                    ],
                ),
            },
            "example.JaxRsResource": {
                "getOrder()": make_callable(
                    signature="getOrder()",
                    annotations=["@GET", '@Path("/{orderId}")'],
                )
            },
            "example.MicronautController": {
                "createItem()": make_callable(
                    signature="createItem()",
                    annotations=['@Post("/{id}")'],
                )
            },
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.SpringController",
            "example.JaxRsResource",
            "example.MicronautController",
        ],
    ).endpoints

    endpoint_keys = {
        (endpoint.framework, endpoint.http_method, endpoint.path_template)
        for endpoint in endpoints
    }

    assert ("spring", "GET", "/api/users/{id}") in endpoint_keys
    assert ("spring", "POST", "/api/users") in endpoint_keys
    assert ("jax-rs", "GET", "/api/orders/{orderId}") in endpoint_keys
    assert ("micronaut", "POST", "/items/{id}") in endpoint_keys


def test_extract_application_endpoints_mounts_jax_rs_subresources_from_locator_methods() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.RootResource": make_type(annotations=['@Path("/api/v1")']),
            "example.RunResource": make_type(),
        },
        methods_by_class={
            "example.RootResource": {
                "runResource()": make_callable(
                    signature="runResource()",
                    annotations=['@Path("/runs/{id}")'],
                    return_type="example.RunResource",
                )
            },
            "example.RunResource": {
                "getRun()": make_callable(
                    signature="getRun()",
                    annotations=["@GET", '@Path("/")'],
                ),
                "abortRun()": make_callable(
                    signature="abortRun()",
                    annotations=["@POST", '@Path("abort")'],
                ),
            },
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.RootResource", "example.RunResource"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert ("GET", "/api/v1/runs/{id}") in endpoint_keys
    assert ("POST", "/api/v1/runs/{id}/abort") in endpoint_keys
    assert ("GET", "/") not in endpoint_keys
    assert ("POST", "/abort") not in endpoint_keys


def test_extract_application_endpoints_inherits_jax_rs_class_path_from_superclass() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.BaseResource": make_type(annotations=['@Path("/api")']),
            "example.OrderResource": make_type(extends_list=["example.BaseResource"]),
        },
        methods_by_class={
            "example.OrderResource": {
                "getOrder()": make_callable(
                    signature="getOrder()",
                    annotations=["@GET", '@Path("/orders/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.BaseResource", "example.OrderResource"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert ("GET", "/api/orders/{id}") in endpoint_keys
    assert ("GET", "/orders/{id}") not in endpoint_keys


def test_extract_application_endpoints_jax_rs_own_class_path_overrides_inherited() -> (
    None
):
    # JAX-RS mounts a class carrying its own @Path there alone; superclass
    # @Path values are not prepended (Jersey resolves the nearest annotated
    # class in the hierarchy).
    analysis = _make_endpoint_analysis(
        classes={
            "example.BaseResource": make_type(annotations=['@Path("/api")']),
            "example.VersionedResource": make_type(
                annotations=['@Path("/v1")'],
                extends_list=["example.BaseResource"],
            ),
            "example.OrderResource": make_type(
                annotations=['@Path("/orders")'],
                extends_list=["example.VersionedResource"],
            ),
        },
        methods_by_class={
            "example.OrderResource": {
                "getOrder()": make_callable(
                    signature="getOrder()",
                    annotations=["@GET", '@Path("/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.BaseResource",
            "example.VersionedResource",
            "example.OrderResource",
        ],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/orders/{id}")}


def test_extract_application_endpoints_inherits_jax_rs_interface_paths() -> None:
    analysis = _make_endpoint_analysis(
        classes={
            "example.ResourceContract": make_type(annotations=['@Path("/contract")']),
            "example.OrderResource": make_type(
                implements_list=["example.ResourceContract"]
            ),
        },
        methods_by_class={
            "example.OrderResource": {
                "getOrder()": make_callable(
                    signature="getOrder()",
                    annotations=["@GET", '@Path("/items/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.ResourceContract", "example.OrderResource"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert ("GET", "/contract/items/{id}") in endpoint_keys
    assert ("GET", "/items/{id}") not in endpoint_keys


def test_extract_application_endpoints_jax_rs_interface_own_path_overrides_parents() -> (
    None
):
    # The implementing class has no @Path of its own, so it inherits from the
    # nearest annotated contract; that contract's own @Path is the whole mount.
    analysis = _make_endpoint_analysis(
        classes={
            "example.RootResourceContract": make_type(annotations=['@Path("/api")']),
            "example.VersionedResourceContract": make_type(
                annotations=['@Path("/v1")'],
                extends_list=["example.RootResourceContract"],
            ),
            "example.OrderResourceContract": make_type(
                annotations=['@Path("/orders")'],
                extends_list=["example.VersionedResourceContract"],
            ),
            "example.OrderResource": make_type(
                implements_list=["example.OrderResourceContract"]
            ),
        },
        methods_by_class={
            "example.OrderResource": {
                "getOrder()": make_callable(
                    signature="getOrder()",
                    annotations=["@GET", '@Path("/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.RootResourceContract",
            "example.VersionedResourceContract",
            "example.OrderResourceContract",
            "example.OrderResource",
        ],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/orders/{id}")}


def test_extract_application_endpoints_handles_cyclic_jax_rs_locator_graphs(
    monkeypatch,
) -> None:
    analysis = _make_endpoint_analysis(
        classes={
            "example.RootResource": make_type(annotations=['@Path("/api")']),
            "example.SubResource": make_type(),
        },
        methods_by_class={
            "example.RootResource": {
                "subResource()": make_callable(
                    signature="subResource()",
                    annotations=['@Path("/runs")'],
                    return_type="example.SubResource",
                ),
                "getRoot()": make_callable(
                    signature="getRoot()",
                    annotations=["@GET", '@Path("/root")'],
                ),
            },
            "example.SubResource": {
                "rootResource()": make_callable(
                    signature="rootResource()",
                    annotations=['@Path("/owner")'],
                    return_type="example.RootResource",
                ),
                "getRun()": make_callable(
                    signature="getRun()",
                    annotations=["@GET", '@Path("/leaf")'],
                ),
            },
        },
    )
    join_call_limit = 64
    join_call_count = 0
    original_join_paths = endpoint_extraction_module._join_paths

    def counting_join_paths(class_path: str, method_path: str) -> str:
        nonlocal join_call_count
        join_call_count += 1
        if join_call_count > join_call_limit:
            raise AssertionError("locator mount expansion exceeded expected bounds")
        return original_join_paths(class_path, method_path)

    monkeypatch.setattr(
        endpoint_extraction_module,
        "_join_paths",
        counting_join_paths,
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.RootResource", "example.SubResource"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {
        ("GET", "/api/root"),
        ("GET", "/api/runs/leaf"),
    }
    assert join_call_count <= join_call_limit


def test_extract_application_endpoints_ignores_consumes_without_path_and_matches_coverage() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")']
            )
        },
        methods_by_class={
            "example.UserController": {
                "createUser()": make_callable(
                    signature="createUser()",
                    annotations=[
                        (
                            "@RequestMapping(method = RequestMethod.POST, "
                            'consumes = "application/json")'
                        )
                    ],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserController"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("POST", "/api/users")}
    assert ("POST", "/api/users/application/json") not in endpoint_keys

    coverage = build_endpoint_coverage_summary(
        application_endpoints=endpoints,
        test_class_analyses=[
            ModelClassAnalysis(
                qualified_class_name="example.ApiTest",
                test_method_analyses=[
                    ModelMethodAnalysis(
                        identity=MethodIdentity(
                            defining_class_name="example.ApiTest",
                            method_signature="testCreateUser()",
                            method_declaration="void testCreateUser()",
                        ),
                        http=HttpAnalysis(
                            request_interactions=[
                                HttpRequestInteraction(
                                    origin=_test_origin("testCreateUser()"),
                                    http_call=HttpCallSite(
                                        http_method="POST",
                                        path="/api/users",
                                        framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                        method_name="postForEntity",
                                    ),
                                    endpoint_candidate=EndpointCandidate(
                                        http_method="POST",
                                        path="/api/users",
                                        source="call-site",
                                    ),
                                )
                            ]
                        ),
                    )
                ],
            )
        ],
    )

    assert coverage.covered_endpoint_count == 1
    assert coverage.untested_endpoint_count == 0
    assert coverage.endpoints[0].covering_test_method_count == 1
    assert coverage.endpoints[0].is_covered is True


def test_extract_application_endpoints_uses_only_leading_positional_path_argument() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")']
            )
        },
        methods_by_class={
            "example.UserController": {
                "getUser()": make_callable(
                    signature="getUser()",
                    annotations=['@GetMapping("/{id}", produces = "application/json")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserController"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/api/users/{id}")}


def test_extract_application_endpoints_supports_positional_path_arrays_without_media_type_leakage() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api")']
            )
        },
        methods_by_class={
            "example.UserController": {
                "getUsers()": make_callable(
                    signature="getUsers()",
                    annotations=[
                        (
                            '@RequestMapping({"/a", "/b"}, method = '
                            'RequestMethod.GET, produces = "application/json")'
                        )
                    ],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserController"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {
        ("GET", "/api/a"),
        ("GET", "/api/b"),
    }
    assert all(
        "application/json" not in endpoint.path_template for endpoint in endpoints
    )


def test_positional_path_with_equals_inside_quotes() -> None:
    from gerbil.analysis.shared.http_mapping_annotations import (
        _extract_leading_positional_paths,
    )

    result = _extract_leading_positional_paths('"/search?q={q}"')
    assert result == ["/search?q={q}"]


def test_named_argument_with_equals_rejected() -> None:
    from gerbil.analysis.shared.http_mapping_annotations import (
        _extract_leading_positional_paths,
    )

    result = _extract_leading_positional_paths('value = "/path"')
    assert result == []


def test_positional_path_without_equals() -> None:
    from gerbil.analysis.shared.http_mapping_annotations import (
        _extract_leading_positional_paths,
    )

    result = _extract_leading_positional_paths('"/api/users"')
    assert result == ["/api/users"]


def test_extract_application_endpoints_parses_class_paths_once_per_class(
    monkeypatch,
) -> None:
    class_annotation = '@RequestMapping("/api/users")'
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserController": make_type(
                annotations=["@RestController", class_annotation]
            )
        },
        methods_by_class={
            "example.UserController": {
                "getOne()": make_callable(
                    signature="getOne()",
                    annotations=['@GetMapping("/{id}")'],
                ),
                "getTwo()": make_callable(
                    signature="getTwo()",
                    annotations=['@GetMapping("/{id}/summary")'],
                ),
            }
        },
    )

    parsed_class_annotation_count = 0
    original_extract_annotation_paths = (
        endpoint_extraction_module._extract_annotation_paths
    )

    def counting_extract_annotation_paths(
        annotation: str, constant_resolver=None
    ) -> list[str]:
        nonlocal parsed_class_annotation_count
        if annotation == class_annotation:
            parsed_class_annotation_count += 1
        return original_extract_annotation_paths(annotation, constant_resolver)

    monkeypatch.setattr(
        endpoint_extraction_module,
        "_extract_annotation_paths",
        counting_extract_annotation_paths,
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserController"],
    ).endpoints

    assert len(endpoints) == 2
    assert parsed_class_annotation_count == 1


def test_extract_application_endpoints_request_mapping_without_method_is_wildcard() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api")']
            )
        },
        methods_by_class={
            "example.UserController": {
                "getOrder()": make_callable(
                    signature="getOrder()",
                    annotations=['@RequestMapping("/orders/get")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserController"],
    ).endpoints
    endpoint_keys = {
        (
            endpoint.http_method,
            endpoint.is_method_wildcard,
            endpoint.path_template,
        )
        for endpoint in endpoints
    }

    assert endpoint_keys == {("UNKNOWN", True, "/api/orders/get")}
    assert ("GET", False, "/api/orders/get") not in endpoint_keys


def test_extract_application_endpoints_request_mapping_with_unresolved_method_is_unknown() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api")']
            )
        },
        methods_by_class={
            "example.UserController": {
                "getOrder()": make_callable(
                    signature="getOrder()",
                    annotations=[
                        '@RequestMapping(path = "/orders/get", method = customMethod())'
                    ],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserController"],
    ).endpoints
    endpoint_keys = {
        (
            endpoint.http_method,
            endpoint.is_method_wildcard,
            endpoint.path_template,
        )
        for endpoint in endpoints
    }

    assert endpoint_keys == {("UNKNOWN", False, "/api/orders/get")}


def test_extract_application_endpoints_request_mapping_expands_trace_and_connect() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api")']
            )
        },
        methods_by_class={
            "example.UserController": {
                "diagnostics()": make_callable(
                    signature="diagnostics()",
                    annotations=[
                        (
                            '@RequestMapping(path = "/diagnostics", method = '
                            "{RequestMethod.TRACE, RequestMethod.CONNECT})"
                        )
                    ],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserController"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {
        ("TRACE", "/api/diagnostics"),
        ("CONNECT", "/api/diagnostics"),
    }


def test_extract_application_endpoints_requires_spring_server_annotation() -> None:
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserRoutes": make_type(annotations=['@RequestMapping("/api")'])
        },
        methods_by_class={
            "example.UserRoutes": {
                "getUser()": make_callable(
                    signature="getUser()",
                    annotations=['@GetMapping("/users/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserRoutes"],
    ).endpoints

    assert endpoints == []


def test_extract_application_endpoints_supports_custom_spring_controller_like_annotation() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserRoutes": make_type(
                annotations=["@AdminController", '@RequestMapping("/api")']
            )
        },
        methods_by_class={
            "example.UserRoutes": {
                "getUser()": make_callable(
                    signature="getUser()",
                    annotations=['@GetMapping("/users/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserRoutes"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/api/users/{id}")}


def test_extract_application_endpoints_excludes_spring_client_annotations() -> None:
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserClient": make_type(
                annotations=[
                    '@FeignClient(name = "users")',
                    "@RestController",
                    '@RequestMapping("/api")',
                ]
            )
        },
        methods_by_class={
            "example.UserClient": {
                "getUser()": make_callable(
                    signature="getUser()",
                    annotations=['@GetMapping("/users/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserClient"],
    ).endpoints

    assert endpoints == []


def test_extract_application_endpoints_allows_server_when_client_annotation_is_only_in_interface() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserClientContract": make_type(
                annotations=['@FeignClient(name = "users")']
            ),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")'],
                implements_list=["example.UserClientContract"],
            ),
        },
        methods_by_class={
            "example.UserController": {
                "getUser()": make_callable(
                    signature="getUser()",
                    annotations=['@GetMapping("/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserClientContract", "example.UserController"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/api/users/{id}")}


def test_extract_application_endpoints_inherits_spring_class_path_from_superclass() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.BaseController": make_type(
                annotations=["@RestController", '@RequestMapping("/api")']
            ),
            "example.UserController": make_type(
                extends_list=["example.BaseController"]
            ),
        },
        methods_by_class={
            "example.UserController": {
                "getUser()": make_callable(
                    signature="getUser()",
                    annotations=['@GetMapping("/users/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.BaseController", "example.UserController"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/api/users/{id}")}


def test_extract_application_endpoints_inherits_spring_method_mapping_from_interface() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(
                annotations=['@RequestMapping("/contract")'],
                is_interface=True,
            ),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")'],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "getUser(java.lang.String, boolean)": make_callable(
                    signature="getUser(java.lang.String, boolean)",
                    annotations=['@GetMapping("/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="java.lang.String",
                            annotations=['@PathVariable("id")'],
                        ),
                        make_callable_parameter(
                            name="verbose",
                            type_name="boolean",
                            annotations=[
                                '@RequestParam(value = "verbose", required = false)'
                            ],
                        ),
                    ],
                ),
            },
            "example.UserController": {
                "getUser(java.lang.String, boolean)": make_callable(
                    signature="getUser(java.lang.String, boolean)",
                    parameters=[
                        make_callable_parameter(
                            name="id", type_name="java.lang.String"
                        ),
                        make_callable_parameter(name="verbose", type_name="boolean"),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UserApi"): [
                "org.springframework.web.bind.annotation.PathVariable",
                "org.springframework.web.bind.annotation.RequestParam",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserController"],
    ).endpoints

    assert [
        (
            endpoint.framework,
            endpoint.http_method,
            endpoint.path_template,
            endpoint.declaring_class_name,
            endpoint.declaring_method_signature,
        )
        for endpoint in endpoints
    ] == [
        (
            "spring",
            "GET",
            "/api/users/{id}",
            "example.UserController",
            "getUser(java.lang.String, boolean)",
        )
    ]
    parameters_by_name = {
        parameter.name: parameter for parameter in endpoints[0].parameters
    }
    assert parameters_by_name["id"].source == EndpointParameterSource.PATH
    assert parameters_by_name["id"].type == "java.lang.String"
    assert not parameters_by_name["id"].is_synthetic
    assert parameters_by_name["verbose"].source == EndpointParameterSource.QUERY
    assert parameters_by_name["verbose"].required is False


def test_extract_application_endpoints_spring_impl_mapping_shadows_interface_mapping() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(is_interface=True),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")'],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/{id}")'],
                ),
            },
            "example.UserController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/v2/{id}")'],
                ),
            },
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserController"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/api/users/v2/{id}")}


def test_extract_application_endpoints_merges_interface_parameter_annotations_into_mapped_override() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(is_interface=True),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")'],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "getUser(java.lang.String, boolean)": make_callable(
                    signature="getUser(java.lang.String, boolean)",
                    annotations=['@GetMapping("/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="java.lang.String",
                            annotations=['@PathVariable("id")'],
                        ),
                        make_callable_parameter(
                            name="verbose",
                            type_name="boolean",
                            annotations=[
                                '@RequestParam(value = "verbose", required = false)'
                            ],
                        ),
                    ],
                ),
            },
            "example.UserController": {
                "getUser(java.lang.String, boolean)": make_callable(
                    signature="getUser(java.lang.String, boolean)",
                    annotations=['@GetMapping("/v2/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id", type_name="java.lang.String"
                        ),
                        make_callable_parameter(name="verbose", type_name="boolean"),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UserApi"): [
                "org.springframework.web.bind.annotation.PathVariable",
                "org.springframework.web.bind.annotation.RequestParam",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserController"],
    ).endpoints

    assert [
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    ] == [("GET", "/api/users/v2/{id}")]
    parameters_by_name = {
        parameter.name: parameter for parameter in endpoints[0].parameters
    }
    assert parameters_by_name["id"].source == EndpointParameterSource.PATH
    assert parameters_by_name["id"].type == "java.lang.String"
    assert not parameters_by_name["id"].is_synthetic
    assert parameters_by_name["verbose"].source == EndpointParameterSource.QUERY
    assert parameters_by_name["verbose"].required is False


def test_extract_application_endpoints_concrete_parameter_annotation_wins_over_interface() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.SearchApi": make_type(is_interface=True),
            "example.SearchController": make_type(
                annotations=["@RestController"],
                implements_list=["example.SearchApi"],
            ),
        },
        methods_by_class={
            "example.SearchApi": {
                "search(int)": make_callable(
                    signature="search(int)",
                    parameters=[
                        make_callable_parameter(
                            name="page",
                            type_name="int",
                            annotations=['@RequestParam("pageNumber")'],
                        ),
                    ],
                ),
            },
            "example.SearchController": {
                "search(int)": make_callable(
                    signature="search(int)",
                    annotations=['@GetMapping("/search")'],
                    parameters=[
                        make_callable_parameter(
                            name="page",
                            type_name="int",
                            annotations=['@RequestParam("page")'],
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.SearchApi"): [
                "org.springframework.web.bind.annotation.RequestParam",
            ],
            _java_file_for_class_name("example.SearchController"): [
                "org.springframework.web.bind.annotation.RequestParam",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.SearchApi", "example.SearchController"],
    ).endpoints

    assert len(endpoints) == 1
    query_parameters = [
        parameter
        for parameter in endpoints[0].parameters
        if parameter.source == EndpointParameterSource.QUERY
    ]
    assert [parameter.name for parameter in query_parameters] == ["page"]


def test_extract_application_endpoints_inherited_annotation_binds_when_concrete_lookalike_fails_validation() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.SearchApi": make_type(is_interface=True),
            "example.SearchController": make_type(
                annotations=["@RestController"],
                implements_list=["example.SearchApi"],
            ),
        },
        methods_by_class={
            "example.SearchApi": {
                "search(java.lang.String)": make_callable(
                    signature="search(java.lang.String)",
                    parameters=[
                        make_callable_parameter(
                            name="q",
                            type_name="java.lang.String",
                            annotations=['@RequestParam("q")'],
                        ),
                    ],
                ),
            },
            "example.SearchController": {
                "search(java.lang.String)": make_callable(
                    signature="search(java.lang.String)",
                    annotations=['@GetMapping("/search")'],
                    parameters=[
                        make_callable_parameter(
                            name="q",
                            type_name="java.lang.String",
                            annotations=['@RequestParam("ignored")'],
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.SearchApi"): [
                "org.springframework.web.bind.annotation.RequestParam",
            ],
            # The concrete class's @RequestParam resolves to a non-Spring
            # annotation, so it must not shadow the inherited Spring one.
            _java_file_for_class_name("example.SearchController"): [
                "com.acme.RequestParam",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.SearchApi", "example.SearchController"],
    ).endpoints

    assert len(endpoints) == 1
    query_parameters = [
        parameter
        for parameter in endpoints[0].parameters
        if parameter.source == EndpointParameterSource.QUERY
    ]
    assert [parameter.name for parameter in query_parameters] == ["q"]


def test_extract_application_endpoints_merges_superclass_parameter_annotations_into_mapped_override() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.AbstractUserController": make_type(),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")'],
                extends_list=["example.AbstractUserController"],
            ),
        },
        methods_by_class={
            "example.AbstractUserController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="java.lang.String",
                            annotations=['@PathVariable("id")'],
                        ),
                    ],
                ),
            },
            "example.UserController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id", type_name="java.lang.String"
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.AbstractUserController"): [
                "org.springframework.web.bind.annotation.PathVariable",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.AbstractUserController",
            "example.UserController",
        ],
    ).endpoints

    assert [
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    ] == [("GET", "/api/users/{id}")]
    parameters_by_name = {
        parameter.name: parameter for parameter in endpoints[0].parameters
    }
    assert parameters_by_name["id"].source == EndpointParameterSource.PATH
    assert not parameters_by_name["id"].is_synthetic


def test_extract_application_endpoints_ignores_private_supertype_parameter_annotations() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.AbstractUserController": make_type(),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")'],
                extends_list=["example.AbstractUserController"],
            ),
        },
        methods_by_class={
            "example.AbstractUserController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    modifiers=["private"],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="java.lang.String",
                            annotations=['@PathVariable("id")'],
                        ),
                    ],
                ),
            },
            "example.UserController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id", type_name="java.lang.String"
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.AbstractUserController"): [
                "org.springframework.web.bind.annotation.PathVariable",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.AbstractUserController",
            "example.UserController",
        ],
    ).endpoints

    assert [
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    ] == [("GET", "/api/users/{id}")]
    parameters_by_name = {
        parameter.name: parameter for parameter in endpoints[0].parameters
    }
    assert parameters_by_name["id"].is_synthetic


def test_extract_application_endpoints_does_not_adopt_private_supertype_mapping() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.AbstractUserController": make_type(),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")'],
                extends_list=["example.AbstractUserController"],
            ),
        },
        methods_by_class={
            "example.AbstractUserController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/{id}")'],
                    modifiers=["private"],
                ),
            },
            "example.UserController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                ),
            },
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.AbstractUserController",
            "example.UserController",
        ],
    ).endpoints

    assert endpoints == []


def test_extract_application_endpoints_resolves_mapping_past_private_supertype_method() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.BaseController": make_type(),
            "example.MiddleController": make_type(
                extends_list=["example.BaseController"],
            ),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")'],
                extends_list=["example.MiddleController"],
            ),
        },
        methods_by_class={
            "example.BaseController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/base/{id}")'],
                ),
            },
            "example.MiddleController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/middle/{id}")'],
                    modifiers=["private"],
                ),
            },
            "example.UserController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                ),
            },
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.BaseController",
            "example.MiddleController",
            "example.UserController",
        ],
    ).endpoints

    assert [
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    ] == [("GET", "/api/users/base/{id}")]


def test_extract_application_endpoints_spring_interface_mapping_requires_exact_signature() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(is_interface=True),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")'],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "getUser(ID)": make_callable(
                    signature="getUser(ID)",
                    annotations=['@GetMapping("/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="ID",
                            annotations=['@PathVariable("id")'],
                        ),
                    ],
                ),
            },
            "example.UserController": {
                "getUser(java.lang.Long)": make_callable(
                    signature="getUser(java.lang.Long)",
                    parameters=[
                        make_callable_parameter(name="id", type_name="java.lang.Long"),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UserApi"): [
                "org.springframework.web.bind.annotation.PathVariable",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserController"],
    ).endpoints

    assert endpoints == []


def test_extract_application_endpoints_spring_interface_mapping_requires_controller_impl() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(is_interface=True),
            "example.UserService": make_type(
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/{id}")'],
                ),
            },
            "example.UserService": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                ),
            },
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserService"],
    ).endpoints

    assert endpoints == []


def test_extract_application_endpoints_inherits_spring_method_mapping_from_superclass() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.AbstractUserController": make_type(),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")'],
                extends_list=["example.AbstractUserController"],
            ),
        },
        methods_by_class={
            "example.AbstractUserController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="java.lang.String",
                            annotations=['@PathVariable("id")'],
                        ),
                    ],
                ),
            },
            "example.UserController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    parameters=[
                        make_callable_parameter(
                            name="id", type_name="java.lang.String"
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.AbstractUserController"): [
                "org.springframework.web.bind.annotation.PathVariable",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.AbstractUserController",
            "example.UserController",
        ],
    ).endpoints

    assert [
        (
            endpoint.http_method,
            endpoint.path_template,
            endpoint.declaring_class_name,
        )
        for endpoint in endpoints
    ] == [("GET", "/api/users/{id}", "example.UserController")]
    parameters_by_name = {
        parameter.name: parameter for parameter in endpoints[0].parameters
    }
    assert parameters_by_name["id"].source == EndpointParameterSource.PATH
    assert not parameters_by_name["id"].is_synthetic


def test_extract_application_endpoints_inherits_spring_methods_from_non_server_abstract_base() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.AbstractCrudController": make_type(),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/users")'],
                extends_list=["example.AbstractCrudController"],
            ),
        },
        methods_by_class={
            "example.AbstractCrudController": {
                "getOne(java.lang.Long)": make_callable(
                    signature="getOne(java.lang.Long)",
                    annotations=['@GetMapping("/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="java.lang.Long",
                            annotations=['@PathVariable("id")'],
                        ),
                    ],
                ),
                "create(example.User)": make_callable(
                    signature="create(example.User)",
                    annotations=["@PostMapping"],
                    parameters=[
                        make_callable_parameter(
                            name="entity",
                            type_name="example.User",
                            annotations=["@RequestBody"],
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.AbstractCrudController"): [
                "org.springframework.web.bind.annotation.PathVariable",
                "org.springframework.web.bind.annotation.RequestBody",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.AbstractCrudController",
            "example.UserController",
        ],
    ).endpoints

    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template, endpoint.declaring_class_name)
        for endpoint in endpoints
    }
    assert endpoint_keys == {
        ("GET", "/users/{id}", "example.UserController"),
        ("POST", "/users", "example.UserController"),
    }
    get_endpoint = next(
        endpoint for endpoint in endpoints if endpoint.http_method == "GET"
    )
    parameters_by_name = {
        parameter.name: parameter for parameter in get_endpoint.parameters
    }
    assert parameters_by_name["id"].source == EndpointParameterSource.PATH
    assert not parameters_by_name["id"].is_synthetic


def test_extract_application_endpoints_inherited_param_resolves_constant_name() -> None:
    analysis = _make_endpoint_analysis(
        classes={
            "example.QueryParams": make_type(
                field_declarations=[_string_constant_field("LIMIT", '"limit"')],
            ),
            "example.AbstractListController": make_type(),
            "example.ItemController": make_type(
                annotations=["@RestController", '@RequestMapping("/items")'],
                extends_list=["example.AbstractListController"],
            ),
        },
        methods_by_class={
            "example.AbstractListController": {
                "list(int)": make_callable(
                    signature="list(int)",
                    annotations=["@GetMapping"],
                    parameters=[
                        make_callable_parameter(
                            name="max",
                            type_name="int",
                            annotations=["@RequestParam(QueryParams.LIMIT)"],
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.AbstractListController"): [
                "example.QueryParams",
                "org.springframework.web.bind.annotation.RequestParam",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.QueryParams",
            "example.AbstractListController",
            "example.ItemController",
        ],
        constant_resolver=_constant_resolver_for(analysis),
    ).endpoints

    get_endpoint = next(
        endpoint
        for endpoint in endpoints
        if endpoint.http_method == "GET"
        and endpoint.declaring_class_name == "example.ItemController"
    )
    parameter_names = {parameter.name for parameter in get_endpoint.parameters}
    # The inherited @RequestParam constant resolves to its string value, not the
    # Java parameter name.
    assert "limit" in parameter_names
    assert "max" not in parameter_names
    limit_parameter = next(
        parameter for parameter in get_endpoint.parameters if parameter.name == "limit"
    )
    assert limit_parameter.source == EndpointParameterSource.QUERY


def test_extract_application_endpoints_skips_server_supertype_for_inherited_methods() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.AbstractController": make_type(
                annotations=["@RestController", '@RequestMapping("/base")'],
            ),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/users")'],
                extends_list=["example.AbstractController"],
            ),
        },
        methods_by_class={
            "example.AbstractController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/{id}")'],
                ),
            },
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.AbstractController",
            "example.UserController",
        ],
    ).endpoints

    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template, endpoint.declaring_class_name)
        for endpoint in endpoints
    }
    assert endpoint_keys == {("GET", "/base/{id}", "example.AbstractController")}


def test_extract_application_endpoints_does_not_double_emit_partially_overridden_spring_base() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.AbstractCrudController": make_type(),
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/users")'],
                extends_list=["example.AbstractCrudController"],
            ),
        },
        methods_by_class={
            "example.AbstractCrudController": {
                "getOne(java.lang.Long)": make_callable(
                    signature="getOne(java.lang.Long)",
                    annotations=['@GetMapping("/{id}")'],
                ),
                "create(example.User)": make_callable(
                    signature="create(example.User)",
                    annotations=["@PostMapping"],
                ),
            },
            "example.UserController": {
                "getOne(java.lang.Long)": make_callable(
                    signature="getOne(java.lang.Long)",
                    annotations=['@GetMapping("/{id}")'],
                ),
            },
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.AbstractCrudController",
            "example.UserController",
        ],
    ).endpoints

    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template, endpoint.declaring_class_name)
        for endpoint in endpoints
    }
    assert endpoint_keys == {
        ("GET", "/users/{id}", "example.UserController"),
        ("POST", "/users", "example.UserController"),
    }


def test_extract_application_endpoints_jax_rs_bare_override_inherits_interface_mapping() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(is_interface=True),
            "example.UserResource": make_type(
                annotations=['@Path("/users")'],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@GET", '@Path("{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="long",
                            annotations=['@PathParam("id")'],
                        ),
                    ],
                ),
            },
            "example.UserResource": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@Override"],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="long",
                            annotations=["@Nonnull"],
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UserApi"): [
                "javax.ws.rs.GET",
                "javax.ws.rs.Path",
                "javax.ws.rs.PathParam",
            ],
            _java_file_for_class_name("example.UserResource"): [
                "javax.ws.rs.Path",
                "javax.annotation.Nonnull",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserResource"],
    ).endpoints

    assert [
        (
            endpoint.http_method,
            endpoint.path_template,
            endpoint.declaring_class_name,
        )
        for endpoint in endpoints
    ] == [("GET", "/users/{id}", "example.UserResource")]
    parameters_by_name = {
        parameter.name: parameter for parameter in endpoints[0].parameters
    }
    assert parameters_by_name["id"].source == EndpointParameterSource.PATH
    assert not parameters_by_name["id"].is_synthetic


def test_extract_application_endpoints_jax_rs_superclass_takes_precedence_over_interface() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(is_interface=True),
            "example.AbstractUserResource": make_type(
                extends_list=["java.lang.Object"],
            ),
            "example.UserResource": make_type(
                annotations=['@Path("/users")'],
                extends_list=["example.AbstractUserResource"],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@POST", '@Path("iface/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="long",
                            annotations=['@QueryParam("id")'],
                        ),
                    ],
                ),
            },
            "example.AbstractUserResource": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@GET", '@Path("super/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="long",
                            annotations=['@PathParam("id")'],
                        ),
                    ],
                ),
            },
            "example.UserResource": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@Override"],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="long",
                            annotations=["@Nonnull"],
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UserApi"): [
                "javax.ws.rs.POST",
                "javax.ws.rs.Path",
                "javax.ws.rs.QueryParam",
            ],
            _java_file_for_class_name("example.AbstractUserResource"): [
                "javax.ws.rs.GET",
                "javax.ws.rs.Path",
                "javax.ws.rs.PathParam",
            ],
            _java_file_for_class_name("example.UserResource"): [
                "javax.ws.rs.Path",
                "javax.annotation.Nonnull",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.UserApi",
            "example.AbstractUserResource",
            "example.UserResource",
        ],
    ).endpoints

    assert [
        (
            endpoint.http_method,
            endpoint.path_template,
            endpoint.declaring_class_name,
        )
        for endpoint in endpoints
    ] == [("GET", "/users/super/{id}", "example.UserResource")]
    parameters_by_name = {
        parameter.name: parameter for parameter in endpoints[0].parameters
    }
    assert parameters_by_name["id"].source == EndpointParameterSource.PATH
    assert not parameters_by_name["id"].is_synthetic


def test_extract_application_endpoints_jax_rs_bare_override_inherits_default_value_query_param_optional() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.MappingApi": make_type(is_interface=True),
            "example.DefaultsApi": make_type(is_interface=True),
            "example.UserResource": make_type(
                annotations=['@Path("/users")'],
                implements_list=["example.MappingApi", "example.DefaultsApi"],
            ),
        },
        methods_by_class={
            "example.MappingApi": {
                "list(int)": make_callable(
                    signature="list(int)",
                    annotations=["@GET"],
                    parameters=[
                        make_callable_parameter(
                            name="limit",
                            type_name="int",
                            annotations=['@QueryParam("limit")'],
                        ),
                    ],
                ),
            },
            "example.DefaultsApi": {
                "list(int)": make_callable(
                    signature="list(int)",
                    annotations=[],
                    parameters=[
                        make_callable_parameter(
                            name="limit",
                            type_name="int",
                            annotations=['@DefaultValue("10")'],
                        ),
                    ],
                ),
            },
            "example.UserResource": {
                "list(int)": make_callable(
                    signature="list(int)",
                    annotations=["@Override"],
                    parameters=[
                        make_callable_parameter(name="limit", type_name="int"),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.MappingApi"): [
                "javax.ws.rs.GET",
                "javax.ws.rs.QueryParam",
            ],
            _java_file_for_class_name("example.DefaultsApi"): [
                "javax.ws.rs.DefaultValue",
            ],
            _java_file_for_class_name("example.UserResource"): [
                "javax.ws.rs.Path",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.MappingApi",
            "example.DefaultsApi",
            "example.UserResource",
        ],
    ).endpoints

    assert [
        (endpoint.http_method, endpoint.path_template, endpoint.declaring_class_name)
        for endpoint in endpoints
    ] == [("GET", "/users", "example.UserResource")]
    parameters_by_name = {
        parameter.name: parameter for parameter in endpoints[0].parameters
    }
    limit_parameter = parameters_by_name["limit"]
    assert limit_parameter.source == EndpointParameterSource.QUERY
    assert limit_parameter.required is False


def test_extract_application_endpoints_jax_rs_interface_with_type_path_emits_once() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(
                annotations=['@Path("/api")'],
                is_interface=True,
            ),
            "example.UserResource": make_type(
                annotations=['@Path("/users")'],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@GET", '@Path("{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="long",
                            annotations=['@PathParam("id")'],
                        ),
                    ],
                ),
            },
            "example.UserResource": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=[],
                    parameters=[
                        make_callable_parameter(name="id", type_name="long"),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UserApi"): [
                "javax.ws.rs.GET",
                "javax.ws.rs.Path",
                "javax.ws.rs.PathParam",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserResource"],
    ).endpoints

    assert [
        (
            endpoint.http_method,
            endpoint.path_template,
            endpoint.declaring_class_name,
        )
        for endpoint in endpoints
    ] == [("GET", "/users/{id}", "example.UserResource")]
    parameters_by_name = {
        parameter.name: parameter for parameter in endpoints[0].parameters
    }
    assert parameters_by_name["id"].source == EndpointParameterSource.PATH
    assert not parameters_by_name["id"].is_synthetic


def test_extract_application_endpoints_jax_rs_superclass_with_type_path_does_not_shadow_subclass_mount() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.BaseUserResource": make_type(
                annotations=['@Path("/api")'],
                is_interface=True,
            ),
            "example.UserResource": make_type(
                annotations=['@Path("/users")'],
                extends_list=["example.BaseUserResource"],
            ),
        },
        methods_by_class={
            "example.BaseUserResource": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@GET", '@Path("{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="long",
                            annotations=['@PathParam("id")'],
                        ),
                    ],
                ),
            },
            "example.UserResource": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@Override"],
                    parameters=[
                        make_callable_parameter(name="id", type_name="long"),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.BaseUserResource"): [
                "javax.ws.rs.GET",
                "javax.ws.rs.Path",
                "javax.ws.rs.PathParam",
            ],
            _java_file_for_class_name("example.UserResource"): [
                "javax.ws.rs.Path",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.BaseUserResource",
            "example.UserResource",
        ],
    ).endpoints

    assert [
        (
            endpoint.http_method,
            endpoint.path_template,
            endpoint.declaring_class_name,
        )
        for endpoint in endpoints
    ] == [("GET", "/users/{id}", "example.UserResource")]
    parameters_by_name = {
        parameter.name: parameter for parameter in endpoints[0].parameters
    }
    assert parameters_by_name["id"].source == EndpointParameterSource.PATH
    assert not parameters_by_name["id"].is_synthetic


def test_extract_application_endpoints_jax_rs_concrete_supertype_with_own_path_keeps_its_mount() -> (
    None
):
    # A concrete @Path supertype keeps its own mount and the subclass does not
    # inherit the supertype's class-level path. The subclass bare override still
    # inherits the supertype's METHOD mapping (Jakarta REST 3.6) and exposes it
    # under the subclass's own mount, so both roots emit their own endpoint.
    analysis = _make_endpoint_analysis(
        classes={
            "example.UsersV1": make_type(
                annotations=['@Path("/v1/users")'],
            ),
            "example.UsersV2": make_type(
                annotations=['@Path("/v2/users")'],
                extends_list=["example.UsersV1"],
            ),
        },
        methods_by_class={
            "example.UsersV1": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@GET", '@Path("{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="long",
                            annotations=['@PathParam("id")'],
                        ),
                    ],
                ),
            },
            "example.UsersV2": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@Override"],
                    parameters=[
                        make_callable_parameter(name="id", type_name="long"),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UsersV1"): [
                "javax.ws.rs.GET",
                "javax.ws.rs.Path",
                "javax.ws.rs.PathParam",
            ],
            _java_file_for_class_name("example.UsersV2"): [
                "javax.ws.rs.Path",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.UsersV1",
            "example.UsersV2",
        ],
    ).endpoints

    assert sorted(
        (
            endpoint.http_method,
            endpoint.path_template,
            endpoint.declaring_class_name,
        )
        for endpoint in endpoints
    ) == [
        ("GET", "/v1/users/{id}", "example.UsersV1"),
        ("GET", "/v2/users/{id}", "example.UsersV2"),
    ]
    v2_endpoint = next(
        endpoint
        for endpoint in endpoints
        if endpoint.declaring_class_name == "example.UsersV2"
    )
    v2_parameters_by_name = {
        parameter.name: parameter for parameter in v2_endpoint.parameters
    }
    assert v2_parameters_by_name["id"].source == EndpointParameterSource.PATH
    assert not v2_parameters_by_name["id"].is_synthetic


def test_extract_application_endpoints_jax_rs_override_with_own_annotation_does_not_inherit() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(is_interface=True),
            "example.UserResource": make_type(
                annotations=['@Path("/users")'],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@GET", '@Path("{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="long",
                            annotations=['@PathParam("id")'],
                        ),
                    ],
                ),
            },
            "example.UserResource": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@GET"],
                    parameters=[
                        make_callable_parameter(name="id", type_name="long"),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UserApi"): [
                "javax.ws.rs.GET",
                "javax.ws.rs.Path",
                "javax.ws.rs.PathParam",
            ],
            _java_file_for_class_name("example.UserResource"): [
                "javax.ws.rs.GET",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserResource"],
    ).endpoints

    assert [
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    ] == [("GET", "/users")]


def test_extract_application_endpoints_jax_rs_consumes_override_does_not_inherit() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(is_interface=True),
            "example.UserResource": make_type(
                annotations=['@Path("/users")'],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@GET", '@Path("{id}")'],
                ),
            },
            "example.UserResource": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=['@Consumes("application/json")'],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UserApi"): [
                "javax.ws.rs.GET",
                "javax.ws.rs.Path",
            ],
            _java_file_for_class_name("example.UserResource"): [
                "javax.ws.rs.Consumes",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserResource"],
    ).endpoints

    assert endpoints == []


def test_extract_application_endpoints_jax_rs_unvalidated_consumes_override_still_inherits() -> (
    None
):
    # A short @Consumes whose per-annotation import validation fails (here an
    # explicit foreign import shadows the JAX-RS one) is not treated as a
    # JAX-RS annotation, so it does not defeat spec-§3.6 inheritance.
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(is_interface=True),
            "example.UserResource": make_type(
                annotations=['@Path("/users")'],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@GET", '@Path("{id}")'],
                ),
            },
            "example.UserResource": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=['@Consumes("application/json")'],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UserApi"): [
                "javax.ws.rs.GET",
                "javax.ws.rs.Path",
            ],
            _java_file_for_class_name("example.UserResource"): [
                "com.example.media.Consumes",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserResource"],
    ).endpoints

    assert [
        (
            endpoint.http_method,
            endpoint.path_template,
            endpoint.declaring_class_name,
        )
        for endpoint in endpoints
    ] == [("GET", "/users/{id}", "example.UserResource")]


def test_extract_application_endpoints_jax_rs_parameter_annotation_override_does_not_inherit() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(is_interface=True),
            "example.UserResource": make_type(
                annotations=['@Path("/users")'],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=["@GET", '@Path("{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="long",
                            annotations=['@PathParam("id")'],
                        ),
                    ],
                ),
            },
            "example.UserResource": {
                "get(long)": make_callable(
                    signature="get(long)",
                    annotations=[],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="long",
                            annotations=['@QueryParam("id")'],
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UserApi"): [
                "javax.ws.rs.GET",
                "javax.ws.rs.Path",
                "javax.ws.rs.PathParam",
            ],
            _java_file_for_class_name("example.UserResource"): [
                "javax.ws.rs.QueryParam",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserResource"],
    ).endpoints

    assert endpoints == []


def test_extract_application_endpoints_jax_rs_suspended_override_does_not_inherit() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.JobApi": make_type(is_interface=True),
            "example.JobResource": make_type(
                annotations=['@Path("/jobs")'],
                implements_list=["example.JobApi"],
            ),
        },
        methods_by_class={
            "example.JobApi": {
                "get(javax.ws.rs.container.AsyncResponse)": make_callable(
                    signature="get(javax.ws.rs.container.AsyncResponse)",
                    annotations=["@GET"],
                    parameters=[
                        make_callable_parameter(
                            name="response",
                            type_name="javax.ws.rs.container.AsyncResponse",
                        ),
                    ],
                ),
            },
            "example.JobResource": {
                "get(javax.ws.rs.container.AsyncResponse)": make_callable(
                    signature="get(javax.ws.rs.container.AsyncResponse)",
                    annotations=[],
                    parameters=[
                        make_callable_parameter(
                            name="response",
                            type_name="javax.ws.rs.container.AsyncResponse",
                            annotations=["@Suspended"],
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.JobApi"): [
                "javax.ws.rs.GET",
            ],
            _java_file_for_class_name("example.JobResource"): [
                "javax.ws.rs.container.Suspended",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.JobApi", "example.JobResource"],
    ).endpoints

    assert endpoints == []


def test_extract_application_endpoints_jax_rs_bare_post_inherits_body_parameter() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserApi": make_type(is_interface=True),
            "example.UserResource": make_type(
                annotations=['@Path("/users")'],
                implements_list=["example.UserApi"],
            ),
        },
        methods_by_class={
            "example.UserApi": {
                "create(example.User)": make_callable(
                    signature="create(example.User)",
                    annotations=["@POST"],
                    parameters=[
                        make_callable_parameter(
                            name="user",
                            type_name="example.User",
                        ),
                    ],
                ),
            },
            "example.UserResource": {
                "create(example.User)": make_callable(
                    signature="create(example.User)",
                    annotations=[],
                    parameters=[
                        make_callable_parameter(
                            name="user",
                            type_name="example.User",
                        ),
                    ],
                ),
            },
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.UserApi"): [
                "javax.ws.rs.POST",
            ],
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserApi", "example.UserResource"],
    ).endpoints

    assert [
        (
            endpoint.http_method,
            endpoint.path_template,
            endpoint.declaring_class_name,
        )
        for endpoint in endpoints
    ] == [("POST", "/users", "example.UserResource")]
    parameters_by_source = {
        parameter.source: parameter for parameter in endpoints[0].parameters
    }
    assert EndpointParameterSource.BODY in parameters_by_source
    assert parameters_by_source[EndpointParameterSource.BODY].type == "example.User"


def test_extract_application_endpoints_micronaut_inherits_methods_from_non_server_abstract_base() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.AbstractController": make_type(),
            "example.UserController": make_type(
                annotations=['@Controller("/users")'],
                extends_list=["example.AbstractController"],
            ),
        },
        methods_by_class={
            "example.AbstractController": {
                "get(java.lang.Long)": make_callable(
                    signature="get(java.lang.Long)",
                    annotations=['@Get("/{id}")'],
                    parameters=[
                        make_callable_parameter(
                            name="id",
                            type_name="java.lang.Long",
                        ),
                    ],
                ),
            },
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.AbstractController",
            "example.UserController",
        ],
    ).endpoints

    assert [
        (
            endpoint.http_method,
            endpoint.path_template,
            endpoint.declaring_class_name,
        )
        for endpoint in endpoints
    ] == [("GET", "/users/{id}", "example.UserController")]


def test_extract_application_endpoints_interface_class_mapping_wins_over_superclass() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.ApiContract": make_type(
                annotations=['@RequestMapping("/iface")'],
                is_interface=True,
            ),
            "example.BaseController": make_type(
                annotations=['@RequestMapping("/base")'],
            ),
            "example.UserController": make_type(
                annotations=["@RestController"],
                extends_list=["example.BaseController"],
                implements_list=["example.ApiContract"],
            ),
        },
        methods_by_class={
            "example.UserController": {
                "getUser(java.lang.String)": make_callable(
                    signature="getUser(java.lang.String)",
                    annotations=['@GetMapping("/{id}")'],
                ),
            },
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.ApiContract",
            "example.BaseController",
            "example.UserController",
        ],
    ).endpoints

    assert [
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    ] == [("GET", "/iface/{id}")]


def test_extract_application_endpoints_supports_fully_qualified_spring_annotations() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserController": make_type(
                annotations=[
                    "@org.springframework.web.bind.annotation.RestController",
                    '@org.springframework.web.bind.annotation.RequestMapping("/api")',
                ]
            )
        },
        methods_by_class={
            "example.UserController": {
                "getUser()": make_callable(
                    signature="getUser()",
                    annotations=[
                        '@org.springframework.web.bind.annotation.GetMapping("/users/{id}")'
                    ],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserController"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/api/users/{id}")}


def test_extract_application_endpoints_rejects_short_name_annotations_without_framework_import_signal() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={
            "example.UserController": make_type(
                annotations=["@Controller", '@RequestMapping("/api")']
            )
        },
        methods_by_class={
            "example.UserController": {
                "getUser()": make_callable(
                    signature="getUser()",
                    annotations=['@GetMapping("/users/{id}")'],
                )
            }
        },
        java_files={
            "example.UserController": "src/main/java/example/UserController.java"
        },
        import_declarations_by_file={
            "src/main/java/example/UserController.java": [
                "example.custom.annotations.Controller",
                "example.custom.annotations.RequestMapping",
                "example.custom.annotations.GetMapping",
            ]
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserController"],
    ).endpoints

    assert endpoints == []


def test_extract_application_endpoints_normalizes_comment_prefixed_annotation_strings() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserController": make_type(
                annotations=[
                    "// Keep class as HTTP controller\n@RestController",
                    '// Base route for user APIs\n@RequestMapping("/api/users")',
                ]
            )
        },
        methods_by_class={
            "example.UserController": {
                "getUser()": make_callable(
                    signature="getUser()",
                    annotations=['// Endpoint route hint\n@GetMapping("/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.UserController"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/api/users/{id}")}


def test_extract_application_endpoints_requires_micronaut_controller_annotation() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={"example.HealthRoutes": make_type()},
        methods_by_class={
            "example.HealthRoutes": {
                "health()": make_callable(
                    signature="health()",
                    annotations=['@Get("/health")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.HealthRoutes"],
    ).endpoints

    assert endpoints == []


def test_extract_application_endpoints_supports_custom_micronaut_controller_like_annotation() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={"example.HealthRoutes": make_type(annotations=["@HealthController"])},
        methods_by_class={
            "example.HealthRoutes": {
                "health()": make_callable(
                    signature="health()",
                    annotations=['@Get("/health")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.HealthRoutes"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/health")}


def test_extract_application_endpoints_excludes_micronaut_client_annotations() -> None:
    analysis = _make_endpoint_analysis(
        classes={
            "example.InventoryClient": make_type(
                annotations=['@Client("/inventory")', '@Controller("/inventory")']
            )
        },
        methods_by_class={
            "example.InventoryClient": {
                "getInventory()": make_callable(
                    signature="getInventory()",
                    annotations=['@Get("/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.InventoryClient"],
    ).endpoints

    assert endpoints == []


def test_extract_application_endpoints_allows_micronaut_server_when_client_annotation_is_only_in_interface() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.InventoryClientContract": make_type(
                annotations=['@Client("/inventory")']
            ),
            "example.InventoryController": make_type(
                annotations=['@Controller("/inventory")'],
                implements_list=["example.InventoryClientContract"],
            ),
        },
        methods_by_class={
            "example.InventoryController": {
                "getInventory()": make_callable(
                    signature="getInventory()",
                    annotations=['@Get("/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=[
            "example.InventoryClientContract",
            "example.InventoryController",
        ],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/inventory/{id}")}


def test_extract_application_endpoints_inherits_micronaut_controller_path_from_interface() -> (
    None
):
    analysis = _make_endpoint_analysis(
        classes={
            "example.InventoryApi": make_type(
                annotations=['@Controller("/inventory")']
            ),
            "example.InventoryController": make_type(
                implements_list=["example.InventoryApi"]
            ),
        },
        methods_by_class={
            "example.InventoryController": {
                "getInventory()": make_callable(
                    signature="getInventory()",
                    annotations=['@Get("/{id}")'],
                )
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.InventoryApi", "example.InventoryController"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {("GET", "/inventory/{id}")}


def test_build_endpoint_coverage_summary_uses_template_matching() -> None:
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/api/users/{id}",
            declaring_class_name="example.UserController",
            declaring_method_signature="getUser()",
        ),
        ApplicationEndpoint(
            framework="spring",
            http_method="POST",
            path_template="/api/orders/{id}",
            declaring_class_name="example.OrderController",
            declaring_method_signature="createOrder()",
        ),
        ApplicationEndpoint(
            framework="spring",
            http_method="DELETE",
            path_template="/api/admin/{id}",
            declaring_class_name="example.AdminController",
            declaring_method_signature="deleteAdmin()",
        ),
    ]

    test_class_analyses = [
        ModelClassAnalysis(
            qualified_class_name="example.ApiTest",
            test_method_analyses=[
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testUserById()",
                        method_declaration="void testUserById()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testUserById()"),
                                http_call=HttpCallSite(
                                    http_method="GET",
                                    path="/api/users/123",
                                    framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                    method_name="getForEntity",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="GET",
                                    path="/api/users/123",
                                    source="call-site",
                                ),
                            )
                        ]
                    ),
                ),
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testOrderCreate()",
                        method_declaration="void testOrderCreate()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testOrderCreate()"),
                                http_call=HttpCallSite(
                                    http_method="POST",
                                    path="/api/orders/9",
                                    framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                    method_name="postForEntity",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="UNKNOWN",
                                    path="/api/orders/9",
                                    source="call-site",
                                ),
                            )
                        ]
                    ),
                ),
            ],
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=test_class_analyses,
    )

    assert coverage.total_application_endpoints == 3
    assert coverage.covered_endpoint_count == 1
    assert coverage.untested_endpoint_count == 2
    assert round(coverage.coverage_ratio, 4) == 0.3333

    endpoint_coverage_by_path = {
        entry.endpoint.path_template: entry for entry in coverage.endpoints
    }

    assert endpoint_coverage_by_path["/api/users/{id}"].covering_test_method_count == 1
    assert endpoint_coverage_by_path["/api/orders/{id}"].covering_test_method_count == 0
    assert endpoint_coverage_by_path["/api/admin/{id}"].covering_test_method_count == 0


def test_build_endpoint_coverage_summary_excludes_non_event_interactions() -> None:
    """BUILDER interactions do not count toward coverage.

    Builder properties are merged into their correlated events upstream, so
    only EVENT interactions drive the coverage metric.
    """
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/api/users/{id}",
            declaring_class_name="example.UserController",
            declaring_method_signature="getUser()",
        )
    ]

    test_class_analyses = [
        ModelClassAnalysis(
            qualified_class_name="example.ApiTest",
            test_method_analyses=[
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testBuilderOnly()",
                        method_declaration="void testBuilderOnly()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testBuilderOnly()"),
                                http_call=HttpCallSite(
                                    http_method="GET",
                                    path="/api/users/1",
                                    framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                    request_role=HttpRequestRole.BUILDER,
                                    method_name="uri",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="GET",
                                    path="/api/users/1",
                                    source="call-site",
                                ),
                            ),
                        ]
                    ),
                )
            ],
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=test_class_analyses,
    )

    assert coverage.covered_endpoint_count == 0
    assert coverage.untested_endpoint_count == 1
    assert coverage.endpoints[0].covering_test_method_count == 0
    assert coverage.endpoints[0].is_covered is False
    assert coverage.coverage_ratio == 0.0


def test_build_endpoint_coverage_summary_excludes_external_url_candidates_from_internal_matching() -> (
    None
):
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/api/users",
            declaring_class_name="example.UserController",
            declaring_method_signature="getUsers()",
        )
    ]

    test_class_analyses = [
        ModelClassAnalysis(
            qualified_class_name="example.ApiTest",
            test_method_analyses=[
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testExternalCall()",
                        method_declaration="void testExternalCall()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testExternalCall()"),
                                http_call=HttpCallSite(
                                    http_method="GET",
                                    path="https://external-host.example/api/users",
                                    framework=HttpDispatchFramework.REST_ASSURED,
                                    method_name="get",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="GET",
                                    path="https://external-host.example/api/users",
                                    source="call-site",
                                ),
                            )
                        ]
                    ),
                )
            ],
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=test_class_analyses,
    )

    assert coverage.covered_endpoint_count == 0
    assert coverage.untested_endpoint_count == 1
    assert coverage.endpoints[0].covering_test_method_count == 0
    assert coverage.endpoints[0].is_covered is False


def test_build_endpoint_coverage_summary_matches_local_and_relative_urls() -> None:
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/api/users/{id}",
            declaring_class_name="example.UserController",
            declaring_method_signature="getUserById()",
        )
    ]

    test_class_analyses = [
        ModelClassAnalysis(
            qualified_class_name="example.ApiTest",
            test_method_analyses=[
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testLocalAbsoluteUrl()",
                        method_declaration="void testLocalAbsoluteUrl()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testLocalAbsoluteUrl()"),
                                http_call=HttpCallSite(
                                    http_method="GET",
                                    path="http://localhost:8080/api/users/42",
                                    framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                    method_name="getForEntity",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="GET",
                                    path="http://localhost:8080/api/users/42",
                                    source="call-site",
                                ),
                            )
                        ]
                    ),
                ),
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testRelativeUrl()",
                        method_declaration="void testRelativeUrl()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testRelativeUrl()"),
                                http_call=HttpCallSite(
                                    http_method="GET",
                                    path="api/users/7",
                                    framework=HttpDispatchFramework.REST_ASSURED,
                                    method_name="get",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="GET",
                                    path="api/users/7",
                                    source="call-site",
                                ),
                            )
                        ]
                    ),
                ),
            ],
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=test_class_analyses,
    )

    assert coverage.covered_endpoint_count == 1
    assert coverage.untested_endpoint_count == 0
    assert coverage.endpoints[0].covering_test_method_count == 2
    assert coverage.endpoints[0].is_covered is True


def test_build_endpoint_coverage_summary_matches_wildcard_paths_after_normalization() -> (
    None
):
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/assets/**",
            declaring_class_name="example.AssetController",
            declaring_method_signature="getAsset()",
        ),
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/files/*.json",
            declaring_class_name="example.FileController",
            declaring_method_signature="getFile()",
        ),
    ]

    test_class_analyses = [
        ModelClassAnalysis(
            qualified_class_name="example.ApiTest",
            test_method_analyses=[
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testAssetWildcard()",
                        method_declaration="void testAssetWildcard()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testAssetWildcard()"),
                                http_call=HttpCallSite(
                                    http_method="GET",
                                    path="http://localhost:8080/assets/css/app.css?download=1",
                                    framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                    method_name="getForEntity",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="GET",
                                    path="http://localhost:8080/assets/css/app.css?download=1",
                                    source="call-site",
                                ),
                            )
                        ]
                    ),
                ),
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testFileWildcard()",
                        method_declaration="void testFileWildcard()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testFileWildcard()"),
                                http_call=HttpCallSite(
                                    http_method="GET",
                                    path="files/report.json",
                                    framework=HttpDispatchFramework.REST_ASSURED,
                                    method_name="get",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="GET",
                                    path="files/report.json",
                                    source="call-site",
                                ),
                            )
                        ]
                    ),
                ),
            ],
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=test_class_analyses,
    )

    assert coverage.covered_endpoint_count == 2
    assert coverage.untested_endpoint_count == 0

    endpoint_coverage_by_path = {
        entry.endpoint.path_template: entry for entry in coverage.endpoints
    }
    assert endpoint_coverage_by_path["/assets/**"].covering_test_method_count == 1
    assert endpoint_coverage_by_path["/files/*.json"].covering_test_method_count == 1


def test_build_endpoint_coverage_summary_excludes_external_wildcard_candidates() -> (
    None
):
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/assets/**",
            declaring_class_name="example.AssetController",
            declaring_method_signature="getAsset()",
        )
    ]

    test_class_analyses = [
        ModelClassAnalysis(
            qualified_class_name="example.ApiTest",
            test_method_analyses=[
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testExternalAssetWildcard()",
                        method_declaration="void testExternalAssetWildcard()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testExternalAssetWildcard()"),
                                http_call=HttpCallSite(
                                    http_method="GET",
                                    path="https://external-host.example/assets/css/app.css?download=1",
                                    framework=HttpDispatchFramework.REST_ASSURED,
                                    method_name="get",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="GET",
                                    path="https://external-host.example/assets/css/app.css?download=1",
                                    source="call-site",
                                ),
                            )
                        ]
                    ),
                )
            ],
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=test_class_analyses,
    )

    assert coverage.covered_endpoint_count == 0
    assert coverage.untested_endpoint_count == 1
    assert coverage.endpoints[0].covering_test_method_count == 0
    assert coverage.endpoints[0].is_covered is False


def test_build_endpoint_coverage_summary_matches_wildcard_production_methods() -> None:
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="UNKNOWN",
            is_method_wildcard=True,
            path_template="/api/users/{id}",
            declaring_class_name="example.UserController",
            declaring_method_signature="routeUser()",
        )
    ]

    test_class_analyses = [
        ModelClassAnalysis(
            qualified_class_name="example.ApiTest",
            test_method_analyses=[
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testGetUser()",
                        method_declaration="void testGetUser()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testGetUser()"),
                                http_call=HttpCallSite(
                                    http_method="GET",
                                    path="/api/users/42",
                                    framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                    method_name="getForEntity",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="GET",
                                    path="/api/users/42",
                                    source="call-site",
                                ),
                            )
                        ]
                    ),
                ),
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testPostUser()",
                        method_declaration="void testPostUser()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testPostUser()"),
                                http_call=HttpCallSite(
                                    http_method="POST",
                                    path="/api/users/42",
                                    framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                    method_name="postForEntity",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="POST",
                                    path="/api/users/42",
                                    source="call-site",
                                ),
                            )
                        ]
                    ),
                ),
            ],
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=test_class_analyses,
    )

    assert coverage.total_application_endpoints == 1
    assert coverage.covered_endpoint_count == 1
    assert coverage.untested_endpoint_count == 0
    assert coverage.endpoints[0].covering_test_method_count == 2


def test_build_endpoint_coverage_summary_excludes_unresolved_production_methods() -> (
    None
):
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="UNKNOWN",
            is_method_wildcard=False,
            path_template="/api/users/{id}",
            declaring_class_name="example.UserController",
            declaring_method_signature="routeUser()",
        )
    ]

    test_class_analyses = [
        ModelClassAnalysis(
            qualified_class_name="example.ApiTest",
            test_method_analyses=[
                ModelMethodAnalysis(
                    identity=MethodIdentity(
                        defining_class_name="example.ApiTest",
                        method_signature="testGetUser()",
                        method_declaration="void testGetUser()",
                    ),
                    http=HttpAnalysis(
                        request_interactions=[
                            HttpRequestInteraction(
                                origin=_test_origin("testGetUser()"),
                                http_call=HttpCallSite(
                                    http_method="GET",
                                    path="/api/users/42",
                                    framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                    method_name="getForEntity",
                                ),
                                endpoint_candidate=EndpointCandidate(
                                    http_method="GET",
                                    path="/api/users/42",
                                    source="call-site",
                                ),
                            )
                        ]
                    ),
                )
            ],
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=test_class_analyses,
    )

    assert coverage.total_application_endpoints == 0
    assert coverage.covered_endpoint_count == 0
    assert coverage.untested_endpoint_count == 0
    assert coverage.endpoints == []


def test_external_url_exclusion_from_coverage_keeps_request_dispatch_decision() -> None:
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/api/users",
            declaring_class_name="example.UserController",
            declaring_method_signature="getUsers()",
        )
    ]

    external_interaction = HttpRequestInteraction(
        origin=_test_origin("testExternalCall()"),
        http_call=HttpCallSite(
            http_method="GET",
            path="https://external-host.example/api/users",
            framework=HttpDispatchFramework.REST_ASSURED,
            method_name="get",
        ),
        endpoint_candidate=EndpointCandidate(
            http_method="GET",
            path="https://external-host.example/api/users",
            source="call-site",
        ),
    )

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            ModelClassAnalysis(
                qualified_class_name="example.ApiTest",
                test_method_analyses=[
                    ModelMethodAnalysis(
                        identity=MethodIdentity(
                            defining_class_name="example.ApiTest",
                            method_signature="testExternalCall()",
                            method_declaration="void testExternalCall()",
                        ),
                        http=HttpAnalysis(request_interactions=[external_interaction]),
                    )
                ],
            )
        ],
    )

    method = make_callable(call_sites=[make_call_site(method_name="get", start_line=1)])
    grouping = build_call_site_grouping(list(method.call_sites))
    nodes = list(grouping.nodes)
    annotate_node_http(
        nodes[0],
        http_method="GET",
        path="https://external-host.example/api/users",
        framework=HttpDispatchFramework.REST_ASSURED,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.ApiTest",
                    method_signature="testExternalCall()",
                ),
                context_class_name="example.ApiTest",
                grouping=grouping,
                method_details=method,
            )
        ]
    )

    result = analyze_request_dispatch(runtime_view=runtime_view)

    assert coverage.covered_endpoint_count == 0
    assert coverage.endpoints[0].covering_test_method_count == 0
    assert result.labels == ["remote-network"]
    assert result.signals == {"remote-network": ["real-http-remote"]}


def test_project_analysis_populates_endpoint_coverage() -> None:
    analysis = _make_endpoint_analysis(
        classes={
            "example.UserController": make_type(
                annotations=["@RestController", '@RequestMapping("/api/users")']
            ),
            "example.ApiTest": make_type(),
        },
        methods_by_class={
            "example.UserController": {
                "getUser()": make_callable(
                    signature="getUser()",
                    annotations=['@GetMapping("/{id}")'],
                )
            },
            "example.ApiTest": {
                "testEndpoint()": make_callable(
                    signature="testEndpoint()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client.TestRestTemplate"
                            ),
                            argument_expr=['"/api/users/1"'],
                        )
                    ],
                )
            },
        },
        java_files={
            "example.UserController": "src/main/java/example/UserController.java",
            "example.ApiTest": "src/test/java/example/ApiTest.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": [
                "org.junit.jupiter.api.Test",
                "org.springframework.boot.test.web.client.TestRestTemplate",
            ],
        },
    )

    project_analysis = ProjectAnalysisInfo(
        analysis=analysis,
        dataset_name="example",
        project_path="/tmp/example",
    ).gather_project_analysis_info()

    assert project_analysis.endpoint_coverage.total_application_endpoints == 1
    assert project_analysis.endpoint_coverage.covered_endpoint_count == 1
    assert project_analysis.endpoint_coverage.coverage_ratio == 1.0

    endpoint_entry = project_analysis.endpoint_coverage.endpoints[0]
    assert endpoint_entry.endpoint.path_template == "/api/users/{id}"
    assert endpoint_entry.covering_test_method_count == 1

    resource_crud_entry = project_analysis.resource_crud.resources[0]
    assert resource_crud_entry.resource_key == "/api/users"
    assert resource_crud_entry.available_operations == [CrudOperation.READ]
    # The resource exposes no write operations, so reading it is not a
    # read-only candidate (read-only requires a writable resource).
    assert resource_crud_entry.read_only_test_count == 0

    resource_sequence = (
        project_analysis.test_class_analyses[0]
        .test_method_analyses[0]
        .http.resource_interaction_sequences[0]
    )
    assert resource_sequence.available_operations == [CrudOperation.READ]
    assert resource_sequence.lifecycle_label == CrudLifecycleLabel.OTHER


def test_http_methods_match_rejects_unknown_observed_method() -> None:
    exact_endpoint = ApplicationEndpoint(
        framework="spring",
        http_method="GET",
        path_template="/api/users/{id}",
        declaring_class_name="example.UserController",
        declaring_method_signature="getUser()",
    )
    wildcard_endpoint = ApplicationEndpoint(
        framework="spring",
        http_method="UNKNOWN",
        is_method_wildcard=True,
        path_template="/api/users/{id}",
        declaring_class_name="example.UserController",
        declaring_method_signature="routeUser()",
    )

    assert _http_methods_match(exact_endpoint, "UNKNOWN") is False
    assert _http_methods_match(wildcard_endpoint, "UNKNOWN") is False


def test_http_methods_match_allows_wildcard_production_method() -> None:
    wildcard_endpoint = ApplicationEndpoint(
        framework="spring",
        http_method="UNKNOWN",
        is_method_wildcard=True,
        path_template="/api/users/{id}",
        declaring_class_name="example.UserController",
        declaring_method_signature="routeUser()",
    )

    assert _http_methods_match(wildcard_endpoint, "GET") is True
    assert _http_methods_match(wildcard_endpoint, "POST") is True


def test_http_methods_match_rejects_unresolved_production_method() -> None:
    unresolved_endpoint = ApplicationEndpoint(
        framework="spring",
        http_method="UNKNOWN",
        is_method_wildcard=False,
        path_template="/api/users/{id}",
        declaring_class_name="example.UserController",
        declaring_method_signature="routeUser()",
    )

    assert _http_methods_match(unresolved_endpoint, "GET") is False
    assert _http_methods_match(unresolved_endpoint, "POST") is False


def test_http_methods_match_exact_match() -> None:
    get_endpoint = ApplicationEndpoint(
        framework="spring",
        http_method="GET",
        path_template="/api/users/{id}",
        declaring_class_name="example.UserController",
        declaring_method_signature="getUser()",
    )
    post_endpoint = ApplicationEndpoint(
        framework="spring",
        http_method="POST",
        path_template="/api/users/{id}",
        declaring_class_name="example.UserController",
        declaring_method_signature="createUser()",
    )

    assert _http_methods_match(get_endpoint, "GET") is True
    assert _http_methods_match(post_endpoint, "GET") is False


def test_template_matches_exact_paths_without_wildcards() -> None:
    assert _template_matches("/api/users", "/api/users") is True
    assert _template_matches("/api/users", "/api/orders") is False


def test_template_matches_single_segment_placeholders() -> None:
    assert _template_matches("/api/users/{id}", "/api/users/123") is True
    assert _template_matches("/api/users/{id}", "/api/users/123/details") is False


def test_template_matches_full_segment_star_without_crossing_slashes() -> None:
    assert _template_matches("/api/*/details", "/api/u1/details") is True
    assert _template_matches("/api/*/details", "/api/u1/x/details") is False


def test_template_matches_within_segment_star_patterns() -> None:
    assert _template_matches("/files/*.json", "/files/report.json") is True
    assert _template_matches("/files/user-*", "/files/user-123") is True
    assert _template_matches("/files/*-raw-*.json", "/files/foo-raw-bar.json") is True
    assert _template_matches("/files/*.json", "/files/report.csv") is False


def test_template_matches_double_star_at_end() -> None:
    assert _template_matches("/assets/**", "/assets") is True
    assert _template_matches("/assets/**", "/assets/") is True
    assert _template_matches("/assets/**", "/assets/css/app.css") is True


def test_template_matches_double_star_matches_root_and_all_paths() -> None:
    assert _template_matches("/**", "/") is True
    assert _template_matches("/**", "/api/users/42") is True


def test_template_matches_double_star_in_middle() -> None:
    assert _template_matches("/api/**/search", "/api/search") is True
    assert _template_matches("/api/**/search", "/api/v1/users/search") is True
    assert _template_matches("/api/**/search", "/api/v1/users/profile") is False


def test_template_matches_normalizes_wildcard_candidates_before_matching() -> None:
    assert (
        _template_matches(
            "/files/*.json",
            "http://localhost/files/report.json?download=1",
        )
        is True
    )


def test_template_matches_mixed_double_star_and_segment_globs() -> None:
    assert (
        _template_matches(
            "/api/**/users/*.json",
            "/api/v1/internal/users/report.json",
        )
        is True
    )
    assert (
        _template_matches(
            "/api/**/users/*.json",
            "/api/v1/internal/users/report.csv",
        )
        is False
    )


def test_template_matches_treats_unsupported_wildcard_like_segments_as_literal() -> (
    None
):
    assert _template_matches("/api/{id", "/api/123") is False
    assert _template_matches("/api/{id", "/api/{id") is True
    assert _template_matches("/api/foo**bar/details", "/api/fooZZbar/details") is False
    assert (
        _template_matches(
            "/api/foo**bar/details",
            "/api/foo**bar/details",
        )
        is True
    )


def test_template_matches_supports_regex_constrained_path_variables() -> None:
    assert _template_matches(r"/api/{id:\d+}", "/api/123") is True
    assert _template_matches(r"/api/{id:\d+}", "/api/abc") is False
    assert _template_matches(r"/api/{id:[0-9]+}", "/api/456") is True
    assert _template_matches(r"/api/{id:[0-9]+}", "/api/abc") is False


def test_template_matches_normalizes_java_source_constraint_escaping() -> None:
    # Templates harvested from Java source carry `\\d` for a regex `\d` and may
    # pad the constraint with whitespace; both normalize before compiling.
    assert _template_matches(r"/api/{id: \\d+}", "/api/123") is True
    assert _template_matches(r"/api/{id: \\d+}", "/api/abc") is False
    assert _template_matches(r"/api/{id:\\d+}", "/api/123") is True
    assert _template_matches("/api/{id: [0-9]+ }", "/api/456") is True


def test_template_matches_embedded_variable_segments() -> None:
    assert _template_matches("/files/{name}.json", "/files/report.json") is True
    assert _template_matches("/files/{name}.json", "/files/report.xml") is False
    assert _template_matches("/files/file-{name}.txt", "/files/file-report.txt") is True
    assert (
        _template_matches("/files/file-{name}.txt", "/files/other-report.txt") is False
    )
    assert _template_matches(r"/api/v{major:\\d+}/users", "/api/v2/users") is True
    assert _template_matches(r"/api/v{major:\\d+}/users", "/api/vX/users") is False


def test_template_matches_treats_spring_catch_all_variables_like_double_star() -> None:
    assert _template_matches("/api/{*rest}", "/api") is True
    assert _template_matches("/api/{*rest}", "/api/value") is True
    assert _template_matches("/api/{*rest}", "/api/value/nested") is True
    assert _template_matches("/api/{*rest}/search", "/api/search") is True
    assert _template_matches("/api/{*rest}/search", "/api/v1/users/search") is True
    assert _template_matches("/api/{*rest}/search", "/api/v1/users/profile") is False


def test_template_matches_rejects_overlapping_repeated_literal_matches() -> None:
    assert _template_matches("/files/*ab*ab", "/files/ab") is False
    assert _template_matches("/files/*foo*foo", "/files/foo") is False
    assert _template_matches("/files/*foo*foo", "/files/foofoo") is True


# Concatenation-truncated paths match templates with one trailing variable.
# Truncation is recorded as extraction-time evidence on the candidate
# (path_truncated), never inferred from the path shape at match time.


def test_template_plain_variable_tail_accepts_only_unconstrained_variables() -> None:
    assert _template_has_plain_variable_tail("/users/{id}") is True
    assert _template_has_plain_variable_tail("/oauth2/authorization/{regId}") is True

    assert _template_has_plain_variable_tail("/users") is False
    assert _template_has_plain_variable_tail("/") is False
    assert _template_has_plain_variable_tail("/files/**") is False
    assert _template_has_plain_variable_tail("/files/{*rest}") is False
    assert _template_has_plain_variable_tail(r"/users/{id:\d+}") is False
    assert _template_has_plain_variable_tail("/users/*") is False
    assert _template_has_plain_variable_tail("/users/{id}/pets") is False


def test_template_matches_truncated_tail_absorbs_single_trailing_variable() -> None:
    assert (
        _template_matches(
            "/users/{id}",
            "/users/",
            observed_has_truncated_tail=True,
        )
        is True
    )
    assert (
        _template_matches(
            "/oauth2/authorization/{registrationId}",
            "/oauth2/authorization/",
            observed_has_truncated_tail=True,
        )
        is True
    )
    # Earlier template variables still bind their own observed segments.
    assert (
        _template_matches(
            "/users/{userId}/orders/{orderId}",
            "/users/7/orders/",
            observed_has_truncated_tail=True,
        )
        is True
    )


def test_template_matches_without_truncation_evidence_stays_strict() -> None:
    assert _template_matches("/users/{id}", "/users/") is False
    assert _template_matches("/users/{id}", "/users") is False


def test_template_matches_truncated_tail_requires_exactly_one_missing_segment() -> None:
    assert (
        _template_matches(
            "/owners/{id}/pets",
            "/owners/",
            observed_has_truncated_tail=True,
        )
        is False
    )
    assert (
        _template_matches(
            "/users/{userId}/{orderId}",
            "/users/",
            observed_has_truncated_tail=True,
        )
        is False
    )


def test_template_matches_truncated_tail_excludes_constrained_variables() -> None:
    assert (
        _template_matches(
            r"/users/{id:\d+}",
            "/users/",
            observed_has_truncated_tail=True,
        )
        is False
    )


def test_template_matches_truncated_tail_does_not_absorb_extra_observed_segments() -> (
    None
):
    assert (
        _template_matches(
            "/{id}",
            "/users/extra/",
            observed_has_truncated_tail=True,
        )
        is False
    )
    assert (
        _template_matches(
            "/users/{id}",
            "/users/7/orders/",
            observed_has_truncated_tail=True,
        )
        is False
    )


def _single_candidate_class_analysis(
    *,
    method_signature: str,
    http_method: str,
    path: str,
    path_truncated: bool = False,
) -> ModelClassAnalysis:
    return ModelClassAnalysis(
        qualified_class_name="example.ApiTest",
        test_method_analyses=[
            ModelMethodAnalysis(
                identity=MethodIdentity(
                    defining_class_name="example.ApiTest",
                    method_signature=method_signature,
                    method_declaration=f"void {method_signature}",
                ),
                http=HttpAnalysis(
                    request_interactions=[
                        HttpRequestInteraction(
                            origin=_test_origin(method_signature),
                            http_call=HttpCallSite(
                                http_method=http_method,
                                path=path,
                                framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                method_name="getForEntity",
                            ),
                            endpoint_candidate=EndpointCandidate(
                                http_method=http_method,
                                path=path,
                                source="call-site",
                                path_truncated=path_truncated,
                            ),
                        )
                    ]
                ),
            )
        ],
    )


def test_build_endpoint_coverage_summary_matches_concatenation_truncated_paths() -> (
    None
):
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/oauth2/authorization/{registrationId}",
            declaring_class_name="example.AuthController",
            declaring_method_signature="authorize()",
        ),
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/owners/{id}/pets",
            declaring_class_name="example.OwnerController",
            declaring_method_signature="listPets()",
        ),
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            _single_candidate_class_analysis(
                method_signature="testAuthorize()",
                http_method="GET",
                path="/oauth2/authorization/",
                path_truncated=True,
            ),
            _single_candidate_class_analysis(
                method_signature="testListPets()",
                http_method="GET",
                path="/owners/",
                path_truncated=True,
            ),
        ],
    )

    endpoint_coverage_by_path = {
        entry.endpoint.path_template: entry for entry in coverage.endpoints
    }

    assert coverage.covered_endpoint_count == 1
    assert (
        endpoint_coverage_by_path["/oauth2/authorization/{registrationId}"].is_covered
        is True
    )
    # Concatenations spanning more than the final segment record only the
    # leading literal and stay unmatched.
    assert endpoint_coverage_by_path["/owners/{id}/pets"].is_covered is False


def test_build_endpoint_coverage_summary_truncated_path_covers_single_variable_sibling() -> (
    None
):
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/users",
            declaring_class_name="example.UserController",
            declaring_method_signature="listUsers()",
        ),
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/users/{id}",
            declaring_class_name="example.UserController",
            declaring_method_signature="getUser()",
        ),
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            _single_candidate_class_analysis(
                method_signature="testUsers()",
                http_method="GET",
                path="/users/",
                path_truncated=True,
            )
        ],
    )

    # The appended value is statically unknown (real segment vs empty/query),
    # so a truncated candidate deliberately covers both the exact collection
    # path and its single-variable sibling; the dual match is the documented
    # residual false-positive surface of the trailing-variable rule.
    assert coverage.covered_endpoint_count == 2


def test_build_endpoint_coverage_summary_literal_trailing_slash_is_not_truncated() -> (
    None
):
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/users",
            declaring_class_name="example.UserController",
            declaring_method_signature="listUsers()",
        ),
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/users/{id}",
            declaring_class_name="example.UserController",
            declaring_method_signature="getUser()",
        ),
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            _single_candidate_class_analysis(
                method_signature="testUsersDirectory()",
                http_method="GET",
                path="/users/",
            )
        ],
    )

    endpoint_coverage_by_path = {
        entry.endpoint.path_template: entry for entry in coverage.endpoints
    }

    # A genuine literal request to "/users/" targets the collection only; the
    # trailing-variable fallback requires extraction-time truncation evidence.
    assert endpoint_coverage_by_path["/users"].is_covered is True
    assert endpoint_coverage_by_path["/users/{id}"].is_covered is False


def test_build_endpoint_coverage_summary_excludes_external_truncated_paths() -> None:
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/users/{id}",
            declaring_class_name="example.UserController",
            declaring_method_signature="getUser()",
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            _single_candidate_class_analysis(
                method_signature="testExternalUsers()",
                http_method="GET",
                path="https://external-host.example/users/",
                path_truncated=True,
            )
        ],
    )

    assert coverage.covered_endpoint_count == 0
    assert coverage.endpoints[0].is_covered is False


def test_build_endpoint_coverage_summary_truncated_path_still_requires_verb_match() -> (
    None
):
    application_endpoints = [
        ApplicationEndpoint(
            framework="spring",
            http_method="GET",
            path_template="/users/{id}",
            declaring_class_name="example.UserController",
            declaring_method_signature="getUser()",
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            _single_candidate_class_analysis(
                method_signature="testDeleteUser()",
                http_method="DELETE",
                path="/users/",
                path_truncated=True,
            )
        ],
    )

    assert coverage.covered_endpoint_count == 0


def test_project_analysis_covers_concatenation_truncated_paths() -> None:
    analysis = _make_endpoint_analysis(
        classes={
            "example.AuthController": make_type(
                annotations=["@RestController", '@RequestMapping("/oauth2")']
            ),
            "example.ApiTest": make_type(),
        },
        methods_by_class={
            "example.AuthController": {
                "authorize()": make_callable(
                    signature="authorize()",
                    annotations=['@GetMapping("/authorization/{registrationId}")'],
                )
            },
            "example.ApiTest": {
                "testAuthorize()": make_callable(
                    signature="testAuthorize()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client.TestRestTemplate"
                            ),
                            argument_expr=['"/oauth2/authorization/" + registrationId'],
                        )
                    ],
                )
            },
        },
        java_files={
            "example.AuthController": "src/main/java/example/AuthController.java",
            "example.ApiTest": "src/test/java/example/ApiTest.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": [
                "org.junit.jupiter.api.Test",
                "org.springframework.boot.test.web.client.TestRestTemplate",
            ],
        },
    )

    project_analysis = ProjectAnalysisInfo(
        analysis=analysis,
        dataset_name="example",
        project_path="/tmp/example",
    ).gather_project_analysis_info()

    assert project_analysis.endpoint_coverage.total_application_endpoints == 1
    assert project_analysis.endpoint_coverage.covered_endpoint_count == 1

    endpoint_entry = project_analysis.endpoint_coverage.endpoints[0]
    assert endpoint_entry.endpoint.path_template == (
        "/oauth2/authorization/{registrationId}"
    )
    assert endpoint_entry.covering_test_method_count == 1


def test_project_analysis_literal_trailing_slash_does_not_cover_variable_tail() -> None:
    analysis = _make_endpoint_analysis(
        classes={
            "example.AuthController": make_type(
                annotations=["@RestController", '@RequestMapping("/oauth2")']
            ),
            "example.ApiTest": make_type(),
        },
        methods_by_class={
            "example.AuthController": {
                "authorize()": make_callable(
                    signature="authorize()",
                    annotations=['@GetMapping("/authorization/{registrationId}")'],
                )
            },
            "example.ApiTest": {
                "testAuthorize()": make_callable(
                    signature="testAuthorize()",
                    annotations=["@Test"],
                    call_sites=[
                        make_call_site(
                            method_name="getForEntity",
                            receiver_type=(
                                "org.springframework.boot.test.web.client.TestRestTemplate"
                            ),
                            argument_expr=['"/oauth2/authorization/"'],
                        )
                    ],
                )
            },
        },
        java_files={
            "example.AuthController": "src/main/java/example/AuthController.java",
            "example.ApiTest": "src/test/java/example/ApiTest.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": [
                "org.junit.jupiter.api.Test",
                "org.springframework.boot.test.web.client.TestRestTemplate",
            ],
        },
    )

    project_analysis = ProjectAnalysisInfo(
        analysis=analysis,
        dataset_name="example",
        project_path="/tmp/example",
    ).gather_project_analysis_info()

    assert project_analysis.endpoint_coverage.total_application_endpoints == 1
    assert project_analysis.endpoint_coverage.covered_endpoint_count == 0
    assert project_analysis.endpoint_coverage.endpoints[0].is_covered is False


def _observed_test_class(
    *observed: tuple[str, str, str, bool],
) -> ModelClassAnalysis:
    """Build a one-test-method analysis whose events carry the given candidates.

    Each tuple is (method_signature, http_method, observed_path, path_truncated).
    """
    return ModelClassAnalysis(
        qualified_class_name="example.ApiTest",
        test_method_analyses=[
            ModelMethodAnalysis(
                identity=MethodIdentity(
                    defining_class_name="example.ApiTest",
                    method_signature=method_signature,
                    method_declaration=f"void {method_signature}",
                ),
                http=HttpAnalysis(
                    request_interactions=[
                        HttpRequestInteraction(
                            origin=_test_origin(method_signature),
                            http_call=HttpCallSite(
                                http_method=http_method,
                                path=observed_path,
                                framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                method_name="exchange",
                            ),
                            endpoint_candidate=EndpointCandidate(
                                http_method=http_method,
                                path=observed_path,
                                source="call-site",
                                path_truncated=path_truncated,
                            ),
                        )
                    ]
                ),
            )
            for method_signature, http_method, observed_path, path_truncated in observed
        ],
    )


def _coverage_by_template(
    coverage: EndpointCoverageSummary,
) -> dict[str, int]:
    return {
        entry.endpoint.path_template: entry.covering_test_method_count
        for entry in coverage.endpoints
    }


# Application-path prefix stripping at coverage match time.


def test_application_path_prefix_strip_covers_endpoint_under_mount() -> None:
    application_endpoints = [
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="GET",
            path_template="/quotes/{symbols}",
            declaring_class_name="example.QuoteResource",
            declaring_method_signature="getQuotes()",
        ),
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="POST",
            path_template="/quotes",
            declaring_class_name="example.QuoteResource",
            declaring_method_signature="createQuote()",
        ),
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            _observed_test_class(
                ("testGet()", "GET", "/rest/quotes/s:0", False),
                ("testPost()", "POST", "/rest/quotes", False),
            )
        ],
        application_path_prefixes=("/rest",),
    )

    counts = _coverage_by_template(coverage)
    assert counts["/quotes/{symbols}"] == 1
    assert counts["/quotes"] == 1


def test_application_path_prefix_strip_ignores_unknown_leading_segment() -> None:
    application_endpoints = [
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="GET",
            path_template="/quotes/{symbols}",
            declaring_class_name="example.QuoteResource",
            declaring_method_signature="getQuotes()",
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            _observed_test_class(("testGet()", "GET", "/other/quotes/s", False))
        ],
        application_path_prefixes=("/rest",),
    )

    assert _coverage_by_template(coverage)["/quotes/{symbols}"] == 0


def test_application_path_prefix_strip_is_segment_wise_not_string_prefix() -> None:
    application_endpoints = [
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="GET",
            path_template="/x/{id}",
            declaring_class_name="example.XResource",
            declaring_method_signature="getX()",
        )
    ]

    # "/api" must not strip "/apiserver/x/1": "apiserver" != "api" segment-wise.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            _observed_test_class(("testGet()", "GET", "/apiserver/x/1", False))
        ],
        application_path_prefixes=("/api",),
    )

    assert _coverage_by_template(coverage)["/x/{id}"] == 0


def test_application_path_prefix_strips_multi_segment_prefix() -> None:
    application_endpoints = [
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="GET",
            path_template="/orders/{id}",
            declaring_class_name="example.OrderResource",
            declaring_method_signature="getOrder()",
        )
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            _observed_test_class(("testGet()", "GET", "/api/v1/orders/7", False))
        ],
        application_path_prefixes=("/api/v1",),
    )

    assert _coverage_by_template(coverage)["/orders/{id}"] == 1


def test_multiple_discovered_prefixes_strip_independently() -> None:
    application_endpoints = [
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="GET",
            path_template="/quotes/{symbols}",
            declaring_class_name="example.QuoteResource",
            declaring_method_signature="getQuotes()",
        ),
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="GET",
            path_template="/accounts/{id}",
            declaring_class_name="example.AccountResource",
            declaring_method_signature="getAccount()",
        ),
    ]

    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            _observed_test_class(
                ("testQuote()", "GET", "/rest/quotes/s", False),
                ("testAccount()", "GET", "/jaxrs/accounts/9", False),
            )
        ],
        application_path_prefixes=("/jaxrs", "/rest"),
    )

    counts = _coverage_by_template(coverage)
    assert counts["/quotes/{symbols}"] == 1
    assert counts["/accounts/{id}"] == 1


def test_direct_match_guard_prevents_cross_application_strip_match() -> None:
    # Endpoint A template /users belongs to app /rest; endpoint B is /rest/users.
    # Observed GET /rest/users matches B DIRECTLY, so the /rest strip must never
    # also credit A.
    endpoint_a = ApplicationEndpoint(
        framework="jax-rs",
        http_method="GET",
        path_template="/users",
        declaring_class_name="example.RestUserResource",
        declaring_method_signature="getUsersA()",
    )
    endpoint_b = ApplicationEndpoint(
        framework="spring",
        http_method="GET",
        path_template="/rest/users",
        declaring_class_name="example.OtherUserController",
        declaring_method_signature="getUsersB()",
    )

    coverage = build_endpoint_coverage_summary(
        application_endpoints=[endpoint_a, endpoint_b],
        test_class_analyses=[
            _observed_test_class(("testUsers()", "GET", "/rest/users", False))
        ],
        application_path_prefixes=("/rest",),
    )

    counts = _coverage_by_template(coverage)
    assert counts["/rest/users"] == 1
    assert counts["/users"] == 0


def test_application_path_strip_preserves_truncated_tail_leniency() -> None:
    application_endpoints = [
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="GET",
            path_template="/quotes/{symbols}",
            declaring_class_name="example.QuoteResource",
            declaring_method_signature="getQuotes()",
        )
    ]

    # The concatenation-truncated observed path is one segment short after the
    # prefix is stripped; the truncated-tail retry absorbs the missing {symbols}.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=application_endpoints,
        test_class_analyses=[
            _observed_test_class(("testGet()", "GET", "/rest/quotes", True))
        ],
        application_path_prefixes=("/rest",),
    )

    assert _coverage_by_template(coverage)["/quotes/{symbols}"] == 1


def test_discovered_application_paths_appear_in_summary() -> None:
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[],
        test_class_analyses=[],
        application_path_prefixes=("/rest", "/jaxrs"),
    )

    assert coverage.discovered_application_paths == ["/jaxrs", "/rest"]


# Unique-suffix fallback for mount prefixes that exist only in deployment
# config (server context roots, test-side base URLs).


def _suffix_fallback_endpoints() -> list[ApplicationEndpoint]:
    return [
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="GET",
            path_template="/apis/registry/v3/search/artifacts",
            declaring_class_name="example.SearchResource",
            declaring_method_signature="search()",
        ),
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="POST",
            path_template="/apis/registry/v3/groups/{groupId}/artifacts",
            declaring_class_name="example.GroupsResource",
            declaring_method_signature="createArtifact()",
        ),
    ]


def test_suffix_fallback_covers_unique_template_with_literal_anchor() -> None:
    coverage = build_endpoint_coverage_summary(
        application_endpoints=_suffix_fallback_endpoints(),
        test_class_analyses=[
            _observed_test_class(
                ("testSearch()", "GET", "/registry/v3/search/artifacts", False),
                ("testCreate()", "POST", "/registry/v3/groups/g1/artifacts", False),
            )
        ],
    )

    counts = _coverage_by_template(coverage)
    assert counts["/apis/registry/v3/search/artifacts"] == 1
    assert counts["/apis/registry/v3/groups/{groupId}/artifacts"] == 1


def test_suffix_fallback_rejects_all_variable_suffix() -> None:
    # Dropping the leading literal leaves {password}/{key}/{notify}, which
    # matches ANY three-segment path and so carries no targeting evidence.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="jax-rs",
                http_method="POST",
                path_template="/builtin-users/{password}/{key}/{notify}",
                declaring_class_name="example.BuiltinUsersResource",
                declaring_method_signature="create()",
            )
        ],
        test_class_analyses=[
            _observed_test_class(
                ("testValidate()", "POST", "/api/admin/validatePassword", False)
            )
        ],
    )

    assert (
        _coverage_by_template(coverage)["/builtin-users/{password}/{key}/{notify}"] == 0
    )


def test_suffix_fallback_requires_literal_dropped_prefix() -> None:
    # A dropped {tenant} would match any base, so the hidden-mount hypothesis
    # has no anchor; the leading variable is a required route segment.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="spring",
                http_method="GET",
                path_template="/{tenant}/users/{id}",
                declaring_class_name="example.UsersController",
                declaring_method_signature="user()",
            )
        ],
        test_class_analyses=[
            _observed_test_class(("testUser()", "GET", "/users/123", False))
        ],
    )

    assert _coverage_by_template(coverage)["/{tenant}/users/{id}"] == 0


def test_suffix_fallback_rejects_mixed_dropped_prefix_with_variable() -> None:
    # Reaching the matching suffix requires dropping both the literal `api`
    # and the variable {tenant}; the variable poisons the whole drop.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="spring",
                http_method="GET",
                path_template="/api/{tenant}/orders/{id}",
                declaring_class_name="example.OrdersController",
                declaring_method_signature="order()",
            )
        ],
        test_class_analyses=[
            _observed_test_class(("testOrder()", "GET", "/orders/123", False))
        ],
    )

    assert _coverage_by_template(coverage)["/api/{tenant}/orders/{id}"] == 0


def test_suffix_fallback_rejects_variable_led_kept_suffix() -> None:
    # The kept suffix {profile}/json re-anchors on nothing concrete: its first
    # observed segment is consumed by the variable, so only the generic tail
    # `json` ties the path to this route.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="spring",
                http_method="POST",
                path_template="/v2/directions/{profile}/json",
                declaring_class_name="example.DirectionsController",
                declaring_method_signature="directions()",
            )
        ],
        test_class_analyses=[
            _observed_test_class(("testProfile()", "POST", "/driving-car/json", False))
        ],
    )

    assert _coverage_by_template(coverage)["/v2/directions/{profile}/json"] == 0


def test_suffix_fallback_allows_variable_after_literal_led_suffix() -> None:
    # A variable inside the kept suffix is fine when the suffix re-anchors on
    # a literal first segment: dropping the `api` mount leaves users/{id}.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="spring",
                http_method="GET",
                path_template="/api/users/{id}",
                declaring_class_name="example.UsersController",
                declaring_method_signature="user()",
            )
        ],
        test_class_analyses=[
            _observed_test_class(("testUser()", "GET", "/users/123", False))
        ],
    )

    assert _coverage_by_template(coverage)["/api/users/{id}"] == 1


def test_suffix_fallback_rejects_generic_tail_after_variable() -> None:
    # /orders/status shares only the generic tail `status` with this route;
    # crediting it would require the variable-led kept suffix {id}/status.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="spring",
                http_method="GET",
                path_template="/api/users/{id}/status",
                declaring_class_name="example.UsersController",
                declaring_method_signature="userStatus()",
            )
        ],
        test_class_analyses=[
            _observed_test_class(("testStatus()", "GET", "/orders/status", False))
        ],
    )

    assert _coverage_by_template(coverage)["/api/users/{id}/status"] == 0


def test_suffix_fallback_requires_unique_endpoint_match() -> None:
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="spring",
                http_method="POST",
                path_template="/v2/directions/export/json",
                declaring_class_name="example.DirectionsController",
                declaring_method_signature="directions()",
            ),
            ApplicationEndpoint(
                framework="spring",
                http_method="POST",
                path_template="/v2/snap/export/json",
                declaring_class_name="example.SnapController",
                declaring_method_signature="snap()",
            ),
        ],
        test_class_analyses=[
            _observed_test_class(("testExport()", "POST", "/export/json", False))
        ],
    )

    counts = _coverage_by_template(coverage)
    assert counts["/v2/directions/export/json"] == 0
    assert counts["/v2/snap/export/json"] == 0


def _duplicate_route_endpoints() -> list[ApplicationEndpoint]:
    # Interface and implementation extraction of the SAME route: identical
    # (http_method, path_template), different declaring classes.
    return [
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="GET",
            path_template="/apis/registry/v3/search/artifacts",
            declaring_class_name="example.SearchResource",
            declaring_method_signature="search()",
        ),
        ApplicationEndpoint(
            framework="jax-rs",
            http_method="GET",
            path_template="/apis/registry/v3/search/artifacts",
            declaring_class_name="example.SearchResourceImpl",
            declaring_method_signature="search()",
        ),
    ]


def test_suffix_fallback_credits_all_duplicates_of_a_single_route() -> None:
    # Duplicate entries of one route are not ambiguity: the fallback credits
    # every entry of the unique matching route, mirroring direct matching.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=_duplicate_route_endpoints(),
        test_class_analyses=[
            _observed_test_class(
                ("testSearch()", "GET", "/registry/v3/search/artifacts", False)
            )
        ],
    )

    assert coverage.covered_endpoint_count == 2
    assert [entry.covering_test_method_count for entry in coverage.endpoints] == [1, 1]


def test_suffix_fallback_distinct_route_alongside_duplicates_blocks_credit() -> None:
    # A second DISTINCT route sharing the suffix keeps the ambiguity guard in
    # force even when one route appears as several duplicate entries.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            *_duplicate_route_endpoints(),
            ApplicationEndpoint(
                framework="spring",
                http_method="GET",
                path_template="/other/v3/search/artifacts",
                declaring_class_name="example.OtherSearchController",
                declaring_method_signature="search()",
            ),
        ],
        test_class_analyses=[
            _observed_test_class(("testSearch()", "GET", "/v3/search/artifacts", False))
        ],
    )

    assert coverage.covered_endpoint_count == 0


def test_suffix_fallback_explicit_and_wildcard_methods_are_distinct_routes() -> None:
    # Same template, but one entry binds GET while the other is a method
    # wildcard: distinct routes, so the ambiguity guard withholds credit.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="spring",
                http_method="GET",
                path_template="/apis/registry/v3/search/artifacts",
                declaring_class_name="example.SearchController",
                declaring_method_signature="search()",
            ),
            ApplicationEndpoint(
                framework="spring",
                http_method="UNKNOWN",
                is_method_wildcard=True,
                path_template="/apis/registry/v3/search/artifacts",
                declaring_class_name="example.WildcardSearchController",
                declaring_method_signature="searchAny()",
            ),
        ],
        test_class_analyses=[
            _observed_test_class(
                ("testSearch()", "GET", "/registry/v3/search/artifacts", False)
            )
        ],
    )

    assert coverage.covered_endpoint_count == 0


def test_suffix_fallback_malformed_regex_constraint_anchors_literally() -> None:
    # {id:bad(} fails to compile as a constraint regex, so its segment matcher
    # degrades to exact literal equality; literalness follows the matcher and
    # the malformed segment anchors the otherwise all-variable suffix.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="spring",
                http_method="GET",
                path_template="/files/{id:bad(}/{name}",
                declaring_class_name="example.FilesController",
                declaring_method_signature="file()",
            )
        ],
        test_class_analyses=[
            _observed_test_class(("testFile()", "GET", "/{id:bad(}/report", False))
        ],
    )

    assert _coverage_by_template(coverage)["/files/{id:bad(}/{name}"] == 1


def test_suffix_fallback_rejects_embedded_variable_segment_as_anchor() -> None:
    # `report-{id}` compiles to an embedded-variable pattern, not the
    # exact-escaped literal, so it cannot lead the kept suffix even though it
    # contains literal text.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="spring",
                http_method="GET",
                path_template="/api/report-{id}/data",
                declaring_class_name="example.ReportsController",
                declaring_method_signature="report()",
            )
        ],
        test_class_analyses=[
            _observed_test_class(("testReport()", "GET", "/report-123/data", False))
        ],
    )

    assert _coverage_by_template(coverage)["/api/report-{id}/data"] == 0


def test_suffix_fallback_skips_truncated_candidates() -> None:
    # Combining the dropped-leading-segments relaxation with the truncated-tail
    # retry would fabricate matches, so truncated candidates never fall back.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=_suffix_fallback_endpoints(),
        test_class_analyses=[
            _observed_test_class(
                ("testSearch()", "GET", "/registry/v3/search/artifacts", True)
            )
        ],
    )

    assert _coverage_by_template(coverage)["/apis/registry/v3/search/artifacts"] == 0


def test_suffix_fallback_requires_two_observed_segments() -> None:
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="spring",
                http_method="POST",
                path_template="/api/injects/search",
                declaring_class_name="example.InjectsController",
                declaring_method_signature="search()",
            )
        ],
        test_class_analyses=[
            _observed_test_class(("testSearch()", "POST", "/search", False))
        ],
    )

    assert _coverage_by_template(coverage)["/api/injects/search"] == 0


def test_suffix_fallback_requires_method_match() -> None:
    coverage = build_endpoint_coverage_summary(
        application_endpoints=_suffix_fallback_endpoints(),
        test_class_analyses=[
            _observed_test_class(
                ("testSearch()", "DELETE", "/registry/v3/search/artifacts", False)
            )
        ],
    )

    assert _coverage_by_template(coverage)["/apis/registry/v3/search/artifacts"] == 0


def test_direct_match_suppresses_suffix_fallback() -> None:
    # The observed path matches endpoint B directly; the suffix fallback must
    # not also credit endpoint A even though A's template ends the same way.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="jax-rs",
                http_method="GET",
                path_template="/apis/search/artifacts",
                declaring_class_name="example.MountedSearchResource",
                declaring_method_signature="searchA()",
            ),
            ApplicationEndpoint(
                framework="spring",
                http_method="GET",
                path_template="/search/artifacts",
                declaring_class_name="example.SearchController",
                declaring_method_signature="searchB()",
            ),
        ],
        test_class_analyses=[
            _observed_test_class(("testSearch()", "GET", "/search/artifacts", False))
        ],
    )

    counts = _coverage_by_template(coverage)
    assert counts["/search/artifacts"] == 1
    assert counts["/apis/search/artifacts"] == 0


def test_application_path_strip_suppresses_suffix_fallback() -> None:
    # The /rest strip already resolves the candidate; ambiguity at the suffix
    # level must not erase that match.
    coverage = build_endpoint_coverage_summary(
        application_endpoints=[
            ApplicationEndpoint(
                framework="jax-rs",
                http_method="GET",
                path_template="/quotes/{symbols}",
                declaring_class_name="example.QuoteResource",
                declaring_method_signature="getQuotes()",
            ),
            ApplicationEndpoint(
                framework="jax-rs",
                http_method="GET",
                path_template="/internal/rest/quotes/{symbols}",
                declaring_class_name="example.InternalQuoteResource",
                declaring_method_signature="getInternalQuotes()",
            ),
        ],
        test_class_analyses=[
            _observed_test_class(("testGet()", "GET", "/rest/quotes/s", False))
        ],
        application_path_prefixes=("/rest",),
    )

    counts = _coverage_by_template(coverage)
    assert counts["/quotes/{symbols}"] == 1
    assert counts["/internal/rest/quotes/{symbols}"] == 0


# @ApplicationPath discovery and normalization.


def test_application_path_discovery_normalizes_and_excludes_root() -> None:
    classes = {
        "example.BareApp": make_type(annotations=['@ApplicationPath("rest")']),
        "example.SlashApp": make_type(annotations=['@ApplicationPath("/rest/")']),
        "example.EmptyApp": make_type(annotations=['@ApplicationPath("")']),
        "example.RootApp": make_type(annotations=['@ApplicationPath("/")']),
        "example.MultiApp": make_type(annotations=['@ApplicationPath("/api/v1")']),
    }
    analysis = _make_endpoint_analysis(classes=classes)

    result = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
    )

    assert isinstance(result, EndpointExtractionResult)
    assert result.application_path_prefixes == ("/api/v1", "/rest")


def test_extract_application_endpoints_result_is_not_a_bare_list() -> None:
    classes = {
        "example.UserController": make_type(
            annotations=["@RestController", '@RequestMapping("/api")']
        )
    }
    analysis = _make_endpoint_analysis(classes=classes)

    result = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
    )

    # Iterating the result directly must fail loudly instead of yielding endpoints.
    with pytest.raises(TypeError):
        list(cast(Any, result))
    assert isinstance(result.endpoints, list)


def test_application_path_discovery_resolves_constant_value() -> None:
    classes = {
        "example.JaxApp": make_type(
            annotations=["@ApplicationPath(JaxApp.BASE)"],
            field_declarations=[_string_constant_field("BASE", '"/rest"')],
        )
    }
    analysis = _make_endpoint_analysis(classes=classes)

    result = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
        constant_resolver=_constant_resolver_for(analysis),
    )

    assert result.application_path_prefixes == ("/rest",)


def test_application_path_discovery_unresolvable_constant_yields_no_prefix() -> None:
    classes = {
        "example.JaxApp": make_type(
            annotations=["@ApplicationPath(JaxApp.BASE)"],
            field_declarations=[
                make_field(
                    type_name="java.lang.String",
                    variables=["BASE"],
                    modifiers=["static", "final"],
                    variable_initializers={"BASE": 'System.getProperty("base")'},
                )
            ],
        )
    }
    analysis = _make_endpoint_analysis(classes=classes)
    result = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
        constant_resolver=_constant_resolver_for(analysis),
    )
    assert result.application_path_prefixes == ()


def test_discovered_application_path_strips_observed_prefix_to_cover_endpoint() -> None:
    classes = {
        "example.RestApplication": make_type(annotations=['@ApplicationPath("/rest")']),
        "example.QuoteResource": make_type(annotations=['@Path("/quotes")']),
    }
    methods_by_class = {
        "example.QuoteResource": {
            "getQuote()": make_callable(
                signature="getQuote()",
                annotations=["@GET", '@Path("/{symbol}")'],
            )
        }
    }
    analysis = _make_endpoint_analysis(
        classes=classes,
        methods_by_class=methods_by_class,
    )

    extraction = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
        constant_resolver=_constant_resolver_for(analysis),
    )
    assert extraction.application_path_prefixes == ("/rest",)
    assert any(
        endpoint.path_template == "/quotes/{symbol}"
        for endpoint in extraction.endpoints
    )

    coverage = build_endpoint_coverage_summary(
        application_endpoints=extraction.endpoints,
        test_class_analyses=[
            _observed_test_class(("testGet()", "GET", "/rest/quotes/IBM", False))
        ],
        application_path_prefixes=extraction.application_path_prefixes,
    )

    assert _coverage_by_template(coverage)["/quotes/{symbol}"] == 1


# Annotation-path constant resolution into endpoint templates.


def test_jax_rs_path_constant_resolves_on_class_and_method() -> None:
    classes = {
        "example.QuoteResource": make_type(
            annotations=["@Path(QuoteResource.BASE)"],
            field_declarations=[
                _string_constant_field("BASE", '"/quotes"'),
                _string_constant_field("DETAIL", '"/{symbol}"'),
            ],
        )
    }
    methods_by_class = {
        "example.QuoteResource": {
            "getQuote()": make_callable(
                signature="getQuote()",
                annotations=["@GET", "@Path(QuoteResource.DETAIL)"],
            )
        }
    }
    analysis = _make_endpoint_analysis(
        classes=classes,
        methods_by_class=methods_by_class,
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
        constant_resolver=_constant_resolver_for(analysis),
    ).endpoints

    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }
    assert ("GET", "/quotes/{symbol}") in endpoint_keys


def test_spring_request_mapping_value_constant_resolves() -> None:
    classes = {
        "example.UserController": make_type(
            annotations=[
                "@RestController",
                "@RequestMapping(value = UserController.BASE)",
            ],
            field_declarations=[_string_constant_field("BASE", '"/api/users"')],
        )
    }
    methods_by_class = {
        "example.UserController": {
            "getUser()": make_callable(
                signature="getUser()",
                annotations=['@GetMapping("/{id}")'],
            )
        }
    }
    analysis = _make_endpoint_analysis(
        classes=classes,
        methods_by_class=methods_by_class,
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
        constant_resolver=_constant_resolver_for(analysis),
    ).endpoints

    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }
    assert ("GET", "/api/users/{id}") in endpoint_keys


def test_unresolvable_path_constant_degrades_to_class_root() -> None:
    classes = {
        "example.QuoteResource": make_type(
            annotations=["@Path(QuoteResource.BASE)"],
            field_declarations=[
                make_field(
                    type_name="java.lang.String",
                    variables=["BASE"],
                    modifiers=["static", "final"],
                    variable_initializers={"BASE": 'System.getProperty("base")'},
                )
            ],
        )
    }
    methods_by_class = {
        "example.QuoteResource": {
            "getQuote()": make_callable(
                signature="getQuote()",
                annotations=["@GET", '@Path("/{symbol}")'],
            )
        }
    }
    analysis = _make_endpoint_analysis(
        classes=classes,
        methods_by_class=methods_by_class,
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
        constant_resolver=_constant_resolver_for(analysis),
    ).endpoints

    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }
    # The unresolvable class constant contributes no class path, so the endpoint
    # degrades to the method @Path alone (today's behavior).
    assert ("GET", "/{symbol}") in endpoint_keys
    assert not any(template.startswith("/rest") for _, template in endpoint_keys)


# Literal+constant concatenation in an annotation path resolves end-to-end.


def _spring_concat_endpoint_keys(method_annotation: str) -> set[tuple[str, str]]:
    classes = {
        "example.UserController": make_type(
            annotations=["@RestController", '@RequestMapping("/api")'],
            field_declarations=[_string_constant_field("SUFFIX", '"/list"')],
        )
    }
    methods_by_class = {
        "example.UserController": {
            "list()": make_callable(
                signature="list()",
                annotations=[method_annotation],
            )
        }
    }
    analysis = _make_endpoint_analysis(
        classes=classes, methods_by_class=methods_by_class
    )
    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
        constant_resolver=_constant_resolver_for(analysis),
    ).endpoints
    return {(endpoint.http_method, endpoint.path_template) for endpoint in endpoints}


def test_spring_get_mapping_literal_plus_constant_resolves_full_template() -> None:
    assert ("GET", "/api/v/list") in _spring_concat_endpoint_keys(
        '@GetMapping("/v" + SUFFIX)'
    )


def test_spring_request_mapping_path_literal_plus_constant_resolves() -> None:
    assert ("GET", "/api/v/list") in _spring_concat_endpoint_keys(
        '@RequestMapping(path = "/v" + SUFFIX, method = RequestMethod.GET)'
    )


def test_spring_array_element_literal_plus_constant_resolves() -> None:
    assert ("GET", "/api/v/list") in _spring_concat_endpoint_keys(
        '@GetMapping({"/v" + SUFFIX})'
    )


def test_jax_rs_path_literal_plus_constant_resolves() -> None:
    classes = {
        "example.QuoteResource": make_type(
            annotations=['@Path("/v" + QuoteResource.SUFFIX)'],
            field_declarations=[_string_constant_field("SUFFIX", '"/list"')],
        )
    }
    methods_by_class = {
        "example.QuoteResource": {
            "list()": make_callable(signature="list()", annotations=["@GET"])
        }
    }
    analysis = _make_endpoint_analysis(
        classes=classes, methods_by_class=methods_by_class
    )
    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
        constant_resolver=_constant_resolver_for(analysis),
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }
    assert ("GET", "/v/list") in endpoint_keys


def test_application_path_literal_plus_constant_resolves_prefix() -> None:
    classes = {
        "example.JaxApp": make_type(
            annotations=['@ApplicationPath("/v" + JaxApp.SUFFIX)'],
            field_declarations=[_string_constant_field("SUFFIX", '"/list"')],
        )
    }
    analysis = _make_endpoint_analysis(classes=classes)
    result = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
        constant_resolver=_constant_resolver_for(analysis),
    )
    assert result.application_path_prefixes == ("/v/list",)


def test_spring_mapping_literal_plus_unresolvable_concat_keeps_leading_literal() -> (
    None
):
    # An unresolvable tail falls through to the literal scan, leaving the leading
    # '/v' head (no false full endpoint beyond today's behavior).
    classes = {
        "example.UserController": make_type(
            annotations=["@RestController", '@RequestMapping("/api")'],
        )
    }
    methods_by_class = {
        "example.UserController": {
            "list()": make_callable(
                signature="list()",
                annotations=['@GetMapping("/v" + dynamicVar)'],
            )
        }
    }
    analysis = _make_endpoint_analysis(
        classes=classes, methods_by_class=methods_by_class
    )
    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=list(classes),
        constant_resolver=_constant_resolver_for(analysis),
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }
    assert ("GET", "/api/v") in endpoint_keys


def test_path_segments_respects_braces_when_splitting() -> None:
    assert _path_segments("/a/{x:y/z}") == ("a", "{x:y/z}")
    assert _path_segments("/books{/id}") == ("books{/id}",)


def test_compile_path_template_segment_matchers_respects_braces_when_splitting() -> (
    None
):
    matchers = _compile_path_template_segment_matchers("/a/{x:y/z}")
    assert len(matchers) == 2


def test_extract_application_endpoints_micronaut_uris_and_rfc6570_templates() -> None:
    analysis = _make_endpoint_analysis(
        classes={
            "example.BookController": make_type(annotations=['@Controller("/books")'])
        },
        methods_by_class={
            "example.BookController": {
                "list()": make_callable(
                    signature="list()",
                    annotations=['@Get(uris = {"/a", "/b"})'],
                ),
                "query()": make_callable(
                    signature="query()",
                    annotations=['@Get("/list{?max,offset}")'],
                ),
                "optionalId()": make_callable(
                    signature="optionalId()",
                    annotations=['@Get("/item{/id}")'],
                ),
            }
        },
    )

    endpoints = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.BookController"],
    ).endpoints
    endpoint_keys = {
        (endpoint.http_method, endpoint.path_template) for endpoint in endpoints
    }

    assert endpoint_keys == {
        ("GET", "/books/a"),
        ("GET", "/books/b"),
        ("GET", "/books/list"),
        ("GET", "/books/item"),
        ("GET", "/books/item/{id}"),
    }

    # The RFC 6570 query expansion `{?max,offset}` is stripped from the path but
    # statically declares optional query parameters, so they surface as synthetic
    # optional query params.
    query_endpoint = next(
        endpoint for endpoint in endpoints if endpoint.path_template == "/books/list"
    )
    query_params_by_name = {
        parameter.name: parameter for parameter in query_endpoint.parameters
    }
    assert set(query_params_by_name) == {"max", "offset"}
    for name in ("max", "offset"):
        parameter = query_params_by_name[name]
        assert parameter.source == EndpointParameterSource.QUERY
        assert parameter.required is False
        assert parameter.is_synthetic is True

    coverage = build_endpoint_coverage_summary(
        application_endpoints=endpoints,
        test_class_analyses=[
            ModelClassAnalysis(
                qualified_class_name="example.BookTest",
                test_method_analyses=[
                    ModelMethodAnalysis(
                        identity=MethodIdentity(
                            defining_class_name="example.BookTest",
                            method_signature="testListBare()",
                            method_declaration="void testListBare()",
                        ),
                        http=HttpAnalysis(
                            request_interactions=[
                                HttpRequestInteraction(
                                    origin=_test_origin("testListBare()"),
                                    http_call=HttpCallSite(
                                        http_method="GET",
                                        path="/books/item",
                                        framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                        method_name="getForEntity",
                                    ),
                                    endpoint_candidate=EndpointCandidate(
                                        http_method="GET",
                                        path="/books/item",
                                        source="call-site",
                                    ),
                                )
                            ]
                        ),
                    ),
                    ModelMethodAnalysis(
                        identity=MethodIdentity(
                            defining_class_name="example.BookTest",
                            method_signature="testListWithId()",
                            method_declaration="void testListWithId()",
                        ),
                        http=HttpAnalysis(
                            request_interactions=[
                                HttpRequestInteraction(
                                    origin=_test_origin("testListWithId()"),
                                    http_call=HttpCallSite(
                                        http_method="GET",
                                        path="/books/item/123",
                                        framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
                                        method_name="getForEntity",
                                    ),
                                    endpoint_candidate=EndpointCandidate(
                                        http_method="GET",
                                        path="/books/item/123",
                                        source="call-site",
                                    ),
                                )
                            ]
                        ),
                    ),
                ],
            )
        ],
    )

    assert coverage.covered_endpoint_count == 2
    covered_templates = {
        entry.endpoint.path_template for entry in coverage.endpoints if entry.is_covered
    }
    assert covered_templates == {"/books/item", "/books/item/{id}"}


def test_extract_application_endpoints_micronaut_rfc6570_query_dedupes_annotated_param() -> (
    None
):
    # A name already bound by @QueryValue is not duplicated by the {?...} synthesis;
    # a bare name present only in the template is synthesized.
    analysis = _make_endpoint_analysis(
        classes={
            "example.BookController": make_type(annotations=['@Controller("/books")'])
        },
        methods_by_class={
            "example.BookController": {
                "query(int, int)": make_callable(
                    signature="query(int, int)",
                    annotations=['@Get("/list{?max,offset}")'],
                    parameters=[
                        make_callable_parameter(
                            name="max",
                            type_name="int",
                            annotations=['@QueryValue("max")'],
                        ),
                        make_callable_parameter(name="offset", type_name="int"),
                    ],
                ),
            }
        },
        import_declarations_by_file={
            _java_file_for_class_name("example.BookController"): [
                "io.micronaut.http.annotation.Controller",
                "io.micronaut.http.annotation.Get",
                "io.micronaut.http.annotation.QueryValue",
            ],
        },
    )
    [endpoint] = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.BookController"],
    ).endpoints

    query_params_by_name = {
        parameter.name: parameter for parameter in endpoint.parameters
    }
    assert set(query_params_by_name) == {"max", "offset"}
    assert all(
        parameter.source == EndpointParameterSource.QUERY
        for parameter in query_params_by_name.values()
    )
    # The @QueryValue-bound `max` is kept (not a synthesized duplicate); `offset`
    # is synthesized from the template.
    assert query_params_by_name["max"].is_synthetic is False
    assert query_params_by_name["offset"].is_synthetic is True
    assert query_params_by_name["offset"].required is False


def test_extract_application_endpoints_micronaut_rfc6570_query_continuation_form() -> (
    None
):
    # RFC 6570 query continuation `{&b}` is also a query parameter (Micronaut's
    # UriTemplate handles `?` and `&` alike). Both must be synthesized as optional
    # query params and neither may leak into the path as a `&b` path segment.
    analysis = _make_endpoint_analysis(
        classes={
            "example.BookController": make_type(annotations=['@Controller("/books")'])
        },
        methods_by_class={
            "example.BookController": {
                "query()": make_callable(
                    signature="query()",
                    annotations=['@Get("/list{?a}{&b}")'],
                ),
            }
        },
    )
    [endpoint] = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.BookController"],
    ).endpoints

    assert endpoint.path_template == "/books/list"
    query_params_by_name = {
        parameter.name: parameter for parameter in endpoint.parameters
    }
    assert set(query_params_by_name) == {"a", "b"}
    for name in ("a", "b"):
        parameter = query_params_by_name[name]
        assert parameter.source == EndpointParameterSource.QUERY
        assert parameter.required is False
        assert parameter.is_synthetic is True


def test_extract_application_endpoints_micronaut_rfc6570_exploded_aggregate_not_synthesized() -> (
    None
):
    # `{?cmd*}` is an exploded aggregate binding a whole object, not a single
    # named query key, so no synthetic query parameter is emitted.
    analysis = _make_endpoint_analysis(
        classes={
            "example.BookController": make_type(annotations=['@Controller("/books")'])
        },
        methods_by_class={
            "example.BookController": {
                "search()": make_callable(
                    signature="search()",
                    annotations=['@Get("/search{?cmd*}")'],
                ),
            }
        },
    )
    [endpoint] = extract_application_endpoints(
        analysis=analysis,
        application_classes=["example.BookController"],
    ).endpoints

    assert endpoint.path_template == "/books/search"
    assert endpoint.parameters == []
