#!/usr/bin/env python3
"""Copy per-project symbol_table/analysis.json files from an input root into per-project output directories."""

import argparse
import shutil
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _copy_project(project_dir: Path, output_root: Path) -> str:
    analysis_file = project_dir / "symbol_table" / "analysis.json"
    if not analysis_file.is_file():
        return "no-source"
    dest_file = output_root / project_dir.name / "analysis.json"
    if dest_file.is_file():
        return "exists"
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(analysis_file, dest_file)
    return "copied"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=10)
    args = parser.parse_args()

    if args.jobs < 1:
        print(f"error: jobs must be at least 1: {args.jobs}", file=sys.stderr)
        return 1
    if not args.input_root.is_dir():
        print(
            f"error: input root is not a directory: {args.input_root}", file=sys.stderr
        )
        return 1

    project_dirs = sorted(p for p in args.input_root.iterdir() if p.is_dir())
    total = len(project_dirs)
    statuses: list[str] = []
    # Copies are I/O-bound, so threads provide the parallelism without process overhead.
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        future_to_project = {
            pool.submit(_copy_project, project_dir, args.output_root): project_dir
            for project_dir in project_dirs
        }
        # Report in completion order so output doubles as live progress.
        for done, future in enumerate(as_completed(future_to_project), start=1):
            project_dir = future_to_project[future]
            status = future.result()
            statuses.append(status)
            prefix = f"[{done}/{total}]"
            if status == "copied":
                dest_file = args.output_root / project_dir.name / "analysis.json"
                print(f"{prefix} copied: {project_dir.name} -> {dest_file}", flush=True)
            elif status == "exists":
                print(
                    f"{prefix} skip: {project_dir.name} (analysis.json already exists)",
                    file=sys.stderr,
                )
            else:
                print(
                    f"{prefix} skip: {project_dir.name} (no symbol_table/analysis.json)",
                    file=sys.stderr,
                )

    counts = Counter(statuses)
    print(
        f"done: {counts['copied']} copied, "
        f"{counts['exists']} skipped (existing), "
        f"{counts['no-source']} skipped (no source)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
