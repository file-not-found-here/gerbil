from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final

from gerbil.analysis.schema import (
    HttpDispatchFramework,
    HttpRequestRole,
    HttpResponseRole,
)
from gerbil.analysis.shared.constants import REST_ASSURED_ROOT_PACKAGES


@dataclass(frozen=True)
class HttpOwnerFamilyRule:
    framework: HttpDispatchFramework
    family_id: str
    receiver_prefixes: tuple[str, ...] = ()
    exact_receiver_types: tuple[str, ...] = ()
    request_role_by_method_name: dict[str, HttpRequestRole] = field(
        default_factory=dict
    )
    response_role_by_method_name: dict[str, HttpResponseRole] = field(
        default_factory=dict
    )
    default_request_role: HttpRequestRole | None = None
    default_response_role: HttpResponseRole | None = None
    http_method_by_method_name: dict[str, str] = field(default_factory=dict)
    http_verb_by_constructor_class_name: dict[str, str] = field(default_factory=dict)
    dynamic_http_method_constructor_class_names: frozenset[str] = frozenset()
    static_import_owners: tuple[str, ...] = ()
    static_import_methods: frozenset[str] = frozenset()
    # True when this rule's receiver types accumulate request path/url state, so
    # a helper returning one plausibly took the request path as an argument.
    request_path_receiver: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "receiver_prefixes",
            tuple(p.lower() for p in self.receiver_prefixes),
        )
        object.__setattr__(
            self,
            "exact_receiver_types",
            tuple(t.lower() for t in self.exact_receiver_types),
        )


@dataclass(frozen=True)
class ReceiverlessRequestInferenceRule:
    framework: HttpDispatchFramework
    owner_family: str
    request_role: HttpRequestRole
    method_http_methods: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "method_http_methods",
            _normalize_http_method_map(dict(self.method_http_methods)),
        )


@dataclass(frozen=True)
class ReceiverlessRequestInferenceTarget:
    framework: HttpDispatchFramework
    owner_family: str
    request_role: HttpRequestRole
    framework_http_method: str


def normalize_method_names(method_names: Iterable[str]) -> frozenset[str]:
    return frozenset(method_name.lower() for method_name in method_names if method_name)


def _normalize_http_method_map(method_map: dict[str, str]) -> dict[str, str]:
    return {
        method_name.lower(): http_method
        for method_name, http_method in method_map.items()
    }


def _request_role_map(
    method_names: Iterable[str],
    role: HttpRequestRole,
) -> dict[str, HttpRequestRole]:
    return {
        method_name.lower(): role for method_name in method_names if method_name.strip()
    }


def _response_role_map(
    method_names: Iterable[str],
    role: HttpResponseRole,
) -> dict[str, HttpResponseRole]:
    return {
        method_name.lower(): role for method_name in method_names if method_name.strip()
    }


def _merge_request_role_maps(
    *role_maps: dict[str, HttpRequestRole],
) -> dict[str, HttpRequestRole]:
    merged: dict[str, HttpRequestRole] = {}
    for role_map in role_maps:
        merged.update(role_map)
    return merged


def _merge_response_role_maps(
    *role_maps: dict[str, HttpResponseRole],
) -> dict[str, HttpResponseRole]:
    merged: dict[str, HttpResponseRole] = {}
    for role_map in role_maps:
        merged.update(role_map)
    return merged


def _nested_receiver_prefixes(
    base_owner: str, nested_names: Iterable[str]
) -> tuple[str, ...]:
    prefixes: list[str] = []
    for nested_name in nested_names:
        prefixes.append(f"{base_owner}${nested_name}")
        prefixes.append(f"{base_owner}.{nested_name}")
    return tuple(prefixes)


def _with_restassured_roots(*type_suffixes: str) -> tuple[str, ...]:
    return tuple(
        f"{root}{type_suffix}"
        for root in REST_ASSURED_ROOT_PACKAGES
        for type_suffix in type_suffixes
    )


def matches_receiver_prefix(receiver_type: str, receiver_prefix: str) -> bool:
    if not receiver_type or not receiver_prefix:
        return False

    if not receiver_type.startswith(receiver_prefix):
        return False

    if receiver_prefix.endswith("."):
        return True

    if len(receiver_type) == len(receiver_prefix):
        return True

    return receiver_type[len(receiver_prefix)] in {".", "$"}


def _simple_class_name(fully_qualified_name: str) -> str:
    if not fully_qualified_name:
        return ""
    simple_name = fully_qualified_name.rsplit(".", 1)[-1]
    return simple_name.rsplit("$", 1)[-1]


def _best_prefix_length(
    receiver_type: str,
    receiver_prefixes: tuple[str, ...],
) -> int:
    lowered_receiver_type = receiver_type.lower()
    best_length = -1
    for receiver_prefix in receiver_prefixes:
        if matches_receiver_prefix(lowered_receiver_type, receiver_prefix):
            best_length = max(best_length, len(receiver_prefix))
    return best_length


def _best_receiver_match_length(rule: HttpOwnerFamilyRule, receiver_type: str) -> int:
    lowered_receiver_type = receiver_type.lower()
    best_length = _best_prefix_length(receiver_type, rule.receiver_prefixes)
    for exact_receiver_type in rule.exact_receiver_types:
        if lowered_receiver_type == exact_receiver_type:
            best_length = max(best_length, len(exact_receiver_type))
    return best_length


def _owner_family_match_strength(
    rule: HttpOwnerFamilyRule,
    *,
    receiver_type: str,
    method_name: str,
    is_constructor_call: bool,
) -> tuple[int, int] | None:
    match_length = _best_receiver_match_length(rule, receiver_type)
    if match_length < 0:
        return None

    simple_class_name = _simple_class_name(receiver_type)
    normalized_method_name = method_name.lower()

    if (
        is_constructor_call
        and simple_class_name in rule.http_verb_by_constructor_class_name
    ):
        return (3, match_length)
    if (
        is_constructor_call
        and simple_class_name in rule.dynamic_http_method_constructor_class_names
    ):
        return (3, match_length)
    if normalized_method_name in rule.request_role_by_method_name:
        return (2, match_length)
    if normalized_method_name in rule.response_role_by_method_name:
        return (2, match_length)
    if rule.default_request_role is not None or rule.default_response_role is not None:
        return (1, match_length)
    return None


def classify_owner_family(
    rule: HttpOwnerFamilyRule,
    *,
    receiver_type: str,
    method_name: str,
    is_constructor_call: bool = False,
) -> tuple[HttpRequestRole | None, HttpResponseRole | None, str]:
    simple_class_name = _simple_class_name(receiver_type)
    normalized_method_name = method_name.lower()

    if (
        is_constructor_call
        and simple_class_name in rule.http_verb_by_constructor_class_name
    ):
        return (
            HttpRequestRole.BUILDER,
            None,
            rule.http_verb_by_constructor_class_name[simple_class_name],
        )
    if (
        is_constructor_call
        and simple_class_name in rule.dynamic_http_method_constructor_class_names
    ):
        return (HttpRequestRole.BUILDER, None, "UNKNOWN")

    request_role = rule.request_role_by_method_name.get(
        normalized_method_name, rule.default_request_role
    )
    response_role = rule.response_role_by_method_name.get(
        normalized_method_name, rule.default_response_role
    )
    http_method = rule.http_method_by_method_name.get(normalized_method_name, "UNKNOWN")
    return request_role, response_role, http_method


_REST_TEMPLATE_HTTP_METHOD_BY_REQUEST_METHOD: Final[dict[str, str]] = (
    _normalize_http_method_map(
        {
            "getForEntity": "GET",
            "getForObject": "GET",
            "headForHeaders": "HEAD",
            "postForLocation": "POST",
            "postForEntity": "POST",
            "postForObject": "POST",
            "put": "PUT",
            "delete": "DELETE",
            "patchForObject": "PATCH",
            "optionsForAllow": "OPTIONS",
            "exchange": "UNKNOWN",
            "execute": "UNKNOWN",
        }
    )
)

_REQUEST_ENTITY_PREFIXES: Final[tuple[str, ...]] = (
    "org.springframework.http.RequestEntity",
    *_nested_receiver_prefixes(
        "org.springframework.http.RequestEntity",
        ("BodyBuilder", "HeadersBuilder"),
    ),
)
_REQUEST_ENTITY_FACTORY_HTTP_METHODS: Final[dict[str, str]] = (
    _normalize_http_method_map(
        {
            "get": "GET",
            "post": "POST",
            "put": "PUT",
            "patch": "PATCH",
            "delete": "DELETE",
            "head": "HEAD",
            "options": "OPTIONS",
            "method": "UNKNOWN",
        }
    )
)
_REQUEST_ENTITY_BUILDER_METHODS: Final[frozenset[str]] = frozenset(
    {"header", "headers", "contentType", "accept", "acceptCharset", "body", "build"}
)

_STANDARD_HTTP_EVENT_METHODS: Final[dict[str, str]] = _normalize_http_method_map(
    {
        "get": "GET",
        "post": "POST",
        "put": "PUT",
        "delete": "DELETE",
        "patch": "PATCH",
        "head": "HEAD",
        "options": "OPTIONS",
        "request": "UNKNOWN",
    }
)

_STANDARD_HTTP_BUILDER_METHODS: Final[dict[str, str]] = _normalize_http_method_map(
    {
        "get": "GET",
        "post": "POST",
        "put": "PUT",
        "delete": "DELETE",
        "patch": "PATCH",
        "head": "HEAD",
        "options": "OPTIONS",
        "method": "UNKNOWN",
        "request": "UNKNOWN",
    }
)

_MOCKMVC_REQUEST_FACTORY_HTTP_METHODS: Final[dict[str, str]] = (
    _normalize_http_method_map(
        {
            "get": "GET",
            "post": "POST",
            "put": "PUT",
            "delete": "DELETE",
            "patch": "PATCH",
            "head": "HEAD",
            "options": "OPTIONS",
            "request": "UNKNOWN",
            "multipart": "POST",
            "asyncDispatch": "UNKNOWN",
        }
    )
)

_RESTDOCS_MOCKMVC_REQUEST_FACTORY_HTTP_METHODS: Final[dict[str, str]] = {
    method_name: http_method
    for method_name, http_method in _MOCKMVC_REQUEST_FACTORY_HTTP_METHODS.items()
    if method_name != "asyncdispatch"
}

_MOCKMVC_MATCHER_ROOT_METHODS: Final[frozenset[str]] = normalize_method_names(
    {
        "status",
        "content",
        "jsonPath",
        "model",
        "view",
        "header",
        "cookie",
        "flash",
        "request",
        "xpath",
        "forwardedUrl",
        "redirectedUrl",
        "handler",
    }
)

_REST_ASSURED_REQUEST_SPEC_PREFIXES: Final[tuple[str, ...]] = _with_restassured_roots(
    "specification.RequestSpecification",
    "specification.FilterableRequestSpecification",
    "specification.RequestSender",
    "specification.RequestSenderOptions",
    "internal.RequestSpecificationImpl",
)
_REST_ASSURED_MODULE_REQUEST_PREFIXES: Final[tuple[str, ...]] = (
    *_with_restassured_roots(
        "module.mockmvc.specification.MockMvcRequestSpecification",
        "module.mockmvc.specification.MockMvcRequestSender",
        "module.mockmvc.specification.MockMvcRequestSenderOptions",
        "module.mockmvc.internal.MockMvcRequestSpecificationImpl",
    ),
    "io.restassured.module.webtestclient.specification.WebTestClientRequestSpecification",
    "io.restassured.module.webtestclient.specification.WebTestClientRequestSender",
    "io.restassured.module.webtestclient.specification.WebTestClientRequestSenderOptions",
    "io.restassured.module.webtestclient.internal.WebTestClientRequestSpecificationImpl",
)
_REST_ASSURED_REQUEST_FACTORY_PREFIXES: Final[tuple[str, ...]] = (
    *_with_restassured_roots(
        "RestAssured",
        "module.mockmvc.RestAssuredMockMvc",
    ),
    "io.restassured.module.webtestclient.RestAssuredWebTestClient",
)
_REST_ASSURED_REQUEST_EVENT_PREFIXES: Final[tuple[str, ...]] = (
    *_REST_ASSURED_REQUEST_SPEC_PREFIXES,
    *_REST_ASSURED_MODULE_REQUEST_PREFIXES,
)

_REST_ASSURED_RESPONSE_PREFIXES: Final[tuple[str, ...]] = _with_restassured_roots(
    "response.Response",
    "internal.RestAssuredResponseImpl",
)
_REST_ASSURED_MODULE_RESPONSE_PREFIXES: Final[tuple[str, ...]] = (
    *_with_restassured_roots(
        "module.mockmvc.response.MockMvcResponse",
        "module.mockmvc.internal.MockMvcRestAssuredResponseImpl",
    ),
    "io.restassured.module.webtestclient.response.WebTestClientResponse",
    "io.restassured.module.webtestclient.internal.WebTestClientRestAssuredResponseImpl",
)

_REST_ASSURED_RESPONSE_SPEC_PREFIXES: Final[tuple[str, ...]] = _with_restassured_roots(
    "specification.ResponseSpecification",
    "specification.FilterableResponseSpecification",
    "internal.ResponseSpecificationImpl",
)

_REST_ASSURED_VALIDATABLE_RESPONSE_PREFIXES: Final[tuple[str, ...]] = (
    _with_restassured_roots(
        "response.ValidatableResponse",
        "response.ValidatableResponseOptions",
        "internal.ValidatableResponseImpl",
        "internal.ValidatableResponseOptionsImpl",
    )
)
_REST_ASSURED_MODULE_VALIDATABLE_RESPONSE_PREFIXES: Final[tuple[str, ...]] = (
    *_with_restassured_roots(
        "module.mockmvc.response.ValidatableMockMvcResponse",
        "module.mockmvc.internal.ValidatableMockMvcResponseImpl",
    ),
    "io.restassured.module.webtestclient.response.ValidatableWebTestClientResponse",
    "io.restassured.module.webtestclient.internal.ValidatableWebTestClientResponseImpl",
)

_REST_ASSURED_RESPONSE_ASSERTION_PREFIXES: Final[tuple[str, ...]] = (
    *_REST_ASSURED_RESPONSE_SPEC_PREFIXES,
    *_REST_ASSURED_VALIDATABLE_RESPONSE_PREFIXES,
    *_REST_ASSURED_MODULE_VALIDATABLE_RESPONSE_PREFIXES,
)

_REST_ASSURED_EXTRACTABLE_RESPONSE_PREFIXES: Final[tuple[str, ...]] = (
    _with_restassured_roots(
        "response.ExtractableResponse",
        "response.ExtractableResponseOptions",
        "response.ResponseOptions",
        "response.ResponseBodyExtractionOptions",
        "internal.RestAssuredResponseOptionsImpl",
    )
)

_WEBTESTCLIENT_REQUEST_SPEC_PREFIXES: Final[tuple[str, ...]] = (
    *_nested_receiver_prefixes(
        "org.springframework.test.web.reactive.server.WebTestClient",
        (
            "RequestHeadersUriSpec",
            "RequestBodyUriSpec",
            "RequestHeadersSpec",
            "RequestBodySpec",
        ),
    ),
    *_nested_receiver_prefixes(
        "org.springframework.test.web.reactive.server.DefaultWebTestClient",
        ("DefaultRequestBodyUriSpec",),
    ),
)

_WEBTESTCLIENT_REQUEST_BUILDER_METHODS: Final[frozenset[str]] = frozenset(
    {
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "options",
        "method",
        "uri",
        "header",
        "headers",
        "cookie",
        "cookies",
        "accept",
        "acceptCharset",
        "contentType",
        "contentLength",
        "body",
        "bodyValue",
        "syncBody",
        "ifModifiedSince",
        "ifNoneMatch",
        "attribute",
        "attributes",
        "apiVersion",
    }
)

_WEBTESTCLIENT_REQUEST_BUILDER_HTTP_METHODS: Final[dict[str, str]] = (
    _normalize_http_method_map(
        {
            "get": "GET",
            "post": "POST",
            "put": "PUT",
            "delete": "DELETE",
            "patch": "PATCH",
            "head": "HEAD",
            "options": "OPTIONS",
            "method": "UNKNOWN",
        }
    )
)

_WEBTESTCLIENT_RESPONSE_SPEC_PREFIXES: Final[tuple[str, ...]] = (
    *_nested_receiver_prefixes(
        "org.springframework.test.web.reactive.server.WebTestClient",
        ("ResponseSpec",),
    ),
    *_nested_receiver_prefixes(
        "org.springframework.test.web.reactive.server.DefaultWebTestClient",
        ("DefaultResponseSpec",),
    ),
)

_WEBTESTCLIENT_BODY_SPEC_PREFIXES: Final[tuple[str, ...]] = (
    *_nested_receiver_prefixes(
        "org.springframework.test.web.reactive.server.WebTestClient",
        ("BodySpec", "BodyContentSpec", "ListBodySpec", "JsonPathAssertions"),
    ),
    *_nested_receiver_prefixes(
        "org.springframework.test.web.reactive.server.DefaultWebTestClient",
        ("DefaultBodySpec", "DefaultBodyContentSpec", "DefaultListBodySpec"),
    ),
    "org.springframework.test.web.reactive.server.BodySpec",
    "org.springframework.test.web.reactive.server.BodyContentSpec",
    "org.springframework.test.web.reactive.server.ListBodySpec",
    "org.springframework.test.web.reactive.server.JsonPathAssertions",
    "org.springframework.test.web.reactive.server.XpathAssertions",
)

_WEBTESTCLIENT_HEADER_ASSERTION_PREFIXES: Final[tuple[str, ...]] = (
    "org.springframework.test.web.reactive.server.HeaderAssertions",
    "org.springframework.test.web.reactive.server.CookieAssertions",
)

_WEBTESTCLIENT_EXCHANGE_RESULT_PREFIXES: Final[tuple[str, ...]] = (
    "org.springframework.test.web.reactive.server.ExchangeResult",
    "org.springframework.test.web.reactive.server.EntityExchangeResult",
    "org.springframework.test.web.reactive.server.FluxExchangeResult",
)

_WEBCLIENT_REQUEST_SPEC_PREFIXES: Final[tuple[str, ...]] = (
    *_nested_receiver_prefixes(
        "org.springframework.web.reactive.function.client.WebClient",
        (
            "UriSpec",
            "RequestHeadersUriSpec",
            "RequestBodyUriSpec",
            "RequestHeadersSpec",
            "RequestBodySpec",
        ),
    ),
    *_nested_receiver_prefixes(
        "org.springframework.web.reactive.function.client.DefaultWebClient",
        (
            "DefaultRequestHeadersUriSpec",
            "DefaultRequestBodyUriSpec",
            "DefaultRequestHeadersSpec",
            "DefaultRequestBodySpec",
        ),
    ),
)

