from __future__ import annotations

from cldk.models.java import JImport
import pytest
from typing import Any, cast

from gerbil.analysis.http.classification import (
    classify_http_on_grouping,
)
from gerbil.analysis.http.framework_registry import (
    HTTP_OWNER_FAMILY_RULES,
    resolve_http_owner_family,
)
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.schema import (
    HttpDispatchFramework,
    HttpRequestRole,
    HttpResponseRole,
    LifecyclePhase,
)
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    classify_http_roles,
    infer_owner_family_http_method,
    make_call_site,
    make_callable,
)


def _classify_http_on_grouping_for_testing(
    *,
    grouping,
    method_details,
    static_import_index: StaticImportIndex,
) -> None:
    owner = MethodRef(
        defining_class_name="example.ApiTest",
        method_signature=method_details.signature,
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=owner,
                context_class_name=owner.defining_class_name,
                grouping=grouping,
                method_details=method_details,
            )
        ]
    )
    receiver_resolver = build_runtime_receiver_resolver_for_testing(
        runtime_view,
        get_static_import_index_for_class=(lambda _class_name: static_import_index),
    )
    classify_http_on_grouping(
        grouping=grouping,
        owner=owner,
        receiver_resolver=receiver_resolver,
    )


def test_classify_http_on_grouping_requires_runtime_context() -> None:
    grouping = build_call_site_grouping(
        [
            make_call_site(
                method_name="exchange",
                receiver_type="org.springframework.web.client.RestTemplate",
            )
        ]
    )

    with pytest.raises(TypeError):
        cast(Any, classify_http_on_grouping)(grouping=grouping)


_STATIC_IMPORT_OWNER_CASES = [
    pytest.param(
        owner_class_name,
        method_name,
        owner_family_rule.family_id,
        classify_http_roles(
            owner_family_rule,
            receiver_type=owner_class_name,
            method_name=method_name,
        )[0],
        classify_http_roles(
            owner_family_rule,
            receiver_type=owner_class_name,
            method_name=method_name,
        )[1],
        id=f"{owner_family_rule.family_id}:{owner_class_name}:{method_name}",
    )
    for owner_family_rule in HTTP_OWNER_FAMILY_RULES
    for owner_class_name in owner_family_rule.static_import_owners
    for method_name in sorted(owner_family_rule.static_import_methods)
]

