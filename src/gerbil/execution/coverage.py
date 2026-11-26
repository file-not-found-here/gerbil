"""Generate and read JaCoCo reports over a workspace's project class directories."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ElementTree
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, computed_field

from gerbil.execution.build import CODELESS_CLASS_FILES
from gerbil.execution.container import TOOLS, Container

_JACOCO = TOOLS / "lib" / "jacoco"


def jacoco_agent(exec_file: PurePosixPath) -> str:
    return f"-javaagent:{_JACOCO / 'jacocoagent.jar'}=destfile={exec_file},append=false"


class CoverageCounter(BaseModel):
    covered: int
    missed: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ratio(self) -> float | None:
        total = self.covered + self.missed
        return self.covered / total if total else None


class CoverageReport(BaseModel):
    counters: dict[str, CoverageCounter]
    covered_classes: list[str]


def report_coverage(
    container: Container,
    exec_files: Sequence[PurePosixPath],
    class_directories: Sequence[str],
    report_directory: PurePosixPath,
    log: Path,
) -> CoverageReport:
    """Report each group of class directories JaCoCo can analyze together and merge them.

    JaCoCo rejects different classes that share a name, which monorepos of independent
    services contain.
    """
    container.host_path(report_directory).mkdir(parents=True, exist_ok=True)
    reports = []
    for index, group in enumerate(_conflict_free_groups(container, class_directories)):
        xml_file = report_directory / f"jacoco-{index}.xml"
        write_report(container, exec_files, group, xml_file, log)
        reports.append(parse_report(container.host_path(xml_file)))
    return _merge(reports)


def _conflict_free_groups(
    container: Container, class_directories: Sequence[str]
) -> list[list[str]]:
    groups: list[tuple[list[str], dict[str, str]]] = []
    for directory in class_directories:
        digests = _class_digests(container.host_path(PurePosixPath(directory)))
        for group, group_digests in groups:
            if all(
                group_digests.get(name, digest) == digest
                for name, digest in digests.items()
            ):
                group.append(directory)
                group_digests.update(digests)
                break
        else:
            groups.append(([directory], digests))
    return [group for group, _ in groups] or [[]]


def _class_digests(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*.class")
        if path.name not in CODELESS_CLASS_FILES
    }


def _merge(reports: Sequence[CoverageReport]) -> CoverageReport:
    counters: dict[str, CoverageCounter] = {}
    for report in reports:
        for name, counter in report.counters.items():
            total = counters.get(name, CoverageCounter(covered=0, missed=0))
            counters[name] = CoverageCounter(
                covered=total.covered + counter.covered,
                missed=total.missed + counter.missed,
            )
    return CoverageReport(
        counters=counters,
        covered_classes=sorted(
            {name for report in reports for name in report.covered_classes}
        ),
    )


def write_report(
    container: Container,
    exec_files: Sequence[PurePosixPath],
    class_directories: Sequence[str],
    xml_file: PurePosixPath,
    log: Path,
) -> None:
    container.run(
        [
            "java",
            "-jar",
            str(_JACOCO / "jacococli.jar"),
            "report",
            *(str(exec_file) for exec_file in exec_files),
            *(
                argument
                for directory in class_directories
                for argument in ("--classfiles", directory)
            ),
            "--xml",
            str(xml_file),
        ],
        log=log,
    )


def parse_report(xml_file: Path) -> CoverageReport:
    """Report-wide counters and the classes with at least one covered line."""
    root = ElementTree.parse(xml_file).getroot()
    covered_classes = sorted(
        element.attrib["name"].replace("/", ".")
        for element in root.iter("class")
        if _counters(element).get("LINE", CoverageCounter(covered=0, missed=0)).covered
    )
    return CoverageReport(counters=_counters(root), covered_classes=covered_classes)


def covered_lines(report_directory: Path) -> dict[str, set[int]]:
    """The covered lines of each class's source file, by class name, over the reports
    `report_coverage` wrote to `report_directory`."""
    lines: dict[str, set[int]] = {}
    for xml_file in sorted(report_directory.glob("jacoco-*.xml")):
        for package in ElementTree.parse(xml_file).getroot().iter("package"):
            by_source_file = {
                source.attrib["name"]: {
                    int(line.attrib["nr"])
                    for line in source.findall("line")
                    if int(line.attrib["ci"])
                }
                for source in package.findall("sourcefile")
            }
            for element in package.findall("class"):
                name = element.attrib["name"].replace("/", ".")
                source_file = element.attrib.get("sourcefilename", "")
                lines.setdefault(name, set()).update(
                    by_source_file.get(source_file, ())
                )
    return lines


def _counters(element: ElementTree.Element) -> dict[str, CoverageCounter]:
    return {
        counter.attrib["type"]: CoverageCounter(
            covered=int(counter.attrib["covered"]), missed=int(counter.attrib["missed"])
        )
        for counter in element.findall("counter")
    }
