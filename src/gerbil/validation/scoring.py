"""Score annotators' exports against the bundle: precision and recall of the extracted
step items, accuracy of what Gerbil says about them, and inter-annotator agreement."""

from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import combinations, permutations
from pathlib import Path
from typing import Any

SCORES_FORMAT = 2
SCORES_FILE = "scores.json"
ANNOTATIONS_DIRECTORY = "annotations"
DEFAULT_BOOTSTRAP_SAMPLES = 2000
# Resampling fewer projects gives an interval narrower than the data supports;
# with one project it has no width at all.
MIN_BOOTSTRAP_PROJECTS = 10
_MIN_RESAMPLES = 40

KINDS = ("builder", "dispatch", "check")
_SCOPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("all", KINDS),
    *((kind, (kind,)) for kind in KINDS),
)
_ANNOTATOR_ID = re.compile(r"author([1-9]\d*)")
_SPLIT = "split"

# The claims the page asks about an item beyond its role, per kind.
ATTRIBUTE_CLAIMS: dict[str, tuple[str, ...]] = {
    "builder": ("part", "step"),
    "dispatch": ("verb", "path"),
    "check": ("target", "code", "step"),
}
# Claims about a value Gerbil either reported or left unresolved.
_VALUE_CLAIMS = frozenset({"dispatch.verb", "dispatch.path", "check.code"})

# A Counter: an outcome that never occurred reads as zero.
Counts = Mapping[str, int]


class ScoringError(Exception):
    pass


def read_annotations(
    paths: Sequence[Path], bundle: dict[str, Any]
) -> list[dict[str, Any]]:
    """The exports of one bundle, one per annotator, ordered by annotator id."""
    annotations: dict[str, dict[str, Any]] = {}
    for path in paths:
        annotation = json.loads(path.read_text(encoding="utf-8"))
        annotator = annotation["annotator"]
        if not _ANNOTATOR_ID.fullmatch(annotator):
            raise ScoringError(f"{path.name}: annotator id is not author<N>")
        if annotation["bundle_digest"] != bundle["bundle_digest"]:
            raise ScoringError(f"{path.name}: exported from another bundle")
        if annotator in annotations:
            raise ScoringError(f"{path.name}: a second export of {annotator}")
        annotations[annotator] = annotation
    return sorted(
        annotations.values(), key=lambda annotation: int(annotation["annotator"][6:])
    )


def _majority(votes: Sequence[str]) -> str | None:
    """The answer more than half the votes give, `split` without one, None unvoted."""
    if not votes:
        return None
    answer, count = Counter(votes).most_common(1)[0]
    return answer if count * 2 > len(votes) else _SPLIT


def _answers(item: dict[str, Any], record: dict[str, Any]) -> dict[str, str]:
    """One annotator's answers on an item, less those a later answer made moot.

    The page keeps such answers.
    """
    given = {
        claim: answer["a"] for claim, answer in record["items"][item["id"]].items()
    }
    if given["role"] == "no":
        return {"role": "no"}
    if given.get("target") == "wrong":
        given.pop("code", None)
    return given


def _call_site(item: dict[str, Any]) -> tuple[Any, ...]:
    span = item["span"]
    return (
        item["kind"],
        item["origin_class"],
        item["origin_signature"],
        item["method_name"],
        span["start_line"],
        span["start_column"],
        span["end_line"],
        span["end_column"],
    )


