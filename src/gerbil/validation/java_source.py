"""Locate the Java source the model's positions point into: a class's file, a method's
lines, and the name token of a call, read with tree-sitter from a repository checkout.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import tree_sitter_java
from tree_sitter import Language, Node, Parser

_JAVA = Language(tree_sitter_java.language())

_CALLABLE_NODE_TYPES = frozenset({"method_declaration", "constructor_declaration"})
_TYPE_DECLARATION_NODE_TYPES = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
    }
)
_PRUNED_DIRECTORIES = frozenset({".git", "node_modules"})
# Build output can hold a second copy of a source file; a real source directory wins.
_BUILD_OUTPUT_DIRECTORIES = frozenset({"target", "build", "out", "bin"})
_CONSTRUCTOR_NAME = "<init>"


@dataclass(frozen=True)
class TokenRange:
    """A span inside one line: 1-based line, 0-based UTF-16 columns, end exclusive."""

    line: int
    start_column: int
    end_column: int


@dataclass(frozen=True, eq=False)
class MethodSource:
    name: str
    parameter_types: tuple[str, ...]
    start_line: int
    end_line: int
    node: Node

    def contains(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", "replace")


def simple_type_name(type_text: str) -> str:
    """`java.util.Map<String, Object>` and `Map<K, V>` both read as `Map`."""
    without_generics: list[str] = []
    depth = 0
    for character in type_text:
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
        elif depth == 0:
            without_generics.append(character)
    name = "".join(without_generics).replace("...", "[]")
    name = "".join(name.split())
    return name.rsplit(".", 1)[-1]


def parse_signature(signature: str) -> tuple[str, tuple[str, ...]]:
    """Split a model signature like `get(java.net.URI, int)` into name and simple types."""
    name, _, rest = signature.partition("(")
    parameters = rest.rsplit(")", 1)[0]
    types: list[str] = []
    depth = 0
    current: list[str] = []
    for character in parameters:
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
        if character == "," and depth == 0:
            types.append("".join(current))
            current = []
        else:
            current.append(character)
    if "".join(current).strip():
        types.append("".join(current))
    return name.strip(), tuple(simple_type_name(t) for t in types)


def _parameter_types(callable_node: Node) -> tuple[str, ...]:
    parameters = callable_node.child_by_field_name("parameters")
    if parameters is None:
        return ()
    types: list[str] = []
    for parameter in parameters.named_children:
        if parameter.type == "formal_parameter":
            type_node = parameter.child_by_field_name("type")
            if type_node is not None:
                types.append(simple_type_name(_text(type_node)))
        elif parameter.type == "spread_parameter":
            type_node = next(
                child for child in parameter.named_children if child.type != "modifiers"
            )
            types.append(simple_type_name(_text(type_node)) + "[]")
    return tuple(types)


class JavaFile:
    """One parsed source file."""

    def __init__(self, path: Path, source: bytes) -> None:
        self.path = path
        self._line_bytes = source.split(b"\n")
        self.lines = [
            line.decode("utf-8", "replace").rstrip("\r") for line in self._line_bytes
        ]
        self._root = Parser(_JAVA).parse(source).root_node

    @classmethod
    def read(cls, path: Path) -> JavaFile:
        return cls(path, path.read_bytes())

    def _utf16_column(self, row: int, byte_column: int) -> int:
        # The unit the model's spans count in, and the one the page slices lines by.
        prefix = self._line_bytes[row][:byte_column].decode("utf-8", "replace")
        return len(prefix.encode("utf-16-le")) // 2

    @cached_property
    def methods(self) -> list[MethodSource]:
        methods: list[MethodSource] = []
        for node in _walk(self._root):
            if node.type not in _CALLABLE_NODE_TYPES:
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            methods.append(
                MethodSource(
                    name=(
                        _CONSTRUCTOR_NAME
                        if node.type == "constructor_declaration"
                        else _text(name_node)
                    ),
                    parameter_types=_parameter_types(node),
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    node=node,
                )
            )
        return methods

    def find_method(
        self, signature: str, containing_lines: Iterable[int] = ()
    ) -> MethodSource | None:
        """The declaration a model signature names.

        Overloads the parameter types cannot tell apart (the model writes qualified
        types, the source may not) are settled by the lines known to sit inside it.
        """
        name, parameter_types = parse_signature(signature)
        lines = list(containing_lines)
        named = [method for method in self.methods if method.name == name]
        for narrow in (
            lambda m: len(m.parameter_types) == len(parameter_types),
            lambda m: m.parameter_types == parameter_types,
            lambda m: all(m.contains(line) for line in lines),
        ):
            narrowed = [method for method in named if narrow(method)]
            if narrowed:
                named = narrowed
        return named[0] if named else None

    @cached_property
    def _call_names_by_end(self) -> dict[tuple[int, int], list[tuple[str, Node]]]:
        by_end: dict[tuple[int, int], list[tuple[str, Node]]] = {}
        for node in _walk(self._root):
            if node.type == "method_invocation":
                name_node = node.child_by_field_name("name")
            elif node.type == "object_creation_expression":
                name_node = node.child_by_field_name("type")
            else:
                continue
            if name_node is None:
                continue
            end_row, end_byte = node.end_point
            end = (end_row + 1, self._utf16_column(end_row, end_byte))
            by_end.setdefault(end, []).append((_text(name_node), name_node))
        return by_end

    def call_token(
        self, method_name: str, end_line: int, end_column: int
    ) -> TokenRange | None:
        """The name token of the call that ends where the model's span ends.

        Every call of a fluent chain shares the chain's start, so only its end tells
        the calls apart. A constructor call is matched on its end alone, since the
        model does not name it by its type.
        """
        calls = self._call_names_by_end.get((end_line, end_column), [])
        named = [node for name, node in calls if name == method_name]
        node = named[0] if named else (calls[0][1] if len(calls) == 1 else None)
        if node is None or node.start_point[0] != node.end_point[0]:
            return None
        row = node.start_point[0]
        return TokenRange(
            line=row + 1,
            start_column=self._utf16_column(row, node.start_point[1]),
            end_column=self._utf16_column(row, node.end_point[1]),
        )

    def unqualified_calls(self, method: MethodSource) -> list[tuple[str, int]]:
        """(name, argument count) of the calls a method makes on its own object."""
        calls: list[tuple[str, int]] = []
        for node in _walk(method.node):
            if node.type != "method_invocation":
                continue
            receiver = node.child_by_field_name("object")
            if receiver is not None and receiver.type not in {"this", "super"}:
                continue
            name_node = node.child_by_field_name("name")
            arguments = node.child_by_field_name("arguments")
            if name_node is None or arguments is None:
                continue
            calls.append((_text(name_node), len(arguments.named_children)))
        return calls

    @cached_property
    def package(self) -> str:
        for node in self._root.named_children:
            if node.type == "package_declaration":
                return _text(node.named_children[-1])
        return ""

    @cached_property
    def imports(self) -> list[str]:
        return [
            _text(node.named_children[-1])
            for node in self._root.named_children
            if node.type == "import_declaration"
        ]

    @cached_property
    def top_level_types(self) -> list[str]:
        return [
            _text(name)
            for node in self._root.named_children
            if node.type in _TYPE_DECLARATION_NODE_TYPES
            and (name := node.child_by_field_name("name")) is not None
        ]

    @cached_property
    def superclass_name(self) -> str | None:
        """What the file's first top-level class extends, as written."""
        for node in self._root.named_children:
            if node.type not in _TYPE_DECLARATION_NODE_TYPES:
                continue
            superclass = node.child_by_field_name("superclass")
            if superclass is None:
                return None
            return _text(superclass.named_children[-1])
        return None


