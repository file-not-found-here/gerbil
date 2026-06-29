from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from gerbil.figures.plotting import (
    box_stats,
    drop_unknown_categories,
    grouped_bars,
    new_figure,
    ordered_union,
    prune_zero_categories,
    renormalize_unknown_share,
    save_figure,
    stacked_share_bars,
    summary_boxes,
)

_DIST = {
    "count": 4,
    "min": 0.0,
    "max": 10.0,
    "mean": 3.0,
    "p25": 1.0,
    "p50": 2.0,
    "p75": 4.0,
    "p90": 8.0,
}

_EMPTY_DIST = {
    "count": 0,
    "min": None,
    "max": None,
    "mean": None,
    "p25": None,
    "p50": None,
    "p75": None,
    "p90": None,
}


def test_ordered_union_preserves_first_seen_order() -> None:
    assert ordered_union([["b", "a"], ["a", "c"], ["d"]]) == ["b", "a", "c", "d"]


def test_prune_zero_categories_drops_categories_dead_in_every_series() -> None:
    categories = ["alive", "zero", "none", "partial"]
    series = [
        ("one", "#111111", [1.0, 0.0, None, 0.0]),
        ("two", "#222222", [2.0, 0.0, None, 3.0]),
    ]

    pruned_categories, pruned_series = prune_zero_categories(categories, series)

    assert pruned_categories == ["alive", "partial"]
    assert pruned_series == [
        ("one", "#111111", [1.0, 0.0]),
        ("two", "#222222", [2.0, 3.0]),
    ]


def test_drop_unknown_categories_handles_both_casings() -> None:
    categories = ["GET", "UNKNOWN", "unknown", "POST"]
    series = [("one", "#111111", [40.0, 10.0, 5.0, 45.0])]

    kept_categories, kept_series = drop_unknown_categories(categories, series)

    assert kept_categories == ["GET", "POST"]
    assert kept_series == [("one", "#111111", [40.0, 45.0])]


def test_renormalize_unknown_share_rescales_remaining_shares() -> None:
    categories = ["a", "b", "unknown"]
    series = [
        ("one", "#111111", [40.0, 40.0, 20.0]),
        ("two", "#222222", [25.0, 25.0, 50.0]),
    ]

    kept_categories, rescaled = renormalize_unknown_share(categories, series)

    assert kept_categories == ["a", "b"]
    # Each series is rescaled by 100 / (100 - unknown share).
    assert rescaled[0] == ("one", "#111111", [50.0, 50.0])
    assert rescaled[1] == ("two", "#222222", [50.0, 50.0])


def test_renormalize_unknown_share_keeps_none_and_all_unknown_series() -> None:
    categories = ["a", "b", "unknown"]
    series: list[tuple[str, str, list[float | None]]] = [
        ("sparse", "#111111", [None, 50.0, 50.0]),
        ("all-unknown", "#222222", [0.0, 0.0, 100.0]),
    ]

    kept_categories, rescaled = renormalize_unknown_share(categories, series)

    assert kept_categories == ["a", "b"]
    assert rescaled[0] == ("sparse", "#111111", [None, 100.0])
    # A fully-unknown series has nothing to rescale; zeros stay zeros.
    assert rescaled[1] == ("all-unknown", "#222222", [0.0, 0.0])


def test_box_stats_maps_percentiles_and_rejects_empty_samples() -> None:
    stats = box_stats(_DIST)

    assert stats is not None
    assert stats["med"] == 2.0
    assert stats["q1"] == 1.0
    assert stats["q3"] == 4.0
    # Whiskers span min to p90; the max is intentionally not drawn.
    assert stats["whislo"] == 0.0
    assert stats["whishi"] == 8.0
    assert stats["mean"] == 3.0

    assert box_stats(None) is None
    assert box_stats(_EMPTY_DIST) is None


def test_summary_boxes_leaves_gaps_for_empty_samples() -> None:
    figure, axes = new_figure()
    try:
        summary_boxes(
            axes[0],
            [
                ("full", _DIST, "#111111"),
                ("empty", _EMPTY_DIST, "#222222"),
                ("missing", None, "#333333"),
            ],
        )
        # Only the populated item draws a box patch.
        assert len(axes[0].patches) == 1
        assert [label.get_text() for label in axes[0].get_xticklabels()] == [
            "full",
            "empty",
            "missing",
        ]
    finally:
        plt.close(figure)


def test_grouped_bars_renders_none_as_zero_height() -> None:
    figure, axes = new_figure()
    try:
        grouped_bars(
            axes[0],
            ["a", "b"],
            [("one", "#111111", [5.0, None]), ("two", "#222222", [1.0, 2.0])],
        )
        bars = [patch for patch in axes[0].patches if isinstance(patch, Rectangle)]
        assert [bar.get_height() for bar in bars] == [5.0, 0.0, 1.0, 2.0]
    finally:
        plt.close(figure)


def test_stacked_share_bars_accumulates_left_offsets() -> None:
    figure, axes = new_figure()
    try:
        stacked_share_bars(
            axes[0],
            ["row1", "row2"],
            ["x", "y"],
            [[40.0, 60.0], [None, 30.0]],
            xlabel="% of tests",
        )
        # Two bars per category; the second category starts where the first ended.
        bars = [patch for patch in axes[0].patches if isinstance(patch, Rectangle)]
        assert [bar.get_width() for bar in bars] == [40.0, 0.0, 60.0, 30.0]
        assert [bar.get_x() for bar in bars] == [0.0, 0.0, 40.0, 0.0]
    finally:
        plt.close(figure)


def test_save_figure_writes_every_format_and_closes(tmp_path: Path) -> None:
    figure, _ = new_figure()

    written = save_figure(figure, tmp_path / "nested", "example")

    assert [path.name for path in written] == ["example.png", "example.pdf"]
    assert all(path.is_file() for path in written)
    assert not plt.fignum_exists(figure.number)
