from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from gerbil.execution.projects import MEASURED_PROJECTS, Suite

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_execution.sh"
_TEXT = _SCRIPT.read_text()
_PROJECTS_BLOCK = re.search(r"^PROJECTS=\((.*?)\)", _TEXT, re.DOTALL | re.MULTILINE)
_SUITES_BLOCK = re.search(r"^SUITES=\((.*?)\)", _TEXT, re.MULTILINE)
assert _PROJECTS_BLOCK is not None and _SUITES_BLOCK is not None
_PROJECTS = _PROJECTS_BLOCK.group(1).split()
_SUITES = _SUITES_BLOCK.group(1).split()


def _stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class _Machine:
    """A copy of the script beside stubs that write results the way the CLI does."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.root = (tmp_path / "repo").resolve()
        (self.root / "scripts").mkdir(parents=True)
        shutil.copy(_SCRIPT, self.root / "scripts" / _SCRIPT.name)
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.calls = tmp_path / "calls.log"
        self.calls.touch()
        self.killed = tmp_path / "killed.log"
        self.killed.touch()
        self.caps = tmp_path / "caps.log"
        self.caps.touch()
        self.out = self.root / "outputs" / "execution"
        self.fail = tmp_path / "fail"
        self.fail.mkdir()
        self.broken_suite = tmp_path / "broken"
        self.broken_suite.mkdir()
        # "<project>.<stage>" files holding a call that stage waits to see made.
        self.wait = tmp_path / "wait"
        self.wait.mkdir()
        self.use_working_uv()
        _stub(
            self.bin,
            "timeout",
            f'echo "$1 $2 $3" >> "{self.caps}"\nshift 3\n"$@"',
        )
        self.containers = tmp_path / "containers"
        self.containers.mkdir()
        _stub(
            self.bin,
            "docker",
            f"""
case "$1" in
  info) [ "$2" = "--format" ] && cat "{tmp_path}/capacity"; exit 0 ;;
  ps) echo "$*" | grep -q "label=gerbil.output" && ls "{self.containers}" ;;
  inspect) cat "{self.containers}/$4" ;;
  kill) echo "$2" >> "{self.killed}"; rm "{self.containers}/$2" ;;
esac
exit 0
""",
        )
        self.use_docker_memory_gib(400)
        self.bundle = self.root / "outputs" / "execution-bundle"
        self.bundle.mkdir(parents=True)
        (self.bundle / "manifest.json").write_text('{"format": 1}')
        self.use_inputs("a1b2")

    def use_docker_memory_gib(self, gib: int, cpus: int = 48) -> None:
        (self.tmp / "capacity").write_text(f"{gib * 1024**3} {cpus}\n")

    def use_inputs(self, identity: str, problem: str | None = None) -> None:
        """What `gerbil execution check` reports of the bundle."""
        report = f"bundle {self.bundle} from Gerbil abc\ninputs {identity}\n"
        if problem is None:
            report += "inputs ready\n"
        (self.tmp / "check").write_text(report)
        (self.tmp / "check-error").write_text(problem or "")

    def add_containers(self) -> None:
        (self.containers / "mine").write_text(f"{self.out}/openvsx/developer\n")
        (self.containers / "other").write_text(f"{self.out}/uaa/developer\n")
        (self.containers / "elsewhere").write_text(
            f"/backup{self.out}/openvsx/developer\n"
        )

    def use_working_uv(self) -> None:
        _stub(
            self.bin,
            "uv",
            f"""
if [ "$3 $4" = "execution check" ]; then
  echo "$*" >> "{self.tmp}/check.log"
  cat "{self.tmp}/check"
  if [ -s "{self.tmp}/check-error" ]; then cat "{self.tmp}/check-error" >&2; exit 1; fi
  exit 0
fi
if [ "$3 $4" = "execution bundle" ] || [ "$3 $4" = "execution results" ]; then
  echo "$*" >> "{self.tmp}/packaging.log"
  exit 0
fi
echo "$*" >> "{self.calls}"
stage=$4
project=$6
root="{self.out}/$project"
suites=""
previous=""
for argument in "$@"; do
  [ "$previous" = "--suite" ] && suites="$suites $argument"
  previous=$argument
done
[ -z "$suites" ] && suites="{" ".join(_SUITES)}"
if [ -e "{self.fail}/$project.$stage" ]; then exit "$(cat "{self.fail}/$project.$stage")"; fi
if [ -e "{self.fail}/$project.$stage.once" ]; then rm "{self.fail}/$project.$stage.once"; exit 1; fi
if [ -e "{self.wait}/$project.$stage" ]; then
  tries=0
  until grep -q -- "$(cat "{self.wait}/$project.$stage")" "{self.calls}"; do
    tries=$((tries + 1))
    [ "$tries" -gt 400 ] && exit 7
    sleep 0.05
  done
fi
code=0
for suite in $suites; do
  mkdir -p "$root/$suite"
  if [ -e "{self.broken_suite}/$project.$stage.$suite" ]; then
    echo failed > "$root/$suite/$stage-error.txt"
    code=1
  else
    rm -f "$root/$suite/$stage-error.txt"
    echo fresh > "$root/$suite/$stage.json"
  fi
done
exit $code
""",
        )

    def use_blocking_uv(self) -> None:
        _stub(
            self.bin,
            "uv",
            f"""
