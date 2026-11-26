from __future__ import annotations

from pathlib import Path

from gerbil.validation.java_source import (
    JavaFile,
    JavaRepo,
    TokenRange,
    parse_signature,
    simple_type_name,
)
from tests.validation_builders import WIDGET_TEST, span_ending, write_repo

_OVERLOADS = """\
package demo;

class Client {
    Client(String base) {
    }

    void send(String path) {
        new HttpGet(path);
    }

    void send(String path, Map<String, Object> body) {
        log("é", http.post(path));
    }

    void send(int id, String... tags) {
    }
}
"""


def _file(source: str) -> JavaFile:
    return JavaFile(Path("Client.java"), source.encode())


def _token_text(java_file: JavaFile, token: TokenRange) -> str:
    return java_file.lines[token.line - 1][token.start_column : token.end_column]


def test_signature_types_are_reduced_to_simple_names() -> None:
    assert parse_signature("send(java.lang.String, java.util.Map<K, V>, int[])") == (
        "send",
        ("String", "Map", "int[]"),
    )
    assert parse_signature("run()") == ("run", ())
    assert simple_type_name("java.util.List<java.lang.String>...") == "List[]"


def test_method_is_found_by_name_and_parameter_types() -> None:
    java_file = _file(_OVERLOADS)

    one = java_file.find_method("send(java.lang.String)")
    two = java_file.find_method("send(java.lang.String, java.util.Map)")
    varargs = java_file.find_method("send(int, java.lang.String[])")
    constructor = java_file.find_method("<init>(java.lang.String)")

    assert one is not None and (one.start_line, one.end_line) == (7, 9)
    assert two is not None and (two.start_line, two.end_line) == (11, 13)
    assert varargs is not None and varargs.start_line == 15
    assert constructor is not None and constructor.start_line == 4
    assert java_file.find_method("missing()") is None


def test_lines_settle_overloads_the_types_cannot() -> None:
    source = _OVERLOADS.replace(
        "void send(int id, String... tags)", "void send(T a, U b)"
    )
    java_file = _file(source)

    # Erased generic parameters: the model's types match neither two-argument overload.
    method = java_file.find_method("send(java.lang.Object, java.lang.Object)", [15])

    assert method is not None and method.start_line == 15


def test_each_call_of_a_chain_is_told_apart_by_where_it_ends() -> None:
    java_file = _file(WIDGET_TEST)

    tokens = {}
    for name, needle in [
        ("post", 'post("/widgets")'),
        ("header", '.header("X-Tenant", "a")'),
        ("perform", '.header("X-Tenant", "a"))'),
        ("isCreated", "isCreated()"),
    ]:
        span = span_ending(WIDGET_TEST, needle)
        token = java_file.call_token(name, span.end_line, span.end_column)
        assert token is not None
        tokens[name] = (token.line, _token_text(java_file, token))

    assert tokens == {
        "post": (8, "post"),
        "header": (8, "header"),
        "perform": (8, "perform"),
        "isCreated": (9, "isCreated"),
    }
    assert java_file.call_token("perform", 8, 1) is None


def test_constructor_call_is_matched_by_its_end_past_a_non_ascii_character() -> None:
    java_file = _file(_OVERLOADS)

    created = span_ending(_OVERLOADS, "new HttpGet(path)")
    token = java_file.call_token("<init>", created.end_line, created.end_column)
    assert token is not None and _token_text(java_file, token) == "HttpGet"

    # A two-byte character sits before the call on its line.
    posted = span_ending(_OVERLOADS, "http.post(path)")
    token = java_file.call_token("post", posted.end_line, posted.end_column)
    assert token is not None and _token_text(java_file, token) == "post"


def test_unqualified_calls_leave_out_calls_on_other_objects() -> None:
    java_file = _file(WIDGET_TEST)
    method = java_file.find_method("createsWidget()")
    assert method is not None

    calls = java_file.unqualified_calls(method)

    assert ("fetch", 1) in calls and ("cleanup", 0) in calls
    assert all(name not in {"perform", "andExpect", "header"} for name, _ in calls)


