"""Node-level assertion classification pass.

Mirrors the HTTP classification pattern: a single pass annotates
``CallSiteNode.assertion_classification`` so downstream consumers
read cheaply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cldk.models.java.models import JCallSite

from gerbil.analysis.http.framework_registry import matches_receiver_prefix
from gerbil.analysis.runtime.call_sites import (
    CallSiteGrouping,
    CallSiteNode,
    MethodRef,
)
from gerbil.analysis.runtime import TestRuntimeView
from gerbil.analysis.shared.constants import (
    FAILURE_EXCEPTION_HINTS,
)
from gerbil.analysis.shared.receiver_resolution import RuntimeReceiverResolver
from gerbil.analysis.schema import (
    AssertionClassification,
    AssertionNodeKind,
    AssertionRole,
    HttpResponseRole,
)

# ── Constants ────────────────────────────────────────────────────────

# Mirrors Spring's StatusResultMatchers/StatusAssertions methods, themselves
# generated from the HttpStatus enum.
_STATUS_METHOD_CODE_HINTS: dict[str, int] = {
    "isContinue": 100,
    "isSwitchingProtocols": 101,
    "isProcessing": 102,
    "isOk": 200,
    "isCreated": 201,
    "isAccepted": 202,
    "isNonAuthoritativeInformation": 203,
    "isNoContent": 204,
    "isResetContent": 205,
    "isPartialContent": 206,
    "isMultiStatus": 207,
    "isMultipleChoices": 300,
    "isMovedPermanently": 301,
    "isFound": 302,
    "isSeeOther": 303,
    "isNotModified": 304,
    "isUseProxy": 305,
    "isTemporaryRedirect": 307,
    "isPermanentRedirect": 308,
    "isBadRequest": 400,
    "isUnauthorized": 401,
    "isPaymentRequired": 402,
    "isForbidden": 403,
    "isNotFound": 404,
    "isMethodNotAllowed": 405,
    "isNotAcceptable": 406,
    "isProxyAuthenticationRequired": 407,
    "isRequestTimeout": 408,
    "isConflict": 409,
    "isGone": 410,
    "isLengthRequired": 411,
    "isPreconditionFailed": 412,
    "isPayloadTooLarge": 413,
    "isUriTooLong": 414,
    "isUnsupportedMediaType": 415,
    "isRequestedRangeNotSatisfiable": 416,
    "isExpectationFailed": 417,
    "isIAmATeapot": 418,
    "isUnprocessableEntity": 422,
    "isLocked": 423,
    "isFailedDependency": 424,
    "isTooEarly": 425,
    "isUpgradeRequired": 426,
    "isPreconditionRequired": 428,
    "isTooManyRequests": 429,
    "isRequestHeaderFieldsTooLarge": 431,
    "isUnavailableForLegalReasons": 451,
    "isInternalServerError": 500,
    "isNotImplemented": 501,
    "isBadGateway": 502,
    "isServiceUnavailable": 503,
    "isGatewayTimeout": 504,
    "isHttpVersionNotSupported": 505,
    "isVariantAlsoNegotiates": 506,
    "isInsufficientStorage": 507,
    "isLoopDetected": 508,
    "isBandwidthLimitExceeded": 509,
    "isNotExtended": 510,
    "isNetworkAuthenticationRequired": 511,
}

_STATUS_SUBJECT_METHOD_HINTS: set[str] = {
    "status",
    "statusCode",
    "statusCodeValue",
    "getStatus",
    "getStatusCode",
    "getStatusCodeValue",
    "getResponseCode",
}

_AMBIGUOUS_STATUS_SUBJECT_METHODS: set[str] = {
    "status",
}

_BODY_SUBJECT_METHOD_HINTS: set[str] = {
    "body",
    "content",
    "responseBody",
    "getBody",
    "getContentAsString",
    "asString",
    "jsonPath",
    "path",
    "readEntity",
}

_EXCEPTION_ROOT_METHODS: set[str] = FAILURE_EXCEPTION_HINTS - {"fail"}

# Receiver roots that identify a call as belonging to a known assertion framework.
_ASSERTION_FRAMEWORK_RECEIVER_PREFIXES: tuple[str, ...] = (
    "org.junit.",
    "org.testng.",
    "org.assertj.",
    "junit.framework.",
)

_STATUS_METHOD_RANGE_HINTS: dict[str, str] = {
    "is2xxSuccessful": "2xx",
    "is3xxRedirection": "3xx",
    "is4xxClientError": "4xx",
    "is5xxServerError": "5xx",
}

_HTTPSTATUS_CONSTANT_CODES: dict[str, int] = {
    "CONTINUE": 100,
    "OK": 200,
    "CREATED": 201,
    "ACCEPTED": 202,
    "NO_CONTENT": 204,
    "RESET_CONTENT": 205,
    "PARTIAL_CONTENT": 206,
    "MULTIPLE_CHOICES": 300,
    "MOVED_PERMANENTLY": 301,
    "FOUND": 302,
    "SEE_OTHER": 303,
    "NOT_MODIFIED": 304,
    "TEMPORARY_REDIRECT": 307,
    "PERMANENT_REDIRECT": 308,
    "BAD_REQUEST": 400,
    "UNAUTHORIZED": 401,
    "PAYMENT_REQUIRED": 402,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "NOT_ACCEPTABLE": 406,
    "PROXY_AUTHENTICATION_REQUIRED": 407,
    "REQUEST_TIMEOUT": 408,
    "CONFLICT": 409,
    "GONE": 410,
    "LENGTH_REQUIRED": 411,
    "PRECONDITION_FAILED": 412,
    "PAYLOAD_TOO_LARGE": 413,
    "URI_TOO_LONG": 414,
    "UNSUPPORTED_MEDIA_TYPE": 415,
    "REQUESTED_RANGE_NOT_SATISFIABLE": 416,
    "EXPECTATION_FAILED": 417,
    "I_AM_A_TEAPOT": 418,
    "UNPROCESSABLE_ENTITY": 422,
    "LOCKED": 423,
    "FAILED_DEPENDENCY": 424,
    "UPGRADE_REQUIRED": 426,
    "PRECONDITION_REQUIRED": 428,
    "TOO_MANY_REQUESTS": 429,
    "REQUEST_HEADER_FIELDS_TOO_LARGE": 431,
    "UNAVAILABLE_FOR_LEGAL_REASONS": 451,
    "INTERNAL_SERVER_ERROR": 500,
    "NOT_IMPLEMENTED": 501,
    "BAD_GATEWAY": 502,
    "SERVICE_UNAVAILABLE": 503,
    "GATEWAY_TIMEOUT": 504,
    "HTTP_VERSION_NOT_SUPPORTED": 505,
    "INSUFFICIENT_STORAGE": 507,
    "NETWORK_AUTHENTICATION_REQUIRED": 511,
}

# Verified against jakarta.ws.rs.core.Response.Status (jakartaee/rest); several
# names differ from Spring (e.g. REQUEST_ENTITY_TOO_LARGE, REQUEST_URI_TOO_LONG).
_JAXRS_RESPONSE_STATUS_CONSTANT_CODES: dict[str, int] = {
    "OK": 200,
    "CREATED": 201,
    "ACCEPTED": 202,
    "NO_CONTENT": 204,
    "RESET_CONTENT": 205,
    "PARTIAL_CONTENT": 206,
    "MULTIPLE_CHOICES": 300,
    "MOVED_PERMANENTLY": 301,
    "FOUND": 302,
    "SEE_OTHER": 303,
    "NOT_MODIFIED": 304,
    "USE_PROXY": 305,
    "TEMPORARY_REDIRECT": 307,
    "PERMANENT_REDIRECT": 308,
    "BAD_REQUEST": 400,
    "UNAUTHORIZED": 401,
    "PAYMENT_REQUIRED": 402,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "NOT_ACCEPTABLE": 406,
    "PROXY_AUTHENTICATION_REQUIRED": 407,
    "REQUEST_TIMEOUT": 408,
    "CONFLICT": 409,
    "GONE": 410,
    "LENGTH_REQUIRED": 411,
    "PRECONDITION_FAILED": 412,
    "REQUEST_ENTITY_TOO_LARGE": 413,
    "REQUEST_URI_TOO_LONG": 414,
    "UNSUPPORTED_MEDIA_TYPE": 415,
    "REQUESTED_RANGE_NOT_SATISFIABLE": 416,
    "EXPECTATION_FAILED": 417,
    "PRECONDITION_REQUIRED": 428,
    "TOO_MANY_REQUESTS": 429,
    "REQUEST_HEADER_FIELDS_TOO_LARGE": 431,
    "UNAVAILABLE_FOR_LEGAL_REASONS": 451,
    "INTERNAL_SERVER_ERROR": 500,
    "NOT_IMPLEMENTED": 501,
    "BAD_GATEWAY": 502,
    "SERVICE_UNAVAILABLE": 503,
    "GATEWAY_TIMEOUT": 504,
    "HTTP_VERSION_NOT_SUPPORTED": 505,
    "NETWORK_AUTHENTICATION_REQUIRED": 511,
}

# Union of org.apache.http.HttpStatus (4.x) and org.apache.hc.core5.http.HttpStatus
# code constants; core5 range markers (SC_SUCCESS, SC_CLIENT_ERROR, ...) are
# excluded because they label ranges, not asserted codes.
_APACHE_HTTPSTATUS_CONSTANT_CODES: dict[str, int] = {
    "SC_CONTINUE": 100,
    "SC_SWITCHING_PROTOCOLS": 101,
    "SC_PROCESSING": 102,
    "SC_EARLY_HINTS": 103,
    "SC_OK": 200,
    "SC_CREATED": 201,
    "SC_ACCEPTED": 202,
    "SC_NON_AUTHORITATIVE_INFORMATION": 203,
    "SC_NO_CONTENT": 204,
    "SC_RESET_CONTENT": 205,
    "SC_PARTIAL_CONTENT": 206,
    "SC_MULTI_STATUS": 207,
    "SC_ALREADY_REPORTED": 208,
    "SC_IM_USED": 226,
    "SC_MULTIPLE_CHOICES": 300,
    "SC_MOVED_PERMANENTLY": 301,
    "SC_MOVED_TEMPORARILY": 302,
    "SC_SEE_OTHER": 303,
    "SC_NOT_MODIFIED": 304,
    "SC_USE_PROXY": 305,
    "SC_TEMPORARY_REDIRECT": 307,
    "SC_PERMANENT_REDIRECT": 308,
    "SC_BAD_REQUEST": 400,
    "SC_UNAUTHORIZED": 401,
    "SC_PAYMENT_REQUIRED": 402,
    "SC_FORBIDDEN": 403,
    "SC_NOT_FOUND": 404,
    "SC_METHOD_NOT_ALLOWED": 405,
    "SC_NOT_ACCEPTABLE": 406,
    "SC_PROXY_AUTHENTICATION_REQUIRED": 407,
    "SC_REQUEST_TIMEOUT": 408,
    "SC_CONFLICT": 409,
    "SC_GONE": 410,
    "SC_LENGTH_REQUIRED": 411,
    "SC_PRECONDITION_FAILED": 412,
    "SC_REQUEST_TOO_LONG": 413,
    "SC_REQUEST_URI_TOO_LONG": 414,
    "SC_UNSUPPORTED_MEDIA_TYPE": 415,
    "SC_REQUESTED_RANGE_NOT_SATISFIABLE": 416,
    "SC_EXPECTATION_FAILED": 417,
    "SC_INSUFFICIENT_SPACE_ON_RESOURCE": 419,
    "SC_METHOD_FAILURE": 420,
    "SC_MISDIRECTED_REQUEST": 421,
    "SC_UNPROCESSABLE_CONTENT": 422,
    "SC_UNPROCESSABLE_ENTITY": 422,
    "SC_LOCKED": 423,
    "SC_FAILED_DEPENDENCY": 424,
    "SC_TOO_EARLY": 425,
    "SC_UPGRADE_REQUIRED": 426,
    "SC_PRECONDITION_REQUIRED": 428,
    "SC_TOO_MANY_REQUESTS": 429,
    "SC_REQUEST_HEADER_FIELDS_TOO_LARGE": 431,
    "SC_UNAVAILABLE_FOR_LEGAL_REASONS": 451,
    "SC_INTERNAL_SERVER_ERROR": 500,
    "SC_NOT_IMPLEMENTED": 501,
    "SC_BAD_GATEWAY": 502,
    "SC_SERVICE_UNAVAILABLE": 503,
    "SC_GATEWAY_TIMEOUT": 504,
    "SC_HTTP_VERSION_NOT_SUPPORTED": 505,
    "SC_VARIANT_ALSO_NEGOTIATES": 506,
    "SC_INSUFFICIENT_STORAGE": 507,
    "SC_LOOP_DETECTED": 508,
    "SC_NOT_EXTENDED": 510,
    "SC_NETWORK_AUTHENTICATION_REQUIRED": 511,
}

# Verified against java.net.HttpURLConnection (Java SE 21), including the
# deprecated HTTP_SERVER_ERROR alias for 500.
_HTTPURLCONNECTION_CONSTANT_CODES: dict[str, int] = {
    "HTTP_OK": 200,
    "HTTP_CREATED": 201,
    "HTTP_ACCEPTED": 202,
    "HTTP_NOT_AUTHORITATIVE": 203,
    "HTTP_NO_CONTENT": 204,
    "HTTP_RESET": 205,
    "HTTP_PARTIAL": 206,
    "HTTP_MULT_CHOICE": 300,
    "HTTP_MOVED_PERM": 301,
    "HTTP_MOVED_TEMP": 302,
    "HTTP_SEE_OTHER": 303,
    "HTTP_NOT_MODIFIED": 304,
    "HTTP_USE_PROXY": 305,
    "HTTP_BAD_REQUEST": 400,
    "HTTP_UNAUTHORIZED": 401,
    "HTTP_PAYMENT_REQUIRED": 402,
    "HTTP_FORBIDDEN": 403,
    "HTTP_NOT_FOUND": 404,
    "HTTP_BAD_METHOD": 405,
    "HTTP_NOT_ACCEPTABLE": 406,
    "HTTP_PROXY_AUTH": 407,
    "HTTP_CLIENT_TIMEOUT": 408,
    "HTTP_CONFLICT": 409,
    "HTTP_GONE": 410,
    "HTTP_LENGTH_REQUIRED": 411,
    "HTTP_PRECON_FAILED": 412,
    "HTTP_ENTITY_TOO_LARGE": 413,
    "HTTP_REQ_TOO_LONG": 414,
    "HTTP_UNSUPPORTED_TYPE": 415,
    "HTTP_INTERNAL_ERROR": 500,
    "HTTP_SERVER_ERROR": 500,
    "HTTP_NOT_IMPLEMENTED": 501,
    "HTTP_BAD_GATEWAY": 502,
    "HTTP_UNAVAILABLE": 503,
    "HTTP_GATEWAY_TIMEOUT": 504,
    "HTTP_VERSION": 505,
}

_STATUS_CATEGORY_METHODS: set[str] = {
    *_STATUS_METHOD_CODE_HINTS,
    *_STATUS_METHOD_RANGE_HINTS,
    "statusCode",
}

_ASSERTJ_STATUS_METHOD_CODE_HINTS: dict[str, int] = {
    "hasStatusOk": 200,
}

_ASSERTJ_STATUS_METHOD_RANGE_HINTS: dict[str, str] = {
    "hasStatus1xxInformational": "1xx",
    "hasStatus2xxSuccessful": "2xx",
    "hasStatus3xxRedirection": "3xx",
    "hasStatus4xxClientError": "4xx",
    "hasStatus5xxServerError": "5xx",
}

# Equality matchers assert the argument code itself; negation matchers verify
# status but must not record the negated code as the asserted status.
_STATUS_VALUE_EQUALITY_MATCHER_METHODS: set[str] = {
    "equalTo",
    "is",
    "isEqualTo",
}

_STATUS_VALUE_NEGATION_MATCHER_METHODS: set[str] = {
    "isNotEqualTo",
}

_STATUS_VALUE_MATCHER_METHODS: set[str] = (
    _STATUS_VALUE_EQUALITY_MATCHER_METHODS | _STATUS_VALUE_NEGATION_MATCHER_METHODS
)

_INTEGER_LITERAL_RE = re.compile(r"^\d{3}$")
_HTTPSTATUS_QUALIFIED_RE = re.compile(r"\bHttpStatus\.([A-Z][A-Z_]+)")
# Bare SC_*/HTTP_* forms (static imports) match only as the whole expression so
# lookalike qualifiers (MyConstants.HTTP_OK) and string literals cannot resolve.
# Import evidence is deliberately not required for bare forms or the short
# HttpStatus qualifier: static imports carry no qualifier to inspect, so an
# exact-name domain collision (a local SC_OK, com.acme.HttpStatus.OK) still
# resolves through the curated name->code maps.
_APACHE_SC_QUALIFIED_RE = re.compile(r"\bHttpStatus\.(SC_[A-Z_]+)")
_APACHE_SC_BARE_RE = re.compile(r"^(SC_[A-Z_]+)$")
_JAXRS_RESPONSE_QUALIFIED_RE = re.compile(r"\bResponse\.Status\.([A-Z][A-Z_]+)")
_HTTPURLCONNECTION_QUALIFIED_RE = re.compile(r"\bHttpURLConnection\.(HTTP_[A-Z_]+)")
_HTTPURLCONNECTION_BARE_RE = re.compile(r"^(HTTP_[A-Z_]+)$")
# `Status` is a common domain-enum name, so the bare-qualified JAX-RS form
# resolves only with CLDK argument-type evidence for the Response.Status enum.
_JAXRS_BARE_STATUS_RE = re.compile(r"^Status\.([A-Z][A-Z_]+)")
_JAXRS_STATUS_ARGUMENT_TYPES: frozenset[str] = frozenset(
    {
        "javax.ws.rs.core.Response.Status",
        "jakarta.ws.rs.core.Response.Status",
    }
)
# A fully bare SCREAMING_SNAKE constant (a statically-imported HttpStatus.NOT_FOUND
# or Response.Status.NOT_FOUND). Such a name is indistinguishable from a domain
# constant, so it resolves only when CLDK types the argument as the enum.
_BARE_STATUS_CONSTANT_RE = re.compile(r"^([A-Z][A-Z0-9_]+)$")
_SPRING_HTTP_STATUS_ARGUMENT_TYPES: frozenset[str] = frozenset(
    {
        "org.springframework.http.HttpStatus",
        "org.springframework.http.HttpStatusCode",
    }
)
# Status matchers wrapping the asserted code, e.g. `statusCode(equalTo(404))` /
# `statusCode(is(SC_NOT_FOUND))`. The inner expression carries the actual code.
_STATUS_MATCHER_WRAPPER_RE = re.compile(r"^(?:equalTo|is|isEqualTo)\s*\(\s*(.+?)\s*\)$")

_HTTP_STATUS_ARGUMENT_TYPES: frozenset[str] = frozenset(
    {
        "int",
        "java.lang.Integer",
        "long",
        "java.lang.Long",
        "org.springframework.http.HttpStatus",
        "org.springframework.http.HttpStatusCode",
        "javax.ws.rs.core.Response.Status",
        "jakarta.ws.rs.core.Response.Status",
        "org.apache.http.StatusLine",
        "org.apache.hc.core5.http.StatusLine",
    }
)

# Apache's status class is also named HttpStatus, so its SC_-prefixed names are
# resolved through the SC_ map first instead of failing through the Spring map.
_STATUS_CONSTANT_EXTRACTORS: tuple[tuple[re.Pattern[str], dict[str, int]], ...] = (
    (_APACHE_SC_QUALIFIED_RE, _APACHE_HTTPSTATUS_CONSTANT_CODES),
    (_APACHE_SC_BARE_RE, _APACHE_HTTPSTATUS_CONSTANT_CODES),
    (_HTTPSTATUS_QUALIFIED_RE, _HTTPSTATUS_CONSTANT_CODES),
    (_JAXRS_RESPONSE_QUALIFIED_RE, _JAXRS_RESPONSE_STATUS_CONSTANT_CODES),
    (_HTTPURLCONNECTION_QUALIFIED_RE, _HTTPURLCONNECTION_CONSTANT_CODES),
    (_HTTPURLCONNECTION_BARE_RE, _HTTPURLCONNECTION_CONSTANT_CODES),
)


def status_range_from_code(code: int) -> str:
    return f"{code // 100}xx"


def _argument_type_is_jaxrs_status(
    argument_types: list[str] | None,
    index: int,
) -> bool:
    if not argument_types or index >= len(argument_types):
        return False
    # CLDK may spell the nested enum with its binary name.
    return (argument_types[index] or "").replace(
        "$", "."
    ) in _JAXRS_STATUS_ARGUMENT_TYPES


_JDK_PRIMITIVE_TYPES: frozenset[str] = frozenset(
    {
        "boolean",
        "byte",
        "char",
        "double",
        "float",
        "int",
        "long",
        "short",
        "void",
    }
)


def _is_domain_type(type_name: str) -> bool:
    if not type_name:
        return False
    if type_name in _JDK_PRIMITIVE_TYPES:
        return False
    return not type_name.startswith(("java.", "javax.", "jakarta.", "com.sun.", "sun."))


def _argument_types_indicate_domain_status(
    argument_types: list[str] | None,
) -> bool:
    if not argument_types:
        return False
    resolved = [t for t in argument_types if t]
    if not resolved:
        return False
    if any(t in _HTTP_STATUS_ARGUMENT_TYPES for t in resolved):
        return False
    return any(_is_domain_type(t) for t in resolved)


def _extract_status_code_from_arguments(
    argument_exprs: list[str],
    argument_types: list[str] | None = None,
) -> int | None:
    for index, expr in enumerate(argument_exprs):
        stripped = expr.strip()
        if stripped.startswith('"'):
            continue
        # Unwrap a single Hamcrest equality matcher so the inner code resolves
        # through the same literal/constant handling as a bare argument.
        matcher_inner = _STATUS_MATCHER_WRAPPER_RE.match(stripped)
        if matcher_inner is not None:
            stripped = matcher_inner.group(1).strip()
        if _INTEGER_LITERAL_RE.match(stripped):
            code = int(stripped)
            if 100 <= code <= 599:
                return code
        for pattern, constant_codes in _STATUS_CONSTANT_EXTRACTORS:
            match = pattern.search(stripped)
            if match:
                status_code = constant_codes.get(match.group(1))
                if status_code is not None:
                    return status_code
        bare_status_match = _JAXRS_BARE_STATUS_RE.match(stripped)
        if bare_status_match and _argument_type_is_jaxrs_status(argument_types, index):
            status_code = _JAXRS_RESPONSE_STATUS_CONSTANT_CODES.get(
                bare_status_match.group(1)
            )
            if status_code is not None:
                return status_code
        # A fully bare enum constant (statically imported) resolves only with
        # CLDK argument-type evidence for the Spring/JAX-RS status enum.
        bare_constant_match = _BARE_STATUS_CONSTANT_RE.match(stripped)
        if bare_constant_match:
            constant_name = bare_constant_match.group(1)
            argument_type = (
                (argument_types[index] or "").replace("$", ".")
                if argument_types and index < len(argument_types)
                else ""
            )
            if argument_type in _SPRING_HTTP_STATUS_ARGUMENT_TYPES:
                status_code = _HTTPSTATUS_CONSTANT_CODES.get(constant_name)
                if status_code is not None:
                    return status_code
            if argument_type in _JAXRS_STATUS_ARGUMENT_TYPES:
                status_code = _JAXRS_RESPONSE_STATUS_CONSTANT_CODES.get(constant_name)
                if status_code is not None:
                    return status_code
    return None


def _to_arg_exprs(call_site: JCallSite) -> list[str]:
    """Convert a call site's argument expressions to a list of strings."""
    return [str(a) for a in (call_site.argument_expr or [])]


