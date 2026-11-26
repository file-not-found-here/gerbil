"""Relates the structure of a suite's API tests to the mutants they kill, joining each
test's traits from its Gerbil analysis with the kill matrix of the mutation stage."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gerbil.analysis.schema.types import ProjectAnalysis
from gerbil.execution.errors import ExecutionError
from gerbil.execution.launcher import TestStatus, test_id
from gerbil.execution.pipeline import SuiteMutation, SuitePaths, SuiteTests
from gerbil.execution.projects import ExecutionProject, Suite
from gerbil.statistics.records import ORIGIN_BUCKETS, TestRecord, project_test
from gerbil.statistics.suite_comparison import ALPHA, holm_adjust

_FIXTURE_BUCKET = ORIGIN_BUCKETS.index("fixture")
_BODY_SURFACES = frozenset(
    {"body-only", "status+body", "body+header", "status+body+header"}
)
_RESAMPLES = 10_000
_SEED = 0


@dataclass(frozen=True)
class Trait:
    """A property splitting tests in two; `holds` is None for a test outside its population."""

    name: str
    label: str
    population: str
    holds: Callable[[TestRecord], bool | None]


def _steps_at_least(count: int) -> Callable[[TestRecord], bool | None]:
    def holds(test: TestRecord) -> bool | None:
        if not test.http_sequence_count:
            return None
        return test.http_sequence_count >= count

    return holds


def _checks_body(test: TestRecord) -> bool | None:
    if test.response_surface_combination in _BODY_SURFACES:
        return True
    return False if test.response_surface_combination == "status-only" else None


def _deep_body_check(test: TestRecord) -> bool | None:
    most = max(test.http_sequence_body_check_counts, default=0)
    return most >= 2 if most else None


def _re_dispatches_endpoint(test: TestRecord) -> bool | None:
    if not (test.has_multiple_http_sequences and test.all_dispatch_events_resolved):
        return None
    return test.re_dispatches_endpoint


def _reads_after_write(test: TestRecord) -> bool | None:
    if not (test.resource_lifecycle_labels and test.writes_resource):
        return None
    return test.has_read_after_write


def _fixture_request(test: TestRecord) -> bool | None:
    return test.event_counts[_FIXTURE_BUCKET] > 0


# Each is drawn from a row of the paper's comparison table; `population` names the
# tests it splits, which is the row's own only where the row is a share of tests.
TRAITS: tuple[Trait, ...] = (
    Trait("multi_step", "Two or more steps", "tests with a step", _steps_at_least(2)),
    Trait(
        "five_or_more_steps",
        "Five or more steps",
        "tests with a step",
        _steps_at_least(5),
    ),
    Trait(
        "checks_body",
        "Checks the body, against only the status",
        "tests checking the body or only the status",
        _checks_body,
    ),
    Trait(
        "deep_body_check",
        "A step with two or more body checks, against at most one",
        "tests with a body check in a step",
        _deep_body_check,
    ),
    Trait(
        "re_dispatches_endpoint",
        "Re-dispatches an endpoint",
        "multi-step tests with every dispatch resolved",
        _re_dispatches_endpoint,
    ),
    Trait(
        "reads_after_write",
        "Reads a resource back after writing it",
        "tests writing a resource",
        _reads_after_write,
    ),
    Trait(
        "fixture_request", "Issues a request from a fixture", "tests", _fixture_request
    ),
)


@dataclass(frozen=True)
class Encounter:
    """One test running one sampled mutant."""

    mutant: str
    mutated_class: str
    test: str
    test_class: str
    killed: bool


@dataclass(frozen=True)
class SuiteKills:
    """A suite's kill matrix on one project, with the structure of the tests in it.

    `unmatched_tests` ran a mutant but have no structure: the analysis has no API test
    of their name, or several passing tests share it. `stopped_mutants` count as killed
    but PIT stopped their worker and so names no test that killed them; they are left
    out, since the tests it does name would read as having let them survive.
    """

    project: str
    tests: Mapping[str, TestRecord]
    encounters: tuple[Encounter, ...]
    unmatched_tests: int
    stopped_mutants: int


def find_analysis(root: Path, project: ExecutionProject) -> Path:
    """The project's analysis under a root that names projects as the dataset or the
    generated suites do."""
    for name in (project.dataset_name, project.name):
        path = root / name / "gerbil.json"
        if path.exists():
            return path
    raise ExecutionError(f"{root} has no analysis of {project.name}")


def _api_test_structure(
    analysis: ProjectAnalysis, analysis_path: Path
) -> dict[tuple[str, str], TestRecord]:
    """Every API test of an analysis by (qualified class name, method signature)."""
    structure: dict[tuple[str, str], TestRecord] = {}
    for test_class in analysis.test_class_analyses:
        for method in test_class.test_method_analyses:
            if not method.is_api_test:
                continue
            key = (test_class.qualified_class_name, method.identity.method_signature)
            if key in structure:
                raise ExecutionError(
                    f"{analysis_path} has {key[0]}#{key[1]} more than once"
                )
            structure[key] = project_test(method)
    return structure


def load_suite_kills(
    project: ExecutionProject, suite: Suite, output_dir: Path, analysis_path: Path
) -> SuiteKills:
    paths = SuitePaths.of(output_dir, project, suite)
    if not paths.mutation.exists():
        raise ExecutionError(
            f"{project.name} has no mutation results for the {suite.value} suite"
        )
    executed = SuiteTests.model_validate_json(paths.tests.read_text())
    mutation = SuiteMutation.model_validate_json(paths.mutation.read_text())
    analysis = ProjectAnalysis.model_validate(
        json.loads(analysis_path.read_text(encoding="utf-8"))
    )
    if analysis.metadata.git_commit_hash != executed.commit:
        raise ExecutionError(
            f"{analysis_path} analyses commit {analysis.metadata.git_commit_hash}, "
            f"but the {suite.value} suite of {project.name} ran at {executed.commit}"
        )
    structure = _api_test_structure(analysis, analysis_path)

    # PIT names a test by class and method name, which overloads share.
    candidates: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for record in executed.tests:
        if record.status is TestStatus.PASSED:
            candidates[test_id(record.key)].add(
                (record.class_name, record.method_signature)
            )
    tests = {
        name: structure[next(iter(keys))]
        for name, keys in candidates.items()
        if len(keys) == 1 and next(iter(keys)) in structure
    }

    # Qualified so that several projects' tests and mutants can be pooled.
    def qualified(name: str) -> str:
        return f"{project.name}:{name}"

    encounters: list[Encounter] = []
    unmatched: set[str] = set()
    stopped_mutants = 0
    for mutant in mutation.mutants:
        if not mutant.covered:
            continue
        if mutant.killed and not mutant.killing_tests:
            stopped_mutants += 1
            continue
        outcomes = [(name, True) for name in mutant.killing_tests] + [
            (name, False) for name in mutant.surviving_tests
        ]
        for name, killed in outcomes:
            if name not in tests:
                unmatched.add(name)
                continue
            encounters.append(
                Encounter(
                    qualified(mutant.mutant_id),
                    qualified(mutant.mutated_class),
                    qualified(name),
                    qualified(name.split("#")[0]),
                    killed,
                )
            )
    return SuiteKills(
        project=project.name,
        tests={qualified(name): test for name, test in tests.items()},
        encounters=tuple(encounters),
        unmatched_tests=len(unmatched),
        stopped_mutants=stopped_mutants,
    )


def _rate(count: int, total: int) -> float | None:
    return count / total if total else None


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _group(
    tests: int, running: int, outcomes: Mapping[str, Sequence[bool]]
) -> dict[str, Any]:
    encounters = sum(len(results) for results in outcomes.values())
    kills = sum(sum(results) for results in outcomes.values())
    killed = sum(any(results) for results in outcomes.values())
    return {
        "tests": tests,
        "tests_running_a_mutant": running,
        "encounters": encounters,
        "kills": kills,
        "kill_rate": _rate(kills, encounters),
        "mutants_run": len(outcomes),
        "mutants_killed": killed,
        "test_strength": _rate(killed, len(outcomes)),
    }


def _class_inference(
    differences: Sequence[float], mutated_classes: Sequence[str]
) -> tuple[list[float] | None, float | None]:
    """The 95% interval and the two-sided p-value of the mean difference, both taking
    the mutated class as the unit because the mutants of a class tend to share their
    outcome: the interval resamples whole classes, and the test flips the sign of
    whole classes' differences, as no difference between the groups would allow."""
    sums: dict[str, float] = defaultdict(float)
    sizes: dict[str, int] = defaultdict(int)
    for difference, mutated_class in zip(differences, mutated_classes):
        sums[mutated_class] += difference
        sizes[mutated_class] += 1
    if len(sums) < 2:
        return None, None
    # Imported lazily: numpy takes long enough to import to slow every CLI command.
    import numpy as np

    class_sums = np.array(list(sums.values()))
    class_sizes = np.array(list(sizes.values()))
    picks = np.random.default_rng(_SEED).integers(
        0, len(class_sums), size=(_RESAMPLES, len(class_sums))
    )
    resampled = class_sums[picks].sum(axis=1) / class_sizes[picks].sum(axis=1)
    interval = [float(bound) for bound in np.quantile(resampled, [0.025, 0.975])]
    if not class_sums.any():
        return interval, None
    signs = np.random.default_rng(_SEED).choice(
        [-1.0, 1.0], size=(_RESAMPLES, len(class_sums))
    )
    # The tolerance keeps flips that only reorder the same sum from falling short.
    at_least_as_extreme = np.abs(signs @ class_sums) >= abs(class_sums.sum()) - 1e-9
    return interval, float((1 + at_least_as_extreme.sum()) / (1 + _RESAMPLES))


