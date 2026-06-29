from __future__ import annotations

from cldk.models.java import JImport

from gerbil.analysis.shared import CommonAnalysis
from gerbil.analysis.shared.constant_resolution import (
    ConstantResolver,
    parse_java_string_literal,
    split_top_level_concat,
)
from tests.cldk_factories import make_field, make_type
from tests.fake_java_analysis import FakeJavaAnalysis


def _resolver(analysis: FakeJavaAnalysis) -> ConstantResolver:
    return CommonAnalysis(analysis).get_constant_resolver()


# parse_java_string_literal


def test_simple_literal_strips_surrounding_quotes() -> None:
    assert parse_java_string_literal('"/rest/quotes"') == "/rest/quotes"


def test_escaped_quote_inside_literal_unescapes() -> None:
    assert parse_java_string_literal('"a\\"b"') == 'a"b'


def test_unicode_escape_decodes() -> None:
    assert parse_java_string_literal('"a\\u0041b"') == "aAb"


def test_simple_escapes_decode() -> None:
    assert parse_java_string_literal('"a\\tb\\n"') == "a\tb\n"


def test_unknown_escape_is_unresolvable() -> None:
    assert parse_java_string_literal('"a\\xb"') is None


def test_octal_escape_is_unresolvable() -> None:
    assert parse_java_string_literal('"\\101"') is None


def test_text_block_is_unresolvable() -> None:
    assert parse_java_string_literal('"""\nhello\n"""') is None


def test_non_literal_token_is_unresolvable() -> None:
    assert parse_java_string_literal("CONSTANT") is None
    assert parse_java_string_literal('"a" + "b"') is None


# split_top_level_concat


def test_plus_inside_quoted_literal_does_not_split() -> None:
    assert split_top_level_concat('"a+b"') == ['"a+b"']


def test_plus_inside_char_literal_does_not_split() -> None:
    assert split_top_level_concat("BASE + '+'") == ["BASE", "'+'"]


def test_parenthesized_plus_does_not_split_at_top_level() -> None:
    assert split_top_level_concat('method("a" + "b") + C') == ['method("a" + "b")', "C"]


def test_empty_token_makes_split_unresolvable() -> None:
    assert split_top_level_concat('"a" + ') is None


# Single-class constant resolution


def test_simple_constant_resolves_to_its_literal_value() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["QUOTES_PATH"],
                        modifiers=["static", "final"],
                        variable_initializers={"QUOTES_PATH": '"/rest/quotes"'},
                    )
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Paths", "QUOTES_PATH") == "/rest/quotes"


def test_concat_of_same_class_constant_and_literal_resolves_recursively() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["BASE", "COMBINED"],
                        modifiers=["static", "final"],
                        variable_initializers={
                            "BASE": '"/base"',
                            "COMBINED": 'BASE + "/sub"',
                        },
                    )
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Paths", "COMBINED") == "/base/sub"


def test_local_value_resolves_bare_identifier_token() -> None:
    analysis = FakeJavaAnalysis(classes={"example.Helper": make_type()})
    resolver = _resolver(analysis)

    assert (
        resolver.resolve_expression(
            "example.Helper",
            'uri + "/tail"',
            local_values={"uri": "/api/widgets"},
        )
        == "/api/widgets/tail"
    )


def test_local_value_shadows_same_named_field_constant() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Helper": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["uri"],
                        modifiers=["static", "final"],
                        variable_initializers={"uri": '"/from-field"'},
                    )
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    assert (
        resolver.resolve_expression(
            "example.Helper", "uri", local_values={"uri": "/from-parameter"}
        )
        == "/from-parameter"
    )
    assert resolver.resolve_expression("example.Helper", "uri") == "/from-field"


def test_none_valued_local_poisons_instead_of_falling_through_to_field() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Helper": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["uri"],
                        modifiers=["static", "final"],
                        variable_initializers={"uri": '"/from-field"'},
                    )
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    # The local shadows the field even when its value is statically unknown.
    assert (
        resolver.resolve_expression("example.Helper", "uri", local_values={"uri": None})
        is None
    )
    assert (
        resolver.resolve_expression(
            "example.Helper", 'uri + "/tail"', local_values={"uri": None}
        )
        is None
    )


