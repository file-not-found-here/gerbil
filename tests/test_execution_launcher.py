from __future__ import annotations

from pathlib import Path, PurePosixPath

from gerbil.execution.build import ModuleClasspath
from gerbil.execution.container import OUTPUT
from gerbil.execution.launcher import (
    JUnitRuntime,
    TestStatus,
    read_results,
    resolve_junit_runtime,
    run_tests,
)
from tests.execution_fakes import recording_container

_M2 = "/root/.m2/repository"


def _classpath(*entries: str) -> ModuleClasspath:
    return ModuleClasspath(
        module="api",
        directory="/workspace/api",
        entries=["/workspace/api/target/test-classes", *entries],
        code_directories=[],
        production_directories=[],
    )


def test_read_results_folds_invocations_per_test(tmp_path: Path) -> None:
    results = tmp_path / "results.tsv"
    results.write_text(
        "SUCCESSFUL\torg.A\tok\n"
        "SUCCESSFUL\torg.A\tparameterized\n"
        "FAILED\torg.A\tparameterized\n"
        "ABORTED\torg.A\tassumed\n"
        "SKIPPED\torg.A$Nested\tdisabled\n"
    )
    tests = [
        ("org.A", "ok"),
        ("org.A", "parameterized"),
        ("org.A", "assumed"),
        ("org.A$Nested", "disabled"),
        ("org.A", "neverReported"),
    ]

    assert read_results(results, tests) == {
        ("org.A", "ok"): TestStatus.PASSED,
        ("org.A", "parameterized"): TestStatus.FAILED,
        ("org.A", "assumed"): TestStatus.SKIPPED,
        ("org.A$Nested", "disabled"): TestStatus.SKIPPED,
        ("org.A", "neverReported"): TestStatus.NOT_RUN,
    }


def test_read_results_without_results_file_marks_tests_not_run(tmp_path: Path) -> None:
    assert read_results(tmp_path / "missing.tsv", [("org.A", "m")]) == {
        ("org.A", "m"): TestStatus.NOT_RUN
    }


def test_junit_runtime_uses_module_launcher_when_present(tmp_path: Path) -> None:
    container = recording_container(tmp_path)
    classpath = _classpath(
        f"{_M2}/org/junit/platform/junit-platform-engine/1.10.2/junit-platform-engine-1.10.2.jar",
        f"{_M2}/org/junit/platform/junit-platform-launcher/1.10.2/junit-platform-launcher-1.10.2.jar",
    )

    runtime = resolve_junit_runtime(container, classpath, tmp_path / "log")

    assert runtime == JUnitRuntime((), platform_native=True)
    assert container.commands == []


def test_junit_runtime_fetches_launcher_matching_engine_version(tmp_path: Path) -> None:
    container = recording_container(tmp_path)
    classpath = _classpath(
        "/root/.gradle/caches/modules-2/files-2.1/org.junit.platform/"
        "junit-platform-engine/1.9.3/abc/junit-platform-engine-1.9.3.jar"
    )

    runtime = resolve_junit_runtime(container, classpath, tmp_path / "log")

    assert runtime == JUnitRuntime(
        (
            f"{_M2}/org/junit/platform/junit-platform-launcher/1.9.3/"
            "junit-platform-launcher-1.9.3.jar",
        ),
        platform_native=True,
    )
    ((command, _),) = container.commands
    # Maven runs only when no earlier stage left the jar in the cache.
    assert command[:2] == ["bash", "-c"]
    assert command[2] == (
        f"test -f {runtime.entries[0]} || mvn --batch-mode --quiet dependency:get "
        "-Dartifact=org.junit.platform:junit-platform-launcher:1.9.3 -Dtransitive=false"
    )


