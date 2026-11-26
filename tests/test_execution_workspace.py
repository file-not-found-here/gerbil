from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gerbil.execution.errors import ExecutionError
from gerbil.execution.projects import PROJECTS, Suite
from gerbil.execution.sources import SuiteSources
from gerbil.execution.workspace import (
    MINIMUM_GIT,
    Clearing,
    apply_workspace_copies,
    materialize_workspace,
    git_version,
    restore_workspace_paths,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _staged_patch(repo: Path, path: Path) -> Path:
    """Write the staged change as a patch, then restore the repository."""
    _git(repo, "add", "-A")
    path.write_text(_git(repo, "diff", "--cached"))
    _git(repo, "reset", "--quiet", "--hard")
    return path


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src/test/java").mkdir(parents=True)
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _cleared_tree(repo: Path, *paths: str) -> str:
    """The tree `paths` leave once deleted from HEAD, without touching the checkout."""
    _git(repo, "rm", "--quiet", "--cached", "--", *paths)
    tree = _git(repo, "write-tree").strip()
    _git(repo, "reset", "--quiet")
    return tree


def test_materialize_checks_out_pinned_commit_clears_and_applies_patches(
    tmp_path: Path, repo: Path
) -> None:
    (repo / "src/test/java/OldTest.java").write_text("class OldTest {}\n")
    (repo / "src/test/java/Kept.java").write_text("class Kept {}\n")
    (repo / "src/test/java/[odd] name*.java").write_text("class Odd {}\n")
    pinned = _commit_all(repo, "pinned")
    cleared = ("src/test/java/OldTest.java", "src/test/java/[odd] name*.java")
    clearing = Clearing(cleared, _cleared_tree(repo, *cleared))
    for path in cleared:
        (repo / path).unlink()
    _commit_all(repo, "cleared")
    (repo / "src/test/java/AgentTest.java").write_text("class AgentTest {}\n")
    agent = _staged_patch(repo, tmp_path / "git_diff.patch")
    (repo / "README").write_text("later\n")
    _commit_all(repo, "later")

    destination = tmp_path / "suite" / "workspace"
    materialize_workspace(repo, pinned, destination, clearing=clearing, patches=[agent])

    assert _git(destination, "rev-parse", "HEAD").strip() == pinned
    assert not (destination / "README").exists()
    assert not (destination / "src/test/java/OldTest.java").exists()
    assert not (destination / "src/test/java/[odd] name*.java").exists()
    assert (destination / "src/test/java/Kept.java").exists()
    assert (destination / "src/test/java/AgentTest.java").exists()
    assert _git(destination, "diff", "--cached", "--name-only") == ""


def test_materialize_refuses_a_clearing_that_leaves_another_tree(
    tmp_path: Path, repo: Path
) -> None:
    (repo / "src/test/java/OldTest.java").write_text("class OldTest {}\n")
    pinned = _commit_all(repo, "pinned")
    destination = tmp_path / "workspace"

    with pytest.raises(ExecutionError, match="not the recorded 0{40}"):
        materialize_workspace(
            repo,
            pinned,
            destination,
            clearing=Clearing(("src/test/java/OldTest.java",), "0" * 40),
        )

    assert not destination.exists()


def test_an_empty_clearing_leaves_the_commits_tree(tmp_path: Path, repo: Path) -> None:
    (repo / "README").write_text("pinned\n")
    pinned = _commit_all(repo, "pinned")
    tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()

    materialize_workspace(
        repo, pinned, tmp_path / "workspace", clearing=Clearing((), tree)
    )

    assert (tmp_path / "workspace" / "README").exists()


def test_materialize_reuses_existing_workspace(tmp_path: Path) -> None:
    destination = tmp_path / "workspace"
    destination.mkdir()
    (destination / "marker").write_text("kept")

    materialize_workspace(tmp_path / "missing-repo", "deadbeef", destination)

    assert (destination / "marker").read_text() == "kept"


def test_materialize_failure_leaves_no_workspace(tmp_path: Path, repo: Path) -> None:
    (repo / "README").write_text("pinned\n")
    pinned = _commit_all(repo, "pinned")
    broken = tmp_path / "broken.patch"
    broken.write_text("not a patch\n")
    destination = tmp_path / "workspace"

    with pytest.raises(ExecutionError, match="apply"):
        materialize_workspace(repo, pinned, destination, patches=[broken])

    assert not destination.exists()


def test_sources_locate_analyses_and_agent_patches(tmp_path: Path) -> None:
    project = PROJECTS["hertzbeat"]
    run = tmp_path / "codex-raw" / "20260925T063521Z__codex__hertzbeat"
    (run / "output").mkdir(parents=True)
    sources = SuiteSources(outputs_root=tmp_path, repos_root=tmp_path / "repos")

    assert sources.analysis_path(project, Suite.DEVELOPER) == (
        tmp_path / "dev-full-gerbil" / "apache_hertzbeat" / "gerbil.json"
    )
    assert sources.analysis_path(project, Suite.CLAUDE_CODE) == (
        tmp_path / "claude-code-gerbil" / "hertzbeat" / "gerbil.json"
    )
    assert sources.source_repo(project) == tmp_path / "repos" / "hertzbeat"
    assert sources.agent_patches(project, Suite.CODEX) == (
        run / "output" / "test_clearing.patch",
        run / "output" / "git_diff.patch",
    )
    assert (
        sources.test_clearing_patch(project) == run / "output" / "test_clearing.patch"
    )


def test_sources_require_exactly_one_agent_run(tmp_path: Path) -> None:
    sources = SuiteSources(outputs_root=tmp_path, repos_root=tmp_path)

    with pytest.raises(ExecutionError, match="found 0"):
        sources.agent_patches(PROJECTS["uaa"], Suite.CLAUDE_CODE)


def test_workspace_copies_create_ignored_build_files(tmp_path: Path) -> None:
    (tmp_path / "application.properties.EXAMPLE").write_text("key=value\n")

    apply_workspace_copies(
        tmp_path, [("application.properties.EXAMPLE", "application.properties")]
    )

    assert (tmp_path / "application.properties").read_text() == "key=value\n"


def test_workspace_restores_bring_back_cleared_test_data(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "data").mkdir()
    (repo / "data" / "fixture.txt").write_text("fixture\n")
    (repo / "data" / "[odd] name*.txt").write_text("odd\n")
    (repo / "module" / "src").mkdir(parents=True)
    (repo / "module" / "pom.xml").write_text("<project/>\n")
    (repo / "module" / "src" / "Helper.java").write_text("class Helper {}\n")
    (repo / "OldTest.java").write_text("class OldTest {}\n")
    _commit_all(repo, "base")
    (repo / "data" / "fixture.txt").unlink()
    (repo / "data" / "[odd] name*.txt").unlink()
    (repo / "module" / "src" / "Helper.java").unlink()
    # A suite's own edit under a restored path.
    (repo / "module" / "pom.xml").write_text("<project><dependencies/></project>\n")
    (repo / "OldTest.java").unlink()

    restore_workspace_paths(repo, ["data", "module"])
    restore_workspace_paths(repo, [])

    assert (repo / "data" / "fixture.txt").read_text() == "fixture\n"
    assert (repo / "data" / "[odd] name*.txt").read_text() == "odd\n"
    assert (repo / "module" / "src" / "Helper.java").read_text() == "class Helper {}\n"
    assert (
        repo / "module" / "pom.xml"
    ).read_text() == "<project><dependencies/></project>\n"
    assert not (repo / "OldTest.java").exists()


def test_git_version_reads_the_installed_git() -> None:
    major, minor = git_version()

    assert (major, minor) >= MINIMUM_GIT
    assert f"git version {major}.{minor}" in _git(Path("."), "version")
