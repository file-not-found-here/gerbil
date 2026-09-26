"""Sankey figure for the test_scope_distribution statistics: API tests narrowing
into focal-resource counts and then focal-endpoint counts. Controller-unit and
non-API "other" tests are reported in the payload but excluded here so the API
funnel reads cleanly."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.patches import PathPatch, Rectangle
from matplotlib.path import Path as MplPath
from matplotlib.transforms import Bbox

from gerbil.figures.plotting import FigureOptions, new_figure

_NODE_WIDTH = 0.07
# Vertical gap between nodes in a column, as a share of the largest column total.
_GAP_FRACTION = 0.05
# Display floor for nonzero nodes so heavily skewed corpora (a handful of broad
# tests against tens of thousands of focused ones) stay legible; labels carry
# the exact counts.
_MIN_NODE_HEIGHT = 0.02
_BAND_SAMPLES = 64
# Every ribbon is a light gray background, kept clearly lighter than the dark
# gray of the "no resource recovered" node so the two never read as the same.
_BAND_COLOR = "#e0e0e0"  # Gray 20
_BAND_ALPHA = 1.0
# This axis-off figure draws in manual coordinates, so the constrained-layout
# axes rectangle is taller than the drawn content; crop the saved output to the
# artists themselves with a thin, uniform margin instead.
_CROP_PAD_INCHES = 0.05
_STAGE_TITLES = ("Test scope", "Focal resources", "Focal endpoints")

# Carbon Design System palette (https://carbondesignsystem.com/elements/color/overview/) in a bold
# blue/cyan/teal suite: distinct hues per node so the funnel never reads as a run
# of similar blues, with a dark neutral gray for nothing recovered.
_INK = "#161616"  # Gray 100
_PILL = "#005d5d"  # Teal 70 — label chip, deeper than the teal middle node
_NODE_COLORS: dict[str, str] = {
    "api": "#0043ce",  # Blue 70 — the core
    "multi_resource": "#4589ff",  # Blue 50 — broad
    "single_resource": "#009d9a",  # Teal 50 — focused spine
    "multi_endpoint": "#08bdba",  # Teal 40 — broad
    "single_endpoint": "#1192e8",  # Cyan 50 — the narrowed tip
    "no_resource_recovered": "#6f6f6f",  # Gray 60 — unresolved
    "no_endpoint_resolved": "#a8a8a8",  # Gray 40 — unresolved
}

# IBM's heritage typeface family, with IBM Plex Sans preferred when installed.
_FONT_RC = {
    "font.family": "sans-serif",
    "font.sans-serif": [
        "IBM Plex Sans",
        "Helvetica Neue",
        "Helvetica",
        "Arial",
        "DejaVu Sans",
    ],
}

_STAGE_TWO = (
    ("multi_resource", ">1 focal resource"),
    ("single_resource", "1 focal resource"),
    ("no_resource_recovered", "No resource recovered"),
)
# "endpoint" wraps onto its own line to save width on the right-hand column.
_STAGE_THREE = (
    ("multi_endpoint", ">1 focal\nendpoint"),
    ("single_endpoint", "1 focal\nendpoint"),
    ("no_endpoint_resolved", "No endpoint\nresolved"),
)


@dataclass
class _Node:
    label: str
    count: int
    stage: int
    pct: float | None = None
    height: float = 0.0
    y_top: float = 0.0
    out_cursor: float = 0.0
    has_outflow: bool = False


def _nodes_and_links(
    payload: Mapping[str, Any],
) -> tuple[dict[str, _Node], list[tuple[str, str]]]:
    """Non-empty nodes keyed by split key, plus (source, target) links."""
    nodes: dict[str, _Node] = {}
    links: list[tuple[str, str]] = []

    api_count = payload["test_type_split"]["api"]["test_count"]
    if api_count:
        nodes["api"] = _Node(label="API tests", count=api_count, stage=0)

    for key, label in _STAGE_TWO:
        entry = payload["focal_resource_split"][key]
        if entry["test_count"] and "api" in nodes:
            nodes[key] = _Node(
                label=label,
                count=entry["test_count"],
                stage=1,
                pct=entry["pct_of_tests"],
            )
            links.append(("api", key))
            nodes["api"].has_outflow = True

    for key, label in _STAGE_THREE:
        entry = payload["focal_endpoint_split"][key]
        if entry["test_count"] and "single_resource" in nodes:
            nodes[key] = _Node(
                label=label,
                count=entry["test_count"],
                stage=2,
                pct=entry["pct_of_tests"],
            )
            links.append(("single_resource", key))
            nodes["single_resource"].has_outflow = True

    return nodes, links


def _display_heights(nodes: dict[str, _Node], links: list[tuple[str, str]]) -> None:
    """Proportional node heights, floored for legibility; parents grow to
    contain their children's bands."""
    total = max(
        sum(node.count for node in nodes.values() if node.stage == stage)
        for stage in range(3)
        if any(node.stage == stage for node in nodes.values())
    )
    for node in nodes.values():
        node.height = max(node.count / total, _MIN_NODE_HEIGHT)

    children: dict[str, list[str]] = {}
    for source, target in links:
        children.setdefault(source, []).append(target)
    for key in sorted(nodes, key=lambda key: -nodes[key].stage):
        if key in children:
            nodes[key].height = max(
                nodes[key].height,
                sum(nodes[child].height for child in children[key]),
            )


