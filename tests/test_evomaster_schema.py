from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from gerbil.evomaster.schema import (
    declare_path_parameters,
    merge_schemas,
    parse_served_schema,
    scope_schema,
)


def _parameter(
    name: str,
    source: str,
    java_type: str = "String",
    required: bool = False,
    aggregate: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "source": source,
        "type": java_type,
        "required": required,
        "is_aggregate": aggregate,
    }


def test_scope_schema_excludes_full_path_matches_and_counts_operations() -> None:
    schema: dict[str, Any] = {
        "paths": {
            "/admin/users": {"get": {}, "post": {}, "parameters": []},
            "/admin": {"get": {}},
            "/items": {"get": {}},
        }
    }

    scoped = scope_schema(schema, [r"/admin/.*"])

    assert set(scoped.schema["paths"]) == {"/admin", "/items"}
    assert scoped.total_operations == 4
    assert scoped.kept_operations == 2


def test_scope_schema_drops_ant_style_wildcard_paths() -> None:
    schema: dict[str, Any] = {
        "paths": {
            "/{hdlService}/**": {"get": {}},
            "/tags/**": {"get": {}, "post": {}},
            "/tags/{id}": {"get": {}},
        }
    }

    scoped = scope_schema(schema, [])

    assert set(scoped.schema["paths"]) == {"/tags/{id}"}
    assert scoped.total_operations == 4
    assert scoped.kept_operations == 1


def test_merge_schemas_unions_paths_and_components() -> None:
    public = {
        "info": {"title": "public"},
        "paths": {"/a": {"get": {}}},
        "components": {"schemas": {"A": {"type": "object"}}},
    }
    internal = {
        "info": {"title": "internal"},
        "paths": {"/b": {"get": {}}},
        "components": {"schemas": {"B": {"type": "object"}}},
    }

    merged = merge_schemas([public, internal]).schema

    assert set(merged["paths"]) == {"/a", "/b"}
    assert set(merged["components"]["schemas"]) == {"A", "B"}
    assert merged["info"] == {"title": "public"}


def test_parse_served_schema_reads_a_plain_document() -> None:
    assert parse_served_schema('{"paths": {}}') == {"paths": {}}


def test_parse_served_schema_decodes_a_base64_document() -> None:
    encoded = base64.b64encode(b'{"paths": {"/a": {}}}').decode()

    assert parse_served_schema(json.dumps(encoded)) == {"paths": {"/a": {}}}


@pytest.mark.parametrize(
    "text", ["<html>", "[1]", '"not base64!"', "{}", '{"paths": null}']
)
def test_parse_served_schema_rejects_other_documents(text: str) -> None:
    with pytest.raises(ValueError):
        parse_served_schema(text)


def test_merge_schemas_keeps_each_documents_methods_on_a_shared_path() -> None:
    merged = merge_schemas(
        [{"paths": {"/a": {"get": {}}}}, {"paths": {"/a": {"post": {}}}}]
    )

    assert set(merged.schema["paths"]["/a"]) == {"get", "post"}
    assert merged.shadowed_operations == 0


def test_merge_schemas_keeps_the_earlier_operation_on_a_method_and_path() -> None:
    internal = {"paths": {"/a": {"get": {"produces": ["application/vnd.v4+json"]}}}}
    public = {
        "paths": {"/a": {"get": {"produces": ["application/vnd.v4-public+json"]}}}
    }

    merged = merge_schemas([internal, public])

    assert merged.schema["paths"]["/a"] == internal["paths"]["/a"]
    assert merged.shadowed_operations == 1


def test_merge_schemas_refuses_conflicting_path_level_entries() -> None:
    first = {"paths": {"/a": {"parameters": [{"name": "x"}]}}}
    second = {"paths": {"/a": {"parameters": [{"name": "y"}]}}}

    with pytest.raises(ValueError, match="parameters /a"):
        merge_schemas([first, second])


def test_merge_schemas_renames_a_later_conflicting_component() -> None:
    first = {
        "paths": {"/a": {"get": {"schema": {"$ref": "#/components/schemas/A"}}}},
        "components": {"schemas": {"A": {"type": "object"}}},
    }
    second = {
        "paths": {"/b": {"get": {"schema": {"$ref": "#/components/schemas/A"}}}},
        "components": {"schemas": {"A": {"type": "string"}}},
    }

    merged = merge_schemas([first, second]).schema

    assert merged["components"]["schemas"] == {
        "A": {"type": "object"},
        "A_2": {"type": "string"},
    }
    assert merged["paths"]["/a"]["get"]["schema"] == {"$ref": "#/components/schemas/A"}
    assert merged["paths"]["/b"]["get"]["schema"] == {
        "$ref": "#/components/schemas/A_2"
    }


def test_merge_schemas_renames_definitions_that_refer_to_a_renamed_one() -> None:
    # Springfox numbers same-named classes per fetch, so `Table` may be either class.
    first = {
        "definitions": {
            "Table": {"title": "first"},
            "Response": {"items": {"$ref": "#/definitions/Table"}},
        }
    }
    second = {
        "paths": {"/b": {"get": {"schema": {"$ref": "#/definitions/Response"}}}},
        "definitions": {
            "Table": {"title": "second"},
            "Response": {"items": {"$ref": "#/definitions/Table"}},
        },
    }

    merged = merge_schemas([first, second]).schema

    assert merged["definitions"]["Table_2"] == {"title": "second"}
    assert merged["definitions"]["Response_2"] == {
        "items": {"$ref": "#/definitions/Table_2"}
    }
    assert merged["definitions"]["Response"] == first["definitions"]["Response"]
    assert merged["paths"]["/b"]["get"]["schema"] == {
        "$ref": "#/definitions/Response_2"
    }