if [ "$3 $4" = "execution check" ]; then
  echo "$*" >> "{self.tmp}/check.log"
  cat "{self.tmp}/check"
  if [ -s "{self.tmp}/check-error" ]; then cat "{self.tmp}/check-error" >&2; exit 1; fi
  exit 0
fi
if [ "$3 $4" = "execution bundle" ] || [ "$3 $4" = "execution results" ]; then
  echo "$*" >> "{self.tmp}/packaging.log"
  exit 0
fi
echo $$ > "{self.tmp}/job.pid"
sleep 60 &
echo $! > "{self.tmp}/child.pid"
wait
""",
        )

    def run(
        self, *args: str, env: Mapping[str, str | None] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the script; an `env` value of None leaves that setting to its default."""
        settings: dict[str, str | None] = {
            **{k: v for k, v in os.environ.items() if k != "OUT"},
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            # One project at a time and one attempt, unless a test asks otherwise.
            "JOBS": "1",
            "RETRIES": "0",
            **(env or {}),
        }
        return subprocess.run(
            [str(self.root / "scripts" / _SCRIPT.name), *args],
            capture_output=True,
            text=True,
            env={k: v for k, v in settings.items() if v is not None},
            timeout=60,
        )

    def drive(
        self, stage: str, *projects: str, env: Mapping[str, str | None] | None = None
    ) -> None:
        self.run("_run", str(self.out), stage, *projects, env=env)

    def stages(self) -> list[tuple[str, str]]:
        return [
            (line.split()[3], line.split()[5])
            for line in self.calls.read_text().splitlines()
        ]

    def result(self, project: str, suite: str, stage: str) -> Path:
        return self.out / project / suite / f"{stage}.json"

    def finish_coverage(self, *projects: str) -> None:
        for project in projects:
            for suite in _SUITES:
                path = self.result(project, suite, "coverage")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

    def wait_until_finished(self, out: Path | None = None) -> None:
        log = (out or self.out) / "run.log"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if log.exists() and "FINISHED" in log.read_text():
                return
            time.sleep(0.05)
        pytest.fail("run did not finish")

    def wait_for_job(self) -> tuple[int, int]:
        child = self.tmp / "child.pid"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if child.exists() and child.read_text().strip():
                return int((self.tmp / "job.pid").read_text()), int(child.read_text())
            time.sleep(0.05)
        pytest.fail("job did not start")

    def runner_pid(self) -> int:
        found = subprocess.run(
            ["pgrep", "-f", f"run_execution.sh _run {self.out}"],
            capture_output=True,
            text=True,
        ).stdout.split()
        assert found
        return int(found[0])


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until_dead(pid: int) -> bool:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def machine(tmp_path: Path) -> _Machine:
    return _Machine(tmp_path)


def test_script_lists_every_measured_project_and_suite() -> None:
    assert sorted(_PROJECTS) == sorted(MEASURED_PROJECTS)
    assert sorted(_SUITES) == sorted(suite.value for suite in Suite)


def _mutation_follows_coverage(stages: list[tuple[str, str]]) -> bool:
    return all(
        stages.index(("coverage", project)) < stages.index(("mutation", project))
        for stage, project in stages
        if stage == "mutation"
    )


def test_run_covers_every_project_in_turn_and_mutates_each_once_covered(
    machine: _Machine,
) -> None:
    machine.drive("all", "openvsx", "uaa")

    stages = machine.stages()
    assert sorted(stages) == [
        ("coverage", "openvsx"),
        ("coverage", "uaa"),
        ("mutation", "openvsx"),
        ("mutation", "uaa"),
    ]
    assert [p for stage, p in stages if stage == "coverage"] == ["openvsx", "uaa"]
    assert _mutation_follows_coverage(stages)
    lines = machine.calls.read_text().splitlines()
    (mutation,) = [line for line in lines if "mutation --project uaa" in line]
    assert "--threads 6 --workers 6 --timeout 180 --sample-size 2401" in mutation
    assert "--threads" not in lines[0]
    assert f"--output-dir {machine.out}" in lines[0]
    assert sorted(machine.caps.read_text().splitlines()) == [
        "-k 60 10d",
        "-k 60 10d",
        "-k 60 8h",
        "-k 60 8h",
    ]


def test_a_projects_mutation_runs_while_the_next_project_is_covered(
    machine: _Machine,
) -> None:
    # uaa's coverage ends only once openvsx's mutation has started.
    (machine.wait / "uaa.coverage").write_text("mutation --project openvsx")

    machine.drive("all", "openvsx", "uaa")

    assert "FINISHED incomplete: none" in (machine.out / "run.log").read_text()
    assert _mutation_follows_coverage(machine.stages())


def test_a_failed_stage_is_tried_again_as_often_as_asked(machine: _Machine) -> None:
    (machine.fail / "uaa.mutation.once").touch()
    (machine.fail / "openvsx.coverage.once").touch()

    machine.drive("all", "openvsx", "uaa", env={"RETRIES": "1"})

    stages = machine.stages()
    assert stages.count(("coverage", "openvsx")) == 2
    assert stages.count(("mutation", "uaa")) == 2
    assert (
        stages.count(("coverage", "uaa")) == stages.count(("mutation", "openvsx")) == 1
    )
    log = (machine.out / "run.log").read_text()
    assert "RETRY 1 of 1: " in log and "FINISHED incomplete: none" in log
    assert re.search(r"uaa\s+done\s+done", machine.run("status").stdout)


