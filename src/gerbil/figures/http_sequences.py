"""Figures for the http_test_sequence_distribution statistics."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from matplotlib.figure import Figure

from gerbil.figures.plotting import (
    ComparisonPayloads,
    FigureOptions,
    SeriesColors,
    add_box_note,
    add_series_legend,
    category_colors,
    grouped_bars,
    hide_axes,
    new_figure,
    summary_boxes,
)

DISTRIBUTION_LABELS = {
    "sequence_count_per_test": "Sequences per test",
    "sequence_length": "Sequence length",
    "request_side_sequence_length": "Request-side length",
    "response_side_sequence_length": "Response-side length",
    "http_assertion_count_per_test": "HTTP assertions per test",
    "sequenced_response_check_count_per_test": "Response checks per test",
    "distinct_endpoint_count_per_test": "Distinct endpoints per test",
}

SHARE_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("tests_with_multiple_sequences", "multiple sequences"),
    ("tests_with_repeated_sequence", "repeated sequence"),
    ("tests_with_shared_sequence", "shared sequence"),
)

# B/D/V abbreviate Build/Dispatch/Verification; the key is spelled out in the
# figure caption. Short labels keep the wedge callouts from dwarfing the pie.
SHAPE_LABELS = {
    "build-dispatch-verification": "B + D + V",
    "build-dispatch-no-verification": "B + D",
    "dispatch-only": "D",
    "dispatch-verification-no-build": "D + V",
}


def build_distributions(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Summary boxes for every per-test and per-sequence distribution."""
    keys = list(DISTRIBUTION_LABELS)
    ncols = 4
    nrows = max(math.ceil(len(keys) / ncols), 1)
    figure, axes = new_figure(
        ncols=ncols,
        nrows=nrows,
        panel_width=3.4,
        panel_height=3.0,
    )
    for ax, key in zip(axes, keys):
        items = [
            (name, payload.get(key), colors[name]) for name, payload in payloads.items()
        ]
        summary_boxes(ax, items, show_tick_labels=False)
        ax.set_title(DISTRIBUTION_LABELS[key], fontsize=12)
    hide_axes(axes[len(keys) :])
    add_series_legend(figure, {name: colors[name] for name in payloads})
    add_box_note(figure)
    return figure


def build_shares(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Shares of tests with multiple, repeated, and shared sequences."""
    categories = [label for _, label in SHARE_CATEGORIES]
    series = [
        (
            name,
            colors[name],
            [
                (payload[key]["proportion"] or 0.0) * 100.0
                for key, _ in SHARE_CATEGORIES
            ],
        )
        for name, payload in payloads.items()
    ]
    figure, axes = new_figure(panel_width=6.4)
    grouped_bars(axes[0], categories, series, ylabel="% of API tests")
    add_series_legend(figure, {name: colors[name] for name in payloads})
    return figure


def build_dev_sequence_shapes(
    payload: Mapping[str, Any], options: FigureOptions
) -> Figure:
    """Pie chart of classified HTTP sequence-shape labels for the dev corpus."""
    shape = payload["sequence_shape_distribution"]
    labels = [label for label, entry in shape["labels"].items() if entry["count"]]
    figure, axes = new_figure(panel_width=6.4, panel_height=5.2)
    ax = axes[0]
    if not labels:
        ax.text(0.5, 0.5, "No classified sequences", ha="center", va="center")
        ax.set_axis_off()
        return figure

    colors = category_colors(labels)
    values = [shape["labels"][label]["count"] for label in labels]
    display_labels = [SHAPE_LABELS.get(label, label) for label in labels]
    # Labels are placed via leader-line annotations below, not by the pie, so
    # the only visible text textprops styles is the autopct percentages.
    wedges = ax.pie(
        values,
        colors=[colors[label] for label in labels],
        autopct="%1.1f%%",
        startangle=90,
        counterclock=False,
        pctdistance=0.72,
        wedgeprops={"edgecolor": "white", "linewidth": 1.0},
        textprops={"fontsize": 14, "fontweight": "bold"},
    )[0]
    ax.set_aspect("equal")
    # Drop the (white) axes frame and patch so the tight-bbox save crops to the
    # pie and its callouts instead of padding out the empty axes rectangle.
    ax.set_frame_on(False)
    ax.patch.set_visible(False)

    # Leader lines from each wedge out to a label anchored on the matching side,
    # so the reader never has to guess which slice a label describes.
    label_props = {"arrowstyle": "-", "color": "0.4"}
    for wedge, display_label in zip(wedges, display_labels):
        mid_angle = (wedge.theta2 - wedge.theta1) / 2.0 + wedge.theta1
        x = math.cos(math.radians(mid_angle))
        y = math.sin(math.radians(mid_angle))
        on_right = x >= 0
        ax.annotate(
            display_label,
            xy=(x, y),
            xytext=(1.3 if on_right else -1.3, 1.25 * y),
            ha="left" if on_right else "right",
            va="center",
            fontsize=15,
            arrowprops={
                **label_props,
                "connectionstyle": f"angle,angleA=0,angleB={mid_angle}",
            },
        )
    return figure
