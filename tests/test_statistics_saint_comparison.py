from __future__ import annotations

import pytest

from gerbil.statistics import saint_comparison as saint_comparison_stats
from gerbil.statistics.records import SAINT_CONTEXT_PATH_PREFIXES

from tests.test_statistics_endpoints import make_endpoint


def test_reports_baseline_and_context_path_stripped_payloads() -> None:
    # Two endpoints SAINT never attributes to under the baseline; stripping the
    # context path lets one of them resolve.
    baseline = [
        make_endpoint(covering_test_count=0),
        make_endpoint(covering_test_count=0),
    ]
    stripped = [
        make_endpoint(covering_test_count=2),
        make_endpoint(covering_test_count=0),
    ]

    result = saint_comparison_stats.compute(baseline, stripped)

    assert result["scope"] == "saint_comparison"
    assert result["stripped_context_path_prefixes"] == list(SAINT_CONTEXT_PATH_PREFIXES)
    # Baseline scores zero coverage; stripping recovers one of two endpoints.
    assert result["baseline"]["coverage"]["proportion"] == pytest.approx(0.0)
    assert result["context_path_stripped"]["coverage"]["proportion"] == pytest.approx(
        0.5
    )
    # Both views carry the full coverage decomposition over the same universe.
    assert result["baseline"]["endpoint_count"] == 2
    assert result["context_path_stripped"]["endpoint_count"] == 2
    assert set(result["context_path_stripped"]["coverage_buckets"]) == {
        "no_test",
        "one_to_three",
        "more_than_three",
    }
    assert result["context_path_stripped"]["scope"] == (
        "all_projects_context_path_stripped"
    )


def test_empty_inputs_yield_none_proportions() -> None:
    result = saint_comparison_stats.compute([], [])
    assert result["baseline"]["coverage"]["proportion"] is None
    assert result["context_path_stripped"]["coverage"]["proportion"] is None
