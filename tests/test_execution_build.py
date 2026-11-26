from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from gerbil.execution.build import (
    ModuleClasspath,
    class_names,
    compile_test_modules,
    list_build_modules,
)
from gerbil.execution.errors import ExecutionError
from gerbil.execution.projects import PROJECTS, BuildTool, ExecutionProject
from tests.execution_fakes import RecordingContainer, recording_container

_MAVEN = ExecutionProject("demo", "org_demo", BuildTool.MAVEN, 17, build_args=("-Px",))
_GRADLE = ExecutionProject(
    "demo", "org_demo", BuildTool.GRADLE, 17, build_root="server"
)


_EFFECTIVE_POM = """<?xml version="1.0" encoding="UTF-8"?>
<projects>
  <project xmlns="http://maven.apache.org/POM/4.0.0">
    <artifactId>demo</artifactId>
    <build><directory>/workspace/target</directory></build>
  </project>
  <project xmlns="http://maven.apache.org/POM/4.0.0">
    <artifactId>api</artifactId>
    <build><directory>/workspace/api/target</directory></build>
  </project>
  <project xmlns="http://maven.apache.org/POM/4.0.0">
    <artifactId>kafka</artifactId>
    <build><directory>/workspace/collector/kafka/target</directory></build>
  </project>
</projects>
"""


def test_maven_build_modules_come_from_the_effective_reactor(tmp_path: Path) -> None:
    def effect(
        container: RecordingContainer, command: list[str], workdir: PurePosixPath
    ) -> None:
        output = next(arg for arg in command if arg.startswith("-Doutput="))
        path = container.host_path(PurePosixPath(output.removeprefix("-Doutput=")))
        path.write_text(_EFFECTIVE_POM)

    container = recording_container(tmp_path, effect)

    modules = list_build_modules(container, _MAVEN, tmp_path / "log")

    assert modules == {".", "api", "collector/kafka"}
    ((command, workdir),) = container.commands
    assert "org.apache.maven.plugins:maven-help-plugin:3.5.1:effective-pom" in command
    assert command[-1] == "-Px"
    assert workdir == PurePosixPath("/workspace")


# Older plugins end each line at the file; newer ones add the module name.
_DEPENDENCIES = """
The following files have been resolved:
   org.demo:core:jar:1.0:compile:/workspace/core/target/classes -- module core
   org.demo:core:jar:tests:1.0:test:/workspace/core/target/test-classes
   org.demo:fixtures:jar:1.0:test:/workspace/fixtures/target/classes
   org.demo:runtime:jar:1.0:provided:/workspace/runtime/target/classes (optional)
   junit:junit:jar:4.13:test:/root/.m2/repository/junit.jar
"""


def _maven_effect(
    container: RecordingContainer, command: list[str], workdir: PurePosixPath
) -> None:
    for module in ("api", "core", "fixtures", "runtime"):
        target = container.workspace / module / "target"
        (target / "classes").mkdir(parents=True)
        (target / "test-classes").mkdir(parents=True)
        (target / "gerbil-classpath.txt").write_text(
            "/workspace/core/target/classes:/workspace/core/target/test-classes"
            ":/workspace/fixtures/target/classes:/workspace/runtime/target/classes"
            ":/root/.m2/repository/junit.jar\n"
        )
        (target / "gerbil-dependencies.txt").write_text(_DEPENDENCIES)


