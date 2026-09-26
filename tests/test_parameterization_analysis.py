from __future__ import annotations

from gerbil.analysis.properties import extract_parameterization_analysis
from tests.cldk_factories import make_import_declarations

_JUNIT_PARAMETERIZATION_IMPORTS = make_import_declarations(
    "org.junit.jupiter.params.ParameterizedTest",
    "org.junit.jupiter.params.provider.ValueSource",
    "org.junit.jupiter.params.provider.CsvSource",
    "org.junit.jupiter.params.provider.EnumSource",
    "org.junit.jupiter.params.provider.MethodSource",
    "org.junit.jupiter.params.provider.CsvFileSource",
    "org.junit.jupiter.params.provider.ArgumentsSource",
    "org.junit.jupiter.params.provider.NullSource",
    "org.junit.jupiter.params.provider.EmptySource",
    "org.junit.jupiter.params.provider.NullAndEmptySource",
    "org.junit.jupiter.params.provider.FieldSource",
)


def test_non_parameterized_method_returns_none() -> None:
    result = extract_parameterization_analysis(["@Test"], class_imports=[])

    assert result is None


def test_parameterized_value_source() -> None:
    result = extract_parameterization_analysis(
        ["@ParameterizedTest", '@ValueSource(strings = {"alpha", "beta"})'],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"static": ["@ValueSource"]}


def test_parameterized_csv_source() -> None:
    result = extract_parameterization_analysis(
        ["@ParameterizedTest", '@CsvSource({"a,b", "c,d", "e,f"})'],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"static": ["@CsvSource"]}


def test_parameterized_enum_source() -> None:
    result = extract_parameterization_analysis(
        ["@ParameterizedTest", "@EnumSource(MyEnum.class)"],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"static": ["@EnumSource"]}


def test_parameterized_method_source() -> None:
    result = extract_parameterization_analysis(
        ["@ParameterizedTest", '@MethodSource("userProvider")'],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"dynamic": ["@MethodSource"]}


def test_parameterized_null_and_empty_source() -> None:
    result = extract_parameterization_analysis(
        ["@ParameterizedTest", "@NullAndEmptySource"],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"static": ["@NullAndEmptySource"]}


def test_parameterized_null_source_and_empty_source_alongside_values() -> None:
    result = extract_parameterization_analysis(
        [
            "@ParameterizedTest",
            "@NullSource",
            "@EmptySource",
            '@ValueSource(strings = {"alpha"})',
        ],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"static": ["@EmptySource", "@NullSource", "@ValueSource"]}


def test_parameterized_field_source() -> None:
    result = extract_parameterization_analysis(
        ["@ParameterizedTest", '@FieldSource("userArguments")'],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"dynamic": ["@FieldSource"]}


def test_parameterized_csv_file_source() -> None:
    result = extract_parameterization_analysis(
        ["@ParameterizedTest", '@CsvFileSource(resources = "/data.csv")'],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"dynamic": ["@CsvFileSource"]}


def test_parameterized_arguments_source() -> None:
    result = extract_parameterization_analysis(
        ["@ParameterizedTest", "@ArgumentsSource(MyProvider.class)"],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"dynamic": ["@ArgumentsSource"]}


def test_mixed_static_and_dynamic_sources() -> None:
    result = extract_parameterization_analysis(
        [
            "@ParameterizedTest",
            "@ValueSource(ints = {1, 2, 3})",
            '@MethodSource("dynamicProvider")',
        ],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {
        "static": ["@ValueSource"],
        "dynamic": ["@MethodSource"],
    }


def test_multiple_static_sources() -> None:
    result = extract_parameterization_analysis(
        [
            "@ParameterizedTest",
            '@ValueSource(strings = {"a", "b"})',
            '@CsvSource({"x,1", "y,2"})',
        ],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"static": ["@CsvSource", "@ValueSource"]}


def test_parameterized_with_no_recognized_source() -> None:
    result = extract_parameterization_analysis(
        ["@ParameterizedTest"],
        class_imports=_JUNIT_PARAMETERIZATION_IMPORTS,
    )

    assert result is not None
    assert result.signals == {}


def test_fully_qualified_annotations_are_supported() -> None:
    result = extract_parameterization_analysis(
        [
            "@org.junit.jupiter.params.ParameterizedTest",
            '@org.junit.jupiter.params.provider.ValueSource(strings = {"alpha", "beta"})',
        ],
        class_imports=[],
    )

    assert result is not None
    assert result.signals == {"static": ["@ValueSource"]}


_TESTNG_IMPORTS = make_import_declarations("org.testng.annotations.Test")


def test_testng_dataprovider_annotation_returns_dynamic_signal() -> None:
    result = extract_parameterization_analysis(
        ['@Test(dataProvider = "userProvider")'],
        class_imports=_TESTNG_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"dynamic": ["@DataProvider"]}


def test_testng_dataprovider_class_attribute_returns_dynamic_signal() -> None:
    result = extract_parameterization_analysis(
        ['@Test(dataProvider = "users", dataProviderClass = UserData.class)'],
        class_imports=_TESTNG_IMPORTS,
    )

    assert result is not None
    assert result.signals == {"dynamic": ["@DataProvider"]}


def test_testng_test_without_dataprovider_returns_none() -> None:
    result = extract_parameterization_analysis(
        ["@Test"],
        class_imports=_TESTNG_IMPORTS,
    )

    assert result is None


def test_testng_dataprovider_requires_testng_import_root() -> None:
    junit_imports = make_import_declarations("org.junit.Test")

    result = extract_parameterization_analysis(
        ['@Test(dataProvider = "userProvider")'],
        class_imports=junit_imports,
    )

    assert result is None
