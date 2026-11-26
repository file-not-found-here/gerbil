from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gerbil.cli import app
from gerbil.validation.bundle import BUNDLE_FILE
from gerbil.validation.sampling import SAMPLE_FILE
from gerbil.validation.scoring import (
    MIN_BOOTSTRAP_PROJECTS,
    SCORES_FILE,
    ScoringError,
    krippendorff_alpha,
    percent_agreement,
    rates,
    read_annotations,
    score,
    tally_test,
)


def _item(
    item_id: str, kind: str, method_name: str, line: int, *, step: int | None = 1
) -> dict[str, Any]:
    return {
        "id": item_id,
        "kind": kind,
        "step": step,
        "method_name": method_name,
        "origin_class": "p.C",
        "origin_signature": "t()",
        "span": {
            "start_line": line,
            "start_column": 9,
            "end_line": line,
            "end_column": 30,
        },
    }


_BUILDER = _item("i1", "builder", "header", 5)
_DISPATCH = _item("i2", "dispatch", "perform", 6)
_CHECK = _item("i3", "check", "isOk", 7)


def _test(
    test_id: str = "p::C::t()",
    *,
    dataset: str = "p",
    step_count: int = 1,
    in_helper: bool = False,
) -> dict[str, Any]:
    return {
        "id": test_id,
        "dataset": dataset,
        "step_count": step_count,
        "has_helper_or_fixture_http": in_helper,
        "items": [_BUILDER, _DISPATCH, _CHECK],
    }


def _correct() -> dict[str, dict[str, dict[str, str]]]:
    return {
        "i1": {
            "role": {"a": "yes"},
            "part": {"a": "correct"},
            "step": {"a": "correct"},
        },
        "i2": {
            "role": {"a": "yes"},
            "verb": {"a": "correct"},
            "path": {"a": "correct"},
        },
        "i3": {
            "role": {"a": "yes"},
            "target": {"a": "correct"},
            "code": {"a": "correct"},
            "step": {"a": "correct"},
        },
    }


def _record(
    changes: dict[str, dict[str, str]] | None = None,
    *,
    added: list[tuple[Any, ...]] | None = None,
    done: bool = True,
    hidden: bool = False,
) -> dict[str, Any]:
    """An all-correct record, with `changes` as {item id: {claim: answer}}."""
    items = _correct()
    for item_id, claims in (changes or {}).items():
        for claim, answer in claims.items():
            items[item_id][claim] = {"a": answer}
    return {
        "hiddenHttp": hidden,
        "notes": "",
        "done": done,
        "items": items,
        "added": [
            {
                "id": f"a{n}",
                "kind": kind,
                "start": start,
                "end": end,
                "unit": unit[0] if unit else "u0",
                "step": "" if kind == "dispatch" else "1",
            }
            for n, (kind, start, end, *unit) in enumerate(added or [], 1)
        ],
    }


def _bundle(*tests: dict[str, Any]) -> dict[str, Any]:
    return {
        "bundle_digest": "bundle",
        "sample_digest": "sample",
        "seed": "seed",
        "tests": list(tests),
    }


