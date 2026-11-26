"""Take a target from its pinned source to a white-box suite, recording each stage."""

from __future__ import annotations

import csv
from collections import Counter
import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel

from gerbil.evomaster.callbacks import MARKER, own_callback_servers
from gerbil.evomaster.schema import (
    declare_path_parameters,
    merge_schemas,
    parse_served_schema,
    scope_schema,
)
from gerbil.evomaster.targets import DOCKER_DIRECTORY, EvoMasterTarget
from gerbil.execution.build import ModuleClasspath, compile_test_modules
from gerbil.execution.container import (
    OUTPUT,
    Container,
    build_image,
    require_case_sensitive_mount,
    write_argfile,
)
from gerbil.execution.errors import ExecutionError
from gerbil.execution.sources import SuiteSources
from gerbil.execution.launcher import (
    TestKey,
    TestStatus,
    resolve_junit_runtime,
    run_tests,
    test_id,
)
from gerbil.execution.projects import Suite
from gerbil.execution.selection import load_api_tests
from gerbil.execution.workspace import (
    apply_patches,
    apply_workspace_copies,
    head_commit,
    materialize_workspace,
    restore_workspace_paths,
    write_atomically,
)

EVOMASTER_IMAGE = "gerbil-evomaster"
GENERATED_DIRECTORY = "generated"
# EvoMaster's own text of each class verification changes.
ORIGINAL_DIRECTORY = "generated-original"
_GERBIL_ROOT = DOCKER_DIRECTORY.parents[1]
_TOOLS = PurePosixPath("/opt/evomaster")
_EVOMASTER_JAVA = PurePosixPath("/opt/java/jdk17/bin/java")
# More than the 8 GB EMB's white-box experiments give: DolphinScheduler's mutation impacts
# outgrow that within an hour.
_EVOMASTER_HEAP = "-Xmx16g"
# Records how full the heap got, so a run shows whether the heap bound its search.
_EVOMASTER_GC_LOG = f"-Xlog:gc:file={OUTPUT / 'logs' / 'evomaster-gc.log'}:uptime"
# Instrumenting a large service as it loads can take minutes before it serves its schema.
_SCHEMA_TIMEOUT_SECONDS = 30 * 60
_DRIVER_ARGS = OUTPUT / "driver.args"
_STATISTICS_FILE = "statistics.csv"
_SNAPSHOT_FILE = "snapshot.csv"
# A class holds at most 65,535 constants; HertzBeat's 5-minute suite of 1,327 tests
# used 21,002.
_TESTS_PER_CLASS = 500
_SCHEMA_FILE = "schema.json"
_VERIFY_DIRECTORY = OUTPUT / "verify"
# EvoMaster asserts every field of a stored response, in one test method. HertzBeat's
# i18n bundle became 628 KB of source, past the JVM's 64 KB method limit and the depth
# CLDK's parser can recurse to, so every suite caps it to stay compilable.
_MAX_ASSERTED_RESPONSE_BYTES = 32768
# The second verification run reorders each class's tests, which exposes tests that
# depend on their predecessors' state.
_REORDERED = (
    "-Djunit.jupiter.testmethod.order.default=org.junit.jupiter.api.MethodOrderer$Random",
)
_TEST_METHOD = re.compile(r"@Test\b[^\n]*\n\s*public void (\w+)\(")


class Stage(str, Enum):
    PREPARE = "prepare"
    BUILD = "build"
    SCHEMA = "schema"
    GENERATE = "generate"
    VERIFY = "verify"


class StageOutcome(BaseModel):
    stage: Stage
    seconds: float
    error: str | None = None
    # Gerbil's own revision, since a pinned checkout can move between a run's stages.
    gerbil_commit: str | None = None


class SearchResult(BaseModel):
    """EvoMaster's own account of its search, from its statistics file."""

    generated_tests: int
    endpoints: int
    endpoints_2xx: int
    potential_faults: int
    covered_targets: int
    covered_lines: int
    total_lines: int
    covered_branches: int
    total_branches: int
    sql_heuristics_evaluations: int
    sql_parsing_failures: int


class SuiteVerification(BaseModel):
    """The suite's outcome in two fresh JVMs, not on the search's leftover state.

    A test whose outcome differs between the two runs counts as unstable only.
    """

    tests: int
    passed: int
    failed: int
    skipped: int
    not_run: int
    unstable: int


