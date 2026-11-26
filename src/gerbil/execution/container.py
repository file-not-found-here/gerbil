"""Run build and test commands inside the shared execution image."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from uuid import uuid4

from gerbil.execution.errors import ExecutionError

IMAGE = "gerbil-execution"
WORKSPACE = PurePosixPath("/workspace")
OUTPUT = PurePosixPath("/out")
TOOLS = PurePosixPath("/opt/gerbil")

_DOCKER_CONTEXT = Path(__file__).resolve().parents[3] / "docker" / "execution"
# Named volumes keep downloaded dependencies and Gradle distributions across runs.
_CACHE_VOLUMES = ("gerbil-m2:/root/.m2", "gerbil-gradle:/root/.gradle")
# The container's own cgroup (v2): the processes the kernel killed for want of memory,
# and the most memory the container held.
MEMORY_EVENTS = PurePosixPath("/sys/fs/cgroup/memory.events")
_MEMORY_PEAK = MEMORY_EVENTS.with_name("memory.peak")
_MEMORY_KILLS = re.compile(r"^oom_kill (\d+)$", re.MULTILINE)
# A second, read-only view of the workspace, which private directories are copied from.
_ORIGIN = PurePosixPath("/origin")
_PROBE_MOUNT = PurePosixPath("/probe")
_SEED_MOUNT = PurePosixPath("/seed")
_PROBE_NOT_VISIBLE = 3
_PROBE_FOLDS_CASE = 4


def build_image(image: str = IMAGE, context: Path = _DOCKER_CONTEXT) -> None:
    completed = subprocess.run(
        ["docker", "build", "--quiet", "--tag", image, str(context)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ExecutionError(f"docker build failed: {completed.stderr.strip()}")


def require_case_sensitive_mount(directory: Path) -> None:
    """Refuse a directory containers cannot see, or see without letter case.

    The JVM misloads a class beside a package of the same name when case folds, so
    this probes through a real mount: Docker silently mounts an unshared path empty.
    """
    name = f".GerbilProbe-{uuid4().hex}"
    probe = directory / name
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.touch()
    except OSError as error:
        raise ExecutionError(f"cannot write to {directory}: {error}") from error
    script = (
        f"[ -e {_PROBE_MOUNT / name} ] || exit {_PROBE_NOT_VISIBLE}; "
        f"[ ! -e {_PROBE_MOUNT / name.lower()} ] || exit {_PROBE_FOLDS_CASE}"
    )
    try:
        completed = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--volume",
                f"{directory.resolve()}:{_PROBE_MOUNT}",
                IMAGE,
                "bash",
                "-c",
                script,
            ],
            capture_output=True,
            text=True,
        )
    finally:
        probe.unlink()
    if completed.returncode == _PROBE_NOT_VISIBLE:
        raise ExecutionError(
            f"{directory} is empty inside containers; pass an --output-dir under a "
            "path Docker shares"
        )
    if completed.returncode == _PROBE_FOLDS_CASE:
        raise ExecutionError(
            f"{directory} is case-insensitive inside containers; pass an --output-dir "
            "on a case-sensitive volume"
        )
    if completed.returncode != 0:
        raise ExecutionError(f"docker run failed: {completed.stderr.strip()}")


def require_memory_accounting(image: str = IMAGE) -> None:
    """Refuse a Docker whose containers cannot tell that the kernel killed one of their
    processes for want of memory, which PIT would count as a killed mutant."""
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            image,
            "grep",
            "-q",
            "^oom_kill ",
            str(MEMORY_EVENTS),
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ExecutionError(
            f"containers have no {MEMORY_EVENTS} with a count of memory kills; "
            "mutation needs a Docker host on cgroup v2"
        )


def seed_maven_repository(seed: Path, image: str = IMAGE) -> None:
    """Copy `seed`, a Maven repository of artifacts no remote serves any more, into the
    shared Maven cache, refusing a seed containers cannot see.

    The seed may be a git checkout, whose `.git` stays behind.
    """
    # A file the cache has alike is left alone and any other is moved into place whole:
    # a JVM of another project's tests may be opening the cache's jars meanwhile.
    script = (
        f'[ -n "$(ls -A {_SEED_MOUNT})" ] || exit {_PROBE_NOT_VISIBLE}; '
        f"cd {_SEED_MOUNT} && find . -path ./.git -prune -o -type f -print0 | "
        "while IFS= read -r -d '' file; do "
        'target="/root/.m2/repository/$file"; '
        'cmp -s "$file" "$target" && continue; '
        'mkdir -p "$(dirname "$target")" && cp -p "$file" "$target.seeding" '
        '&& mv "$target.seeding" "$target" || exit 1; done'
    )
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{seed.resolve()}:{_SEED_MOUNT}:ro",
            "--volume",
            _CACHE_VOLUMES[0],
            image,
            "bash",
            "-o",
            "pipefail",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode == _PROBE_NOT_VISIBLE:
        raise ExecutionError(
            f"{seed} is empty inside containers; keep the bundle under a path Docker shares"
        )
    if completed.returncode != 0:
        raise ExecutionError(
            f"seeding Maven's cache failed: {completed.stderr.strip()}"
        )


# Repository ids Maven Central is declared under; every other source may disappear.
_CENTRAL_IDS = ("central", "maven-central", "mavenCentral", "mavenCentral-default")


def export_maven_seed(destination: Path, image: str = IMAGE) -> None:
    """Copy each artifact version in the shared Maven cache that is a snapshot or came from
    a repository other than Maven Central to `destination`, a new Maven repository,
    leaving out the cache's records of failed downloads.

    Snapshot repositories drop old builds and other repositories go offline, so the
    cache that downloaded them is their last copy.
    """
    if destination.exists():
        raise ExecutionError(f"{destination} exists; export to a new directory")
    partial = destination.with_name(f"{destination.name}.partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    central = "|".join(_CENTRAL_IDS)
    script = (
        "cd /root/.m2/repository && { "
        'find . -type d -name "*SNAPSHOT" -prune -print; '
        "find . -name _remote.repositories -print0 | "
        "while IFS= read -r -d '' record; do "
        f"if grep -E '>[^=]*=' \"$record\" | grep -qvE '>({central})='; "
        'then dirname "$record"; fi; done; } | sort -u | tr "\\n" "\\0" '
        '| tar --null --exclude="*.lastUpdated" --exclude=resolver-status.properties '
        f"-cf - -T - | tar -C {_SEED_MOUNT} -xf -"
    )
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{_CACHE_VOLUMES[0]}:ro",
            "--volume",
            f"{partial.resolve()}:{_SEED_MOUNT}",
            image,
            "bash",
            "-o",
            "pipefail",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ExecutionError(
            f"exporting the Maven seed failed: {completed.stderr.strip()}"
        )
    partial.rename(destination)


@dataclass(frozen=True)
class Container:
    """A suite's source tree mounted at /workspace and its results directory at /out.

    `network` names a Docker network, such as "none" to cut the container off,
    `hostname` replaces the container's generated name, and `cpus` caps the CPU time
    its processes share. Each run gets its own in-memory copy of the
    `private_directories` of the workspace, for state that containers running side by
    side on one workspace must not share; what a run writes there is discarded.
    """

    workspace: Path
    output: Path
    jdk: int
    image: str = IMAGE
    network: str | None = None
    hostname: str | None = None
    cpus: int | None = None
    private_directories: tuple[PurePosixPath, ...] = ()

    def offline(self) -> Container:
        """This container cut off from every network, as generated suites run."""
        # Without a network nothing resolves a generated host name, which services look up.
        return replace(self, network="none", hostname="localhost")

    def run(
        self,
        command: Sequence[str],
        *,
        log: Path,
        workdir: PurePosixPath = WORKSPACE,
        timeout_seconds: int | None = None,
        memory_report: PurePosixPath | None = None,
    ) -> None:
        """Run `command`; with `memory_report`, a mounted directory, leave the
        container's memory accounting there however the command ends."""
        log.parent.mkdir(parents=True, exist_ok=True)
        copies = "".join(
            f"cp -a {shlex.quote(str(_ORIGIN / directory.relative_to(WORKSPACE)))}/. "
            f"{shlex.quote(str(directory))}/ && "
            for directory in self.private_directories
        )
        script = f'{copies}export PATH="$JAVA_HOME/bin:$PATH" && {shlex.join(command)}'
        if memory_report is not None:
            script += (
                f"; code=$?; cp {MEMORY_EVENTS} {shlex.quote(str(memory_report))}/; "
                f"cp {_MEMORY_PEAK} {shlex.quote(str(memory_report))}/ 2> /dev/null; "
                "exit $code"
            )
        name = f"gerbil-{uuid4().hex}"
        docker_command = [
            "docker",
            "run",
            "--rm",
            "--name",
            name,
            *(["--network", self.network] if self.network is not None else []),
            *(["--hostname", self.hostname] if self.hostname is not None else []),
            *(["--cpus", str(self.cpus)] if self.cpus is not None else []),
            "--volume",
            f"{self.workspace.resolve()}:{WORKSPACE}",
            "--volume",
            f"{self.output.resolve()}:{OUTPUT}",
            *(
                ["--volume", f"{self.workspace.resolve()}:{_ORIGIN}:ro"]
                if self.private_directories
                else []
            ),
            *(
                argument
                for directory in self.private_directories
                # Docker's tmpfs forbids running files unless asked, unlike the workspace.
                for argument in ("--tmpfs", f"{directory}:exec")
            ),
            "--label",
            f"gerbil.output={self.output.resolve()}",
            *(
                argument
                for volume in _CACHE_VOLUMES
                for argument in ("--volume", volume)
            ),
            "--env",
            f"JAVA_HOME=/opt/java/jdk{self.jdk}",
            "--workdir",
            str(workdir),
            self.image,
            "bash",
            "-c",
            script,
        ]
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"$ {shlex.join(command)}\n")
            handle.flush()
            try:
                completed = subprocess.run(
                    docker_command,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired as error:
                _remove_container(name)
                raise ExecutionError(
                    f"{command[0]} exceeded {timeout_seconds}s; see {log}"
                ) from error
            except BaseException:
                _remove_container(name)
                raise
        if completed.returncode != 0:
            raise ExecutionError(
                f"{command[0]} exited with {completed.returncode}; see {log}"
            )

    def host_path(self, path: PurePosixPath) -> Path:
        for mount, host in ((WORKSPACE, self.workspace), (OUTPUT, self.output)):
            if path.is_relative_to(mount):
                return host / path.relative_to(mount)
        raise ValueError(f"{path} is not under a mounted directory")


def memory_kills(report: Path) -> int | None:
    """Processes the kernel killed for want of memory in the container that left its
    accounting in `report`, or None if it left none."""
    events = report / MEMORY_EVENTS.name
    if not events.exists():
        return None
    kills = _MEMORY_KILLS.search(events.read_text())
    return int(kills.group(1)) if kills else None


def peak_memory(report: Path) -> int | None:
    """The most bytes that container held, where the kernel records it."""
    peak = report / _MEMORY_PEAK.name
    return int(peak.read_text()) if peak.exists() else None


def clear_memory_report(report: Path) -> None:
    for name in (MEMORY_EVENTS.name, _MEMORY_PEAK.name):
        (report / name).unlink(missing_ok=True)


def stop_containers(output: Path) -> None:
    """Kill the containers writing to `output`, whose results are not wanted any more."""
    listed = subprocess.run(
        [
            "docker",
            "ps",
            "--quiet",
            "--filter",
            f"label=gerbil.output={output.resolve()}",
        ],
        capture_output=True,
        text=True,
    )
    for container in listed.stdout.split():
        subprocess.run(["docker", "kill", container], capture_output=True, text=True)


def _remove_container(name: str) -> None:
    """Stopping the client of `docker run` leaves its container running, still writing."""
    subprocess.run(["docker", "rm", "--force", name], capture_output=True, text=True)


def write_argfile(path: Path, arguments: Sequence[str]) -> None:
    """Write a `java @argfile`, quoting each argument for the Java launcher."""
    path.parent.mkdir(parents=True, exist_ok=True)
    quoted = (
        '"' + argument.replace("\\", "\\\\").replace('"', '\\"') + '"'
        for argument in arguments
    )
    path.write_text("\n".join(quoted) + "\n", encoding="utf-8")
