from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gerbil import cli
from gerbil.analysis.schema import HttpSequenceSummary, ProjectAnalysis
from gerbil.statistics.records import ProjectStatsRecord, project_project
from gerbil.statistics.sampling import (
    EXCLUDED_DATASETS,
    build_candidate_pool,
    build_sample_payload,
    draw_random_sample,
    project_complexity,
    rank_interesting_projects,
    select_sample,
    write_sample,
)
from tests.statistics_builders import (
    api_test,
    endpoint_entry,
    non_api_test,
    project,
    write_gerbil_output,
)


def _seq_summary(
    count: int, *, multi: bool, distinct_endpoints: int = 1
) -> HttpSequenceSummary:
    return HttpSequenceSummary(
        sequence_count=count,
        has_multiple_sequences=multi,
        distinct_endpoint_count=distinct_endpoints,
    )


def _api_test(sequence_count: int, *, multi: bool, resolves_endpoint: bool = True):
    return api_test(
        sequence_summary=_seq_summary(
            sequence_count,
            multi=multi,
            distinct_endpoints=1 if resolves_endpoint else 0,
        )
    )


def _project_analysis(
    name: str,
    sequence_specs: list[tuple[int, bool]],
    *,
    endpoints: int = 1,
    resolves_endpoints: bool = True,
) -> ProjectAnalysis:
    """Project with one API test per (sequence_count, is_multi) spec.

    Defaults to one endpoint and method+path-resolving API tests so helper-built
    projects land inside the API-test-and-endpoints universe; pass endpoints=0 or
    resolves_endpoints=False to exercise the respective exclusion.
    """
    return project(
        dataset_name=name,
        tests=[
            _api_test(count, multi=multi, resolves_endpoint=resolves_endpoints)
            for count, multi in sequence_specs
        ],
        endpoints=[endpoint_entry(covering_test_count=0) for _ in range(endpoints)],
    )


def _record(
    name: str,
    sequence_specs: list[tuple[int, bool]],
    *,
    endpoints: int = 1,
    resolves_endpoints: bool = True,
) -> ProjectStatsRecord:
    return project_project(
        _project_analysis(
            name,
            sequence_specs,
            endpoints=endpoints,
            resolves_endpoints=resolves_endpoints,
        )
    )


# --- project_complexity ---------------------------------------------------


def test_project_complexity_aggregates_api_tests_only() -> None:
    analysis = project(
        dataset_name="svc",
        tests=[
            _api_test(3, multi=True),
            _api_test(5, multi=True),
            _api_test(1, multi=False),
            # Non-API tests never count toward complexity.
            non_api_test(),
        ],
        endpoints=[
            endpoint_entry(covering_test_count=2),
            endpoint_entry(covering_test_count=0),
        ],
    )
    record = project_project(analysis)

    complexity = project_complexity(record, Path("/in/svc/gerbil.json"))

    assert complexity.dataset_name == "svc"
    assert complexity.gerbil_path == Path("/in/svc/gerbil.json")
    assert complexity.api_test_count == 3
    assert complexity.resolved_endpoint_test_count == 3
    assert complexity.multi_sequence_test_count == 2
    assert complexity.total_sequence_count == 9
    assert complexity.max_sequence_count == 5
    assert complexity.mean_sequence_count_per_test == 3.0
    assert complexity.endpoint_count == 2


def test_project_complexity_counts_only_endpoint_resolving_api_tests() -> None:
    analysis = project(
        dataset_name="svc",
        tests=[
            _api_test(2, multi=True, resolves_endpoint=True),
            # API tests whose HTTP calls never resolve a method+path endpoint.
            _api_test(3, multi=True, resolves_endpoint=False),
            _api_test(1, multi=False, resolves_endpoint=False),
            non_api_test(),
        ],
        endpoints=[endpoint_entry(covering_test_count=0)],
    )
    record = project_project(analysis)

    complexity = project_complexity(record, Path("/in/svc/gerbil.json"))

    assert complexity.api_test_count == 3
    assert complexity.resolved_endpoint_test_count == 1


