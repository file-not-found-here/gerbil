"""Zip a directory into standalone parts small enough to send by email."""

from __future__ import annotations

import re
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from gerbil.execution.errors import ExecutionError


# Zip's per-file header, per-file index entry and end record, before names and extras.
_ENTRY_BYTES = 30 + 36
_INDEX_ENTRY_BYTES = 46 + 36
_END_BYTES = 22


def zip_directory(directory: Path, part_bytes: int) -> list[Path]:
    """Zip `directory` beside itself, each part holding whole files under the directory's
    name, so extracting every part into one place rebuilds it.

    Parts are named `<name>-<UTC time>-<n>of<m>.zip`, so parts of different zippings
    cannot be mistaken for one another. A part grows past `part_bytes` only to hold a
    single larger file. Earlier archives of the directory are removed.
    """
    name = re.escape(directory.name)
    stale = re.compile(rf"{name}(-\d+|-\d{{8}}T\d{{6}}Z-\d+of\d+)?\.zip(\.partial)?")
    for path in directory.parent.iterdir():
        if stale.fullmatch(path.name):
            path.unlink()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    partials: list[Path] = []
    archive: zipfile.ZipFile | None = None
    index = 0
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        entry_name = path.relative_to(directory.parent).as_posix()
        if archive is not None and archive.fp is not None:
            entry = _ENTRY_BYTES + len(entry_name) + path.stat().st_size
            if archive.fp.tell() + index + entry + _END_BYTES > part_bytes:
                archive.close()
                archive = None
        if archive is None:
            index = 0
            partials.append(
                directory.with_name(f"{directory.name}-{len(partials) + 1}.zip.partial")
            )
            archive = zipfile.ZipFile(
                partials[-1], "w", zipfile.ZIP_DEFLATED, compresslevel=9
            )
        archive.write(path, entry_name)
        index += _INDEX_ENTRY_BYTES + len(entry_name)
    if archive is not None:
        archive.close()
    parts = [
        directory.with_name(f"{directory.name}-{stamp}-{number}of{len(partials)}.zip")
        for number in range(1, len(partials) + 1)
    ]
    for partial, part in zip(partials, parts, strict=True):
        partial.replace(part)
    return parts


def replace_written_directory(building: Path, destination: Path, marker: str) -> None:
    """Move `building` to `destination`, replacing only a directory that holds `marker`,
    which marks it as written by the same command."""
    if destination.exists():
        if not (destination / marker).is_file():
            raise ExecutionError(
                f"{destination} exists and was not written by this command; "
                "choose a new directory"
            )
        try:
            shutil.rmtree(destination)
        except OSError as error:
            raise ExecutionError(f"cannot replace {destination}: {error}") from error
    building.rename(destination)
