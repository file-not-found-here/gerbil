"""Bundle the untracked inputs `gerbil execution` needs into one directory to send to the
machine that runs it, and read them there beside the checkout's committed suite patches.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from gerbil.evomaster.pipeline import RunPaths, gerbil_revision
from gerbil.evomaster.staging import EXPORTED_RUN, completed_run, generated_api_tests
from gerbil.evomaster.targets import target_for
from gerbil.execution.archive import replace_written_directory
from gerbil.execution.errors import ExecutionError
from gerbil.execution.projects import MEASURED_PROJECTS, ExecutionProject, Suite
from gerbil.execution.selection import ApiTest, SuiteAnalysis, load_api_tests
from gerbil.execution.sources import EvoMasterRuns, SuiteSources
from gerbil.execution.workspace import MINIMUM_GIT, Clearing, git_version

FORMAT = 1
MANIFEST = "manifest.json"
MAVEN_SEED = "maven-seed"
_CLEARING = "clearing.json"
_TRACKED_RUNS = {
    Suite.CLAUDE_CODE: "claude_code",
    Suite.CODEX: "codex",
    Suite.EVOMASTER: "evomaster",
}


def tracked_patch(outputs_root: Path, project: ExecutionProject, suite: Suite) -> Path:
    """The suite's committed patch from the agents' starting point."""
    return (
        outputs_root
        / _TRACKED_RUNS[suite]
        / "runs"
        / project.name
        / "test_patches"
        / "git_diff.patch"
    )


class _SelectedTest(BaseModel):
    class_name: str
    method_signature: str
    dispatch: list[str]


class _Selection(BaseModel):
    """A suite's API tests as Gerbil's analysis found them; scope is decided at execution."""

    commit: str
    api_tests: list[_SelectedTest]


class _ClearingFile(BaseModel):
    paths: list[str]


class SuiteEntry(BaseModel):
    api_tests: int
    selection_sha256: str
    patch_sha256: str | None


class ProjectEntry(BaseModel):
    commit: str
    cleared_tree: str
    clearing_sha256: str
    suites: dict[Suite, SuiteEntry]


class EvoMasterEntry(BaseModel):
    seed: int
    max_time: str


class Manifest(BaseModel):
    format: int
    gerbil_commit: str
    created: str
    evomaster: EvoMasterEntry
    maven_seed_sha256: str | None
    projects: dict[str, ProjectEntry]


@dataclass(frozen=True)
class SuiteTree:
    """A suite's tree: the pinned commit, cleared and patched for all but the developers'."""

    commit: str
    clearing: Clearing | None
    patches: tuple[Path, ...]
    patch_digests: tuple[str, ...]
    selection_sha256: str

    def digest(self, project: ExecutionProject) -> str:
        """Changes with anything the suite's results depend on but the code measuring it."""
        return _sha256_text(
            json.dumps(
                {
                    "commit": self.commit,
                    "cleared_tree": self.clearing.tree if self.clearing else None,
                    "patches": list(self.patch_digests),
                    "selection": self.selection_sha256,
                    "copies": [list(copy) for copy in project.workspace_copies],
                    "restores": list(project.workspace_restores),
                }
            )
        )