def test_project_complexity_mean_is_none_without_api_tests() -> None:
    record = project_project(project(dataset_name="svc", tests=[non_api_test()]))

    complexity = project_complexity(record, Path("/in/svc/gerbil.json"))

    assert complexity.api_test_count == 0
    assert complexity.max_sequence_count == 0
    assert complexity.mean_sequence_count_per_test is None


# --- rank_interesting_projects --------------------------------------------


def test_rank_excludes_projects_without_api_tests() -> None:
    records = [
        _record("has-multi", [(3, True), (1, False)]),
        _record("single-only", [(1, False), (1, False)]),
        _record("no-api", []),
    ]
    paths = [Path(f"/in/{record.dataset_name}/gerbil.json") for record in records]

    ranked = rank_interesting_projects(records, paths)

    # Both API-test projects qualify; only the no-API project is excluded. They tie
    # on API test count (2), so total sequence volume orders has-multi first.
    assert [project.dataset_name for project in ranked] == ["has-multi", "single-only"]


def test_rank_excludes_projects_without_endpoints() -> None:
    records = [
        _record("with-endpoints", [(2, True)], endpoints=2),
        # More API tests, but no endpoints: outside the API-test-and-endpoints universe.
        _record("no-endpoints", [(3, True), (2, True)], endpoints=0),
    ]
    paths = [Path(f"/in/{record.dataset_name}/gerbil.json") for record in records]

    ranked = rank_interesting_projects(records, paths)

    assert [project.dataset_name for project in ranked] == ["with-endpoints"]


def test_rank_excludes_projects_without_resolved_endpoints() -> None:
    records = [
        _record("resolves", [(2, True)], endpoints=2),
        # More API tests against discovered endpoints, but no test resolves a
        # method+path endpoint (e.g. raw HttpClient usage), so it is excluded.
        _record(
            "unresolved",
            [(3, True), (2, True)],
            endpoints=5,
            resolves_endpoints=False,
        ),
    ]
    paths = [Path(f"/in/{record.dataset_name}/gerbil.json") for record in records]

    ranked = rank_interesting_projects(records, paths)

    assert [project.dataset_name for project in ranked] == ["resolves"]


@pytest.mark.parametrize("excluded", sorted(EXCLUDED_DATASETS))
def test_rank_excludes_curated_datasets(excluded: str) -> None:
    records = [
        # Curated excludes are dropped despite leading on API test count, carrying
        # endpoints, and resolving them.
        _record(excluded, [(5, True), (5, True)], endpoints=4),
        _record("app", [(1, False)], endpoints=1),
    ]
    paths = [Path(f"/in/{record.dataset_name}/gerbil.json") for record in records]

    ranked = rank_interesting_projects(records, paths)

    assert [project.dataset_name for project in ranked] == ["app"]


def test_rank_orders_by_api_count_then_total_then_max_then_name() -> None:
    records = [
        # Same API count (2) as 'two-tests-high'; lower total sequences -> after it.
        _record("two-tests-low", [(2, True), (2, True)]),
        _record("two-tests-high", [(5, True), (3, True)]),
        # Most API tests -> ranks first regardless of sequence totals.
        _record("four-tests", [(2, True), (2, True), (2, True), (2, True)]),
        _record("one-test", [(9, True)]),
    ]
    paths = [Path(f"/in/{record.dataset_name}/gerbil.json") for record in records]

    ranked = rank_interesting_projects(records, paths)

    assert [project.dataset_name for project in ranked] == [
        "four-tests",
        "two-tests-high",
        "two-tests-low",
        "one-test",
    ]


def test_rank_breaks_full_ties_by_dataset_name() -> None:
    records = [
        _record("zeta", [(2, True), (2, True)]),
        _record("alpha", [(2, True), (2, True)]),
    ]
    paths = [Path(f"/in/{record.dataset_name}/gerbil.json") for record in records]

    ranked = rank_interesting_projects(records, paths)

    assert [project.dataset_name for project in ranked] == ["alpha", "zeta"]


