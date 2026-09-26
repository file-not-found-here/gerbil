from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, NamedTuple, Protocol

from cldk.models.java import JCallable
from cldk.models.java.models import JCallSite, JImport

from gerbil.analysis.http.spring_declarative_client import (
    classify_spring_declarative_client_call_site,
)
from gerbil.analysis.http.framework_registry import (
    ReceiverlessRequestInferenceTarget,
    _REST_ASSURED_EXTRACTOR_METHODS,
    classify_owner_family,
    infer_receiverless_request_target,
    is_request_builder_receiver_type,
    matches_receiver_prefix,
    resolve_http_owner_family,
)
from gerbil.analysis.runtime import RuntimeEvent, TestRuntimeView
from gerbil.analysis.runtime.call_sites import (
    CallSiteGrouping,
    CallSiteNode,
    MethodRef,
    PathRecovery,
)
from gerbil.analysis.schema import (
    BuilderCorrelationSource,
    EndpointCandidate,
    HttpCallSite,
    HttpClassification,
    HttpDispatchFramework,
    HttpMockedCallSite,
    HttpMockedInteraction,
    HttpRequestInteraction,
    HttpRequestRole,
    HttpResponseRole,
    MockingContext,
    MockingContextKind,
)
from gerbil.analysis.shared.constants import (
    AUTH_HEADER_HINTS,
)
from gerbil.analysis.shared.receiver_resolution import (
    ResolvedCallee,
    ResolvedReceiver,
    RuntimeReceiverResolver,
)
from gerbil.analysis.shared.url_utils import (
    extract_query_param_names,
    is_local_hostname,
    safe_urlparse,
)
from urllib.parse import parse_qs

LOGGER = logging.getLogger(__name__)

_QUOTED_STRING_RE: re.Pattern[str] = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"')
_HTTP_METHOD_TOKENS: Final[frozenset[str]] = frozenset(
    {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE", "CONNECT"}
)
# Uppercase-only on purpose: verb constants are uppercase by Java convention
# (`HttpMethod.GET`), while a case-insensitive match would misread accessor
# calls such as `verbs.get(i)` as a fixed GET.
_QUALIFIED_HTTP_METHOD_LITERAL_RE: re.Pattern[str] = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_$.]*\s*\.\s*"
    r"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|TRACE|CONNECT)\b"
)
_HEADER_HINT_TOKENS: Final[frozenset[str]] = frozenset(
    {token.lower() for token in AUTH_HEADER_HINTS} | {"content-type"}
)
_AUTH_HINT_TOKEN_LOOKUP: Final[dict[str, str]] = {
    token.lower(): token for token in AUTH_HEADER_HINTS
}
_MEDIA_TYPE_TOP_LEVEL_TYPES: Final[frozenset[str]] = frozenset(
    {
        "application",
        "audio",
        "example",
        "font",
        "image",
        "message",
        "model",
        "multipart",
        "text",
        "video",
        "*",
    }
)
_RELATIVE_PATH_EXCLUDED_METHODS: Final[frozenset[str]] = frozenset(
    {
        "header",
        "headers",
        "addheader",
        "setheader",
        "contenttype",
        "accept",
        "acceptcharset",
        "cookie",
        "cookies",
    }
)
_RELATIVE_PATH_WHITELISTED_METHODS: Final[frozenset[str]] = frozenset(
    {
        "connect",
        "delete",
        "enqueue",
        "exchange",
        "exchangetoflux",
        "exchangetomono",
        "execute",
        "get",
        "getforentity",
        "getforobject",
        "head",
        "headforheaders",
        "method",
        "options",
        "optionsforallow",
        "patch",
        "patchforobject",
        "path",
        "perform",
        "post",
        "postforentity",
        "postforlocation",
        "postforobject",
        "put",
        "request",
        "retrieve",
        "send",
        "sendasync",
        "trace",
        "uri",
        "url",
    }
)
# Java identifier; also matches `$`-containing synthetic names CLDK can emit.
_JAVA_IDENTIFIER_PATTERN: Final[str] = r"[A-Za-z_$][\w$]*"
_PATH_TEMPLATE_VARIABLE_RE: re.Pattern[str] = re.compile(
    r"\{(\*?)([^{}:]+)(?::[^{}]+)?\}"
)
_EMBEDDED_QUERY_PARAM_RE: re.Pattern[str] = re.compile(
    r"\bqueryParam(?:s)?\s*\(\s*\"([^\"\\]*(?:\\.[^\"\\]*)*)\""
)
_STRING_LITERAL_EXPRESSION_RE: re.Pattern[str] = re.compile(
    r"^\s*\"([^\"\\]*(?:\\.[^\"\\]*)*)\"\s*$"
)
_MOCK_MULTIPART_FILE_NAME_RE: re.Pattern[str] = re.compile(
    rf"^\s*new\s+(?:{_JAVA_IDENTIFIER_PATTERN}\.)*MockMultipartFile\s*"
    r"\(\s*\"([^\"\\]*(?:\\.[^\"\\]*)*)\"\s*,"
)
# Reactive form/multipart bodies built inline: `fromFormData("name", ...)` /
# `fromMultipartData("name", ...)`. The field name is the first literal argument;
# the MultiValueMap overload has no literal here and is intentionally not matched.
_BODY_INSERTER_FORM_NAME_RE: re.Pattern[str] = re.compile(
    r"\bfrom(?:FormData|MultipartData)\s*\(\s*\"([^\"\\]*(?:\\.[^\"\\]*)*)\"\s*,"
)
# The MultiValueMap overload `fromFormData(formData)` / `fromMultipartData(map)`
# carries the field names on the map variable, populated elsewhere in the method.
# Capture that variable when it is the sole, simple identifier argument.
_BODY_INSERTER_VARIABLE_RE: re.Pattern[str] = re.compile(
    rf"\bfrom(?:FormData|MultipartData)\s*\(\s*({_JAVA_IDENTIFIER_PATTERN})\s*\)"
)
# Simple class names whose UPPER_SNAKE constants are HTTP header names following
# the SCREAMING_SNAKE -> kebab convention (Spring/JAX-RS/Apache/Guava `HttpHeaders`,
# Netty `HttpHeaderNames`). Any constant qualified by these is decoded mechanically.
_HEADER_CONSTANT_CLASS_SIMPLE_NAMES: Final[frozenset[str]] = frozenset(
    {"HttpHeaders", "HttpHeaderNames"}
)
# Public constants on the header classes above that are not header names, so they
# are excluded from mechanical decoding (e.g. Spring `HttpHeaders.EMPTY`).
_NON_HEADER_CONSTANT_NAMES: Final[frozenset[str]] = frozenset({"EMPTY"})
# Standard HTTP header constant names (SCREAMING_SNAKE). A bare/static-imported
# token cannot be tied back to its declaring class, so it is only decoded when it
# is a recognized standard header; arbitrary project constants are left alone.
_STANDARD_HTTP_HEADER_CONSTANTS: Final[frozenset[str]] = frozenset(
    {
        "ACCEPT",
        "ACCEPT_CHARSET",
        "ACCEPT_ENCODING",
        "ACCEPT_LANGUAGE",
        "ACCEPT_PATCH",
        "ACCEPT_RANGES",
        "ACCESS_CONTROL_ALLOW_CREDENTIALS",
        "ACCESS_CONTROL_ALLOW_HEADERS",
        "ACCESS_CONTROL_ALLOW_METHODS",
        "ACCESS_CONTROL_ALLOW_ORIGIN",
        "ACCESS_CONTROL_EXPOSE_HEADERS",
        "ACCESS_CONTROL_MAX_AGE",
        "ACCESS_CONTROL_REQUEST_HEADERS",
        "ACCESS_CONTROL_REQUEST_METHOD",
        "AGE",
        "ALLOW",
        "AUTHORIZATION",
        "CACHE_CONTROL",
        "CONNECTION",
        "CONTENT_DISPOSITION",
        "CONTENT_ENCODING",
        "CONTENT_LANGUAGE",
        "CONTENT_LENGTH",
        "CONTENT_LOCATION",
        "CONTENT_RANGE",
        "CONTENT_TYPE",
        "COOKIE",
        "DATE",
        "ETAG",
        "EXPECT",
        "EXPIRES",
        "FROM",
        "HOST",
        "IF_MATCH",
        "IF_MODIFIED_SINCE",
        "IF_NONE_MATCH",
        "IF_RANGE",
        "IF_UNMODIFIED_SINCE",
        "LAST_MODIFIED",
        "LINK",
        "LOCATION",
        "MAX_FORWARDS",
        "ORIGIN",
        "PRAGMA",
        "PROXY_AUTHENTICATE",
        "PROXY_AUTHORIZATION",
        "RANGE",
        "REFERER",
        "RETRY_AFTER",
        "SERVER",
        "SET_COOKIE",
        "TE",
        "TRAILER",
        "TRANSFER_ENCODING",
        "UPGRADE",
        "USER_AGENT",
        "VARY",
        "VIA",
        "WARNING",
        "WWW_AUTHENTICATE",
        "X_CSRF_TOKEN",
        "X_FORWARDED_FOR",
        "X_FORWARDED_HOST",
        "X_FORWARDED_PROTO",
        "X_FRAME_OPTIONS",
        "X_REQUESTED_WITH",
    }
)
# Optional `Class.` qualifier followed by a trailing SCREAMING_SNAKE constant. The
# `\b` keeps the constant anchored to a token boundary so a glued camelCase tail
# (e.g. `requestDATE`) is not mistaken for a bare standard header constant.
_HEADER_CONSTANT_REFERENCE_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?:(?P<qualifier>{_JAVA_IDENTIFIER_PATTERN})\s*\.\s*)?"
    r"\b(?P<constant>[A-Z][A-Z0-9_]*)\s*$"
)
# Header objects whose first constructor argument is the header name, e.g.
# RestAssured `new Header("X-Token", v)` / Apache `new BasicHeader("X-Token", v)`.
# The name is captured only when that first argument is itself a string literal.
_HEADER_OBJECT_NAME_RE: Final[re.Pattern[str]] = re.compile(
    rf"^\s*new\s+(?:{_JAVA_IDENTIFIER_PATTERN}\.)*(?:Basic)?Header\s*"
    r"\(\s*\"([^\"\\]*(?:\\.[^\"\\]*)*)\"\s*,"
)
_DIRECT_HEADER_METHOD_TO_NAME: Final[dict[str, str]] = {
    "accept": "accept",
    "acceptcharset": "accept-charset",
    "contentlength": "content-length",
    "contenttype": "content-type",
}
_BODY_PAYLOAD_METHODS: Final[frozenset[str]] = frozenset(
    {
        "body",
        "bodybytearray",
        "bodyfile",
        "bodyform",
        "bodystream",
        "bodystring",
        "bodyvalue",
        "bodywithsinglequotes",
        "content",
        "methodjson",
        "postjson",
        "putjson",
        "setbody",
        "setentity",
        "syncbody",
        "withbinarydata",
    }
)
# Verbs that conventionally carry a request body. DELETE can technically carry one but
# rarely does, so it is excluded to keep the verb-gated heuristics free of false
# positives (matches the existing post/put/patch dispatch branch).
_BODY_CAPABLE_HTTP_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH"})
# Empty RestTemplate request entities: `null` and the `HttpEntity.EMPTY` sentinel
# (possibly package-qualified) carry no body.
_EMPTY_HTTP_ENTITY_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:null|(?:[\w$]+\.)*HttpEntity\.EMPTY)\s*$"
)
# A no-argument `new HttpEntity<>()` / `new HttpEntity()` (any qualifier or type
# argument) constructs a body-less entity.
_EMPTY_HTTP_ENTITY_CONSTRUCTOR_RE: re.Pattern[str] = re.compile(
    r"^\s*new\s+(?:[\w$]+\.)*HttpEntity\s*(?:<[^>]*>)?\s*\(\s*\)\s*$"
)
# A `new HttpEntity<>(null)` / `new HttpEntity<>(null, headers)` has an explicit null
# body slot: across all three constructors (`(body)`, `(headers)`, `(body, headers)`) a
# null first argument carries no body. Anchoring on `null` followed by `,` or `)` keeps
# the match robust against nested commas in a trailing headers expression.
_NULL_BODY_HTTP_ENTITY_CONSTRUCTOR_RE: re.Pattern[str] = re.compile(
    r"^\s*new\s+(?:[\w$]+\.)*HttpEntity\s*(?:<[^>]*>)?\s*\(\s*null\s*[,)]"
)
# Java HttpClient's empty body publisher: `BodyPublishers.noBody()` (any qualifier,
# including the static-imported bare form) and `null`.
_NO_BODY_PUBLISHER_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:null|(?:[\w$]+\.)*noBody\s*\(\s*\))\s*$"
)
# A JAX-RS client entity argument is built by an `Entity.*(...)` static factory
# (`Entity.json(dto)`, `Entity.entity(...)`, possibly package-qualified). A bare
# variable or a response-type `Class` argument does not match.
_JAXRS_ENTITY_FACTORY_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:[\w$]+\.)*Entity\s*\.\s*[\w$]+\s*\("
)
# MultiValueMap mutators whose first argument is the field key/header name. Used to
# recover form/multipart field names from a reactive body inserter's map and header
# names from an HttpHeaders/MultiValueMap container (HttpHeaders is a MultiValueMap).
_MULTIVALUEMAP_KEY_METHODS: Final[frozenset[str]] = frozenset(
    {"add", "set", "put", "addifabsent", "addall"}
)
# HttpHeaders typed setters whose header name is encoded in the method name rather
# than an argument (e.g. `headers.setIfMatch(etag)`). `setBasicAuth`/`setBearerAuth`
# write the Authorization header despite their names.
_TYPED_HEADER_SETTER_TO_NAME: Final[dict[str, str]] = {
    "setaccept": "accept",
    "setacceptcharset": "accept-charset",
    "setacceptlanguage": "accept-language",
    "setbasicauth": "authorization",
    "setbearerauth": "authorization",
    "setcachecontrol": "cache-control",
    "setcontentdisposition": "content-disposition",
    "setcontentlength": "content-length",
    "setcontenttype": "content-type",
    "setetag": "etag",
    "setexpires": "expires",
    "sethost": "host",
    "setifmatch": "if-match",
    "setifmodifiedsince": "if-modified-since",
    "setifnonematch": "if-none-match",
    "setifunmodifiedsince": "if-unmodified-since",
    "setlastmodified": "last-modified",
    "setlocation": "location",
    "setorigin": "origin",
    "setrange": "range",
}
_HEADER_CONTAINER_MUTATOR_METHODS: Final[frozenset[str]] = (
    _MULTIVALUEMAP_KEY_METHODS | frozenset(_TYPED_HEADER_SETTER_TO_NAME)
)
_SINGLE_IDENTIFIER_ARGUMENT_RE: Final[re.Pattern[str]] = re.compile(
    rf"^\s*({_JAVA_IDENTIFIER_PATTERN})\s*$"
)
_MOCKITO_RECEIVER_PREFIXES: Final[tuple[str, ...]] = ("org.mockito.",)
_MOCKITO_STUBBING_METHODS: Final[frozenset[str]] = frozenset(
    {
        "when",
        "given",
        "doreturn",
        "dothrow",
        "doanswer",
        "donothing",
        "docallrealmethod",
        "willreturn",
        "willthrow",
        "willanswer",
        "willdonothing",
        "willcallrealmethod",
    }
)
_MOCKITO_VERIFICATION_METHODS: Final[frozenset[str]] = frozenset(
    {
        "verify",
        "then",
        "should",
    }
)
_MOCKITO_ARGUMENT_STUBBING_METHODS: Final[frozenset[str]] = frozenset(
    {
        "when",
        "given",
    }
)
_WEBTESTCLIENT_RESPONSE_SUBJECT_METHODS: Final[frozenset[str]] = frozenset(
    {
        "expectStatus",
        "expectHeader",
        "expectCookie",
        "expectBody",
        "expectBodyList",
    }
)
_WEBTESTCLIENT_RESPONSE_EXTRACTOR_METHODS: Final[frozenset[str]] = frozenset(
    {
        "returnResult",
        "consumeWith",
        "getResponseBody",
    }
)
_WEBTESTCLIENT_BODY_MATCHER_METHODS: Final[frozenset[str]] = frozenset(
    {
        "jsonPath",
        "xpath",
    }
)
_WEBTESTCLIENT_VOID_BODY_ARGUMENTS: Final[frozenset[str]] = frozenset(
    {
        "Void.class",
        "java.lang.Void.class",
        "Void.TYPE",
        "java.lang.Void.TYPE",
    }
)
_REST_ASSURED_RESPONSE_INSPECTOR_METHODS: Final[frozenset[str]] = frozenset({"then"})
_REST_ASSURED_STATUS_ASSERTION_METHODS: Final[frozenset[str]] = frozenset(
    {"statusCode", "statusLine"}
)
_REST_ASSURED_BODY_ASSERTION_METHODS: Final[frozenset[str]] = frozenset({"body"})
_REST_ASSURED_HEADER_ASSERTION_METHODS: Final[frozenset[str]] = frozenset(
    {
        "header",
        "headers",
        "contentType",
        "contentTypeCompatibleWith",
        "cookie",
        "cookies",
    }
)
_REST_ASSURED_EXTRACTOR_ROOT_METHODS: Final[frozenset[str]] = frozenset({"extract"})
_MOCKMVC_RESPONSE_INSPECTOR_METHODS: Final[frozenset[str]] = frozenset(
    {"andExpect", "andExpectAll", "andDo"}
)
_MOCKMVC_RESPONSE_EXTRACTOR_METHODS: Final[frozenset[str]] = frozenset({"andReturn"})
_MOCKMVC_ACTIVE_RESPONSE_ROLE_BY_SUBJECT_METHOD: Final[dict[str, HttpResponseRole]] = {
    "status": HttpResponseRole.STATUS_ASSERTION,
    "header": HttpResponseRole.HEADER_ASSERTION,
    "cookie": HttpResponseRole.HEADER_ASSERTION,
    "content": HttpResponseRole.BODY_ASSERTION,
    "jsonPath": HttpResponseRole.BODY_ASSERTION,
    "model": HttpResponseRole.BODY_ASSERTION,
    "view": HttpResponseRole.BODY_ASSERTION,
    "request": HttpResponseRole.BODY_ASSERTION,
    "xpath": HttpResponseRole.BODY_ASSERTION,
    "flash": HttpResponseRole.BODY_ASSERTION,
    "forwardedUrl": HttpResponseRole.BODY_ASSERTION,
    "redirectedUrl": HttpResponseRole.BODY_ASSERTION,
    "handler": HttpResponseRole.BODY_ASSERTION,
}
_MOCKMVC_ROOT_RESPONSE_ROLE_BY_SUBJECT_METHOD: Final[dict[str, HttpResponseRole]] = {
    method_name: (
        HttpResponseRole.HEADER_ASSERTION
        if active_role == HttpResponseRole.HEADER_ASSERTION
        else HttpResponseRole.MATCHER
    )
    for method_name, active_role in _MOCKMVC_ACTIVE_RESPONSE_ROLE_BY_SUBJECT_METHOD.items()
}

