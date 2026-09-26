"""Figures for the http_behavior_location statistics."""

from __future__ import annotations

from matplotlib.figure import Figure

from gerbil.figures.plotting import (
    ComparisonPayloads,
    FigureOptions,
    SeriesColors,
    add_box_note,
    add_series_legend,
    grouped_summary_boxes,
    new_figure,
    ordered_union,
)

ARTIFACT_LABELS = {
    "http_builders": "HTTP builder calls",
    "http_events": "HTTP dispatch events",
    "http_verifications": "HTTP verifications",
}

STRUCTURE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("setup methods", ("fixtures", "setup_method_count")),
    ("teardown methods", ("fixtures", "teardown_method_count")),
    ("test helpers", ("test_helper_method_count",)),
    ("assertions", ("assertion_count",)),
    ("mocked interactions", ("mocked_interaction_count",)),
)


def build_location(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Per-origin call-count boxes for builders, events, and verifications."""
    artifacts = list(ARTIFACT_LABELS)
    origins = ordered_union(list(payload["by_origin"]) for payload in payloads.values())
    figure, axes = new_figure(ncols=len(artifacts), panel_width=5.0)
    for ax, artifact in zip(axes, artifacts):
        series = [
            (
                name,
                colors[name],
                [
                    payload["by_origin"].get(origin, {}).get(artifact)
                    for origin in origins
                ],
            )
            for name, payload in payloads.items()
        ]
        grouped_summary_boxes(ax, origins, series, ylabel="count per API test")
        ax.set_title(ARTIFACT_LABELS[artifact], fontsize=12)
    add_series_legend(figure, {name: colors[name] for name in payloads})
    add_box_note(figure)
    return figure


def build_test_structure(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Fixture, helper, assertion, and mocked-interaction count boxes per test."""
    figure, axes = new_figure(panel_width=7.4)
    group_labels = [label for label, _ in STRUCTURE_GROUPS]
    series = []
    for name, payload in payloads.items():
        dists = []
        for _, key_path in STRUCTURE_GROUPS:
            value = payload
            for key in key_path:
                value = value.get(key, {})
            dists.append(value or None)
        series.append((name, colors[name], dists))
    grouped_summary_boxes(axes[0], group_labels, series, ylabel="count per API test")
    add_series_legend(figure, {name: colors[name] for name in payloads})
    add_box_note(figure)
    return figure
