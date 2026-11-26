from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from gerbil import cli
from gerbil.execution.errors import ExecutionError
from gerbil.execution.mutation import MutantRecord
from gerbil.execution.pipeline import (
    ServiceMutants,
    SuiteMutation,
    SuitePaths,
    service_mutants_path,
)
from gerbil.execution.projects import BuildTool, ExecutionProject, Suite
from gerbil.execution.resample import resample_mutation, resample_project
from gerbil.execution.sampling import sample_digest, sample_mutants

_PROJECT = ExecutionProject("demo", "org_demo", BuildTool.MAVEN, 17)
_SEED = "gerbil"
_MUTANTS = [f"org.demo.Service|m{index}|()V|M|0" for index in range(40)]
_LARGER = sample_mutants(_MUTANTS, 20, _SEED)
_SMALLER = sample_mutants(_MUTANTS, 8, _SEED)
_COVERED_LINES = {1, 2}


def _record(mutant_id: str) -> MutantRecord:
    index = int(mutant_id.split("|")[1][1:])
    killed = index % 3 == 0
    covered = index % 2 == 0
    return MutantRecord(
        mutant_id=mutant_id,
        mutated_class="org.demo.Service",
        mutated_method=f"m{index}",
        line=index % 4,
        mutator="M",
        status="KILLED" if killed else "SURVIVED" if covered else "NO_COVERAGE",
        killed=killed,
        covered=covered or killed,
        killing_tests=["org.demo.ApiTest#t0"] if killed else [],
        surviving_tests=["org.demo.ApiTest#t1"] if covered and not killed else [],
    )


def _mutation(sample: list[str], unreached: int = 0) -> SuiteMutation:
    records = [_record(mutant_id) for mutant_id in sample]
    on_lines = [record for record in records if record.line in _COVERED_LINES]
    return SuiteMutation(
        target_classes=1,
        unreached_mutants=unreached,
        line_covered_mutants=len(on_lines),
        untested_line_covered_mutants=sum(not record.covered for record in on_lines),
        sample_sha256=sample_digest(sample),
        runs=[],
        mutants=records,
    )


def _lines() -> dict[str, set[int]]:
    return {"org.demo.Service": set(_COVERED_LINES)}


def _write_jacoco(directory: Path) -> None:
    directory.mkdir(parents=True)
    lines = "".join(
        f'<line nr="{line}" mi="0" ci="{int(line in _COVERED_LINES)}"/>'
        for line in range(4)
    )
    (directory / "jacoco-0.xml").write_text(
        '<report name="r"><package name="org/demo">'
        '<class name="org/demo/Service" sourcefilename="Service.java"/>'
        f'<sourcefile name="Service.java">{lines}</sourcefile>'
        "</package></report>"
    )


def _write_project(output_dir: Path, sample: list[str]) -> None:
    path = service_mutants_path(output_dir, _PROJECT)
    path.parent.mkdir(parents=True)
    path.write_text(
        ServiceMutants(
            mutants=_MUTANTS, sample=sample, sample_seed=_SEED
        ).model_dump_json()
    )


def _write_suite(output_dir: Path, suite: Suite, mutation: SuiteMutation) -> SuitePaths:
    paths = SuitePaths.of(output_dir, _PROJECT, suite)
    _write_jacoco(paths.coverage_reports)
    paths.mutation.write_text(mutation.model_dump_json())
    return paths


def test_the_smaller_sample_is_the_start_of_the_larger_ranking() -> None:
    assert set(_SMALLER) < set(_LARGER)


def test_resampling_equals_a_run_on_the_smaller_sample() -> None:
    resampled = resample_mutation(_mutation(_LARGER), _LARGER, _SMALLER, _lines())

    assert resampled == _mutation(_SMALLER)


def test_resampling_refuses_results_of_another_sample() -> None:
    other = sample_mutants(_MUTANTS, 20, "other")

    with pytest.raises(ExecutionError, match="another sample"):
        resample_mutation(_mutation(other), _LARGER, _SMALLER, _lines())


def test_resampling_refuses_a_sample_outside_the_one_run() -> None:
    outside = sorted(set(_MUTANTS) - set(_LARGER))[:3]

    with pytest.raises(ExecutionError, match="not part of"):
        resample_mutation(_mutation(_LARGER), _LARGER, outside, _lines())


def test_resampling_refuses_unreached_mutants_it_cannot_attribute() -> None:
    with pytest.raises(ExecutionError, match="no PIT run mutated"):
        resample_mutation(_mutation(_LARGER, unreached=2), _LARGER, _SMALLER, _lines())


