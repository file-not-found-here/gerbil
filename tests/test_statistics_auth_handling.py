from __future__ import annotations

import pytest

from gerbil.statistics import auth_handling as auth_handling_stats
from gerbil.statistics.records import AUTH_HANDLING_LABELS, project_project
from tests.statistics_builders import api_test, non_api_test, project


def test_auth_handling_counts_final_label_over_api_tests() -> None:
    record = project_project(
        project(
            tests=[
                api_test(auth_handling_label="mocked"),
                api_test(auth_handling_label="mocked"),
                api_test(auth_handling_label="real-flow"),
                non_api_test(),
            ]
        )
    )

    result = auth_handling_stats.compute(record.tests)

    assert result["scope"] == "api_tests"
    assert result["test_count"] == 3
    assert tuple(result["label_split"]) == AUTH_HANDLING_LABELS
    assert result["label_split"]["mocked"] == {
        "test_count": 2,
        "pct_of_tests": pytest.approx(100.0 * 2 / 3),
    }
    assert result["label_split"]["real-flow"] == {
        "test_count": 1,
        "pct_of_tests": pytest.approx(100.0 / 3),
    }
    assert result["label_split"]["bypassed"] == {
        "test_count": 0,
        "pct_of_tests": 0.0,
    }


def test_auth_handling_empty_input_keeps_canonical_label_keys() -> None:
    result = auth_handling_stats.compute([])

    assert result["scope"] == "api_tests"
    assert result["test_count"] == 0
    assert tuple(result["label_split"]) == AUTH_HANDLING_LABELS
    assert all(
        entry == {"test_count": 0, "pct_of_tests": None}
        for entry in result["label_split"].values()
    )
