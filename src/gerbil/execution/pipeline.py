"""Run the coverage and mutation stages over a project's developer, agent, and EvoMaster
suites."""

from __future__ import annotations

import shutil
import threading
import traceback
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, TypeAdapter

from gerbil.evomaster.targets import target_for
from gerbil.execution.build import (
    ModuleClasspath,
    class_names,
    compile_test_modules,
    list_build_modules,
)
from gerbil.execution.bundle import ExecutionInputs
from gerbil.execution.container import (
    OUTPUT,
    WORKSPACE,
    Container,
    build_image,
    require_case_sensitive_mount,
    seed_maven_repository,
    stop_containers,
)
from gerbil.execution.coverage import (
    CoverageReport,
    covered_lines,
    jacoco_agent,
    report_coverage,
)
from gerbil.execution.disk import FreeSpace
from gerbil.execution.errors import ExecutionError
from gerbil.execution.launcher import (
    JUnitRuntime,
    TestKey,
    TestStatus,
    resolve_junit_runtime,
    run_tests,
    test_id,
)
from gerbil.execution.mutation import (
    MutantRecord,
    PitMutant,
    PitRun,
    PitSettings,
    attribute_mutants,
    list_mutants,
    parse_mutations,
    run_pit,
)
from gerbil.execution.projects import ExecutionProject, Suite
from gerbil.execution.sampling import mutated_class, sample_digest, sample_mutants
from gerbil.execution.selection import (
    SuiteAnalysis,
    TestLocation,
    TestSourceIndex,
    method_name,
)
from gerbil.execution.workers import WorkerPool
from gerbil.execution.workspace import (
    apply_workspace_copies,
    materialize_workspace,
    restore_workspace_paths,
    write_atomically,
)

_CLASSPATHS = TypeAdapter(list[ModuleClasspath])

# Fixed rather than a quarter of the Docker VM, so results do not depend on its size, and
# the same for tests and PIT's workers, so a test passes or fails alike in both. A list
# preallocated to any int size (8 GiB of references) stays below PIT's 90% worker limit.
HEAP = "-Xmx16g"
# Mockito's inline mocks attach an agent to their own JVM, which otherwise listens for one
# only once it finds a trigger file of its own user in its working directory, and a
# workspace mounted over 9p shows the files a container creates as the host user's.
ATTACH_LISTENER = "-XX:+StartAttachListener"

_COVERAGE_REPORTS = OUTPUT / "coverage"
# How often stopping suites looks for a container that started after the last look.
_STOP_POLL_SECONDS = 5.0

# Maven's local repository, where a missing JUnit runtime is downloaded to, is not safe
# for the concurrent writers that suites mutated at once would be.
_RUNTIME_LOCK = threading.Lock()


class TestRecord(BaseModel):
    class_name: str
    method_signature: str
    dispatch: list[str]
    module: str | None
    binary_class_name: str | None
    status: TestStatus

    @property
    def key(self) -> TestKey:
        assert self.binary_class_name is not None
        return self.binary_class_name, method_name(self.method_signature)


# Prevent pytest from treating the "Test"-prefixed record as a test class.
setattr(TestRecord, "__test__", False)


class SuiteTests(BaseModel):
    """Every API test of a suite with its baseline outcome or reason for not running."""

    commit: str
    tests: list[TestRecord]


class SuiteCoverage(BaseModel):
    """`heap` is the tests' JVM heap flag, which PIT's workers must share."""

    heap: str
    measured_tests: list[str]
    report: CoverageReport


class SuiteMutation(BaseModel):
    """PIT's outcomes for the sampled mutants of the service classes the suite covers.

    `sample_sha256` identifies the sample they were drawn from. `unreached_mutants`
    are sampled mutants of covered classes that no module with tests of the suite has
    among the class directories PIT mutates, so they count as not covered.
    `line_covered_mutants` are the mutants on lines the suite's passing tests ran in the
    coverage stage, and `untested_line_covered_mutants` those of them PIT ran no
    selected test against: few, unless PIT's workers did not run what coverage ran.
    """

    target_classes: int
    unreached_mutants: int
    line_covered_mutants: int
    untested_line_covered_mutants: int
    sample_sha256: str
    runs: list[PitRun]
    mutants: list[MutantRecord]


