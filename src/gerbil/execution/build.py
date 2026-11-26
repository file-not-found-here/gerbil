"""Compile a workspace's test modules and export each module's test runtime and production
classpaths."""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from pydantic import BaseModel

from gerbil.execution.container import OUTPUT, TOOLS, WORKSPACE, Container
from gerbil.execution.errors import ExecutionError
from gerbil.execution.projects import BuildTool, ExecutionProject

_CLASSPATH_FILE = "gerbil-classpath.txt"
_PRODUCTION_CLASSPATH_FILE = "gerbil-production-classpath.txt"
_DEPENDENCIES_FILE = "gerbil-dependencies.txt"
CODELESS_CLASS_FILES = frozenset({"module-info.class", "package-info.class"})
_EFFECTIVE_POM = OUTPUT / "effective-pom.xml"
_EFFECTIVE_POM_GOAL = "org.apache.maven.plugins:maven-help-plugin:3.5.1:effective-pom"
_GRADLE = ("./gradlew", "--no-daemon", "--init-script", str(TOOLS / "gerbil.gradle"))
# Quality gates and coverage plugins that are irrelevant to (or would collide with)
# compiling and running the tests ourselves.
MAVEN_SKIP_PROPERTIES = (
    "-Dcheckstyle.skip",
    "-Denforcer.skip",
    "-Djacoco.skip",
    "-Dlicense.skip",
    "-Dmaven.javadoc.skip",
    "-Dpmd.skip",
    "-Drat.skip",
    "-Dspotbugs.skip",
    "-Dspotless.check.skip",
)


class ModuleClasspath(BaseModel):
    """A test module's runtime classpath and project class directories, as container paths.

    `production_directories` are those its production code runs with, its own included.
    """

    module: str
    directory: str
    entries: list[str]
    code_directories: list[str]
    production_directories: list[str]


class _Listing(NamedTuple):
    entries: list[str]
    production_entries: list[str]


def build_root(project: ExecutionProject) -> PurePosixPath:
    return WORKSPACE / project.build_root


def list_build_modules(
    container: Container, project: ExecutionProject, log: Path
) -> set[str]:
    """Module directories, relative to the build root, that the project build includes."""
    root = build_root(project)
    if project.build_tool is BuildTool.MAVEN:
        return _maven_modules(container, project, root, log)
    return set(_gradle_projects(container, project, root, log))


def compile_test_modules(
    container: Container,
    project: ExecutionProject,
    modules: Sequence[str],
    log: Path,
) -> list[ModuleClasspath]:
    """Compile `modules` (directories relative to the build root) and their dependencies."""
    root = build_root(project)
    if project.build_tool is BuildTool.MAVEN:
        listings = _compile_maven(container, project, root, modules, log)
    else:
        listings = _compile_gradle(container, project, root, modules, log)
    classpaths = []
    for module, listing in listings.items():
        code_directories = _code_directories(container, listing.entries)
        classpaths.append(
            ModuleClasspath(
                module=module,
                directory=str(root / module),
                entries=listing.entries,
                code_directories=code_directories,
                # PIT reads classes from the test classpath; a directory only the
                # compiler sees, or an included build's, would be counted unmutated.
                production_directories=[
                    directory
                    for directory in _code_directories(
                        container, listing.production_entries
                    )
                    if directory in code_directories
                ],
            )
        )
    return classpaths


def _code_directories(container: Container, entries: Sequence[str]) -> list[str]:
    return list(
        dict.fromkeys(
            entry for entry in entries if _is_code_directory(container, entry)
        )
    )


def class_names(container: Container, directories: Sequence[str]) -> list[str]:
    """Binary names of the classes in project class directories."""
    names = set()
    for directory in directories:
        root = container.host_path(PurePosixPath(directory))
        for path in root.rglob("*.class"):
            if path.name not in CODELESS_CLASS_FILES:
                names.add(path.relative_to(root).with_suffix("").as_posix())
    return sorted(name.replace("/", ".") for name in names)


def _is_code_directory(container: Container, entry: str) -> bool:
    """Main output directories of workspace modules; test outputs are named test*."""
    path = PurePosixPath(entry)
    return (
        path.is_relative_to(WORKSPACE)
        and not path.name.startswith("test")
        and container.host_path(path).is_dir()
    )


