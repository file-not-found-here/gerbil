from __future__ import annotations

import pytest

from gerbil.analysis.schema import (
    CallSiteOriginKind,
    HttpDispatchFramework,
    HttpRequestInteraction,
    HttpRequestRole,
    TestingFramework,
)
from gerbil.statistics import frameworks as frameworks_stats
from gerbil.statistics.records import HTTP_DISPATCH_FRAMEWORKS, project_project
from tests.statistics_builders import (
    api_test,
    class_analysis,
    origin,
    project,
    request_interaction,
)


def test_testing_framework_split_counts_classes_per_framework() -> None:
    record = project_project(
        project(
            test_classes=[
                class_analysis(
                    qualified_class_name="A",
                    testing_frameworks=[
                        TestingFramework.JUNIT5,
                        TestingFramework.ASSERTJ,
                    ],
                ),
                class_analysis(
                    qualified_class_name="B",
                    testing_frameworks=[TestingFramework.JUNIT5],
                ),
                class_analysis(qualified_class_name="C"),
            ]
        )
    )

    result = frameworks_stats.compute_testing_framework_distribution(
        record.test_classes
    )

    assert result["scope"] == "test_classes"
    assert result["test_class_count"] == 3
    assert result["framework_split"]["junit5"] == {
        "class_count": 2,
        "pct_of_classes": pytest.approx(100.0 * 2 / 3),
    }
    assert result["framework_split"]["assertj"] == {
        "class_count": 1,
        "pct_of_classes": pytest.approx(100.0 / 3),
    }
    assert result["framework_split"]["mockito"] == {
        "class_count": 0,
        "pct_of_classes": 0.0,
    }
    assert result["classes_without_frameworks"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }
    assert result["framework_count_per_class"]["mean"] == pytest.approx(1.0)


def test_testing_framework_category_split_counts_class_once_per_category() -> None:
    record = project_project(
        project(
            test_classes=[
                class_analysis(
                    qualified_class_name="A",
                    testing_frameworks=[
                        TestingFramework.ASSERTJ,
                        TestingFramework.HAMCREST,
                    ],
                ),
                class_analysis(
                    qualified_class_name="B",
                    testing_frameworks=[
                        TestingFramework.JUNIT4,
                        TestingFramework.MOCKITO,
                        TestingFramework.SPRING_TEST,
                    ],
                ),
            ]
        )
    )

    result = frameworks_stats.compute_testing_framework_distribution(
        record.test_classes
    )

    category_split = result["category_split"]
    # Two assertion libraries on one class still count the class once.
    assert category_split["assertion-library"]["class_count"] == 1
    assert category_split["assertion-library"]["pct_of_classes"] == pytest.approx(50.0)
    assert category_split["test-runner"]["class_count"] == 1
    assert category_split["mocking-library"]["class_count"] == 1
    assert category_split["api-test-framework"]["class_count"] == 1
    assert category_split["test-runner"]["frameworks"] == [
        "junit3",
        "junit4",
        "junit5",
        "testng",
    ]


def test_testing_framework_categories_partition_the_enum() -> None:
    members = [
        framework
        for _, category_members in frameworks_stats.TESTING_FRAMEWORK_CATEGORIES
        for framework in category_members
    ]

    assert len(members) == len(set(members))
    assert set(members) == set(TestingFramework)


def test_testing_framework_empty_input_keeps_canonical_keys() -> None:
    result = frameworks_stats.compute_testing_framework_distribution([])

    assert result["test_class_count"] == 0
    assert set(result["framework_split"]) == {
        framework.value for framework in TestingFramework
    }
    assert result["framework_split"]["junit5"] == {
        "class_count": 0,
        "pct_of_classes": None,
    }
    assert set(result["category_split"]) == {
        "test-runner",
        "assertion-library",
        "mocking-library",
        "api-test-framework",
    }
    assert result["classes_without_frameworks"]["proportion"] is None


def _dispatch_record():
    return project_project(
        project(
            tests=[
                api_test(
                    request_interactions=[
                        request_interaction(
                            CallSiteOriginKind.TEST_METHOD, HttpRequestRole.EVENT
                        ),
                        request_interaction(
                            CallSiteOriginKind.TEST_METHOD, HttpRequestRole.BUILDER
                        ),
                        request_interaction(
                            CallSiteOriginKind.TEST_METHOD,
                            HttpRequestRole.EVENT,
                            framework=HttpDispatchFramework.OKHTTP,
                        ),
                        # No resolved call site: contributes to no framework.
                        HttpRequestInteraction(
                            origin=origin(CallSiteOriginKind.TEST_METHOD)
                        ),
                    ]
                ),
                api_test(
                    request_interactions=[
                        request_interaction(
                            CallSiteOriginKind.FIXTURE,
                            HttpRequestRole.EVENT,
                            framework=HttpDispatchFramework.REST_ASSURED,
                        ),
                    ]
                ),
            ]
        )
    )


def test_http_dispatch_framework_split_counts_call_sites() -> None:
    record = _dispatch_record()

    result = frameworks_stats.compute_http_dispatch_framework_distribution(record.tests)

    assert result["scope"] == "http_call_sites"
    assert result["call_site_count"] == 4
    # Builders and events both count as call sites.
    assert result["framework_split"]["mockmvc"] == {
        "call_site_count": 2,
        "pct_of_call_sites": pytest.approx(50.0),
    }
    assert result["framework_split"]["okhttp"] == {
        "call_site_count": 1,
        "pct_of_call_sites": pytest.approx(25.0),
    }
    assert result["framework_split"]["rest-assured"] == {
        "call_site_count": 1,
        "pct_of_call_sites": pytest.approx(25.0),
    }
    assert result["framework_split"]["webclient"] == {
        "call_site_count": 0,
        "pct_of_call_sites": 0.0,
    }


def test_http_dispatch_framework_event_split_excludes_builders() -> None:
    record = _dispatch_record()

    result = frameworks_stats.compute_http_dispatch_framework_event_distribution(
        record.tests
    )

    assert result["scope"] == "http_dispatch_events"
    assert result["event_count"] == 3
    # The mockmvc builder call site does not count as a dispatched event.
    assert result["framework_split"]["mockmvc"] == {
        "event_count": 1,
        "pct_of_events": pytest.approx(100.0 / 3),
    }
    assert result["framework_split"]["okhttp"] == {
        "event_count": 1,
        "pct_of_events": pytest.approx(100.0 / 3),
    }
    assert result["framework_split"]["rest-assured"] == {
        "event_count": 1,
        "pct_of_events": pytest.approx(100.0 / 3),
    }


def test_http_dispatch_framework_empty_input_keeps_canonical_keys() -> None:
    result = frameworks_stats.compute_http_dispatch_framework_distribution([])

    assert result["call_site_count"] == 0
    assert set(result["framework_split"]) == set(HTTP_DISPATCH_FRAMEWORKS)
    assert result["framework_split"]["mockmvc"] == {
        "call_site_count": 0,
        "pct_of_call_sites": None,
    }


def test_http_dispatch_framework_event_empty_input_keeps_canonical_keys() -> None:
    result = frameworks_stats.compute_http_dispatch_framework_event_distribution([])

    assert result["event_count"] == 0
    assert set(result["framework_split"]) == set(HTTP_DISPATCH_FRAMEWORKS)
    assert result["framework_split"]["mockmvc"] == {
        "event_count": 0,
        "pct_of_events": None,
    }