def _to_arg_types(call_site: JCallSite) -> list[str]:
    """Convert a call site's resolved argument types to a list of strings."""
    return [str(t) for t in (call_site.argument_types or [])]


_BODY_CATEGORY_METHODS: set[str] = {
    "jsonPath",
    "body",
    "content",
    "contains",
    "containsString",
    "matches",
    "responseBody",
}

_HEADER_SUBJECT_METHOD_HINTS: set[str] = {
    "getHeader",
    "getHeaders",
    "getHeaderString",
    "getFirstHeader",
    "header",
    "headers",
    "contentType",
}

_HEADER_CATEGORY_METHODS: set[str] = {
    "header",
    "headers",
    "contentType",
    "cookie",
    "cookies",
}

# Fluent entry points whose terminal verifier carries the assertion; counting
# the entry point too would double-count one logical assertion.
_ASSERTION_WRAPPER_METHODS: set[str] = {
    "assertThat",
    "assertThatCode",
    "assertThatNoException",
    "assertThatObject",
    "assertWithMessage",
}

_NEGATED_ASSERTION_ROOT_METHODS: set[str] = {
    "assertFalse",
    "assertNotEquals",
    "assertNotSame",
}

# AssertJ BDD entry points (org.assertj.core.api.BDDAssertions) mirror the
# Assertions `assertThat*` roots with `then*` names. Unlike `assert*`, these names
# are not self-identifying (Mockito's BDDMockito.then, Reactor's Mono.then), so
# they are mapped to their analog only when the receiver resolves to a known
# assertion framework; the downstream logic then treats them identically.
_ASSERTJ_BDD_ROOT_ANALOGS: dict[str, str] = {
    "then": "assertThat",
    "thenObject": "assertThatObject",
    "thenCode": "assertThatCode",
    "thenNoException": "assertThatNoException",
    "thenThrownBy": "assertThatThrownBy",
    "thenExceptionOfType": "assertThatExceptionOfType",
}