def test_maven_compile_exports_module_classpaths(tmp_path: Path) -> None:
    container = recording_container(tmp_path, _maven_effect)

    api, core = compile_test_modules(
        container, _MAVEN, ["api", "core"], tmp_path / "log"
    )

    ((command, workdir),) = container.commands
    assert command[:8] == [
        "mvn",
        "--batch-mode",
        "--no-transfer-progress",
        "--no-snapshot-updates",
        "--projects",
        "api,core",
        "--also-make",
        "test-compile",
    ]
    assert "-Djacoco.skip" in command
    assert command[-1] == "-Px"
    listing = command.index("dependency:list")
    assert command[listing + 1 : listing + 3] == [
        "-DoutputFile=target/gerbil-dependencies.txt",
        "-DoutputAbsoluteArtifactFilename=true",
    ]
    assert workdir == PurePosixPath("/workspace")
    assert api == ModuleClasspath(
        module="api",
        directory="/workspace/api",
        entries=[
            "/workspace/api/target/test-classes",
            "/workspace/api/target/classes",
            "/workspace/core/target/classes",
            "/workspace/core/target/test-classes",
            "/workspace/fixtures/target/classes",
            "/workspace/runtime/target/classes",
            "/root/.m2/repository/junit.jar",
        ],
        code_directories=[
            "/workspace/api/target/classes",
            "/workspace/core/target/classes",
            "/workspace/fixtures/target/classes",
            "/workspace/runtime/target/classes",
        ],
        # A test-scoped module is left out; a provided one is deployed with the service.
        production_directories=[
            "/workspace/api/target/classes",
            "/workspace/core/target/classes",
            "/workspace/runtime/target/classes",
        ],
    )
    assert core.directory == "/workspace/core"


def _gradle_effect(
    container: RecordingContainer, command: list[str], workdir: PurePosixPath
) -> None:
    root = container.workspace / "server"
    (root / "build").mkdir(parents=True, exist_ok=True)
    if command[-1] == "gerbilProjects":
        (root / "build" / "gerbil-projects.tsv").write_text(
            "project\t:\t/workspace/server\n"
            "project\t:web\t/workspace/server/web\n"
            "build\tgate\t/workspace/server/gate\n"
            "build\tkork\t/workspace/kork"
        )
        (root / "gate" / "build").mkdir(parents=True, exist_ok=True)
        (root / "gate" / "build" / "gerbil-projects.tsv").write_text(
            "project\t:\t/workspace/server/gate\n"
            "project\t:gate-web\t/workspace/server/gate/gate-web"
        )
        return
    for classes in ("server/fixtures", "server/annotations", "kork/kork-web"):
        (container.workspace / classes / "build/classes/java/main").mkdir(
            parents=True, exist_ok=True
        )
    for module in (root, root / "web"):
        directory = f"/workspace/{module.relative_to(container.workspace)}"
        (module / "build" / "classes/java/main").mkdir(parents=True, exist_ok=True)
        (module / "build" / "gerbil-classpath.txt").write_text(
            f"{directory}/build/classes/java/test\n"
            f"{directory}/build/classes/java/main\n"
            "/workspace/server/build/classes/java/main\n"
            "/workspace/server/fixtures/build/classes/java/main\n"
            "/workspace/server/build/resources/main\n"
            "/workspace/kork/kork-web/build/libs/kork-web.jar"
        )
        # Directories off the test classpath: one only the compiler sees, and an
        # included build's.
        (module / "build" / "gerbil-production-classpath.txt").write_text(
            f"{directory}/build/classes/java/main\n"
            "/workspace/server/build/classes/java/main\n"
            "/workspace/server/annotations/build/classes/java/main\n"
            "/workspace/kork/kork-web/build/classes/java/main\n"
            "/root/.gradle/guava.jar"
        )


def test_gradle_compile_maps_module_directories_to_project_tasks(
    tmp_path: Path,
) -> None:
    container = recording_container(tmp_path, _gradle_effect)

    root, web = compile_test_modules(container, _GRADLE, [".", "web"], tmp_path / "log")

    (listing, _), (compile_command, workdir) = container.commands
    assert listing[-1] == "gerbilProjects"
    assert compile_command[-2:] == [":gerbilClasspath", ":web:gerbilClasspath"]
    assert workdir == PurePosixPath("/workspace/server")
    assert root.directory == "/workspace/server"
    assert web.code_directories == [
        "/workspace/server/web/build/classes/java/main",
        "/workspace/server/build/classes/java/main",
        "/workspace/server/fixtures/build/classes/java/main",
    ]
    assert web.production_directories == [
        "/workspace/server/web/build/classes/java/main",
        "/workspace/server/build/classes/java/main",
    ]


