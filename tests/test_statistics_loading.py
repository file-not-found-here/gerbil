from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from gerbil.analysis.schema import ProjectAnalysis
from gerbil.statistics.loading import (
    discover_gerbil_files,
    load_project_record,
    load_project_records,
)
from tests.statistics_builders import (
    api_test,
    endpoint_entry,
    non_api_test,
    project,
    write_gerbil_output,
)


def _two_good_projects(input_root: Path) -> None:
    write_gerbil_output(
        input_root,
        "service-a",
        project(dataset_name="service-a", tests=[api_test(), non_api_test()]),
    )
    write_gerbil_output(
        input_root,
        "service-b",
        project(
            dataset_name="service-b",
            tests=[api_test()],
            endpoints=[endpoint_entry(covering_test_count=1)],
        ),
    )


def _write_corrupt(input_root: Path, name: str) -> Path:
    project_dir = input_root / name
    project_dir.mkdir(parents=True)
    corrupt = project_dir / "gerbil.json"
    corrupt.write_text("{not valid json", encoding="utf-8")
    return corrupt


def _complete_raw(dataset_name: str = "proj") -> dict[str, object]:
    serialized = project(dataset_name=dataset_name, tests=[api_test()])
    raw: dict[str, object] = json.loads(serialized.model_dump_json())
    return raw


def _write_raw(input_root: Path, name: str, raw: dict[str, object]) -> Path:
    project_dir = input_root / name
    project_dir.mkdir(parents=True)
    output_file = project_dir / "gerbil.json"
    output_file.write_text(json.dumps(raw), encoding="utf-8")
    return output_file


def test_load_project_record_projects_single_output(tmp_path: Path) -> None:
    _two_good_projects(tmp_path)

    record = load_project_record(tmp_path / "service-a" / "gerbil.json")

    assert record.dataset_name == "service-a"
    assert len(record.tests) == 2


def test_discover_returns_sorted_paths(tmp_path: Path) -> None:
    write_gerbil_output(tmp_path, "z-svc", project(dataset_name="z-svc"))
    write_gerbil_output(tmp_path, "a-svc", project(dataset_name="a-svc"))

    discovered = discover_gerbil_files(tmp_path)

    assert [path.parent.name for path in discovered] == ["a-svc", "z-svc"]


@pytest.mark.parametrize("jobs", [1, 2])
def test_records_returned_in_path_order_regardless_of_jobs(
    tmp_path: Path, jobs: int
) -> None:
    _two_good_projects(tmp_path)
    paths = discover_gerbil_files(tmp_path)

    records, failures = load_project_records(paths, jobs=jobs)

    assert failures == []
    assert [record.dataset_name for record in records] == ["service-a", "service-b"]


@pytest.mark.parametrize("jobs", [1, 2])
def test_on_loaded_callback_fires_for_each_success(tmp_path: Path, jobs: int) -> None:
    _two_good_projects(tmp_path)
    paths = discover_gerbil_files(tmp_path)

    loaded_paths: list[Path] = []
    records, failures = load_project_records(
        paths, jobs=jobs, on_loaded=loaded_paths.append
    )

    assert failures == []
    assert len(records) == 2
    # Completion order is nondeterministic under parallelism, so compare as sets.
    assert set(loaded_paths) == set(paths)


@pytest.mark.parametrize("jobs", [1, 2])
def test_on_failed_callback_fires_with_path_and_error(
    tmp_path: Path, jobs: int
) -> None:
    _two_good_projects(tmp_path)
    corrupt = _write_corrupt(tmp_path, "broken")
    paths = discover_gerbil_files(tmp_path)

    failed: list[tuple[Path, str]] = []
    loaded: list[Path] = []
    records, failures = load_project_records(
        paths,
        jobs=jobs,
        on_loaded=loaded.append,
        on_failed=lambda p, e: failed.append((p, e)),
    )

    assert {record.dataset_name for record in records} == {"service-a", "service-b"}
    assert [failure.path for failure in failures] == [corrupt]
    assert [path for path, _ in failed] == [corrupt]
    assert failed[0][1]  # a non-empty error string
    assert set(loaded) == {
        tmp_path / "service-a" / "gerbil.json",
        tmp_path / "service-b" / "gerbil.json",
    }


# --- Schema-drift detection at the loading boundary ---


def test_missing_top_level_field_names_all_missing_keys(tmp_path: Path) -> None:
    raw = _complete_raw()
    del raw["test_class_analyses"]
    del raw["endpoint_coverage"]
    stale = _write_raw(tmp_path, "stale-svc", raw)

    with pytest.raises(
        ValueError, match=r"endpoint_coverage, test_class_analyses"
    ) as excinfo:
        load_project_record(stale)
    assert "schema drift" in str(excinfo.value)


