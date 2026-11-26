"""Materialize a suite's source tree from the pinned commit and write its results."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from gerbil.execution.errors import ExecutionError


@dataclass(frozen=True)
class Clearing:
    """The agents' test clearing: the files it deletes from the pinned commit, and the
    git tree that leaves."""

    paths: tuple[str, ...]
    tree: str


def materialize_workspace(
    source_repo: Path,
    commit: str,
    destination: Path,
    *,
    clearing: Clearing | None = None,
    patches: Sequence[Path] = (),
) -> None:
    """Clone `source_repo` at `commit`, clear its tests and apply `patches`; an existing
    tree is reused."""
    if destination.exists():
        return
    staging = destination.with_name(f"{destination.name}.partial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.parent.mkdir(parents=True, exist_ok=True)
    _git("clone", "--quiet", "--no-checkout", str(source_repo), str(staging))
    _git("-C", str(staging), "checkout", "--quiet", commit)
    if clearing is not None:
        clear_tests(staging, clearing)
    apply_patches(staging, patches)
    staging.rename(destination)


def clear_tests(workspace: Path, clearing: Clearing) -> None:
    """Delete the cleared files, refusing a result other than the recorded tree.

    The deletions are left unstaged, as applying the clearing patch leaves them.
    """
    if clearing.paths:
        _git(
            "--literal-pathspecs",
            "-C",
            str(workspace),
            "rm",
            "--quiet",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
            stdin="\0".join(clearing.paths),
        )
    tree = _git("-C", str(workspace), "write-tree").strip()
    if tree != clearing.tree:
        raise ExecutionError(
            f"clearing {workspace} left tree {tree}, not the recorded {clearing.tree}"
        )
    _git("-C", str(workspace), "reset", "--quiet")


def apply_patches(workspace: Path, patches: Sequence[Path]) -> None:
    for patch in patches:
        _git("-C", str(workspace), "apply", "--whitespace=nowarn", str(patch.resolve()))


def apply_workspace_copies(workspace: Path, copies: Sequence[tuple[str, str]]) -> None:
    for source, target in copies:
        shutil.copyfile(workspace / source, workspace / target)


def restore_workspace_paths(workspace: Path, paths: Sequence[str]) -> None:
    """Bring back the commit's files under `paths` that the workspace lacks.

    Files already present are kept, so a suite's own edits under a restored path survive.
    """
    if not paths:
        return
    listed = _git(
        "-C", str(workspace), "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *paths
    )
    missing = [
        name
        for name in listed.split("\0")
        if name and not os.path.lexists(workspace / name)
    ]
    if missing:
        _git(
            "--literal-pathspecs",
            "-C",
            str(workspace),
            "checkout",
            "--quiet",
            "HEAD",
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
            stdin="\0".join(missing),
        )


# `--pathspec-from-file`, which clearing and restoring use, arrived in git 2.26.
MINIMUM_GIT = (2, 26)


def git_version() -> tuple[int, int]:
    words = _git("version").split()
    major, minor = words[2].split(".")[:2]
    return int(major), int(minor)


def head_commit(workspace: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ExecutionError(f"no git history in {workspace}")
    return completed.stdout.strip()


def write_atomically(path: Path, text: str) -> None:
    """A result file's existence marks the stage finished, so it must never be partial."""
    staging = path.with_name(f"{path.name}.partial")
    with staging.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    staging.replace(path)


def _git(*args: str, stdin: str | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], capture_output=True, text=True, input=stdin
    )
    if completed.returncode != 0:
        raise ExecutionError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout
