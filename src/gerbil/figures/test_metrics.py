"""Figures for the test_metric_comparison statistics."""

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
    hide_axes,
    new_figure,
    ordered_union,
    series_colors,
    summary_boxes,
)

METRIC_LABELS = {
    "expanded_ncloc": "NCLOC (expanded)",
    "expanded_cyclomatic_complexity": "Cyclomatic complexity (expanded)",
    "expanded_helper_method_count": "Helper methods (expanded)",
    "expanded_objects_created": "Objects created (expanded)",
    "expanded_assertion_count": "Assertions (expanded)",
}

COHORT_LABELS = {
    "api": "API tests",
    "non_api": "non-API tests",
    "controller_unit_test": "controller unit tests",
}


def _metric_label(metric: str) -> str:
    return METRIC_LABELS.get(metric, metric)


def build_api_metrics(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Per-metric summary boxes over API tests, one box per directory."""
    metrics = ordered_union(
        list(payload["comparisons"]) for payload in payloads.values()
    )
    ncols = 2
    nrows = max(math.ceil(len(metrics) / ncols), 1)
    figure, axes = new_figure(ncols=ncols, nrows=nrows)
    for ax, metric in zip(axes, metrics):
        items = [
            (name, payload["comparisons"][metric].get("api"), colors[name])
            for name, payload in payloads.items()
        ]
        summary_boxes(ax, items, show_tick_labels=False)
        ax.set_title(_metric_label(metric), fontsize=12)
    hide_axes(axes[len(metrics) :])
    add_series_legend(figure, {name: colors[name] for name in payloads})
    add_box_note(figure)
    return figure


def build_dev_metric_breakdown(
    payload: Mapping[str, Any], options: FigureOptions
) -> Figure:
    """Per-metric summary boxes split by API / non-API / controller-unit cohorts."""
    metrics = list(payload["comparisons"])
    cohorts = ordered_union(list(payload["comparisons"][metric]) for metric in metrics)
    colors = series_colors(cohorts)
    ncols = 2
    nrows = max(math.ceil(len(metrics) / ncols), 1)
    figure, axes = new_figure(ncols=ncols, nrows=nrows)
    for ax, metric in zip(axes, metrics):
        items = [
            (
                COHORT_LABELS.get(cohort, cohort),
                payload["comparisons"][metric].get(cohort),
                colors[cohort],
            )
            for cohort in cohorts
        ]
        summary_boxes(ax, items)
        ax.set_title(_metric_label(metric), fontsize=12)
    hide_axes(axes[len(metrics) :])
    add_box_note(figure)
    return figure
