from __future__ import annotations

import pytest

from gerbil.analysis.runtime.call_sites import MethodRef
from gerbil.analysis.shared.reachability import Reachability
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    make_call_site,
    make_callable,
    make_field,
    make_import_declaration,
    make_type,
    make_variable_declaration,
)
from tests.fake_java_analysis import FakeJavaAnalysis


def test_build_helper_resolver_returns_callables() -> None:
    reachability = Reachability(FakeJavaAnalysis())

    resolve_helper, load_call_sites = reachability.build_helper_resolver(
        qualified_class_name="example.Test"
    )

    assert callable(resolve_helper)
    assert callable(load_call_sites)


def test_build_helper_resolver_resolves_helper_targets_and_loads_call_sites() -> None:
    helper_inner_call = make_call_site(
        method_name="inner",
        start_line=8,
        start_column=5,
        end_line=8,
        end_column=12,
    )
    analysis = FakeJavaAnalysis(
        classes={
            "example.Test": make_type(extends_list=["example.BaseTest"]),
            "example.BaseTest": make_type(),
            "example.Util": make_type(),
            "example.Service": make_type(),
        },
        methods_by_class={
            "example.Test": {
                "testFoo()": make_callable(signature="testFoo()"),
                "helper()": make_callable(
                    signature="helper()",
                    call_sites=[helper_inner_call],
                ),
            },
            "example.BaseTest": {
                "baseHelper()": make_callable(signature="baseHelper()"),
            },
            "example.Util": {
                "utilHelper()": make_callable(signature="utilHelper()"),
            },
            "example.Service": {
                "serviceHelper()": make_callable(signature="serviceHelper()"),
            },
        },
    )
    reachability = Reachability(analysis)
    resolve_helper, load_call_sites = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
        add_extended_class=True,
        test_utility_classes=["example.Util"],
    )

    owner = MethodRef(defining_class_name="example.Test", method_signature="testFoo()")

    local_helper_call = make_call_site(
        method_name="helper",
        callee_signature="helper()",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=8,
    )
    super_helper_call = make_call_site(
        method_name="baseHelper",
        callee_signature="baseHelper()",
        receiver_expr="super",
        start_line=2,
        start_column=1,
        end_line=2,
        end_column=14,
    )
    utility_helper_call = make_call_site(
        method_name="utilHelper",
        callee_signature="utilHelper()",
        receiver_type="example.Util",
        start_line=3,
        start_column=1,
        end_line=3,
        end_column=14,
    )
    non_helper_call = make_call_site(
        method_name="serviceHelper",
        callee_signature="serviceHelper()",
        receiver_type="example.Service",
        start_line=4,
        start_column=1,
        end_line=4,
        end_column=17,
    )

    assert resolve_helper(owner, local_helper_call) == MethodRef(
        defining_class_name="example.Test",
        method_signature="helper()",
    )
    assert resolve_helper(owner, super_helper_call) == MethodRef(
        defining_class_name="example.BaseTest",
        method_signature="baseHelper()",
    )
    assert resolve_helper(owner, utility_helper_call) == MethodRef(
        defining_class_name="example.Util",
        method_signature="utilHelper()",
    )
    assert resolve_helper(owner, non_helper_call) is None

    missing_signature_call = make_call_site(
        method_name="unknown",
        callee_signature="",
        start_line=5,
        start_column=1,
        end_line=5,
        end_column=8,
    )
    assert resolve_helper(owner, missing_signature_call) is None

    helper_call_sites = load_call_sites(
        MethodRef(defining_class_name="example.Test", method_signature="helper()")
    )
    assert helper_call_sites is not None
    assert [call_site.method_name for call_site in helper_call_sites] == ["inner"]

    assert (
        load_call_sites(
            MethodRef(defining_class_name="example.Test", method_signature="missing()")
        )
        is None
    )