_SUBJECT_METHOD_HINTS: set[str] = (
    _STATUS_SUBJECT_METHOD_HINTS
    | _BODY_SUBJECT_METHOD_HINTS
    | _HEADER_SUBJECT_METHOD_HINTS
)

_FLUENT_VERIFIER_METHOD_PREFIXES: tuple[str, ...] = (
    "contains",
    "does",
    "ends",
    "has",
    "is",
    "matches",
    "starts",
)

_FLUENT_VERIFIER_METHODS: set[str] = {
    *_STATUS_VALUE_MATCHER_METHODS,
    "allSatisfy",
    "anySatisfy",
    "noneSatisfy",
    "satisfies",
    "satisfiesAnyOf",
    "satisfiesExactly",
}

_FLUENT_WRAPPER_METHODS: set[str] = {
    "as",
    "describedAs",
    "extracting",
    "filteredOn",
    "flatExtracting",
    "overridingErrorMessage",
    "usingComparator",
    "usingElementComparator",
    "usingRecursiveComparison",
    "withFailMessage",
}

_WRAPPER_ARGUMENT_MATCHER_METHODS: set[str] = {
    *_STATUS_VALUE_MATCHER_METHODS,
    "anything",
    "instanceOf",
    "not",
    "notNullValue",
    "nullValue",
    "sameInstance",
    "samePropertyValuesAs",
    "typeCompatibleWith",
}

