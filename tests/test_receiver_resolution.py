from __future__ import annotations

from cldk.models.java import JImport
import pytest
from typing import Any, cast

from gerbil.analysis.runtime.call_sites import MethodRef
from gerbil.analysis.shared import CommonAnalysis
from gerbil.analysis.shared.receiver_resolution import (
    RuntimeReceiverResolver,
    resolve_receiver,
)
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    make_call_site,
    make_callable,
    make_field,
    make_type,
    make_variable_declaration,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def _runtime_resolver(
    *,
    analysis: FakeJavaAnalysis,
    owner: MethodRef,
    owner_method_details,
) -> RuntimeReceiverResolver:
    common_analysis = CommonAnalysis(analysis)
    return RuntimeReceiverResolver(
        analysis=analysis,
        load_method_details=(
            lambda method_ref: (
                owner_method_details
                if method_ref == owner
                else analysis.get_method(
                    method_ref.defining_class_name,
                    method_ref.method_signature,
                )
            )
        ),
        get_static_import_index_for_class=lambda _class_name: StaticImportIndex.EMPTY,
        get_class_imports_for_class=common_analysis.get_class_imports,
        get_superclass_chain_for_class=common_analysis.get_superclass_chain,
        constant_resolver=common_analysis.get_constant_resolver(),
    )


def _resolve_with_analysis(
    *,
    call_site,
    analysis: FakeJavaAnalysis,
    owner_class_name: str = "example.ApiTest",
    owner_method_details=None,
):
    common_analysis = CommonAnalysis(analysis)
    return resolve_receiver(
        call_site=call_site,
        static_import_index=StaticImportIndex.EMPTY,
        owner_class_name=owner_class_name,
        owner_method_details=owner_method_details
        or make_callable(call_sites=[call_site]),
        analysis=analysis,
        get_class_imports_for_class=common_analysis.get_class_imports,
        get_superclass_chain_for_class=common_analysis.get_superclass_chain,
    )


def test_resolve_receiver_prefers_explicit_receiver_type() -> None:
    call_site = make_call_site(
        method_name="get",
        receiver_type="org.springframework.web.client.RestTemplate",
    )

    analysis = FakeJavaAnalysis(classes={"example.ApiTest": make_type()})

    resolved = resolve_receiver(
        call_site=call_site,
        static_import_index=StaticImportIndex.EMPTY,
        owner_class_name="example.ApiTest",
        owner_method_details=None,
        analysis=analysis,
    )

    assert resolved.receiver_type == "org.springframework.web.client.RestTemplate"
    assert resolved.source == "explicit_receiver_type"


def test_resolve_receiver_requires_explicit_context_arguments() -> None:
    call_site = make_call_site(
        method_name="get",
        receiver_type="org.springframework.web.client.RestTemplate",
    )

    with pytest.raises(TypeError):
        cast(Any, resolve_receiver)(
            call_site=call_site,
            static_import_index=StaticImportIndex.EMPTY,
        )


def test_resolve_receiver_prefers_local_symbol_over_field_symbol() -> None:
    call_site = make_call_site(
        method_name="send",
        receiver_expr="client",
        receiver_type="",
        start_line=25,
    )
    owner_method = make_callable(
        call_sites=[call_site],
        variable_declarations=[
            make_variable_declaration(
                name="client",
                type_name="java.net.http.HttpClient",
                start_line=10,
            )
        ],
    )
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(
                field_declarations=[
                    make_field(
                        type_name="org.springframework.web.client.RestTemplate",
                        variables=["client"],
                    )
                ]
            )
        }
    )
    common_analysis = CommonAnalysis(analysis)

    resolved = resolve_receiver(
        call_site=call_site,
        static_import_index=StaticImportIndex.EMPTY,
        owner_class_name="example.ApiTest",
        owner_method_details=owner_method,
        analysis=analysis,
        get_class_imports_for_class=common_analysis.get_class_imports,
        get_superclass_chain_for_class=common_analysis.get_superclass_chain,
    )

    assert resolved.receiver_type == "java.net.http.HttpClient"
    assert resolved.source == "local_symbol"


