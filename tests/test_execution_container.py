from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from gerbil.execution import container
from gerbil.execution.container import require_case_sensitive_mount
from gerbil.execution.errors import ExecutionError

_REAL_RUN = subprocess.run


def _mount_seen_as(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, visible: bool, folds: bool
) -> Path:
    """Run the probe script for real, with each probe path backed by its own file."""
    views = tmp_path / "views"
    views.mkdir()
    exact = views / "exact"
    lowered = views / "lowered"
    if visible:
        exact.touch()
    if folds:
        lowered.touch()

    def run(command: Sequence[str], **kwargs: Any) -> Any:
        host, mount = command[command.index("--volume") + 1].split(":")
        (probe,) = [
            path.name for path in Path(host).iterdir() if path.name.startswith(".")
        ]
        script = (
            command[-1]
            .replace(f"{mount}/{probe.lower()}", str(lowered))
            .replace(f"{mount}/{probe}", str(exact))
        )
        return _REAL_RUN(["bash", "-c", script], **kwargs)

    monkeypatch.setattr(container.subprocess, "run", run)
    directory = tmp_path / "execution"
    directory.mkdir()
    return directory


def test_case_sensitive_mount_is_accepted_and_the_probe_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = _mount_seen_as(monkeypatch, tmp_path, visible=True, folds=False)

    require_case_sensitive_mount(directory)

    assert list(directory.iterdir()) == []


def test_mount_that_folds_case_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = _mount_seen_as(monkeypatch, tmp_path, visible=True, folds=True)

    with pytest.raises(ExecutionError, match="case-insensitive"):
        require_case_sensitive_mount(directory)

    assert list(directory.iterdir()) == []


def test_mount_docker_shows_empty_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = _mount_seen_as(monkeypatch, tmp_path, visible=False, folds=False)

    with pytest.raises(ExecutionError, match="empty inside containers"):
        require_case_sensitive_mount(directory)


