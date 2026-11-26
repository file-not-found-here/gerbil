"""Draw the seeded sample of API tests that annotators validate, with a cap on how many
tests one project contributes."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from gerbil.analysis.schema import (
    CallSiteOriginKind,
    ProjectAnalysis,
    TestMethodAnalysis,
)

DEFAULT_SAMPLE_SIZE = 100
DEFAULT_PER_PROJECT_CAP = 2
DEFAULT_SAMPLE_SEED = "23"

SAMPLE_FILE = "sample.json"
MODELS_FILE = "models.json"


@dataclass(frozen=True)
class TestCandidate:
    """One API test of the corpus, with what the sample reports about it."""

    dataset_name: str
    # Directory name of the project's checkout, not where it was analyzed: the
    # sample travels to other machines and must not name anyone's file system.
    checkout: str
    qualified_class_name: str
    method_signature: str
    # Position among the project's tests sharing this class name and signature
    # (the same class compiled in two modules), so the id stays unique.
    occurrence: int
    step_count: int
    # A step item (builder, dispatch or check) sits in a helper or a fixture.
    has_helper_or_fixture_http: bool

    @property
    def test_id(self) -> str:
        suffix = f"#{self.occurrence}" if self.occurrence else ""
        return (
            f"{self.dataset_name}::{self.qualified_class_name}"
            f"::{self.method_signature}{suffix}"
        )


setattr(TestCandidate, "__test__", False)


def _has_helper_or_fixture_http(test: TestMethodAnalysis) -> bool:
    return any(
        step.origin.kind is not CallSiteOriginKind.TEST_METHOD
        for sequence in test.http.test_sequences
        for step in sequence.steps
    )


def project_tests(
    analysis: ProjectAnalysis,
) -> list[tuple[TestCandidate, TestMethodAnalysis]]:
    """The project's API tests that have at least one API invocation step."""
    checkout = Path(analysis.metadata.project_path).name
    tests: list[tuple[TestCandidate, TestMethodAnalysis]] = []
    seen: Counter[tuple[str, str]] = Counter()
    for test_class in analysis.test_class_analyses:
        for test in test_class.test_method_analyses:
            if not test.is_api_test or not test.http.test_sequences:
                continue
            key = (test_class.qualified_class_name, test.identity.method_signature)
            candidate = TestCandidate(
                dataset_name=analysis.dataset_name,
                checkout=checkout,
                qualified_class_name=key[0],
                method_signature=key[1],
                occurrence=seen[key],
                step_count=len(test.http.test_sequences),
                has_helper_or_fixture_http=_has_helper_or_fixture_http(test),
            )
            tests.append((candidate, test))
            seen[key] += 1
    return tests


def load_project_candidates(path: Path) -> list[TestCandidate]:
    """Top-level so spawn-based worker processes can pickle it."""
    raw = json.loads(path.read_bytes())
    # Most corpus projects have no API test; skip validating their whole model.
    if not raw["summary"]["api_test_count"]:
        return []
    analysis = ProjectAnalysis.model_validate(raw)
    return [candidate for candidate, _ in project_tests(analysis)]


def load_candidates(
    paths: Sequence[Path],
    *,
    jobs: int,
    on_loaded: Callable[[Path], None] | None = None,
) -> tuple[list[TestCandidate], dict[str, Path]]:
    """Every project's candidates in `paths` order, and each project's model file."""
    by_path: dict[Path, list[TestCandidate]] = {}
    if jobs == 1:
        for path in paths:
            by_path[path] = load_project_candidates(path)
            if on_loaded is not None:
                on_loaded(path)
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(load_project_candidates, path): path for path in paths
            }
            for future in as_completed(futures):
                path = futures[future]
                by_path[path] = future.result()
                if on_loaded is not None:
                    on_loaded(path)

    candidates: list[TestCandidate] = []
    model_files: dict[str, Path] = {}
    for path in paths:
        for dataset_name in {c.dataset_name for c in by_path[path]}:
            if dataset_name in model_files:
                raise ValueError(
                    f"two analyses are named {dataset_name}: "
                    f"{model_files[dataset_name]} and {path}"
                )
            model_files[dataset_name] = path
        candidates.extend(by_path[path])
    return candidates, model_files


def _rank(seed: str, test_id: str) -> tuple[bytes, str]:
    return hashlib.sha256(f"{seed}\0{test_id}".encode()).digest(), test_id


