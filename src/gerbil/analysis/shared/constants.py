from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from gerbil.analysis.schema import (
    HttpDispatchFramework,
    TestingFramework,
)

# ---------------------------------------------------------------------------
# fixture_constants
# ---------------------------------------------------------------------------

SETUP_ANNOTATIONS: Final[set[str]] = {
    "@Before",
    "@BeforeEach",
    "@BeforeAll",
    "@BeforeClass",
    "@BeforeMethod",
    "@BeforeSuite",
    "@BeforeTest",
    "@BeforeGroups",
}

TEARDOWN_ANNOTATIONS: Final[set[str]] = {
    "@After",
    "@AfterEach",
    "@AfterAll",
    "@AfterClass",
    "@AfterMethod",
    "@AfterSuite",
    "@AfterTest",
    "@AfterGroups",
}

# Fixture execution priority: lower number = runs first.
# Setup: suite < test < class < method.
SETUP_ANNOTATION_PRIORITY: Final[dict[str, int]] = {
    "@BeforeSuite": -2,
    "@BeforeTest": -1,
    "@BeforeAll": 0,
    "@BeforeClass": 0,
    "@BeforeGroups": 0,
    "@BeforeEach": 1,
    "@Before": 1,
    "@BeforeMethod": 1,
}

# Teardown: method < class < test < suite (reverse of setup).
TEARDOWN_ANNOTATION_PRIORITY: Final[dict[str, int]] = {
    "@AfterEach": 0,
    "@After": 0,
    "@AfterMethod": 0,
    "@AfterAll": 1,
    "@AfterClass": 1,
    "@AfterGroups": 1,
    "@AfterTest": 2,
    "@AfterSuite": 3,
}

# ---------------------------------------------------------------------------
# framework_constants
# ---------------------------------------------------------------------------

TEST_DIRS: Final[tuple[str, ...]] = (
    "src/test/java",
    "src/integrationTest/java",
    "src/functionalTest/java",
)

# RestAssured 3.0 renamed its root package com.jayway.restassured -> io.restassured
# while keeping identical subpackage/class names, so both roots share one rule set.
REST_ASSURED_ROOT_PACKAGES: Final[tuple[str, ...]] = (
    "io.restassured.",
    "com.jayway.restassured.",
)


def _rest_assured_prefixes(*module_suffixes: str) -> tuple[str, ...]:
    return tuple(
        f"{root}{suffix}"
        for root in REST_ASSURED_ROOT_PACKAGES
        for suffix in module_suffixes
    )


FRAMEWORK_PREFIXES: Final[dict[str, TestingFramework]] = {
    "org.junit.jupiter.": TestingFramework.JUNIT5,
    "org.junit.": TestingFramework.JUNIT4,
    "junit.framework.": TestingFramework.JUNIT3,
    "org.testng.": TestingFramework.TESTNG,
    "org.assertj.": TestingFramework.ASSERTJ,
    "org.hamcrest.": TestingFramework.HAMCREST,
    "com.google.common.truth.": TestingFramework.GOOGLE_TRUTH,
    "org.mockito.": TestingFramework.MOCKITO,
    "org.easymock.": TestingFramework.EASYMOCK,
    "org.powermock.": TestingFramework.POWERMOCK,
    # JMockit's groupId is org.jmockit but its Java package is `mockit`.
    "mockit.": TestingFramework.JMOCKIT,
    "org.jmock.": TestingFramework.JMOCK,
    "org.springframework.boot.test.": TestingFramework.SPRING_TEST,
    "org.springframework.test.": TestingFramework.SPRING_TEST,
    **{root: TestingFramework.REST_ASSURED for root in REST_ASSURED_ROOT_PACKAGES},
    # Karate 1.x ships under com.intuit.karate; 2.x moved to io.karatelabs.
    "com.intuit.karate.": TestingFramework.KARATE,
    "io.karatelabs.": TestingFramework.KARATE,
    "au.com.dius.pact.": TestingFramework.PACT,
    "org.citrusframework.": TestingFramework.CITRUS,
}

