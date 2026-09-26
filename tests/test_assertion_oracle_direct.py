from __future__ import annotations

from cldk.models.java import JImport

from gerbil.analysis.runtime.call_sites import MethodRef, build_call_site_grouping
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.schema import (
    AssertionClassification,
    AssertionRole,
    OracleTypeDecision,
    LifecyclePhase,
)
from gerbil.analysis.properties.assertion.oracle import classify_oracle_type
from gerbil.analysis.assertion import classify_assertions_on_runtime_view
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
)


def _runtime_view_for_method(
    method_details,
    *,
    class_name: str = "example.ApiTest",
    method_signature: str = "testCase()",
) -> TestRuntimeView:
    if method_details is None:
        return TestRuntimeView()

    return TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name=class_name,
                    method_signature=method_signature,
                ),
                context_class_name=class_name,
                grouping=build_call_site_grouping(list(method_details.call_sites)),
                method_details=method_details,
            )
        ]
    )


def _runtime_receiver_resolver(
    runtime_view: TestRuntimeView,
    *,
    static_import_index: StaticImportIndex = StaticImportIndex.EMPTY,
):
    return build_runtime_receiver_resolver_for_testing(
        runtime_view,
        get_static_import_index_for_class=lambda _class_name: static_import_index,
    )


def _classify_oracle(
    runtime_view: TestRuntimeView,
    method_details,
    *,
    class_imports: list[JImport] | None = None,
    static_import_index: StaticImportIndex = StaticImportIndex.EMPTY,
) -> OracleTypeDecision:
    resolver = _runtime_receiver_resolver(
        runtime_view, static_import_index=static_import_index
    )
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=resolver,
    )
    return classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method_details,
        class_imports=class_imports or [],
        receiver_resolver=resolver,
    )


# ── Example-based detection ──────────────────────────────────────────


def test_example_based_from_status_assertion() -> None:
    method = make_callable(call_sites=[make_call_site(method_name="assertEquals")])
    runtime_view = _runtime_view_for_method(method)
    for node in runtime_view.entries[0].grouping.nodes:
        if node.call_site.method_name == "assertEquals":
            node.assertion_classification = AssertionClassification(
                role=AssertionRole.STATUS
            )

    result = _classify_oracle(runtime_view, method)
    assert result.label == "example-based"


def test_example_based_from_body_assertion() -> None:
    method = make_callable(call_sites=[make_call_site(method_name="assertThat")])
    runtime_view = _runtime_view_for_method(method)
    for node in runtime_view.entries[0].grouping.nodes:
        if node.call_site.method_name == "assertThat":
            node.assertion_classification = AssertionClassification(
                role=AssertionRole.BODY
            )

    result = _classify_oracle(runtime_view, method)
    assert result.label == "example-based"


def test_example_based_from_exception_assertion() -> None:
    method = make_callable(call_sites=[make_call_site(method_name="assertThrows")])
    runtime_view = _runtime_view_for_method(method)
    for node in runtime_view.entries[0].grouping.nodes:
        if node.call_site.method_name == "assertThrows":
            node.assertion_classification = AssertionClassification(
                role=AssertionRole.EXCEPTION
            )

    result = _classify_oracle(runtime_view, method)
    assert result.label == "example-based"


# ── Contract detection via receiver type ─────────────────────────────


def test_contract_from_pact_receiver() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="uponReceiving",
                receiver_type="au.com.dius.pact.consumer.dsl.PactDslWithProvider",
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "contract"


def test_contract_from_rest_assured_jsv_receiver() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="matchesJsonSchemaInClasspath",
                receiver_type="io.restassured.module.jsv.JsonSchemaValidator",
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    for node in runtime_view.entries[0].grouping.nodes:
        node.assertion_classification = AssertionClassification(role=AssertionRole.BODY)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "contract"
    assert "example-based" in result.signals


