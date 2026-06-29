#!/usr/bin/env python3
"""Report project directories under a target dir that never appear in a run .log file."""

import argparse
import re
import sys
from pathlib import Path

# Matches lines like "[2636/2647] ok: name (45.2s)" or "[2642/2647] failed: name: Command ...".
_LOG_LINE = re.compile(r"^\[\d+/\d+\]\s+\w+:\s+([^\s:]+)")


def _logged_projects(log_path: Path) -> set[str]:
    names: set[str] = set()
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = _LOG_LINE.match(line)
            if match:
                names.add(match.group(1))
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    args = parser.parse_args()

    if not args.log_path.is_file():
        print(f"error: log path is not a file: {args.log_path}", file=sys.stderr)
        return 1
    if not args.target_dir.is_dir():
        print(f"error: target dir is not a directory: {args.target_dir}", file=sys.stderr)
        return 1

    logged = _logged_projects(args.log_path)
    project_dirs = sorted(p.name for p in args.target_dir.iterdir() if p.is_dir())

    missing = [name for name in project_dirs if name not in logged]
    for name in missing:
        print(name)

    print(
        f"done: {len(missing)} of {len(project_dirs)} project(s) missing from log "
        f"({len(logged)} logged)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
