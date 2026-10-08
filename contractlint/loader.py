"""Dialect loaders.

Two in, one model out. Avro-style `.avsc` records and a subset of JSON Schema
draft 2020-12. Neither loader guesses quietly: anything it has to decide for
you lands in `SchemaDoc.assumptions`, and anything it cannot represent raises.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contractlint.errors import SchemaError
from contractlint.types import FieldNode, Kind, SchemaDoc, TypeNode

AVRO_PRIMITIVES: dict[str, Kind] = {
    "boolean": Kind.BOOLEAN,
    "int": Kind.INT,
    "long": Kind.LONG,
    "float": Kind.FLOAT,
    "double": Kind.DOUBLE,
    "string": Kind.STRING,
    "bytes": Kind.BYTES,
}

JSON_SCHEMA_PRIMITIVES: dict[str, Kind] = {
    "boolean": Kind.BOOLEAN,
    "string": Kind.STRING,
    # JSON Schema has no integer width. Both map to the wider Avro kind, which
    # is recorded as an assumption on every JSON Schema document: a subject in
    # this dialect cannot express int and so can never trip the int to long
    # widening rule, in either direction.
    "integer": Kind.LONG,
    "number": Kind.DOUBLE,
}

AVRO_SUFFIXES = (".avsc",)
JSON_SCHEMA_SUFFIXES = (".schema.json",)

#: Keywords this loader will not pretend to understand. A $ref or a oneOf
#: changes what a document means, and a checker that ignores one compares the
#: wrong thing while reporting success.
UNSUPPORTED_JSON_KEYWORDS = ("$ref", "oneOf", "anyOf", "allOf", "not", "if", "patternProperties")


class _Assumptions:
    """Collects loader decisions in order, without duplicates."""

    def __init__(self) -> None:
        self._seen: list[str] = []

    def add(self, note: str) -> None:
        if note not in self._seen:
            self._seen.append(note)

    def frozen(self) -> tuple[str, ...]:
        return tuple(self._seen)


def _avro_type(node: Any, *, path: str, source: str | None) -> TypeNode:
    if isinstance(node, str):
        if node == "null":
            raise SchemaError("a bare null type has no value to compare", path=path, source=source)
        if node not in AVRO_PRIMITIVES:
            raise SchemaError(
                f"unknown or unsupported Avro type {node!r}; a named type reference "
                "is not resolved",
                path=path,
                source=source,
            )
        return TypeNode(kind=AVRO_PRIMITIVES[node])

    if isinstance(node, list):
        if not node:
            raise SchemaError("an empty union has no type", path=path, source=source)
        nullable = "null" in node
        branches = [b for b in node if b != "null"]
        if len(branches) != 1:
            raise SchemaError(
                f"only a two-branch union with null is supported, got {len(branches)} "
                "non-null branches; a real union needs per-branch resolution and this "
                "checker does not do it",
                path=path,
                source=source,
            )
        inner = _avro_type(branches[0], path=path, source=source)
        return inner.model_copy(update={"nullable": nullable})

    if not isinstance(node, dict):
        raise SchemaError(
            f"a type must be a string, list or object, got {type(node).__name__}",
            path=path,
            source=source,
        )

    declared = node.get("type")
    if declared is None:
        raise SchemaError("object type with no 'type' key", path=path, source=source)
    if isinstance(declared, (list, dict)):
        # {"type": ["null", "string"]} is legal Avro and means the same as the
        # bare union, so unwrap it rather than refusing.
        return _avro_type(declared, path=path, source=source)

    logical = node.get("logicalType")

    if declared == "enum":
        symbols = node.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            raise SchemaError("enum with no symbols", path=path, source=source)
        enum_default = node.get("default")
        if enum_default is not None and enum_default not in symbols:
            raise SchemaError(
                f"enum default {enum_default!r} is not one of its own symbols",
                path=path,
                source=source,
            )
        return TypeNode(
            kind=Kind.ENUM,
            symbols=tuple(str(s) for s in symbols),
            enum_default=enum_default,
            named=node.get("name"),
        )

    if declared == "array":
        if "items" not in node:
            raise SchemaError("array with no items type", path=path, source=source)
        return TypeNode(
            kind=Kind.ARRAY,
            items=_avro_type(node["items"], path=f"{path}[]", source=source),
        )

    if declared == "map":
        if "values" not in node:
            raise SchemaError("map with no values type", path=path, source=source)
        return TypeNode(
            kind=Kind.MAP,
            values=_avro_type(node["values"], path=f"{path}{{}}", source=source),
        )

    if declared == "record":
        return TypeNode(
            kind=Kind.RECORD,
            named=node.get("name"),
            fields=_avro_fields(node, path=path, source=source),
        )

    if declared in AVRO_PRIMITIVES:
        return TypeNode(kind=AVRO_PRIMITIVES[declared], logical=logical)

    raise SchemaError(f"unsupported Avro type {declared!r}", path=path, source=source)


def _avro_fields(record: Any, *, path: str, source: str | None) -> tuple[FieldNode, ...]:
    raw = record.get("fields")
    if not isinstance(raw, list):
        raise SchemaError("record with no fields list", path=path or ".", source=source)
    out: list[FieldNode] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict) or "name" not in entry:
            raise SchemaError("a field needs a name", path=path or ".", source=source)
        name = str(entry["name"])
        if name in seen:
            raise SchemaError(f"duplicate field {name!r}", path=path or ".", source=source)
        seen.add(name)
        child = f"{path}.{name}" if path else name
        if "type" not in entry:
            raise SchemaError("a field needs a type", path=child, source=source)
        out.append(
            FieldNode(
                name=name,
                type=_avro_type(entry["type"], path=child, source=source),
                has_default="default" in entry,
                default=entry.get("default"),
                aliases=tuple(str(a) for a in entry.get("aliases", ())),
                doc=entry.get("doc"),
            )
        )
    return tuple(out)


def _load_avro(obj: Any, *, source: str | None) -> tuple[TypeNode, tuple[str, ...]]:
    if not isinstance(obj, dict) or obj.get("type") != "record":
        raise SchemaError("the top level of a subject must be a record", source=source)
    root = TypeNode(
        kind=Kind.RECORD,
        named=obj.get("name"),
        fields=_avro_fields(obj, path="", source=source),
    )
    return root, ()


def _json_schema_type(node: Any, *, path: str, source: str | None, notes: _Assumptions) -> TypeNode:
    if not isinstance(node, dict):
        raise SchemaError("a property must be an object", path=path, source=source)
    for keyword in UNSUPPORTED_JSON_KEYWORDS:
        if keyword in node:
            raise SchemaError(
                f"{keyword} is not supported; this checker compares a flat normalized "
                "model and cannot tell what two indirect schemas have in common",
                path=path,
                source=source,
            )

    declared = node.get("type")
    nullable = False
    if isinstance(declared, list):
        names = [t for t in declared if t != "null"]
        nullable = "null" in declared
        if len(names) != 1:
            raise SchemaError(
                f"a type union needs exactly one non-null member, got {len(names)}",
                path=path,
                source=source,
            )
        declared = names[0]
    if declared is None:
        raise SchemaError("a property needs a type", path=path, source=source)

    if "enum" in node:
        symbols = node["enum"]
        if not isinstance(symbols, list) or not symbols:
            raise SchemaError("enum with no values", path=path, source=source)
        plain = [s for s in symbols if s is not None]
        if len(plain) != len(symbols):
            nullable = True
            notes.add("a null inside an enum list is read as nullability, not as a symbol")
        return TypeNode(
            kind=Kind.ENUM,
            symbols=tuple(str(s) for s in plain),
            nullable=nullable,
        )

    if declared == "array":
        items = node.get("items")
        if items is None:
            raise SchemaError("array with no items schema", path=path, source=source)
        return TypeNode(
            kind=Kind.ARRAY,
            nullable=nullable,
            items=_json_schema_type(items, path=f"{path}[]", source=source, notes=notes),
        )

    if declared == "object":
        if "properties" in node:
            return TypeNode(
                kind=Kind.RECORD,
                nullable=nullable,
                fields=_json_schema_fields(node, path=path, source=source, notes=notes),
            )
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            return TypeNode(
                kind=Kind.MAP,
                nullable=nullable,
                values=_json_schema_type(extra, path=f"{path}{{}}", source=source, notes=notes),
            )
        raise SchemaError(
            "an object needs either properties or an additionalProperties schema; "
            "an open object has no fields to compare",
            path=path,
            source=source,
        )

    if declared in JSON_SCHEMA_PRIMITIVES:
        if declared == "integer":
            notes.add("JSON Schema integer is loaded as Avro long, since the dialect has no width")
        if declared == "number":
            notes.add("JSON Schema number is loaded as Avro double")
        return TypeNode(
            kind=JSON_SCHEMA_PRIMITIVES[declared],
            nullable=nullable,
            logical=node.get("format"),
        )

    raise SchemaError(f"unsupported JSON Schema type {declared!r}", path=path, source=source)


def _json_schema_fields(
    node: Any, *, path: str, source: str | None, notes: _Assumptions
) -> tuple[FieldNode, ...]:
    properties = node.get("properties")
    if not isinstance(properties, dict):
        raise SchemaError("object with no properties", path=path or ".", source=source)
    required = node.get("required", [])
    if not isinstance(required, list):
        raise SchemaError("required must be a list", path=path or ".", source=source)
    required_names = {str(r) for r in required}
    unknown = required_names - set(properties)
    if unknown:
        raise SchemaError(
            f"required names a property that does not exist: {sorted(unknown)}",
            path=path or ".",
            source=source,
        )

    out: list[FieldNode] = []
    for name, prop in properties.items():
        child = f"{path}.{name}" if path else name
        loaded = _json_schema_type(prop, path=child, source=source, notes=notes)
        is_required = name in required_names
        has_default = "default" in prop
        default = prop.get("default")

        if is_required and has_default:
            notes.add(
                "a default on a required property is ignored: required means the value "
                "is present, so the default is never the value a reader sees"
            )
            has_default = False
            default = None
        elif not is_required and not has_default:
            # The honest mapping. An optional property may be absent from a
            # payload, so a reader has to cope with its absence, which in the
            # normalized model is a nullable field defaulting to null.
            notes.add(
                "an optional property with no default is loaded as nullable with a null "
                "default, so a nullability change on an optional property is invisible here"
            )
            loaded = loaded.model_copy(update={"nullable": True})
            has_default = True
            default = None

        out.append(
            FieldNode(
                name=str(name),
                type=loaded,
                has_default=has_default,
                default=default,
                aliases=tuple(str(a) for a in prop.get("x-aliases", ())),
                doc=prop.get("description"),
            )
        )
    return tuple(out)


def _load_json_schema(obj: Any, *, source: str | None) -> tuple[TypeNode, tuple[str, ...]]:
    if not isinstance(obj, dict) or obj.get("type") != "object":
        raise SchemaError("the top level of a subject must be an object", source=source)
    notes = _Assumptions()
    root = TypeNode(
        kind=Kind.RECORD,
        fields=_json_schema_fields(obj, path="", source=source, notes=notes),
    )
    return root, notes.frozen()


def dialect_for_path(path: str | Path) -> str:
    name = Path(path).name
    if name.endswith(JSON_SCHEMA_SUFFIXES):
        return "json-schema"
    if name.endswith(AVRO_SUFFIXES):
        return "avro"
    raise SchemaError(
        f"cannot tell the dialect of {name!r}; use .avsc or .schema.json",
        source=str(path),
    )


def load_schema_obj(
    obj: Any,
    *,
    dialect: str,
    subject: str,
    version: int | None = None,
    source_path: str | None = None,
) -> SchemaDoc:
    """Normalize an already-parsed document."""
    if dialect == "avro":
        root, assumptions = _load_avro(obj, source=source_path)
    elif dialect == "json-schema":
        root, assumptions = _load_json_schema(obj, source=source_path)
    else:
        raise SchemaError(f"unknown dialect {dialect!r}")
    return SchemaDoc(
        subject=subject,
        version=version,
        dialect=dialect,
        root=root,
        source_path=source_path,
        assumptions=assumptions,
    )


def load_schema_file(
    path: str | Path, *, subject: str | None = None, version: int | None = None
) -> SchemaDoc:
    """Read and normalize one schema file.

    The dialect comes from the suffix. The subject defaults to the containing
    directory's name, which is how the on-disk registry is laid out.
    """
    p = Path(path)
    dialect = dialect_for_path(p)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"cannot read the file: {exc.strerror}", source=str(p)) from exc
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"not valid JSON: {exc.msg} on line {exc.lineno}", source=str(p)) from exc
    return load_schema_obj(
        obj,
        dialect=dialect,
        subject=subject or p.parent.name,
        version=version,
        source_path=str(p),
    )
