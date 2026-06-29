from __future__ import annotations

from gerbil.analysis.schema import LifecyclePhase
from gerbil.statistics import project_composition
from gerbil.statistics.records import ProjectStatsRecord, project_project
from tests.statistics_builders import (
    api_test,
    class_analysis,
    endpoint_entry,
    fixture,
    non_api_test,
    project,
)

_QUADRANT_NAMES = (
    "endpoints_and_api_tests",
    "endpoints_no_api_tests",
    "api_tests_no_endpoints",
    "no_endpoints_no_api_tests",
)


def _record(
    *, dataset_name: str, tests: list | None = None, endpoint_count: int = 0
) -> ProjectStatsRecord:
    return project_project(
        project(
            dataset_name=dataset_name,
            tests=tests or [],
            endpoints=[
                endpoint_entry(covering_test_count=0) for _ in range(endpoint_count)
            ],
        )
    )


def _all_quadrant_records() -> list[ProjectStatsRecord]:
    return [
        _record(
            dataset_name="both",
            tests=[api_test(), api_test(), non_api_test()],
            endpoint_count=2,
        ),
        _record(
            dataset_name="endpoints-only",
            tests=[non_api_test()],
            endpoint_count=1,
        ),
        _record(dataset_name="api-tests-only", tests=[api_test()]),
        _record(dataset_name="neither", tests=[non_api_test()]),
        _record(dataset_name="neither-empty"),
    ]


def test_every_quadrant_is_reported_with_project_counts() -> None:
    payload = project_composition.compute(_all_quadrant_records())

    assert payload["scope"] == "all_projects"
    assert payload["project_count"] == 5
    assert tuple(payload["quadrants"]) == _QUADRANT_NAMES
    assert payload["quadrants"]["endpoints_and_api_tests"]["project_count"] == 1
    assert payload["quadrants"]["endpoints_no_api_tests"]["project_count"] == 1
    assert payload["quadrants"]["api_tests_no_endpoints"]["project_count"] == 1
    assert payload["quadrants"]["no_endpoints_no_api_tests"]["project_count"] == 2


def test_untested_projects_are_counted_across_the_whole_dataset() -> None:
    payload = project_composition.compute(_all_quadrant_records())

    # Only the "neither-empty" project carries no tests at all.
    assert payload["projects_with_zero_tests"] == 1


def test_quadrants_report_their_test_corpus_sizes() -> None:
    payload = project_composition.compute(_all_quadrant_records())
    quadrants = payload["quadrants"]

    both = quadrants["endpoints_and_api_tests"]
    assert both["test_count"] == 3
    assert both["api_test_count"] == 2
    assert both["tests_per_project"]["mean"] == 3.0
    assert both["api_tests_per_project"]["mean"] == 2.0

    api_only = quadrants["api_tests_no_endpoints"]
    assert api_only["test_count"] == 1
    assert api_only["api_test_count"] == 1

    neither = quadrants["no_endpoints_no_api_tests"]
    assert neither["project_count"] == 2
    assert neither["test_count"] == 1
    assert neither["api_test_count"] == 0
    # One project has a single non-API test, the other has none.
    assert neither["tests_per_project"]["min"] == 0.0
    assert neither["tests_per_project"]["max"] == 1.0
    assert neither["projects_with_zero_tests"] == 1


def test_zero_test_projects_are_distinguished_from_non_http_tested_ones() -> None:
    payload = project_composition.compute(_all_quadrant_records())
    quadrants = payload["quadrants"]

    # The endpoints-only quadrant holds one project, tested at a non-HTTP layer.
    assert quadrants["endpoints_no_api_tests"]["project_count"] == 1
    assert quadrants["endpoints_no_api_tests"]["projects_with_zero_tests"] == 0
    # The both quadrant project carries tests too.
    assert quadrants["endpoints_and_api_tests"]["projects_with_zero_tests"] == 0
    # The neither quadrant mixes one non-API-tested project with one untested repo.
    assert quadrants["no_endpoints_no_api_tests"]["projects_with_zero_tests"] == 1


def test_endpoints_without_api_tests_still_count_the_non_api_corpus() -> None:
    records = [
        _record(
            dataset_name="srv",
            tests=[non_api_test(), non_api_test()],
            endpoint_count=3,
        )
    ]

    payload = project_composition.compute(records)
    quadrant = payload["quadrants"]["endpoints_no_api_tests"]

    assert quadrant["project_count"] == 1
    assert quadrant["test_count"] == 2
    assert quadrant["api_test_count"] == 0


def test_endpoint_presence_ignores_coverage_counts() -> None:
    # An uncovered endpoint still makes the project an endpoints project.
    records = [_record(dataset_name="srv", endpoint_count=1)]

    payload = project_composition.compute(records)

    assert payload["quadrants"]["endpoints_no_api_tests"]["project_count"] == 1
    assert payload["quadrants"]["no_endpoints_no_api_tests"]["project_count"] == 0


def test_empty_records_zero_every_quadrant() -> None:
    payload = project_composition.compute([])

    assert payload["project_count"] == 0
    assert payload["projects_with_zero_tests"] == 0
    for name in _QUADRANT_NAMES:
        quadrant = payload["quadrants"][name]
        assert quadrant["project_count"] == 0
        assert quadrant["test_count"] == 0
        assert quadrant["api_test_count"] == 0
        assert quadrant["projects_with_zero_tests"] == 0
        assert quadrant["tests_per_project"]["count"] == 0
        assert quadrant["tests_per_project"]["mean"] is None


