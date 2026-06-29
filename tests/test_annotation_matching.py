from __future__ import annotations

import pytest
from cldk.models.java import JImport

from gerbil.analysis.shared.annotations import (
    annotation_matches_expected,
    matches_import_root,
)
from tests.cldk_factories import make_import_declaration, make_import_declarations


@pytest.mark.parametrize(
    ("class_import", "import_root", "expected"),
    [
        (
            JImport(
                path="org.junit.jupiter.api.Test", is_static=False, is_wildcard=False
            ),
            "org.junit.jupiter.api",
            True,
        ),
        (
            JImport(
                path="org.junit.jupiter.api.Test", is_static=False, is_wildcard=False
            ),
            "org.junit",
            True,
        ),
        (
            JImport(
                path="org.junit.jupiterx.api.Test", is_static=False, is_wildcard=False
            ),
            "org.junit.jupiter.api",
            False,
        ),
        (
            JImport(
                path="org.springframework.test.web.reactive.server.WebTestClient",
                is_static=False,
                is_wildcard=False,
            ),
            "org.springframework.test.web.reactive.server",
            True,
        ),
        (
            JImport(
                path="org.springframework.test.web.reactive.serverless.Client",
                is_static=False,
                is_wildcard=False,
            ),
            "org.springframework.test.web.reactive.server",
            False,
        ),
        (
            JImport(path="org.junit.jupiter.api", is_static=False, is_wildcard=True),
            "org.junit.jupiter.api",
            True,
        ),
        (
            JImport(
                path="org.springframework.web.bind.annotation",
                is_static=False,
                is_wildcard=True,
            ),
            "org.springframework.web.bind.annotation",
            True,
        ),
        (
            JImport(
                path="org.springframework.web.bind.annotationx",
                is_static=False,
                is_wildcard=True,
            ),
            "org.springframework.web.bind.annotation",
            False,
        ),
    ],
)
def test_matches_import_root_is_boundary_safe(
    class_import: JImport,
    import_root: str,
    expected: bool,
) -> None:
    assert (
        matches_import_root(class_import=class_import, import_root=import_root)
        is expected
    )


def test_annotation_matches_expected_accepts_qualified_known_annotation() -> None:
    assert annotation_matches_expected(
        annotation="@org.junit.jupiter.api.Test",
        expected_annotation="@Test",
        class_imports=[],
    )


def test_annotation_matches_expected_rejects_qualified_unknown_package() -> None:
    assert not annotation_matches_expected(
        annotation="@com.example.Test",
        expected_annotation="@Test",
        class_imports=[],
    )


def test_annotation_matches_expected_requires_import_for_ambiguous_short_name() -> None:
    assert not annotation_matches_expected(
        annotation="@Test",
        expected_annotation="@Test",
        class_imports=make_import_declarations("org.assertj.core.api.Assertions"),
    )


def test_annotation_matches_expected_accepts_import_for_ambiguous_short_name() -> None:
    assert annotation_matches_expected(
        annotation="@Test",
        expected_annotation="@Test",
        class_imports=make_import_declarations("org.junit.jupiter.api.Test"),
    )


@pytest.mark.parametrize(
    ("annotation", "expected_annotation", "import_path"),
    [
        ("@Test", "@Test", "org.junit.jupiter.api.*"),
        ("@Test", "@Test", "org.testng.annotations.*"),
        (
            "@RequestMapping",
            "@RequestMapping",
            "org.springframework.web.bind.annotation.*",
        ),
        (
            "@GetMapping",
            "@GetMapping",
            "org.springframework.web.bind.annotation.*",
        ),
    ],
)
def test_annotation_matches_expected_accepts_wildcard_import_for_short_name(
    annotation: str,
    expected_annotation: str,
    import_path: str,
) -> None:
    assert annotation_matches_expected(
        annotation=annotation,
        expected_annotation=expected_annotation,
        class_imports=make_import_declarations(import_path),
    )


def test_annotation_matches_expected_rejects_conflicting_explicit_short_name_import() -> (
    None
):
    assert not annotation_matches_expected(
        annotation="@SpringBootTest",
        expected_annotation="@SpringBootTest",
        class_imports=make_import_declarations("com.example.SpringBootTest"),
    )


def test_annotation_matches_expected_conflicting_explicit_import_vetoes_root_signal() -> (
    None
):
    # An explicit single-type import shadows wildcard imports in Java, so a
    # foreign `Test` import wins even when an allowed root is also imported.
    assert not annotation_matches_expected(
        annotation="@Test",
        expected_annotation="@Test",
        class_imports=make_import_declarations(
            "com.example.Test",
            "org.junit.jupiter.api.Assertions",
        ),
    )


def test_annotation_matches_expected_accepts_explicit_short_name_import_from_allowed_root() -> (
    None
):
    assert annotation_matches_expected(
        annotation="@Test",
        expected_annotation="@Test",
        class_imports=make_import_declarations(
            "org.junit.jupiter.api.Test",
            "org.junit.jupiter.api.Assertions",
        ),
    )


def test_annotation_matches_expected_rejects_short_name_when_import_context_is_incomplete() -> (
    None
):
    assert not annotation_matches_expected(
        annotation="@WebFluxTest",
        expected_annotation="@WebFluxTest",
        class_imports=make_import_declarations("org.junit.jupiter.api.Test"),
    )


def test_annotation_matches_expected_rejects_unrelated_wildcard_import_context() -> (
    None
):
    assert not annotation_matches_expected(
        annotation="@Test",
        expected_annotation="@Test",
        class_imports=make_import_declarations("com.example.*"),
    )


def test_annotation_matches_expected_rejects_short_name_with_only_static_imports() -> (
    None
):
    assert not annotation_matches_expected(
        annotation="@RequestMapping",
        expected_annotation="@RequestMapping",
        class_imports=[
            make_import_declaration(
                "org.mockito.Mockito",
                is_static=True,
                is_wildcard=True,
            )
        ],
    )
