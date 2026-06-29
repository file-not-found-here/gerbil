from __future__ import annotations

from gerbil.analysis.schema import PreconditionType, StateObservationMedium
from gerbil.statistics import state_conditions as state_conditions_stats
from gerbil.statistics.records import project_project, project_test
from tests.statistics_builders import (
    api_test,
    non_api_test,
    postcondition,
    precondition,
    project,
)


def test_projects_precondition_and_postcondition_labels() -> None:
    test = api_test(
        preconditions=[
            precondition(PreconditionType.DB_SEEDING),
            precondition(PreconditionType.DB_SEEDING),
            precondition(PreconditionType.FS_SEEDING),
        ],
        postconditions=[
            postcondition(StateObservationMedium.DB),
            postcondition(StateObservationMedium.FS),
        ],
    )

    record = project_test(test)

    assert record.precondition_types == ("db-seeding", "db-seeding", "fs-seeding")
    assert record.postcondition_types == ("db", "fs")


def test_condition_distributions_count_entries_and_labels_per_test() -> None:
    records = project_project(
        project(
            tests=[
                api_test(
                    preconditions=[
                        precondition(PreconditionType.DB_SEEDING),
                        precondition(PreconditionType.DB_SEEDING),
                        precondition(PreconditionType.FS_SEEDING),
                    ],
                    postconditions=[
                        postcondition(StateObservationMedium.DB),
                        postcondition(StateObservationMedium.FS),
                    ],
                ),
                api_test(
                    preconditions=[precondition(PreconditionType.MQ_SEEDING)],
                    postconditions=[
                        postcondition(StateObservationMedium.MQ),
                        postcondition(StateObservationMedium.MQ),
                    ],
                ),
                api_test(postconditions=[postcondition(StateObservationMedium.DB)]),
                # Non-API tests carry no state analysis and are excluded.
                non_api_test(),
            ]
        )
    )

    result = state_conditions_stats.compute(records.tests)

    assert result["scope"] == "api_tests"
    assert result["api_test_count"] == 3
    assert result["preconditions"]["entry_count_per_test"]["mean"] == 4 / 3
    assert result["postconditions"]["entry_count_per_test"]["mean"] == 5 / 3

    pre_by_label = result["preconditions"]["entry_count_per_label_per_test"]
    assert pre_by_label["db-seeding"]["max"] == 2.0
    assert pre_by_label["container-bootstrap"]["max"] == 0.0
    assert pre_by_label["fs-seeding"]["mean"] == 1 / 3

    post_by_label = result["postconditions"]["entry_count_per_label_per_test"]
    assert post_by_label["db"]["max"] == 1.0
    assert post_by_label["mq"]["max"] == 2.0
    assert post_by_label["fs"]["mean"] == 1 / 3

    pre_share = result["preconditions"]["type_share"]
    assert pre_share["total"] == 4
    assert pre_share["by_type"]["db-seeding"] == {
        "count": 2,
        "total": 4,
        "proportion": 0.5,
    }
    assert pre_share["by_type"]["container-bootstrap"] == {
        "count": 0,
        "total": 4,
        "proportion": 0.0,
    }

    post_share = result["postconditions"]["type_share"]
    assert post_share["total"] == 5
    assert post_share["by_type"]["mq"] == {
        "count": 2,
        "total": 5,
        "proportion": 0.4,
    }


def test_state_cooccurrence_counts_each_test_once_by_matching_subsystem() -> None:
    records = project_project(
        project(
            tests=[
                api_test(
                    preconditions=[
                        precondition(PreconditionType.DB_SEEDING),
                        precondition(PreconditionType.DB_SEEDING),
                    ],
                    postconditions=[
                        postcondition(StateObservationMedium.DB),
                        postcondition(StateObservationMedium.DB),
                    ],
                ),
                api_test(
                    preconditions=[precondition(PreconditionType.DB_SEEDING)],
                    postconditions=[postcondition(StateObservationMedium.MQ)],
                ),
                api_test(
                    preconditions=[precondition(PreconditionType.MQ_SEEDING)],
                    postconditions=[postcondition(StateObservationMedium.MQ)],
                ),
                api_test(
                    preconditions=[precondition(PreconditionType.CONTAINER_BOOTSTRAP)],
                    postconditions=[postcondition(StateObservationMedium.FS)],
                ),
            ]
        )
    )

    cooccurrence = state_conditions_stats.compute(records.tests)["state_cooccurrence"]

    assert cooccurrence["database"]["tests_with_precondition_and_postcondition"] == {
        "count": 1,
        "total": 4,
        "proportion": 0.25,
    }
    assert cooccurrence["message_queue"][
        "tests_with_precondition_and_postcondition"
    ] == {
        "count": 1,
        "total": 4,
        "proportion": 0.25,
    }
    assert cooccurrence["file_system"]["tests_with_precondition_and_postcondition"] == {
        "count": 0,
        "total": 4,
        "proportion": 0.0,
    }


def test_empty_inputs_yield_zeroed_condition_distributions() -> None:
    result = state_conditions_stats.compute([])

    assert result["api_test_count"] == 0
    assert result["preconditions"]["entry_count_per_test"]["count"] == 0
    assert result["preconditions"]["entry_count_per_test"]["mean"] is None
    assert result["preconditions"]["type_share"]["total"] == 0
    assert (
        result["preconditions"]["type_share"]["by_type"]["db-seeding"]["proportion"]
        is None
    )
    assert (
        result["postconditions"]["entry_count_per_label_per_test"]["db"]["count"] == 0
    )
    assert (
        result["state_cooccurrence"]["database"][
            "tests_with_precondition_and_postcondition"
        ]["proportion"]
        is None
    )
