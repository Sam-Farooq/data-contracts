"""Walk two normalized schemas and emit what changed.

The walk is name-based and recursive. Nothing here decides whether a change is
acceptable: it classifies, and `rules.RULES` holds the consequence. That split
is what makes the rule table reviewable on its own.
"""

from __future__ import annotations

from contractlint.rules import Change, ChangeKind, change
from contractlint.types import (
    CONTAINER_KINDS,
    FieldNode,
    Kind,
    SchemaDoc,
    TypeNode,
    readable_as,
    split_logical,
    unit_factor,
)


def diff_schemas(old: SchemaDoc, new: SchemaDoc) -> list[Change]:
    """Every difference between two versions of a subject, in walk order."""
    changes: list[Change] = []
    _diff_record(old.root, new.root, path="", out=changes, dialect=new.dialect)
    return changes


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def _alias_matches(
    new_fields: tuple[FieldNode, ...], old_names: set[str], new_names: set[str]
) -> dict[str, str]:
    """Map an old field name to the new name that claims it through an alias.

    An alias only counts as a rename when the old name is gone from the new
    schema. A field that keeps its name and also lists it as an alias has not
    been renamed.
    """
    matched: dict[str, str] = {}
    for f in new_fields:
        if f.name in old_names:
            continue
        for alias in f.aliases:
            if alias in old_names and alias not in new_names and alias not in matched:
                matched[alias] = f.name
                break
    return matched


def _diff_record(
    old: TypeNode, new: TypeNode, *, path: str, out: list[Change], dialect: str
) -> None:
    old_map = old.field_map()
    new_map = new.field_map()
    renamed = _alias_matches(new.fields, set(old_map), set(new_map))

    removed = [n for n in old_map if n not in new_map and n not in renamed]
    added = [n for n in new_map if n not in old_map and n not in set(renamed.values())]

    for name in removed:
        field = old_map[name]
        kind = (
            ChangeKind.FIELD_REMOVED_OPTIONAL
            if field.has_default
            else ChangeKind.FIELD_REMOVED_REQUIRED
        )
        default = f", default {field.default!r}" if field.has_default else ", no default"
        out.append(change(kind, _join(path, name), f"{field.type.label()}{default}, removed"))

    for name in added:
        field = new_map[name]
        kind = (
            ChangeKind.FIELD_ADDED_OPTIONAL
            if field.has_default
            else ChangeKind.FIELD_ADDED_REQUIRED
        )
        default = f", default {field.default!r}" if field.has_default else ", no default"
        out.append(change(kind, _join(path, name), f"added as {field.type.label()}{default}"))

    if len(removed) == 1 and len(added) == 1:
        gone, arrived = old_map[removed[0]], new_map[added[0]]
        if gone.type == arrived.type:
            out.append(
                change(
                    ChangeKind.LIKELY_RENAME,
                    _join(path, arrived.name),
                    f"{removed[0]} left and {added[0]} arrived, both {gone.type.label()}",
                )
            )

    for old_name, new_name in renamed.items():
        out.append(
            change(
                ChangeKind.FIELD_RENAMED_WITH_ALIAS,
                _join(path, new_name),
                f"{old_name} -> {new_name}, matched by alias",
            )
        )
        _diff_field(old_map[old_name], new_map[new_name], path=path, out=out, dialect=dialect)

    for name, new_field in new_map.items():
        if name in old_map:
            _diff_field(old_map[name], new_field, path=path, out=out, dialect=dialect)

    kept_old = [n for n in old_map if n in new_map]
    kept_new = [n for n in new_map if n in old_map]
    if kept_old != kept_new:
        out.append(
            change(
                ChangeKind.FIELD_REORDERED,
                path or ".",
                f"{', '.join(kept_old)} -> {', '.join(kept_new)}",
            )
        )


def _diff_field(
    old: FieldNode, new: FieldNode, *, path: str, out: list[Change], dialect: str
) -> None:
    here = _join(path, new.name)
    _diff_type(old.type, new.type, path=here, out=out, dialect=dialect)

    if old.has_default and new.has_default and old.default != new.default:
        out.append(
            change(
                ChangeKind.DEFAULT_CHANGED,
                here,
                f"default {old.default!r} -> {new.default!r}",
            )
        )
    elif not old.has_default and new.has_default:
        out.append(change(ChangeKind.DEFAULT_ADDED, here, f"default {new.default!r} added"))
    elif old.has_default and not new.has_default:
        out.append(change(ChangeKind.DEFAULT_REMOVED, here, f"default {old.default!r} removed"))

    if old.doc != new.doc:
        out.append(change(ChangeKind.DOC_CHANGED, here, "doc text changed"))


def _diff_type(old: TypeNode, new: TypeNode, *, path: str, out: list[Change], dialect: str) -> None:
    if old.nullable and not new.nullable:
        out.append(change(ChangeKind.NULLABILITY_REMOVED, path, f"{old.label()} -> {new.label()}"))
    elif not old.nullable and new.nullable:
        out.append(change(ChangeKind.NULLABILITY_ADDED, path, f"{old.label()} -> {new.label()}"))

    _diff_logical(old, new, path=path, out=out)

    if old.kind is not new.kind:
        if old.kind in CONTAINER_KINDS or new.kind in CONTAINER_KINDS:
            out.append(
                change(
                    ChangeKind.CONTAINER_KIND_CHANGED,
                    path,
                    f"{old.kind.value} -> {new.kind.value}",
                )
            )
            return
        _diff_primitive_kind(old, new, path=path, out=out)
        return

    if old.kind is Kind.RECORD:
        _diff_record(old, new, path=path, out=out, dialect=dialect)
    elif old.kind is Kind.ARRAY and old.items is not None and new.items is not None:
        _diff_type(old.items, new.items, path=f"{path}[]", out=out, dialect=dialect)
    elif old.kind is Kind.MAP and old.values is not None and new.values is not None:
        _diff_type(old.values, new.values, path=f"{path}{{}}", out=out, dialect=dialect)
    elif old.kind is Kind.ENUM:
        _diff_enum(old, new, path=path, out=out, dialect=dialect)


