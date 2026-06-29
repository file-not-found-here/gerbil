"""Figures for the assertion_verification_distribution statistics."""

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
    grouped_bars,
    grouped_summary_boxes,
    horizontal_bars,
    new_figure,
    ordered_union,
    prune_zero_categories,
    renormalize_unknown_share,
    stacked_share_bars,
)

EXACT_CODE_TOP_N = 15
EXCEPTION_CATEGORY = "exception"


def build_targets(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Response-surface assertion shares by target plus per-test target boxes."""
    targets = ordered_union(
        list(payload["assertion_targets"]["by_target"]) for payload in payloads.values()
    )
    figure, axes = new_figure(ncols=2, panel_width=5.4)
    share_series = [
        (
            name,
            colors[name],
            [
                (
                    payload["assertion_targets"]["by_target"]
                    .get(target, {})
                    .get("proportion")
                    or 0.0
                )
                * 100.0
                for target in targets
            ],
        )
        for name, payload in payloads.items()
    ]
    grouped_bars(
        axes[0], targets, share_series, ylabel="% of response-surface assertions"
    )
    axes[0].set_title("Assertion share by target", fontsize=12)

    box_series = [
        (
            name,
            colors[name],
            [payload["target_assertions_per_test"].get(target) for target in targets],
        )
        for name, payload in payloads.items()
    ]
    grouped_summary_boxes(
        axes[1], targets, box_series, ylabel="assertions per API test"
    )
    axes[1].set_title("Target assertions per test", fontsize=12)
    add_series_legend(figure, {name: colors[name] for name in payloads})
    add_box_note(figure)
    return figure


def build_surface_combinations(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Stacked response-surface combination shares, one bar per directory."""
    categories = ordered_union(
        list(payload["response_surface_combinations"]["by_combination"])
        for payload in payloads.values()
    )
    rows = [
        [
            (
                payload["response_surface_combinations"]["by_combination"]
                .get(category, {})
                .get("proportion")
                or 0.0
            )
            * 100.0
            for category in categories
        ]
        for payload in payloads.values()
    ]
    figure, axes = new_figure(
        panel_width=8.0,
        panel_height=0.8 * len(payloads) + 1.6,
    )
    stacked_share_bars(
        axes[0], list(payloads), categories, rows, xlabel="% of API tests"
    )
    figure.legend(loc="outside lower center", ncols=4, frameon=False, fontsize=11)
    return figure


def build_oracle_types(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Stacked oracle-type shares, one bar per directory."""
    categories = ordered_union(
        list(payload["oracle_types"]["by_type"]) for payload in payloads.values()
    )
    rows = [
        [
            (
                payload["oracle_types"]["by_type"].get(category, {}).get("proportion")
                or 0.0
            )
            * 100.0
            for category in categories
        ]
        for payload in payloads.values()
    ]
    figure, axes = new_figure(
        panel_width=8.0,
        panel_height=0.8 * len(payloads) + 1.6,
    )
    stacked_share_bars(
        axes[0], list(payloads), categories, rows, xlabel="% of API tests"
    )
    figure.legend(loc="outside lower center", ncols=4, frameon=False, fontsize=11)
    return figure


def build_status_ranges(
    payloads: ComparisonPayloads, colors: SeriesColors, options: FigureOptions
) -> Figure:
    """Status-range assertion shares and per-test range/exception reach."""
    figure, axes = new_figure(ncols=2, panel_width=5.4)

    ranges = ordered_union(
        list(payload["status_assertions"]["range_assertion_counts"])
        for payload in payloads.values()
    )
    range_series = [
        (
            name,
            colors[name],
            [
                (
                    payload["status_assertions"]["range_assertion_counts"]
                    .get(range_key, {})
                    .get("proportion")
                    or 0.0
                )
                * 100.0
                for range_key in ranges
            ],
        )
        for name, payload in payloads.items()
    ]
    share_ylabel = "% of status assertions"
    if options.ignore_unknown:
        ranges, range_series = renormalize_unknown_share(ranges, range_series)
        share_ylabel = "% of classified status assertions"
    pruned_ranges, range_series = prune_zero_categories(ranges, range_series)
    grouped_bars(axes[0], pruned_ranges, range_series, ylabel=share_ylabel)
    axes[0].set_title("Assertion share by status range", fontsize=12)

    test_categories = ordered_union(
        list(payload["status_assertions"]["tests_with_range"])
        for payload in payloads.values()
    )
    test_series = [
        (
            name,
            colors[name],
            [
                (
                    payload["status_assertions"]["tests_with_range"]
                    .get(category, {})
                    .get("proportion")
                    or 0.0
                )
                * 100.0
                for category in test_categories
            ]
            + [(payload["has_exception_assertion"]["proportion"] or 0.0) * 100.0],
        )
        for name, payload in payloads.items()
    ]
    grouped_bars(
        axes[1],
        [*test_categories, EXCEPTION_CATEGORY],
        test_series,
        ylabel="% of API tests",
    )
    axes[1].set_title("Tests asserting a range / exception", fontsize=12)
    add_series_legend(figure, {name: colors[name] for name in payloads})
    return figure


def build_dev_exact_status_codes(
    payload: Mapping[str, Any], options: FigureOptions
) -> Figure:
    """Exact status-code assertion distribution for the dev directory."""
    assertion_shares = payload["status_assertions"][
        "exact_status_code_assertion_counts"
    ]
    ordered_codes = sorted(
        assertion_shares, key=lambda code: assertion_shares[code]["count"], reverse=True
    )
    top_codes = ordered_codes[:EXACT_CODE_TOP_N]
    remainder = sum(
        assertion_shares[code]["count"] for code in ordered_codes[EXACT_CODE_TOP_N:]
    )

    figure, axes = new_figure(
        ncols=2,
        panel_width=5.4,
        panel_height=0.3 * len(top_codes) + 1.6,
    )
    labels = list(top_codes)
    values = [
        (assertion_shares[code]["proportion"] or 0.0) * 100.0 for code in top_codes
    ]
    total = payload["status_assertions"]["exact_status_code_assertion_count"]
    if remainder:
        labels.append("other")
        values.append(remainder / total * 100.0 if total else 0.0)
    horizontal_bars(
        axes[0], labels, values, color="#4878d0", xlabel="% of exact status assertions"
    )
    axes[0].set_title("Assertion share by code", fontsize=12)

    test_shares = payload["status_assertions"]["tests_with_exact_status_code"]
    test_values = [
        (test_shares.get(code, {}).get("proportion") or 0.0) * 100.0
        for code in top_codes
    ]
    horizontal_bars(
        axes[1], list(top_codes), test_values, color="#ee854a", xlabel="% of API tests"
    )
    axes[1].set_title("Tests asserting the code", fontsize=12)
    return figure