_STATUS_CHAIN_SUBJECT_METHODS: set[str] = {
    "status",
    "expectStatus",
}

_STATUS_CHAIN_END_METHODS: set[str] = {
    "expectBody",
    "expectBodyList",
    "expectCookie",
    "expectHeader",
    "returnResult",
    "consumeWith",
}

_CONFIDENT_STATUS_METHODS: set[str] = {
    *_STATUS_METHOD_RANGE_HINTS,
    *_ASSERTJ_STATUS_METHOD_CODE_HINTS,
    *_ASSERTJ_STATUS_METHOD_RANGE_HINTS,
    "hasStatus",
    "statusCode",
}

_MOCKMVC_ASSERTION_CONTEXT_METHODS: set[str] = {
    "andExpect",
    "andExpectAll",
}

_HTTP_STATUS_SUBJECT_RECEIVER_PREFIXES: tuple[str, ...] = (
    "org.springframework.test.web.servlet.result.MockMvcResultMatchers",
    "org.springframework.test.web.reactive.server.WebTestClient.ResponseSpec",
    "org.springframework.test.web.reactive.server.WebTestClient$ResponseSpec",
    "org.springframework.test.web.reactive.server.DefaultWebTestClient.DefaultResponseSpec",
    "org.springframework.test.web.reactive.server.DefaultWebTestClient$DefaultResponseSpec",
)

_HTTP_STATUS_VERIFIER_RECEIVER_PREFIXES: tuple[str, ...] = (
    "org.springframework.test.web.servlet.result.StatusResultMatchers",
    "org.springframework.test.web.reactive.server.StatusAssertions",
)

_RESPONSE_SURFACE_ROLES: frozenset[AssertionRole] = frozenset(
    {AssertionRole.STATUS, AssertionRole.BODY, AssertionRole.HEADER}
)


@dataclass(frozen=True)
class _ArgumentCategory:
    node: CallSiteNode
    classification: AssertionClassification


@dataclass(frozen=True)
class _ArgumentScan:
    method_names: set[str]
    categories: list[_ArgumentCategory]
    status_subject_nodes: list[CallSiteNode]
    has_nested_assertion_roots: bool


@dataclass
class _ReceiverContext:
    receiver_resolver: RuntimeReceiverResolver
    receiver_type_by_call_site: dict[tuple[MethodRef, int], str]

    def receiver_type(self, owner: MethodRef, node: CallSiteNode) -> str:
        key = (owner, id(node.call_site))
        if key not in self.receiver_type_by_call_site:
            self.receiver_type_by_call_site[key] = (
                self.receiver_resolver.resolve_for_event(
                    owner, node.call_site
                ).receiver_type.strip()
            )
        return self.receiver_type_by_call_site[key]


