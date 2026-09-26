"""Dev-only figures for the project_composition statistics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from matplotlib.figure import Figure

from gerbil.figures.plotting import (
    FigureOptions,
    add_box_note,
    add_series_legend,
    grouped_bars,
    new_figure,
    series_colors,
    summary_boxes,
)

QUADRANT_LABELS = {
    "endpoints_and_api_tests": "endpoints + API tests",
    "endpoints_no_api_tests": "endpoints only",
    "api_tests_no_endpoints": "API tests only",
    "no_endpoints_no_api_tests": "neither",
}


def build_dev_composition(payload: Mapping[str, Any], options: FigureOptions) -> Figure:
    """Project counts and per-project test volumes by composition quadrant."""
    quadrants = list(payload["quadrants"])
    labels = [QUADRANT_LABELS.get(quadrant, quadrant) for quadrant in quadrants]
    colors = series_colors(labels)
    figure, axes = new_figure(ncols=3, panel_width=4.6)

    count_series = [
        (
            "projects",
            "#4878d0",
            [payload["quadrants"][quadrant]["project_count"] for quadrant in quadrants],
        ),
        (
            "projects with zero tests",
            "#d65f5f",
            [
                payload["quadrants"][quadrant]["projects_with_zero_tests"]
                for quadrant in quadrants
            ],
        ),
    ]
    grouped_bars(axes[0], labels, count_series, ylabel="projects")
    axes[0].set_title("Projects per quadrant", fontsize=12)
    axes[0].legend(fontsize=11, frameon=False)

    test_items = [
        (
            label,
            payload["quadrants"][quadrant]["tests_per_project"],
            colors[label],
        )
        for label, quadrant in zip(labels, quadrants)
    ]
    summary_boxes(
        axes[1], test_items, ylabel="tests per project", show_tick_labels=False
    )
    axes[1].set_title("Tests per project", fontsize=12)

    api_items = [
        (
            label,
            payload["quadrants"][quadrant]["api_tests_per_project"],
            colors[label],
        )
        for label, quadrant in zip(labels, quadrants)
    ]
    summary_boxes(
        axes[2], api_items, ylabel="API tests per project", show_tick_labels=False
    )
    axes[2].set_title("API tests per project", fontsize=12)
    add_series_legend(figure, colors)
    add_box_note(figure)
    return figure
