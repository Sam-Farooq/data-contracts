"""The case table.

Each row is a concrete schema pair, the change kinds it should produce, and the
directions of reading it should break. The rules are the whole product, so the
table is the whole test: a row per rule, plus the combinations that only make
sense together.

BW and FW are the directions. An empty set means both readers still work.
"""

from __future__ import annotations

from typing import Any

import pytest

from contractlint.diff import diff_schemas
from contractlint.rules import RULES, Change, ChangeKind
from contractlint.types import Direction

from .conftest import avro, doc, enum, field

BW = {Direction.BACKWARD}
FW = {Direction.FORWARD}
BOTH = {Direction.BACKWARD, Direction.FORWARD}
OK: set[Direction] = set()

K = ChangeKind


def changes(old: dict[str, Any], new: dict[str, Any]) -> list[Change]:
    return diff_schemas(doc(old, version=1), doc(new, version=2))


def broken(old: dict[str, Any], new: dict[str, Any], *, strict: bool = False) -> set[Direction]:
    found: set[Direction] = set()
    for c in changes(old, new):
        found |= c.breaks_under(strict=strict)
    return found


ONE = avro(field("x", "string"))

# id, old, new, expected kinds in walk order, directions broken
CASES: list[tuple[str, dict[str, Any], dict[str, Any], tuple[ChangeKind, ...], set[Direction]]] = [
    (
        "nothing changed",
        ONE,
        avro(field("x", "string")),
        (),
        OK,
    ),
    (
        "add an optional field",
        ONE,
        avro(field("x", "string"), field("y", "long", default=0)),
        (K.FIELD_ADDED_OPTIONAL,),
        OK,
    ),
    (
        "add a nullable field with a null default",
        ONE,
        avro(field("x", "string"), field("y", ["null", "long"], default=None)),
        (K.FIELD_ADDED_OPTIONAL,),
        OK,
    ),
    (
        "add a required field",
        ONE,
        avro(field("x", "string"), field("y", "long")),
        (K.FIELD_ADDED_REQUIRED,),
        BW,
    ),
    (
        "add a nullable field with no default, which is still required",
        ONE,
        avro(field("x", "string"), field("y", ["null", "long"])),
        (K.FIELD_ADDED_REQUIRED,),
        BW,
    ),
    (
        "remove a field that had a default",
        avro(field("x", "string"), field("y", "long", default=0)),
        ONE,
        (K.FIELD_REMOVED_OPTIONAL,),
        OK,
    ),
    (
        "remove a field that had no default",
        avro(field("x", "string"), field("y", "long")),
        ONE,
        (K.FIELD_REMOVED_REQUIRED,),
        BW,
    ),
    (
        "widen int to long",
        avro(field("x", "int")),
        avro(field("x", "long")),
        (K.TYPE_WIDENED,),
        FW,
    ),
    (
        "widen int to double",
        avro(field("x", "int")),
        avro(field("x", "double")),
        (K.TYPE_WIDENED,),
        FW,
    ),
    (
        "widen float to double",
        avro(field("x", "float")),
        avro(field("x", "double")),
        (K.TYPE_WIDENED,),
        FW,
    ),
    (
        "narrow long to int",
        avro(field("x", "long")),
        avro(field("x", "int")),
        (K.TYPE_NARROWED,),
        BW,
    ),
    (
        "narrow double to float",
        avro(field("x", "double")),
        avro(field("x", "float")),
        (K.TYPE_NARROWED,),
        BW,
    ),
    (
        "string to bytes promotes both ways",
        ONE,
        avro(field("x", "bytes")),
        (K.TYPE_INTERCHANGEABLE,),
        OK,
    ),
    (
        "bytes to string promotes both ways",
        avro(field("x", "bytes")),
        ONE,
        (K.TYPE_INTERCHANGEABLE,),
        OK,
    ),
    (
        "string to long promotes neither way",
        ONE,
        avro(field("x", "long")),
        (K.TYPE_CHANGED,),
        BOTH,
    ),
    (
        "boolean to int promotes neither way",
        avro(field("x", "boolean")),
        avro(field("x", "int")),
        (K.TYPE_CHANGED,),
        BOTH,
    ),
    (
        "add nullability, default unchanged",
        avro(field("x", "string", default="a")),
        avro(field("x", ["null", "string"], default="a")),
        (K.NULLABILITY_ADDED,),
        FW,
    ),
    (
        "remove nullability, default unchanged",
        avro(field("x", ["null", "string"], default="a")),
        avro(field("x", "string", default="a")),
        (K.NULLABILITY_REMOVED,),
        BW,
    ),
    (
        "append an enum symbol with no fallback",
        avro(field("c", enum("CARD", "WIRE"))),
        avro(field("c", enum("CARD", "WIRE", "ACH"))),
        (K.ENUM_SYMBOL_ADDED,),
        FW,
    ),
    (
        "append an enum symbol when the old reader has a fallback",
        avro(field("c", enum("CARD", "WIRE", default="CARD"))),
        avro(field("c", enum("CARD", "WIRE", "ACH", default="CARD"))),
        (K.ENUM_SYMBOL_ADDED_WITH_FALLBACK,),
        OK,
    ),
    (
        "drop an enum symbol with no fallback",
        avro(field("c", enum("CARD", "WIRE", "ACH"))),
        avro(field("c", enum("CARD", "WIRE"))),
        (K.ENUM_SYMBOL_REMOVED,),
        BW,
    ),
    (
        "drop an enum symbol and add a fallback in the same change",
        avro(field("c", enum("CARD", "WIRE", "ACH"))),
        avro(field("c", enum("CARD", "WIRE", default="CARD"))),
        (K.ENUM_SYMBOL_REMOVED_WITH_FALLBACK, K.ENUM_FALLBACK_ADDED),
        OK,
    ),
    (
        "reorder two enum symbols",
        avro(field("c", enum("CARD", "WIRE"))),
        avro(field("c", enum("WIRE", "CARD"))),
        (K.ENUM_SYMBOLS_REINDEXED,),
        BOTH,
    ),
    (
        "insert an enum symbol at the front",
        avro(field("c", enum("CARD", "WIRE"))),
        avro(field("c", enum("ACH", "CARD", "WIRE"))),
        (K.ENUM_SYMBOL_ADDED, K.ENUM_SYMBOLS_REINDEXED),
        BOTH,
    ),
    (
        "add an enum fallback on its own",
        avro(field("c", enum("CARD", "WIRE"))),
        avro(field("c", enum("CARD", "WIRE", default="CARD"))),
        (K.ENUM_FALLBACK_ADDED,),
        OK,
    ),
    (
        "remove an enum fallback on its own",
        avro(field("c", enum("CARD", "WIRE", default="CARD"))),
        avro(field("c", enum("CARD", "WIRE"))),
        (K.ENUM_FALLBACK_REMOVED,),
        OK,
    ),
    (
        "change a default",
        avro(field("x", "long", default=0)),
        avro(field("x", "long", default=-1)),
        (K.DEFAULT_CHANGED,),
        OK,
    ),
    (
        "add a default",
        avro(field("x", "long")),
        avro(field("x", "long", default=0)),
        (K.DEFAULT_ADDED,),
        OK,
    ),
    (
        "remove a default",
        avro(field("x", "long", default=0)),
        avro(field("x", "long")),
        (K.DEFAULT_REMOVED,),
        OK,
    ),
    (
        "change the doc text",
        avro(field("x", "string", doc="the old words")),
        avro(field("x", "string", doc="the new words")),
        (K.DOC_CHANGED,),
        OK,
    ),
    (
        "reorder two fields",
        avro(field("x", "string"), field("y", "long")),
        avro(field("y", "long"), field("x", "string")),
        (K.FIELD_REORDERED,),
        OK,
    ),
    (
        "rename with an alias",
        avro(field("amount", "long")),
        avro(field("amount_minor", "long", aliases=["amount"])),
        (K.FIELD_RENAMED_WITH_ALIAS,),
        FW,
    ),
    (
        "rename with an alias and widen at the same time",
        avro(field("amount", "int")),
        avro(field("amount_minor", "long", aliases=["amount"])),
        (K.FIELD_RENAMED_WITH_ALIAS, K.TYPE_WIDENED),
        FW,
    ),
    (
        "rename with no alias is a removal plus an addition",
        avro(field("amount", "long")),
        avro(field("amount_minor", "long")),
        (K.FIELD_REMOVED_REQUIRED, K.FIELD_ADDED_REQUIRED, K.LIKELY_RENAME),
        BW,
    ),
    (
        "rename with no alias where the new field has a default",
        avro(field("amount", "long")),
        avro(field("amount_minor", "long", default=0)),
        (K.FIELD_REMOVED_REQUIRED, K.FIELD_ADDED_OPTIONAL, K.LIKELY_RENAME),
        BW,
    ),
    (
        "widen the item type of an array",
        avro(field("xs", {"type": "array", "items": "int"})),
        avro(field("xs", {"type": "array", "items": "long"})),
        (K.TYPE_WIDENED,),
        FW,
    ),
    (
        "narrow the value type of a map",
        avro(field("m", {"type": "map", "values": "long"})),
        avro(field("m", {"type": "map", "values": "int"})),
        (K.TYPE_NARROWED,),
        BW,
    ),
    (
        "turn an array into a map",
        avro(field("xs", {"type": "array", "items": "string"})),
        avro(field("xs", {"type": "map", "values": "string"})),
        (K.CONTAINER_KIND_CHANGED,),
        BOTH,
    ),
    (
        "turn a record into an array",
        avro(
            field(
                "r",
                {"type": "record", "name": "Inner", "fields": [{"name": "a", "type": "string"}]},
            )
        ),
        avro(field("r", {"type": "array", "items": "string"})),
        (K.CONTAINER_KIND_CHANGED,),
        BOTH,
    ),
]


