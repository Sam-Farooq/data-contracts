"""What each dialect becomes, and what each loader refuses."""

from __future__ import annotations

import json

import pytest

from contractlint.errors import SchemaError
from contractlint.loader import dialect_for_path, load_schema_file, load_schema_obj
from contractlint.types import Kind

from .conftest import avro, doc, enum, field, jdoc


def test_a_primitive_field_keeps_its_kind():
    d = doc(avro(field("id", "string"), field("n", "int")))
    assert [f.type.kind for f in d.root.fields] == [Kind.STRING, Kind.INT]


def test_a_union_with_null_becomes_a_nullable_type():
    d = doc(avro(field("note", ["null", "string"], default=None)))
    f = d.root.fields[0]
    assert f.type.kind is Kind.STRING
    assert f.type.nullable is True
    assert f.has_default is True
    assert f.default is None


def test_a_default_of_null_is_a_default():
    """`default is None` cannot tell a null default from no default at all."""
    with_default = doc(avro(field("note", ["null", "string"], default=None))).root.fields[0]
    without = doc(avro(field("note", ["null", "string"]))).root.fields[0]
    assert with_default.has_default is True
    assert without.has_default is False
    assert with_default.default is without.default is None


def test_a_logical_type_is_carried_but_the_kind_is_the_physical_one():
    d = doc(avro(field("at", {"type": "long", "logicalType": "timestamp-millis"})))
    assert d.root.fields[0].type.kind is Kind.LONG
    assert d.root.fields[0].type.logical == "timestamp-millis"


def test_a_decimal_carries_its_precision_and_scale():
    d = doc(
        avro(
            field(
                "fee",
                {"type": "bytes", "logicalType": "decimal", "precision": 9, "scale": 2},
            )
        )
    )
    t = d.root.fields[0].type
    assert t.kind is Kind.BYTES
    assert t.logical == "decimal"
    assert (t.precision, t.scale) == (9, 2)
    assert t.label() == "bytes/decimal(9,2)"


def test_a_decimal_scale_defaults_to_zero():
    """The specification says so, so this is the spec rather than a guess."""
    d = doc(avro(field("fee", {"type": "bytes", "logicalType": "decimal", "precision": 4})))
    assert d.root.fields[0].type.scale == 0


def test_a_nullable_decimal_keeps_its_digits_through_the_union():
    d = doc(
        avro(
            field(
                "fee",
                ["null", {"type": "bytes", "logicalType": "decimal", "precision": 6, "scale": 3}],
                default=None,
            )
        )
    )
    t = d.root.fields[0].type
    assert t.nullable is True
    assert (t.precision, t.scale) == (6, 3)


@pytest.mark.parametrize(
    ("node", "fragment"),
    [
        ({"type": "bytes", "logicalType": "decimal"}, "needs an integer precision"),
        (
            {"type": "bytes", "logicalType": "decimal", "precision": "9"},
            "needs an integer precision",
        ),
        ({"type": "bytes", "logicalType": "decimal", "precision": 0}, "must be at least 1"),
        (
            {"type": "bytes", "logicalType": "decimal", "precision": 9, "scale": "2"},
            "scale must be an integer",
        ),
        (
            {"type": "bytes", "logicalType": "decimal", "precision": 9, "scale": -1},
            "between 0 and its precision 9",
        ),
        (
            {"type": "bytes", "logicalType": "decimal", "precision": 9, "scale": 10},
            "between 0 and its precision 9",
        ),
    ],
)
def test_a_decimal_without_usable_digits_is_refused(node, fragment):
    with pytest.raises(SchemaError) as exc:
        doc(avro(field("fee", node)))
    assert fragment in str(exc.value)


def test_an_enum_keeps_symbol_order_and_its_default():
    d = doc(avro(field("c", enum("CARD", "TRANSFER", default="CARD"))))
    t = d.root.fields[0].type
    assert t.symbols == ("CARD", "TRANSFER")
    assert t.enum_default == "CARD"