class JavaRepo:
    """A repository checkout, indexed by source file name."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._files: dict[Path, JavaFile] = {}

    @cached_property
    def _paths_by_stem(self) -> dict[str, list[Path]]:
        index: dict[str, list[Path]] = {}
        for directory, subdirectories, file_names in os.walk(self.root):
            subdirectories[:] = sorted(
                name for name in subdirectories if name not in _PRUNED_DIRECTORIES
            )
            for file_name in sorted(file_names):
                if file_name.endswith(".java"):
                    index.setdefault(file_name[:-5], []).append(
                        Path(directory, file_name)
                    )
        return index

    @cached_property
    def _paths_by_directory(self) -> dict[Path, list[Path]]:
        index: dict[Path, list[Path]] = {}
        for paths in self._paths_by_stem.values():
            for path in paths:
                index.setdefault(path.parent, []).append(path)
        return index

    def _source_first(self, paths: list[Path]) -> list[Path]:
        def in_build_output(path: Path) -> bool:
            parts = path.relative_to(self.root).parts
            return any(part in _BUILD_OUTPUT_DIRECTORIES for part in parts)

        return sorted(paths, key=lambda path: (in_build_output(path), path))

    def class_files(self, qualified_class_name: str) -> list[Path]:
        """Files that can declare the class; a nested class lives in its outer one's."""
        parts = qualified_class_name.replace("$", ".").split(".")
        for end in range(len(parts), 0, -1):
            suffix = Path(*parts[:end]).with_suffix(".java").parts
            matches = [
                path
                for path in self._paths_by_stem.get(parts[end - 1], [])
                if path.parts[-len(suffix) :] == suffix
            ]
            if matches:
                return self._source_first(matches)
        # A class that is not public may sit in another file of its package.
        for end in range(len(parts), 1, -1):
            package = parts[: end - 1]
            matches = [
                path
                for directory, paths in self._paths_by_directory.items()
                if list(directory.parts[-len(package) :]) == package
                for path in paths
                if parts[end - 1] in self.java_file(path).top_level_types
            ]
            if matches:
                return self._source_first(matches)
        return []

    def java_file(self, path: Path) -> JavaFile:
        if path not in self._files:
            self._files[path] = JavaFile.read(path)
        return self._files[path]

    def superclass_file(self, java_file: JavaFile) -> JavaFile | None:
        """The file declaring what the class extends, when the name settles on one."""
        name = java_file.superclass_name
        if name is None:
            return None
        simple_name = simple_type_name(name)
        written = name.split("<", 1)[0].strip()
        qualified_names = [written] if "." in written else []
        qualified_names += [
            imported
            for imported in java_file.imports
            if imported.rsplit(".", 1)[-1] == simple_name
        ]
        if java_file.package:
            qualified_names.append(f"{java_file.package}.{simple_name}")
        for qualified_name in qualified_names:
            paths = self.class_files(qualified_name)
            if paths:
                return self.java_file(paths[0])
        unqualified = self._paths_by_stem.get(simple_name, [])
        return self.java_file(unqualified[0]) if len(unqualified) == 1 else None

    def relative_path(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()