_EXPECTED_STATIC_IMPORT_OWNER_SURFACE = {
    (
        HttpDispatchFramework.JAVA_HTTPCLIENT,
        "java-httpclient.request",
        ("java.net.http.httprequest",),
        (),
        ("java.net.http.HttpRequest",),
        ("newbuilder",),
    ),
    (
        HttpDispatchFramework.MICRONAUT_CLIENT,
        "micronaut-client.request",
        ("io.micronaut.http.httprequest", "io.micronaut.http.mutablehttprequest"),
        (),
        ("io.micronaut.http.HttpRequest",),
        (
            "create",
            "delete",
            "get",
            "head",
            "options",
            "patch",
            "post",
            "put",
        ),
    ),
    (
        HttpDispatchFramework.REST_TEMPLATE,
        "rest-template.request",
        (
            "org.springframework.http.requestentity",
            "org.springframework.http.requestentity$bodybuilder",
            "org.springframework.http.requestentity.bodybuilder",
            "org.springframework.http.requestentity$headersbuilder",
            "org.springframework.http.requestentity.headersbuilder",
        ),
        (),
        ("org.springframework.http.RequestEntity",),
        ("delete", "get", "head", "method", "options", "patch", "post", "put"),
    ),
    (
        HttpDispatchFramework.MOCKMVC,
        "mockmvc.matcher_root",
        ("org.springframework.test.web.servlet.result.mockmvcresultmatchers",),
        (),
        ("org.springframework.test.web.servlet.result.MockMvcResultMatchers",),
        (
            "content",
            "cookie",
            "flash",
            "forwardedurl",
            "handler",
            "header",
            "jsonpath",
            "model",
            "redirectedurl",
            "request",
            "status",
            "view",
            "xpath",
        ),
    ),
    (
        HttpDispatchFramework.MOCKMVC,
        "mockmvc.request_factory",
        ("org.springframework.restdocs.mockmvc.restdocumentationrequestbuilders",),
        (),
        ("org.springframework.restdocs.mockmvc.RestDocumentationRequestBuilders",),
        (
            "delete",
            "get",
            "head",
            "multipart",
            "options",
            "patch",
            "post",
            "put",
            "request",
        ),
    ),
    (
        HttpDispatchFramework.MOCKMVC,
        "mockmvc.request_factory",
        ("org.springframework.test.web.servlet.request.mockmvcrequestbuilders",),
        (),
        ("org.springframework.test.web.servlet.request.MockMvcRequestBuilders",),
        (
            "asyncdispatch",
            "delete",
            "get",
            "head",
            "multipart",
            "options",
            "patch",
            "post",
            "put",
            "request",
        ),
    ),
    (
        HttpDispatchFramework.REST_ASSURED,
        "rest-assured.request_factory",
        (
            "io.restassured.restassured",
            "io.restassured.module.mockmvc.restassuredmockmvc",
            "com.jayway.restassured.restassured",
            "com.jayway.restassured.module.mockmvc.restassuredmockmvc",
            "io.restassured.module.webtestclient.restassuredwebtestclient",
        ),
        (),
        (
            "io.restassured.RestAssured",
            "io.restassured.module.mockmvc.RestAssuredMockMvc",
            "com.jayway.restassured.RestAssured",
            "com.jayway.restassured.module.mockmvc.RestAssuredMockMvc",
            "io.restassured.module.webtestclient.RestAssuredWebTestClient",
        ),
        (
            "delete",
            "get",
            "given",
            "head",
            "options",
            "patch",
            "post",
            "put",
            "request",
            "when",
        ),
    ),
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.request_factory",
        ("org.springframework.test.web.reactive.server.webtestclient",),
        (),
        ("org.springframework.test.web.reactive.server.WebTestClient",),
        (
            "bindtoapplicationcontext",
            "bindtocontroller",
            "bindtorouterfunction",
            "bindtoserver",
            "bindtowebhandler",
        ),
    ),
}
_EXPECTED_EMPTY_STATIC_IMPORT_RULES = {
    (
        HttpDispatchFramework.MICRONAUT_CLIENT,
        "micronaut-client.request",
        (
            "io.micronaut.http.client.httpclient",
            "io.micronaut.http.client.blockinghttpclient",
            "io.micronaut.http.client.streaminghttpclient",
        ),
        (),
    ),
    (
        HttpDispatchFramework.APACHE_HTTPCLIENT,
        "apache-httpclient.request",
        (
            "org.apache.http.client.fluent.executor",
            "org.apache.http.client.fluent.request",
            "org.apache.http.client.httpclient",
            "org.apache.http.nio.client.httpasyncclient",
            "org.apache.http.nio.client.httppipeliningclient",
            "org.apache.http.impl.client.abstracthttpclient",
            "org.apache.http.impl.client.closeablehttpclient",
            "org.apache.http.impl.client.contentencodinghttpclient",
            "org.apache.http.impl.client.decompressinghttpclient",
            "org.apache.http.impl.client.defaulthttpclient",
            "org.apache.http.impl.client.futurerequestexecutionservice",
            "org.apache.http.impl.client.minimalhttpclient",
            "org.apache.http.impl.client.systemdefaulthttpclient",
            "org.apache.http.impl.nio.client.abstracthttpasyncclient",
            "org.apache.http.impl.nio.client.closeablehttpasyncclient",
            "org.apache.http.impl.nio.client.closeablehttppipeliningclient",
            "org.apache.http.impl.nio.client.defaulthttpasyncclient",
            "org.apache.hc.client5.http.fluent.async",
            "org.apache.hc.client5.http.fluent.executor",
            "org.apache.hc.client5.http.fluent.request",
            "org.apache.hc.client5.http.async.httpasyncclient",
            "org.apache.hc.client5.http.classic.httpclient",
            "org.apache.hc.client5.http.impl.async.closeablehttpasyncclient",
            "org.apache.hc.client5.http.impl.async.internalh2asyncclient",
            "org.apache.hc.client5.http.impl.async.internalhttpasyncclient",
            "org.apache.hc.client5.http.impl.async.minimalh2asyncclient",
            "org.apache.hc.client5.http.impl.async.minimalhttpasyncclient",
            "org.apache.hc.client5.http.impl.classic.closeablehttpclient",
            "org.apache.hc.client5.http.impl.classic.futurerequestexecutionservice",
            "org.apache.hc.client5.http.impl.classic.internalhttpclient",
            "org.apache.hc.client5.http.impl.classic.minimalhttpclient",
        ),
        (),
    ),
    (
        HttpDispatchFramework.APACHE_HTTPCLIENT,
        "apache-httpclient.request",
        (
            "org.apache.http.client.fluent.request",
            "org.apache.http.httpentityenclosingrequest",
            "org.apache.http.httprequest",
            "org.apache.http.client.methods.httpdelete",
            "org.apache.http.client.methods.httpentityenclosingrequestbase",
            "org.apache.http.client.methods.httpget",
            "org.apache.http.client.methods.httphead",
            "org.apache.http.client.methods.httpoptions",
            "org.apache.http.client.methods.httppatch",
            "org.apache.http.client.methods.httppost",
            "org.apache.http.client.methods.httpput",
            "org.apache.http.client.methods.httprequestbase",
            "org.apache.http.client.methods.httprequestwrapper",
            "org.apache.http.client.methods.httptrace",
            "org.apache.http.client.methods.httpurirequest",
            "org.apache.http.client.methods.requestbuilder",
            "org.apache.http.message.basichttpentityenclosingrequest",
            "org.apache.http.message.basichttprequest",
            "org.apache.hc.client5.http.async.methods.basichttprequests",
            "org.apache.hc.client5.http.async.methods.basicrequestbuilder",
            "org.apache.hc.client5.http.async.methods.simplehttprequest",
            "org.apache.hc.client5.http.async.methods.simplehttprequests",
            "org.apache.hc.client5.http.async.methods.simplerequestbuilder",
            "org.apache.hc.client5.http.classic.methods.",
            "org.apache.hc.client5.http.fluent.request",
            "org.apache.hc.core5.http.classichttprequest",
            "org.apache.hc.core5.http.httprequest",
            "org.apache.hc.core5.http.io.support.classicrequestbuilder",
            "org.apache.hc.core5.http.message.basicclassichttprequest",
            "org.apache.hc.core5.http.message.basichttprequest",
            "org.apache.hc.core5.http.message.httprequestwrapper",
            "org.apache.hc.core5.http.support.abstractrequestbuilder",
        ),
        (),
    ),
    (
        HttpDispatchFramework.CITRUS,
        "citrus.request",
        (
            "org.citrusframework.http.actions.httpclientactionbuilder",
            "org.citrusframework.http.actions.httpserveractionbuilder",
        ),
        (),
    ),
    (
        HttpDispatchFramework.CITRUS,
        "citrus.request",
        (
            "org.citrusframework.http.actions.httpclientactionbuilder$httpclientsendactionbuilder",
            "org.citrusframework.http.actions.httpclientactionbuilder.httpclientsendactionbuilder",
            "org.citrusframework.http.actions.httpserveractionbuilder$httpserverreceiveactionbuilder",
            "org.citrusframework.http.actions.httpserveractionbuilder.httpserverreceiveactionbuilder",
            "org.citrusframework.http.actions.httpclientrequestactionbuilder",
            "org.citrusframework.http.actions.httpserverrequestactionbuilder",
        ),
        (),
    ),
    (
        HttpDispatchFramework.CITRUS,
        "citrus.request",
        (
            "org.citrusframework.http.actions.httpclientrequestactionbuilder",
            "org.citrusframework.http.actions.httpserverrequestactionbuilder",
        ),
        (),
    ),
    (
        HttpDispatchFramework.FEIGN,
        "feign.request",
        (
            "feign.client",
            "feign.asyncclient",
        ),
        (),
    ),
    (
        HttpDispatchFramework.FEIGN,
        "feign.request",
        ("feign.requesttemplate",),
        (),
    ),
    (
        HttpDispatchFramework.JAVA_HTTPCLIENT,
        "java-httpclient.request",
        ("java.net.http.httpclient",),
        (),
    ),
    (
        HttpDispatchFramework.JAVA_HTTPCLIENT,
        "java-httpclient.request",
        (
            "java.net.http.httprequest$builder",
            "java.net.http.httprequest.builder",
        ),
        (),
    ),
    (
        HttpDispatchFramework.JAX_RS,
        "jaxrs-client.request",
        (
            "javax.ws.rs.client.client",
            "jakarta.ws.rs.client.client",
        ),
        (),
    ),
    (
        HttpDispatchFramework.JAX_RS,
        "jaxrs-client.request",
        (
            "javax.ws.rs.client.webtarget",
            "jakarta.ws.rs.client.webtarget",
        ),
        (),
    ),
    (
        HttpDispatchFramework.JAX_RS,
        "jaxrs-client.request",
        (
            "javax.ws.rs.client.invocation",
            "jakarta.ws.rs.client.invocation",
        ),
        (),
    ),
    (
        HttpDispatchFramework.JAX_RS,
        "jaxrs-client.request",
        (
            "javax.ws.rs.client.invocation",
            "javax.ws.rs.client.syncinvoker",
            "javax.ws.rs.client.asyncinvoker",
            "javax.ws.rs.client.rxinvoker",
            "javax.ws.rs.client.completionstagerxinvoker",
            "jakarta.ws.rs.client.invocation",
            "jakarta.ws.rs.client.syncinvoker",
            "jakarta.ws.rs.client.asyncinvoker",
            "jakarta.ws.rs.client.rxinvoker",
            "jakarta.ws.rs.client.completionstagerxinvoker",
        ),
        (),
    ),
    (
        HttpDispatchFramework.KARATE,
        "karate.request",
        (),
        ("com.intuit.karate.http", "io.karatelabs.http.http"),
    ),
    (
        HttpDispatchFramework.MOCKMVC,
        "mockmvc.body_assertion",
        (
            "org.springframework.test.web.servlet.result.contentresultmatchers",
            "org.springframework.test.web.servlet.result.jsonpathresultmatchers",
            "org.springframework.test.web.servlet.result.modelresultmatchers",
            "org.springframework.test.web.servlet.result.viewresultmatchers",
            "org.springframework.test.web.servlet.result.requestresultmatchers",
            "org.springframework.test.web.servlet.result.xpathresultmatchers",
            "org.springframework.test.web.servlet.result.flashattributeresultmatchers",
            "org.springframework.test.web.servlet.result.handlerresultmatchers",
        ),
        (),
    ),
    (
        HttpDispatchFramework.MOCKMVC,
        "mockmvc.header_assertion",
        (
            "org.springframework.test.web.servlet.result.headerresultmatchers",
            "org.springframework.test.web.servlet.result.cookieresultmatchers",
        ),
        (),
    ),
    (
        HttpDispatchFramework.MOCKMVC,
        "mockmvc.request_builder",
        (
            "org.springframework.test.web.servlet.request.mockhttpservletrequestbuilder",
            "org.springframework.test.web.servlet.request.mockmultiparthttpservletrequestbuilder",
            "org.springframework.test.web.servlet.request.abstractmockhttpservletrequestbuilder",
            "org.springframework.test.web.servlet.request.abstractmockmultiparthttpservletrequestbuilder",
        ),
        (),
    ),
    (
        HttpDispatchFramework.MOCKMVC,
        "mockmvc.request_executor",
        ("org.springframework.test.web.servlet.mockmvc",),
        (),
    ),
    (
        HttpDispatchFramework.MOCKMVC,
        "mockmvc.response_inspector",
        ("org.springframework.test.web.servlet.resultactions",),
        (),
    ),
    (
        HttpDispatchFramework.MOCKMVC,
        "mockmvc.status_assertion",
        ("org.springframework.test.web.servlet.result.statusresultmatchers",),
        (),
    ),
    (
        HttpDispatchFramework.OKHTTP,
        "okhttp.request",
        ("okhttp3.call",),
        (),
    ),
    (
        HttpDispatchFramework.OKHTTP,
        "okhttp.request",
        (
            "okhttp3.okhttpclient",
            "okhttp3.call$factory",
            "okhttp3.call.factory",
        ),
        (),
    ),
    (
        HttpDispatchFramework.OKHTTP,
        "okhttp.request",
        ("okhttp3.request",),
        (),
    ),
    (
        HttpDispatchFramework.OKHTTP,
        "okhttp.request",
        (
            "okhttp3.request$builder",
            "okhttp3.request.builder",
        ),
        (),
    ),
    (
        HttpDispatchFramework.PACT,
        "pact.request",
        (
            "au.com.dius.pact.consumer.dsl.pactdslrequestwithoutpath",
            "au.com.dius.pact.consumer.dsl.pactdslrequestwithpath",
        ),
        (),
    ),
    (
        HttpDispatchFramework.REST_ASSURED,
        "rest-assured.body_assertion",
        (
            "io.restassured.specification.responsespecification",
            "io.restassured.specification.filterableresponsespecification",
            "io.restassured.internal.responsespecificationimpl",
            "com.jayway.restassured.specification.responsespecification",
            "com.jayway.restassured.specification.filterableresponsespecification",
            "com.jayway.restassured.internal.responsespecificationimpl",
            "io.restassured.response.validatableresponse",
            "io.restassured.response.validatableresponseoptions",
            "io.restassured.internal.validatableresponseimpl",
            "io.restassured.internal.validatableresponseoptionsimpl",
            "com.jayway.restassured.response.validatableresponse",
            "com.jayway.restassured.response.validatableresponseoptions",
            "com.jayway.restassured.internal.validatableresponseimpl",
            "com.jayway.restassured.internal.validatableresponseoptionsimpl",
            "io.restassured.module.mockmvc.response.validatablemockmvcresponse",
            "io.restassured.module.mockmvc.internal.validatablemockmvcresponseimpl",
            "com.jayway.restassured.module.mockmvc.response.validatablemockmvcresponse",
            "com.jayway.restassured.module.mockmvc.internal.validatablemockmvcresponseimpl",
            "io.restassured.module.webtestclient.response.validatablewebtestclientresponse",
            "io.restassured.module.webtestclient.internal.validatablewebtestclientresponseimpl",
        ),
        (),
    ),
    (
        HttpDispatchFramework.REST_ASSURED,
        "rest-assured.request_builder",
        (
            "io.restassured.specification.requestspecification",
            "io.restassured.specification.filterablerequestspecification",
            "io.restassured.specification.requestsender",
            "io.restassured.specification.requestsenderoptions",
            "io.restassured.internal.requestspecificationimpl",
            "com.jayway.restassured.specification.requestspecification",
            "com.jayway.restassured.specification.filterablerequestspecification",
            "com.jayway.restassured.specification.requestsender",
            "com.jayway.restassured.specification.requestsenderoptions",
            "com.jayway.restassured.internal.requestspecificationimpl",
            "io.restassured.module.mockmvc.specification.mockmvcrequestspecification",
            "io.restassured.module.mockmvc.specification.mockmvcrequestsender",
            "io.restassured.module.mockmvc.specification.mockmvcrequestsenderoptions",
            "io.restassured.module.mockmvc.internal.mockmvcrequestspecificationimpl",
            "com.jayway.restassured.module.mockmvc.specification.mockmvcrequestspecification",
            "com.jayway.restassured.module.mockmvc.specification.mockmvcrequestsender",
            "com.jayway.restassured.module.mockmvc.specification.mockmvcrequestsenderoptions",
            "com.jayway.restassured.module.mockmvc.internal.mockmvcrequestspecificationimpl",
            "io.restassured.module.webtestclient.specification.webtestclientrequestspecification",
            "io.restassured.module.webtestclient.specification.webtestclientrequestsender",
            "io.restassured.module.webtestclient.specification.webtestclientrequestsenderoptions",
            "io.restassured.module.webtestclient.internal.webtestclientrequestspecificationimpl",
        ),
        (),
    ),
    (
        HttpDispatchFramework.REST_ASSURED,
        "rest-assured.request_event",
        (
            "io.restassured.specification.requestspecification",
            "io.restassured.specification.filterablerequestspecification",
            "io.restassured.specification.requestsender",
            "io.restassured.specification.requestsenderoptions",
            "io.restassured.internal.requestspecificationimpl",
            "com.jayway.restassured.specification.requestspecification",
            "com.jayway.restassured.specification.filterablerequestspecification",
            "com.jayway.restassured.specification.requestsender",
            "com.jayway.restassured.specification.requestsenderoptions",
            "com.jayway.restassured.internal.requestspecificationimpl",
            "io.restassured.module.mockmvc.specification.mockmvcrequestspecification",
            "io.restassured.module.mockmvc.specification.mockmvcrequestsender",
            "io.restassured.module.mockmvc.specification.mockmvcrequestsenderoptions",
            "io.restassured.module.mockmvc.internal.mockmvcrequestspecificationimpl",
            "com.jayway.restassured.module.mockmvc.specification.mockmvcrequestspecification",
            "com.jayway.restassured.module.mockmvc.specification.mockmvcrequestsender",
            "com.jayway.restassured.module.mockmvc.specification.mockmvcrequestsenderoptions",
            "com.jayway.restassured.module.mockmvc.internal.mockmvcrequestspecificationimpl",
            "io.restassured.module.webtestclient.specification.webtestclientrequestspecification",
            "io.restassured.module.webtestclient.specification.webtestclientrequestsender",
            "io.restassured.module.webtestclient.specification.webtestclientrequestsenderoptions",
            "io.restassured.module.webtestclient.internal.webtestclientrequestspecificationimpl",
        ),
        (),
    ),
    (
        HttpDispatchFramework.REST_ASSURED,
        "rest-assured.response_extractor",
        (
            "io.restassured.response.response",
            "io.restassured.internal.restassuredresponseimpl",
            "com.jayway.restassured.response.response",
            "com.jayway.restassured.internal.restassuredresponseimpl",
            "io.restassured.module.mockmvc.response.mockmvcresponse",
            "io.restassured.module.mockmvc.internal.mockmvcrestassuredresponseimpl",
            "com.jayway.restassured.module.mockmvc.response.mockmvcresponse",
            "com.jayway.restassured.module.mockmvc.internal.mockmvcrestassuredresponseimpl",
            "io.restassured.module.webtestclient.response.webtestclientresponse",
            "io.restassured.module.webtestclient.internal.webtestclientrestassuredresponseimpl",
            "io.restassured.response.validatableresponse",
            "io.restassured.response.validatableresponseoptions",
            "io.restassured.internal.validatableresponseimpl",
            "io.restassured.internal.validatableresponseoptionsimpl",
            "com.jayway.restassured.response.validatableresponse",
            "com.jayway.restassured.response.validatableresponseoptions",
            "com.jayway.restassured.internal.validatableresponseimpl",
            "com.jayway.restassured.internal.validatableresponseoptionsimpl",
            "io.restassured.module.mockmvc.response.validatablemockmvcresponse",
            "io.restassured.module.mockmvc.internal.validatablemockmvcresponseimpl",
            "com.jayway.restassured.module.mockmvc.response.validatablemockmvcresponse",
            "com.jayway.restassured.module.mockmvc.internal.validatablemockmvcresponseimpl",
            "io.restassured.module.webtestclient.response.validatablewebtestclientresponse",
            "io.restassured.module.webtestclient.internal.validatablewebtestclientresponseimpl",
            "io.restassured.response.extractableresponse",
            "io.restassured.response.extractableresponseoptions",
            "io.restassured.response.responseoptions",
            "io.restassured.response.responsebodyextractionoptions",
            "io.restassured.internal.restassuredresponseoptionsimpl",
            "com.jayway.restassured.response.extractableresponse",
            "com.jayway.restassured.response.extractableresponseoptions",
            "com.jayway.restassured.response.responseoptions",
            "com.jayway.restassured.response.responsebodyextractionoptions",
            "com.jayway.restassured.internal.restassuredresponseoptionsimpl",
            "io.restassured.path.json.jsonpath",
            "io.restassured.path.xml.xmlpath",
            "com.jayway.restassured.path.json.jsonpath",
            "com.jayway.restassured.path.xml.xmlpath",
        ),
        (),
    ),
    (
        HttpDispatchFramework.REST_ASSURED,
        "rest-assured.response_inspector",
        (
            "io.restassured.response.response",
            "io.restassured.internal.restassuredresponseimpl",
            "com.jayway.restassured.response.response",
            "com.jayway.restassured.internal.restassuredresponseimpl",
            "io.restassured.module.mockmvc.response.mockmvcresponse",
            "io.restassured.module.mockmvc.internal.mockmvcrestassuredresponseimpl",
            "com.jayway.restassured.module.mockmvc.response.mockmvcresponse",
            "com.jayway.restassured.module.mockmvc.internal.mockmvcrestassuredresponseimpl",
            "io.restassured.module.webtestclient.response.webtestclientresponse",
            "io.restassured.module.webtestclient.internal.webtestclientrestassuredresponseimpl",
        ),
        (),
    ),
    (
        HttpDispatchFramework.REST_ASSURED,
        "rest-assured.status_assertion",
        (
            "io.restassured.specification.responsespecification",
            "io.restassured.specification.filterableresponsespecification",
            "io.restassured.internal.responsespecificationimpl",
            "com.jayway.restassured.specification.responsespecification",
            "com.jayway.restassured.specification.filterableresponsespecification",
            "com.jayway.restassured.internal.responsespecificationimpl",
            "io.restassured.response.validatableresponse",
            "io.restassured.response.validatableresponseoptions",
            "io.restassured.internal.validatableresponseimpl",
            "io.restassured.internal.validatableresponseoptionsimpl",
            "com.jayway.restassured.response.validatableresponse",
            "com.jayway.restassured.response.validatableresponseoptions",
            "com.jayway.restassured.internal.validatableresponseimpl",
            "com.jayway.restassured.internal.validatableresponseoptionsimpl",
            "io.restassured.module.mockmvc.response.validatablemockmvcresponse",
            "io.restassured.module.mockmvc.internal.validatablemockmvcresponseimpl",
            "com.jayway.restassured.module.mockmvc.response.validatablemockmvcresponse",
            "com.jayway.restassured.module.mockmvc.internal.validatablemockmvcresponseimpl",
            "io.restassured.module.webtestclient.response.validatablewebtestclientresponse",
            "io.restassured.module.webtestclient.internal.validatablewebtestclientresponseimpl",
        ),
        (),
    ),
    (
        HttpDispatchFramework.REST_TEMPLATE,
        "rest-template.request",
        (
            "org.springframework.web.client.restoperations",
            "org.springframework.web.client.resttemplate",
        ),
        (),
    ),
    (
        HttpDispatchFramework.REST_CLIENT,
        "rest-client.request",
        ("org.springframework.web.client.restclient",),
        (),
    ),
    (
        HttpDispatchFramework.REST_CLIENT,
        "rest-client.request",
        (
            "org.springframework.web.client.restclient$urispec",
            "org.springframework.web.client.restclient.urispec",
            "org.springframework.web.client.restclient$requestheadersurispec",
            "org.springframework.web.client.restclient.requestheadersurispec",
            "org.springframework.web.client.restclient$requestbodyurispec",
            "org.springframework.web.client.restclient.requestbodyurispec",
            "org.springframework.web.client.restclient$requestheadersspec",
            "org.springframework.web.client.restclient.requestheadersspec",
            "org.springframework.web.client.restclient$requestbodyspec",
            "org.springframework.web.client.restclient.requestbodyspec",
            "org.springframework.web.client.defaultrestclient$defaultrequestbodyurispec",
            "org.springframework.web.client.defaultrestclient.defaultrequestbodyurispec",
        ),
        (),
    ),
    (
        HttpDispatchFramework.TEST_REST_TEMPLATE,
        "test-rest-template.request",
        (
            "org.springframework.boot.test.web.client.testresttemplate",
            "org.springframework.boot.resttestclient.testresttemplate",
        ),
        (),
    ),
    (
        HttpDispatchFramework.WEBCLIENT,
        "webclient.request",
        ("org.springframework.web.reactive.function.client.webclient",),
        (),
    ),
    (
        HttpDispatchFramework.WEBCLIENT,
        "webclient.request",
        (
            "org.springframework.web.reactive.function.client.webclient$urispec",
            "org.springframework.web.reactive.function.client.webclient.urispec",
            "org.springframework.web.reactive.function.client.webclient$requestheadersurispec",
            "org.springframework.web.reactive.function.client.webclient.requestheadersurispec",
            "org.springframework.web.reactive.function.client.webclient$requestbodyurispec",
            "org.springframework.web.reactive.function.client.webclient.requestbodyurispec",
            "org.springframework.web.reactive.function.client.webclient$requestheadersspec",
            "org.springframework.web.reactive.function.client.webclient.requestheadersspec",
            "org.springframework.web.reactive.function.client.webclient$requestbodyspec",
            "org.springframework.web.reactive.function.client.webclient.requestbodyspec",
            "org.springframework.web.reactive.function.client.defaultwebclient$defaultrequestheadersurispec",
            "org.springframework.web.reactive.function.client.defaultwebclient.defaultrequestheadersurispec",
            "org.springframework.web.reactive.function.client.defaultwebclient$defaultrequestbodyurispec",
            "org.springframework.web.reactive.function.client.defaultwebclient.defaultrequestbodyurispec",
            "org.springframework.web.reactive.function.client.defaultwebclient$defaultrequestheadersspec",
            "org.springframework.web.reactive.function.client.defaultwebclient.defaultrequestheadersspec",
            "org.springframework.web.reactive.function.client.defaultwebclient$defaultrequestbodyspec",
            "org.springframework.web.reactive.function.client.defaultwebclient.defaultrequestbodyspec",
        ),
        (),
    ),
    (
        HttpDispatchFramework.WEBCLIENT,
        "webclient.request",
        ("org.springframework.web.reactive.function.client.exchangefunction",),
        (),
    ),
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.body_assertion",
        (
            "org.springframework.test.web.reactive.server.webtestclient$bodyspec",
            "org.springframework.test.web.reactive.server.webtestclient.bodyspec",
            "org.springframework.test.web.reactive.server.webtestclient$bodycontentspec",
            "org.springframework.test.web.reactive.server.webtestclient.bodycontentspec",
            "org.springframework.test.web.reactive.server.webtestclient$listbodyspec",
            "org.springframework.test.web.reactive.server.webtestclient.listbodyspec",
            "org.springframework.test.web.reactive.server.webtestclient$jsonpathassertions",
            "org.springframework.test.web.reactive.server.webtestclient.jsonpathassertions",
            "org.springframework.test.web.reactive.server.defaultwebtestclient$defaultbodyspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient.defaultbodyspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient$defaultbodycontentspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient.defaultbodycontentspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient$defaultlistbodyspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient.defaultlistbodyspec",
            "org.springframework.test.web.reactive.server.bodyspec",
            "org.springframework.test.web.reactive.server.bodycontentspec",
            "org.springframework.test.web.reactive.server.listbodyspec",
            "org.springframework.test.web.reactive.server.jsonpathassertions",
            "org.springframework.test.web.reactive.server.xpathassertions",
        ),
        (),
    ),
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.header_assertion",
        (
            "org.springframework.test.web.reactive.server.headerassertions",
            "org.springframework.test.web.reactive.server.cookieassertions",
        ),
        (),
    ),
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.matcher_root",
        (
            "org.springframework.test.web.reactive.server.webtestclient$responsespec",
            "org.springframework.test.web.reactive.server.webtestclient.responsespec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient$defaultresponsespec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient.defaultresponsespec",
        ),
        (),
    ),
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.request_builder",
        ("org.springframework.test.web.reactive.server.webtestclient",),
        (),
    ),
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.request_builder",
        (
            "org.springframework.test.web.reactive.server.webtestclient$requestheadersurispec",
            "org.springframework.test.web.reactive.server.webtestclient.requestheadersurispec",
            "org.springframework.test.web.reactive.server.webtestclient$requestbodyurispec",
            "org.springframework.test.web.reactive.server.webtestclient.requestbodyurispec",
            "org.springframework.test.web.reactive.server.webtestclient$requestheadersspec",
            "org.springframework.test.web.reactive.server.webtestclient.requestheadersspec",
            "org.springframework.test.web.reactive.server.webtestclient$requestbodyspec",
            "org.springframework.test.web.reactive.server.webtestclient.requestbodyspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient$defaultrequestbodyurispec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient.defaultrequestbodyurispec",
        ),
        (),
    ),
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.request_executor",
        (
            "org.springframework.test.web.reactive.server.webtestclient",
            "org.springframework.test.web.reactive.server.webtestclient$requestheadersurispec",
            "org.springframework.test.web.reactive.server.webtestclient.requestheadersurispec",
            "org.springframework.test.web.reactive.server.webtestclient$requestbodyurispec",
            "org.springframework.test.web.reactive.server.webtestclient.requestbodyurispec",
            "org.springframework.test.web.reactive.server.webtestclient$requestheadersspec",
            "org.springframework.test.web.reactive.server.webtestclient.requestheadersspec",
            "org.springframework.test.web.reactive.server.webtestclient$requestbodyspec",
            "org.springframework.test.web.reactive.server.webtestclient.requestbodyspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient$defaultrequestbodyurispec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient.defaultrequestbodyurispec",
        ),
        (),
    ),
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.response_extractor",
        (
            "org.springframework.test.web.reactive.server.webtestclient$responsespec",
            "org.springframework.test.web.reactive.server.webtestclient.responsespec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient$defaultresponsespec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient.defaultresponsespec",
            "org.springframework.test.web.reactive.server.webtestclient$bodyspec",
            "org.springframework.test.web.reactive.server.webtestclient.bodyspec",
            "org.springframework.test.web.reactive.server.webtestclient$bodycontentspec",
            "org.springframework.test.web.reactive.server.webtestclient.bodycontentspec",
            "org.springframework.test.web.reactive.server.webtestclient$listbodyspec",
            "org.springframework.test.web.reactive.server.webtestclient.listbodyspec",
            "org.springframework.test.web.reactive.server.webtestclient$jsonpathassertions",
            "org.springframework.test.web.reactive.server.webtestclient.jsonpathassertions",
            "org.springframework.test.web.reactive.server.defaultwebtestclient$defaultbodyspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient.defaultbodyspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient$defaultbodycontentspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient.defaultbodycontentspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient$defaultlistbodyspec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient.defaultlistbodyspec",
            "org.springframework.test.web.reactive.server.bodyspec",
            "org.springframework.test.web.reactive.server.bodycontentspec",
            "org.springframework.test.web.reactive.server.listbodyspec",
            "org.springframework.test.web.reactive.server.jsonpathassertions",
            "org.springframework.test.web.reactive.server.xpathassertions",
            "org.springframework.test.web.reactive.server.exchangeresult",
            "org.springframework.test.web.reactive.server.entityexchangeresult",
            "org.springframework.test.web.reactive.server.fluxexchangeresult",
        ),
        (),
    ),
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.response_inspector",
        (
            "org.springframework.test.web.reactive.server.webtestclient$responsespec",
            "org.springframework.test.web.reactive.server.webtestclient.responsespec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient$defaultresponsespec",
            "org.springframework.test.web.reactive.server.defaultwebtestclient.defaultresponsespec",
        ),
        (),
    ),
    (
        HttpDispatchFramework.WEBTESTCLIENT,
        "webtestclient.status_assertion",
        ("org.springframework.test.web.reactive.server.statusassertions",),
        (),
    ),
}