def test_nested_records_arrays_and_maps_load():
    d = doc(
        avro(
            field(
                "hits",
                {
                    "type": "array",
                    "items": {
                        "type": "record",
                        "name": "H",
                        "fields": [{"name": "doc_id", "type": "string"}],
                    },
                },
            ),
            field("tags", {"type": "map", "values": "string"}),
        )
    )
    hits, tags = d.root.fields
    assert hits.type.kind is Kind.ARRAY
    assert hits.type.items.kind is Kind.RECORD
    assert hits.type.items.fields[0].name == "doc_id"
    assert tags.type.kind is Kind.MAP
    assert tags.type.values.kind is Kind.STRING


def test_aliases_are_read():
    d = doc(avro(field("amount_minor", "long", aliases=["amount"])))
    assert d.root.fields[0].aliases == ("amount",)


@pytest.mark.parametrize(
    ("obj", "fragment"),
    [
        (
            {"type": "enum", "name": "E", "symbols": ["A"]},
            "top level of a subject must be a record",
        ),
        (avro(field("x", ["string", "long"])), "two-branch union"),
        (avro(field("x", ["null", "string", "long"])), "two-branch union"),
        (avro(field("x", [])), "empty union"),
        (avro(field("x", "decimal")), "unknown or unsupported Avro type"),
        (avro(field("x", 7)), "must be a string, list or object"),
        (avro(field("x", {"logicalType": "date"})), "object type with no 'type' key"),
        (avro(field("x", {"type": "enum", "name": "E", "symbols": []})), "enum with no symbols"),
        (avro(field("x", {"type": "array"})), "array with no items"),
        (avro(field("x", {"type": "map"})), "map with no values"),
        (avro(field("x", "string"), field("x", "long")), "duplicate field"),
        ({"type": "record", "name": "R"}, "no fields list"),
        (avro({"name": "x"}), "a field needs a type"),
        (avro({"type": "string"}), "a field needs a name"),
        (avro(field("x", enum("A", "B", default="C"))), "not one of its own symbols"),
    ],
)
def test_the_avro_loader_refuses_rather_than_guesses(obj, fragment):
    with pytest.raises(SchemaError) as exc:
        doc(obj)
    assert fragment in str(exc.value)


def test_the_error_names_the_field_it_gave_up_on():
    with pytest.raises(SchemaError) as exc:
        doc(avro(field("ok", "string"), field("bad", ["string", "long"])))
    assert "at bad" in str(exc.value)


def test_a_nested_error_carries_the_whole_path():
    with pytest.raises(SchemaError) as exc:
        doc(
            avro(
                field(
                    "outer",
                    {
                        "type": "record",
                        "name": "O",
                        "fields": [{"name": "inner", "type": ["string", "long"]}],
                    },
                )
            )
        )
    assert "at outer.inner" in str(exc.value)


# --- JSON Schema -----------------------------------------------------------


def test_required_and_optional_properties():
    d = jdoc(
        {
            "type": "object",
            "required": ["a"],
            "properties": {"a": {"type": "string"}, "b": {"type": "string", "default": "x"}},
        }
    )
    a, b = d.root.fields
    assert a.required() is True
    assert b.required() is False
    assert b.default == "x"


def test_an_optional_property_with_no_default_becomes_nullable_and_says_so():
    d = jdoc({"type": "object", "properties": {"b": {"type": "string"}}})
    assert d.root.fields[0].type.nullable is True
    assert d.root.fields[0].has_default is True
    assert any("optional property with no default" in a for a in d.assumptions)


