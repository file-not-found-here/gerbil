"""End-to-end request-path recovery on the local smoke fixture: helper-argument
adoption, helper-parameter binding, host-concat literals, static RestAssured
base-path config, and suffix-fallback endpoint matching through the real CLDK
pipeline."""

from __future__ import annotations

from gerbil.analysis.schema import HttpRequestRole, ProjectAnalysis


def _event_candidate_paths_by_test(
    project_analysis: ProjectAnalysis,
) -> dict[tuple[str, str], list[str | None]]:
    paths: dict[tuple[str, str], list[str | None]] = {}
    for test_class in project_analysis.test_class_analyses:
        for test_method in test_class.test_method_analyses:
            key = (
                test_class.qualified_class_name.rsplit(".", 1)[-1],
                test_method.identity.method_signature,
            )
            for interaction in test_method.http.request_interactions:
                call = interaction.http_call
                if call is None or call.request_role != HttpRequestRole.EVENT:
                    continue
                candidate = interaction.endpoint_candidate
                paths.setdefault(key, []).append(
                    candidate.path if candidate is not None else None
                )
    return paths


def test_webtarget_helper_argument_resolves_event_candidate(
    request_path_recovery_smoke_project_analysis: ProjectAnalysis,
) -> None:
    paths = _event_candidate_paths_by_test(request_path_recovery_smoke_project_analysis)
    assert paths[("WebTargetHelperChainTest", "getsWidgetCount()")] == [
        "/v2/widget/count?isActive=true"
    ]


def test_webtarget_helper_chain_appends_compose_with_adopted_argument(
    request_path_recovery_smoke_project_analysis: ProjectAnalysis,
) -> None:
    paths = _event_candidate_paths_by_test(request_path_recovery_smoke_project_analysis)
    assert paths[("WebTargetHelperChainTest", "getsWidgetSummary()")] == [
        "/api/v2/widget/summary"
    ]


def test_webtarget_direct_chain_appended_paths_compose(
    request_path_recovery_smoke_project_analysis: ProjectAnalysis,
) -> None:
    paths = _event_candidate_paths_by_test(request_path_recovery_smoke_project_analysis)
    assert paths[("WebTargetHelperChainTest", "listsWidgetTags()")] == [
        "/api/v2/widget/tags"
    ]


def test_spec_helper_base_with_query_only_event_resolves_candidate(
    request_path_recovery_smoke_project_analysis: ProjectAnalysis,
) -> None:
    paths = _event_candidate_paths_by_test(request_path_recovery_smoke_project_analysis)
    assert paths[("SpecHelperQueryOnlyTest", "listsUserDatasources()")] == [
        "/v1/metadata/datasource?type=UserDatasource"
    ]


def test_helper_parameter_literal_binds_into_helper_dispatch(
    request_path_recovery_smoke_project_analysis: ProjectAnalysis,
) -> None:
    paths = _event_candidate_paths_by_test(request_path_recovery_smoke_project_analysis)
    assert paths[("HelperParameterBindingTest", "pingsRegistry()")] == [
        "/api/registry/ping"
    ]


def test_host_only_literal_yields_path_from_later_literal(
    request_path_recovery_smoke_project_analysis: ProjectAnalysis,
) -> None:
    paths = _event_candidate_paths_by_test(request_path_recovery_smoke_project_analysis)
    assert paths[("HostConcatRestTemplateTest", "listsTopics()")] == [
        "/broker/rest/list"
    ]


def test_suffix_fallback_covers_endpoint_mounted_in_deployment_config(
    request_path_recovery_smoke_project_analysis: ProjectAnalysis,
) -> None:
    coverage = request_path_recovery_smoke_project_analysis.endpoint_coverage
    entries_by_template = {
        entry.endpoint.path_template: entry for entry in coverage.endpoints
    }
    entry = entries_by_template["/apis/registry/v3/search/artifacts"]
    assert entry.covering_test_method_count == 1
    assert entry.covering_test_methods[0].method_signature == (
        "searchesArtifactsUnderConfiguredMount()"
    )


def test_static_base_path_config_composes_into_event_candidate(
    request_path_recovery_smoke_project_analysis: ProjectAnalysis,
) -> None:
    # The literal basePath set by the inherited fixture composes; the dynamic
    # baseURI assignment beside it contributes nothing.
    paths = _event_candidate_paths_by_test(request_path_recovery_smoke_project_analysis)
    assert paths[("StaticBasePathDirectionsTest", "getsDirectionsJson()")] == [
        "/ors/v2/directions/driving-car/json"
    ]


def test_static_base_path_config_yields_direct_endpoint_match(
    request_path_recovery_smoke_project_analysis: ProjectAnalysis,
) -> None:
    coverage = request_path_recovery_smoke_project_analysis.endpoint_coverage
    entries_by_template = {
        entry.endpoint.path_template: entry for entry in coverage.endpoints
    }
    entry = entries_by_template["/ors/v2/directions/{profile}/json"]
    assert entry.covering_test_method_count == 1
    assert entry.covering_test_methods[0].method_signature == "getsDirectionsJson()"
