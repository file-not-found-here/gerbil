"""Tests whether the projects selected for the comparison subset differ from the
other corpus projects on the tested measures of the paper's comparison table,
each taken once per project."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gerbil.statistics.distributions import summarize
from gerbil.statistics.records import ProjectStatsRecord, api_test_count
from gerbil.statistics.suite_comparison import (
    ALPHA,
    CLIFFS_DELTA_THRESHOLDS,
    MAGNITUDES,
    MEASURES,
    DistributionMeasure,
    ProportionMeasure,
    Suite,
    build_suite,
    holm_adjust,
    mann_whitney_comparison,
)

SUBSET_SELECTION = "subset_selection"

# Scope: projects with >= 10 API tests and >= 1 identified application endpoint.
# Below 10 API tests a per-project share swings by 10+ points on a single test,
# and such a project could never have been selected; one without endpoints
# (mostly libraries and frameworks) fails the subset's own eligibility rule,
# which needs requests attributed to endpoints.
MIN_API_TEST_COUNT = 10
MIN_ENDPOINT_COUNT = 1
SCOPE = "projects_with_min_api_tests_and_endpoints"
SCOPE_DESCRIPTION = (
    f"Projects with at least {MIN_API_TEST_COUNT} API tests and at least "
    f"{MIN_ENDPOINT_COUNT} identified application endpoint."
)

# Spearman's p-value uses a t-distribution with n - 2 degrees of freedom.
_MIN_CORRELATION_SAMPLE = 3

TestedMeasure = ProportionMeasure | DistributionMeasure

# The comparison table leaves its descriptive rows untested, and two of them (a
# vocabulary size, a maximum) grow with a suite's size, so a project's value
# would mostly restate the API test count the subset was selected on.
TESTED_MEASURES: tuple[TestedMeasure, ...] = tuple(
    measure
    for measure in MEASURES
    if isinstance(measure, ProportionMeasure | DistributionMeasure)
)


@dataclass(frozen=True)
class ProjectValue:
    """A project's value of one measure and the units it is taken over."""

    value: float | None
    units: int

    def to_dict(self) -> dict[str, float | int | None]:
        return {"value": self.value, "units": self.units}


def project_value(measure: TestedMeasure, suite: Suite) -> ProjectValue:
    """The measure over one project's suite: the share for a proportion, the mean
    for a count per unit; undefined when the project has none of its units."""
    if isinstance(measure, ProportionMeasure):
        share = measure.share(suite)
        return ProjectValue(value=share.proportion, units=share.total)
    values = measure.values(suite)
    return ProjectValue(
        value=sum(values) / len(values) if values else None, units=len(values)
    )


def is_in_scope(record: ProjectStatsRecord) -> bool:
    return (
        api_test_count(record) >= MIN_API_TEST_COUNT
        and len(record.endpoints) >= MIN_ENDPOINT_COUNT
    )


def spearman_correlation(
    xs: Sequence[float], ys: Sequence[float]
) -> tuple[float | None, float | None]:
    """Two-sided Spearman (rho, p), or (None, None) when undefined: fewer than
    three samples, or a constant input (no ranking to correlate)."""
    # Imported lazily: scipy.stats takes ~1s to import, and the batch commands
    # re-import the CLI (and with it this package) in every spawned worker.
    from scipy.stats import spearmanr

    if len(xs) < _MIN_CORRELATION_SAMPLE or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None, None
    result = spearmanr(xs, ys)
    return float(result.statistic), float(result.pvalue)


def _project_row(record: ProjectStatsRecord, *, selected: bool) -> dict[str, Any]:
    suite = build_suite(record.dataset_name, [record])
    return {
        "dataset_name": record.dataset_name,
        "selected": selected,
        "api_test_count": api_test_count(record),
        "endpoint_count": len(record.endpoints),
        "measures": {
            measure.name: project_value(measure, suite).to_dict()
            for measure in TESTED_MEASURES
        },
    }


def _group_summary(values: Sequence[float]) -> dict[str, Any]:
    summary = summarize(values)
    return {
        "project_count": summary.count,
        "min": summary.min,
        "p25": summary.p25,
        "median": summary.p50,
        "p75": summary.p75,
        "max": summary.max,
    }


