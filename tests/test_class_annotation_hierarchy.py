from __future__ import annotations

import pytest

from gerbil.analysis.shared import CommonAnalysis
from gerbil.analysis.shared.class_utils import (
    ClassAnnotationResolutionConfig,
    ResolvedAnnotation,
    resolve_effective_class_annotations,
)
from tests.cldk_factories import make_type
from tests.fake_java_analysis import FakeJavaAnalysis


def test_resolve_effective_class_annotations_can_toggle_interface_traversal() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiContract": make_type(annotations=["@Controller"]),
            "example.BaseController": make_type(
                annotations=['@RequestMapping("/api")']
            ),
            "example.UserController": make_type(
                extends_list=["example.BaseController"],
                implements_list=["example.ApiContract"],
            ),
        }
    )
    known_class_names = set(analysis.get_classes().keys())

    without_interfaces = resolve_effective_class_annotations(
        analysis=analysis,
        qualified_class_name="example.UserController",
        known_class_names=known_class_names,
        config=ClassAnnotationResolutionConfig(
            include_superclasses=True,
            include_interfaces=False,
            require_inherited_annotations_from_parents=False,
        ),
    )
    with_interfaces = resolve_effective_class_annotations(
        analysis=analysis,
        qualified_class_name="example.UserController",
        known_class_names=known_class_names,
        config=ClassAnnotationResolutionConfig(
            include_superclasses=True,
            include_interfaces=True,
            require_inherited_annotations_from_parents=False,
        ),
    )

    without_interface_annotations = [
        resolved_annotation.annotation for resolved_annotation in without_interfaces
    ]
    with_interface_annotations = [
        resolved_annotation.annotation for resolved_annotation in with_interfaces
    ]

    assert '@RequestMapping("/api")' in without_interface_annotations
    assert "@Controller" not in without_interface_annotations
    assert without_interfaces[0].declaring_class_name == "example.BaseController"

    assert '@RequestMapping("/api")' in with_interface_annotations
    assert "@Controller" in with_interface_annotations


def test_resolve_effective_class_annotations_prefers_interface_over_superclass() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={
            "example.ApiContract": make_type(annotations=['@RequestMapping("/iface")']),
            "example.BaseController": make_type(
                annotations=['@RequestMapping("/base")']
            ),
            "example.UserController": make_type(
                extends_list=["example.BaseController"],
                implements_list=["example.ApiContract"],
            ),
        }
    )

    resolved = resolve_effective_class_annotations(
        analysis=analysis,
        qualified_class_name="example.UserController",
        known_class_names=set(analysis.get_classes().keys()),
        config=ClassAnnotationResolutionConfig(
            include_superclasses=True,
            include_interfaces=True,
        ),
    )

    request_mappings = [
        resolved_annotation
        for resolved_annotation in resolved
        if resolved_annotation.annotation.startswith("@RequestMapping")
    ]
    assert [
        (annotation.annotation, annotation.declaring_class_name)
        for annotation in request_mappings
    ] == [('@RequestMapping("/iface")', "example.ApiContract")]


def test_resolve_effective_class_annotations_searches_superinterfaces_before_superclass() -> (
    None
):
    analysis = FakeJavaAnalysis(
        classes={
            "example.RootContract": make_type(
                annotations=['@RequestMapping("/root-iface")'],
                is_interface=True,
            ),
            "example.ApiContract": make_type(
                extends_list=["example.RootContract"],
                is_interface=True,
            ),
            "example.BaseController": make_type(
                annotations=['@RequestMapping("/base")']
            ),
            "example.UserController": make_type(
                extends_list=["example.BaseController"],
                implements_list=["example.ApiContract"],
            ),
        }
    )

    resolved = resolve_effective_class_annotations(
        analysis=analysis,
        qualified_class_name="example.UserController",
        known_class_names=set(analysis.get_classes().keys()),
        config=ClassAnnotationResolutionConfig(
            include_superclasses=True,
            include_interfaces=True,
        ),
    )

    request_mappings = [
        resolved_annotation.declaring_class_name
        for resolved_annotation in resolved
        if resolved_annotation.annotation.startswith("@RequestMapping")
    ]
    assert request_mappings == ["example.RootContract"]


