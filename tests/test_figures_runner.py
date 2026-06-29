from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pytest
from matplotlib.figure import Figure

from gerbil.figures import COMPARISON_FIGURES, DEV_FIGURES, FigureOptions
from gerbil.figures.loading import StatsDirectory
from gerbil.figures.plotting import series_colors
from gerbil.statistics.runner import (
    AUTH_HANDLING_DISTRIBUTION,
    TESTING_FRAMEWORK_DISTRIBUTION,
)
from tests.figures_builders import dev_statistics, tool_statistics


@pytest.fixture(scope="module")
def dev_payloads() -> dict:
    return dev_statistics()


@pytest.fixture(scope="module")
def tool_payloads() -> dict:
    return tool_statistics()


_OPTION_VARIANTS = [FigureOptions(), FigureOptions(ignore_unknown=True)]
_OPTION_IDS = ["default", "ignore-unknown"]


@pytest.mark.parametrize("options", _OPTION_VARIANTS, ids=_OPTION_IDS)
@pytest.mark.parametrize(
    "name,stem,build", COMPARISON_FIGURES, ids=[spec[0] for spec in COMPARISON_FIGURES]
)
def test_comparison_builders_accept_real_payloads(
    name: str,
    stem: str,
    build,
    options: FigureOptions,
    dev_payloads: dict,
    tool_payloads: dict,
) -> None:
    payloads = {"dev-stats": dev_payloads[stem], "tool-stats": tool_payloads[stem]}
    colors = series_colors(["dev-stats", "tool-stats"])

    figure = build(payloads, colors, options)
    try:
        assert isinstance(figure, Figure)
        assert figure.axes
    finally:
        plt.close(figure)


@pytest.mark.parametrize("options", _OPTION_VARIANTS, ids=_OPTION_IDS)
@pytest.mark.parametrize(
    "name,stem,build", DEV_FIGURES, ids=[spec[0] for spec in DEV_FIGURES]
)
def test_dev_builders_accept_real_payloads(
    name: str, stem: str, build, options: FigureOptions, dev_payloads: dict
) -> None:
    figure = build(dev_payloads[stem], options)
    try:
        assert isinstance(figure, Figure)
        assert figure.axes
    finally:
        plt.close(figure)


def test_comparison_builders_accept_a_single_directory(dev_payloads: dict) -> None:
    colors = series_colors(["only-stats"])
    for name, stem, build in COMPARISON_FIGURES:
        figure = build({"only-stats": dev_payloads[stem]}, colors, FigureOptions())
        plt.close(figure)


def test_ignore_unknown_renormalizes_dispatch_labels(dev_payloads: dict) -> None:
    from matplotlib.patches import Rectangle

    from gerbil.figures import request_dispatch
    from gerbil.statistics.runner import REQUEST_DISPATCH_DISTRIBUTION

    # The dev fixture has one in-process, one local-network, and one unknown
    # test, so classified shares renormalize from 33.3% each to 50% each.
    payloads = {"dev-stats": dev_payloads[REQUEST_DISPATCH_DISTRIBUTION]}
    colors = series_colors(["dev-stats"])

    figure = request_dispatch.build_labels(
        payloads, colors, FigureOptions(ignore_unknown=True)
    )
    try:
        ax = figure.axes[0]
        labels = [tick.get_text() for tick in ax.get_xticklabels()]
        bars = [patch for patch in ax.patches if isinstance(patch, Rectangle)]
        heights = dict(zip(labels, (bar.get_height() for bar in bars)))
        assert "unknown" not in heights
        assert heights["in-process"] == pytest.approx(50.0)
        assert heights["local-network"] == pytest.approx(50.0)
        assert heights["multiple labels"] == pytest.approx(0.0)
        assert ax.get_ylabel() == "% of classified API tests"
    finally:
        plt.close(figure)


def _stats_dir(name: str, payloads: dict) -> StatsDirectory:
    return StatsDirectory(name=name, path=Path(f"/stats/{name}"), payloads=payloads)