@pytest.mark.parametrize(
    ("old", "new", "kinds", "directions"),
    [pytest.param(*case[1:], id=case[0]) for case in CASES],
)
def test_the_rule_table(old, new, kinds, directions):
    assert tuple(c.kind for c in changes(old, new)) == kinds
    assert broken(old, new) == directions


def test_every_rule_in_the_table_has_at_least_one_case():
    """A rule nobody wrote a case for is a rule nobody has checked."""
    seen: set[ChangeKind] = set()
    for _id, old, new, _kinds, _directions in CASES:
        seen |= {c.kind for c in changes(old, new)}
    assert seen == set(RULES)


def test_strict_promotes_the_quiet_enum_fallback_cases():
    old = avro(field("c", enum("CARD", "WIRE", default="CARD")))
    new = avro(field("c", enum("CARD", "WIRE", "ACH", default="CARD")))
    assert broken(old, new) == OK
    assert broken(old, new, strict=True) == FW


def test_strict_promotes_a_changed_default():
    old = avro(field("x", "long", default=0))
    new = avro(field("x", "long", default=-1))
    assert broken(old, new) == OK
    assert broken(old, new, strict=True) == BW


def test_strict_leaves_a_cosmetic_change_alone():
    old = avro(field("x", "string", doc="before"))
    new = avro(field("x", "string", doc="after"))
    assert broken(old, new, strict=True) == OK


