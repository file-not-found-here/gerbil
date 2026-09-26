"""Builds and writes comparison and dev figures from loaded statistics directories."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from matplotlib.figure import Figure

from gerbil.figures import (
    assertion_verification,
    auth_handling,
    dependency_strategy,
    endpoints,
    frameworks,
    http_behavior,
    http_sequences,
    parameter_exercise,
    project_composition,
    request_dispatch,
    resource_interaction,
    state_conditions,
    test_metrics,
    test_scope,
)
from gerbil.figures.loading import StatsDirectory
from gerbil.figures.plotting import FigureOptions, save_figure, series_colors
from gerbil.statistics.runner import (
    ASSERTION_VERIFICATION_DISTRIBUTION,
    AUTH_HANDLING_DISTRIBUTION,
    DEPENDENCY_STRATEGY_DISTRIBUTION,
    ENDPOINT_DISTRIBUTION,
    HTTP_BEHAVIOR_LOCATION,
    HTTP_DISPATCH_FRAMEWORK_DISTRIBUTION,
    HTTP_DISPATCH_FRAMEWORK_EVENT_DISTRIBUTION,
    HTTP_TEST_SEQUENCE_DISTRIBUTION,
    PARAMETER_EXERCISE_DISTRIBUTION,
    PROJECT_COMPOSITION,
    REQUEST_DISPATCH_DISTRIBUTION,
    RESOURCE_INTERACTION_DISTRIBUTION,
    STATE_CONDITION_DISTRIBUTION,
    TEST_METRIC_COMPARISON,
    TEST_SCOPE_DISTRIBUTION,
    TESTING_FRAMEWORK_DISTRIBUTION,
)

COMPARISON_SUBDIR = "comparison"
DEV_SUBDIR = "dev"

ComparisonBuilder = Callable[
    [Mapping[str, Mapping[str, Any]], Mapping[str, str], FigureOptions], Figure
]
DevBuilder = Callable[[Mapping[str, Any], FigureOptions], Figure]

# (figure name, statistics file stem, builder)
COMPARISON_FIGURES: tuple[tuple[str, str, ComparisonBuilder], ...] = (
    ("test_metrics_api", TEST_METRIC_COMPARISON, test_metrics.build_api_metrics),
    ("auth_handling_labels", AUTH_HANDLING_DISTRIBUTION, auth_handling.build_labels),
    (
        "dependency_strategies",
        DEPENDENCY_STRATEGY_DISTRIBUTION,
        dependency_strategy.build_strategies,
    ),
    (
        "request_dispatch_labels",
        REQUEST_DISPATCH_DISTRIBUTION,
        request_dispatch.build_labels,
    ),
    (
        "assertion_targets",
        ASSERTION_VERIFICATION_DISTRIBUTION,
        assertion_verification.build_targets,
    ),
    (
        "assertion_surface_combinations",
        ASSERTION_VERIFICATION_DISTRIBUTION,
        assertion_verification.build_surface_combinations,
    ),
    (
        "assertion_oracle_types",
        ASSERTION_VERIFICATION_DISTRIBUTION,
        assertion_verification.build_oracle_types,
    ),
    (
        "assertion_status_ranges",
        ASSERTION_VERIFICATION_DISTRIBUTION,
        assertion_verification.build_status_ranges,
    ),
    ("http_behavior_location", HTTP_BEHAVIOR_LOCATION, http_behavior.build_location),
    (
        "http_test_structure",
        HTTP_BEHAVIOR_LOCATION,
        http_behavior.build_test_structure,
    ),
    (
        "http_sequences",
        HTTP_TEST_SEQUENCE_DISTRIBUTION,
        http_sequences.build_distributions,
    ),
    (
        "http_sequence_shares",
        HTTP_TEST_SEQUENCE_DISTRIBUTION,
        http_sequences.build_shares,
    ),
    ("endpoint_coverage", ENDPOINT_DISTRIBUTION, endpoints.build_coverage),
    (
        "endpoint_parameter_surface",
        ENDPOINT_DISTRIBUTION,
        endpoints.build_parameter_surface,
    ),
    (
        "parameter_exercise",
        PARAMETER_EXERCISE_DISTRIBUTION,
        parameter_exercise.build_exercise,
    ),
    (
        "resource_operations",
        RESOURCE_INTERACTION_DISTRIBUTION,
        resource_interaction.build_operations,
    ),
    (
        "resource_exercise",
        RESOURCE_INTERACTION_DISTRIBUTION,
        resource_interaction.build_exercise,
    ),
    (
        "resource_lifecycle_labels",
        RESOURCE_INTERACTION_DISTRIBUTION,
        resource_interaction.build_lifecycle_labels,
    ),
)

DEV_FIGURES: tuple[tuple[str, str, DevBuilder], ...] = (
    (
        "testing_frameworks",
        TESTING_FRAMEWORK_DISTRIBUTION,
        frameworks.build_dev_testing_frameworks,
    ),
    (
        "http_dispatch_framework_call_sites",
        HTTP_DISPATCH_FRAMEWORK_DISTRIBUTION,
        frameworks.build_dev_dispatch_call_sites,
    ),
    (
        "http_dispatch_framework_events",
        HTTP_DISPATCH_FRAMEWORK_EVENT_DISTRIBUTION,
        frameworks.build_dev_dispatch_events,
    ),
    (
        "http_sequence_shapes",
        HTTP_TEST_SEQUENCE_DISTRIBUTION,
        http_sequences.build_dev_sequence_shapes,
    ),
    (
        "project_composition",
        PROJECT_COMPOSITION,
        project_composition.build_dev_composition,
    ),
    (
        "state_conditions",
        STATE_CONDITION_DISTRIBUTION,
        state_conditions.build_dev_state_conditions,
    ),
    (
        "assertion_exact_status_codes",
        ASSERTION_VERIFICATION_DISTRIBUTION,
        assertion_verification.build_dev_exact_status_codes,
    ),
    (
        "test_metrics_breakdown",
        TEST_METRIC_COMPARISON,
        test_metrics.build_dev_metric_breakdown,
    ),
    (
        "request_dispatch_metrics",
        REQUEST_DISPATCH_DISTRIBUTION,
        request_dispatch.build_dev_label_metrics,
    ),
    (
        "request_dispatch_outcomes",
        REQUEST_DISPATCH_DISTRIBUTION,
        request_dispatch.build_dev_label_outcomes,
    ),
    (
        "endpoint_coverage_buckets",
        ENDPOINT_DISTRIBUTION,
        endpoints.build_dev_coverage_buckets,
    ),
    (
        "test_scope_sankey",
        TEST_SCOPE_DISTRIBUTION,
        test_scope.build_dev_scope_sankey,
    ),
)


@dataclass(frozen=True)
class FigureResult:
    """One generated figure and the files written for it."""

    name: str
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class SkippedFigure:
    """A figure that could not be generated, with the reason."""

    name: str
    reason: str


def generate_all_figures(
    stats_dirs: Sequence[StatsDirectory],
    dev_dir_name: str,
    output_dir: Path,
    options: FigureOptions = FigureOptions(),
) -> tuple[list[FigureResult], list[SkippedFigure]]:
    """Write every comparison and dev figure, returning results and skips."""
    dev_dir = next(
        directory for directory in stats_dirs if directory.name == dev_dir_name
    )
    colors = series_colors([directory.name for directory in stats_dirs])
    results: list[FigureResult] = []
    skipped: list[SkippedFigure] = []

    for name, stem, build in COMPARISON_FIGURES:
        figure_name = f"{COMPARISON_SUBDIR}/{name}"
        payloads = {
            directory.name: directory.payloads[stem]
            for directory in stats_dirs
            if stem in directory.payloads
        }
        if not payloads:
            skipped.append(
                SkippedFigure(
                    name=figure_name,
                    reason=f"no statistics directory has {stem}.json",
                )
            )
            continue
        figure = build(payloads, colors, options)
        paths = save_figure(figure, output_dir / COMPARISON_SUBDIR, name)
        results.append(FigureResult(name=figure_name, paths=tuple(paths)))

    for name, stem, build in COMPARISON_FIGURES:
        figure_name = f"{DEV_SUBDIR}/{name}"
        if stem not in dev_dir.payloads:
            skipped.append(
                SkippedFigure(
                    name=figure_name,
                    reason=f"{dev_dir.name} has no {stem}.json",
                )
            )
            continue
        figure = build({dev_dir.name: dev_dir.payloads[stem]}, colors, options)
        paths = save_figure(figure, output_dir / DEV_SUBDIR, name)
        results.append(FigureResult(name=figure_name, paths=tuple(paths)))

    for name, stem, build_dev in DEV_FIGURES:
        figure_name = f"{DEV_SUBDIR}/{name}"
        if stem not in dev_dir.payloads:
            skipped.append(
                SkippedFigure(
                    name=figure_name,
                    reason=f"{dev_dir.name} has no {stem}.json",
                )
            )
            continue
        figure = build_dev(dev_dir.payloads[stem], options)
        paths = save_figure(figure, output_dir / DEV_SUBDIR, name)
        results.append(FigureResult(name=figure_name, paths=tuple(paths)))

    return results, skipped