def test_local_value_never_matches_qualified_token() -> None:
    analysis = FakeJavaAnalysis(classes={"example.Helper": make_type()})
    resolver = _resolver(analysis)

    assert (
        resolver.resolve_expression(
            "example.Helper", "Other.uri", local_values={"uri": "/bound"}
        )
        is None
    )


def test_expression_resolves_when_every_token_resolves() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["BASE"],
                        modifiers=["static", "final"],
                        variable_initializers={"BASE": '"/base"'},
                    )
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_expression("example.Paths", 'BASE + "/x"') == "/base/x"


# Qualified cross-class references


def test_qualified_reference_resolves_through_import() -> None:
    paths_class = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["QUOTES"],
                modifiers=["static", "final"],
                variable_initializers={"QUOTES": '"/rest/quotes"'},
            )
        ]
    )
    analysis = FakeJavaAnalysis(
        classes={
            "other.pkg.Paths": paths_class,
            "example.ApiTest": make_type(),
        }
    )
    resolver = ConstantResolver(
        analysis=analysis,
        get_class_imports_for_class=lambda class_name: (
            [JImport(path="other.pkg.Paths", is_static=False, is_wildcard=False)]
            if class_name == "example.ApiTest"
            else []
        ),
        get_class_resolution_order=lambda class_name, _include: [class_name],
    )

    assert (
        resolver.resolve_identifier("example.ApiTest", "Paths.QUOTES") == "/rest/quotes"
    )


def test_qualified_reference_resolves_for_same_package_without_import() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["QUOTES"],
                        modifiers=["static", "final"],
                        variable_initializers={"QUOTES": '"/rest/quotes"'},
                    )
                ]
            ),
            "example.ApiTest": make_type(),
        }
    )
    resolver = _resolver(analysis)

    assert (
        resolver.resolve_identifier("example.ApiTest", "Paths.QUOTES") == "/rest/quotes"
    )


def test_qualified_reference_to_unknown_class_is_unresolvable() -> None:
    analysis = FakeJavaAnalysis(classes={"example.ApiTest": make_type()})
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.ApiTest", "External.QUOTES") is None


def test_fully_qualified_reference_resolves_when_class_is_present() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "other.pkg.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["QUOTES"],
                        modifiers=["static", "final"],
                        variable_initializers={"QUOTES": '"/rest/quotes"'},
                    )
                ]
            ),
            "example.ApiTest": make_type(),
        }
    )
    resolver = _resolver(analysis)

    assert (
        resolver.resolve_identifier("example.ApiTest", "other.pkg.Paths.QUOTES")
        == "/rest/quotes"
    )


# Inheritance


def test_inherited_constant_from_project_superclass_resolves() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Base": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["PATH"],
                        modifiers=["static", "final"],
                        variable_initializers={"PATH": '"/base"'},
                    )
                ]
            ),
            "example.Child": make_type(extends_list=["example.Base"]),
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Child", "PATH") == "/base"


def test_subclass_redeclaration_shadows_inherited_constant() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Base": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["PATH"],
                        modifiers=["static", "final"],
                        variable_initializers={"PATH": '"/base"'},
                    )
                ]
            ),
            "example.Child": make_type(
                extends_list=["example.Base"],
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["PATH"],
                        modifiers=["static", "final"],
                        variable_initializers={"PATH": '"/child"'},
                    )
                ],
            ),
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Child", "PATH") == "/child"


# Interfaces


def test_interface_constant_resolves_despite_empty_modifiers() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Constants": make_type(
                is_interface=True,
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["PATH"],
                        modifiers=[],
                        variable_initializers={"PATH": '"/api"'},
                    )
                ],
            )
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Constants", "PATH") == "/api"


def test_ambiguous_interface_constants_with_different_values_is_unresolvable() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.IfaceA": make_type(
                is_interface=True,
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["PATH"],
                        variable_initializers={"PATH": '"/a"'},
                    )
                ],
            ),
            "example.IfaceB": make_type(
                is_interface=True,
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["PATH"],
                        variable_initializers={"PATH": '"/b"'},
                    )
                ],
            ),
            "example.Impl": make_type(
                implements_list=["example.IfaceA", "example.IfaceB"]
            ),
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Impl", "PATH") is None


def test_duplicate_interface_constants_with_same_value_resolves() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.IfaceA": make_type(
                is_interface=True,
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["PATH"],
                        variable_initializers={"PATH": '"/same"'},
                    )
                ],
            ),
            "example.IfaceB": make_type(
                is_interface=True,
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["PATH"],
                        variable_initializers={"PATH": '"/same"'},
                    )
                ],
            ),
            "example.Impl": make_type(
                implements_list=["example.IfaceA", "example.IfaceB"]
            ),
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Impl", "PATH") == "/same"


