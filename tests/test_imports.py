from __future__ import annotations

from cldk.models.java import JImport

from gerbil.analysis.shared import CommonAnalysis
from tests.cldk_factories import make_type
from tests.fake_java_analysis import FakeJavaAnalysis


class _CountingFakeJavaAnalysis(FakeJavaAnalysis):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.get_java_compilation_unit_calls: int = 0

    def get_java_compilation_unit(self, java_file: str):  # type: ignore[override]
        self.get_java_compilation_unit_calls += 1
        return super().get_java_compilation_unit(java_file)


def test_common_analysis_returns_deduplicated_structured_imports() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        java_files={"example.ApiTest": "src/test/java/example/ApiTest.java"},
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": [
                JImport(
                    path="org.junit.jupiter.api.Test",
                    is_static=False,
                    is_wildcard=False,
                ),
                JImport(
                    path="org.junit.jupiter.api.Test",
                    is_static=False,
                    is_wildcard=False,
                ),
                JImport(
                    path="org.junit.jupiter.api.Assertions.assertEquals",
                    is_static=True,
                    is_wildcard=False,
                ),
            ]
        },
    )
    common = CommonAnalysis(analysis)

    imports = common.get_class_imports("example.ApiTest")

    assert imports == [
        JImport(path="org.junit.jupiter.api.Test", is_static=False, is_wildcard=False),
        JImport(
            path="org.junit.jupiter.api.Assertions.assertEquals",
            is_static=True,
            is_wildcard=False,
        ),
    ]


def test_common_analysis_returns_defensive_copy_for_class_imports() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        java_files={"example.ApiTest": "src/test/java/example/ApiTest.java"},
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": [
                JImport(
                    path="org.junit.jupiter.api.Test",
                    is_static=False,
                    is_wildcard=False,
                )
            ]
        },
    )
    common = CommonAnalysis(analysis)

    first = common.get_class_imports("example.ApiTest")
    first.append(JImport(path="mutated.path", is_static=False, is_wildcard=False))
    second = common.get_class_imports("example.ApiTest")

    assert second == [
        JImport(path="org.junit.jupiter.api.Test", is_static=False, is_wildcard=False)
    ]


def test_common_analysis_returns_cloned_import_entries_from_cache() -> None:
    analysis = FakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        java_files={"example.ApiTest": "src/test/java/example/ApiTest.java"},
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": [
                JImport(
                    path="org.junit.jupiter.api.Test",
                    is_static=False,
                    is_wildcard=False,
                )
            ]
        },
    )
    common = CommonAnalysis(analysis)

    first = common.get_class_imports("example.ApiTest")
    first[0].path = "mutated.path"
    second = common.get_class_imports("example.ApiTest")

    assert second == [
        JImport(path="org.junit.jupiter.api.Test", is_static=False, is_wildcard=False)
    ]


def test_common_analysis_effective_imports_deduplicate_in_hierarchy_order() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.ChildTest": make_type(extends_list=["example.BaseTest"]),
            "example.BaseTest": make_type(),
        },
        java_files={
            "example.ChildTest": "src/test/java/example/ChildTest.java",
            "example.BaseTest": "src/test/java/example/BaseTest.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ChildTest.java": [
                JImport(
                    path="org.junit.jupiter.api.Test",
                    is_static=False,
                    is_wildcard=False,
                )
            ],
            "src/test/java/example/BaseTest.java": [
                JImport(
                    path="org.junit.jupiter.api.Test",
                    is_static=False,
                    is_wildcard=False,
                ),
                JImport(
                    path="org.springframework.test.context.junit.jupiter.SpringExtension",
                    is_static=False,
                    is_wildcard=False,
                ),
            ],
        },
    )
    common = CommonAnalysis(analysis)

    imports = common.get_effective_class_imports("example.ChildTest")

    assert imports == [
        JImport(path="org.junit.jupiter.api.Test", is_static=False, is_wildcard=False),
        JImport(
            path="org.springframework.test.context.junit.jupiter.SpringExtension",
            is_static=False,
            is_wildcard=False,
        ),
    ]


def test_common_analysis_returns_cloned_effective_import_entries_from_cache() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.ChildTest": make_type(extends_list=["example.BaseTest"]),
            "example.BaseTest": make_type(),
        },
        java_files={
            "example.ChildTest": "src/test/java/example/ChildTest.java",
            "example.BaseTest": "src/test/java/example/BaseTest.java",
        },
        import_declarations_by_file={
            "src/test/java/example/ChildTest.java": [
                JImport(
                    path="org.junit.jupiter.api.Test",
                    is_static=False,
                    is_wildcard=False,
                )
            ],
            "src/test/java/example/BaseTest.java": [
                JImport(
                    path="org.springframework.test.context.junit.jupiter.SpringExtension",
                    is_static=False,
                    is_wildcard=False,
                )
            ],
        },
    )
    common = CommonAnalysis(analysis)

    first = common.get_effective_class_imports("example.ChildTest")
    first[0].path = "mutated.path"
    second = common.get_effective_class_imports("example.ChildTest")

    assert second == [
        JImport(path="org.junit.jupiter.api.Test", is_static=False, is_wildcard=False),
        JImport(
            path="org.springframework.test.context.junit.jupiter.SpringExtension",
            is_static=False,
            is_wildcard=False,
        ),
    ]


def test_common_analysis_caches_static_import_index_per_owner_class() -> None:
    analysis = _CountingFakeJavaAnalysis(
        classes={"example.ApiTest": make_type()},
        java_files={"example.ApiTest": "src/test/java/example/ApiTest.java"},
        import_declarations_by_file={
            "src/test/java/example/ApiTest.java": [
                JImport(
                    path="org.mockito.Mockito.verify",
                    is_static=True,
                    is_wildcard=False,
                )
            ]
        },
    )
    common = CommonAnalysis(analysis)

    first = common.get_static_import_index("example.ApiTest")
    second = common.get_static_import_index("example.ApiTest")

    assert first is second
    assert first.resolve("verify") == "org.mockito.Mockito"
    assert analysis.get_java_compilation_unit_calls == 1


def test_common_analysis_returns_empty_imports_when_compilation_unit_missing() -> None:
    common = CommonAnalysis(FakeJavaAnalysis())

    assert common.get_class_imports("example.MissingTest") == []
