"""Turn sampled tests into the annotation bundle: each test's source, cut to the methods
that run with it, and the step items the model extracted, placed on that source."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gerbil.analysis.schema import (
    ApiSequenceStep,
    HttpCallSite,
    LifecyclePhase,
    SequenceStepKind,
    TestMethodAnalysis,
)
from gerbil.validation.java_source import JavaFile, JavaRepo, MethodSource
from gerbil.validation.sampling import TestCandidate

BUNDLE_FORMAT = 1
BUNDLE_FILE = "bundle.json"

_ITEM_KIND: dict[SequenceStepKind, str] = {
    SequenceStepKind.REQUEST_BUILD: "builder",
    SequenceStepKind.HTTP_REQUEST: "dispatch",
    SequenceStepKind.RESPONSE_CHECK: "check",
}

# Helpers the model extracted nothing from are shown so annotators can find what it
# missed there; these bounds keep a test with a deep helper tree readable.
_CALLEE_DEPTH = 2
_CALLEE_LIMIT = 8
_SUPERCLASS_DEPTH = 3


class BundleError(Exception):
    pass


@dataclass
class _Unit:
    role: str
    class_name: str
    signature: str
    java_file: JavaFile
    method: MethodSource
    has_items: bool

    @property
    def key(self) -> tuple[Path, int]:
        return self.java_file.path, self.method.start_line


def _events_by_order(test: TestMethodAnalysis) -> dict[int, HttpCallSite]:
    """The request call behind each builder and dispatch of the call sequence.

    Both lists hold one entry per request-role call of the runtime view, in its order.
    """
    request_steps = [
        step
        for step in test.http.call_sequence
        if step.kind is not SequenceStepKind.RESPONSE_CHECK
    ]
    calls = [
        interaction.http_call
        for interaction in test.http.request_interactions
        if interaction.http_call is not None
    ]
    return {step.order: call for step, call in zip(request_steps, calls, strict=True)}


HEADER_OR_AUTH = "header/auth"


def _builder_parts(call: HttpCallSite) -> list[str]:
    """The request parts a builder call sets, as its own record carries them.

    The dispatch's correlation sources cannot label a call on their own: they name
    it by its method and its chain's first line, which several `.param(..)` calls
    share.
    """
    sets = {
        "method": call.http_method != "UNKNOWN",
        "path": bool(call.path),
        # The paper reports headers and authentication as one part.
        HEADER_OR_AUTH: bool(call.headers or call.header_names or call.auth_hints),
        "parameter": bool(
            call.query_param_names or call.path_param_names or call.form_param_names
        ),
        "body": call.has_body_payload,
    }
    return [part for part, is_set in sets.items() if is_set]


_CONTRIBUTED_PART: dict[str, str] = {
    "http_method": "method",
    "path": "path",
    "headers": HEADER_OR_AUTH,
    "header_names": HEADER_OR_AUTH,
    "auth_hints": HEADER_OR_AUTH,
    "query_param_names": "parameter",
    "path_param_names": "parameter",
    "form_param_names": "parameter",
    "rest_assured_ambiguous_param_names": "parameter",
    "has_body_payload": "body",
}
_PART_ORDER = ("method", "path", HEADER_OR_AUTH, "parameter", "body")


def _contributed_parts(
    step: ApiSequenceStep, dispatch: HttpCallSite | None
) -> list[str]:
    """The parts the dispatch credits to a call of this name in this chain.

    Only for a builder whose own record is empty: REST Assured `param(..)` keeps its
    parameters on the dispatch alone, until the verb decides query or form.
    """
    if dispatch is None:
        return []
    found = {
        _CONTRIBUTED_PART[name]
        for source in dispatch.correlated_builder_sources
        if source.method_name == step.method_name
        and source.start_line == step.source_span.start_line
        for name in source.contributed_properties
        if name in _CONTRIBUTED_PART
    }
    return [part for part in _PART_ORDER if part in found]


def _dispatch_claims(step: ApiSequenceStep) -> dict[str, Any]:
    return {
        "http_method": step.http_method,
        "path": step.http_path,
        "path_truncated": step.path_truncated,
    }


def _check_claims(step: ApiSequenceStep) -> dict[str, Any]:
    assert step.assertion_role is not None
    return {
        "target": step.assertion_role.value,
        "status_code": step.status_code,
        "status_range": step.status_range,
    }


def _items(test: TestMethodAnalysis) -> list[dict[str, Any]]:
    events = _events_by_order(test)
    step_of_order = {
        step.order: sequence.order
        for sequence in test.http.test_sequences
        for step in sequence.steps
    }
    dispatch_of_sequence = {
        sequence.order: events[step.order]
        for sequence in test.http.test_sequences
        for step in sequence.steps
        if step.kind is SequenceStepKind.HTTP_REQUEST
    }
    items: list[dict[str, Any]] = []
    for step in test.http.call_sequence:
        sequence_order = step_of_order.get(step.order)
        item: dict[str, Any] = {
            "id": f"i{step.order}",
            "kind": _ITEM_KIND[step.kind],
            # None when the model kept the call out of every step.
            "step": sequence_order,
            "method_name": step.method_name,
            "origin_kind": step.origin.kind.value,
            "phase": step.phase.value,
            "origin_class": step.origin.defining_class_name,
            "origin_signature": step.origin.method_signature,
            "span": step.source_span.model_dump(),
        }
        if step.kind is SequenceStepKind.REQUEST_BUILD:
            dispatch = (
                None
                if sequence_order is None
                else dispatch_of_sequence.get(sequence_order)
            )
            item["parts"] = _builder_parts(events[step.order]) or _contributed_parts(
                step, dispatch
            )
        elif step.kind is SequenceStepKind.HTTP_REQUEST:
            item.update(_dispatch_claims(step))
        else:
            item.update(_check_claims(step))
        items.append(item)
    return items


def _asked(item: dict[str, Any]) -> bool:
    """False for a builder that sets no part of the request, such as `given()`.

    The paper defines a builder as a call that configures the request, and its
    request-part shares never count these; the code they sit in is still shown.
    """
    return item.get("parts") != []


def _locate(
    repo: JavaRepo, class_name: str, signature: str, lines: Sequence[int]
) -> tuple[JavaFile, MethodSource] | None:
    for path in repo.class_files(class_name):
        java_file = repo.java_file(path)
        method = java_file.find_method(signature, lines)
        if method is not None and all(method.contains(line) for line in lines):
            return java_file, method
    return None


def _scope_files(repo: JavaRepo, java_file: JavaFile) -> list[JavaFile]:
    """The file and the superclass files whose methods it can call unqualified."""
    files = [java_file]
    for _ in range(_SUPERCLASS_DEPTH):
        parent = repo.superclass_file(files[-1])
        if parent is None or parent in files:
            break
        files.append(parent)
    return files


def _callee_units(repo: JavaRepo, units: list[_Unit]) -> list[_Unit]:
    known = {unit.key for unit in units}
    callees: list[_Unit] = []
    frontier = list(units)
    for _ in range(_CALLEE_DEPTH):
        next_frontier: list[_Unit] = []
        for unit in frontier:
            scope = _scope_files(repo, unit.java_file)
            for name, argument_count in unit.java_file.unqualified_calls(unit.method):
                declared = [
                    (java_file, method)
                    for java_file in scope
                    for method in java_file.methods
                    if method.name == name
                ]
                same_arity = [
                    pair
                    for pair in declared
                    if len(pair[1].parameter_types) == argument_count
                ]
                if not (same_arity or declared):
                    continue
                # Overloads are not resolved: an extra helper shown costs less
                # than a missed one, and the nearest class's declaration wins.
                java_file, method = (same_arity or declared)[0]
                callee = _Unit(
                    role="helper",
                    class_name=java_file.path.stem,
                    signature=f"{method.name}({', '.join(method.parameter_types)})",
                    java_file=java_file,
                    method=method,
                    has_items=False,
                )
                if callee.key in known or len(callees) >= _CALLEE_LIMIT:
                    continue
                known.add(callee.key)
                callees.append(callee)
                next_frontier.append(callee)
        frontier = next_frontier
    return callees


def build_test_entry(
    candidate: TestCandidate, test: TestMethodAnalysis, repo: JavaRepo
) -> dict[str, Any]:
    """One test of the bundle: its code units and the items placed on them."""
    extracted = _items(test)
    items = [item for item in extracted if _asked(item)]

    # Test method first, then the fixtures around it, then helpers in the order the
    # runtime view reaches them.
    wanted: dict[tuple[str, str], str] = {
        (test.identity.defining_class_name, test.identity.method_signature): "test"
    }
    for phase in (LifecyclePhase.SETUP, LifecyclePhase.TEARDOWN):
        for fixture in test.fixtures:
            if fixture.phase is phase:
                wanted.setdefault(
                    (fixture.defining_class_name, fixture.method_signature),
                    phase.value,
                )
    for item in extracted:
        if item["origin_class"] and item["origin_signature"]:
            wanted.setdefault(
                (item["origin_class"], item["origin_signature"]), "helper"
            )

    units: list[_Unit] = []
    unit_index: dict[tuple[str, str], int] = {}
    missing_units: list[dict[str, str]] = []
    for (class_name, signature), role in wanted.items():
        placed = [
            item
            for item in extracted
            if (item["origin_class"], item["origin_signature"])
            == (class_name, signature)
        ]
        lines = [
            line
            for item in placed
            for line in (item["span"]["start_line"], item["span"]["end_line"])
        ]
        located = _locate(repo, class_name, signature, lines)
        if located is None:
            if role == "test":
                raise BundleError(
                    f"{candidate.test_id}: no source for {class_name}#{signature} "
                    f"in the {candidate.checkout} checkout"
                )
            missing_units.append(
                {"role": role, "class": class_name, "signature": signature}
            )
            continue
        java_file, method = located
        unit_index[(class_name, signature)] = len(units)
        has_items = any(_asked(item) for item in placed)
        units.append(_Unit(role, class_name, signature, java_file, method, has_items))
    units.extend(_callee_units(repo, units))

    for item in items:
        index = unit_index.get((item["origin_class"], item["origin_signature"]))
        item["unit"] = None if index is None else f"u{index}"
        item["token"] = None
        if index is None:
            continue
        unit = units[index]
        token = unit.java_file.call_token(
            item["method_name"], item["span"]["end_line"], item["span"]["end_column"]
        )
        if token is not None:
            item["token"] = {
                "line": token.line,
                "start": token.start_column,
                "end": token.end_column,
            }

    return {
        "id": candidate.test_id,
        "dataset": candidate.dataset_name,
        "class": candidate.qualified_class_name,
        "method": candidate.method_signature,
        "step_count": len(test.http.test_sequences),
        "has_helper_or_fixture_http": candidate.has_helper_or_fixture_http,
        "units": [
            {
                "id": f"u{index}",
                "role": unit.role,
                "class": unit.class_name,
                "signature": unit.signature,
                "file": repo.relative_path(unit.java_file.path),
                "start_line": unit.method.start_line,
                "lines": unit.java_file.lines[
                    unit.method.start_line - 1 : unit.method.end_line
                ],
                "has_items": unit.has_items,
            }
            for index, unit in enumerate(units)
        ],
        "missing_units": missing_units,
        "items": items,
    }


def bundle_digest(tests: Sequence[dict[str, Any]]) -> str:
    """Identifies what annotators saw, so annotations of another bundle are not mixed."""
    canonical = json.dumps(tests, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_bundle(
    sample_payload: dict[str, Any],
    sample: Sequence[TestCandidate],
    models: dict[str, TestMethodAnalysis],
    repos_root: Path,
) -> dict[str, Any]:
    """The bundle for a sample; each project's checkout is `repos_root/<checkout>`."""
    repos: dict[str, JavaRepo] = {}
    tests: list[dict[str, Any]] = []
    for candidate in sample:
        if candidate.checkout not in repos:
            checkout = repos_root / candidate.checkout
            if not checkout.is_dir():
                raise BundleError(
                    f"{candidate.dataset_name}: no checkout named "
                    f"{candidate.checkout} under the repos root"
                )
            repos[candidate.checkout] = JavaRepo(checkout)
        tests.append(
            build_test_entry(
                candidate, models[candidate.test_id], repos[candidate.checkout]
            )
        )
    return {
        "format": BUNDLE_FORMAT,
        "sample_digest": sample_payload["sample"]["digest"],
        "seed": sample_payload["seed"],
        "bundle_digest": bundle_digest(tests),
        "tests": tests,
    }


def write_bundle(bundle: dict[str, Any], output_dir: Path) -> None:
    """Keep what the page embeds, so scoring reads the items annotators judged."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / BUNDLE_FILE).write_text(json.dumps(bundle) + "\n", encoding="utf-8")


def read_bundle(sample_dir: Path) -> dict[str, Any]:
    bundle: dict[str, Any] = json.loads(
        (sample_dir / BUNDLE_FILE).read_text(encoding="utf-8")
    )
    return bundle
