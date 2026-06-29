"""Figures for the request_dispatch_distribution statistics."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from matplotlib.figure import Figure

from gerbil.figures.plotting import (
    UNKNOWN_CATEGORIES,
    ComparisonPayloads,
    FigureOptions,
    SeriesColors,
    add_box_note,
    add_series_legend,
    grouped_bars,
    hide_axes,
    new_figure,
    ordered_union,
    renormalize_unknown_share,
    series_colors,
    summary_boxes,
)

MULTIPLE_LABELS = "multiple labels"

METRIC_LABELS = {
    "expanded_ncloc": "NCLOC (expanded)",
    "expanded_cyclomatic_complexity": "Cyclomatic complexity (expanded)",
    "expanded_objects_created": "Objects created (expanded)",
    "expanded_helper_method_count": "Helper methods (expanded)",
    "mocked_interaction_count": "Mocked interactions",
    "dependency_strategy_label_count": "Dependency strategy labels",
}


def _labels(payload: Mapping[str, Any], options: FigureOptions) -> list[str]:
    labels = list(payload["per_label"])
    if options.ignore_unknown:
        labels = [label for label in labels if label not in UNKNOWN_CATEGORIES]
    return labels


def build_labels(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Grouped bars of the request-dispatch label split across directories."""
    categories = ordered_union(
        list(payload["label_split"]) for payload in payloads.values()
    )

    def values(payload: Mapping[str, Any]) -> list[float | None]:
        split = [
            payload["label_split"].get(category, {}).get("pct_of_labeled_tests") or 0.0
            for category in categories
        ]
        multiple = (payload["multiple_label_tests"]["proportion"] or 0.0) * 100.0
        return [*split, multiple]

    series = [
        (name, colors[name], values(payload)) for name, payload in payloads.items()
    ]
    categories = [*categories, MULTIPLE_LABELS]
    ylabel = "% of labeled API tests"
    if options.ignore_unknown:
        # The multiple-labels share has the same denominator, so it rescales too.
        categories, series = renormalize_unknown_share(categories, series)
        ylabel = "% of classified API tests"
    figure, axes = new_figure(panel_width=6.4)
    grouped_bars(axes[0], categories, series, ylabel=ylabel)
    add_series_legend(figure, {name: colors[name] for name in payloads})
    return figure


def build_dev_label_metrics(
    payload: Mapping[str, Any], options: FigureOptions
) -> Figure:
    """Per-metric summary boxes split by dispatch label (dev only)."""
    labels = _labels(payload, options)
    metrics = ordered_union(
        list(payload["per_label"][label]["metrics"]) for label in labels
    )
    colors = series_colors(labels)
    ncols = 3
    nrows = max(math.ceil(len(metrics) / ncols), 1)
    figure, axes = new_figure(ncols=ncols, nrows=nrows)
    for ax, metric in zip(axes, metrics):
        items = [
            (label, payload["per_label"][label]["metrics"].get(metric), colors[label])
            for label in labels
        ]
        summary_boxes(ax, items, show_tick_labels=False)
        ax.set_title(METRIC_LABELS.get(metric, metric), fontsize=12)
    hide_axes(axes[len(metrics) :])
    add_series_legend(figure, colors)
    add_box_note(figure)
    return figure


def build_dev_label_outcomes(
    payload: Mapping[str, Any], options: FigureOptions
) -> Figure:
    """Status-range means and resource-lifecycle shares by dispatch label (dev only)."""
    labels = _labels(payload, options)
    colors = series_colors(labels)
    ranges = ordered_union(
        list(payload["per_label"][label]["status_range_counts"]) for label in labels
    )
    if options.ignore_unknown:
        ranges = [
            range_key for range_key in ranges if range_key not in UNKNOWN_CATEGORIES
        ]
    figure, axes = new_figure(ncols=2, panel_width=5.4)
    range_series = [
        (
            label,
            colors[label],
            [
                payload["per_label"][label]["status_range_counts"][range_key]["mean"]
                for range_key in ranges
            ],
        )
        for label in labels
    ]
    grouped_bars(
        axes[0], ranges, range_series, ylabel="mean status assertions per test"
    )
    axes[0].set_title("Status-range assertions", fontsize=12)

    lifecycle_categories = ["read after write", "cleanup delete"]
    lifecycle_series = [
        (
            label,
            colors[label],
            [
                (
                    payload["per_label"][label]["resource_lifecycle"][key]["proportion"]
                    or 0.0
                )
                * 100.0
                for key in ("has_read_after_write", "has_cleanup_delete")
            ],
        )
        for label in labels
    ]
    grouped_bars(axes[1], lifecycle_categories, lifecycle_series, ylabel="% of tests")
    axes[1].set_title("Resource lifecycle signals", fontsize=12)
    add_series_legend(figure, colors)
    return figure