def _annotations(
    bundle: dict[str, Any], *per_annotator: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """One export per annotator, its records given in the bundle's test order."""
    return [
        {
            "annotator": f"author{number}",
            "bundle_digest": bundle["bundle_digest"],
            "tests": {
                test["id"]: record
                for test, record in zip(bundle["tests"], records, strict=True)
            },
        }
        for number, records in enumerate(per_annotator, 1)
    ]


_SAMPLE = {"sample": {"digest": "sample"}}


def test_items_every_annotator_confirms_are_true_positives() -> None:
    tally = tally_test(_test(), [_record(), _record(), _record()])

    assert rates(tally)["extraction"]["all"] == {
        "tp": 3,
        "fp": 0,
        "fn": 0,
        "split": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    assert rates(tally)["api_test"]["precision"] == 1.0
    assert rates(tally)["attributes"]["builder.part"]["accuracy"] == 1.0


def test_the_majority_decides_and_two_annotators_can_split() -> None:
    not_a_check = {"i3": {"role": "no"}}

    outvoted = tally_test(_test(), [_record(not_a_check), _record(), _record()])
    rejected = tally_test(
        _test(), [_record(not_a_check), _record(not_a_check), _record()]
    )
    split = tally_test(_test(), [_record(not_a_check), _record()])

    assert (outvoted["check.tp"], outvoted["check.fp"]) == (1, 0)
    assert (rejected["check.tp"], rejected["check.fp"]) == (0, 1)
    assert rates(rejected)["extraction"]["check"]["precision"] == 0.0
    # Excluded from both rates rather than counted either way.
    assert (split["check.tp"], split["check.fp"], split["check.split"]) == (0, 0, 1)
    assert rates(split)["extraction"]["all"]["precision"] == 1.0


def test_attributes_are_judged_by_those_who_accepted_the_item() -> None:
    tally = tally_test(
        _test(),
        [
            _record({"i1": {"part": "wrong"}}),
            _record({"i1": {"part": "wrong"}}),
            # Rejected the builder; its stale part answer does not count.
            _record({"i1": {"role": "no", "part": "correct"}}),
        ],
    )

    assert tally["builder.tp"] == 1
    assert (tally["builder.part.wrong"], tally["builder.part.correct"]) == (1, 0)
    assert rates(tally)["attributes"]["builder.part"]["accuracy"] == 0.0


def test_a_status_code_is_not_judged_on_a_check_of_another_target() -> None:
    wrong_target = {"i3": {"target": "wrong", "code": "correct"}}

    tally = tally_test(_test(), [_record(wrong_target), _record(wrong_target)])

    assert tally["check.target.wrong"] == 1
    assert not [key for key in tally if key.startswith("check.code")]


def test_a_status_code_is_judged_only_when_the_majority_keeps_the_target() -> None:
    wrong_target = _record({"i3": {"target": "wrong"}})
    del wrong_target["items"]["i3"]["code"]

    outvoted = tally_test(_test(), [wrong_target, wrong_target, _record()])
    kept = tally_test(_test(), [wrong_target, _record(), _record()])

    # The one annotator who kept the target does not get to judge its code.
    assert outvoted["check.target.wrong"] == 1
    assert not [key for key in outvoted if key.startswith("check.code")]
    assert (kept["check.target.correct"], kept["check.code.correct"]) == (1, 1)


def test_four_annotators_need_three_to_agree() -> None:
    not_a_check = _record({"i3": {"role": "no"}})
    missed = _record(added=[("check", 10, 10)])

    tied = tally_test(_test(), [not_a_check, not_a_check, _record(), _record()])
    carried = tally_test(_test(), [not_a_check, _record(), _record(), _record()])
    two_found = tally_test(_test(), [missed, missed, _record(), _record()])
    three_found = tally_test(_test(), [missed, missed, missed, _record()])

    assert (tied["check.split"], tied["check.tp"], tied["check.fp"]) == (1, 0, 0)
    assert carried["check.tp"] == 1
    assert (two_found["check.fn"], three_found["check.fn"]) == (0, 1)


def test_method_and_status_code_abstentions_are_scored_like_paths() -> None:
    tally = tally_test(
        _test(),
        [
            _record({"i2": {"verb": "undeterminable"}, "i3": {"code": "found"}}),
            _record({"i2": {"verb": "undeterminable"}, "i3": {"code": "found"}}),
            _record({"i2": {"verb": "found"}, "i3": {"code": "undeterminable"}}),
        ],
    )

    attributes = rates(tally)["attributes"]
    verb, code = attributes["dispatch.verb"], attributes["check.code"]
    assert (verb["unresolved_undeterminable"], verb["justified_abstention"]) == (1, 1.0)
    assert (verb["precision"], verb["recall"]) == (None, None)
    assert (code["unresolved_readable"], code["recall"]) == (1, 0.0)
    assert code["precision"] is None


def test_tests_calling_code_not_shown_are_counted_by_majority() -> None:
    hidden, shown = _record(hidden=True), _record()

    tallies = tally_test(_test(), [hidden, hidden, shown]) + tally_test(
        _test(), [hidden, shown, shown]
    )

    assert rates(tallies)["tests_calling_http_code_not_shown"] == 1


def test_an_unresolved_value_is_a_miss_only_when_it_was_readable() -> None:
    readable = tally_test(
        _test(),
        [
            _record({"i2": {"path": "found"}}),
            _record({"i2": {"path": "found"}}),
            _record({"i2": {"path": "undeterminable"}}),
        ],
    )
    unreadable = tally_test(_test(), [_record({"i2": {"path": "undeterminable"}})])
    reported = tally_test(
        _test(),
        [_record({"i2": {"path": "partial"}}), _record({"i2": {"path": "partial"}})],
    )

    path = rates(readable + unreadable + reported + tally_test(_test(), [_record()]))[
        "attributes"
    ]["dispatch.path"]

    assert path == {
        "correct": 1,
        "partial": 1,
        "wrong": 0,
        "unresolved_readable": 1,
        "unresolved_undeterminable": 1,
        "split": 0,
        # One of the two reported paths is right; the unresolved ones are no claim.
        "precision": 0.5,
        # One of the three readable paths was recovered.
        "recall": 0.3333,
        "justified_abstention": 0.5,
    }


def test_missed_items_count_what_a_majority_added_in_a_region() -> None:
    def fn(*added: list[tuple[Any, ...]]) -> tuple[int, int]:
        tally = tally_test(_test(), [_record(added=items) for items in added])
        return tally["check.fn"], tally["dispatch.fn"]

    # Overlapping selections are one region; a lone annotator is outvoted.
    assert fn([("check", 10, 12)], [("check", 12, 13)], []) == (1, 0)
    assert fn([("check", 10, 12)], [], []) == (0, 0)
    # Two of three found two checks there.
    assert fn(
        [("check", 10, 10), ("check", 11, 11)],
        [("check", 10, 11), ("check", 11, 12)],
        [("check", 10, 12)],
    ) == (2, 0)
    # Selections chained by overlap are one region.
    assert fn([("check", 10, 12)], [("check", 12, 14)], [("check", 14, 16)]) == (1, 0)
    # Adjacent lines do not overlap, so each annotator stands alone.
    assert fn([("check", 10, 11)], [("check", 12, 13)], []) == (0, 0)
    # The same lines of another method are another region.
    assert fn([("check", 10, 10, "u0")], [("check", 10, 10, "u1")], []) == (0, 0)
    # Separate lines, and separate kinds, are separate regions.
    assert fn([("check", 10, 10)], [("check", 20, 20)], []) == (0, 0)
    assert fn([("check", 10, 10)], [("dispatch", 10, 10)], [("dispatch", 10, 10)]) == (
        0,
        1,
    )


def _helper_run(run: int) -> list[dict[str, Any]]:
    """One run of a helper whose dispatch and check sit on lines 20 and 21."""
    return [
        _item(f"d{run}", "dispatch", "perform", 20, step=run),
        _item(f"c{run}", "check", "isOk", 21, step=run),
    ]


def _helper_test(runs: int, **fields: Any) -> dict[str, Any]:
    items = [item for run in range(1, runs + 1) for item in _helper_run(run)]
    return {**_test(step_count=runs, **fields), "items": items}


def _helper_record(
    runs: int,
    changes: dict[str, dict[str, str]] | None = None,
    *,
    added: list[tuple[str, int, str]] | None = None,
) -> dict[str, Any]:
    """An all-correct record of `_helper_test`, with `added` as (kind, line, step)."""
    items: dict[str, dict[str, dict[str, str]]] = {}
    for run in range(1, runs + 1):
        items[f"d{run}"] = {
            "role": {"a": "yes"},
            "verb": {"a": "correct"},
            "path": {"a": "correct"},
        }
        items[f"c{run}"] = {
            "role": {"a": "yes"},
            "target": {"a": "correct"},
            "code": {"a": "correct"},
            "step": {"a": "correct"},
        }
    for item_id, claims in (changes or {}).items():
        for claim, answer in claims.items():
            items[item_id][claim] = {"a": answer}
    return {
        "hiddenHttp": False,
        "notes": "",
        "done": True,
        "items": items,
        "added": [
            {
                "id": f"a{n}",
                "kind": kind,
                "unit": "u0",
                "start": line,
                "end": line,
                "step": step,
            }
            for n, (kind, line, step) in enumerate(added or [], 1)
        ],
    }


def _extraction(
    test: dict[str, Any], *records: dict[str, Any], remove_duplicates: bool
) -> dict[str, Any]:
    bundle = _bundle(test)
    scores = score(
        bundle,
        _SAMPLE,
        _annotations(bundle, *([record] for record in records)),
        bootstrap_samples=50,
        remove_duplicates=remove_duplicates,
    )
    extraction: dict[str, Any] = scores["overall"]["extraction"]
    return extraction


def test_a_call_site_run_several_times_is_scored_once_without_duplicates() -> None:
    # The helper's check is rejected on each of its three runs.
    rejected = {f"c{run}": {"role": "no"} for run in (1, 2, 3)}
    records = [_helper_record(3, rejected), _helper_record(3, rejected)]

    kept = _extraction(_helper_test(3), *records, remove_duplicates=False)
    removed = _extraction(_helper_test(3), *records, remove_duplicates=True)

    assert (kept["dispatch"]["tp"], kept["check"]["fp"]) == (3, 3)
    assert (removed["dispatch"]["tp"], removed["check"]["fp"]) == (1, 1)
    assert (kept["all"]["precision"], removed["all"]["precision"]) == (0.5, 0.5)


def test_the_first_run_of_a_duplicated_call_site_is_the_one_scored() -> None:
    first_wrong = [_helper_record(2, {"c1": {"role": "no"}}) for _ in range(2)]
    later_wrong = [_helper_record(2, {"c2": {"role": "no"}}) for _ in range(2)]

    first = _extraction(_helper_test(2), *first_wrong, remove_duplicates=True)
    later = _extraction(_helper_test(2), *later_wrong, remove_duplicates=True)

    assert (first["check"]["tp"], first["check"]["fp"]) == (0, 1)
    assert (later["check"]["tp"], later["check"]["fp"]) == (1, 0)


def test_a_run_in_no_step_gives_way_to_the_first_run_in_a_step() -> None:
    def check(item_id: str, step: int | None) -> dict[str, Any]:
        return _item(item_id, "check", "isOk", 21, step=step)

    test = {**_test(), "items": [check("c1", None), check("c2", 2), check("c3", 3)]}
    accepted = {
        "role": {"a": "yes"},
        "target": {"a": "correct"},
        "code": {"a": "correct"},
        "step": {"a": "correct"},
    }
    answers = {"c1": {"role": {"a": "no"}}, "c2": accepted, "c3": {"role": {"a": "no"}}}
    record = {**_record(), "items": answers}

    check_rates = _extraction(test, record, remove_duplicates=True)["check"]
    unattached = {**test, "items": [check("c1", None), check("c3", None)]}
    first = _extraction(unattached, record, remove_duplicates=True)["check"]

    assert (check_rates["tp"], check_rates["fp"]) == (1, 0)
    # With no run in a step, the first run stands.
    assert (first["tp"], first["fp"]) == (0, 1)


def test_an_api_test_is_found_by_a_later_run_of_a_duplicated_dispatch() -> None:
    later_run_only = _helper_record(2, {"d1": {"role": "no"}})
    bundle = _bundle(_helper_test(2))

    scores = score(
        bundle,
        _SAMPLE,
        _annotations(bundle, [later_run_only]),
        bootstrap_samples=50,
        remove_duplicates=True,
    )

    assert scores["overall"]["extraction"]["dispatch"]["fp"] == 1
    assert scores["overall"]["api_test"]["yes"] == 1


def test_calls_differing_in_kind_method_position_or_origin_are_not_duplicates() -> None:
    base = _item("i1", "builder", "header", 5)
    other_kind = {**base, "id": "i2", "kind": "check"}
    other_method = {**base, "id": "i3", "method_name": "param"}
    other_column = {**base, "id": "i4", "span": {**base["span"], "end_column": 50}}
    other_origin = {**base, "id": "i5", "origin_signature": "u()"}
    other_class = {**base, "id": "i6", "origin_class": "p.D"}
    repeat = {**base, "id": "i7", "step": 2}
    items = [
        base,
        other_kind,
        other_method,
        other_column,
        other_origin,
        other_class,
        repeat,
    ]
    test = {**_test(), "items": items}
    answers = {item["id"]: {"role": {"a": "no"}} for item in items}
    record = {**_record(), "items": answers}
    bundle = _bundle(test)

    scores = score(
        bundle,
        _SAMPLE,
        _annotations(bundle, [record]),
        bootstrap_samples=50,
        remove_duplicates=True,
    )

    assert scores["duplicates"]["extracted_items"] == 1
    assert scores["overall"]["extraction"]["all"]["fp"] == 6


def test_added_items_repeated_for_later_steps_count_once_without_duplicates() -> None:
    # One annotator marks the helper's missed check for every run, the other for
    # the first run alone.
    every_run = _helper_record(
        3, added=[("check", 22, "1"), ("check", 22, "2"), ("check", 22, "3")]
    )
    first_run = _helper_record(3, added=[("check", 22, "1")])

    def fn(*records: dict[str, Any], remove_duplicates: bool) -> int:
        check: dict[str, int] = _extraction(
            _helper_test(3), *records, remove_duplicates=remove_duplicates
        )["check"]
        return check["fn"]

    assert fn(every_run, every_run, remove_duplicates=False) == 3
    assert fn(every_run, every_run, remove_duplicates=True) == 1
    assert fn(every_run, first_run, remove_duplicates=True) == 1


def test_added_items_of_the_first_step_on_the_same_lines_all_stay() -> None:
    def fn(*added: tuple[str, int, str]) -> tuple[int, int]:
        records = [_helper_record(3, added=list(added)) for _ in range(2)]
        extraction = _extraction(_helper_test(3), *records, remove_duplicates=True)
        return extraction["check"]["fn"], extraction["dispatch"]["fn"]

    # Two checks on one line, each marked for two steps: the first step's two stay.
    assert fn(
        ("check", 22, "2"), ("check", 22, "2"), ("check", 22, "3"), ("check", 22, "3")
    ) == (2, 0)
    # The lowest step is the first, wherever it was added and above an unnumbered one.
    assert fn(("check", 22, "10"), ("check", 22, "9"), ("check", 22, "missed")) == (
        1,
        0,
    )
    assert fn(("check", 22, "missed"), ("check", 22, "none")) == (1, 0)
    # Other lines, and another kind on the same lines, are not repeats.
    assert fn(("check", 22, "1"), ("check", 23, "2"), ("builder", 22, "2")) == (2, 0)
    # An added dispatch has no step to tell a repeat by.
    assert fn(("dispatch", 22, ""), ("dispatch", 22, "")) == (0, 2)


def test_duplicates_are_counted_whether_or_not_they_are_removed() -> None:
    bundle = _bundle(_helper_test(3), _test("p::C::u()"))
    repeated = _helper_record(3, added=[("check", 22, "1"), ("check", 22, "2")])
    annotations = _annotations(
        bundle, [repeated, _record()], [_helper_record(3), _record()]
    )

    kept, removed = (
        score(
            bundle,
            _SAMPLE,
            annotations,
            bootstrap_samples=50,
            remove_duplicates=remove_duplicates,
        )
        for remove_duplicates in (False, True)
    )

    counted = {"extracted_items": 4, "added_items": {"author1": 1, "author2": 0}}
    assert kept["duplicates"] == {"removed": False, **counted}
    assert removed["duplicates"] == {"removed": True, **counted}
    # Agreement and each annotator's own rates read the same items as the rest.
    assert kept["agreement"]["claims"]["check.role"]["units"] == 4
    assert removed["agreement"]["claims"]["check.role"]["units"] == 2
    assert kept["per_annotator"]["author1"]["check"]["fn"] == 2
    assert removed["per_annotator"]["author1"]["check"]["fn"] == 1
    assert removed["tests"]["multi_step"] == 1


def test_recall_falls_with_missed_items() -> None:
    tally = tally_test(
        _test(),
        [_record(added=[("check", 10, 10)]), _record(added=[("check", 10, 10)])],
    )

    assert rates(tally)["extraction"]["check"] == {
        "tp": 1,
        "fp": 0,
        "fn": 1,
        "split": 0,
        "precision": 1.0,
        "recall": 0.5,
        "f1": 0.6667,
    }


def test_a_test_is_an_api_test_when_a_majority_keeps_or_adds_a_dispatch() -> None:
    rejected = _record({item: {"role": "no"} for item in ("i1", "i2", "i3")})

    tally = tally_test(_test(), [rejected, rejected, _record()])

    assert tally["api_test.no"] == 1 and rates(tally)["api_test"]["precision"] == 0.0
    assert [tally[f"{kind}.fp"] for kind in ("builder", "dispatch", "check")] == [
        1,
        1,
        1,
    ]
    # Gerbil's dispatch was wrong, but the test does send a request it missed.
    elsewhere = _record({"i2": {"role": "no"}}, added=[("dispatch", 10, 10)])
    tally = tally_test(_test(), [elsewhere, elsewhere, _record()])
    assert tally["dispatch.fp"] == 1 and tally["dispatch.fn"] == 1
    assert tally["api_test.yes"] == 1


def test_each_annotator_finds_an_api_test_by_any_dispatch_they_keep() -> None:
    second = {"id": "i4", "kind": "dispatch", "step": 2}
    test = {**_test(), "items": [_BUILDER, _DISPATCH, second, _CHECK]}

    def record(first_role: str, second_role: str) -> dict[str, Any]:
        answers = _record({"i2": {"role": first_role}})
        answers["items"]["i4"] = {
            "role": {"a": second_role},
            "verb": {"a": "correct"},
            "path": {"a": "correct"},
        }
        return answers

    # Two of three annotators keep a dispatch, though not the same one.
    tally = tally_test(
        test, [record("yes", "no"), record("no", "yes"), record("no", "no")]
    )
    assert tally["dispatch.tp"] == 0 and tally["api_test.yes"] == 1

    split = tally_test(test, [record("yes", "no"), record("no", "no")])
    assert split["api_test.split"] == 1
    assert rates(split)["api_test"]["precision"] is None


def test_only_tests_every_annotator_completed_are_scored() -> None:
    bundle = _bundle(_test("p::C::a()"), _test("p::C::b()"), _test("p::C::c()"))
    annotations = _annotations(
        bundle,
        [_record(), _record(), _record()],
        [_record(), _record(done=False), _record()],
    )
    del annotations[0]["tests"]["p::C::c()"]

    scores = score(bundle, _SAMPLE, annotations, bootstrap_samples=50)

    assert scores["tests"] == {
        "in_bundle": 3,
        "scored": 1,
        "not_completed_by_every_annotator": 2,
        "projects": 1,
        "single_step": 1,
        "multi_step": 0,
    }
    assert scores["annotators"] == ["author1", "author2"]
    assert scores["overall"]["extraction"]["all"]["tp"] == 3

    annotations[1]["tests"]["p::C::a()"]["done"] = False
    with pytest.raises(ScoringError, match="no test was completed"):
        score(bundle, _SAMPLE, annotations, bootstrap_samples=50)


def test_rates_are_split_by_step_count_and_by_where_the_http_code_sits() -> None:
    bundle = _bundle(
        _test("p::C::single()"),
        _test("q::C::multi()", dataset="q", step_count=2, in_helper=True),
        _test("q::C::multi2()", dataset="q", step_count=3, in_helper=True),
    )
    not_a_check = _record({"i3": {"role": "no"}})
    annotations = _annotations(bundle, [_record(), not_a_check, not_a_check])

    scores = score(bundle, _SAMPLE, annotations, bootstrap_samples=50)

    # 3 checks: the single-step one right, both multi-step ones wrong.
    assert scores["overall"]["extraction"]["check"]["precision"] == 0.3333
    by_steps = scores["by_step_count"]
    assert by_steps["single_step"]["extraction"]["check"]["precision"] == 1.0
    assert by_steps["multi_step"]["extraction"]["check"]["precision"] == 0.0
    assert (scores["tests"]["single_step"], scores["tests"]["multi_step"]) == (1, 2)
    by_location = scores["by_http_location"]
    assert by_location["test_method_only"]["extraction"]["check"]["precision"] == 1.0
    assert by_location["helper_or_fixture"]["extraction"]["check"]["precision"] == 0.0


def _projects(count: int) -> dict[str, Any]:
    """One test per project."""
    return _bundle(*[_test(f"p{n}::C::t()", dataset=f"p{n}") for n in range(count)])


def test_intervals_resample_projects_and_reproduce() -> None:
    bundle = _projects(12)
    wrong = _record({"i3": {"role": "no"}})
    annotations = _annotations(bundle, [wrong] * 3 + [_record()] * 9)

    first = score(bundle, _SAMPLE, annotations, bootstrap_samples=400)
    second = score(bundle, _SAMPLE, annotations, bootstrap_samples=400)

    check = first["overall"]["extraction"]["check"]
    low, high = check["ci95"]["precision"]
    assert check["precision"] == 0.75 and low < 0.75 < high and 0 <= low and high <= 1
    assert first == second
    # Nothing varies across projects for builders.
    assert first["overall"]["extraction"]["builder"]["ci95"]["precision"] == [1.0, 1.0]
    # Too few resamples to take percentiles of.
    few = score(bundle, _SAMPLE, annotations, bootstrap_samples=10)
    assert few["overall"]["extraction"]["check"]["ci95"]["precision"] is None


def test_too_few_projects_give_no_interval() -> None:
    assert MIN_BOOTSTRAP_PROJECTS == 10
    bundle = _projects(9)
    wrong = _record({"i3": {"role": "no"}})
    annotations = _annotations(bundle, [wrong] * 3 + [_record()] * 6)

    scores = score(bundle, _SAMPLE, annotations, bootstrap_samples=400)

    # Resampling so few projects, or one, would report a falsely tight interval.
    assert scores["overall"]["extraction"]["check"]["precision"] == 0.6667
    assert scores["overall"]["extraction"]["check"]["ci95"] is None


def test_alpha_matches_a_published_example() -> None:
    # Krippendorff's worked example for nominal data with missing ratings:
    # three observers, fifteen units, alpha = 0.691.
    observers = [
        "*****3412113 3*3".replace(" ", ""),
        "1*213343*******",
        "**2134 4*2113 3*4".replace(" ", ""),
    ]
    units = [
        [ratings[unit] for ratings in observers if ratings[unit] != "*"]
        for unit in range(15)
    ]

    alpha = krippendorff_alpha(units)

    assert alpha is not None and round(alpha, 3) == 0.691


def test_alpha_is_undefined_without_variation_and_one_for_full_agreement() -> None:
    assert krippendorff_alpha([["yes", "yes"], ["yes", "yes"]]) is None
    assert krippendorff_alpha([["yes"], ["no"]]) is None
    assert krippendorff_alpha([["yes", "yes"], ["no", "no"]]) == 1.0
    assert percent_agreement([["yes", "yes", "no"], ["no", "no"]]) == 0.5
    assert percent_agreement([["yes"]]) is None


def test_agreement_covers_each_claim_and_the_missed_items() -> None:
    bundle = _bundle(_test("p::C::a()"), _test("p::C::b()"))
    annotations = _annotations(
        bundle,
        [_record(added=[("check", 10, 10)]), _record({"i3": {"role": "no"}})],
        [_record(added=[("check", 10, 11)]), _record()],
        [_record(), _record({"i3": {"role": "no"}})],
    )

    agreement = score(bundle, _SAMPLE, annotations, bootstrap_samples=50)["agreement"]

    role = agreement["claims"]["check.role"]
    # Ratings yes/yes/yes and no/yes/no: 2 of 6 coincidences disagree, 3.2 expected.
    assert role == {"units": 2, "alpha": 0.375, "percent_agreement": 0.6667}
    assert "api_test" not in agreement["claims"]
    # Only the annotators who kept the check judged its target.
    assert agreement["claims"]["check.target"]["units"] == 1
    # Pairs (1,2), (1,3), (2,3) share 1, 0 and 0 of their added checks.
    assert agreement["missed_items"]["check"] == {"regions": 1, "pairwise_f1": 0.3333}
    assert agreement["missed_items"]["dispatch"] == {"regions": 0, "pairwise_f1": None}


def test_one_annotator_is_scored_without_agreement() -> None:
    bundle = _bundle(_test())
    annotations = _annotations(bundle, [_record({"i3": {"role": "no"}})])

    scores = score(bundle, _SAMPLE, annotations, bootstrap_samples=50)

    assert scores["agreement"] is None
    assert scores["per_annotator"]["author1"]["check"]["precision"] == 0.0
    assert scores["overall"]["extraction"]["check"]["precision"] == 0.0


def test_annotators_are_scored_alone_to_show_their_spread() -> None:
    bundle = _bundle(_test())
    annotations = _annotations(
        bundle, [_record({"i3": {"role": "no"}})], [_record()], [_record()]
    )

    per_annotator = score(bundle, _SAMPLE, annotations, bootstrap_samples=50)[
        "per_annotator"
    ]

    assert [
        per_annotator[name]["all"]["precision"] for name in sorted(per_annotator)
    ] == [
        0.6667,
        1.0,
        1.0,
    ]


def _write(directory: Path, name: str, annotation: dict[str, Any]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(annotation), encoding="utf-8")
    return path


def test_exports_are_read_in_annotator_order_and_checked(tmp_path: Path) -> None:
    bundle = _bundle(_test())
    first, second = _annotations(bundle, [_record()], [_record()])
    paths = [_write(tmp_path, "b.json", second), _write(tmp_path, "a.json", first)]

    assert [a["annotator"] for a in read_annotations(paths, bundle)] == [
        "author1",
        "author2",
    ]

    tenth = _write(tmp_path, "c.json", {**first, "annotator": "author10"})
    assert [a["annotator"] for a in read_annotations([tenth, *paths], bundle)] == [
        "author1",
        "author2",
        "author10",
    ]

    with pytest.raises(ScoringError, match="copy.json: a second export of author1"):
        read_annotations([*paths, _write(tmp_path, "copy.json", first)], bundle)
    with pytest.raises(ScoringError, match="other.json: exported from another bundle"):
        read_annotations(
            [_write(tmp_path, "other.json", {**first, "bundle_digest": "x"})], bundle
        )
    with pytest.raises(ScoringError, match="named.json: annotator id is not author<N>"):
        read_annotations(
            [_write(tmp_path, "named.json", {**first, "annotator": "Ada Lovelace"})],
            bundle,
        )


def _sample_dir(tmp_path: Path, bundle: dict[str, Any]) -> Path:
    sample_dir = tmp_path / "validation"
    _write(sample_dir, BUNDLE_FILE, bundle)
    _write(sample_dir, SAMPLE_FILE, _SAMPLE)
    return sample_dir


def test_a_bundle_of_another_sample_is_not_scored() -> None:
    bundle = _bundle(_test())
    payload = {**_SAMPLE, "sample": {"digest": "other"}}

    with pytest.raises(ScoringError, match="built from another sample"):
        score(bundle, payload, _annotations(bundle, [_record()]))


def test_cli_scores_the_exports_and_writes_nothing_identifying(tmp_path: Path) -> None:
    bundle = _bundle(_test("p::C::a()"), _test("q::C::b()", dataset="q"))
    sample_dir = _sample_dir(tmp_path, bundle)
    exports = _annotations(
        bundle,
        [_record(), _record({"i3": {"role": "no"}})],
        [_record(), _record({"i3": {"role": "no"}})],
        [_record(), _record()],
    )
    for export in exports:
        _write(sample_dir / "annotations", f"{export['annotator']}.json", export)

    result = CliRunner().invoke(
        app,
        [
            "validation",
            "score",
            "--sample-dir",
            str(sample_dir),
            "--bootstrap-samples",
            "50",
        ],
    )

    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed["annotators"] == ["author1", "author2", "author3"]
    assert printed["extraction"]["check"]["precision"] == 0.5
    assert printed["api_test_precision"] == 1.0
    assert printed["duplicates"]["removed"] is False
    assert printed["file"] == SCORES_FILE and str(tmp_path) not in result.output
    written = (sample_dir / SCORES_FILE).read_text()
    scores = json.loads(written)
    assert scores["overall"]["extraction"]["check"]["fp"] == 1
    assert scores["agreement"]["claims"]["check.role"]["units"] == 2
    assert str(tmp_path) not in written
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}", written)


def test_cli_removes_duplicates_on_request(tmp_path: Path) -> None:
    bundle = _bundle(_helper_test(2))
    sample_dir = _sample_dir(tmp_path, bundle)
    rejected = {"c1": {"role": "no"}, "c2": {"role": "no"}}
    (export,) = _annotations(bundle, [_helper_record(2, rejected)])
    _write(sample_dir / "annotations", "author1.json", export)
    arguments = ["validation", "score", "--sample-dir", str(sample_dir)]

    kept = CliRunner().invoke(app, [*arguments, "--keep-duplicates"])
    kept_scores = json.loads((sample_dir / SCORES_FILE).read_text())
    removed = CliRunner().invoke(app, [*arguments, "--remove-duplicates"])
    removed_scores = json.loads((sample_dir / SCORES_FILE).read_text())

    assert kept.exit_code == 0 and removed.exit_code == 0, removed.output
    assert json.loads(kept.output)["duplicates"] == {
        "removed": False,
        "extracted_items": 2,
        "added_items": {"author1": 0},
    }
    assert json.loads(removed.output)["duplicates"]["removed"] is True
    assert kept_scores["overall"]["extraction"]["check"]["fp"] == 2
    assert removed_scores["overall"]["extraction"]["check"]["fp"] == 1


def test_cli_reports_exports_it_cannot_score(tmp_path: Path) -> None:
    bundle = _bundle(_test())
    sample_dir = _sample_dir(tmp_path, bundle)
    arguments = ["validation", "score", "--sample-dir", str(sample_dir)]

    empty = CliRunner().invoke(app, arguments)

    assert empty.exit_code == 1 and "no annotations to score" in empty.output

    (export,) = _annotations(bundle, [_record()])
    _write(tmp_path / "elsewhere", "named.json", {**export, "annotator": "Ada"})
    named = CliRunner().invoke(
        app, [*arguments, "--annotations", str(tmp_path / "elsewhere")]
    )

    assert named.exit_code == 1 and "annotator id is not author<N>" in named.output
    assert not (sample_dir / SCORES_FILE).exists()