class ServiceMutants(BaseModel):
    """Every mutant of the service's production classes, and the random sample of them
    that every suite is challenged with and mutation scores are over."""

    mutants: list[str]
    sample: list[str]
    sample_seed: str


def extend_mutation(
    base: SuiteMutation,
    added: SuiteMutation,
    base_sample: Sequence[str],
    sample: Sequence[str],
    covered_classes: Iterable[str],
) -> SuiteMutation:
    """`base`, run on `base_sample`, and `added`, run on the rest of `sample`, as one
    run on `sample`; `covered_classes` are the suite's from its coverage stage."""
    if base.sample_sha256 != sample_digest(base_sample):
        raise ExecutionError("its mutants were drawn from another sample")
    if not set(base_sample) <= set(sample):
        raise ExecutionError("the larger sample does not hold the one it ran on")
    if added.sample_sha256 != sample_digest(set(sample) - set(base_sample)):
        raise ExecutionError("the added mutants are not the rest of the larger sample")
    # Counts add up because the two runs challenged disjoint mutants of one build.
    return SuiteMutation(
        target_classes=len(
            {mutated_class(mutant_id) for mutant_id in sample}.intersection(
                covered_classes
            )
        ),
        unreached_mutants=base.unreached_mutants + added.unreached_mutants,
        line_covered_mutants=base.line_covered_mutants + added.line_covered_mutants,
        untested_line_covered_mutants=base.untested_line_covered_mutants
        + added.untested_line_covered_mutants,
        sample_sha256=sample_digest(sample),
        runs=[*base.runs, *added.runs],
        mutants=[*base.mutants, *added.mutants],
    )


@dataclass(frozen=True)
class MutationSettings:
    """`workers` is the budget of PIT workers that every mutation process writing to
    one output directory shares; a PIT run holds as many as it has threads. A PIT run
    starts once the output directory's disk has `min_free_gb` free. With `extend_from`,
    each suite keeps its results on the first `extend_from` of the ranked mutants and
    is challenged with the rest of the sample only."""

    pit: PitSettings
    workers: int
    sample_size: int
    sample_seed: str
    min_free_gb: int
    extend_from: int | None = None


@dataclass(frozen=True)
class _Extension:
    """A suite's results on `base_sample`, extended by a run on the rest of `sample`."""

    base: SuiteMutation
    base_sample: Sequence[str]
    sample: Sequence[str]


def worker_pool(output_dir: Path, workers: int) -> WorkerPool:
    return WorkerPool(output_dir / ".workers", workers)


def service_mutants_path(output_dir: Path, project: ExecutionProject) -> Path:
    return output_dir / project.name / "mutants.json"


def _service_mutants_directory(output_dir: Path, project: ExecutionProject) -> Path:
    return output_dir / project.name / "service-mutants"


@dataclass(frozen=True)
class SuitePaths:
    root: Path

    @classmethod
    def of(
        cls, output_dir: Path, project: ExecutionProject, suite: Suite
    ) -> SuitePaths:
        return cls(output_dir / project.name / suite.value)

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def workspace_inputs(self) -> Path:
        """The digest of what the workspace was built from, written once it is complete."""
        return self.root / "workspace.sha256"

    def logs(self, stage: str) -> Path:
        return self.root / "logs" / stage

    @property
    def tests(self) -> Path:
        return self.root / "tests.json"

    @property
    def classpaths(self) -> Path:
        return self.root / "classpaths.json"

    @property
    def coverage(self) -> Path:
        return self.root / "coverage.json"

    @property
    def mutation(self) -> Path:
        return self.root / "mutation.json"

    @property
    def coverage_reports(self) -> Path:
        """JaCoCo's reports of the coverage stage, at `_COVERAGE_REPORTS` in containers."""
        return self.root / "coverage"

    def error(self, stage: str) -> Path:
        return self.root / f"{stage}-error.txt"