def test_a_pass_that_covers_no_project_is_still_retried_and_finished(
    machine: _Machine,
) -> None:
    # No project reaches mutation in the first pass, so its xargs has no input.
    (machine.fail / "uaa.coverage.once").touch()

    machine.drive("all", "uaa", env={"RETRIES": "1"})

    log = (machine.out / "run.log").read_text()
    assert "ABORTED" not in log
    assert "RETRY 1 of 1: uaa" in log and "FINISHED incomplete: none" in log
    assert machine.stages() == [
        ("coverage", "uaa"),
        ("coverage", "uaa"),
        ("mutation", "uaa"),
    ]


def test_mutating_no_project_does_nothing(machine: _Machine) -> None:
    # What GNU xargs runs for an empty input when it is not told otherwise.
    assert machine.run("_one", str(machine.out)).returncode == 0
    assert machine.stages() == []
    assert "xargs -r " in _TEXT


@pytest.mark.parametrize("stage", ["coverage", "mutation"])
def test_one_stage_alone_is_retried_too(machine: _Machine, stage: str) -> None:
    machine.finish_coverage("openvsx")
    (machine.fail / f"uaa.{stage}.once").touch()
    if stage == "mutation":
        machine.finish_coverage("uaa")

    machine.drive(stage, "openvsx", "uaa", env={"RETRIES": "1"})

    assert machine.stages().count((stage, "uaa")) == 2
    assert "FINISHED incomplete: none" in (machine.out / "run.log").read_text()


def test_every_project_is_mutated_at_once_unless_jobs_holds_them_back(
    machine: _Machine,
) -> None:
    machine.finish_coverage("openvsx", "uaa", "kylin")
    # Each project's mutation ends only once the other two have started.
    for project, others in (
        ("openvsx", "kylin"),
        ("uaa", "openvsx"),
        ("kylin", "uaa"),
    ):
        (machine.wait / f"{project}.mutation").write_text(
            f"mutation --project {others}"
        )

    machine.drive("mutation", "openvsx", "uaa", "kylin", env={"JOBS": None})

    log = (machine.out / "run.log").read_text()
    assert "jobs=9" in log and "FINISHED incomplete: none" in log


def test_a_stage_that_keeps_failing_is_not_retried_forever(machine: _Machine) -> None:
    (machine.fail / "uaa.mutation").write_text("1")

    machine.drive("all", "uaa", env={"RETRIES": "2"})

    assert machine.stages().count(("mutation", "uaa")) == 3
    log = (machine.out / "run.log").read_text()
    assert "RETRY 2 of 2: uaa" in log and "RETRY 3" not in log
    assert "FINISHED incomplete: uaa" in log


def test_run_resumes_after_finished_stages(machine: _Machine) -> None:
    machine.drive("all", "openvsx")
    machine.calls.write_text("")

    machine.drive("all", "openvsx", "uaa")

    assert machine.stages() == [("coverage", "uaa"), ("mutation", "uaa")]


def test_run_recognises_results_from_a_manual_run(machine: _Machine) -> None:
    machine.finish_coverage("uaa")

    machine.drive("all", "uaa")

    assert machine.stages() == [("mutation", "uaa")]


def test_deleted_results_are_produced_again(machine: _Machine) -> None:
    machine.drive("all", "uaa")
    shutil.rmtree(machine.out / "uaa")
    machine.calls.write_text("")

    machine.drive("all", "uaa")

    assert machine.stages() == [("coverage", "uaa"), ("mutation", "uaa")]


def test_failed_coverage_holds_back_mutation_and_is_retried(machine: _Machine) -> None:
    (machine.broken_suite / "uaa.coverage.codex").touch()

    machine.drive("all", "uaa")

    assert machine.stages() == [("coverage", "uaa")]
    assert "FINISHED incomplete: uaa" in (machine.out / "run.log").read_text()
    status = machine.run("status").stdout
    assert re.search(r"uaa\s+failed: codex\s+waiting for coverage", status)

    (machine.broken_suite / "uaa.coverage.codex").unlink()
    machine.calls.write_text("")
    machine.drive("all", "uaa")

    assert machine.stages() == [("coverage", "uaa"), ("mutation", "uaa")]
    assert "FINISHED incomplete: none" in (machine.out / "run.log").read_text()
    assert re.search(r"uaa\s+done\s+done", machine.run("status").stdout)


def test_outage_failing_every_suite_is_not_treated_as_finished(
    machine: _Machine,
) -> None:
    for suite in _SUITES:
        (machine.broken_suite / f"uaa.coverage.{suite}").touch()

    machine.drive("all", "uaa")

    assert machine.stages() == [("coverage", "uaa")]
    assert re.search(
        r"uaa\s+failed: developer claude-code codex evomaster\s+waiting for coverage",
        machine.run("status").stdout,
    )


def test_finished_mutation_survives_repeated_runs(machine: _Machine) -> None:
    machine.drive("all", "uaa")
    machine.result("uaa", "developer", "mutation").write_text("expensive")
    machine.calls.write_text("")

    machine.drive("all", "uaa")

    assert machine.stages() == []
    assert machine.result("uaa", "developer", "mutation").read_text() == "expensive"


def test_aborted_xargs_is_not_reported_as_finished(machine: _Machine) -> None:
    machine.finish_coverage("uaa")
    _stub(machine.bin, "xargs", "exit 125")

    machine.drive("mutation", "uaa")

    log = (machine.out / "run.log").read_text()
    assert "ABORTED xargs exit=125" in log
    assert "FINISHED" not in log