def test_generate_all_figures_writes_comparison_and_dev_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dev_payloads: dict,
    tool_payloads: dict,
) -> None:
    from gerbil.figures import runner

    monkeypatch.setattr(runner, "COMPARISON_FIGURES", runner.COMPARISON_FIGURES[:2])
    monkeypatch.setattr(runner, "DEV_FIGURES", runner.DEV_FIGURES[:1])
    stats_dirs = [
        _stats_dir("dev-stats", dev_payloads),
        _stats_dir("tool-stats", tool_payloads),
    ]

    results, skipped = runner.generate_all_figures(
        stats_dirs, "dev-stats", tmp_path / "figures"
    )

    assert skipped == []
    assert [result.name for result in results] == [
        f"comparison/{runner.COMPARISON_FIGURES[0][0]}",
        f"comparison/{runner.COMPARISON_FIGURES[1][0]}",
        f"dev/{runner.COMPARISON_FIGURES[0][0]}",
        f"dev/{runner.COMPARISON_FIGURES[1][0]}",
        f"dev/{runner.DEV_FIGURES[0][0]}",
    ]
    for result in results:
        assert [path.suffix for path in result.paths] == [".png", ".pdf"]
        for path in result.paths:
            assert path.is_file()


def test_generate_all_figures_skips_missing_stems(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dev_payloads: dict,
    tool_payloads: dict,
) -> None:
    from gerbil.figures import runner

    monkeypatch.setattr(
        runner,
        "COMPARISON_FIGURES",
        tuple(
            spec
            for spec in runner.COMPARISON_FIGURES
            if spec[0] == "auth_handling_labels"
        ),
    )
    monkeypatch.setattr(
        runner,
        "DEV_FIGURES",
        tuple(spec for spec in runner.DEV_FIGURES if spec[0] == "testing_frameworks"),
    )
    # No directory has the auth payload; the dev directory lacks the framework one.
    dev_without = {
        stem: payload
        for stem, payload in dev_payloads.items()
        if stem not in (AUTH_HANDLING_DISTRIBUTION, TESTING_FRAMEWORK_DISTRIBUTION)
    }
    tool_without = {
        stem: payload
        for stem, payload in tool_payloads.items()
        if stem != AUTH_HANDLING_DISTRIBUTION
    }
    stats_dirs = [
        _stats_dir("dev-stats", dev_without),
        _stats_dir("tool-stats", tool_without),
    ]

    results, skipped = runner.generate_all_figures(
        stats_dirs, "dev-stats", tmp_path / "figures"
    )

    assert results == []
    assert [(skip.name, skip.reason) for skip in skipped] == [
        (
            "comparison/auth_handling_labels",
            f"no statistics directory has {AUTH_HANDLING_DISTRIBUTION}.json",
        ),
        (
            "dev/auth_handling_labels",
            f"dev-stats has no {AUTH_HANDLING_DISTRIBUTION}.json",
        ),
        (
            "dev/testing_frameworks",
            f"dev-stats has no {TESTING_FRAMEWORK_DISTRIBUTION}.json",
        ),
    ]


def test_comparison_figures_build_from_dirs_that_have_the_stem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dev_payloads: dict,
    tool_payloads: dict,
) -> None:
    from gerbil.figures import runner

    monkeypatch.setattr(
        runner,
        "COMPARISON_FIGURES",
        tuple(
            spec
            for spec in runner.COMPARISON_FIGURES
            if spec[0] == "auth_handling_labels"
        ),
    )
    monkeypatch.setattr(runner, "DEV_FIGURES", ())
    tool_without = {
        stem: payload
        for stem, payload in tool_payloads.items()
        if stem != AUTH_HANDLING_DISTRIBUTION
    }
    stats_dirs = [
        _stats_dir("dev-stats", dev_payloads),
        _stats_dir("tool-stats", tool_without),
    ]

    results, skipped = runner.generate_all_figures(
        stats_dirs, "dev-stats", tmp_path / "figures"
    )

    # The figure still renders from the directories that carry the payload.
    assert skipped == []
    assert [result.name for result in results] == [
        "comparison/auth_handling_labels",
        "dev/auth_handling_labels",
    ]