def run_coverage(
    project: ExecutionProject,
    inputs: ExecutionInputs,
    output_dir: Path,
    suites: Sequence[Suite],
    min_free_gb: int,
) -> dict[Suite, str]:
    """Baseline-run each suite's selected tests and measure the passing ones' coverage
    of the service EvoMaster runs.

    Every suite compiles the union of all suites' test modules and the service's, and
    starts once the output directory's disk has `min_free_gb` free. Returns the error
    of each suite that failed.
    """
    build_image()
    require_case_sensitive_mount(output_dir)
    space = FreeSpace(output_dir, min_free_gb)
    target = target_for(project)
    analyses = {suite: inputs.selection(project, suite) for suite in Suite}
    commit = inputs.entry(project).commit
    paths = {suite: SuitePaths.of(output_dir, project, suite) for suite in Suite}
    for suite in Suite:
        space.wait()
        _materialize_suite(project, suite, inputs, paths[suite])
    seed = inputs.maven_seed
    if seed is not None:
        seed_maven_repository(seed)
    developer = paths[Suite.DEVELOPER]
    modules_log = output_dir / project.name / "modules.log"
    modules_log.unlink(missing_ok=True)
    build_modules = list_build_modules(
        Container(developer.workspace, developer.root, project.jdk),
        project,
        modules_log,
    )
    plans = {
        suite: _plan_suite(
            project, analyses[suite], paths[suite].workspace, commit, build_modules
        )
        for suite in Suite
    }
    modules = sorted(
        {
            record.module
            for plan in plans.values()
            for record in plan.tests
            if record.module is not None
        }
        | {target.module}
    )
    failures = {}
    for suite in suites:
        paths[suite].coverage.unlink(missing_ok=True)
        # PIT targets the classes this suite's tests cover.
        for path in (paths[suite].mutation, *_kept_mutation_results(paths[suite])):
            path.unlink(missing_ok=True)
        paths[suite].error("mutation").unlink(missing_ok=True)
        space.wait()
        error = _attempt(
            paths[suite],
            "coverage",
            partial(
                _run_suite_coverage,
                project,
                suite,
                paths[suite],
                plans[suite],
                modules,
                target.module,
            ),
        )
        if error is not None:
            failures[suite] = error
    return failures


def run_mutation(
    project: ExecutionProject,
    output_dir: Path,
    suites: Sequence[Suite],
    settings: MutationSettings,
) -> dict[Suite, str]:
    """List every mutant of the service's production classes, sample them, then
    challenge the sampled mutants each suite covers with its passing tests, the suites
    side by side.

    Suites whose coverage stage failed are not mutated. Returns the error of each
    suite that failed.
    """
    measured = []
    for suite in Suite:
        paths = SuitePaths.of(output_dir, project, suite)
        if paths.coverage.exists():
            measured.append(suite)
        elif suite in suites and not paths.error("coverage").exists():
            raise ExecutionError(f"run coverage for {suite.value} first")
    threads = min(settings.pit.threads, settings.workers)
    if project.pit_threads is not None:
        threads = min(threads, project.pit_threads)
    pit = replace(settings.pit, threads=threads)
    pool = worker_pool(output_dir, settings.workers)
    build_image()
    require_case_sensitive_mount(output_dir)
    service_module = target_for(project).module
    extend_from = settings.extend_from
    if extend_from is not None and not extend_from < settings.sample_size:
        raise ExecutionError(
            f"{extend_from} mutants are not fewer than {settings.sample_size}"
        )
    for suite in suites:
        # Results to extend stay until the listing shows the sample still holds them.
        if extend_from is None or suite not in measured:
            SuitePaths.of(output_dir, project, suite).mutation.unlink(missing_ok=True)
    runnable = [suite for suite in suites if suite in measured]
    failures = {
        suite: f"coverage failed; see {SuitePaths.of(output_dir, project, suite).error('coverage')}"
        for suite in suites
        if suite not in measured
    }
    if not runnable:
        return failures
    with pool.joined():
        failures |= _mutate_suites(
            project,
            output_dir,
            runnable,
            measured[0],
            service_module,
            pit,
            pool,
            FreeSpace(output_dir, settings.min_free_gb),
            settings,
        )
    return {suite: failures[suite] for suite in suites if suite in failures}