@dataclass(frozen=True)
class ExecutionInputs:
    """A bundle, the checkout's committed suite patches, and the source repositories."""

    bundle: Path
    outputs_root: Path
    repos_root: Path
    manifest: Manifest

    @classmethod
    def load(
        cls, bundle: Path, outputs_root: Path, repos_root: Path
    ) -> ExecutionInputs:
        path = bundle / MANIFEST
        if not path.is_file():
            raise ExecutionError(f"no bundle at {bundle}: {MANIFEST} is missing")
        manifest = Manifest.model_validate_json(path.read_text())
        if manifest.format != FORMAT:
            raise ExecutionError(
                f"{bundle} is a format {manifest.format} bundle; this checkout reads "
                f"format {FORMAT}"
            )
        return cls(bundle, outputs_root, repos_root, manifest)

    @property
    def identity(self) -> str:
        """Changes with what the bundle holds, not with when or by which Gerbil it was made."""
        return _sha256_text(
            self.manifest.model_dump_json(exclude={"created", "gerbil_commit"})
        )

    def source_repo(self, project: ExecutionProject) -> Path:
        return self.repos_root / project.name

    def entry(self, project: ExecutionProject) -> ProjectEntry:
        entry = self.manifest.projects.get(project.name)
        if entry is None:
            raise ExecutionError(f"the bundle at {self.bundle} has no {project.name}")
        return entry

    def selection(self, project: ExecutionProject, suite: Suite) -> SuiteAnalysis:
        path = self.bundle / project.name / f"{suite.value}.json"
        text = _verified(path, self.entry(project).suites[suite].selection_sha256)
        selection = _Selection.model_validate_json(text)
        return SuiteAnalysis(
            selection.commit,
            tuple(
                ApiTest(test.class_name, test.method_signature, tuple(test.dispatch))
                for test in selection.api_tests
            ),
        )

    def tree(self, project: ExecutionProject, suite: Suite) -> SuiteTree:
        entry = self.entry(project)
        selection = entry.suites[suite].selection_sha256
        if suite is Suite.DEVELOPER:
            return SuiteTree(entry.commit, None, (), (), selection)
        clearing = _ClearingFile.model_validate_json(
            _verified(self.bundle / project.name / _CLEARING, entry.clearing_sha256)
        )
        digest = entry.suites[suite].patch_sha256
        assert digest is not None
        patch = tracked_patch(self.outputs_root, project, suite)
        _verified(patch, digest)
        return SuiteTree(
            entry.commit,
            Clearing(tuple(clearing.paths), entry.cleared_tree),
            (patch,),
            (digest,),
            selection,
        )

    @property
    def maven_seed(self) -> Path | None:
        """Artifacts that no repository serves any more, laid out as a Maven repository."""
        if self.manifest.maven_seed_sha256 is None:
            return None
        seed = self.bundle / MAVEN_SEED
        if _directory_sha256(seed) != self.manifest.maven_seed_sha256:
            raise ExecutionError(f"{seed} differs from the bundle's manifest")
        return seed

    def problems(self, projects: Iterable[ExecutionProject]) -> list[str]:
        """Everything that would stop these projects' suites from being rebuilt."""
        found = []
        version = git_version()
        if version < MINIMUM_GIT:
            found.append(
                f"git {version[0]}.{version[1]} is older than "
                f"{MINIMUM_GIT[0]}.{MINIMUM_GIT[1]}, which clearing needs"
            )
        selected = list(projects)
        missing = [p.name for p in selected if p.name not in self.manifest.projects]
        if missing:
            found.append(f"the bundle has no {', '.join(missing)}")
        for project in selected:
            if project.name in missing:
                continue
            repo = self.source_repo(project)
            if not _has_commit(repo, self.entry(project).commit):
                found.append(
                    f"{repo} lacks commit {self.entry(project).commit}; run "
                    f"git submodule update --init {repo.relative_to(repo.parents[1])}"
                )
            for suite in Suite:
                try:
                    self.selection(project, suite)
                    self.tree(project, suite)
                except ExecutionError as error:
                    found.append(str(error))
        try:
            _ = self.maven_seed
        except ExecutionError as error:
            found.append(str(error))
        return found


def build_bundle(
    sources: SuiteSources,
    runs: EvoMasterRuns,
    maven_seed: Path | None,
    destination: Path,
    projects: Iterable[ExecutionProject] = MEASURED_PROJECTS.values(),
) -> Manifest:
    """Write the selections, test clearings and Maven seed `projects` need to `destination`.

    Refuses a suite of another commit than the developers' analysis, and committed patches
    or EvoMaster records that differ from the runs they were saved from.
    """
    if runs.max_time is None:
        raise ExecutionError("a bundle measures EvoMaster runs of one budget")
    if destination.exists() and not (destination / MANIFEST).is_file():
        raise ExecutionError(
            f"{destination} exists and is not a bundle; choose a new directory"
        )
    if maven_seed is not None and not maven_seed.is_dir():
        raise ExecutionError(f"no Maven seed at {maven_seed}")
    building = destination.with_name(f"{destination.name}.partial")
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir(parents=True)
    entries = {
        project.name: _bundle_project(sources, runs, project, building / project.name)
        for project in projects
    }
    seed_digest = None
    if maven_seed is not None:
        shutil.copytree(
            maven_seed, building / MAVEN_SEED, ignore=shutil.ignore_patterns(".git")
        )
        seed_digest = _directory_sha256(building / MAVEN_SEED)
    manifest = Manifest(
        format=FORMAT,
        gerbil_commit=gerbil_revision(),
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        evomaster=EvoMasterEntry(seed=runs.seed, max_time=runs.max_time),
        maven_seed_sha256=seed_digest,
        projects=entries,
    )
    (building / MANIFEST).write_text(manifest.model_dump_json(indent=2))
    replace_written_directory(building, destination, MANIFEST)
    return manifest