class TargetRun(BaseModel):
    """How far a target got, which is the funnel a paper reports attempts from."""

    target: str
    project: str
    max_time: str
    seed: int
    until: Stage
    cpus: int | None = None
    commit: str | None = None
    stages: list[StageOutcome] = []
    schema_operations: int | None = None
    scoped_operations: int | None = None
    shadowed_operations: int | None = None
    search: SearchResult | None = None
    # Classes Gerbil gave the SSRF callback servers their tests probe.
    repaired_classes: list[str] = []
    verification: SuiteVerification | None = None
    finished: bool = False

    @property
    def failed_stage(self) -> Stage | None:
        return next((o.stage for o in self.stages if o.error is not None), None)


@dataclass(frozen=True)
class RunSettings:
    max_time: str = "1h"
    seed: int = 1234
    until: Stage = Stage.VERIFY
    search_timeout_seconds: int = 4 * 60 * 60
    # Caps the search's CPU time, so a budget buys the same whatever runs beside it.
    cpus: int | None = None
    # Reruns a recorded run from this stage, keeping its earlier stages' results.
    resume_from: Stage | None = None


@dataclass(frozen=True)
class RunPaths:
    """A target's shared source tree, and one seed's logs and results.

    The seed directory is the container's /out, so a driver's log lands beside the
    stage's own.
    """

    root: Path
    seed: int
    until: Stage = Stage.VERIFY

    @classmethod
    def of(
        cls,
        output_dir: Path,
        target: EvoMasterTarget,
        seed: int,
        until: Stage = Stage.VERIFY,
    ) -> RunPaths:
        return cls(output_dir / target.name, seed, until)

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def seed_dir(self) -> Path:
        # A probe stopping early must not touch a full run's suite, logs or record.
        probe = "" if self.until is Stage.VERIFY else f"-until-{self.until.value}"
        return self.root / f"seed-{self.seed}{probe}"

    @property
    def generated(self) -> Path:
        return self.seed_dir / GENERATED_DIRECTORY

    @property
    def originals(self) -> Path:
        return self.seed_dir / ORIGINAL_DIRECTORY

    @property
    def record(self) -> Path:
        return self.seed_dir / "run.json"

    @property
    def classpath(self) -> Path:
        return self.seed_dir / "classpath.json"

    @property
    def statistics(self) -> Path:
        return self.seed_dir / _STATISTICS_FILE

    @property
    def snapshot(self) -> Path:
        return self.seed_dir / _SNAPSHOT_FILE

    def log(self, stage: Stage) -> Path:
        return self.seed_dir / "logs" / f"{stage.value}.log"

    def driver_log(self, stage: Stage) -> PurePosixPath:
        return OUTPUT / "logs" / f"driver-{stage.value}.log"


def run_target(
    target: EvoMasterTarget,
    sources: SuiteSources,
    output_dir: Path,
    settings: RunSettings,
) -> TargetRun:
    """Run the target's stages up to `settings.until`, stopping at the first failure."""
    build_image()
    build_image(EVOMASTER_IMAGE, DOCKER_DIRECTORY)
    require_case_sensitive_mount(output_dir)
    paths = RunPaths.of(output_dir, target, settings.seed, settings.until)
    paths.seed_dir.mkdir(parents=True, exist_ok=True)
    if settings.resume_from is None:
        record = TargetRun(
            target=target.name,
            project=target.project.name,
            max_time=settings.max_time,
            seed=settings.seed,
            until=settings.until,
            cpus=settings.cpus,
        )
    else:
        record = _resumed(paths.record, settings)
    # An interruption before the first stage ends must still read as an attempt.
    write_atomically(paths.record, record.model_dump_json(indent=2))

    def prepare() -> None:
        record.commit = _prepare(target, sources, paths)

    def schema() -> None:
        _write_scoped_schema(target, paths, record)

    def generate() -> None:
        _generate(target, paths, settings, record)

    def verify() -> None:
        record.repaired_classes = _own_callback_servers(
            paths.generated, paths.originals
        )
        record.verification = _verify(target, paths, settings)
        assert record.search is not None
        if record.verification.tests != record.search.generated_tests:
            raise ExecutionError(
                f"the suite has {record.verification.tests} of "
                f"{record.search.generated_tests} generated tests; "
                f"see {paths.log(Stage.VERIFY)}"
            )
        if record.verification.not_run:
            raise ExecutionError(
                f"{record.verification.not_run} generated tests did not run; "
                f"see {paths.log(Stage.VERIFY)}"
            )

    stages: list[tuple[Stage, Callable[[], None]]] = [
        (Stage.PREPARE, prepare),
        (Stage.BUILD, lambda: _build(target, paths)),
        (Stage.SCHEMA, schema),
        (Stage.GENERATE, generate),
        (Stage.VERIFY, verify),
    ]
    first = (
        0 if settings.resume_from is None else list(Stage).index(settings.resume_from)
    )
    planned = stages[first : list(Stage).index(settings.until) + 1]
    for stage, action in planned:
        paths.log(stage).parent.mkdir(parents=True, exist_ok=True)
        paths.log(stage).unlink(missing_ok=True)
        succeeded = _attempt(stage, action, record)
        write_atomically(paths.record, record.model_dump_json(indent=2))
        if not succeeded:
            break
    record.finished = True
    write_atomically(paths.record, record.model_dump_json(indent=2))
    return record


