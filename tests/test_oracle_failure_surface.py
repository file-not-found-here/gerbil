from __future__ import annotations

from cldk.models.java import JImport

from gerbil.analysis.runtime.call_sites import (
    MethodRef,
    build_call_site_grouping,
)
from gerbil.analysis.schema import HttpResponseRole, LifecyclePhase
from gerbil.analysis.properties.assertion.surface import build_assertion_summary
from gerbil.analysis.properties.assertion.oracle import classify_oracle_type
from gerbil.analysis.properties.assertion.failure import classify_failure_scenarios
from gerbil.analysis.assertion import classify_assertions_on_runtime_view
from gerbil.analysis.runtime import PhaseEntry, TestRuntimeView
from gerbil.analysis.shared.static_imports import StaticImportIndex
from tests.cldk_factories import (
    annotate_node_http,
    build_runtime_receiver_resolver_for_testing,
    make_call_site,
    make_callable,
)


def _runtime_view_for_method(
    method, *, class_name: str = "example.TestClass"
) -> TestRuntimeView:
    return TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name=class_name,
                    method_signature="testCase()",
                ),
                context_class_name=class_name,
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
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


def test_oracle_surface_and_failure_classification() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="andExpect",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=40,
            ),
            make_call_site(
                method_name="statusCode",
                argument_expr=["500"],
                start_line=1,
                start_column=5,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="assertThrows",
                argument_expr=["RuntimeException.class"],
                start_line=2,
            ),
            make_call_site(
                method_name="assertTimeout",
                argument_expr=["Duration.ofMillis(100)"],
                start_line=3,
            ),
        ]
    )

    runtime_view = _runtime_view_for_method(method)
    nodes = list(runtime_view.entries[0].grouping.nodes)
    annotate_node_http(
        nodes[1],
        http_method="GET",
        path="/api",
        request_role=None,
        response_role=HttpResponseRole.STATUS_ASSERTION,
    )
    resolver = _runtime_receiver_resolver(runtime_view)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=resolver,
    )

    surface = build_assertion_summary(runtime_view=runtime_view)
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=[],
        receiver_resolver=resolver,
    )
    failure = classify_failure_scenarios(runtime_view=runtime_view)

    assert surface.status_count >= 1
    assert oracle.label == "example-based"
    assert failure.has_exception_assertion is True
    assert failure.has_server_error_assertion is True


def test_teardown_only_hints_change_oracle_and_failure_labels() -> None:
    method = make_callable(call_sites=[])
    teardown_method = make_callable(
        signature="afterEach()",
        call_sites=[make_call_site(method_name="assertTimeout", start_line=9)],
    )
    runtime_view = TestRuntimeView(
        entries=[
            PhaseEntry(
                phase=LifecyclePhase.TEST,
                method_ref=MethodRef(
                    defining_class_name="example.TestClass",
                    method_signature="testCase()",
                ),
                context_class_name="example.TestClass",
                grouping=build_call_site_grouping(list(method.call_sites)),
                method_details=method,
            ),
            PhaseEntry(
                phase=LifecyclePhase.TEARDOWN,
                method_ref=MethodRef(
                    defining_class_name="example.TestClass",
                    method_signature="afterEach()",
                ),
                context_class_name="example.TestClass",
                grouping=build_call_site_grouping(list(teardown_method.call_sites)),
                method_details=teardown_method,
            ),
        ],
    )

    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    surface = build_assertion_summary(runtime_view=runtime_view)
    failure = classify_failure_scenarios(runtime_view=runtime_view)

    assert surface.status_count == 0
    assert surface.body_count == 0
    assert surface.header_count == 0
    assert failure.has_client_error_assertion is False
    assert failure.has_server_error_assertion is False
    assert failure.has_exception_assertion is False


