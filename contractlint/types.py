"""The normalized schema model.

Both input dialects are flattened into this before anything is compared. The
rules then have one shape to reason about, and a dialect is a loader rather
than a second rule table.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class Direction(StrEnum):
    """Which way data flows past a schema change.

    BACKWARD: a reader on the new schema can read data written with the old one.
    FORWARD:  a reader on the old schema can read data written with the new one.

    Neither word means anything on its own, which is why every rule in this
    repository is stated as a direction and never as "safe".
    """

    BACKWARD = "backward"
    FORWARD = "forward"


BOTH: frozenset[Direction] = frozenset(Direction)
NEITHER: frozenset[Direction] = frozenset()


class Kind(StrEnum):
    """Primitive and container kinds, named the way Avro names them."""

    BOOLEAN = "boolean"
    INT = "int"
    LONG = "long"
    FLOAT = "float"
    DOUBLE = "double"
    STRING = "string"
    BYTES = "bytes"
    ENUM = "enum"
    RECORD = "record"
    ARRAY = "array"
    MAP = "map"


#: What a writer's kind can be read as. Straight out of the Avro specification's
#: schema resolution section, and deliberately not extended: a checker that
#: invents a promotion the serializer will not perform passes a change that
#: fails in production.
PROMOTIONS: dict[Kind, frozenset[Kind]] = {
    Kind.INT: frozenset({Kind.INT, Kind.LONG, Kind.FLOAT, Kind.DOUBLE}),
    Kind.LONG: frozenset({Kind.LONG, Kind.FLOAT, Kind.DOUBLE}),
    Kind.FLOAT: frozenset({Kind.FLOAT, Kind.DOUBLE}),
    Kind.DOUBLE: frozenset({Kind.DOUBLE}),
    Kind.STRING: frozenset({Kind.STRING, Kind.BYTES}),
    Kind.BYTES: frozenset({Kind.BYTES, Kind.STRING}),
    Kind.BOOLEAN: frozenset({Kind.BOOLEAN}),
    Kind.ENUM: frozenset({Kind.ENUM}),
    Kind.RECORD: frozenset({Kind.RECORD}),
    Kind.ARRAY: frozenset({Kind.ARRAY}),
    Kind.MAP: frozenset({Kind.MAP}),
}

CONTAINER_KINDS: frozenset[Kind] = frozenset({Kind.RECORD, Kind.ARRAY, Kind.MAP})


def readable_as(writer: Kind, reader: Kind) -> bool:
    """True when a value written as `writer` can be read as `reader`."""
    return reader in PROMOTIONS[writer]


class TypeNode(BaseModel):
    """A type, with nullability hoisted out of the union it came from."""

    model_config = ConfigDict(frozen=True)

    kind: Kind
    nullable: bool = False
    #: enum only, in declaration order
    symbols: tuple[str, ...] = ()
    #: enum only: the symbol a reader falls back to for a symbol it does not know
    enum_default: str | None = None
    #: record only
    fields: tuple[FieldNode, ...] = ()
    #: array only
    items: TypeNode | None = None
    #: map only, the value type; map keys are strings in both dialects
    values: TypeNode | None = None
    #: carried for display and never compared. A logical type is an annotation
    #: over a physical one, and two schemas that disagree about it still
    #: serialize and deserialize identically.
    logical: str | None = None
    #: the declared name of a record or enum, for messages
    named: str | None = None

    def label(self) -> str:
        base = self.kind.value
        if self.kind is Kind.ENUM:
            base = f"enum{{{','.join(self.symbols)}}}"
        elif self.kind is Kind.ARRAY and self.items is not None:
            base = f"array<{self.items.label()}>"
        elif self.kind is Kind.MAP and self.values is not None:
            base = f"map<{self.values.label()}>"
        elif self.kind is Kind.RECORD:
            base = f"record({len(self.fields)} fields)"
        if self.logical:
            base = f"{base}/{self.logical}"
        return f"{base}?" if self.nullable else base

    def field_map(self) -> dict[str, FieldNode]:
        return {f.name: f for f in self.fields}


class FieldNode(BaseModel):
    """A record field.

    `has_default` is separate from `default` because a default of null, 0 or ""
    is a default, and `default is None` cannot tell those apart from a field
    that declared nothing.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    type: TypeNode
    has_default: bool = False
    default: Any = None
    #: names this field used to go by. A reader matches a writer's field by
    #: alias when the names differ.
    aliases: tuple[str, ...] = ()
    doc: str | None = None

    def required(self) -> bool:
        return not self.has_default


class SchemaDoc(BaseModel):
    """One version of one subject, normalized."""

    model_config = ConfigDict(frozen=True)

    subject: str
    version: int | None = None
    dialect: str
    root: TypeNode
    source_path: str | None = None
    #: decisions the loader made that the file did not state. Printed by
    #: `contractlint show`, because a rule applied to an assumption is only as
    #: good as the assumption.
    assumptions: tuple[str, ...] = ()

    def label(self) -> str:
        return f"{self.subject}" if self.version is None else f"{self.subject} v{self.version}"


TypeNode.model_rebuild()
FieldNode.model_rebuild()