def test_retry_mutates_only_the_suites_without_results(machine: _Machine) -> None:
    (machine.broken_suite / "uaa.mutation.codex").touch()
    machine.drive("all", "uaa")
    machine.result("uaa", "developer", "mutation").write_text("expensive")
    (machine.broken_suite / "uaa.mutation.codex").unlink()
    machine.calls.write_text("")

    machine.drive("all", "uaa")

    assert machine.stages() == [("mutation", "uaa")]
    line = machine.calls.read_text()
    assert "--suite codex" in line
    assert "--suite developer" not in line
    assert machine.result("uaa", "developer", "mutation").read_text() == "expensive"


def test_mutation_waits_for_coverage_to_settle(machine: _Machine) -> None:
    (machine.fail / "uaa.coverage").write_text("1")
    path = machine.result("uaa", "developer", "coverage")
    path.parent.mkdir(parents=True)
    path.touch()

    machine.drive("all", "uaa")

    assert machine.stages() == [("coverage", "uaa")]
    assert re.search(r"uaa\s+failed: claude-code codex", machine.run("status").stdout)


def test_failure_stops_only_that_project_and_is_retried(machine: _Machine) -> None:
    machine.finish_coverage("openvsx", "uaa")
    machine.add_containers()
    (machine.fail / "openvsx.mutation").write_text("1")

    machine.drive("mutation", "openvsx", "uaa")

    assert machine.stages() == [("mutation", "openvsx"), ("mutation", "uaa")]
    assert machine.killed.read_text() == "mine\n"
    status = machine.run("status").stdout
    assert re.search(r"openvsx\s+done\s+failed \(exit 1\), 0 of 4 suites", status)

    (machine.fail / "openvsx.mutation").unlink()
    machine.calls.write_text("")
    machine.drive("mutation", "openvsx", "uaa")

    assert machine.stages() == [("mutation", "openvsx")]
    assert re.search(r"openvsx\s+done\s+done", machine.run("status").stdout)


def test_exit_255_does_not_abandon_later_projects(machine: _Machine) -> None:
    machine.finish_coverage("openvsx", "uaa")
    (machine.fail / "openvsx.mutation").write_text("255")

    machine.drive("mutation", "openvsx", "uaa")

    assert machine.stages() == [("mutation", "openvsx"), ("mutation", "uaa")]
    assert "FINISHED" in (machine.out / "run.log").read_text()


def test_settings_come_from_the_environment(machine: _Machine) -> None:
    machine.drive(
        "all",
        "uaa",
        env={
            "THREADS": "8",
            "WORKERS": "20",
            "SAMPLE": "500",
            "PIT_TIMEOUT": "60",
            "COVERAGE_CAP": "1h",
            "MUTATION_CAP": "2d",
            "PAUSE_BELOW_GB": "25",
        },
    )

    coverage, mutation = machine.calls.read_text().splitlines()
    assert "--pause-below-gb 25 --bundle" in coverage
    assert (
        "--threads 8 --workers 20 --timeout 60 --sample-size 500 --pause-below-gb 25"
        in mutation
    )
    assert machine.caps.read_text().splitlines() == ["-k 60 1h", "-k 60 2d"]


def test_coverage_reads_the_bundle_of_the_environment(machine: _Machine) -> None:
    machine.drive("coverage", "uaa", env={"BUNDLE": "/elsewhere/bundle"})

    (uaa,) = machine.calls.read_text().splitlines()
    assert uaa.endswith("--bundle /elsewhere/bundle")


def test_jobs_run_mutation_projects_concurrently(
    machine: _Machine, tmp_path: Path
) -> None:
    machine.finish_coverage("openvsx", "uaa")
    # Each project waits for the other to start, which only concurrency can satisfy.
    _stub(
        machine.bin,
        "uv",
        f"""
touch "{tmp_path}/started-$6"
for _ in $(seq 100); do
  if [ -e "{tmp_path}/started-openvsx" ] && [ -e "{tmp_path}/started-uaa" ]; then
    for suite in {" ".join(_SUITES)}; do touch "{machine.out}/$6/$suite/mutation.json"; done
    exit 0
  fi
  sleep 0.05
done
exit 1
""",
    )

    machine.drive("mutation", "openvsx", "uaa", env={"JOBS": "2"})

    assert machine.result("openvsx", "codex", "mutation").exists()
    assert machine.result("uaa", "codex", "mutation").exists()


def test_stale_running_marker_does_not_outlive_a_new_run(machine: _Machine) -> None:
    (machine.out / ".run").mkdir(parents=True)
    (machine.out / ".run" / "kylin.mutation.running").write_text("then")

    machine.drive("coverage", "uaa")

    assert not (machine.out / ".run" / "kylin.mutation.running").exists()


def test_start_runs_in_the_background_and_status_reports_it(machine: _Machine) -> None:
    started = machine.run("start", "coverage", "uaa")

    assert started.returncode == 0, started.stdout + started.stderr
    assert "started as pid" in started.stdout
    machine.wait_until_finished()
    assert machine.stages() == [("coverage", "uaa")]

    status = machine.run("status").stdout
    assert "not running" in status
    assert re.search(r"uaa\s+done\s+pending, 0 of 4 suites", status)
    assert re.search(r"openvsx\s+pending\s+waiting for coverage", status)
    assert not (machine.out / ".run" / "start.lock").exists()


