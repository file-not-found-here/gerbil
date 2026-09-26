#!/usr/bin/env python3
"""Copy Claude Code message trajectories into normalized per-project directories."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExtractionResult:
    project: str
    source: str
    destination: str
    status: str


def project_name(raw_project_dir: Path) -> str:
    return raw_project_dir.name.split("__")[-1]


def discover_raw_project_dirs(input_root: Path) -> list[Path]:
    return sorted(
        path
        for path in input_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def extract_trajectories(
    input_root: Path,
    output_root: Path,
    *,
    overwrite: bool = False,
) -> list[ExtractionResult]:
    if not input_root.is_dir():
        raise NotADirectoryError(f"input root is not a directory: {input_root}")

    results: list[ExtractionResult] = []
    for raw_project_dir in discover_raw_project_dirs(input_root):
        project = project_name(raw_project_dir)
        source = raw_project_dir / "output" / "messages.jsonl"
        destination = output_root / project / "trajectories" / "messages.jsonl"
        status = "copied"
        if not source.is_file():
            status = "exists" if destination.is_file() else "missing"
        elif destination.exists() and not overwrite:
            status = "exists"
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        results.append(
            ExtractionResult(
                project=project,
                source=str(source),
                destination=str(destination),
                status=status,
            )
        )
    return results


def _summary(results: list[ExtractionResult]) -> dict[str, object]:
    counts = Counter(result.status for result in results)
    return {
        "projects": len(results),
        "copied": counts["copied"],
        "existing": counts["exists"],
        "missing": counts["missing"],
        "results": [asdict(result) for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-root", type=Path, default=Path("outputs/claude-code-raw")
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("outputs/claude_code/runs")
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    try:
        results = extract_trajectories(
            args.input_root,
            args.output_root,
            overwrite=args.overwrite,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports concise errors
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(_summary(results), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