def test_junit_runtime_fetches_engine_for_jupiter_api_alone(tmp_path: Path) -> None:
    container = recording_container(tmp_path)
    classpath = _classpath(
        f"{_M2}/org/junit/platform/junit-platform-engine/1.8.2/junit-platform-engine-1.8.2.jar",
        f"{_M2}/org/junit/platform/junit-platform-launcher/1.8.2/junit-platform-launcher-1.8.2.jar",
        f"{_M2}/org/junit/vintage/junit-vintage-engine/5.8.2/junit-vintage-engine-5.8.2.jar",
        f"{_M2}/org/junit/jupiter/junit-jupiter-api/5.8.2/junit-jupiter-api-5.8.2.jar",
    )

    runtime = resolve_junit_runtime(container, classpath, tmp_path / "log")

    assert runtime == JUnitRuntime(
        (
            f"{_M2}/org/junit/jupiter/junit-jupiter-engine/5.8.2/"
            "junit-jupiter-engine-5.8.2.jar",
        ),
        platform_native=True,
    )
    ((command, _),) = container.commands
    assert "-Dartifact=org.junit.jupiter:junit-jupiter-engine:5.8.2" in command[2]


def test_junit_runtime_keeps_declared_jupiter_engine(tmp_path: Path) -> None:
    container = recording_container(tmp_path)
    classpath = _classpath(
        f"{_M2}/org/junit/platform/junit-platform-engine/1.8.2/junit-platform-engine-1.8.2.jar",
        f"{_M2}/org/junit/platform/junit-platform-launcher/1.8.2/junit-platform-launcher-1.8.2.jar",
        f"{_M2}/org/junit/jupiter/junit-jupiter-api/5.8.2/junit-jupiter-api-5.8.2.jar",
        f"{_M2}/org/junit/jupiter/junit-jupiter-engine/5.8.2/junit-jupiter-engine-5.8.2.jar",
    )

    runtime = resolve_junit_runtime(container, classpath, tmp_path / "log")

    assert runtime == JUnitRuntime((), platform_native=True)
    assert container.commands == []


def test_junit_runtime_adds_vintage_platform_for_junit4_modules(tmp_path: Path) -> None:
    container = recording_container(tmp_path)

    runtime = resolve_junit_runtime(
        container, _classpath(f"{_M2}/junit/junit/4.13.2/junit-4.13.2.jar"), tmp_path
    )

    assert runtime == JUnitRuntime(("/opt/gerbil/lib/junit4/*",), platform_native=False)


def test_run_tests_launches_from_module_directory(tmp_path: Path) -> None:
    run_directory = OUTPUT / "runs" / "api" / "baseline"

    def effect(container, command, workdir):  # type: ignore[no-untyped-def]
        host = container.host_path(run_directory)
        (host / "results.tsv").write_text("SUCCESSFUL\torg.A$Inner\tm\n")

    container = recording_container(tmp_path, effect)
    statuses = run_tests(
        container,
        _classpath("/root/.m2/lib.jar"),
        JUnitRuntime(("/extra.jar",), platform_native=True),
        [("org.A$Inner", "m"), ("org.B", "n")],
        jvm_args=["-javaagent:/agent.jar=destfile=/out/x.exec", "--add-opens=a/b=C"],
        run_directory=run_directory,
        log=tmp_path / "log",
    )

    assert statuses == {
        ("org.A$Inner", "m"): TestStatus.PASSED,
        ("org.B", "n"): TestStatus.NOT_RUN,
    }
    ((command, workdir),) = container.commands
    assert command == ["java", "@/out/runs/api/baseline/java.args"]
    assert workdir == PurePosixPath("/workspace/api")
    host = tmp_path / "runs/api/baseline"
    assert (host / "selection.tsv").read_text() == "org.A$Inner\tm\norg.B\tn\n"
    argfile = (host / "java.args").read_text().splitlines()
    assert argfile == [
        '"-javaagent:/agent.jar=destfile=/out/x.exec"',
        '"--add-opens=a/b=C"',
        '"-cp"',
        '"/extra.jar:/workspace/api/target/test-classes:/root/.m2/lib.jar:'
        '/opt/gerbil/lib/launcher"',
        '"GerbilLauncher"',
        '"/out/runs/api/baseline/selection.tsv"',
        '"/out/runs/api/baseline/results.tsv"',
    ]
