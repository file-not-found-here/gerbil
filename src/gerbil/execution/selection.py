"""Select a suite's in-scope API tests from gerbil.json and locate their test classes."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from gerbil.analysis.schema import RequestDispatch

IN_SCOPE_DISPATCH = frozenset(
    {RequestDispatch.IN_PROCESS.value, RequestDispatch.LOCAL_NETWORK.value}
)

_SKIPPED_DIRECTORIES = frozenset({".git", "build", "node_modules", "target"})
_TEST_SOURCE_ROOT = ("src", "test")
_TEST_SOURCE_LANGUAGES = frozenset({"java", "groovy", "kotlin"})


class _Identity(BaseModel):
    method_signature: str


class _RequestDispatch(BaseModel):
    labels: list[str]


class _Http(BaseModel):
    request_dispatch: _RequestDispatch


class _TestMethod(BaseModel):
    identity: _Identity
    is_api_test: bool
    http: _Http


class _TestClass(BaseModel):
    qualified_class_name: str
    test_method_analyses: list[_TestMethod]


class _Metadata(BaseModel):
    git_commit_hash: str


class _Analysis(BaseModel):
    metadata: _Metadata
    test_class_analyses: list[_TestClass]


def method_name(method_signature: str) -> str:
    return method_signature.split("(", 1)[0]


@dataclass(frozen=True)
class ApiTest:
    class_name: str
    method_signature: str
    dispatch: tuple[str, ...]

    @property
    def method_name(self) -> str:
        return method_name(self.method_signature)

    @property
    def in_scope(self) -> bool:
        """Every request the test makes is dispatched in-process or to localhost."""
        return bool(self.dispatch) and IN_SCOPE_DISPATCH.issuperset(self.dispatch)


@dataclass(frozen=True)
class SuiteAnalysis:
    commit: str
    api_tests: tuple[ApiTest, ...]


def load_api_tests(analysis_path: Path) -> SuiteAnalysis:
    analysis = _Analysis.model_validate_json(analysis_path.read_bytes())
    api_tests = tuple(
        ApiTest(
            class_name=test_class.qualified_class_name,
            method_signature=method.identity.method_signature,
            dispatch=tuple(method.http.request_dispatch.labels),
        )
        for test_class in analysis.test_class_analyses
        for method in test_class.test_method_analyses
        if method.is_api_test
    )
    return SuiteAnalysis(analysis.metadata.git_commit_hash, api_tests)


@dataclass(frozen=True)
class TestLocation:
    module: str
    binary_class_name: str


class TestSourceIndex:
    """Top-level Java classes under each module's test sources, keyed by qualified name."""

    def __init__(self, sources_by_class: dict[str, list[tuple[str, Path]]]) -> None:
        self._sources_by_class = sources_by_class

    @classmethod
    def build(cls, build_root: Path) -> TestSourceIndex:
        sources_by_class: dict[str, list[tuple[str, Path]]] = defaultdict(list)
        for directory, subdirectories, files in os.walk(build_root):
            subdirectories[:] = [
                name for name in subdirectories if name not in _SKIPPED_DIRECTORIES
            ]
            parts = Path(directory).relative_to(build_root).parts
            for start in range(len(parts) - len(_TEST_SOURCE_ROOT)):
                language = parts[start + len(_TEST_SOURCE_ROOT)]
                if (
                    parts[start : start + len(_TEST_SOURCE_ROOT)] != _TEST_SOURCE_ROOT
                    or language not in _TEST_SOURCE_LANGUAGES
                ):
                    continue
                module = "/".join(parts[:start]) or "."
                package = parts[start + len(_TEST_SOURCE_ROOT) + 1 :]
                for file_name in files:
                    if file_name.endswith(".java"):
                        class_name = ".".join((*package, file_name[: -len(".java")]))
                        sources_by_class[class_name].append(
                            (module, Path(directory) / file_name)
                        )
                break
        return cls(dict(sources_by_class))

    def locate(self, class_name: str, method: str) -> list[TestLocation]:
        """Locations of a dotted class name whose trailing segments may be nested classes.

        When modules share the class name, only those whose source declares `method`
        are kept (unless none does, e.g. for an inherited test method).
        """
        parts = class_name.split(".")
        for end in range(len(parts), 0, -1):
            sources = self._sources_by_class.get(".".join(parts[:end]))
            if not sources:
                continue
            if len(sources) > 1:
                declaration = re.compile(rf"\b{re.escape(method)}\s*\(")
                sources = [
                    source
                    for source in sources
                    if declaration.search(source[1].read_text(errors="replace"))
                ] or sources
            binary_name = ".".join(parts[:end]) + "".join(
                f"${nested}" for nested in parts[end:]
            )
            return [TestLocation(module, binary_name) for module, _ in sources]
        return []


# Prevent pytest from treating the "Test"-prefixed types as test classes.
setattr(TestLocation, "__test__", False)
setattr(TestSourceIndex, "__test__", False)
