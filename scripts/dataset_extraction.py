#!/usr/bin/env python3
"""Build JSON project lists from per-project Gerbil output directories."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from gerbil.analysis.schema import ProjectAnalysis


MODES = ("hamster", "gerbil")


def _api_test_count(analysis: ProjectAnalysis) -> int:
    return sum(
        1
        for test_class in analysis.test_class_analyses
        for test in test_class.test_method_analyses
        if test.is_api_test
    )


def load_api_test_count(gerbil_path: Path) -> int:
    analysis = ProjectAnalysis.model_validate_json(gerbil_path.read_bytes())
    return _api_test_count(analysis)


def discover_project_dirs(project_root: Path) -> list[Path]:
    return sorted(
        path
        for path in project_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def build_dataset(project_root: Path, *, mode: str) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    if not project_root.is_dir():
        raise NotADirectoryError(f"project root is not a directory: {project_root}")

    gerbil_mode = mode == "gerbil"
    projects: list[dict[str, Any]] = []
    for project_dir in discover_project_dirs(project_root):
        entry: dict[str, Any] = {
            "name": project_dir.name,
            "project_dir": str(project_dir.resolve()),
        }
        if gerbil_mode:
            gerbil_path = project_dir / "gerbil.json"
            if not gerbil_path.is_file():
                raise FileNotFoundError(f"missing gerbil.json: {gerbil_path}")
            api_test_count = load_api_test_count(gerbil_path)
            if api_test_count == 0:
                continue
            entry["gerbil_path"] = str(gerbil_path.resolve())
            entry["api_test_count"] = api_test_count
        projects.append(entry)

    return {
        "project_root": str(project_root.resolve()),
        "mode": mode,
        "gerbil": gerbil_mode,
        "project_count": len(projects),
        "projects": projects,
    }


def write_dataset(payload: dict[str, Any], output_dir: Path, *, filename: str) -> Path:
    output_path = output_dir / filename
    if output_path.name != filename:
        raise ValueError(f"filename must not include directories: {filename}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--mode", choices=MODES, default="hamster")
    args = parser.parse_args()

    try:
        payload = build_dataset(args.project_root, mode=args.mode)
        output_path = write_dataset(payload, args.output_dir, filename=args.filename)
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports concise errors
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "output_file": str(output_path),
                "project_count": payload["project_count"],
                "gerbil": payload["gerbil"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