def _mutate_suites(
    project: ExecutionProject,
    output_dir: Path,
    suites: Sequence[Suite],
    listing_suite: Suite,
    service_module: str,
    pit: PitSettings,
    pool: WorkerPool,
    space: FreeSpace,
    settings: MutationSettings,
) -> dict[Suite, str]:
    """Sample the service's mutants as `listing_suite`'s build lists them and challenge
    each of `suites` with the sample. Returns the error of each suite that failed."""
    space.wait()
    with pool.hold(1):
        # Every suite builds the same production classes, so any measured one lists them.
        listed = _list_service_mutants(
            project,
            SuitePaths.of(output_dir, project, listing_suite),
            service_module,
            replace(pit, threads=1),
            _service_mutants_directory(output_dir, project),
        )
    service = ServiceMutants(
        mutants=listed,
        sample=sample_mutants(listed, settings.sample_size, settings.sample_seed),
        sample_seed=settings.sample_seed,
    )
    challenged = service.sample
    extensions: dict[Suite, _Extension] = {}
    if settings.extend_from is not None:
        base_sample = sample_mutants(listed, settings.extend_from, settings.sample_seed)
        # Every suite's base is found before any file changes or PIT run.
        extensions = {
            suite: _Extension(
                _extended_base(
                    SuitePaths.of(output_dir, project, suite),
                    suite,
                    base_sample,
                    settings.extend_from,
                ),
                base_sample,
                service.sample,
            )
            for suite in suites
        }
        for suite, extension in extensions.items():
            paths = SuitePaths.of(output_dir, project, suite)
            write_atomically(
                _extended_path(paths, settings.extend_from),
                extension.base.model_dump_json(indent=2),
            )
            paths.mutation.unlink(missing_ok=True)
        challenged = sorted(set(service.sample) - set(base_sample))
    write_atomically(
        service_mutants_path(output_dir, project), service.model_dump_json(indent=2)
    )
    stops = {suite: threading.Event() for suite in suites}
    # Suites have their own trees and containers, so they share nothing but the pool.
    with ThreadPoolExecutor(max_workers=len(suites)) as executor:
        attempts = {
            suite: executor.submit(
                _attempt,
                SuitePaths.of(output_dir, project, suite),
                "mutation",
                partial(
                    _run_suite_mutation,
                    project,
                    suite,
                    SuitePaths.of(output_dir, project, suite),
                    service_module,
                    pit,
                    challenged,
                    pool,
                    space,
                    stops[suite],
                    extension=extensions.get(suite),
                ),
            )
            for suite in suites
        }
        try:
            for attempt in as_completed(attempts.values()):
                attempt.result()
        except BaseException:
            # An interrupt reaches this thread only; the suites' PIT runs would go on.
            for suite in suites:
                stops[suite].set()
            _stop_runs(
                [SuitePaths.of(output_dir, project, suite).root for suite in suites],
                list(attempts.values()),
            )
            raise
    return {
        suite: error
        for suite, attempt in attempts.items()
        if (error := attempt.result()) is not None
    }


def _extended_path(paths: SuitePaths, size: int) -> Path:
    return paths.root / f"mutation-{size}.json"