# Order matters: longest prefixes first for first-match detection.
SORTED_FRAMEWORK_PREFIXES: Final[list[tuple[str, TestingFramework]]] = sorted(
    FRAMEWORK_PREFIXES.items(), key=lambda item: len(item[0]), reverse=True
)


@dataclass(frozen=True)
class FrameworkDecompositionRule:
    parent_framework: TestingFramework
    prefix: str
    framework: HttpDispatchFramework
    annotation_hints: tuple[str, ...] = ()


FRAMEWORK_DECOMPOSITION_RULES: Final[tuple[FrameworkDecompositionRule, ...]] = (
    FrameworkDecompositionRule(
        parent_framework=TestingFramework.SPRING_TEST,
        prefix="org.springframework.test.web.servlet.",
        framework=HttpDispatchFramework.MOCKMVC,
        annotation_hints=("@WebMvcTest", "@AutoConfigureMockMvc"),
    ),
    FrameworkDecompositionRule(
        parent_framework=TestingFramework.SPRING_TEST,
        prefix="org.springframework.test.web.reactive.server.",
        framework=HttpDispatchFramework.WEBTESTCLIENT,
        annotation_hints=("@WebFluxTest",),
    ),
    FrameworkDecompositionRule(
        parent_framework=TestingFramework.SPRING_TEST,
        prefix="org.springframework.boot.test.web.client",
        framework=HttpDispatchFramework.TEST_REST_TEMPLATE,
    ),
)

SPRING_DECOMPOSITION_RULES: Final[tuple[FrameworkDecompositionRule, ...]] = tuple(
    rule
    for rule in FRAMEWORK_DECOMPOSITION_RULES
    if rule.parent_framework == TestingFramework.SPRING_TEST
)

# Order matters: longest prefixes first for first-match detection.
SORTED_SPRING_DECOMPOSITION_PREFIXES: Final[list[tuple[str, HttpDispatchFramework]]] = (
    sorted(
        [(rule.prefix, rule.framework) for rule in SPRING_DECOMPOSITION_RULES],
        key=lambda item: len(item[0]),
        reverse=True,
    )
)

SPRING_DECOMPOSITION_ANNOTATION_HINTS: Final[dict[str, HttpDispatchFramework]] = {
    annotation: rule.framework
    for rule in SPRING_DECOMPOSITION_RULES
    for annotation in rule.annotation_hints
}

IN_PROCESS_DISPATCH_FRAMEWORKS: Final[set[HttpDispatchFramework]] = {
    HttpDispatchFramework.MOCKMVC,
}

REAL_HTTP_DISPATCH_FRAMEWORKS: Final[set[HttpDispatchFramework]] = {
    HttpDispatchFramework.TEST_REST_TEMPLATE,
    HttpDispatchFramework.REST_TEMPLATE,
    HttpDispatchFramework.REST_CLIENT,
    HttpDispatchFramework.JAVA_HTTPCLIENT,
    # @MicronautTest starts the application's EmbeddedServer and injected
    # @Client HttpClients dispatch to it over real HTTP (micronaut-test guide).
    HttpDispatchFramework.MICRONAUT_CLIENT,
    HttpDispatchFramework.APACHE_HTTPCLIENT,
    HttpDispatchFramework.OKHTTP,
    HttpDispatchFramework.REST_ASSURED,
    HttpDispatchFramework.WEBCLIENT,
    HttpDispatchFramework.JAX_RS,
    HttpDispatchFramework.FEIGN,
    HttpDispatchFramework.HTTP_INTERFACE,
    HttpDispatchFramework.KARATE,
    # Pact dispatch is over real HTTP (consumer mock server / provider replay),
    # but the Pact DSL is builder-only, so consumer-test events come from the
    # user's own client framework and this entry stays dormant until a
    # PACT-owned event classification exists.
    HttpDispatchFramework.PACT,
    HttpDispatchFramework.CITRUS,
}

