from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scipy.stats import fisher_exact, mannwhitneyu
from typer.testing import CliRunner

from gerbil import cli
from gerbil.statistics.records import ProjectStatsRecord, TestRecord, project_test
from gerbil.statistics.runner import (
    ASSERTION_CLUSTERING_DISTRIBUTION,
    HTTP_TEST_SEQUENCE_DISTRIBUTION,
)
from gerbil.statistics.suite_comparison import (
    ALPHA,
    CLIFFS_DELTA_THRESHOLDS,
    COHENS_H_THRESHOLDS,
    MEASURES,
    DescriptiveMeasure,
    DistributionMeasure,
    ProportionMeasure,
    Suite,
    build_suite,
    cliffs_delta,
    cohens_h,
    compute,
    holm_adjust,
    magnitude,
)
from tests.statistics_builders import api_test, project, write_gerbil_output

_BASE_API_TEST = project_test(api_test())


def _test(**overrides: Any) -> TestRecord:
    return replace(_BASE_API_TEST, **overrides)


def _step_test(
    *check_counts: int,
    body_check_counts: tuple[int, ...] | None = None,
    **overrides: Any,
) -> TestRecord:
    """An API test with one single-request step per entry, carrying that many
    response checks; every per-step field stays aligned."""
    steps = len(check_counts)
    fields: dict[str, Any] = {
        "http_sequence_count": steps,
        "http_sequence_lengths": tuple(1 + count for count in check_counts),
        "http_sequence_request_build_counts": (0,) * steps,
        "http_sequence_http_request_counts": (1,) * steps,
        "http_sequence_request_side_lengths": (1,) * steps,
        "http_sequence_response_check_lengths": check_counts,
        "http_sequence_status_check_counts": (0,) * steps,
        "http_sequence_body_check_counts": body_check_counts or (0,) * steps,
        "http_sequence_header_check_counts": (0,) * steps,
        "http_sequence_status_ranges": ((),) * steps,
        "http_sequence_crud_operations": (("read",),) * steps,
        "http_sequence_verb_operations": (("GET",),) * steps,
        "dispatch_event_request_builder_counts": (0,) * steps,
        "dispatch_event_response_check_counts": check_counts,
        "event_counts": (steps, 0, 0),
    }
    return _test(**(fields | overrides))


def _suite(name: str, tests: list[TestRecord]) -> Suite:
    record = ProjectStatsRecord(
        dataset_name=name,
        tests=tuple(tests),
        test_classes=(),
        endpoints=(),
        endpoint_parameters=(),
        resources=(),
    )
    return build_suite(name, [record])


def _measure(payload: dict[str, Any], name: str) -> dict[str, Any]:
    (entry,) = [entry for entry in payload["measures"] if entry["name"] == name]
    return entry


def _tested_comparisons(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        comparison
        for entry in payload["measures"]
        for comparison in entry["comparisons"].values()
        if comparison.get("test") is not None
    ]


# --- effect sizes and correction ---------------------------------------------


def test_cohens_h_is_signed_and_bounded_by_pi() -> None:
    assert cohens_h(0.5, 0.5) == 0.0
    assert cohens_h(1.0, 0.0) == pytest.approx(math.pi)
    assert cohens_h(0.5, 0.25) == pytest.approx(math.pi / 6)
    assert cohens_h(0.25, 0.5) == pytest.approx(-math.pi / 6)


def test_cliffs_delta_matches_the_pairwise_definition_with_ties() -> None:
    first = [1, 2, 2, 5]
    second = [2, 3, 0]
    greater = sum(1 for x in first for y in second if x > y)
    less = sum(1 for x in first for y in second if x < y)
    u_statistic = float(mannwhitneyu(first, second).statistic)

    delta = cliffs_delta(u_statistic, len(first), len(second))

    assert delta == pytest.approx((greater - less) / (len(first) * len(second)))


def test_cliffs_delta_is_one_under_complete_separation() -> None:
    assert cliffs_delta(6.0, 3, 2) == pytest.approx(1.0)
    assert cliffs_delta(0.0, 3, 2) == pytest.approx(-1.0)