def test_a_hint_never_breaks_anything_by_itself():
    """The removal and the addition carry the breakage; the hint is advice."""
    found = changes(avro(field("a", "long")), avro(field("b", "long")))
    hint = next(c for c in found if c.hint)
    assert hint.breaks == frozenset()
    assert hint.strict_breaks == frozenset()


def test_an_alias_is_not_a_rename_when_the_old_name_is_still_there():
    old = avro(field("amount", "long"))
    new = avro(field("amount", "long", aliases=["amount"]), field("other", "long", default=0))
    assert [c.kind for c in changes(old, new)] == [K.FIELD_ADDED_OPTIONAL]


def test_an_alias_claiming_a_name_that_never_existed_is_ignored():
    old = avro(field("amount", "long"))
    new = avro(field("amount", "long"), field("fee", "long", default=0, aliases=["nonesuch"]))
    assert [c.kind for c in changes(old, new)] == [K.FIELD_ADDED_OPTIONAL]


def test_two_removals_and_two_additions_produce_no_rename_hint():
    """The hint only makes sense when there is exactly one candidate pairing."""
    old = avro(field("a", "long"), field("b", "long"))
    new = avro(field("c", "long"), field("d", "long"))
    assert not [c for c in changes(old, new) if c.hint]


def test_a_rename_hint_needs_the_types_to_match():
    old = avro(field("a", "long"))
    new = avro(field("b", "string"))
    assert not [c for c in changes(old, new) if c.hint]


def test_appending_an_enum_symbol_does_not_reindex_the_existing_ones():
    found = changes(
        avro(field("c", enum("CARD", "WIRE"))), avro(field("c", enum("CARD", "WIRE", "ACH")))
    )
    assert K.ENUM_SYMBOLS_REINDEXED not in {c.kind for c in found}


def test_a_change_inside_a_nested_record_carries_the_dotted_path():
    inner_old = {"type": "record", "name": "I", "fields": [{"name": "a", "type": "string"}]}
    inner_new = {
        "type": "record",
        "name": "I",
        "fields": [{"name": "a", "type": "string"}, {"name": "b", "type": "long"}],
    }
    found = changes(avro(field("outer", inner_old)), avro(field("outer", inner_new)))
    assert [(c.kind, c.path) for c in found] == [(K.FIELD_ADDED_REQUIRED, "outer.b")]


def test_a_change_inside_an_array_of_records_carries_the_container_marker():
    item_old = {"type": "record", "name": "H", "fields": [{"name": "score", "type": "float"}]}
    item_new = {"type": "record", "name": "H", "fields": [{"name": "score", "type": "double"}]}
    found = changes(
        avro(field("hits", {"type": "array", "items": item_old})),
        avro(field("hits", {"type": "array", "items": item_new})),
    )
    assert [(c.kind, c.path) for c in found] == [(K.TYPE_WIDENED, "hits[].score")]
    assert found[0].root_field == "hits"


def test_a_logical_type_on_its_own_is_not_a_change():
    old = avro(field("at", {"type": "long"}))
    new = avro(field("at", {"type": "long", "logicalType": "timestamp-millis"}))
    assert changes(old, new) == []


def test_every_change_can_explain_itself():
    for _id, old, new, _kinds, _directions in CASES:
        for c in changes(old, new):
            assert c.why == RULES[c.kind].why
            assert len(c.why) > 20
            assert c.detail