_REST_CLIENT_REQUEST_SPEC_PREFIXES: Final[tuple[str, ...]] = (
    *_nested_receiver_prefixes(
        "org.springframework.web.client.RestClient",
        (
            "UriSpec",
            "RequestHeadersUriSpec",
            "RequestBodyUriSpec",
            "RequestHeadersSpec",
            "RequestBodySpec",
        ),
    ),
    *_nested_receiver_prefixes(
        "org.springframework.web.client.DefaultRestClient",
        ("DefaultRequestBodyUriSpec",),
    ),
)


def _with_jaxrs_roots(*type_suffixes: str) -> tuple[str, ...]:
    return tuple(
        f"{root}{type_suffix}"
        for root in ("javax.ws.rs.client.", "jakarta.ws.rs.client.")
        for type_suffix in type_suffixes
    )


_JAXRS_CLIENT_PREFIXES: Final[tuple[str, ...]] = _with_jaxrs_roots("Client")
_JAXRS_WEB_TARGET_PREFIXES: Final[tuple[str, ...]] = _with_jaxrs_roots("WebTarget")
_JAXRS_TARGET_BUILDER_METHODS: Final[frozenset[str]] = frozenset(
    {
        "target",
        "path",
        "queryParam",
        "matrixParam",
        "resolveTemplate",
        "resolveTemplateFromEncoded",
        "resolveTemplates",
        "resolveTemplatesFromEncoded",
        "request",
        "property",
    }
)
_JAXRS_INVOCATION_BUILDER_PREFIXES: Final[tuple[str, ...]] = _with_jaxrs_roots(
    "Invocation",
)
_JAXRS_INVOKER_PREFIXES: Final[tuple[str, ...]] = _with_jaxrs_roots(
    "Invocation",
    "SyncInvoker",
    "AsyncInvoker",
    "RxInvoker",
    "CompletionStageRxInvoker",
)

# SyncInvoker/AsyncInvoker dispatch verbs. `request` (WebTarget) is a BUILDER, so it
# is deliberately excluded here. `method` carries its verb as a string/enum argument.
_JAXRS_CLIENT_EVENT_HTTP_METHODS: Final[dict[str, str]] = _normalize_http_method_map(
    {
        "get": "GET",
        "post": "POST",
        "put": "PUT",
        "delete": "DELETE",
        "head": "HEAD",
        "options": "OPTIONS",
        "trace": "TRACE",
        "method": "UNKNOWN",
    }
)

_JAVA_HTTPCLIENT_BUILDER_PREFIXES: Final[tuple[str, ...]] = (
    *_nested_receiver_prefixes("java.net.http.HttpRequest", ("Builder",)),
)

_MICRONAUT_CLIENT_PREFIXES: Final[tuple[str, ...]] = (
    "io.micronaut.http.client.HttpClient",
    "io.micronaut.http.client.BlockingHttpClient",
    "io.micronaut.http.client.StreamingHttpClient",
)
_MICRONAUT_REQUEST_PREFIXES: Final[tuple[str, ...]] = (
    "io.micronaut.http.HttpRequest",
    "io.micronaut.http.MutableHttpRequest",
)
# exchange/retrieve verbs are overload-dependent: the HttpRequest overloads carry
# the verb in the request object, and the String-URI overloads delegate to
# HttpRequest.GET(uri) (classification applies that default from argument types).
_MICRONAUT_CLIENT_EVENT_HTTP_METHODS: Final[dict[str, str]] = (
    _normalize_http_method_map(
        {
            "exchange": "UNKNOWN",
            "retrieve": "UNKNOWN",
            "dataStream": "UNKNOWN",
            "exchangeStream": "UNKNOWN",
            "jsonStream": "UNKNOWN",
        }
    )
)
# HttpRequest static factories encode the verb in the method name; create(...)
# carries it as an HttpMethod argument.
_MICRONAUT_REQUEST_FACTORY_HTTP_METHODS: Final[dict[str, str]] = (
    _normalize_http_method_map(
        {
            "GET": "GET",
            "POST": "POST",
            "PUT": "PUT",
            "PATCH": "PATCH",
            "DELETE": "DELETE",
            "HEAD": "HEAD",
            "OPTIONS": "OPTIONS",
            "create": "UNKNOWN",
        }
    )
)
_MICRONAUT_REQUEST_BUILDER_METHODS: Final[frozenset[str]] = frozenset(
    {
        "uri",
        "header",
        "headers",
        "body",
        "contentType",
        "contentLength",
        "contentEncoding",
        "accept",
        "cookie",
        "cookies",
        "basicAuth",
        "bearerAuth",
    }
)

_OKHTTP_REQUEST_BUILDER_PREFIXES: Final[tuple[str, ...]] = (
    *_nested_receiver_prefixes("okhttp3.Request", ("Builder",)),
)

_APACHE_HTTPCLIENT4_EVENT_PREFIXES: Final[tuple[str, ...]] = (
    "org.apache.http.client.fluent.Executor",
    "org.apache.http.client.fluent.Request",
    "org.apache.http.client.HttpClient",
    "org.apache.http.nio.client.HttpAsyncClient",
    "org.apache.http.nio.client.HttpPipeliningClient",
    "org.apache.http.impl.client.AbstractHttpClient",
    "org.apache.http.impl.client.CloseableHttpClient",
    "org.apache.http.impl.client.ContentEncodingHttpClient",
    "org.apache.http.impl.client.DecompressingHttpClient",
    "org.apache.http.impl.client.DefaultHttpClient",
    "org.apache.http.impl.client.FutureRequestExecutionService",
    "org.apache.http.impl.client.MinimalHttpClient",
    "org.apache.http.impl.client.SystemDefaultHttpClient",
    "org.apache.http.impl.nio.client.AbstractHttpAsyncClient",
    "org.apache.http.impl.nio.client.CloseableHttpAsyncClient",
    "org.apache.http.impl.nio.client.CloseableHttpPipeliningClient",
    "org.apache.http.impl.nio.client.DefaultHttpAsyncClient",
)
_APACHE_HTTPCLIENT4_REQUEST_PREFIXES: Final[tuple[str, ...]] = (
    "org.apache.http.client.fluent.Request",
    "org.apache.http.HttpEntityEnclosingRequest",
    "org.apache.http.HttpRequest",
    "org.apache.http.client.methods.HttpDelete",
    "org.apache.http.client.methods.HttpEntityEnclosingRequestBase",
    "org.apache.http.client.methods.HttpGet",
    "org.apache.http.client.methods.HttpHead",
    "org.apache.http.client.methods.HttpOptions",
    "org.apache.http.client.methods.HttpPatch",
    "org.apache.http.client.methods.HttpPost",
    "org.apache.http.client.methods.HttpPut",
    "org.apache.http.client.methods.HttpRequestBase",
    "org.apache.http.client.methods.HttpRequestWrapper",
    "org.apache.http.client.methods.HttpTrace",
    "org.apache.http.client.methods.HttpUriRequest",
    "org.apache.http.client.methods.RequestBuilder",
    "org.apache.http.message.BasicHttpEntityEnclosingRequest",
    "org.apache.http.message.BasicHttpRequest",
)
_APACHE_HTTPCLIENT5_EVENT_PREFIXES: Final[tuple[str, ...]] = (
    "org.apache.hc.client5.http.fluent.Async",
    "org.apache.hc.client5.http.fluent.Executor",
    "org.apache.hc.client5.http.fluent.Request",
    "org.apache.hc.client5.http.async.HttpAsyncClient",
    "org.apache.hc.client5.http.classic.HttpClient",
    "org.apache.hc.client5.http.impl.async.CloseableHttpAsyncClient",
    "org.apache.hc.client5.http.impl.async.InternalH2AsyncClient",
    "org.apache.hc.client5.http.impl.async.InternalHttpAsyncClient",
    "org.apache.hc.client5.http.impl.async.MinimalH2AsyncClient",
    "org.apache.hc.client5.http.impl.async.MinimalHttpAsyncClient",
    "org.apache.hc.client5.http.impl.classic.CloseableHttpClient",
    "org.apache.hc.client5.http.impl.classic.FutureRequestExecutionService",
    "org.apache.hc.client5.http.impl.classic.InternalHttpClient",
    "org.apache.hc.client5.http.impl.classic.MinimalHttpClient",
)
_APACHE_HTTPCLIENT5_REQUEST_PREFIXES: Final[tuple[str, ...]] = (
    "org.apache.hc.client5.http.async.methods.BasicHttpRequests",
    "org.apache.hc.client5.http.async.methods.BasicRequestBuilder",
    "org.apache.hc.client5.http.async.methods.SimpleHttpRequest",
    "org.apache.hc.client5.http.async.methods.SimpleHttpRequests",
    "org.apache.hc.client5.http.async.methods.SimpleRequestBuilder",
    "org.apache.hc.client5.http.classic.methods.",
    "org.apache.hc.client5.http.fluent.Request",
    "org.apache.hc.core5.http.ClassicHttpRequest",
    "org.apache.hc.core5.http.HttpRequest",
    "org.apache.hc.core5.http.io.support.ClassicRequestBuilder",
    "org.apache.hc.core5.http.message.BasicClassicHttpRequest",
    "org.apache.hc.core5.http.message.BasicHttpRequest",
    "org.apache.hc.core5.http.message.HttpRequestWrapper",
    "org.apache.hc.core5.http.support.AbstractRequestBuilder",
)
_APACHE_HTTPCLIENT_CONSTRUCTOR_VERBS: Final[dict[str, str]] = {
    "HttpGet": "GET",
    "HttpPost": "POST",
    "HttpPut": "PUT",
    "HttpDelete": "DELETE",
    "HttpPatch": "PATCH",
    "HttpHead": "HEAD",
    "HttpOptions": "OPTIONS",
    "HttpTrace": "TRACE",
}
_APACHE_HTTPCLIENT_DYNAMIC_METHOD_CONSTRUCTORS: Final[frozenset[str]] = frozenset(
    {"SimpleHttpRequest"}
)
_APACHE_HTTPCLIENT_REQUEST_VERB_METHODS: Final[dict[str, str]] = (
    _normalize_http_method_map(
        {
            "get": "GET",
            "post": "POST",
            "put": "PUT",
            "delete": "DELETE",
            "patch": "PATCH",
            "head": "HEAD",
            "options": "OPTIONS",
            "trace": "TRACE",
        }
    )
)

