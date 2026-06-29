"""Tests for the registry-derived request-path receiver predicate."""

from __future__ import annotations

import pytest

from gerbil.analysis.http.framework_registry import (
    HTTP_OWNER_FAMILY_RULES,
    is_request_builder_receiver_type,
)
from gerbil.analysis.schema import HttpRequestRole

# Receiver types that accumulate request path/url state via builder methods.
_PATH_RECEIVER_TYPES = (
    "jakarta.ws.rs.client.WebTarget",
    "javax.ws.rs.client.WebTarget",
    "jakarta.ws.rs.client.Invocation",
    "jakarta.ws.rs.client.Invocation$Builder",
    "javax.ws.rs.client.Invocation.Builder",
    "io.restassured.specification.RequestSpecification",
    "io.restassured.specification.FilterableRequestSpecification",
    "io.restassured.module.mockmvc.specification.MockMvcRequestSpecification",
    "org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder",
    "org.springframework.test.web.servlet.request.MockMultipartHttpServletRequestBuilder",
    "org.springframework.test.web.reactive.server.WebTestClient$RequestHeadersUriSpec",
    "org.springframework.test.web.reactive.server.WebTestClient$RequestBodyUriSpec",
    "org.springframework.test.web.reactive.server.WebTestClient$RequestHeadersSpec",
    "org.springframework.web.reactive.function.client.WebClient$RequestHeadersUriSpec",
    "org.springframework.web.reactive.function.client.WebClient$RequestBodySpec",
    "org.springframework.web.client.RestClient$RequestBodyUriSpec",
    "okhttp3.Request",
    "okhttp3.Request$Builder",
    "okhttp3.Request.Builder",
    "java.net.http.HttpRequest$Builder",
    "com.intuit.karate.Http",
    "feign.RequestTemplate",
    "org.apache.http.client.methods.HttpPost",
    "org.apache.http.client.fluent.Request",
    "org.apache.hc.core5.http.io.support.ClassicRequestBuilder",
    "org.citrusframework.http.actions.HttpClientRequestActionBuilder",
)

# Client factories, response-side receivers, and unknown types never carry the
# request path as a helper argument.
_NON_PATH_RECEIVER_TYPES = (
    "",
    "java.lang.String",
    "com.example.PageObject",
    "okhttp3.OkHttpClient",
    "okhttp3.Call",
    "okhttp3.Call$Factory",
    "jakarta.ws.rs.client.Client",
    "javax.ws.rs.client.Client",
    "java.net.http.HttpClient",
    "java.net.http.HttpRequest",
    "java.net.http.HttpRequest$BodyPublisher",
    "org.springframework.web.reactive.function.client.WebClient",
    "org.springframework.web.reactive.function.client.WebClient$ResponseSpec",
    "org.springframework.web.client.RestClient",
    "org.springframework.web.client.RestClient$ResponseSpec",
    "org.springframework.test.web.reactive.server.WebTestClient",
    "org.springframework.test.web.reactive.server.WebTestClient$ResponseSpec",
    "org.springframework.web.client.RestTemplate",
    "org.springframework.boot.test.web.client.TestRestTemplate",
    "org.springframework.test.web.servlet.MockMvc",
    "org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
    "com.intuit.karate.http.Response",
    "com.intuit.karate.http.HttpRequest",
    "io.restassured.RestAssured",
    "io.restassured.response.Response",
    "io.restassured.response.ValidatableResponse",
    "org.apache.http.impl.client.CloseableHttpClient",
    "au.com.dius.pact.consumer.dsl.PactDslRequestWithoutPath",
    "au.com.dius.pact.consumer.dsl.PactDslRequestWithPath",
    "org.citrusframework.http.actions.HttpClientActionBuilder",
)


@pytest.mark.parametrize("receiver_type", _PATH_RECEIVER_TYPES)
def test_path_accumulating_receivers_are_request_builder_receivers(
    receiver_type: str,
) -> None:
    assert is_request_builder_receiver_type(receiver_type)


@pytest.mark.parametrize("receiver_type", _NON_PATH_RECEIVER_TYPES)
def test_factory_response_and_unknown_receivers_are_rejected(
    receiver_type: str,
) -> None:
    assert not is_request_builder_receiver_type(receiver_type)


def test_predicate_is_case_insensitive() -> None:
    assert is_request_builder_receiver_type(
        "IO.RESTASSURED.SPECIFICATION.REQUESTSPECIFICATION"
    )
    assert not is_request_builder_receiver_type("OKHTTP3.OKHTTPCLIENT")


def test_prefix_lookalikes_are_rejected() -> None:
    assert not is_request_builder_receiver_type("okhttp3.RequestLookalike")
    assert not is_request_builder_receiver_type("jakarta.ws.rs.client.WebTargetExtras")


def test_request_path_receiver_rules_have_a_builder_role() -> None:
    for rule in HTTP_OWNER_FAMILY_RULES:
        if not rule.request_path_receiver:
            continue
        has_builder_role = (
            rule.default_request_role == HttpRequestRole.BUILDER
            or HttpRequestRole.BUILDER in rule.request_role_by_method_name.values()
        )
        assert has_builder_role, (
            f"{rule.framework}:{rule.family_id} is flagged as a request-path "
            "receiver but registers no BUILDER role"
        )
