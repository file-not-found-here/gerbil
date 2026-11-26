"""The annotation page's judgment logic, run under Node from the page itself."""

from __future__ import annotations

import json
import shutil
import subprocess
from importlib import resources
from typing import Any

import pytest

from gerbil.validation.scoring import ATTRIBUTE_CLAIMS

_NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(_NODE is None, reason="needs node to run the page's JS")

_DISPATCH = {
    "id": "i2",
    "kind": "dispatch",
    "step": 1,
    "http_method": "GET",
    "path": "/widgets",
    "path_truncated": False,
}
_UNRESOLVED = {**_DISPATCH, "http_method": None, "path": None}
_STATUS_CHECK = {
    "id": "i3",
    "kind": "check",
    "step": 1,
    "target": "status",
    "status_code": 200,
    "status_range": "2xx",
}
_BUILDER = {"id": "i1", "kind": "builder", "step": None, "parts": ["header/auth"]}


def _logic() -> str:
    page = resources.files("gerbil.validation").joinpath("annotate.html").read_text()
    block = page.split("/* logic:start", 1)[1].split("/* logic:end */", 1)[0]
    return block.split("*/", 1)[1]


def _run(expression: str, **values: Any) -> Any:
    bindings = "".join(
        f"const {name} = {json.dumps(value)};\n" for name, value in values.items()
    )
    script = f"{_logic()}\n{bindings}console.log(JSON.stringify({expression}));"
    assert _NODE is not None
    result = subprocess.run(
        [_NODE, "-e", script], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def _options(item: dict[str, Any]) -> dict[str, list[str]]:
    claims = _run(
        "claimsFor(item).map((c) => [c.key, c.opts.map((o) => o[0])])", item=item
    )
    return dict(claims)


def test_a_reported_value_is_a_claim_to_judge() -> None:
    assert _options(_DISPATCH) == {
        "role": ["yes", "no"],
        "verb": ["correct", "wrong"],
        "path": ["correct", "partial", "wrong"],
    }
    assert _options(_STATUS_CHECK)["code"] == ["correct", "wrong"]


def test_an_unresolved_value_is_an_abstention_with_no_correct_answer() -> None:
    options = _options(_UNRESOLVED)

    assert options["verb"] == options["path"] == ["found", "undeterminable"]
    no_status = {**_STATUS_CHECK, "status_code": None, "status_range": None}
    assert _options(no_status)["code"] == ["found", "undeterminable"]
    # A range without a code is still something Gerbil reported.
    assert _options({**_STATUS_CHECK, "status_code": None})["code"] == [
        "correct",
        "wrong",
    ]
    # Only a status check has a status code to resolve.
    assert "code" not in _options({**no_status, "target": "body"})


def test_all_correct_cannot_answer_for_an_abstention() -> None:
    answers = _run("allCorrect(item)", item=_UNRESOLVED)

    assert answers == {"role": {"a": "yes"}}
    assert _run("itemState(item, answers)", item=_UNRESOLVED, answers=answers) == "todo"
    assert (
        _run(
            "itemState(item, allCorrect(item))",
            item=_DISPATCH,
        )
        == "ok"
    )


@pytest.mark.parametrize(
    ("path_answer", "state"),
    [
        ({"a": "undeterminable"}, "ok"),
        ({"a": "found"}, "todo"),
        ({"a": "found", "v": "  "}, "todo"),
        ({"a": "found", "v": "/widgets/{id}"}, "issue"),
    ],
)
def test_an_abstention_needs_a_value_or_a_cannot_be_determined(
    path_answer: dict[str, str], state: str
) -> None:
    answers = {
        "role": {"a": "yes"},
        "verb": {"a": "undeterminable"},
        "path": path_answer,
    }

    assert _run("itemState(item, answers)", item=_UNRESOLVED, answers=answers) == state


def test_rejecting_an_item_leaves_only_its_role_to_answer() -> None:
    required = _run(
        "requiredClaims(item, answers).map((c) => c.key)",
        item=_DISPATCH,
        answers={"role": {"a": "no"}},
    )

    assert required == ["role"]
    assert (
        _run("itemState(item, answers)", item=_DISPATCH, answers={"role": {"a": "no"}})
        == "issue"
    )


def test_a_check_of_another_target_has_no_status_code_to_judge() -> None:
    answers = {
        "role": {"a": "yes"},
        "target": {"a": "wrong", "v": "body"},
        "step": {"a": "correct"},
    }

    required = _run(
        "requiredClaims(item, answers).map((c) => c.key)",
        item=_STATUS_CHECK,
        answers=answers,
    )

    assert required == ["role", "target", "step"]
    assert _run("itemState(item, answers)", item=_STATUS_CHECK, answers=answers) == (
        "issue"
    )
    # A wrong answer that needs a value is unfinished without it.
    answers["target"] = {"a": "wrong"}
    assert _run("itemState(item, answers)", item=_STATUS_CHECK, answers=answers) == (
        "todo"
    )


def test_a_builder_with_several_corrected_parts_needs_at_least_one() -> None:
    def state(parts: list[str]) -> str:
        answers = {
            "role": {"a": "yes"},
            "part": {"a": "wrong", "v": parts},
            "step": {"a": "wrong", "v": "none"},
        }
        return str(_run("itemState(item, answers)", item=_BUILDER, answers=answers))

    assert state([]) == "todo"
    assert state(["method", "path"]) == "issue"


def test_a_test_is_ready_once_every_item_is_answered() -> None:
    test = {"items": [_DISPATCH, _STATUS_CHECK]}

    def ready(record: dict[str, Any]) -> bool:
        return bool(_run("testReady(test, record)", test=test, record=record))

    correct = {
        "i2": _run("allCorrect(item)", item=_DISPATCH),
        "i3": _run("allCorrect(item)", item=_STATUS_CHECK),
    }
    assert ready({"items": correct})
    assert not ready({"items": {"i2": correct["i2"]}})
    # A test that sends no request to the application is every card answered No.
    assert ready({"items": {"i2": {"role": {"a": "no"}}, "i3": {"role": {"a": "no"}}}})


@pytest.mark.parametrize(
    ("draft", "ready"),
    [
        ({"kind": "", "verb": "GET"}, False),
        ({"kind": "dispatch", "verb": ""}, False),
        ({"kind": "dispatch", "verb": "undeterminable"}, True),
        ({"kind": "builder", "parts": [], "step": "1"}, False),
        ({"kind": "builder", "parts": ["body"], "step": ""}, False),
        ({"kind": "builder", "parts": ["body"], "step": "missed"}, True),
        ({"kind": "check", "target": "", "step": "1"}, False),
        ({"kind": "check", "target": "status", "step": "1"}, True),
    ],
)
def test_a_missed_item_needs_its_kind_and_what_it_belongs_to(
    draft: dict[str, Any], ready: bool
) -> None:
    assert _run("draftReady(draft)", draft=draft) is ready


def test_source_text_is_escaped_into_the_page() -> None:
    assert (
        _run("esc(text)", text='<b a="1">&\'') == "&lt;b a=&quot;1&quot;&gt;&amp;&#39;"
    )


def test_scoring_reads_the_claims_the_page_asks() -> None:
    no_status = {**_STATUS_CHECK, "status_code": None, "status_range": None}

    for item in (_BUILDER, _DISPATCH, _UNRESOLVED, _STATUS_CHECK, no_status):
        claims = ATTRIBUTE_CLAIMS[str(item["kind"])]
        assert tuple(_options(item)) == ("role", *claims)