def _is_fluent_verifier_method(method_name: str) -> bool:
    if method_name in _FLUENT_VERIFIER_METHODS:
        return True
    return method_name.startswith(_FLUENT_VERIFIER_METHOD_PREFIXES)


def _fluent_chain_node_kind(
    method_name: str,
) -> AssertionNodeKind:
    if method_name in _FLUENT_WRAPPER_METHODS:
        return AssertionNodeKind.WRAPPER
    if _is_fluent_verifier_method(method_name):
        return AssertionNodeKind.VERIFIER
    return AssertionNodeKind.WRAPPER


def _receiver_type_matches(receiver_type: str, prefixes: tuple[str, ...]) -> bool:
    normalized = receiver_type.strip().lower()
    return bool(normalized) and any(
        matches_receiver_prefix(normalized, prefix.lower()) for prefix in prefixes
    )


def _is_assertion_framework_receiver(receiver_type: str) -> bool:
    return _receiver_type_matches(
        receiver_type,
        _ASSERTION_FRAMEWORK_RECEIVER_PREFIXES,
    )


def _has_http_response_evidence(node: CallSiteNode) -> bool:
    classification = node.http_classification
    return classification is not None and classification.response_role is not None


def _has_known_status_receiver(
    node: CallSiteNode,
    *,
    owner: MethodRef,
    receiver_context: _ReceiverContext,
    subject: bool,
) -> bool:
    prefixes = (
        _HTTP_STATUS_SUBJECT_RECEIVER_PREFIXES
        if subject
        else _HTTP_STATUS_VERIFIER_RECEIVER_PREFIXES
    )
    return _receiver_type_matches(
        receiver_context.receiver_type(owner, node),
        prefixes,
    )


def _has_mockmvc_assertion_ancestor(node: CallSiteNode) -> bool:
    current = node.parent
    while current is not None:
        method_name = current.call_site.method_name or ""
        if method_name in _MOCKMVC_ASSERTION_CONTEXT_METHODS:
            return True
        if _has_http_response_evidence(current):
            return True
        current = current.parent
    return False


def _status_chain_has_http_context(
    subject_node: CallSiteNode,
    chain_nodes: list[CallSiteNode],
    *,
    owner: MethodRef,
    receiver_context: _ReceiverContext,
) -> bool:
    method_name = subject_node.call_site.method_name or ""
    if method_name == "expectStatus":
        return True
    if any(_has_http_response_evidence(node) for node in chain_nodes):
        return True
    if _has_known_status_receiver(
        subject_node,
        owner=owner,
        receiver_context=receiver_context,
        subject=True,
    ):
        return True
    if any(
        _has_known_status_receiver(
            node,
            owner=owner,
            receiver_context=receiver_context,
            subject=False,
        )
        for node in chain_nodes
    ):
        return True
    return _has_mockmvc_assertion_ancestor(subject_node)


def _status_subject_has_required_context(
    node: CallSiteNode,
    *,
    owner: MethodRef,
    receiver_context: _ReceiverContext,
) -> bool:
    method_name = node.call_site.method_name or ""
    if method_name not in _STATUS_SUBJECT_METHOD_HINTS:
        return False
    if method_name not in _AMBIGUOUS_STATUS_SUBJECT_METHODS:
        return True
    return (
        _has_http_response_evidence(node)
        or _has_known_status_receiver(
            node,
            owner=owner,
            receiver_context=receiver_context,
            subject=True,
        )
        or _has_mockmvc_assertion_ancestor(node)
    )


def _is_confident_status_category(
    method_name: str,
    argument_exprs: list[str],
) -> bool:
    if method_name not in _CONFIDENT_STATUS_METHODS:
        return False
    if method_name == "statusCode":
        return bool(argument_exprs)
    if method_name == "hasStatus":
        return bool(argument_exprs)
    return True


def _category_has_required_context(
    classification: AssertionClassification,
    method_name: str,
    argument_exprs: list[str],
    role_context: AssertionRole,
    *,
    has_status_receiver_context: bool = False,
) -> bool:
    if classification.role not in _RESPONSE_SURFACE_ROLES:
        return True
    if classification.role == role_context:
        return True
    if classification.role == AssertionRole.STATUS:
        return (
            _is_confident_status_category(method_name, argument_exprs)
            or has_status_receiver_context
        )
    return False


def _fallback_node_kind_for_unapplied_category(
    category: AssertionClassification | None,
    method_name: str,
) -> AssertionNodeKind:
    if category is not None and category.role in _RESPONSE_SURFACE_ROLES:
        return AssertionNodeKind.VERIFIER
    return _fluent_chain_node_kind(method_name)


def _classify_by_category(
    method_name: str,
    argument_exprs: list[str] | None = None,
    argument_types: list[str] | None = None,
    *,
    node_kind: AssertionNodeKind = AssertionNodeKind.VERIFIER,
) -> AssertionClassification | None:
    """Map a method name to an assertion classification via category sets.

    Returns ``None`` when the method doesn't belong to any known category
    so callers can apply their own fallback.
    """
    if method_name in _ASSERTJ_STATUS_METHOD_CODE_HINTS:
        code = _ASSERTJ_STATUS_METHOD_CODE_HINTS[method_name]
        return AssertionClassification(
            role=AssertionRole.STATUS,
            status_code=code,
            status_range=status_range_from_code(code),
            node_kind=node_kind,
        )
    if method_name in _ASSERTJ_STATUS_METHOD_RANGE_HINTS:
        return AssertionClassification(
            role=AssertionRole.STATUS,
            status_range=_ASSERTJ_STATUS_METHOD_RANGE_HINTS[method_name],
            node_kind=node_kind,
        )
    if method_name == "hasStatus":
        parsed_code = (
            _extract_status_code_from_arguments(argument_exprs, argument_types)
            if argument_exprs
            else None
        )
        return AssertionClassification(
            role=AssertionRole.STATUS,
            status_code=parsed_code,
            status_range=(
                status_range_from_code(parsed_code) if parsed_code is not None else None
            ),
            node_kind=node_kind,
        )
    if method_name in _STATUS_METHOD_CODE_HINTS:
        code = _STATUS_METHOD_CODE_HINTS[method_name]
        return AssertionClassification(
            role=AssertionRole.STATUS,
            status_code=code,
            status_range=status_range_from_code(code),
            node_kind=node_kind,
        )
    if method_name in _STATUS_METHOD_RANGE_HINTS:
        return AssertionClassification(
            role=AssertionRole.STATUS,
            status_range=_STATUS_METHOD_RANGE_HINTS[method_name],
            node_kind=node_kind,
        )
    if method_name in _STATUS_CATEGORY_METHODS:
        # Remaining status methods (e.g. statusCode) — try argument parsing
        parsed_code = (
            _extract_status_code_from_arguments(argument_exprs, argument_types)
            if argument_exprs
            else None
        )
        return AssertionClassification(
            role=AssertionRole.STATUS,
            status_code=parsed_code,
            status_range=(
                status_range_from_code(parsed_code) if parsed_code is not None else None
            ),
            node_kind=node_kind,
        )
    if method_name in _BODY_CATEGORY_METHODS:
        return AssertionClassification(
            role=AssertionRole.BODY,
            node_kind=node_kind,
        )
    if method_name in _HEADER_CATEGORY_METHODS:
        return AssertionClassification(
            role=AssertionRole.HEADER,
            node_kind=node_kind,
        )
    return None


def _status_classification_from_arguments(
    argument_exprs: list[str],
    argument_types: list[str] | None = None,
    *,
    node_kind: AssertionNodeKind = AssertionNodeKind.DIRECT,
) -> AssertionClassification:
    parsed_code = _extract_status_code_from_arguments(argument_exprs, argument_types)
    return AssertionClassification(
        role=AssertionRole.STATUS,
        status_code=parsed_code,
        status_range=(
            status_range_from_code(parsed_code) if parsed_code is not None else None
        ),
        node_kind=node_kind,
    )


