from __future__ import annotations

import pytest

from gerbil.analysis.properties.resource_interaction.path_normalization import (
    normalize_production_resource_key,
    normalize_request_path,
    resource_key,
    strip_application_path_prefix,
)


# ---------------------------------------------------------------------------
# normalize_request_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        ("/users/42", "/users/{id}"),
        ("/orgs/9/users/42", "/orgs/{id}/users/{id}"),
    ],
)
def test_normalize_request_path_replaces_numeric_segments(
    raw_path: str,
    expected: str,
) -> None:
    assert normalize_request_path(raw_path) == expected


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        (
            "/users/550e8400-e29b-41d4-a716-446655440000",
            "/users/{id}",
        ),
        (
            "/users/550E8400-E29B-41D4-A716-446655440000",
            "/users/{id}",
        ),
    ],
)
def test_normalize_request_path_replaces_uuid_like_segments(
    raw_path: str,
    expected: str,
) -> None:
    assert normalize_request_path(raw_path) == expected


def test_normalize_request_path_replaces_mixed_numeric_and_uuid() -> None:
    path = "/orgs/9/users/550e8400-e29b-41d4-a716-446655440000"
    assert normalize_request_path(path) == "/orgs/{id}/users/{id}"


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        (
            "/orders/507f1f77bcf86cd799439011",
            "/orders/{id}",
        ),
        (
            "/items/550e8400e29b41d4a716446655440000",
            "/items/{id}",
        ),
        (
            "/reports/2024-01-15",
            "/reports/{id}",
        ),
    ],
)
def test_normalize_request_path_replaces_hex_and_date_id_segments(
    raw_path: str,
    expected: str,
) -> None:
    assert normalize_request_path(raw_path) == expected


def test_normalize_request_path_preserves_non_id_segments() -> None:
    assert normalize_request_path("/api/v1/users") == "/api/v1/users"


def test_normalize_request_path_preserves_short_hex_word_segments() -> None:
    # Only exact 24- and 32-character hex strings are abstracted; an 8-char
    # word like "deadbeef" is short enough to be a real route word.
    assert normalize_request_path("/colors/deadbeef") == "/colors/deadbeef"


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        ("/users/{userId}", "/users/{id}"),
        ("/orders/{id:[0-9]+}", "/orders/{id}"),
        ("/orders/{id:\\d{4}}", "/orders/{id}"),
        ("/users/:id", "/users/{id}"),
        ("/products/{name}", "/products/{id}"),
        ("/products/{p}/configurations/{c}", "/products/{id}/configurations/{id}"),
        ("/users/%s", "/users/{id}"),
        ("/users/%d", "/users/{id}"),
        ("/users/%x", "/users/{id}"),
        ("/users/%10s", "/users/{id}"),
        ("/users/%2$5d", "/users/{id}"),
    ],
)
def test_normalize_request_path_replaces_template_variable_segments(
    raw_path: str,
    expected: str,
) -> None:
    # Request paths are extracted from source string literals only, so a
    # {variable} segment is an author-written URI template, not an
    # unresolved value.
    assert normalize_request_path(raw_path) == expected


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        ("/users/42?expand=true", "/users/{id}"),
        ("/users/42?expand=true&include=roles", "/users/{id}"),
    ],
)
def test_normalize_request_path_strips_query_parameters(
    raw_path: str,
    expected: str,
) -> None:
    assert normalize_request_path(raw_path) == expected


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        ("/users/42/", "/users/{id}"),
        ("/users/42///", "/users/{id}"),
        ("/", "/"),
    ],
)
def test_normalize_request_path_canonicalizes_trailing_slashes(
    raw_path: str,
    expected: str,
) -> None:
    assert normalize_request_path(raw_path) == expected


@pytest.mark.parametrize("raw_path", [None, "", "   "])
def test_normalize_request_path_returns_none_for_empty(
    raw_path: str | None,
) -> None:
    assert normalize_request_path(raw_path) is None