def _diff_logical(old: TypeNode, new: TypeNode, *, path: str, out: list[Change]) -> None:
    """Compare the annotation sitting over the physical type.

    This is the one part of the walk the kind comparison cannot do for us. A
    logical type does not change the bytes, so two schemas that disagree about
    it resolve perfectly and hand a reader a wrong value. It runs before the
    kind comparison and independently of it, the same way nullability does,
    because an annotation can change with or without the type under it.
    """
    if old.logical == new.logical:
        _diff_decimal(old, new, path=path, out=out)
        return
    if old.logical is None:
        out.append(change(ChangeKind.LOGICAL_TYPE_ADDED, path, f"{old.label()} -> {new.label()}"))
        return
    if new.logical is None:
        out.append(change(ChangeKind.LOGICAL_TYPE_REMOVED, path, f"{old.label()} -> {new.label()}"))
        return

    old_family, old_unit = split_logical(old.logical)
    new_family, new_unit = split_logical(new.logical)
    if old_family == new_family and old_unit is not None and new_unit is not None:
        factor = unit_factor(old_unit, new_unit)
        out.append(
            change(
                ChangeKind.LOGICAL_UNIT_CHANGED,
                path,
                f"{old.logical} -> {new.logical}, a factor of {factor} on every value "
                "already written",
            )
        )
        return
    out.append(change(ChangeKind.LOGICAL_TYPE_CHANGED, path, f"{old.logical} -> {new.logical}"))


def _diff_decimal(old: TypeNode, new: TypeNode, *, path: str, out: list[Change]) -> None:
    """Scale and precision, for the two sides that agree they are decimals.

    Only reached when the annotation itself did not change, so either both sides
    are decimals and carry digits, or neither does and there is nothing here.
    """
    if old.scale is None or new.scale is None or old.precision is None or new.precision is None:
        return
    if old.scale != new.scale:
        factor = 10 ** abs(new.scale - old.scale)
        out.append(
            change(
                ChangeKind.DECIMAL_SCALE_CHANGED,
                path,
                f"scale {old.scale} -> {new.scale}, a factor of {factor} on every value "
                "already written",
            )
        )
    if old.precision != new.precision:
        kind = (
            ChangeKind.DECIMAL_PRECISION_WIDENED
            if new.precision > old.precision
            else ChangeKind.DECIMAL_PRECISION_NARROWED
        )
        out.append(change(kind, path, f"precision {old.precision} -> {new.precision}"))


def _diff_primitive_kind(old: TypeNode, new: TypeNode, *, path: str, out: list[Change]) -> None:
    label = f"{old.kind.value} -> {new.kind.value}"
    old_into_new = readable_as(old.kind, new.kind)
    new_into_old = readable_as(new.kind, old.kind)
    if old_into_new and new_into_old:
        out.append(change(ChangeKind.TYPE_INTERCHANGEABLE, path, label))
    elif old_into_new:
        out.append(change(ChangeKind.TYPE_WIDENED, path, label))
    elif new_into_old:
        out.append(change(ChangeKind.TYPE_NARROWED, path, label))
    else:
        out.append(change(ChangeKind.TYPE_CHANGED, path, label))


def _diff_enum(old: TypeNode, new: TypeNode, *, path: str, out: list[Change], dialect: str) -> None:
    gained = [s for s in new.symbols if s not in old.symbols]
    lost = [s for s in old.symbols if s not in new.symbols]

    if gained:
        kind = (
            ChangeKind.ENUM_SYMBOL_ADDED_WITH_FALLBACK
            if old.enum_default is not None
            else ChangeKind.ENUM_SYMBOL_ADDED
        )
        out.append(change(kind, path, f"symbols added: {', '.join(gained)}"))
    if lost:
        kind = (
            ChangeKind.ENUM_SYMBOL_REMOVED_WITH_FALLBACK
            if new.enum_default is not None
            else ChangeKind.ENUM_SYMBOL_REMOVED
        )
        out.append(change(kind, path, f"symbols removed: {', '.join(lost)}"))

    if old.enum_default is None and new.enum_default is not None:
        out.append(change(ChangeKind.ENUM_FALLBACK_ADDED, path, f"fallback {new.enum_default}"))
    elif old.enum_default is not None and new.enum_default is None:
        out.append(
            change(ChangeKind.ENUM_FALLBACK_REMOVED, path, f"fallback {old.enum_default} removed")
        )

    # Position matters only where the encoding writes the index. JSON Schema has
    # no binary form and writes the symbol itself, so a reorder there is nothing.
    if dialect == "avro":
        for symbol in new.symbols:
            if symbol not in old.symbols:
                continue
            was, now = old.symbols.index(symbol), new.symbols.index(symbol)
            if was != now:
                out.append(
                    change(
                        ChangeKind.ENUM_SYMBOLS_REINDEXED,
                        path,
                        f"{symbol} moved from index {was} to {now}",
                    )
                )
                return
