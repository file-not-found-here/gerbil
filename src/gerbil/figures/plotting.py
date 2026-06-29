"""Shared matplotlib primitives for figure generation: summary box plots,
grouped/stacked bars, palettes, and figure saving."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.colors import to_hex
from matplotlib.figure import Figure
from matplotlib.layout_engine import ConstrainedLayoutEngine
from matplotlib.patches import Patch

matplotlib.use("Agg")

# Figure titles live in the surrounding caption, so the readable text in a
# figure is its panel titles, labels, and annotations: size them up here.
plt.rcParams.update(
    {
        "font.size": 12,
        "axes.titlesize": 13,
        "axes.labelsize": 12,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
        "legend.fontsize": 11,
    }
)

# Statistics payloads carry distribution summaries, not raw samples, so box
# plots are reconstructed from min/p25/p50/p75/p90/mean.
BOX_NOTE = "box: p25–p75 | line: median | diamond: mean | whiskers: min–p90"

FIGURE_FORMATS: tuple[str, ...] = ("png", "pdf")

# Type aliases shared by figure builders: comparison builders receive one
# payload per statistics directory plus a stable color per directory name.
ComparisonPayloads = Mapping[str, Mapping[str, Any]]
SeriesColors = Mapping[str, str]

UNKNOWN_CATEGORIES = frozenset({"unknown", "UNKNOWN"})


@dataclass(frozen=True)
class FigureOptions:
    """Cross-cutting figure-generation switches passed to every builder."""

    # Orient categorical figures to classified items only: drop "unknown"
    # categories, renormalizing share-based splits over the classified portion.
    ignore_unknown: bool = False


_MEAN_PROPS = {
    "marker": "D",
    "markerfacecolor": "white",
    "markeredgecolor": "black",
    "markersize": 4,
}
_MEDIAN_PROPS = {"color": "black"}


def _palette(colormap_name: str, names: Sequence[str]) -> dict[str, str]:
    colormap = matplotlib.colormaps[colormap_name]
    return {
        name: to_hex(colormap(index % colormap.N)) for index, name in enumerate(names)
    }


def series_colors(names: Sequence[str]) -> dict[str, str]:
    """Stable color per series name, assigned by position from tab10."""
    return _palette("tab10", names)


def category_colors(names: Sequence[str]) -> dict[str, str]:
    """Stable color per category name, assigned by position from tab20."""
    return _palette("tab20", names)


def ordered_union(sequences: Iterable[Sequence[str]]) -> list[str]:
    """Union of string sequences preserving first-seen order."""
    seen: dict[str, None] = {}
    for sequence in sequences:
        for item in sequence:
            seen.setdefault(item, None)
    return list(seen)


def prune_zero_categories(
    categories: Sequence[str],
    series: Sequence[tuple[str, str, Sequence[float | None]]],
) -> tuple[list[str], list[tuple[str, str, list[float | None]]]]:
    """Drop categories whose value is zero or None in every series."""
    kept = [
        index
        for index in range(len(categories))
        if any((values[index] or 0.0) != 0.0 for _, _, values in series)
    ]
    return (
        [categories[index] for index in kept],
        [
            (name, color, [values[index] for index in kept])
            for name, color, values in series
        ],
    )


def drop_unknown_categories(
    categories: Sequence[str],
    series: Sequence[tuple[str, str, Sequence[float | None]]],
) -> tuple[list[str], list[tuple[str, str, list[float | None]]]]:
    """Drop unknown categories without rescaling (for mean/count values)."""
    kept = [
        index
        for index, category in enumerate(categories)
        if category not in UNKNOWN_CATEGORIES
    ]
    return (
        [categories[index] for index in kept],
        [
            (name, color, [values[index] for index in kept])
            for name, color, values in series
        ],
    )


def renormalize_unknown_share(
    categories: Sequence[str],
    series: Sequence[tuple[str, str, Sequence[float | None]]],
    *,
    total: float = 100.0,
) -> tuple[list[str], list[tuple[str, str, list[float | None]]]]:
    """Drop unknown categories and rescale each series by total/(total-unknown)
    so the remaining shares describe classified items only.

    A series whose unknown share consumes the whole total keeps its (zero)
    remaining values unscaled.
    """
    kept_categories, kept_series = drop_unknown_categories(categories, series)
    rescaled: list[tuple[str, str, list[float | None]]] = []
    for (name, color, values), (_, _, kept_values) in zip(series, kept_series):
        unknown_share = sum(
            value or 0.0
            for category, value in zip(categories, values)
            if category in UNKNOWN_CATEGORIES
        )
        scale = total / (total - unknown_share) if unknown_share < total else 1.0
        rescaled.append(
            (
                name,
                color,
                [None if value is None else value * scale for value in kept_values],
            )
        )
    return kept_categories, rescaled


def box_stats(dist: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Matplotlib bxp stats from a distribution summary; None for empty samples."""
    if dist is None or not dist.get("count") or dist.get("p50") is None:
        return None
    return {
        "label": "",
        "med": dist["p50"],
        "q1": dist["p25"],
        "q3": dist["p75"],
        "whislo": dist["min"],
        "whishi": dist["p90"],
        "mean": dist["mean"],
        "fliers": [],
    }