def test_resampling_refuses_coverage_reports_that_disagree_with_the_run() -> None:
    with pytest.raises(ExecutionError, match="line-covered"):
        resample_mutation(
            _mutation(_LARGER), _LARGER, _SMALLER, {"org.demo.Service": {3}}
        )


def test_resampling_a_project_rewrites_suites_and_sample_and_keeps_originals(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path, _LARGER)
    developer = _write_suite(tmp_path, Suite.DEVELOPER, _mutation(_LARGER))
    codex = _write_suite(tmp_path, Suite.CODEX, _mutation(_SMALLER))

    rescored = resample_project(
        tmp_path, _PROJECT, [Suite.DEVELOPER, Suite.CODEX], 20, 8
    )

    assert rescored == [Suite.DEVELOPER]
    assert SuiteMutation.model_validate_json(
        developer.mutation.read_text()
    ) == _mutation(_SMALLER)
    assert SuiteMutation.model_validate_json(
        (developer.root / "mutation-20.json").read_text()
    ) == _mutation(_LARGER)
    assert not (codex.root / "mutation-20.json").exists()
    service = ServiceMutants.model_validate_json(
        service_mutants_path(tmp_path, _PROJECT).read_text()
    )
    assert service.sample == _SMALLER
    assert (tmp_path / "demo" / "mutants-20.json").exists()


def test_resampling_a_project_leaves_a_sample_already_smaller(tmp_path: Path) -> None:
    _write_project(tmp_path, _SMALLER)
    _write_suite(tmp_path, Suite.CODEX, _mutation(_LARGER))

    assert resample_project(tmp_path, _PROJECT, [Suite.CODEX], 20, 8) == [Suite.CODEX]
    assert not (tmp_path / "demo" / "mutants-20.json").exists()


def test_resampling_a_project_changes_nothing_when_a_suite_fails(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path, _LARGER)
    developer = _write_suite(tmp_path, Suite.DEVELOPER, _mutation(_LARGER))
    _write_suite(tmp_path, Suite.CODEX, _mutation(_LARGER, unreached=1))
    before = developer.mutation.read_text()

    with pytest.raises(ExecutionError, match="codex"):
        resample_project(tmp_path, _PROJECT, [Suite.DEVELOPER, Suite.CODEX], 20, 8)

    assert developer.mutation.read_text() == before
    assert not (developer.root / "mutation-20.json").exists()


def test_resampling_a_project_refuses_a_service_sample_of_another_size(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path, sample_mutants(_MUTANTS, 12, _SEED))
    _write_suite(tmp_path, Suite.CODEX, _mutation(_LARGER))

    with pytest.raises(ExecutionError, match="neither 20 nor 8"):
        resample_project(tmp_path, _PROJECT, [Suite.CODEX], 20, 8)


def test_resampling_needs_a_smaller_size(tmp_path: Path) -> None:
    with pytest.raises(ExecutionError, match="not fewer"):
        resample_project(tmp_path, _PROJECT, [Suite.CODEX], 8, 8)


def test_the_resample_command_rescores_and_rewrites_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_project(tmp_path, _LARGER)
    developer = _write_suite(tmp_path, Suite.DEVELOPER, _mutation(_LARGER))
    summarized: list[str] = []

    def write_summary(project: ExecutionProject, output_dir: Path) -> Path:
        summarized.append(project.name)
        path = output_dir / project.name / "summary.json"
        path.write_text("{}")
        return path

    monkeypatch.setitem(cli.MEASURED_PROJECTS, "demo", _PROJECT)
    monkeypatch.setattr(cli, "write_summary", write_summary)

    result = CliRunner().invoke(
        cli.app,
        [
            "execution",
            "resample",
            "--project",
            "demo",
            "--suite",
            "developer",
            "--from-size",
            "20",
            "--sample-size",
            "8",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "rescored on 8 mutants: developer" in result.stdout
    assert summarized == ["demo"]
    assert SuiteMutation.model_validate_json(
        developer.mutation.read_text()
    ) == _mutation(_SMALLER)


def test_the_resample_command_reports_why_it_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(cli.MEASURED_PROJECTS, "demo", _PROJECT)

    result = CliRunner().invoke(
        cli.app,
        [
            "execution",
            "resample",
            "--project",
            "demo",
            "--suite",
            "codex",
            "--from-size",
            "20",
            "--sample-size",
            "8",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "mutants.json is missing" in result.stderr
