"""Rescore a project's mutation results on a smaller sample nested in the one they ran on."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from gerbil.execution.coverage import covered_lines
from gerbil.execution.errors import ExecutionError
from gerbil.execution.mutation import MutantRecord
from gerbil.execution.pipeline import (
    ServiceMutants,
    SuiteMutation,
    SuitePaths,
    service_mutants_path,
)
from gerbil.execution.projects import ExecutionProject, Suite
from gerbil.execution.sampling import sample_digest, sample_mutants
from gerbil.execution.workspace import write_atomically


def resample_mutation(
    mutation: SuiteMutation,
    larger: Sequence[str],
    smaller: Sequence[str],
    lines: dict[str, set[int]],
) -> SuiteMutation:
    """`mutation`, run on `larger`, as if run on `smaller`; `lines` are the suite's
    covered lines from its coverage stage."""
    if mutation.sample_sha256 != sample_digest(larger):
        raise ExecutionError("its mutants were drawn from another sample")
    kept = set(smaller)
    if not kept <= set(larger):
        raise ExecutionError("the smaller sample is not part of the one it ran on")
    # Unreached mutants are counted per class, and the classes are not recorded.
    if mutation.unreached_mutants:
        raise ExecutionError(
            f"{mutation.unreached_mutants} of its mutants are of classes no PIT run "
            "mutated, which its results do not name"
        )
    if _line_covered(mutation.mutants, lines) != (
        mutation.line_covered_mutants,
        mutation.untested_line_covered_mutants,
    ):
        raise ExecutionError(
            "its coverage reports no longer give its line-covered mutants"
        )
    records = [record for record in mutation.mutants if record.mutant_id in kept]
    line_covered, untested = _line_covered(records, lines)
    return mutation.model_copy(
        update={
            "line_covered_mutants": line_covered,
            "untested_line_covered_mutants": untested,
            "sample_sha256": sample_digest(smaller),
            "mutants": records,
        }
    )


def resample_project(
    output_dir: Path,
    project: ExecutionProject,
    suites: Sequence[Suite],
    from_size: int,
    size: int,
) -> list[Suite]:
    """Rescore `suites`, run on the first `from_size` of the service's ranked mutants,
    on the first `size`, keeping each original as `mutation-<from_size>.json`; the
    service's sample becomes the smaller one. Returns the suites rescored."""
    if not size < from_size:
        raise ExecutionError(f"{size} mutants are not fewer than {from_size}")
    mutants_path = service_mutants_path(output_dir, project)
    if not mutants_path.exists():
        raise ExecutionError(f"{mutants_path} is missing; run mutation first")
    service = ServiceMutants.model_validate_json(mutants_path.read_text())
    larger = sample_mutants(service.mutants, from_size, service.sample_seed)
    smaller = sample_mutants(service.mutants, size, service.sample_seed)
    if sample_digest(service.sample) not in (
        sample_digest(larger),
        sample_digest(smaller),
    ):
        raise ExecutionError(
            f"{mutants_path} holds a sample of neither {from_size} nor {size} mutants"
        )

    rescored: list[tuple[Suite, SuitePaths, SuiteMutation, SuiteMutation]] = []
    for suite in suites:
        paths = SuitePaths.of(output_dir, project, suite)
        if not paths.mutation.exists():
            raise ExecutionError(f"{paths.mutation} is missing")
        mutation = SuiteMutation.model_validate_json(paths.mutation.read_text())
        if mutation.sample_sha256 == sample_digest(smaller):
            continue
        try:
            resampled = resample_mutation(
                mutation, larger, smaller, covered_lines(paths.coverage_reports)
            )
        except ExecutionError as error:
            raise ExecutionError(f"{suite.value}: {error}") from error
        rescored.append((suite, paths, mutation, resampled))

    # Every suite is checked before any file changes.
    for _, paths, mutation, resampled in rescored:
        original = paths.root / f"mutation-{from_size}.json"
        if not original.exists():
            write_atomically(original, mutation.model_dump_json(indent=2))
        write_atomically(paths.mutation, resampled.model_dump_json(indent=2))
    if sample_digest(service.sample) != sample_digest(smaller):
        original = mutants_path.with_name(f"mutants-{from_size}.json")
        if not original.exists():
            write_atomically(original, service.model_dump_json(indent=2))
        write_atomically(
            mutants_path,
            service.model_copy(update={"sample": smaller}).model_dump_json(indent=2),
        )
    return [suite for suite, _, _, _ in rescored]


def _line_covered(
    records: Sequence[MutantRecord], lines: dict[str, set[int]]
) -> tuple[int, int]:
    """The records on covered lines, and those of them no selected test ran."""
    on_lines = [
        record
        for record in records
        if record.line in lines.get(record.mutated_class, ())
    ]
    return len(on_lines), sum(not record.covered for record in on_lines)