def test_contract_from_legacy_rest_assured_jsv_receiver() -> None:
    """RestAssured 2.x shipped the json-schema-validator module under com.jayway.restassured."""
    method = make_callable(
        call_sites=[
            make_call_site(
                # `using` is not a contract method hint, so a match here proves the
                # legacy jsv receiver prefix is what drives detection.
                method_name="using",
                receiver_type="com.jayway.restassured.module.jsv.JsonSchemaValidator",
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "contract"
    assert any(
        "com.jayway.restassured.module.jsv" in signal
        for signal in result.signals["contract"]
    )


def test_contract_from_spring_cloud_contract_receiver() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="trigger",
                receiver_type="org.springframework.cloud.contract.stubrunner.StubTrigger",
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "contract"


def test_contract_from_everit_receiver() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="validate",
                receiver_type="org.everit.json.schema.Schema",
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "contract"


def test_contract_from_networknt_receiver() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="validate",
                receiver_type="com.networknt.schema.JsonSchema",
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "contract"


def test_contract_from_openapi_validator_receiver() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="validate",
                receiver_type="com.atlassian.oai.validator.OpenApiInteractionValidator",
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "contract"


# ── Contract detection via method name (fallback) ────────────────────


def test_contract_from_matchesJsonSchemaInClasspath_method() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(method_name="matchesJsonSchemaInClasspath"),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "contract"


def test_contract_from_matchesJsonSchema_method() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(method_name="matchesJsonSchema"),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "contract"


def test_contract_from_validatesAgainst_method() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(method_name="validatesAgainst"),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "contract"


# ── Property-based detection via annotation ──────────────────────────


def test_property_based_from_jqwik_annotation() -> None:
    method = make_callable(
        annotations=["@Property"],
        call_sites=[make_call_site(method_name="assertThat")],
    )
    class_imports = [
        JImport(path="net.jqwik.api.Property", is_static=False, is_wildcard=False)
    ]
    runtime_view = _runtime_view_for_method(method)
    for node in runtime_view.entries[0].grouping.nodes:
        node.assertion_classification = AssertionClassification(
            role=AssertionRole.GENERAL
        )

    result = _classify_oracle(runtime_view, method, class_imports=class_imports)
    assert result.label == "property-based"
    assert "example-based" in result.signals


def test_property_based_from_quickcheck_annotation() -> None:
    method = make_callable(
        annotations=["@Property"],
        call_sites=[],
    )
    class_imports = [
        JImport(
            path="com.pholser.junit.quickcheck.Property",
            is_static=False,
            is_wildcard=False,
        )
    ]
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method, class_imports=class_imports)
    assert result.label == "property-based"


def test_property_annotation_fails_closed_without_matching_import() -> None:
    method = make_callable(
        annotations=["@Property"],
        call_sites=[make_call_site(method_name="assertThat")],
    )
    runtime_view = _runtime_view_for_method(method)
    for node in runtime_view.entries[0].grouping.nodes:
        node.assertion_classification = AssertionClassification(
            role=AssertionRole.GENERAL
        )

    result = _classify_oracle(runtime_view, method, class_imports=[])
    assert result.label == "example-based"
    assert result.label != "property-based"


# ── Property-based detection via receiver type ───────────────────────


def test_property_based_from_jqwik_receiver() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="check",
                receiver_type="net.jqwik.api.PropertyChecker",
                callee_signature="net.jqwik.api.PropertyChecker.check()",
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "property-based"


def test_property_based_from_quicktheories_receiver() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="forAll",
                receiver_type="org.quicktheories.QuickTheory",
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "property-based"


# ── Property-based detection via strong method names ─────────────────


def test_property_based_from_forAll_method() -> None:
    method = make_callable(call_sites=[make_call_site(method_name="forAll")])
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "property-based"


def test_property_based_from_qt_method() -> None:
    method = make_callable(call_sites=[make_call_site(method_name="qt")])
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "property-based"


# ── Property-based: check disambiguation ─────────────────────────────


def test_check_with_property_receiver_context() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="check",
                receiver_type="net.jqwik.api.PropertyChecker",
                callee_signature="net.jqwik.api.PropertyChecker.check()",
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "property-based"


def test_check_without_property_context_not_property_based() -> None:
    method = make_callable(call_sites=[make_call_site(method_name="check")])
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label != "property-based"


def test_check_with_property_static_import_context() -> None:
    method = make_callable(call_sites=[make_call_site(method_name="check")])
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="net.jqwik.api.PropertyChecker",
                is_static=True,
                is_wildcard=True,
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(
        runtime_view, method, static_import_index=static_import_index
    )
    assert result.label == "property-based"


# ── Implicit detection ───────────────────────────────────────────────


def test_implicit_from_empty_runtime_view() -> None:
    result = _classify_oracle(TestRuntimeView(), None)
    assert result.label == "implicit"


def test_implicit_from_no_assertion_nodes() -> None:
    method = make_callable(call_sites=[make_call_site(method_name="someSetupCall")])
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "implicit"


# ── Precedence and signal retention ──────────────────────────────────


def test_contract_with_assertions_has_example_based_signal() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="matchesJsonSchemaInClasspath",
                start_line=12,
                start_column=5,
                end_line=12,
                end_column=40,
            ),
            make_call_site(
                method_name="assertEquals",
                start_line=13,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    for node in runtime_view.entries[0].grouping.nodes:
        node.assertion_classification = AssertionClassification(role=AssertionRole.BODY)

    result = _classify_oracle(runtime_view, method)
    assert result.label == "contract"
    assert "example-based" in result.signals


def test_property_with_assertions_has_example_based_signal() -> None:
    method = make_callable(
        annotations=["@Property"],
        call_sites=[make_call_site(method_name="assertThat")],
    )
    class_imports = [
        JImport(path="net.jqwik.api.Property", is_static=False, is_wildcard=False)
    ]
    runtime_view = _runtime_view_for_method(method)
    for node in runtime_view.entries[0].grouping.nodes:
        node.assertion_classification = AssertionClassification(
            role=AssertionRole.GENERAL
        )
    result = _classify_oracle(runtime_view, method, class_imports=class_imports)
    assert result.label == "property-based"
    assert "example-based" in result.signals


def test_property_beats_contract_in_precedence() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="forAll",
                receiver_type="net.jqwik.api.Arbitraries",
            ),
            make_call_site(
                method_name="matchesJsonSchemaInClasspath",
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert result.label == "property-based"
    assert "contract" in result.signals


# ── Signal-focused tests ─────────────────────────────────────────────


def test_example_based_signals_include_assertion_count() -> None:
    method = make_callable(call_sites=[make_call_site(method_name="assertEquals")])
    runtime_view = _runtime_view_for_method(method)
    for node in runtime_view.entries[0].grouping.nodes:
        node.assertion_classification = AssertionClassification(
            role=AssertionRole.STATUS
        )

    result = _classify_oracle(runtime_view, method)
    assert "assertion-count:1" in result.signals["example-based"]


def test_contract_signals_include_method_hint() -> None:
    method = make_callable(
        call_sites=[make_call_site(method_name="matchesJsonSchemaInClasspath")]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert "method:matchesJsonSchemaInClasspath" in result.signals["contract"]


def test_property_signals_include_receiver() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="check",
                receiver_type="net.jqwik.api.PropertyChecker",
                callee_signature="net.jqwik.api.PropertyChecker.check()",
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert any(
        entry.startswith("receiver:") for entry in result.signals["property-based"]
    )


def test_property_with_contract_signals_retains_both() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="forAll",
                receiver_type="net.jqwik.api.Arbitraries",
            ),
            make_call_site(method_name="matchesJsonSchemaInClasspath"),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method)
    assert "property-based" in result.signals
    assert "contract" in result.signals


def test_implicit_signals_are_empty() -> None:
    result = _classify_oracle(TestRuntimeView(), None)
    assert result.signals == {}


def test_property_annotation_signal() -> None:
    method = make_callable(
        annotations=["@Property"],
        call_sites=[],
    )
    class_imports = [
        JImport(path="net.jqwik.api.Property", is_static=False, is_wildcard=False)
    ]
    runtime_view = _runtime_view_for_method(method)
    result = _classify_oracle(runtime_view, method, class_imports=class_imports)
    assert "annotation:@Property" in result.signals["property-based"]
