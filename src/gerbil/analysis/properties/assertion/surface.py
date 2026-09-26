"""Assertion summary builder.

Reads ``node.assertion_classification`` set by the assertion classification
pass and produces an ``AssertionSummary`` with per-role counts for all five
``AssertionRole`` variants.
"""

from __future__ import annotations

from gerbil.analysis.runtime import TestRuntimeView
from gerbil.analysis.schema import AssertionRole, AssertionSummary


def build_assertion_summary(
    *,
    runtime_view: TestRuntimeView,
) -> AssertionSummary:
    counts: dict[AssertionRole, int] = {role: 0 for role in AssertionRole}

    for event in runtime_view.iter_events():
        ac = event.node.assertion_classification
        if ac is None or not ac.is_countable:
            continue
        counts[ac.role] += 1

    return AssertionSummary(
        **{f"{role.value}_count": counts[role] for role in AssertionRole}
    )


__all__ = ["build_assertion_summary"]
