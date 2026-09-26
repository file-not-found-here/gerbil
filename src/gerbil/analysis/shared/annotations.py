from __future__ import annotations

import re
from typing import Final

from cldk.models.java import JImport

from gerbil.analysis.shared.imports import (
    has_conflicting_explicit_import,
    has_import_root_signal,
    matches_import_root as _matches_import_root,
)

# ---------------------------------------------------------------------------
# annotation_utils
# ---------------------------------------------------------------------------

_ANNOTATION_TOKEN_RE: re.Pattern[str] = re.compile(r"@[A-Za-z_$][A-Za-z0-9_$.]*")


def annotation_token(annotation: str) -> str:
    """Return the canonical annotation token, preserving qualification.

    Examples:
        `@Test(timeout = 10)` -> `@Test`
        `@org.junit.jupiter.api.Test` -> `@org.junit.jupiter.api.Test`
    """

    annotation_source = (annotation or "").strip()
    if not annotation_source:
        return ""

    token_match = _ANNOTATION_TOKEN_RE.search(annotation_source)
    if token_match:
        return token_match.group(0)

    return annotation_source.split("(", 1)[0].strip()


def annotation_short_name_from_token(annotation_token_value: str) -> str:
    """Return a simple annotation name (without package qualification)."""

    normalized_token = (annotation_token_value or "").strip()
    if not normalized_token:
        return ""
    if not normalized_token.startswith("@"):
        return normalized_token

    qualified_name = normalized_token.removeprefix("@").strip()
    if not qualified_name:
        return ""
    return f"@{qualified_name.rsplit('.', 1)[-1]}"


def annotation_short_name(annotation: str) -> str:
    """Return a simplified annotation name suitable for heuristic matching."""

    return annotation_short_name_from_token(annotation_token(annotation))


def annotation_body(annotation: str) -> str:
    """Return the argument body between parentheses for an annotation."""

    annotation_source = (annotation or "").strip()
    if not annotation_source:
        return ""

    token_match = _ANNOTATION_TOKEN_RE.search(annotation_source)
    if token_match:
        annotation_source = annotation_source[token_match.start() :]

    if "(" not in annotation_source or ")" not in annotation_source:
        return ""
    return annotation_source.split("(", 1)[1].rsplit(")", 1)[0].strip()


# ---------------------------------------------------------------------------
# annotation_matching
# ---------------------------------------------------------------------------

# Mapping from short annotation names to known import/package roots.
KNOWN_ANNOTATION_IMPORT_ROOTS: Final[dict[str, set[str]]] = {
    "@After": {"org.junit"},
    "@AfterAll": {"org.junit.jupiter.api"},
    "@AfterClass": {"org.junit", "org.testng.annotations"},
    "@AfterEach": {"org.junit.jupiter.api"},
    "@AfterGroups": {"org.testng.annotations"},
    "@AfterMethod": {"org.testng.annotations"},
    "@AfterSuite": {"org.testng.annotations"},
    "@AfterTest": {"org.testng.annotations"},
    "@ArgumentsSource": {"org.junit.jupiter.params.provider"},
    "@ArgumentsSources": {"org.junit.jupiter.params.provider"},
    "@AutoConfigureMockMvc": {
        "org.springframework.boot.test.autoconfigure.web.servlet"
    },
    "@AutoConfigureWireMock": {"org.springframework.cloud.contract.wiremock"},
    "@Before": {"org.junit"},
    "@BeforeAll": {"org.junit.jupiter.api"},
    "@BeforeClass": {"org.junit", "org.testng.annotations"},
    "@BeforeEach": {"org.junit.jupiter.api"},
    "@BeforeGroups": {"org.testng.annotations"},
    "@BeforeMethod": {"org.testng.annotations"},
    "@BeforeSuite": {"org.testng.annotations"},
    "@BeforeTest": {"org.testng.annotations"},
    "@MockitoBean": {"org.springframework.test.context.bean.override.mockito"},
    "@MockitoSpyBean": {"org.springframework.test.context.bean.override.mockito"},
    "@Container": {"org.testcontainers.junit.jupiter"},
    "@CsvFileSource": {"org.junit.jupiter.params.provider"},
    "@CsvSource": {"org.junit.jupiter.params.provider"},
    "@DeleteMapping": {"org.springframework.web.bind.annotation"},
    "@DockerComposeTest": {
        "org.springframework.boot.testcontainers",
        "org.springframework.boot.testcontainers.service.connection",
    },
    "@EmptySource": {"org.junit.jupiter.params.provider"},
    "@EnumSource": {"org.junit.jupiter.params.provider"},
    "@FieldSource": {"org.junit.jupiter.params.provider"},
    "@GetMapping": {"org.springframework.web.bind.annotation"},
    "@JerseyTest": {"org.glassfish.jersey.test"},
    "@MethodSource": {"org.junit.jupiter.params.provider"},
    "@NullAndEmptySource": {"org.junit.jupiter.params.provider"},
    "@NullSource": {"org.junit.jupiter.params.provider"},
    "@MicronautTest": {
        "io.micronaut.test.annotation",
        "io.micronaut.test.extensions.junit5.annotation",
    },
    "@Mock": {"org.easymock", "mockit", "org.mockito"},
    "@MockBean": {"org.springframework.boot.test.mock.mockito"},
    "@ParameterizedTest": {"org.junit.jupiter.params"},
    "@PatchMapping": {"org.springframework.web.bind.annotation"},
    "@Property": {"net.jqwik.api", "com.pholser.junit.quickcheck"},
    "@PermitAll": {"jakarta.annotation.security", "javax.annotation.security"},
    "@PostMapping": {"org.springframework.web.bind.annotation"},
    "@PutMapping": {"org.springframework.web.bind.annotation"},
    "@QuarkusIntegrationTest": {"io.quarkus.test.junit"},
    "@RepeatedTest": {"org.junit.jupiter.api"},
    "@RequestMapping": {"org.springframework.web.bind.annotation"},
    "@SpringBootTest": {"org.springframework.boot.test.context"},
    "@Spy": {"org.mockito"},
    "@SpyBean": {"org.springframework.boot.test.mock.mockito"},
    "@Sql": {"org.springframework.test.context.jdbc"},
    "@SqlConfig": {"org.springframework.test.context.jdbc"},
    "@SqlGroup": {"org.springframework.test.context.jdbc"},
    "@Test": {"org.junit", "org.junit.jupiter.api", "org.testng.annotations"},
    "@TestFactory": {"org.junit.jupiter.api"},
    "@Testcontainers": {"org.testcontainers.junit.jupiter"},
    "@ValueSource": {"org.junit.jupiter.params.provider"},
    "@WebFluxTest": {"org.springframework.boot.test.autoconfigure.web.reactive"},
    "@WebMvcTest": {"org.springframework.boot.test.autoconfigure.web.servlet"},
    "@WithAnonymousUser": {"org.springframework.security.test.context.support"},
    "@WithMockUser": {"org.springframework.security.test.context.support"},
    "@WithUserDetails": {"org.springframework.security.test.context.support"},
    "@WireMockTest": {
        "com.github.tomakehurst.wiremock.junit5",
        "org.springframework.cloud.contract.wiremock",
    },
}


