from __future__ import annotations

import math

import pytest

from gerbil.statistics.distributions import share, summarize


def test_summarize_matches_linear_interpolation_percentiles() -> None:
    dist = summarize([1, 2, 3, 4])

    assert dist.count == 4
    assert dist.min == 1.0
    assert dist.max == 4.0
    assert dist.mean == 2.5
    # NumPy/R-7 linear interpolation over [1, 2, 3, 4].
    assert dist.p25 == pytest.approx(1.75)
    assert dist.p50 == pytest.approx(2.5)
    assert dist.p75 == pytest.approx(3.25)
    assert dist.p90 == pytest.approx(3.7)


def test_summarize_is_order_independent() -> None:
    assert summarize([4, 1, 3, 2]).to_dict() == summarize([1, 2, 3, 4]).to_dict()


def test_summarize_single_value_collapses_all_percentiles() -> None:
    dist = summarize([5])

    assert dist.count == 1
    for value in (
        dist.min,
        dist.max,
        dist.mean,
        dist.p25,
        dist.p50,
        dist.p75,
        dist.p90,
    ):
        assert value == 5.0


def test_summarize_empty_sample_is_all_none() -> None:
    dist = summarize([])

    assert dist.count == 0
    assert dist.to_dict() == {
        "count": 0,
        "min": None,
        "max": None,
        "mean": None,
        "p25": None,
        "p50": None,
        "p75": None,
        "p90": None,
    }


def test_summarize_percentile_lands_exactly_on_a_sample_point() -> None:
    # With 5 points the 50th percentile rank is an integer index (no interpolation).
    dist = summarize([10, 20, 30, 40, 50])

    assert dist.p50 == 30.0
    assert dist.p25 == 20.0
    assert dist.p75 == 40.0
    assert dist.p90 == pytest.approx(46.0)


def test_share_counts_true_values() -> None:
    result = share([True, False, True])

    assert result.count == 2
    assert result.total == 3
    assert result.proportion == pytest.approx(2 / 3)


def test_share_empty_sample_has_none_proportion() -> None:
    result = share([])

    assert result.to_dict() == {"count": 0, "total": 0, "proportion": None}


def test_share_all_true_is_one() -> None:
    result = share([True, True])

    assert result.proportion == pytest.approx(1.0)
    assert not math.isnan(result.proportion or 0.0)