def test_resolve_receiver_uses_declaring_superclass_imports_for_inherited_field() -> (
    None
):
    call_site = make_call_site(
        method_name="perform",
        receiver_expr="mockMvc",
        receiver_type="",
        start_line=9,
    )
    owner_method = make_callable(call_sites=[call_site])
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(extends_list=["example.BaseApiTest"]),
            "example.BaseApiTest": make_type(
                field_declarations=[
                    make_field(type_name="MockMvc", variables=["mockMvc"])
                ]
            ),
            "org.springframework.test.web.servlet.MockMvc": make_type(),
        },
        java_files={
            "example.ApiTest": "src/test/java/example/ApiTest.java",
            "example.BaseApiTest": "src/test/java/example/BaseApiTest.java",
        },
        import_declarations_by_file={
            "src/test/java/example/BaseApiTest.java": [
                JImport(
                    path="org.springframework.test.web.servlet.MockMvc",
                    is_static=False,
                    is_wildcard=False,
                )
            ]
        },
    )
    common_analysis = CommonAnalysis(analysis)

    resolved = resolve_receiver(
        call_site=call_site,
        static_import_index=StaticImportIndex.EMPTY,
        owner_class_name="example.ApiTest",
        owner_method_details=owner_method,
        analysis=analysis,
        get_class_imports_for_class=common_analysis.get_class_imports,
        get_superclass_chain_for_class=common_analysis.get_superclass_chain,
    )

    assert resolved.receiver_type == "org.springframework.test.web.servlet.MockMvc"
    assert resolved.source == "inherited_field_symbol"


def test_unresolved_receiver_expr_blocks_static_import_fallback() -> None:
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get",
                is_static=True,
                is_wildcard=False,
            )
        ]
    )
    call_site = make_call_site(
        method_name="get",
        receiver_expr="requestBuilder",
        receiver_type="",
    )

    resolved = resolve_receiver(
        call_site=call_site,
        static_import_index=static_import_index,
        owner_class_name="example.ApiTest",
        owner_method_details=make_callable(call_sites=[call_site]),
        analysis=FakeJavaAnalysis(classes={"example.ApiTest": make_type()}),
    )

    assert resolved.receiver_type == ""
    assert resolved.source == "unresolved_receiver_expr"


def test_empty_receiver_expr_allows_static_import_fallback() -> None:
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get",
                is_static=True,
                is_wildcard=False,
            )
        ]
    )
    call_site = make_call_site(
        method_name="get",
        receiver_expr="",
        receiver_type="",
    )

    analysis = FakeJavaAnalysis(classes={"example.ApiTest": make_type()})

    resolved = resolve_receiver(
        call_site=call_site,
        static_import_index=static_import_index,
        owner_class_name="example.ApiTest",
        owner_method_details=None,
        analysis=analysis,
    )

    assert (
        resolved.receiver_type
        == "org.springframework.test.web.servlet.request.MockMvcRequestBuilders"
    )
    assert resolved.source == "static_import_method"


def test_receiver_expr_resolves_explicitly_imported_class_literal() -> None:
    call_site = make_call_site(
        method_name="newBuilder",
        receiver_expr="HttpRequest",
        receiver_type="",
    )
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        java_files={"example.ApiTest": "ApiTest.java"},
        import_declarations_by_file={
            "ApiTest.java": [
                JImport(
                    path="java.net.http.HttpRequest",
                    is_static=False,
                    is_wildcard=False,
                )
            ]
        },
    )

    resolved = _resolve_with_analysis(call_site=call_site, analysis=analysis)

    assert resolved.receiver_type == "java.net.http.HttpRequest"
    assert resolved.source == "class_literal_receiver"


