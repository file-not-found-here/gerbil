"""Figure generation over per-tool statistics outputs: cross-directory
comparisons plus dev-only detail figures."""

from gerbil.figures.loading import (
    StatsDirectory,
    load_stats_directories,
    load_stats_directory,
)
from gerbil.figures.plotting import FigureOptions
from gerbil.figures.runner import (
    COMPARISON_FIGURES,
    DEV_FIGURES,
    FigureResult,
    SkippedFigure,
    generate_all_figures,
)

__all__ = [
    "COMPARISON_FIGURES",
    "DEV_FIGURES",
    "FigureOptions",
    "FigureResult",
    "SkippedFigure",
    "StatsDirectory",
    "generate_all_figures",
    "load_stats_directories",
    "load_stats_directory",
]