def test_start_defaults_to_every_project(machine: _Machine) -> None:
    assert machine.run("start").returncode == 0

    machine.wait_until_finished()

    stages = machine.stages()
    assert [p for stage, p in stages if stage == "coverage"] == _PROJECTS
    assert sorted(p for stage, p in stages if stage == "mutation") == sorted(_PROJECTS)
    assert _mutation_follows_coverage(stages)


def test_start_drops_duplicate_projects(machine: _Machine) -> None:
    assert machine.run("start", "coverage", "uaa", "uaa").returncode == 0

    machine.wait_until_finished()

    assert machine.stages() == [("coverage", "uaa")]


def test_start_remembers_the_output_directory(
    machine: _Machine, tmp_path: Path
) -> None:
    custom = tmp_path / "bigdisk"

    started = machine.run("start", "coverage", "uaa", env={"OUT": str(custom)})

    assert started.returncode == 0
    machine.wait_until_finished(custom)
    assert f"results: {custom.resolve()}" in machine.run("status").stdout


@pytest.mark.parametrize(
    "setting",
    [
        {"JOBS": "abc"},
        {"JOBS": "0"},
        {"THREADS": "2x"},
        {"WORKERS": "08"},
        {"SAMPLE": "0"},
        {"RETRIES": "-1"},
        {"RETRIES": "01"},
        {"PIT_TIMEOUT": "-1"},
        {"PAUSE_BELOW_GB": "0"},
        {"PAUSE_BELOW_GB": "50", "MIN_FREE_GB": "40"},
        {"COVERAGE_CAP": "8hours"},
        {"MUTATION_CAP": "soon"},
    ],
)
def test_start_rejects_bad_settings(
    machine: _Machine, setting: Mapping[str, str | None]
) -> None:
    started = machine.run("start", env=setting)

    assert started.returncode == 2
    assert machine.stages() == []


def test_start_rejects_unknown_stage_and_project(machine: _Machine) -> None:
    assert "unknown stage" in machine.run("start", "nonsense").stderr
    assert "unknown project" in machine.run("start", "all", "petclinic").stderr
    assert machine.stages() == []


def test_start_refuses_when_the_bundle_does_not_check_out(machine: _Machine) -> None:
    machine.use_inputs("a1b2", problem="error: resources/uaa lacks commit abc")

    started = machine.run("start")

    assert started.returncode == 1
    assert "error: resources/uaa lacks commit abc" in started.stderr
    assert "ready" not in started.stdout
    assert machine.stages() == []
    assert not (machine.out / ".run" / "bundle").exists()


def test_check_reports_the_bundle_then_ready(machine: _Machine) -> None:
    checked = machine.run("check")

    assert checked.returncode == 0
    assert f"bundle {machine.bundle} from Gerbil abc" in checked.stderr
    assert checked.stdout == (
        "ready\ndocker: 400 GiB, 48 CPUs; mutation would run 20 PIT workers\n"
    )
    assert (
        machine.run("check", env={"WORKERS": "7"}).stdout.splitlines()[1]
        == "docker: 400 GiB, 48 CPUs; mutation would run 7 PIT workers"
    )


def test_start_refuses_while_another_start_holds_the_lock(machine: _Machine) -> None:
    (machine.out / ".run" / "start.lock").mkdir(parents=True)

    assert "another start" in machine.run("start").stderr


def test_run_survives_hangup_and_stop_ends_every_process(machine: _Machine) -> None:
    machine.use_blocking_uv()
    assert machine.run("start", "coverage", "openvsx").returncode == 0
    job, child = machine.wait_for_job()
    runner = machine.runner_pid()
    machine.add_containers()

    os.kill(runner, signal.SIGHUP)
    time.sleep(0.3)
    assert _alive(runner)

    assert "running since" in machine.run("status").stdout
    assert "already running" in machine.run("start").stderr

    assert "stopped" in machine.run("stop").stdout

    assert _wait_until_dead(runner)
    assert _wait_until_dead(job)
    assert _wait_until_dead(child)
    assert set(machine.killed.read_text().split()) == {"mine", "other"}
    status = machine.run("status").stdout
    assert "not running" in status
    assert re.search(r"openvsx\s+pending", status)


def test_stop_and_start_handle_work_whose_runner_died(machine: _Machine) -> None:
    machine.use_blocking_uv()
    assert machine.run("start", "coverage", "openvsx").returncode == 0
    job, child = machine.wait_for_job()
    runner = machine.runner_pid()
    os.kill(runner, signal.SIGKILL)
    assert _wait_until_dead(runner)

    assert "already running" in machine.run("start").stderr

    assert "stopped" in machine.run("stop").stdout
    assert _wait_until_dead(job)
    assert _wait_until_dead(child)


def test_stop_without_a_run_still_clears_its_containers(machine: _Machine) -> None:
    machine.add_containers()

    assert "stopped" in machine.run("stop").stdout

    assert set(machine.killed.read_text().split()) == {"mine", "other"}
    assert (machine.containers / "elsewhere").exists()


def test_stop_reports_containers_it_could_not_kill(machine: _Machine) -> None:
    machine.add_containers()
    (machine.out / ".run").mkdir(parents=True)
    (machine.out / ".run" / "uaa.coverage.running").touch()
    docker = (machine.bin / "docker").read_text()
    (machine.bin / "docker").write_text(
        docker.replace('rm "' + str(machine.containers) + '/$2"', "exit 1")
    )

    stopped = machine.run("stop")

    assert stopped.returncode == 1
    assert "still alive" in stopped.stderr
    assert (machine.out / ".run" / "uaa.coverage.running").exists()


