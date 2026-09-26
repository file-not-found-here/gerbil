"""Figures for the endpoint_distribution statistics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from matplotlib.figure import Figure

from gerbil.figures.plotting import (
    ComparisonPayloads,
    FigureOptions,
    SeriesColors,
    add_box_note,
    add_series_legend,
    drop_unknown_categories,
    grouped_bars,
    new_figure,
    ordered_union,
    series_colors,
    summary_boxes,
)

BUCKET_LABELS = {
    "no_test": "no test",
    "one_to_three": "1–3 tests",
    "more_than_three": ">3 tests",
}

VARIABLE_SOURCES: tuple[tuple[str, str], ...] = (
    ("path_variable_count", "path"),
    ("query_variable_count", "query"),
    ("header_variable_count", "header"),
    ("form_variable_count", "form"),
)


def build_coverage(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Endpoint coverage share, tests per covered endpoint, and bucket split."""
    figure, axes = new_figure(ncols=3, panel_width=4.2)

    coverage_series = [
        (
            name,
            colors[name],
            [(payload["endpoint_coverage"]["coverage"]["proportion"] or 0.0) * 100.0],
        )
        for name, payload in payloads.items()
    ]
    grouped_bars(
        axes[0], ["endpoints with ≥1 test"], coverage_series, ylabel="% of endpoints"
    )
    axes[0].set_title("Coverage", fontsize=12)

    items = [
        (
            name,
            payload["endpoint_coverage"]["tests_per_endpoint_among_covered"],
            colors[name],
        )
        for name, payload in payloads.items()
    ]
    summary_boxes(
        axes[1], items, ylabel="tests per covered endpoint", show_tick_labels=False
    )
    axes[1].set_title("Tests per covered endpoint", fontsize=12)

    buckets = ordered_union(
        list(payload["endpoint_coverage"]["coverage_buckets"])
        for payload in payloads.values()
    )

    def bucket_pct(payload: Mapping[str, Any], bucket: str) -> float:
        total = payload["endpoint_coverage"]["endpoint_count"]
        count = (
            payload["endpoint_coverage"]["coverage_buckets"]
            .get(bucket, {})
            .get("endpoint_count", 0)
        )
        return count / total * 100.0 if total else 0.0

    bucket_series = [
        (name, colors[name], [bucket_pct(payload, bucket) for bucket in buckets])
        for name, payload in payloads.items()
    ]
    grouped_bars(
        axes[2],
        [BUCKET_LABELS.get(bucket, bucket) for bucket in buckets],
        bucket_series,
        ylabel="% of endpoints",
    )
    axes[2].set_title("Coverage buckets", fontsize=12)
    add_series_legend(figure, {name: colors[name] for name in payloads})
    add_box_note(figure)
    return figure


def build_parameter_surface(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Parameter surface of gated endpoints: body share and per-source means."""
    figure, axes = new_figure(ncols=3, panel_width=4.2)

    body_series = [
        (
            name,
            colors[name],
            [
                (
                    payload["endpoint_coverage"]["parameter_surface"][
                        "endpoints_with_body"
                    ]["proportion"]
                    or 0.0
                )
                * 100.0
            ],
        )
        for name, payload in payloads.items()
    ]
    grouped_bars(axes[0], ["endpoints with body"], body_series, ylabel="% of endpoints")
    axes[0].set_title("Request bodies", fontsize=12)

    for ax, kind, title in (
        (axes[1], "required_by_source", "Required parameters"),
        (axes[2], "optional_by_source", "Optional parameters"),
    ):
        sources = ordered_union(
            list(payload["endpoint_coverage"]["parameter_surface"][kind])
            for payload in payloads.values()
        )
        series = [
            (
                name,
                colors[name],
                [
                    payload["endpoint_coverage"]["parameter_surface"][kind]
                    .get(source, {})
                    .get("mean")
                    or 0.0
                    for source in sources
                ],
            )
            for name, payload in payloads.items()
        ]
        if options.ignore_unknown:
            # Means per source, not shares: unknown is dropped without rescaling.
            sources, series = drop_unknown_categories(sources, series)
        grouped_bars(ax, sources, series, ylabel="mean parameters per endpoint")
        ax.set_title(title, fontsize=12)
    add_series_legend(figure, {name: colors[name] for name in payloads})
    return figure


def build_dev_coverage_buckets(
    payload: Mapping[str, Any], options: FigureOptions
) -> Figure:
    """Endpoint characteristics per coverage bucket for the dev directory."""
    buckets = list(payload["endpoint_coverage"]["coverage_buckets"])
    bucket_payloads = payload["endpoint_coverage"]["coverage_buckets"]
    colors = series_colors(buckets)
    labels = [BUCKET_LABELS.get(bucket, bucket) for bucket in buckets]
    figure, axes = new_figure(ncols=3, panel_width=4.6)

    items = [
        (label, bucket_payloads[bucket].get("route_depth"), colors[bucket])
        for label, bucket in zip(labels, buckets)
    ]
    summary_boxes(axes[0], items, ylabel="route depth")
    axes[0].set_title("Route depth", fontsize=12)

    source_labels = [label for _, label in VARIABLE_SOURCES]
    source_series = [
        (
            label,
            colors[bucket],
            [
                bucket_payloads[bucket].get(key, {}).get("mean") or 0.0
                for key, _ in VARIABLE_SOURCES
            ],
        )
        for label, bucket in zip(labels, buckets)
    ]
    grouped_bars(
        axes[1], source_labels, source_series, ylabel="mean variables per endpoint"
    )
    axes[1].set_title("Variables by source", fontsize=12)

    query_series = [
        (
            label,
            colors[bucket],
            [
                bucket_payloads[bucket].get(key, {}).get("mean") or 0.0
                for key in (
                    "required_query_variable_count",
                    "optional_query_variable_count",
                )
            ],
        )
        for label, bucket in zip(labels, buckets)
    ]
    grouped_bars(
        axes[2],
        ["required query", "optional query"],
        query_series,
        ylabel="mean variables per endpoint",
    )
    axes[2].set_title("Query requiredness", fontsize=12)
    add_series_legend(
        figure, {label: colors[bucket] for label, bucket in zip(labels, buckets)}
    )
    add_box_note(figure)
    return figure
