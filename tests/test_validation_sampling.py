from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from gerbil.validation.sampling import (
    MODELS_FILE,
    TestCandidate,
    _rank,
    build_sample_payload,
    draw_sample,
    load_candidates,
    load_sampled_tests,
    project_tests,
    read_sample,
    sample_digest,
    write_sample,
)
from tests.validation_builders import api_test, non_api_test, project, write_model


def _candidate(project_name: str, index: int, step_count: int) -> TestCandidate:
    return TestCandidate(
        dataset_name=project_name,
        checkout=project_name,
        qualified_class_name="demo.WidgetTest",
        method_signature=f"test{index}()",
        occurrence=0,
        step_count=step_count,
        has_helper_or_fixture_http=False,
    )


# 30 projects of 20 tests each, alternating one step and three.
_CORPUS = [
    _candidate(f"project{p}", index, 1 if index % 2 else 3)
    for p in range(30)
    for index in range(20)
]


def _draw(size: int = 100, cap: int = 5, seed: str = "seed") -> list[TestCandidate]:
    return draw_sample(_CORPUS, size=size, per_project_cap=cap, seed=seed)


def test_sample_takes_tests_in_seeded_order_without_balancing_step_counts() -> None:
    sample = _draw()
    ranked = sorted(_CORPUS, key=lambda c: _rank("seed", c.test_id))
    per_project: Counter[str] = Counter()
    expected = []
    for candidate in ranked:
        if len(expected) == 100:
            break
        if per_project[candidate.dataset_name] < 5:
            expected.append(candidate)
            per_project[candidate.dataset_name] += 1

    assert len(sample) == 100
    assert sample == expected


def test_a_corpus_of_one_step_count_gives_a_sample_of_that_step_count() -> None:
    corpus = [
        _candidate(f"project{p}", index, 1) for p in range(4) for index in range(5)
    ]

    sample = draw_sample(corpus, size=10, per_project_cap=5, seed="seed")

    assert len(sample) == 10
    assert {candidate.step_count for candidate in sample} == {1}


def test_no_project_contributes_more_than_the_cap() -> None:
    per_project = Counter(candidate.dataset_name for candidate in _draw(cap=5))

    assert max(per_project.values()) == 5
    assert max(Counter(c.dataset_name for c in _draw(cap=4)).values()) == 4


def test_sample_is_the_same_whatever_order_the_corpus_is_read_in() -> None:
    reordered = draw_sample(
        list(reversed(_CORPUS)), size=100, per_project_cap=5, seed="seed"
    )

    assert reordered == _draw()


def test_another_seed_draws_another_sample() -> None:
    assert sample_digest(_draw(seed="seed")) != sample_digest(_draw(seed="other"))


def test_a_corpus_the_cap_leaves_too_few_tests_stays_short() -> None:
    corpus = [_candidate("only", index, 1) for index in range(10)]

    sample = draw_sample(corpus, size=6, per_project_cap=5, seed="seed")

    assert len(sample) == 5


def test_candidates_are_the_api_tests_that_have_a_step() -> None:
    analysis = project(
        "demo",
        [
            api_test("one()", 1),
            api_test("three()", 3, in_helper=True),
            non_api_test("unit()"),
            # The same class compiled in a second module.
            api_test("one()", 2),
        ],
    )

    candidates = [candidate for candidate, _ in project_tests(analysis)]

    assert {c.checkout for c in candidates} == {"demo"}
    assert [
        (c.test_id, c.step_count, c.has_helper_or_fixture_http) for c in candidates
    ] == [
        ("demo::demo.WidgetTest::one()", 1, False),
        ("demo::demo.WidgetTest::three()", 3, True),
        ("demo::demo.WidgetTest::one()#1", 2, False),
    ]


def test_candidates_load_in_path_order_and_skip_projects_without_api_tests(
    tmp_path: Path,
) -> None:
    paths = sorted(
        [
            write_model(tmp_path, project("b", [api_test("two()", 2)])),
            write_model(tmp_path, project("a", [api_test("one()", 1)])),
            write_model(tmp_path, project("empty", [])),
        ]
    )

    candidates, model_files = load_candidates(paths, jobs=2)

    assert [c.dataset_name for c in candidates] == ["a", "b"]
    assert model_files == {"a": paths[0], "b": paths[1]}