def _paired(
    rates: Mapping[str, tuple[float, float]], mutated_classes: Mapping[str, str]
) -> dict[str, Any]:
    """Compares the groups on the mutants with a (holding, lacking) kill rate each."""
    mutants = sorted(rates)
    differences = [rates[mutant][0] - rates[mutant][1] for mutant in mutants]
    classes = [mutated_classes[mutant] for mutant in mutants]
    interval, p_value = _class_inference(differences, classes)
    return {
        "mutants": len(mutants),
        "mutated_classes": len(set(classes)),
        "mean_kill_rate_holding": _mean([rates[mutant][0] for mutant in mutants]),
        "mean_kill_rate_lacking": _mean([rates[mutant][1] for mutant in mutants]),
        "mean_difference": _mean(differences),
        "difference_interval": interval,
        "holding_higher": sum(difference > 0 for difference in differences),
        "lacking_higher": sum(difference < 0 for difference in differences),
        "tied": sum(difference == 0 for difference in differences),
        "test": (
            None if p_value is None else {"name": "class_sign_flip", "p_value": p_value}
        ),
    }


def _kill_share(results: Sequence[bool]) -> float:
    return sum(results) / len(results)


def _trait_entry(
    trait: Trait,
    tests: Mapping[str, TestRecord],
    encounters: Sequence[Encounter],
    mutated_classes: Mapping[str, str],
) -> dict[str, Any]:
    membership = {name: trait.holds(test) for name, test in tests.items()}
    # By group, then mutant, then test class: whether each test killed the mutant.
    outcomes: dict[bool, dict[str, dict[str, list[bool]]]] = {True: {}, False: {}}
    running: dict[bool, set[str]] = {True: set(), False: set()}
    for encounter in encounters:
        holds = membership[encounter.test]
        if holds is None:
            continue
        outcomes[holds].setdefault(encounter.mutant, {}).setdefault(
            encounter.test_class, []
        ).append(encounter.killed)
        running[holds].add(encounter.test)
    by_mutant = {
        holds: {
            mutant: [killed for results in by_class.values() for killed in results]
            for mutant, by_class in outcomes[holds].items()
        }
        for holds in (True, False)
    }
    shared = by_mutant[True].keys() & by_mutant[False].keys()
    killed_by = [
        (any(by_mutant[True][mutant]), any(by_mutant[False][mutant]))
        for mutant in shared
    ]

    same_class: dict[str, tuple[float, float]] = {}
    test_classes: set[str] = set()
    for mutant in shared:
        holding, lacking = outcomes[True][mutant], outcomes[False][mutant]
        both = holding.keys() & lacking.keys()
        if both:
            test_classes |= both
            same_class[mutant] = (
                sum(_kill_share(holding[name]) for name in both) / len(both),
                sum(_kill_share(lacking[name]) for name in both) / len(both),
            )

    members = list(membership.values())
    return {
        "name": trait.name,
        "label": trait.label,
        "population": trait.population,
        "holding": _group(members.count(True), len(running[True]), by_mutant[True]),
        "lacking": _group(members.count(False), len(running[False]), by_mutant[False]),
        "shared_mutants": {
            **_paired(
                {
                    mutant: (
                        _kill_share(by_mutant[True][mutant]),
                        _kill_share(by_mutant[False][mutant]),
                    )
                    for mutant in shared
                },
                mutated_classes,
            ),
            "killed_only_by_holding": killed_by.count((True, False)),
            "killed_only_by_lacking": killed_by.count((False, True)),
            "killed_by_both": killed_by.count((True, True)),
            "killed_by_neither": killed_by.count((False, False)),
        },
        "same_test_class": {
            **_paired(same_class, mutated_classes),
            "test_classes": len(test_classes),
        },
    }