def test_magnitude_thresholds_are_inclusive_lower_bounds() -> None:
    assert magnitude(0.19, COHENS_H_THRESHOLDS) == "negligible"
    assert magnitude(0.2, COHENS_H_THRESHOLDS) == "small"
    assert magnitude(-0.5, COHENS_H_THRESHOLDS) == "medium"
    assert magnitude(-0.8, COHENS_H_THRESHOLDS) == "large"
    assert magnitude(0.146, CLIFFS_DELTA_THRESHOLDS) == "negligible"
    assert magnitude(0.147, CLIFFS_DELTA_THRESHOLDS) == "small"
    assert magnitude(0.33, CLIFFS_DELTA_THRESHOLDS) == "medium"
    assert magnitude(-0.474, CLIFFS_DELTA_THRESHOLDS) == "large"


def test_holm_adjust_scales_by_remaining_tests_in_input_order() -> None:
    assert holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm_adjust([0.2]) == [0.2]


def test_holm_adjust_is_monotone_and_capped_at_one() -> None:
    # 0.011 * 2 = 0.022 would undercut the smaller p-value's 0.03.
    assert holm_adjust([0.01, 0.011, 0.5]) == pytest.approx([0.03, 0.03, 0.5])
    # Tied p-values share one adjusted value.
    assert holm_adjust([0.01, 0.01, 0.04]) == pytest.approx([0.03, 0.03, 0.04])
    assert holm_adjust([0.6, 0.9]) == [1.0, 1.0]
    assert holm_adjust([]) == []


# --- measures ----------------------------------------------------------------


def test_measures_are_the_comparison_table_rows() -> None:
    names = [measure.name for measure in MEASURES]

    assert len(names) == len(set(names)) == 22
    assert names[0] == "request_builders_per_step"
    assert names[-1] == "writing_tests_with_read_after_write"
    assert sum(isinstance(m, ProportionMeasure) for m in MEASURES) == 17
    assert sum(isinstance(m, DistributionMeasure) for m in MEASURES) == 2
    assert sum(isinstance(m, DescriptiveMeasure) for m in MEASURES) == 3


def test_proportion_measure_uses_fisher_exact_and_cohens_h() -> None:
    # 4 of 10 baseline steps carry a response check, against 9 of 10.
    baseline = _suite("dev", [_step_test(1, 0, 0, 2, 0), _step_test(1, 0, 0, 3, 0)])
    other = _suite("gen", [_step_test(1, 1, 1, 1, 0), _step_test(1, 1, 1, 1, 2)])

    entry = _measure(compute(baseline, [other]), "steps_with_response_check")

    assert entry["kind"] == "proportion"
    assert entry["unit"] == "steps"
    assert entry["baseline"] == {
        "count": 4,
        "total": 10,
        "proportion": pytest.approx(0.4),
    }
    comparison = entry["comparisons"]["gen"]
    assert (comparison["count"], comparison["total"]) == (9, 10)
    assert comparison["test"]["name"] == "fisher_exact"
    assert comparison["test"]["p_value"] == pytest.approx(
        fisher_exact([[4, 6], [9, 1]]).pvalue
    )
    assert comparison["effect_size"] == {
        "name": "cohens_h",
        "value": pytest.approx(cohens_h(0.4, 0.9)),
        "magnitude": "large",
    }
    assert comparison["effect_size"]["value"] < 0


def test_single_step_tests_have_exactly_one_step() -> None:
    baseline = _suite("dev", [_step_test(1), _step_test(1, 1), _step_test()])
    other = _suite("gen", [_step_test(1), _step_test(1)])

    entry = _measure(compute(baseline, [other]), "single_step_tests")

    # A test without any step is not a single-step test.
    assert (entry["baseline"]["count"], entry["baseline"]["total"]) == (1, 3)
    assert entry["comparisons"]["gen"]["proportion"] == pytest.approx(1.0)
    assert entry["comparisons"]["gen"]["effect_size"]["value"] < 0


