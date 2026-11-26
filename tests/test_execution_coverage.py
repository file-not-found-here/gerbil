from __future__ import annotations

from pathlib import Path, PurePosixPath

from gerbil.execution.coverage import (
    CoverageCounter,
    jacoco_agent,
    parse_report,
    report_coverage,
    write_report,
)
from tests.execution_fakes import RecordingContainer, recording_container

_REPORT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">
<report name="JaCoCo Coverage Report">
  <sessioninfo id="s" start="1" dump="2"/>
  <package name="org/demo">
    <class name="org/demo/UserController" sourcefilename="UserController.java">
      <method name="get" desc="()V" line="10">
        <counter type="LINE" missed="0" covered="2"/>
      </method>
      <counter type="INSTRUCTION" missed="4" covered="6"/>
      <counter type="LINE" missed="1" covered="2"/>
    </class>
    <class name="org/demo/UserController$1" sourcefilename="UserController.java">
      <counter type="LINE" missed="3" covered="0"/>
    </class>
    <class name="org/demo/Unloaded" sourcefilename="Unloaded.java">
      <counter type="INSTRUCTION" missed="5" covered="0"/>
    </class>
    <counter type="LINE" missed="4" covered="2"/>
  </package>
  <counter type="INSTRUCTION" missed="9" covered="6"/>
  <counter type="BRANCH" missed="0" covered="0"/>
  <counter type="LINE" missed="4" covered="2"/>
</report>
"""


def test_parse_report_reads_totals_and_classes_with_covered_lines(
    tmp_path: Path,
) -> None:
    report_file = tmp_path / "coverage.xml"
    report_file.write_text(_REPORT)

    report = parse_report(report_file)

    assert report.counters == {
        "INSTRUCTION": CoverageCounter(covered=6, missed=9),
        "BRANCH": CoverageCounter(covered=0, missed=0),
        "LINE": CoverageCounter(covered=2, missed=4),
    }
    assert report.covered_classes == ["org.demo.UserController"]


def test_counter_ratio_is_none_without_coverable_items() -> None:
    assert CoverageCounter(covered=0, missed=0).ratio is None
    assert CoverageCounter(covered=1, missed=3).ratio == 0.25


def test_jacoco_agent_writes_to_given_exec_file() -> None:
    assert jacoco_agent(PurePosixPath("/out/runs/api/baseline/jacoco.exec")) == (
        "-javaagent:/opt/gerbil/lib/jacoco/jacocoagent.jar"
        "=destfile=/out/runs/api/baseline/jacoco.exec,append=false"
    )


def test_write_report_passes_exec_files_and_class_directories(tmp_path: Path) -> None:
    container = recording_container(tmp_path)

    write_report(
        container,
        [PurePosixPath("/out/a.exec"), PurePosixPath("/out/b.exec")],
        ["/workspace/api/target/classes", "/workspace/core/target/classes"],
        PurePosixPath("/out/coverage.xml"),
        tmp_path / "log",
    )

    ((command, _),) = container.commands
    assert command == [
        "java",
        "-jar",
        "/opt/gerbil/lib/jacoco/jacococli.jar",
        "report",
        "/out/a.exec",
        "/out/b.exec",
        "--classfiles",
        "/workspace/api/target/classes",
        "--classfiles",
        "/workspace/core/target/classes",
        "--xml",
        "/out/coverage.xml",
    ]


def _report_xml(name: str, covered: int, missed: int) -> str:
    return (
        f'<report name="x"><package name="org"><class name="org/{name}">'
        f'<counter type="LINE" missed="{missed}" covered="{covered}"/>'
        f'</class></package><counter type="LINE" missed="{missed}" '
        f'covered="{covered}"/></report>'
    )


def _class_file(root: Path, relative: str, content: bytes) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return "/workspace/" + relative.split("/org/")[0]


def test_report_coverage_splits_conflicting_classes_and_merges_counts(
    tmp_path: Path,
) -> None:
    def effect(
        container: RecordingContainer, command: list[str], workdir: PurePosixPath
    ) -> None:
        directories = [
            command[index + 1]
            for index, argument in enumerate(command)
            if argument == "--classfiles"
        ]
        name, covered, missed = (
            ("Other", 0, 4) if directories == ["/workspace/b/classes"] else ("A", 3, 1)
        )
        container.host_path(PurePosixPath(command[-1])).write_text(
            _report_xml(name, covered, missed)
        )

    container = recording_container(tmp_path, effect)
    workspace = container.workspace
    a = _class_file(workspace, "a/classes/org/Same.class", b"one")
    b = _class_file(workspace, "b/classes/org/Same.class", b"two")
    c = _class_file(workspace, "c/classes/org/Same.class", b"one")
    d = _class_file(workspace, "d/classes/org/Unique.class", b"three")
    _class_file(workspace, "a/classes/org/package-info.class", b"p1")
    _class_file(workspace, "d/classes/org/package-info.class", b"p2")

    report = report_coverage(
        container,
        [PurePosixPath("/out/a.exec")],
        [a, b, c, d],
        PurePosixPath("/out/coverage"),
        tmp_path / "log",
    )

    assert [command[-1] for command, _ in container.commands] == [
        "/out/coverage/jacoco-0.xml",
        "/out/coverage/jacoco-1.xml",
    ]
    first, second = (command for command, _ in container.commands)
    assert [first[i + 1] for i, x in enumerate(first) if x == "--classfiles"] == [
        a,
        c,
        d,
    ]
    assert [second[i + 1] for i, x in enumerate(second) if x == "--classfiles"] == [b]
    assert report.counters == {"LINE": CoverageCounter(covered=3, missed=5)}
    assert report.covered_classes == ["org.A"]