def _bundle_project(
    sources: SuiteSources,
    runs: EvoMasterRuns,
    project: ExecutionProject,
    directory: Path,
) -> ProjectEntry:
    target = target_for(project)
    analyses = {
        suite: (
            generated_api_tests(target, runs)
            if suite is Suite.EVOMASTER
            else _load_analysis(sources.analysis_path(project, suite))
        )
        for suite in Suite
    }
    commit = analyses[Suite.DEVELOPER].commit
    for suite, analysis in analyses.items():
        if analysis.commit != commit:
            raise ExecutionError(
                f"{project.name}'s {suite.value} suite is of commit {analysis.commit}, "
                f"not the developers' {commit}"
            )
    clearing_patch = sources.test_clearing_patch(project)
    other_clearing = sources.agent_patches(project, Suite.CLAUDE_CODE)[0]
    if clearing_patch.read_bytes() != other_clearing.read_bytes():
        raise ExecutionError(f"{project.name}'s agents start from different clearings")
    clearing = derive_clearing(sources.source_repo(project), commit, clearing_patch)
    directory.mkdir(parents=True)
    clearing_text = _ClearingFile(paths=list(clearing.paths)).model_dump_json()
    (directory / _CLEARING).write_text(clearing_text)

    suites = {}
    for suite, analysis in analyses.items():
        patch_digest = None
        if suite is not Suite.DEVELOPER:
            patch = tracked_patch(sources.outputs_root, project, suite)
            if not patch.is_file():
                raise ExecutionError(f"{patch} is missing")
            _require_saved(sources, runs, project, suite, patch)
            patch_digest = _sha256_bytes(patch.read_bytes())
        text = _Selection(
            commit=analysis.commit,
            api_tests=[
                _SelectedTest(
                    class_name=test.class_name,
                    method_signature=test.method_signature,
                    dispatch=list(test.dispatch),
                )
                for test in analysis.api_tests
            ],
        ).model_dump_json()
        (directory / f"{suite.value}.json").write_text(text)
        suites[suite] = SuiteEntry(
            api_tests=len(analysis.api_tests),
            selection_sha256=_sha256_text(text),
            patch_sha256=patch_digest,
        )
    return ProjectEntry(
        commit=commit,
        cleared_tree=clearing.tree,
        clearing_sha256=_sha256_text(clearing_text),
        suites=suites,
    )


def _load_analysis(path: Path) -> SuiteAnalysis:
    if not path.is_file():
        raise ExecutionError(f"{path} is missing")
    return load_api_tests(path)


def _require_saved(
    sources: SuiteSources,
    runs: EvoMasterRuns,
    project: ExecutionProject,
    suite: Suite,
    patch: Path,
) -> None:
    """The committed copy must be what was analyzed: the agent's run, or EvoMaster's run."""
    if suite is Suite.EVOMASTER:
        target = target_for(project)
        completed_run(target, runs)
        record = RunPaths.of(runs.output_dir, target, runs.seed).record
        saved = patch.parents[1] / EXPORTED_RUN
        if not saved.is_file() or saved.read_bytes() != record.read_bytes():
            raise ExecutionError(
                f"{saved} is not {record}; run gerbil evomaster export again"
            )
        return
    run_patch = sources.agent_patches(project, suite)[1]
    if patch.read_bytes() != run_patch.read_bytes():
        raise ExecutionError(f"{patch} differs from the run's {run_patch}")


def derive_clearing(repo: Path, commit: str, clearing_patch: Path) -> Clearing:
    """The files `clearing_patch` deletes from `commit`, refusing any other change."""
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}

        def git(*args: str) -> str:
            completed = subprocess.run(
                ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env
            )
            if completed.returncode != 0:
                raise ExecutionError(
                    f"git {args[0]} in {repo} failed: {completed.stderr.strip()}"
                )
            return completed.stdout

        git("read-tree", commit)
        git("apply", "--cached", "--whitespace=nowarn", str(clearing_patch.resolve()))
        tree = git("write-tree").strip()
        fields = git(
            "diff-index", "--cached", "--no-renames", "--name-status", "-z", commit
        ).split("\0")
    changes = list(zip(fields[0::2], fields[1::2], strict=False))
    others = [path for status, path in changes if status != "D"]
    if others:
        raise ExecutionError(
            f"{clearing_patch} does more than delete files, e.g. {others[0]}"
        )
    return Clearing(tuple(sorted(path for _, path in changes)), tree)


def _has_commit(repo: Path, commit: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{commit}^{{commit}}"],
        capture_output=True,
    )
    return completed.returncode == 0


def _verified(path: Path, digest: str) -> bytes:
    if not path.is_file():
        raise ExecutionError(f"{path} is missing")
    data = path.read_bytes()
    if _sha256_bytes(data) != digest:
        raise ExecutionError(f"{path} differs from the bundle's manifest")
    return data


def _directory_sha256(directory: Path) -> str:
    """Leaves out a `.git` at the top, so a seed may be a git checkout."""
    digest = hashlib.sha256()
    for path in sorted(
        p
        for p in directory.rglob("*")
        if p.is_file() and p.relative_to(directory).parts[0] != ".git"
    ):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode())


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
