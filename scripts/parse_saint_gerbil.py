#!/usr/bin/env python3
"""Build the saint_gerbil_raw dataset: per-project repos with non-ASTER tests cleared and ASTER tests merged across strategies."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --- Test-scope detection (adapted from general-agent-eval java_test_clearing.py) ---

SOURCE_SET_NAMES = {
    "acceptancetest",
    "contracttest",
    "e2etest",
    "functionaltest",
    "integrationtest",
    "smoketest",
    "systemtest",
    "test",
    "testfixtures",
    "tests",
}
TEST_ROOT_DIR_NAMES = {"test", "tests"}
BROAD_TEST_SUPPORT_DIR_NAMES = {"fixture", "fixtures", "testdata", "testing"}
EXPLICIT_TEST_SUPPORT_DIR_NAMES = {
    "testfixture",
    "testfixtures",
    "testhelper",
    "testhelpers",
    "testresource",
    "testresources",
    "testutil",
    "testutils",
}
TEST_FILE_PATTERN = re.compile(
    r".*(?:Test|Tests|TestCase|IT|ITCase|IntegrationTest)\.(?:java|kt|groovy|scala)$"
)

ASTER_PREFIX = "ASTER_"
BUILD_DIR_NAMES = {"target", "build"}
COPY_IGNORE_NAMES = {".git", ".DS_Store"}


def _normalized_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _is_under_src_main(relative_parts: tuple[str, ...]) -> bool:
    lowered = tuple(part.lower() for part in relative_parts)
    for index, part in enumerate(lowered[:-1]):
        if part == "src" and lowered[index + 1] == "main":
            return True
    return False


def _source_set_rule(relative_parts: tuple[str, ...]) -> str | None:
    for index, part in enumerate(relative_parts[:-1]):
        if part.lower() != "src":
            continue
        if _normalized_name(relative_parts[index + 1]) in SOURCE_SET_NAMES:
            return f"src/{relative_parts[index + 1]} source set"
    return None


def _is_test_scoped(relative_parts: tuple[str, ...]) -> bool:
    if _source_set_rule(relative_parts) is not None:
        return True
    for part in relative_parts:
        normalized = _normalized_name(part)
        if (
            normalized in TEST_ROOT_DIR_NAMES
            or normalized in EXPLICIT_TEST_SUPPORT_DIR_NAMES
        ):
            return True
    return False


def _directory_rule(root: Path, path: Path) -> str | None:
    relative_parts = path.relative_to(root).parts
    if _is_under_src_main(relative_parts):
        return None
    source_set_rule = _source_set_rule(relative_parts)
    if source_set_rule is not None:
        return source_set_rule
    normalized = _normalized_name(path.name)
    if normalized in TEST_ROOT_DIR_NAMES:
        return f"{path.name} test directory"
    if normalized in EXPLICIT_TEST_SUPPORT_DIR_NAMES:
        return f"{path.name} test support directory"
    if normalized in BROAD_TEST_SUPPORT_DIR_NAMES and _is_test_scoped(
        relative_parts[:-1]
    ):
        return f"{path.name} test support directory"
    return None


def _file_rule(root: Path, path: Path) -> str | None:
    relative_parts = path.relative_to(root).parts
    if not TEST_FILE_PATTERN.fullmatch(path.name):
        return None
    if _is_under_src_main(relative_parts):
        return None
    if _is_test_scoped(relative_parts[:-1]):
        return "JVM test filename in test-scoped path"
    return None


def _is_aster_test_file(path: Path) -> bool:
    return path.suffix == ".java" and path.name.startswith(ASTER_PREFIX)


# --- Clearing ---


@dataclass
class ClearReport:
    removed_files: int = 0
    removed_dirs: int = 0
    preserved_aster: int = 0


def _prune_empty_dirs(scope: Path) -> int:
    """Remove empty directories within scope (and scope itself if emptied), bottom-up."""
    pruned = 0
    for current_dir, _dir_names, _file_names in os.walk(scope, topdown=False):
        path = Path(current_dir)
        try:
            next(path.iterdir())
        except StopIteration:
            path.rmdir()
            pruned += 1
        except FileNotFoundError:
            continue
    return pruned


def _clear_scope_preserving_aster(scope: Path, report: ClearReport) -> None:
    """Delete every non-ASTER file under a removable test scope, preserving ASTER tests."""
    for current_dir, _dir_names, file_names in os.walk(scope):
        current = Path(current_dir)
        for filename in file_names:
            path = current / filename
            if _is_aster_test_file(path):
                report.preserved_aster += 1
                continue
            path.unlink()
            report.removed_files += 1
    report.removed_dirs += _prune_empty_dirs(scope)


def clear_non_aster_tests(repo_root: Path) -> ClearReport:
    """Clear all non-ASTER tests in a repo via path-based test-scope detection, keeping ASTER tests."""
    report = ClearReport()
    for current_dir, dir_names, file_names in os.walk(repo_root, topdown=True):
        current = Path(current_dir)
        if ".git" in dir_names:
            dir_names.remove(".git")

        for dirname in tuple(dir_names):
            path = current / dirname
            if _directory_rule(repo_root, path) is not None:
                dir_names.remove(dirname)  # handled here; do not descend
                _clear_scope_preserving_aster(path, report)

        # Stray test-pattern files in test-scoped paths outside a removed scope.
        for filename in file_names:
            path = current / filename
            if _is_aster_test_file(path):
                continue
            if _file_rule(repo_root, path) is not None:
                path.unlink()
                report.removed_files += 1
    return report


# --- Merge ---


def _rename_java_class(content: str, old_class: str, new_class: str) -> str:
    return re.sub(rf"\b{re.escape(old_class)}\b", new_class, content)


def _merge_aster_file(src: Path, dest: Path) -> bool:
    """Copy an ASTER test into dest, incrementing the name (and public class) on conflict. Returns True if renamed."""
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return False

    stem, parent = dest.stem, dest.parent
    index = 1
    while (candidate := parent / f"{stem}_{index}.java").exists():
        index += 1
    candidate.parent.mkdir(parents=True, exist_ok=True)
    content = _rename_java_class(src.read_text(encoding="utf-8"), stem, candidate.stem)
    candidate.write_text(content, encoding="utf-8")
    return True


@dataclass
class ProjectReport:
    name: str
    base_strategy: str
    clear: ClearReport = field(default_factory=ClearReport)
    merged: int = 0
    conflicts: int = 0
    merged_per_strategy: dict[str, int] = field(default_factory=dict)


def _make_copy_ignore(keep_build: bool):
    def _ignore(_directory: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in COPY_IGNORE_NAMES}
        if not keep_build:
            ignored |= {name for name in names if name in BUILD_DIR_NAMES}
        return ignored

    return _ignore


def _discover_strategies(input_dir: Path) -> list[str]:
    return sorted(p.name for p in input_dir.iterdir() if p.is_dir())


def _discover_projects(input_dir: Path, strategies: list[str]) -> list[str]:
    projects: set[str] = set()
    for strategy in strategies:
        for project_dir in (input_dir / strategy).iterdir():
            if project_dir.is_dir():
                projects.add(project_dir.name)
    return sorted(projects)


def process_project(
    name: str,
    input_dir: Path,
    strategies: list[str],
    output_root: Path,
    keep_build: bool,
) -> ProjectReport:
    present = [s for s in strategies if (input_dir / s / name).is_dir()]
    base_strategy = present[0]
    report = ProjectReport(name=name, base_strategy=base_strategy)

    out_repo = output_root / name
    if out_repo.exists():
        shutil.rmtree(out_repo)
    shutil.copytree(
        input_dir / base_strategy / name,
        out_repo,
        ignore=_make_copy_ignore(keep_build),
    )

    # Base repo's own ASTER tests survive clearing; only non-ASTER tests are removed.
    report.clear = clear_non_aster_tests(out_repo)
    report.merged_per_strategy[base_strategy] = report.clear.preserved_aster
    report.merged += report.clear.preserved_aster

    for strategy in present[1:]:
        src_repo = input_dir / strategy / name
        count = 0
        for src in sorted(src_repo.rglob("*.java")):
            if not _is_aster_test_file(src):
                continue
            if any(part in BUILD_DIR_NAMES for part in src.relative_to(src_repo).parts):
                continue
            dest = out_repo / src.relative_to(src_repo)
            if _merge_aster_file(src, dest):
                report.conflicts += 1
            count += 1
        report.merged_per_strategy[strategy] = count
        report.merged += count

    return report


def main() -> int:
    gerbil_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=gerbil_root / "outputs" / "saint_gerbil_unprocessed",
        help="Root holding per-strategy subdirectories of projects (default: outputs/saint_gerbil_unprocessed).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=gerbil_root / "outputs",
        help="Directory under which saint_gerbil_raw/ is written (default: outputs/, relative to the Gerbil root).",
    )
    parser.add_argument(
        "--keep-build",
        action="store_true",
        help="Keep build output dirs (target/, build/); excluded by default since stale test classes would re-run.",
    )
    args = parser.parse_args()

    input_dir: Path = args.input_dir.resolve()
    if not input_dir.is_dir():
        print(f"error: input dir not found: {input_dir}", file=sys.stderr)
        return 1

    strategies = _discover_strategies(input_dir)
    if not strategies:
        print(f"error: no strategy subdirectories under {input_dir}", file=sys.stderr)
        return 1

    output_root = (args.output_dir / "saint_gerbil_raw").resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    projects = _discover_projects(input_dir, strategies)

    print(f"strategies: {', '.join(strategies)}")
    print(f"output:     {output_root}\n")

    for name in projects:
        report = process_project(
            name, input_dir, strategies, output_root, args.keep_build
        )
        per_strategy = ", ".join(
            f"{s}={n}" for s, n in report.merged_per_strategy.items()
        )
        print(
            f"{name}: base={report.base_strategy} "
            f"cleared {report.clear.removed_files} files / {report.clear.removed_dirs} dirs, "
            f"merged {report.merged} ASTER tests ({per_strategy}), "
            f"{report.conflicts} renamed"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