def _draw_box(ax: Axes, stats: dict[str, Any], position: float, color: str) -> None:
    ax.bxp(
        [stats],
        positions=[position],
        widths=0.7,
        patch_artist=True,
        showmeans=True,
        boxprops={"facecolor": color, "alpha": 0.85},
        meanprops=_MEAN_PROPS,
        medianprops=_MEDIAN_PROPS,
        showfliers=False,
    )


def summary_boxes(
    ax: Axes,
    items: Sequence[tuple[str, Mapping[str, Any] | None, str]],
    *,
    ylabel: str | None = None,
    show_tick_labels: bool = True,
) -> None:
    """One box per (label, distribution, color) item; empty samples leave a gap."""
    for position, (_, dist, color) in enumerate(items):
        stats = box_stats(dist)
        if stats is not None:
            _draw_box(ax, stats, position, color)
    ax.set_xticks(range(len(items)))
    if show_tick_labels:
        ax.set_xticklabels([label for label, _, _ in items], rotation=20, ha="right")
    else:
        ax.set_xticklabels([""] * len(items))
    ax.set_xlim(-0.7, len(items) - 0.3)
    if ylabel:
        ax.set_ylabel(ylabel)


def grouped_summary_boxes(
    ax: Axes,
    group_labels: Sequence[str],
    series: Sequence[tuple[str, str, Sequence[Mapping[str, Any] | None]]],
    *,
    ylabel: str | None = None,
) -> None:
    """Boxes grouped by label; series are (name, color, dists aligned to groups)."""
    step = len(series) + 1
    for series_index, (_, color, dists) in enumerate(series):
        for group_index, dist in enumerate(dists):
            stats = box_stats(dist)
            if stats is not None:
                _draw_box(ax, stats, group_index * step + series_index, color)
    centers = [
        index * step + (len(series) - 1) / 2 for index in range(len(group_labels))
    ]
    ax.set_xticks(centers)
    ax.set_xticklabels(group_labels, rotation=20, ha="right")
    ax.set_xlim(-1, (len(group_labels) - 1) * step + len(series))
    if ylabel:
        ax.set_ylabel(ylabel)


def grouped_bars(
    ax: Axes,
    categories: Sequence[str],
    series: Sequence[tuple[str, str, Sequence[float | None]]],
    *,
    ylabel: str | None = None,
) -> None:
    """Grouped bars per category; None values render as zero-height bars."""
    width = 0.8 / max(len(series), 1)
    for series_index, (name, color, values) in enumerate(series):
        offsets = [
            index - 0.4 + width * (series_index + 0.5)
            for index in range(len(categories))
        ]
        ax.bar(
            offsets,
            [value or 0.0 for value in values],
            width=width,
            color=color,
            label=name,
        )
    ax.set_xticks(range(len(categories)))
    ax.set_xticklabels(categories, rotation=20, ha="right")
    if ylabel:
        ax.set_ylabel(ylabel)


