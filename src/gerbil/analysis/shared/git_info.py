"""Git metadata extraction (commit hash, remote identity) for analyzed project roots."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from gerbil.analysis.shared.url_utils import safe_urlparse

_GIT_TIMEOUT_SECONDS = 10

# scp-like remote syntax: [user@]host:path (e.g. git@github.com:owner/repo.git)
_SCP_REMOTE_PATTERN = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.*)$")


class GitInfo(NamedTuple):
    commit_hash: str | None
    remote_host: str | None
    repository: str | None


def _normalize_repository(path: str) -> str | None:
    repository = path.strip("/").removesuffix(".git")
    return repository or None


def parse_remote_url(remote_url: str) -> tuple[str | None, str | None]:
    """Extract (host, repository) from a remote URL, dropping credentials and ports."""
    if "://" in remote_url:
        parsed = safe_urlparse(remote_url)
        # parsed.hostname excludes userinfo (tokens) and port by construction.
        if parsed is None or parsed.scheme == "file" or not parsed.hostname:
            return None, None
        return parsed.hostname, _normalize_repository(parsed.path)

    scp_match = _SCP_REMOTE_PATTERN.match(remote_url)
    if scp_match:
        host = scp_match.group("host").lower()
        return host, _normalize_repository(scp_match.group("path"))

    # Local-path remotes have no canonical host/repository identity.
    return None, None


def _run_git(project_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def read_git_info(project_root: Path) -> GitInfo:
    # Only the "origin" remote is consulted: analyzed projects are clones, so
    # origin is the canonical repo identity; other remote names are not searched.
    remote_url = _run_git(project_root, "remote", "get-url", "origin")
    remote_host, repository = (
        parse_remote_url(remote_url) if remote_url else (None, None)
    )
    return GitInfo(
        commit_hash=_run_git(project_root, "rev-parse", "HEAD"),
        remote_host=remote_host,
        repository=repository,
    )