def test_class_file_prefers_sources_and_resolves_nested_classes(tmp_path: Path) -> None:
    repo_root = write_repo(tmp_path / "repo")
    copy = repo_root / "target" / "generated" / "demo"
    copy.mkdir(parents=True)
    (copy / "WidgetTest.java").write_text(WIDGET_TEST, encoding="utf-8")
    repo = JavaRepo(repo_root)

    files = repo.class_files("demo.WidgetTest")

    assert [repo.relative_path(path) for path in files] == [
        "src/test/java/demo/WidgetTest.java",
        "target/generated/demo/WidgetTest.java",
    ]
    assert repo.class_files("demo.WidgetTest.Inner") == files
    assert repo.class_files("demo.WidgetTest$Inner") == files
    assert repo.class_files("other.WidgetTest") == []


def test_a_class_that_is_not_public_is_found_in_its_package_neighbour(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "src/test/java/demo/AppTests.java",
        """\
package demo;

public class AppTests {
}

class Service {
    String go() {
        return client.getForEntity(url, String.class).getBody();
    }

    static class Inner {
    }
}
""",
    )
    _write(
        tmp_path,
        "src/test/java/other/Other.java",
        "package other;\n\nclass Other {\n}\n",
    )
    repo = JavaRepo(tmp_path)

    files = repo.class_files("demo.Service")

    assert [repo.relative_path(path) for path in files] == [
        "src/test/java/demo/AppTests.java"
    ]
    assert repo.class_files("demo.Service.Inner") == files
    assert repo.class_files("other.Service") == []
    assert repo.class_files("demo.Missing") == []
    go = repo.java_file(files[0]).find_method("go()", [8])
    assert go is not None and (go.start_line, go.end_line) == (7, 9)


def test_superclass_is_found_in_the_same_package(tmp_path: Path) -> None:
    repo = JavaRepo(write_repo(tmp_path / "repo"))
    widget_test = repo.java_file(repo.class_files("demo.WidgetTest")[0])

    base = repo.superclass_file(widget_test)

    assert base is not None and base.path.name == "BaseTest.java"
    assert repo.superclass_file(base) is None


def test_columns_count_utf16_units_as_the_model_and_the_page_do() -> None:
    line = '        log("\U0001f600", http.post(path));'
    java_file = _file(f"class C {{\n    void m() {{\n{line}\n    }}\n}}\n")
    utf16 = line.encode("utf-16-le")
    call_end = line.index("http.post(path)") + len("http.post(path)")

    # The emoji is one code point and two UTF-16 units, so the two counts differ.
    token = java_file.call_token(
        "post", 3, len(line[:call_end].encode("utf-16-le")) // 2
    )

    assert token is not None
    assert (
        utf16[token.start_column * 2 : token.end_column * 2].decode("utf-16-le")
        == "post"
    )
    assert java_file.call_token("post", 3, call_end) is None


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_superclass_is_found_through_its_import_among_same_named_classes(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "a/Base.java", "package a;\npublic class Base {}\n")
    _write(tmp_path, "c/Base.java", "package c;\npublic class Base {}\n")
    _write(
        tmp_path,
        "b/Child.java",
        "package b;\nimport a.Base;\nclass Child extends Base<String> {}\n",
    )
    _write(
        tmp_path,
        "b/Wild.java",
        "package b;\nimport a.*;\nclass Wild extends Base {}\n",
    )
    _write(
        tmp_path,
        "b/Lone.java",
        "package b;\nimport d.*;\nclass Lone extends Only {}\n",
    )
    _write(tmp_path, "d/Only.java", "package d;\npublic class Only {}\n")
    repo = JavaRepo(tmp_path)

    def superclass(relative: str) -> str | None:
        parent = repo.superclass_file(repo.java_file(tmp_path / relative))
        return None if parent is None else repo.relative_path(parent.path)

    assert superclass("b/Child.java") == "a/Base.java"
    # A wildcard import does not say which of two classes named Base is meant.
    assert superclass("b/Wild.java") is None
    # A name only one file declares needs no import to settle it.
    assert superclass("b/Lone.java") == "d/Only.java"