def test_receiver_expr_explicit_imported_class_literal_shadows_same_package() -> None:
    call_site = make_call_site(
        method_name="newBuilder",
        receiver_expr="HttpRequest",
        receiver_type="",
    )
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(),
            "example.HttpRequest": make_type(),
        },
        java_files={"example.ApiTest": "ApiTest.java"},
        import_declarations_by_file={
            "ApiTest.java": [
                JImport(
                    path="java.net.http.HttpRequest",
                    is_static=False,
                    is_wildcard=False,
                )
            ]
        },
    )

    resolved = _resolve_with_analysis(call_site=call_site, analysis=analysis)

    assert resolved.receiver_type == "java.net.http.HttpRequest"
    assert resolved.source == "class_literal_receiver"


def test_receiver_expr_resolves_same_package_class_literal() -> None:
    call_site = make_call_site(
        method_name="helper",
        receiver_expr="ApiHelpers",
        receiver_type="",
    )
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(),
            "example.ApiHelpers": make_type(),
        }
    )

    resolved = _resolve_with_analysis(call_site=call_site, analysis=analysis)

    assert resolved.receiver_type == "example.ApiHelpers"
    assert resolved.source == "class_literal_receiver"


def test_receiver_expr_same_package_class_literal_shadows_wildcard_import() -> None:
    call_site = make_call_site(
        method_name="get",
        receiver_expr="RequestBuilders",
        receiver_type="",
    )
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(),
            "example.RequestBuilders": make_type(),
            "example.web.RequestBuilders": make_type(),
        },
        java_files={"example.ApiTest": "ApiTest.java"},
        import_declarations_by_file={
            "ApiTest.java": [
                JImport(path="example.web", is_static=False, is_wildcard=True)
            ]
        },
    )

    resolved = _resolve_with_analysis(call_site=call_site, analysis=analysis)

    assert resolved.receiver_type == "example.RequestBuilders"
    assert resolved.source == "class_literal_receiver"


def test_receiver_expr_resolves_unambiguous_wildcard_imported_class_literal() -> None:
    call_site = make_call_site(
        method_name="get",
        receiver_expr="RequestBuilders",
        receiver_type="",
    )
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(),
            "example.web.RequestBuilders": make_type(),
        },
        java_files={"example.ApiTest": "ApiTest.java"},
        import_declarations_by_file={
            "ApiTest.java": [
                JImport(
                    path="example.web",
                    is_static=False,
                    is_wildcard=True,
                )
            ]
        },
    )

    resolved = _resolve_with_analysis(call_site=call_site, analysis=analysis)

    assert resolved.receiver_type == "example.web.RequestBuilders"
    assert resolved.source == "class_literal_receiver"


def test_receiver_expr_resolves_fully_qualified_class_literal() -> None:
    call_site = make_call_site(
        method_name="newBuilder",
        receiver_expr="java.net.http.HttpRequest",
        receiver_type="",
    )
    analysis = FakeJavaAnalysis(classes={"example.ApiTest": make_type()})

    resolved = _resolve_with_analysis(call_site=call_site, analysis=analysis)

    assert resolved.receiver_type == "java.net.http.HttpRequest"
    assert resolved.source == "class_literal_receiver"


def test_receiver_expr_resolves_imported_nested_class_literal() -> None:
    call_site = make_call_site(
        method_name="create",
        receiver_expr="HttpRequest.Builder",
        receiver_type="",
    )
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        java_files={"example.ApiTest": "ApiTest.java"},
        import_declarations_by_file={
            "ApiTest.java": [
                JImport(
                    path="java.net.http.HttpRequest",
                    is_static=False,
                    is_wildcard=False,
                )
            ]
        },
    )

    resolved = _resolve_with_analysis(call_site=call_site, analysis=analysis)

    assert resolved.receiver_type == "java.net.http.HttpRequest.Builder"
    assert resolved.source == "class_literal_receiver"