# Citrus HTTP action DSL (org.citrusframework.http.actions) is emitted fully
# receiverless by CLDK — the http().client(...).send()/.receive() fluent chain
# carries no receiver type, so it is recovered structurally from the chain shape
# rather than the owner-family registry. The verb/path live on the send chain and
# the response checks on a separate receive chain; method names like body/header
# are shared between the two and only chain context disambiguates their role.
_CITRUS_HTTP_ROOT_METHOD: Final[str] = "http"
_CITRUS_CLIENT_METHOD: Final[str] = "client"
_CITRUS_SEND_METHOD: Final[str] = "send"
_CITRUS_RECEIVE_METHOD: Final[str] = "receive"
_CITRUS_VERB_HTTP_METHODS: Final[dict[str, str]] = {
    "get": "GET",
    "post": "POST",
    "put": "PUT",
    "delete": "DELETE",
    "patch": "PATCH",
    "head": "HEAD",
    "options": "OPTIONS",
    "trace": "TRACE",
}
_REST_ASSURED_BASE_PREFIX_METHODS: Final[frozenset[str]] = frozenset(
    {"baseuri", "basepath"}
)
# basePath is defined as a path, so a bare segment (`basePath("api")`) is a
# valid prefix. baseUri is a URI whose bare token is usually a host
# (`baseUri("localhost")`), so it is excluded — turning a hostname into a path
# segment would be wrong.
_REST_ASSURED_SINGLE_SEGMENT_PREFIX_METHODS: Final[frozenset[str]] = frozenset(
    {"basepath"}
)
# Methods whose argument the framework API contract fixes as a path segment, so
# a bare token (`path("users")`) keeps its segment: JAX-RS WebTarget.path
# appends a segment, karate Http.path accumulates segments into the builder's
# paths, and citrus HttpMessage.path sets the request-path header verbatim.
# Extraction without a known owner family keeps only the basePath allowance.
_SINGLE_SEGMENT_PATH_METHODS_BY_OWNER_FAMILY: Final[dict[str, frozenset[str]]] = {
    "rest-assured.request_builder": _REST_ASSURED_SINGLE_SEGMENT_PREFIX_METHODS,
    "jaxrs-client.request": frozenset({"path"}),
    "karate.request": frozenset({"path"}),
    "citrus.request": frozenset({"path"}),
}
_REST_ASSURED_QUALIFIED_NAME: Final[str] = "io.restassured.RestAssured"
# Static config fields seed every default request spec
# (RestAssured.createTestSpecification passes baseURI/basePath to
# RequestSpecificationImpl), so a field assignment configures all of a test
# class's requests unless a chain-level setter replaces it.
_REST_ASSURED_STATIC_BASE_FIELD_KEYS: Final[dict[str, str]] = {
    "baseURI": "baseuri",
    "basePath": "basepath",
}
_REST_ASSURED_STATIC_BASE_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\bio\.restassured\.|(?<![.\w]))RestAssured\s*\.\s*"
    r"(baseURI|basePath)\s*=(?!=)\s*([^;]*);"
)
_BARE_STATIC_BASE_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(baseURI|basePath)\s*=(?!=)\s*([^;]*);"
)
# Keeps string literals (an extracted RHS is one) while dropping comments, so
# commented-out config never registers and `//` inside a URL literal survives.
_JAVA_COMMENT_OR_STRING_RE: Final[re.Pattern[str]] = re.compile(
    r'("(?:\\.|[^"\\])*")|/\*.*?\*/|//[^\n]*', re.DOTALL
)
_CITRUS_PATH_BUILDER_METHODS: Final[frozenset[str]] = frozenset({"path", "uri"})
_CITRUS_QUERY_BUILDER_METHODS: Final[frozenset[str]] = frozenset({"queryParam"})
_CITRUS_HEADER_BUILDER_METHODS: Final[frozenset[str]] = frozenset(
    {"header", "headers", "contentType", "accept"}
)
_CITRUS_BODY_BUILDER_METHODS: Final[frozenset[str]] = frozenset({"body", "payload"})
_CITRUS_RESPONSE_STATUS_METHODS: Final[frozenset[str]] = frozenset({"response"})
_CITRUS_RESPONSE_BODY_METHODS: Final[frozenset[str]] = frozenset({"body", "payload"})
_CITRUS_RESPONSE_HEADER_METHODS: Final[frozenset[str]] = frozenset(
    {"header", "headers", "contentType"}
)
_PATH_ARGUMENT_POSITIONS_BY_OWNER_FAMILY: Final[
    dict[str, dict[str, tuple[int, ...]]]
] = {
    "rest-assured.request_factory": {
        "get": (0,),
        "post": (0,),
        "put": (0,),
        "delete": (0,),
        "patch": (0,),
        "head": (0,),
        "options": (0,),
        "request": (1,),
    },
    "rest-assured.request_event": {
        "get": (0,),
        "post": (0,),
        "put": (0,),
        "delete": (0,),
        "patch": (0,),
        "head": (0,),
        "options": (0,),
        "request": (1,),
    },
    "rest-assured.request_builder": {
        "baseuri": (0,),
        "basepath": (0,),
    },
    "test-rest-template.request": {
        "getforentity": (0,),
        "getforobject": (0,),
        "headforheaders": (0,),
        "postforlocation": (0,),
        "postforentity": (0,),
        "postforobject": (0,),
        "put": (0,),
        "delete": (0,),
        "patchforobject": (0,),
        "optionsforallow": (0,),
        "exchange": (0,),
        "execute": (0,),
    },
    "rest-template.request": {
        "getforentity": (0,),
        "getforobject": (0,),
        "headforheaders": (0,),
        "postforlocation": (0,),
        "postforentity": (0,),
        "postforobject": (0,),
        "put": (0,),
        "delete": (0,),
        "patchforobject": (0,),
        "optionsforallow": (0,),
        "exchange": (0,),
        "execute": (0,),
        "get": (0,),
        "post": (0,),
        "patch": (0,),
        "head": (0,),
        "options": (0,),
        "method": (1,),
    },
    "mockmvc.request_factory": {
        "get": (0,),
        "post": (0,),
        "put": (0,),
        "delete": (0,),
        "patch": (0,),
        "head": (0,),
        "options": (0,),
        "multipart": (0, 1),
        "request": (1,),
    },
    "webtestclient.request_builder": {"uri": (0,)},
    "webclient.request": {"uri": (0,)},
    "rest-client.request": {"uri": (0,)},
    "java-httpclient.request": {"newbuilder": (0,), "uri": (0,)},
    # exchange/retrieve cover the String-URI overloads; the HttpRequest-object
    # overloads put a non-literal request there, which extracts nothing.
    "micronaut-client.request": {
        "get": (0,),
        "post": (0,),
        "put": (0,),
        "patch": (0,),
        "delete": (0,),
        "head": (0,),
        "options": (0,),
        "create": (1,),
        "uri": (0,),
        "exchange": (0,),
        "retrieve": (0,),
        "datastream": (0,),
        "exchangestream": (0,),
        "jsonstream": (0,),
    },
    "okhttp.request": {"url": (0,)},
    "karate.request": {
        "to": (0,),
        "url": (0,),
        "path": (0,),
    },
    "pact.request": {"path": (0,)},
    "citrus.request": {
        "get": (0,),
        "post": (0,),
        "put": (0,),
        "delete": (0,),
        "patch": (0,),
        "head": (0,),
        "options": (0,),
        "trace": (0,),
        "path": (0,),
        "uri": (0,),
    },
    "feign.request": {
        "uri": (0,),
        "target": (0,),
        "append": (0,),
        "insert": (1,),
    },
    "apache-httpclient.request": {
        "get": (0,),
        "post": (0,),
        "put": (0,),
        "delete": (0,),
        "patch": (0,),
        "head": (0,),
        "options": (0,),
        "trace": (0,),
        "setpath": (0,),
        "seturi": (0,),
    },
    "jaxrs-client.request": {"target": (0,), "path": (0,)},
}
# Methods whose path argument APPENDS to the receiver's accumulated path.
# Verified against framework semantics: JAX-RS WebTarget.path creates a new
# target "by appending path to the URI of the current target instance"
# (WebTarget javadoc), and karate Http.path accumulates segments into
# HttpRequestBuilder.paths, joined into the URI at request time. Setter-style
# path methods (uri, url, basePath) replace and must stay unlisted.
_APPENDING_PATH_METHODS_BY_OWNER_FAMILY: Final[dict[str, frozenset[str]]] = {
    "jaxrs-client.request": frozenset({"path"}),
    "karate.request": frozenset({"path"}),
}


def _is_appending_path_method(owner_family: str, method_name: str) -> bool:
    return method_name.lower() in _APPENDING_PATH_METHODS_BY_OWNER_FAMILY.get(
        owner_family, frozenset()
    )


_PATH_ARGUMENT_POSITIONS_BY_CONSTRUCTOR_FRAMEWORK: Final[
    dict[HttpDispatchFramework, tuple[int, ...]]
] = {
    HttpDispatchFramework.APACHE_HTTPCLIENT: (0,),
}
_APACHE_DYNAMIC_METHOD_CONSTRUCTOR_CLASS_NAMES: Final[frozenset[str]] = frozenset(
    {"SimpleHttpRequest"}
)
_HTTP_METHOD_ARGUMENT_POSITIONS_BY_OWNER_FAMILY: Final[
    dict[str, dict[str, tuple[int, ...]]]
] = {
    "rest-assured.request_factory": {"request": (0,)},
    "rest-assured.request_event": {"request": (0,)},
    "test-rest-template.request": {"exchange": (1,), "execute": (1,)},
    "rest-template.request": {"exchange": (1,), "execute": (1,), "method": (0,)},
    "mockmvc.request_factory": {"multipart": (0,), "request": (0,)},
    "webtestclient.request_builder": {"method": (0,)},
    "webclient.request": {"method": (0,)},
    "rest-client.request": {"method": (0,)},
    "java-httpclient.request": {"method": (0,)},
    "micronaut-client.request": {"create": (0,)},
    "okhttp.request": {"method": (0,)},
    "karate.request": {"method": (0,), "methodjson": (0,)},
    "pact.request": {"method": (0,)},
    "citrus.request": {"method": (0,)},
    "feign.request": {"method": (0,)},
    "apache-httpclient.request": {"create": (0,), "setmethod": (0,)},
    "jaxrs-client.request": {"method": (0,)},
}
_HTTP_METHOD_TYPE_ARGUMENT_POSITIONS_BY_OWNER_FAMILY: Final[
    dict[str, dict[str, tuple[int, ...]]]
] = {
    "apache-httpclient.request": {"execute": (0, 1), "executeopen": (0, 1)},
}


@dataclass(frozen=True)
class _ResolvedHttpTarget:
    framework: HttpDispatchFramework
    receiver_type: str
    owner_family: str | None
    request_role: HttpRequestRole | None
    response_role: HttpResponseRole | None
    framework_http_method: str | None = None
    verb_by_class_name: dict[str, str] | None = None


class _CorrelationTarget(Protocol):
    http_method: str
    path: str
    path_truncated: bool
    framework: HttpDispatchFramework
    headers: list[str]
    header_names: list[str]
    query_param_names: list[str]
    path_param_names: list[str]
    form_param_names: list[str]
    rest_assured_ambiguous_param_names: list[str]
    has_body_payload: bool
    auth_hints: list[str]
    correlated_builder_sources: list[BuilderCorrelationSource]


def _is_rest_assured_base_prefix_builder(
    method_name: str,
    builder: _CorrelationTarget,
) -> bool:
    return (
        builder.framework == HttpDispatchFramework.REST_ASSURED
        and method_name.lower() in _REST_ASSURED_BASE_PREFIX_METHODS
    )


# Property names _merge_builder_into_event can contribute to an event, in
# emission order; the statistics layer keys its builder-usage table off this
# tuple, so keep it in sync with the contributed.append(...) calls below.
BUILDER_CONTRIBUTED_PROPERTY_NAMES: tuple[str, ...] = (
    "http_method",
    "path",
    "headers",
    "header_names",
    "query_param_names",
    "path_param_names",
    "form_param_names",
    "has_body_payload",
    "auth_hints",
)


def _merge_builder_into_event(
    event: _CorrelationTarget,
    builder: _CorrelationTarget,
    *,
    builder_method_name: str,
    builder_start_line: int,
) -> None:
    contributed: list[str] = []
    if event.http_method == "UNKNOWN" and builder.http_method != "UNKNOWN":
        event.http_method = builder.http_method
        contributed.append("http_method")

    # baseUri/basePath arguments are prefixes, not request paths; they are
    # composed onto the event path after all builders merge.
    if (
        not event.path
        and builder.path
        and not _is_rest_assured_base_prefix_builder(builder_method_name, builder)
    ):
        event.path = builder.path
        event.path_truncated = builder.path_truncated
        contributed.append("path")

    new_headers = [h for h in builder.headers if h not in event.headers]
    if new_headers:
        event.headers.extend(new_headers)
        contributed.append("headers")

    new_header_names = [h for h in builder.header_names if h not in event.header_names]
    if new_header_names:
        event.header_names.extend(new_header_names)
        contributed.append("header_names")

    new_query_param_names = [
        p for p in builder.query_param_names if p not in event.query_param_names
    ]
    if new_query_param_names:
        event.query_param_names.extend(new_query_param_names)
        contributed.append("query_param_names")

    new_path_param_names = [
        p for p in builder.path_param_names if p not in event.path_param_names
    ]
    if new_path_param_names:
        event.path_param_names.extend(new_path_param_names)
        contributed.append("path_param_names")

    new_form_param_names = [
        p for p in builder.form_param_names if p not in event.form_param_names
    ]
    if new_form_param_names:
        event.form_param_names.extend(new_form_param_names)
        contributed.append("form_param_names")

    new_ambiguous_param_names = [
        p
        for p in builder.rest_assured_ambiguous_param_names
        if p not in event.rest_assured_ambiguous_param_names
    ]
    if new_ambiguous_param_names:
        event.rest_assured_ambiguous_param_names.extend(new_ambiguous_param_names)
        # Record a provisional property that normalization will rewrite to the
        # final bucket once the HTTP verb is finalized.
        contributed.append(_REST_ASSURED_AMBIGUOUS_PARAM_PROPERTY)

    if builder.has_body_payload and not event.has_body_payload:
        event.has_body_payload = True
        contributed.append("has_body_payload")

    new_auth_hints = [h for h in builder.auth_hints if h not in event.auth_hints]
    if new_auth_hints:
        event.auth_hints.extend(new_auth_hints)
        contributed.append("auth_hints")

    if contributed:
        event.correlated_builder_sources.append(
            BuilderCorrelationSource(
                method_name=builder_method_name,
                start_line=builder_start_line,
                framework=builder.framework,
                contributed_properties=contributed,
            )
        )


@dataclass(frozen=True)
class _BasePrefixEvidence:
    path: str
    truncated: bool
    method_name: str
    start_line: int


@dataclass(frozen=True)
class _QueuedBuilderGroup:
    builders: list[tuple[CallSiteNode, HttpClassification]]
    target_names: frozenset[str]


# Builders for the RestTemplate/TestRestTemplate pair are interchangeable: a
# RequestEntity built for one can be exchanged by the other.
_BUILDER_FRAMEWORK_COMPATIBILITY_GROUPS: Final[
    frozenset[frozenset[HttpDispatchFramework]]
] = frozenset(
    {
        frozenset(
            {
                HttpDispatchFramework.REST_TEMPLATE,
                HttpDispatchFramework.TEST_REST_TEMPLATE,
            }
        )
    }
)


def _builder_framework_matches_event_framework(
    builder_framework: HttpDispatchFramework,
    event_framework: HttpDispatchFramework,
) -> bool:
    if builder_framework == event_framework:
        return True
    return any(
        {builder_framework, event_framework}.issubset(group)
        for group in _BUILDER_FRAMEWORK_COMPATIBILITY_GROUPS
    )


def _record_base_prefix(
    base_prefixes: dict[str, _BasePrefixEvidence],
    builder_method_name: str,
    builder_start_line: int,
    builder: _CorrelationTarget,
    *,
    overwrite: bool,
) -> None:
    if not _is_rest_assured_base_prefix_builder(builder_method_name, builder):
        return
    if not builder.path:
        return
    key = builder_method_name.lower()
    # Chain setters replace their previous value (RequestSpecificationImpl
    # setter semantics), so same-chain recording overwrites; drained
    # cross-chain builders are weaker evidence and never clobber chain values.
    if not overwrite and key in base_prefixes:
        return
    base_prefixes[key] = _BasePrefixEvidence(
        path=builder.path,
        truncated=builder.path_truncated,
        method_name=builder_method_name,
        start_line=builder_start_line,
    )