_CITRUS_ACTION_ROOT_PREFIXES: Final[tuple[str, ...]] = (
    "org.citrusframework.http.actions.HttpClientActionBuilder",
    "org.citrusframework.http.actions.HttpServerActionBuilder",
)
_CITRUS_REQUEST_EVENT_PREFIXES: Final[tuple[str, ...]] = (
    *_nested_receiver_prefixes(
        "org.citrusframework.http.actions.HttpClientActionBuilder",
        ("HttpClientSendActionBuilder",),
    ),
    *_nested_receiver_prefixes(
        "org.citrusframework.http.actions.HttpServerActionBuilder",
        ("HttpServerReceiveActionBuilder",),
    ),
    "org.citrusframework.http.actions.HttpClientRequestActionBuilder",
    "org.citrusframework.http.actions.HttpServerRequestActionBuilder",
)
_CITRUS_REQUEST_BUILDER_PREFIXES: Final[tuple[str, ...]] = (
    "org.citrusframework.http.actions.HttpClientRequestActionBuilder",
    "org.citrusframework.http.actions.HttpServerRequestActionBuilder",
)

RECEIVERLESS_REQUEST_INFERENCE_RULES: Final[
    tuple[ReceiverlessRequestInferenceRule, ...]
] = (
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        owner_family="webtestclient.request_executor",
        request_role=HttpRequestRole.EVENT,
        method_http_methods={
            "exchange": "UNKNOWN",
            "exchangeSuccessfully": "UNKNOWN",
            "exchangeToMono": "UNKNOWN",
            "exchangeToFlux": "UNKNOWN",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        owner_family="webtestclient.request_builder",
        request_role=HttpRequestRole.BUILDER,
        method_http_methods={
            "get": "GET",
            "post": "POST",
            "put": "PUT",
            "delete": "DELETE",
            "patch": "PATCH",
            "head": "HEAD",
            "options": "OPTIONS",
            "method": "UNKNOWN",
            "uri": "UNKNOWN",
            "header": "UNKNOWN",
            "headers": "UNKNOWN",
            "cookie": "UNKNOWN",
            "cookies": "UNKNOWN",
            "accept": "UNKNOWN",
            "acceptCharset": "UNKNOWN",
            "contentType": "UNKNOWN",
            "contentLength": "UNKNOWN",
            "body": "UNKNOWN",
            "bodyValue": "UNKNOWN",
            "syncBody": "UNKNOWN",
            "ifModifiedSince": "UNKNOWN",
            "ifNoneMatch": "UNKNOWN",
            "attribute": "UNKNOWN",
            "attributes": "UNKNOWN",
            "apiVersion": "UNKNOWN",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.WEBCLIENT,
        owner_family="webclient.request",
        request_role=HttpRequestRole.EVENT,
        method_http_methods={
            "retrieve": "UNKNOWN",
            "exchange": "UNKNOWN",
            "exchangeToMono": "UNKNOWN",
            "exchangeToFlux": "UNKNOWN",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.WEBCLIENT,
        owner_family="webclient.request",
        request_role=HttpRequestRole.BUILDER,
        method_http_methods={
            "get": "GET",
            "head": "HEAD",
            "post": "POST",
            "put": "PUT",
            "patch": "PATCH",
            "delete": "DELETE",
            "options": "OPTIONS",
            "method": "UNKNOWN",
            "uri": "UNKNOWN",
            "header": "UNKNOWN",
            "headers": "UNKNOWN",
            "cookie": "UNKNOWN",
            "cookies": "UNKNOWN",
            "accept": "UNKNOWN",
            "acceptCharset": "UNKNOWN",
            "contentType": "UNKNOWN",
            "contentLength": "UNKNOWN",
            "body": "UNKNOWN",
            "bodyValue": "UNKNOWN",
            "syncBody": "UNKNOWN",
            "ifModifiedSince": "UNKNOWN",
            "ifNoneMatch": "UNKNOWN",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.REST_CLIENT,
        owner_family="rest-client.request",
        request_role=HttpRequestRole.EVENT,
        method_http_methods={
            "retrieve": "UNKNOWN",
            "exchange": "UNKNOWN",
            "exchangeForRequiredValue": "UNKNOWN",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.REST_CLIENT,
        owner_family="rest-client.request",
        request_role=HttpRequestRole.BUILDER,
        method_http_methods={
            "get": "GET",
            "head": "HEAD",
            "post": "POST",
            "put": "PUT",
            "patch": "PATCH",
            "delete": "DELETE",
            "options": "OPTIONS",
            "method": "UNKNOWN",
            "uri": "UNKNOWN",
            "header": "UNKNOWN",
            "headers": "UNKNOWN",
            "cookie": "UNKNOWN",
            "cookies": "UNKNOWN",
            "accept": "UNKNOWN",
            "acceptCharset": "UNKNOWN",
            "contentType": "UNKNOWN",
            "contentLength": "UNKNOWN",
            "body": "UNKNOWN",
            "ifModifiedSince": "UNKNOWN",
            "ifNoneMatch": "UNKNOWN",
            "attribute": "UNKNOWN",
            "attributes": "UNKNOWN",
            "apiVersion": "UNKNOWN",
            "httpRequest": "UNKNOWN",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.JAVA_HTTPCLIENT,
        owner_family="java-httpclient.request",
        request_role=HttpRequestRole.EVENT,
        method_http_methods={
            "send": "UNKNOWN",
            "sendAsync": "UNKNOWN",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.JAVA_HTTPCLIENT,
        owner_family="java-httpclient.request",
        request_role=HttpRequestRole.BUILDER,
        method_http_methods={
            "newBuilder": "UNKNOWN",
            "uri": "UNKNOWN",
            "header": "UNKNOWN",
            "headers": "UNKNOWN",
            "setHeader": "UNKNOWN",
            "method": "UNKNOWN",
            "get": "GET",
            "post": "POST",
            "put": "PUT",
            "delete": "DELETE",
            "head": "HEAD",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.MICRONAUT_CLIENT,
        owner_family="micronaut-client.request",
        request_role=HttpRequestRole.EVENT,
        method_http_methods=dict(_MICRONAUT_CLIENT_EVENT_HTTP_METHODS),
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.MICRONAUT_CLIENT,
        owner_family="micronaut-client.request",
        request_role=HttpRequestRole.BUILDER,
        method_http_methods={
            **_MICRONAUT_REQUEST_FACTORY_HTTP_METHODS,
            **{
                method_name: "UNKNOWN"
                for method_name in _MICRONAUT_REQUEST_BUILDER_METHODS
            },
            "toBlocking": "UNKNOWN",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.OKHTTP,
        owner_family="okhttp.request",
        request_role=HttpRequestRole.EVENT,
        method_http_methods={
            "execute": "UNKNOWN",
            "enqueue": "UNKNOWN",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.OKHTTP,
        owner_family="okhttp.request",
        request_role=HttpRequestRole.BUILDER,
        method_http_methods={
            "newCall": "UNKNOWN",
            "url": "UNKNOWN",
            "addHeader": "UNKNOWN",
            "header": "UNKNOWN",
            "method": "UNKNOWN",
            "get": "GET",
            "head": "HEAD",
            "post": "POST",
            "put": "PUT",
            "patch": "PATCH",
            "delete": "DELETE",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.REST_ASSURED,
        owner_family="rest-assured.request_event",
        request_role=HttpRequestRole.EVENT,
        method_http_methods=dict(_STANDARD_HTTP_EVENT_METHODS),
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.REST_ASSURED,
        owner_family="rest-assured.request_builder",
        request_role=HttpRequestRole.BUILDER,
        method_http_methods={
            "given": "UNKNOWN",
            "when": "UNKNOWN",
            "header": "UNKNOWN",
            "headers": "UNKNOWN",
            "queryParam": "UNKNOWN",
            "queryParams": "UNKNOWN",
            "pathParam": "UNKNOWN",
            "pathParams": "UNKNOWN",
            "param": "UNKNOWN",
            "params": "UNKNOWN",
            "auth": "UNKNOWN",
            "oauth2": "UNKNOWN",
            "body": "UNKNOWN",
            "contentType": "UNKNOWN",
            "accept": "UNKNOWN",
            "cookie": "UNKNOWN",
            "cookies": "UNKNOWN",
            "baseUri": "UNKNOWN",
            "basePath": "UNKNOWN",
            "port": "UNKNOWN",
            "formParam": "UNKNOWN",
            "formParams": "UNKNOWN",
            "multiPart": "UNKNOWN",
            "spec": "UNKNOWN",
            "filter": "UNKNOWN",
            "urlEncodingEnabled": "UNKNOWN",
            "relaxedHTTPSValidation": "UNKNOWN",
        },
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.MOCKMVC,
        owner_family="mockmvc.request_executor",
        request_role=HttpRequestRole.EVENT,
        method_http_methods={"perform": "UNKNOWN"},
    ),
    ReceiverlessRequestInferenceRule(
        framework=HttpDispatchFramework.MOCKMVC,
        owner_family="mockmvc.request_builder",
        request_role=HttpRequestRole.BUILDER,
        method_http_methods={
            "request": "UNKNOWN",
            "header": "UNKNOWN",
            "headers": "UNKNOWN",
            "param": "UNKNOWN",
            "params": "UNKNOWN",
            "file": "UNKNOWN",
            "content": "UNKNOWN",
            "contentType": "UNKNOWN",
            "accept": "UNKNOWN",
            "characterEncoding": "UNKNOWN",
            "queryParam": "UNKNOWN",
            "queryParams": "UNKNOWN",
            "cookie": "UNKNOWN",
            "cookies": "UNKNOWN",
            "flashAttr": "UNKNOWN",
            "sessionAttr": "UNKNOWN",
            "requestAttr": "UNKNOWN",
            "contextPath": "UNKNOWN",
            "servletPath": "UNKNOWN",
            "secure": "UNKNOWN",
            "locale": "UNKNOWN",
            "with": "UNKNOWN",
            "principal": "UNKNOWN",
        },
    ),
)


def infer_receiverless_request_target(
    framework: HttpDispatchFramework,
    method_name: str,
) -> ReceiverlessRequestInferenceTarget | None:
    normalized_method_name = method_name.lower()
    for rule in RECEIVERLESS_REQUEST_INFERENCE_RULES:
        if rule.framework != framework:
            continue
        framework_http_method = rule.method_http_methods.get(normalized_method_name)
        if framework_http_method is None:
            continue
        return ReceiverlessRequestInferenceTarget(
            framework=rule.framework,
            owner_family=rule.owner_family,
            request_role=rule.request_role,
            framework_http_method=framework_http_method,
        )
    return None


# Methods that appear after `.extract()` and read response state. Shared with the
# receiverless recovery path so both typed and recovered chains agree on the
# same extractor set.
_REST_ASSURED_EXTRACTOR_METHODS: Final[frozenset[str]] = frozenset(
    {
        "extract",
        "path",
        "as",
        "response",
        "body",
        "headers",
        "header",
        "cookies",
        "detailedCookies",
        "cookie",
        "detailedCookie",
        "contentType",
        "statusCode",
        "statusLine",
        "sessionId",
        "time",
        "timeIn",
        "asString",
        "asPrettyString",
        "asByteArray",
        "asInputStream",
        "jsonPath",
        "xmlPath",
        "htmlPath",
    }
)


HTTP_OWNER_FAMILY_RULES: Final[tuple[HttpOwnerFamilyRule, ...]] = (
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_ASSURED,
        family_id="rest-assured.request_factory",
        receiver_prefixes=_REST_ASSURED_REQUEST_FACTORY_PREFIXES,
        request_role_by_method_name=_merge_request_role_maps(
            _request_role_map({"given", "when"}, HttpRequestRole.BUILDER),
            _request_role_map(
                _STANDARD_HTTP_EVENT_METHODS.keys(),
                HttpRequestRole.EVENT,
            ),
        ),
        http_method_by_method_name=dict(_STANDARD_HTTP_EVENT_METHODS),
        static_import_owners=_REST_ASSURED_REQUEST_FACTORY_PREFIXES,
        static_import_methods=normalize_method_names(
            {"given", "when"} | set(_STANDARD_HTTP_EVENT_METHODS)
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_ASSURED,
        family_id="rest-assured.request_builder",
        receiver_prefixes=(
            *_REST_ASSURED_REQUEST_SPEC_PREFIXES,
            *_REST_ASSURED_MODULE_REQUEST_PREFIXES,
        ),
        request_role_by_method_name=_request_role_map(
            {
                "given",
                "when",
                "header",
                "headers",
                "queryParam",
                "queryParams",
                "pathParam",
                "pathParams",
                "param",
                "params",
                "auth",
                "oauth2",
                "body",
                "contentType",
                "accept",
                "cookie",
                "cookies",
                "baseUri",
                "basePath",
                "port",
                "formParam",
                "formParams",
                "multiPart",
                "spec",
                "filter",
                "urlEncodingEnabled",
                "relaxedHTTPSValidation",
            },
            HttpRequestRole.BUILDER,
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_ASSURED,
        family_id="rest-assured.request_event",
        receiver_prefixes=_REST_ASSURED_REQUEST_EVENT_PREFIXES,
        request_role_by_method_name=_request_role_map(
            _STANDARD_HTTP_EVENT_METHODS.keys(),
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name=dict(_STANDARD_HTTP_EVENT_METHODS),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_ASSURED,
        family_id="rest-assured.response_inspector",
        receiver_prefixes=(
            *_REST_ASSURED_RESPONSE_PREFIXES,
            *_REST_ASSURED_MODULE_RESPONSE_PREFIXES,
        ),
        response_role_by_method_name=_response_role_map(
            {"then"},
            HttpResponseRole.INSPECTOR,
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_ASSURED,
        family_id="rest-assured.status_assertion",
        receiver_prefixes=_REST_ASSURED_RESPONSE_ASSERTION_PREFIXES,
        response_role_by_method_name=_response_role_map(
            {"statusCode", "statusLine"},
            HttpResponseRole.STATUS_ASSERTION,
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_ASSURED,
        family_id="rest-assured.body_assertion",
        receiver_prefixes=_REST_ASSURED_RESPONSE_ASSERTION_PREFIXES,
        response_role_by_method_name=_merge_response_role_maps(
            _response_role_map(
                {"body"},
                HttpResponseRole.BODY_ASSERTION,
            ),
            _response_role_map(
                {
                    "header",
                    "headers",
                    "contentType",
                    "contentTypeCompatibleWith",
                    "cookie",
                    "cookies",
                },
                HttpResponseRole.HEADER_ASSERTION,
            ),
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_ASSURED,
        family_id="rest-assured.response_extractor",
        receiver_prefixes=(
            *_REST_ASSURED_RESPONSE_PREFIXES,
            *_REST_ASSURED_MODULE_RESPONSE_PREFIXES,
            *_REST_ASSURED_VALIDATABLE_RESPONSE_PREFIXES,
            *_REST_ASSURED_MODULE_VALIDATABLE_RESPONSE_PREFIXES,
            *_REST_ASSURED_EXTRACTABLE_RESPONSE_PREFIXES,
            *_with_restassured_roots("path.json.JsonPath", "path.xml.XmlPath"),
        ),
        response_role_by_method_name=_response_role_map(
            _REST_ASSURED_EXTRACTOR_METHODS,
            HttpResponseRole.EXTRACTOR,
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
        family_id="test-rest-template.request",
        receiver_prefixes=(
            "org.springframework.boot.test.web.client.TestRestTemplate",
            "org.springframework.boot.resttestclient.TestRestTemplate",
        ),
        request_role_by_method_name=_request_role_map(
            _REST_TEMPLATE_HTTP_METHOD_BY_REQUEST_METHOD.keys(),
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name=dict(_REST_TEMPLATE_HTTP_METHOD_BY_REQUEST_METHOD),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_TEMPLATE,
        family_id="rest-template.request",
        receiver_prefixes=(
            "org.springframework.web.client.RestOperations",
            "org.springframework.web.client.RestTemplate",
        ),
        request_role_by_method_name=_request_role_map(
            _REST_TEMPLATE_HTTP_METHOD_BY_REQUEST_METHOD.keys(),
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name=dict(_REST_TEMPLATE_HTTP_METHOD_BY_REQUEST_METHOD),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_TEMPLATE,
        family_id="rest-template.request",
        receiver_prefixes=_REQUEST_ENTITY_PREFIXES,
        request_role_by_method_name=_merge_request_role_maps(
            _request_role_map(
                _REQUEST_ENTITY_FACTORY_HTTP_METHODS.keys(),
                HttpRequestRole.BUILDER,
            ),
            _request_role_map(
                _REQUEST_ENTITY_BUILDER_METHODS,
                HttpRequestRole.BUILDER,
            ),
        ),
        http_method_by_method_name=dict(_REQUEST_ENTITY_FACTORY_HTTP_METHODS),
        static_import_owners=("org.springframework.http.RequestEntity",),
        static_import_methods=normalize_method_names(
            _REQUEST_ENTITY_FACTORY_HTTP_METHODS.keys()
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.MOCKMVC,
        family_id="mockmvc.request_factory",
        receiver_prefixes=(
            "org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
        ),
        request_role_by_method_name=_request_role_map(
            _MOCKMVC_REQUEST_FACTORY_HTTP_METHODS.keys(),
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name=dict(_MOCKMVC_REQUEST_FACTORY_HTTP_METHODS),
        static_import_owners=(
            "org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
        ),
        static_import_methods=normalize_method_names(
            _MOCKMVC_REQUEST_FACTORY_HTTP_METHODS.keys()
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.MOCKMVC,
        family_id="mockmvc.request_factory",
        receiver_prefixes=(
            "org.springframework.restdocs.mockmvc.RestDocumentationRequestBuilders",
        ),
        request_role_by_method_name=_request_role_map(
            _RESTDOCS_MOCKMVC_REQUEST_FACTORY_HTTP_METHODS.keys(),
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name=dict(_RESTDOCS_MOCKMVC_REQUEST_FACTORY_HTTP_METHODS),
        static_import_owners=(
            "org.springframework.restdocs.mockmvc.RestDocumentationRequestBuilders",
        ),
        static_import_methods=normalize_method_names(
            _RESTDOCS_MOCKMVC_REQUEST_FACTORY_HTTP_METHODS.keys()
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.MOCKMVC,
        family_id="mockmvc.request_builder",
        receiver_prefixes=(
            "org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder",
            "org.springframework.test.web.servlet.request.MockMultipartHttpServletRequestBuilder",
            "org.springframework.test.web.servlet.request.AbstractMockHttpServletRequestBuilder",
            "org.springframework.test.web.servlet.request.AbstractMockMultipartHttpServletRequestBuilder",
        ),
        request_role_by_method_name=_request_role_map(
            {
                "request",
                "header",
                "headers",
                "param",
                "params",
                "file",
                "content",
                "contentType",
                "accept",
                "characterEncoding",
                "queryParam",
                "queryParams",
                "cookie",
                "cookies",
                "flashAttr",
                "sessionAttr",
                "requestAttr",
                "contextPath",
                "servletPath",
                "secure",
                "locale",
                "with",
                "principal",
            },
            HttpRequestRole.BUILDER,
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.MOCKMVC,
        family_id="mockmvc.request_executor",
        receiver_prefixes=("org.springframework.test.web.servlet.MockMvc",),
        request_role_by_method_name=_request_role_map(
            {"perform"},
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name={"perform": "UNKNOWN"},
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.MOCKMVC,
        family_id="mockmvc.response_inspector",
        receiver_prefixes=("org.springframework.test.web.servlet.ResultActions",),
        response_role_by_method_name=_merge_response_role_maps(
            _response_role_map(
                {"andExpect", "andExpectAll", "andDo"},
                HttpResponseRole.INSPECTOR,
            ),
            _response_role_map({"andReturn"}, HttpResponseRole.EXTRACTOR),
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.MOCKMVC,
        family_id="mockmvc.matcher_root",
        receiver_prefixes=(
            "org.springframework.test.web.servlet.result.MockMvcResultMatchers",
        ),
        response_role_by_method_name=_merge_response_role_maps(
            _response_role_map(
                {"header", "cookie"},
                HttpResponseRole.HEADER_ASSERTION,
            ),
            _response_role_map(
                _MOCKMVC_MATCHER_ROOT_METHODS
                - normalize_method_names({"header", "cookie"}),
                HttpResponseRole.MATCHER,
            ),
        ),
        static_import_owners=(
            "org.springframework.test.web.servlet.result.MockMvcResultMatchers",
        ),
        static_import_methods=_MOCKMVC_MATCHER_ROOT_METHODS,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.MOCKMVC,
        family_id="mockmvc.status_assertion",
        receiver_prefixes=(
            "org.springframework.test.web.servlet.result.StatusResultMatchers",
        ),
        default_response_role=HttpResponseRole.STATUS_ASSERTION,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.MOCKMVC,
        family_id="mockmvc.header_assertion",
        receiver_prefixes=(
            "org.springframework.test.web.servlet.result.HeaderResultMatchers",
            "org.springframework.test.web.servlet.result.CookieResultMatchers",
        ),
        default_response_role=HttpResponseRole.HEADER_ASSERTION,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.MOCKMVC,
        family_id="mockmvc.body_assertion",
        receiver_prefixes=(
            "org.springframework.test.web.servlet.result.ContentResultMatchers",
            "org.springframework.test.web.servlet.result.JsonPathResultMatchers",
            "org.springframework.test.web.servlet.result.ModelResultMatchers",
            "org.springframework.test.web.servlet.result.ViewResultMatchers",
            "org.springframework.test.web.servlet.result.RequestResultMatchers",
            "org.springframework.test.web.servlet.result.XpathResultMatchers",
            "org.springframework.test.web.servlet.result.FlashAttributeResultMatchers",
            "org.springframework.test.web.servlet.result.HandlerResultMatchers",
        ),
        default_response_role=HttpResponseRole.BODY_ASSERTION,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        family_id="webtestclient.request_factory",
        receiver_prefixes=(
            "org.springframework.test.web.reactive.server.WebTestClient",
        ),
        request_role_by_method_name=_request_role_map(
            {
                "bindToServer",
                "bindToApplicationContext",
                "bindToController",
                "bindToRouterFunction",
                "bindToWebHandler",
            },
            HttpRequestRole.BUILDER,
        ),
        static_import_owners=(
            "org.springframework.test.web.reactive.server.WebTestClient",
        ),
        static_import_methods=normalize_method_names(
            {
                "bindToServer",
                "bindToApplicationContext",
                "bindToController",
                "bindToRouterFunction",
                "bindToWebHandler",
            }
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        family_id="webtestclient.request_builder",
        # The WebTestClient root is a configured client factory: its verb
        # methods take no URI, so helpers returning it carry base-URL config.
        receiver_prefixes=(
            "org.springframework.test.web.reactive.server.WebTestClient",
        ),
        request_role_by_method_name=_request_role_map(
            _WEBTESTCLIENT_REQUEST_BUILDER_METHODS,
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name=dict(_WEBTESTCLIENT_REQUEST_BUILDER_HTTP_METHODS),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        family_id="webtestclient.request_builder",
        receiver_prefixes=_WEBTESTCLIENT_REQUEST_SPEC_PREFIXES,
        request_role_by_method_name=_request_role_map(
            _WEBTESTCLIENT_REQUEST_BUILDER_METHODS,
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name=dict(_WEBTESTCLIENT_REQUEST_BUILDER_HTTP_METHODS),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        family_id="webtestclient.request_executor",
        receiver_prefixes=(
            "org.springframework.test.web.reactive.server.WebTestClient",
            *_WEBTESTCLIENT_REQUEST_SPEC_PREFIXES,
        ),
        request_role_by_method_name=_request_role_map(
            {"exchange", "exchangeSuccessfully", "exchangeToMono", "exchangeToFlux"},
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {
                "exchange": "UNKNOWN",
                "exchangeSuccessfully": "UNKNOWN",
                "exchangeToMono": "UNKNOWN",
                "exchangeToFlux": "UNKNOWN",
            }
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        family_id="webtestclient.response_inspector",
        receiver_prefixes=_WEBTESTCLIENT_RESPONSE_SPEC_PREFIXES,
        response_role_by_method_name=_response_role_map(
            {"expectAll"},
            HttpResponseRole.INSPECTOR,
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        family_id="webtestclient.matcher_root",
        receiver_prefixes=_WEBTESTCLIENT_RESPONSE_SPEC_PREFIXES,
        response_role_by_method_name=_response_role_map(
            {
                "expectStatus",
                "expectHeader",
                "expectCookie",
                "expectBody",
                "expectBodyList",
            },
            HttpResponseRole.MATCHER,
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        family_id="webtestclient.response_extractor",
        receiver_prefixes=(
            *_WEBTESTCLIENT_RESPONSE_SPEC_PREFIXES,
            *_WEBTESTCLIENT_BODY_SPEC_PREFIXES,
            *_WEBTESTCLIENT_EXCHANGE_RESULT_PREFIXES,
        ),
        response_role_by_method_name=_response_role_map(
            {
                "returnResult",
                "consumeWith",
                "getResponseBody",
                "getResponseBodyContent",
                "getStatus",
                "getResponseHeaders",
                "getResponseCookies",
            },
            HttpResponseRole.EXTRACTOR,
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        family_id="webtestclient.status_assertion",
        receiver_prefixes=(
            "org.springframework.test.web.reactive.server.StatusAssertions",
        ),
        default_response_role=HttpResponseRole.STATUS_ASSERTION,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        family_id="webtestclient.header_assertion",
        receiver_prefixes=_WEBTESTCLIENT_HEADER_ASSERTION_PREFIXES,
        default_response_role=HttpResponseRole.HEADER_ASSERTION,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        family_id="webtestclient.body_assertion",
        receiver_prefixes=_WEBTESTCLIENT_BODY_SPEC_PREFIXES,
        response_role_by_method_name=_merge_response_role_maps(
            _response_role_map({"jsonPath", "xpath"}, HttpResponseRole.MATCHER),
            _response_role_map(
                {"returnResult", "consumeWith"}, HttpResponseRole.EXTRACTOR
            ),
        ),
        default_response_role=HttpResponseRole.BODY_ASSERTION,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBCLIENT,
        family_id="webclient.request",
        receiver_prefixes=(
            "org.springframework.web.reactive.function.client.WebClient",
        ),
        request_role_by_method_name=_request_role_map(
            {
                "get",
                "head",
                "post",
                "put",
                "patch",
                "delete",
                "options",
                "method",
            },
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {
                "get": "GET",
                "head": "HEAD",
                "post": "POST",
                "put": "PUT",
                "patch": "PATCH",
                "delete": "DELETE",
                "options": "OPTIONS",
                "method": "UNKNOWN",
            }
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBCLIENT,
        family_id="webclient.request",
        receiver_prefixes=_WEBCLIENT_REQUEST_SPEC_PREFIXES,
        request_role_by_method_name=_merge_request_role_maps(
            _request_role_map(
                {"retrieve", "exchangeToMono", "exchangeToFlux", "exchange"},
                HttpRequestRole.EVENT,
            ),
            _request_role_map(
                {
                    "uri",
                    "header",
                    "headers",
                    "cookie",
                    "cookies",
                    "accept",
                    "acceptCharset",
                    "contentType",
                    "contentLength",
                    "body",
                    "bodyValue",
                    "syncBody",
                    "ifModifiedSince",
                    "ifNoneMatch",
                },
                HttpRequestRole.BUILDER,
            ),
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {
                "retrieve": "UNKNOWN",
                "exchangeToMono": "UNKNOWN",
                "exchangeToFlux": "UNKNOWN",
                "exchange": "UNKNOWN",
            }
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.WEBCLIENT,
        family_id="webclient.request",
        receiver_prefixes=(
            "org.springframework.web.reactive.function.client.ExchangeFunction",
        ),
        request_role_by_method_name=_request_role_map(
            {"exchange"},
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name=_normalize_http_method_map({"exchange": "UNKNOWN"}),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_CLIENT,
        family_id="rest-client.request",
        receiver_prefixes=("org.springframework.web.client.RestClient",),
        request_role_by_method_name=_request_role_map(
            {
                "get",
                "head",
                "post",
                "put",
                "patch",
                "delete",
                "options",
                "method",
            },
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {
                "get": "GET",
                "head": "HEAD",
                "post": "POST",
                "put": "PUT",
                "patch": "PATCH",
                "delete": "DELETE",
                "options": "OPTIONS",
                "method": "UNKNOWN",
            }
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_CLIENT,
        family_id="rest-client.request",
        receiver_prefixes=_REST_CLIENT_REQUEST_SPEC_PREFIXES,
        request_role_by_method_name=_merge_request_role_maps(
            _request_role_map(
                {"retrieve", "exchange", "exchangeForRequiredValue"},
                HttpRequestRole.EVENT,
            ),
            _request_role_map(
                {
                    "uri",
                    "header",
                    "headers",
                    "cookie",
                    "cookies",
                    "accept",
                    "acceptCharset",
                    "contentType",
                    "contentLength",
                    "body",
                    "ifModifiedSince",
                    "ifNoneMatch",
                    "attribute",
                    "attributes",
                    "apiVersion",
                    "httpRequest",
                },
                HttpRequestRole.BUILDER,
            ),
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {
                "retrieve": "UNKNOWN",
                "exchange": "UNKNOWN",
                "exchangeForRequiredValue": "UNKNOWN",
            }
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.JAVA_HTTPCLIENT,
        family_id="java-httpclient.request",
        receiver_prefixes=("java.net.http.HttpClient",),
        request_role_by_method_name=_request_role_map(
            {"send", "sendAsync"},
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {
                "send": "UNKNOWN",
                "sendAsync": "UNKNOWN",
            }
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.JAVA_HTTPCLIENT,
        family_id="java-httpclient.request",
        # Not a path receiver: the bare HttpRequest prefix also covers nested
        # BodyPublisher payload types whose helper String args are bodies, and
        # path-bearing construction is captured by the HttpRequest.Builder rule.
        receiver_prefixes=("java.net.http.HttpRequest",),
        request_role_by_method_name=_request_role_map(
            {"newBuilder"},
            HttpRequestRole.BUILDER,
        ),
        static_import_owners=("java.net.http.HttpRequest",),
        static_import_methods=normalize_method_names({"newBuilder"}),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.JAVA_HTTPCLIENT,
        family_id="java-httpclient.request",
        receiver_prefixes=_JAVA_HTTPCLIENT_BUILDER_PREFIXES,
        request_role_by_method_name=_request_role_map(
            {
                "uri",
                "header",
                "headers",
                "setHeader",
                "method",
                "get",
                "post",
                "put",
                "delete",
                "head",
            },
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {
                "get": "GET",
                "post": "POST",
                "put": "PUT",
                "delete": "DELETE",
                "head": "HEAD",
                "method": "UNKNOWN",
            }
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.MICRONAUT_CLIENT,
        family_id="micronaut-client.request",
        # The client is a configured factory (base URL via @Client), so helpers
        # returning it carry config rather than request-path state.
        receiver_prefixes=_MICRONAUT_CLIENT_PREFIXES,
        request_role_by_method_name=_merge_request_role_maps(
            _request_role_map(
                _MICRONAUT_CLIENT_EVENT_HTTP_METHODS.keys(),
                HttpRequestRole.EVENT,
            ),
            _request_role_map({"toBlocking"}, HttpRequestRole.BUILDER),
        ),
        http_method_by_method_name=dict(_MICRONAUT_CLIENT_EVENT_HTTP_METHODS),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.MICRONAUT_CLIENT,
        family_id="micronaut-client.request",
        receiver_prefixes=_MICRONAUT_REQUEST_PREFIXES,
        request_role_by_method_name=_merge_request_role_maps(
            _request_role_map(
                _MICRONAUT_REQUEST_FACTORY_HTTP_METHODS.keys(),
                HttpRequestRole.BUILDER,
            ),
            _request_role_map(
                _MICRONAUT_REQUEST_BUILDER_METHODS,
                HttpRequestRole.BUILDER,
            ),
        ),
        http_method_by_method_name=dict(_MICRONAUT_REQUEST_FACTORY_HTTP_METHODS),
        static_import_owners=("io.micronaut.http.HttpRequest",),
        static_import_methods=normalize_method_names(
            _MICRONAUT_REQUEST_FACTORY_HTTP_METHODS.keys()
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.OKHTTP,
        family_id="okhttp.request",
        receiver_prefixes=("okhttp3.Call",),
        request_role_by_method_name=_request_role_map(
            {"execute", "enqueue"},
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {
                "execute": "UNKNOWN",
                "enqueue": "UNKNOWN",
            }
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.OKHTTP,
        family_id="okhttp.request",
        receiver_prefixes=(
            "okhttp3.OkHttpClient",
            *_nested_receiver_prefixes("okhttp3.Call", ("Factory",)),
        ),
        request_role_by_method_name=_request_role_map(
            {"newCall"},
            HttpRequestRole.BUILDER,
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.OKHTTP,
        family_id="okhttp.request",
        receiver_prefixes=_OKHTTP_REQUEST_BUILDER_PREFIXES,
        request_role_by_method_name=_request_role_map(
            {
                "url",
                "addHeader",
                "header",
                "headers",
                "method",
                "get",
                "head",
                "post",
                "put",
                "patch",
                "delete",
            },
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {
                "get": "GET",
                "head": "HEAD",
                "post": "POST",
                "put": "PUT",
                "patch": "PATCH",
                "delete": "DELETE",
                "method": "UNKNOWN",
            }
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.OKHTTP,
        family_id="okhttp.request",
        # Scoped to okhttp3.Request so OkHttpClient/HttpUrl/Response.newBuilder()
        # are not classified as request builders. Request.newBuilder() copies the
        # source request's verb, so its presence must suppress the verb-less
        # Request.Builder GET default.
        receiver_prefixes=("okhttp3.Request",),
        request_role_by_method_name=_request_role_map(
            {"newBuilder"},
            HttpRequestRole.BUILDER,
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.KARATE,
        family_id="karate.request",
        # Karate 1.x ships com.intuit.karate.Http; 2.x moved to io.karatelabs.
        # patch/patchJson/contentType exist only on the 2.x surface.
        exact_receiver_types=("com.intuit.karate.Http", "io.karatelabs.http.Http"),
        request_role_by_method_name=_merge_request_role_maps(
            _request_role_map(
                {
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "method",
                    "methodJson",
                    "postJson",
                    "putJson",
                    "patchJson",
                },
                HttpRequestRole.EVENT,
            ),
            _request_role_map(
                # `to` is the static Http.to(url) chain-start factory.
                {"to", "url", "param", "path", "header", "contentType"},
                HttpRequestRole.BUILDER,
            ),
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {
                "get": "GET",
                "post": "POST",
                "put": "PUT",
                "patch": "PATCH",
                "delete": "DELETE",
                "method": "UNKNOWN",
                "methodJson": "UNKNOWN",
                "postJson": "POST",
                "putJson": "PUT",
                "patchJson": "PATCH",
            }
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.PACT,
        family_id="pact.request",
        receiver_prefixes=(
            "au.com.dius.pact.consumer.dsl.PactDslRequestWithoutPath",
            "au.com.dius.pact.consumer.dsl.PactDslRequestWithPath",
        ),
        request_role_by_method_name=_request_role_map(
            {
                "method",
                "path",
                "query",
                "encodedQuery",
                "headers",
                "body",
                "bodyWithSingleQuotes",
                "withBinaryData",
            },
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name=_normalize_http_method_map({"method": "UNKNOWN"}),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.CITRUS,
        family_id="citrus.request",
        receiver_prefixes=_CITRUS_ACTION_ROOT_PREFIXES,
        request_role_by_method_name=_merge_request_role_maps(
            _request_role_map({"request"}, HttpRequestRole.EVENT),
            _request_role_map({"send"}, HttpRequestRole.BUILDER),
        ),
        http_method_by_method_name=_normalize_http_method_map({"request": "UNKNOWN"}),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.CITRUS,
        family_id="citrus.request",
        receiver_prefixes=_CITRUS_REQUEST_EVENT_PREFIXES,
        request_role_by_method_name=_request_role_map(
            {
                "request",
                "get",
                "post",
                "put",
                "delete",
                "patch",
                "head",
                "options",
                "trace",
            },
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {
                "request": "UNKNOWN",
                "get": "GET",
                "post": "POST",
                "put": "PUT",
                "delete": "DELETE",
                "patch": "PATCH",
                "head": "HEAD",
                "options": "OPTIONS",
                "trace": "TRACE",
            }
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.CITRUS,
        family_id="citrus.request",
        receiver_prefixes=_CITRUS_REQUEST_BUILDER_PREFIXES,
        request_role_by_method_name=_request_role_map(
            {
                "send",
                "method",
                "path",
                "uri",
                "queryParam",
                "header",
                "headers",
                "body",
                "contentType",
                "accept",
                "cookie",
            },
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name=_normalize_http_method_map({"method": "UNKNOWN"}),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.FEIGN,
        family_id="feign.request",
        receiver_prefixes=("feign.Client", "feign.AsyncClient"),
        request_role_by_method_name=_request_role_map(
            {"execute"},
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name=_normalize_http_method_map({"execute": "UNKNOWN"}),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.FEIGN,
        family_id="feign.request",
        receiver_prefixes=("feign.RequestTemplate",),
        request_role_by_method_name=_request_role_map(
            {
                "method",
                "uri",
                "query",
                "queries",
                "header",
                "headers",
                "body",
                "target",
                "append",
                "insert",
                "request",
            },
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name=_normalize_http_method_map({"method": "UNKNOWN"}),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.APACHE_HTTPCLIENT,
        family_id="apache-httpclient.request",
        receiver_prefixes=(
            *_APACHE_HTTPCLIENT4_EVENT_PREFIXES,
            *_APACHE_HTTPCLIENT5_EVENT_PREFIXES,
        ),
        request_role_by_method_name=_request_role_map(
            {"execute", "executeHttp2", "executeOpen"},
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name=_normalize_http_method_map(
            {"execute": "UNKNOWN", "executeHttp2": "UNKNOWN", "executeOpen": "UNKNOWN"}
        ),
        http_verb_by_constructor_class_name=_APACHE_HTTPCLIENT_CONSTRUCTOR_VERBS,
        dynamic_http_method_constructor_class_names=(
            _APACHE_HTTPCLIENT_DYNAMIC_METHOD_CONSTRUCTORS
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.APACHE_HTTPCLIENT,
        family_id="apache-httpclient.request",
        receiver_prefixes=(
            *_APACHE_HTTPCLIENT4_REQUEST_PREFIXES,
            *_APACHE_HTTPCLIENT5_REQUEST_PREFIXES,
        ),
        request_role_by_method_name=_request_role_map(
            {
                "get",
                "post",
                "put",
                "delete",
                "patch",
                "head",
                "options",
                "trace",
                "create",
                "copy",
                "body",
                "bodyByteArray",
                "bodyFile",
                "bodyForm",
                "bodyStream",
                "bodyString",
                "setURI",
                "setUri",
                "setHeader",
                "addHeader",
                "setEntity",
                "setMethod",
                "setBody",
                "setPath",
            },
            HttpRequestRole.BUILDER,
        ),
        http_method_by_method_name={
            **_APACHE_HTTPCLIENT_REQUEST_VERB_METHODS,
            **_normalize_http_method_map(
                {
                    "create": "UNKNOWN",
                    "setMethod": "UNKNOWN",
                }
            ),
        },
        http_verb_by_constructor_class_name=_APACHE_HTTPCLIENT_CONSTRUCTOR_VERBS,
        dynamic_http_method_constructor_class_names=(
            _APACHE_HTTPCLIENT_DYNAMIC_METHOD_CONSTRUCTORS
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.JAX_RS,
        family_id="jaxrs-client.request",
        # Client is a factory: target(uri) takes the base URI and helper args
        # are config (certs, base URLs), so it is not a path receiver.
        receiver_prefixes=_JAXRS_CLIENT_PREFIXES,
        request_role_by_method_name=_request_role_map(
            _JAXRS_TARGET_BUILDER_METHODS,
            HttpRequestRole.BUILDER,
        ),
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.JAX_RS,
        family_id="jaxrs-client.request",
        receiver_prefixes=_JAXRS_WEB_TARGET_PREFIXES,
        request_role_by_method_name=_request_role_map(
            _JAXRS_TARGET_BUILDER_METHODS,
            HttpRequestRole.BUILDER,
        ),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.JAX_RS,
        family_id="jaxrs-client.request",
        receiver_prefixes=_JAXRS_INVOCATION_BUILDER_PREFIXES,
        request_role_by_method_name=_merge_request_role_maps(
            _request_role_map(
                _JAXRS_CLIENT_EVENT_HTTP_METHODS.keys(),
                HttpRequestRole.EVENT,
            ),
            _request_role_map(
                {
                    "header",
                    "headers",
                    "accept",
                    "acceptEncoding",
                    "acceptLanguage",
                    "cookie",
                    "cacheControl",
                    "property",
                },
                HttpRequestRole.BUILDER,
            ),
        ),
        http_method_by_method_name=dict(_JAXRS_CLIENT_EVENT_HTTP_METHODS),
        request_path_receiver=True,
    ),
    HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.JAX_RS,
        family_id="jaxrs-client.request",
        receiver_prefixes=_JAXRS_INVOKER_PREFIXES,
        request_role_by_method_name=_request_role_map(
            _JAXRS_CLIENT_EVENT_HTTP_METHODS.keys(),
            HttpRequestRole.EVENT,
        ),
        http_method_by_method_name=dict(_JAXRS_CLIENT_EVENT_HTTP_METHODS),
    ),
)


def resolve_http_owner_family(
    receiver_type: str,
    method_name: str,
    *,
    is_constructor_call: bool = False,
) -> HttpOwnerFamilyRule | None:
    """Resolve a framework owner family from receiver type and method name."""

    if not receiver_type:
        return None

    best_match: tuple[int, int, int] | None = None
    best_rule_index: int | None = None
    for rule_index, rule in enumerate(HTTP_OWNER_FAMILY_RULES):
        match_strength = _owner_family_match_strength(
            rule,
            receiver_type=receiver_type,
            method_name=method_name,
            is_constructor_call=is_constructor_call,
        )
        if match_strength is None:
            continue
        candidate = (match_strength[1], match_strength[0], -rule_index)
        if best_match is None or candidate > best_match:
            best_match = candidate
            best_rule_index = rule_index

    if best_rule_index is None:
        return None
    return HTTP_OWNER_FAMILY_RULES[best_rule_index]


def _exact_receiver_path_flags() -> dict[str, bool]:
    exact_flags: dict[str, bool] = {}
    for rule in HTTP_OWNER_FAMILY_RULES:
        for exact_receiver_type in rule.exact_receiver_types:
            exact_flags[exact_receiver_type] = (
                exact_flags.get(exact_receiver_type, False)
                or rule.request_path_receiver
            )
    return exact_flags


def _prefix_receiver_path_flags() -> dict[str, bool]:
    prefix_flags: dict[str, bool] = {}
    for rule in HTTP_OWNER_FAMILY_RULES:
        for receiver_prefix in rule.receiver_prefixes:
            prefix_flags[receiver_prefix] = (
                prefix_flags.get(receiver_prefix, False) or rule.request_path_receiver
            )
    return prefix_flags


# Registry-derived matching surface for path-receiver checks: each lowered
# exact type/prefix maps to whether any owning rule is a path receiver.
_EXACT_RECEIVER_PATH_FLAGS: Final[dict[str, bool]] = _exact_receiver_path_flags()
_PREFIX_RECEIVER_PATH_FLAGS: Final[dict[str, bool]] = _prefix_receiver_path_flags()


@lru_cache(maxsize=None)
def is_request_builder_receiver_type(receiver_type: str) -> bool:
    """True when a helper returning this type plausibly accumulated the request
    path/url, judged by the longest receiver match across the registry.

    Mixed builder/event receiver types (e.g. RestAssured ``RequestSpecification``)
    intentionally count as path receivers here. This predicate gates
    helper-return path recovery and is deliberately broader than
    ``resolve_http_owner_family``'s single-rule classification, which would
    select the non-path event rule and lose those recovered paths."""
    if not receiver_type:
        return False
    lowered_receiver_type = receiver_type.lower()
    if _EXACT_RECEIVER_PATH_FLAGS.get(lowered_receiver_type, False):
        return True

    # Longest-match arbitration mirrors _best_receiver_match_length: nested
    # response/spec types must resolve to their longer dedicated rules instead
    # of inheriting the shorter client-root prefix match.
    best_length = -1
    best_is_path_receiver = False
    for receiver_prefix, is_path_receiver in _PREFIX_RECEIVER_PATH_FLAGS.items():
        if not matches_receiver_prefix(lowered_receiver_type, receiver_prefix):
            continue
        if len(receiver_prefix) > best_length:
            best_length = len(receiver_prefix)
            best_is_path_receiver = is_path_receiver
        elif len(receiver_prefix) == best_length:
            best_is_path_receiver = best_is_path_receiver or is_path_receiver
    if lowered_receiver_type in _EXACT_RECEIVER_PATH_FLAGS and best_length < len(
        lowered_receiver_type
    ):
        best_is_path_receiver = _EXACT_RECEIVER_PATH_FLAGS[lowered_receiver_type]
    return best_is_path_receiver


__all__ = [
    "HTTP_OWNER_FAMILY_RULES",
    "HttpOwnerFamilyRule",
    "RECEIVERLESS_REQUEST_INFERENCE_RULES",
    "ReceiverlessRequestInferenceRule",
    "ReceiverlessRequestInferenceTarget",
    "classify_owner_family",
    "infer_receiverless_request_target",
    "is_request_builder_receiver_type",
    "matches_receiver_prefix",
    "normalize_method_names",
    "resolve_http_owner_family",
]