def test_probe_reports_docker_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def run(command: Sequence[str], **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(command, 125, "", "no such image")

    monkeypatch.setattr(container.subprocess, "run", run)

    with pytest.raises(ExecutionError, match="no such image"):
        require_case_sensitive_mount(tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_unwritable_output_path_is_reported(tmp_path: Path) -> None:
    occupied = tmp_path / "file"
    occupied.touch()

    with pytest.raises(ExecutionError, match="cannot write"):
        require_case_sensitive_mount(occupied)


def test_run_labels_the_container_with_its_output_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands = []

    def run(command: Sequence[str], **kwargs: Any) -> Any:
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(container.subprocess, "run", run)

    container.Container(tmp_path, tmp_path / "out", 17).run(
        ["true"], log=tmp_path / "log"
    )

    ((*_, label_flag, label), *_) = [
        command[: command.index("--label") + 2] for command in commands
    ]
    assert label_flag == "--label"
    assert label == f"gerbil.output={(tmp_path / 'out').resolve()}"


def _recorded_runs(
    monkeypatch: pytest.MonkeyPatch, timeout_on_run: bool = False
) -> list[list[str]]:
    commands: list[list[str]] = []

    def run(command: Sequence[str], **kwargs: Any) -> Any:
        commands.append(list(command))
        if timeout_on_run and command[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(container.subprocess, "run", run)
    return commands


def test_run_can_cut_the_container_off_on_another_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands = _recorded_runs(monkeypatch)

    container.Container(
        tmp_path,
        tmp_path / "out",
        17,
        image="other",
        network="none",
        hostname="localhost",
    ).run(["true"], log=tmp_path / "log")

    ((*options, image, _bash, _flag, _script),) = commands
    assert options[options.index("--network") + 1] == "none"
    assert options[options.index("--hostname") + 1] == "localhost"
    assert image == "other"


@pytest.mark.parametrize("cpus", [None, 6])
def test_run_caps_the_containers_cpus_only_when_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cpus: int | None
) -> None:
    commands = _recorded_runs(monkeypatch)

    container.Container(tmp_path, tmp_path / "out", 17, cpus=cpus).offline().run(
        ["true"], log=tmp_path / "log"
    )

    ((*options, _image, _bash, _flag, _script),) = commands
    if cpus is None:
        assert "--cpus" not in options
    else:
        assert options[options.index("--cpus") + 1] == "6"


def test_an_offline_container_is_the_same_container_without_a_network(
    tmp_path: Path,
) -> None:
    networked = container.Container(tmp_path, tmp_path / "out", 17, image="other")

    offline = networked.offline()

    assert offline == container.Container(
        tmp_path,
        tmp_path / "out",
        17,
        image="other",
        network="none",
        hostname="localhost",
    )


def test_run_keeps_dockers_default_network_unless_told_otherwise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands = _recorded_runs(monkeypatch)

    container.Container(tmp_path, tmp_path / "out", 17).run(
        ["true"], log=tmp_path / "log"
    )

    ((*options, image, _bash, _flag, _script),) = commands
    assert "--network" not in options
    assert "--hostname" not in options
    assert image == container.IMAGE


def test_a_timed_out_run_removes_its_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands = _recorded_runs(monkeypatch, timeout_on_run=True)

    with pytest.raises(ExecutionError, match="exceeded 5s"):
        container.Container(tmp_path, tmp_path / "out", 17).run(
            ["sleep", "9"], log=tmp_path / "log", timeout_seconds=5
        )

    started, removed = commands
    assert removed == ["docker", "rm", "--force", started[started.index("--name") + 1]]


def test_an_interrupted_run_removes_its_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def run(command: Sequence[str], **kwargs: Any) -> Any:
        commands.append(list(command))
        if command[:2] == ["docker", "run"]:
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(container.subprocess, "run", run)

    with pytest.raises(KeyboardInterrupt):
        container.Container(tmp_path, tmp_path / "out", 17).run(
            ["sleep", "9"], log=tmp_path / "log"
        )

    started, removed = commands
    assert removed == ["docker", "rm", "--force", started[started.index("--name") + 1]]


def _run_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cgroup: Path, command: list[str]
) -> int:
    """Run a container's script for real, with `cgroup` standing for its cgroup directory
    and `tmp_path` for the /out mount; returns the script's exit code."""

    def run(docker: Sequence[str], **kwargs: Any) -> Any:
        script = (
            docker[-1]
            .replace("/sys/fs/cgroup", str(cgroup))
            .replace("/out", str(tmp_path))
            .replace('export PATH="$JAVA_HOME/bin:$PATH" && ', "")
        )
        return _REAL_RUN(["bash", "-c", script], **kwargs)

    monkeypatch.setattr(container.subprocess, "run", run)
    try:
        container.Container(tmp_path / "workspace", tmp_path, 17).run(
            command,
            log=tmp_path / "log",
            memory_report=container.OUTPUT / "pit",
        )
    except ExecutionError as error:
        return int(str(error).split("exited with ")[1].split(";")[0])
    return 0


@pytest.mark.parametrize("code", [0, 3])
def test_run_leaves_the_containers_memory_accounting_however_the_command_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int
) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "memory.events").write_text(
        "low 0\noom 2\noom_kill 1\noom_group_kill 0\n"
    )
    (cgroup / "memory.peak").write_text("123456\n")
    report = tmp_path / "pit"
    report.mkdir()

    assert (
        _run_script(monkeypatch, tmp_path, cgroup, ["bash", "-c", f"exit {code}"])
        == code
    )

    assert container.memory_kills(report) == 1
    assert container.peak_memory(report) == 123456
    container.clear_memory_report(report)
    assert container.memory_kills(report) is None
    assert container.peak_memory(report) is None


def test_a_kernel_without_peak_memory_still_reports_its_kills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "memory.events").write_text("oom 0\noom_kill 0\n")
    report = tmp_path / "pit"
    report.mkdir()

    assert _run_script(monkeypatch, tmp_path, cgroup, ["true"]) == 0

    assert container.memory_kills(report) == 0
    assert container.peak_memory(report) is None


def test_a_container_without_memory_events_reports_no_kill_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    report = tmp_path / "pit"
    report.mkdir()

    assert _run_script(monkeypatch, tmp_path, cgroup, ["true"]) == 0

    assert container.memory_kills(report) is None


def test_a_container_copies_its_private_directories_into_memory_before_the_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runs = _recorded_runs(monkeypatch)
    private = container.Container(
        tmp_path,
        tmp_path,
        17,
        private_directories=(container.WORKSPACE / "web/target/test state",),
    )

    private.run(["java", "-version"], log=tmp_path / "log")

    (command,) = runs
    workspace = tmp_path.resolve()
    assert f"{workspace}:/origin:ro" in command
    tmpfs = command[command.index("--tmpfs") + 1]
    assert tmpfs == "/workspace/web/target/test state:exec"
    assert command[-1] == (
        "cp -a '/origin/web/target/test state'/. '/workspace/web/target/test state'/ && "
        'export PATH="$JAVA_HOME/bin:$PATH" && java -version'
    )