def _status_classification_from_value_matcher(
    method_name: str,
    argument_exprs: list[str],
    argument_types: list[str] | None = None,
    *,
    node_kind: AssertionNodeKind,
) -> AssertionClassification:
    if method_name in _STATUS_VALUE_EQUALITY_MATCHER_METHODS:
        return _status_classification_from_arguments(
            argument_exprs,
            argument_types,
            node_kind=node_kind,
        )
    return AssertionClassification(
        role=AssertionRole.STATUS,
        node_kind=node_kind,
    )


def _status_evidence_is_only_getstatus_subject(
    scan: _ArgumentScan,
    *,
    owner: MethodRef,
    receiver_context: _ReceiverContext,
) -> bool:
    contextual_subjects = [
        node
        for node in scan.status_subject_nodes
        if _status_subject_has_required_context(
            node,
            owner=owner,
            receiver_context=receiver_context,
        )
    ]
    if not contextual_subjects:
        return False
    if not all(
        (node.call_site.method_name or "") == "getStatus"
        for node in contextual_subjects
    ):
        return False
    return not any(
        category.classification.role == AssertionRole.STATUS
        and (
            _is_confident_status_category(
                category.node.call_site.method_name or "",
                _to_arg_exprs(category.node.call_site),
            )
            or _has_known_status_receiver(
                category.node,
                owner=owner,
                receiver_context=receiver_context,
                subject=False,
            )
        )
        for category in scan.categories
    )


def _role_from_argument_scan(
    scan: _ArgumentScan,
    *,
    owner: MethodRef,
    receiver_context: _ReceiverContext,
    argument_types: list[str] | None = None,
) -> AssertionRole:
    roles: set[AssertionRole] = set()

    status_from_subjects = any(
        _status_subject_has_required_context(
            node,
            owner=owner,
            receiver_context=receiver_context,
        )
        for node in scan.status_subject_nodes
    )
    if status_from_subjects:
        if _status_evidence_is_only_getstatus_subject(
            scan,
            owner=owner,
            receiver_context=receiver_context,
        ) and _argument_types_indicate_domain_status(argument_types):
            status_from_subjects = False
    if status_from_subjects:
        roles.add(AssertionRole.STATUS)
    elif any(
        category.classification.role == AssertionRole.STATUS
        and (
            _is_confident_status_category(
                category.node.call_site.method_name or "",
                _to_arg_exprs(category.node.call_site),
            )
            or _has_known_status_receiver(
                category.node,
                owner=owner,
                receiver_context=receiver_context,
                subject=False,
            )
        )
        for category in scan.categories
    ):
        roles.add(AssertionRole.STATUS)
    if scan.method_names & _BODY_SUBJECT_METHOD_HINTS:
        roles.add(AssertionRole.BODY)
    if scan.method_names & _HEADER_SUBJECT_METHOD_HINTS:
        roles.add(AssertionRole.HEADER)

    if len(roles) == 1:
        return next(iter(roles))
    return AssertionRole.GENERAL


def _first_status_category(
    scan: _ArgumentScan,
) -> AssertionClassification | None:
    for category in scan.categories:
        if category.classification.role == AssertionRole.STATUS:
            return category.classification
    return None


def _assertion_root_classification(
    role: AssertionRole,
    argument_exprs: list[str],
    scan: _ArgumentScan,
    *,
    argument_types: list[str] | None = None,
    node_kind: AssertionNodeKind,
    root_method_name: str = "",
) -> AssertionClassification:
    if role != AssertionRole.STATUS:
        return AssertionClassification(
            role=role,
            node_kind=node_kind,
        )

    classification = _status_classification_from_arguments(
        argument_exprs,
        argument_types,
        node_kind=node_kind,
    )
    if classification.status_code is None and classification.status_range is None:
        status_category = _first_status_category(scan)
        if status_category is not None:
            classification = AssertionClassification(
                role=AssertionRole.STATUS,
                status_code=status_category.status_code,
                status_range=status_category.status_range,
                node_kind=node_kind,
            )

    if root_method_name in _NEGATED_ASSERTION_ROOT_METHODS and (
        classification.status_code is not None
        or classification.status_range is not None
    ):
        classification = AssertionClassification(
            role=AssertionRole.STATUS,
            node_kind=node_kind,
        )

    return classification


def _with_node_kind(
    classification: AssertionClassification,
    node_kind: AssertionNodeKind,
) -> AssertionClassification:
    return AssertionClassification(
        role=classification.role,
        status_code=classification.status_code,
        status_range=classification.status_range,
        node_kind=node_kind,
    )


def _promote_direct_argument_verifiers(
    scan: _ArgumentScan,
    role: AssertionRole,
    root_method_name: str = "",
) -> bool:
    if role not in {
        AssertionRole.STATUS,
        AssertionRole.BODY,
        AssertionRole.HEADER,
    }:
        return False

    strip_codes = (
        root_method_name in _NEGATED_ASSERTION_ROOT_METHODS
        and role == AssertionRole.STATUS
    )
    promoted = False
    for category in scan.categories:
        classification = category.classification
        if classification.role != role:
            continue
        method_name = category.node.call_site.method_name or ""
        if method_name in _SUBJECT_METHOD_HINTS:
            continue
        if strip_codes and (
            classification.status_code is not None
            or classification.status_range is not None
        ):
            classification = AssertionClassification(
                role=AssertionRole.STATUS,
                node_kind=AssertionNodeKind.VERIFIER,
            )
        category.node.assertion_classification = _with_node_kind(
            classification,
            AssertionNodeKind.VERIFIER,
        )
        promoted = True
    return promoted


def _argument_matcher_classification(
    node: CallSiteNode,
    role: AssertionRole,
) -> AssertionClassification | None:
    if role not in {
        AssertionRole.STATUS,
        AssertionRole.BODY,
        AssertionRole.HEADER,
    }:
        return None

    method_name = node.call_site.method_name or ""
    if method_name in _SUBJECT_METHOD_HINTS:
        return None

    classification = node.assertion_classification
    if classification is not None and classification.role == role:
        return _with_node_kind(
            classification,
            AssertionNodeKind.VERIFIER,
        )

    if method_name not in _WRAPPER_ARGUMENT_MATCHER_METHODS:
        return None

    if role == AssertionRole.STATUS:
        return _status_classification_from_value_matcher(
            method_name,
            _to_arg_exprs(node.call_site),
            _to_arg_types(node.call_site),
            node_kind=AssertionNodeKind.VERIFIER,
        )

    return AssertionClassification(
        role=role,
        node_kind=AssertionNodeKind.VERIFIER,
    )


def _promote_wrapper_argument_matchers(
    parent: CallSiteNode,
    role: AssertionRole,
) -> None:
    candidates: list[tuple[CallSiteNode, AssertionClassification]] = []
    stack: list[tuple[CallSiteNode, bool]] = [
        (child, False) for child in reversed(parent.argument_children())
    ]
    while stack:
        child, negated = stack.pop()
        if child.resolved_helper is not None:
            continue
        classification = _argument_matcher_classification(child, role)
        if classification is not None:
            # A code under `not(...)` is the rejected status, not the asserted one.
            if negated and (
                classification.status_code is not None
                or classification.status_range is not None
            ):
                classification = AssertionClassification(
                    role=classification.role,
                    node_kind=classification.node_kind,
                )
            candidates.append((child, classification))
        child_negated = negated or (child.call_site.method_name or "") == "not"
        stack.extend(
            (grandchild, child_negated) for grandchild in reversed(child.children)
        )

    candidate_ids = {id(node) for node, _ in candidates}
    for node, classification in candidates:
        if any(
            id(descendant) in candidate_ids for descendant in node.all_descendants()
        ):
            continue
        node.assertion_classification = classification