def test_distribution_measure_uses_mann_whitney_and_cliffs_delta() -> None:
    baseline = _suite(
        "dev",
        [
            _test(dispatch_event_request_builder_counts=(3, 4, 5)),
            _test(dispatch_event_request_builder_counts=(4,)),
        ],
    )
    other = _suite(
        "gen",
        [
            _test(dispatch_event_request_builder_counts=(1, 2)),
            _test(dispatch_event_request_builder_counts=(2,)),
        ],
    )

    entry = _measure(compute(baseline, [other]), "request_builders_per_step")

    assert entry["kind"] == "distribution"
    assert entry["baseline"] == {
        "count": 4,
        "mean": pytest.approx(4.0),
        "median": pytest.approx(4.0),
    }
    comparison = entry["comparisons"]["gen"]
    assert comparison["count"] == 3
    assert comparison["mean"] == pytest.approx(5 / 3)
    assert comparison["median"] == pytest.approx(2.0)
    assert comparison["test"]["name"] == "mann_whitney_u"
    assert comparison["test"]["p_value"] == pytest.approx(
        mannwhitneyu([3, 4, 5, 4], [1, 2, 2], alternative="two-sided").pvalue
    )
    assert comparison["effect_size"] == {
        "name": "cliffs_delta",
        "value": pytest.approx(1.0),
        "magnitude": "large",
    }


def test_cliffs_delta_follows_the_observations_not_the_means() -> None:
    # One extreme step lifts the baseline mean, yet four of its five steps are
    # below every compared step.
    baseline = _suite(
        "dev", [_test(dispatch_event_request_builder_counts=(0, 0, 0, 0, 100))]
    )
    other = _suite(
        "gen", [_test(dispatch_event_request_builder_counts=(1, 1, 1, 1, 1))]
    )

    entry = _measure(compute(baseline, [other]), "request_builders_per_step")

    comparison = entry["comparisons"]["gen"]
    assert entry["baseline"]["mean"] > comparison["mean"]
    assert entry["baseline"]["median"] < comparison["median"]
    assert comparison["effect_size"]["value"] == pytest.approx(-0.6)


def test_distribution_measures_test_the_samples_behind_the_reported_means() -> None:
    suite = _suite(
        "dev",
        [
            _step_test(
                0,
                2,
                3,
                body_check_counts=(0, 2, 3),
                dispatch_event_request_builder_counts=(1, 4, 0),
            ),
            _step_test(
                0,
                5,
                body_check_counts=(0, 5),
                dispatch_event_request_builder_counts=(7, 0),
            ),
        ],
    )

    payload = compute(suite, [_suite("gen", [_step_test(1)])])

    builders = suite.statistics[HTTP_TEST_SEQUENCE_DISTRIBUTION][
        "request_dispatch_event_fan"
    ]["request_builder_fan_in"]
    body_checks = suite.statistics[ASSERTION_CLUSTERING_DISTRIBUTION][
        "per_sequence_assertion_count"
    ]["by_target"]["body"]["checks_per_bearing_sequence"]
    assert _measure(payload, "request_builders_per_step")["baseline"] == {
        "count": builders["count"],
        "mean": pytest.approx(builders["mean"]),
        "median": pytest.approx(builders["p50"]),
    }
    # Steps without a body check are outside the sample.
    assert _measure(payload, "body_checks_per_checking_step")["baseline"] == {
        "count": 3,
        "mean": pytest.approx(body_checks["mean"]),
        "median": pytest.approx(body_checks["p50"]),
    }


def test_descriptive_measure_carries_values_without_a_test() -> None:
    baseline = _suite("dev", [_step_test(1)])
    other = _suite("gen", [_step_test(1)])

    entry = _measure(compute(baseline, [other]), "distinct_header_names")

    assert entry == {
        "name": "distinct_header_names",
        "label": "Distinct header names",
        "kind": "descriptive",
        "baseline": {"value": 0},
        "comparisons": {"gen": {"value": 0}},
    }


# --- correction and summary --------------------------------------------------


_MULTI_STEP: dict[str, Any] = {
    "has_multiple_http_sequences": True,
    "all_dispatch_events_resolved": True,
    "re_dispatches_endpoint": True,
}