def test_rank_requires_aligned_records_and_paths() -> None:
    records = [_record("a", [(2, True)])]

    with pytest.raises(ValueError):
        rank_interesting_projects(records, [])


# --- select_sample --------------------------------------------------------


def _ranked(*names: str) -> list:
    records = [_record(name, [(2, True)]) for name in names]
    paths = [Path(f"/in/{name}/gerbil.json") for name in names]
    return rank_interesting_projects(records, paths)


def test_select_count_keeps_top_n() -> None:
    ranked = _ranked("a", "b", "c", "d")
    selected = select_sample(ranked, count=2)
    assert len(selected) == 2


def test_select_count_exceeding_set_returns_all() -> None:
    ranked = _ranked("a", "b")
    assert len(select_sample(ranked, count=10)) == 2


def test_select_percentile_rounds_up_to_at_least_one() -> None:
    ranked = _ranked("a", "b", "c", "d", "e", "f", "g", "h", "i", "j")
    # 10% of 10 -> 1; 25% of 10 -> ceil(2.5) = 3.
    assert len(select_sample(ranked, percentile=10)) == 1
    assert len(select_sample(ranked, percentile=25)) == 3
    assert len(select_sample(ranked, percentile=100)) == 10


def test_select_percentile_on_empty_set_returns_empty() -> None:
    assert select_sample([], percentile=10) == []


def test_select_requires_exactly_one_parameter() -> None:
    ranked = _ranked("a")
    with pytest.raises(ValueError, match="exactly one"):
        select_sample(ranked)
    with pytest.raises(ValueError, match="exactly one"):
        select_sample(ranked, count=1, percentile=10)


def test_select_rejects_out_of_range_percentile() -> None:
    ranked = _ranked("a")
    with pytest.raises(ValueError, match="0, 100"):
        select_sample(ranked, percentile=0)
    with pytest.raises(ValueError, match="0, 100"):
        select_sample(ranked, percentile=150)


def test_select_rejects_non_positive_count() -> None:
    ranked = _ranked("a")
    with pytest.raises(ValueError, match="count"):
        select_sample(ranked, count=0)


# --- build_candidate_pool / draw_random_sample ----------------------------


def _ranked_by_test_count(n: int) -> list:
    # n interesting projects with strictly decreasing API test counts: p00 carries
    # the most (n tests), p{n-1} the fewest (1).
    records = [_record(f"p{i:02d}", [(2, True)] * (n - i)) for i in range(n)]
    paths = [Path(f"/in/p{i:02d}/gerbil.json") for i in range(n)]
    return rank_interesting_projects(records, paths)


def test_build_candidate_pool_keeps_top_fraction() -> None:
    ranked = _ranked_by_test_count(10)
    assert [p.dataset_name for p in build_candidate_pool(ranked, 20)] == ["p00", "p01"]
    # Rounds up to at least one project.
    assert [p.dataset_name for p in build_candidate_pool(ranked, 1)] == ["p00"]
    assert len(build_candidate_pool(ranked, 100)) == 10


def test_build_candidate_pool_empty_and_range() -> None:
    assert build_candidate_pool([], 10) == []
    with pytest.raises(ValueError, match="pool_percent"):
        build_candidate_pool(_ranked_by_test_count(3), 0)
    with pytest.raises(ValueError, match="pool_percent"):
        build_candidate_pool(_ranked_by_test_count(3), 150)


def test_draw_random_sample_is_seed_reproducible_and_subset() -> None:
    pool = _ranked_by_test_count(10)
    first = draw_random_sample(pool, count=4, seed=7)
    again = draw_random_sample(pool, count=4, seed=7)

    names = {p.dataset_name for p in pool}
    assert [p.dataset_name for p in first] == [p.dataset_name for p in again]
    assert len(first) == 4
    assert {p.dataset_name for p in first} <= names


def test_draw_random_sample_returns_rank_sorted() -> None:
    pool = _ranked_by_test_count(10)
    drawn = draw_random_sample(pool, count=5, seed=3)
    counts = [p.api_test_count for p in drawn]
    assert counts == sorted(counts, reverse=True)


