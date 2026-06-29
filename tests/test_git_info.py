"""Tests for git metadata extraction and its wiring into project metadata."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gerbil.analysis.project import ProjectAnalysisInfo
from gerbil.analysis.shared.git_info import parse_remote_url, read_git_info
from tests.fake_java_analysis import FakeJavaAnalysis

_REMOTE_URL = "https://github.com/example/sample-project.git"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_repo_with_commit(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    (repo / "README.md").write_text("sample", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(
        repo,
        "-c",
        "user.name=Gerbil Test",
        "-c",
        "user.email=gerbil@example.com",
        "commit",
        "-m",
        "init",
    )
    return _git(repo, "rev-parse", "HEAD")


# Remote URL parsing: credentials and ports never leak into host/repository.


@pytest.mark.parametrize(
    ("remote_url", "expected_host", "expected_repository"),
    [
        ("https://github.com/owner/repo.git", "github.com", "owner/repo"),
        ("https://github.com/owner/repo", "github.com", "owner/repo"),
        ("https://github.com/owner/repo.git/", "github.com", "owner/repo"),
        (
            "https://x-access-token:ghp_secret@github.com/owner/repo.git",
            "github.com",
            "owner/repo",
        ),
        (
            "https://oauth2:glpat-secret@gitlab.com/owner/repo.git",
            "gitlab.com",
            "owner/repo",
        ),
        ("git@github.com:owner/repo.git", "github.com", "owner/repo"),
        ("git@GitHub.com:Owner/Repo.git", "github.com", "Owner/Repo"),
        ("ssh://git@github.com/owner/repo.git", "github.com", "owner/repo"),
        ("ssh://git@github.com:2222/owner/repo.git", "github.com", "owner/repo"),
        ("git://github.com/owner/repo.git", "github.com", "owner/repo"),
        (
            "https://gitlab.com/group/subgroup/repo.git",
            "gitlab.com",
            "group/subgroup/repo",
        ),
        ("/local/path/repo.git", None, None),
        ("../relative/repo", None, None),
        ("file:///local/path/repo.git", None, None),
        ("git@github.com:", "github.com", None),
        ("https://[/owner/repo.git", None, None),
    ],
)
def test_parse_remote_url(
    remote_url: str, expected_host: str | None, expected_repository: str | None
) -> None:
    assert parse_remote_url(remote_url) == (expected_host, expected_repository)


# Repository-level extraction


def test_repo_with_origin_remote_reports_hash_and_identity(tmp_path: Path) -> None:
    commit_hash = _init_repo_with_commit(tmp_path)
    _git(tmp_path, "remote", "add", "origin", _REMOTE_URL)

    git_info = read_git_info(tmp_path)

    assert git_info.commit_hash == commit_hash
    assert git_info.remote_host == "github.com"
    assert git_info.repository == "example/sample-project"


def test_repo_without_remote_reports_hash_only(tmp_path: Path) -> None:
    commit_hash = _init_repo_with_commit(tmp_path)

    git_info = read_git_info(tmp_path)

    assert git_info.commit_hash == commit_hash
    assert git_info.remote_host is None
    assert git_info.repository is None


def test_repo_with_local_path_remote_reports_hash_only(tmp_path: Path) -> None:
    commit_hash = _init_repo_with_commit(tmp_path)
    _git(tmp_path, "remote", "add", "origin", "/mirrors/sample-project.git")

    git_info = read_git_info(tmp_path)

    assert git_info.commit_hash == commit_hash
    assert git_info.remote_host is None
    assert git_info.repository is None


def test_repo_without_commits_reports_nothing(tmp_path: Path) -> None:
    _git(tmp_path, "init")

    git_info = read_git_info(tmp_path)

    assert git_info == (None, None, None)


def test_non_repo_directory_reports_nothing(tmp_path: Path) -> None:
    assert read_git_info(tmp_path) == (None, None, None)


def test_missing_directory_reports_nothing(tmp_path: Path) -> None:
    assert read_git_info(tmp_path / "does-not-exist") == (None, None, None)


def test_project_inside_repo_resolves_enclosing_repo(tmp_path: Path) -> None:
    commit_hash = _init_repo_with_commit(tmp_path)
    _git(tmp_path, "remote", "add", "origin", _REMOTE_URL)
    nested_project = tmp_path / "modules" / "service"
    nested_project.mkdir(parents=True)

    git_info = read_git_info(nested_project)

    assert git_info.commit_hash == commit_hash
    assert git_info.remote_host == "github.com"
    assert git_info.repository == "example/sample-project"


# Project metadata wiring


def test_project_metadata_includes_git_info(tmp_path: Path) -> None:
    commit_hash = _init_repo_with_commit(tmp_path)
    _git(tmp_path, "remote", "add", "origin", _REMOTE_URL)

    project_analysis = ProjectAnalysisInfo(
        analysis=FakeJavaAnalysis(),
        dataset_name="sample-project",
        project_path=str(tmp_path),
    ).gather_project_analysis_info()

    assert project_analysis.metadata.git_commit_hash == commit_hash
    assert project_analysis.metadata.git_remote_host == "github.com"
    assert project_analysis.metadata.git_repository == "example/sample-project"


def test_project_metadata_omits_git_info_outside_repo(tmp_path: Path) -> None:
    project_analysis = ProjectAnalysisInfo(
        analysis=FakeJavaAnalysis(),
        dataset_name="sample-project",
        project_path=str(tmp_path),
    ).gather_project_analysis_info()

    assert project_analysis.metadata.git_commit_hash is None
    assert project_analysis.metadata.git_remote_host is None
    assert project_analysis.metadata.git_repository is None