def test_merge_schemas_keeps_a_later_swagger2_documents_shared_parameters() -> None:
    first = {"swagger": "2.0", "parameters": {"page": {"in": "query"}}}
    second = {
        "swagger": "2.0",
        "paths": {"/b": {"get": {"parameters": [{"$ref": "#/parameters/page"}]}}},
        "parameters": {"page": {"in": "header"}, "size": {"in": "query"}},
    }

    merged = merge_schemas([first, second]).schema

    assert merged["parameters"] == {
        "page": {"in": "query"},
        "page_2": {"in": "header"},
        "size": {"in": "query"},
    }
    assert merged["paths"]["/b"]["get"]["parameters"] == [
        {"$ref": "#/parameters/page_2"}
    ]


def test_merge_schemas_renames_past_names_already_taken() -> None:
    first = {"definitions": {"A": {"title": "1"}, "A_2": {"title": "2"}}}
    second = {"definitions": {"A": {"title": "3"}}}

    merged = merge_schemas([first, second]).schema

    assert merged["definitions"]["A_3"] == {"title": "3"}


def test_merge_schemas_leaves_properties_named_like_sections_alone() -> None:
    first = {"definitions": {"A": {"title": "1"}}}
    second = {
        "definitions": {
            "A": {"title": "2"},
            "B": {"properties": {"definitions": {"A": {"type": "string"}}}},
        }
    }

    merged = merge_schemas([first, second]).schema

    assert merged["definitions"]["B"] == second["definitions"]["B"]


def test_merge_schemas_accepts_identical_components() -> None:
    shared = {"components": {"schemas": {"A": {"type": "object"}}}}

    merged = merge_schemas([shared, shared])

    assert merged.schema["components"]["schemas"] == {"A": {"type": "object"}}
    assert merged.shadowed_operations == 0


def _path_operation(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "parameters": [
            {"name": "id", "in": "path", "required": True, "schema": schema},
            {"name": "q", "in": "query", "schema": {"type": "array"}},
        ]
    }


def test_declare_adds_a_variable_an_operation_leaves_out() -> None:
    wide = {"type": "integer", "format": "int64"}
    schema: dict[str, Any] = {
        "paths": {
            "/a/{id}": {"get": _path_operation(wide)},
            "/a/{id}/{other}": {
                "post": {"parameters": [{"name": "q", "in": "query", "schema": {}}]},
                "summary": "not an operation",
            },
        }
    }

    item = declare_path_parameters(schema)["paths"]["/a/{id}/{other}"]

    assert item["post"]["parameters"][1:] == [
        {"name": "id", "in": "path", "required": True, "schema": wide},
        {"name": "other", "in": "path", "required": True, "schema": {"type": "string"}},
    ]
    assert item["summary"] == "not an operation"


def test_declare_types_a_variable_by_the_first_path_parameter_of_its_name() -> None:
    query = {"name": "id", "in": "query", "schema": {"type": "boolean"}}
    narrow = {"type": "integer", "format": "int32"}
    schema: dict[str, Any] = {
        "paths": {
            "/q": {"get": {"parameters": [query]}},
            "/a/{id}": {"get": _path_operation(narrow)},
            "/b/{id}": {"get": _path_operation({"type": "string"})},
            "/c/{id}": {"get": {}},
        }
    }

    (parameter,) = declare_path_parameters(schema)["paths"]["/c/{id}"]["get"][
        "parameters"
    ]

    assert parameter["schema"] == narrow


def test_declare_leaves_differently_typed_declared_variables_alone() -> None:
    schema: dict[str, Any] = {
        "paths": {
            "/a/{id}": {
                "get": _path_operation({"type": "integer", "format": "int64"}),
                "delete": _path_operation({"type": "array", "items": {}}),
            },
            "/b/{id}": {"get": _path_operation({"type": "integer"})},
        }
    }

    assert declare_path_parameters(schema) == schema


def test_declare_respects_a_variable_declared_on_the_path_item() -> None:
    shared = {
        "name": "id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }
    schema = {"paths": {"/a/{id}": {"parameters": [shared], "get": {"summary": "x"}}}}

    assert declare_path_parameters(schema) == schema


def test_declare_types_a_swagger2_variable_on_the_parameter_itself() -> None:
    declared = {"name": "id", "in": "path", "type": "integer", "format": "int64"}
    schema: dict[str, Any] = {
        "swagger": "2.0",
        "paths": {
            "/a/{id}": {"get": {"parameters": [declared]}},
            "/a/{id}/b": {"get": {"parameters": []}},
            "/c/{key}": {"get": {"parameters": []}},
        },
    }

    paths = declare_path_parameters(schema)["paths"]

    assert paths["/a/{id}/b"]["get"]["parameters"] == [
        {
            "name": "id",
            "in": "path",
            "required": True,
            "type": "integer",
            "format": "int64",
        }
    ]
    assert paths["/c/{key}"]["get"]["parameters"] == [
        {"name": "key", "in": "path", "required": True, "type": "string"}
    ]


def test_merging_one_swagger2_document_adds_no_openapi3_sections() -> None:
    document = {
        "swagger": "2.0",
        "paths": {"/a": {"get": {}}},
        "definitions": {"A": {"type": "object"}},
    }

    assert merge_schemas([document]).schema == document