def _extended_base(
    paths: SuitePaths, suite: Suite, base_sample: Sequence[str], size: int
) -> SuiteMutation:
    """The suite's results on `base_sample`: its latest ones if they are, else those an
    earlier extension kept."""
    digest = sample_digest(base_sample)
    for path in (paths.mutation, _extended_path(paths, size)):
        if path.exists():
            mutation = SuiteMutation.model_validate_json(path.read_text())
            if mutation.sample_sha256 == digest:
                return mutation
    raise ExecutionError(
        f"{suite.value}: no mutation results on the first {size} mutants of this "
        "listing to extend"
    )


def _kept_mutation_results(paths: SuitePaths) -> list[Path]:
    """Results on other samples that resampling or an extension kept."""
    return sorted(paths.root.glob("mutation-*.json"))


def _list_service_mutants(
    project: ExecutionProject,
    paths: SuitePaths,
    service_module: str,
    settings: PitSettings,
    directory: Path,
) -> list[str]:
    """List the mutants in `paths`' build, writing PIT's files to `directory`."""
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    container = Container(paths.workspace, directory, project.jdk)
    classpaths = _CLASSPATHS.validate_json(paths.classpaths.read_text())
    service = _service_classpath(classpaths, service_module)
    log = directory / "pit.log"
    return list_mutants(
        container,
        service,
        resolve_junit_runtime(container, service, log),
        class_names(container, service.production_directories),
        mutable_directories=service.production_directories,
        settings=settings,
        report_directory=OUTPUT,
        log=log,
    )


def _service_classpath(
    classpaths: Sequence[ModuleClasspath], service_module: str
) -> ModuleClasspath:
    (service,) = (
        classpath for classpath in classpaths if classpath.module == service_module
    )
    return service


def _materialize_suite(
    project: ExecutionProject, suite: Suite, inputs: ExecutionInputs, paths: SuitePaths
) -> None:
    """Build the suite's tree unless one from the same inputs is there, first discarding
    results the old tree gave."""
    tree = inputs.tree(project, suite)
    digest = tree.digest(project)
    marker = paths.workspace_inputs
    if paths.workspace.exists() and marker.is_file() and marker.read_text() == digest:
        return
    _discard_results(paths)
    marker.unlink(missing_ok=True)
    if paths.workspace.exists():
        try:
            shutil.rmtree(paths.workspace)
        except OSError as error:
            # Containers write the build's outputs as root under Linux's own Docker.
            raise ExecutionError(
                f"cannot replace {paths.workspace}: {error}"
            ) from error
    materialize_workspace(
        inputs.source_repo(project),
        tree.commit,
        paths.workspace,
        clearing=tree.clearing,
        patches=tree.patches,
    )
    apply_workspace_copies(paths.workspace, project.workspace_copies)
    restore_workspace_paths(paths.workspace, project.workspace_restores)
    write_atomically(marker, digest)


def _discard_results(paths: SuitePaths) -> None:
    """A new tree holds another suite, which the old results must not stand for."""
    for path in (
        paths.tests,
        paths.classpaths,
        paths.coverage,
        paths.mutation,
        paths.error("coverage"),
        paths.error("mutation"),
        *_kept_mutation_results(paths),
    ):
        path.unlink(missing_ok=True)


def _attempt(paths: SuitePaths, stage: str, action: Callable[[], None]) -> str | None:
    """Run one suite's stage, recording its error so later suites still run."""
    paths.error(stage).unlink(missing_ok=True)
    shutil.rmtree(paths.logs(stage), ignore_errors=True)
    try:
        action()
    except ExecutionError as error:
        paths.error(stage).write_text(str(error))
        return str(error)
    except Exception as error:
        # A defect in one suite's run must not lose the other suites' results.
        paths.error(stage).write_text(traceback.format_exc())
        return f"unexpected {type(error).__name__}: {error}"
    return None


