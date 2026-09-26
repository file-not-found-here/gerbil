from __future__ import annotations

from cldk.analysis.java import JavaAnalysis
from cldk.models.java import JCallable
from cldk.models.java.models import JCallSite


def get_ncloc(declaration: str, body: str) -> int:
    code: str = declaration + body
    code_lines: list[str] = get_non_comment_lines(code)
    body_lines: list[str] = get_non_comment_lines(body)
    if body_lines in ([], ["}"], ["{}"], ["{", "}"]):
        return 0
    return len(code_lines)


def get_non_comment_lines(code: str) -> list[str]:
    code_without_comments: list[str] = []
    inside_block_comment: bool = False
    inside_line_comment: bool = False
    inside_string: bool = False
    inside_char: bool = False
    inside_text_block: bool = False
    index: int = 0

    while index < len(code):
        character = code[index]
        next_two = code[index : index + 2]
        next_three = code[index : index + 3]

        if inside_block_comment:
            if next_two == "*/":
                inside_block_comment = False
                index += 2
                continue
            if character == "\n":
                code_without_comments.append(character)
            index += 1
            continue

        if inside_line_comment:
            if character == "\n":
                inside_line_comment = False
                code_without_comments.append(character)
            index += 1
            continue

        if inside_text_block:
            if next_three == '"""':
                inside_text_block = False
                code_without_comments.append(next_three)
                index += 3
                continue
            code_without_comments.append(character)
            index += 1
            continue

        if inside_string:
            code_without_comments.append(character)
            if character == "\\" and index + 1 < len(code):
                code_without_comments.append(code[index + 1])
                index += 2
                continue
            if character == '"':
                inside_string = False
            index += 1
            continue

        if inside_char:
            code_without_comments.append(character)
            if character == "\\" and index + 1 < len(code):
                code_without_comments.append(code[index + 1])
                index += 2
                continue
            if character == "'":
                inside_char = False
            index += 1
            continue

        if next_three == '"""':
            inside_text_block = True
            code_without_comments.append(next_three)
            index += 3
            continue

        if next_two == "//":
            inside_line_comment = True
            index += 2
            continue

        if next_two == "/*":
            inside_block_comment = True
            index += 2
            continue

        if character == '"':
            inside_string = True
            code_without_comments.append(character)
            index += 1
            continue

        if character == "'":
            inside_char = True
            code_without_comments.append(character)
            index += 1
            continue

        code_without_comments.append(character)
        index += 1

    code_lines: list[str] = []
    for line in "".join(code_without_comments).splitlines():
        stripped_line: str = line.strip()
        if stripped_line:
            code_lines.append(stripped_line)

    return code_lines


def count_objects_created(method_details: JCallable | None) -> int:
    if method_details is None:
        return 0
    return sum(
        1 for call_site in method_details.call_sites if call_site.is_constructor_call
    )


def get_call_sites_sorted(method_details: JCallable | None) -> list[JCallSite]:
    if method_details is None:
        return []
    return sorted(
        list(method_details.call_sites),
        key=lambda call_site: (
            int(call_site.start_line),
            int(call_site.start_column),
        ),
    )


def get_application_method_metrics(
    analysis: JavaAnalysis,
    application_classes: list[str],
) -> tuple[int, int]:
    method_count: int = 0
    cyclomatic_complexity: int = 0
    for class_name in application_classes:
        methods = analysis.get_methods_in_class(class_name)
        method_count += len(methods)
        for method in methods.values():
            cyclomatic_complexity += int(method.cyclomatic_complexity or 0)
    return method_count, cyclomatic_complexity


def get_test_utility_method_count(
    analysis: JavaAnalysis,
    test_utility_classes: list[str],
) -> int:
    method_count: int = 0
    for class_name in test_utility_classes:
        method_count += len(analysis.get_methods_in_class(class_name))
    return method_count


__all__ = [
    "count_objects_created",
    "get_application_method_metrics",
    "get_call_sites_sorted",
    "get_ncloc",
    "get_non_comment_lines",
    "get_test_utility_method_count",
]