def test_receiver_expr_qualified_local_symbol_does_not_fallback_to_class_literal() -> (
    None
):
    call_site = make_call_site(
        method_name="create",
        receiver_expr="HttpRequest.Builder",
        receiver_type="",
        start_line=20,
    )
    owner_method = make_callable(
        call_sites=[call_site],
        variable_declarations=[
            make_variable_declaration(
                name="HttpRequest",
                type_name="MissingType",
                start_line=10,
            )
        ],
    )
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        java_files={"example.ApiTest": "ApiTest.java"},
        import_declarations_by_file={
            "ApiTest.java": [
                JImport(
                    path="java.net.http.HttpRequest",
                    is_static=False,
                    is_wildcard=False,
                )
            ]
        },
    )

    resolved = _resolve_with_analysis(
        call_site=call_site,
        owner_method_details=owner_method,
        analysis=analysis,
    )

    assert resolved.receiver_type == ""
    assert resolved.source == "unresolved_receiver_expr"


def test_receiver_expr_does_not_guess_ambiguous_wildcard_class_literal() -> None:
    call_site = make_call_site(
        method_name="get",
        receiver_expr="RequestBuilders",
        receiver_type="",
    )
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(),
            "example.web.RequestBuilders": make_type(),
            "example.http.RequestBuilders": make_type(),
        },
        java_files={"example.ApiTest": "ApiTest.java"},
        import_declarations_by_file={
            "ApiTest.java": [
                JImport(path="example.web", is_static=False, is_wildcard=True),
                JImport(path="example.http", is_static=False, is_wildcard=True),
            ]
        },
    )

    resolved = _resolve_with_analysis(call_site=call_site, analysis=analysis)

    assert resolved.receiver_type == ""
    assert resolved.source == "unresolved_receiver_expr"


def test_receiver_expr_does_not_treat_lowercase_unresolved_symbol_as_class_literal() -> (
    None
):
    call_site = make_call_site(
        method_name="newBuilder",
        receiver_expr="httpRequest",
        receiver_type="",
    )
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        java_files={"example.ApiTest": "ApiTest.java"},
        import_declarations_by_file={
            "ApiTest.java": [
                JImport(
                    path="java.net.http.HttpRequest",
                    is_static=False,
                    is_wildcard=False,
                )
            ]
        },
    )

    resolved = _resolve_with_analysis(call_site=call_site, analysis=analysis)

    assert resolved.receiver_type == ""
    assert resolved.source == "unresolved_receiver_expr"


def test_receiver_expr_local_symbol_shadows_imported_class_literal() -> None:
    call_site = make_call_site(
        method_name="send",
        receiver_expr="HttpRequest",
        receiver_type="",
        start_line=20,
    )
    owner_method = make_callable(
        call_sites=[call_site],
        variable_declarations=[
            make_variable_declaration(
                name="HttpRequest",
                type_name="example.TestRequest",
                start_line=10,
            )
        ],
    )
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(),
            "example.TestRequest": make_type(),
        },
        java_files={"example.ApiTest": "ApiTest.java"},
        import_declarations_by_file={
            "ApiTest.java": [
                JImport(
                    path="java.net.http.HttpRequest",
                    is_static=False,
                    is_wildcard=False,
                )
            ]
        },
    )

    resolved = _resolve_with_analysis(
        call_site=call_site,
        owner_method_details=owner_method,
        analysis=analysis,
    )

    assert resolved.receiver_type == "example.TestRequest"
    assert resolved.source == "local_symbol"


def test_receiver_expr_unresolved_local_symbol_does_not_fallback_to_class_literal() -> (
    None
):
    call_site = make_call_site(
        method_name="newBuilder",
        receiver_expr="HttpRequest",
        receiver_type="",
        start_line=20,
    )
    owner_method = make_callable(
        call_sites=[call_site],
        variable_declarations=[
            make_variable_declaration(
                name="HttpRequest",
                type_name="MissingType",
                start_line=10,
            )
        ],
    )
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        java_files={"example.ApiTest": "ApiTest.java"},
        import_declarations_by_file={
            "ApiTest.java": [
                JImport(
                    path="java.net.http.HttpRequest",
                    is_static=False,
                    is_wildcard=False,
                )
            ]
        },
    )

    resolved = _resolve_with_analysis(
        call_site=call_site,
        owner_method_details=owner_method,
        analysis=analysis,
    )

    assert resolved.receiver_type == ""
    assert resolved.source == "unresolved_receiver_expr"