# Unresolvable initializer shapes


def test_ternary_initializer_is_unresolvable() -> None:
    analysis = _single_constant_analysis('true ? "/a" : "/b"')
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Paths", "PATH") is None


def test_method_call_initializer_is_unresolvable() -> None:
    analysis = _single_constant_analysis('System.getProperty("path")')
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Paths", "PATH") is None


def test_text_block_initializer_is_unresolvable() -> None:
    analysis = _single_constant_analysis('"""\n/api\n"""')
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Paths", "PATH") is None


def test_static_block_initialized_field_is_unresolvable() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["PATH"],
                        modifiers=["static", "final"],
                        variable_initializers={},
                    )
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Paths", "PATH") is None


def test_non_string_typed_field_is_excluded() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="int",
                        variables=["PORT"],
                        modifiers=["static", "final"],
                        variable_initializers={"PORT": "8080"},
                    ),
                    make_field(
                        type_name="char",
                        variables=["SEP"],
                        modifiers=["static", "final"],
                        variable_initializers={"SEP": "'/'"},
                    ),
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Paths", "PORT") is None
    assert resolver.resolve_identifier("example.Paths", "SEP") is None


def test_non_static_final_string_field_is_excluded() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["path"],
                        modifiers=["private", "final"],
                        variable_initializers={"path": '"/instance"'},
                    )
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Paths", "path") is None


def test_one_unresolvable_token_makes_whole_expression_unresolvable() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["CONTEXT", "PATH"],
                        modifiers=["static", "final"],
                        variable_initializers={
                            "CONTEXT": '"/ctx"',
                            "PATH": '"/path"',
                        },
                    ),
                    make_field(
                        type_name="int",
                        variables=["PORT"],
                        modifiers=["static", "final"],
                        variable_initializers={"PORT": "8080"},
                    ),
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_expression("example.Paths", "CONTEXT + PORT + PATH") is None


def test_bare_numeric_literal_token_makes_expression_unresolvable() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["PATH"],
                        modifiers=["static", "final"],
                        variable_initializers={"PATH": '"/p"'},
                    )
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    # A bare numeric literal is neither a String literal nor a resolvable
    # identifier, so it poisons the whole concatenation.
    assert resolver.resolve_expression("example.Paths", "PATH + 1") is None


def test_parenthesized_token_is_unresolvable() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["BASE"],
                        modifiers=["static", "final"],
                        variable_initializers={"BASE": '"/base"'},
                    )
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_expression("example.Paths", '("/base")') is None


# Multi-declarator and cycles


def test_multi_declarator_field_resolves_per_variable() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["A", "B"],
                        modifiers=["static", "final"],
                        variable_initializers={"A": '"/a"', "B": '"/b"'},
                    )
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Paths", "A") == "/a"
    assert resolver.resolve_identifier("example.Paths", "B") == "/b"


def test_reference_cycle_resolves_to_none() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["A", "B"],
                        modifiers=["static", "final"],
                        variable_initializers={
                            "A": 'B + "/x"',
                            "B": 'A + "/y"',
                        },
                    )
                ]
            )
        }
    )
    resolver = _resolver(analysis)

    assert resolver.resolve_identifier("example.Paths", "A") is None
    assert resolver.resolve_identifier("example.Paths", "B") is None


def _single_constant_analysis(initializer: str) -> FakeJavaAnalysis:
    return FakeJavaAnalysis(
        classes={
            "example.Paths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["PATH"],
                        modifiers=["static", "final"],
                        variable_initializers={"PATH": initializer},
                    )
                ]
            )
        }
    )


# Wildcard-import qualifiers


def test_qualified_reference_resolves_through_wildcard_import() -> None:
    constants_class = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["USERS"],
                modifiers=["static", "final"],
                variable_initializers={"USERS": '"/users"'},
            )
        ]
    )
    analysis = FakeJavaAnalysis(
        classes={
            "com.app.constants.ApiPaths": constants_class,
            "example.ApiTest": make_type(),
        },
        java_files={"example.ApiTest": "example/ApiTest.java"},
        import_declarations_by_file={"example/ApiTest.java": ["com.app.constants.*"]},
    )
    resolver = CommonAnalysis(analysis).get_constant_resolver()

    assert resolver.resolve_identifier("example.ApiTest", "ApiPaths.USERS") == "/users"