REST_ASSURED_IN_PROCESS_RECEIVER_PREFIXES: Final[tuple[str, ...]] = (
    *_rest_assured_prefixes("module.mockmvc."),
    # The webtestclient module ships only under io.restassured (added in 4.x, post-rename).
    "io.restassured.module.webtestclient.",
)

MODAL_DISPATCH_FRAMEWORK: Final[HttpDispatchFramework] = (
    HttpDispatchFramework.WEBTESTCLIENT
)

TEST_ANNOTATIONS: Final[set[str]] = {
    "@Test",
    "@ParameterizedTest",
    "@TestFactory",
    "@RepeatedTest",
}

# ---------------------------------------------------------------------------
# classification_constants
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Dependency strategy: Tier 1 — Environment annotations (always active)
# ---------------------------------------------------------------------------

MOCKED_ENVIRONMENT_ANNOTATIONS: Final[set[str]] = {
    "@MockBean",
    "@SpyBean",
    "@MockitoBean",
    "@MockitoSpyBean",
    "@InjectMocks",
    "@InjectMock",
}

MOCKED_FIELD_ANNOTATIONS: Final[set[str]] = {"@Mock", "@Spy", "@Captor"}

MOCKED_RECEIVER_HINTS: Final[set[str]] = {
    "org.mockito.",
    "org.easymock.",
    "org.powermock.",
    "mockit.",
    "org.jmock.",
}

MOCKED_CALL_NAMES: Final[set[str]] = {
    "mock",
    "spy",
    "when",
    "thenReturn",
    "verify",
    "given",
    "willReturn",
    "expect",
    "andReturn",
    "replay",
}

VIRTUALIZED_ENVIRONMENT_ANNOTATIONS: Final[set[str]] = {
    "@WireMockTest",
    "@AutoConfigureWireMock",
}

VIRTUALIZED_STRATEGY_RECEIVER_HINTS: Final[set[str]] = {
    "com.github.tomakehurst.wiremock.",
    "org.mockserver.client.",
    "com.mbtest.mountebank",
    "io.specto.hoverfly",
}

CONTAINERIZED_ENVIRONMENT_ANNOTATIONS: Final[set[str]] = {"@Testcontainers"}
CONTAINERIZED_FIELD_ANNOTATIONS: Final[set[str]] = {"@Container"}

CONTAINERIZED_RECEIVER_HINTS: Final[set[str]] = {"org.testcontainers."}

# Convenience union for static import resolution (replaces old SIMULATED_RECEIVER_HINTS)
STATIC_IMPORT_RECEIVER_HINTS: Final[set[str]] = (
    MOCKED_RECEIVER_HINTS | VIRTUALIZED_STRATEGY_RECEIVER_HINTS
)

VIRTUALIZED_STATIC_METHODS_BY_RECEIVER: Final[dict[str, set[str]]] = {
    "com.github.tomakehurst.wiremock.client.WireMock": {
        "aResponse",
        "any",
        "anyRequestedFor",
        "created",
        "delete",
        "deleteRequestedFor",
        "equalTo",
        "get",
        "getRequestedFor",
        "head",
        "matching",
        "matchingJsonPath",
        "notFound",
        "ok",
        "okForContentType",
        "okJson",
        "options",
        "patch",
        "post",
        "postRequestedFor",
        "put",
        "serverError",
        "status",
        "stubFor",
        "urlEqualTo",
        "urlPathEqualTo",
        "verify",
    },
    "org.mockserver.client.MockServerClient": {
        "when",
        "verify",
        "clear",
        "reset",
        "stop",
        "retrieveRecordedRequests",
        "retrieveRecordedExpectations",
    },
    "io.specto.hoverfly": {
        "service",
        "response",
        "success",
        "serverError",
        "created",
        "badRequest",
        "unauthorised",
        "forbidden",
    },
    "com.mbtest.mountebank": {
        "stub",
        "response",
        "predicate",
        "when",
    },
}


