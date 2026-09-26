"""Shared parsing of HTTP mapping annotations (Spring MVC verb/path and the
@HttpExchange family), used by both server-side endpoint extraction and
client-side Spring declarative HTTP client (@FeignClient/@HttpExchange)
classification."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import NamedTuple
from urllib.parse import ParseResult

from cldk.models.java import JImport

from gerbil.analysis.shared.annotations import (
    annotation_body as _annotation_body_shared,
)
from gerbil.analysis.shared.annotations import (
    annotation_matches_expected,
)
from gerbil.analysis.shared.annotations import (
    annotation_short_name_from_token,
    annotation_token,
)
from gerbil.analysis.shared.constant_resolution import split_top_level_concat
from gerbil.analysis.shared.url_utils import safe_urlparse

ConstantExpressionResolver = Callable[[str], str | None]

QUOTED_STRING_RE: re.Pattern[str] = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')
# Annotation attribute value: a brace list, a quoted string, or a bare token.
# Path-bearing values are parsed by a brace/quote-aware scanner instead of this
# pattern, so array elements containing URI-template braces are not truncated.
ATTRIBUTE_VALUE_PATTERN: str = r"(\{[^}]*\}|\"[^\"]*\"|[^,)]+)"
_REQUEST_METHOD_TOKEN_RE: re.Pattern[str] = re.compile(
    r"(?:RequestMethod\.|HttpMethod\.|\b)"
    r"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|TRACE|CONNECT)\b",
    re.IGNORECASE,
)
_MULTI_SLASH_RE: re.Pattern[str] = re.compile(r"/{2,}")
# RFC 6570 optional slash expansion: ``{/var}`` expands to nothing or ``/value``.
_OPTIONAL_SLASH_VARIABLE_RE: re.Pattern[str] = re.compile(r"\{(/[^{}:]+)\}")
# RFC 6570 query/fragment expansion groups declare request parameters, not path:
# query (``{?a}``), query continuation (``{&a}``), and fragment (``{#a}``).
_QUERY_FRAGMENT_TEMPLATE_RE: re.Pattern[str] = re.compile(r"\{[?#&][^{}]*\}")
# RFC 6570 query expansion groups (``{?max,offset}`` and the continuation form
# ``{&b}``), capturing the var list; fragment ``{#...}`` is not a query parameter.
_QUERY_TEMPLATE_RE: re.Pattern[str] = re.compile(r"\{[?&]([^{}]*)\}")

SPRING_DIRECT_METHOD_ANNOTATIONS: dict[str, str] = {
    "@GetMapping": "GET",
    "@PostMapping": "POST",
    "@PutMapping": "PUT",
    "@DeleteMapping": "DELETE",
    "@PatchMapping": "PATCH",
}

# Spring HTTP Interface (@HttpExchange) method shortcuts. Verb-fixed analogs of
# the MVC @*Mapping annotations, carried by @HttpExchange client interface methods.
SPRING_EXCHANGE_DIRECT_METHOD_ANNOTATIONS: dict[str, str] = {
    "@GetExchange": "GET",
    "@PostExchange": "POST",
    "@PutExchange": "PUT",
    "@DeleteExchange": "DELETE",
    "@PatchExchange": "PATCH",
}

# Server-side endpoint extraction only recognizes MVC mapping annotations.
SPRING_METHOD_ANNOTATION_NAMES: set[str] = {
    *SPRING_DIRECT_METHOD_ANNOTATIONS,
    "@RequestMapping",
}

# @HttpExchange client methods carry the @HttpExchange family; the verb-less base
# @HttpExchange is treated like @RequestMapping (its `method` attribute drives the
# verb, absent => wildcard/UNKNOWN).
SPRING_EXCHANGE_METHOD_ANNOTATION_NAMES: set[str] = {
    *SPRING_EXCHANGE_DIRECT_METHOD_ANNOTATIONS,
    "@HttpExchange",
}

# Class-level markers for the two Spring declarative HTTP client mechanisms,
# mapped to the dispatch framework they represent.
SPRING_CLIENT_CLASS_ANNOTATION_KINDS: dict[str, str] = {
    "@FeignClient": "feign",
    "@HttpExchange": "http-interface",
}

SPRING_CLIENT_CLASS_ANNOTATIONS: set[str] = set(SPRING_CLIENT_CLASS_ANNOTATION_KINDS)

# Each client family matches only its own method annotations: Spring Cloud
# OpenFeign reads MVC @*Mapping (via SpringMvcContract); Spring HTTP Interface
# reads the @HttpExchange family. Cross-family annotations are not recognized.
_CLIENT_METHOD_ANNOTATION_NAMES_BY_KIND: dict[str, set[str]] = {
    "feign": SPRING_METHOD_ANNOTATION_NAMES,
    "http-interface": SPRING_EXCHANGE_METHOD_ANNOTATION_NAMES,
}

SPRING_ANNOTATION_IMPORT_ROOTS: dict[str, set[str]] = {
    "@Controller": {"org.springframework.stereotype"},
    "@RestController": {"org.springframework.web.bind.annotation"},
    "@RepositoryRestController": {"org.springframework.data.rest.webmvc"},
    "@BasePathAwareController": {"org.springframework.data.rest.webmvc"},
    "@ControllerEndpoint": {"org.springframework.boot.actuate.endpoint.web.annotation"},
    "@RestControllerEndpoint": {
        "org.springframework.boot.actuate.endpoint.web.annotation"
    },
    "@FeignClient": {"org.springframework.cloud.openfeign"},
    "@HttpExchange": {"org.springframework.web.service.annotation"},
    "@GetExchange": {"org.springframework.web.service.annotation"},
    "@PostExchange": {"org.springframework.web.service.annotation"},
    "@PutExchange": {"org.springframework.web.service.annotation"},
    "@DeleteExchange": {"org.springframework.web.service.annotation"},
    "@PatchExchange": {"org.springframework.web.service.annotation"},
    "@RequestMapping": {"org.springframework.web.bind.annotation"},
    "@GetMapping": {"org.springframework.web.bind.annotation"},
    "@PostMapping": {"org.springframework.web.bind.annotation"},
    "@PutMapping": {"org.springframework.web.bind.annotation"},
    "@DeleteMapping": {"org.springframework.web.bind.annotation"},
    "@PatchMapping": {"org.springframework.web.bind.annotation"},
}


class ProductionMethodSpec(NamedTuple):
    http_method: str
    is_method_wildcard: bool


def annotation_name_token(annotation: str) -> str:
    return annotation_token(annotation)


def annotation_short_name(annotation: str) -> str:
    return annotation_short_name_from_token(annotation_name_token(annotation))


def normalized_annotation_source(annotation: str) -> str:
    raw_annotation = (annotation or "").strip()
    if not raw_annotation:
        return ""

    annotation_name_token_value = annotation_name_token(raw_annotation)
    if not annotation_name_token_value:
        return raw_annotation

    token_start = raw_annotation.find(annotation_name_token_value)
    if token_start < 0:
        return raw_annotation
    return raw_annotation[token_start:].strip()


def annotation_body(annotation: str) -> str:
    return _annotation_body_shared(normalized_annotation_source(annotation))


def deduplicate_strings(values: list[str]) -> list[str]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduplicated.append(value)
    return deduplicated


def _has_top_level_equals(text: str) -> bool:
    """Return True when text contains '=' outside quoted strings."""
    in_quotes: bool = False
    is_escaping: bool = False
    for character in text:
        if is_escaping:
            is_escaping = False
            continue
        if character == "\\" and in_quotes:
            is_escaping = True
            continue
        if character == '"':
            in_quotes = not in_quotes
            continue
        if character == "=" and not in_quotes:
            return True
    return False


def _is_top_level_position(text: str, position: int) -> bool:
    """True when ``position`` is outside quoted strings and nested brackets."""
    in_quotes: bool = False
    is_escaping: bool = False
    nesting_depth: int = 0
    for index, character in enumerate(text[:position]):
        if is_escaping:
            is_escaping = False
            continue
        if character == "\\" and in_quotes:
            is_escaping = True
            continue
        if character == '"':
            in_quotes = not in_quotes
            continue
        if in_quotes:
            continue
        if character in "([{":
            nesting_depth += 1
            continue
        if character in ")]}":
            if nesting_depth > 0:
                nesting_depth -= 1
    return not in_quotes and nesting_depth == 0


def _read_top_level_value(text: str, start: int) -> str:
    """Read the value after ``=`` until a top-level comma or end of text."""
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    if index < len(text) and text[index] == "=":
        index += 1
    while index < len(text) and text[index].isspace():
        index += 1

    in_quotes: bool = False
    is_escaping: bool = False
    nesting_depth: int = 0
    value_chars: list[str] = []
    while index < len(text):
        character = text[index]
        if is_escaping:
            value_chars.append(character)
            is_escaping = False
            index += 1
            continue
        if character == "\\" and in_quotes:
            value_chars.append(character)
            is_escaping = True
            index += 1
            continue
        if character == '"':
            value_chars.append(character)
            in_quotes = not in_quotes
            index += 1
            continue
        if in_quotes:
            value_chars.append(character)
            index += 1
            continue
        if character in "([{":
            nesting_depth += 1
            value_chars.append(character)
            index += 1
            continue
        if character in ")]}":
            if nesting_depth > 0:
                nesting_depth -= 1
            value_chars.append(character)
            index += 1
            continue
        if character == "," and nesting_depth == 0:
            break
        value_chars.append(character)
        index += 1
    return "".join(value_chars).strip()


def _top_level_attribute_values(
    annotation_body_text: str,
    attribute_name_pattern: str,
    flags: int = 0,
) -> list[str]:
    """Return raw values for every top-level ``<name> = ...`` assignment."""
    values: list[str] = []
    for match in re.finditer(attribute_name_pattern, annotation_body_text, flags):
        if not _is_top_level_position(annotation_body_text, match.start()):
            continue
        after = match.end()
        if after >= len(annotation_body_text):
            continue
        if not re.match(r"\s*=", annotation_body_text[after:]):
            continue
        values.append(_read_top_level_value(annotation_body_text, after))
    return values


def _top_level_method_attribute_values(annotation_body_text: str) -> list[str]:
    return _top_level_attribute_values(
        annotation_body_text, r"\bmethod\b", re.IGNORECASE
    )


def _top_level_path_attribute_values(annotation_body_text: str) -> list[str]:
    # ``url`` belongs to Spring HTTP Interface annotations; ``uri``/``uris`` are
    # Micronaut path-bearing attributes.
    return _top_level_attribute_values(
        annotation_body_text, r"\b(?:path|value|uris?|url)\b"
    )


def _split_top_level_array_elements(array_body: str) -> list[str]:
    """Split a brace-array body into its top-level comma-separated elements."""
    elements: list[str] = []
    current: list[str] = []
    in_quotes: bool = False
    is_escaping: bool = False
    nesting_depth: int = 0

    for character in array_body:
        if is_escaping:
            current.append(character)
            is_escaping = False
            continue
        if character == "\\" and in_quotes:
            current.append(character)
            is_escaping = True
            continue
        if character == '"':
            current.append(character)
            in_quotes = not in_quotes
            continue
        if in_quotes:
            current.append(character)
            continue
        if character in "([{":
            nesting_depth += 1
            current.append(character)
            continue
        if character in ")]}":
            if nesting_depth > 0:
                nesting_depth -= 1
            current.append(character)
            continue
        if character == "," and nesting_depth == 0:
            elements.append("".join(current).strip())
            current = []
            continue
        current.append(character)

    elements.append("".join(current).strip())
    return [element for element in elements if element]


def _expand_optional_slash_variables(path: str) -> list[str]:
    """Expand RFC 6570 ``{/var}`` groups into zero-or-one segment variants."""
    match = _OPTIONAL_SLASH_VARIABLE_RE.search(path)
    if match is None:
        return [path]

    prefix = path[: match.start()]
    suffix = path[match.end() :]
    name = match.group(1).lstrip("/")
    expanded_suffixes = _expand_optional_slash_variables(suffix)
    return [prefix + tail for tail in expanded_suffixes] + [
        prefix + f"/{{{name}}}" + tail for tail in expanded_suffixes
    ]


def _resolve_path_element(
    element: str,
    constant_resolver: ConstantExpressionResolver | None,
) -> list[str]:
    """Harvest paths from one path-valued element: literals first, else resolve.

    A pure quoted literal keeps absolute precedence. A genuine top-level
    concatenation that mixes a literal with a constant (e.g. ``"/v" + SUFFIX``)
    is routed to the resolver FIRST so the composed path is recovered; an
    unresolvable concat falls through to the literal scan (emitting the leading
    literal head, matching the request side).
    """
    if constant_resolver is not None:
        tokens = split_top_level_concat(element)
        if tokens is not None and len(tokens) > 1:
            resolved = constant_resolver(element)
            if resolved is not None:
                return _expand_optional_slash_variables(resolved)

    literal_paths = [path for path in QUOTED_STRING_RE.findall(element) if path]
    if literal_paths:
        return [
            expanded
            for path in literal_paths
            for expanded in _expand_optional_slash_variables(path)
        ]
    if constant_resolver is None:
        return []
    resolved = constant_resolver(element)
    return _expand_optional_slash_variables(resolved) if resolved is not None else []


def _resolve_brace_array_paths(
    array_text: str,
    constant_resolver: ConstantExpressionResolver | None,
) -> list[str]:
    """Strip the enclosing braces, split top-level elements, resolve each."""
    array_body = array_text[1:].rstrip()
    if array_body.endswith("}"):
        array_body = array_body[:-1]
    paths: list[str] = []
    for element in _split_top_level_array_elements(array_body):
        paths.extend(_resolve_path_element(element, constant_resolver))
    return paths


def _extract_leading_positional_paths(
    annotation_body_text: str,
    constant_resolver: ConstantExpressionResolver | None = None,
) -> list[str]:
    leading_argument_chars: list[str] = []
    in_quotes: bool = False
    is_escaping: bool = False
    nesting_depth: int = 0

    for character in annotation_body_text:
        if is_escaping:
            leading_argument_chars.append(character)
            is_escaping = False
            continue

        if character == "\\" and in_quotes:
            leading_argument_chars.append(character)
            is_escaping = True
            continue

        if character == '"':
            leading_argument_chars.append(character)
            in_quotes = not in_quotes
            continue

        if in_quotes:
            leading_argument_chars.append(character)
            continue

        if character in "([{":
            nesting_depth += 1
            leading_argument_chars.append(character)
            continue

        if character in ")]}":
            if nesting_depth > 0:
                nesting_depth -= 1
            leading_argument_chars.append(character)
            continue

        if character == "," and nesting_depth == 0:
            break

        leading_argument_chars.append(character)

    leading_argument = "".join(leading_argument_chars).strip()
    if not leading_argument:
        return []
    if _has_top_level_equals(leading_argument):
        return []

    if leading_argument.startswith("{"):
        return deduplicate_strings(
            _resolve_brace_array_paths(leading_argument, constant_resolver)
        )

    # A single positional element (quoted literal, literal+constant concat, or a
    # bare constant identifier) resolves through the shared path-element handler.
    return deduplicate_strings(
        _resolve_path_element(leading_argument, constant_resolver)
    )


def paths_or_root(paths: list[str]) -> list[str]:
    return paths or [""]


def _extract_path_attribute_value_paths(
    raw_value: str,
    constant_resolver: ConstantExpressionResolver | None,
) -> list[str]:
    """Harvest paths from a path attribute value (brace array, literal, or identifier)."""
    if raw_value.startswith("{"):
        return _resolve_brace_array_paths(raw_value, constant_resolver)
    return _resolve_path_element(raw_value, constant_resolver)


def extract_annotation_paths(
    annotation: str,
    constant_resolver: ConstantExpressionResolver | None = None,
) -> list[str]:
    body = annotation_body(annotation)
    if not body:
        return []

    paths: list[str] = []
    for raw_value in _top_level_path_attribute_values(body):
        paths.extend(_extract_path_attribute_value_paths(raw_value, constant_resolver))

    if paths:
        return deduplicate_strings(paths)

    return _extract_leading_positional_paths(body, constant_resolver)


def extract_request_mapping_method_specs(annotation: str) -> list[ProductionMethodSpec]:
    body = annotation_body(annotation)
    if not body:
        return [ProductionMethodSpec("UNKNOWN", True)]

    raw_values = _top_level_method_attribute_values(body)
    if not raw_values:
        return [ProductionMethodSpec("UNKNOWN", True)]

    methods: list[str] = []
    for raw_value in raw_values:
        if raw_value.startswith("{"):
            elements = _split_top_level_array_elements(raw_value[1:-1].strip())
            for element in elements:
                methods.extend(
                    method.upper()
                    for method in _REQUEST_METHOD_TOKEN_RE.findall(element)
                )
        else:
            methods.extend(
                method.upper() for method in _REQUEST_METHOD_TOKEN_RE.findall(raw_value)
            )

    if not methods:
        return [ProductionMethodSpec("UNKNOWN", False)]
    return [
        ProductionMethodSpec(method, False) for method in deduplicate_strings(methods)
    ]


def normalize_path(path: str) -> str:
    candidate = path.strip()
    if not candidate:
        return "/"

    # RFC 6570 query/fragment expansion groups (``{?a,b}``, ``{#a}``) declare
    # request parameters, not path segments; drop them before URL parsing.
    candidate = _QUERY_FRAGMENT_TEMPLATE_RE.sub("", candidate)

    # A scheme-less leading "//" is a sloppy concat artifact, not an authority
    # (classify_request_target already deems such literals local); collapse it
    # before urlparse swallows the first segment as a netloc.
    if candidate.startswith("//"):
        candidate = f"/{candidate.lstrip('/')}"

    parsed = safe_urlparse(candidate)
    if parsed is None:
        # Bracket-broken authority: the str contract has no rejection channel,
        # so keep the literal opaque, minus any query/fragment tail so no
        # caller ever sees query text inside a path.
        normalized_path = candidate.partition("#")[0].partition("?")[0]
    elif parsed.scheme.lower() in {"http", "https"}:
        normalized_path = parsed.path or "/"
    else:
        # Bare-query/bare-fragment candidates parse to an empty path; strip
        # their tails too so the invariant above holds on this branch as well.
        normalized_path = parsed.path or candidate.partition("#")[0].partition("?")[0]

    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"

    normalized_path = _MULTI_SLASH_RE.sub("/", normalized_path)
    if len(normalized_path) > 1:
        normalized_path = normalized_path.rstrip("/")

    return normalized_path or "/"


def extract_rfc6570_query_parameter_names(path: str) -> list[str]:
    """Names declared by RFC 6570 query expansions in a route template, in order,
    de-duplicated. Covers both the query form ``{?max,offset}`` and the query
    continuation form ``{&b}`` (Micronaut treats both as query parameters).

    Fragment groups (``{#...}``) are not query parameters. Exploded aggregate
    vars (``{?cmd*}``, which Micronaut binds to a whole object rather than a
    single named key) are excluded, and a ``:prefix`` length modifier is dropped.
    """
    names: list[str] = []
    for match in _QUERY_TEMPLATE_RE.finditer(path):
        for raw_name in match.group(1).split(","):
            name = raw_name.strip()
            if not name or name.endswith("*"):
                continue
            name = name.split(":", 1)[0].strip()
            if name and name not in names:
                names.append(name)
    return names


def join_paths(class_path: str, method_path: str) -> str:
    normalized_class_path = normalize_path(class_path)
    normalized_method_path = normalize_path(method_path)

    if normalized_class_path == "/":
        return normalized_method_path
    if normalized_method_path == "/":
        return normalized_class_path

    combined = (
        f"{normalized_class_path.rstrip('/')}/{normalized_method_path.lstrip('/')}"
    )
    return normalize_path(combined)


def _http_authority(parsed: ParseResult) -> str | None:
    """Return ``scheme://authority`` for an absolute http(s) URL, else ``None``."""
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"} and parsed.netloc:
        return f"{scheme}://{parsed.netloc}"
    return None


def join_request_paths(base_path: str, method_path: str) -> str:
    """Compose a client request target, preserving an absolute http(s) authority.

    Unlike :func:`join_paths` (which strips scheme+host down to a server-side
    route template), this keeps the authority so request-dispatch can tell local
    from remote targets. An absolute method ``url`` is the full target (its
    authority wins); otherwise an absolute base contributes its authority with
    the paths joined beneath it; a fully relative pair falls back to
    :func:`join_paths`. An unparseable (bracket-broken) input poisons the join:
    the composed target is returned verbatim so downstream classifiers report
    unknown rather than local. Returns ``""`` when both inputs are empty.
    """
    if not base_path and not method_path:
        return ""

    method_candidate = method_path.strip()
    method_parsed = safe_urlparse(method_candidate)
    if method_parsed is None:
        return method_candidate
    method_authority = _http_authority(method_parsed)
    if method_authority is not None:
        return _attach_authority(method_authority, method_parsed.path)

    base_candidate = base_path.strip()
    base_parsed = safe_urlparse(base_candidate)
    if base_parsed is None:
        return base_candidate + method_candidate
    base_authority = _http_authority(base_parsed)
    if base_authority is not None:
        return _attach_authority(
            base_authority, join_paths(base_parsed.path, method_path)
        )

    return join_paths(base_path, method_path)


def _attach_authority(authority: str, path: str) -> str:
    normalized_path = normalize_path(path)
    return authority if normalized_path == "/" else f"{authority}{normalized_path}"


def annotation_matches_spring(
    annotation: str,
    expected_annotation_name: str,
    *,
    class_imports: list[JImport],
) -> bool:
    if expected_annotation_name not in SPRING_ANNOTATION_IMPORT_ROOTS:
        return False
    return annotation_matches_expected(
        annotation=annotation,
        expected_annotation=expected_annotation_name,
        class_imports=class_imports,
        import_roots_by_annotation=SPRING_ANNOTATION_IMPORT_ROOTS,
    )


def spring_client_interface_kind(
    class_annotations: list[str],
    *,
    class_imports: list[JImport],
) -> str | None:
    """Return which Spring declarative HTTP client a type is, or ``None``.

    ``"feign"`` for @FeignClient (Spring Cloud OpenFeign), ``"http-interface"``
    for @HttpExchange (Spring HTTP Interface). The first matching marker wins.
    """
    for annotation in class_annotations:
        annotation_name = annotation_short_name(annotation)
        kind = SPRING_CLIENT_CLASS_ANNOTATION_KINDS.get(annotation_name)
        if kind is None:
            continue
        if annotation_matches_spring(
            annotation,
            annotation_name,
            class_imports=class_imports,
        ):
            return kind
    return None


def extract_spring_method_mapping(
    method_annotations: list[str],
    *,
    class_imports: list[JImport],
    kind: str,
    constant_resolver: ConstantExpressionResolver | None = None,
) -> tuple[str, bool, str] | None:
    """Resolve the (http_method, is_wildcard, path) for a declarative client method.

    ``kind`` selects the recognized annotation family: ``"feign"`` matches MVC
    @*Mapping annotations, ``"http-interface"`` matches the @HttpExchange family.
    Returns ``None`` when no annotation of that family is present. The first
    matching annotation wins, mirroring how a single client method carries
    exactly one mapping annotation.
    """
    allowed_annotation_names = _CLIENT_METHOD_ANNOTATION_NAMES_BY_KIND.get(kind)
    if allowed_annotation_names is None:
        return None
    for annotation in method_annotations:
        annotation_name = annotation_short_name(annotation)
        if annotation_name not in allowed_annotation_names:
            continue
        if not annotation_matches_spring(
            annotation,
            annotation_name,
            class_imports=class_imports,
        ):
            continue

        method_paths = extract_annotation_paths(annotation, constant_resolver)
        method_path = method_paths[0] if method_paths else ""

        # @RequestMapping(method=...) and @HttpExchange(method="GET") both carry
        # the verb in a `method` attribute; an absent attribute => UNKNOWN/wildcard.
        if annotation_name in ("@RequestMapping", "@HttpExchange"):
            specs = extract_request_mapping_method_specs(annotation)
            spec = specs[0]
            return (spec.http_method, spec.is_method_wildcard, method_path)
        if annotation_name in SPRING_DIRECT_METHOD_ANNOTATIONS:
            return (
                SPRING_DIRECT_METHOD_ANNOTATIONS[annotation_name],
                False,
                method_path,
            )
        return (
            SPRING_EXCHANGE_DIRECT_METHOD_ANNOTATIONS[annotation_name],
            False,
            method_path,
        )
    return None


def extract_spring_client_base_path(
    class_annotations: list[str],
    *,
    class_imports: list[JImport],
    kind: str,
    constant_resolver: ConstantExpressionResolver | None = None,
) -> str:
    """Resolve a declarative client's base path within its own annotation family.

    For ``"http-interface"`` the base is a class-level @HttpExchange(url=...)
    prefix. For ``"feign"`` it is a class-level @RequestMapping path, falling
    back to a @FeignClient(path=...) attribute. A literal absolute http(s)
    @FeignClient(url=...) contributes its authority (so an external Feign client
    is not mistaken for a local request); a service-name/placeholder ``url`` is
    not a target and is ignored.
    """
    if kind == "http-interface":
        for annotation in class_annotations:
            if annotation_matches_spring(
                annotation,
                "@HttpExchange",
                class_imports=class_imports,
            ):
                paths = extract_annotation_paths(annotation, constant_resolver)
                if paths:
                    return paths[0]
        return ""

    request_mapping_path = ""
    for annotation in class_annotations:
        if annotation_matches_spring(
            annotation,
            "@RequestMapping",
            class_imports=class_imports,
        ):
            paths = extract_annotation_paths(annotation, constant_resolver)
            if paths:
                request_mapping_path = paths[0]
                break

    feign_path = ""
    feign_url = ""
    for annotation in class_annotations:
        if annotation_matches_spring(
            annotation,
            "@FeignClient",
            class_imports=class_imports,
        ):
            feign_paths = _extract_feign_path_attribute(annotation, constant_resolver)
            feign_path = feign_paths[0] if feign_paths else ""
            feign_url = _extract_feign_absolute_url(annotation, constant_resolver)
            break

    base_path = request_mapping_path or feign_path
    if feign_url:
        return join_request_paths(feign_url, base_path)
    return base_path


def extract_http_exchange_type_method(
    class_annotations: list[str],
    *,
    class_imports: list[JImport],
) -> ProductionMethodSpec | None:
    """Return the verb default declared by a type-level @HttpExchange, if any.

    Spring inherits a type-level @HttpExchange ``method`` to methods that do not
    declare their own verb. Returns the first class-level @HttpExchange spec, or
    ``None`` when the type carries no @HttpExchange marker.
    """
    for annotation in class_annotations:
        if annotation_matches_spring(
            annotation,
            "@HttpExchange",
            class_imports=class_imports,
        ):
            return extract_request_mapping_method_specs(annotation)[0]
    return None


def _extract_feign_path_attribute(
    annotation: str,
    constant_resolver: ConstantExpressionResolver | None = None,
) -> list[str]:
    body = annotation_body(annotation)
    if not body:
        return []
    paths: list[str] = []
    for raw_value in _top_level_attribute_values(body, r"\bpath\b"):
        paths.extend(_extract_path_attribute_value_paths(raw_value, constant_resolver))
    return deduplicate_strings(paths)


def _extract_feign_absolute_url(
    annotation: str,
    constant_resolver: ConstantExpressionResolver | None = None,
) -> str:
    """A literal/constant-resolved absolute http(s) @FeignClient(url=...), else ``""``.

    A service name (``url = "orders"``) or property placeholder
    (``url = "${orders.url}"``) is not an absolute target, so it is skipped.
    """
    body = annotation_body(annotation)
    if not body:
        return ""
    for raw_value in _top_level_attribute_values(body, r"\burl\b"):
        url = _resolve_whole_url_value(raw_value, constant_resolver)
        if url:
            return url
    return ""


def _resolve_whole_url_value(
    raw_value: str,
    constant_resolver: ConstantExpressionResolver | None,
) -> str:
    """Resolve a url attribute to a complete absolute http(s) target, else ``""``.

    Only a whole quoted literal or a fully constant-resolved expression qualifies:
    a partial concat (``"https://api." + env``) must NOT contribute its literal
    head as a fabricated authority, so it is rejected unless it resolves end to
    end. The result must carry a real host (netloc).
    """
    candidate = raw_value.strip()
    literal_match = QUOTED_STRING_RE.fullmatch(candidate)
    if literal_match is not None:
        value = literal_match.group(1).strip()
    elif constant_resolver is not None:
        resolved = constant_resolver(candidate)
        value = resolved.strip() if resolved is not None else ""
    else:
        return ""

    if not value.startswith(("http://", "https://")):
        return ""
    parsed = safe_urlparse(value)
    if parsed is None or not parsed.netloc:
        return ""
    # A `${...}`/`#{...}` placeholder host (e.g. ``https://${service.host}``) is
    # an unresolved runtime value, not a concrete authority; a real host never
    # carries braces, so reject any brace-bearing netloc.
    if "{" in parsed.netloc or "}" in parsed.netloc:
        return ""
    return value