def draw_sample(
    candidates: Iterable[TestCandidate],
    *,
    size: int,
    per_project_cap: int,
    seed: str,
) -> list[TestCandidate]:
    """Up to `size` tests, in the seeded order annotators see them.

    Tests are ranked by a hash of the seed and their id, so the draw needs no random
    number generator to reproduce. Walking that order, a test is taken while its
    project is under the cap; a corpus the cap leaves too few tests stays short.
    """
    per_project: Counter[str] = Counter()
    sample: list[TestCandidate] = []
    for candidate in sorted(candidates, key=lambda c: _rank(seed, c.test_id)):
        if len(sample) == size:
            break
        if per_project[candidate.dataset_name] >= per_project_cap:
            continue
        sample.append(candidate)
        per_project[candidate.dataset_name] += 1
    return sample


def load_sampled_tests(
    sample: Sequence[TestCandidate], model_files: dict[str, Path]
) -> dict[str, TestMethodAnalysis]:
    """The model of each sampled test, keyed by test id, reading each project once."""
    wanted: dict[str, set[str]] = {}
    for candidate in sample:
        wanted.setdefault(candidate.dataset_name, set()).add(candidate.test_id)
    models: dict[str, TestMethodAnalysis] = {}
    for dataset_name, test_ids in wanted.items():
        analysis = ProjectAnalysis.model_validate_json(
            model_files[dataset_name].read_bytes()
        )
        for candidate, test in project_tests(analysis):
            if candidate.test_id in test_ids:
                models[candidate.test_id] = test
    return models


def sample_digest(sample: Iterable[TestCandidate]) -> str:
    """Identifies a sample, so annotations of another one are not scored together."""
    ids = sorted(candidate.test_id for candidate in sample)
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()


def _step_counts(candidates: Sequence[TestCandidate]) -> dict[str, int]:
    single = sum(candidate.step_count == 1 for candidate in candidates)
    return {"single_step": single, "multi_step": len(candidates) - single}


def build_sample_payload(
    candidates: Sequence[TestCandidate],
    sample: Sequence[TestCandidate],
    *,
    size: int,
    per_project_cap: int,
    seed: str,
    excluded_datasets: Sequence[str],
) -> dict[str, Any]:
    """The sample and the corpus it was drawn from."""
    return {
        "seed": seed,
        "size": size,
        "per_project_cap": per_project_cap,
        "excluded_datasets": sorted(excluded_datasets),
        "universe": {
            "project_count": len({c.dataset_name for c in candidates}),
            "api_test_count": len(candidates),
            "by_step_count": _step_counts(candidates),
        },
        "sample": {
            "digest": sample_digest(sample),
            "test_count": len(sample),
            "project_count": len({c.dataset_name for c in sample}),
            "by_step_count": _step_counts(sample),
            "with_helper_or_fixture_http": sum(
                c.has_helper_or_fixture_http for c in sample
            ),
        },
        "tests": [{"test_id": c.test_id, **asdict(c)} for c in sample],
    }


def write_sample(
    payload: dict[str, Any],
    models: dict[str, TestMethodAnalysis],
    output_dir: Path,
) -> None:
    """Write the sample and its tests' models, all the page and scoring read later."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / SAMPLE_FILE).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / MODELS_FILE).write_text(
        json.dumps(
            {
                "sample_digest": payload["sample"]["digest"],
                "tests": {
                    test_id: test.model_dump(mode="json")
                    for test_id, test in models.items()
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def read_sample_payload(sample_dir: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(
        (sample_dir / SAMPLE_FILE).read_text(encoding="utf-8")
    )
    return payload


def read_sample(
    sample_dir: Path,
) -> tuple[dict[str, Any], list[TestCandidate], dict[str, TestMethodAnalysis]]:
    """What `write_sample` wrote: the payload, its tests in annotation order, their models."""
    payload = read_sample_payload(sample_dir)
    tests = [
        TestCandidate(
            dataset_name=entry["dataset_name"],
            checkout=entry["checkout"],
            qualified_class_name=entry["qualified_class_name"],
            method_signature=entry["method_signature"],
            occurrence=entry["occurrence"],
            step_count=entry["step_count"],
            has_helper_or_fixture_http=entry["has_helper_or_fixture_http"],
        )
        for entry in payload["tests"]
    ]
    stored = json.loads((sample_dir / MODELS_FILE).read_text(encoding="utf-8"))
    if stored["sample_digest"] != payload["sample"]["digest"]:
        raise ValueError(
            f"{MODELS_FILE} and {SAMPLE_FILE} in {sample_dir.name} are of different samples"
        )
    models = {
        test_id: TestMethodAnalysis.model_validate(test)
        for test_id, test in stored["tests"].items()
    }
    return payload, tests, models