def test_start_refuses_a_leftover_container(machine: _Machine) -> None:
    machine.add_containers()

    assert "already running" in machine.run("start").stderr
    assert machine.stages() == []


def test_a_container_of_another_output_directory_does_not_block_start(
    machine: _Machine,
) -> None:
    machine.add_containers()
    (machine.containers / "mine").unlink()
    (machine.containers / "other").unlink()

    assert machine.run("start", "coverage", "uaa").returncode == 0
    machine.wait_until_finished()


@pytest.mark.parametrize("folder", ["a+b", "x(1)", "y[2]", "with space"])
def test_guard_handles_special_characters_in_the_output_path(
    machine: _Machine, tmp_path: Path, folder: str
) -> None:
    machine.use_blocking_uv()
    env = {"OUT": str(tmp_path / folder)}
    assert machine.run("start", "coverage", "uaa", env=env).returncode == 0
    job, child = machine.wait_for_job()

    assert re.search(r"^running$", machine.run("status", env=env).stdout, re.MULTILINE)
    assert "already running" in machine.run("start", env=env).stderr

    assert "stopped" in machine.run("stop", env=env).stdout
    assert _wait_until_dead(job)
    assert _wait_until_dead(child)


def test_guard_ignores_a_command_merely_quoting_the_output_path(
    machine: _Machine,
) -> None:
    machine.out.mkdir(parents=True)
    quoting = subprocess.Popen(
        [
            "bash",
            "-c",
            "sleep 30; : gerbil execution x --output-dir " + str(machine.out) + " ",
        ]
    )
    time.sleep(0.3)
    try:
        assert "not running" in machine.run("status").stdout
    finally:
        quoting.kill()
        quoting.wait()


def test_resume_keeps_the_pit_timeout_of_the_results(machine: _Machine) -> None:
    assert (
        machine.run("start", "mutation", "uaa", env={"PIT_TIMEOUT": "600"}).returncode
        == 0
    )
    machine.wait_until_finished()
    (machine.out / "run.log").unlink()

    refused = machine.run("start", "mutation", "uaa", env={"PIT_TIMEOUT": "60"})
    assert refused.returncode == 2
    assert "PIT_TIMEOUT=600" in refused.stderr

    machine.finish_coverage("uaa")
    assert machine.run("start", "mutation", "uaa").returncode == 0
    machine.wait_until_finished()
    assert "--timeout 180" not in machine.calls.read_text()
    assert "--timeout 600" in machine.calls.read_text()


def test_start_pins_the_bundles_inputs_and_keeps_its_manifest(
    machine: _Machine,
) -> None:
    assert machine.run("start", "coverage", "uaa").returncode == 0
    machine.wait_until_finished()

    assert (machine.out / ".run" / "bundle").read_text() == "a1b2\n"
    assert (machine.out / ".bundle" / "manifest.json").read_text() == '{"format": 1}'
    (uaa,) = machine.calls.read_text().splitlines()
    assert uaa.endswith(f"--bundle {machine.out}/.bundle")


def test_a_bundle_unpacked_during_a_run_does_not_reach_it(machine: _Machine) -> None:
    assert machine.run("start", "coverage", "uaa").returncode == 0
    machine.wait_until_finished()
    (machine.out / "run.log").unlink()
    (machine.bundle / "manifest.json").write_text('{"format": 2}')

    assert machine.run("start", "coverage", "openvsx").returncode == 0
    machine.wait_until_finished()

    assert (machine.out / ".bundle" / "manifest.json").read_text() == '{"format": 1}'
    assert machine.calls.read_text().count(f"--bundle {machine.out}/.bundle") == 2


def test_gathering_results_is_not_a_running_job(machine: _Machine) -> None:
    machine.out.mkdir(parents=True)
    gathering = subprocess.Popen(
        [
            "bash",
            "-c",
            "sleep 30; : uv run gerbil execution results --output-dir "
            + str(machine.out),
        ]
    )
    time.sleep(0.3)
    try:
        assert "not running" in machine.run("status").stdout
    finally:
        gathering.kill()
        gathering.wait()


def test_resume_refuses_another_bundles_inputs(machine: _Machine) -> None:
    assert machine.run("start", "coverage", "uaa").returncode == 0
    machine.wait_until_finished()
    (machine.out / "run.log").unlink()
    shutil.rmtree(machine.out / "uaa")
    machine.use_inputs("c3d4")

    refused = machine.run("start", "coverage", "uaa")

    assert refused.returncode == 2
    assert "another bundle's inputs" in refused.stderr
    assert machine.stages() == [("coverage", "uaa")]

    machine.use_inputs("a1b2")
    assert machine.run("start", "coverage", "uaa").returncode == 0
    machine.wait_until_finished()
    assert machine.stages() == [("coverage", "uaa"), ("coverage", "uaa")]


def test_bundle_writes_the_bundle_in_parts_of_the_given_size(
    machine: _Machine,
) -> None:
    bundled = machine.run("bundle", env={"PART_MB": "10", "BUNDLE": "send/bundle"})

    assert bundled.returncode == 0, bundled.stderr
    assert (machine.tmp / "packaging.log").read_text() == (
        "run gerbil execution bundle --output send/bundle --part-mb 10\n"
    )