def test_mockmvc_owner_families_split_request_and_response_roles() -> None:
    request_rule = resolve_http_owner_family(
        "org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
        "get",
    )
    assert request_rule is not None
    assert request_rule.family_id == "mockmvc.request_factory"
    assert classify_http_roles(
        request_rule,
        receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
        method_name="get",
    ) == (HttpRequestRole.BUILDER, None)
    assert (
        infer_owner_family_http_method(
            request_rule,
            receiver_type="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
            method_name="get",
        )
        == "GET"
    )

    matcher_rule = resolve_http_owner_family(
        "org.springframework.test.web.servlet.result.MockMvcResultMatchers",
        "content",
    )
    assert matcher_rule is not None
    assert matcher_rule.family_id == "mockmvc.matcher_root"
    assert classify_http_roles(
        matcher_rule,
        receiver_type="org.springframework.test.web.servlet.result.MockMvcResultMatchers",
        method_name="content",
    ) == (None, HttpResponseRole.MATCHER)


@pytest.mark.parametrize(
    ("receiver_type", "method_name"),
    [
        ("java.net.http.HttpResponse", "headers"),
        ("java.net.http.HttpHeaders", "map"),
        ("java.net.http.HttpRequest", "method"),
        ("java.net.http.HttpRequest", "headers"),
        ("okhttp3.Response", "header"),
        ("okhttp3.Response", "headers"),
        ("okhttp3.Response", "newBuilder"),
        ("okhttp3.Headers", "get"),
        ("okhttp3.HttpUrl", "newBuilder"),
        ("okhttp3.mockwebserver.MockWebServer", "url"),
        ("org.apache.http.HttpResponse", "setHeader"),
        ("org.apache.http.message.BasicHttpResponse", "setHeader"),
        ("org.apache.http.client.methods.CloseableHttpResponse", "setHeader"),
        ("org.apache.hc.client5.http.impl.classic.CloseableHttpResponse", "setHeader"),
        ("org.apache.hc.core5.http.message.BasicClassicHttpResponse", "setHeader"),
        ("org.apache.hc.core5.http.io.support.ClassicResponseBuilder", "setHeader"),
        ("org.springframework.web.client.RestClient$ResponseSpec", "body"),
        (
            "org.springframework.web.client.DefaultRestClient$DefaultResponseSpec",
            "body",
        ),
        ("org.springframework.web.reactive.function.client.ClientResponse", "headers"),
        ("org.springframework.web.reactive.function.client.ClientResponse", "body"),
        ("org.springframework.web.reactive.function.client.ClientRequest", "method"),
        ("org.springframework.web.reactive.function.client.ClientRequest", "headers"),
        ("io.restassured.path.json.JsonPath", "get"),
        ("com.jayway.restassured.path.xml.XmlPath", "get"),
        ("com.intuit.karate.http.HttpResponse", "get"),
        ("com.intuit.karate.http.HttpRequestBuilder", "get"),
        ("au.com.dius.pact.consumer.dsl.PactDslResponse", "body"),
        ("au.com.dius.pact.consumer.dsl.PactDslResponse", "headers"),
        ("org.citrusframework.http.actions.HttpClientResponseActionBuilder", "body"),
        ("org.citrusframework.http.actions.HttpClientResponseActionBuilder", "status"),
        ("org.citrusframework.http.actions.HttpServerResponseActionBuilder", "body"),
        ("jakarta.ws.rs.client.ClientResponseContext", "getHeaders"),
        ("jakarta.ws.rs.client.ClientRequestContext", "setMethod"),
        ("jakarta.ws.rs.client.ClientBuilder", "newBuilder"),
        ("jakarta.ws.rs.client.Entity", "json"),
        ("feign.Response", "headers"),
        ("feign.Request", "headers"),
    ],
)
def test_response_and_accessor_receivers_do_not_classify_as_http_requests(
    receiver_type: str,
    method_name: str,
) -> None:
    owner_family_rule = resolve_http_owner_family(receiver_type, method_name)
    if owner_family_rule is None:
        return

    request_role, _response_role = classify_http_roles(
        owner_family_rule,
        receiver_type=receiver_type,
        method_name=method_name,
    )
    assert request_role is None