OBSERVATION_MEDIUM_DB_QUERY_RECEIVER_METHODS: Final[dict[str, set[str]]] = {
    "org.springframework.data.": {
        "findById",
        "findByName",
        "findAll",
        "findOne",
        "count",
        "exists",
        "existsById",
    },
    "org.springframework.jdbc.": {
        "queryForObject",
        "queryForList",
        "queryForMap",
        "query",
    },
    "javax.persistence.EntityManager": {
        "find",
        "createQuery",
        "createNativeQuery",
    },
    "jakarta.persistence.EntityManager": {
        "find",
        "createQuery",
        "createNativeQuery",
    },
    "org.springframework.boot.test.autoconfigure.orm.jpa.TestEntityManager": {"find"},
    "org.springframework.test.jdbc.JdbcTestUtils": {"countRowsInTable"},
    "org.jooq.DSLContext": {
        "fetch",
        "fetchOne",
        "fetchOptional",
        "fetchAny",
    },
}

OBSERVATION_MEDIUM_MQ_RECEIVER_METHODS: Final[dict[str, set[str]]] = {
    "org.apache.kafka.clients.consumer.KafkaConsumer": {"poll"},
    "org.springframework.amqp.rabbit.core.RabbitTemplate": {
        "receive",
        "receiveAndConvert",
    },
    "org.springframework.jms.core.JmsTemplate": {"receive", "receiveSelected"},
    "org.springframework.kafka.test.EmbeddedKafkaBroker": {
        "consumeFromAllEmbeddedTopics"
    },
}

OBSERVATION_MEDIUM_FS_RECEIVER_METHODS: Final[dict[str, set[str]]] = {
    "java.nio.file.Files": {
        "exists",
        "readString",
        "readAllBytes",
        "readAllLines",
        "newBufferedReader",
        "size",
        "list",
        "lines",
    },
    "java.io.File": {"exists", "length"},
}

CONTRACT_METHOD_HINTS: Final[set[str]] = {
    "matchesJsonSchemaInClasspath",
    "matchesJsonSchema",
    "validatesAgainst",
    "satisfiesContract",
}

CONTRACT_RECEIVER_PREFIXES: Final[tuple[str, ...]] = (
    "au.com.dius.pact.",
    *_rest_assured_prefixes("module.jsv."),
    "org.everit.json.schema.",
    "com.networknt.schema.",
    "com.atlassian.oai.validator.",
    "org.springframework.cloud.contract.",
)

STRONG_PROPERTY_METHODS: Final[set[str]] = {"forAll", "qt"}
AMBIGUOUS_PROPERTY_METHODS: Final[set[str]] = {"check"}

PROPERTY_RECEIVER_PREFIXES: Final[tuple[str, ...]] = (
    "net.jqwik.",
    "org.quicktheories.",
    "com.pholser.junit.quickcheck.",
)

WAIT_SIGNAL_RECEIVER_METHODS: Final[dict[str, set[str]]] = {
    "org.awaitility.": {"await", "untilasserted", "until"},
    "java.lang.Thread": {"sleep"},
    "java.util.concurrent.CountDownLatch": {"await"},
    "java.util.concurrent.CompletableFuture": {"get"},
}

FAILURE_EXCEPTION_HINTS: Final[set[str]] = {
    "assertThrows",
    "assertThrowsExactly",
    "expectThrows",
    "assertThatThrownBy",
    "assertThatExceptionOfType",
    "fail",
}

AUTH_HEADER_HINTS: Final[set[str]] = {
    "Authorization",
    "Bearer",
    "Basic",
    "X-API-Key",
}

SPRING_SECURITY_MOCKMVC_REQUEST_POST_PROCESSORS: Final[str] = (
    "org.springframework.security.test.web.servlet.request."
    "SecurityMockMvcRequestPostProcessors"
)

