"""Builders for the schema pairs the rule tests are made of.

Every schema in this suite is written here, in Python, a few fields at a time.
None of it is loaded from a running system and none of it needs one: the whole
product is a comparison between two documents.
"""

from __future__ import annotations

from typing import Any

import pytest

from contractlint.loader import load_schema_obj
from contractlint.types import SchemaDoc


def avro(*fields: dict[str, Any], name: str = "Rec") -> dict[str, Any]:
    """An Avro record wrapper, so a case is one line of fields."""
    return {"type": "record", "name": name, "fields": list(fields)}


def field(name: str, type_: Any, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name, "type": type_}
    out.update(extra)
    return out


def enum(*symbols: str, default: str | None = None, name: str = "E") -> dict[str, Any]:
    node: dict[str, Any] = {"type": "enum", "name": name, "symbols": list(symbols)}
    if default is not None:
        node["default"] = default
    return node


def ts(unit: str) -> dict[str, Any]:
    """A long annotated as a timestamp at one unit: millis, micros or nanos."""
    return {"type": "long", "logicalType": f"timestamp-{unit}"}


def dec(precision: int, scale: int) -> dict[str, Any]:
    """A decimal over bytes, with the two numbers that give it a value."""
    return {"type": "bytes", "logicalType": "decimal", "precision": precision, "scale": scale}


def doc(obj: dict[str, Any], *, subject: str = "test.subject", version: int = 1) -> SchemaDoc:
    """Normalize an Avro-style dict into a SchemaDoc."""
    return load_schema_obj(obj, dialect="avro", subject=subject, version=version)


def jdoc(obj: dict[str, Any], *, subject: str = "test.subject", version: int = 1) -> SchemaDoc:
    """Normalize a JSON Schema dict into a SchemaDoc."""
    return load_schema_obj(obj, dialect="json-schema", subject=subject, version=version)


@pytest.fixture
def base() -> SchemaDoc:
    """A three field record most rule cases mutate one field of."""
    return doc(
        avro(
            field("id", "string"),
            field("amount", "long"),
            field("note", ["null", "string"], default=None),
        )
    )