# Framework annotations applied to subclasses at runtime: each is declared
# @Inherited in its framework source, except TestNG's @Test, whose annotation
# finder walks the superclass chain (JDK15AnnotationFinder).
RUNTIME_INHERITED_ANNOTATION_IMPORT_ROOTS: Final[dict[str, set[str]]] = {
    "@SpringBootTest": {"org.springframework.boot.test.context"},
    "@Sql": {"org.springframework.test.context.jdbc"},
    "@SqlConfig": {"org.springframework.test.context.jdbc"},
    "@SqlGroup": {"org.springframework.test.context.jdbc"},
    "@Test": {"org.testng.annotations"},
    "@Testcontainers": {"org.testcontainers.junit.jupiter"},
    "@WebFluxTest": {"org.springframework.boot.test.autoconfigure.web.reactive"},
    "@WebMvcTest": {"org.springframework.boot.test.autoconfigure.web.servlet"},
    "@WireMockTest": {"com.github.tomakehurst.wiremock.junit5"},
    "@WithAnonymousUser": {"org.springframework.security.test.context.support"},
    "@WithMockUser": {"org.springframework.security.test.context.support"},
    "@WithUserDetails": {"org.springframework.security.test.context.support"},
}


def matches_import_root(class_import: JImport, import_root: str) -> bool:
    """Check whether an import entry is within an expected package root.

    Args:
        class_import: Structured import declaration.
        import_root: Expected root package name.

    Returns:
        True when the import entry belongs to the expected root package.
    """

    return _matches_import_root(class_import, import_root)


def annotation_matches_expected(
    annotation: str,
    expected_annotation: str,
    *,
    class_imports: list[JImport],
    import_roots_by_annotation: dict[str, set[str]] | None = None,
) -> bool:
    """Check whether an annotation matches an expected semantic annotation name.

    Matching uses short-name normalization with package-aware validation:
    - Fully qualified annotations must resolve to known package roots.
    - Unqualified annotations require matching import roots in class imports.
    - If imports are unavailable for known annotations, matching fails closed.
    - Unknown annotations (without configured roots) match by short name only.

    Args:
        annotation: Raw annotation literal from source.
        expected_annotation: Expected short or qualified annotation name.
        class_imports: Imports visible in the declaring class context.
        import_roots_by_annotation: Optional annotation->allowed-root mapping.

    Returns:
        True when the annotation semantically matches the expected annotation.
    """

    annotation_name_token = annotation_token(annotation)
    if not annotation_name_token:
        return False

    expected_short_name = annotation_short_name_from_token(expected_annotation)
    if not expected_short_name:
        return False

    annotation_short = annotation_short_name_from_token(annotation_name_token)
    if annotation_short != expected_short_name:
        return False

    resolved_roots_by_annotation = (
        import_roots_by_annotation or KNOWN_ANNOTATION_IMPORT_ROOTS
    )
    allowed_roots = resolved_roots_by_annotation.get(expected_short_name)
    if not allowed_roots:
        return True

    qualified_name = annotation_name_token.removeprefix("@").strip()
    if "." in qualified_name:
        short_name = expected_short_name.removeprefix("@")
        package_name = qualified_name.rsplit(".", 1)[0]
        if qualified_name != f"{package_name}.{short_name}":
            return False
        return any(
            package_name == import_root.rstrip(".")
            or package_name.startswith(f"{import_root.rstrip('.')}.")
            for import_root in allowed_roots
        )

    # An explicit single-type import from a foreign package shadows wildcard
    # imports in Java, so it vetoes any allowed-root signal.
    short_name = expected_short_name.removeprefix("@")
    if has_conflicting_explicit_import(class_imports, short_name, allowed_roots):
        return False

    return has_import_root_signal(class_imports, allowed_roots)


__all__ = [
    "KNOWN_ANNOTATION_IMPORT_ROOTS",
    "RUNTIME_INHERITED_ANNOTATION_IMPORT_ROOTS",
    "annotation_body",
    "annotation_matches_expected",
    "annotation_short_name",
    "annotation_short_name_from_token",
    "annotation_token",
    "matches_import_root",
]
