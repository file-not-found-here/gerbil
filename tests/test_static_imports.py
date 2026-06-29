from __future__ import annotations

from cldk.models.java import JImport
import pytest
from gerbil.analysis.shared import CommonAnalysis
from gerbil.analysis.schema import HttpRequestRole, HttpResponseRole
from gerbil.analysis.http.framework_registry import (
    resolve_http_owner_family,
)
from gerbil.analysis.shared.static_imports import (
    StaticImportIndex,
    matches_receiver_prefix,
)
from gerbil.analysis.shared.reachability import Reachability
from gerbil.analysis.shared.receiver_resolution import (
    resolve_receiver as _resolve_receiver,
)
from gerbil.analysis.runtime.call_sites import MethodRef
from tests.cldk_factories import (
    classify_http_roles,
    make_callable,
    make_call_site,
    make_import_declaration,
    make_type,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def resolve_receiver(
    *,
    call_site,
    static_import_index: StaticImportIndex,
):
    owner_class_name = "example.ApiTest"
    analysis = FakeJavaAnalysis(classes={owner_class_name: make_type()})
    common_analysis = CommonAnalysis(analysis)
    return _resolve_receiver(
        call_site=call_site,
        static_import_index=static_import_index,
        owner_class_name=owner_class_name,
        owner_method_details=None,
        analysis=analysis,
        get_class_imports_for_class=common_analysis.get_class_imports,
        get_superclass_chain_for_class=common_analysis.get_superclass_chain,
    )


class TestMatchesReceiverPrefix:
    def test_exact_match(self) -> None:
        assert matches_receiver_prefix("java.lang.Thread", "java.lang.Thread")

    def test_prefix_with_dot(self) -> None:
        assert matches_receiver_prefix("org.mockito.Mockito", "org.mockito.")

    def test_prefix_without_dot_boundary(self) -> None:
        assert matches_receiver_prefix("org.mockito.Mockito", "org.mockito")

    def test_no_match(self) -> None:
        assert not matches_receiver_prefix("com.example.Foo", "org.mockito.")

    def test_empty_receiver(self) -> None:
        assert not matches_receiver_prefix("", "org.mockito.")

    def test_empty_prefix(self) -> None:
        assert not matches_receiver_prefix("org.mockito.Mockito", "")

    def test_dollar_boundary(self) -> None:
        assert matches_receiver_prefix("org.mockito$Inner", "org.mockito")


class TestStaticImportIndex:
    def test_wildcard_static_import_registers_mockmvc_request_builders(self) -> None:
        receiver = "org.springframework.test.web.servlet.request.MockMvcRequestBuilders"
        index = StaticImportIndex.from_import_entries(
            [JImport(path=receiver, is_static=True, is_wildcard=True)]
        )

        assert index.resolve("get") == receiver
        assert index.resolve("post") == receiver

    def test_wildcard_static_import_registers_restdocs_mockmvc_request_builders(
        self,
    ) -> None:
        receiver = (
            "org.springframework.restdocs.mockmvc.RestDocumentationRequestBuilders"
        )
        index = StaticImportIndex.from_import_entries(
            [JImport(path=receiver, is_static=True, is_wildcard=True)]
        )

        assert index.resolve("get") == receiver
        assert index.resolve("post") == receiver
        assert index.resolve("asyncDispatch") is None

    def test_wildcard_static_import_registers_spring_security_post_processors(
        self,
    ) -> None:
        receiver = (
            "org.springframework.security.test.web.servlet.request."
            "SecurityMockMvcRequestPostProcessors"
        )
        index = StaticImportIndex.from_import_entries(
            [JImport(path=receiver, is_static=True, is_wildcard=True)]
        )

        for method_name in [
            "anonymous",
            "csrf",
            "digest",
            "httpBasic",
            "opaqueToken",
            "testSecurityContext",
            "user",
        ]:
            assert index.resolve(method_name) == receiver

    def test_wildcard_static_import_registers_property_methods(self) -> None:
        receiver = "net.jqwik.api.PropertyChecker"
        index = StaticImportIndex.from_import_entries(
            [JImport(path=receiver, is_static=True, is_wildcard=True)]
        )

        assert index.resolve("check") == receiver
        assert index.resolve("forAll") == receiver

    def test_wildcard_static_import_registers_quicktheories_property_methods(
        self,
    ) -> None:
        receiver = "org.quicktheories.QuickTheory"
        index = StaticImportIndex.from_import_entries(
            [JImport(path=receiver, is_static=True, is_wildcard=True)]
        )

        assert index.resolve("qt") == receiver
        assert index.resolve("forAll") == receiver

    def test_wildcard_static_import_registers_awaitility_wait_methods(self) -> None:
        receiver = "org.awaitility.Awaitility"
        index = StaticImportIndex.from_import_entries(
            [JImport(path=receiver, is_static=True, is_wildcard=True)]
        )

        assert index.resolve("await") == receiver
        assert index.resolve("untilAsserted") == receiver
        assert index.resolve("until") == receiver

    def test_wildcard_static_import_matches_sibling_framework_class_methods(
        self,
    ) -> None:
        receiver = "org.springframework.test.web.servlet.result.MockMvcResultMatchers"
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=receiver,
                    is_static=True,
                    is_wildcard=True,
                )
            ]
        )

        assert index.resolve("status") == receiver
        assert index.resolve("jsonPath") == receiver
        assert index.has_method("status") is True

    def test_wildcard_static_import_registers_mockito_spy(self) -> None:
        receiver = "org.mockito.Mockito"
        index = StaticImportIndex.from_import_entries(
            [JImport(path=receiver, is_static=True, is_wildcard=True)]
        )

        assert index.resolve("spy") == receiver
        assert index.has_method("spy") is True

    def test_wildcard_static_import_registers_wiremock_fixture_helpers(
        self,
    ) -> None:
        receiver = "com.github.tomakehurst.wiremock.client.WireMock"
        index = StaticImportIndex.from_import_entries(
            [JImport(path=receiver, is_static=True, is_wildcard=True)]
        )

        assert index.resolve("created") == receiver
        assert index.resolve("matchingJsonPath") == receiver
        assert index.resolve("postRequestedFor") == receiver
        assert index.resolve("deleteRequestedFor") == receiver
        assert index.resolve("okJson") == receiver
        assert index.resolve("status") == receiver

    def test_wildcard_static_import_registers_java_http_request_builders(
        self,
    ) -> None:
        receiver = "java.net.http.HttpRequest"
        index = StaticImportIndex.from_import_entries(
            [JImport(path=receiver, is_static=True, is_wildcard=True)]
        )

        assert index.resolve("newBuilder") == receiver
        assert index.has_method("newBuilder") is True

    def test_named_static_import_registers_only_imported_method(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders.get"
                    ),
                    is_static=True,
                    is_wildcard=False,
                )
            ]
        )

        assert (
            index.resolve("get")
            == "org.springframework.test.web.servlet.request.MockMvcRequestBuilders"
        )
        assert index.resolve("post") is None

    def test_named_static_import_registers_spring_security_post_processor(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.security.test.web.servlet.request."
                        "SecurityMockMvcRequestPostProcessors.user"
                    ),
                    is_static=True,
                    is_wildcard=False,
                )
            ]
        )

        assert (
            index.resolve("user")
            == "org.springframework.security.test.web.servlet.request."
            "SecurityMockMvcRequestPostProcessors"
        )
        assert index.resolve("anonymous") is None

    def test_named_static_import_registers_method_without_registry_lookup(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path="com.example.AuthUtils.parseToken",
                    is_static=True,
                    is_wildcard=False,
                )
            ]
        )

        assert index.resolve("parseToken") == "com.example.AuthUtils"

    def test_named_static_import_beats_framework_wildcard_for_same_method(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
                JImport(
                    path="com.example.AuthUtils.get",
                    is_static=True,
                    is_wildcard=False,
                ),
            ]
        )

        assert index.resolve("get") == "com.example.AuthUtils"

    def test_non_static_import_is_ignored(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path="org.springframework.test.web.servlet.request.MockMvcRequestBuilders",
                    is_static=False,
                    is_wildcard=False,
                )
            ]
        )

        assert index is StaticImportIndex.EMPTY

    def test_ambiguous_named_import_is_excluded_but_evidence_remains(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders.get"
                    ),
                    is_static=True,
                    is_wildcard=False,
                ),
                JImport(
                    path="io.restassured.RestAssured.get",
                    is_static=True,
                    is_wildcard=False,
                ),
            ]
        )

        assert index.resolve("get") is None
        assert index.has_method("get") is True