def test_empty_sample_is_untested_and_left_out_of_the_correction() -> None:
    multi_step_suite = _suite("dev", [_step_test(1, 1, **_MULTI_STEP)] * 2)
    single_step_suite = _suite("gen", [_step_test(1), _step_test(0)])
    untested = {
        "count": 0,
        "total": 0,
        "proportion": None,
        "test": None,
        "effect_size": None,
    }

    empty_compared = compute(multi_step_suite, [single_step_suite])
    empty_baseline = compute(single_step_suite, [multi_step_suite])

    name = "multi_step_tests_redispatching_endpoint"
    assert _measure(empty_compared, name)["comparisons"]["gen"] == untested
    assert _measure(empty_baseline, name)["comparisons"]["dev"] == {
        "count": 2,
        "total": 2,
        "proportion": pytest.approx(1.0),
        "test": None,
        "effect_size": None,
    }
    for payload in (empty_compared, empty_baseline):
        tested = _tested_comparisons(payload)
        assert payload["summary"]["tested_comparison_count"] == len(tested) < 19
        assert all("p_adjusted" in comparison["test"] for comparison in tested)


def test_p_values_are_holm_adjusted_over_every_tested_comparison() -> None:
    baseline = _suite("dev", [_step_test(1, 0, 0, 0, 0)] * 8)
    first = _suite("gen-a", [_step_test(1)] * 30)
    second = _suite("gen-b", [_step_test(1, 0)] * 6)

    payload = compute(baseline, [first, second])

    tested = _tested_comparisons(payload)
    assert len(tested) > 2
    assert [comparison["test"]["p_adjusted"] for comparison in tested] == pytest.approx(
        holm_adjust([comparison["test"]["p_value"] for comparison in tested])
    )
    assert all(
        comparison["test"]["significant"] == (comparison["test"]["p_adjusted"] < ALPHA)
        for comparison in tested
    )
    assert payload["summary"]["significant_count"] == sum(
        1 for comparison in tested if comparison["test"]["significant"]
    )
    assert payload["method"]["alpha"] == ALPHA
    assert payload["suites"] == ["gen-a", "gen-b"]
    assert payload["suite_sizes"] == {
        "dev": {"project_count": 1, "api_test_count": 8},
        "gen-a": {"project_count": 1, "api_test_count": 30},
        "gen-b": {"project_count": 1, "api_test_count": 6},
    }


def test_a_p_value_equal_to_alpha_is_not_significant() -> None:
    baseline = _suite("dev", [_step_test(1, 0, 0, 0, 0)] * 8)
    other = _suite("gen", [_step_test(1)] * 30)
    name = "steps_with_response_check"
    p_adjusted = _measure(compute(baseline, [other]), name)["comparisons"]["gen"][
        "test"
    ]["p_adjusted"]
    assert 0 < p_adjusted < 1

    at_alpha = compute(baseline, [other], alpha=p_adjusted)
    above_alpha = compute(baseline, [other], alpha=p_adjusted * 1.01)

    assert not _measure(at_alpha, name)["comparisons"]["gen"]["test"]["significant"]
    assert _measure(above_alpha, name)["comparisons"]["gen"]["test"]["significant"]


def test_identical_samples_are_all_negligible_and_not_significant() -> None:
    tests = [_step_test(1, 0), _step_test(1)]

    payload = compute(_suite("dev", tests), [_suite("gen", tests)])

    summary = payload["summary"]
    tested_count = summary["tested_comparison_count"]
    assert tested_count > 0
    assert summary["significant_count"] == 0
    assert summary["magnitude_counts"] == {
        "negligible": tested_count,
        "small": 0,
        "medium": 0,
        "large": 0,
    }
    assert len(summary["not_significant"]) == tested_count
    assert summary["significant_but_negligible"] == []


def test_summary_separates_negligible_from_not_significant_differences() -> None:
    reference = {"measure": "steps_with_response_check", "suite": "gen"}
    # 5,000 steps at 50% checked against 5,000 at 55%: significant, yet h < 0.2.
    large_samples = compute(
        _suite("dev", [_step_test(1, 0)] * 2500),
        [_suite("gen", [_step_test(1, 0)] * 2250 + [_step_test(1, 1)] * 250)],
    )
    # 1 of 3 steps checked against 3 of 3: a large h that 6 steps cannot establish.
    small_samples = compute(
        _suite("dev", [_step_test(1, 0, 0)]), [_suite("gen", [_step_test(1, 1, 1)])]
    )

    assert reference in large_samples["summary"]["significant_but_negligible"]
    assert reference not in large_samples["summary"]["not_significant"]
    comparison = _measure(small_samples, reference["measure"])["comparisons"]["gen"]
    assert comparison["effect_size"]["magnitude"] == "large"
    assert reference in small_samples["summary"]["not_significant"]
    assert reference not in small_samples["summary"]["significant_but_negligible"]


