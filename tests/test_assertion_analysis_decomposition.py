from __future__ import annotations

import inspect
import re

import gerbil.analysis.properties.assertion as assertion_analysis
import gerbil.analysis.properties.assertion.failure as assertion_failure
import gerbil.analysis.properties.assertion.oracle as assertion_oracle
import gerbil.analysis.properties.assertion.status_distribution as assertion_status_distribution
import gerbil.analysis.properties.assertion.surface as assertion_surface


def test_assertion_analysis_facade_reexports_runtime_api_from_submodules() -> None:
    assert (
        assertion_analysis.build_assertion_summary
        is assertion_surface.build_assertion_summary
    )
    assert (
        assertion_analysis.build_status_code_distribution
        is assertion_status_distribution.build_status_code_distribution
    )
    assert (
        assertion_analysis.build_status_code_counts
        is assertion_status_distribution.build_status_code_counts
    )
    assert (
        assertion_analysis.classify_oracle_type is assertion_oracle.classify_oracle_type
    )
    assert (
        assertion_analysis.classify_failure_scenarios
        is assertion_failure.classify_failure_scenarios
    )


def test_assertion_analysis_facade_has_no_private_algorithm_definitions() -> None:
    source = inspect.getsource(assertion_analysis)

    assert re.search(r"^def\s+_", source, flags=re.MULTILINE) is None
    assert re.search(r"^_[A-Z0-9_]+\s*=", source, flags=re.MULTILINE) is None


def test_assertion_analysis_all_exports_runtime_functions_only() -> None:
    assert set(assertion_analysis.__all__) == {
        "build_assertion_summary",
        "build_status_code_counts",
        "build_status_code_distribution",
        "classify_failure_scenarios",
        "classify_oracle_type",
    }
    assert all(not symbol.startswith("_") for symbol in assertion_analysis.__all__)
