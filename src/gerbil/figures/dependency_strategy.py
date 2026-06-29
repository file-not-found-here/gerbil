"""Figures for the dependency_strategy_distribution statistics."""

from __future__ import annotations

from matplotlib.figure import Figure

from gerbil.figures.plotting import (
    ComparisonPayloads,
    FigureOptions,
    SeriesColors,
    add_series_legend,
    grouped_bars,
    new_figure,
    ordered_union,
)

MULTIPLE_STRATEGIES = "multiple strategies"


def build_strategies(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Grouped bars of the dependency-strategy split, plus multi-strategy share."""
    categories = ordered_union(
        list(payload["strategy_split"]) for payload in payloads.values()
    )

    def values(payload: dict) -> list[float | None]:
        split = [
            payload["strategy_split"].get(category, {}).get("pct_of_tests") or 0.0
            for category in categories
        ]
        multiple = (payload["multiple_strategy_tests"]["proportion"] or 0.0) * 100.0
        return [*split, multiple]

    series = [
        (name, colors[name], values(dict(payload)))
        for name, payload in payloads.items()
    ]
    figure, axes = new_figure(panel_width=6.4)
    grouped_bars(
        axes[0],
        [*categories, MULTIPLE_STRATEGIES],
        series,
        ylabel="% of API tests",
    )
    add_series_legend(figure, {name: colors[name] for name in payloads})
    return figure