def test_resolve_effective_class_annotations_requires_filter_in_inherited_only_mode() -> (
    None
):
    analysis = FakeJavaAnalysis(classes={"example.Controller": make_type()})

    with pytest.raises(ValueError):
        resolve_effective_class_annotations(
            analysis=analysis,
            qualified_class_name="example.Controller",
            config=ClassAnnotationResolutionConfig(
                include_superclasses=True,
                include_interfaces=False,
                require_inherited_annotations_from_parents=True,
            ),
        )


def test_resolve_effective_class_annotations_can_filter_parent_annotations() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.BaseController": make_type(annotations=["@RestController"]),
            "example.UserController": make_type(
                extends_list=["example.BaseController"],
            ),
        }
    )

    allow_none = resolve_effective_class_annotations(
        analysis=analysis,
        qualified_class_name="example.UserController",
        config=ClassAnnotationResolutionConfig(
            include_superclasses=True,
            include_interfaces=False,
            require_inherited_annotations_from_parents=True,
        ),
        inherited_annotation_filter=lambda annotation_name, declaring_class_name: False,
    )
    allow_all = resolve_effective_class_annotations(
        analysis=analysis,
        qualified_class_name="example.UserController",
        config=ClassAnnotationResolutionConfig(
            include_superclasses=True,
            include_interfaces=False,
            require_inherited_annotations_from_parents=True,
        ),
        inherited_annotation_filter=lambda annotation_name, declaring_class_name: True,
    )
    no_filter_required = resolve_effective_class_annotations(
        analysis=analysis,
        qualified_class_name="example.UserController",
        config=ClassAnnotationResolutionConfig(
            include_superclasses=True,
            include_interfaces=False,
            require_inherited_annotations_from_parents=False,
        ),
    )

    assert allow_none == []
    assert allow_all == [
        ResolvedAnnotation(
            annotation="@RestController",
            declaring_class_name="example.BaseController",
        )
    ]
    assert no_filter_required == [
        ResolvedAnnotation(
            annotation="@RestController",
            declaring_class_name="example.BaseController",
        )
    ]


def test_spring_boot_test_on_base_class_is_visible_to_subclass() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.BaseIntegrationTest": make_type(annotations=["@SpringBootTest"]),
            "example.UserApiTest": make_type(
                extends_list=["example.BaseIntegrationTest"]
            ),
        },
        java_files={
            "example.BaseIntegrationTest": (
                "src/test/java/example/BaseIntegrationTest.java"
            ),
            "example.UserApiTest": "src/test/java/example/UserApiTest.java",
        },
        import_declarations_by_file={
            "src/test/java/example/BaseIntegrationTest.java": [
                "org.springframework.boot.test.context.SpringBootTest"
            ],
            "src/test/java/example/UserApiTest.java": [],
        },
    )

    resolved = CommonAnalysis(analysis).resolve_effective_class_annotations(
        "example.UserApiTest"
    )

    assert resolved == [
        ResolvedAnnotation(
            annotation="@SpringBootTest",
            declaring_class_name="example.BaseIntegrationTest",
        )
    ]


def test_project_local_test_annotation_on_base_requires_in_source_inherited() -> None:
    def build_analysis(local_annotation_markers: list[str]) -> FakeJavaAnalysis:
        return FakeJavaAnalysis(
            classes={
                "example.Test": make_type(annotations=local_annotation_markers),
                "example.BaseTest": make_type(annotations=["@Test"]),
                "example.ChildTest": make_type(extends_list=["example.BaseTest"]),
            }
        )

    without_inherited = CommonAnalysis(
        build_analysis([])
    ).resolve_effective_class_annotations("example.ChildTest")
    with_inherited = CommonAnalysis(
        build_analysis(["@Inherited"])
    ).resolve_effective_class_annotations("example.ChildTest")

    assert without_inherited == []
    assert with_inherited == [
        ResolvedAnnotation(annotation="@Test", declaring_class_name="example.BaseTest")
    ]


