from __future__ import annotations

import pytest

from gerbil.analysis.http import framework_registry as registry
from gerbil.analysis.http.framework_registry import (
    HTTP_OWNER_FAMILY_RULES,
    RECEIVERLESS_REQUEST_INFERENCE_RULES,
    HttpOwnerFamilyRule,
    infer_receiverless_request_target,
    resolve_http_owner_family,
)
from gerbil.analysis.schema import (
    HttpDispatchFramework,
    HttpRequestRole,
    HttpResponseRole,
)
from tests.cldk_factories import (
    classify_http_roles,
    infer_owner_family_http_method,
)


def _receiver_type_for_prefix(receiver_prefix: str) -> str:
    if receiver_prefix.endswith("."):
        return f"{receiver_prefix}ExampleReceiver"
    return f"{receiver_prefix}$Default"


def _receiver_type_cases_for_rule(rule: HttpOwnerFamilyRule) -> tuple[str, ...]:
    prefix_receivers = tuple(
        _receiver_type_for_prefix(prefix) for prefix in rule.receiver_prefixes
    )
    return (*prefix_receivers, *rule.exact_receiver_types)


def _lookalike_receiver_type_for_prefix(receiver_prefix: str) -> str:
    if receiver_prefix.endswith("."):
        return f"{receiver_prefix[:-1]}x.ExampleReceiver"
    return f"{receiver_prefix}Lookalike"


def _representative_method_name(rule: HttpOwnerFamilyRule) -> str:
    candidate_method_names = sorted(
        set(rule.request_role_by_method_name) | set(rule.response_role_by_method_name)
    )
    if rule.default_request_role is not None or rule.default_response_role is not None:
        candidate_method_names.append("probe")

    for receiver_type in _receiver_type_cases_for_rule(rule):
        for method_name in candidate_method_names:
            if resolve_http_owner_family(receiver_type, method_name) == rule:
                return method_name
    raise AssertionError(
        f"Missing representative method for owner family: {rule.family_id}"
    )


def _representative_request_method_name(rule: HttpOwnerFamilyRule) -> str | None:
    candidate_method_names = sorted(rule.request_role_by_method_name)
    if rule.default_request_role is not None:
        candidate_method_names.append("probe")

    for receiver_type in _receiver_type_cases_for_rule(rule):
        for method_name in candidate_method_names:
            resolved_rule = resolve_http_owner_family(receiver_type, method_name)
            if resolved_rule == rule:
                return method_name
    return None


_PREFIX_CASES = [
    pytest.param(
        rule.family_id,
        receiver_type,
        _representative_method_name(rule),
        id=f"{rule.family_id}:{receiver_type}",
    )
    for rule in HTTP_OWNER_FAMILY_RULES
    for receiver_type in _receiver_type_cases_for_rule(rule)
]

_BOUNDARY_REJECTION_CASES = [
    pytest.param(
        rule.family_id,
        prefix,
        _representative_method_name(rule),
        id=f"{rule.family_id}:{prefix}",
    )
    for rule in HTTP_OWNER_FAMILY_RULES
    for prefix in rule.receiver_prefixes
    if resolve_http_owner_family(
        _lookalike_receiver_type_for_prefix(prefix),
        _representative_method_name(rule),
    )
    is None
]
_EXACT_RECEIVER_REJECTION_CASES = [
    pytest.param(
        rule.family_id,
        receiver_type,
        _representative_method_name(rule),
        id=f"{rule.family_id}:{receiver_type}",
    )
    for rule in HTTP_OWNER_FAMILY_RULES
    for receiver_type in rule.exact_receiver_types
]