def test_failure_scenarios_detects_4xx_range_status_predicate() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertTrue",
                argument_expr=["status.is4xxClientError()"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="is4xxClientError",
                start_line=1,
                start_column=10,
                end_line=1,
                end_column=28,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    failure = classify_failure_scenarios(runtime_view=runtime_view)

    assert failure.has_client_error_assertion is True
    assert failure.has_server_error_assertion is False


def test_failure_scenarios_negated_status_equality_is_not_client_error() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertThat",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="getStatusCode",
                start_line=1,
                start_column=12,
                end_line=1,
                end_column=27,
            ),
            make_call_site(
                method_name="isNotEqualTo",
                argument_expr=["404"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=48,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    failure = classify_failure_scenarios(runtime_view=runtime_view)

    assert failure.has_client_error_assertion is False
    assert failure.has_server_error_assertion is False


def test_failure_scenarios_detects_assert_throws_variant_roots() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertThrowsExactly",
                argument_expr=["IllegalStateException.class"],
                start_line=1,
            ),
            make_call_site(
                method_name="expectThrows",
                argument_expr=["RuntimeException.class"],
                start_line=2,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    failure = classify_failure_scenarios(runtime_view=runtime_view)

    assert failure.has_exception_assertion is True


def test_failure_scenarios_detects_generic_status_matcher_4xx() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="assertThat",
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=30,
            ),
            make_call_site(
                method_name="getStatusCode",
                start_line=1,
                start_column=12,
                end_line=1,
                end_column=27,
            ),
            make_call_site(
                method_name="isEqualTo",
                argument_expr=["404"],
                start_line=1,
                start_column=1,
                end_line=1,
                end_column=45,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    failure = classify_failure_scenarios(runtime_view=runtime_view)

    assert failure.has_client_error_assertion is True
    assert failure.has_server_error_assertion is False


def test_junit_static_import_fail_classifies_as_general_assertion() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="fail",
                start_line=1,
                start_column=1,
            ),
        ]
    )
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="org.junit.Assert.fail",
                is_static=True,
                is_wildcard=False,
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    surface = build_assertion_summary(runtime_view=runtime_view)
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=[],
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    assert surface.general_count == 1
    assert oracle.label == "example-based"


def test_bare_domain_fail_is_not_classified() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="fail",
                receiver_type="com.example.Job",
                start_line=1,
                start_column=1,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    surface = build_assertion_summary(runtime_view=runtime_view)
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=[],
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    assert surface.total_count == 0
    assert oracle.label == "implicit"


def test_junit_expected_exception_annotation_sets_failure_and_oracle_signals() -> None:
    method = make_callable(
        annotations=["@Test(expected = NotFoundException.class)"],
        call_sites=[],
    )
    class_imports = [JImport(path="org.junit.Test", is_static=False, is_wildcard=False)]
    runtime_view = _runtime_view_for_method(method)

    failure = classify_failure_scenarios(
        runtime_view=runtime_view,
        method_annotations=method.annotations,
        class_imports=class_imports,
    )
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=class_imports,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    assert failure.has_exception_assertion is True
    assert oracle.label == "example-based"
    assert "expected-exception-annotation" in oracle.signals["example-based"]


def test_testng_expected_exceptions_annotation_sets_failure_and_oracle_signals() -> (
    None
):
    method = make_callable(
        annotations=["@Test(expectedExceptions = {NotFoundException.class})"],
        call_sites=[],
    )
    class_imports = [
        JImport(path="org.testng.annotations.Test", is_static=False, is_wildcard=False)
    ]
    runtime_view = _runtime_view_for_method(method)

    failure = classify_failure_scenarios(
        runtime_view=runtime_view,
        method_annotations=method.annotations,
        class_imports=class_imports,
    )
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=class_imports,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    assert failure.has_exception_assertion is True
    assert oracle.label == "example-based"
    assert "expected-exception-annotation" in oracle.signals["example-based"]


def test_test_timeout_annotation_does_not_set_exception_signal() -> None:
    method = make_callable(
        annotations=["@Test(timeout = 1000)"],
        call_sites=[],
    )
    class_imports = [JImport(path="org.junit.Test", is_static=False, is_wildcard=False)]
    runtime_view = _runtime_view_for_method(method)

    failure = classify_failure_scenarios(
        runtime_view=runtime_view,
        method_annotations=method.annotations,
        class_imports=class_imports,
    )
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=class_imports,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    assert failure.has_exception_assertion is False
    assert oracle.label == "implicit"