def _join_url_path_parts(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    return f"{left.rstrip('/')}/{right.lstrip('/')}"


def _compose_appending_chain_path(
    ordered_builders: list[tuple[CallSiteNode, HttpClassification]],
) -> tuple[str, bool, list[BuilderCorrelationSource]] | None:
    """Compose a chain's builder paths in chain order, or None to fall back.

    Sound only when every path-bearing member after the first is a registered
    appending method; the first member sets the base (chain-start factory or
    first appended segment). A truncated non-final member would fabricate
    adjacency across the statically unknown appended value, so only the final
    member may be truncated. REST Assured baseUri/basePath setters compose
    separately and are excluded here.
    """
    path_bearing = [
        (node, builder)
        for node, builder in ordered_builders
        if builder.path
        and not _is_rest_assured_base_prefix_builder(
            node.call_site.method_name or "", builder
        )
    ]
    if len(path_bearing) < 2:
        return None
    for node, builder in path_bearing[1:]:
        if not _is_appending_path_method(
            builder.owner_family or "", node.call_site.method_name or ""
        ):
            return None
    if any(builder.path_truncated for _, builder in path_bearing[:-1]):
        return None
    composed = ""
    for _, builder in path_bearing:
        composed = _join_url_path_parts(composed, builder.path)
    sources = [
        BuilderCorrelationSource(
            method_name=node.call_site.method_name or "",
            start_line=int(node.call_site.start_line),
            framework=builder.framework,
            contributed_properties=["path"],
        )
        for node, builder in path_bearing
    ]
    return composed, path_bearing[-1][1].path_truncated, sources


def _apply_composed_chain_path_to_event(
    event: HttpClassification,
    ordered_builders: list[tuple[CallSiteNode, HttpClassification]],
) -> None:
    if event.path:
        return
    composed = _compose_appending_chain_path(ordered_builders)
    if composed is None:
        return
    path, truncated, sources = composed
    event.path = path
    event.path_truncated = truncated
    event.correlated_builder_sources.extend(sources)


def _compose_base_prefixes_into_event(
    event: _CorrelationTarget,
    base_prefixes: dict[str, _BasePrefixEvidence],
) -> None:
    """Compose REST Assured baseUri/basePath setters onto the event path.

    Mirrors RequestSpecificationImpl.getTargetPath: a fully qualified request
    path bypasses the base components; otherwise the target is
    baseUri + basePath + path joined without double slashes. A truncated
    component followed by another non-empty component would fabricate
    adjacency across the statically unknown appended value, so composition
    only proceeds when truncation is confined to the final component.
    """
    if event.framework != HttpDispatchFramework.REST_ASSURED:
        return
    if event.path.startswith(("http://", "https://")):
        return

    components: list[tuple[str, bool, _BasePrefixEvidence | None]] = [
        (evidence.path, evidence.truncated, evidence)
        for key in ("baseuri", "basepath")
        if (evidence := base_prefixes.get(key)) is not None
    ]
    if not components:
        return
    if event.path:
        components.append((event.path, event.path_truncated, None))

    if any(truncated for _, truncated, _ in components[:-1]):
        return

    composed = ""
    for part, _, _ in components:
        composed = _join_url_path_parts(composed, part)
    event.path = composed
    event.path_truncated = components[-1][1]

    for _, _, evidence in components:
        if evidence is None:
            continue
        event.correlated_builder_sources.append(
            BuilderCorrelationSource(
                method_name=evidence.method_name,
                start_line=evidence.start_line,
                framework=HttpDispatchFramework.REST_ASSURED,
                contributed_properties=["path"],
            )
        )


_REST_ASSURED_AMBIGUOUS_PARAM_PROPERTY: str = "rest_assured_ambiguous_param_names"


def _normalize_rest_assured_ambiguous_params(
    classification: HttpClassification,
) -> None:
    # Resolve REST Assured's overloaded `param`/`params` once the final verb is
    # known: POST sends them as form data, any other KNOWN verb as query. When
    # the verb stays UNKNOWN (e.g. `request(methodVar, ...)` with a dynamic
    # method), the bucket is unknowable statically, so the names are dropped
    # rather than guessed as query — guessing query would be a false positive
    # whenever the runtime verb is POST.
    if classification.framework != HttpDispatchFramework.REST_ASSURED:
        return
    if not classification.rest_assured_ambiguous_param_names:
        return

    target_names: list[str] | None
    final_property: str | None
    if classification.http_method == "POST":
        final_property = "form_param_names"
        target_names = classification.form_param_names
    elif classification.http_method == "UNKNOWN":
        final_property = None
        target_names = None
    else:
        final_property = "query_param_names"
        target_names = classification.query_param_names

    if target_names is not None:
        for name in classification.rest_assured_ambiguous_param_names:
            if name not in target_names:
                target_names.append(name)
    classification.rest_assured_ambiguous_param_names.clear()

    # Rewrite provisional builder-correlation sources to the resolved bucket, or
    # drop the provisional contribution entirely for an UNKNOWN verb, so the
    # transient ambiguous property never leaks into output or construction stats.
    retained_sources: list[BuilderCorrelationSource] = []
    for source in classification.correlated_builder_sources:
        if _REST_ASSURED_AMBIGUOUS_PARAM_PROPERTY not in source.contributed_properties:
            retained_sources.append(source)
            continue
        if final_property is None:
            rewritten_properties = [
                prop
                for prop in source.contributed_properties
                if prop != _REST_ASSURED_AMBIGUOUS_PARAM_PROPERTY
            ]
        else:
            rewritten_properties = [
                (
                    final_property
                    if prop == _REST_ASSURED_AMBIGUOUS_PARAM_PROPERTY
                    else prop
                )
                for prop in source.contributed_properties
            ]
        if rewritten_properties:
            source.contributed_properties = rewritten_properties
            retained_sources.append(source)
    classification.correlated_builder_sources[:] = retained_sources


def _strip_java_comments(code: str) -> str:
    return _JAVA_COMMENT_OR_STRING_RE.sub(lambda match: match.group(1) or "", code)


def _bare_assignment_is_statement_level(code: str, match_start: int) -> bool:
    """False for type-led declarations (``String basePath = ...``), qualified
    writes (``spec.basePath = ...``), and ``name = value`` argument lists."""
    index = match_start - 1
    while index >= 0 and code[index] in " \t\r\n":
        index -= 1
    if index < 0:
        return True
    preceding = code[index]
    if preceding in ".,(":
        return False
    return not (preceding.isalnum() or preceding in "_>]")


def _statically_imports_rest_assured_field(
    imports: list[JImport], field_name: str
) -> bool:
    for import_entry in imports:
        if not import_entry.is_static:
            continue
        path = (import_entry.path or "").strip()
        if import_entry.is_wildcard:
            if path.removesuffix("*").rstrip(".") == _REST_ASSURED_QUALIFIED_NAME:
                return True
        elif path == f"{_REST_ASSURED_QUALIFIED_NAME}.{field_name}":
            return True
    return False


def _discover_rest_assured_static_base_prefixes(
    test_class_name: str,
    receiver_resolver: RuntimeReceiverResolver,
) -> dict[str, _BasePrefixEvidence]:
    """Discover ``RestAssured.baseURI``/``basePath`` assignments in the test
    class lineage that resolve to constants.

    Field assignments are statements, not call sites, so they are recovered
    from callable code text across the class and its superclasses (setup
    fixtures, constructors, helpers). Each field contributes independently and
    only when every lineage assignment to it resolves to ONE value: a dynamic
    or conflicting assignment excludes that field rather than guessing.
    RequestSpecBuilder specs shared through fields are out of scope.
    """
    values_by_key: dict[str, set[str]] = {}
    poisoned_keys: set[str] = set()

    def _record(field_name: str, rhs_expression: str, class_name: str) -> None:
        key = _REST_ASSURED_STATIC_BASE_FIELD_KEYS[field_name]
        resolved = receiver_resolver.resolve_constant_expression(
            class_name, rhs_expression.strip()
        )
        if resolved is None:
            poisoned_keys.add(key)
        else:
            values_by_key.setdefault(key, set()).add(resolved)

    lineage = [test_class_name, *receiver_resolver.superclass_chain(test_class_name)]
    for class_name in lineage:
        callables = receiver_resolver.methods_in_class(class_name)
        if not callables:
            continue
        imports = receiver_resolver.class_imports(class_name)
        # A simple-name RestAssured receiver is only credible alongside real
        # io.restassured imports; a fully qualified one needs no import.
        simple_name_allowed = any(
            (import_entry.path or "").startswith("io.restassured")
            for import_entry in imports
        )
        bare_field_names = {
            field_name
            for field_name in _REST_ASSURED_STATIC_BASE_FIELD_KEYS
            if _statically_imports_rest_assured_field(imports, field_name)
        }
        for callable_details in callables.values():
            raw_code = str(callable_details.code or "")
            if not raw_code or ("RestAssured" not in raw_code and not bare_field_names):
                continue
            code = _strip_java_comments(raw_code)
            for match in _REST_ASSURED_STATIC_BASE_ASSIGNMENT_RE.finditer(code):
                if not simple_name_allowed and not match.group(0).startswith(
                    "io.restassured."
                ):
                    continue
                _record(match.group(1), match.group(2), class_name)
            if not bare_field_names:
                continue
            for match in _BARE_STATIC_BASE_ASSIGNMENT_RE.finditer(code):
                if match.group(1) not in bare_field_names:
                    continue
                if not _bare_assignment_is_statement_level(code, match.start()):
                    continue
                _record(match.group(1), match.group(2), class_name)

    prefixes: dict[str, _BasePrefixEvidence] = {}
    for field_name, key in _REST_ASSURED_STATIC_BASE_FIELD_KEYS.items():
        values = values_by_key.get(key, set())
        if key in poisoned_keys or len(values) != 1:
            continue
        # Re-extracting the resolved value applies the same vetting a
        # chain-level baseUri/basePath argument gets (host-only locals skipped,
        # bare hosts rejected for baseUri, bare segments normalized for
        # basePath).
        extracted = _extract_path([f'"{next(iter(values))}"'], field_name)
        if not extracted.path:
            continue
        prefixes[key] = _BasePrefixEvidence(
            path=extracted.path,
            truncated=False,
            method_name=f"RestAssured.{field_name}",
            start_line=0,
        )
    return prefixes


def _resolved_role_label(
    request_role: HttpRequestRole | None,
    response_role: HttpResponseRole | None,
) -> str:
    if request_role is not None:
        return request_role.value
    if response_role is not None:
        return response_role.value
    return "unknown"


def _resolve_owner_family_target(
    *,
    receiver_type: str,
    method_name: str,
    is_constructor_call: bool,
) -> _ResolvedHttpTarget | None:
    owner_family_rule = resolve_http_owner_family(
        receiver_type,
        method_name,
        is_constructor_call=is_constructor_call,
    )
    if owner_family_rule is None:
        return None

    request_role, response_role, http_method = classify_owner_family(
        owner_family_rule,
        receiver_type=receiver_type,
        method_name=method_name,
        is_constructor_call=is_constructor_call,
    )
    if request_role is None and response_role is None:
        return None

    framework_http_method = http_method if request_role is not None else "UNKNOWN"
    return _ResolvedHttpTarget(
        framework=owner_family_rule.framework,
        receiver_type=receiver_type,
        owner_family=owner_family_rule.family_id,
        request_role=request_role,
        response_role=response_role,
        framework_http_method=framework_http_method,
        verb_by_class_name=(
            owner_family_rule.http_verb_by_constructor_class_name or None
        ),
    )


def _looks_like_media_type_literal(candidate: str) -> bool:
    normalized_candidate: str = candidate.strip().lower()
    if not normalized_candidate:
        return False

    media_type_part: str = normalized_candidate.split(";", 1)[0].strip()
    if media_type_part.count("/") != 1:
        return False

    top_level_type, subtype = media_type_part.split("/", 1)
    if top_level_type not in _MEDIA_TYPE_TOP_LEVEL_TYPES:
        return False
    if not subtype:
        return False
    if "/" in subtype:
        return False
    return True


def _is_relative_path_literal(
    candidate: str, *, require_path_separator: bool = True
) -> bool:
    normalized_candidate: str = candidate.strip()
    if not normalized_candidate:
        return False

    normalized_candidate_lower: str = normalized_candidate.lower()
    if normalized_candidate.startswith("/"):
        return False
    if normalized_candidate_lower.startswith(("http://", "https://")):
        return False
    if any(character.isspace() for character in normalized_candidate):
        return False
    if normalized_candidate.startswith(("{", "[")):
        return False
    if require_path_separator and "/" not in normalized_candidate:
        return False

    parsed_candidate = safe_urlparse(normalized_candidate)
    if parsed_candidate is None:
        return False
    if parsed_candidate.scheme or parsed_candidate.netloc:
        return False

    return True


def _method_allows_single_segment_path(
    method_name: str, owner_family: str | None
) -> bool:
    if owner_family is None:
        return method_name.lower() in _REST_ASSURED_SINGLE_SEGMENT_PREFIX_METHODS
    return method_name.lower() in _SINGLE_SEGMENT_PATH_METHODS_BY_OWNER_FAMILY.get(
        owner_family, frozenset()
    )


def _allow_relative_path_for_method(method_name: str, literal: str) -> bool:
    normalized_method_name: str = method_name.lower()
    if normalized_method_name in _RELATIVE_PATH_EXCLUDED_METHODS:
        return False
    if normalized_method_name in _RELATIVE_PATH_WHITELISTED_METHODS:
        return True
    return not _looks_like_media_type_literal(literal)


def _normalize_relative_path_literal(path: str) -> str:
    normalized_path: str = path.strip().lstrip("/")
    if not normalized_path:
        return ""
    return f"/{normalized_path}"


class _ExtractedPath(NamedTuple):
    path: str
    truncated: bool


_NO_EXTRACTED_PATH: Final[_ExtractedPath] = _ExtractedPath("", False)


def _is_local_host_only_url_literal(literal: str) -> bool:
    """True for scheme://local-host[:port][/] literals carrying no path beyond root.

    Such a literal contributes nothing to the request path; in a concatenation
    like ``"http://localhost:" + port + "/api/x"`` the path lives in a later
    literal, so the scan must not stop here. A bare root path counts as
    host-only, but a query or fragment does not: those carry request evidence
    (query parameter names) that skipping would silently drop. Restricted to
    local hostnames so an external host keeps its authority (and thus its
    externality) instead of being reinterpreted as a local endpoint path.
    """
    if not literal.startswith(("http://", "https://")):
        return False
    parsed = safe_urlparse(literal)
    if parsed is None or parsed.path not in ("", "/"):
        return False
    if parsed.query or parsed.fragment:
        return False
    return is_local_hostname(parsed.hostname)


def _concat_truncates_path(literal: str, expression_remainder: str) -> bool:
    """True when a `+` after a trailing-slash literal cuts off a path segment.

    The literal's path component must end in `/` beyond root with no query
    string (a query means the cut falls inside the query, not the path), and
    a `+` must follow the literal outside quotes in the same expression.
    """
    parsed = safe_urlparse(literal)
    if parsed is None:
        return False
    if parsed.scheme.lower() in {"http", "https"}:
        path_component = parsed.path
    else:
        path_component = parsed.path or literal
    if len(path_component) <= 1 or not path_component.endswith("/") or parsed.query:
        return False
    return "+" in _QUOTED_STRING_RE.sub("", expression_remainder)


def _resolve_full_path_expression(
    expression: str,
    method_name: str,
    resolve_expression: Callable[[str], str | None],
    owner_family: str | None,
) -> _ExtractedPath | None:
    """Resolve a whole argument expression to a complete path, or None to fall through.

    A full resolution means nothing was cut off, so the returned path is never
    truncated; the literal scan handles partial concatenations.
    """
    resolved_value = resolve_expression(expression)
    if resolved_value is None:
        return None

    value = resolved_value.strip()
    if not value:
        return None

    if (
        value.startswith("/")
        or value.startswith("http://")
        or value.startswith("https://")
    ):
        return _ExtractedPath(value, False)

    if not _is_relative_path_literal(
        value,
        require_path_separator=not _method_allows_single_segment_path(
            method_name, owner_family
        ),
    ):
        return None
    if not _allow_relative_path_for_method(method_name, value):
        return None

    canonical_path = _normalize_relative_path_literal(value)
    if not canonical_path:
        return None
    return _ExtractedPath(canonical_path, False)


def _extract_path(
    argument_exprs: list[str],
    method_name: str,
    resolve_expression: Callable[[str], str | None] | None = None,
    *,
    owner_family: str | None = None,
) -> _ExtractedPath:
    for expression in argument_exprs:
        if resolve_expression is not None:
            resolved = _resolve_full_path_expression(
                expression, method_name, resolve_expression, owner_family
            )
            if resolved is not None:
                return resolved
        for match in _QUOTED_STRING_RE.finditer(expression):
            normalized_match: str = match.group(1).strip()
            if not normalized_match:
                continue
            if (
                normalized_match.startswith("/")
                or normalized_match.startswith("http://")
                or normalized_match.startswith("https://")
            ):
                if _is_local_host_only_url_literal(normalized_match):
                    continue
                return _ExtractedPath(
                    normalized_match,
                    _concat_truncates_path(normalized_match, expression[match.end() :]),
                )

            if not _is_relative_path_literal(
                normalized_match,
                require_path_separator=not _method_allows_single_segment_path(
                    method_name, owner_family
                ),
            ):
                continue
            if not _allow_relative_path_for_method(method_name, normalized_match):
                continue

            canonical_path = _normalize_relative_path_literal(normalized_match)
            if not canonical_path:
                continue
            return _ExtractedPath(
                canonical_path,
                _concat_truncates_path(normalized_match, expression[match.end() :]),
            )
    return _NO_EXTRACTED_PATH


_WHOLE_QUOTED_LITERAL_RE: Final[re.Pattern[str]] = re.compile(r'^"([^"]*)"$')


def _karate_path_segment_value(
    expression: str,
    resolve_expression: Callable[[str], str | None] | None,
) -> str | None:
    stripped = expression.strip()
    literal_match = _WHOLE_QUOTED_LITERAL_RE.match(stripped)
    if literal_match is not None:
        value: str | None = literal_match.group(1)
    elif resolve_expression is not None:
        value = resolve_expression(stripped)
    else:
        value = None
    if value is None:
        return None
    value = value.strip()
    if not value or value.startswith(("http://", "https://")):
        return None
    if not value.startswith("/") and not _is_relative_path_literal(
        value, require_path_separator=False
    ):
        return None
    if not _allow_relative_path_for_method("path", value):
        return None
    return value


def _karate_varargs_path(
    *,
    argument_exprs: list[str],
    method_name: str,
    owner_family: str | None,
    resolve_expression: Callable[[str], str | None] | None,
) -> _ExtractedPath | None:
    """Compose karate's path(String...) varargs into one appended path.

    Karate appends every vararg as a path segment, so literal (or
    constant-resolvable) segments compose in order. The first unresolvable
    segment truncates the composed path there: a later literal must not
    fabricate adjacency across the statically unknown segment.
    """
    if owner_family != "karate.request" or method_name.lower() != "path":
        return None
    if len(argument_exprs) < 2:
        return None

    segments: list[str] = []
    truncated = False
    for expression in argument_exprs:
        segment = _karate_path_segment_value(expression, resolve_expression)
        if segment is None:
            truncated = True
            break
        segments.append(segment)

    composed = ""
    for segment in segments:
        composed = _join_url_path_parts(composed, segment)
    return _ExtractedPath(_normalize_relative_path_literal(composed), truncated)


def _extract_http_method_literal(argument_exprs: list[str]) -> str:
    for expression in argument_exprs:
        for quoted_literal in _QUOTED_STRING_RE.findall(expression):
            normalized_quoted_literal: str = quoted_literal.upper()
            if normalized_quoted_literal in _HTTP_METHOD_TOKENS:
                return normalized_quoted_literal

        qualified_literal_match = _QUALIFIED_HTTP_METHOD_LITERAL_RE.search(expression)
        if qualified_literal_match:
            return qualified_literal_match.group(1).upper()

        stripped_expression: str = expression.strip()
        if stripped_expression in _HTTP_METHOD_TOKENS:
            return stripped_expression
    return "UNKNOWN"


def _argument_exprs_at_positions(
    argument_exprs: list[str],
    positions: tuple[int, ...],
) -> list[str]:
    return [
        argument_exprs[index] for index in positions if 0 <= index < len(argument_exprs)
    ]


def _argument_types_at_positions(
    argument_types: list[str],
    positions: tuple[int, ...],
) -> list[str]:
    return [
        argument_types[index] for index in positions if 0 <= index < len(argument_types)
    ]


def _positions_for_owner_family_method(
    position_map: dict[str, dict[str, tuple[int, ...]]],
    owner_family: str | None,
    method_name: str,
) -> tuple[int, ...]:
    if not owner_family:
        return ()
    return position_map.get(owner_family, {}).get(method_name.lower(), ())


def _apache_create_path_argument_positions(argument_count: int) -> tuple[int, ...]:
    if argument_count == 4:
        return (3,)
    if argument_count == 3:
        return (2,)
    if argument_count == 2:
        return (1,)
    return ()


def _is_apache_dynamic_method_constructor(
    resolved_target: _ResolvedHttpTarget | ReceiverlessRequestInferenceTarget,
) -> bool:
    return (
        resolved_target.framework == HttpDispatchFramework.APACHE_HTTPCLIENT
        and _simple_class_name(getattr(resolved_target, "receiver_type", ""))
        in _APACHE_DYNAMIC_METHOD_CONSTRUCTOR_CLASS_NAMES
    )


def _path_argument_positions(
    *,
    resolved_target: _ResolvedHttpTarget | ReceiverlessRequestInferenceTarget,
    method_name: str,
    is_constructor_call: bool,
    argument_count: int,
) -> tuple[int, ...]:
    if is_constructor_call:
        if _is_apache_dynamic_method_constructor(resolved_target):
            return _apache_create_path_argument_positions(argument_count)
        return _PATH_ARGUMENT_POSITIONS_BY_CONSTRUCTOR_FRAMEWORK.get(
            resolved_target.framework,
            (),
        )
    if (
        resolved_target.owner_family == "apache-httpclient.request"
        and method_name.lower() == "create"
    ):
        return _apache_create_path_argument_positions(argument_count)
    return _positions_for_owner_family_method(
        _PATH_ARGUMENT_POSITIONS_BY_OWNER_FAMILY,
        resolved_target.owner_family,
        method_name,
    )


def _http_method_argument_positions(
    *,
    resolved_target: _ResolvedHttpTarget | ReceiverlessRequestInferenceTarget,
    method_name: str,
    is_constructor_call: bool,
) -> tuple[int, ...]:
    if is_constructor_call and _is_apache_dynamic_method_constructor(resolved_target):
        return (0,)
    return _positions_for_owner_family_method(
        _HTTP_METHOD_ARGUMENT_POSITIONS_BY_OWNER_FAMILY,
        resolved_target.owner_family,
        method_name,
    )


def _http_method_type_argument_positions(
    *,
    resolved_target: _ResolvedHttpTarget | ReceiverlessRequestInferenceTarget,
    method_name: str,
) -> tuple[int, ...]:
    return _positions_for_owner_family_method(
        _HTTP_METHOD_TYPE_ARGUMENT_POSITIONS_BY_OWNER_FAMILY,
        resolved_target.owner_family,
        method_name,
    )


def _simple_class_name(fully_qualified_name: str) -> str:
    """Extract the simple class name from a fully qualified Java type name."""
    if not fully_qualified_name:
        return ""
    return fully_qualified_name.rsplit(".", 1)[-1]


def _extract_http_method_from_argument_types(
    argument_types: list[str],
    verb_by_class_name: dict[str, str],
) -> str:
    """Infer HTTP verb from resolved argument types encoding a verb in their class name."""
    if not verb_by_class_name:
        return "UNKNOWN"
    for argument_type in argument_types:
        simple_name = _simple_class_name(argument_type)
        verb = verb_by_class_name.get(simple_name)
        if verb:
            return verb
    return "UNKNOWN"


def _extract_http_method(
    method_name: str,
    argument_exprs: list[str],
    framework_http_method: str | None = None,
    argument_types: list[str] | None = None,
    verb_by_class_name: dict[str, str] | None = None,
    method_argument_exprs: list[str] | None = None,
    method_argument_types: list[str] | None = None,
) -> str:
    method_from_args = _extract_http_method_literal(
        method_argument_exprs if method_argument_exprs is not None else argument_exprs
    )
    if method_from_args != "UNKNOWN":
        return method_from_args

    selected_argument_types = (
        method_argument_types if method_argument_types is not None else argument_types
    )
    if selected_argument_types and verb_by_class_name:
        method_from_arg_types = _extract_http_method_from_argument_types(
            selected_argument_types,
            verb_by_class_name,
        )
        if method_from_arg_types != "UNKNOWN":
            return method_from_arg_types

    if framework_http_method and framework_http_method != "UNKNOWN":
        return framework_http_method

    return "UNKNOWN"


def _extract_headers(argument_exprs: list[str]) -> list[str]:
    headers: list[str] = []
    for expression in argument_exprs:
        normalized_expression: str = expression.lower()
        if "header" in normalized_expression or any(
            token in normalized_expression for token in _HEADER_HINT_TOKENS
        ):
            headers.append(expression)
    return headers


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _extract_url_query_param_names(path: str) -> list[str]:
    return sorted(extract_query_param_names(path))


def _extract_path_param_names(path: str) -> list[str]:
    names: list[str] = []
    for match in _PATH_TEMPLATE_VARIABLE_RE.finditer(path):
        name = match.group(2).strip()
        if name:
            names.append(name)
    return _dedupe_preserving_order(names)


def _has_string_concatenation(expression: str) -> bool:
    """True when a `+` joins parts outside string literals (a built-up value)."""
    return "+" in _QUOTED_STRING_RE.sub("", expression)


def _string_literal_argument(argument_exprs: list[str], index: int) -> str:
    if index >= len(argument_exprs):
        return ""
    match = _STRING_LITERAL_EXPRESSION_RE.match(argument_exprs[index])
    if match is None:
        return ""
    return match.group(1).strip()


def _mockmvc_multipart_file_name(argument_exprs: list[str]) -> str:
    literal_arg = _string_literal_argument(argument_exprs, 0)
    if literal_arg:
        return literal_arg
    if not argument_exprs:
        return ""
    match = _MOCK_MULTIPART_FILE_NAME_RE.match(argument_exprs[0])
    if match is None:
        return ""
    return match.group(1).strip()


def _normalize_header_constant(constant_name: str) -> str:
    return constant_name.replace("_", "-").lower()


def _header_name_literal(expression: str) -> str:
    """A header-name string literal: the whole argument, or the first argument of a
    recognized header wrapper (`new Header("X-Token", v)`).

    A literal anywhere else in the expression -- a value, a `String.format` template,
    or a wrapper whose name argument is not itself a literal -- is not the name.
    """
    whole_literal = _string_literal_argument([expression], 0)
    if whole_literal:
        return whole_literal
    wrapper_match = _HEADER_OBJECT_NAME_RE.match(expression)
    return wrapper_match.group(1).strip() if wrapper_match is not None else ""


def _header_name_from_argument(argument_exprs: list[str], index: int) -> str:
    if index >= len(argument_exprs):
        return ""

    expression = argument_exprs[index].strip()
    literal_name = _header_name_literal(expression)
    if literal_name:
        return literal_name.lower()

    # A constant reference (`HttpHeaders.AUTHORIZATION`, bare `CONTENT_TYPE`). A `+`
    # outside string literals means the constant is concatenated into a larger,
    # dynamic name (`prefix + HttpHeaders.AUTHORIZATION`), so it is only a fragment
    # and must not be taken as the name.
    if _has_string_concatenation(expression):
        return ""
    constant_match = _HEADER_CONSTANT_REFERENCE_RE.search(expression)
    if constant_match is None:
        return ""
    constant_name = constant_match.group("constant")
    if constant_name in _NON_HEADER_CONSTANT_NAMES:
        return ""

    qualifier = constant_match.group("qualifier")
    if qualifier in _HEADER_CONSTANT_CLASS_SIMPLE_NAMES:
        return _normalize_header_constant(constant_name)
    if qualifier is None and constant_name in _STANDARD_HTTP_HEADER_CONSTANTS:
        return _normalize_header_constant(constant_name)
    return ""


def _extract_alternating_literal_names(argument_exprs: list[str]) -> list[str]:
    return [
        name
        for index in range(0, len(argument_exprs), 2)
        if (name := _string_literal_argument(argument_exprs, index))
    ]


def _extract_alternating_header_names(argument_exprs: list[str]) -> list[str]:
    return [
        name
        for index in range(0, len(argument_exprs), 2)
        if (name := _header_name_from_argument(argument_exprs, index))
    ]


def _extract_embedded_query_param_names(argument_exprs: list[str]) -> list[str]:
    names: list[str] = []
    for expression in argument_exprs:
        for match in _EMBEDDED_QUERY_PARAM_RE.finditer(expression):
            name = match.group(1).strip()
            if name:
                names.append(name)
    return _dedupe_preserving_order(names)


def _extract_header_names(method_name: str, argument_exprs: list[str]) -> list[str]:
    normalized_method_name = method_name.lower()
    if normalized_method_name in _DIRECT_HEADER_METHOD_TO_NAME:
        return [_DIRECT_HEADER_METHOD_TO_NAME[normalized_method_name]]
    if normalized_method_name in {"header", "setheader", "addheader"}:
        return _dedupe_preserving_order(
            [name for name in [_header_name_from_argument(argument_exprs, 0)] if name]
        )
    if normalized_method_name == "headers":
        return _dedupe_preserving_order(
            _extract_alternating_header_names(argument_exprs)
        )
    return []


def _extract_query_param_names(
    method_name: str,
    argument_exprs: list[str],
    path: str,
    owner_family: str | None = None,
    framework: HttpDispatchFramework | None = None,
) -> list[str]:
    names = _extract_url_query_param_names(path)
    normalized_method_name = method_name.lower()

    # In REST Assured, `param`/`params` are method-dependent (query for GET,
    # form for POST). They are tracked separately so the final verb can decide.
    if normalized_method_name in {"param", "params"}:
        if framework == HttpDispatchFramework.REST_ASSURED:
            return _dedupe_preserving_order(names)

    if normalized_method_name in {"queryparam", "param", "query"} or (
        owner_family == "pact.request" and normalized_method_name == "encodedquery"
    ):
        if owner_family == "pact.request":
            query_literal = _string_literal_argument(argument_exprs, 0)
            if query_literal:
                names.extend(parse_qs(query_literal, keep_blank_values=True).keys())
        else:
            names.append(_string_literal_argument(argument_exprs, 0))
    elif normalized_method_name in {"queryparams", "params", "queries"}:
        names.extend(_extract_alternating_literal_names(argument_exprs))
    elif normalized_method_name == "uri":
        names.extend(_extract_embedded_query_param_names(argument_exprs))

    return _dedupe_preserving_order(names)


def _extract_rest_assured_ambiguous_param_names(
    method_name: str,
    argument_exprs: list[str],
) -> list[str]:
    # REST Assured's `param`/`params` are query for GET and form for POST;
    # keep them in a separate provenance bucket until the final verb is known.
    normalized_method_name = method_name.lower()
    if normalized_method_name == "param":
        name = _string_literal_argument(argument_exprs, 0)
        return _dedupe_preserving_order([name] if name else [])
    if normalized_method_name == "params":
        return _dedupe_preserving_order(
            _extract_alternating_literal_names(argument_exprs)
        )
    return []


def _extract_form_param_names(
    *,
    framework: HttpDispatchFramework,
    method_name: str,
    argument_exprs: list[str],
) -> list[str]:
    normalized_method_name = method_name.lower()
    if normalized_method_name == "formparam" or (
        # `multipart` is a form-field setter on RestAssured's RequestSpecification,
        # but a request-factory verb on MockMvc whose first arg is the URL path.
        normalized_method_name == "multipart"
        and framework == HttpDispatchFramework.REST_ASSURED
    ):
        return _dedupe_preserving_order(
            [name for name in [_string_literal_argument(argument_exprs, 0)] if name]
        )
    if normalized_method_name == "formparams":
        return _dedupe_preserving_order(
            _extract_alternating_literal_names(argument_exprs)
        )
    # MockMvc multipart parts: `.file("part", bytes)` or
    # `.file(new MockMultipartFile("part", ...))`.
    if normalized_method_name == "file" and framework == HttpDispatchFramework.MOCKMVC:
        return _dedupe_preserving_order(
            [name for name in [_mockmvc_multipart_file_name(argument_exprs)] if name]
        )
    # WebTestClient/WebClient form/multipart bodies built inline with literal
    # field names, e.g. `body(BodyInserters.fromFormData("user", "v"))`.
    if normalized_method_name == "body" and framework in {
        HttpDispatchFramework.WEBTESTCLIENT,
        HttpDispatchFramework.WEBCLIENT,
    }:
        return _extract_body_inserter_form_names(argument_exprs)
    return []


def _extract_body_inserter_form_names(argument_exprs: list[str]) -> list[str]:
    names: list[str] = []
    for expression in argument_exprs:
        for match in _BODY_INSERTER_FORM_NAME_RE.finditer(expression):
            name = match.group(1).strip()
            if name:
                names.append(name)
    return _dedupe_preserving_order(names)


def _body_inserter_variable_names(argument_exprs: list[str]) -> list[str]:
    names: list[str] = []
    for expression in argument_exprs:
        for match in _BODY_INSERTER_VARIABLE_RE.finditer(expression):
            name = match.group(1).strip()
            if name:
                names.append(name)
    return _dedupe_preserving_order(names)


def _nodes_sorted_by_span(grouping: CallSiteGrouping) -> list[CallSiteNode]:
    return sorted(
        grouping.nodes,
        key=lambda node: (
            node.span.start.line,
            node.span.start.col,
            node.span.end.line,
            node.span.end.col,
        ),
    )


def _literal_keys_set_on_variable(
    sorted_nodes: list[CallSiteNode],
    variable_name: str,
    *,
    before_node: CallSiteNode,
    mutator_methods: frozenset[str],
    extract_key: Callable[[str, list[str]], str],
) -> list[str]:
    """Keys set on a variable by mutator calls earlier in the same method.

    Walks ``sorted_nodes`` (call sites ordered by span) for invocations on
    ``variable_name`` whose method is a recognized mutator positioned before
    ``before_node``, collecting the key returned by ``extract_key``. Used to
    recover form/header field names from a container populated before it is
    passed to a dispatch call.
    """
    keys: list[str] = []
    for node in sorted_nodes:
        # Fluent-chain calls share a start position in CLDK; the dispatch node's
        # end is the useful boundary for excluding later mutations.
        if node.span.start >= before_node.span.end:
            continue
        call_site = node.call_site
        if (call_site.receiver_expr or "") != variable_name:
            continue
        method_name = call_site.method_name or ""
        if method_name.lower() not in mutator_methods:
            continue
        argument_exprs = [str(argument) for argument in (call_site.argument_expr or [])]
        key = extract_key(method_name, argument_exprs)
        if key:
            keys.append(key)
    return _dedupe_preserving_order(keys)


def _extract_request_path_param_names(
    method_name: str,
    argument_exprs: list[str],
    path: str,
) -> list[str]:
    names = _extract_path_param_names(path)
    normalized_method_name = method_name.lower()
    if normalized_method_name == "pathparam":
        names.append(_string_literal_argument(argument_exprs, 0))
    elif normalized_method_name == "pathparams":
        names.extend(_extract_alternating_literal_names(argument_exprs))
    return _dedupe_preserving_order(names)


def _extract_auth_hints(argument_exprs: list[str]) -> list[str]:
    hints: set[str] = set()
    for expression in argument_exprs:
        normalized_expression: str = expression.lower()
        for normalized_token, canonical_token in _AUTH_HINT_TOKEN_LOOKUP.items():
            if normalized_token in normalized_expression:
                hints.add(canonical_token)
    return sorted(hints)


def _argument_at(argument_exprs: list[str], index: int) -> str:
    return argument_exprs[index] if index < len(argument_exprs) else ""


def _is_non_empty_http_entity(expression: str) -> bool:
    """A RestTemplate request entity that carries content. `null`, `HttpEntity.EMPTY`, a
    no-argument `new HttpEntity<>()`, and an explicit-null body slot
    (`new HttpEntity<>(null[, headers])`) are body-less. A non-null single-argument
    `new HttpEntity<>(headers)` is syntactically indistinguishable from a body-bearing
    `new HttpEntity<>(body)` and is intentionally treated as a body."""
    if not expression.strip():
        return False
    if _EMPTY_HTTP_ENTITY_RE.match(expression):
        return False
    if _NULL_BODY_HTTP_ENTITY_CONSTRUCTOR_RE.match(expression):
        return False
    return _EMPTY_HTTP_ENTITY_CONSTRUCTOR_RE.match(expression) is None


def _resttemplate_exchange_has_body(
    http_method: str, argument_exprs: list[str]
) -> bool:
    """`exchange(url, HttpMethod, HttpEntity, responseType, ...)`: a body when the verb
    is body-capable and the request entity (arg index 2) carries content. The
    `exchange(RequestEntity, Class)` overload embeds verb and body in the first argument
    and is not decoded here (a conservative miss, never a false positive)."""
    if http_method not in _BODY_CAPABLE_HTTP_METHODS:
        return False
    return _is_non_empty_http_entity(_argument_at(argument_exprs, 2))


def _is_non_no_body_publisher(expression: str) -> bool:
    """A Java HttpClient BodyPublisher argument that carries content: present and not the
    explicit `BodyPublishers.noBody()`."""
    if not expression.strip():
        return False
    return _NO_BODY_PUBLISHER_RE.match(expression) is None


def _jaxrs_method_has_body(argument_exprs: list[str]) -> bool:
    """`Invocation.Builder.method(verb, ...)` carries a body when its second argument is
    an `Entity.*(...)` factory call. The `method(verb)` and `method(verb, Class)`
    response-type overloads have no entity there, so a bare variable or a `Class`
    argument is intentionally not treated as a body."""
    return bool(_JAXRS_ENTITY_FACTORY_RE.match(_argument_at(argument_exprs, 1)))


def _is_request_entity_receiver(receiver_type: str) -> bool:
    return matches_receiver_prefix(
        (receiver_type or "").lower(),
        "org.springframework.http.requestentity",
    )


def _has_body(
    *,
    framework: HttpDispatchFramework,
    method_name: str,
    http_method: str,
    argument_exprs: list[str],
    receiver_type: str = "",
) -> bool:
    normalized_method_name: str = method_name.lower()
    if normalized_method_name in _BODY_PAYLOAD_METHODS and argument_exprs:
        return True

    if framework in {
        HttpDispatchFramework.REST_TEMPLATE,
        HttpDispatchFramework.TEST_REST_TEMPLATE,
    }:
        if _is_request_entity_receiver(receiver_type):
            # RequestEntity verb factories take a URI/URI-template plus optional
            # template variables; a body is only introduced by the explicit
            # .body(...) builder call (handled above).
            return False
        if normalized_method_name in {
            "postforentity",
            "postforobject",
            "postforlocation",
            "put",
        }:
            return len(argument_exprs) >= 2
        if normalized_method_name == "patchforobject":
            return len(argument_exprs) >= 2
        if normalized_method_name == "exchange":
            return _resttemplate_exchange_has_body(http_method, argument_exprs)

    if framework == HttpDispatchFramework.JAVA_HTTPCLIENT:
        # `.POST`/`.PUT` take a BodyPublisher first; `.method(verb, BodyPublisher)` takes
        # it second. Either way, exclude the explicit `BodyPublishers.noBody()`.
        if normalized_method_name in {"post", "put", "patch"}:
            return _is_non_no_body_publisher(_argument_at(argument_exprs, 0))
        if normalized_method_name == "method":
            return _is_non_no_body_publisher(_argument_at(argument_exprs, 1))

    if framework == HttpDispatchFramework.OKHTTP and normalized_method_name in {
        "post",
        "put",
        "patch",
    }:
        return bool(argument_exprs)

    # HttpRequest.POST/PUT/PATCH(uri, body) require a body argument; DELETE
    # carries one only in its two-argument overload.
    if framework == HttpDispatchFramework.MICRONAUT_CLIENT and (
        normalized_method_name in {"post", "put", "patch", "delete"}
    ):
        return len(argument_exprs) >= 2

    # karate post/put/patch and their *Json variants only have body-carrying
    # overloads; delete carries a body only in its 2.x delete(Object) overload,
    # and method/methodJson only in their two-argument (verb, body) overloads.
    if framework == HttpDispatchFramework.KARATE:
        if normalized_method_name in {
            "post",
            "put",
            "patch",
            "postjson",
            "putjson",
            "patchjson",
            "delete",
        }:
            return bool(argument_exprs)
        if normalized_method_name in {"method", "methodjson"}:
            return len(argument_exprs) >= 2

    if framework == HttpDispatchFramework.JAX_RS:
        # `post`/`put` have only entity-bearing overloads, so any argument is a body.
        if normalized_method_name in {"post", "put"}:
            return bool(argument_exprs)
        if normalized_method_name == "method":
            return _jaxrs_method_has_body(argument_exprs)

    if framework == HttpDispatchFramework.FEIGN and normalized_method_name == "body":
        return True

    return False


def _is_mockito_reference(value: str) -> bool:
    return any(value.startswith(prefix) for prefix in _MOCKITO_RECEIVER_PREFIXES)


def _mocking_context_from_wrapper(
    wrapper_node: CallSiteNode,
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
) -> MockingContext | None:
    method_name = wrapper_node.call_site.method_name or ""
    normalized_method_name = method_name.lower()
    if normalized_method_name in _MOCKITO_STUBBING_METHODS:
        kind = MockingContextKind.STUBBING
    elif normalized_method_name in _MOCKITO_VERIFICATION_METHODS:
        kind = MockingContextKind.VERIFICATION
    else:
        return None

    resolved_receiver = receiver_resolver(wrapper_node.call_site)
    receiver_type = resolved_receiver.receiver_type
    callee_signature = wrapper_node.call_site.callee_signature or ""
    if not (
        _is_mockito_reference(receiver_type) or _is_mockito_reference(callee_signature)
    ):
        return None

    return MockingContext(kind=kind, wrapper_method=method_name)


def _mocking_context_from_receiver_chain(
    grouping: CallSiteGrouping,
    node: CallSiteNode,
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
) -> MockingContext | None:
    context: MockingContext | None = None
    for chain_node in grouping.receiver_chain_for(
        line=node.span.start.line,
        col=node.span.start.col,
    ):
        if chain_node is node:
            break
        chain_context = _mocking_context_from_wrapper(
            chain_node,
            receiver_resolver=receiver_resolver,
        )
        if chain_context is not None:
            context = chain_context

    return context


def _mocking_context_from_argument_wrapper(
    node: CallSiteNode,
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
) -> MockingContext | None:
    receiver_chain_root = node
    while (
        receiver_chain_root.parent is not None
        and receiver_chain_root.parent.span.start == receiver_chain_root.span.start
    ):
        receiver_chain_root = receiver_chain_root.parent

    wrapper_node = receiver_chain_root.parent
    if wrapper_node is None:
        return None

    normalized_wrapper_method = (wrapper_node.call_site.method_name or "").lower()
    if normalized_wrapper_method not in _MOCKITO_ARGUMENT_STUBBING_METHODS:
        return None

    return _mocking_context_from_wrapper(
        wrapper_node,
        receiver_resolver=receiver_resolver,
    )


def _mocking_context_for_node(
    grouping: CallSiteGrouping,
    node: CallSiteNode,
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
) -> MockingContext | None:
    context = _mocking_context_from_receiver_chain(
        grouping,
        node,
        receiver_resolver=receiver_resolver,
    )
    if context is not None:
        return context

    return _mocking_context_from_argument_wrapper(
        node,
        receiver_resolver=receiver_resolver,
    )


def _apply_mocking_context_if_present(
    grouping: CallSiteGrouping,
    node: CallSiteNode,
    classification: HttpClassification,
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
) -> None:
    context = _mocking_context_for_node(
        grouping,
        node,
        receiver_resolver=receiver_resolver,
    )
    if context is None:
        return

    classification.mocking_context = context
    classification.request_role = None
    classification.response_role = None


def _is_webtestclient_void_body_argument(argument_exprs: list[str]) -> bool:
    for argument_expr in argument_exprs:
        compact_argument = "".join(str(argument_expr).split())
        if compact_argument in _WEBTESTCLIENT_VOID_BODY_ARGUMENTS:
            return True
    return False


def _webtestclient_expect_body_role(argument_exprs: list[str]) -> HttpResponseRole:
    if argument_exprs and not _is_webtestclient_void_body_argument(argument_exprs):
        return HttpResponseRole.BODY_ASSERTION
    return HttpResponseRole.MATCHER


def _normalize_webtestclient_response_role(
    call_site: JCallSite,
    classification: HttpClassification,
) -> None:
    if (
        classification.framework != HttpDispatchFramework.WEBTESTCLIENT
        or classification.response_role is None
    ):
        return

    method_name = call_site.method_name or ""
    if method_name in {"expectStatus", "expectHeader", "expectCookie"}:
        classification.response_role = HttpResponseRole.MATCHER
    elif method_name in {"expectBody", "expectBodyList"}:
        classification.response_role = _webtestclient_expect_body_role(
            [str(argument) for argument in (call_site.argument_expr or [])]
        )


_MICRONAUT_CLIENT_URI_EVENT_METHODS: Final[frozenset[str]] = frozenset(
    {"exchange", "retrieve"}
)


def _apply_micronaut_string_uri_get_default(
    call_site: JCallSite,
    classification: HttpClassification,
) -> None:
    """The String-URI exchange/retrieve overloads delegate to HttpRequest.GET(uri)
    (HttpClient/BlockingHttpClient interface default methods), fixing the verb."""
    if classification.framework != HttpDispatchFramework.MICRONAUT_CLIENT:
        return
    if classification.request_role != HttpRequestRole.EVENT:
        return
    if classification.http_method != "UNKNOWN":
        return
    method_name = (call_site.method_name or "").lower()
    if method_name not in _MICRONAUT_CLIENT_URI_EVENT_METHODS:
        return
    argument_types = list(call_site.argument_types or [])
    if argument_types and argument_types[0] == "java.lang.String":
        classification.http_method = "GET"


def _classify_call_site(
    call_site: JCallSite,
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
    callee_resolver: Callable[[JCallSite], ResolvedCallee | None] | None = None,
    resolve_expression: Callable[[str], str | None] | None = None,
    resolve_class_expression: Callable[[str, str], str | None] | None = None,
) -> HttpClassification | None:
    method_name: str = call_site.method_name or ""
    argument_exprs: list[str] = [
        str(argument) for argument in (call_site.argument_expr or [])
    ]
    argument_types: list[str] = list(call_site.argument_types or [])
    resolved_receiver = receiver_resolver(call_site)
    receiver_type = resolved_receiver.receiver_type
    resolved_target: _ResolvedHttpTarget | None = None

    if receiver_type:
        resolved_target = _resolve_owner_family_target(
            receiver_type=receiver_type,
            method_name=method_name,
            is_constructor_call=bool(call_site.is_constructor_call),
        )

    if resolved_target is None:
        if callee_resolver is not None and not call_site.is_constructor_call:
            callee = callee_resolver(call_site)
            if callee is not None:
                declarative_classification = (
                    classify_spring_declarative_client_call_site(
                        call_site, callee, resolve_class_expression
                    )
                )
                if declarative_classification is not None:
                    return declarative_classification
        diagnostic_path = _extract_path(
            argument_exprs=argument_exprs,
            method_name=method_name,
            resolve_expression=resolve_expression,
        )
        if diagnostic_path.path:
            LOGGER.debug(
                "Skipping unrecognized HTTP-like call site: "
                "method=%s receiver=%s path=%s",
                method_name,
                receiver_type,
                diagnostic_path.path,
            )
        return None

    path_argument_exprs = _argument_exprs_at_positions(
        argument_exprs,
        _path_argument_positions(
            resolved_target=resolved_target,
            method_name=method_name,
            is_constructor_call=bool(call_site.is_constructor_call),
            argument_count=len(argument_exprs),
        ),
    )
    varargs_path = _karate_varargs_path(
        argument_exprs=argument_exprs,
        method_name=method_name,
        owner_family=resolved_target.owner_family,
        resolve_expression=resolve_expression,
    )
    if varargs_path is not None:
        path, path_truncated = varargs_path
    else:
        path, path_truncated = _extract_path(
            argument_exprs=path_argument_exprs,
            method_name=method_name,
            resolve_expression=resolve_expression,
            owner_family=resolved_target.owner_family,
        )
    method_argument_positions = _http_method_argument_positions(
        resolved_target=resolved_target,
        method_name=method_name,
        is_constructor_call=bool(call_site.is_constructor_call),
    )
    method_argument_type_positions = _http_method_type_argument_positions(
        resolved_target=resolved_target,
        method_name=method_name,
    )
    http_method: str = _extract_http_method(
        method_name=method_name,
        argument_exprs=argument_exprs,
        framework_http_method=resolved_target.framework_http_method,
        argument_types=argument_types,
        verb_by_class_name=resolved_target.verb_by_class_name,
        method_argument_exprs=_argument_exprs_at_positions(
            argument_exprs,
            method_argument_positions,
        ),
        method_argument_types=_argument_types_at_positions(
            argument_types,
            method_argument_type_positions,
        ),
    )

    classification = HttpClassification(
        http_method=http_method,
        path=path,
        framework=resolved_target.framework,
        path_truncated=path_truncated,
        receiver_type=resolved_target.receiver_type,
        owner_family=resolved_target.owner_family,
        request_role=resolved_target.request_role,
        response_role=resolved_target.response_role,
        headers=_extract_headers(argument_exprs),
        header_names=_extract_header_names(method_name, argument_exprs),
        query_param_names=_extract_query_param_names(
            method_name,
            argument_exprs,
            path,
            owner_family=resolved_target.owner_family,
            framework=resolved_target.framework,
        ),
        path_param_names=_extract_request_path_param_names(
            method_name,
            argument_exprs,
            path,
        ),
        form_param_names=_extract_form_param_names(
            framework=resolved_target.framework,
            method_name=method_name,
            argument_exprs=argument_exprs,
        ),
        rest_assured_ambiguous_param_names=(
            _extract_rest_assured_ambiguous_param_names(method_name, argument_exprs)
            if resolved_target.framework == HttpDispatchFramework.REST_ASSURED
            else []
        ),
        has_body_payload=_has_body(
            framework=resolved_target.framework,
            method_name=method_name,
            http_method=http_method,
            argument_exprs=argument_exprs,
            receiver_type=resolved_target.receiver_type,
        ),
        auth_hints=_extract_auth_hints(argument_exprs),
    )
    _normalize_webtestclient_response_role(call_site, classification)
    _apply_micronaut_string_uri_get_default(call_site, classification)
    return classification


def _request_builder_evidence(
    nodes: list[CallSiteNode],
    call_site_to_classification: dict[int, HttpClassification],
) -> list[HttpClassification]:
    evidence: list[HttpClassification] = []
    for node in nodes:
        classification = call_site_to_classification.get(id(node.call_site))
        if classification is None:
            continue
        if classification.request_role != HttpRequestRole.BUILDER:
            continue
        evidence.append(classification)
    return evidence


def _single_evidence_framework(
    evidence: list[HttpClassification],
) -> HttpDispatchFramework | None:
    frameworks = {classification.framework for classification in evidence}
    if len(frameworks) != 1:
        return None
    return next(iter(frameworks))


def _inferred_request_classification(
    call_site: JCallSite,
    *,
    target: ReceiverlessRequestInferenceTarget,
    receiver_type: str,
    resolve_expression: Callable[[str], str | None] | None = None,
) -> HttpClassification | None:
    method_name = call_site.method_name or ""
    argument_exprs = [str(argument) for argument in (call_site.argument_expr or [])]
    argument_types = list(call_site.argument_types or [])
    path_argument_exprs = _argument_exprs_at_positions(
        argument_exprs,
        _path_argument_positions(
            resolved_target=target,
            method_name=method_name,
            is_constructor_call=bool(call_site.is_constructor_call),
            argument_count=len(argument_exprs),
        ),
    )
    path, path_truncated = _extract_path(
        argument_exprs=path_argument_exprs,
        method_name=method_name,
        resolve_expression=resolve_expression,
        owner_family=target.owner_family,
    )
    method_argument_positions = _http_method_argument_positions(
        resolved_target=target,
        method_name=method_name,
        is_constructor_call=bool(call_site.is_constructor_call),
    )
    method_argument_type_positions = _http_method_type_argument_positions(
        resolved_target=target,
        method_name=method_name,
    )
    http_method = _extract_http_method(
        method_name=method_name,
        argument_exprs=argument_exprs,
        framework_http_method=target.framework_http_method,
        argument_types=argument_types,
        method_argument_exprs=_argument_exprs_at_positions(
            argument_exprs,
            method_argument_positions,
        ),
        method_argument_types=_argument_types_at_positions(
            argument_types,
            method_argument_type_positions,
        ),
    )
    classification = HttpClassification(
        http_method=http_method,
        path=path,
        framework=target.framework,
        path_truncated=path_truncated,
        receiver_type=receiver_type,
        owner_family=target.owner_family,
        request_role=target.request_role,
        headers=_extract_headers(argument_exprs),
        header_names=_extract_header_names(method_name, argument_exprs),
        query_param_names=_extract_query_param_names(
            method_name,
            argument_exprs,
            path,
            owner_family=target.owner_family,
            framework=target.framework,
        ),
        path_param_names=_extract_request_path_param_names(
            method_name,
            argument_exprs,
            path,
        ),
        form_param_names=_extract_form_param_names(
            framework=target.framework,
            method_name=method_name,
            argument_exprs=argument_exprs,
        ),
        rest_assured_ambiguous_param_names=(
            _extract_rest_assured_ambiguous_param_names(method_name, argument_exprs)
            if target.framework == HttpDispatchFramework.REST_ASSURED
            else []
        ),
        has_body_payload=_has_body(
            framework=target.framework,
            method_name=method_name,
            http_method=http_method,
            argument_exprs=argument_exprs,
            receiver_type=receiver_type,
        ),
        auth_hints=_extract_auth_hints(argument_exprs),
    )
    _apply_micronaut_string_uri_get_default(call_site, classification)
    return classification


def _inferred_classification_from_framework(
    call_site: JCallSite,
    *,
    framework: HttpDispatchFramework,
    receiver_type: str,
    resolve_expression: Callable[[str], str | None] | None = None,
) -> HttpClassification | None:
    target = infer_receiverless_request_target(
        framework,
        call_site.method_name or "",
    )
    if target is None:
        return None
    return _inferred_request_classification(
        call_site,
        target=target,
        receiver_type=receiver_type,
        resolve_expression=resolve_expression,
    )


def _sync_endpoint_candidate(
    node: CallSiteNode, classification: HttpClassification
) -> None:
    """Mirror the classification's request identity onto the node's candidate."""
    if classification.request_role is None or not classification.path:
        return
    if node.endpoint_candidate is not None:
        node.endpoint_candidate.http_method = classification.http_method
        node.endpoint_candidate.path = classification.path
        node.endpoint_candidate.path_truncated = classification.path_truncated
    else:
        node.endpoint_candidate = EndpointCandidate(
            http_method=classification.http_method,
            path=classification.path,
            source="call-site",
            start_line=int(node.call_site.start_line),
            path_truncated=classification.path_truncated,
        )


def _annotate_inferred_request_node(
    node: CallSiteNode,
    classification: HttpClassification,
    call_site_to_classification: dict[int, HttpClassification],
) -> None:
    node.http_classification = classification
    call_site_to_classification[id(node.call_site)] = classification
    _sync_endpoint_candidate(node, classification)


def _infer_request_nodes_from_builder_evidence(
    grouping: CallSiteGrouping,
    call_site_to_classification: dict[int, HttpClassification],
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
    resolve_expression: Callable[[str], str | None] | None = None,
) -> None:
    """Recover request nodes anchored by already-classified builders."""

    if not call_site_to_classification:
        return

    for chain_nodes in grouping.receiver_chains():
        active_builder_nodes: list[CallSiteNode] = []
        for chain_node in chain_nodes:
            existing_classification = call_site_to_classification.get(
                id(chain_node.call_site)
            )
            if existing_classification is not None:
                if existing_classification.mocking_context is not None:
                    active_builder_nodes.clear()
                elif existing_classification.request_role == HttpRequestRole.EVENT:
                    active_builder_nodes.clear()
                elif existing_classification.response_role is not None:
                    active_builder_nodes.clear()
                elif existing_classification.request_role == HttpRequestRole.BUILDER:
                    active_builder_nodes.append(chain_node)
                continue

            evidence_nodes = list(active_builder_nodes)
            evidence_nodes.extend(chain_node.argument_children())
            for argument_child in chain_node.argument_children():
                evidence_nodes.extend(argument_child.all_descendants())

            evidence = _request_builder_evidence(
                evidence_nodes,
                call_site_to_classification,
            )
            framework = _single_evidence_framework(evidence)
            if framework is None:
                continue

            nearest_receiver_type = next(
                (
                    classification.receiver_type
                    for classification in reversed(evidence)
                    if classification.receiver_type
                ),
                "",
            )
            classification = _inferred_classification_from_framework(
                chain_node.call_site,
                framework=framework,
                receiver_type=nearest_receiver_type,
                resolve_expression=resolve_expression,
            )
            if classification is not None:
                _apply_mocking_context_if_present(
                    grouping,
                    chain_node,
                    classification,
                    receiver_resolver=receiver_resolver,
                )
                _annotate_inferred_request_node(
                    chain_node,
                    classification,
                    call_site_to_classification,
                )

                if classification.request_role == HttpRequestRole.EVENT:
                    active_builder_nodes.clear()
                elif classification.request_role == HttpRequestRole.BUILDER:
                    active_builder_nodes.append(chain_node)


def _event_chain_prefix(
    grouping: CallSiteGrouping,
    event_node: CallSiteNode,
) -> list[CallSiteNode]:
    """The event's receiver-chain nodes preceding the event, in chain order."""
    chain_prefix: list[CallSiteNode] = []
    for chain_node in grouping.receiver_chain_for(
        line=event_node.span.start.line,
        col=event_node.span.start.col,
    ):
        if chain_node is event_node:
            break
        chain_prefix.append(chain_node)
    return chain_prefix


def _builder_candidates_for_event(
    grouping: CallSiteGrouping,
    event_node: CallSiteNode,
) -> list[CallSiteNode]:

    chain_prefix = _event_chain_prefix(grouping, event_node)

    candidates: list[CallSiteNode] = list(chain_prefix)
    for chain_node in chain_prefix:
        candidates.extend(chain_node.all_descendants())
    candidates.extend(event_node.all_descendants())

    deduped_candidates: list[CallSiteNode] = []
    seen: set[int] = set()
    for candidate in candidates:
        if id(candidate) in seen:
            continue
        seen.add(id(candidate))
        deduped_candidates.append(candidate)
    return deduped_candidates


def _helper_return_receiver_overrides(
    grouping: CallSiteGrouping,
    receiver_resolver: RuntimeReceiverResolver,
) -> dict[int, ResolvedReceiver]:
    helper_return_receivers: dict[int, ResolvedReceiver] = {}

    for chain_nodes in grouping.receiver_chains():
        for previous_node, current_node in zip(chain_nodes, chain_nodes[1:]):
            if previous_node.resolved_helper is None:
                continue
            if (current_node.call_site.receiver_type or "").strip():
                continue
            if not (current_node.call_site.receiver_expr or "").strip():
                continue

            resolved_receiver = receiver_resolver.resolve_helper_return_receiver(
                previous_node.resolved_helper
            )
            if not resolved_receiver.receiver_type:
                continue
            helper_return_receivers[id(current_node.call_site)] = resolved_receiver

    return helper_return_receivers


def _resolve_variable_backed_names(
    grouping: CallSiteGrouping,
    *,
    accept: Callable[[CallSiteNode, HttpClassification], bool],
    container_variables: Callable[[list[str]], list[str]],
    mutator_methods: frozenset[str],
    extract_key: Callable[[str, list[str]], str],
    target_names: Callable[[HttpClassification], list[str]],
) -> None:
    """Append container-variable field names to each dispatch node ``accept`` selects.

    For a selected node, resolves the container variables passed to it, collects the
    keys mutator calls set on those variables earlier in the method, and appends the
    new ones to ``target_names(classification)`` so correlation can merge them onto
    the request event. The span sort is hoisted out of the per-variable scan so a
    grouping with many dispatch sites sorts its nodes once.
    """
    sorted_nodes = _nodes_sorted_by_span(grouping)
    for node in grouping.nodes:
        classification = node.http_classification
        if classification is None or not accept(node, classification):
            continue
        argument_exprs = [
            str(argument) for argument in (node.call_site.argument_expr or [])
        ]
        existing = target_names(classification)
        for variable_name in container_variables(argument_exprs):
            for name in _literal_keys_set_on_variable(
                sorted_nodes,
                variable_name,
                before_node=node,
                mutator_methods=mutator_methods,
                extract_key=extract_key,
            ):
                if name not in existing:
                    existing.append(name)


def _resolve_variable_backed_form_names(grouping: CallSiteGrouping) -> None:
    """Recover WebTestClient/WebClient form field names from a map variable.

    The inline `fromFormData("name", ...)` overload is named at the call site, but
    the common fixture builds a map (`formData.add("name", ...)`) and passes it as
    `fromFormData(formData)`. This resolves those keys from same-method population
    and appends them to the body builder's form names so correlation can merge them.
    """
    _resolve_variable_backed_names(
        grouping,
        accept=lambda node, classification: (
            classification.framework
            in {
                HttpDispatchFramework.WEBTESTCLIENT,
                HttpDispatchFramework.WEBCLIENT,
            }
            and (node.call_site.method_name or "").lower() == "body"
        ),
        container_variables=_body_inserter_variable_names,
        mutator_methods=_MULTIVALUEMAP_KEY_METHODS,
        extract_key=lambda _method_name, argument_exprs: _string_literal_argument(
            argument_exprs, 0
        ),
        target_names=lambda classification: classification.form_param_names,
    )


def _resolve_variable_backed_query_names(grouping: CallSiteGrouping) -> None:
    """Recover query-param names from a map variable passed to `.queryParams(var)`.

    The `.queryParam("name", v)` / `.queryParams("a", va, "b", vb)` forms are named
    at the call site, but RestAssured and MockMvc also accept a pre-built map
    (`params.put("status", v)` / `params.add("status", v)`) passed as
    `.queryParams(params)`. This resolves those keys from same-method population and
    appends them to the node's query names so correlation can merge them onto the
    request event. The single-identifier-argument gate keeps the literal vararg
    overload (already handled at extraction) out of this map-variable path.
    """
    _resolve_variable_backed_names(
        grouping,
        accept=lambda node, classification: (
            classification.request_role is not None
            and (node.call_site.method_name or "").lower() == "queryparams"
        ),
        container_variables=_single_container_variable,
        mutator_methods=_MULTIVALUEMAP_KEY_METHODS,
        extract_key=lambda _method_name, argument_exprs: _string_literal_argument(
            argument_exprs, 0
        ),
        target_names=lambda classification: classification.query_param_names,
    )


def _resolve_variable_backed_ambiguous_param_names(grouping: CallSiteGrouping) -> None:
    """Recover RestAssured `params(mapVar)` keys into the ambiguous-param bucket.

    Like `queryParams(mapVar)` but for the overloaded `params`/`params(Map)`, whose
    query-vs-form role is unknown until the verb is finalized. The recovered keys
    go into `rest_assured_ambiguous_param_names` so the same verb-driven
    normalization that handles literal `params(...)` resolves them.
    """
    _resolve_variable_backed_names(
        grouping,
        accept=lambda node, classification: (
            classification.framework == HttpDispatchFramework.REST_ASSURED
            and (node.call_site.method_name or "").lower() == "params"
        ),
        container_variables=_single_container_variable,
        mutator_methods=_MULTIVALUEMAP_KEY_METHODS,
        extract_key=lambda _method_name, argument_exprs: _string_literal_argument(
            argument_exprs, 0
        ),
        target_names=lambda classification: (
            classification.rest_assured_ambiguous_param_names
        ),
    )


def _single_variable_argument(argument_exprs: list[str]) -> str:
    """The sole argument when it is a bare identifier (a container variable)."""
    if len(argument_exprs) != 1:
        return ""
    match = _SINGLE_IDENTIFIER_ARGUMENT_RE.match(argument_exprs[0])
    return match.group(1) if match is not None else ""


def _single_container_variable(argument_exprs: list[str]) -> list[str]:
    variable = _single_variable_argument(argument_exprs)
    return [variable] if variable else []


def _header_container_key(method_name: str, argument_exprs: list[str]) -> str:
    if method_name.lower() in _MULTIVALUEMAP_KEY_METHODS:
        return _header_name_from_argument(argument_exprs, 0)
    return _TYPED_HEADER_SETTER_TO_NAME.get(method_name.lower(), "")


def _resolve_variable_backed_header_names(grouping: CallSiteGrouping) -> None:
    """Recover header names from a container variable passed to `.headers(var)`.

    The `.header("Name", ...)` form is named at the call site, but tests commonly
    build an HttpHeaders/MultiValueMap variable (`headers.add("Name", ...)`,
    `headers.setIfMatch(...)`) and pass it as `.headers(headers)`. This resolves
    those names from same-method population and appends them to the node's header
    names so correlation can merge them onto the request event. The request-role
    gate keeps it off response-side `.headers(...)` assertions.
    """
    _resolve_variable_backed_names(
        grouping,
        accept=lambda node, classification: (
            classification.request_role is not None
            and (node.call_site.method_name or "").lower() == "headers"
        ),
        container_variables=_single_container_variable,
        mutator_methods=_HEADER_CONTAINER_MUTATOR_METHODS,
        extract_key=_header_container_key,
        target_names=lambda classification: classification.header_names,
    )


def classify_http_on_grouping(
    grouping: CallSiteGrouping,
    *,
    owner: MethodRef,
    receiver_resolver: RuntimeReceiverResolver,
    parameter_values: Mapping[str, str | None] | None = None,
    static_base_prefixes: Mapping[str, _BasePrefixEvidence] | None = None,
) -> None:
    """Classify HTTP call sites and annotate nodes directly.

    Sets ``node.http_classification`` and ``node.endpoint_candidate`` on matching
    nodes in the grouping. ``parameter_values`` carries the owner's parameter
    names bound to caller-resolved values (None for statically unknown values)
    when the grouping is a helper expansion.
    """

    # Build a mapping from JCallSite identity to the grouping's CallSiteNode.
    call_site_id_to_node: dict[int, CallSiteNode] = {
        id(node.call_site): node for node in grouping.nodes
    }

    def _resolve_owner_expression(expression: str) -> str | None:
        return receiver_resolver.resolve_constant_expression(
            owner.defining_class_name, expression, local_values=parameter_values
        )

    # Run the same classification logic per call site, but annotate nodes.
    call_site_to_classification: dict[int, HttpClassification] = {}
    call_site_to_resolved_receiver: dict[int, ResolvedReceiver] = {}

    def _resolve_call_site_receiver(
        call_site: JCallSite,
    ) -> ResolvedReceiver:
        cached_receiver = call_site_to_resolved_receiver.get(id(call_site))
        if cached_receiver is not None:
            return cached_receiver

        resolved_receiver = receiver_resolver.resolve_for_event(owner, call_site)
        if resolved_receiver.receiver_type:
            call_site_to_resolved_receiver[id(call_site)] = resolved_receiver
            return resolved_receiver

        resolved_receiver = helper_return_receivers.get(
            id(call_site), resolved_receiver
        )
        call_site_to_resolved_receiver[id(call_site)] = resolved_receiver
        return resolved_receiver

    call_site_to_resolved_callee: dict[int, ResolvedCallee | None] = {}

    def _resolve_call_site_callee(call_site: JCallSite) -> ResolvedCallee | None:
        if id(call_site) not in call_site_to_resolved_callee:
            # Feed the resolved receiver type (e.g. a field/local recovered via
            # field_symbol) so declarative-client calls through injected fields,
            # where CLDK leaves call_site.receiver_type empty, still resolve.
            resolved_receiver_type = _resolve_call_site_receiver(
                call_site
            ).receiver_type
            call_site_to_resolved_callee[id(call_site)] = (
                receiver_resolver.resolve_callee(
                    call_site, resolved_receiver_type=resolved_receiver_type
                )
            )
        return call_site_to_resolved_callee[id(call_site)]

    call_sites_to_classify = [node.call_site for node in grouping.nodes]
    helper_return_receivers = _helper_return_receiver_overrides(
        grouping=grouping,
        receiver_resolver=receiver_resolver,
    )
    sorted_call_sites: list[JCallSite] = sorted(
        call_sites_to_classify,
        key=lambda cs: (int(cs.start_line), int(cs.start_column)),
    )

    for call_site in sorted_call_sites:
        node = call_site_id_to_node.get(id(call_site))
        if node is None:
            continue

        classification = _classify_call_site(
            receiver_resolver=_resolve_call_site_receiver,
            callee_resolver=_resolve_call_site_callee,
            call_site=call_site,
            resolve_expression=_resolve_owner_expression,
            resolve_class_expression=receiver_resolver.resolve_constant_expression,
        )
        if classification is None:
            continue
        _apply_mocking_context_if_present(
            grouping,
            node,
            classification,
            receiver_resolver=_resolve_call_site_receiver,
        )
        node.http_classification = classification
        call_site_to_classification[id(call_site)] = classification
        _sync_endpoint_candidate(node, classification)

    _infer_request_nodes_from_builder_evidence(
        grouping=grouping,
        call_site_to_classification=call_site_to_classification,
        receiver_resolver=_resolve_call_site_receiver,
        resolve_expression=_resolve_owner_expression,
    )

    # Resolve form/header/query field names off container variables before
    # correlation merges them onto request events.
    _resolve_variable_backed_form_names(grouping)
    _resolve_variable_backed_header_names(grouping)
    _resolve_variable_backed_query_names(grouping)
    _resolve_variable_backed_ambiguous_param_names(grouping)

    # Builder-chain correlation: merge builder properties onto request events.
    _correlate_builder_chains_on_nodes(
        grouping=grouping,
        call_site_to_classification=call_site_to_classification,
        method_details=receiver_resolver.method_details_for_owner(owner),
        static_base_prefixes=static_base_prefixes,
    )
    _recover_event_paths_from_builder_returning_helpers(
        grouping=grouping,
        call_site_to_classification=call_site_to_classification,
        receiver_resolver=receiver_resolver,
        resolve_expression=_resolve_owner_expression,
    )
    _recover_webtestclient_response_roles(
        grouping=grouping,
        call_site_to_classification=call_site_to_classification,
        receiver_resolver=_resolve_call_site_receiver,
    )
    _recover_rest_assured_response_roles(
        grouping=grouping,
        call_site_to_classification=call_site_to_classification,
        receiver_resolver=_resolve_call_site_receiver,
    )
    _recover_mockmvc_response_roles(
        grouping=grouping,
        call_site_to_classification=call_site_to_classification,
        receiver_resolver=_resolve_call_site_receiver,
    )
    _recover_citrus_http_roles(
        grouping=grouping,
        call_site_to_classification=call_site_to_classification,
        receiver_resolver=_resolve_call_site_receiver,
        resolve_expression=_resolve_owner_expression,
    )


def _is_okhttp_request_builder_receiver(receiver_type: str) -> bool:
    return receiver_type.replace("$", ".").endswith("Request.Builder")


def _apply_okhttp_verbless_builder_get_default(
    classification: HttpClassification,
    merged_okhttp_builders: list[tuple[str, str]],
) -> None:
    """Default a verb-less visible Request.Builder chain to GET.

    okhttp3.Request.Builder initializes its method to GET, so a chain that sets
    url(...) and never calls a verb method dispatches a GET at runtime. The
    default is suppressed when the chain selects the verb dynamically
    (method(...)) or copies it from another request (Request.newBuilder()).
    """
    if classification.framework != HttpDispatchFramework.OKHTTP:
        return
    if classification.http_method != "UNKNOWN":
        return

    saw_request_builder_url = False
    for method_name, receiver_type in merged_okhttp_builders:
        if _is_okhttp_request_builder_receiver(receiver_type):
            if method_name == "method":
                return
            if method_name == "url":
                saw_request_builder_url = True
        elif (
            method_name == "newBuilder"
            and _simple_class_name(receiver_type) == "Request"
        ):
            return

    if saw_request_builder_url:
        classification.http_method = "GET"


def _simple_identifier(value: str) -> str:
    match = _SINGLE_IDENTIFIER_ARGUMENT_RE.match(value)
    return match.group(1) if match is not None else ""


def _variable_declaration_covers_nodes(
    declaration: object,
    chain_nodes: list[CallSiteNode],
) -> bool:
    if not chain_nodes:
        return False
    if not str(getattr(declaration, "initializer", "") or "").strip():
        return False

    group_start = min(
        (node.span.start.line, node.span.start.col) for node in chain_nodes
    )
    group_end = max((node.span.end.line, node.span.end.col) for node in chain_nodes)
    declaration_start = (
        int(getattr(declaration, "start_line", -1)),
        int(getattr(declaration, "start_column", -1)),
    )
    declaration_end = (
        int(getattr(declaration, "end_line", -1)),
        int(getattr(declaration, "end_column", -1)),
    )
    if declaration_start[0] < 0 or declaration_end[0] < 0:
        return False
    return declaration_start <= group_start and group_end <= declaration_end


def _queued_builder_target_names(
    chain_nodes: list[CallSiteNode],
    method_details: JCallable | None,
) -> frozenset[str]:
    names: list[str] = []
    for chain_node in chain_nodes:
        receiver_name = _simple_identifier(chain_node.call_site.receiver_expr or "")
        if receiver_name:
            names.append(receiver_name)

    if method_details is not None:
        for declaration in method_details.variable_declarations or []:
            if not _variable_declaration_covers_nodes(declaration, chain_nodes):
                continue
            declaration_name = str(getattr(declaration, "name", "") or "").strip()
            if declaration_name:
                names.append(declaration_name)

    return frozenset(_dedupe_preserving_order(names))


def _queued_group_has_framework(
    group: _QueuedBuilderGroup,
    framework: HttpDispatchFramework,
) -> bool:
    return any(bclass.framework == framework for _, bclass in group.builders)


def _queued_group_has_rest_assured_base_prefix(
    group: _QueuedBuilderGroup,
) -> bool:
    return any(
        _is_rest_assured_base_prefix_builder(
            bnode.call_site.method_name or "",
            bclass,
        )
        for bnode, bclass in group.builders
    )


def _select_rest_assured_satisfied_event_queue_groups(
    event_node: CallSiteNode,
    builder_queue: list[_QueuedBuilderGroup],
) -> set[int]:
    rest_assured_indexes = [
        index
        for index, group in enumerate(builder_queue)
        if _queued_group_has_framework(group, HttpDispatchFramework.REST_ASSURED)
    ]
    if not rest_assured_indexes:
        return set()

    event_receiver_name = _simple_identifier(event_node.call_site.receiver_expr or "")
    if event_receiver_name:
        matched_indexes = {
            index
            for index in rest_assured_indexes
            if event_receiver_name in builder_queue[index].target_names
        }
        if matched_indexes:
            return matched_indexes
        if any(builder_queue[index].target_names for index in rest_assured_indexes):
            return set()

    unnamed_indexes = [
        index for index in rest_assured_indexes if not builder_queue[index].target_names
    ]
    if any(
        _queued_group_has_rest_assured_base_prefix(builder_queue[index])
        for index in unnamed_indexes
    ):
        return {unnamed_indexes[0]}

    return set()


def _correlate_builder_chains_on_nodes(
    grouping: CallSiteGrouping,
    call_site_to_classification: dict[int, HttpClassification],
    *,
    method_details: JCallable | None = None,
    static_base_prefixes: Mapping[str, _BasePrefixEvidence] | None = None,
) -> None:
    """Merge builder properties onto related request events on nodes.

    Handles both same-chain (fluent) and cross-chain (split builder/event) patterns.
    BUILDER-only chains are queued in source order and drained into the next
    unsatisfied EVENT chain.
    """

    if not call_site_to_classification:
        return

    builder_queue: list[_QueuedBuilderGroup] = []

    for chain_nodes in grouping.receiver_chains():
        # Classify chain nodes by request role.
        event_pairs: list[tuple[CallSiteNode, HttpClassification]] = []
        builder_pairs: list[tuple[CallSiteNode, HttpClassification]] = []
        for chain_node in chain_nodes:
            classification = call_site_to_classification.get(id(chain_node.call_site))
            if classification is None:
                continue
            if classification.request_role == HttpRequestRole.EVENT:
                event_pairs.append((chain_node, classification))
            elif classification.request_role == HttpRequestRole.BUILDER:
                builder_pairs.append((chain_node, classification))

        if event_pairs:
            for event_node, classification in event_pairs:
                # Merged okhttp builders are tracked independently of whether they
                # contributed properties, so verb-ambiguous builders stay visible
                # to the GET default.
                merged_okhttp_builders: list[tuple[str, str]] = []
                base_prefixes: dict[str, _BasePrefixEvidence] = {}

                # Compose the chain prefix's appended path segments before the
                # per-builder merge, which is first-wins and would otherwise
                # keep only one segment of a target(...).path(a).path(b) chain.
                _apply_composed_chain_path_to_event(
                    classification,
                    [
                        (chain_node, chain_classification)
                        for chain_node in _event_chain_prefix(grouping, event_node)
                        if (
                            chain_classification := call_site_to_classification.get(
                                id(chain_node.call_site)
                            )
                        )
                        is not None
                        and chain_classification.request_role == HttpRequestRole.BUILDER
                    ],
                )

                # Same-chain correlation: merge builders from within this chain.
                for builder_node in _builder_candidates_for_event(grouping, event_node):
                    builder_classification = call_site_to_classification.get(
                        id(builder_node.call_site)
                    )
                    if builder_classification is None:
                        continue
                    if builder_classification.request_role != HttpRequestRole.BUILDER:
                        continue
                    if not _builder_framework_matches_event_framework(
                        builder_classification.framework,
                        classification.framework,
                    ):
                        continue
                    _merge_builder_into_event(
                        classification,
                        builder_classification,
                        builder_method_name=builder_node.call_site.method_name or "",
                        builder_start_line=int(builder_node.call_site.start_line),
                    )
                    _record_base_prefix(
                        base_prefixes,
                        builder_node.call_site.method_name or "",
                        int(builder_node.call_site.start_line),
                        builder_classification,
                        overwrite=True,
                    )
                    if builder_classification.framework == HttpDispatchFramework.OKHTTP:
                        merged_okhttp_builders.append(
                            (
                                builder_node.call_site.method_name or "",
                                builder_classification.receiver_type,
                            )
                        )

                selected_rest_assured_queue_groups: set[int] = set()
                if classification.framework == HttpDispatchFramework.REST_ASSURED:
                    selected_rest_assured_queue_groups = (
                        _select_rest_assured_satisfied_event_queue_groups(
                            event_node,
                            builder_queue,
                        )
                    )

                drain_unsatisfied_event_queue = (
                    classification.http_method == "UNKNOWN"
                    or not (classification.path or base_prefixes)
                )

                # Cross-chain: drain queued builders when the event is still
                # unsatisfied (recorded base prefixes compose into a path later,
                # so they satisfy the path requirement here), or when a REST
                # Assured event can be tied to queued spec builders. Only
                # builders of the event's own framework belong to it; foreign
                # builders are retained for their own framework's event.
                if drain_unsatisfied_event_queue or selected_rest_assured_queue_groups:
                    retained_queue: list[_QueuedBuilderGroup] = []
                    for index, group in enumerate(builder_queue):
                        if (
                            not drain_unsatisfied_event_queue
                            and index not in selected_rest_assured_queue_groups
                        ):
                            retained_queue.append(group)
                            continue

                        # A queued group is a complete receiver chain, so its
                        # appended segments compose the same way.
                        _apply_composed_chain_path_to_event(
                            classification,
                            [
                                (bnode, bclass)
                                for bnode, bclass in group.builders
                                if _builder_framework_matches_event_framework(
                                    bclass.framework,
                                    classification.framework,
                                )
                            ],
                        )

                        retained_builders: list[
                            tuple[CallSiteNode, HttpClassification]
                        ] = []
                        for bnode, bclass in group.builders:
                            if not _builder_framework_matches_event_framework(
                                bclass.framework,
                                classification.framework,
                            ):
                                retained_builders.append((bnode, bclass))
                                continue
                            _merge_builder_into_event(
                                classification,
                                bclass,
                                builder_method_name=bnode.call_site.method_name or "",
                                builder_start_line=int(bnode.call_site.start_line),
                            )
                            _record_base_prefix(
                                base_prefixes,
                                bnode.call_site.method_name or "",
                                int(bnode.call_site.start_line),
                                bclass,
                                overwrite=False,
                            )
                            if bclass.framework == HttpDispatchFramework.OKHTTP:
                                merged_okhttp_builders.append(
                                    (
                                        bnode.call_site.method_name or "",
                                        bclass.receiver_type,
                                    )
                                )

                        if retained_builders:
                            retained_queue.append(
                                _QueuedBuilderGroup(
                                    builders=retained_builders,
                                    target_names=group.target_names,
                                )
                            )
                    builder_queue[:] = retained_queue

                _apply_okhttp_verbless_builder_get_default(
                    classification, merged_okhttp_builders
                )
                # Static config seeds only the gaps: a chain or queued-spec
                # setter recorded above replaces the static default, mirroring
                # the spec being seeded from the static fields at given().
                if (
                    static_base_prefixes
                    and classification.framework == HttpDispatchFramework.REST_ASSURED
                ):
                    for key, evidence in static_base_prefixes.items():
                        base_prefixes.setdefault(key, evidence)
                _compose_base_prefixes_into_event(classification, base_prefixes)
                _normalize_rest_assured_ambiguous_params(classification)
                _sync_endpoint_candidate(event_node, classification)
        elif builder_pairs:
            # BUILDER-only chain — queue for future cross-chain correlation.
            builder_queue.append(
                _QueuedBuilderGroup(
                    builders=builder_pairs,
                    target_names=_queued_builder_target_names(
                        chain_nodes,
                        method_details,
                    ),
                )
            )


def _query_only_event_argument(
    argument_exprs: list[str],
    resolve_expression: Callable[[str], str | None],
) -> str:
    """A query-only value (``"?key=value"``) that is an entire event argument.

    Scraping a query literal out of a partially resolved concatenation would
    drop the unresolved remainder (a path extension) with no truncation signal,
    so only whole-argument resolutions qualify.
    """
    for expression in argument_exprs:
        resolved = resolve_expression(expression)
        if resolved is None:
            continue
        value = resolved.strip()
        if value.startswith("?") and len(value) > 1:
            return value
    return ""


@dataclass(frozen=True)
class _HelperPathEvidence:
    helper_node: CallSiteNode
    helper_ref: MethodRef
    intervening_chain_nodes: tuple[CallSiteNode, ...]
    from_chain: bool


def _helper_path_evidence(
    grouping: CallSiteGrouping,
    event_node: CallSiteNode,
) -> list[_HelperPathEvidence]:
    """Helper calls that can carry path evidence for an event, nearest first:
    the event's receiver-chain prefix walked outward-in, then argument helpers."""
    chain_prefix = _event_chain_prefix(grouping, event_node)
    evidence: list[_HelperPathEvidence] = []
    for index in range(len(chain_prefix) - 1, -1, -1):
        helper_ref = chain_prefix[index].resolved_helper
        if helper_ref is None:
            continue
        evidence.append(
            _HelperPathEvidence(
                helper_node=chain_prefix[index],
                helper_ref=helper_ref,
                intervening_chain_nodes=tuple(chain_prefix[index + 1 :]),
                from_chain=True,
            )
        )
    for descendant in event_node.all_descendants():
        helper_ref = descendant.resolved_helper
        if helper_ref is None:
            continue
        evidence.append(
            _HelperPathEvidence(
                helper_node=descendant,
                helper_ref=helper_ref,
                intervening_chain_nodes=(),
                from_chain=False,
            )
        )
    return evidence


def _chain_extends_path_after_helper(
    intervening_chain_nodes: tuple[CallSiteNode, ...],
    call_site_to_classification: dict[int, HttpClassification],
) -> bool:
    """True when a builder between the helper and the event takes a path
    argument that failed extraction — the helper's path is then a prefix of
    the real request path, not the whole of it."""
    for chain_node in intervening_chain_nodes:
        chain_classification = call_site_to_classification.get(id(chain_node.call_site))
        if chain_classification is None:
            continue
        if chain_classification.request_role != HttpRequestRole.BUILDER:
            continue
        if chain_classification.path:
            continue
        if not (chain_node.call_site.argument_expr or []):
            continue
        if _positions_for_owner_family_method(
            _PATH_ARGUMENT_POSITIONS_BY_OWNER_FAMILY,
            chain_classification.owner_family,
            chain_node.call_site.method_name or "",
        ):
            return True
    return False


def _refresh_path_derived_param_names(classification: HttpClassification) -> None:
    """Mirror query/template names embedded in a recovered path onto the
    classification, matching what direct extraction derives."""
    for name in _extract_url_query_param_names(classification.path):
        if name not in classification.query_param_names:
            classification.query_param_names.append(name)
    for name in _extract_path_param_names(classification.path):
        if name not in classification.path_param_names:
            classification.path_param_names.append(name)


def _record_helper_path_recovery(
    node: CallSiteNode,
    classification: HttpClassification,
    evidence: _HelperPathEvidence,
    adopted_base: str,
) -> None:
    classification.correlated_builder_sources.append(
        BuilderCorrelationSource(
            method_name=evidence.helper_node.call_site.method_name or "",
            start_line=int(evidence.helper_node.call_site.start_line),
            framework=classification.framework,
            contributed_properties=["path"],
        )
    )
    _refresh_path_derived_param_names(classification)
    _sync_endpoint_candidate(node, classification)
    node.path_recovery = PathRecovery(
        helper_node=evidence.helper_node,
        adopted_base=adopted_base,
    )


def _recover_event_paths_from_builder_returning_helpers(
    grouping: CallSiteGrouping,
    call_site_to_classification: dict[int, HttpClassification],
    *,
    receiver_resolver: RuntimeReceiverResolver,
    resolve_expression: Callable[[str], str | None],
) -> None:
    """Adopt or compose a helper call's path argument for a request event.

    A helper whose return type is a registered request-construction receiver
    (``newRequest("/v2/...").request().get()``, ``given(BASE).when().get()``)
    received the request path or its base as its argument. A pathless event
    adopts the argument outright; an event with its own relative path composes
    the helper base in front of it, matching how these chains append at
    runtime (RequestSpecificationImpl base + path, WebTarget.path, karate
    Http.path). A query-only event argument (``get("?type=x")``) rides along
    unless the recovered path is incomplete.
    """
    for node in grouping.nodes:
        classification = call_site_to_classification.get(id(node.call_site))
        if classification is None:
            continue
        if classification.request_role != HttpRequestRole.EVENT:
            continue
        if classification.path.startswith(("http://", "https://")):
            continue

        for evidence in _helper_path_evidence(grouping, node):
            if classification.path and not evidence.from_chain:
                # Only a chain receiver's base precedes the event's own path.
                continue
            returned = receiver_resolver.resolve_helper_return_receiver(
                evidence.helper_ref
            )
            if not is_request_builder_receiver_type(returned.receiver_type):
                continue
            helper_call_site = evidence.helper_node.call_site
            # A user-chosen helper name must not inherit framework whitelist
            # leniency: a helper named `request` taking "application/json"
            # would otherwise adopt it as a relative path.
            extracted = _extract_path(
                [str(argument) for argument in (helper_call_site.argument_expr or [])],
                "",
                resolve_expression,
            )
            if not extracted.path:
                continue

            chain_extends = _chain_extends_path_after_helper(
                evidence.intervening_chain_nodes, call_site_to_classification
            )
            if classification.path:
                # Composing across a truncated base or an unextracted chain
                # extension would fabricate adjacency across unknown segments.
                if extracted.truncated or chain_extends:
                    break
                classification.path = _join_url_path_parts(
                    extracted.path, classification.path
                )
                _record_helper_path_recovery(
                    node, classification, evidence, extracted.path
                )
                break

            recovered_path = extracted.path
            truncated = extracted.truncated or chain_extends
            if not truncated and "?" not in recovered_path:
                recovered_path += _query_only_event_argument(
                    [
                        str(argument)
                        for argument in (node.call_site.argument_expr or [])
                    ],
                    resolve_expression,
                )
            classification.path = recovered_path
            classification.path_truncated = truncated
            _record_helper_path_recovery(node, classification, evidence, extracted.path)
            break


def _webtestclient_recovered_response_classification(
    node: CallSiteNode,
    *,
    response_role: HttpResponseRole,
    owner_family: str,
) -> HttpClassification:
    return HttpClassification(
        http_method="UNKNOWN",
        path="",
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        receiver_type=node.call_site.receiver_type or "",
        owner_family=owner_family,
        response_role=response_role,
    )


def _webtestclient_receiverless_response_state(
    node: CallSiteNode,
) -> tuple[HttpResponseRole | None, HttpResponseRole | None, bool]:
    method_name = node.call_site.method_name or ""
    if method_name == "expectStatus":
        return HttpResponseRole.MATCHER, HttpResponseRole.STATUS_ASSERTION, True
    if method_name in {"expectHeader", "expectCookie"}:
        return (
            HttpResponseRole.MATCHER,
            HttpResponseRole.HEADER_ASSERTION,
            True,
        )
    if method_name in {"expectBody", "expectBodyList"}:
        return (
            _webtestclient_expect_body_role(
                [str(argument) for argument in (node.call_site.argument_expr or [])]
            ),
            HttpResponseRole.BODY_ASSERTION,
            True,
        )
    if method_name in _WEBTESTCLIENT_RESPONSE_EXTRACTOR_METHODS:
        return HttpResponseRole.EXTRACTOR, None, True
    return None, None, False


def _webtestclient_active_role_after_existing_classification(
    node: CallSiteNode,
    *,
    classification: HttpClassification,
    active_response_role: HttpResponseRole | None,
) -> HttpResponseRole | None:
    _, subject_active_role, recognized_subject = (
        _webtestclient_receiverless_response_state(node)
    )
    if recognized_subject:
        return subject_active_role
    if classification.response_role == HttpResponseRole.EXTRACTOR:
        return None
    if classification.response_role == HttpResponseRole.MATCHER:
        return active_response_role
    return classification.response_role


def _recover_webtestclient_response_roles(
    grouping: CallSiteGrouping,
    call_site_to_classification: dict[int, HttpClassification],
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
) -> None:
    for chain_nodes in grouping.receiver_chains():
        seen_exchange_event = False
        active_response_role: HttpResponseRole | None = None

        for chain_node in chain_nodes:
            method_name = chain_node.call_site.method_name or ""
            classification = call_site_to_classification.get(id(chain_node.call_site))
            if (
                classification is not None
                and classification.framework == HttpDispatchFramework.WEBTESTCLIENT
                and classification.request_role == HttpRequestRole.EVENT
            ):
                seen_exchange_event = True
                active_response_role = None
                continue

            if not seen_exchange_event:
                continue
            if classification is not None and classification.response_role is not None:
                active_response_role = (
                    _webtestclient_active_role_after_existing_classification(
                        chain_node,
                        classification=classification,
                        active_response_role=active_response_role,
                    )
                )
                continue

            if not _is_receiverless(
                chain_node,
                receiver_resolver=receiver_resolver,
            ):
                active_response_role = None
                continue

            (
                recovered_role,
                subject_active_role,
                recognized_subject,
            ) = _webtestclient_receiverless_response_state(chain_node)
            owner_family = "webtestclient.response_role_recovery"
            if recognized_subject:
                active_response_role = subject_active_role
            elif (
                active_response_role == HttpResponseRole.BODY_ASSERTION
                and method_name in _WEBTESTCLIENT_BODY_MATCHER_METHODS
            ):
                recovered_role = HttpResponseRole.MATCHER
            elif (
                active_response_role is not None
                and method_name not in _WEBTESTCLIENT_RESPONSE_SUBJECT_METHODS
                and method_name not in _WEBTESTCLIENT_BODY_MATCHER_METHODS
            ):
                recovered_role = active_response_role

            if recovered_role is None:
                continue

            recovered_classification = _webtestclient_recovered_response_classification(
                chain_node,
                response_role=recovered_role,
                owner_family=owner_family,
            )
            chain_node.http_classification = recovered_classification
            call_site_to_classification[id(chain_node.call_site)] = (
                recovered_classification
            )


def _recovered_response_classification(
    node: CallSiteNode,
    *,
    framework: HttpDispatchFramework,
    response_role: HttpResponseRole,
    owner_family: str,
) -> HttpClassification:
    return HttpClassification(
        http_method="UNKNOWN",
        path="",
        framework=framework,
        receiver_type=node.call_site.receiver_type or "",
        owner_family=owner_family,
        response_role=response_role,
    )


def _annotate_recovered_response_role(
    node: CallSiteNode,
    call_site_to_classification: dict[int, HttpClassification],
    *,
    framework: HttpDispatchFramework,
    response_role: HttpResponseRole,
    owner_family: str,
) -> None:
    classification = _recovered_response_classification(
        node,
        framework=framework,
        response_role=response_role,
        owner_family=owner_family,
    )
    node.http_classification = classification
    call_site_to_classification[id(node.call_site)] = classification


def _is_receiverless(
    node: CallSiteNode,
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
) -> bool:
    if (node.call_site.receiver_type or "").strip():
        return False
    return not receiver_resolver(node.call_site).receiver_type.strip()


def _rest_assured_recovered_response_role(
    method_name: str,
    *,
    active_validation: bool,
    active_extractor: bool,
) -> tuple[HttpResponseRole | None, bool, bool]:
    if method_name in _REST_ASSURED_RESPONSE_INSPECTOR_METHODS:
        return HttpResponseRole.INSPECTOR, True, False
    if active_validation:
        if method_name in _REST_ASSURED_STATUS_ASSERTION_METHODS:
            return HttpResponseRole.STATUS_ASSERTION, True, False
        if method_name in _REST_ASSURED_BODY_ASSERTION_METHODS:
            return HttpResponseRole.BODY_ASSERTION, True, False
        if method_name in _REST_ASSURED_HEADER_ASSERTION_METHODS:
            return HttpResponseRole.HEADER_ASSERTION, True, False
        if method_name in _REST_ASSURED_EXTRACTOR_ROOT_METHODS:
            return HttpResponseRole.EXTRACTOR, False, True
    if active_extractor and method_name in _REST_ASSURED_EXTRACTOR_METHODS:
        return HttpResponseRole.EXTRACTOR, False, True
    return None, active_validation, active_extractor


def _rest_assured_response_state_after_existing_classification(
    node: CallSiteNode,
    *,
    classification: HttpClassification,
    active_validation: bool,
    active_extractor: bool,
) -> tuple[bool, bool]:
    method_name = node.call_site.method_name or ""
    if classification.response_role == HttpResponseRole.INSPECTOR:
        return (
            method_name in _REST_ASSURED_RESPONSE_INSPECTOR_METHODS,
            False,
        )
    if classification.response_role == HttpResponseRole.EXTRACTOR:
        return (
            False,
            method_name in _REST_ASSURED_EXTRACTOR_ROOT_METHODS
            or method_name in _REST_ASSURED_EXTRACTOR_METHODS,
        )
    if classification.response_role in {
        HttpResponseRole.STATUS_ASSERTION,
        HttpResponseRole.BODY_ASSERTION,
        HttpResponseRole.HEADER_ASSERTION,
    }:
        return True, False
    return active_validation, active_extractor


def _recover_rest_assured_response_roles(
    grouping: CallSiteGrouping,
    call_site_to_classification: dict[int, HttpClassification],
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
) -> None:
    for chain_nodes in grouping.receiver_chains():
        seen_request_event = False
        active_validation = False
        active_extractor = False

        for chain_node in chain_nodes:
            method_name = chain_node.call_site.method_name or ""
            classification = call_site_to_classification.get(id(chain_node.call_site))
            if (
                classification is not None
                and classification.framework == HttpDispatchFramework.REST_ASSURED
                and classification.request_role == HttpRequestRole.EVENT
            ):
                seen_request_event = True
                active_validation = False
                active_extractor = False
                continue

            if not seen_request_event:
                continue

            if (
                classification is not None
                and classification.framework == HttpDispatchFramework.REST_ASSURED
                and classification.response_role is not None
            ):
                active_validation, active_extractor = (
                    _rest_assured_response_state_after_existing_classification(
                        chain_node,
                        classification=classification,
                        active_validation=active_validation,
                        active_extractor=active_extractor,
                    )
                )
                continue

            if not _is_receiverless(
                chain_node,
                receiver_resolver=receiver_resolver,
            ):
                active_validation = False
                active_extractor = False
                continue

            (
                recovered_role,
                active_validation,
                active_extractor,
            ) = _rest_assured_recovered_response_role(
                method_name,
                active_validation=active_validation,
                active_extractor=active_extractor,
            )
            if recovered_role is None:
                continue

            _annotate_recovered_response_role(
                chain_node,
                call_site_to_classification,
                framework=HttpDispatchFramework.REST_ASSURED,
                response_role=recovered_role,
                owner_family="rest-assured.response_role_recovery",
            )


def _mockmvc_receiverless_response_role(method_name: str) -> HttpResponseRole | None:
    if method_name in _MOCKMVC_RESPONSE_INSPECTOR_METHODS:
        return HttpResponseRole.INSPECTOR
    if method_name in _MOCKMVC_RESPONSE_EXTRACTOR_METHODS:
        return HttpResponseRole.EXTRACTOR
    return None


def _mockmvc_argument_chains(node: CallSiteNode) -> list[list[CallSiteNode]]:
    argument_chains: list[list[CallSiteNode]] = []
    for argument_child in node.argument_children():
        argument_start = argument_child.span.start
        chain_nodes = [
            candidate
            for candidate in [argument_child, *argument_child.all_descendants()]
            if candidate.span.start == argument_start
        ]
        argument_chains.append(
            sorted(chain_nodes, key=lambda chain_node: chain_node.span.end)
        )
    return argument_chains


def _mockmvc_active_role_after_existing_classification(
    node: CallSiteNode,
    *,
    classification: HttpClassification,
    active_response_role: HttpResponseRole | None,
) -> HttpResponseRole | None:
    method_name = node.call_site.method_name or ""
    subject_role = _MOCKMVC_ACTIVE_RESPONSE_ROLE_BY_SUBJECT_METHOD.get(method_name)
    if subject_role is not None:
        return subject_role
    if classification.response_role == HttpResponseRole.EXTRACTOR:
        return None
    if classification.response_role == HttpResponseRole.MATCHER:
        return active_response_role
    return classification.response_role


def _recover_mockmvc_argument_response_roles(
    node: CallSiteNode,
    call_site_to_classification: dict[int, HttpClassification],
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
) -> None:
    for chain_nodes in _mockmvc_argument_chains(node):
        active_response_role: HttpResponseRole | None = None

        for chain_node in chain_nodes:
            method_name = chain_node.call_site.method_name or ""
            classification = call_site_to_classification.get(id(chain_node.call_site))
            if (
                classification is not None
                and classification.framework == HttpDispatchFramework.MOCKMVC
                and classification.response_role is not None
            ):
                active_response_role = (
                    _mockmvc_active_role_after_existing_classification(
                        chain_node,
                        classification=classification,
                        active_response_role=active_response_role,
                    )
                )
                continue

            if not _is_receiverless(
                chain_node,
                receiver_resolver=receiver_resolver,
            ):
                active_response_role = None
                continue

            subject_active_role = _MOCKMVC_ACTIVE_RESPONSE_ROLE_BY_SUBJECT_METHOD.get(
                method_name
            )
            root_response_role = _MOCKMVC_ROOT_RESPONSE_ROLE_BY_SUBJECT_METHOD.get(
                method_name
            )
            recovered_role: HttpResponseRole | None
            if root_response_role is not None and subject_active_role is not None:
                recovered_role = root_response_role
                active_response_role = subject_active_role
            else:
                recovered_role = active_response_role

            if recovered_role is None:
                continue

            _annotate_recovered_response_role(
                chain_node,
                call_site_to_classification,
                framework=HttpDispatchFramework.MOCKMVC,
                response_role=recovered_role,
                owner_family="mockmvc.response_role_recovery",
            )


def _recover_mockmvc_response_roles(
    grouping: CallSiteGrouping,
    call_site_to_classification: dict[int, HttpClassification],
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
) -> None:
    for chain_nodes in grouping.receiver_chains():
        seen_request_event = False

        for chain_node in chain_nodes:
            classification = call_site_to_classification.get(id(chain_node.call_site))
            if (
                classification is not None
                and classification.framework == HttpDispatchFramework.MOCKMVC
                and classification.request_role == HttpRequestRole.EVENT
            ):
                seen_request_event = True
                continue

            if not seen_request_event:
                continue

            if (
                classification is not None
                and classification.framework == HttpDispatchFramework.MOCKMVC
                and classification.response_role is not None
            ):
                if classification.response_role == HttpResponseRole.INSPECTOR:
                    _recover_mockmvc_argument_response_roles(
                        chain_node,
                        call_site_to_classification,
                        receiver_resolver=receiver_resolver,
                    )
                continue

            if not _is_receiverless(
                chain_node,
                receiver_resolver=receiver_resolver,
            ):
                continue

            recovered_role = _mockmvc_receiverless_response_role(
                chain_node.call_site.method_name or ""
            )
            if recovered_role is None:
                continue

            _annotate_recovered_response_role(
                chain_node,
                call_site_to_classification,
                framework=HttpDispatchFramework.MOCKMVC,
                response_role=recovered_role,
                owner_family="mockmvc.response_role_recovery",
            )
            if recovered_role == HttpResponseRole.INSPECTOR:
                _recover_mockmvc_argument_response_roles(
                    chain_node,
                    call_site_to_classification,
                    receiver_resolver=receiver_resolver,
                )


def _citrus_chain_kind(chain_nodes: list[CallSiteNode]) -> str | None:
    """Return ``"send"``/``"receive"`` if the chain is a Citrus HTTP action chain.

    The signature is an innermost receiverless ``http()`` root followed by a
    ``client(...)`` node and a ``send``/``receive`` action — distinctive enough
    to identify the org.citrusframework HTTP DSL without resolved receivers.
    """
    if not chain_nodes:
        return None
    root = chain_nodes[0]
    if (root.call_site.method_name or "") != _CITRUS_HTTP_ROOT_METHOD:
        return None
    if (root.call_site.receiver_expr or "").strip():
        return None
    method_names = {(node.call_site.method_name or "") for node in chain_nodes}
    if _CITRUS_CLIENT_METHOD not in method_names:
        return None
    if _CITRUS_RECEIVE_METHOD in method_names:
        return "receive"
    if _CITRUS_SEND_METHOD in method_names:
        return "send"
    return None


def _recover_citrus_send_chain(
    chain_nodes: list[CallSiteNode],
    call_site_to_classification: dict[int, HttpClassification],
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
    resolve_expression: Callable[[str], str | None] | None = None,
) -> None:
    verb_node: CallSiteNode | None = None
    send_node: CallSiteNode | None = None
    http_method = "UNKNOWN"
    path = ""
    path_truncated = False
    has_body = False
    query_param_names: list[str] = []
    header_names: list[str] = []

    for node in chain_nodes:
        method_name = node.call_site.method_name or ""
        argument_exprs = [str(arg) for arg in (node.call_site.argument_expr or [])]
        if method_name == _CITRUS_SEND_METHOD and send_node is None:
            send_node = node
        elif method_name in _CITRUS_VERB_HTTP_METHODS and verb_node is None:
            verb_node = node
            http_method = _CITRUS_VERB_HTTP_METHODS[method_name]
            verb_path = _extract_path(
                argument_exprs=argument_exprs,
                method_name=method_name,
                resolve_expression=resolve_expression,
            )
            if verb_path.path:
                path = verb_path.path
                path_truncated = verb_path.truncated
        elif method_name in _CITRUS_PATH_BUILDER_METHODS and not path:
            # The chain root is a verified citrus http().client() chain, so the
            # citrus.request single-segment allowance applies to path().
            builder_path = _extract_path(
                argument_exprs=argument_exprs,
                method_name=method_name,
                resolve_expression=resolve_expression,
                owner_family="citrus.request",
            )
            if builder_path.path:
                path = builder_path.path
                path_truncated = builder_path.truncated
        elif method_name in _CITRUS_QUERY_BUILDER_METHODS:
            query_param_names.extend(
                _extract_query_param_names(
                    method_name,
                    argument_exprs,
                    "",
                    owner_family="citrus.request",
                )
            )
        elif method_name in _CITRUS_HEADER_BUILDER_METHODS:
            header_names.extend(_extract_header_names(method_name, argument_exprs))
        elif method_name in _CITRUS_BODY_BUILDER_METHODS and any(
            arg.strip() for arg in argument_exprs
        ):
            has_body = True

    event_node = verb_node or send_node
    if event_node is None or event_node.http_classification is not None:
        return
    if not _is_receiverless(event_node, receiver_resolver=receiver_resolver):
        return

    classification = HttpClassification(
        http_method=http_method,
        path=path,
        framework=HttpDispatchFramework.CITRUS,
        path_truncated=path_truncated,
        owner_family="citrus.request_recovery",
        request_role=HttpRequestRole.EVENT,
        query_param_names=_dedupe_preserving_order(query_param_names),
        header_names=_dedupe_preserving_order(header_names),
        has_body_payload=has_body,
    )
    event_node.http_classification = classification
    call_site_to_classification[id(event_node.call_site)] = classification
    _sync_endpoint_candidate(event_node, classification)


def _recover_citrus_receive_chain(
    chain_nodes: list[CallSiteNode],
    call_site_to_classification: dict[int, HttpClassification],
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
) -> None:
    seen_receive = False
    for node in chain_nodes:
        method_name = node.call_site.method_name or ""
        if method_name == _CITRUS_RECEIVE_METHOD:
            seen_receive = True
            continue
        if not seen_receive:
            continue

        if method_name in _CITRUS_RESPONSE_STATUS_METHODS:
            response_role = HttpResponseRole.STATUS_ASSERTION
        elif method_name in _CITRUS_RESPONSE_HEADER_METHODS:
            response_role = HttpResponseRole.HEADER_ASSERTION
        elif method_name in _CITRUS_RESPONSE_BODY_METHODS:
            response_role = HttpResponseRole.BODY_ASSERTION
        else:
            continue

        if node.http_classification is not None:
            continue
        if not _is_receiverless(node, receiver_resolver=receiver_resolver):
            continue

        _annotate_recovered_response_role(
            node,
            call_site_to_classification,
            framework=HttpDispatchFramework.CITRUS,
            response_role=response_role,
            owner_family="citrus.response_role_recovery",
        )


def _recover_citrus_http_roles(
    grouping: CallSiteGrouping,
    call_site_to_classification: dict[int, HttpClassification],
    *,
    receiver_resolver: Callable[[JCallSite], ResolvedReceiver],
    resolve_expression: Callable[[str], str | None] | None = None,
) -> None:
    """Recover Citrus request events and response roles from receiverless chains.

    Citrus models a request (``send``) and its response check (``receive``) as
    separate fluent statements, so each is its own receiver chain; the send chain
    yields the request EVENT (verb + path) and the receive chain yields the
    status/body/header response roles consumed by Tier 1 assertion classification.
    """
    for chain_nodes in grouping.receiver_chains():
        kind = _citrus_chain_kind(chain_nodes)
        if kind == "send":
            _recover_citrus_send_chain(
                chain_nodes,
                call_site_to_classification,
                receiver_resolver=receiver_resolver,
                resolve_expression=resolve_expression,
            )
        elif kind == "receive":
            _recover_citrus_receive_chain(
                chain_nodes,
                call_site_to_classification,
                receiver_resolver=receiver_resolver,
            )


def classify_http_on_runtime_view(
    runtime_view: TestRuntimeView,
    receiver_resolver: RuntimeReceiverResolver,
) -> None:
    """Walk the runtime view and annotate nodes with HTTP classification.

    This is the single mutation pass for HTTP request-side classification.
    After this call, nodes with HTTP significance carry ``http_classification``
    and optionally ``endpoint_candidate`` attributes.
    """

    # Static RestAssured config is process-global state set up by the test
    # class lineage; one discovery covers every entry (fixtures, test body,
    # expanded helpers) of this test's execution.
    test_entry = runtime_view.test_entry() or (
        runtime_view.entries[0] if runtime_view.entries else None
    )
    static_base_prefixes = (
        _discover_rest_assured_static_base_prefixes(
            test_entry.context_class_name,
            receiver_resolver,
        )
        if test_entry is not None and test_entry.context_class_name
        else {}
    )

    for entry in runtime_view.entries:
        _classify_grouping_for_owner(
            grouping=entry.grouping,
            owner=entry.method_ref,
            receiver_resolver=receiver_resolver,
            static_base_prefixes=static_base_prefixes,
        )


_BINDABLE_PARAMETER_TYPES: Final[frozenset[str]] = frozenset(
    {"java.lang.String", "String"}
)

# Assignment to a name: simple `=` or a Java compound operator, excluding
# comparison (`==`; `<=`/`>=`/`!=` fail because their first char is not an
# assignment-operator char). The lookbehind excludes `.`-qualified field
# writes (`this.url = url`), which never rebind the parameter.
_ASSIGNMENT_TO_NAME_TEMPLATE: Final[str] = (
    r"(?<!\.)\b{name}\s*(?:[+\-*/%&|^]|<<|>>>?)?=(?!=)"
)


def _parameter_is_reassigned(parameter_name: str, helper_code: str) -> bool:
    pattern = _ASSIGNMENT_TO_NAME_TEMPLATE.format(name=re.escape(parameter_name))
    # Assignment-shaped text inside string literals ("?url=" + value) is data,
    # not a rebinding.
    return re.search(pattern, _QUOTED_STRING_RE.sub("", helper_code)) is not None


def _helper_parameter_values(
    helper_node: CallSiteNode,
    *,
    caller_owner: MethodRef,
    callee: MethodRef,
    receiver_resolver: RuntimeReceiverResolver,
    caller_parameter_values: Mapping[str, str | None] | None,
) -> dict[str, str | None]:
    """Bind the callee's parameter names to caller-resolved argument values.

    Every parameter name is bound: a parameter shadows any same-named field for
    the whole helper body per JLS scoping, so a parameter without a usable
    value binds to None, which poisons resolution instead of falling through to
    the shadowed field. Only String parameters whose argument fully resolves in
    the caller's context carry a value — and never when the helper body
    reassigns the parameter, since the call-site binding then no longer holds
    at the dispatch. Arity mismatches (varargs, overload ambiguity) bind every
    name to None.
    """
    callee_details = receiver_resolver.method_details_for_owner(callee)
    if callee_details is None:
        return {}
    parameters = callee_details.parameters or []
    parameter_names = [str(parameter.name or "") for parameter in parameters]
    bound: dict[str, str | None] = {name: None for name in parameter_names if name}
    argument_exprs = [
        str(argument) for argument in (helper_node.call_site.argument_expr or [])
    ]
    if len(parameters) != len(argument_exprs):
        return bound

    helper_code = str(callee_details.code or "")
    for parameter, parameter_name, argument_expr in zip(
        parameters, parameter_names, argument_exprs
    ):
        if not parameter_name or not argument_expr.strip():
            continue
        if str(parameter.type or "") not in _BINDABLE_PARAMETER_TYPES:
            continue
        if helper_code and _parameter_is_reassigned(parameter_name, helper_code):
            continue
        resolved = receiver_resolver.resolve_constant_expression(
            caller_owner.defining_class_name,
            argument_expr,
            local_values=caller_parameter_values,
        )
        if resolved is not None:
            bound[parameter_name] = resolved
    return bound


def _expansion_chain_path(
    expansion_grouping: CallSiteGrouping,
) -> tuple[str, bool] | None:
    """The single path an expansion's builders compute, or None when ambiguous.

    Path-bearing BUILDERs must all sit on one receiver chain: a lone builder
    carries its path directly, and a multi-step chain composes when its later
    members are registered appending methods.
    """
    chains_with_paths: list[list[tuple[CallSiteNode, HttpClassification]]] = []
    for chain_nodes in expansion_grouping.receiver_chains():
        path_bearing = [
            (chain_node, expanded)
            for chain_node in chain_nodes
            if (expanded := chain_node.http_classification) is not None
            and expanded.request_role == HttpRequestRole.BUILDER
            and expanded.path
        ]
        if path_bearing:
            chains_with_paths.append(path_bearing)
    if len(chains_with_paths) != 1:
        return None
    chain_path_builders = chains_with_paths[0]
    if len(chain_path_builders) == 1:
        only = chain_path_builders[0][1]
        return only.path, only.path_truncated
    composed = _compose_appending_chain_path(chain_path_builders)
    if composed is None:
        return None
    composed_path, composed_truncated, _ = composed
    return composed_path, composed_truncated


def _upgrade_recovered_paths_from_expansions(grouping: CallSiteGrouping) -> None:
    """Replace a recovered path's helper-argument base with the fuller path the
    helper's expansion computed (helper-internal constants + bound parameters).

    Applies only when the expansion's path-bearing BUILDERs yield a single
    unambiguous path ending with the adopted base — the deterministic signal
    that the helper argument flowed into that chain's path.
    """
    for node in grouping.nodes:
        recovery = node.path_recovery
        if recovery is None:
            continue
        classification = node.http_classification
        if classification is None or not classification.path:
            continue
        base = recovery.adopted_base
        if not base.startswith("/") or not classification.path.startswith(base):
            continue
        expansion = recovery.helper_node.helper_expansion
        if expansion is None:
            continue
        chain_path = _expansion_chain_path(expansion.grouping)
        if chain_path is None:
            continue
        expansion_path, expansion_truncated = chain_path
        if expansion_path == base or not expansion_path.endswith(base):
            continue
        remainder = classification.path[len(base) :]
        # A truncated expansion path followed by more path would fabricate
        # adjacency across the statically unknown appended value.
        if expansion_truncated and remainder:
            continue
        classification.path = expansion_path + remainder
        classification.path_truncated = classification.path_truncated or (
            expansion_truncated
        )
        _refresh_path_derived_param_names(classification)
        _sync_endpoint_candidate(node, classification)


def _classify_grouping_for_owner(
    grouping: CallSiteGrouping,
    owner: MethodRef,
    receiver_resolver: RuntimeReceiverResolver,
    parameter_values: Mapping[str, str | None] | None = None,
    static_base_prefixes: Mapping[str, _BasePrefixEvidence] | None = None,
) -> None:
    """Recursively classify a grouping and its helper expansions."""

    classify_http_on_grouping(
        grouping=grouping,
        owner=owner,
        receiver_resolver=receiver_resolver,
        parameter_values=parameter_values,
        static_base_prefixes=static_base_prefixes,
    )

    # Runtime grouping expansion is canonical and acyclic, so classification
    # must visit every grouping instance to annotate node-local state; each
    # expansion instance gets its own call-site argument bindings.
    for node in grouping.nodes:
        if node.helper_expansion is not None:
            _classify_grouping_for_owner(
                grouping=node.helper_expansion.grouping,
                owner=node.helper_expansion.callee,
                receiver_resolver=receiver_resolver,
                parameter_values=_helper_parameter_values(
                    node,
                    caller_owner=owner,
                    callee=node.helper_expansion.callee,
                    receiver_resolver=receiver_resolver,
                    caller_parameter_values=parameter_values,
                ),
                static_base_prefixes=static_base_prefixes,
            )

    _upgrade_recovered_paths_from_expansions(grouping)


def build_output_http_request_interactions(
    runtime_view: TestRuntimeView,
) -> list[HttpRequestInteraction]:
    """Convert annotated nodes to output schema objects.

    Called after ``classify_http_on_runtime_view`` to produce the serializable
    request-side interaction list for ``HttpAnalysis.request_interactions``.
    """

    interactions: list[HttpRequestInteraction] = []

    for event in runtime_view.iter_events():
        interaction = build_http_request_interaction_for_event(event)
        if interaction is not None:
            interactions.append(interaction)

    return interactions


def build_http_request_interaction_for_event(
    event: RuntimeEvent,
) -> HttpRequestInteraction | None:
    classification = event.node.http_classification
    if classification is None:
        return None
    if classification.request_role is None:
        return None

    call_site = event.node.call_site
    return HttpRequestInteraction(
        origin=event.origin_context,
        http_call=HttpCallSite(
            http_method=classification.http_method,
            path=classification.path,
            framework=classification.framework,
            request_role=classification.request_role,
            method_name=call_site.method_name or "",
            receiver_type=classification.receiver_type,
            callee_signature=call_site.callee_signature or "",
            start_line=int(call_site.start_line),
            headers=list(classification.headers),
            header_names=list(classification.header_names),
            query_param_names=list(classification.query_param_names),
            path_param_names=list(classification.path_param_names),
            form_param_names=list(classification.form_param_names),
            has_body_payload=classification.has_body_payload,
            auth_hints=list(classification.auth_hints),
            correlated_builder_sources=list(classification.correlated_builder_sources),
        ),
        endpoint_candidate=event.node.endpoint_candidate,
    )


def build_output_http_mocked_interactions(
    runtime_view: TestRuntimeView,
) -> list[HttpMockedInteraction]:
    """Convert mocked HTTP-shaped call sites to output schema objects.

    Mocked interactions are kept separate from request interactions because they
    are evidence of mocked collaborators, not request dispatch.
    """

    interactions: list[HttpMockedInteraction] = []

    for event in runtime_view.iter_events():
        classification = event.node.http_classification
        if classification is None or classification.mocking_context is None:
            continue

        call_site = event.node.call_site
        interactions.append(
            HttpMockedInteraction(
                origin=event.origin_context,
                http_call=HttpMockedCallSite(
                    http_method=classification.http_method,
                    path=classification.path,
                    framework=classification.framework,
                    method_name=call_site.method_name or "",
                    receiver_type=classification.receiver_type,
                    callee_signature=call_site.callee_signature or "",
                    start_line=int(call_site.start_line),
                    mocking_context=classification.mocking_context,
                ),
            )
        )

    return interactions
