from __future__ import annotations

import pytest

from gerbil.analysis.shared.url_utils import (
    classify_request_target,
    is_external_http_url,
    safe_urlparse,
)


class TestSafeUrlparse:
    def test_parses_ordinary_url(self) -> None:
        parsed = safe_urlparse("https://api.example.com/v1?expand=true")
        assert parsed is not None
        assert parsed.hostname == "api.example.com"
        assert parsed.path == "/v1"

    def test_parses_bracketed_ipv6_url(self) -> None:
        parsed = safe_urlparse("http://[::1]:8080/api")
        assert parsed is not None
        assert parsed.hostname == "::1"

    def test_returns_none_for_xpath_selector_literal(self) -> None:
        assert safe_urlparse(r"//iframe[@id=\"OverlayIFrame\"]") is None

    def test_returns_none_for_dangling_bracket_authority(self) -> None:
        assert safe_urlparse("http://[") is None

    def test_rejects_non_str_input(self) -> None:
        # urlparse(None) would silently coerce through the bytes path and
        # bypass the `parsed is None` guards.
        with pytest.raises(TypeError):
            safe_urlparse(None)  # type: ignore[arg-type]


class TestClassifyRequestTarget:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/api/users", "local"),
            ("http://localhost:8080/api", "local"),
            ("https://api.example.com/v1", "external"),
            ("ftp://example.com/file", "unknown"),
            ("", "unknown"),
            ("//host/path", "local"),
        ],
    )
    def test_common_targets(self, path: str, expected: str) -> None:
        assert classify_request_target(path, bare_token_is_local=False) == expected

    def test_bare_token_locality_is_caller_controlled(self) -> None:
        assert classify_request_target("users", bare_token_is_local=True) == "local"
        assert classify_request_target("users", bare_token_is_local=False) == "unknown"

    @pytest.mark.parametrize("path", [r"//iframe[@id=\"OverlayIFrame\"]", "http://["])
    def test_unparseable_literals_are_unknown(self, path: str) -> None:
        assert classify_request_target(path, bare_token_is_local=True) == "unknown"


class TestIsExternalHttpUrl:
    def test_hostless_url_is_not_external(self) -> None:
        assert is_external_http_url("http:///path") is False

    def test_empty_host_https_is_not_external(self) -> None:
        assert is_external_http_url("https:///some/path") is False

    def test_real_external_url_is_external(self) -> None:
        assert is_external_http_url("https://api.example.com/v1") is True

    def test_localhost_is_not_external(self) -> None:
        assert is_external_http_url("http://localhost:8080/api") is False

    def test_loopback_ip_is_not_external(self) -> None:
        assert is_external_http_url("http://127.0.0.1:8080/api") is False

    def test_rfc1918_10_range_is_not_external(self) -> None:
        assert is_external_http_url("http://10.1.2.3/service") is False

    def test_rfc1918_172_private_range_is_not_external(self) -> None:
        assert is_external_http_url("https://172.16.10.5/service") is False

    def test_rfc1918_172_public_boundary_remains_external(self) -> None:
        assert is_external_http_url("https://172.32.10.5/service") is True

    def test_rfc1918_192_168_range_is_not_external(self) -> None:
        assert is_external_http_url("http://192.168.1.10/service") is False

    def test_link_local_ipv4_is_not_external(self) -> None:
        assert is_external_http_url("http://169.254.10.20/service") is False

    def test_public_ip_http_url_is_external(self) -> None:
        assert is_external_http_url("https://8.8.8.8/dns-query") is True

    def test_non_http_scheme_is_not_external(self) -> None:
        assert is_external_http_url("ftp://example.com/file") is False

    def test_empty_string_is_not_external(self) -> None:
        assert is_external_http_url("") is False

    def test_dangling_bracket_authority_is_not_external(self) -> None:
        assert is_external_http_url("http://[") is False
