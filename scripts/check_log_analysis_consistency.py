#!/usr/bin/env python3
"""Cross-check a run .log against on-disk symbol_table/analysis.json files: a logged success must have analysis.json and a logged failure must not (plus flag orphan analysis with no log entry)."""

import argparse
import re
import sys
from pathlib import Path

# Matches lines like "[2636/2647] ok: name (45.2s)" or "[2642/2647] failed: name: Command ...".
_LOG_LINE = re.compile(r"^\[\d+/\d+\]\s+(\w+):\s+([^\s:]+)")


def _parse_log(log_path: Path) -> tuple[set[str], set[str]]:
    ok: set[str] = set()
    failed: set[str] = set()
    with log_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = _LOG_LINE.match(line)
            if not match:
                continue
            status, name = match.group(1), match.group(2)
            if status == "ok":
                ok.add(name)
            elif status == "failed":
                failed.add(name)
    return ok, failed


def _has_analysis(target_dir: Path, name: str) -> bool:
    return (target_dir / name / "symbol_table" / "analysis.json").is_file()


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

    ok, failed = _parse_log(args.log_path)
    on_disk = {p.name for p in args.target_dir.iterdir() if p.is_dir()}

    # Check A: every failed project must NOT have a symbol_table/analysis.json.
    a_violations: list[str] = []
    a_unchecked: list[str] = []
    for name in sorted(failed):
        if name not in on_disk:
            a_unchecked.append(name)
        elif _has_analysis(args.target_dir, name):
            a_violations.append(name)

    # Check B: every succeeded project MUST have a symbol_table/analysis.json.
    b_violations: list[tuple[str, str]] = []
    for name in sorted(ok):
        if not _has_analysis(args.target_dir, name):
            reason = "no-analysis-file" if name in on_disk else "no-project-dir"
            b_violations.append((name, reason))

    # Check C: an analysis.json on disk must correspond to a project in the log.
    c_violations = [
        name
        for name in sorted(on_disk)
        if _has_analysis(args.target_dir, name) and name not in ok and name not in failed
    ]

    print("Check A: failed projects must NOT have symbol_table/analysis.json")
    if a_violations:
        print(f"  FAIL: {len(a_violations)} violation(s)")
        for name in a_violations:
            print(f"    {name}")
    else:
        print("  OK: no failed project has analysis.json")
    if a_unchecked:
        print(
            f"  note: {len(a_unchecked)} failed project(s) absent from target-dir (not checked)"
        )
        for name in a_unchecked:
            print(f"    {name}")

    print("Check B: succeeded projects MUST have symbol_table/analysis.json")
    if b_violations:
        print(f"  FAIL: {len(b_violations)} violation(s)")
        for name, reason in b_violations:
            print(f"    {name} ({reason})")
    else:
        print("  OK: every succeeded project has analysis.json")

    print("Check C: each analysis.json must correspond to a logged project")
    if c_violations:
        print(f"  FAIL: {len(c_violations)} violation(s)")
        for name in c_violations:
            print(f"    {name} (missing-from-log)")
    else:
        print("  OK: no orphan analysis.json")

    total = len(a_violations) + len(b_violations) + len(c_violations)
    if total:
        print(f"result: {total} violation(s)", file=sys.stderr)
        return 1
    print("result: consistent", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
