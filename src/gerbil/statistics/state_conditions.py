"""Distributions over state preconditions and postconditions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from gerbil.statistics.distributions import count_share_entries, share, summarize
from gerbil.statistics.records import (
    POSTCONDITION_TYPES,
    PRECONDITION_TYPES,
    TestRecord,
)

_STATE_COOCCURRENCE: tuple[tuple[str, str, str], ...] = (
    ("database", "db-seeding", "db"),
    ("message_queue", "mq-seeding", "mq"),
    ("file_system", "fs-seeding", "fs"),
)


def _condition_payload(
    tests: Sequence[TestRecord], labels: Sequence[str], attribute: str
) -> dict[str, Any]:
    per_test_labels = [getattr(test, attribute) for test in tests]
    counts = Counter(
        label for labels_for_test in per_test_labels for label in labels_for_test
    )
    total = sum(counts.values())

    return {
        "entry_count_per_test": summarize(
            len(labels_for_test) for labels_for_test in per_test_labels
        ).to_dict(),
        "entry_count_per_label_per_test": {
            label: summarize(
                labels_for_test.count(label) for labels_for_test in per_test_labels
            ).to_dict()
            for label in labels
        },
        "type_share": {
            "total": total,
            "by_type": count_share_entries(counts, labels, total),
        },
    }


def _state_cooccurrence(tests: Sequence[TestRecord]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for name, precondition_type, postcondition_type in _STATE_COOCCURRENCE:
        payload[name] = {
            "precondition_type": precondition_type,
            "postcondition_type": postcondition_type,
            "tests_with_precondition_and_postcondition": share(
                precondition_type in test.precondition_types
                and postcondition_type in test.postcondition_types
                for test in tests
            ).to_dict(),
        }
    return payload


def compute(tests: Sequence[TestRecord]) -> dict[str, Any]:
    # State is only analyzed for API tests; non-API tests would contribute
    # structural zeros indistinguishable from "analyzed, none found".
    api_tests = [test for test in tests if test.is_api_test]
    return {
        "scope": "api_tests",
        "api_test_count": len(api_tests),
        "preconditions": _condition_payload(
            api_tests, PRECONDITION_TYPES, "precondition_types"
        ),
        "postconditions": _condition_payload(
            api_tests, POSTCONDITION_TYPES, "postcondition_types"
        ),
        "state_cooccurrence": _state_cooccurrence(api_tests),
    }