@pytest.mark.parametrize(
    "receiver_type",
    [
        "org.apache.http.nio.client.HttpAsyncClient",
        "org.apache.http.impl.nio.client.CloseableHttpAsyncClient",
        "org.apache.http.impl.nio.client.CloseableHttpPipeliningClient",
        "org.apache.hc.client5.http.async.HttpAsyncClient",
        "org.apache.hc.client5.http.impl.async.CloseableHttpAsyncClient",
        "org.apache.hc.client5.http.impl.async.InternalHttpAsyncClient",
    ],
)
def test_apache_async_clients_classify_execute_as_request_event(
    receiver_type: str,
) -> None:
    owner_family_rule = resolve_http_owner_family(receiver_type, "execute")

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == "apache-httpclient.request"
    assert classify_http_roles(
        owner_family_rule,
        receiver_type=receiver_type,
        method_name="execute",
    ) == (HttpRequestRole.EVENT, None)


@pytest.mark.parametrize(
    "receiver_type",
    [
        "org.apache.http.client.fluent.Request",
        "org.apache.http.client.fluent.Executor",
        "org.apache.hc.client5.http.fluent.Request",
        "org.apache.hc.client5.http.fluent.Executor",
        "org.apache.hc.client5.http.fluent.Async",
        "org.apache.hc.client5.http.impl.classic.InternalHttpClient",
    ],
)
def test_apache_fluent_clients_classify_execute_as_request_event(
    receiver_type: str,
) -> None:
    owner_family_rule = resolve_http_owner_family(receiver_type, "execute")

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == "apache-httpclient.request"
    assert classify_http_roles(
        owner_family_rule,
        receiver_type=receiver_type,
        method_name="execute",
    ) == (HttpRequestRole.EVENT, None)