def _status_chain_verifier_classification(
    node: CallSiteNode,
) -> AssertionClassification | None:
    method_name = node.call_site.method_name or ""
    category = _classify_by_category(
        method_name,
        _to_arg_exprs(node.call_site),
        _to_arg_types(node.call_site),
        node_kind=AssertionNodeKind.VERIFIER,
    )
    if category is not None and category.role == AssertionRole.STATUS:
        return category

    if method_name in _STATUS_VALUE_MATCHER_METHODS:
        return _status_classification_from_value_matcher(
            method_name,
            _to_arg_exprs(node.call_site),
            _to_arg_types(node.call_site),
            node_kind=AssertionNodeKind.VERIFIER,
        )

    return None


def _classify_status_receiver_chain(
    chain_nodes: list[CallSiteNode],
    *,
    owner: MethodRef,
    receiver_context: _ReceiverContext,
) -> None:
    """Classify local ``status().is*`` / ``expectStatus().is*`` chains.

    CLDK often omits receiver types for fluent response checks, so the HTTP
    role registry cannot always identify StatusResultMatchers/StatusAssertions.
    The syntactic receiver chain still exposes the subject and verifier locally.
    """

    subject_node: CallSiteNode | None = None
    for node in chain_nodes:
        if node.resolved_helper is not None:
            continue

        method_name = node.call_site.method_name or ""
        if method_name in _STATUS_CHAIN_SUBJECT_METHODS:
            if not _status_chain_has_http_context(
                node,
                chain_nodes,
                owner=owner,
                receiver_context=receiver_context,
            ):
                subject_node = None
                continue
            subject_node = node
            if node.assertion_classification is None:
                node.assertion_classification = AssertionClassification(
                    role=AssertionRole.STATUS,
                    node_kind=AssertionNodeKind.SUBJECT,
                )
            continue

        if subject_node is None:
            continue

        if method_name in _STATUS_CHAIN_END_METHODS:
            return

        classification = _status_chain_verifier_classification(node)
        if classification is None:
            continue

        if node.assertion_classification is None:
            node.assertion_classification = classification
        return


# ── Public API ───────────────────────────────────────────────────────


def classify_assertions_on_runtime_view(
    runtime_view: TestRuntimeView,
    receiver_resolver: RuntimeReceiverResolver,
) -> None:
    """Walk the runtime view and annotate nodes with assertion classification."""

    receiver_context = _ReceiverContext(
        receiver_resolver=receiver_resolver,
        receiver_type_by_call_site={},
    )
    for entry in runtime_view.entries:
        _classify_assertions_recursively(
            grouping=entry.grouping,
            owner=entry.method_ref,
            receiver_context=receiver_context,
        )


def _classify_assertions_recursively(
    grouping: CallSiteGrouping,
    *,
    owner: MethodRef,
    receiver_context: _ReceiverContext,
) -> None:
    """Recursively classify assertions on a grouping and its helper expansions."""

    _classify_assertions_on_grouping(
        grouping=grouping,
        owner=owner,
        receiver_context=receiver_context,
    )

    for node in grouping.nodes:
        if node.helper_expansion is not None:
            _classify_assertions_recursively(
                grouping=node.helper_expansion.grouping,
                owner=node.helper_expansion.callee,
                receiver_context=receiver_context,
            )


def _classify_assertions_on_grouping(
    grouping: CallSiteGrouping,
    *,
    owner: MethodRef,
    receiver_context: _ReceiverContext,
) -> None:
    """Classify assertion nodes on a single grouping.

    Tier 1: Nodes with HttpResponseRole (from HTTP classification pass).
    Tier 2: Standalone assertion patterns (JUnit, AssertJ, etc.).
    """

    # ── Tier 1: HTTP response role ──────────────────────────────────
    for node in grouping.nodes:
        classification = node.http_classification
        if classification is None or classification.response_role is None:
            continue

        role = classification.response_role
        if role == HttpResponseRole.STATUS_ASSERTION:
            method_name = node.call_site.method_name or ""
            resolved = _classify_by_category(
                method_name,
                _to_arg_exprs(node.call_site),
                _to_arg_types(node.call_site),
            )
            if resolved is not None and resolved.role == AssertionRole.STATUS:
                node.assertion_classification = resolved
            elif method_name in _STATUS_VALUE_MATCHER_METHODS:
                node.assertion_classification = (
                    _status_classification_from_value_matcher(
                        method_name,
                        _to_arg_exprs(node.call_site),
                        _to_arg_types(node.call_site),
                        node_kind=AssertionNodeKind.VERIFIER,
                    )
                )
            else:
                # The HTTP pass already deemed this a status assertion, so the
                # status code (e.g. a literal or HttpStatus constant) can be read
                # straight from the arguments even for framework-specific methods
                # absent from the category maps (e.g. Citrus ``response(...)``).
                node.assertion_classification = _status_classification_from_arguments(
                    _to_arg_exprs(node.call_site),
                    _to_arg_types(node.call_site),
                    node_kind=AssertionNodeKind.VERIFIER,
                )
        elif role == HttpResponseRole.BODY_ASSERTION:
            node.assertion_classification = AssertionClassification(
                role=AssertionRole.BODY,
                node_kind=AssertionNodeKind.VERIFIER,
            )
        elif role == HttpResponseRole.HEADER_ASSERTION:
            node.assertion_classification = AssertionClassification(
                role=AssertionRole.HEADER,
                node_kind=AssertionNodeKind.VERIFIER,
            )
        # INSPECTOR / EXTRACTOR / MATCHER → skip (not assertions)

    # ── Tier 2: Standalone assertion patterns ───────────────────────
    # Build wrapper chain info for the grouping
    _classify_tier2_assertions(
        grouping,
        owner=owner,
        receiver_context=receiver_context,
    )