def test_results_gathers_the_status_and_results_of_the_run(
    machine: _Machine,
) -> None:
    machine.drive("coverage", "uaa")

    gathered = machine.run("results", env={"RESULTS": "back"})

    assert gathered.returncode == 0, gathered.stderr
    assert (machine.tmp / "packaging.log").read_text() == (
        f"run gerbil execution results --output-dir {machine.out} "
        "--destination back --part-mb 15\n"
    )
    status = (machine.out / "status.txt").read_text()
    assert re.search(r"uaa\s+done", status)


def test_usage_lists_every_setting(machine: _Machine) -> None:
    usage = machine.run().stdout

    assert "MUTATION_CAP" in usage
    assert not (machine.out / ".run").exists()


def test_check_probes_the_results_directory_and_free_disk(machine: _Machine) -> None:
    assert machine.run("check", env={"MIN_FREE_GB": "75"}).returncode == 0

    (call,) = (machine.tmp / "check.log").read_text().splitlines()
    assert call.endswith(
        "--bundle outputs/execution-bundle --output-dir outputs/execution"
        " --min-free-gb 75"
    )


def test_resuming_reads_the_runs_own_copy_of_the_bundle(machine: _Machine) -> None:
    assert machine.run("start", "coverage", "uaa").returncode == 0
    machine.wait_until_finished()
    (machine.out / "run.log").unlink()
    shutil.rmtree(machine.bundle)
    (machine.tmp / "check.log").write_text("")

    assert machine.run("start", "coverage", "openvsx").returncode == 0
    machine.wait_until_finished()

    (call,) = (machine.tmp / "check.log").read_text().splitlines()
    assert f"--bundle {machine.out}/.bundle " in call
    assert machine.stages() == [("coverage", "uaa"), ("coverage", "openvsx")]


def test_first_pin_refuses_results_no_bundle_was_pinned_to(machine: _Machine) -> None:
    machine.finish_coverage("uaa")

    refused = machine.run("start", "coverage", "openvsx")

    assert refused.returncode == 2
    assert "results no bundle is pinned to" in refused.stderr
    assert not (machine.out / ".run" / "bundle").exists()
    assert machine.stages() == []


def test_run_log_records_the_code_of_each_stage(machine: _Machine) -> None:
    machine.drive("coverage", "uaa")

    log = (machine.out / "run.log").read_text()
    assert "RUN stage=coverage jobs=1 threads=6 workers=6 sample=2401 gerbil=" in log
    assert "START coverage uaa gerbil=" in log


def test_a_script_replaced_in_place_during_a_run_is_not_read_further(
    machine: _Machine,
) -> None:
    machine.use_blocking_uv()
    assert machine.run("start", "coverage", "uaa").returncode == 0
    _, child = machine.wait_for_job()
    # A longer version, as a pull brings: the bytes where the runner stopped reading
    # become its dispatch line again, which bash would run a second time.
    script = machine.root / "scripts" / _SCRIPT.name
    text = script.read_text()
    last_line = text.splitlines(keepends=True)[-1]
    shebang, rest = text.split("\n", 1)
    script.write_text(f"{shebang}\n{'#' * (len(last_line) - 1)}\n{rest}")

    os.kill(child, signal.SIGTERM)
    machine.wait_until_finished()
    time.sleep(1)
    try:
        assert (machine.out / "run.log").read_text().count("RUN stage") == 1
    finally:
        machine.run("stop")


@pytest.mark.parametrize(("stage", "workers"), [("all", 1), ("mutation", 2)])
def test_mutation_refuses_docker_memory_short_of_its_workers(
    machine: _Machine, stage: str, workers: int
) -> None:
    machine.use_docker_memory_gib(32)

    refused = machine.run("start", stage, "uaa", env={"WORKERS": str(workers)})

    assert refused.returncode == 2
    # Both stages together run coverage's test JVM beside the workers.
    assert f"{stage} needs 36 GiB for 2 JVM(s) of up to 18 GiB" in refused.stderr
    assert f"(WORKERS={workers}); Docker has 32 GiB" in refused.stderr
    # Offered only when leaving out coverage's JVM would make the workers fit.
    assert ("coverage, then mutation" in refused.stderr) is (stage == "all")


def test_no_split_is_suggested_when_the_workers_alone_exceed_dockers_memory(
    machine: _Machine,
) -> None:
    machine.use_docker_memory_gib(32)

    refused = machine.run("start", "all", "uaa", env={"WORKERS": "2"})

    assert refused.returncode == 2
    assert "all needs 54 GiB for 3 JVM(s)" in refused.stderr
    assert "coverage, then mutation" not in refused.stderr
    assert machine.stages() == []
    assert not (machine.out / ".run" / "bundle").exists()


def test_mutation_starts_when_docker_memory_covers_its_workers(
    machine: _Machine,
) -> None:
    machine.use_docker_memory_gib(36)
    machine.finish_coverage("uaa")
    (machine.out / ".run").mkdir(parents=True, exist_ok=True)
    (machine.out / ".run" / "bundle").write_text("a1b2\n")

    started = machine.run("start", "mutation", "uaa", env={"WORKERS": "2"})

    assert started.returncode == 0, started.stderr
    machine.wait_until_finished()
    assert machine.stages() == [("mutation", "uaa")]


