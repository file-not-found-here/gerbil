from __future__ import annotations

import json
from pathlib import Path

from gerbil.execution.selection import (
    ApiTest,
    TestLocation,
    TestSourceIndex,
    load_api_tests,
)


def _method(signature: str, *, api: bool, labels: list[str]) -> dict[str, object]:
    return {
        "identity": {"method_signature": signature, "annotations": ["@Test"]},
        "is_api_test": api,
        "http": {"request_dispatch": {"labels": labels, "signals": {}}},
    }


def _write_analysis(path: Path, classes: dict[str, list[dict[str, object]]]) -> Path:
    path.write_text(
        json.dumps(
            {
                "dataset_name": "demo",
                "metadata": {"git_commit_hash": "abc123"},
                "test_class_analyses": [
                    {"qualified_class_name": name, "test_method_analyses": methods}
                    for name, methods in classes.items()
                ],
            }
        )
    )
    return path


def test_load_api_tests_keeps_api_tests_with_their_dispatch(tmp_path: Path) -> None:
    analysis = _write_analysis(
        tmp_path / "gerbil.json",
        {
            "org.demo.UserApiTest": [
                _method("createsUser()", api=True, labels=["in-process"]),
                _method("helperUnitTest()", api=False, labels=[]),
            ],
            "org.demo.RemoteTest": [
                _method("callsRemote(String)", api=True, labels=["remote-network"]),
            ],
        },
    )

    suite = load_api_tests(analysis)

    assert suite.commit == "abc123"
    assert suite.api_tests == (
        ApiTest("org.demo.UserApiTest", "createsUser()", ("in-process",)),
        ApiTest("org.demo.RemoteTest", "callsRemote(String)", ("remote-network",)),
    )


def test_in_scope_requires_every_request_in_process_or_local() -> None:
    def scope(*labels: str) -> bool:
        return ApiTest("C", "m()", labels).in_scope

    assert scope("in-process")
    assert scope("local-network")
    assert scope("in-process", "local-network")
    assert not scope("in-process", "remote-network")
    assert not scope("local-network", "remote-network")
    assert not scope("unknown")
    assert not scope()


def test_method_name_drops_parameter_list() -> None:
    test = ApiTest("C", "findsUser(org.demo.UserRepository, List)", ("in-process",))

    assert test.method_name == "findsUser"


def _touch(path: Path, body: str = "class X {}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_source_index_maps_classes_to_modules(tmp_path: Path) -> None:
    _touch(tmp_path / "src/test/java/org/demo/RootTest.java")
    _touch(tmp_path / "server/api/src/test/java/org/demo/api/UserApiTest.java")
    _touch(tmp_path / "server/api/src/main/java/org/demo/api/UserController.java")

    index = TestSourceIndex.build(tmp_path)

    assert index.locate("org.demo.RootTest", "m") == [
        TestLocation(".", "org.demo.RootTest")
    ]
    assert index.locate("org.demo.api.UserApiTest", "m") == [
        TestLocation("server/api", "org.demo.api.UserApiTest")
    ]
    assert index.locate("org.demo.api.UserController", "m") == []


def test_source_index_resolves_nested_classes_to_binary_names(tmp_path: Path) -> None:
    _touch(tmp_path / "api/src/test/java/org/demo/AuditTests.java")

    index = TestSourceIndex.build(tmp_path)

    assert index.locate("org.demo.AuditTests.AsAdmin.WithScope", "m") == [
        TestLocation("api", "org.demo.AuditTests$AsAdmin$WithScope")
    ]


def test_source_index_ignores_build_outputs(tmp_path: Path) -> None:
    _touch(tmp_path / "api/target/generated-test-sources/src/test/java/org/A.java")
    _touch(tmp_path / "web/build/tmp/src/test/java/org/B.java")

    index = TestSourceIndex.build(tmp_path)

    assert index.locate("org.A", "m") == []
    assert index.locate("org.B", "m") == []


def test_source_index_picks_the_module_whose_source_declares_the_method(
    tmp_path: Path,
) -> None:
    _touch(
        tmp_path / "server-it/src/test/java/org/demo/SharedTest.java",
        "class SharedTest { @Test void listsUsers() {} }",
    )
    _touch(
        tmp_path / "metadata/src/test/java/org/demo/SharedTest.java",
        "class SharedTest { @Test void savesMetadata() {} }",
    )

    index = TestSourceIndex.build(tmp_path)

    assert index.locate("org.demo.SharedTest", "listsUsers") == [
        TestLocation("server-it", "org.demo.SharedTest")
    ]
    assert sorted(
        location.module for location in index.locate("org.demo.SharedTest", "inherited")
    ) == ["metadata", "server-it"]


def test_source_index_finds_java_tests_under_other_test_languages(
    tmp_path: Path,
) -> None:
    _touch(tmp_path / "web/src/test/groovy/org/demo/MixedTest.java")
    _touch(tmp_path / "web/src/test/resources/org/demo/FixtureTest.java")
    _touch(tmp_path / "web/src/integration/java/org/demo/LiveTest.java")

    index = TestSourceIndex.build(tmp_path)

    assert index.locate("org.demo.MixedTest", "m") == [
        TestLocation("web", "org.demo.MixedTest")
    ]
    assert index.locate("org.demo.FixtureTest", "m") == []
    assert index.locate("org.demo.LiveTest", "m") == []