@pytest.mark.parametrize(
    ("receiver_type", "method_name", "http_method"),
    [
        ("org.apache.http.client.fluent.Request", "Get", "GET"),
        ("org.apache.hc.client5.http.fluent.Request", "post", "POST"),
        (
            "org.apache.hc.client5.http.async.methods.SimpleRequestBuilder",
            "put",
            "PUT",
        ),
        (
            "org.apache.hc.client5.http.async.methods.BasicRequestBuilder",
            "delete",
            "DELETE",
        ),
        (
            "org.apache.hc.client5.http.async.methods.SimpleHttpRequest",
            "setUri",
            "UNKNOWN",
        ),
        (
            "org.apache.hc.core5.http.support.AbstractRequestBuilder",
            "setPath",
            "UNKNOWN",
        ),
    ],
)
def test_apache_fluent_and_async_request_builders_classify_as_request_builders(
    receiver_type: str,
    method_name: str,
    http_method: str,
) -> None:
    owner_family_rule = resolve_http_owner_family(receiver_type, method_name)

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == "apache-httpclient.request"
    assert classify_http_roles(
        owner_family_rule,
        receiver_type=receiver_type,
        method_name=method_name,
    ) == (HttpRequestRole.BUILDER, None)
    assert (
        infer_owner_family_http_method(
            owner_family_rule,
            receiver_type=receiver_type,
            method_name=method_name,
        )
        == http_method
    )


