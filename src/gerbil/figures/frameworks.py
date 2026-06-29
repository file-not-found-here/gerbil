"""Dev-only figures for the testing and HTTP-dispatch framework distributions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from matplotlib.figure import Figure

from gerbil.figures.plotting import FigureOptions, horizontal_bars, new_figure

NO_FRAMEWORK = "none"


def _sorted_nonzero(
    split: Mapping[str, Mapping[str, Any]], count_key: str, pct_key: str
) -> tuple[list[str], list[float]]:
    """Split entries sorted by count descending, zero-count entries dropped."""
    ordered = sorted(
        (name for name in split if split[name][count_key]),
        key=lambda name: split[name][count_key],
        reverse=True,
    )
    return ordered, [split[name][pct_key] for name in ordered]


def build_dev_testing_frameworks(
    payload: Mapping[str, Any], options: FigureOptions
) -> Figure:
    """Testing-framework and category splits over test classes (dev only)."""
    frameworks, framework_values = _sorted_nonzero(
        payload["framework_split"], "class_count", "pct_of_classes"
    )
    figure, axes = new_figure(
        ncols=2,
        panel_width=5.4,
        panel_height=0.28 * max(len(frameworks), 6) + 1.4,
    )
    horizontal_bars(
        axes[0],
        frameworks,
        framework_values,
        color="#4878d0",
        xlabel="% of test classes",
    )
    axes[0].set_title("Frameworks (classes may use several)", fontsize=12)

    categories, category_values = _sorted_nonzero(
        payload["category_split"], "class_count", "pct_of_classes"
    )
    categories.append(NO_FRAMEWORK)
    category_values.append(
        (payload["classes_without_frameworks"]["proportion"] or 0.0) * 100.0
    )
    horizontal_bars(
        axes[1],
        categories,
        category_values,
        color="#ee854a",
        xlabel="% of test classes",
    )
    axes[1].set_title("Framework categories", fontsize=12)
    return figure


def build_dev_dispatch_call_sites(
    payload: Mapping[str, Any], options: FigureOptions
) -> Figure:
    """HTTP dispatch framework split over call sites (dev only)."""
    frameworks, values = _sorted_nonzero(
        payload["framework_split"], "call_site_count", "pct_of_call_sites"
    )
    figure, axes = new_figure(
        panel_width=6.4,
        panel_height=0.28 * max(len(frameworks), 6) + 1.4,
    )
    horizontal_bars(
        axes[0], frameworks, values, color="#4878d0", xlabel="% of HTTP call sites"
    )
    return figure


def build_dev_dispatch_events(
    payload: Mapping[str, Any], options: FigureOptions
) -> Figure:
    """HTTP dispatch framework split over dispatch events (dev only)."""
    frameworks, values = _sorted_nonzero(
        payload["framework_split"], "event_count", "pct_of_events"
    )
    figure, axes = new_figure(
        panel_width=6.4,
        panel_height=0.28 * max(len(frameworks), 6) + 1.4,
    )
    horizontal_bars(
        axes[0], frameworks, values, color="#ee854a", xlabel="% of HTTP dispatch events"
    )
    return figure