def test_build_helper_resolver_uses_owner_static_imports_for_nested_helpers() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Test": make_type(),
            "example.Util": make_type(),
            "example.MoreUtil": make_type(),
        },
        methods_by_class={
            "example.Test": {
                "testFoo()": make_callable(signature="testFoo()"),
            },
            "example.Util": {
                "utilHelper()": make_callable(
                    signature="utilHelper()",
                    call_sites=[
                        make_call_site(
                            method_name="leaf",
                            callee_signature="leaf()",
                            is_static_call=True,
                            start_line=2,
                            start_column=1,
                            end_line=2,
                            end_column=6,
                        )
                    ],
                ),
            },
            "example.MoreUtil": {
                "leaf()": make_callable(signature="leaf()"),
            },
        },
    )
    static_import_indexes = {
        "example.Test": StaticImportIndex.from_import_entries(
            [
                make_import_declaration(
                    "example.Util.utilHelper",
                    is_static=True,
                )
            ]
        ),
        "example.Util": StaticImportIndex.from_import_entries(
            [
                make_import_declaration(
                    "example.MoreUtil.leaf",
                    is_static=True,
                )
            ]
        ),
    }
    reachability = Reachability(analysis)
    resolve_helper, load_call_sites = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
        add_extended_class=True,
        test_utility_classes=["example.Util", "example.MoreUtil"],
        get_static_import_index_for_class=lambda class_name: static_import_indexes.get(
            class_name, StaticImportIndex.EMPTY
        ),
    )

    owner = MethodRef(defining_class_name="example.Test", method_signature="testFoo()")
    static_utility_call = make_call_site(
        method_name="utilHelper",
        callee_signature="utilHelper()",
        is_static_call=True,
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=10,
    )

    first_helper = resolve_helper(owner, static_utility_call)

    assert first_helper == MethodRef(
        defining_class_name="example.Util",
        method_signature="utilHelper()",
    )

    assert first_helper is not None
    nested_call_sites = load_call_sites(first_helper)
    assert nested_call_sites is not None

    nested_helper = resolve_helper(first_helper, nested_call_sites[0])

    assert nested_helper == MethodRef(
        defining_class_name="example.MoreUtil",
        method_signature="leaf()",
    )


def test_build_helper_resolver_local_helper_wins_over_static_import() -> None:
    """Local/inherited helper must shadow a statically-imported method of the
    same name, mirroring Java's resolution order."""
    analysis = FakeJavaAnalysis(
        classes={
            "example.Test": make_type(),
            "example.Util": make_type(),
        },
        methods_by_class={
            "example.Test": {
                "testFoo()": make_callable(signature="testFoo()"),
                "helper()": make_callable(signature="helper()"),
            },
            "example.Util": {
                "helper()": make_callable(signature="helper()"),
            },
        },
    )
    static_import_indexes = {
        "example.Test": StaticImportIndex.from_import_entries(
            [
                make_import_declaration(
                    "example.Util.helper",
                    is_static=True,
                )
            ]
        ),
    }
    reachability = Reachability(analysis)
    resolve_helper, _ = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
        add_extended_class=True,
        test_utility_classes=["example.Util"],
        get_static_import_index_for_class=lambda class_name: static_import_indexes.get(
            class_name, StaticImportIndex.EMPTY
        ),
    )

    owner = MethodRef(defining_class_name="example.Test", method_signature="testFoo()")
    call = make_call_site(
        method_name="helper",
        callee_signature="helper()",
        is_static_call=True,
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=8,
    )

    result = resolve_helper(owner, call)

    # Local helper() must win over the static import from example.Util.
    assert result == MethodRef(
        defining_class_name="example.Test",
        method_signature="helper()",
    )


def test_build_helper_resolver_unqualified_local_helper_short_circuits_receiver_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Test": make_type(),
        },
        methods_by_class={
            "example.Test": {
                "testFoo()": make_callable(signature="testFoo()"),
                "helper()": make_callable(signature="helper()"),
            },
        },
    )
    reachability = Reachability(analysis)
    resolve_helper, _ = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
        add_extended_class=True,
    )

    def _unexpected_receiver_resolution(**_kwargs: object) -> None:
        raise AssertionError(
            "resolve_receiver should not run for unqualified local helper resolution"
        )

    monkeypatch.setattr(
        "gerbil.analysis.shared.reachability.resolve_receiver",
        _unexpected_receiver_resolution,
    )

    owner = MethodRef(defining_class_name="example.Test", method_signature="testFoo()")
    call = make_call_site(
        method_name="helper",
        callee_signature="helper()",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=8,
    )

    assert resolve_helper(owner, call) == MethodRef(
        defining_class_name="example.Test",
        method_signature="helper()",
    )


def test_build_helper_resolver_resolves_helper_via_local_receiver_symbol() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Test": make_type(),
            "example.HelperClient": make_type(),
        },
        methods_by_class={
            "example.Test": {
                "testFoo()": make_callable(
                    signature="testFoo()",
                    variable_declarations=[
                        make_variable_declaration(
                            name="client",
                            type_name="example.HelperClient",
                        )
                    ],
                ),
            },
            "example.HelperClient": {
                "helperCall()": make_callable(signature="helperCall()"),
            },
        },
    )
    reachability = Reachability(analysis)
    resolve_helper, _ = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
        add_extended_class=True,
        test_utility_classes=["example.HelperClient"],
    )

    owner = MethodRef(defining_class_name="example.Test", method_signature="testFoo()")
    call = make_call_site(
        method_name="helperCall",
        callee_signature="helperCall()",
        receiver_expr="client",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=18,
    )

    assert resolve_helper(owner, call) == MethodRef(
        defining_class_name="example.HelperClient",
        method_signature="helperCall()",
    )