def test_empty_suites_yield_a_serializable_payload_without_tests() -> None:
    empty = build_suite("empty", [])
    populated = _suite("dev", [_step_test(1, 0)])

    for payload in (compute(populated, [empty]), compute(empty, [populated])):
        assert payload["summary"]["tested_comparison_count"] == 0
        assert payload["suite_sizes"]["empty"] == {
            "project_count": 0,
            "api_test_count": 0,
        }
        json.dumps(payload, allow_nan=False)
    assert compute(populated, [])["summary"]["tested_comparison_count"] == 0


def test_suite_names_must_be_distinct() -> None:
    suite = _suite("dev", [_step_test(1)])

    with pytest.raises(ValueError, match="distinct"):
        compute(suite, [suite])
    with pytest.raises(ValueError, match="distinct"):
        compute(suite, [_suite("gen", []), _suite("gen", [])])


# --- CLI ---------------------------------------------------------------------


def _write_suite(root: Path, name: str) -> Path:
    suite_root = root / name
    write_gerbil_output(suite_root, "service", project(tests=[api_test()]))
    return suite_root


def _invoke(baseline: str, suite: str, output_dir: Path) -> Any:
    return CliRunner().invoke(
        cli.app,
        [
            "suite-comparison",
            "--baseline",
            baseline,
            "--suite",
            suite,
            "--output-dir",
            str(output_dir),
            "--jobs",
            "1",
        ],
    )


def _report(output: str) -> dict[str, Any]:
    """The JSON report that follows the loading progress lines."""
    report: dict[str, Any] = json.loads(output[output.index("{\n") :])
    return report


def test_suite_comparison_cli_writes_the_payload(tmp_path: Path) -> None:
    developer_root = _write_suite(tmp_path, "developer")
    generated_root = _write_suite(tmp_path, "generated")
    output_dir = tmp_path / "out"

    result = _invoke(
        f"developer={developer_root}", f"generated={generated_root}", output_dir
    )

    assert result.exit_code == 0, result.output
    payload = json.loads((output_dir / "suite_comparison.json").read_text())
    assert payload["baseline"] == "developer"
    assert payload["suites"] == ["generated"]
    assert [entry["name"] for entry in payload["measures"]] == [
        measure.name for measure in MEASURES
    ]
    report = _report(result.output)
    assert report["loaded"] == {
        "developer": {"analyses": 1, "loaded": 1, "failed": 0},
        "generated": {"analyses": 1, "loaded": 1, "failed": 0},
    }
    assert report["failures"] == []


def test_suite_comparison_cli_writes_nothing_when_an_analysis_fails_to_load(
    tmp_path: Path,
) -> None:
    developer_root = _write_suite(tmp_path, "developer")
    generated_root = _write_suite(tmp_path, "generated")
    corrupt = generated_root / "broken" / "gerbil.json"
    corrupt.parent.mkdir()
    corrupt.write_text("{not json", encoding="utf-8")
    output_dir = tmp_path / "out"

    result = _invoke(
        f"developer={developer_root}", f"generated={generated_root}", output_dir
    )

    assert result.exit_code == 1
    report = _report(result.output)
    assert report["loaded"]["generated"] == {"analyses": 2, "loaded": 1, "failed": 1}
    assert [failure["path"] for failure in report["failures"]] == [str(corrupt)]
    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("suite_spec", "message"),
    [
        ("generated", "expected NAME=ROOT"),
        ("developer={root}/developer", "suite names must be distinct"),
        ("generated={root}/missing", "not a directory"),
        ("generated={root}/empty", "does not contain any gerbil.json"),
    ],
)
def test_suite_comparison_cli_rejects_invalid_suites(
    tmp_path: Path, suite_spec: str, message: str
) -> None:
    developer_root = _write_suite(tmp_path, "developer")
    (tmp_path / "empty").mkdir()
    output_dir = tmp_path / "out"

    result = _invoke(
        f"developer={developer_root}", suite_spec.format(root=tmp_path), output_dir
    )

    assert result.exit_code == 2
    assert message in result.output
    assert not output_dir.exists()