def _plan_suite(
    project: ExecutionProject,
    analysis: SuiteAnalysis,
    workspace: Path,
    commit: str,
    build_modules: set[str],
) -> SuiteTests:
    index = TestSourceIndex.build(workspace / project.build_root)
    records = []
    for test in analysis.api_tests:
        locations: list[TestLocation] = []
        if not test.in_scope:
            status = TestStatus.OUT_OF_SCOPE
        else:
            locations = [
                location
                for location in index.locate(test.class_name, test.method_name)
                if location.module in build_modules
            ]
            status = TestStatus.NOT_RUN if locations else TestStatus.NOT_IN_BUILD
        options: list[TestLocation | None] = [*locations] or [None]
        for location in options:
            records.append(
                TestRecord(
                    class_name=test.class_name,
                    method_signature=test.method_signature,
                    dispatch=list(test.dispatch),
                    module=location.module if location else None,
                    binary_class_name=location.binary_class_name if location else None,
                    status=status,
                )
            )
    return SuiteTests(commit=commit, tests=records)


def _run_suite_coverage(
    project: ExecutionProject,
    suite: Suite,
    paths: SuitePaths,
    plan: SuiteTests,
    modules: Sequence[str],
    service_module: str,
) -> None:
    paths.tests.write_text(plan.model_dump_json(indent=2))
    container = Container(paths.workspace, paths.root, project.jdk)
    tests_container = _tests_container(suite, container)
    jvm_args = (HEAP, ATTACH_LISTENER, *_tests_jvm_args(project, suite))
    classpaths = compile_test_modules(
        container, project, modules, paths.logs("coverage") / "build.log"
    )
    paths.classpaths.write_text(_CLASSPATHS.dump_json(classpaths, indent=2).decode())

    records_by_module: dict[str, list[TestRecord]] = defaultdict(list)
    for record in plan.tests:
        if record.status is TestStatus.NOT_RUN and record.module is not None:
            records_by_module[record.module].append(record)

    exec_files: list[PurePosixPath] = []
    for classpath in classpaths:
        records = records_by_module.get(classpath.module)
        if not records:
            continue
        log = paths.logs("coverage") / f"tests-{_slug(classpath.module)}.log"
        runtime = resolve_junit_runtime(container, classpath, log)
        run_root = OUTPUT / "runs" / _slug(classpath.module)

        run_directory = run_root / "baseline"
        statuses = _run_measured(
            tests_container,
            jvm_args,
            classpath,
            runtime,
            [record.key for record in records],
            run_directory,
            log,
        )
        passed = [
            key for key, status in statuses.items() if status is TestStatus.PASSED
        ]
        if passed and len(passed) < len(statuses):
            # Coverage counts passing tests only, so re-measure without the others.
            run_directory = run_root / "coverage"
            statuses |= _run_measured(
                tests_container,
                jvm_args,
                classpath,
                runtime,
                passed,
                run_directory,
                log,
            )
        for record in records:
            record.status = statuses[record.key]
        if any(status is TestStatus.PASSED for status in statuses.values()):
            exec_files.append(run_directory / "jacoco.exec")

    report = report_coverage(
        container,
        exec_files,
        _service_classpath(classpaths, service_module).production_directories,
        _COVERAGE_REPORTS,
        paths.logs("coverage") / "report.log",
    )
    coverage = SuiteCoverage(
        heap=HEAP,
        measured_tests=sorted(
            {
                test_id(record.key)
                for record in plan.tests
                if record.status is TestStatus.PASSED
            }
        ),
        report=report,
    )
    paths.tests.write_text(plan.model_dump_json(indent=2))
    write_atomically(paths.coverage, coverage.model_dump_json(indent=2))


def _run_measured(
    container: Container,
    jvm_args: Sequence[str],
    classpath: ModuleClasspath,
    runtime: JUnitRuntime,
    tests: Sequence[TestKey],
    run_directory: PurePosixPath,
    log: Path,
) -> dict[TestKey, TestStatus]:
    return run_tests(
        container,
        classpath,
        runtime,
        tests,
        jvm_args=[jacoco_agent(run_directory / "jacoco.exec"), *jvm_args],
        run_directory=run_directory,
        log=log,
    )


def _tests_container(suite: Suite, container: Container) -> Container:
    """Where the suite's tests run: EvoMaster's offline, as they were generated and verified."""
    return container.offline() if suite is Suite.EVOMASTER else container