def test_draw_random_sample_varies_with_seed() -> None:
    pool = _ranked_by_test_count(10)
    selections = {
        tuple(p.dataset_name for p in draw_random_sample(pool, count=5, seed=seed))
        for seed in range(20)
    }
    # A genuine random draw over C(10,5)=252 options yields more than one outcome.
    assert len(selections) > 1


def test_draw_random_sample_count_exceeding_pool_returns_all() -> None:
    pool = _ranked_by_test_count(3)
    drawn = draw_random_sample(pool, count=10, seed=1)
    assert {p.dataset_name for p in drawn} == {"p00", "p01", "p02"}


# --- build_sample_payload -------------------------------------------------


def test_build_payload_reports_selection_summary_and_ranked_projects() -> None:
    ranked = rank_interesting_projects(
        [
            _record("top", [(4, True), (4, True)], endpoints=3),
            _record("next", [(2, True)], endpoints=1),
        ],
        [Path("/in/top/gerbil.json"), Path("/in/next/gerbil.json")],
    )
    selected = select_sample(ranked, count=1)

    payload = build_sample_payload(ranked, selected, count=1, percentile=None)

    assert payload["selection"] == {
        "strategy": "api_test_count",
        "mode": "top",
        "count": 1,
        "percentile": None,
        "seed": None,
        "pool_percent": None,
        "interesting_project_count": 2,
        "pool_project_count": None,
        "selected_project_count": 1,
    }
    # The summary aggregates the selected projects only (top, not next).
    assert payload["summary"] == {
        "project_count": 1,
        "api_test_count": 2,
        "resolved_endpoint_test_count": 2,
        "multi_sequence_test_count": 2,
        "total_sequence_count": 8,
        "endpoint_count": 3,
    }
    assert len(payload["projects"]) == 1
    entry = payload["projects"][0]
    assert entry["rank"] == 1
    assert entry["dataset_name"] == "top"
    assert entry["gerbil_path"] == "/in/top/gerbil.json"
    assert entry["project_dir"] == "/in/top"
    assert entry["api_test_count"] == 2
    assert entry["resolved_endpoint_test_count"] == 2
    assert entry["multi_sequence_test_count"] == 2
    assert entry["total_sequence_count"] == 8
    assert entry["endpoint_count"] == 3


def test_write_sample_round_trips_empty_payload(tmp_path: Path) -> None:
    payload = build_sample_payload([], [], count=10, percentile=None)

    output_file = write_sample(payload, tmp_path / "out")

    assert output_file == tmp_path / "out" / "interesting_projects.json"
    written = json.loads(output_file.read_text())
    assert written["projects"] == []
    assert written["selection"]["selected_project_count"] == 0
    assert written["summary"] == {
        "project_count": 0,
        "api_test_count": 0,
        "resolved_endpoint_test_count": 0,
        "multi_sequence_test_count": 0,
        "total_sequence_count": 0,
        "endpoint_count": 0,
    }


# --- CLI ------------------------------------------------------------------


def _sample_corpus(input_root: Path) -> None:
    write_gerbil_output(
        input_root,
        "alpha",
        _project_analysis("alpha", [(3, True), (4, True), (1, False)], endpoints=2),
    )
    write_gerbil_output(
        input_root,
        "beta",
        _project_analysis("beta", [(2, True), (1, False)], endpoints=1),
    )
    # A single-sequence-only project still has API tests, so it is interesting.
    write_gerbil_output(input_root, "gamma", _project_analysis("gamma", [(1, False)]))
    # Only projects with no API tests are excluded.
    write_gerbil_output(
        input_root, "delta", project(dataset_name="delta", tests=[non_api_test()])
    )