def test_build_helper_resolver_resolves_helper_via_field_receiver_symbol() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Test": make_type(
                field_declarations=[
                    make_field(
                        type_name="example.HelperClient",
                        variables=["client"],
                    )
                ]
            ),
            "example.HelperClient": make_type(),
        },
        methods_by_class={
            "example.Test": {
                "testFoo()": make_callable(signature="testFoo()"),
            },
            "example.HelperClient": {
                "helperCall()": make_callable(signature="helperCall()"),
            },
        },
    )
    reachability = Reachability(analysis)
    resolve_helper, _ = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
        add_extended_class=True,
        test_utility_classes=["example.HelperClient"],
    )

    owner = MethodRef(defining_class_name="example.Test", method_signature="testFoo()")
    call = make_call_site(
        method_name="helperCall",
        callee_signature="helperCall()",
        receiver_expr="client",
        start_line=2,
        start_column=1,
        end_line=2,
        end_column=18,
    )

    assert resolve_helper(owner, call) == MethodRef(
        defining_class_name="example.HelperClient",
        method_signature="helperCall()",
    )


def test_build_helper_resolver_does_not_guess_when_explicit_receiver_expr_is_unresolved() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={
            "example.Test": make_type(),
        },
        methods_by_class={
            "example.Test": {
                "testFoo()": make_callable(signature="testFoo()"),
                "helperCall()": make_callable(signature="helperCall()"),
            },
        },
    )
    reachability = Reachability(analysis)
    resolve_helper, _ = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
        add_extended_class=True,
    )

    owner = MethodRef(defining_class_name="example.Test", method_signature="testFoo()")
    call = make_call_site(
        method_name="helperCall",
        callee_signature="helperCall()",
        receiver_expr="unknownClient",
        start_line=3,
        start_column=1,
        end_line=3,
        end_column=25,
    )

    assert resolve_helper(owner, call) is None


def test_get_class_resolution_order_excludes_interfaces_when_disabled() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Child": make_type(
                extends_list=["example.Parent"],
                implements_list=["example.Iface"],
            ),
            "example.Parent": make_type(
                extends_list=["example.GrandParent"],
            ),
            "example.GrandParent": make_type(),
            "example.Iface": make_type(),
        },
        methods_by_class={},
    )
    reachability = Reachability(analysis)

    order = reachability.get_class_resolution_order(
        "example.Child", include_interfaces=False
    )

    assert order == ["example.Child", "example.Parent", "example.GrandParent"]


def test_get_class_resolution_order_excludes_superclasses_when_disabled() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Child": make_type(
                extends_list=["example.Parent"],
                implements_list=["example.Iface"],
            ),
            "example.Parent": make_type(),
            "example.Iface": make_type(),
        },
        methods_by_class={},
    )
    reachability = Reachability(analysis)

    order = reachability.get_class_resolution_order(
        "example.Child", include_superclasses=False
    )

    assert order == ["example.Child", "example.Iface"]


def test_get_class_resolution_order_defaults_include_everything() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Child": make_type(
                extends_list=["example.Parent"],
                implements_list=["example.Iface"],
            ),
            "example.Parent": make_type(),
            "example.Iface": make_type(),
        },
        methods_by_class={},
    )
    reachability = Reachability(analysis)

    order = reachability.get_class_resolution_order("example.Child")

    assert order == ["example.Child", "example.Parent", "example.Iface"]


def test_get_class_resolution_order_resolves_same_package_bare_supertype() -> None:
    # A same-package base class is referenced by its bare name (no import), so the
    # hierarchy resolver must resolve it against the declaring class's package.
    analysis = FakeJavaAnalysis(
        classes={
            "example.UserApiTest": make_type(extends_list=["BaseApiTest"]),
            "example.BaseApiTest": make_type(),
        },
        methods_by_class={},
    )
    reachability = Reachability(analysis)

    order = reachability.get_class_resolution_order("example.UserApiTest")

    assert order == ["example.UserApiTest", "example.BaseApiTest"]


