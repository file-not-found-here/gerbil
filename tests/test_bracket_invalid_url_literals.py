"""Bracket-broken URL literals (XPath selectors, dangling IPv6 brackets) must
degrade to unknown/rejected targets instead of raising or masquerading as
local paths. Degenerate relative literals (bare queries/fragments, scheme-less
double slashes) must normalize to clean local paths without leaking query
text or inventing an authority."""

from __future__ import annotations

import pytest

from gerbil.analysis.properties.endpoint.coverage import (
    _matched_endpoint_indices_for_candidate,
)
from gerbil.analysis.properties.endpoint.extraction import (
    normalize_observed_path_with_context,
)
from gerbil.analysis.schema import ApplicationEndpoint, EndpointCandidate
from gerbil.analysis.shared.http_mapping_annotations import (
    join_request_paths,
    normalize_path,
)
from gerbil.analysis.shared.url_utils import (
    classify_request_target,
    extract_query_param_names,
)

# A Selenium XPath literal as it appears in Java source: `@` splits a fake
# userinfo and the bracketed remainder fails IPv6 host validation.
XPATH_LITERAL = r"//iframe[@id=\"OverlayIFrame\"]"
# A concat fragment from IPv6 URL building (`"http://[" + host + "]"`).
DANGLING_BRACKET_URL = "http://["


class TestNormalizePathKeepsUnparseableLiteralsOpaque:
    @pytest.mark.parametrize("literal", [XPATH_LITERAL, DANGLING_BRACKET_URL])
    def test_returns_slash_prefixed_string_without_raising(self, literal: str) -> None:
        normalized = normalize_path(literal)
        assert normalized.startswith("/")

    @pytest.mark.parametrize(
        "literal", ["http://[?q=1", "http://[#frag", "http://[?q=1#frag"]
    )
    def test_strips_query_and_fragment_tails(self, literal: str) -> None:
        normalized = normalize_path(literal)
        assert "?" not in normalized
        assert "#" not in normalized


class TestNormalizePathDegenerateRelativeInputs:
    @pytest.mark.parametrize("literal", ["?page=2", "#frag", "?page=2#frag"])
    def test_bare_query_or_fragment_normalizes_to_root(self, literal: str) -> None:
        assert normalize_path(literal) == "/"

    def test_scheme_less_double_slash_collapses_to_local_path(self) -> None:
        # A leading "//" without a scheme is a concat artifact, not an
        # authority, so the first segment must stay in the path.
        assert normalize_path("//users/1") == "/users/1"

    def test_absolute_http_url_still_strips_scheme_and_authority(self) -> None:
        assert normalize_path("http://localhost:8080/users/1") == "/users/1"


class TestJoinRequestPathsPoisonsUnparseableInputs:
    def test_unparseable_method_path_is_returned_verbatim(self) -> None:
        joined = join_request_paths("http://localhost:8080", DANGLING_BRACKET_URL)
        assert joined == DANGLING_BRACKET_URL

    def test_unparseable_base_keeps_join_unparseable(self) -> None:
        joined = join_request_paths(DANGLING_BRACKET_URL, "/users")
        assert joined == "http://[/users"
        assert classify_request_target(joined, bare_token_is_local=False) == "unknown"


class TestClassifyRequestTargetRejectsUnparseableLiterals:
    @pytest.mark.parametrize("literal", [XPATH_LITERAL, DANGLING_BRACKET_URL])
    @pytest.mark.parametrize("bare_token_is_local", [True, False])
    def test_unparseable_literal_is_unknown(
        self, literal: str, bare_token_is_local: bool
    ) -> None:
        assert (
            classify_request_target(literal, bare_token_is_local=bare_token_is_local)
            == "unknown"
        )

    def test_parseable_protocol_relative_path_stays_local(self) -> None:
        assert (
            classify_request_target("//host/path", bare_token_is_local=False) == "local"
        )


class TestObservedPathRejection:
    @pytest.mark.parametrize("literal", [XPATH_LITERAL, DANGLING_BRACKET_URL])
    def test_unparseable_literal_is_not_a_request_path(self, literal: str) -> None:
        assert normalize_observed_path_with_context(literal) == (None, "unknown")

    def test_parseable_path_normalizes_with_context(self) -> None:
        assert normalize_observed_path_with_context("/users/1") == (
            "/users/1",
            "local",
        )

    def test_scheme_less_double_slash_normalizes_to_local_path(self) -> None:
        assert normalize_observed_path_with_context("//users/1") == (
            "/users/1",
            "local",
        )


def test_unparseable_candidate_matches_no_endpoints() -> None:
    # Without rejection, '/http:/[' segments would fullmatch the plain {var}
    # template matchers and falsely cover the endpoint.
    candidate = EndpointCandidate(
        http_method="GET",
        path=DANGLING_BRACKET_URL,
        source="test",
    )
    endpoints = [
        ApplicationEndpoint(
            http_method="GET",
            path_template="/{owner}/{repo}",
            framework="spring",
            declaring_class_name="example.Controller",
        )
    ]
    assert _matched_endpoint_indices_for_candidate(candidate, endpoints, ()) == set()


def test_query_params_are_not_extracted_from_unparseable_literals() -> None:
    assert extract_query_param_names("http://[?q=1&r=2") == set()
