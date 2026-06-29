from __future__ import annotations

import pytest

from gerbil.statistics import parameterization as parameterization_stats
from gerbil.statistics.records import CRUD_OPERATIONS, HTTP_METHODS, TestRecord


def make_test(
    *,
    is_api_test: bool = True,
    is_parameterized: bool = False,
    static_sources: tuple[str, ...] = (),
    dynamic_sources: tuple[str, ...] = (),
) -> TestRecord:
    return TestRecord(
        is_api_test=is_api_test,
        is_controller_unit_test=False,
        expanded_ncloc=0,
        expanded_cyclomatic_complexity=0,
        expanded_helper_method_count=0,
        test_helper_method_count=0,
        expanded_objects_created=0,
        expanded_assertion_count=0,
        mocked_interaction_count=0,
        dependency_strategy_label_count=0,
        dispatch_labels=(),
        has_read_after_write=False,
        has_cleanup_delete=False,
        resource_lifecycle_labels=(),
        setup_fixture_count=0,
        teardown_fixture_count=0,
        status_range_counts=(0, 0, 0, 0, 0, 0),
        builder_counts=(0, 0, 0),
        event_counts=(0, 0, 0),
        verification_counts=(0, 0, 0),
        fixture_builder_phase_counts=(0, 0),
        fixture_event_phase_counts=(0, 0),
        fixture_verification_phase_counts=(0, 0),
        http_method_counts=(0,) * len(HTTP_METHODS),
        crud_operation_counts=(0,) * len(CRUD_OPERATIONS),
        is_parameterized=is_parameterized,
        parameterization_static_sources=static_sources,
        parameterization_dynamic_sources=dynamic_sources,
    )


def test_parameterized_share_split_by_cohort() -> None:
    tests = [
        make_test(is_parameterized=True, static_sources=("ValueSource",)),
        make_test(),
        make_test(is_api_test=False, is_parameterized=True),
        make_test(is_api_test=False),
        make_test(is_api_test=False),
    ]

    payload = parameterization_stats.compute(tests)

    assert payload["api"]["test_count"] == 2
    assert payload["api"]["parameterized"] == {
        "count": 1,
        "total": 2,
        "proportion": pytest.approx(0.5),
    }
    assert payload["non_api"]["parameterized"] == {
        "count": 1,
        "total": 3,
        "proportion": pytest.approx(1 / 3),
    }


def test_source_kinds_partition_parameterized_tests() -> None:
    tests = [
        make_test(is_parameterized=True, static_sources=("CsvSource",)),
        make_test(
            is_parameterized=True,
            static_sources=("ValueSource",),
            dynamic_sources=("MethodSource",),
        ),
        make_test(is_parameterized=True, dynamic_sources=("MethodSource",)),
        # @ParameterizedTest with a provider outside the curated mapping.
        make_test(is_parameterized=True),
        make_test(),
    ]

    source_kinds = parameterization_stats.compute(tests)["api"]["source_kinds"]

    assert source_kinds["test_count"] == 4
    by_kind = source_kinds["by_kind"]
    assert by_kind["static_only"]["count"] == 1
    assert by_kind["dynamic_only"]["count"] == 1
    assert by_kind["mixed"]["count"] == 1
    assert by_kind["no_recognized_source"]["count"] == 1
    assert by_kind["mixed"]["proportion"] == pytest.approx(0.25)


def test_source_annotations_pool_across_kinds_sorted() -> None:
    tests = [
        make_test(
            is_parameterized=True,
            static_sources=("CsvSource", "ValueSource"),
            dynamic_sources=("MethodSource",),
        ),
        make_test(is_parameterized=True, static_sources=("ValueSource",)),
    ]

    annotations = parameterization_stats.compute(tests)["api"]["source_annotations"]

    assert annotations["total"] == 4
    assert list(annotations["by_annotation"]) == [
        "CsvSource",
        "MethodSource",
        "ValueSource",
    ]
    assert annotations["by_annotation"]["ValueSource"]["count"] == 2
    assert annotations["by_annotation"]["ValueSource"]["proportion"] == pytest.approx(
        0.5
    )


def test_empty_cohorts_report_none_proportions() -> None:
    payload = parameterization_stats.compute([])

    assert payload["api"]["test_count"] == 0
    assert payload["api"]["parameterized"]["proportion"] is None
    assert payload["api"]["source_kinds"]["test_count"] == 0