def _first_runs(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The items less each later run of a call site already seen.

    A helper called several times puts its calls into the sequence once per call.
    A run in no step gives way to the first run in one, the run an annotator's
    added items name.
    """
    first: dict[tuple[Any, ...], dict[str, Any]] = {}
    for item in items:
        kept = first.setdefault(_call_site(item), item)
        if kept["step"] is None and item["step"] is not None:
            first[_call_site(item)] = item
    return [item for item in items if first[_call_site(item)] is item]


def _step_rank(added: dict[str, Any]) -> tuple[int, int]:
    step = added["step"]
    return (0, int(step)) if step.isdigit() else (1, 0)


def _first_step_added(added: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The added items less those repeating, for a later step, one on the same lines.

    Annotators sometimes mark a repeated miss on its first step alone, so only that
    step compares across them. Items on the same lines for the same step are
    separate calls and all stay; so do repeats of an added dispatch, for which the
    page records no step.
    """
    by_lines: defaultdict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in added:
        by_lines[(item["kind"], item["unit"], item["start"], item["end"])].append(item)
    first_step = {
        lines: min(group, key=_step_rank)["step"] for lines, group in by_lines.items()
    }
    return [
        item
        for item in added
        if item["step"]
        == first_step[(item["kind"], item["unit"], item["start"], item["end"])]
    ]


def _without_duplicates(
    test: dict[str, Any], records: Sequence[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return (
        {**test, "items": _first_runs(test["items"])},
        [{**record, "added": _first_step_added(record["added"])} for record in records],
    )


def _missed_regions(
    records: Sequence[dict[str, Any]],
) -> dict[str, list[list[int]]]:
    """Per kind, each region annotators added items on, as per-annotator counts.

    Added items of one kind whose line ranges overlap in one unit are one region:
    annotators select lines, and two of them rarely select the same ones for a call.
    """
    added = sorted(
        (item["kind"], item["unit"], item["start"], item["end"], index)
        for index, record in enumerate(records)
        for item in record["added"]
    )
    regions: dict[str, list[list[int]]] = {kind: [] for kind in KINDS}
    current: tuple[str, str] | None = None
    current_end = 0
    for kind, unit, start, end, index in added:
        if (kind, unit) != current or start > current_end:
            regions[kind].append([0] * len(records))
            current, current_end = (kind, unit), end
        current_end = max(current_end, end)
        regions[kind][-1][index] += 1
    return regions


def _majority_count(counts: Sequence[int]) -> int:
    """The largest count more than half the annotators reached."""
    return sorted(counts, reverse=True)[len(counts) // 2]


def tally_test(
    test: dict[str, Any],
    records: Sequence[dict[str, Any]],
    *,
    remove_duplicates: bool = False,
) -> Counter[str]:
    """Consensus counts for one test, each annotator's record given in the same order."""
    tally: Counter[str] = Counter()
    hidden = _majority(["yes" if record["hiddenHttp"] else "no" for record in records])
    tally[f"hidden_http.{hidden}"] += 1
    # Gerbil called the test an API test. An annotator agrees by accepting one of its
    # dispatches or adding one Gerbil missed, and the majority of annotators decides.
    # Read before duplicates go: any run of a dispatch an annotator accepts counts.
    tally[f"api_test.{_majority([_sends_a_request(test, r) for r in records])}"] += 1
    if remove_duplicates:
        test, records = _without_duplicates(test, records)

    for item in test["items"]:
        kind = item["kind"]
        answers = [_answers(item, record) for record in records]
        role = _majority([given["role"] for given in answers])
        if role != "yes":
            tally[f"{kind}.{'fp' if role == 'no' else _SPLIT}"] += 1
            continue
        tally[f"{kind}.tp"] += 1
        verdicts: dict[str, str] = {}
        for claim in ATTRIBUTE_CLAIMS[kind]:
            # A status code is judged only on what the majority agrees is a
            # status check, not by the annotators it outvoted.
            if claim == "code" and verdicts.get("target") != "correct":
                continue
            verdict = _majority([given[claim] for given in answers if claim in given])
            if verdict is not None:
                verdicts[claim] = verdict
                tally[f"{kind}.{claim}.{verdict}"] += 1

    for kind, regions in _missed_regions(records).items():
        tally[f"{kind}.fn"] += sum(_majority_count(counts) for counts in regions)
    return tally


def _sends_a_request(test: dict[str, Any], record: dict[str, Any]) -> str:
    """Whether one annotator found the test dispatching a request to the application."""
    accepted = any(
        _answers(item, record)["role"] == "yes"
        for item in test["items"]
        if item["kind"] == "dispatch"
    )
    added = any(item["kind"] == "dispatch" for item in record["added"])
    return "yes" if accepted or added else "no"


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _extraction(counts: Counts, kinds: Sequence[str]) -> dict[str, Any]:
    tp, fp, fn, split = (
        sum(counts[f"{kind}.{outcome}"] for kind in kinds)
        for outcome in ("tp", "fp", "fn", _SPLIT)
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "split": split,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn),
    }


def _attribute(counts: Counts, claim: str) -> dict[str, Any]:
    correct, wrong, split = (
        counts[f"{claim}.{answer}"] for answer in ("correct", "wrong", _SPLIT)
    )
    if claim not in _VALUE_CLAIMS:
        return {
            "correct": correct,
            "wrong": wrong,
            "split": split,
            "accuracy": _ratio(correct, correct + wrong),
        }
    partial, found, undeterminable = (
        counts[f"{claim}.{answer}"] for answer in ("partial", "found", "undeterminable")
    )
    reported = correct + partial + wrong
    return {
        "correct": correct,
        "partial": partial,
        "wrong": wrong,
        "unresolved_readable": found,
        "unresolved_undeterminable": undeterminable,
        "split": split,
        # An unresolved value is no claim, so it is not in precision; one an
        # annotator could read is a miss, so it is in recall.
        "precision": _ratio(correct, reported),
        "recall": _ratio(correct, reported + found),
        "justified_abstention": _ratio(undeterminable, found + undeterminable),
    }


def rates(counts: Counts) -> dict[str, Any]:
    """Every reported rate, from summed tallies."""
    api_yes, api_no = counts["api_test.yes"], counts["api_test.no"]
    return {
        "api_test": {
            "yes": api_yes,
            "no": api_no,
            "split": counts[f"api_test.{_SPLIT}"],
            "precision": _ratio(api_yes, api_yes + api_no),
        },
        "extraction": {scope: _extraction(counts, kinds) for scope, kinds in _SCOPES},
        "attributes": {
            f"{kind}.{claim}": _attribute(counts, f"{kind}.{claim}")
            for kind in KINDS
            for claim in ATTRIBUTE_CLAIMS[kind]
        },
        "tests_calling_http_code_not_shown": counts["hidden_http.yes"],
    }


def _total(tallies: Iterable[Counts]) -> Counter[str]:
    total: Counter[str] = Counter()
    for tally in tallies:
        total.update(tally)
    return total


def _project_tallies(
    tests: Sequence[dict[str, Any]], tallies: Mapping[str, Counts]
) -> dict[str, Counter[str]]:
    """Each project's scored tests, summed: the unit the intervals resample."""
    grouped: dict[str, list[Counts]] = {}
    for test in tests:
        grouped.setdefault(test["dataset"], []).append(tallies[test["id"]])
    return {project: _total(group) for project, group in sorted(grouped.items())}


def _percentile_interval(values: list[float]) -> list[float] | None:
    if len(values) < _MIN_RESAMPLES:
        return None
    values.sort()
    return [values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]]


def _bootstrap_intervals(
    by_project: Mapping[str, Counts], samples: int, seed: str
) -> dict[str, dict[str, list[float] | None]] | None:
    """95% intervals of extraction precision and recall, resampling whole projects.

    A project's tests share idioms, so its items are not independent draws.
    """
    projects = sorted(by_project)
    if len(projects) < MIN_BOOTSTRAP_PROJECTS:
        return None
    generator = random.Random(seed)
    draws: defaultdict[tuple[str, str], list[float]] = defaultdict(list)
    for _ in range(samples):
        counts = _total(
            by_project[project]
            for project in generator.choices(projects, k=len(projects))
        )
        for scope, kinds in _SCOPES:
            result = _extraction(counts, kinds)
            for rate in ("precision", "recall"):
                if result[rate] is not None:
                    draws[(scope, rate)].append(result[rate])
    return {
        scope: {
            rate: _percentile_interval(draws[(scope, rate)])
            for rate in ("precision", "recall")
        }
        for scope, _ in _SCOPES
    }


def _overall_rates(
    by_project: Mapping[str, Counts], samples: int, seed: str
) -> dict[str, Any]:
    result = rates(_total(by_project.values()))
    intervals = _bootstrap_intervals(by_project, samples, seed)
    for scope, extraction in result["extraction"].items():
        extraction["ci95"] = None if intervals is None else intervals[scope]
    return result


def krippendorff_alpha(units: Iterable[Sequence[str]]) -> float | None:
    """Nominal Krippendorff's alpha; a unit may have any number of ratings.

    None when the ratings never vary, where chance agreement is undefined.
    """
    coincidences: defaultdict[tuple[str, str], float] = defaultdict(float)
    for values in units:
        if len(values) < 2:
            continue
        for first, second in permutations(values, 2):
            coincidences[(first, second)] += 1 / (len(values) - 1)
    totals: defaultdict[str, float] = defaultdict(float)
    for (first, _), weight in coincidences.items():
        totals[first] += weight
    pairable = sum(totals.values())
    expected = sum(
        totals[first] * totals[second] for first, second in permutations(totals, 2)
    )
    if not expected:
        return None
    observed = sum(
        weight for (first, second), weight in coincidences.items() if first != second
    )
    return round(1 - (pairable - 1) * observed / expected, 4)


def percent_agreement(units: Iterable[Sequence[str]]) -> float | None:
    pairs = [pair for values in units for pair in combinations(values, 2)]
    return _ratio(sum(first == second for first, second in pairs), len(pairs))


def _pairwise_f1(regions: Sequence[Sequence[int]], annotators: int) -> float | None:
    """Mean F1 of each annotator pair's added items, matched region by region."""
    scores = [
        _ratio(
            2 * sum(min(counts[a], counts[b]) for counts in regions),
            sum(counts[a] + counts[b] for counts in regions),
        )
        for a, b in combinations(range(annotators), 2)
    ]
    defined = [score for score in scores if score is not None]
    return _ratio(sum(defined), len(defined))


def _agreement(
    tests: Sequence[dict[str, Any]],
    records_by_test: dict[str, list[dict[str, Any]]],
    remove_duplicates: bool,
) -> dict[str, Any]:
    ratings: dict[str, list[list[str]]] = {}
    missed: dict[str, list[list[int]]] = {scope: [] for scope, _ in _SCOPES}
    for test in tests:
        records: Sequence[dict[str, Any]] = records_by_test[test["id"]]
        if remove_duplicates:
            test, records = _without_duplicates(test, records)
        for item in test["items"]:
            answers = [_answers(item, record) for record in records]
            for claim in ("role", *ATTRIBUTE_CLAIMS[item["kind"]]):
                ratings.setdefault(f"{item['kind']}.{claim}", []).append(
                    [given[claim] for given in answers if claim in given]
                )
        for kind, regions in _missed_regions(records).items():
            missed[kind].extend(regions)
            missed["all"].extend(regions)
    annotators = len(next(iter(records_by_test.values())))
    return {
        "claims": {
            claim: {
                "units": sum(len(values) >= 2 for values in units),
                "alpha": krippendorff_alpha(units),
                "percent_agreement": percent_agreement(units),
            }
            for claim, units in sorted(ratings.items())
        },
        # No fixed set of units to rate, so agreement is the annotators' mutual F1.
        "missed_items": {
            scope: {
                "regions": len(regions),
                "pairwise_f1": _pairwise_f1(regions, annotators),
            }
            for scope, regions in missed.items()
        },
    }


def score(
    bundle: dict[str, Any],
    sample_payload: dict[str, Any],
    annotations: Sequence[dict[str, Any]],
    *,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    remove_duplicates: bool = False,
) -> dict[str, Any]:
    """Score the tests every annotator completed.

    Ground truth is the annotators' majority, claim by claim. `remove_duplicates`
    scores a call site run several times, and what annotators added on it, once.
    """
    if not annotations:
        raise ScoringError("no annotations to score")
    if bundle["sample_digest"] != sample_payload["sample"]["digest"]:
        raise ScoringError("the bundle was built from another sample")
    records_by_test: dict[str, list[dict[str, Any]]] = {}
    for test in bundle["tests"]:
        records = [annotation["tests"].get(test["id"]) for annotation in annotations]
        if all(record is not None and record["done"] for record in records):
            records_by_test[test["id"]] = records
    scored = [test for test in bundle["tests"] if test["id"] in records_by_test]
    if not scored:
        raise ScoringError("no test was completed by every annotator")

    annotators = [annotation["annotator"] for annotation in annotations]
    duplicate_items = 0
    duplicate_added = dict.fromkeys(annotators, 0)
    for test in scored:
        records = records_by_test[test["id"]]
        first_test, first_records = _without_duplicates(test, records)
        duplicate_items += len(test["items"]) - len(first_test["items"])
        for annotator, record, first_record in zip(
            annotators, records, first_records, strict=True
        ):
            duplicate_added[annotator] += len(record["added"]) - len(
                first_record["added"]
            )

    tallies: dict[str, Counts] = {
        test["id"]: tally_test(
            test, records_by_test[test["id"]], remove_duplicates=remove_duplicates
        )
        for test in scored
    }
    by_project = _project_tallies(scored, tallies)
    single_step = [test for test in scored if test["step_count"] == 1]
    multi_step = [test for test in scored if test["step_count"] > 1]

    def group_rates(tests: Sequence[dict[str, Any]]) -> dict[str, Any]:
        return rates(_total(tallies[test["id"]] for test in tests))

    return {
        "format": SCORES_FORMAT,
        "bundle_digest": bundle["bundle_digest"],
        "sample_digest": bundle["sample_digest"],
        "annotators": annotators,
        "tests": {
            "in_bundle": len(bundle["tests"]),
            "scored": len(scored),
            "not_completed_by_every_annotator": len(bundle["tests"]) - len(scored),
            "projects": len(by_project),
            "single_step": len(single_step),
            "multi_step": len(multi_step),
        },
        "duplicates": {
            "removed": remove_duplicates,
            "extracted_items": duplicate_items,
            "added_items": duplicate_added,
        },
        "overall": _overall_rates(by_project, bootstrap_samples, bundle["seed"]),
        "by_step_count": {
            "single_step": group_rates(single_step),
            "multi_step": group_rates(multi_step),
        },
        "by_http_location": {
            "test_method_only": group_rates(
                [test for test in scored if not test["has_helper_or_fixture_http"]]
            ),
            "helper_or_fixture": group_rates(
                [test for test in scored if test["has_helper_or_fixture_http"]]
            ),
        },
        "agreement": (
            _agreement(scored, records_by_test, remove_duplicates)
            if len(annotators) > 1
            else None
        ),
        # Each annotator taken alone as ground truth, to show their spread.
        "per_annotator": {
            annotator: rates(
                _total(
                    tally_test(
                        test,
                        [records_by_test[test["id"]][index]],
                        remove_duplicates=remove_duplicates,
                    )
                    for test in scored
                )
            )["extraction"]
            for index, annotator in enumerate(annotators)
        },
    }


def write_scores(scores: dict[str, Any], sample_dir: Path) -> None:
    (sample_dir / SCORES_FILE).write_text(
        json.dumps(scores, indent=2) + "\n", encoding="utf-8"
    )
