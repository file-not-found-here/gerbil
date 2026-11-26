"""Run selected test methods with GerbilLauncher and read back their outcomes."""

from __future__ import annotations

import re
import shlex
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

from gerbil.execution.build import ModuleClasspath
from gerbil.execution.container import TOOLS, Container, write_argfile

TestKey = tuple[str, str]
"""A test method as (binary class name, method name)."""


def test_id(key: TestKey) -> str:
    return f"{key[0]}#{key[1]}"


_PLATFORM_ENGINE = re.compile(r"junit-platform-engine-(\d[^/]*)\.jar$")
_PLATFORM_LAUNCHER = re.compile(r"junit-platform-launcher-\d[^/]*\.jar$")
_JUPITER_API = re.compile(r"junit-jupiter-api-(\d[^/]*)\.jar$")
_JUPITER_ENGINE = re.compile(r"junit-jupiter-engine-\d[^/]*\.jar$")
_M2_REPOSITORY = PurePosixPath("/root/.m2/repository")
_LAUNCHER_CLASSES = str(TOOLS / "lib" / "launcher")
_JUNIT4_PLATFORM = str(TOOLS / "lib" / "junit4" / "*")


class TestStatus(str, Enum):
    """Baseline outcome of a selected test, or why it was not run."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_RUN = "not-run"
    NOT_IN_BUILD = "not-in-build"
    OUT_OF_SCOPE = "out-of-scope"


# Prevent pytest from treating the "Test"-prefixed enum as a test class.
setattr(TestStatus, "__test__", False)


@dataclass(frozen=True)
class JUnitRuntime:
    """Classpath additions for running a module's tests on the JUnit Platform."""

    entries: tuple[str, ...]
    platform_native: bool


def resolve_junit_runtime(
    container: Container, classpath: ModuleClasspath, log: Path
) -> JUnitRuntime:
    """Match the module's JUnit Platform version, or add the vintage engine for JUnit 4."""
    platform_version = _jar_version(_PLATFORM_ENGINE, classpath)
    if platform_version is None:
        return JUnitRuntime((_JUNIT4_PLATFORM,), platform_native=False)
    entries = []
    if not any(_PLATFORM_LAUNCHER.search(entry) for entry in classpath.entries):
        entries.append(
            _fetch_jar(
                container,
                "org.junit.platform",
                "junit-platform-launcher",
                platform_version,
                log,
            )
        )
    # Surefire supplies the engine for a module that declares only the Jupiter API;
    # without it those tests are silently undiscovered.
    jupiter_version = _jar_version(_JUPITER_API, classpath)
    if jupiter_version is not None and not any(
        _JUPITER_ENGINE.search(entry) for entry in classpath.entries
    ):
        entries.append(
            _fetch_jar(
                container,
                "org.junit.jupiter",
                "junit-jupiter-engine",
                jupiter_version,
                log,
            )
        )
    return JUnitRuntime(tuple(entries), platform_native=True)


def _jar_version(pattern: re.Pattern[str], classpath: ModuleClasspath) -> str | None:
    return next(
        (
            match.group(1)
            for entry in classpath.entries
            if (match := pattern.search(entry))
        ),
        None,
    )


def _fetch_jar(
    container: Container, group: str, artifact: str, version: str, log: Path
) -> str:
    jar = (
        _M2_REPOSITORY
        / group.replace(".", "/")
        / artifact
        / version
        / f"{artifact}-{version}.jar"
    )
    fetch = [
        "mvn",
        "--batch-mode",
        "--quiet",
        "dependency:get",
        f"-Dartifact={group}:{artifact}:{version}",
        "-Dtransitive=false",
    ]
    # A jar an earlier stage fetched needs no Maven, which would write to the local
    # repository while another project's build may be writing to it.
    container.run(
        ["bash", "-c", f"test -f {shlex.quote(str(jar))} || {shlex.join(fetch)}"],
        log=log,
        workdir=TOOLS,
    )
    return str(jar)


def run_tests(
    container: Container,
    classpath: ModuleClasspath,
    runtime: JUnitRuntime,
    tests: Sequence[TestKey],
    *,
    jvm_args: Sequence[str],
    run_directory: PurePosixPath,
    log: Path,
    timeout_seconds: int | None = None,
) -> dict[TestKey, TestStatus]:
    """Run `tests` in one JVM from the module directory, as PIT runs them."""
    host_directory = container.host_path(run_directory)
    host_directory.mkdir(parents=True, exist_ok=True)
    (host_directory / "results.tsv").unlink(missing_ok=True)
    (host_directory / "selection.tsv").write_text(
        "".join(f"{class_name}\t{method}\n" for class_name, method in tests),
        encoding="utf-8",
    )
    write_argfile(
        host_directory / "java.args",
        [
            *jvm_args,
            "-cp",
            # First, so the launcher's platform jars shadow an older
            # junit-platform-commons that a JUnit 4 module pulls in transitively.
            ":".join([*runtime.entries, *classpath.entries, _LAUNCHER_CLASSES]),
            "GerbilLauncher",
            str(run_directory / "selection.tsv"),
            str(run_directory / "results.tsv"),
        ],
    )
    container.run(
        ["java", f"@{run_directory / 'java.args'}"],
        log=log,
        workdir=PurePosixPath(classpath.directory),
        timeout_seconds=timeout_seconds,
    )
    return read_results(host_directory / "results.tsv", tests)


def read_results(path: Path, tests: Sequence[TestKey]) -> dict[TestKey, TestStatus]:
    """Fold per-invocation launcher lines into one status per selected test."""
    reported: dict[TestKey, set[str]] = defaultdict(set)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            status, class_name, method = line.split("\t")
            reported[(class_name, method)].add(status)
    return {test: _fold(reported.get(test, set())) for test in tests}


def _fold(statuses: set[str]) -> TestStatus:
    if not statuses:
        return TestStatus.NOT_RUN
    if "FAILED" in statuses:
        return TestStatus.FAILED
    if "SUCCESSFUL" in statuses:
        return TestStatus.PASSED
    return TestStatus.SKIPPED