def test_sample_projects_defaults_to_count_ten(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    _sample_corpus(input_root)

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["analyses"] == 4
    assert summary["loaded"] == 4
    assert summary["interesting_projects"] == 3
    assert summary["selected_projects"] == 3

    payload = json.loads((output_dir / "interesting_projects.json").read_text())
    assert payload["selection"]["count"] == 10
    assert payload["selection"]["percentile"] is None
    # Ranked by API test count: alpha (3) > beta (2) > gamma (1); delta has no API
    # tests and is excluded.
    assert [entry["dataset_name"] for entry in payload["projects"]] == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert [entry["rank"] for entry in payload["projects"]] == [1, 2, 3]
    assert [entry["endpoint_count"] for entry in payload["projects"]] == [2, 1, 1]
    # The summary aggregates the selected projects (alpha + beta + gamma).
    assert payload["summary"] == {
        "project_count": 3,
        "api_test_count": 6,
        "resolved_endpoint_test_count": 6,
        "multi_sequence_test_count": 3,
        "total_sequence_count": 12,
        "endpoint_count": 4,
    }


def test_sample_projects_excludes_no_endpoint_unresolved_and_known_libraries(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    # Interesting: API tests that resolve a method+path endpoint.
    write_gerbil_output(
        input_root, "app", _project_analysis("app", [(2, True)], endpoints=1)
    )
    # Excluded: more API tests, but no endpoints.
    write_gerbil_output(
        input_root,
        "no-endpoints",
        _project_analysis(
            "no-endpoints", [(9, True), (9, True), (9, True)], endpoints=0
        ),
    )
    # Excluded: API tests and endpoints, but no test resolves a method+path
    # endpoint (e.g. raw HttpClient usage).
    write_gerbil_output(
        input_root,
        "unresolved",
        _project_analysis(
            "unresolved", [(9, True)] * 5, endpoints=4, resolves_endpoints=False
        ),
    )
    # Excluded: an API-testing library, even with the most tests and endpoints.
    write_gerbil_output(
        input_root,
        "rest-assured_rest-assured",
        _project_analysis("rest-assured_rest-assured", [(5, True)] * 4, endpoints=3),
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["analyses"] == 4
    assert summary["interesting_projects"] == 1
    payload = json.loads((output_dir / "interesting_projects.json").read_text())
    assert [entry["dataset_name"] for entry in payload["projects"]] == ["app"]


def test_sample_projects_count_limits_selection(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    _sample_corpus(input_root)

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
            "--count",
            "1",
        ],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads((output_dir / "interesting_projects.json").read_text())
    assert [entry["dataset_name"] for entry in payload["projects"]] == ["alpha"]


def test_sample_projects_percentile_limits_selection(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    _sample_corpus(input_root)

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
            "--percentile",
            "50",
        ],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads((output_dir / "interesting_projects.json").read_text())
    assert payload["selection"]["percentile"] == 50
    assert payload["selection"]["count"] is None
    # 50% of the 3 interesting projects -> ceil(1.5) = 2.
    assert [entry["dataset_name"] for entry in payload["projects"]] == ["alpha", "beta"]


def test_sample_projects_rejects_count_and_percentile_together(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    _sample_corpus(input_root)

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(tmp_path / "out"),
            "--count",
            "5",
            "--percentile",
            "10",
        ],
    )

    assert result.exit_code != 0
    assert "not both" in result.stderr


def test_sample_projects_rejects_out_of_range_percentile(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    _sample_corpus(input_root)

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(tmp_path / "out"),
            "--percentile",
            "150",
        ],
    )

    assert result.exit_code != 0
    assert "(0, 100]" in result.stderr


def _write_test_count_corpus(input_root: Path, n: int) -> None:
    # n interesting projects with strictly decreasing API test counts (p00 most).
    for i in range(n):
        name = f"p{i:02d}"
        write_gerbil_output(
            input_root, name, _project_analysis(name, [(2, True)] * (n - i))
        )


def test_sample_projects_random_draws_from_top_pool(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    _write_test_count_corpus(input_root, 10)

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
            "--random",
            "--seed",
            "5",
            "--pool-percent",
            "20",
            "--count",
            "2",
        ],
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads((output_dir / "interesting_projects.json").read_text())
    assert payload["selection"]["mode"] == "random"
    assert payload["selection"]["seed"] == 5
    assert payload["selection"]["pool_percent"] == 20
    assert payload["selection"]["interesting_project_count"] == 10
    assert payload["selection"]["pool_project_count"] == 2
    # Pool is the top 20% (p00, p01) by API test count; count 2 draws the whole
    # pool, never the lower-count interesting projects.
    assert [entry["dataset_name"] for entry in payload["projects"]] == ["p00", "p01"]


def test_sample_projects_random_is_reproducible_per_seed(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    _write_test_count_corpus(input_root, 10)

    def _run(output_dir: Path, seed: str) -> list[str]:
        result = CliRunner().invoke(
            cli.app,
            [
                "sample-projects",
                "--input-root",
                str(input_root),
                "--output-dir",
                str(output_dir),
                "--jobs",
                "1",
                "--random",
                "--seed",
                seed,
                "--pool-percent",
                "100",
                "--count",
                "4",
            ],
        )
        assert result.exit_code == 0, result.stderr
        payload = json.loads((output_dir / "interesting_projects.json").read_text())
        return [entry["dataset_name"] for entry in payload["projects"]]

    assert _run(tmp_path / "a", "11") == _run(tmp_path / "b", "11")


def test_sample_projects_random_rejects_out_of_range_pool_percent(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "input"
    _write_test_count_corpus(input_root, 3)

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(tmp_path / "out"),
            "--random",
            "--pool-percent",
            "0",
        ],
    )

    assert result.exit_code != 0
    assert "--pool-percent must be in the range (0, 100]" in result.stderr


def test_sample_projects_runs_in_parallel_worker_processes(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    _sample_corpus(input_root)

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "2",
        ],
    )

    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["loaded"] == 4
    assert (output_dir / "interesting_projects.json").is_file()