def _compile_maven(
    container: Container,
    project: ExecutionProject,
    root: PurePosixPath,
    modules: Sequence[str],
    log: Path,
) -> dict[str, _Listing]:
    container.run(
        [
            "mvn",
            "--batch-mode",
            "--no-transfer-progress",
            # Seeded snapshots stand for builds their repositories no longer serve.
            "--no-snapshot-updates",
            "--projects",
            ",".join([*modules, *project.extra_build_modules]),
            "--also-make",
            project.maven_phase,
            "dependency:build-classpath",
            f"-Dmdep.outputFile=target/{_CLASSPATH_FILE}",
            # Lists each dependency's scope, to list's `outputFile` rather than to
            # build-classpath's `mdep.outputFile`.
            "dependency:list",
            f"-DoutputFile=target/{_DEPENDENCIES_FILE}",
            "-DoutputAbsoluteArtifactFilename=true",
            *MAVEN_SKIP_PROPERTIES,
            *project.build_args,
        ],
        log=log,
        workdir=root,
    )
    listings: dict[str, _Listing] = {}
    for module in modules:
        target = root / module / "target"
        classpath = container.host_path(target / _CLASSPATH_FILE).read_text()
        dependencies = container.host_path(target / _DEPENDENCIES_FILE).read_text()
        listings[module] = _Listing(
            entries=[
                str(target / "test-classes"),
                str(target / "classes"),
                *(
                    _classes_of(entry)
                    for entry in classpath.strip().split(":")
                    if entry
                ),
            ],
            production_entries=[
                str(target / "classes"),
                *(
                    _classes_of(path)
                    for scope, path in _listed_dependencies(dependencies)
                    if scope != "test"
                ),
            ],
        )
    return listings


def _listed_dependencies(listing: str) -> list[tuple[str, str]]:
    """(scope, file) of each dependency `dependency:list` resolved.

    Its lines read `group:artifact:type[:classifier]:version:scope:file`, which newer
    plugins follow with the module name.
    """
    dependencies = []
    for line in listing.splitlines():
        fields = line.strip().split(" ", 1)[0].split(":")
        if len(fields) >= 6 and fields[-1].startswith("/"):
            dependencies.append((fields[-2], fields[-1]))
    return dependencies


def _classes_of(entry: str) -> str:
    """Class directory of a sibling module's packaged jar, so its code stays measurable."""
    path = PurePosixPath(entry)
    if not (
        path.is_relative_to(WORKSPACE)
        and path.parent.name == "target"
        and path.suffix == ".jar"
    ):
        return entry
    name = "test-classes" if path.stem.endswith("-tests") else "classes"
    return str(path.parent / name)


def _compile_gradle(
    container: Container,
    project: ExecutionProject,
    root: PurePosixPath,
    modules: Sequence[str],
    log: Path,
) -> dict[str, _Listing]:
    project_paths = _gradle_projects(container, project, root, log)
    tasks = []
    for module in modules:
        if module not in project_paths:
            raise ExecutionError(f"no Gradle project at {root / module}")
        tasks.append(f"{project_paths[module].rstrip(':')}:gerbilClasspath")
    container.run([*_GRADLE, *tasks, *project.build_args], log=log, workdir=root)
    return {
        module: _Listing(
            *(
                container.host_path(root / module / "build" / name)
                .read_text()
                .splitlines()
                for name in (_CLASSPATH_FILE, _PRODUCTION_CLASSPATH_FILE)
            )
        )
        for module in modules
    }


def _gradle_projects(
    container: Container, project: ExecutionProject, root: PurePosixPath, log: Path
) -> dict[str, str]:
    """Gradle project paths keyed by project directory relative to the build root.

    Projects of included builds are addressed through the including build's name.
    """
    container.run(
        [*_GRADLE, "gerbilProjects", *project.build_args], log=log, workdir=root
    )
    projects: dict[str, str] = {}
    builds = [(root, "")]
    while builds:
        build, prefix = builds.pop()
        listing = container.host_path(build / "build" / "gerbil-projects.tsv")
        for line in listing.read_text().splitlines():
            kind, name, directory = line.split("\t")
            path = PurePosixPath(directory)
            if not path.is_relative_to(root):
                continue
            if kind == "build":
                builds.append((path, f"{prefix}:{name}"))
            else:
                project_path = prefix + ("" if name == ":" else name)
                projects[str(path.relative_to(root))] = project_path or ":"
    return projects


def _maven_modules(
    container: Container, project: ExecutionProject, root: PurePosixPath, log: Path
) -> set[str]:
    """Reactor modules, including profile-activated ones, from the effective POM."""
    container.run(
        [
            "mvn",
            "--batch-mode",
            "--quiet",
            "--no-snapshot-updates",
            _EFFECTIVE_POM_GOAL,
            f"-Doutput={_EFFECTIVE_POM}",
            *MAVEN_SKIP_PROPERTIES,
            *project.build_args,
        ],
        log=log,
        workdir=root,
    )
    pom = ElementTree.parse(container.host_path(_EFFECTIVE_POM)).getroot()
    projects = list(pom) if _local_name(pom.tag) == "projects" else [pom]
    modules = set()
    for element in projects:
        build_directory = PurePosixPath(_child_text(element, "build", "directory"))
        modules.add(str(build_directory.parent.relative_to(root)))
    return modules


def _child_text(element: ElementTree.Element, *names: str) -> str:
    for name in names:
        element = next(child for child in element if _local_name(child.tag) == name)
    return element.text or ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
