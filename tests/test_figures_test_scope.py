from __future__ import annotations

import json
from typing import Any

from matplotlib.figure import Figure
from matplotlib.patches import PathPatch, Rectangle

from gerbil.analysis.schema import (
    HttpSequenceSummary,
    LifecyclePhase,
    ResourceInteractionSequence,
    ResourceInteractionStep,
)
from gerbil.figures.plotting import FigureOptions
from gerbil.figures.test_scope import build_dev_scope_sankey
from gerbil.statistics import test_scope as test_scope_stats
from gerbil.statistics.records import project_project
from tests.statistics_builders import api_test, non_api_test, project


def _sequence(resource_key: str, phase: LifecyclePhase) -> ResourceInteractionSequence:
    return ResourceInteractionSequence(
        resource_key=resource_key,
        steps=[
            ResourceInteractionStep(
                http_method="GET",
                path=resource_key,
                normalized_path=resource_key,
                event_order=1,
                phase=phase,
            )
        ],
    )


def _payload() -> dict[str, Any]:
    tests = [
        api_test(
            resource_sequences=[_sequence("/items", LifecyclePhase.TEST)],
            sequence_summary=HttpSequenceSummary(distinct_endpoint_count=2),
        ),
        api_test(
            resource_sequences=[_sequence("/items", LifecyclePhase.TEST)],
            sequence_summary=HttpSequenceSummary(distinct_endpoint_count=1),
        ),
        api_test(
            resource_sequences=[
                _sequence("/items", LifecyclePhase.TEST),
                _sequence("/users", LifecyclePhase.SETUP),
            ],
            sequence_summary=HttpSequenceSummary(distinct_endpoint_count=2),
        ),
        api_test(),
        non_api_test(is_controller_unit_test=True),
        non_api_test(),
    ]
    record = project_project(project(tests=tests))
    # Mirror the on-disk JSON round-trip the figures loader performs.
    return json.loads(json.dumps(test_scope_stats.compute(record.tests)))


def _texts(figure: Figure) -> list[str]:
    return [text.get_text() for text in figure.axes[0].texts]


def test_sankey_renders_every_populated_node_and_flow() -> None:
    figure = build_dev_scope_sankey(_payload(), FigureOptions())

    texts = _texts(figure)
    for label in (
        "API tests",
        ">1 focal resource",
        "1 focal resource",
        "No resource recovered",
        ">1 focal\nendpoint",
        "1 focal\nendpoint",
    ):
        assert any(label in text for text in texts)
    patches = figure.axes[0].patches
    # One rectangle per populated node (api plus its five descendants); the
    # excluded controller-unit test type adds no node.
    assert len([p for p in patches if isinstance(p, Rectangle)]) == 6
    # One flat ribbon per link: api fans into three buckets, single-resource
    # into two.
    assert len([p for p in patches if isinstance(p, PathPatch)]) == 5


def test_sankey_excludes_non_api_and_controller_tests() -> None:
    figure = build_dev_scope_sankey(_payload(), FigureOptions())

    texts = _texts(figure)
    assert not any("No endpoint resolved" in text for text in texts)
    assert not any("Other tests" in text for text in texts)
    assert not any("Controller unit tests" in text for text in texts)


def test_sankey_handles_empty_corpus() -> None:
    payload = json.loads(json.dumps(test_scope_stats.compute([])))

    figure = build_dev_scope_sankey(payload, FigureOptions())

    assert isinstance(figure, Figure)
    assert not figure.axes[0].patches
