"""Parallel loading of gerbil.json analyses into compact ProjectStatsRecords."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from types import UnionType
from typing import Union, get_args, get_origin

from pydantic import BaseModel

from gerbil.analysis.schema import ProjectAnalysis
from gerbil.statistics.records import ProjectStatsRecord, project_project


@dataclass(frozen=True)
class LoadFailure:
    path: Path
    error: str


def discover_gerbil_files(input_root: Path) -> list[Path]:
    """All gerbil.json outputs under input_root, sorted by path."""
    return sorted(input_root.rglob("gerbil.json"))


_MISSING_FIELD_REPORT_LIMIT = 10


def _missing_schema_fields(
    annotation: object,
    value: object,
    path: str,
) -> list[str]:
    """Dotted paths of schema fields absent from a raw JSON value.

    Walks model fields, list/dict containers, and Model-or-None unions. Values
    of the wrong shape are left for pydantic validation to reject; only key
    absence (which defaults would mask) is reported here.
    """
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        for arm in get_args(annotation):
            if isinstance(arm, type) and issubclass(arm, BaseModel):
                return _missing_schema_fields(arm, value, path)
        return []
    if origin is list:
        if not isinstance(value, list):
            return []
        (item_annotation,) = get_args(annotation)
        return [
            missing_path
            for index, item in enumerate(value)
            for missing_path in _missing_schema_fields(
                item_annotation, item, f"{path}[{index}]"
            )
        ]
    if origin is dict:
        if not isinstance(value, dict):
            return []
        _, value_annotation = get_args(annotation)
        return [
            missing_path
            for key, item in value.items()
            for missing_path in _missing_schema_fields(
                value_annotation, item, f"{path}[{key}]"
            )
        ]
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if not isinstance(value, dict):
            return []
        missing: list[str] = []
        for field_name, field_info in annotation.model_fields.items():
            field_path = f"{path}.{field_name}" if path else field_name
            if field_name not in value:
                missing.append(field_path)
                continue
            missing.extend(
                _missing_schema_fields(
                    field_info.annotation, value[field_name], field_path
                )
            )
        return missing
    return []


def load_project_record(path: Path) -> ProjectStatsRecord:
    """Load and validate one gerbil.json, projecting it to a compact record.

    Top-level so it is picklable by spawn-based worker processes.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"gerbil.json must contain a JSON object, got {type(raw).__name__}"
        )
    # The schema's defaults would let a drifted file validate as an empty or
    # partially empty project, so completeness is enforced recursively here at
    # the loading boundary.
    missing = sorted(_missing_schema_fields(ProjectAnalysis, raw, ""))
    if missing:
        reported = missing[:_MISSING_FIELD_REPORT_LIMIT]
        overflow = len(missing) - len(reported)
        suffix = f" (+{overflow} more)" if overflow else ""
        raise ValueError(
            "gerbil.json is missing required ProjectAnalysis fields "
            f"(schema drift): {', '.join(reported)}{suffix}"
        )
    return project_project(ProjectAnalysis.model_validate(raw))


def load_project_records(
    paths: Sequence[Path],
    *,
    jobs: int,
    on_loaded: Callable[[Path], None] | None = None,
    on_failed: Callable[[Path, str], None] | None = None,
) -> tuple[list[ProjectStatsRecord], list[LoadFailure]]:
    """Load every analysis, parallelizing the CPU-bound parse/validate over `jobs`.

    Records are returned in `paths` order; failures are collected rather than
    raised so one corrupt output does not abort the corpus.
    """
    records_by_path: dict[Path, ProjectStatsRecord] = {}
    failures: list[LoadFailure] = []

    def _record_success(path: Path, record: ProjectStatsRecord) -> None:
        records_by_path[path] = record
        if on_loaded is not None:
            on_loaded(path)

    def _record_failure(path: Path, error: str) -> None:
        failures.append(LoadFailure(path=path, error=error))
        if on_failed is not None:
            on_failed(path, error)

    if jobs == 1:
        for path in paths:
            try:
                record = load_project_record(path)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                _record_failure(path, str(exc))
            else:
                _record_success(path, record)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            future_to_path = {
                pool.submit(load_project_record, path): path for path in paths
            }
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    record = future.result()
                except Exception as exc:  # noqa: BLE001 - reported, not raised
                    _record_failure(path, str(exc))
                else:
                    _record_success(path, record)

    records = [records_by_path[path] for path in paths if path in records_by_path]
    return records, failures