def _tests_jvm_args(project: ExecutionProject, suite: Suite) -> tuple[str, ...]:
    if suite is not Suite.EVOMASTER:
        return project.jvm_args
    return (*project.jvm_args, *target_for(project).jvm_args)


def _run_suite_mutation(
    project: ExecutionProject,
    suite: Suite,
    paths: SuitePaths,
    service_module: str,
    settings: PitSettings,
    sample: Sequence[str],
    pool: WorkerPool,
    space: FreeSpace,
    stop: threading.Event,
    extension: _Extension | None = None,
) -> None:
    """Challenge the sampled mutants the suite covers; `stop` ends its PIT runs early.
    With `extension`, `sample` is the rest of the extension's sample, and the results
    extend its base."""
    coverage = SuiteCoverage.model_validate_json(paths.coverage.read_text())
    if coverage.heap != HEAP:
        raise ExecutionError(
            f"coverage ran with {coverage.heap}, not {HEAP}; run coverage again"
        )
    container = Container(paths.workspace, paths.root, project.jdk)
    tests_container = _tests_container(suite, container)
    classpaths = _CLASSPATHS.validate_json(paths.classpaths.read_text())
    service_directories = set(
        _service_classpath(classpaths, service_module).production_directories
    )
    # A mutant in a class no test of the suite runs cannot be killed by it.
    target_classes = sorted(
        {mutated_class(mutant_id) for mutant_id in sample}.intersection(
            coverage.report.covered_classes
        )
    )
    plan = SuiteTests.model_validate_json(paths.tests.read_text())
    tests_by_module: dict[str, set[TestKey]] = defaultdict(set)
    for record in plan.tests:
        if record.module is not None and record.status is TestStatus.PASSED:
            tests_by_module[record.module].add(record.key)

    # Kept apart from the other runs of the suite, which share its workspace.
    pit_container = replace(
        tests_container,
        private_directories=tuple(
            WORKSPACE / directory for directory in project.pit_private_directories
        ),
    )
    mutants: list[PitMutant] = []
    runs: list[PitRun] = []
    mutated: set[str] = set()
    for classpath in classpaths:
        tests = sorted(tests_by_module.get(classpath.module, ()))
        # Leaves out other services' directories, whose classes may share names.
        mutable = [
            directory
            for directory in classpath.code_directories
            if directory in service_directories
        ]
        if not tests or not mutable or not target_classes:
            continue
        slug = _slug(classpath.module)
        log = paths.logs("mutation") / f"{slug}.log"
        with _RUNTIME_LOCK:
            runtime = resolve_junit_runtime(container, classpath, log)
        shards = _shards(
            sample,
            sorted(_classes_in(container, mutable, target_classes)),
            project.pit_containers,
        )

        def challenge(index: int) -> tuple[PitRun, list[PitMutant]]:
            # One run keeps the module's own directory and log, as before sharding.
            name = slug if len(shards) == 1 else f"{slug}-{index}"
            shard_log = paths.logs("mutation") / f"{name}.log"
            report_directory = OUTPUT / "pit" / name
            while True:
                # Before taking workers, which other runs could use meanwhile.
                space.wait(stop)
                with pool.hold(settings.threads, stop):
                    # The wait for workers may have outlasted the free space.
                    if space.short():
                        continue
                    run = run_pit(
                        pit_container,
                        classpath,
                        runtime,
                        tests,
                        shards[index],
                        mutable_directories=mutable,
                        jvm_args=(
                            HEAP,
                            ATTACH_LISTENER,
                            *_tests_jvm_args(project, suite),
                        ),
                        settings=settings,
                        report_directory=report_directory,
                        log=shard_log,
                        kept_mutants=sample,
                    )
                break
            produced = parse_mutations(
                container.host_path(report_directory / "mutations.xml")
            )
            _require_sampled_mutants(
                {mutant.mutant_id for mutant in produced},
                sample,
                set(shards[index]),
                shard_log,
            )
            return run, produced

        if not shards:
            continue
        with ThreadPoolExecutor(max_workers=len(shards)) as executor:
            challenges = [
                executor.submit(challenge, index) for index in range(len(shards))
            ]
            try:
                for finished in as_completed(challenges):
                    finished.result()
            except BaseException:
                # One run's failure fails the suite, so the others' hours are wasted.
                stop.set()
                _stop_runs([paths.root], challenges)
                raise
        for finished in challenges:
            run, produced = finished.result()
            runs.append(run)
            mutants += produced
        mutated.update(name for shard in shards for name in shard)

    selected = {key for keys in tests_by_module.values() for key in keys}
    records = attribute_mutants(mutants, selected)
    lines = covered_lines(container.host_path(_COVERAGE_REPORTS))
    line_covered = [
        record
        for record in records
        if record.line in lines.get(record.mutated_class, ())
    ]
    unreached = set(target_classes) - mutated
    mutation = SuiteMutation(
        target_classes=len(target_classes),
        unreached_mutants=sum(
            mutated_class(mutant_id) in unreached for mutant_id in sample
        ),
        line_covered_mutants=len(line_covered),
        untested_line_covered_mutants=sum(
            not record.covered for record in line_covered
        ),
        sample_sha256=sample_digest(sample),
        runs=runs,
        mutants=records,
    )
    if extension is not None:
        mutation = extend_mutation(
            extension.base,
            mutation,
            extension.base_sample,
            extension.sample,
            coverage.report.covered_classes,
        )
    write_atomically(paths.mutation, mutation.model_dump_json(indent=2))