def test_sample_projects_exits_nonzero_on_load_failure(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    _sample_corpus(input_root)
    # Sorts ahead of every real project, so a skipped failure shifts the
    # record<->path alignment for the kept interesting projects if mis-derived.
    (input_root / "aaa-broken").mkdir()
    (input_root / "aaa-broken" / "gerbil.json").write_text("nope", encoding="utf-8")

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 1
    summary = json.loads(result.stdout)
    assert summary["failed"] == 1
    # The broken project never reaches the ranked set; the sample is still written
    # with each project's path correctly paired to its dataset_name (the skip must
    # not offset the record<->path alignment).
    payload = json.loads((output_dir / "interesting_projects.json").read_text())
    assert [entry["dataset_name"] for entry in payload["projects"]] == [
        "alpha",
        "beta",
        "gamma",
    ]
    for entry in payload["projects"]:
        assert entry["gerbil_path"].endswith(f"/{entry['dataset_name']}/gerbil.json")
        assert entry["project_dir"].endswith(f"/{entry['dataset_name']}")


def test_sample_projects_writes_empty_file_when_none_interesting(
    tmp_path: Path,
) -> None:
    input_root = tmp_path / "input"
    output_dir = tmp_path / "out"
    # Only projects without API tests: none is an API-test repository, so the
    # interesting set is empty.
    write_gerbil_output(
        input_root, "gamma", project(dataset_name="gamma", tests=[non_api_test()])
    )
    write_gerbil_output(
        input_root, "delta", project(dataset_name="delta", tests=[non_api_test()])
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
        ],
    )

    assert result.exit_code == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["interesting_projects"] == 0
    assert summary["selected_projects"] == 0

    output_file = output_dir / "interesting_projects.json"
    assert output_file.is_file()
    payload = json.loads(output_file.read_text())
    assert payload["projects"] == []
    assert payload["selection"]["interesting_project_count"] == 0
    assert payload["selection"]["selected_project_count"] == 0
    assert payload["summary"]["project_count"] == 0
    assert payload["summary"]["endpoint_count"] == 0


def test_sample_projects_rejects_missing_input_root(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(tmp_path / "missing"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code != 0
    assert "input_root does not exist" in result.stderr


def test_sample_projects_rejects_input_root_without_outputs(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()

    result = CliRunner().invoke(
        cli.app,
        [
            "sample-projects",
            "--input-root",
            str(input_root),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code != 0
    assert "does not contain any gerbil.json outputs" in result.stderr