class TestResolveReceiverType:
    def test_prefers_explicit_receiver_type(self) -> None:
        call_site = make_call_site(
            method_name="get",
            receiver_type="org.springframework.web.client.RestTemplate",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=StaticImportIndex.EMPTY
        ).receiver_type

        assert resolved == "org.springframework.web.client.RestTemplate"

    def test_prefers_explicit_receiver_type_over_colliding_static_import(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                )
            ]
        )
        call_site = make_call_site(
            method_name="get",
            receiver_type="org.springframework.web.client.RestTemplate",
            is_static_call=True,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert resolved == "org.springframework.web.client.RestTemplate"

    def test_uses_named_import_when_receiver_is_missing(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders.get"
                    ),
                    is_static=True,
                    is_wildcard=False,
                )
            ]
        )
        call_site = make_call_site(
            method_name="get",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert (
            resolved
            == "org.springframework.test.web.servlet.request.MockMvcRequestBuilders"
        )

    def test_uses_named_import_for_explicit_static_call(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders.get"
                    ),
                    is_static=True,
                    is_wildcard=False,
                )
            ]
        )
        call_site = make_call_site(
            method_name="get",
            receiver_type="",
            is_static_call=True,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert (
            resolved
            == "org.springframework.test.web.servlet.request.MockMvcRequestBuilders"
        )

    def test_does_not_fallback_without_import_evidence(self) -> None:
        call_site = make_call_site(
            method_name="get",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=StaticImportIndex.EMPTY
        ).receiver_type

        assert resolved == ""

    def test_uses_framework_wildcard_receiver_for_known_imported_method(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                )
            ]
        )
        call_site = make_call_site(
            method_name="get",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert (
            resolved
            == "org.springframework.test.web.servlet.request.MockMvcRequestBuilders"
        )

    def test_wildcard_receiver_does_not_trigger_without_import_mapping(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path="com.example.AuthUtils",
                    is_static=True,
                    is_wildcard=True,
                )
            ]
        )
        call_site = make_call_site(
            method_name="parseToken",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert resolved == ""

    def test_framework_wildcard_receiver_resolves_wiremock_stubfor(
        self,
    ) -> None:
        receiver = "com.github.tomakehurst.wiremock.client.WireMock"
        index = StaticImportIndex.from_import_entries(
            [
                JImport(path=receiver, is_static=True, is_wildcard=True),
            ]
        )
        call_site = make_call_site(
            method_name="stubFor",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert resolved == receiver

    def test_unknown_method_not_claimed_by_single_framework_wildcard(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path="com.github.tomakehurst.wiremock.client.WireMock",
                    is_static=True,
                    is_wildcard=True,
                ),
            ]
        )
        call_site = make_call_site(
            method_name="customMatcher",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert resolved == ""

    def test_sibling_wildcard_static_import_claims_framework_status(self) -> None:
        receiver = "org.springframework.test.web.servlet.result.MockMvcResultMatchers"
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=receiver,
                    is_static=True,
                    is_wildcard=True,
                )
            ]
        )
        call_site = make_call_site(
            method_name="status",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert resolved == receiver

    def test_result_matcher_root_beats_builder_wildcard_for_status(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.result."
                        "MockMvcResultMatchers"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
            ]
        )
        call_site = make_call_site(
            method_name="status",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert (
            resolved
            == "org.springframework.test.web.servlet.result.MockMvcResultMatchers"
        )

    def test_result_matcher_content_wins_when_builder_wildcard_has_no_static_content(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.result."
                        "MockMvcResultMatchers"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
            ]
        )
        call_site = make_call_site(
            method_name="content",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert (
            resolved
            == "org.springframework.test.web.servlet.result.MockMvcResultMatchers"
        )


class TestStaticImportHttpClassification:
    def test_named_java_http_request_import_resolves_to_http_receiver(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path="java.net.http.HttpRequest.newBuilder",
                    is_static=True,
                    is_wildcard=False,
                )
            ]
        )

        receiver = index.resolve("newBuilder")
        assert receiver == "java.net.http.HttpRequest"

        rule = resolve_http_owner_family(receiver, "newBuilder")
        assert rule is not None
        assert rule.family_id == "java-httpclient.request"
        assert classify_http_roles(
            rule, receiver_type=receiver, method_name="newBuilder"
        ) == (HttpRequestRole.BUILDER, None)

    def test_named_mockmvc_request_builder_import_resolves_to_http_receiver(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders.get"
                    ),
                    is_static=True,
                    is_wildcard=False,
                )
            ]
        )

        receiver = index.resolve("get")
        assert (
            receiver
            == "org.springframework.test.web.servlet.request.MockMvcRequestBuilders"
        )

        rule = resolve_http_owner_family(receiver, "get")
        assert rule is not None
        assert rule.family_id == "mockmvc.request_factory"
        assert classify_http_roles(rule, receiver_type=receiver, method_name="get") == (
            HttpRequestRole.BUILDER,
            None,
        )

    def test_named_restdocs_mockmvc_request_builder_import_resolves_to_http_receiver(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.restdocs.mockmvc."
                        "RestDocumentationRequestBuilders.get"
                    ),
                    is_static=True,
                    is_wildcard=False,
                )
            ]
        )

        receiver = index.resolve("get")
        assert (
            receiver
            == "org.springframework.restdocs.mockmvc.RestDocumentationRequestBuilders"
        )

        rule = resolve_http_owner_family(receiver, "get")
        assert rule is not None
        assert rule.family_id == "mockmvc.request_factory"
        assert classify_http_roles(rule, receiver_type=receiver, method_name="get") == (
            HttpRequestRole.BUILDER,
            None,
        )
        assert resolve_http_owner_family(receiver, "asyncDispatch") is None

    def test_wildcard_mockmvc_result_matchers_resolve_to_response_receiver(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.result."
                        "MockMvcResultMatchers"
                    ),
                    is_static=True,
                    is_wildcard=True,
                )
            ]
        )

        receiver = index.resolve("content")
        assert (
            receiver
            == "org.springframework.test.web.servlet.result.MockMvcResultMatchers"
        )

        rule = resolve_http_owner_family(receiver, "content")
        assert rule is not None
        assert rule.family_id == "mockmvc.matcher_root"
        assert classify_http_roles(
            rule, receiver_type=receiver, method_name="content"
        ) == (None, HttpResponseRole.MATCHER)

    def test_mockmvc_request_builder_wildcard_does_not_claim_instance_content(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                )
            ]
        )

        assert index.resolve("content") is None

    def test_multiple_http_wildcard_frameworks_keep_get_ambiguous(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
                JImport(
                    path="io.restassured.RestAssured",
                    is_static=True,
                    is_wildcard=True,
                ),
            ]
        )

        assert index.resolve("get") is None

    def test_multiple_named_http_imports_keep_get_ambiguous(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders.get"
                    ),
                    is_static=True,
                    is_wildcard=False,
                ),
                JImport(
                    path="io.restassured.RestAssured.get",
                    is_static=True,
                    is_wildcard=False,
                ),
            ]
        )

        assert index.resolve("get") is None

    @pytest.mark.parametrize(
        "owner",
        [
            "io.restassured.module.mockmvc.RestAssuredMockMvc",
            "io.restassured.module.webtestclient.RestAssuredWebTestClient",
            "com.jayway.restassured.module.mockmvc.RestAssuredMockMvc",
        ],
    )
    def test_restassured_module_wildcard_import_resolves_to_request_factory(
        self,
        owner: str,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [JImport(path=owner, is_static=True, is_wildcard=True)]
        )

        receiver = index.resolve("get")

        assert receiver == owner
        rule = resolve_http_owner_family(receiver, "get")
        assert rule is not None
        assert rule.family_id == "rest-assured.request_factory"
        assert classify_http_roles(rule, receiver_type=receiver, method_name="get") == (
            HttpRequestRole.EVENT,
            None,
        )

    def test_legacy_restassured_webtestclient_wildcard_import_is_not_claimed(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "com.jayway.restassured.module.webtestclient."
                        "RestAssuredWebTestClient"
                    ),
                    is_static=True,
                    is_wildcard=True,
                )
            ]
        )

        assert index.resolve("get") is None

    @pytest.mark.parametrize(
        ("import_path", "method_name"),
        [
            ("org.springframework.web.client.RestTemplate", "postForEntity"),
            ("org.springframework.web.reactive.function.client.WebClient", "get"),
            ("com.intuit.karate.Http", "get"),
            ("feign.RequestTemplate", "method"),
        ],
    )
    def test_intentionally_empty_http_owner_families_do_not_claim_receiverless_methods(
        self,
        import_path: str,
        method_name: str,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [JImport(path=import_path, is_static=True, is_wildcard=True)]
        )
        call_site = make_call_site(method_name=method_name, receiver_type="")

        assert (
            resolve_receiver(
                call_site=call_site, static_import_index=index
            ).receiver_type
            == ""
        )

    def test_exact_non_http_import_prevents_wildcard_http_classification(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path="com.example.AuthUtils.get",
                    is_static=True,
                    is_wildcard=False,
                ),
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
            ]
        )

        assert index.resolve("get") == "com.example.AuthUtils"
        assert resolve_http_owner_family("com.example.AuthUtils", "get") is None

    def test_multiple_curated_wildcard_receivers_keep_unknown_method_ambiguous(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path="com.github.tomakehurst.wiremock.client.WireMock",
                    is_static=True,
                    is_wildcard=True,
                ),
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
            ]
        )
        call_site = make_call_site(
            method_name="customMatcher",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert resolved == ""

    def test_multiple_framework_wildcard_receivers_stay_ambiguous_for_static_call(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
                JImport(
                    path="io.restassured.RestAssured",
                    is_static=True,
                    is_wildcard=True,
                ),
            ]
        )
        call_site = make_call_site(
            method_name="get",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert resolved == ""

    def test_falls_back_to_imports_when_receiver_is_missing_even_with_callee_signature(
        self,
    ) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path="net.jqwik.api.PropertyChecker",
                    is_static=True,
                    is_wildcard=True,
                )
            ]
        )
        call_site = make_call_site(
            method_name="check",
            callee_signature="example.ApiTest.check()",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert resolved == "net.jqwik.api.PropertyChecker"

    def test_utility_method_resolves_over_single_framework_wildcard(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path="io.restassured.RestAssured",
                    is_static=True,
                    is_wildcard=True,
                ),
                JImport(
                    path="java.nio.file.Files",
                    is_static=True,
                    is_wildcard=True,
                ),
            ]
        )
        call_site = make_call_site(
            method_name="exists",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert resolved == "java.nio.file.Files"

    def test_local_helper_not_claimed_by_single_framework_wildcard(self) -> None:
        index = StaticImportIndex.from_import_entries(
            [
                JImport(
                    path=(
                        "org.springframework.test.web.servlet.request."
                        "MockMvcRequestBuilders"
                    ),
                    is_static=True,
                    is_wildcard=True,
                ),
            ]
        )
        call_site = make_call_site(
            method_name="helper",
            receiver_type="",
            is_static_call=False,
        )

        resolved = resolve_receiver(
            call_site=call_site, static_import_index=index
        ).receiver_type

        assert resolved == ""


class TestProjectLocalWildcardStaticImport:
    def test_wildcard_static_import_of_analyzed_class_expands_static_methods(
        self,
    ) -> None:
        analysis = FakeJavaAnalysis(
            classes={
                "example.Test": make_type(),
                "example.TestFixtures": make_type(),
            },
            methods_by_class={
                "example.TestFixtures": {
                    "createUser()": make_callable(
                        signature="createUser()",
                        modifiers=["static", "public"],
                    )
                }
            },
            import_declarations_by_file={
                "example/Test.java": [
                    make_import_declaration(
                        "example.TestFixtures", is_static=True, is_wildcard=True
                    ),
                ]
            },
            java_files={"example.Test": "example/Test.java"},
        )
        common = CommonAnalysis(analysis)
        index = common.get_static_import_index("example.Test")

        assert index.resolve("createUser") == "example.TestFixtures"

    def test_ambiguous_project_local_wildcard_imports_fail_closed(self) -> None:
        analysis = FakeJavaAnalysis(
            classes={
                "example.Test": make_type(),
                "example.FixturesA": make_type(),
                "example.FixturesB": make_type(),
            },
            methods_by_class={
                "example.FixturesA": {
                    "helper()": make_callable(
                        signature="helper()",
                        modifiers=["static", "public"],
                    )
                },
                "example.FixturesB": {
                    "helper()": make_callable(
                        signature="helper()",
                        modifiers=["static", "public"],
                    )
                },
            },
            import_declarations_by_file={
                "example/Test.java": [
                    make_import_declaration(
                        "example.FixturesA", is_static=True, is_wildcard=True
                    ),
                    make_import_declaration(
                        "example.FixturesB", is_static=True, is_wildcard=True
                    ),
                ]
            },
            java_files={"example.Test": "example/Test.java"},
        )
        common = CommonAnalysis(analysis)
        index = common.get_static_import_index("example.Test")

        assert index.resolve("helper") is None
        assert index.has_method("helper") is True

    def test_project_local_wildcard_static_import_makes_helper_reachable(
        self,
    ) -> None:
        inner_http_call = make_call_site(
            method_name="get",
            callee_signature="get(java.lang.String)",
            receiver_type="",
            is_static_call=False,
            start_line=2,
            start_column=1,
            end_line=2,
            end_column=15,
        )
        analysis = FakeJavaAnalysis(
            classes={
                "example.Test": make_type(),
                "example.TestFixtures": make_type(
                    callable_declarations={
                        "createUser()": make_callable(
                            signature="createUser()",
                            modifiers=["static", "public"],
                            call_sites=[inner_http_call],
                        )
                    }
                ),
            },
            methods_by_class={
                "example.Test": {
                    "testFoo()": make_callable(
                        signature="testFoo()",
                        call_sites=[
                            make_call_site(
                                method_name="createUser",
                                callee_signature="createUser()",
                                receiver_type="",
                                is_static_call=False,
                                start_line=1,
                                start_column=1,
                                end_line=1,
                                end_column=15,
                            )
                        ],
                    )
                },
                "example.TestFixtures": {
                    "createUser()": make_callable(
                        signature="createUser()",
                        modifiers=["static", "public"],
                        call_sites=[inner_http_call],
                    )
                },
            },
            import_declarations_by_file={
                "example/Test.java": [
                    make_import_declaration(
                        "example.TestFixtures", is_static=True, is_wildcard=True
                    ),
                ]
            },
            java_files={"example.Test": "example/Test.java"},
        )
        reachability = Reachability(analysis)
        common = CommonAnalysis(analysis)
        resolve_helper, load_call_sites = reachability.build_helper_resolver(
            qualified_class_name="example.Test",
            add_extended_class=True,
            test_utility_classes=["example.TestFixtures"],
            get_static_import_index_for_class=common.get_static_import_index,
        )

        owner = MethodRef(
            defining_class_name="example.Test", method_signature="testFoo()"
        )
        helper_call = make_call_site(
            method_name="createUser",
            callee_signature="createUser()",
            receiver_type="",
            is_static_call=False,
            start_line=1,
            start_column=1,
            end_line=1,
            end_column=15,
        )
        helper_ref = resolve_helper(owner, helper_call)

        assert helper_ref == MethodRef(
            defining_class_name="example.TestFixtures",
            method_signature="createUser()",
        )
        assert [
            call_site.method_name for call_site in load_call_sites(helper_ref) or []
        ] == ["get"]

    def test_project_local_wildcard_ambiguous_with_framework_wildcard_fails_closed(
        self,
    ) -> None:
        analysis = FakeJavaAnalysis(
            classes={
                "example.Test": make_type(),
                "example.Fixtures": make_type(),
            },
            methods_by_class={
                "example.Fixtures": {
                    "get()": make_callable(
                        signature="get()",
                        modifiers=["static", "public"],
                    )
                }
            },
            import_declarations_by_file={
                "example/Test.java": [
                    make_import_declaration(
                        "example.Fixtures", is_static=True, is_wildcard=True
                    ),
                    make_import_declaration(
                        "io.restassured.RestAssured",
                        is_static=True,
                        is_wildcard=True,
                    ),
                ]
            },
            java_files={"example.Test": "example/Test.java"},
        )
        common = CommonAnalysis(analysis)
        index = common.get_static_import_index("example.Test")

        assert index.resolve("get") is None
        assert index.has_method("get") is True