def _stop_runs(outputs: Sequence[Path], runs: Sequence[Future[Any]]) -> None:
    """Kill the containers writing to `outputs` until `runs`, told to stop, have ended:
    a run may start its container just after a look for containers to kill."""
    while True:
        for output in outputs:
            stop_containers(output)
        if not wait(runs, timeout=_STOP_POLL_SECONDS).not_done:
            return


def _shards(
    sample: Sequence[str], class_names: Sequence[str], count: int
) -> list[list[str]]:
    """Split `class_names` into at most `count` groups of about as many sampled mutants."""
    weights = Counter(mutated_class(mutant_id) for mutant_id in sample)
    shards: list[list[str]] = [[] for _ in range(min(count, len(class_names)))]
    loads = [0] * len(shards)
    for name in sorted(class_names, key=lambda name: (-weights[name], name)):
        lightest = loads.index(min(loads))
        shards[lightest].append(name)
        loads[lightest] += weights[name]
    return [sorted(shard) for shard in shards]


def _classes_in(
    container: Container, directories: Sequence[str], class_names: Sequence[str]
) -> set[str]:
    """Those of `class_names` with a class file in `directories`, which PIT can mutate."""
    roots = [container.host_path(PurePosixPath(directory)) for directory in directories]
    return {
        name
        for name in class_names
        if any((root / f"{name.replace('.', '/')}.class").is_file() for root in roots)
    }


def _require_sampled_mutants(
    produced: set[str], sample: Sequence[str], mutated: set[str], log: Path
) -> None:
    """Refuse a PIT run that left out a sampled mutant of a class it mutated, which
    would otherwise count as a mutant the suite does not cover, or made another."""
    unsampled = produced.difference(sample)
    if unsampled:
        raise ExecutionError(
            f"PIT mutated {len(unsampled)} mutants outside the sample, such as "
            f"{min(unsampled)}; see {log}"
        )
    missing = {
        mutant_id for mutant_id in sample if mutated_class(mutant_id) in mutated
    } - produced
    if missing:
        raise ExecutionError(
            f"PIT made no result for {len(missing)} sampled mutants of the classes it "
            f"mutated, such as {min(missing)}; see {log}"
        )


def _slug(module: str) -> str:
    return "root" if module == "." else module.replace("/", "__")
