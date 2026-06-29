"""Figures for the resource_interaction_distribution statistics."""

from __future__ import annotations

from matplotlib.figure import Figure

from gerbil.figures.plotting import (
    ComparisonPayloads,
    FigureOptions,
    SeriesColors,
    add_box_note,
    add_series_legend,
    grouped_bars,
    new_figure,
    ordered_union,
    prune_zero_categories,
    renormalize_unknown_share,
    summary_boxes,
)


def build_operations(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """HTTP method and CRUD operation splits over resource requests."""
    figure, axes = new_figure(ncols=2, panel_width=6.0)

    methods = ordered_union(
        list(payload["http_method_distribution"]["methods"])
        for payload in payloads.values()
    )
    method_series = [
        (
            name,
            colors[name],
            [
                payload["http_method_distribution"]["methods"]
                .get(method, {})
                .get("pct")
                or 0.0
                for method in methods
            ],
        )
        for name, payload in payloads.items()
    ]
    method_ylabel = "% of resource requests"
    if options.ignore_unknown:
        methods, method_series = renormalize_unknown_share(methods, method_series)
        method_ylabel = "% of classified resource requests"
    pruned_methods, method_series = prune_zero_categories(methods, method_series)
    grouped_bars(axes[0], pruned_methods, method_series, ylabel=method_ylabel)
    axes[0].set_title("HTTP methods", fontsize=12)

    operations = ordered_union(
        list(payload["crud_operation_distribution"]["operations"])
        for payload in payloads.values()
    )
    operation_series = [
        (
            name,
            colors[name],
            [
                payload["crud_operation_distribution"]["operations"]
                .get(operation, {})
                .get("pct")
                or 0.0
                for operation in operations
            ],
        )
        for name, payload in payloads.items()
    ]
    grouped_bars(
        axes[1], operations, operation_series, ylabel="% of CRUD-mapped requests"
    )
    axes[1].set_title("CRUD operations", fontsize=12)
    add_series_legend(figure, {name: colors[name] for name in payloads})
    return figure


def build_exercise(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Resource testing reach: tested share, per-operation rates, completeness."""
    figure, axes = new_figure(ncols=2, nrows=2, panel_width=5.0)

    tested_series = [
        (name, colors[name], [(payload["tested"]["proportion"] or 0.0) * 100.0])
        for name, payload in payloads.items()
    ]
    grouped_bars(axes[0], ["resources tested"], tested_series, ylabel="% of resources")
    axes[0].set_title("Tested resources", fontsize=12)

    operations = ordered_union(
        list(payload["per_operation_exercise"]) for payload in payloads.values()
    )
    operation_series = [
        (
            name,
            colors[name],
            [
                (
                    payload["per_operation_exercise"].get(operation, {}).get("rate")
                    or 0.0
                )
                * 100.0
                for operation in operations
            ],
        )
        for name, payload in payloads.items()
    ]
    grouped_bars(
        axes[1],
        operations,
        operation_series,
        ylabel="% of resources offering the operation",
    )
    axes[1].set_title("Per-operation exercise", fontsize=12)

    behavior_series = [
        (
            name,
            colors[name],
            [
                (
                    payload["read_only_when_writable"]["proportion_of_writable_tested"]
                    or 0.0
                )
                * 100.0,
                (payload["full_crud_tested"]["proportion_of_capable"] or 0.0) * 100.0,
            ],
        )
        for name, payload in payloads.items()
    ]
    grouped_bars(
        axes[2],
        ["read-only among tested writable", "full CRUD among capable"],
        behavior_series,
        ylabel="% of resources",
    )
    axes[2].set_title("Write-avoidance and full CRUD", fontsize=12)

    items = [
        (name, payload["exercised_completeness"]["among_tested"], colors[name])
        for name, payload in payloads.items()
    ]
    summary_boxes(
        axes[3], items, ylabel="exercised completeness", show_tick_labels=False
    )
    axes[3].set_title("Completeness among tested", fontsize=12)
    add_series_legend(figure, {name: colors[name] for name in payloads})
    add_box_note(figure)
    return figure


def build_lifecycle_labels(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Grouped bars of the CRUD lifecycle label split over test-resource pairs."""
    labels = ordered_union(
        list(payload["lifecycle_label_distribution"]["labels"])
        for payload in payloads.values()
    )
    series = [
        (
            name,
            colors[name],
            [
                payload["lifecycle_label_distribution"]["labels"]
                .get(label, {})
                .get("pct")
                or 0.0
                for label in labels
            ],
        )
        for name, payload in payloads.items()
    ]
    labels, series = prune_zero_categories(labels, series)
    figure, axes = new_figure(panel_width=8.0)
    grouped_bars(axes[0], labels, series, ylabel="% of test–resource pairs")
    add_series_legend(figure, {name: colors[name] for name in payloads})
    return figure
