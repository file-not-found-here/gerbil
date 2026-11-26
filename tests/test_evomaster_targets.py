from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from gerbil.evomaster.pipeline import RunSettings, evomaster_arguments
from gerbil.evomaster.targets import (
    DOCKER_DIRECTORY,
    TARGETS,
    EvoMasterTarget,
    target_for,
)
from gerbil.execution.projects import PROJECTS

_ALL_TARGETS = list(TARGETS.values())
_REPOSITORIES = DOCKER_DIRECTORY.parents[1] / "resources"
_BUILD_FILE = re.compile(r"pom\.xml|.*\.gradle(\.kts)?")


def _driver_source(target: EvoMasterTarget) -> Path:
    module = Path(target.project.build_root) / target.module
    relative = target.driver.replace(".", "/") + ".java"
    return target.files / module / "src/test/java" / relative


@pytest.mark.parametrize("target", _ALL_TARGETS, ids=lambda target: target.name)
def test_the_driver_is_a_test_source_of_its_module(target: EvoMasterTarget) -> None:
    assert _driver_source(target).is_file()


@pytest.mark.parametrize("target", _ALL_TARGETS, ids=lambda target: target.name)
def test_drivers_stay_outside_the_packages_applications_scan(
    target: EvoMasterTarget,
) -> None:
    assert target.driver.startswith("em.embedded.")
    assert target.driver.endswith(".EmbeddedEvoMasterController")


@pytest.mark.parametrize("target", _ALL_TARGETS, ids=lambda target: target.name)
def test_drivers_listen_where_the_search_looks_for_them(
    target: EvoMasterTarget,
) -> None:
    source = _driver_source(target).read_text()

    assert "setControllerPort(40100)" in source
    assert "new InstrumentedSutStarter(" in source


_KEEPS_RUNNING_OUTSIDE_THE_SEARCH = re.compile(
    r"public void stopSut\(\) \{(\s*//[^\n]*)*\s*"
    r"(stopped = true;\s*\}|if \(!isInstrumentationActivated\(\)\) \{\s*return;\s*\})"
)


@pytest.mark.parametrize("target", _ALL_TARGETS, ids=lambda target: target.name)
def test_drivers_share_the_one_service_of_their_jvm(target: EvoMasterTarget) -> None:
    # Each class of a split suite, and of a PIT run, makes its own controller.
    source = _driver_source(target).read_text()
    declarations = re.findall(r"^    [^\s/*][^\n]*;$", source, re.M)

    assert _KEEPS_RUNNING_OUTSIDE_THE_SEARCH.search(source)
    assert declarations
    assert [d for d in declarations if not re.search(r"\bstatic\b", d)] == []


@pytest.mark.parametrize("target", _ALL_TARGETS, ids=lambda target: target.name)
def test_drivers_declare_the_schema_the_harness_fetches(
    target: EvoMasterTarget,
) -> None:
    assert f'"{target.schema_paths[0]}' in _driver_source(target).read_text()


@pytest.mark.parametrize("target", _ALL_TARGETS, ids=lambda target: target.name)
def test_the_driver_client_is_the_version_the_image_runs(
    target: EvoMasterTarget,
) -> None:
    image = re.search(
        r"^ARG EVOMASTER_VERSION=(\S+)$",
        (DOCKER_DIRECTORY / "Dockerfile").read_text(),
        re.M,
    )
    patch = target.build_patch.read_text()
    clients = re.findall(
        r"evomaster-(?:client-java-controller|test-utils-java)"
        r"(?::|</artifactId>\s*\+\s*<version>)([\d.]+)",
        patch,
    )

    assert image is not None
    assert clients == [image.group(1)] * 2


@pytest.mark.parametrize("target", _ALL_TARGETS, ids=lambda target: target.name)
def test_the_build_patch_only_changes_build_files(target: EvoMasterTarget) -> None:
    changed = re.findall(r"^diff --git a/(\S+) ", target.build_patch.read_text(), re.M)

    assert changed
    assert all(_BUILD_FILE.fullmatch(Path(path).name) for path in changed)


@pytest.mark.parametrize("target", _ALL_TARGETS, ids=lambda target: target.name)
def test_the_build_patch_applies_to_the_pinned_source(
    target: EvoMasterTarget,
) -> None:
    repository = _REPOSITORIES / target.project.name
    if not (repository / ".git").exists():
        pytest.skip(f"no checkout of {target.project.name}")
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "apply",
            "--check",
            str(target.build_patch.resolve()),
        ],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("target", _ALL_TARGETS, ids=lambda target: target.name)
def test_restores_are_resources_of_the_pinned_source(target: EvoMasterTarget) -> None:
    repository = _REPOSITORIES / target.project.name
    for path in target.restores:
        # Restoring developer test code would put it in the EvoMaster suite's tree.
        assert not path.endswith((".java", ".kt", ".groovy")), path
        if (repository / ".git").exists():
            assert (repository / path).exists(), path


@pytest.mark.parametrize("target", _ALL_TARGETS, ids=lambda target: target.name)
def test_target_excluded_paths_are_valid_patterns(target: EvoMasterTarget) -> None:
    for pattern in target.excluded_paths:
        re.compile(pattern)