def _classify_tier2_assertions(
    grouping: CallSiteGrouping,
    *,
    owner: MethodRef,
    receiver_context: _ReceiverContext,
) -> None:
    """Classify assertion nodes that lack HTTP response roles.

    Walks receiver chains looking for ``assert*`` roots and exception roots.
    Every chain member after a root inherits classification.  Argument
    children get category-aware classification (status/body methods are
    recognized, everything else gets GENERAL).
    """

    receiver_chains = grouping.receiver_chains()

    for chain_nodes in receiver_chains:
        _classify_status_receiver_chain(
            chain_nodes,
            owner=owner,
            receiver_context=receiver_context,
        )

        assertion_root: CallSiteNode | None = None
        root_role: AssertionRole | None = None

        for node in chain_nodes:
            if node.assertion_classification is not None:
                continue
            if node.resolved_helper is not None:
                continue

            method_name = node.call_site.method_name or ""

            if (
                method_name in _ASSERTJ_BDD_ROOT_ANALOGS
                and _is_assertion_framework_receiver(
                    receiver_context.receiver_type(owner, node)
                )
            ):
                method_name = _ASSERTJ_BDD_ROOT_ANALOGS[method_name]

            if method_name in _EXCEPTION_ROOT_METHODS:
                node.assertion_classification = AssertionClassification(
                    role=AssertionRole.EXCEPTION,
                    node_kind=AssertionNodeKind.DIRECT,
                )
                _classify_argument_children(node, AssertionRole.GENERAL)
                assertion_root = node
                root_role = AssertionRole.EXCEPTION
                continue

            # `fail` alone is not evidence of exception testing, but when gated by
            # a known assertion-framework receiver it is an explicit countable oracle.
            if method_name == "fail":
                receiver_type = receiver_context.receiver_type(owner, node)
                if _is_assertion_framework_receiver(receiver_type):
                    node.assertion_classification = AssertionClassification(
                        role=AssertionRole.GENERAL,
                        node_kind=AssertionNodeKind.DIRECT,
                    )
                    _classify_argument_children(node, AssertionRole.GENERAL)
                    assertion_root = node
                    root_role = AssertionRole.GENERAL
                continue

            if method_name.startswith("assert"):
                argument_scan = _scan_and_classify_argument_children(
                    node, owner=owner, receiver_context=receiver_context
                )
                argument_types = _to_arg_types(node.call_site)
                role = _role_from_argument_scan(
                    argument_scan,
                    owner=owner,
                    receiver_context=receiver_context,
                    argument_types=argument_types,
                )
                argument_exprs = _to_arg_exprs(node.call_site)
                if method_name in _ASSERTION_WRAPPER_METHODS:
                    node.assertion_classification = _assertion_root_classification(
                        role,
                        argument_exprs,
                        argument_scan,
                        argument_types=argument_types,
                        node_kind=AssertionNodeKind.WRAPPER,
                        root_method_name=method_name,
                    )
                    _promote_wrapper_argument_matchers(node, role)
                else:
                    promoted_argument_verifier = _promote_direct_argument_verifiers(
                        argument_scan,
                        role,
                        root_method_name=method_name,
                    )
                    # Nested assertion roots (e.g. lambdas under assertAll) are
                    # counted individually, so the grouping call must not count.
                    node_kind = (
                        AssertionNodeKind.WRAPPER
                        if promoted_argument_verifier
                        or argument_scan.has_nested_assertion_roots
                        else AssertionNodeKind.DIRECT
                    )
                    node.assertion_classification = _assertion_root_classification(
                        role,
                        argument_exprs,
                        argument_scan,
                        argument_types=argument_types,
                        node_kind=node_kind,
                        root_method_name=method_name,
                    )
                assertion_root = node
                root_role = role
                continue

            if assertion_root is not None:
                category = _classify_by_category(
                    method_name,
                    _to_arg_exprs(node.call_site),
                    _to_arg_types(node.call_site),
                )
                inherited = (
                    AssertionRole.GENERAL
                    if root_role == AssertionRole.EXCEPTION
                    else root_role
                )
                assert inherited is not None
                has_status_receiver_context = (
                    category is not None
                    and category.role == AssertionRole.STATUS
                    and _has_known_status_receiver(
                        node,
                        owner=owner,
                        receiver_context=receiver_context,
                        subject=False,
                    )
                )
                if category is not None and _category_has_required_context(
                    category,
                    method_name,
                    _to_arg_exprs(node.call_site),
                    inherited,
                    has_status_receiver_context=has_status_receiver_context,
                ):
                    node.assertion_classification = category
                else:
                    node_kind = _fallback_node_kind_for_unapplied_category(
                        category,
                        method_name,
                    )
                    if inherited == AssertionRole.STATUS:
                        node.assertion_classification = (
                            _status_classification_from_value_matcher(
                                method_name,
                                _to_arg_exprs(node.call_site),
                                _to_arg_types(node.call_site),
                                node_kind=node_kind,
                            )
                        )
                    else:
                        node.assertion_classification = AssertionClassification(
                            role=inherited,
                            node_kind=node_kind,
                        )


def _classify_argument_children(parent: CallSiteNode, role: AssertionRole) -> None:
    """Recursively classify all argument children with the given role."""
    stack: list[CallSiteNode] = list(reversed(parent.argument_children()))
    while stack:
        child = stack.pop()
        if child.assertion_classification is not None:
            continue
        if child.resolved_helper is not None:
            continue
        child.assertion_classification = AssertionClassification(
            role=role,
            node_kind=AssertionNodeKind.SUBJECT,
        )
        stack.extend(reversed(child.children))


def _receiver_backbone_has_assertion_root(
    node: CallSiteNode,
    *,
    owner: MethodRef,
    receiver_context: _ReceiverContext,
) -> bool:
    current: CallSiteNode | None = node
    while current is not None:
        if current.resolved_helper is None:
            method_name = current.call_site.method_name or ""
            if method_name in _ASSERTJ_BDD_ROOT_ANALOGS and (
                _is_assertion_framework_receiver(
                    receiver_context.receiver_type(owner, current)
                )
            ):
                method_name = _ASSERTJ_BDD_ROOT_ANALOGS[method_name]
            if method_name in _EXCEPTION_ROOT_METHODS or method_name.startswith(
                "assert"
            ):
                return True
        receivers = current.receiver_children()
        current = receivers[0] if receivers else None
    return False


def _scan_and_classify_argument_children(
    parent: CallSiteNode,
    *,
    owner: MethodRef,
    receiver_context: _ReceiverContext,
) -> _ArgumentScan:
    """Single-pass: collect subject hints while classifying children category-aware.

    Returns method-name and category evidence used to classify the assertion root.
    Argument chains carrying their own assertion root (e.g. lambdas passed to
    ``assertAll``) are left unclassified for the top-level chain walk to classify
    as countable roots, and only flagged so the parent demotes itself to a wrapper.
    """
    method_names: set[str] = set()
    categories: list[_ArgumentCategory] = []
    status_subject_nodes: list[CallSiteNode] = []
    has_nested_assertion_roots = False
    stack: list[tuple[CallSiteNode, bool]] = [
        (child, True) for child in reversed(parent.argument_children())
    ]
    while stack:
        child, is_chain_head = stack.pop()
        if child.resolved_helper is not None:
            continue
        if is_chain_head and _receiver_backbone_has_assertion_root(
            child, owner=owner, receiver_context=receiver_context
        ):
            has_nested_assertion_roots = True
            continue
        method_name = child.call_site.method_name or ""
        if method_name:
            method_names.add(method_name)
        if method_name in _STATUS_SUBJECT_METHOD_HINTS:
            status_subject_nodes.append(child)
        if child.assertion_classification is not None:
            if child.assertion_classification.role in _RESPONSE_SURFACE_ROLES:
                categories.append(
                    _ArgumentCategory(
                        node=child,
                        classification=child.assertion_classification,
                    )
                )
            continue
        if method_name in _SUBJECT_METHOD_HINTS:
            child.assertion_classification = AssertionClassification(
                role=AssertionRole.GENERAL,
                node_kind=AssertionNodeKind.SUBJECT,
            )
        else:
            category = _classify_by_category(
                method_name,
                _to_arg_exprs(child.call_site),
                _to_arg_types(child.call_site),
                node_kind=AssertionNodeKind.SUBJECT,
            )
            if category is not None:
                categories.append(
                    _ArgumentCategory(
                        node=child,
                        classification=category,
                    )
                )
                child.assertion_classification = category
            else:
                child.assertion_classification = AssertionClassification(
                    role=AssertionRole.GENERAL,
                    node_kind=AssertionNodeKind.SUBJECT,
                )
        stack.extend(
            (grandchild, grandchild.span.start != child.span.start)
            for grandchild in reversed(child.children)
        )
    return _ArgumentScan(
        method_names=method_names,
        categories=categories,
        status_subject_nodes=status_subject_nodes,
        has_nested_assertion_roots=has_nested_assertion_roots,
    )


__all__ = [
    "classify_assertions_on_runtime_view",
]
