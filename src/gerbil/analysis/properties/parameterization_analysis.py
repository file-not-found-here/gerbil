from __future__ import annotations

import re

from cldk.models.java import JImport

from gerbil.analysis.shared.annotations import (
    annotation_body,
    annotation_matches_expected,
    annotation_short_name,
)
from gerbil.analysis.schema import ParameterizationSummary

_PARAMETERIZED_TEST_ANNOTATION: str = "@ParameterizedTest"
_TESTNG_TEST_ROOTS: dict[str, set[str]] = {"@Test": {"org.testng.annotations"}}
_TESTNG_DATA_PROVIDER_RE: str = r"\bdataProvider(?:Class)?\s*="

_STATIC_SOURCE_ANNOTATIONS: set[str] = {
    "@ValueSource",
    "@CsvSource",
    "@EnumSource",
    "@NullSource",
    "@EmptySource",
    "@NullAndEmptySource",
}

_DYNAMIC_SOURCE_ANNOTATIONS: set[str] = {
    "@MethodSource",
    "@FieldSource",
    "@CsvFileSource",
    "@ArgumentsSource",
    "@ArgumentsSources",
}


def _has_testng_data_provider(annotation: str) -> bool:
    body = annotation_body(annotation)
    return bool(body and re.search(_TESTNG_DATA_PROVIDER_RE, body))


def extract_parameterization_analysis(
    method_annotations: list[str],
    class_imports: list[JImport],
) -> ParameterizationSummary | None:
    """Classify parameterized test sources from method annotations.

    Returns None when the method is not parameterized. Otherwise returns
    a summary with static/dynamic source counts and the source annotation
    names that were detected.
    """
    if any(
        annotation_matches_expected(
            annotation=annotation,
            expected_annotation=_PARAMETERIZED_TEST_ANNOTATION,
            class_imports=class_imports,
        )
        for annotation in method_annotations
    ):
        static_annotations: list[str] = []
        dynamic_annotations: list[str] = []

        for annotation in method_annotations:
            for expected in _STATIC_SOURCE_ANNOTATIONS:
                if annotation_matches_expected(
                    annotation=annotation,
                    expected_annotation=expected,
                    class_imports=class_imports,
                ):
                    static_annotations.append(annotation_short_name(annotation))
                    break
            else:
                for expected in _DYNAMIC_SOURCE_ANNOTATIONS:
                    if annotation_matches_expected(
                        annotation=annotation,
                        expected_annotation=expected,
                        class_imports=class_imports,
                    ):
                        dynamic_annotations.append(annotation_short_name(annotation))
                        break

        signals: dict[str, list[str]] = {}
        if static_annotations:
            signals["static"] = sorted(static_annotations)
        if dynamic_annotations:
            signals["dynamic"] = sorted(dynamic_annotations)

        return ParameterizationSummary(signals=signals)

    # TestNG @Test(dataProvider=...) delegates to a provider method.
    for annotation in method_annotations:
        if annotation_matches_expected(
            annotation=annotation,
            expected_annotation="@Test",
            class_imports=class_imports,
            import_roots_by_annotation=_TESTNG_TEST_ROOTS,
        ) and _has_testng_data_provider(annotation):
            return ParameterizationSummary(signals={"dynamic": ["@DataProvider"]})

    return None