_ROLE_CASES = [
    pytest.param(
        rule.family_id,
        _receiver_type_cases_for_rule(rule)[0],
        _representative_method_name(rule),
        classify_http_roles(
            rule,
            receiver_type=_receiver_type_cases_for_rule(rule)[0],
            method_name=_representative_method_name(rule),
        )[0],
        classify_http_roles(
            rule,
            receiver_type=_receiver_type_cases_for_rule(rule)[0],
            method_name=_representative_method_name(rule),
        )[1],
        id=rule.family_id,
    )
    for rule in HTTP_OWNER_FAMILY_RULES
]

_REQUEST_HTTP_METHOD_CASES = [
    pytest.param(
        rule.family_id,
        _receiver_type_cases_for_rule(rule)[0],
        request_method_name,
        infer_owner_family_http_method(
            rule,
            receiver_type=_receiver_type_cases_for_rule(rule)[0],
            method_name=request_method_name,
        ),
        id=rule.family_id,
    )
    for rule in HTTP_OWNER_FAMILY_RULES
    for request_method_name in [_representative_request_method_name(rule)]
    if request_method_name is not None
]

_EXPECTED_RECEIVERLESS_REQUEST_INFERENCE_SURFACE = {
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.request_executor",
        HttpRequestRole.EVENT,
        (
            ("exchange", "UNKNOWN"),
            ("exchangesuccessfully", "UNKNOWN"),
            ("exchangetoflux", "UNKNOWN"),
            ("exchangetomono", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.request_builder",
        HttpRequestRole.BUILDER,
        (
            ("accept", "UNKNOWN"),
            ("acceptcharset", "UNKNOWN"),
            ("apiversion", "UNKNOWN"),
            ("attribute", "UNKNOWN"),
            ("attributes", "UNKNOWN"),
            ("body", "UNKNOWN"),
            ("bodyvalue", "UNKNOWN"),
            ("contentlength", "UNKNOWN"),
            ("contenttype", "UNKNOWN"),
            ("cookie", "UNKNOWN"),
            ("cookies", "UNKNOWN"),
            ("delete", "DELETE"),
            ("get", "GET"),
            ("head", "HEAD"),
            ("header", "UNKNOWN"),
            ("headers", "UNKNOWN"),
            ("ifmodifiedsince", "UNKNOWN"),
            ("ifnonematch", "UNKNOWN"),
            ("method", "UNKNOWN"),
            ("options", "OPTIONS"),
            ("patch", "PATCH"),
            ("post", "POST"),
            ("put", "PUT"),
            ("syncbody", "UNKNOWN"),
            ("uri", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.WEBCLIENT,
        "webclient.request",
        HttpRequestRole.EVENT,
        (
            ("exchange", "UNKNOWN"),
            ("exchangetoflux", "UNKNOWN"),
            ("exchangetomono", "UNKNOWN"),
            ("retrieve", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.WEBCLIENT,
        "webclient.request",
        HttpRequestRole.BUILDER,
        (
            ("accept", "UNKNOWN"),
            ("acceptcharset", "UNKNOWN"),
            ("body", "UNKNOWN"),
            ("bodyvalue", "UNKNOWN"),
            ("contentlength", "UNKNOWN"),
            ("contenttype", "UNKNOWN"),
            ("cookie", "UNKNOWN"),
            ("cookies", "UNKNOWN"),
            ("delete", "DELETE"),
            ("get", "GET"),
            ("head", "HEAD"),
            ("header", "UNKNOWN"),
            ("headers", "UNKNOWN"),
            ("ifmodifiedsince", "UNKNOWN"),
            ("ifnonematch", "UNKNOWN"),
            ("method", "UNKNOWN"),
            ("options", "OPTIONS"),
            ("patch", "PATCH"),
            ("post", "POST"),
            ("put", "PUT"),
            ("syncbody", "UNKNOWN"),
            ("uri", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.REST_CLIENT,
        "rest-client.request",
        HttpRequestRole.EVENT,
        (
            ("exchange", "UNKNOWN"),
            ("exchangeforrequiredvalue", "UNKNOWN"),
            ("retrieve", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.REST_CLIENT,
        "rest-client.request",
        HttpRequestRole.BUILDER,
        (
            ("accept", "UNKNOWN"),
            ("acceptcharset", "UNKNOWN"),
            ("apiversion", "UNKNOWN"),
            ("attribute", "UNKNOWN"),
            ("attributes", "UNKNOWN"),
            ("body", "UNKNOWN"),
            ("contentlength", "UNKNOWN"),
            ("contenttype", "UNKNOWN"),
            ("cookie", "UNKNOWN"),
            ("cookies", "UNKNOWN"),
            ("delete", "DELETE"),
            ("get", "GET"),
            ("head", "HEAD"),
            ("header", "UNKNOWN"),
            ("headers", "UNKNOWN"),
            ("httprequest", "UNKNOWN"),
            ("ifmodifiedsince", "UNKNOWN"),
            ("ifnonematch", "UNKNOWN"),
            ("method", "UNKNOWN"),
            ("options", "OPTIONS"),
            ("patch", "PATCH"),
            ("post", "POST"),
            ("put", "PUT"),
            ("uri", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.JAVA_HTTPCLIENT,
        "java-httpclient.request",
        HttpRequestRole.EVENT,
        (
            ("send", "UNKNOWN"),
            ("sendasync", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.JAVA_HTTPCLIENT,
        "java-httpclient.request",
        HttpRequestRole.BUILDER,
        (
            ("delete", "DELETE"),
            ("get", "GET"),
            ("head", "HEAD"),
            ("header", "UNKNOWN"),
            ("headers", "UNKNOWN"),
            ("method", "UNKNOWN"),
            ("newbuilder", "UNKNOWN"),
            ("post", "POST"),
            ("put", "PUT"),
            ("setheader", "UNKNOWN"),
            ("uri", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.MICRONAUT_CLIENT,
        "micronaut-client.request",
        HttpRequestRole.EVENT,
        (
            ("datastream", "UNKNOWN"),
            ("exchange", "UNKNOWN"),
            ("exchangestream", "UNKNOWN"),
            ("jsonstream", "UNKNOWN"),
            ("retrieve", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.MICRONAUT_CLIENT,
        "micronaut-client.request",
        HttpRequestRole.BUILDER,
        (
            ("accept", "UNKNOWN"),
            ("basicauth", "UNKNOWN"),
            ("bearerauth", "UNKNOWN"),
            ("body", "UNKNOWN"),
            ("contentencoding", "UNKNOWN"),
            ("contentlength", "UNKNOWN"),
            ("contenttype", "UNKNOWN"),
            ("cookie", "UNKNOWN"),
            ("cookies", "UNKNOWN"),
            ("create", "UNKNOWN"),
            ("delete", "DELETE"),
            ("get", "GET"),
            ("head", "HEAD"),
            ("header", "UNKNOWN"),
            ("headers", "UNKNOWN"),
            ("options", "OPTIONS"),
            ("patch", "PATCH"),
            ("post", "POST"),
            ("put", "PUT"),
            ("toblocking", "UNKNOWN"),
            ("uri", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.OKHTTP,
        "okhttp.request",
        HttpRequestRole.EVENT,
        (
            ("enqueue", "UNKNOWN"),
            ("execute", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.OKHTTP,
        "okhttp.request",
        HttpRequestRole.BUILDER,
        (
            ("addheader", "UNKNOWN"),
            ("delete", "DELETE"),
            ("get", "GET"),
            ("head", "HEAD"),
            ("header", "UNKNOWN"),
            ("method", "UNKNOWN"),
            ("newcall", "UNKNOWN"),
            ("patch", "PATCH"),
            ("post", "POST"),
            ("put", "PUT"),
            ("url", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.REST_ASSURED,
        "rest-assured.request_event",
        HttpRequestRole.EVENT,
        (
            ("delete", "DELETE"),
            ("get", "GET"),
            ("head", "HEAD"),
            ("options", "OPTIONS"),
            ("patch", "PATCH"),
            ("post", "POST"),
            ("put", "PUT"),
            ("request", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.REST_ASSURED,
        "rest-assured.request_builder",
        HttpRequestRole.BUILDER,
        (
            ("accept", "UNKNOWN"),
            ("auth", "UNKNOWN"),
            ("basepath", "UNKNOWN"),
            ("baseuri", "UNKNOWN"),
            ("body", "UNKNOWN"),
            ("contenttype", "UNKNOWN"),
            ("cookie", "UNKNOWN"),
            ("cookies", "UNKNOWN"),
            ("filter", "UNKNOWN"),
            ("formparam", "UNKNOWN"),
            ("formparams", "UNKNOWN"),
            ("given", "UNKNOWN"),
            ("header", "UNKNOWN"),
            ("headers", "UNKNOWN"),
            ("multipart", "UNKNOWN"),
            ("oauth2", "UNKNOWN"),
            ("param", "UNKNOWN"),
            ("params", "UNKNOWN"),
            ("pathparam", "UNKNOWN"),
            ("pathparams", "UNKNOWN"),
            ("port", "UNKNOWN"),
            ("queryparam", "UNKNOWN"),
            ("queryparams", "UNKNOWN"),
            ("relaxedhttpsvalidation", "UNKNOWN"),
            ("spec", "UNKNOWN"),
            ("urlencodingenabled", "UNKNOWN"),
            ("when", "UNKNOWN"),
        ),
    ),
    (
        HttpDispatchFramework.MOCKMVC,
        "mockmvc.request_executor",
        HttpRequestRole.EVENT,
        (("perform", "UNKNOWN"),),
    ),
    (
        HttpDispatchFramework.MOCKMVC,
        "mockmvc.request_builder",
        HttpRequestRole.BUILDER,
        (
            ("accept", "UNKNOWN"),
            ("characterencoding", "UNKNOWN"),
            ("content", "UNKNOWN"),
            ("contenttype", "UNKNOWN"),
            ("contextpath", "UNKNOWN"),
            ("cookie", "UNKNOWN"),
            ("cookies", "UNKNOWN"),
            ("file", "UNKNOWN"),
            ("flashattr", "UNKNOWN"),
            ("header", "UNKNOWN"),
            ("headers", "UNKNOWN"),
            ("locale", "UNKNOWN"),
            ("param", "UNKNOWN"),
            ("params", "UNKNOWN"),
            ("principal", "UNKNOWN"),
            ("queryparam", "UNKNOWN"),
            ("queryparams", "UNKNOWN"),
            ("request", "UNKNOWN"),
            ("requestattr", "UNKNOWN"),
            ("secure", "UNKNOWN"),
            ("servletpath", "UNKNOWN"),
            ("sessionattr", "UNKNOWN"),
            ("with", "UNKNOWN"),
        ),
    ),
}


@pytest.mark.parametrize(("family_id", "receiver_type", "method_name"), _PREFIX_CASES)
def test_resolve_http_owner_family_matches_all_registered_prefixes(
    family_id: str,
    receiver_type: str,
    method_name: str,
) -> None:
    owner_family_rule = resolve_http_owner_family(receiver_type, method_name)

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == family_id


@pytest.mark.parametrize(
    ("_family_id", "receiver_prefix", "method_name"),
    _BOUNDARY_REJECTION_CASES,
)
def test_boundary_safe_matching_rejects_lookalikes_for_all_prefixes(
    _family_id: str,
    receiver_prefix: str,
    method_name: str,
) -> None:
    lookalike_receiver_type = _lookalike_receiver_type_for_prefix(receiver_prefix)

    owner_family_rule = resolve_http_owner_family(lookalike_receiver_type, method_name)

    assert owner_family_rule is None


@pytest.mark.parametrize(
    ("_family_id", "receiver_type", "method_name"),
    _EXACT_RECEIVER_REJECTION_CASES,
)
def test_exact_receiver_matching_rejects_lookalikes(
    _family_id: str,
    receiver_type: str,
    method_name: str,
) -> None:
    owner_family_rule = resolve_http_owner_family(
        _lookalike_receiver_type_for_prefix(receiver_type),
        method_name,
    )

    assert owner_family_rule is None


def test_resolve_http_owner_family_is_case_insensitive() -> None:
    owner_family_rule = resolve_http_owner_family(
        "ORG.SPRINGFRAMEWORK.WEB.REACTIVE.FUNCTION.CLIENT.WEBCLIENT",
        "get",
    )

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == "webclient.request"


@pytest.mark.parametrize(
    (
        "receiver_type",
        "method_name",
        "expected_family_id",
        "expected_request_role",
        "expected_response_role",
    ),
    [
        (
            "io.restassured.internal.RequestSpecificationImpl",
            "header",
            "rest-assured.request_builder",
            HttpRequestRole.BUILDER,
            None,
        ),
        (
            "io.restassured.internal.RestAssuredResponseImpl",
            "then",
            "rest-assured.response_inspector",
            None,
            HttpResponseRole.INSPECTOR,
        ),
        (
            "io.restassured.internal.ValidatableResponseImpl",
            "statusCode",
            "rest-assured.status_assertion",
            None,
            HttpResponseRole.STATUS_ASSERTION,
        ),
        (
            "io.restassured.internal.ValidatableResponseOptionsImpl",
            "body",
            "rest-assured.body_assertion",
            None,
            HttpResponseRole.BODY_ASSERTION,
        ),
        (
            "io.restassured.internal.ResponseSpecificationImpl",
            "statusCode",
            "rest-assured.status_assertion",
            None,
            HttpResponseRole.STATUS_ASSERTION,
        ),
        (
            "io.restassured.internal.RestAssuredResponseOptionsImpl",
            "asString",
            "rest-assured.response_extractor",
            None,
            HttpResponseRole.EXTRACTOR,
        ),
        (
            "io.restassured.internal.RestAssuredResponseOptionsImpl",
            "statusCode",
            "rest-assured.response_extractor",
            None,
            HttpResponseRole.EXTRACTOR,
        ),
        (
            "org.springframework.test.web.reactive.server."
            "DefaultWebTestClient$DefaultRequestBodyUriSpec",
            "exchangeSuccessfully",
            "webtestclient.request_executor",
            HttpRequestRole.EVENT,
            None,
        ),
        (
            "org.springframework.test.web.reactive.server."
            "DefaultWebTestClient$DefaultResponseSpec",
            "expectStatus",
            "webtestclient.matcher_root",
            None,
            HttpResponseRole.MATCHER,
        ),
        (
            "org.springframework.test.web.reactive.server."
            "DefaultWebTestClient$DefaultBodyContentSpec",
            "jsonPath",
            "webtestclient.body_assertion",
            None,
            HttpResponseRole.MATCHER,
        ),
        (
            "org.springframework.test.web.reactive.server."
            "DefaultWebTestClient$DefaultBodyContentSpec",
            "xpath",
            "webtestclient.body_assertion",
            None,
            HttpResponseRole.MATCHER,
        ),
        (
            "org.springframework.test.web.reactive.server.XpathAssertions",
            "string",
            "webtestclient.body_assertion",
            None,
            HttpResponseRole.BODY_ASSERTION,
        ),
        (
            "org.springframework.test.web.reactive.server.EntityExchangeResult",
            "getResponseBody",
            "webtestclient.response_extractor",
            None,
            HttpResponseRole.EXTRACTOR,
        ),
        (
            "org.springframework.web.client.DefaultRestClient$DefaultRequestBodyUriSpec",
            "retrieve",
            "rest-client.request",
            HttpRequestRole.EVENT,
            None,
        ),
    ],
)
def test_internal_framework_implementation_receivers_are_classified(
    receiver_type: str,
    method_name: str,
    expected_family_id: str,
    expected_request_role: HttpRequestRole | None,
    expected_response_role: HttpResponseRole | None,
) -> None:
    owner_family_rule = resolve_http_owner_family(receiver_type, method_name)

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == expected_family_id
    assert classify_http_roles(
        owner_family_rule,
        receiver_type=receiver_type,
        method_name=method_name,
    ) == (expected_request_role, expected_response_role)


def test_karate_http_to_static_factory_is_request_builder() -> None:
    owner_family_rule = resolve_http_owner_family("com.intuit.karate.Http", "to")

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == "karate.request"
    assert classify_http_roles(
        owner_family_rule,
        receiver_type="com.intuit.karate.Http",
        method_name="to",
    ) == (HttpRequestRole.BUILDER, None)


@pytest.mark.parametrize(
    ("receiver_type", "method_name", "expected_request_role"),
    [
        (
            "io.micronaut.http.client.HttpClient",
            "exchange",
            HttpRequestRole.EVENT,
        ),
        (
            "io.micronaut.http.client.HttpClient",
            "toBlocking",
            HttpRequestRole.BUILDER,
        ),
        (
            "io.micronaut.http.client.BlockingHttpClient",
            "retrieve",
            HttpRequestRole.EVENT,
        ),
        (
            "io.micronaut.http.client.StreamingHttpClient",
            "exchangeStream",
            HttpRequestRole.EVENT,
        ),
        (
            "io.micronaut.http.MutableHttpRequest",
            "header",
            HttpRequestRole.BUILDER,
        ),
    ],
)
def test_micronaut_client_surface_resolves_request_roles(
    receiver_type: str,
    method_name: str,
    expected_request_role: HttpRequestRole,
) -> None:
    owner_family_rule = resolve_http_owner_family(receiver_type, method_name)

    assert owner_family_rule is not None
    assert owner_family_rule.framework == HttpDispatchFramework.MICRONAUT_CLIENT
    assert owner_family_rule.family_id == "micronaut-client.request"
    assert classify_http_roles(
        owner_family_rule,
        receiver_type=receiver_type,
        method_name=method_name,
    ) == (expected_request_role, None)


@pytest.mark.parametrize(
    ("method_name", "expected_http_method"),
    [
        ("GET", "GET"),
        ("POST", "POST"),
        ("PUT", "PUT"),
        ("PATCH", "PATCH"),
        ("DELETE", "DELETE"),
        ("HEAD", "HEAD"),
        ("OPTIONS", "OPTIONS"),
        ("create", "UNKNOWN"),
    ],
)
def test_micronaut_http_request_factories_carry_method_name_verbs(
    method_name: str,
    expected_http_method: str,
) -> None:
    owner_family_rule = resolve_http_owner_family(
        "io.micronaut.http.HttpRequest", method_name
    )

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == "micronaut-client.request"
    assert classify_http_roles(
        owner_family_rule,
        receiver_type="io.micronaut.http.HttpRequest",
        method_name=method_name,
    ) == (HttpRequestRole.BUILDER, None)
    assert (
        infer_owner_family_http_method(
            owner_family_rule,
            receiver_type="io.micronaut.http.HttpRequest",
            method_name=method_name,
        )
        == expected_http_method
    )


def test_mockmvc_receiver_does_not_match_response_inspector_methods() -> None:
    assert (
        resolve_http_owner_family(
            "org.springframework.test.web.servlet.MockMvc",
            "andExpect",
        )
        is None
    )


@pytest.mark.parametrize(
    (
        "family_id",
        "receiver_type",
        "method_name",
        "expected_request_role",
        "expected_response_role",
    ),
    _ROLE_CASES,
)
def test_classify_http_roles_covers_all_registered_owner_families(
    family_id: str,
    receiver_type: str,
    method_name: str,
    expected_request_role: HttpRequestRole | None,
    expected_response_role: HttpResponseRole | None,
) -> None:
    owner_family_rule = resolve_http_owner_family(receiver_type, method_name)

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == family_id
    assert classify_http_roles(
        owner_family_rule,
        receiver_type=receiver_type,
        method_name=method_name,
    ) == (expected_request_role, expected_response_role)


@pytest.mark.parametrize(
    ("family_id", "receiver_type", "method_name", "expected_http_method"),
    _REQUEST_HTTP_METHOD_CASES,
)
def test_infer_owner_family_http_method_covers_request_side_owner_families(
    family_id: str,
    receiver_type: str,
    method_name: str,
    expected_http_method: str,
) -> None:
    owner_family_rule = resolve_http_owner_family(receiver_type, method_name)

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == family_id
    assert (
        infer_owner_family_http_method(
            owner_family_rule,
            receiver_type=receiver_type,
            method_name=method_name,
        )
        == expected_http_method
    )


def test_receiverless_request_inference_surface_is_explicitly_audited() -> None:
    actual_surface = {
        (
            rule.framework,
            rule.owner_family,
            rule.request_role,
            tuple(sorted(rule.method_http_methods.items())),
        )
        for rule in RECEIVERLESS_REQUEST_INFERENCE_RULES
    }

    assert actual_surface == _EXPECTED_RECEIVERLESS_REQUEST_INFERENCE_SURFACE


def test_infer_receiverless_request_target_uses_registered_surface() -> None:
    for rule in RECEIVERLESS_REQUEST_INFERENCE_RULES:
        for method_name, expected_http_method in rule.method_http_methods.items():
            target = infer_receiverless_request_target(
                rule.framework,
                method_name.upper(),
            )

            assert target is not None
            assert target.framework == rule.framework
            assert target.owner_family == rule.owner_family
            assert target.request_role == rule.request_role
            assert target.framework_http_method == expected_http_method


@pytest.mark.parametrize(
    ("framework", "method_name"),
    [
        (HttpDispatchFramework.APACHE_HTTPCLIENT, "execute"),
        (HttpDispatchFramework.MOCKMVC, "multipart"),
        (HttpDispatchFramework.REST_TEMPLATE, "exchange"),
    ],
)
def test_receiverless_request_inference_does_not_broaden_to_full_registry(
    framework: HttpDispatchFramework,
    method_name: str,
) -> None:
    assert infer_receiverless_request_target(framework, method_name) is None


def test_resolve_http_owner_family_uses_prefix_precedence_when_multiple_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generic_rule = HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_ASSURED,
        family_id="generic-http.request",
        receiver_prefixes=("com.example.",),
        request_role_by_method_name={"send": HttpRequestRole.EVENT},
        http_method_by_method_name={"send": "UNKNOWN"},
    )
    specific_rule = HttpOwnerFamilyRule(
        framework=HttpDispatchFramework.REST_ASSURED,
        family_id="specific-http.request",
        receiver_prefixes=("com.example.http.",),
        request_role_by_method_name={"send": HttpRequestRole.EVENT},
        http_method_by_method_name={"send": "UNKNOWN"},
    )
    monkeypatch.setattr(
        registry,
        "HTTP_OWNER_FAMILY_RULES",
        (
            generic_rule,
            specific_rule,
        ),
    )

    owner_family_rule = resolve_http_owner_family("com.example.http.Client", "send")

    assert owner_family_rule is specific_rule


def test_http_owner_family_registry_framework_ids_are_enum_aligned() -> None:
    registry_frameworks = {
        owner_family_rule.framework for owner_family_rule in HTTP_OWNER_FAMILY_RULES
    }
    enum_frameworks = set(HttpDispatchFramework)

    assert registry_frameworks.issubset(enum_frameworks)