def test_get_class_resolution_order_bare_supertype_without_match_is_dropped() -> None:
    # A bare supertype that matches no analyzed same-package class must not be
    # fabricated as a resolution-order entry.
    analysis = FakeJavaAnalysis(
        classes={"example.UserApiTest": make_type(extends_list=["BaseApiTest"])},
        methods_by_class={},
    )
    reachability = Reachability(analysis)

    order = reachability.get_class_resolution_order("example.UserApiTest")

    assert order == ["example.UserApiTest"]


def test_build_helper_resolver_resolves_grandparent_helpers() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Child": make_type(extends_list=["example.Parent"]),
            "example.Parent": make_type(extends_list=["example.GrandParent"]),
            "example.GrandParent": make_type(),
        },
        methods_by_class={
            "example.Child": {
                "testFoo()": make_callable(signature="testFoo()"),
            },
            "example.Parent": {},
            "example.GrandParent": {
                "sharedSetup()": make_callable(signature="sharedSetup()"),
            },
        },
    )
    reachability = Reachability(analysis)
    resolve_helper, _ = reachability.build_helper_resolver(
        qualified_class_name="example.Child",
        add_extended_class=True,
    )

    owner = MethodRef(defining_class_name="example.Child", method_signature="testFoo()")
    grandparent_call = make_call_site(
        method_name="sharedSetup",
        callee_signature="sharedSetup()",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=15,
    )

    result = resolve_helper(owner, grandparent_call)

    assert result == MethodRef(
        defining_class_name="example.GrandParent",
        method_signature="sharedSetup()",
    )


def test_build_helper_resolver_excludes_abstract_interface_methods() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Test": make_type(
                extends_list=[],
                implements_list=["example.Iface"],
            ),
            "example.Iface": make_type(),
        },
        methods_by_class={
            "example.Test": {
                "testFoo()": make_callable(signature="testFoo()"),
            },
            "example.Iface": {
                "abstractMethod()": make_callable(
                    signature="abstractMethod()",
                    code="",
                ),
            },
        },
    )
    reachability = Reachability(analysis)
    resolve_helper, _ = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
        add_extended_class=True,
    )

    owner = MethodRef(defining_class_name="example.Test", method_signature="testFoo()")
    iface_call = make_call_site(
        method_name="abstractMethod",
        callee_signature="abstractMethod()",
        receiver_type="example.Iface",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=15,
    )

    result = resolve_helper(owner, iface_call)

    assert result is None


def test_build_helper_resolver_resolves_default_interface_methods() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Test": make_type(
                extends_list=[],
                implements_list=["example.Iface"],
            ),
            "example.Iface": make_type(),
        },
        methods_by_class={
            "example.Test": {
                "testFoo()": make_callable(signature="testFoo()"),
            },
            "example.Iface": {
                "defaultHelper()": make_callable(
                    signature="defaultHelper()",
                    modifiers=["default"],
                    code="{ return helper(); }",
                ),
            },
        },
    )
    reachability = Reachability(analysis)
    resolve_helper, _ = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
        add_extended_class=True,
    )

    owner = MethodRef(defining_class_name="example.Test", method_signature="testFoo()")
    iface_call = make_call_site(
        method_name="defaultHelper",
        callee_signature="defaultHelper()",
        receiver_type="example.Iface",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=15,
    )

    result = resolve_helper(owner, iface_call)

    assert result == MethodRef(
        defining_class_name="example.Iface",
        method_signature="defaultHelper()",
    )


def test_resolve_helper_finds_inherited_method_via_receiver_type() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Test": make_type(extends_list=["example.Base"]),
            "example.Base": make_type(),
        },
        methods_by_class={
            "example.Test": {
                "testFoo()": make_callable(signature="testFoo()"),
            },
            "example.Base": {
                "inherited()": make_callable(signature="inherited()"),
            },
        },
    )
    reachability = Reachability(analysis)
    resolve_helper, _ = reachability.build_helper_resolver(
        qualified_class_name="example.Test",
        add_extended_class=True,
    )

    owner = MethodRef(defining_class_name="example.Test", method_signature="testFoo()")
    call = make_call_site(
        method_name="inherited",
        callee_signature="inherited()",
        receiver_type="example.Test",
        start_line=1,
        start_column=1,
        end_line=1,
        end_column=12,
    )

    result = resolve_helper(owner, call)

    assert result == MethodRef(
        defining_class_name="example.Base",
        method_signature="inherited()",
    )


def _java_file_path(qualified_class_name: str) -> str:
    return f"src/main/java/{qualified_class_name.replace('.', '/')}.java"


