"""Figures for the auth_handling_distribution statistics."""

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
    prune_zero_categories,
    renormalize_unknown_share,
)


def build_labels(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Grouped bars of the auth-handling label split across directories."""
    categories = ordered_union(
        list(payload["label_split"]) for payload in payloads.values()
    )
    series = [
        (
            name,
            colors[name],
            [
                payload["label_split"].get(category, {}).get("pct_of_tests") or 0.0
                for category in categories
            ],
        )
        for name, payload in payloads.items()
    ]
    ylabel = "% of API tests"
    if options.ignore_unknown:
        categories, series = renormalize_unknown_share(categories, series)
        ylabel = "% of classified API tests"
    categories, series = prune_zero_categories(categories, series)
    figure, axes = new_figure(panel_width=6.4)
    grouped_bars(axes[0], categories, series, ylabel=ylabel)
    add_series_legend(figure, {name: colors[name] for name in payloads})
    return figure
