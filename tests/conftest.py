from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that require CLDK and git submodules.",
    )
    parser.addoption(
        "--analysis-backend-path",
        action="store",
        default=None,
        help="Optional directory containing codeanalyzer-*.jar for CLDK Java analysis.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    from pathlib import Path

    run_integration = config.getoption("--run-integration")
    skip_marker = pytest.mark.skip(
        reason="pass --run-integration to run integration tests"
    )
    integration_root = Path(__file__).resolve().parent / "integration"

    for item in items:
        item_path = Path(str(item.fspath)).resolve()
        if integration_root not in item_path.parents:
            continue

        item.add_marker(pytest.mark.integration)
        if not run_integration:
            item.add_marker(skip_marker)
