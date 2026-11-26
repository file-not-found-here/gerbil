from __future__ import annotations

import json
from pathlib import Path

from scripts.agent_patch_extraction import extract_patches
from scripts.trajectory_extraction import CODEX_DIFF_REPORT, extract_trajectories


def _raw_run(root: Path, name: str, messages: list[dict[str, object]]) -> Path:
    output = root / name / "output"
    output.mkdir(parents=True)
    (output / "messages.jsonl").write_text(
        "".join(json.dumps(message) + "\n" for message in messages)
    )
    (output / "git_diff.patch").write_text("diff --git a/T.java b/T.java\n")
    (output / "test_clearing.patch").write_text("diff --git a/Old.java b/Old.java\n")
    return output


def _report(turn: str, diff: str) -> dict[str, object]:
    return {"method": CODEX_DIFF_REPORT, "payload": {"turn_id": turn, "diff": diff}}


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_claude_code_trajectories_are_copied_verbatim(tmp_path: Path) -> None:
    messages: list[dict[str, object]] = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "text": f"mentions {CODEX_DIFF_REPORT} in passing"},
    ]
    output = _raw_run(tmp_path / "raw", "20260619T0__claude-code__uaa", messages)

    (result,) = extract_trajectories(tmp_path / "raw", tmp_path / "runs")

    copied = tmp_path / "runs/uaa/trajectories/messages.jsonl"
    assert copied.read_bytes() == (output / "messages.jsonl").read_bytes()
    assert (result.status, result.dropped_diff_reports) == ("copied", 0)


def test_codex_trajectories_keep_only_each_turns_last_diff_report(
    tmp_path: Path,
) -> None:
    edit: dict[str, object] = {
        "method": "item/completed",
        "payload": {"item": {"type": "fileChange"}},
    }
    messages: list[dict[str, object]] = [
        _report("t1", "a"),
        edit,
        _report("t1", "a b"),
        _report("t2", "c"),
        edit,
        _report("t2", "c d"),
        {"method": "turn/completed", "payload": {"turn": {"id": "t2"}}},
    ]
    _raw_run(tmp_path / "raw", "20261107T0__codex__spinnaker", messages)

    (result,) = extract_trajectories(tmp_path / "raw", tmp_path / "runs")

    copied = _lines(tmp_path / "runs/spinnaker/trajectories/messages.jsonl")
    assert copied == [
        edit,
        _report("t1", "a b"),
        edit,
        _report("t2", "c d"),
        messages[-1],
    ]
    assert result.dropped_diff_reports == 2


def test_trajectories_already_extracted_are_kept_without_overwrite(
    tmp_path: Path,
) -> None:
    _raw_run(tmp_path / "raw", "x__codex__kylin", [{"method": "turn/started"}])
    existing = tmp_path / "runs/kylin/trajectories/messages.jsonl"
    existing.parent.mkdir(parents=True)
    existing.write_text("earlier\n")

    (kept,) = extract_trajectories(tmp_path / "raw", tmp_path / "runs")
    (replaced,) = extract_trajectories(
        tmp_path / "raw", tmp_path / "runs", overwrite=True
    )

    assert (kept.status, replaced.status) == ("exists", "copied")
    assert _lines(existing) == [{"method": "turn/started"}]


def test_patches_are_copied_into_each_projects_test_patches(tmp_path: Path) -> None:
    _raw_run(tmp_path / "raw", "x__codex__openvsx", [])

    results = extract_patches(tmp_path / "raw", tmp_path / "runs")

    patches = tmp_path / "runs/openvsx/test_patches"
    assert sorted(path.name for path in patches.iterdir()) == [
        "git_diff.patch",
        "test_clearing.patch",
    ]
    assert {result.status for result in results} == {"copied"}