@pytest.mark.parametrize(
    "raw_path",
    [r"//iframe[@id=\"OverlayIFrame\"]", "http://["],
)
def test_normalize_request_path_returns_none_for_bracket_invalid_authorities(
    raw_path: str,
) -> None:
    assert normalize_request_path(raw_path) is None


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        ("http://localhost:8080/users/1", "/users/{id}"),
        ("https://127.0.0.1/api/v1/users/99?expand=true", "/api/v1/users/{id}"),
        ("http://localhost:8080", "/"),
        ("https://localhost:8080/", "/"),
    ],
)
def test_normalize_request_path_strips_local_absolute_url_components(
    raw_path: str,
    expected: str,
) -> None:
    assert normalize_request_path(raw_path) == expected


@pytest.mark.parametrize(
    "raw_path",
    [
        "https://api.github.com/users/login",
        "https://example.com/api/v1/users/99",
        "http://external.host/users/1",
    ],
)
def test_normalize_request_path_rejects_external_absolute_urls(
    raw_path: str,
) -> None:
    assert normalize_request_path(raw_path) is None


def test_normalize_request_path_collapses_scheme_less_double_slash() -> None:
    # A leading "//" without a scheme is a concat artifact, not an authority,
    # so the first segment must remain part of the path.
    assert normalize_request_path("//users/1") == "/users/{id}"
    assert normalize_request_path("///users/1") == "/users/{id}"


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        # A single template group spanning the whole segment abstracts.
        ("/orders/{id:\\d{4}}", "/orders/{id}"),
        # Multiple template groups or embedded literal text stay literal.
        ("/files/{a}{b}", "/files/{a}{b}"),
        ("/files/{a:\\d+}{b}", "/files/{a:\\d+}{b}"),
        ("/files/{name:.+}.{ext}", "/files/{name:.+}.{ext}"),
        ("/files/literal{x}", "/files/literal{x}"),
    ],
)
def test_normalize_request_path_preserves_non_single_template_segments(
    raw_path: str,
    expected: str,
) -> None:
    assert normalize_request_path(raw_path) == expected


@pytest.mark.parametrize(
    "raw_path",
    [
        # Only angle-bracketed placeholders are unresolved sentinels.
        "<dynamic>",
        "<unknown>",
        "<unresolved>",
        "/users/${id}",
        "/users/*",
        "/files/**",
        "/files/{*path}",
        "/users/user-%s",
    ],
)
def test_normalize_request_path_rejects_unresolved_and_wildcards(
    raw_path: str,
) -> None:
    assert normalize_request_path(raw_path) is None


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        # Bare "dynamic"/"unknown"/"unresolved" are real route segments, not
        # unresolved markers, so they must survive normalization.
        ("/api/apps/{monitorId}/define/dynamic", "/api/apps/{id}/define/dynamic"),
        ("/instances/unknown", "/instances/unknown"),
        ("/dynamic/config/value", "/dynamic/config/value"),
        ("/dynamic", "/dynamic"),
        ("/api/admin/users/unresolved", "/api/admin/users/unresolved"),
    ],
)
def test_normalize_request_path_preserves_literal_sentinel_word_segments(
    raw_path: str,
    expected: str,
) -> None:
    assert normalize_request_path(raw_path) == expected


# ---------------------------------------------------------------------------
# resource_key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("normalized_path", "expected_key"),
    [
        ("/users/{id}", "/users"),
        ("/orgs/{id}/users/{id}", "/orgs/{id}/users"),
        ("/users", "/users"),
        ("/api/v1/items", "/api/v1/items"),
        ("/", "/"),
    ],
)
def test_resource_key_strips_trailing_id(
    normalized_path: str,
    expected_key: str,
) -> None:
    assert resource_key(normalized_path) == expected_key


