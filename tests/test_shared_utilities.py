from __future__ import annotations

from collections import OrderedDict

import pytest

from gerbil.analysis.shared.annotations import (
    annotation_token,
)
from gerbil.analysis.shared.caching import cache_get, cache_put
import gerbil.analysis.shared.class_utils as class_utils
import gerbil.analysis.shared.http_mapping_annotations as http_mapping_annotations


def test_annotation_token_consumers_share_single_helper() -> None:
    assert class_utils._annotation_token is annotation_token
    assert http_mapping_annotations.annotation_token is annotation_token


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        ("@WebMvcTest(controllers = Example.class)", "@WebMvcTest"),
        (
            "@org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest",
            "@org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest",
        ),
        ('  @WithMockUser(username = "alice")  ', "@WithMockUser"),
        ("", ""),
    ],
)
def test_annotation_token_canonicalization_is_uniform(
    annotation: str, expected: str
) -> None:
    assert class_utils._annotation_token(annotation) == expected
    assert http_mapping_annotations.annotation_name_token(annotation) == expected


def test_cache_get_refreshes_recency() -> None:
    cache: OrderedDict[str, int] = OrderedDict(
        [
            ("oldest", 1),
            ("middle", 2),
            ("newest", 3),
        ]
    )

    assert cache_get(cache, "middle") == 2
    assert list(cache.keys()) == ["oldest", "newest", "middle"]


def test_cache_put_evicts_lru_after_recency_refresh() -> None:
    cache: OrderedDict[str, int] = OrderedDict(
        [
            ("oldest", 1),
            ("middle", 2),
            ("newest", 3),
        ]
    )

    assert cache_get(cache, "oldest") == 1
    cache_put(cache, "incoming", 4, max_entries=3)

    assert list(cache.keys()) == ["middle", "newest", "oldest", "incoming"][1:]
    assert "middle" not in cache


def test_cache_put_honors_max_size() -> None:
    cache: OrderedDict[str, int] = OrderedDict()

    cache_put(cache, "first", 1, max_entries=2)
    cache_put(cache, "second", 2, max_entries=2)
    cache_put(cache, "third", 3, max_entries=2)

    assert len(cache) == 2
    assert list(cache.keys()) == ["second", "third"]


# extract_annotation_paths routes only path-valued elements to the resolver.


def _always_resolved(_expression: str) -> str | None:
    return "RESOLVED"


def test_extract_annotation_paths_resolves_path_attribute_constant() -> None:
    assert http_mapping_annotations.extract_annotation_paths(
        "@GetMapping(value = SOME_CONST)", _always_resolved
    ) == ["RESOLVED"]


def test_extract_annotation_paths_resolves_leading_positional_constant() -> None:
    assert http_mapping_annotations.extract_annotation_paths(
        "@GetMapping(SOME_CONST)", _always_resolved
    ) == ["RESOLVED"]


def test_extract_annotation_paths_ignores_non_path_default_value_attribute() -> None:
    # defaultValue is not a path attribute; the resolver must never see it even
    # though it is live (the positive controls above prove the resolver resolves).
    assert (
        http_mapping_annotations.extract_annotation_paths(
            "@RequestParam(defaultValue = SOME_CONST)", _always_resolved
        )
        == []
    )


def test_extract_annotation_paths_ignores_non_path_name_attribute() -> None:
    assert (
        http_mapping_annotations.extract_annotation_paths(
            "@GetMapping(name = SOME_CONST)", _always_resolved
        )
        == []
    )


def test_extract_annotation_paths_ignores_consumes_and_produces_constants() -> None:
    assert (
        http_mapping_annotations.extract_annotation_paths(
            "@GetMapping(consumes = SOME_CONST, produces = OTHER_CONST)",
            _always_resolved,
        )
        == []
    )


def test_extract_annotation_paths_resolves_top_level_concat_element() -> None:
    # A literal+constant concatenation resolves as one element (resolution-first).
    assert http_mapping_annotations.extract_annotation_paths(
        '@GetMapping("/v" + SUFFIX)', lambda _expression: "/v/list"
    ) == ["/v/list"]


def test_extract_annotation_paths_resolves_path_attribute_concat_before_comma() -> None:
    # The path attribute value must capture the full concat, not truncate at the
    # quote, even when another attribute follows.
    assert http_mapping_annotations.extract_annotation_paths(
        '@RequestMapping(path = "/v" + SUFFIX, method = RequestMethod.GET)',
        lambda _expression: "/v/list",
    ) == ["/v/list"]


def test_extract_annotation_paths_unresolvable_concat_keeps_leading_literal() -> None:
    # An unresolvable concat falls through to the literal scan, emitting the head.
    assert http_mapping_annotations.extract_annotation_paths(
        '@GetMapping("/v" + dynamicVar)', lambda _expression: None
    ) == ["/v"]


