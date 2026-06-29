"""Inventory of every project carrying API tests: per-project application class
and method counts, API/non-API test counts, and endpoint count."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gerbil.statistics.records import ProjectStatsRecord, api_test_count

INVENTORY_OUTPUT_FILENAME = "api_test_projects.json"


@dataclass(frozen=True)
class ProjectInventoryEntry:
    """One API-test project's headline counts."""

    dataset_name: str
    application_class_count: int
    application_method_count: int
    api_test_count: int
    non_api_test_count: int
    endpoint_count: int


def project_inventory_entry(record: ProjectStatsRecord) -> ProjectInventoryEntry:
    """Project one loaded record to its inventory entry."""
    api_tests = api_test_count(record)
    return ProjectInventoryEntry(
        dataset_name=record.dataset_name,
        application_class_count=record.application_class_count,
        application_method_count=record.application_method_count,
        api_test_count=api_tests,
        non_api_test_count=len(record.tests) - api_tests,
        endpoint_count=len(record.endpoints),
    )


def collect_api_test_projects(
    records: Sequence[ProjectStatsRecord],
) -> list[ProjectInventoryEntry]:
    """Every project carrying at least one API test, ranked by API test count.

    dataset_name breaks ties so equal-count projects keep a deterministic order.
    """
    entries = [
        project_inventory_entry(record)
        for record in records
        if api_test_count(record) > 0
    ]
    return sorted(
        entries, key=lambda entry: (-entry.api_test_count, entry.dataset_name)
    )


def build_inventory_payload(
    entries: Sequence[ProjectInventoryEntry],
) -> dict[str, Any]:
    """Serializable payload: corpus-wide totals and the per-project entries."""
    return {
        "scope": "projects_with_api_tests",
        "project_count": len(entries),
        "summary": {
            "application_class_count": sum(
                entry.application_class_count for entry in entries
            ),
            "application_method_count": sum(
                entry.application_method_count for entry in entries
            ),
            "api_test_count": sum(entry.api_test_count for entry in entries),
            "non_api_test_count": sum(entry.non_api_test_count for entry in entries),
            "endpoint_count": sum(entry.endpoint_count for entry in entries),
        },
        "projects": [
            {
                "dataset_name": entry.dataset_name,
                "application_class_count": entry.application_class_count,
                "application_method_count": entry.application_method_count,
                "api_test_count": entry.api_test_count,
                "non_api_test_count": entry.non_api_test_count,
                "endpoint_count": entry.endpoint_count,
            }
            for entry in entries
        ],
    }


def write_inventory(payload: dict[str, Any], output_dir: Path) -> Path:
    """Write the inventory payload to <output_dir>/api_test_projects.json."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / INVENTORY_OUTPUT_FILENAME
    output_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return output_file
