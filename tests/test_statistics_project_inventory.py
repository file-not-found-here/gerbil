from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from gerbil import cli
from gerbil.analysis.schema import ProjectAnalysis
from gerbil.statistics.project_inventory import (
    build_inventory_payload,
    collect_api_test_projects,
    project_inventory_entry,
    write_inventory,
)
from gerbil.statistics.records import ProjectStatsRecord, project_project
from tests.statistics_builders import (
    api_test,
    endpoint_entry,
    non_api_test,
    project,
    write_gerbil_output,
)


def _project_analysis(
    name: str,
    *,
    api_tests: int = 1,
    non_api_tests: int = 0,
    endpoints: int = 1,
    application_class_count: int = 0,
    application_method_count: int = 0,
) -> ProjectAnalysis:
    return project(
        dataset_name=name,
        tests=[api_test() for _ in range(api_tests)]
        + [non_api_test() for _ in range(non_api_tests)],
        endpoints=[endpoint_entry(covering_test_count=0) for _ in range(endpoints)],
        application_class_count=application_class_count,
        application_method_count=application_method_count,
    )


def _record(name: str, **kwargs: int) -> ProjectStatsRecord:
    return project_project(_project_analysis(name, **kwargs))


# --- project_inventory_entry ----------------------------------------------


def test_inventory_entry_projects_each_count() -> None:
    record = _record(
        "svc",
        api_tests=3,
        non_api_tests=2,
        endpoints=4,
        application_class_count=7,
        application_method_count=42,
    )

    entry = project_inventory_entry(record)

    assert entry.dataset_name == "svc"
    assert entry.application_class_count == 7
    assert entry.application_method_count == 42
    assert entry.api_test_count == 3
    assert entry.non_api_test_count == 2
    assert entry.endpoint_count == 4


# --- collect_api_test_projects --------------------------------------------


def test_collect_excludes_projects_without_api_tests() -> None:
    records = [
        _record("has-api", api_tests=2),
        # Tests, but none are API tests: outside the inventory.
        _record("non-api-only", api_tests=0, non_api_tests=3),
    ]

    entries = collect_api_test_projects(records)

    assert [entry.dataset_name for entry in entries] == ["has-api"]


def test_collect_orders_by_api_test_count_then_name() -> None:
    records = [
        _record("two-z", api_tests=2),
        _record("four", api_tests=4),
        _record("two-a", api_tests=2),
        _record("one", api_tests=1),
    ]

    entries = collect_api_test_projects(records)

    # Most API tests first; equal-count projects break the tie by dataset_name.
    assert [entry.dataset_name for entry in entries] == [
        "four",
        "two-a",
        "two-z",
        "one",
    ]


# --- build_inventory_payload ----------------------------------------------


def test_build_payload_reports_summary_and_projects() -> None:
    records = [
        _record(
            "top",
            api_tests=3,
            non_api_tests=1,
            endpoints=2,
            application_class_count=5,
            application_method_count=20,
        ),
        _record(
            "next",
            api_tests=1,
            non_api_tests=2,
            endpoints=1,
            application_class_count=3,
            application_method_count=8,
        ),
    ]
    entries = collect_api_test_projects(records)

    payload = build_inventory_payload(entries)

    assert payload["scope"] == "projects_with_api_tests"
    assert payload["project_count"] == 2
    assert payload["summary"] == {
        "application_class_count": 8,
        "application_method_count": 28,
        "api_test_count": 4,
        "non_api_test_count": 3,
        "endpoint_count": 3,
    }
    assert [entry["dataset_name"] for entry in payload["projects"]] == ["top", "next"]
    top = payload["projects"][0]
    assert top == {
        "dataset_name": "top",
        "application_class_count": 5,
        "application_method_count": 20,
        "api_test_count": 3,
        "non_api_test_count": 1,
        "endpoint_count": 2,
    }


def test_write_inventory_round_trips_empty_payload(tmp_path: Path) -> None:
    payload = build_inventory_payload([])

    output_file = write_inventory(payload, tmp_path / "out")

    assert output_file == tmp_path / "out" / "api_test_projects.json"
    written = json.loads(output_file.read_text())
    assert written["projects"] == []
    assert written["project_count"] == 0
    assert written["summary"] == {
        "application_class_count": 0,
        "application_method_count": 0,
        "api_test_count": 0,
        "non_api_test_count": 0,
        "endpoint_count": 0,
    }


# --- CLI ------------------------------------------------------------------


def _corpus(input_root: Path) -> None:
    write_gerbil_output(
        input_root,
        "alpha",
        _project_analysis(
            "alpha",
            api_tests=3,
            non_api_tests=1,
            endpoints=2,
            application_class_count=5,
            application_method_count=20,
        ),
    )
    write_gerbil_output(
        input_root,
        "beta",
        _project_analysis(
            "beta",
            api_tests=2,
            endpoints=1,
            application_class_count=3,
            application_method_count=8,
        ),
    )
    # No API tests: excluded from the inventory.
    write_gerbil_output(
        input_root, "gamma", project(dataset_name="gamma", tests=[non_api_test()])
    )


def test_api_test_projects_writes_inventory(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    _corpus(input_root)

    result = CliRunner().invoke(
        cli.app,
        [
            "api-test-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["analyses"] == 3
    assert summary["loaded"] == 3
    assert summary["failed"] == 0
    assert summary["api_test_projects"] == 2

    payload = json.loads((output_dir / "api_test_projects.json").read_text())
    # alpha (3 API tests) ranks ahead of beta (2); gamma has none and is excluded.
    assert [entry["dataset_name"] for entry in payload["projects"]] == ["alpha", "beta"]
    assert payload["summary"] == {
        "application_class_count": 8,
        "application_method_count": 28,
        "api_test_count": 5,
        "non_api_test_count": 1,
        "endpoint_count": 3,
    }


def test_api_test_projects_runs_in_parallel_worker_processes(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    _corpus(input_root)

    result = CliRunner().invoke(
        cli.app,
        [
            "api-test-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "2",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["loaded"] == 3
    assert (output_dir / "api_test_projects.json").is_file()


def test_api_test_projects_exits_nonzero_on_load_failure(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    _corpus(input_root)
    (input_root / "broken").mkdir()
    (input_root / "broken" / "gerbil.json").write_text("nope", encoding="utf-8")

    result = CliRunner().invoke(
        cli.app,
        [
            "api-test-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 1
    summary = json.loads(result.stdout)
    assert summary["failed"] == 1
    # The broken project never reaches the inventory; the loadable ones still do.
    payload = json.loads((output_dir / "api_test_projects.json").read_text())
    assert [entry["dataset_name"] for entry in payload["projects"]] == ["alpha", "beta"]


def test_api_test_projects_writes_empty_file_when_none_have_api_tests(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    write_gerbil_output(
        input_root, "gamma", project(dataset_name="gamma", tests=[non_api_test()])
    )
    write_gerbil_output(
        input_root, "delta", project(dataset_name="delta", tests=[non_api_test()])
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "api-test-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["api_test_projects"] == 0

    output_file = output_dir / "api_test_projects.json"
    assert output_file.is_file()
    payload = json.loads(output_file.read_text())
    assert payload["projects"] == []
    assert payload["project_count"] == 0


def test_api_test_projects_rejects_missing_input_root(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "api-test-projects",
            "--input-root",
            str(tmp_path / "missing"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code != 0
    assert "input_root does not exist" in result.stderr


def test_api_test_projects_rejects_input_root_without_outputs(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()

    result = CliRunner().invoke(
        cli.app,
        [
            "api-test-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code != 0
    assert "does not contain any gerbil.json outputs" in result.stderr