@pytest.mark.parametrize(
    ("gib", "cpus", "workers"),
    # A worker for each 18 GiB but two, kept for coverage's test JVM and PIT's own
    # processes; one per CPU at most, and never none.
    [(377, 48, 18), (400, 8, 8), (40, 48, 1), (20, 48, 1)],
)
def test_start_fits_the_worker_budget_to_dockers_memory_and_cpus(
    machine: _Machine, gib: int, cpus: int, workers: int
) -> None:
    machine.use_docker_memory_gib(gib, cpus)
    machine.finish_coverage("uaa")
    (machine.out / ".run").mkdir(parents=True, exist_ok=True)
    (machine.out / ".run" / "bundle").write_text("a1b2\n")

    started = machine.run("start", "mutation", "uaa")

    assert started.returncode == 0, started.stderr
    machine.wait_until_finished()
    assert f"--workers {workers} " in machine.calls.read_text()
    assert f"settings: workers={workers} threads=6" in machine.run("status").stdout


def test_resume_keeps_the_sample_size_of_the_results(machine: _Machine) -> None:
    assert machine.run("start", "all", "uaa", env={"SAMPLE": "500"}).returncode == 0
    machine.wait_until_finished()

    changed = machine.run("start", "all", "uaa", env={"SAMPLE": "600"})

    assert changed.returncode == 2
    assert "these results use SAMPLE=500" in changed.stderr
    shutil.rmtree(machine.out / "uaa" / "codex")
    machine.calls.write_text("")
    (machine.out / "run.log").unlink()
    assert machine.run("start", "all", "uaa").returncode == 0
    machine.wait_until_finished()
    assert "--sample-size 500" in machine.calls.read_text()


def test_coverage_alone_fixes_no_mutation_setting(machine: _Machine) -> None:
    assert machine.run("start", "coverage", "uaa").returncode == 0
    machine.wait_until_finished()
    (machine.out / "run.log").unlink()

    started = machine.run("start", "mutation", "uaa", env={"SAMPLE": "500"})

    assert started.returncode == 0, started.stderr
    machine.wait_until_finished()
    assert "--sample-size 500" in machine.calls.read_text()
    assert (machine.out / ".run" / "sample").read_text() == "500\n"


def test_a_refused_start_pins_nothing(machine: _Machine) -> None:
    (machine.out / "uaa" / "codex").mkdir(parents=True)
    (machine.out / "uaa" / "codex" / "coverage.json").touch()

    # Results that no bundle is pinned to refuse the start.
    refused = machine.run("start", "all", "uaa", env={"SAMPLE": "500"})

    assert refused.returncode == 2
    assert not (machine.out / ".run" / "sample").exists()
    assert not (machine.out / ".run" / "pit-timeout").exists()


@pytest.mark.parametrize(("gib", "starts"), [(17, False), (18, True)])
def test_coverage_needs_memory_for_one_test_jvm_only(
    machine: _Machine, gib: int, starts: bool
) -> None:
    machine.use_docker_memory_gib(gib)

    started = machine.run("start", "coverage", "uaa", env={"THREADS": "8"})

    assert (started.returncode == 0) is starts
    if starts:
        machine.wait_until_finished()
        assert machine.stages() == [("coverage", "uaa")]
    else:
        assert "coverage needs 18 GiB for 1 JVM(s)" in started.stderr
        assert machine.stages() == []


def test_unreadable_docker_memory_refuses_to_start(machine: _Machine) -> None:
    (machine.tmp / "capacity").write_text("unknown\n")

    refused = machine.run("start", "coverage", "uaa")

    assert refused.returncode == 2
    assert "cannot read Docker's memory and CPUs: 'unknown '" in refused.stderr
    assert machine.stages() == []


@pytest.mark.parametrize(
    ("free_gb", "line"),
    [
        (30.5, "disk: 30 GB free; new suites and PIT runs wait below 10"),
        (9.9, "disk: 9 GB free; new suites and PIT runs wait until 10 are"),
    ],
)
def test_status_reports_free_disk_and_whether_new_work_waits(
    machine: _Machine, free_gb: float, line: str
) -> None:
    machine.drive("coverage", "uaa")
    # df counts 1024-byte blocks; the script reports GB of 10^9 bytes, as the CLI does.
    blocks = int(free_gb * 10**9 / 1024)
    # A device whose name holds a space, as macOS's "map auto_home" does.
    _stub(
        machine.bin,
        "df",
        "echo 'Filesystem 1024-blocks Used Available Capacity Mounted on'\n"
        f"echo 'map auto_home 999 1 {blocks} 1% /'",
    )

    assert line in machine.run("status").stdout.splitlines()


def test_status_reports_the_free_disk_a_started_run_waits_for(
    machine: _Machine,
) -> None:
    started = machine.run("start", "coverage", "uaa", env={"PAUSE_BELOW_GB": "1"})
    assert started.returncode == 0, started.stdout + started.stderr
    machine.wait_until_finished()

    # Asked without the setting, as from another shell.
    status = machine.run("status").stdout

    assert re.search(r"^disk: \d+ GB free; .* (below|until) 1( are)?$", status, re.M)


def test_status_survives_a_df_that_fails(machine: _Machine) -> None:
    machine.drive("coverage", "uaa")
    _stub(machine.bin, "df", "exit 1")

    status = machine.run("status")

    assert status.returncode == 0 and status.stderr == ""
    assert "disk:" not in status.stdout