def test_a_container_without_private_directories_mounts_the_workspace_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runs = _recorded_runs(monkeypatch)

    container.Container(tmp_path, tmp_path, 17).run(["true"], log=tmp_path / "log")

    assert "--tmpfs" not in runs[0] and not any("/origin" in part for part in runs[0])


def test_stopping_kills_the_containers_writing_to_an_output_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def run(command: Sequence[str], **_: Any) -> Any:
        commands.append(list(command))
        listed = "abc\ndef\n" if command[1] == "ps" else ""
        return subprocess.CompletedProcess(command, 0, listed, "")

    monkeypatch.setattr(container.subprocess, "run", run)

    container.stop_containers(tmp_path)

    assert commands == [
        [
            "docker",
            "ps",
            "--quiet",
            "--filter",
            f"label=gerbil.output={tmp_path.resolve()}",
        ],
        ["docker", "kill", "abc"],
        ["docker", "kill", "def"],
    ]


def test_a_run_without_a_memory_report_runs_the_command_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runs = _recorded_runs(monkeypatch)

    container.Container(tmp_path, tmp_path, 17).run(
        ["java", "-version"], log=tmp_path / "log"
    )

    assert runs[0][-1] == 'export PATH="$JAVA_HOME/bin:$PATH" && java -version'


@pytest.mark.parametrize(("code", "accepted"), [(0, True), (1, False)])
def test_memory_accounting_is_required_of_the_docker_host(
    monkeypatch: pytest.MonkeyPatch, code: int, accepted: bool
) -> None:
    commands: list[Sequence[str]] = []

    def run(command: Sequence[str], **_: Any) -> Any:
        commands.append(command)
        return subprocess.CompletedProcess(command, code, "", "")

    monkeypatch.setattr(container.subprocess, "run", run)

    if accepted:
        container.require_memory_accounting()
    else:
        with pytest.raises(ExecutionError, match="cgroup v2"):
            container.require_memory_accounting()
    assert commands[0][-4:] == [
        "grep",
        "-q",
        "^oom_kill ",
        "/sys/fs/cgroup/memory.events",
    ]


def _seeding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, seen: Path
) -> tuple[Path, list[list[str]]]:
    """Run the seeding script for real, `seen` standing for what the container sees."""
    cache = tmp_path / "m2"
    commands: list[list[str]] = []

    def run(command: Sequence[str], **kwargs: Any) -> Any:
        commands.append(list(command))
        script = (
            command[-1]
            .replace("/root/.m2", str(cache))
            .replace(str(container._SEED_MOUNT), str(seen))
        )
        return _REAL_RUN(["bash", "-o", "pipefail", "-c", script], **kwargs)

    monkeypatch.setattr(container.subprocess, "run", run)
    return cache, commands


def test_seeding_copies_the_seed_into_mavens_cache_volume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed = tmp_path / "seed"
    (seed / "org/kie/kie-api/1").mkdir(parents=True)
    (seed / "org/kie/kie-api/1/kie-api-1.jar").write_text("jar")
    (seed / ".git").mkdir()
    (seed / ".git/HEAD").write_text("ref: refs/heads/main")
    cache, commands = _seeding(monkeypatch, tmp_path, seen=seed)

    container.seed_maven_repository(seed)

    assert (cache / "repository/org/kie/kie-api/1/kie-api-1.jar").read_text() == "jar"
    assert not (cache / "repository/.git").exists()
    (command,) = commands
    assert f"{seed.resolve()}:/seed:ro" in command
    assert "gerbil-m2:/root/.m2" in command


