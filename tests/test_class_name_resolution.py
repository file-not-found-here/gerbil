from __future__ import annotations

import pytest

from gerbil.analysis.shared.class_utils import (
    normalize_type_reference,
    resolve_known_class_name,
)


@pytest.mark.parametrize(
    ("type_reference", "expected"),
    [
        ("", ""),
        ("   ", ""),
        ("example.api.UserController", "example.api.UserController"),
        ("List<example.api.UserController>", "List"),
        ("final UserController[]", "UserController"),
    ],
)
def test_normalize_type_reference(type_reference: str, expected: str) -> None:
    assert normalize_type_reference(type_reference) == expected


def test_resolve_known_class_name_prefers_exact_match() -> None:
    known_class_names = {"example.api.UserController"}

    resolved = resolve_known_class_name(
        type_reference="example.api.UserController",
        declaring_class_name="example.api.EndpointTest",
        known_class_names=known_class_names,
    )

    assert resolved == "example.api.UserController"


def test_resolve_known_class_name_resolves_same_package_short_name() -> None:
    known_class_names = {"example.api.UserController"}

    resolved = resolve_known_class_name(
        type_reference="UserController",
        declaring_class_name="example.api.EndpointTest",
        known_class_names=known_class_names,
    )

    assert resolved == "example.api.UserController"


def test_resolve_known_class_name_resolves_unique_suffix_match() -> None:
    known_class_names = {
        "example.api.BaseController",
        "example.users.UserController",
    }

    resolved = resolve_known_class_name(
        type_reference="BaseController",
        declaring_class_name="example.api.EndpointTest",
        known_class_names=known_class_names,
    )

    assert resolved == "example.api.BaseController"


def test_resolve_known_class_name_returns_none_for_ambiguous_short_name() -> None:
    known_class_names = {
        "example.users.UserController",
        "example.orders.UserController",
    }

    resolved = resolve_known_class_name(
        type_reference="UserController",
        declaring_class_name="example.api.EndpointTest",
        known_class_names=known_class_names,
    )

    assert resolved is None


def test_resolve_known_class_name_returns_none_for_unresolvable_reference() -> None:
    known_class_names = {"example.api.BaseController"}

    resolved = resolve_known_class_name(
        type_reference="UnknownController[]",
        declaring_class_name="example.api.EndpointTest",
        known_class_names=known_class_names,
    )

    assert resolved is None