# What each stage records, cleared when a run resumes at or before it.
_STAGE_RESULTS: dict[Stage, tuple[str, ...]] = {
    Stage.PREPARE: ("commit",),
    Stage.BUILD: (),
    Stage.SCHEMA: ("schema_operations", "scoped_operations", "shadowed_operations"),
    Stage.GENERATE: ("search",),
    Stage.VERIFY: ("repaired_classes", "verification"),
}


def _resumed(record: Path, settings: RunSettings) -> TargetRun:
    """The recorded run as it stood before `settings.resume_from`, which every earlier
    stage must have passed."""
    assert settings.resume_from is not None
    start = list(Stage).index(settings.resume_from)
    if list(Stage).index(settings.until) < start:
        raise ExecutionError(
            f"cannot resume from {settings.resume_from.value} until an earlier stage"
        )
    if not record.is_file():
        raise ExecutionError(f"no run to resume at {record}")
    run = TargetRun.model_validate_json(record.read_text())
    kept = run.stages[:start]
    if [o.stage for o in kept] != list(Stage)[:start] or any(o.error for o in kept):
        raise ExecutionError(
            f"{record} did not pass every stage before {settings.resume_from.value}"
        )
    # The record states the search's budget, so a resumed run must not change it.
    if (run.max_time, run.cpus) != (settings.max_time, settings.cpus):
        raise ExecutionError(
            f"{record} ran with max time {run.max_time} and {run.cpus} CPUs"
        )
    cleared = {name for stage in list(Stage)[start:] for name in _STAGE_RESULTS[stage]}
    return TargetRun(
        **run.model_dump(exclude={*cleared, "stages", "until", "finished"}),
        stages=kept,
        until=settings.until,
    )


def _attempt(stage: Stage, action: Callable[[], None], record: TargetRun) -> bool:
    gerbil_commit = gerbil_revision()
    started = time.monotonic()
    error = None
    try:
        action()
    # Stages read files and documents the build and the service produced.
    except (ExecutionError, OSError, ValueError, KeyError) as failure:
        error = f"{type(failure).__name__}: {failure}"
    record.stages.append(
        StageOutcome(
            stage=stage,
            seconds=round(time.monotonic() - started, 1),
            error=error,
            gerbil_commit=gerbil_commit,
        )
    )
    return error is None