def test_seeding_rewrites_no_file_the_cache_has_alike(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed = tmp_path / "seed"
    version = seed / "org/kie/kie api/1"
    version.mkdir(parents=True)
    (version / "kie-api-1.jar").write_text("jar")
    (version / "kie-api-1.pom").write_text("pom")
    (version / "_remote.repositories").write_text("the seed's record")
    cache, _ = _seeding(monkeypatch, tmp_path, seen=seed)
    cached = cache / "repository/org/kie/kie api/1"
    cached.mkdir(parents=True)
    (cached / "kie-api-1.jar").write_text("jar")
    (cached / "_remote.repositories").write_text("a stale record")
    # A copy an interrupted seeding left behind.
    (cached / "kie-api-1.pom.seeding").write_text("po")
    in_use = (cached / "kie-api-1.jar").stat().st_ino

    container.seed_maven_repository(seed)

    # Rewriting it could hand a half-written jar to a JVM starting meanwhile.
    assert (cached / "kie-api-1.jar").stat().st_ino == in_use
    assert (cached / "_remote.repositories").read_text() == "the seed's record"
    assert {path.name: path.read_text() for path in cached.iterdir()} == {
        "kie-api-1.jar": "jar",
        "kie-api-1.pom": "pom",
        "_remote.repositories": "the seed's record",
    }


def test_seeding_fails_when_a_file_cannot_be_copied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed = tmp_path / "seed"
    (seed / "org").mkdir(parents=True)
    (seed / "org/a.jar").write_text("jar")
    cache, _ = _seeding(monkeypatch, tmp_path, seen=seed)
    # A file where the artifact's directory must go.
    (cache / "repository").mkdir(parents=True)
    (cache / "repository/org").write_text("")

    with pytest.raises(ExecutionError, match="seeding Maven's cache failed"):
        container.seed_maven_repository(seed)


def test_seeding_refuses_a_seed_containers_cannot_see(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed = tmp_path / "seed"
    (seed / "org").mkdir(parents=True)
    empty = tmp_path / "empty"
    empty.mkdir()
    _seeding(monkeypatch, tmp_path, seen=empty)

    with pytest.raises(ExecutionError, match="empty inside containers"):
        container.seed_maven_repository(seed)


def test_the_seed_keeps_snapshots_and_non_central_artifacts_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "m2"
    repository = cache / "repository"
    remote = "#NOTE: resolver file\n#Fri Oct 30 23:44:26 UTC 2026\n"
    for relative, text in {
        "org/kie/kie-api/999-SNAPSHOT/kie-api-999-SNAPSHOT.jar": "jar",
        "org/kie/kie-api/999-SNAPSHOT/_remote.repositories": remote
        + "kie-api-999-SNAPSHOT.jar>apache.snapshots=\n",
        "org/kie/kie-api/999-SNAPSHOT/resolver-status.properties": "status",
        "org/kie/kie-api/999-SNAPSHOT/kie-api-999-SNAPSHOT.pom.lastUpdated": "failed",
        "org/drools/core/999-20250622-SNAPSHOT/core-999-20250622-SNAPSHOT.pom": "pom",
        "org/apache/curator/curator/2.12.0-kylin-r5/curator-2.12.0-kylin-r5.jar": "fork",
        "org/apache/curator/curator/2.12.0-kylin-r5/_remote.repositories": remote
        + "curator-2.12.0-kylin-r5.jar>nexus=\n",
        "org/kie/kie-api/1.0/kie-api-1.0.jar": "release",
        "org/kie/kie-api/1.0/_remote.repositories": remote
        + "kie-api-1.0.jar>central=\nkie-api-1.0.pom>maven-central=\n",
        "org/kie/kie-bom/1.0/kie-bom-1.0.pom": "installed locally",
    }.items():
        (repository / relative).parent.mkdir(parents=True, exist_ok=True)
        (repository / relative).write_text(text)
    commands: list[list[str]] = []

    def run(command: Sequence[str], **kwargs: Any) -> Any:
        commands.append(list(command))
        partial = command[command.index("--volume", 5) + 1].split(":")[0]
        script = (
            command[-1]
            .replace("/root/.m2", str(cache))
            .replace(str(container._SEED_MOUNT), partial)
        )
        return _REAL_RUN(["bash", "-o", "pipefail", "-c", script], **kwargs)

    monkeypatch.setattr(container.subprocess, "run", run)
    destination = tmp_path / "seed"

    container.export_maven_seed(destination)

    exported = sorted(
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    )
    assert exported == [
        "org/apache/curator/curator/2.12.0-kylin-r5/_remote.repositories",
        "org/apache/curator/curator/2.12.0-kylin-r5/curator-2.12.0-kylin-r5.jar",
        "org/drools/core/999-20250622-SNAPSHOT/core-999-20250622-SNAPSHOT.pom",
        "org/kie/kie-api/999-SNAPSHOT/_remote.repositories",
        "org/kie/kie-api/999-SNAPSHOT/kie-api-999-SNAPSHOT.jar",
    ]
    assert "gerbil-m2:/root/.m2:ro" in commands[0]
    assert not destination.with_name("seed.partial").exists()


def test_exporting_the_seed_refuses_an_existing_directory(tmp_path: Path) -> None:
    (tmp_path / "seed").mkdir()

    with pytest.raises(ExecutionError, match="export to a new directory"):
        container.export_maven_seed(tmp_path / "seed")