AUTH_MOCKED_STATIC_METHODS_BY_RECEIVER: Final[dict[str, set[str]]] = {
    SPRING_SECURITY_MOCKMVC_REQUEST_POST_PROCESSORS: {
        "authentication",
        "jwt",
        "opaqueToken",
        "oauth2Client",
        "oauth2Login",
        "oidcLogin",
        "securityContext",
        "testSecurityContext",
        "user",
        "x509",
    }
}

AUTH_BYPASSED_STATIC_METHODS_BY_RECEIVER: Final[dict[str, set[str]]] = {
    SPRING_SECURITY_MOCKMVC_REQUEST_POST_PROCESSORS: {"anonymous"}
}

AUTH_TEST_TOKEN_STATIC_METHODS_BY_RECEIVER: Final[dict[str, set[str]]] = {
    SPRING_SECURITY_MOCKMVC_REQUEST_POST_PROCESSORS: {"digest", "httpBasic"}
}

AUTH_STATIC_IMPORT_METHODS_BY_RECEIVER: Final[dict[str, set[str]]] = {
    SPRING_SECURITY_MOCKMVC_REQUEST_POST_PROCESSORS: (
        AUTH_MOCKED_STATIC_METHODS_BY_RECEIVER[
            SPRING_SECURITY_MOCKMVC_REQUEST_POST_PROCESSORS
        ]
        | AUTH_BYPASSED_STATIC_METHODS_BY_RECEIVER[
            SPRING_SECURITY_MOCKMVC_REQUEST_POST_PROCESSORS
        ]
        | AUTH_TEST_TOKEN_STATIC_METHODS_BY_RECEIVER[
            SPRING_SECURITY_MOCKMVC_REQUEST_POST_PROCESSORS
        ]
        | {"csrf"}
    )
}

DB_SEEDING_ANNOTATIONS: Final[set[str]] = {
    "@Sql",
    "@SqlGroup",
    "@SqlConfig",
    "@DataSet",
    "@DatabaseSetup",
    "@FlywayTest",
}

# Post-test DB comparison annotations: database-rider's @ExpectedDataSet and
# spring-test-dbunit's @ExpectedDatabase assert database state after the test
# runs, so they are state observations (oracles), not seeding.
DB_STATE_ASSERTION_ANNOTATIONS: Final[set[str]] = {
    "@ExpectedDataSet",
    "@ExpectedDatabase",
}

CONTAINER_BOOTSTRAP_ANNOTATIONS: Final[set[str]] = {
    "@Testcontainers",
    "@Container",
}

# Precondition-specific receiver/method maps for Tier 2 (programmatic) seeding
# detection over SETUP-phase runtime events. Intentionally decoupled from
# OBSERVATION_MEDIUM_* maps, which describe state observation (reads), not
# seeding (writes); keeping the taxonomies separate lets each evolve without
# cross-impact.
DB_SEEDING_RECEIVER_METHODS: Final[dict[str, set[str]]] = {
    "org.springframework.jdbc.": {"update", "batchUpdate", "execute"},
    # Repository supertypes live in subpackages like data.jpa.repository, so the
    # prefix must cover the org.springframework.data. root; method names gate it.
    "org.springframework.data.": {
        "save",
        "saveAll",
        "insert",
        "insertAll",
    },
    "javax.persistence.EntityManager": {"persist", "merge"},
    "jakarta.persistence.EntityManager": {"persist", "merge"},
    "org.jooq.DSLContext": {"insertInto", "mergeInto", "batchInsert"},
    "org.springframework.jdbc.datasource.init.ResourceDatabasePopulator": {
        "populate",
        "execute",
    },
}

CONTAINER_BOOTSTRAP_RECEIVER_METHODS: Final[dict[str, set[str]]] = {
    "org.testcontainers.": {"start"},
}

