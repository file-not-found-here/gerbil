from __future__ import annotations

from gerbil.analysis.shared.class_utils import is_in_test_directory
from gerbil.analysis.shared.framework_inference import infer_spring_subframeworks
from gerbil.analysis.shared.constants import (
    FRAMEWORK_PREFIXES,
    TEST_DIRS,
)
from gerbil.analysis.schema import (
    HttpDispatchFramework,
    TestingFramework as Framework,
)
from gerbil.analysis.properties.request_dispatch import analyze_request_dispatch
from tests.cldk_factories import (
    make_import_declaration,
    make_import_declarations,
    make_resolved_annotation,
)


def test_spring_prefixes_remain_holistic() -> None:
    assert FRAMEWORK_PREFIXES["org.springframework.test."] == Framework.SPRING_TEST
    assert "org.springframework.test.web.servlet." not in FRAMEWORK_PREFIXES
    assert "org.springframework.test.web.reactive.server" not in FRAMEWORK_PREFIXES


def test_test_dirs_are_immutable_tuple() -> None:
    assert TEST_DIRS == (
        "src/test/java",
        "src/integrationTest/java",
        "src/functionalTest/java",
    )
    assert isinstance(TEST_DIRS, tuple)


def test_is_in_test_directory_is_path_segment_safe() -> None:
    test_dirs = ("src/test/java",)

    assert is_in_test_directory("src/test/java/example/FooTest.java", test_dirs)
    assert not is_in_test_directory("src/test/javax/example/FooTest.java", test_dirs)


def test_infer_spring_subframeworks_from_imports_and_annotations() -> None:
    frameworks = infer_spring_subframeworks(
        class_imports=make_import_declarations(
            "org.springframework.test.web.servlet.MockMvc",
            "org.springframework.boot.test.web.client.TestRestTemplate",
        ),
        class_annotations=[
            make_resolved_annotation(
                "@org.springframework.boot.test.autoconfigure.web.reactive.WebFluxTest"
            )
        ],
        class_annotation_imports_by_class={},
    )

    assert HttpDispatchFramework.MOCKMVC in frameworks
    assert HttpDispatchFramework.WEBTESTCLIENT in frameworks
    assert HttpDispatchFramework.TEST_REST_TEMPLATE in frameworks


def test_infer_spring_subframeworks_rejects_webtestclient_prefix_lookalike() -> None:
    frameworks = infer_spring_subframeworks(
        class_imports=make_import_declarations(
            "org.springframework.test.web.reactive.serverless.Client"
        ),
        class_annotations=[],
        class_annotation_imports_by_class={},
    )

    assert HttpDispatchFramework.WEBTESTCLIENT not in frameworks


def test_infer_spring_subframeworks_accepts_webtestclient_valid_package() -> None:
    frameworks = infer_spring_subframeworks(
        class_imports=make_import_declarations(
            "org.springframework.test.web.reactive.server.WebTestClient",
            "org.springframework.test.web.reactive.server.WebTestClient.ResponseSpec",
        ),
        class_annotations=[],
        class_annotation_imports_by_class={},
    )

    assert HttpDispatchFramework.WEBTESTCLIENT in frameworks


def test_infer_spring_subframeworks_accepts_static_imports() -> None:
    frameworks = infer_spring_subframeworks(
        class_imports=[
            make_import_declaration(
                "org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get",
                is_static=True,
            ),
            make_import_declaration(
                "org.springframework.test.web.reactive.server.WebTestClient.bindToServer",
                is_static=True,
            ),
        ],
        class_annotations=[],
        class_annotation_imports_by_class={},
    )

    assert HttpDispatchFramework.MOCKMVC in frameworks
    assert HttpDispatchFramework.WEBTESTCLIENT in frameworks


def test_request_dispatch_returns_unknown_without_runtime() -> None:
    result = analyze_request_dispatch(runtime_view=None)
    assert result.labels == ["unknown"]