def test_extract_annotation_paths_plus_inside_quoted_literal_is_not_a_concat() -> None:
    # A '+' inside a quoted literal must not trigger concat resolution.
    assert http_mapping_annotations.extract_annotation_paths(
        '@GetMapping("/a+b")', _always_resolved
    ) == ["/a+b"]


def test_extract_annotation_paths_pure_literal_skips_resolver() -> None:
    def _fail(_expression: str) -> str | None:
        raise AssertionError("resolver must not be called for a pure literal")

    assert http_mapping_annotations.extract_annotation_paths(
        '@GetMapping("/v/list")', _fail
    ) == ["/v/list"]


def test_extract_request_mapping_method_specs_ignores_method_equals_inside_quotes() -> (
    None
):
    # ``params="method=save"`` is a request-parameter condition, not a verb.
    assert http_mapping_annotations.extract_request_mapping_method_specs(
        '@RequestMapping(value = "/users", params = "method=save")'
    ) == [http_mapping_annotations.ProductionMethodSpec("UNKNOWN", True)]
    assert http_mapping_annotations.extract_request_mapping_method_specs(
        '@RequestMapping(value = "/legacy", params = {"method=delete"})'
    ) == [http_mapping_annotations.ProductionMethodSpec("UNKNOWN", True)]
    assert http_mapping_annotations.extract_request_mapping_method_specs(
        '@RequestMapping(value = "/x", headers = "X-Method=foo")'
    ) == [http_mapping_annotations.ProductionMethodSpec("UNKNOWN", True)]


def test_extract_request_mapping_method_specs_parses_top_level_method_attribute() -> (
    None
):
    assert http_mapping_annotations.extract_request_mapping_method_specs(
        '@RequestMapping(value = "/x", method = RequestMethod.GET)'
    ) == [http_mapping_annotations.ProductionMethodSpec("GET", False)]
    assert http_mapping_annotations.extract_request_mapping_method_specs(
        "@RequestMapping(method = {RequestMethod.GET, RequestMethod.POST})"
    ) == [
        http_mapping_annotations.ProductionMethodSpec("GET", False),
        http_mapping_annotations.ProductionMethodSpec("POST", False),
    ]
    assert http_mapping_annotations.extract_request_mapping_method_specs(
        '@HttpExchange(value = "/x", method = "GET")'
    ) == [http_mapping_annotations.ProductionMethodSpec("GET", False)]
    assert http_mapping_annotations.extract_request_mapping_method_specs(
        '@RequestMapping(params = "method=save", method = RequestMethod.PUT)'
    ) == [http_mapping_annotations.ProductionMethodSpec("PUT", False)]


def test_extract_request_mapping_method_specs_unresolved_method_is_unknown_not_wildcard() -> (
    None
):
    assert http_mapping_annotations.extract_request_mapping_method_specs(
        '@RequestMapping(path = "/x", method = customMethod())'
    ) == [http_mapping_annotations.ProductionMethodSpec("UNKNOWN", False)]


def test_extract_annotation_paths_supports_micronaut_uris_attribute() -> None:
    assert http_mapping_annotations.extract_annotation_paths(
        '@Get(uris = {"/a", "/b"})'
    ) == ["/a", "/b"]


def test_extract_annotation_paths_expands_rfc6570_optional_slash_variables() -> None:
    assert http_mapping_annotations.extract_annotation_paths('@Get("/books{/id}")') == [
        "/books",
        "/books/{id}",
    ]
    assert http_mapping_annotations.extract_annotation_paths('@Get("/a{/b}{/c}")') == [
        "/a",
        "/a/{c}",
        "/a/{b}",
        "/a/{b}/{c}",
    ]


def test_normalize_path_strips_rfc6570_query_and_fragment_templates() -> None:
    assert http_mapping_annotations.normalize_path("/list{?max,offset}") == "/list"
    assert http_mapping_annotations.normalize_path("/x{#anchor}") == "/x"
    assert http_mapping_annotations.normalize_path("/a/{id}") == "/a/{id}"


def test_extract_annotation_paths_uris_array_with_uri_template_braces() -> None:
    assert http_mapping_annotations.extract_annotation_paths(
        '@Get(uris = {"/books{/id}", "/list{?max,offset}"})'
    ) == ["/books", "/books/{id}", "/list{?max,offset}"]


def test_extract_annotation_paths_mixed_uris_array_with_plain_and_template_elements() -> (
    None
):
    assert http_mapping_annotations.extract_annotation_paths(
        '@Get(uris = {"/plain", "/books{/id}"})'
    ) == ["/plain", "/books", "/books/{id}"]


def test_extract_annotation_paths_single_string_with_uri_template_braces() -> None:
    assert http_mapping_annotations.extract_annotation_paths('@Get("/books{/id}")') == [
        "/books",
        "/books/{id}",
    ]


def test_path_attribute_value_after_newline_equals_is_captured() -> None:
    values = http_mapping_annotations._top_level_path_attribute_values(
        'path\n    = API_ROOT, consumes = "application/json"'
    )
    assert values == ["API_ROOT"]