def gerbil_revision() -> str:
    """The Gerbil checkout's commit, marked dirty when tracked files differ from it."""
    changes = subprocess.run(
        [
            "git",
            "-C",
            str(_GERBIL_ROOT),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    commit = head_commit(_GERBIL_ROOT)
    return f"{commit}-dirty" if changes.strip() else commit


def _prepare(target: EvoMasterTarget, sources: SuiteSources, paths: RunPaths) -> str:
    project = target.project
    commit = load_api_tests(sources.analysis_path(project, Suite.DEVELOPER)).commit
    materialize_workspace(sources.source_repo(project), commit, paths.workspace)
    # An existing tree is reused, and a refreshed analysis may pin another commit.
    workspace_commit = head_commit(paths.workspace)
    if workspace_commit != commit:
        raise ExecutionError(
            f"{paths.workspace} is at {workspace_commit}, but the analysis pins {commit}"
        )
    prepare_tree(target, sources, paths.workspace)
    return commit


def prepare_tree(
    target: EvoMasterTarget, sources: SuiteSources, workspace: Path
) -> None:
    """Turn a checkout of the pinned commit into the tree the suite is generated,
    staged and measured on: the agents' starting point plus the harness.

    That is the agents' test clearing, the cleared paths the build and the driver need
    back, and the files the project's CI creates.
    """
    project = target.project
    reset_tracked_sources(workspace)
    apply_patches(workspace, [sources.test_clearing_patch(project)])
    restore_workspace_paths(workspace, [*project.workspace_restores, *target.restores])
    apply_workspace_copies(workspace, project.workspace_copies)
    apply_harness(target, workspace)


def reset_tracked_sources(workspace: Path) -> None:
    """Undo an earlier harness, keeping ignored build output so builds stay incremental."""
    for command in (("checkout", "--quiet", "HEAD", "--", "."), ("clean", "-fdq")):
        completed = subprocess.run(
            ["git", "-C", str(workspace), *command], capture_output=True, text=True
        )
        if completed.returncode != 0:
            raise ExecutionError(
                f"git {command[0]} in {workspace} failed: {completed.stderr.strip()}"
            )


def apply_harness(target: EvoMasterTarget, workspace: Path) -> None:
    """Add the target's driver sources and apply its build changes to `workspace`."""
    for source in sorted(target.files.rglob("*")):
        if source.is_file():
            copied = workspace / source.relative_to(target.files)
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, copied)
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "apply",
            "--whitespace=nowarn",
            str(target.build_patch.resolve()),
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ExecutionError(
            f"{target.build_patch} does not apply: {completed.stderr.strip()}"
        )


def _build(target: EvoMasterTarget, paths: RunPaths) -> None:
    container = Container(paths.workspace, paths.seed_dir, target.project.jdk)
    (classpath,) = compile_test_modules(
        container, target.project, [target.module], paths.log(Stage.BUILD)
    )
    paths.classpath.write_text(classpath.model_dump_json(indent=2))
    write_argfile(
        paths.seed_dir / _DRIVER_ARGS.name,
        driver_arguments(target, classpath),
    )


def driver_arguments(target: EvoMasterTarget, classpath: ModuleClasspath) -> list[str]:
    """The driver's JVM, which runs the service with EvoMaster's agent attached."""
    return [
        *target.project.jvm_args,
        *target.jvm_args,
        # The driver loads EvoMaster's instrumentation agent into its own JVM.
        "-Djdk.attach.allowAttachSelf=true",
        *_EVOMASTER_OPENS,
        "-cp",
        ":".join(classpath.entries),
        target.driver,
    ]


# The flags EvoMaster documents for its agent on JDK 17+ (docs/jdks.md).
_EVOMASTER_OPENS = (
    "--add-opens=java.base/java.util.regex=ALL-UNNAMED",
    "--add-opens=java.base/java.net=ALL-UNNAMED",
    "--add-opens=java.base/java.lang=ALL-UNNAMED",
)


def _isolated(
    target: EvoMasterTarget, paths: RunPaths, cpus: int | None = None
) -> Container:
    """The driver's container, which nothing outside it can reach and which reaches nothing."""
    return Container(
        paths.workspace,
        paths.seed_dir,
        target.project.jdk,
        image=EVOMASTER_IMAGE,
        cpus=cpus,
    ).offline()


def _module_directory(target: EvoMasterTarget, paths: RunPaths) -> PurePosixPath:
    classpath = ModuleClasspath.model_validate_json(paths.classpath.read_text())
    return PurePosixPath(classpath.directory)


def _write_scoped_schema(
    target: EvoMasterTarget, paths: RunPaths, record: TargetRun
) -> None:
    documents = [(path, _fetched_schema_name(path)) for path in target.schema_paths]
    for _, name in documents:
        (paths.seed_dir / name).unlink(missing_ok=True)
    _isolated(target, paths).run(
        [
            str(_TOOLS / "with-driver.sh"),
            str(_DRIVER_ARGS),
            str(paths.driver_log(Stage.SCHEMA)),
            str(_TOOLS / "fetch-schema.sh"),
            *(part for path, name in documents for part in (path, str(OUTPUT / name))),
        ],
        log=paths.log(Stage.SCHEMA),
        timeout_seconds=_SCHEMA_TIMEOUT_SECONDS,
        workdir=_module_directory(target, paths),
    )
    merged = merge_schemas(
        [_read_schema(paths.seed_dir / name, path) for path, name in documents]
    )
    scoped = scope_schema(declare_path_parameters(merged.schema), target.excluded_paths)
    record.schema_operations = scoped.total_operations
    record.scoped_operations = scoped.kept_operations
    record.shadowed_operations = merged.shadowed_operations
    if scoped.kept_operations == 0:
        raise ExecutionError(f"{target.name}'s schema left no operations in scope")
    (paths.seed_dir / _SCHEMA_FILE).write_text(json.dumps(scoped.schema, indent=2))


def _fetched_schema_name(path: str) -> str:
    document = re.sub(r"\W+", "-", path).strip("-")
    return f"schema.fetched.{document}.json"


def _read_schema(fetched: Path, path: str) -> dict[str, Any]:
    try:
        return parse_served_schema(fetched.read_text())
    except ValueError as error:
        raise ExecutionError(f"{path} did not serve a schema: {error}") from error


def evomaster_arguments(target: EvoMasterTarget, settings: RunSettings) -> list[str]:
    return [
        "--blackBox=false",
        f"--overrideOpenAPIUrl=file://{OUTPUT / _SCHEMA_FILE}",
        f"--outputFolder={OUTPUT / GENERATED_DIRECTORY}",
        f"--outputFilePrefix=em.generated.EvoMaster_{target.name.replace('-', '_')}",
        "--testSuiteSplitType=NONE",
        f"--maxTestsPerTestSuite={_TESTS_PER_CLASS}",
        # EvoMaster (4.0.0 through 6.2.0) copies query parameter names into test names,
        # so names like `a-b` and `user[]` do not compile.
        "--nameWithQueryParameters=false",
        # The predefined test only fetches the schema, which UAA, DSpace and Kylin serve
        # during the search alone; 6.2.0 also crashes scoring it after the search, as the
        # schema's path is no resource its dependency tracking knows.
        "--addPreDefinedTests=false",
        f"--maxResponseByteSize={_MAX_ASSERTED_RESPONSE_BYTES}",
        f"--maxTime={settings.max_time}",
        f"--seed={settings.seed}",
        "--writeStatistics=true",
        f"--statisticsFile={OUTPUT / _STATISTICS_FILE}",
        # Coverage as the budget runs, to compare at shorter budgets and see a plateau.
        "--snapshotInterval=5",
        f"--snapshotStatisticsFile={OUTPUT / _SNAPSHOT_FILE}",
        "--showProgress=false",
        "--createConfigPathIfMissing=false",
        *target.search_arguments,
    ]


def _generate(
    target: EvoMasterTarget,
    paths: RunPaths,
    settings: RunSettings,
    record: TargetRun,
) -> None:
    log = paths.log(Stage.GENERATE)
    # An earlier run's suite must not stand in for this one's.
    shutil.rmtree(paths.generated, ignore_errors=True)
    shutil.rmtree(paths.originals, ignore_errors=True)
    paths.statistics.unlink(missing_ok=True)
    paths.snapshot.unlink(missing_ok=True)
    _isolated(target, paths, settings.cpus).run(
        [
            str(_TOOLS / "with-driver.sh"),
            str(_DRIVER_ARGS),
            str(paths.driver_log(Stage.GENERATE)),
            str(_EVOMASTER_JAVA),
            _EVOMASTER_HEAP,
            _EVOMASTER_GC_LOG,
            "-jar",
            str(_TOOLS / "evomaster.jar"),
            *evomaster_arguments(target, settings),
        ],
        log=log,
        workdir=_module_directory(target, paths),
        timeout_seconds=settings.search_timeout_seconds,
    )
    record.search = read_search_result(paths.statistics)
    if not generated_sources(paths.generated) or not record.search.generated_tests:
        raise ExecutionError(f"EvoMaster wrote no tests; see {log}")
    # EvoMaster still exits cleanly, with tests asserting failures, in this case.
    if not record.search.endpoints_2xx:
        raise ExecutionError(
            f"no operation answered 2xx, so login may be broken; see {log}"
        )


def generated_sources(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.java"))


def generated_tests(directory: Path) -> list[TestKey]:
    """Each generated test method, keyed by its class's binary name."""
    keys = []
    for source in generated_sources(directory):
        class_name = _class_name(directory, source)
        for method in _TEST_METHOD.findall(source.read_text(encoding="utf-8")):
            keys.append((class_name, method))
    return keys


def _class_name(directory: Path, source: Path) -> str:
    return ".".join(source.relative_to(directory).with_suffix("").parts)


def _own_callback_servers(directory: Path, originals: Path) -> list[str]:
    """Give each class the SSRF callback servers its tests probe, keeping EvoMaster's text
    under `originals` and returning every class Gerbil changed, in this run or the one it
    resumes."""
    repaired = []
    for source in generated_sources(directory):
        text = source.read_text(encoding="utf-8")
        owned = own_callback_servers(text)
        if owned != text:
            original = originals / source.relative_to(directory)
            original.parent.mkdir(parents=True, exist_ok=True)
            write_atomically(original, text)
            write_atomically(source, owned)
        if MARKER in owned:
            repaired.append(_class_name(directory, source))
    return repaired


def _verify(
    target: EvoMasterTarget, paths: RunPaths, settings: RunSettings
) -> SuiteVerification:
    """Compile the suite against the driver's module, then run it as the module's tests."""
    log = paths.log(Stage.VERIFY)
    classpath = ModuleClasspath.model_validate_json(paths.classpath.read_text())
    classes = _VERIFY_DIRECTORY / "classes"
    shutil.rmtree(paths.seed_dir / "verify", ignore_errors=True)
    networked = Container(paths.workspace, paths.seed_dir, target.project.jdk)
    sources = [
        str(OUTPUT / source.relative_to(paths.seed_dir))
        for source in generated_sources(paths.generated)
    ]
    write_argfile(
        paths.seed_dir / "verify" / "javac.args",
        [
            "-encoding",
            "UTF-8",
            "-nowarn",
            "-d",
            str(classes),
            "-cp",
            ":".join(classpath.entries),
            *sources,
        ],
    )
    networked.run(
        ["javac", f"@{_VERIFY_DIRECTORY / 'javac.args'}"],
        log=log,
        workdir=PurePosixPath(classpath.directory),
    )
    suite_classpath = classpath.model_copy(
        update={"entries": [str(classes), *classpath.entries]}
    )
    runtime = resolve_junit_runtime(networked, suite_classpath, log)
    tests = generated_tests(paths.generated)
    runs = [
        run_tests(
            _isolated(target, paths),
            suite_classpath,
            runtime,
            tests,
            jvm_args=[*target.project.jvm_args, *target.jvm_args, *reordering],
            run_directory=_VERIFY_DIRECTORY / name,
            timeout_seconds=settings.search_timeout_seconds,
            log=log,
        )
        for name, reordering in (("run", ()), ("reordered", _REORDERED))
    ]
    outcomes = {test_id(test): [run[test].value for run in runs] for test in tests}
    write_atomically(
        paths.seed_dir / "verify" / "outcomes.json", json.dumps(outcomes, indent=2)
    )
    counts = Counter(
        statuses[0] if len(set(statuses)) == 1 else "unstable"
        for statuses in outcomes.values()
    )
    return SuiteVerification(
        tests=len(tests),
        passed=counts[TestStatus.PASSED.value],
        failed=counts[TestStatus.FAILED.value],
        skipped=counts[TestStatus.SKIPPED.value],
        not_run=counts[TestStatus.NOT_RUN.value],
        unstable=counts["unstable"],
    )


def read_search_result(statistics: Path) -> SearchResult:
    try:
        with statistics.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        row = rows[-1]
        return SearchResult(
            generated_tests=int(row["generatedTests"]),
            endpoints=int(row["endpoints"]),
            endpoints_2xx=int(row["covered2xx"]),
            potential_faults=int(row["potentialFaults"]),
            covered_targets=int(row["coveredTargets"]),
            covered_lines=int(row["coveredLines"]),
            total_lines=int(row["numberOfLines"]),
            covered_branches=int(row["coveredBranches"]),
            total_branches=int(row["numberOfBranches"]),
            sql_heuristics_evaluations=int(row["sqlHeuristicsEvaluationCount"]),
            sql_parsing_failures=int(row["sqlParsingFailureCount"]),
        )
    except (OSError, IndexError, KeyError, ValueError) as error:
        raise ExecutionError(f"unreadable {statistics}: {error!r}") from error