def horizontal_bars(
    ax: Axes,
    labels: Sequence[str],
    values: Sequence[float],
    *,
    color: str,
    xlabel: str | None = None,
    value_format: str = "{:.1f}%",
) -> None:
    """Horizontal bars in the given order, first label on top, values annotated."""
    positions = range(len(labels))
    bars = ax.barh(positions, values, color=color, alpha=0.85)
    ax.bar_label(
        bars,
        labels=[value_format.format(value) for value in values],
        padding=3,
        fontsize=10,
    )
    ax.set_yticks(positions)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.margins(x=0.15)
    if xlabel:
        ax.set_xlabel(xlabel)


def stacked_share_bars(
    ax: Axes,
    row_labels: Sequence[str],
    categories: Sequence[str],
    rows: Sequence[Sequence[float | None]],
    *,
    xlabel: str,
) -> None:
    """One 100%-style stacked horizontal bar per row, first row on top."""
    colors = category_colors(categories)
    lefts = [0.0] * len(row_labels)
    for category_index, category in enumerate(categories):
        widths = [row[category_index] or 0.0 for row in rows]
        ax.barh(
            range(len(row_labels)),
            widths,
            left=lefts,
            color=colors[category],
            label=category,
        )
        lefts = [left + width for left, width in zip(lefts, widths)]
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels)
    ax.invert_yaxis()
    ax.set_xlabel(xlabel)


def new_figure(
    *,
    ncols: int = 1,
    nrows: int = 1,
    panel_width: float = 4.6,
    panel_height: float = 3.4,
) -> tuple[Figure, list[Axes]]:
    """Constrained-layout figure with a flat list of axes; the title lives in
    the surrounding figure caption, not on the figure itself."""
    figure, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(panel_width * ncols, panel_height * nrows),
        layout="constrained",
        squeeze=False,
    )
    return figure, [ax for row in axes for ax in row]


def hide_axes(axes: Sequence[Axes]) -> None:
    for ax in axes:
        ax.set_visible(False)


def add_series_legend(figure: Figure, colors: Mapping[str, str]) -> None:
    """Figure-level legend mapping series names to colors, below the panels."""
    handles = [Patch(facecolor=color, label=name) for name, color in colors.items()]
    figure.legend(
        handles=handles,
        loc="outside lower center",
        ncols=min(len(handles), 4),
        frameon=False,
        fontsize=11,
    )


def add_box_note(figure: Figure) -> None:
    # Reserve a thin top band so the note clears the panel titles now that the
    # figure carries no suptitle (which used to leave that headroom).
    engine = figure.get_layout_engine()
    if isinstance(engine, ConstrainedLayoutEngine):
        engine.set(rect=(0.0, 0.0, 1.0, 0.94))
    figure.text(0.99, 0.99, BOX_NOTE, ha="right", va="top", fontsize=9, color="0.4")


def save_figure(figure: Figure, output_dir: Path, name: str) -> list[Path]:
    """Write the figure as <name>.<fmt> for every configured format, then close it.

    A builder may set ``crop_bbox_inches`` on the figure to crop to a precise
    bounding box (in inches) instead of the default tight box; this lets
    manual-coordinate figures trim the slack their axis-off rectangle leaves.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    bbox = getattr(figure, "crop_bbox_inches", "tight")
    written: list[Path] = []
    for fmt in FIGURE_FORMATS:
        path = output_dir / f"{name}.{fmt}"
        figure.savefig(path, dpi=300, bbox_inches=bbox)
        written.append(path)
    plt.close(figure)
    return written
