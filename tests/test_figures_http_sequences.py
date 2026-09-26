from __future__ import annotations

from typing import Any

import matplotlib.pyplot as plt
from matplotlib.patches import Wedge
from matplotlib.text import Annotation

from gerbil.figures.http_sequences import build_dev_sequence_shapes
from gerbil.figures.plotting import FigureOptions


def _payload(counts: dict[str, int]) -> dict[str, Any]:
    return {
        "sequence_shape_distribution": {
            "labels": {label: {"count": count} for label, count in counts.items()},
            "classified_sequence_count": sum(counts.values()),
        }
    }


def test_sequence_shape_pie_carries_no_title_and_one_wedge_per_nonzero_label() -> None:
    payload = _payload(
        {
            "build-dispatch-verification": 10,
            "build-dispatch-no-verification": 5,
            "dispatch-only": 0,
        }
    )

    figure = build_dev_sequence_shapes(payload, FigureOptions())
    try:
        ax = figure.axes[0]
        # Titles live in the caption: neither a figure suptitle nor the old
        # "N classified sequences" axes title is rendered.
        assert figure.get_suptitle() == ""
        assert ax.get_title() == ""
        # Zero-count labels are dropped; only the two populated shapes draw.
        wedges = [patch for patch in ax.patches if isinstance(patch, Wedge)]
        assert len(wedges) == 2
    finally:
        plt.close(figure)


def test_sequence_shape_pie_labels_are_abbreviated_and_leader_lined() -> None:
    payload = _payload(
        {
            "build-dispatch-verification": 3,
            "dispatch-verification-no-build": 1,
        }
    )

    figure = build_dev_sequence_shapes(payload, FigureOptions())
    try:
        ax = figure.axes[0]
        annotations = [text for text in ax.texts if isinstance(text, Annotation)]
        # Each wedge gets a short B/D/V label placed by an annotation.
        assert {annotation.get_text() for annotation in annotations} == {
            "B + D + V",
            "D + V",
        }
        # Every label is connected to its wedge by a leader line (an arrow).
        assert all(annotation.arrowprops is not None for annotation in annotations)
    finally:
        plt.close(figure)


def test_sequence_shape_pie_handles_no_classified_sequences() -> None:
    figure = build_dev_sequence_shapes(_payload({"dispatch-only": 0}), FigureOptions())
    try:
        ax = figure.axes[0]
        assert not [patch for patch in ax.patches if isinstance(patch, Wedge)]
        assert any("No classified sequences" in text.get_text() for text in ax.texts)
    finally:
        plt.close(figure)