def test_missing_nested_method_block_is_rejected(tmp_path: Path) -> None:
    raw = _complete_raw()
    test_classes = raw["test_class_analyses"]
    assert isinstance(test_classes, list)
    del test_classes[0]["test_method_analyses"][0]["http"]
    stale = _write_raw(tmp_path, "stale-svc", raw)

    with pytest.raises(
        ValueError,
        match=r"test_class_analyses\[0\]\.test_method_analyses\[0\]\.http",
    ) as excinfo:
        load_project_record(stale)
    assert "schema drift" in str(excinfo.value)


def test_missing_deep_field_inside_nested_model_is_rejected(tmp_path: Path) -> None:
    raw = _complete_raw()
    test_classes = raw["test_class_analyses"]
    assert isinstance(test_classes, list)
    del test_classes[0]["test_method_analyses"][0]["http"]["request_dispatch"]["labels"]
    stale = _write_raw(tmp_path, "stale-svc", raw)

    with pytest.raises(
        ValueError,
        match=r"http\.request_dispatch\.labels",
    ):
        load_project_record(stale)


def test_null_model_valued_union_field_passes_completeness(tmp_path: Path) -> None:
    raw = _complete_raw()
    test_classes = raw["test_class_analyses"]
    assert isinstance(test_classes, list)
    identity = test_classes[0]["test_method_analyses"][0]["identity"]
    # Present-as-null is a complete serialization of a Model-or-None field,
    # unlike key absence.
    identity["parameterization"] = None
    output_file = _write_raw(tmp_path, "svc", raw)

    record = load_project_record(output_file)

    assert record.dataset_name == "proj"


def test_missing_field_report_is_capped(tmp_path: Path) -> None:
    raw = json.loads(
        project(
            dataset_name="proj", tests=[api_test() for _ in range(12)]
        ).model_dump_json()
    )
    test_classes = raw["test_class_analyses"]
    assert isinstance(test_classes, list)
    for test_method in test_classes[0]["test_method_analyses"]:
        del test_method["http"]
    stale = _write_raw(tmp_path, "stale-svc", raw)

    with pytest.raises(ValueError, match=r"\(\+2 more\)") as excinfo:
        load_project_record(stale)
    assert str(excinfo.value).count("test_method_analyses") == 10


@pytest.mark.parametrize("jobs", [1, 2])
def test_drifted_file_lands_in_failures_without_a_record(
    tmp_path: Path, jobs: int
) -> None:
    _two_good_projects(tmp_path)
    raw = _complete_raw("stale-svc")
    del raw["test_class_analyses"]
    stale = _write_raw(tmp_path, "stale-svc", raw)
    paths = discover_gerbil_files(tmp_path)

    records, failures = load_project_records(paths, jobs=jobs)

    assert {record.dataset_name for record in records} == {"service-a", "service-b"}
    assert [failure.path for failure in failures] == [stale]
    assert "test_class_analyses" in failures[0].error


def test_unknown_project_level_key_is_rejected(tmp_path: Path) -> None:
    raw = _complete_raw()
    raw["legacy_field"] = 1
    drifted = _write_raw(tmp_path, "drifted-svc", raw)

    with pytest.raises(ValidationError, match="legacy_field"):
        load_project_record(drifted)


def test_unknown_class_level_key_is_rejected(tmp_path: Path) -> None:
    raw = _complete_raw()
    test_classes = raw["test_class_analyses"]
    assert isinstance(test_classes, list)
    test_classes[0]["legacy_field"] = 1
    drifted = _write_raw(tmp_path, "drifted-svc", raw)

    with pytest.raises(ValidationError, match="legacy_field"):
        load_project_record(drifted)


def test_non_object_top_level_is_rejected(tmp_path: Path) -> None:
    drifted = _write_raw(tmp_path, "drifted-svc", {})
    drifted.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        load_project_record(drifted)


def test_writer_dump_emits_every_project_field() -> None:
    # model_dump_json without exclusions must keep current outputs loadable.
    raw = _complete_raw()
    assert set(ProjectAnalysis.model_fields) <= raw.keys()


def test_round_trip_dump_then_load_succeeds(tmp_path: Path) -> None:
    output = write_gerbil_output(
        tmp_path, "round-trip", project(dataset_name="round-trip", tests=[api_test()])
    )

    record = load_project_record(output)

    assert record.dataset_name == "round-trip"
    assert len(record.tests) == 1