# Nested constant-holder qualifiers


def test_nested_constant_holder_qualifier_resolves() -> None:
    constants_class = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["USERS"],
                modifiers=["static", "final"],
                variable_initializers={"USERS": '"/users"'},
            )
        ]
    )
    analysis = FakeJavaAnalysis(
        classes={
            "com.app.ApiConstants": make_type(),
            "com.app.ApiConstants.Paths": constants_class,
            "example.ApiTest": make_type(),
        },
        java_files={"example.ApiTest": "example/ApiTest.java"},
        import_declarations_by_file={"example/ApiTest.java": ["com.app.ApiConstants"]},
    )
    resolver = CommonAnalysis(analysis).get_constant_resolver()

    assert (
        resolver.resolve_identifier("example.ApiTest", "ApiConstants.Paths.USERS")
        == "/users"
    )


# Ambiguous wildcard qualifier matches fail closed


def test_ambiguous_wildcard_import_qualifier_fails_closed() -> None:
    analysis = FakeJavaAnalysis(
        classes={
            "com.app.a.ApiPaths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["USERS"],
                        modifiers=["static", "final"],
                        variable_initializers={"USERS": '"/a/users"'},
                    )
                ]
            ),
            "com.app.b.ApiPaths": make_type(
                field_declarations=[
                    make_field(
                        type_name="java.lang.String",
                        variables=["USERS"],
                        modifiers=["static", "final"],
                        variable_initializers={"USERS": '"/b/users"'},
                    )
                ]
            ),
            "example.ApiTest": make_type(),
        },
        java_files={"example.ApiTest": "example/ApiTest.java"},
        import_declarations_by_file={
            "example/ApiTest.java": [
                "com.app.a.*",
                "com.app.b.*",
            ]
        },
    )
    resolver = CommonAnalysis(analysis).get_constant_resolver()

    assert resolver.resolve_identifier("example.ApiTest", "ApiPaths.USERS") is None


# Qualifier precedence: same-package > wildcard, explicit > wildcard


def test_same_package_qualifier_beats_wildcard_import() -> None:
    wildcard_class = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["USERS"],
                modifiers=["static", "final"],
                variable_initializers={"USERS": '"/wildcard/users"'},
            )
        ]
    )
    same_package_class = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["USERS"],
                modifiers=["static", "final"],
                variable_initializers={"USERS": '"/same-package/users"'},
            )
        ]
    )
    analysis = FakeJavaAnalysis(
        classes={
            "com.app.constants.ApiPaths": wildcard_class,
            "example.ApiPaths": same_package_class,
            "example.ApiTest": make_type(),
        },
        java_files={"example.ApiTest": "example/ApiTest.java"},
        import_declarations_by_file={"example/ApiTest.java": ["com.app.constants.*"]},
    )
    resolver = CommonAnalysis(analysis).get_constant_resolver()

    assert (
        resolver.resolve_identifier("example.ApiTest", "ApiPaths.USERS")
        == "/same-package/users"
    )


def test_explicit_import_dotted_decomposition_beats_wildcard() -> None:
    explicit_class = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["USERS"],
                modifiers=["static", "final"],
                variable_initializers={"USERS": '"/explicit/users"'},
            )
        ]
    )
    wildcard_class = make_type(
        field_declarations=[
            make_field(
                type_name="java.lang.String",
                variables=["USERS"],
                modifiers=["static", "final"],
                variable_initializers={"USERS": '"/wildcard/users"'},
            )
        ]
    )
    analysis = FakeJavaAnalysis(
        classes={
            "com.app.ApiConstants": make_type(),
            "com.app.ApiConstants.Paths": explicit_class,
            "com.other.ApiConstants.Paths": wildcard_class,
            "example.ApiTest": make_type(),
        },
        java_files={"example.ApiTest": "example/ApiTest.java"},
        import_declarations_by_file={
            "example/ApiTest.java": [
                "com.app.ApiConstants",
                "com.other.*",
            ]
        },
    )
    resolver = CommonAnalysis(analysis).get_constant_resolver()

    assert (
        resolver.resolve_identifier("example.ApiTest", "ApiConstants.Paths.USERS")
        == "/explicit/users"
    )