def test_two_analyses_of_one_name_are_rejected(tmp_path: Path) -> None:
    paths = [
        write_model(tmp_path / "x", project("demo", [api_test("one()", 1)])),
        write_model(tmp_path / "y", project("demo", [api_test("one()", 1)])),
    ]

    with pytest.raises(ValueError, match="two analyses are named demo"):
        load_candidates(paths, jobs=1)


def test_sampled_tests_are_read_back_by_id(tmp_path: Path) -> None:
    analysis = project(
        "demo", [api_test("one()", 1), api_test("three()", 3), api_test("one()", 2)]
    )
    path = write_model(tmp_path, analysis)
    candidates, model_files = load_candidates([path], jobs=1)

    models = load_sampled_tests([candidates[2], candidates[1]], model_files)

    assert {
        test_id: len(test.http.test_sequences) for test_id, test in models.items()
    } == {"demo::demo.WidgetTest::one()#1": 2, "demo::demo.WidgetTest::three()": 3}


def test_sample_directory_round_trips_and_names_no_file_system(tmp_path: Path) -> None:
    analysis = project(
        "demo",
        [api_test("one()", 1), api_test("two()", 2)],
        project_path="/home/someone/repos/demo-checkout",
    )
    path = write_model(tmp_path / "models", analysis)
    candidates, model_files = load_candidates([path], jobs=1)
    sample = draw_sample(candidates, size=2, per_project_cap=5, seed="seed")
    payload = build_sample_payload(
        candidates,
        sample,
        size=2,
        per_project_cap=5,
        seed="seed",
        excluded_datasets=["tuned-on"],
    )

    assert payload["universe"] == {
        "project_count": 1,
        "api_test_count": 2,
        "by_step_count": {"single_step": 1, "multi_step": 1},
    }
    assert payload["sample"]["by_step_count"] == {"single_step": 1, "multi_step": 1}
    assert payload["sample"]["digest"] == sample_digest(sample)
    assert payload["excluded_datasets"] == ["tuned-on"]

    output_dir = tmp_path / "out" / "validation"
    write_sample(payload, load_sampled_tests(sample, model_files), output_dir)
    read_payload, read_tests, read_models = read_sample(output_dir)

    assert read_payload == payload
    assert read_tests == sample
    assert [c.checkout for c in read_tests] == ["demo-checkout"] * 2
    assert set(read_models) == {c.test_id for c in sample}
    assert (
        read_models[sample[0].test_id]
        == load_sampled_tests(sample, model_files)[sample[0].test_id]
    )
    for written in output_dir.iterdir():
        text = written.read_text()
        assert "/home/someone" not in text and str(tmp_path) not in text


def test_models_of_another_sample_are_rejected(tmp_path: Path) -> None:
    analysis = project("demo", [api_test("one()", 1), api_test("two()", 2)])
    path = write_model(tmp_path / "models", analysis)
    candidates, model_files = load_candidates([path], jobs=1)

    def write(size: int, directory: Path) -> None:
        sample = draw_sample(candidates, size=size, per_project_cap=5, seed="seed")
        payload = build_sample_payload(
            candidates,
            sample,
            size=size,
            per_project_cap=5,
            seed="seed",
            excluded_datasets=[],
        )
        write_sample(payload, load_sampled_tests(sample, model_files), directory)

    write(2, tmp_path / "full")
    write(3, tmp_path / "other")
    (tmp_path / "full" / MODELS_FILE).write_text(
        (tmp_path / "other" / MODELS_FILE).read_text()
    )
    # Same tests, so the same digest: swapping in the other file is harmless.
    read_sample(tmp_path / "full")

    stored = json.loads((tmp_path / "full" / MODELS_FILE).read_text())
    stored["sample_digest"] = "0" * 64
    (tmp_path / "full" / MODELS_FILE).write_text(json.dumps(stored))
    with pytest.raises(ValueError, match="different samples"):
        read_sample(tmp_path / "full")