@pytest.mark.parametrize(
    ("target", "path"),
    [
        ("hertzbeat", "/api/alert/sse/subscribe"),
        ("hertzbeat", "/api/manager/sse/subscribe"),
    ],
)
def test_scope_drops_event_streams_that_never_finish(target: str, path: str) -> None:
    assert any(re.fullmatch(p, path) for p in TARGETS[target].excluded_paths)


@pytest.mark.parametrize(
    ("path", "dropped"),
    [
        # Each path's only operation is a multipart upload, except the jar path's DELETE.
        ("/kylin/api/custom/jar", True),
        ("/kylin/api/metastore/import/models", True),
        ("/kylin/api/metastore/models", True),
        ("/kylin/api/metastore/validation/models", True),
        ("/kylin/api/projects/{project}/project_kerberos_info", True),
        # Form-encoded rather than multipart, so EvoMaster sends it.
        ("/kylin/api/metastore/backup/models", False),
        ("/kylin/api/metastore/previews/models", False),
        ("/kylin/api/custom/jar/list", False),
        ("/kylin/api/projects/{project}/project_config", False),
    ],
)
def test_kylin_scope_drops_the_upload_paths(path: str, dropped: bool) -> None:
    excluded = TARGETS["kylin"].excluded_paths

    assert any(re.fullmatch(p, path) for p in excluded) is dropped


@pytest.mark.parametrize(
    ("path", "dropped"),
    [("/error429", True), ("/error", False), ("/error404", False), ("/info", False)],
)
def test_uaa_scope_drops_only_the_error_page_that_always_answers_429(
    path: str, dropped: bool
) -> None:
    excluded = TARGETS["uaa"].excluded_paths

    assert any(re.fullmatch(p, path) for p in excluded) is dropped


def test_every_project_has_the_one_target_execution_measures() -> None:
    for project in PROJECTS.values():
        assert target_for(project).project is project


def test_target_names_make_valid_directory_and_class_names() -> None:
    for name in TARGETS:
        assert re.fullmatch(r"[a-z][a-z0-9-]*", name)


_DSPACE = TARGETS["dspace"]
# The webapp's own test classes come first on its test classpath.
_DSPACE_TEST_SOURCES = (
    "dspace-server-webapp/src/test/java",
    "dspace-api/src/test/java",
)
_DSPACE_CARRIED = _DSPACE.files / "dspace-server-webapp/src/test/java"


def _dspace_repository() -> Path:
    repository = _REPOSITORIES / _DSPACE.project.name
    if not (repository / ".git").exists():
        pytest.skip("no checkout of DSpace")
    return repository


def _dspace_test_source(repository: Path, relative: Path) -> Path | None:
    for root in _DSPACE_TEST_SOURCES:
        if (repository / root / relative).is_file():
            return Path(root) / relative
    return None


def _pinned(repository: Path, path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), "show", f"HEAD:{path}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_dspace_carries_each_test_double_its_test_installation_names() -> None:
    repository = _dspace_repository()
    # The installation keeps the first module's copy of a file, and dspace-api comes first.
    installed: dict[Path, Path] = {}
    for restore in _DSPACE.restores:
        if restore.endswith("src/test/data"):
            folder = repository / restore / "dspaceFolder"
            for config in folder.rglob("*"):
                if config.suffix in (".xml", ".cfg"):
                    installed.setdefault(config.relative_to(folder), config)
    named: set[str] = set()
    for config in installed.values():
        text = re.sub(
            r"<!--.*?-->|^[ \t]*#[^\n]*", "", config.read_text(), flags=re.S | re.M
        )
        named.update(re.findall(r"\borg\.dspace(?:\.\w+)+", text))
    sources = [Path(*name.split(".")).with_suffix(".java") for name in named]
    doubles = [s for s in sources if _dspace_test_source(repository, s) is not None]

    carried = {
        copy.relative_to(_DSPACE_CARRIED)
        for copy in (_DSPACE_CARRIED / "org").rglob("*.java")
    }

    assert doubles
    # Besides the doubles, only the embedded Solr server they share.
    assert carried == {*doubles, Path("org/dspace/solr/MockSolrServer.java")}


def test_dspace_test_doubles_are_the_pinned_sources() -> None:
    repository = _dspace_repository()
    copies = sorted((_DSPACE_CARRIED / "org").rglob("*.java"))

    assert copies
    for copy in copies:
        relative = copy.relative_to(_DSPACE_CARRIED)
        original = _dspace_test_source(repository, relative)
        assert original is not None, relative
        expected = _pinned(repository, original)
        if relative.name == "MockSolrServer.java":
            # Reading dspace.dir itself keeps the test base classes out of the harness.
            expected = expected.replace(
                "import org.dspace.AbstractDSpaceIntegrationTest;\n", ""
            ).replace(
                "AbstractDSpaceIntegrationTest.getDspaceDir()",
                'System.getProperty("dspace.dir")',
            )
        assert copy.read_text() == expected, relative


def test_dspace_suites_do_not_fetch_the_schema_only_the_search_serves() -> None:
    driver = _driver_source(_DSPACE).read_text()

    assert '"--springdoc.api-docs.enabled=" + documented' in driver
    assert "documented = isInstrumentationActivated();" in driver
    assert "--addPreDefinedTests=false" in evomaster_arguments(_DSPACE, RunSettings())
