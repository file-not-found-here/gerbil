from __future__ import annotations

from cldk.models.java.models import JCallSite

from gerbil.analysis.schema import TestingFramework as Framework
from gerbil.analysis.properties import detect_resource_interaction_sequences
from gerbil.analysis.properties.resource_interaction import (
    normalize_request_path,
    resource_key,
)
from gerbil.analysis.test_method import MethodAnalysisInfo
from tests.cldk_factories import make_call_site, make_callable, make_type
from tests.fake_java_analysis import FakeJavaAnalysis


def _http_call_site(start_line: int, method_name: str, path: str) -> JCallSite:
    return make_call_site(
        method_name=method_name,
        receiver_type="io.restassured.specification.RequestSpecification",
        argument_expr=[f'"{path}"'],
        callee_signature=(
            "io.restassured.specification.RequestSpecification"
            f".{method_name}(java.lang.String)"
        ),
        start_line=start_line,
    )


def _analyze_test_method(call_sites: list[JCallSite]):
    qualified_class_name = "example.ApiResourceTest"
    method_signature = "testResourceInteraction()"
    method_name = method_signature.split("(", maxsplit=1)[0]

    analysis = FakeJavaAnalysis(
        classes={qualified_class_name: make_type()},
        methods_by_class={
            qualified_class_name: {
                method_signature: make_callable(
                    signature=method_signature,
                    annotations=["@Test"],
                    declaration=f"void {method_name}()",
                    code="{}",
                    call_sites=call_sites,
                )
            }
        },
        java_files={qualified_class_name: "src/test/java/example/ApiResourceTest.java"},
        import_declarations_by_file={
            "src/test/java/example/ApiResourceTest.java": [
                "org.junit.jupiter.api.Test",
                "io.restassured.specification.RequestSpecification",
            ]
        },
    )

    return MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name=qualified_class_name,
        method_signature=method_signature,
        setup_methods=[],
        teardown_methods=[],
    )


def test_public_api_exports_are_available() -> None:
    assert callable(detect_resource_interaction_sequences)
    assert callable(normalize_request_path)
    assert callable(resource_key)


def test_method_analysis_populates_resource_interaction_sequences() -> None:
    result = _analyze_test_method(
        call_sites=[
            _http_call_site(10, "post", "/users"),
            _http_call_site(11, "get", "/users"),
            make_call_site(
                method_name="assertEquals",
                argument_expr=["1", "1"],
                start_line=13,
            ),
        ]
    )

    assert len(result.http.resource_interaction_sequences) == 1
    seq = result.http.resource_interaction_sequences[0]
    assert seq.resource_key == "/users"
    assert len(seq.steps) == 2
    assert seq.steps[0].http_method == "POST"
    assert seq.steps[1].http_method == "GET"


def test_method_analysis_single_mutation_sequence() -> None:
    result = _analyze_test_method(
        call_sites=[
            _http_call_site(10, "post", "/users"),
            make_call_site(
                method_name="assertEquals",
                argument_expr=["201", "201"],
                start_line=11,
            ),
        ]
    )

    assert len(result.http.resource_interaction_sequences) == 1
    seq = result.http.resource_interaction_sequences[0]
    assert seq.resource_key == "/users"
    assert len(seq.steps) == 1
    assert seq.steps[0].http_method == "POST"


def test_method_analysis_populates_http_test_sequences_and_summary() -> None:
    result = _analyze_test_method(
        call_sites=[
            _http_call_site(10, "get", "/users/1"),
            _http_call_site(11, "get", "/users/2"),
        ]
    )

    assert len(result.http.test_sequences) == 2
    assert result.http.test_sequences[0].fingerprint == (
        result.http.test_sequences[1].fingerprint
    )
    assert result.http.sequence_summary.sequence_count == 2
    assert result.http.sequence_summary.has_repeated_sequence
    assert result.http.sequence_summary.distinct_http_method_count == 1
    assert result.http.sequence_summary.distinct_resource_count == 1
    assert result.http.sequence_summary.distinct_endpoint_count == 1


def test_method_analysis_dynamic_path_is_excluded() -> None:
    result = _analyze_test_method(
        call_sites=[
            _http_call_site(10, "post", "/users/${id}"),
            _http_call_site(11, "get", "/users/1"),
            make_call_site(
                method_name="assertEquals", argument_expr=["1", "1"], start_line=12
            ),
        ]
    )

    assert len(result.http.resource_interaction_sequences) == 1
    seq = result.http.resource_interaction_sequences[0]
    assert seq.steps[0].http_method == "GET"


def test_method_analysis_missing_method_uses_safe_defaults() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.Missing": make_type()},
        methods_by_class={},
    )

    result = MethodAnalysisInfo(
        analysis=analysis,
        application_classes=[],
    ).get_test_method_analysis_info(
        testing_frameworks=[Framework.JUNIT5],
        qualified_class_name="example.Missing",
        method_signature="doesNotExist()",
        setup_methods=[],
        teardown_methods=[],
    )

    assert result.http.resource_interaction_sequences == []


def test_multiple_resources_produce_separate_sequences() -> None:
    result = _analyze_test_method(
        call_sites=[
            _http_call_site(10, "post", "/users/1"),
            _http_call_site(11, "get", "/users/2"),
            _http_call_site(12, "post", "/orders/1"),
            _http_call_site(13, "get", "/orders/2"),
        ]
    )

    sequences = result.http.resource_interaction_sequences
    assert len(sequences) == 2
    keys = {seq.resource_key for seq in sequences}
    assert keys == {"/users", "/orders"}
