#!/usr/bin/env python3
"""Report repos under a repo-root that lack a corresponding analysis-root/<repo>/analysis.json file."""

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--analysis-root", type=Path, required=True)
    args = parser.parse_args()

    if not args.repo_root.is_dir():
        print(f"error: repo root is not a directory: {args.repo_root}", file=sys.stderr)
        return 1
    if not args.analysis_root.is_dir():
        print(
            f"error: analysis root is not a directory: {args.analysis_root}",
            file=sys.stderr,
        )
        return 1

    repos = sorted(p.name for p in args.repo_root.iterdir() if p.is_dir())

    missing = [
        name
        for name in repos
        if not (args.analysis_root / name / "analysis.json").is_file()
    ]
    for name in missing:
        print(name)

    print(
        f"done: {len(missing)} of {len(repos)} repo(s) missing analysis.json",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
