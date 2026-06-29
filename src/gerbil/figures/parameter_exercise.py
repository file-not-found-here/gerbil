"""Figures for the parameter_exercise_distribution statistics."""

from __future__ import annotations

from matplotlib.figure import Figure

from gerbil.figures.plotting import (
    ComparisonPayloads,
    FigureOptions,
    SeriesColors,
    add_box_note,
    add_series_legend,
    grouped_bars,
    grouped_summary_boxes,
    new_figure,
    ordered_union,
)

RATE_LABELS = {
    "exercise_rate": "all",
    "required_exercise_rate": "required",
    "optional_exercise_rate": "optional",
    "simple_1_way_optional_coverage": "simple 1-way",
    "simple_2_way_optional_coverage": "simple 2-way",
    "total_2_way_optional_coverage": "total 2-way",
}


def build_exercise(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Parameter-exercise coverage, holistic rate boxes, and per-source means."""
    figure, axes = new_figure(ncols=3, panel_width=4.6)

    coverage_series = [
        (name, colors[name], [(payload["coverage"]["proportion"] or 0.0) * 100.0])
        for name, payload in payloads.items()
    ]
    grouped_bars(
        axes[0], ["endpoints with ≥1 test"], coverage_series, ylabel="% of endpoints"
    )
    axes[0].set_title("Route coverage", fontsize=12)

    rates = ordered_union(
        list(payload["among_covered"]["holistic"]) for payload in payloads.values()
    )
    rate_series = [
        (
            name,
            colors[name],
            [payload["among_covered"]["holistic"].get(rate) for rate in rates],
        )
        for name, payload in payloads.items()
    ]
    grouped_summary_boxes(
        axes[1],
        [RATE_LABELS.get(rate, rate) for rate in rates],
        rate_series,
        ylabel="exercise rate per endpoint",
    )
    axes[1].set_title("Holistic rates (covered endpoints)", fontsize=12)

    sources = ordered_union(
        list(payload["among_covered"]["by_source"]["exercise_rate"])
        for payload in payloads.values()
    )
    source_series = [
        (
            name,
            colors[name],
            [
                payload["among_covered"]["by_source"]["exercise_rate"]
                .get(source, {})
                .get("mean")
                or 0.0
                for source in sources
            ],
        )
        for name, payload in payloads.items()
    ]
    grouped_bars(axes[2], sources, source_series, ylabel="mean exercise rate")
    axes[2].set_title("Exercise rate by source", fontsize=12)
    add_series_legend(figure, {name: colors[name] for name in payloads})
    add_box_note(figure)
    return figure