def test_integer_widens_to_long_and_the_document_records_it():
    d = jdoc({"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}})
    assert d.root.fields[0].type.kind is Kind.LONG
    assert any("integer is loaded as Avro long" in a for a in d.assumptions)


def test_number_becomes_double():
    d = jdoc({"type": "object", "required": ["n"], "properties": {"n": {"type": "number"}}})
    assert d.root.fields[0].type.kind is Kind.DOUBLE


def test_a_default_on_a_required_property_is_dropped_with_a_note():
    d = jdoc(
        {
            "type": "object",
            "required": ["a"],
            "properties": {"a": {"type": "string", "default": "zzz"}},
        }
    )
    assert d.root.fields[0].has_default is False
    assert any("default on a required property is ignored" in a for a in d.assumptions)


def test_a_type_list_with_null_is_nullable():
    d = jdoc(
        {"type": "object", "required": ["a"], "properties": {"a": {"type": ["string", "null"]}}}
    )
    assert d.root.fields[0].type.nullable is True
    assert d.root.fields[0].required() is True


def test_an_enum_list_containing_null_is_read_as_nullability():
    d = jdoc(
        {
            "type": "object",
            "required": ["s"],
            "properties": {"s": {"type": "string", "enum": ["a", "b", None]}},
        }
    )
    t = d.root.fields[0].type
    assert t.symbols == ("a", "b")
    assert t.nullable is True


def test_additional_properties_becomes_a_map():
    d = jdoc(
        {
            "type": "object",
            "required": ["m"],
            "properties": {"m": {"type": "object", "additionalProperties": {"type": "string"}}},
        }
    )
    assert d.root.fields[0].type.kind is Kind.MAP


def test_format_is_carried_as_the_logical_type():
    d = jdoc(
        {
            "type": "object",
            "required": ["at"],
            "properties": {"at": {"type": "string", "format": "date-time"}},
        }
    )
    assert d.root.fields[0].type.logical == "date-time"


@pytest.mark.parametrize("keyword", ["$ref", "oneOf", "anyOf", "allOf", "not", "if"])
def test_indirection_is_refused(keyword):
    with pytest.raises(SchemaError) as exc:
        jdoc({"type": "object", "required": ["a"], "properties": {"a": {keyword: "whatever"}}})
    assert keyword in str(exc.value)


@pytest.mark.parametrize(
    ("obj", "fragment"),
    [
        ({"type": "array"}, "top level of a subject must be an object"),
        ({"type": "object"}, "object with no properties"),
        ({"type": "object", "properties": {}, "required": ["ghost"]}, "does not exist"),
        ({"type": "object", "properties": {"a": {"type": "object"}}}, "needs either properties"),
        ({"type": "object", "properties": {"a": {}}}, "needs a type"),
        ({"type": "object", "properties": {"a": {"type": ["string", "integer"]}}}, "one non-null"),
        ({"type": "object", "properties": {"a": {"type": "null"}}}, "unsupported JSON Schema type"),
        ({"type": "object", "properties": {"a": 7}}, "must be an object"),
    ],
)
def test_the_json_schema_loader_refuses_rather_than_guesses(obj, fragment):
    with pytest.raises(SchemaError) as exc:
        jdoc(obj)
    assert fragment in str(exc.value)


# --- files -----------------------------------------------------------------


def test_the_dialect_comes_from_the_suffix():
    assert dialect_for_path("registry/x/v1.avsc") == "avro"
    assert dialect_for_path("registry/x/v1.schema.json") == "json-schema"


def test_a_bare_json_suffix_is_ambiguous_and_refused():
    with pytest.raises(SchemaError) as exc:
        dialect_for_path("registry/x/v1.json")
    assert "cannot tell the dialect" in str(exc.value)


def test_the_subject_defaults_to_the_parent_directory(tmp_path):
    subject_dir = tmp_path / "payments.transaction"
    subject_dir.mkdir()
    p = subject_dir / "v1.avsc"
    p.write_text(json.dumps(avro(field("id", "string"))))
    assert load_schema_file(p).subject == "payments.transaction"


def test_bad_json_names_the_line(tmp_path):
    p = tmp_path / "v1.avsc"
    p.write_text('{"type": "record",\n "fields": [,]}')
    with pytest.raises(SchemaError) as exc:
        load_schema_file(p)
    assert "not valid JSON" in str(exc.value)
    assert "line 2" in str(exc.value)


def test_a_missing_file_is_a_schema_error(tmp_path):
    with pytest.raises(SchemaError) as exc:
        load_schema_file(tmp_path / "nope.avsc")
    assert "cannot read the file" in str(exc.value)


def test_an_unknown_dialect_argument_is_refused():
    with pytest.raises(SchemaError):
        load_schema_obj({}, dialect="protobuf", subject="x")