@pytest.mark.parametrize(
    ("receiver_type", "method_name", "request_role", "http_method"),
    [
        (
            "org.springframework.web.client.RestClient",
            "post",
            HttpRequestRole.BUILDER,
            "POST",
        ),
        (
            "org.springframework.web.client.RestClient",
            "method",
            HttpRequestRole.BUILDER,
            "UNKNOWN",
        ),
        (
            "org.springframework.web.client.RestClient$RequestBodyUriSpec",
            "uri",
            HttpRequestRole.BUILDER,
            "UNKNOWN",
        ),
        (
            "org.springframework.web.client.RestClient$RequestBodySpec",
            "body",
            HttpRequestRole.BUILDER,
            "UNKNOWN",
        ),
        (
            "org.springframework.web.client.DefaultRestClient$DefaultRequestBodyUriSpec",
            "retrieve",
            HttpRequestRole.EVENT,
            "UNKNOWN",
        ),
        (
            "org.springframework.web.client.RestClient$RequestHeadersSpec",
            "exchangeForRequiredValue",
            HttpRequestRole.EVENT,
            "UNKNOWN",
        ),
    ],
)
def test_rest_client_owner_families_cover_request_roots_specs_and_events(
    receiver_type: str,
    method_name: str,
    request_role: HttpRequestRole,
    http_method: str,
) -> None:
    owner_family_rule = resolve_http_owner_family(receiver_type, method_name)

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == "rest-client.request"
    assert owner_family_rule.framework == HttpDispatchFramework.REST_CLIENT
    assert classify_http_roles(
        owner_family_rule,
        receiver_type=receiver_type,
        method_name=method_name,
    ) == (request_role, None)
    assert (
        infer_owner_family_http_method(
            owner_family_rule,
            receiver_type=receiver_type,
            method_name=method_name,
        )
        == http_method
    )