def test_subclass_annotation_shadows_inherited_base_annotation() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.BaseNgTest": make_type(annotations=['@Test(groups = {"base"})']),
            "example.ChildNgTest": make_type(
                annotations=['@Test(groups = {"child"})'],
                extends_list=["example.BaseNgTest"],
            ),
        },
        java_files={
            "example.BaseNgTest": "src/test/java/example/BaseNgTest.java",
            "example.ChildNgTest": "src/test/java/example/ChildNgTest.java",
        },
        import_declarations_by_file={
            "src/test/java/example/BaseNgTest.java": ["org.testng.annotations.Test"],
            "src/test/java/example/ChildNgTest.java": ["org.testng.annotations.Test"],
        },
    )

    resolved = CommonAnalysis(analysis).resolve_effective_class_annotations(
        "example.ChildNgTest"
    )

    assert resolved == [
        ResolvedAnnotation(
            annotation='@Test(groups = {"child"})',
            declaring_class_name="example.ChildNgTest",
        )
    ]


def test_nested_class_inherits_outer_spring_boot_test_annotation() -> None:
    nested_test_file = "src/test/java/example/OuterTest.java"
    analysis = FakeJavaAnalysis(
        classes={
            "example.OuterTest": make_type(annotations=["@SpringBootTest"]),
            "example.OuterTest.InnerTest": make_type(
                parent_type="example.OuterTest",
                annotations=["@Nested"],
            ),
        },
        java_files={
            "example.OuterTest": nested_test_file,
            "example.OuterTest.InnerTest": nested_test_file,
        },
        import_declarations_by_file={
            nested_test_file: [
                "org.springframework.boot.test.context.SpringBootTest",
                "org.junit.jupiter.api.Nested",
            ],
        },
    )

    resolved = CommonAnalysis(analysis).resolve_effective_class_annotations(
        "example.OuterTest.InnerTest"
    )

    assert resolved == [
        ResolvedAnnotation(
            annotation="@Nested",
            declaring_class_name="example.OuterTest.InnerTest",
        ),
        ResolvedAnnotation(
            annotation="@SpringBootTest",
            declaring_class_name="example.OuterTest",
        ),
    ]


def test_nested_class_annotation_shadows_outer_annotation() -> None:
    nested_test_file = "src/test/java/example/OuterTest.java"
    analysis = FakeJavaAnalysis(
        classes={
            "example.OuterTest": make_type(annotations=['@Test(groups = {"outer"})']),
            "example.OuterTest.InnerTest": make_type(
                parent_type="example.OuterTest",
                annotations=["@Nested", '@Test(groups = {"inner"})'],
            ),
        },
        java_files={
            "example.OuterTest": nested_test_file,
            "example.OuterTest.InnerTest": nested_test_file,
        },
        import_declarations_by_file={
            nested_test_file: [
                "org.testng.annotations.Test",
                "org.junit.jupiter.api.Nested",
                "org.junit.jupiter.api.Test",
            ],
        },
    )

    resolved = CommonAnalysis(analysis).resolve_effective_class_annotations(
        "example.OuterTest.InnerTest"
    )

    assert resolved == [
        ResolvedAnnotation(
            annotation="@Nested",
            declaring_class_name="example.OuterTest.InnerTest",
        ),
        ResolvedAnnotation(
            annotation='@Test(groups = {"inner"})',
            declaring_class_name="example.OuterTest.InnerTest",
        ),
    ]