def test_gradle_compile_rejects_directories_outside_the_build(tmp_path: Path) -> None:
    container = recording_container(tmp_path, _gradle_effect)

    with pytest.raises(ExecutionError, match="no Gradle project"):
        compile_test_modules(container, _GRADLE, ["standalone"], tmp_path / "log")


def test_gradle_build_modules_include_included_builds_under_the_root(
    tmp_path: Path,
) -> None:
    container = recording_container(tmp_path, _gradle_effect)

    assert list_build_modules(container, _GRADLE, tmp_path / "log") == {
        ".",
        "web",
        "gate",
        "gate/gate-web",
    }


def test_gradle_compile_addresses_included_build_projects(tmp_path: Path) -> None:
    def effect(
        container: RecordingContainer, command: list[str], workdir: PurePosixPath
    ) -> None:
        _gradle_effect(container, command, workdir)
        if command[-1] != "gerbilProjects":
            listing = container.workspace / "server/gate/gate-web/build"
            listing.mkdir(parents=True, exist_ok=True)
            for name in ("gerbil-classpath.txt", "gerbil-production-classpath.txt"):
                (listing / name).write_text("/root/.gradle/x.jar")

    container = recording_container(tmp_path, effect)

    compile_test_modules(container, _GRADLE, ["gate/gate-web"], tmp_path / "log")

    (_, _), (compile_command, _) = container.commands
    assert compile_command[-1] == ":gate:gate-web:gerbilClasspath"


def test_every_project_has_a_supported_jdk() -> None:
    assert {project.jdk for project in PROJECTS.values()} <= {11, 17, 21}


def test_maven_package_phase_maps_sibling_jars_to_class_directories(
    tmp_path: Path,
) -> None:
    def effect(
        container: RecordingContainer, command: list[str], workdir: PurePosixPath
    ) -> None:
        for module in ("api", "core"):
            target = container.workspace / module / "target"
            (target / "classes").mkdir(parents=True)
        (container.workspace / "api/target/gerbil-classpath.txt").write_text(
            "/workspace/core/target/core-1.jar:"
            "/workspace/core/target/core-1-tests.jar:"
            "/root/.m2/repository/junit.jar\n"
        )
        (container.workspace / "api/target/gerbil-dependencies.txt").write_text(
            "   org.demo:core:jar:1:compile:/workspace/core/target/core-1.jar\n"
            "   org.demo:core:test-jar:tests:1:compile:"
            "/workspace/core/target/core-1-tests.jar\n"
        )

    container = recording_container(tmp_path, effect)
    project = replace(_MAVEN, maven_phase="package", extra_build_modules=("dist",))

    (api,) = compile_test_modules(container, project, ["api"], tmp_path / "log")

    ((command, _),) = container.commands
    assert command[command.index("--projects") + 1] == "api,dist"
    assert command[command.index("--also-make") + 1] == "package"
    assert api.entries == [
        "/workspace/api/target/test-classes",
        "/workspace/api/target/classes",
        "/workspace/core/target/classes",
        "/workspace/core/target/test-classes",
        "/root/.m2/repository/junit.jar",
    ]
    assert api.code_directories == [
        "/workspace/api/target/classes",
        "/workspace/core/target/classes",
    ]
    assert api.production_directories == [
        "/workspace/api/target/classes",
        "/workspace/core/target/classes",
    ]


def test_class_names_are_binary_names_of_the_directories_code(tmp_path: Path) -> None:
    container = recording_container(tmp_path)
    for relative in (
        "api/classes/org/demo/Service.class",
        "api/classes/org/demo/Service$1.class",
        "api/classes/org/demo/package-info.class",
        "api/classes/module-info.class",
        "api/classes/org/demo/messages.properties",
        "core/classes/org/core/Tool.class",
    ):
        path = container.workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    assert class_names(
        container, ["/workspace/api/classes", "/workspace/core/classes"]
    ) == ["org.core.Tool", "org.demo.Service", "org.demo.Service$1"]
