"""Compare a project's suites on test outcomes, coverage, and mutation scores."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from pydantic import BaseModel

from gerbil.execution.coverage import CoverageCounter
from gerbil.execution.errors import ExecutionError
from gerbil.execution.pipeline import (
    ServiceMutants,
    SuiteCoverage,
    SuiteMutation,
    SuitePaths,
    SuiteTests,
    service_mutants_path,
)
from gerbil.execution.projects import ExecutionProject, Suite
from gerbil.execution.sampling import sample_digest


class MutationScores(BaseModel):
    """Kills over every viable sampled mutant of the service, and over the covered ones.

    `unreached` mutants are of classes the suite covers but PIT could not mutate for it.
    Of the `line_covered` mutants, on lines the suite's passing tests ran in the
    coverage stage, PIT ran no selected test against the `untested_line_covered` ones.
    """

    killed: int
    covered: int
    unreached: int
    line_covered: int
    untested_line_covered: int
    mutation_score: float | None
    test_strength: float | None
    status_counts: dict[str, int]


class SuiteSummary(BaseModel):
    api_tests: int
    status_counts: dict[str, int]
    measured_tests: int | None = None
    coverage: dict[str, CoverageCounter] | None = None
    covered_classes: int | None = None
    mutation: MutationScores | None = None
    errors: dict[str, str] = {}


class ExecutionSummary(BaseModel):
    """`mutants` are the viable ones of the sample drawn from the `service_mutants`, and
    `kills_by_suites` counts them by the exact set of suites killing them."""

    project: str
    suites: dict[str, SuiteSummary]
    mutation_suites: list[str]
    service_mutants: int | None = None
    sample_seed: str | None = None
    mutants: int | None = None
    non_viable_mutants: int | None = None
    kills_by_suites: dict[str, int] | None = None


def write_summary(project: ExecutionProject, output_dir: Path) -> Path:
    path = output_dir / project.name / "summary.json"
    # A summary that cannot be made must not leave an earlier one standing.
    path.unlink(missing_ok=True)
    path.write_text(summarize(project, output_dir).model_dump_json(indent=2))
    return path


def summarize(project: ExecutionProject, output_dir: Path) -> ExecutionSummary:
    suites: dict[Suite, SuiteSummary] = {}
    mutations: dict[Suite, SuiteMutation] = {}
    for suite in Suite:
        paths = SuitePaths.of(output_dir, project, suite)
        if not paths.tests.exists():
            continue
        tests = SuiteTests.model_validate_json(paths.tests.read_text())
        summary = SuiteSummary(
            api_tests=len(
                {(record.class_name, record.method_signature) for record in tests.tests}
            ),
            status_counts=dict(Counter(record.status.value for record in tests.tests)),
            errors={
                stage: paths.error(stage).read_text()
                for stage in ("coverage", "mutation")
                if paths.error(stage).exists()
            },
        )
        if paths.coverage.exists():
            coverage = SuiteCoverage.model_validate_json(paths.coverage.read_text())
            summary.measured_tests = len(coverage.measured_tests)
            summary.coverage = coverage.report.counters
            summary.covered_classes = len(coverage.report.covered_classes)
        if paths.mutation.exists():
            mutations[suite] = SuiteMutation.model_validate_json(
                paths.mutation.read_text()
            )
        suites[suite] = summary

    service_mutants = None
    sample_seed = None
    mutants = None
    non_viable_mutants = None
    kills_by_suites = None
    scored: list[Suite] = []
    if mutations:
        mutants_path = service_mutants_path(output_dir, project)
        if not mutants_path.exists():
            raise ExecutionError(f"{mutants_path} is missing; run mutation again")
        service = ServiceMutants.model_validate_json(mutants_path.read_text())
        sample = set(service.sample)
        digest = sample_digest(service.sample)
        for suite, mutation in list(mutations.items()):
            if mutation.sample_sha256 != digest:
                suites[suite].errors[
                    "mutation"
                ] = "its mutants were drawn from another sample; run mutation again"
                del mutations[suite]
        # Only a mutant some test runs shows it is non-viable, so the sample has them.
        non_viable = {
            mutant.mutant_id
            for mutation in mutations.values()
            for mutant in mutation.mutants
            if mutant.status == "NON_VIABLE"
        }
        universe = sample - non_viable
        killers: dict[str, list[str]] = defaultdict(list)
        for suite, mutation in mutations.items():
            records = [
                mutant
                for mutant in mutation.mutants
                if mutant.mutant_id not in non_viable
            ]
            unsampled = {mutant.mutant_id for mutant in records} - universe
            if unsampled:
                suites[suite].errors["mutation"] = (
                    f"{len(unsampled)} mutants are missing from the service's "
                    f"sample, such as {min(unsampled)}"
                )
                continue
            scored.append(suite)
            killed = [mutant.mutant_id for mutant in records if mutant.killed]
            covered = sum(mutant.covered for mutant in records)
            for mutant_id in killed:
                killers[mutant_id].append(suite.value)
            statuses = Counter(mutant.status for mutant in records)
            # PIT only mutates the classes the suite covers.
            statuses["NO_COVERAGE"] += len(universe) - len(records)
            suites[suite].mutation = MutationScores(
                killed=len(killed),
                covered=covered,
                unreached=mutation.unreached_mutants,
                line_covered=mutation.line_covered_mutants,
                untested_line_covered=mutation.untested_line_covered_mutants,
                mutation_score=len(killed) / len(universe) if universe else None,
                test_strength=len(killed) / covered if covered else None,
                status_counts=dict(statuses),
            )
        service_mutants = len(service.mutants)
        sample_seed = service.sample_seed
        mutants = len(universe)
        non_viable_mutants = len(sample & non_viable)
        kills_by_suites = dict(
            Counter("+".join(killers[mutant_id]) or "none" for mutant_id in universe)
        )
    return ExecutionSummary(
        project=project.name,
        suites={suite.value: summary for suite, summary in suites.items()},
        mutation_suites=[suite.value for suite in scored],
        service_mutants=service_mutants,
        sample_seed=sample_seed,
        mutants=mutants,
        non_viable_mutants=non_viable_mutants,
        kills_by_suites=kills_by_suites,
    )
