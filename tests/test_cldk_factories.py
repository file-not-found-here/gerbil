from __future__ import annotations

from tests.cldk_factories import make_call_site, make_callable


def test_make_call_site_defaults_preserved() -> None:
    call_site = make_call_site(method_name="invoke")

    assert call_site.is_static_call is False
    assert call_site.is_private is False
    assert call_site.is_public is True
    assert call_site.is_protected is False
    assert call_site.is_unspecified is False
    assert call_site.is_constructor_call is False


def test_make_call_site_flag_overrides_round_trip() -> None:
    call_site = make_call_site(
        method_name="invoke",
        is_static_call=True,
        is_private=True,
        is_public=False,
        is_protected=True,
        is_unspecified=True,
        is_constructor_call=True,
    )

    assert call_site.is_static_call is True
    assert call_site.is_private is True
    assert call_site.is_public is False
    assert call_site.is_protected is True
    assert call_site.is_unspecified is True
    assert call_site.is_constructor_call is True


def test_make_callable_defaults_preserved() -> None:
    callable_details = make_callable()

    assert callable_details.is_implicit is False
    assert callable_details.is_constructor is False
    assert callable_details.is_entrypoint is False


def test_make_callable_flag_overrides_round_trip() -> None:
    callable_details = make_callable(
        is_implicit=True,
        is_constructor=True,
        is_entrypoint=True,
    )

    assert callable_details.is_implicit is True
    assert callable_details.is_constructor is True
    assert callable_details.is_entrypoint is True