def test_nested_annotations_do_not_leak_to_outer_class() -> None:
    nested_test_file = "src/test/java/example/OuterTest.java"
    analysis = FakeJavaAnalysis(
        classes={
            "example.OuterTest": make_type(),
            "example.OuterTest.InnerTest": make_type(
                parent_type="example.OuterTest",
                annotations=["@Nested", "@SpringBootTest"],
            ),
        },
        java_files={
            "example.OuterTest": nested_test_file,
            "example.OuterTest.InnerTest": nested_test_file,
        },
        import_declarations_by_file={
            nested_test_file: [
                "org.springframework.boot.test.context.SpringBootTest",
                "org.junit.jupiter.api.Nested",
            ],
        },
    )

    resolved = CommonAnalysis(analysis).resolve_effective_class_annotations(
        "example.OuterTest"
    )

    assert resolved == []


def test_static_inner_class_without_nested_inherits_no_annotations() -> None:
    nested_test_file = "src/test/java/example/OuterTest.java"
    analysis = FakeJavaAnalysis(
        classes={
            "example.OuterTest": make_type(annotations=["@SpringBootTest"]),
            "example.OuterTest.Helper": make_type(
                parent_type="example.OuterTest",
            ),
        },
        java_files={
            "example.OuterTest": nested_test_file,
            "example.OuterTest.Helper": nested_test_file,
        },
        import_declarations_by_file={
            nested_test_file: [
                "org.springframework.boot.test.context.SpringBootTest",
            ],
        },
    )

    resolved = CommonAnalysis(analysis).resolve_effective_class_annotations(
        "example.OuterTest.Helper"
    )

    assert resolved == []


def test_nested_class_inherits_non_inherited_outer_auto_configure_mock_mvc() -> None:
    nested_test_file = "src/test/java/example/OuterTest.java"
    analysis = FakeJavaAnalysis(
        classes={
            "example.OuterTest": make_type(
                annotations=["@AutoConfigureMockMvc"],
            ),
            "example.OuterTest.InnerTest": make_type(
                parent_type="example.OuterTest",
                annotations=["@Nested"],
            ),
        },
        java_files={
            "example.OuterTest": nested_test_file,
            "example.OuterTest.InnerTest": nested_test_file,
        },
        import_declarations_by_file={
            nested_test_file: [
                "org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc",
                "org.junit.jupiter.api.Nested",
            ],
        },
    )

    resolved = CommonAnalysis(analysis).resolve_effective_class_annotations(
        "example.OuterTest.InnerTest"
    )

    assert resolved == [
        ResolvedAnnotation(
            annotation="@Nested",
            declaring_class_name="example.OuterTest.InnerTest",
        ),
        ResolvedAnnotation(
            annotation="@AutoConfigureMockMvc",
            declaring_class_name="example.OuterTest",
        ),
    ]


def test_enclosing_class_superclass_annotations_keep_inherited_filter() -> None:
    nested_test_file = "src/test/java/example/OuterTest.java"
    base_test_file = "src/test/java/example/BaseTest.java"
    analysis = FakeJavaAnalysis(
        classes={
            "example.BaseTest": make_type(annotations=["@AutoConfigureMockMvc"]),
            "example.OuterTest": make_type(
                annotations=["@SpringBootTest"],
                extends_list=["example.BaseTest"],
            ),
            "example.OuterTest.InnerTest": make_type(
                parent_type="example.OuterTest",
                annotations=["@Nested"],
            ),
        },
        java_files={
            "example.BaseTest": base_test_file,
            "example.OuterTest": nested_test_file,
            "example.OuterTest.InnerTest": nested_test_file,
        },
        import_declarations_by_file={
            base_test_file: [
                "org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc",
            ],
            nested_test_file: [
                "org.springframework.boot.test.context.SpringBootTest",
                "org.junit.jupiter.api.Nested",
            ],
        },
    )

    resolved = CommonAnalysis(analysis).resolve_effective_class_annotations(
        "example.OuterTest.InnerTest"
    )

    assert resolved == [
        ResolvedAnnotation(
            annotation="@Nested",
            declaring_class_name="example.OuterTest.InnerTest",
        ),
        ResolvedAnnotation(
            annotation="@SpringBootTest",
            declaring_class_name="example.OuterTest",
        ),
    ]