def _universe_records() -> list[ProjectStatsRecord]:
    """Two API-test projects (one with endpoints, one without) plus two projects
    with no API tests that the universe must exclude."""
    both = project_project(
        project(
            dataset_name="both",
            test_classes=[
                # Two API-test classes; their fixtures sum at the project level.
                class_analysis(
                    qualified_class_name="ApiTests",
                    tests=[
                        api_test(expanded_ncloc=10),
                        api_test(expanded_ncloc=30),
                    ],
                    fixtures=[
                        fixture(LifecyclePhase.SETUP),
                        fixture(LifecyclePhase.TEARDOWN),
                    ],
                ),
                class_analysis(
                    qualified_class_name="ApiHelpers",
                    tests=[api_test(expanded_ncloc=50)],
                    fixtures=[fixture(LifecyclePhase.SETUP)],
                ),
                # A non-API class with its own fixtures: excluded from the
                # per-project fixture total.
                class_analysis(
                    qualified_class_name="UnitTests",
                    tests=[non_api_test()],
                    fixtures=[fixture(LifecyclePhase.SETUP)],
                ),
            ],
            endpoints=[endpoint_entry(covering_test_count=2)],
            application_class_count=5,
            application_method_count=20,
        )
    )
    api_only = project_project(
        project(
            dataset_name="api-only",
            test_classes=[
                class_analysis(
                    qualified_class_name="ApiTests",
                    tests=[api_test(expanded_ncloc=70)],
                    fixtures=[fixture(LifecyclePhase.SETUP)],
                ),
            ],
            application_class_count=3,
            application_method_count=12,
        )
    )
    endpoints_only = project_project(
        project(
            dataset_name="endpoints-only",
            tests=[non_api_test()],
            endpoints=[endpoint_entry(covering_test_count=0)],
            application_class_count=100,
            application_method_count=400,
        )
    )
    neither = project_project(project(dataset_name="neither", tests=[non_api_test()]))
    return [both, api_only, endpoints_only, neither]


def test_api_test_universe_spans_projects_with_api_tests_regardless_of_endpoints() -> (
    None
):
    universe = project_composition.compute(_universe_records())["api_test_universe"]

    assert universe["scope"] == "projects_with_api_tests"
    # Only "both" and "api-only" carry API tests.
    assert universe["project_count"] == 2
    assert universe["api_test_count"] == 4
    assert universe["api_test_class_count"] == 3


def test_api_test_universe_distributions_use_their_named_unit() -> None:
    universe = project_composition.compute(_universe_records())["api_test_universe"]

    # Per project: 3 API tests in "both", 1 in "api-only".
    assert universe["api_tests_per_project"]["min"] == 1.0
    assert universe["api_tests_per_project"]["max"] == 3.0
    assert universe["api_tests_per_project"]["mean"] == 2.0
    # Per project: two API-test classes in "both", one in "api-only".
    assert universe["api_test_classes_per_project"]["min"] == 1.0
    assert universe["api_test_classes_per_project"]["max"] == 2.0
    assert universe["api_test_classes_per_project"]["mean"] == 1.5

    # Per API test: NCLOC of each of the four API tests.
    ncloc = universe["api_test_ncloc"]
    assert ncloc["count"] == 4
    assert ncloc["min"] == 10.0
    assert ncloc["max"] == 70.0
    assert ncloc["mean"] == 40.0

    # Per project: fixtures summed over the API-test classes (both: 2 + 1, with
    # the non-API class's fixture excluded; api-only: 1).
    fixtures = universe["fixtures_per_project"]
    assert fixtures["count"] == 2
    assert fixtures["min"] == 1.0
    assert fixtures["max"] == 3.0
    assert fixtures["mean"] == 2.0


def test_api_test_universe_reports_application_class_and_method_counts() -> None:
    universe = project_composition.compute(_universe_records())["api_test_universe"]

    # Summed over the two API-test projects only (5 + 3, 20 + 12); the
    # endpoints-only project's 100/400 are excluded.
    assert universe["application_class_count"] == 8
    assert universe["application_method_count"] == 32
    assert universe["application_classes_per_project"]["mean"] == 4.0
    assert universe["application_methods_per_project"]["mean"] == 16.0


def test_api_test_universe_reports_endpoint_counts() -> None:
    universe = project_composition.compute(_universe_records())["api_test_universe"]

    # Summed over the two API-test projects only ("both" has 1 endpoint,
    # "api-only" has none); the endpoints-only project's endpoint is excluded.
    assert universe["endpoint_count"] == 1
    endpoints = universe["endpoints_per_project"]
    assert endpoints["count"] == 2
    assert endpoints["min"] == 0.0
    assert endpoints["max"] == 1.0
    assert endpoints["mean"] == 0.5


def test_api_test_universe_is_empty_without_api_tests() -> None:
    universe = project_composition.compute([])["api_test_universe"]

    assert universe["project_count"] == 0
    assert universe["api_test_count"] == 0
    assert universe["api_test_class_count"] == 0
    assert universe["application_class_count"] == 0
    assert universe["application_method_count"] == 0
    assert universe["endpoint_count"] == 0
    assert universe["api_test_ncloc"]["count"] == 0
    assert universe["api_test_ncloc"]["mean"] is None
    assert universe["fixtures_per_project"]["count"] == 0
    assert universe["endpoints_per_project"]["count"] == 0