_COMPARISONS = ("shared_mutants", "same_test_class")


def _scope(
    tests: Mapping[str, TestRecord], encounters: Sequence[Encounter], alpha: float
) -> dict[str, Any]:
    """Every trait over one set of tests, with the p-values Holm-adjusted over them."""
    mutated_classes = {
        encounter.mutant: encounter.mutated_class for encounter in encounters
    }
    traits = [
        _trait_entry(trait, tests, encounters, mutated_classes) for trait in TRAITS
    ]
    tested = [
        entry[comparison]["test"]
        for entry in traits
        for comparison in _COMPARISONS
        if entry[comparison]["test"] is not None
    ]
    adjusted = holm_adjust([test["p_value"] for test in tested])
    for test, p_adjusted in zip(tested, adjusted):
        test["p_adjusted"] = p_adjusted
        test["significant"] = p_adjusted < alpha
    return {"tests": len(tests), "encounters": len(encounters), "traits": traits}


def _pooled(kills: Sequence[SuiteKills], alpha: float) -> dict[str, Any]:
    return _scope(
        {name: test for project in kills for name, test in project.tests.items()},
        [encounter for project in kills for encounter in project.encounters],
        alpha,
    )


def compute(
    suite: str,
    kills: Sequence[SuiteKills],
    *,
    left_out: Sequence[str] = (),
    alpha: float = ALPHA,
) -> dict[str, Any]:
    """Serializable payload: each trait's split of the suite's tests, pooled over the
    projects and for each project; `left_out` are the measured projects not among them.
    """
    pooled = _pooled(kills, alpha)
    if len(kills) > 1:
        for project in kills:
            rest = _pooled([other for other in kills if other is not project], alpha)
            for entry, rest_entry in zip(pooled["traits"], rest["traits"]):
                for comparison in _COMPARISONS:
                    entry[comparison].setdefault("mean_difference_without", {})[
                        project.project
                    ] = rest_entry[comparison]["mean_difference"]
    return {
        "suite": suite,
        "projects": [project.project for project in kills],
        "projects_left_out": list(left_out),
        "method": {
            "encounter": "a passing test running a sampled mutant",
            "kill_rate": "a group's kills over its encounters",
            "test_strength": "mutants a group kills over the mutants it runs",
            "shared_mutants": (
                "The mutants that tests of both groups run. A group's kill rate on "
                "a mutant is the share of its tests running the mutant that kill "
                "it, and the mean kill rates and their difference are over the "
                "shared mutants, each counted once whatever the size of the groups."
            ),
            "same_test_class": (
                "The shared mutants that tests of both groups in one test class "
                "run, which share that class's harness and fixtures. A group's "
                "kill rate on a mutant is the mean, over such test classes, of the "
                "share of the class's tests of the group running the mutant that "
                "kill it."
            ),
            "mean_difference_without": (
                "The pooled mean difference with each project left out in turn."
            ),
            "difference_interval": (
                "95% percentile interval of the mean difference over "
                f"{_RESAMPLES} resamples of whole mutated classes"
            ),
            "test": (
                "class_sign_flip: the share of "
                f"{_RESAMPLES} random sign flips of whole mutated classes' "
                "differences whose mean difference is at least as far from zero "
                "as the observed one, counting the observed one"
            ),
            "alternative": "two-sided",
            "correction": (
                "holm, over the tested comparisons of one set of tests; not over "
                "suites, nor over the pooled and per-project sets together"
            ),
            "alpha": alpha,
            "untested": (
                "A comparison whose mutants are of fewer than two classes has no "
                "interval or test, and one whose classes' differences are all zero "
                "has no test."
            ),
            "killed_only_by": (
                "Counts of shared mutants by which groups kill them; a group with "
                "more tests has more chances to kill."
            ),
        },
        "pooled": pooled,
        "by_project": {
            project.project: {
                "unmatched_tests": project.unmatched_tests,
                "stopped_mutants": project.stopped_mutants,
                **_scope(project.tests, project.encounters, alpha),
            }
            for project in kills
        },
    }