def test_legacy_restassured_webtestclient_module_is_not_classified() -> None:
    owner_family_rule = resolve_http_owner_family(
        "com.jayway.restassured.module.webtestclient.RestAssuredWebTestClient",
        "get",
    )

    assert owner_family_rule is None


def test_webtestclient_owner_families_cover_matchers_assertions_and_extractors() -> (
    None
):
    response_spec = (
        "org.springframework.test.web.reactive.server.WebTestClient$ResponseSpec"
    )
    response_rule = resolve_http_owner_family(response_spec, "expectStatus")
    assert response_rule is not None
    assert response_rule.family_id == "webtestclient.matcher_root"
    assert classify_http_roles(
        response_rule,
        receiver_type=response_spec,
        method_name="expectStatus",
    ) == (None, HttpResponseRole.MATCHER)
    assert classify_http_roles(
        response_rule,
        receiver_type=response_spec,
        method_name="expectHeader",
    ) == (None, HttpResponseRole.MATCHER)

    status_rule = resolve_http_owner_family(
        "org.springframework.test.web.reactive.server.StatusAssertions",
        "isOk",
    )
    assert status_rule is not None
    assert status_rule.family_id == "webtestclient.status_assertion"
    assert classify_http_roles(
        status_rule,
        receiver_type="org.springframework.test.web.reactive.server.StatusAssertions",
        method_name="isOk",
    ) == (None, HttpResponseRole.STATUS_ASSERTION)

    body_spec = (
        "org.springframework.test.web.reactive.server.WebTestClient$BodyContentSpec"
    )
    body_matcher_rule = resolve_http_owner_family(body_spec, "jsonPath")
    assert body_matcher_rule is not None
    assert body_matcher_rule.family_id == "webtestclient.body_assertion"
    assert classify_http_roles(
        body_matcher_rule,
        receiver_type=body_spec,
        method_name="jsonPath",
    ) == (None, HttpResponseRole.MATCHER)

    extractor_rule = resolve_http_owner_family(body_spec, "returnResult")
    assert extractor_rule is not None
    assert extractor_rule.family_id == "webtestclient.response_extractor"
    assert classify_http_roles(
        extractor_rule,
        receiver_type=body_spec,
        method_name="returnResult",
    ) == (None, HttpResponseRole.EXTRACTOR)


def test_rest_assured_owner_families_cover_inspection_assertion_and_extraction() -> (
    None
):
    then_rule = resolve_http_owner_family("io.restassured.response.Response", "then")
    assert then_rule is not None
    assert then_rule.family_id == "rest-assured.response_inspector"
    assert classify_http_roles(
        then_rule,
        receiver_type="io.restassured.response.Response",
        method_name="then",
    ) == (None, HttpResponseRole.INSPECTOR)

    status_rule = resolve_http_owner_family(
        "io.restassured.response.ValidatableResponse",
        "statusCode",
    )
    assert status_rule is not None
    assert status_rule.family_id == "rest-assured.status_assertion"
    assert classify_http_roles(
        status_rule,
        receiver_type="io.restassured.response.ValidatableResponse",
        method_name="statusCode",
    ) == (None, HttpResponseRole.STATUS_ASSERTION)

    extractor_rule = resolve_http_owner_family(
        "io.restassured.response.ValidatableResponse",
        "extract",
    )
    assert extractor_rule is not None
    assert extractor_rule.family_id == "rest-assured.response_extractor"
    assert classify_http_roles(
        extractor_rule,
        receiver_type="io.restassured.response.ValidatableResponse",
        method_name="extract",
    ) == (None, HttpResponseRole.EXTRACTOR)