# ---------------------------------------------------------------------------
# normalize_production_resource_key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path_template", "expected_key"),
    [
        ("/users", "/users"),
        ("/users/{userId}", "/users"),
        ("/orders/{id:[0-9]+}", "/orders"),
        ("/orders/{id:[0-9]*}", "/orders"),
        ("/orders/{id:\\d{4}}", "/orders"),
        ("/files/{name:.*}", "/files"),
        ("/orgs/{orgId}/users/{userId}", "/orgs/{id}/users"),
        ("http://localhost:8080/api/users/{id}", "/api/users"),
    ],
)
def test_normalize_production_resource_key_accepts_template_segments(
    path_template: str,
    expected_key: str,
) -> None:
    assert normalize_production_resource_key(path_template) == expected_key


@pytest.mark.parametrize(
    ("path_template", "expected_key"),
    [
        # Regex quantifiers inside a single template group abstract correctly.
        ("/orders/{id:\\d{4}}", "/orders"),
        # Segments that are not one single template group stay literal.
        ("/files/{a}{b}", "/files/{a}{b}"),
        ("/files/{a:\\d+}{b}", "/files/{a:\\d+}{b}"),
        ("/files/{name:.+}.{ext}", "/files/{name:.+}.{ext}"),
        ("/files/literal{x}", "/files/literal{x}"),
    ],
)
def test_normalize_production_resource_key_keeps_non_single_template_segments_literal(
    path_template: str,
    expected_key: str,
) -> None:
    assert normalize_production_resource_key(path_template) == expected_key


@pytest.mark.parametrize(
    "path_template",
    [
        "",
        "<unknown>",
        "/files/**",
        "/files/{*path}",
        "/users/*",
    ],
)
def test_normalize_production_resource_key_returns_none_for_unusable_templates(
    path_template: str,
) -> None:
    assert normalize_production_resource_key(path_template) is None


@pytest.mark.parametrize(
    ("path_template", "expected_key"),
    [
        # Endpoint templates with a literal "dynamic"/"unknown" segment yield a
        # usable production resource key rather than being discarded.
        ("/api/apps/{monitorId}/define/dynamic", "/api/apps/{id}/define/dynamic"),
        ("/dynamic/{taskCategory}/taskTypes", "/dynamic/{id}/taskTypes"),
        (
            "/manager/chargepoints/unknown/add/{idTag}",
            "/manager/chargepoints/unknown/add",
        ),
    ],
)
def test_normalize_production_resource_key_accepts_literal_sentinel_word_segments(
    path_template: str,
    expected_key: str,
) -> None:
    assert normalize_production_resource_key(path_template) == expected_key


# ---------------------------------------------------------------------------
# strip_application_path_prefix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("normalized_path", "prefix", "expected"),
    [
        ("/rest/quotes", "/rest", "/quotes"),
        ("/rest/api/quotes", "/rest/api", "/quotes"),
        ("/rest", "/rest", "/"),
        ("/rest/quotes", "rest/", "/quotes"),
    ],
)
def test_strip_application_path_prefix_removes_leading_segments(
    normalized_path: str,
    prefix: str,
    expected: str,
) -> None:
    assert strip_application_path_prefix(normalized_path, prefix) == expected


@pytest.mark.parametrize(
    ("normalized_path", "prefix"),
    [
        # Whole-segment match only: /api must not strip /apiserver.
        ("/apiserver/items", "/api"),
        ("/quotes", "/rest"),
        ("/rest/quotes", "/rest/api"),
        # Root-equivalent prefixes never strip.
        ("/rest/quotes", "/"),
        ("/rest/quotes", ""),
        # ID-like prefixes abstract to {id} and would strip any ID-led path.
        ("/{id}/quotes", "/1"),
        ("/{id}/quotes", "/550e8400-e29b-41d4-a716-446655440000"),
        ("/rest/{id}/quotes", "/rest/7"),
    ],
)
def test_strip_application_path_prefix_rejects_non_leading_prefixes(
    normalized_path: str,
    prefix: str,
) -> None:
    assert strip_application_path_prefix(normalized_path, prefix) is None


def test_empty_constraint_template_segment_stays_literal() -> None:
    assert normalize_production_resource_key("/orders/{id:}") == "/orders/{id:}"