MQ_SEEDING_RECEIVER_METHODS: Final[dict[str, set[str]]] = {
    "org.springframework.kafka.core.KafkaTemplate": {"send", "sendDefault"},
    "org.apache.kafka.clients.producer.KafkaProducer": {"send"},
    "org.springframework.amqp.rabbit.core.RabbitTemplate": {
        "send",
        "convertAndSend",
    },
    "org.springframework.jms.core.JmsTemplate": {"send", "convertAndSend"},
}

FS_SEEDING_RECEIVER_METHODS: Final[dict[str, set[str]]] = {
    "java.nio.file.Files": {
        "write",
        "writeString",
        "createFile",
        "createDirectory",
        "createDirectories",
        "createTempFile",
        "createTempDirectory",
        "copy",
    },
}


__all__ = [
    # fixture_constants
    "SETUP_ANNOTATIONS",
    "SETUP_ANNOTATION_PRIORITY",
    "TEARDOWN_ANNOTATIONS",
    "TEARDOWN_ANNOTATION_PRIORITY",
    # framework_constants
    "FRAMEWORK_PREFIXES",
    "SORTED_FRAMEWORK_PREFIXES",
    "SORTED_SPRING_DECOMPOSITION_PREFIXES",
    "SPRING_DECOMPOSITION_ANNOTATION_HINTS",
    "IN_PROCESS_DISPATCH_FRAMEWORKS",
    "REAL_HTTP_DISPATCH_FRAMEWORKS",
    "REST_ASSURED_IN_PROCESS_RECEIVER_PREFIXES",
    "MODAL_DISPATCH_FRAMEWORK",
    "TEST_ANNOTATIONS",
    "TEST_DIRS",
    # classification_constants
    "AUTH_HEADER_HINTS",
    "AUTH_BYPASSED_STATIC_METHODS_BY_RECEIVER",
    "AUTH_MOCKED_STATIC_METHODS_BY_RECEIVER",
    "AUTH_STATIC_IMPORT_METHODS_BY_RECEIVER",
    "AUTH_TEST_TOKEN_STATIC_METHODS_BY_RECEIVER",
    "AMBIGUOUS_PROPERTY_METHODS",
    "CONTRACT_METHOD_HINTS",
    "CONTRACT_RECEIVER_PREFIXES",
    "CONTAINERIZED_ENVIRONMENT_ANNOTATIONS",
    "CONTAINERIZED_FIELD_ANNOTATIONS",
    "CONTAINERIZED_RECEIVER_HINTS",
    "FAILURE_EXCEPTION_HINTS",
    "MOCKED_CALL_NAMES",
    "MOCKED_ENVIRONMENT_ANNOTATIONS",
    "MOCKED_FIELD_ANNOTATIONS",
    "MOCKED_RECEIVER_HINTS",
    "OBSERVATION_MEDIUM_DB_QUERY_RECEIVER_METHODS",
    "OBSERVATION_MEDIUM_FS_RECEIVER_METHODS",
    "OBSERVATION_MEDIUM_MQ_RECEIVER_METHODS",
    "CONTAINER_BOOTSTRAP_ANNOTATIONS",
    "CONTAINER_BOOTSTRAP_RECEIVER_METHODS",
    "DB_SEEDING_ANNOTATIONS",
    "DB_SEEDING_RECEIVER_METHODS",
    "DB_STATE_ASSERTION_ANNOTATIONS",
    "FS_SEEDING_RECEIVER_METHODS",
    "MQ_SEEDING_RECEIVER_METHODS",
    "PROPERTY_RECEIVER_PREFIXES",
    "STATIC_IMPORT_RECEIVER_HINTS",
    "STRONG_PROPERTY_METHODS",
    "VIRTUALIZED_ENVIRONMENT_ANNOTATIONS",
    "VIRTUALIZED_STATIC_METHODS_BY_RECEIVER",
    "VIRTUALIZED_STRATEGY_RECEIVER_HINTS",
    "WAIT_SIGNAL_RECEIVER_METHODS",
]
