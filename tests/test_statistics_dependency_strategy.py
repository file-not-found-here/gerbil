from __future__ import annotations

import pytest

from gerbil.statistics import dependency_strategy as dependency_strategy_stats
from gerbil.statistics.records import project_project
from tests.statistics_builders import api_test, non_api_test, project


def test_dependency_strategy_counts_each_strategy_separately() -> None:
    record = project_project(
        project(
            tests=[
                api_test(dependency_labels=["mocked"]),
                api_test(dependency_labels=["mocked", "containerized"]),
                api_test(dependency_labels=["virtualized"]),
                non_api_test(),
            ]
        )
    )

    result = dependency_strategy_stats.compute(record.tests)

    assert result["scope"] == "api_tests"
    assert result["test_count"] == 3
    assert result["strategy_split"]["mocked"] == {
        "test_count": 2,
        "pct_of_tests": pytest.approx(100.0 * 2 / 3),
    }
    assert result["strategy_split"]["containerized"] == {
        "test_count": 1,
        "pct_of_tests": pytest.approx(100.0 / 3),
    }
    assert result["strategy_split"]["virtualized"] == {
        "test_count": 1,
        "pct_of_tests": pytest.approx(100.0 / 3),
    }
    assert result["multiple_strategy_tests"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }
    assert result["strategy_label_count_per_test"]["mean"] == pytest.approx(4 / 3)


def test_dependency_strategy_empty_input_keeps_canonical_strategy_keys() -> None:
    result = dependency_strategy_stats.compute([])

    assert result["test_count"] == 0
    assert set(result["strategy_split"]) == {
        "mocked",
        "virtualized",
        "containerized",
    }
    assert result["strategy_split"]["mocked"] == {
        "test_count": 0,
        "pct_of_tests": None,
    }
    assert result["multiple_strategy_tests"] == {
        "count": 0,
        "total": 0,
        "proportion": None,
    }