def _measure_entry(
    measure: TestedMeasure, rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    defined = [
        (row["api_test_count"], row["selected"], value)
        for row in rows
        if (value := row["measures"][measure.name]["value"]) is not None
    ]
    selected = [value for _, is_selected, value in defined if is_selected]
    rest = [value for _, is_selected, value in defined if not is_selected]
    rho, p_value = spearman_correlation(
        [count for count, _, _ in defined], [value for _, _, value in defined]
    )
    return {
        "name": measure.name,
        "label": measure.label,
        "kind": measure.kind,
        "unit": measure.unit,
        "selected": _group_summary(selected),
        "rest": _group_summary(rest),
        **mann_whitney_comparison(selected, rest),
        "api_test_count_correlation": {
            "project_count": len(defined),
            "rho": rho,
            "p_value": p_value,
        },
    }


def _adjust(tests: Sequence[dict[str, Any]], alpha: float) -> None:
    """Adds the Holm-adjusted p-value and its significance to each test."""
    adjusted = holm_adjust([test["p_value"] for test in tests])
    for test, p_adjusted in zip(tests, adjusted):
        test["p_adjusted"] = p_adjusted
        test["significant"] = p_adjusted < alpha


def _names(names: Collection[str]) -> str:
    return ", ".join(sorted(names))


def compute(
    records: Sequence[ProjectStatsRecord],
    selected: Collection[str],
    *,
    alpha: float = ALPHA,
) -> dict[str, Any]:
    """Serializable payload: per tested measure, the selected and remaining
    projects' values, their Mann-Whitney U test and Cliff's delta, and the
    Spearman correlation with API test count, with each family of p-values
    Holm-adjusted; plus the per-project values they were computed from."""
    selected_names = set(selected)
    if not selected_names:
        raise ValueError("no project is selected")
    pool = [record for record in records if is_in_scope(record)]
    pool_names = Counter(record.dataset_name for record in pool)
    # A name selects every record that carries it, which would count one project
    # twice (an input root holding two analyses of it, for instance).
    if duplicates := {name for name, count in pool_names.items() if count > 1}:
        raise ValueError(f"projects share a dataset name: {_names(duplicates)}")
    if absent := selected_names - {record.dataset_name for record in records}:
        raise ValueError(f"selected projects are not in the corpus: {_names(absent)}")
    if out_of_scope := selected_names - set(pool_names):
        raise ValueError(
            f"selected projects are out of scope ({SCOPE_DESCRIPTION}): "
            f"{_names(out_of_scope)}"
        )
    if len(pool) == len(selected_names):
        raise ValueError("every in-scope project is selected; none is left to compare")

    rows = sorted(
        (
            _project_row(record, selected=record.dataset_name in selected_names)
            for record in pool
        ),
        key=lambda row: (-row["api_test_count"], row["dataset_name"]),
    )
    measures = [_measure_entry(measure, rows) for measure in TESTED_MEASURES]

    tested = [entry for entry in measures if entry["test"] is not None]
    _adjust([entry["test"] for entry in tested], alpha)
    correlated = [
        entry
        for entry in measures
        if entry["api_test_count_correlation"]["p_value"] is not None
    ]
    _adjust([entry["api_test_count_correlation"] for entry in correlated], alpha)

    magnitude_counts = {name: 0 for name in MAGNITUDES}
    for entry in tested:
        magnitude_counts[entry["effect_size"]["magnitude"]] += 1

    return {
        "scope": SCOPE,
        "scope_description": SCOPE_DESCRIPTION,
        "min_api_test_count": MIN_API_TEST_COUNT,
        "min_endpoint_count": MIN_ENDPOINT_COUNT,
        "analyzed_project_count": len(records),
        "project_count": len(rows),
        "selected_project_count": len(selected_names),
        "rest_project_count": len(rows) - len(selected_names),
        "selected_projects": sorted(selected_names),
        "method": {
            "project_value": (
                "Each measure is taken once per project: the share for a "
                "proportion measure, the mean for a distribution measure. A "
                "project without any unit of a measure is left out of that measure."
            ),
            "test": "mann_whitney_u",
            "implementation": "scipy.stats.mannwhitneyu with its default method",
            "effect_size": "cliffs_delta",
            "effect_sign": "positive when selected projects tend to be higher",
            "magnitude_thresholds": dict(zip(MAGNITUDES[1:], CLIFFS_DELTA_THRESHOLDS)),
            "api_test_count_correlation": (
                "Two-sided Spearman rank correlation between API test count and "
                "the per-project value, over the selected and remaining projects "
                "together; undefined below three projects or for a constant value."
            ),
            "alternative": "two-sided",
            "correction": (
                "holm, separately over the selection tests and over the correlations"
            ),
            "alpha": alpha,
            "untested": (
                "A measure with no defined value among the selected or among the "
                "remaining projects has no test or effect size and is left out of "
                "the correction."
            ),
            "limitations": (
                "The selected projects are few, so the test has little power; "
                "read a non-significant difference with its effect size. A "
                "project counts once per measure however few units it has, so a "
                "share over a handful of units weighs as much as one over "
                "hundreds; each project's units are listed with its value."
            ),
        },
        "summary": {
            "tested_measure_count": len(tested),
            "significant": [
                entry["name"] for entry in tested if entry["test"]["significant"]
            ],
            "magnitude_counts": magnitude_counts,
            "significant_correlations": [
                entry["name"]
                for entry in correlated
                if entry["api_test_count_correlation"]["significant"]
            ],
        },
        "measures": measures,
        "projects": rows,
    }