def test_expected_exception_annotation_without_import_evidence_is_ignored() -> None:
    method = make_callable(
        annotations=["@Test(expected = NotFoundException.class)"],
        call_sites=[],
    )
    runtime_view = _runtime_view_for_method(method)

    failure = classify_failure_scenarios(
        runtime_view=runtime_view,
        method_annotations=method.annotations,
        class_imports=[],
    )
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=[],
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    assert failure.has_exception_assertion is False
    assert oracle.label == "implicit"


def test_wildcard_junit_assert_fail_classifies_as_general_assertion() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="fail",
                start_line=1,
                start_column=1,
            ),
        ]
    )
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="org.junit.Assert",
                is_static=True,
                is_wildcard=True,
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    surface = build_assertion_summary(runtime_view=runtime_view)
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=[],
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    assert surface.general_count == 1
    assert oracle.label == "example-based"


def test_wildcard_non_assertion_fail_does_not_classify() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="fail",
                start_line=1,
                start_column=1,
            ),
        ]
    )
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="com.example.Helpers",
                is_static=True,
                is_wildcard=True,
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    surface = build_assertion_summary(runtime_view=runtime_view)
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=[],
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    assert surface.total_count == 0
    assert oracle.label == "implicit"


def test_ambiguous_double_wildcard_fail_fails_closed() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="fail",
                start_line=1,
                start_column=1,
            ),
        ]
    )
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="org.junit.Assert",
                is_static=True,
                is_wildcard=True,
            ),
            JImport(
                path="org.assertj.core.api.Assertions",
                is_static=True,
                is_wildcard=True,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    surface = build_assertion_summary(runtime_view=runtime_view)
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=[],
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    assert surface.total_count == 0
    assert oracle.label == "implicit"


def test_junit3_assert_fail_direct_receiver_classifies_as_general_assertion() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="fail",
                receiver_type="junit.framework.Assert",
                start_line=1,
                start_column=1,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    surface = build_assertion_summary(runtime_view=runtime_view)
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=[],
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    assert surface.general_count == 1
    assert oracle.label == "example-based"


def test_junit3_testcase_fail_direct_receiver_classifies_as_general_assertion() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="fail",
                receiver_type="junit.framework.TestCase",
                start_line=1,
                start_column=1,
            ),
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    surface = build_assertion_summary(runtime_view=runtime_view)
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=[],
        receiver_resolver=_runtime_receiver_resolver(runtime_view),
    )

    assert surface.general_count == 1
    assert oracle.label == "example-based"


def test_junit3_assert_static_import_fail_classifies_as_general_assertion() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="fail",
                start_line=1,
                start_column=1,
            ),
        ]
    )
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="junit.framework.Assert.fail",
                is_static=True,
                is_wildcard=False,
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    surface = build_assertion_summary(runtime_view=runtime_view)
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=[],
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    assert surface.general_count == 1
    assert oracle.label == "example-based"


def test_junit3_wildcard_testcase_fail_classifies_as_general_assertion() -> None:
    method = make_callable(
        call_sites=[
            make_call_site(
                method_name="fail",
                start_line=1,
                start_column=1,
            ),
        ]
    )
    static_import_index = StaticImportIndex.from_import_entries(
        [
            JImport(
                path="junit.framework.TestCase",
                is_static=True,
                is_wildcard=True,
            )
        ]
    )
    runtime_view = _runtime_view_for_method(method)
    classify_assertions_on_runtime_view(
        runtime_view,
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    surface = build_assertion_summary(runtime_view=runtime_view)
    oracle = classify_oracle_type(
        runtime_view=runtime_view,
        method_details=method,
        class_imports=[],
        receiver_resolver=_runtime_receiver_resolver(
            runtime_view, static_import_index=static_import_index
        ),
    )

    assert surface.general_count == 1
    assert oracle.label == "example-based"
