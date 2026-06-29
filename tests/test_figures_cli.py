from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from gerbil import cli
from gerbil.statistics.runner import write_statistics
from tests.figures_builders import dev_statistics, tool_statistics

_COMPARISON_FIGURES = (
    "test_metrics_api",
    "auth_handling_labels",
    "dependency_strategies",
    "request_dispatch_labels",
    "assertion_targets",
    "assertion_surface_combinations",
    "assertion_oracle_types",
    "assertion_status_ranges",
    "http_behavior_location",
    "http_test_structure",
    "http_sequences",
    "http_sequence_shares",
    "endpoint_coverage",
    "endpoint_parameter_surface",
    "parameter_exercise",
    "resource_operations",
    "resource_exercise",
    "resource_lifecycle_labels",
)

_DEV_FIGURES = (
    "testing_frameworks",
    "http_dispatch_framework_call_sites",
    "http_dispatch_framework_events",
    "http_sequence_shapes",
    "project_composition",
    "state_conditions",
    "assertion_exact_status_codes",
    "test_metrics_breakdown",
    "request_dispatch_metrics",
    "request_dispatch_outcomes",
    "endpoint_coverage_buckets",
    "test_scope_sankey",
)


def _sample_stats_root(stats_root: Path) -> None:
    write_statistics(dev_statistics(), stats_root / "hamster-stats")
    write_statistics(tool_statistics(), stats_root / "tool-stats")


def test_figures_command_writes_comparison_and_dev_figures(tmp_path: Path) -> None:
    stats_root = tmp_path / "stats"
    output_dir = tmp_path / "figures"
    _sample_stats_root(stats_root)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "figures",
            "--stats-root",
            str(stats_root),
            "--output-dir",
            str(output_dir),
            "--dev-dir",
            "hamster-stats",
        ],
    )

    assert result.exit_code == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["stats_dirs"] == ["hamster-stats", "tool-stats"]
    assert summary["dev_dir"] == "hamster-stats"
    assert summary["ignore_unknown"] is False
    assert summary["figures"] == 2 * len(_COMPARISON_FIGURES) + len(_DEV_FIGURES)
    assert summary["skipped"] == []
    assert len(summary["outputs"]) == 2 * summary["figures"]

    for name in _COMPARISON_FIGURES:
        assert (output_dir / "comparison" / f"{name}.png").is_file()
        assert (output_dir / "comparison" / f"{name}.pdf").is_file()
        assert (output_dir / "dev" / f"{name}.png").is_file()
        assert (output_dir / "dev" / f"{name}.pdf").is_file()
    for name in _DEV_FIGURES:
        assert (output_dir / "dev" / f"{name}.png").is_file()
        assert (output_dir / "dev" / f"{name}.pdf").is_file()


def test_figures_command_ignore_unknown_flag(tmp_path: Path) -> None:
    stats_root = tmp_path / "stats"
    _sample_stats_root(stats_root)
    # Trim to the dispatch payload so only the dispatch figures render.
    for stats_dir in (stats_root / "hamster-stats", stats_root / "tool-stats"):
        for path in stats_dir.glob("*.json"):
            if path.stem != "request_dispatch_distribution":
                path.unlink()

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "figures",
            "--stats-root",
            str(stats_root),
            "--output-dir",
            str(tmp_path / "figures"),
            "--dev-dir",
            "hamster-stats",
            "--ignore-unknown",
        ],
    )

    assert result.exit_code == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["ignore_unknown"] is True
    assert summary["figures"] == 4
    skipped = {entry["figure"] for entry in summary["skipped"]}
    assert len(skipped) == 2 * len(_COMPARISON_FIGURES) + len(_DEV_FIGURES) - 4
    assert (
        tmp_path / "figures" / "comparison" / "request_dispatch_labels.png"
    ).is_file()
    assert (tmp_path / "figures" / "dev" / "request_dispatch_labels.png").is_file()
    assert (tmp_path / "figures" / "dev" / "request_dispatch_metrics.png").is_file()
    assert (tmp_path / "figures" / "dev" / "request_dispatch_outcomes.png").is_file()


def test_figures_command_reports_skips_for_missing_dev_payloads(
    tmp_path: Path,
) -> None:
    stats_root = tmp_path / "stats"
    _sample_stats_root(stats_root)
    (stats_root / "hamster-stats" / "testing_framework_distribution.json").unlink()

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "figures",
            "--stats-root",
            str(stats_root),
            "--output-dir",
            str(tmp_path / "figures"),
            "--dev-dir",
            "hamster-stats",
        ],
    )

    assert result.exit_code == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["skipped"] == [
        {
            "figure": "dev/testing_frameworks",
            "reason": "hamster-stats has no testing_framework_distribution.json",
        }
    ]


def test_figures_command_rejects_missing_stats_root(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "figures",
            "--stats-root",
            str(tmp_path / "missing"),
            "--output-dir",
            str(tmp_path / "figures"),
            "--dev-dir",
            "hamster-stats",
        ],
    )

    assert result.exit_code != 0
    assert "stats_root does not exist" in result.stderr


def test_figures_command_rejects_unknown_dev_dir(tmp_path: Path) -> None:
    stats_root = tmp_path / "stats"
    _sample_stats_root(stats_root)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "figures",
            "--stats-root",
            str(stats_root),
            "--output-dir",
            str(tmp_path / "figures"),
            "--dev-dir",
            "nope-stats",
        ],
    )

    assert result.exit_code != 0
    assert "dev_dir 'nope-stats' not found" in result.stderr