@pytest.mark.parametrize(
    ("receiver_type", "method_name", "family_id", "response_role"),
    [
        (
            "io.restassured.module.mockmvc.response.MockMvcResponse",
            "then",
            "rest-assured.response_inspector",
            HttpResponseRole.INSPECTOR,
        ),
        (
            "com.jayway.restassured.module.mockmvc.response.MockMvcResponse",
            "then",
            "rest-assured.response_inspector",
            HttpResponseRole.INSPECTOR,
        ),
        (
            "io.restassured.module.webtestclient.response.WebTestClientResponse",
            "then",
            "rest-assured.response_inspector",
            HttpResponseRole.INSPECTOR,
        ),
        (
            "io.restassured.module.mockmvc.response.ValidatableMockMvcResponse",
            "statusCode",
            "rest-assured.status_assertion",
            HttpResponseRole.STATUS_ASSERTION,
        ),
        (
            "com.jayway.restassured.module.mockmvc.response.ValidatableMockMvcResponse",
            "body",
            "rest-assured.body_assertion",
            HttpResponseRole.BODY_ASSERTION,
        ),
        (
            "io.restassured.module.webtestclient.response.ValidatableWebTestClientResponse",
            "extract",
            "rest-assured.response_extractor",
            HttpResponseRole.EXTRACTOR,
        ),
        (
            "io.restassured.module.webtestclient.internal.ValidatableWebTestClientResponseImpl",
            "header",
            "rest-assured.body_assertion",
            HttpResponseRole.HEADER_ASSERTION,
        ),
    ],
)
def test_rest_assured_module_responses_classify_like_core_responses(
    receiver_type: str,
    method_name: str,
    family_id: str,
    response_role: HttpResponseRole,
) -> None:
    owner_family_rule = resolve_http_owner_family(receiver_type, method_name)

    assert owner_family_rule is not None
    assert owner_family_rule.family_id == family_id
    assert classify_http_roles(
        owner_family_rule,
        receiver_type=receiver_type,
        method_name=method_name,
    ) == (None, response_role)


def test_response_roles_remain_internal_and_do_not_emit_http_calls() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="exchange",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$RequestHeadersSpec"
                ),
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=24,
            ),
            make_call_site(
                method_name="expectStatus",
                receiver_type=(
                    "org.springframework.test.web.reactive.server."
                    "WebTestClient$ResponseSpec"
                ),
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=38,
            ),
            make_call_site(
                method_name="isOk",
                receiver_type="org.springframework.test.web.reactive.server.StatusAssertions",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=45,
            ),
        ]
    )
    grouping = build_call_site_grouping(method.call_sites)

    _classify_http_on_grouping_for_testing(
        grouping=grouping,
        method_details=method,
        static_import_index=StaticImportIndex.EMPTY,
    )
    classified_nodes = {
        node.call_site.method_name: node.http_classification for node in grouping.nodes
    }

    assert classified_nodes["exchange"] is not None
    assert classified_nodes["exchange"].request_role == HttpRequestRole.EVENT
    assert classified_nodes["expectStatus"] is not None
    assert classified_nodes["expectStatus"].response_role == HttpResponseRole.MATCHER
    assert classified_nodes["isOk"] is not None
    assert classified_nodes["isOk"].response_role == HttpResponseRole.STATUS_ASSERTION

    # Only the request-role node (exchange) should have request_role set;
    # response-role nodes (expectStatus, isOk) should not.
    request_nodes = [
        node
        for node in grouping.nodes
        if node.http_classification is not None
        and node.http_classification.request_role is not None
    ]
    assert len(request_nodes) == 1
    assert request_nodes[0].call_site.method_name == "exchange"


def test_static_imported_mockmvc_matcher_root_is_not_emitted_as_http() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="content",
                receiver_type="",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=9,
            )
        ]
    )
    grouping = build_call_site_grouping(method.call_sites)
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="org.springframework.test.web.servlet.result.MockMvcResultMatchers",
                is_static=True,
                is_wildcard=True,
            )
        ]
    )

    _classify_http_on_grouping_for_testing(
        grouping=grouping,
        method_details=method,
        static_import_index=static_import_index,
    )

    node = next(iter(grouping.nodes))
    assert node.http_classification is not None
    assert node.http_classification.owner_family == "mockmvc.matcher_root"
    assert node.http_classification.request_role is None
    assert node.http_classification.response_role == HttpResponseRole.MATCHER
    assert node.endpoint_candidate is None

    # Response-role-only nodes should not have request_role set.
    assert node.http_classification.request_role is None


def test_http_static_import_owner_surface_is_explicitly_audited() -> None:
    actual_surface = {
        (
            owner_family_rule.framework,
            owner_family_rule.family_id,
            owner_family_rule.receiver_prefixes,
            owner_family_rule.exact_receiver_types,
            owner_family_rule.static_import_owners,
            tuple(sorted(owner_family_rule.static_import_methods)),
        )
        for owner_family_rule in HTTP_OWNER_FAMILY_RULES
        if owner_family_rule.static_import_owners
    }

    assert actual_surface == _EXPECTED_STATIC_IMPORT_OWNER_SURFACE


def test_http_owner_families_without_static_imports_are_explicitly_audited() -> None:
    actual_empty_rules = {
        (
            owner_family_rule.framework,
            owner_family_rule.family_id,
            owner_family_rule.receiver_prefixes,
            owner_family_rule.exact_receiver_types,
        )
        for owner_family_rule in HTTP_OWNER_FAMILY_RULES
        if not owner_family_rule.static_import_owners
    }

    assert actual_empty_rules == _EXPECTED_EMPTY_STATIC_IMPORT_RULES


@pytest.mark.parametrize(
    (
        "owner_class_name",
        "method_name",
        "expected_owner_family",
        "expected_request_role",
        "expected_response_role",
    ),
    _STATIC_IMPORT_OWNER_CASES,
)
def test_http_static_import_owner_breadth_maps_all_registered_methods(
    owner_class_name: str,
    method_name: str,
    expected_owner_family: str,
    expected_request_role: HttpRequestRole | None,
    expected_response_role: HttpResponseRole | None,
) -> None:
    index = StaticImportIndex.from_import_entries(
        [JImport(path=owner_class_name, is_static=True, is_wildcard=True)]
    )

    resolved_receiver = index.resolve(method_name)
    assert resolved_receiver is not None

    rule = resolve_http_owner_family(resolved_receiver, method_name)
    assert rule is not None
    assert rule.family_id == expected_owner_family

    request_role, response_role = classify_http_roles(
        rule, receiver_type=resolved_receiver, method_name=method_name
    )
    assert request_role == expected_request_role
    assert response_role == expected_response_role


@pytest.mark.parametrize(
    (
        "owner_class_name",
        "method_name",
        "expected_owner_family",
        "expected_request_role",
        "expected_response_role",
    ),
    _STATIC_IMPORT_OWNER_CASES,
)
def test_http_named_static_import_owner_breadth_maps_all_registered_methods(
    owner_class_name: str,
    method_name: str,
    expected_owner_family: str,
    expected_request_role: HttpRequestRole | None,
    expected_response_role: HttpResponseRole | None,
) -> None:
    index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path=f"{owner_class_name}.{method_name}",
                is_static=True,
                is_wildcard=False,
            )
        ]
    )

    resolved_receiver = index.resolve(method_name)
    assert resolved_receiver is not None

    rule = resolve_http_owner_family(resolved_receiver, method_name)
    assert rule is not None
    assert rule.family_id == expected_owner_family

    request_role, response_role = classify_http_roles(
        rule, receiver_type=resolved_receiver, method_name=method_name
    )
    assert request_role == expected_request_role
    assert response_role == expected_response_role
