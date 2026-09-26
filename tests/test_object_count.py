from __future__ import annotations

from gerbil.analysis.shared import CommonAnalysis
from tests.cldk_factories import (
    make_call_site,
    make_callable,
    make_variable_declaration,
)


class TestCountObjectsCreated:
    def test_constructor_call_with_new_declaration_counted_once(self) -> None:
        method = make_callable(
            call_sites=[
                make_call_site(
                    method_name="Foo",
                    is_constructor_call=True,
                ),
            ],
            variable_declarations=[
                make_variable_declaration(
                    name="foo",
                    type_name="com.example.Foo",
                    initializer="new Foo()",
                ),
            ],
        )

        assert CommonAnalysis.count_objects_created(method) == 1

    def test_multiple_constructors(self) -> None:
        method = make_callable(
            call_sites=[
                make_call_site(method_name="Foo", is_constructor_call=True),
                make_call_site(method_name="Bar", is_constructor_call=True),
            ],
        )

        assert CommonAnalysis.count_objects_created(method) == 2

    def test_constructor_free_new_initializer_is_not_counted(self) -> None:
        method = make_callable(
            variable_declarations=[
                make_variable_declaration(
                    name="foo",
                    type_name="com.example.Foo",
                    initializer="new Foo()",
                )
            ],
        )

        assert CommonAnalysis.count_objects_created(method) == 0

    def test_no_constructors(self) -> None:
        method = make_callable(
            call_sites=[
                make_call_site(method_name="doStuff", is_constructor_call=False),
            ],
        )

        assert CommonAnalysis.count_objects_created(method) == 0

    def test_none_method(self) -> None:
        assert CommonAnalysis.count_objects_created(None) == 0
