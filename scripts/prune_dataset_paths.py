#!/usr/bin/env python3
"""Prune dataset JSON path fields: drop them entirely, or rewrite them repo-relative."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PATH_FIELDS = ("project_root", "project_dir", "gerbil_path")


def _relativize(value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        return value
    return str(path.relative_to(REPO_ROOT))


def prune_dataset(dataset: dict[str, Any], *, relative: bool) -> None:
    entries = [dataset, *dataset.get("projects", [])]
    for entry in entries:
        for field in PATH_FIELDS:
            if field not in entry:
                continue
            if relative:
                entry[field] = _relativize(entry[field])
            else:
                del entry[field]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument(
        "--relative",
        action="store_true",
        help="rewrite paths repo-relative instead of removing them",
    )
    args = parser.parse_args()

    for file in args.files:
        dataset = json.loads(file.read_text())
        prune_dataset(dataset, relative=args.relative)
        file.write_text(json.dumps(dataset, indent=2) + "\n")
        print(f"pruned {file}")


if __name__ == "__main__":
    main()