def test_get_class_resolution_order_adds_qualified_library_supertype() -> None:
    repo_type = "example.UserRepository"
    java_file = _java_file_path(repo_type)
    analysis = FakeJavaAnalysis(
        classes={
            repo_type: make_type(
                extends_list=[
                    "org.springframework.data.jpa.repository.JpaRepository<example.User, java.lang.Long>"
                ],
            ),
        },
        java_files={repo_type: java_file},
        import_declarations_by_file={java_file: []},
    )

    order = Reachability(analysis).get_class_resolution_order(repo_type)

    assert order == [
        repo_type,
        "org.springframework.data.jpa.repository.JpaRepository",
    ]


def test_get_class_resolution_order_resolves_bare_supertype_via_import() -> None:
    repo_type = "example.UserRepository"
    java_file = _java_file_path(repo_type)
    analysis = FakeJavaAnalysis(
        classes={
            repo_type: make_type(
                extends_list=["JpaRepository<example.User, java.lang.Long>"],
            ),
        },
        java_files={repo_type: java_file},
        import_declarations_by_file={
            java_file: ["org.springframework.data.jpa.repository.JpaRepository"]
        },
    )

    order = Reachability(analysis).get_class_resolution_order(repo_type)

    assert order == [
        repo_type,
        "org.springframework.data.jpa.repository.JpaRepository",
    ]


def test_get_class_resolution_order_resolves_bare_supertype_via_wildcard_import() -> (
    None
):
    repo_type = "example.UserRepository"
    java_file = _java_file_path(repo_type)
    analysis = FakeJavaAnalysis(
        classes={
            repo_type: make_type(
                extends_list=["JpaRepository<example.User, java.lang.Long>"],
            ),
        },
        java_files={repo_type: java_file},
        import_declarations_by_file={
            java_file: ["org.springframework.data.jpa.repository.*"]
        },
    )

    order = Reachability(analysis).get_class_resolution_order(repo_type)

    assert order == [
        repo_type,
        "org.springframework.data.jpa.repository.JpaRepository",
    ]


def test_get_class_resolution_order_adds_library_interface_from_implements_list() -> (
    None
):
    repo_type = "example.UserRepositoryImpl"
    java_file = _java_file_path(repo_type)
    analysis = FakeJavaAnalysis(
        classes={
            repo_type: make_type(
                implements_list=[
                    "org.springframework.data.jpa.repository.JpaRepository<example.User, java.lang.Long>"
                ],
            ),
        },
        java_files={repo_type: java_file},
        import_declarations_by_file={java_file: []},
    )

    order = Reachability(analysis).get_class_resolution_order(repo_type)

    assert order == [
        repo_type,
        "org.springframework.data.jpa.repository.JpaRepository",
    ]


def test_get_class_resolution_order_does_not_traverse_library_supertype() -> None:
    """Library supertypes are terminal candidates; their own parents are unknowable."""
    container_type = "example.MyPg"
    java_file = _java_file_path(container_type)
    analysis = FakeJavaAnalysis(
        classes={
            container_type: make_type(
                extends_list=[
                    "org.testcontainers.containers.PostgreSQLContainer<example.MyPg>"
                ],
            ),
        },
        java_files={container_type: java_file},
        import_declarations_by_file={java_file: []},
    )

    order = Reachability(analysis).get_class_resolution_order(container_type)

    assert order == [
        container_type,
        "org.testcontainers.containers.PostgreSQLContainer",
    ]


def test_get_class_resolution_order_drops_unresolvable_bare_supertype() -> None:
    """A bare name with no import evidence is dropped from the hierarchy."""
    repo_type = "example.UserRepository"
    java_file = _java_file_path(repo_type)
    analysis = FakeJavaAnalysis(
        classes={
            repo_type: make_type(extends_list=["UnknownRepository<T>"]),
        },
        java_files={repo_type: java_file},
        import_declarations_by_file={java_file: []},
    )

    order = Reachability(analysis).get_class_resolution_order(repo_type)

    assert order == [repo_type]


def test_get_class_resolution_order_drops_ambiguous_bare_supertype_with_multiple_wildcards() -> (
    None
):
    """Multiple wildcard imports make a bare supertype name ambiguous; fail closed."""
    repo_type = "example.UserRepository"
    java_file = _java_file_path(repo_type)
    analysis = FakeJavaAnalysis(
        classes={
            repo_type: make_type(extends_list=["JpaRepository<T, ID>"]),
        },
        java_files={repo_type: java_file},
        import_declarations_by_file={
            java_file: [
                "org.springframework.data.jpa.repository.*",
                "org.springframework.data.mongodb.repository.*",
            ]
        },
    )

    order = Reachability(analysis).get_class_resolution_order(repo_type)

    assert order == [repo_type]