def test_runtime_receiver_resolver_resolves_this_receiver() -> None:
    call_site = make_call_site(
        method_name="send", receiver_expr="this", receiver_type=""
    )
    owner = MethodRef(
        defining_class_name="example.ApiTest",
        method_signature="testCase()",
    )
    owner_method_details = make_callable(
        signature=owner.method_signature, call_sites=[call_site]
    )
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        methods_by_class={
            "example.ApiTest": {owner.method_signature: owner_method_details}
        },
    )

    resolved = _runtime_resolver(
        analysis=analysis,
        owner=owner,
        owner_method_details=owner_method_details,
    ).resolve_for_event(owner, call_site)

    assert resolved.receiver_type == "example.ApiTest"
    assert resolved.source == "this_receiver"


def test_runtime_receiver_resolver_resolves_super_receiver() -> None:
    call_site = make_call_site(
        method_name="send", receiver_expr="super", receiver_type=""
    )
    owner = MethodRef(
        defining_class_name="example.ApiTest",
        method_signature="testCase()",
    )
    owner_method_details = make_callable(
        signature=owner.method_signature, call_sites=[call_site]
    )
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(extends_list=["example.BaseApiTest"]),
            "example.BaseApiTest": make_type(),
        },
        methods_by_class={
            "example.ApiTest": {owner.method_signature: owner_method_details}
        },
    )

    resolved = _runtime_resolver(
        analysis=analysis,
        owner=owner,
        owner_method_details=owner_method_details,
    ).resolve_for_event(owner, call_site)

    assert resolved.receiver_type == "example.BaseApiTest"
    assert resolved.source == "super_receiver"


def test_runtime_receiver_resolver_resolves_this_member_field() -> None:
    call_site = make_call_site(
        method_name="send",
        receiver_expr="this.httpClient",
        receiver_type="",
    )
    owner = MethodRef(
        defining_class_name="example.ApiTest",
        method_signature="testCase()",
    )
    owner_method_details = make_callable(
        signature=owner.method_signature, call_sites=[call_site]
    )
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.net.http.HttpClient",
                        variables=["httpClient"],
                    )
                ]
            )
        },
        methods_by_class={
            "example.ApiTest": {owner.method_signature: owner_method_details}
        },
    )

    resolved = _runtime_resolver(
        analysis=analysis,
        owner=owner,
        owner_method_details=owner_method_details,
    ).resolve_for_event(owner, call_site)

    assert resolved.receiver_type == "java.net.http.HttpClient"
    assert resolved.source == "field_symbol"


def test_runtime_receiver_resolver_resolves_super_member_field() -> None:
    call_site = make_call_site(
        method_name="send",
        receiver_expr="super.httpClient",
        receiver_type="",
    )
    owner = MethodRef(
        defining_class_name="example.ApiTest",
        method_signature="testCase()",
    )
    owner_method_details = make_callable(
        signature=owner.method_signature, call_sites=[call_site]
    )
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiTest": make_type(extends_list=["example.BaseApiTest"]),
            "example.BaseApiTest": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.net.http.HttpClient",
                        variables=["httpClient"],
                    )
                ]
            ),
        },
        methods_by_class={
            "example.ApiTest": {owner.method_signature: owner_method_details}
        },
    )

    resolved = _runtime_resolver(
        analysis=analysis,
        owner=owner,
        owner_method_details=owner_method_details,
    ).resolve_for_event(owner, call_site)

    assert resolved.receiver_type == "java.net.http.HttpClient"
    assert resolved.source == "inherited_field_symbol"
