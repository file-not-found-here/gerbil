"""Loads per-tool statistics directories produced by the statistics runner."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StatsDirectory:
    """One tool's statistics output, with payloads keyed by file stem."""

    name: str
    path: Path
    payloads: dict[str, dict[str, Any]]


def load_stats_directory(path: Path) -> StatsDirectory:
    payloads = {
        entry.stem: json.loads(entry.read_text(encoding="utf-8"))
        for entry in sorted(path.glob("*.json"))
    }
    return StatsDirectory(name=path.name, path=path, payloads=payloads)


def load_stats_directories(stats_root: Path, dev_dir_name: str) -> list[StatsDirectory]:
    """Load every statistics directory under the root, dev directory first.

    Subdirectories without any .json payloads are ignored.
    """
    candidates = [
        entry
        for entry in sorted(stats_root.iterdir())
        if entry.is_dir() and not entry.name.startswith(".")
    ]
    loaded = [load_stats_directory(entry) for entry in candidates]
    loaded = [directory for directory in loaded if directory.payloads]
    names = [directory.name for directory in loaded]
    if not names:
        raise ValueError(
            f"stats_root does not contain any statistics directories: {stats_root}"
        )
    if dev_dir_name not in names:
        raise ValueError(
            f"dev_dir {dev_dir_name!r} not found among statistics directories: {names}"
        )
    return sorted(
        loaded, key=lambda directory: (directory.name != dev_dir_name, directory.name)
    )
