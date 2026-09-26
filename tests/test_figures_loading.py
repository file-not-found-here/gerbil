from __future__ import annotations

import json
from pathlib import Path

import pytest

from gerbil.figures.loading import load_stats_directories, load_stats_directory


def _write_stats_dir(root: Path, name: str, payloads: dict[str, dict]) -> Path:
    stats_dir = root / name
    stats_dir.mkdir(parents=True)
    for stem, payload in payloads.items():
        (stats_dir / f"{stem}.json").write_text(json.dumps(payload), encoding="utf-8")
    return stats_dir


def test_load_stats_directory_keys_payloads_by_stem(tmp_path: Path) -> None:
    stats_dir = _write_stats_dir(
        tmp_path, "tool-a", {"auth": {"x": 1}, "endpoints": {"y": 2}}
    )

    loaded = load_stats_directory(stats_dir)

    assert loaded.name == "tool-a"
    assert loaded.path == stats_dir
    assert loaded.payloads == {"auth": {"x": 1}, "endpoints": {"y": 2}}


def test_load_stats_directories_puts_dev_first_then_sorts(tmp_path: Path) -> None:
    _write_stats_dir(tmp_path, "zeta-stats", {"auth": {}})
    _write_stats_dir(tmp_path, "alpha-stats", {"auth": {}})
    _write_stats_dir(tmp_path, "mid-stats", {"auth": {}})

    loaded = load_stats_directories(tmp_path, "mid-stats")

    assert [directory.name for directory in loaded] == [
        "mid-stats",
        "alpha-stats",
        "zeta-stats",
    ]


def test_load_stats_directories_ignores_dirs_without_payloads(tmp_path: Path) -> None:
    _write_stats_dir(tmp_path, "tool-a", {"auth": {}})
    (tmp_path / "empty").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "stray.txt").write_text("not a dir", encoding="utf-8")

    loaded = load_stats_directories(tmp_path, "tool-a")

    assert [directory.name for directory in loaded] == ["tool-a"]


def test_load_stats_directories_rejects_missing_dev_dir(tmp_path: Path) -> None:
    _write_stats_dir(tmp_path, "tool-a", {"auth": {}})

    with pytest.raises(ValueError, match="dev_dir 'missing' not found"):
        load_stats_directories(tmp_path, "missing")


def test_load_stats_directories_rejects_empty_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not contain any statistics"):
        load_stats_directories(tmp_path, "anything")