def _layout(nodes: dict[str, _Node]) -> float:
    """Assign node y positions (y grows downward), returning the tallest column."""
    stages: dict[int, list[_Node]] = {}
    for node in nodes.values():
        stages.setdefault(node.stage, []).append(node)

    gap = _GAP_FRACTION * max(
        sum(node.height for node in column) for column in stages.values()
    )
    heights = {
        stage: sum(node.height for node in column) + gap * (len(column) - 1)
        for stage, column in stages.items()
    }
    tallest = max(heights.values())
    for stage, column in stages.items():
        y = (tallest - heights[stage]) / 2.0
        for node in column:
            node.y_top = y
            y += node.height + gap
    return tallest


def _band(
    axes: Axes,
    x0: float,
    x1: float,
    y0_top: float,
    y1_top: float,
    thickness: float,
) -> None:
    """A flat light-gray smoothstep ribbon connecting two nodes."""
    xs: list[float] = []
    tops: list[float] = []
    for index in range(_BAND_SAMPLES + 1):
        t = index / _BAND_SAMPLES
        ease = t * t * (3.0 - 2.0 * t)
        xs.append(x0 + (x1 - x0) * t)
        tops.append(y0_top + (y1_top - y0_top) * ease)
    bottoms = [top + thickness for top in tops]

    outline = MplPath(list(zip(xs, tops)) + list(zip(reversed(xs), reversed(bottoms))))
    axes.add_patch(
        PathPatch(
            outline,
            facecolor=_BAND_COLOR,
            edgecolor="none",
            alpha=_BAND_ALPHA,
            zorder=1,
        )
    )


def _stat_line(node: _Node) -> str:
    if node.pct is None:
        return f"{node.count:,}"
    return f"{node.count:,}  ({node.pct:.1f}%)"


def _outside_label(
    axes: Axes, x: float, center_y: float, ha: str, node: _Node, tallest: float
) -> None:
    """Bold name above its count/percent, both black; the name grows upward and
    the stat downward from a small gap so multi-line names stay centered."""
    gap = 0.01 * tallest
    axes.text(
        x,
        center_y - gap,
        node.label,
        ha=ha,
        va="bottom",
        color=_INK,
        fontsize=14,
        fontweight="bold",
        linespacing=1.15,
        zorder=5,
    )
    axes.text(
        x,
        center_y + gap,
        _stat_line(node),
        ha=ha,
        va="top",
        color=_INK,
        fontsize=12,
        zorder=5,
    )


def _inside_label(axes: Axes, x: float, center_y: float, node: _Node) -> None:
    """A junction node carries flows on both edges, so its label floats on a
    rounded pill centered over the node."""
    axes.text(
        x,
        center_y,
        f"{node.label}\n{_stat_line(node)}",
        ha="center",
        va="center",
        color="white",
        fontsize=12.5,
        fontweight="bold",
        linespacing=1.6,
        zorder=6,
        bbox={
            "boxstyle": "round,pad=0.55",
            "facecolor": _PILL,
            "edgecolor": "none",
            "alpha": 0.97,
        },
    )


def _crop_to_content(figure: Figure, axes: Axes) -> None:
    """Tell save_figure to crop to the drawn artists plus a thin uniform margin,
    so the oversized axis-off rectangle does not pad the output vertically."""
    figure.canvas.draw()
    boxes = [
        artist.get_tightbbox() for artist in (*axes.images, *axes.texts, *axes.patches)
    ]
    present = [box for box in boxes if box is not None]
    if not present:
        return
    union = Bbox.union(present).padded(_CROP_PAD_INCHES * figure.dpi)
    setattr(
        figure, "crop_bbox_inches", union.transformed(figure.dpi_scale_trans.inverted())
    )


def build_dev_scope_sankey(
    payload: Mapping[str, Any], options: FigureOptions
) -> Figure:
    """Three-stage Sankey narrowing API tests to focal resources and endpoints."""
    with plt.rc_context(_FONT_RC):
        nodes, links = _nodes_and_links(payload)
        figure, axes_list = new_figure(panel_width=9.6, panel_height=5.4)
        axes = axes_list[0]
        axes.set_axis_off()
        if not nodes:
            return figure

        _display_heights(nodes, links)
        tallest = _layout(nodes)

        for source_key, target_key in links:
            source = nodes[source_key]
            target = nodes[target_key]
            _band(
                axes,
                source.stage + _NODE_WIDTH,
                target.stage,
                source.y_top + source.out_cursor,
                target.y_top,
                target.height,
            )
            source.out_cursor += target.height

        pad = 0.06
        for key, node in nodes.items():
            axes.add_patch(
                Rectangle(
                    (node.stage, node.y_top),
                    _NODE_WIDTH,
                    node.height,
                    facecolor=_NODE_COLORS[key],
                    edgecolor="none",
                    zorder=3,
                )
            )
            center_y = node.y_top + node.height / 2.0
            if node.stage == 0:
                _outside_label(axes, node.stage - pad, center_y, "right", node, tallest)
            elif node.has_outflow:
                _inside_label(axes, node.stage + _NODE_WIDTH / 2.0, center_y, node)
            else:
                _outside_label(
                    axes,
                    node.stage + _NODE_WIDTH + pad,
                    center_y,
                    "left",
                    node,
                    tallest,
                )

        for stage, title in enumerate(_STAGE_TITLES):
            if any(node.stage == stage for node in nodes.values()):
                axes.text(
                    stage + _NODE_WIDTH / 2.0,
                    -0.06 * tallest,
                    title,
                    ha="center",
                    va="bottom",
                    color=_INK,
                    fontsize=15,
                    fontweight="bold",
                    zorder=5,
                )

        axes.set_xlim(-pad - 0.02, 2 + _NODE_WIDTH + pad)
        axes.set_ylim(tallest * 1.06, -0.15 * tallest)
        _crop_to_content(figure, axes)
    return figure
