"""Dev-only figures for the state_condition_distribution statistics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from matplotlib.figure import Figure

from gerbil.figures.plotting import FigureOptions, horizontal_bars, new_figure


def build_dev_state_conditions(
    payload: Mapping[str, Any], options: FigureOptions
) -> Figure:
    """Pre/postcondition entry counts by type and their co-occurrence."""
    figure, axes = new_figure(ncols=3, panel_width=4.6, panel_height=2.8)

    for ax, side, title, color in (
        (axes[0], "preconditions", "Precondition entries", "#4878d0"),
        (axes[1], "postconditions", "Postcondition entries", "#ee854a"),
    ):
        by_type = payload[side]["type_share"]["by_type"]
        labels = list(by_type)
        counts = [by_type[label]["count"] for label in labels]
        horizontal_bars(
            ax, labels, counts, color=color, xlabel="entries", value_format="{:.0f}"
        )
        ax.set_title(title, fontsize=12)

    cooccurrence = payload["state_cooccurrence"]
    labels = list(cooccurrence)
    counts = [
        cooccurrence[label]["tests_with_precondition_and_postcondition"]["count"]
        for label in labels
    ]
    horizontal_bars(
        axes[2],
        labels,
        counts,
        color="#6acc64",
        xlabel="tests with both",
        value_format="{:.0f}",
    )
    axes[2].set_title("Pre/post co-occurrence", fontsize=12)
    return figure
