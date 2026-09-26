"""Tests verifying that unrecognized call sites are rejected (not fabricated)."""

from __future__ import annotations

import logging

import pytest

from gerbil.analysis.http.classification import (
    _classify_call_site,
)
from gerbil.analysis.shared import CommonAnalysis
from gerbil.analysis.shared.receiver_resolution import resolve_receiver
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    make_call_site,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def _classify_call_site_for_testing(call_site):
    owner_class_name = "example.ApiTest"
    analysis = FakeJavaAnalysis(classes={owner_class_name: make_type()})
    common_analysis = CommonAnalysis(analysis)
    return _classify_call_site(
        call_site=call_site,
        receiver_resolver=lambda current_call_site: resolve_receiver(
            call_site=current_call_site,
            static_import_index=StaticImportIndex.EMPTY,
            owner_class_name=owner_class_name,
            owner_method_details=None,
            analysis=analysis,
            get_class_imports_for_class=common_analysis.get_class_imports,
            get_superclass_chain_for_class=common_analysis.get_superclass_chain,
        ),
    )


def test_unrecognized_receiver_with_path_returns_none_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A call site with a suggestive method name but no known receiver is skipped."""
    call_site = make_call_site(
        method_name="send",
        receiver_type="com.example.UnknownClient",
        argument_expr=['"/api/data"'],
    )
    with caplog.at_level(logging.DEBUG, logger="gerbil.analysis.http.classification"):
        result = _classify_call_site_for_testing(call_site)

    assert result is None
    assert any(
        "Skipping unrecognized HTTP-like call site" in msg for msg in caplog.messages
    )
    assert any("send" in msg for msg in caplog.messages)


def test_unrecognized_receiver_without_path_returns_none_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A call site with no path and no known receiver is silently skipped."""
    call_site = make_call_site(
        method_name="send",
        receiver_type="com.example.UnknownClient",
    )
    with caplog.at_level(logging.DEBUG, logger="gerbil.analysis.http.classification"):
        result = _classify_call_site_for_testing(call_site)

    assert result is None
    assert not any(
        "Skipping unrecognized HTTP-like call site" in msg for msg in caplog.messages
    )


def test_known_framework_still_classified() -> None:
    """MockMvc perform with a recognized receiver still produces a classification."""
    call_site = make_call_site(
        method_name="perform",
        receiver_type="org.springframework.test.web.servlet.MockMvc",
        argument_expr=['"/api/items"'],
    )
    result = _classify_call_site_for_testing(call_site)
    assert result is not None
    assert result.framework == "mockmvc"
