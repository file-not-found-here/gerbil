"""Extraction-time detection of concatenation-truncated request paths."""

from __future__ import annotations

from collections.abc import Callable

from gerbil.analysis.http.classification import _ExtractedPath, _extract_path


def _extract(expression: str, method_name: str = "getForEntity") -> _ExtractedPath:
    return _extract_path(argument_exprs=[expression], method_name=method_name)


def _extract_with_resolver(
    expression: str,
    resolve_expression: Callable[[str], str | None],
    method_name: str = "getForEntity",
) -> _ExtractedPath:
    return _extract_path(
        argument_exprs=[expression],
        method_name=method_name,
        resolve_expression=resolve_expression,
    )


# A trailing-slash literal followed by `+` records a truncated path.


def test_leading_literal_followed_by_concatenation_is_truncated() -> None:
    assert _extract('"/users/" + id') == ("/users/", True)
    assert _extract('"/oauth2/authorization/" + registrationId') == (
        "/oauth2/authorization/",
        True,
    )
    assert _extract('"http://localhost:8080/users/" + id') == (
        "http://localhost:8080/users/",
        True,
    )


def test_relative_literal_followed_by_concatenation_is_truncated() -> None:
    assert _extract('"users/" + id') == ("/users/", True)


def test_multi_part_concatenation_marks_the_leading_literal_truncated() -> None:
    assert _extract('"/users/" + id + "/pets"') == ("/users/", True)


# Literals without a dynamic tail are never truncated.


def test_literal_without_concatenation_is_not_truncated() -> None:
    assert _extract('"/users/"') == ("/users/", False)
    assert _extract('"/users"') == ("/users", False)


def test_literal_without_trailing_slash_is_not_truncated() -> None:
    assert _extract('"/users" + id') == ("/users", False)


def test_literal_preceded_by_concatenation_is_not_truncated() -> None:
    # The dynamic part precedes the literal, so nothing is cut off the tail.
    assert _extract('basePath + "/users/"') == ("/users/", False)


def test_concat_method_call_is_not_treated_as_truncation() -> None:
    # Only `+` joins are recognized; String.concat is a documented gap.
    assert _extract('"/users/".concat(id)') == ("/users/", False)


# The cut must fall inside the path component.


def test_query_string_literal_is_not_truncated() -> None:
    assert _extract('"/users/?page=" + page') == ("/users/?page=", False)
    assert _extract('"/a?b=/" + c') == ("/a?b=/", False)


def test_root_only_literal_is_not_truncated() -> None:
    assert _extract('"/" + id') == ("/", False)


def test_expression_without_path_literal_yields_nothing() -> None:
    assert _extract("id + 1") == ("", False)


# A local host-only URL literal carries no path: the scan moves on to a later
# literal in the same expression instead of stopping at the authority.


def test_local_host_only_literal_yields_later_path_literal() -> None:
    assert _extract('"http://localhost:" + port + "/broker/rest/list"') == (
        "/broker/rest/list",
        False,
    )
    assert _extract('"https://127.0.0.1:" + port + "/x/y"') == ("/x/y", False)


def test_local_host_only_literal_with_truncated_tail_literal() -> None:
    assert _extract('"http://localhost:" + port + "/api/topics/" + topic') == (
        "/api/topics/",
        True,
    )


def test_local_host_only_literal_without_later_literal_yields_nothing() -> None:
    assert _extract('"http://localhost:" + port') == ("", False)


def test_local_host_root_literal_yields_later_path_literal() -> None:
    # A bare-root authority is still host-only; stopping at it would claim "/"
    # while losing the real path.
    assert _extract('"http://localhost:8080/" + "broker/rest/list"') == (
        "/broker/rest/list",
        False,
    )
    assert _extract('"http://localhost:8080/" + "/broker/rest/list"') == (
        "/broker/rest/list",
        False,
    )


def test_local_host_literal_with_query_is_kept() -> None:
    # The query string carries parameter-name evidence the skip would drop.
    assert _extract('"http://localhost:8080?action=ping"') == (
        "http://localhost:8080?action=ping",
        False,
    )


def test_local_host_literal_with_fragment_is_kept() -> None:
    assert _extract('"http://localhost:8080#status"') == (
        "http://localhost:8080#status",
        False,
    )


def test_local_host_literal_with_path_component_is_kept() -> None:
    assert _extract('"http://localhost:8080/users/" + id') == (
        "http://localhost:8080/users/",
        True,
    )


def test_external_host_only_literal_keeps_its_authority() -> None:
    # An external authority must not be reinterpreted as a local endpoint path.
    assert _extract('"http://api.example.com:" + port + "/x/y"') == (
        "http://api.example.com:",
        False,
    )


# A resolver that fully resolves an expression yields the complete, untruncated path.


def test_resolved_constant_concat_yields_full_path_untruncated() -> None:
    def resolve(expression: str) -> str | None:
        return "/rest/quotes/s:0" if expression == 'QUOTES_PATH + "/s:0"' else None

    assert _extract_with_resolver('QUOTES_PATH + "/s:0"', resolve) == (
        "/rest/quotes/s:0",
        False,
    )


def test_resolved_relative_value_is_normalized_and_untruncated() -> None:
    def resolve(_expression: str) -> str | None:
        return "rest/quotes"

    assert _extract_with_resolver("PATHS.QUOTES", resolve) == ("/rest/quotes", False)


def test_resolved_absolute_url_value_is_untruncated() -> None:
    def resolve(_expression: str) -> str | None:
        return "https://api.example.com/rest/quotes"

    assert _extract_with_resolver("BASE_URL", resolve) == (
        "https://api.example.com/rest/quotes",
        False,
    )


# When resolution declines, the literal scan keeps today's truncation semantics.


def test_unresolved_expression_falls_back_to_literal_suffix_truncated() -> None:
    def resolve(_expression: str) -> str | None:
        return None

    assert _extract_with_resolver('"/products/" + id', resolve) == (
        "/products/",
        True,
    )


def test_resolver_clears_truncation_that_literal_scan_would_report() -> None:
    # The same trailing-slash + identifier expression is truncated on fallback
    # but untruncated once the resolver returns the whole path.
    expression = '"/products/" + SUFFIX'

    def unresolvable(_expression: str) -> str | None:
        return None

    def resolvable(_expression: str) -> str | None:
        return "/products/all"

    assert _extract_with_resolver(expression, unresolvable) == ("/products/", True)
    assert _extract_with_resolver(expression, resolvable) == ("/products/all", False)


def test_resolved_value_failing_path_shape_falls_back_to_literal_scan() -> None:
    # A resolved value that is neither absolute nor a relative path literal
    # (a bare token without a slash) is rejected, so the literal scan runs.
    def resolve(_expression: str) -> str | None:
        return "products"

    assert _extract_with_resolver('"/products" + SUFFIX', resolve) == (
        "/products",
        False,
    )


def test_resolver_returning_empty_value_falls_back_to_literal_scan() -> None:
    def resolve(_expression: str) -> str | None:
        return ""

    assert _extract_with_resolver('"/products/" + id', resolve) == ("/products/", True)


def test_resolver_declining_on_numeric_tail_falls_back_to_literal_scan() -> None:
    # A bare numeric tail poisons the resolver (it returns None); the literal scan
    # then yields the leading literal. With no trailing slash it is not truncated.
    assert _extract_with_resolver('"/p" + 1', lambda _expression: None) == (
        "/p",
        False,
    )
