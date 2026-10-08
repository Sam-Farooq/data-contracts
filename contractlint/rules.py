"""The rule table.

One entry per kind of change, each one stating which direction of reading it
breaks and why. Every entry is derived from schema resolution: a reader walks
its own fields, takes a value from the writer when the names match, falls back
to its own default when they do not, and fails when there is neither. Nothing
in this table is a house style preference.

`strict_breaks` is for the changes where resolution keeps working and a reader
observes a different value than it used to. Those are not compatibility
failures and they are not nothing, so they are reported either way and only
count as failures under --strict.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from contractlint.types import BOTH, NEITHER, Direction

BACKWARD = frozenset({Direction.BACKWARD})
FORWARD = frozenset({Direction.FORWARD})


class ChangeKind(StrEnum):
    FIELD_ADDED_OPTIONAL = "field_added_optional"
    FIELD_ADDED_REQUIRED = "field_added_required"
    FIELD_REMOVED_OPTIONAL = "field_removed_optional"
    FIELD_REMOVED_REQUIRED = "field_removed_required"
    FIELD_RENAMED_WITH_ALIAS = "field_renamed_with_alias"
    FIELD_REORDERED = "field_reordered"
    LIKELY_RENAME = "likely_rename"
    TYPE_WIDENED = "type_widened"
    TYPE_NARROWED = "type_narrowed"
    TYPE_INTERCHANGEABLE = "type_interchangeable"
    TYPE_CHANGED = "type_changed"
    CONTAINER_KIND_CHANGED = "container_kind_changed"
    NULLABILITY_ADDED = "nullability_added"
    NULLABILITY_REMOVED = "nullability_removed"
    ENUM_SYMBOL_ADDED = "enum_symbol_added"
    ENUM_SYMBOL_ADDED_WITH_FALLBACK = "enum_symbol_added_with_fallback"
    ENUM_SYMBOL_REMOVED = "enum_symbol_removed"
    ENUM_SYMBOL_REMOVED_WITH_FALLBACK = "enum_symbol_removed_with_fallback"
    ENUM_FALLBACK_ADDED = "enum_fallback_added"
    ENUM_FALLBACK_REMOVED = "enum_fallback_removed"
    ENUM_SYMBOLS_REINDEXED = "enum_symbols_reindexed"
    DEFAULT_ADDED = "default_added"
    DEFAULT_CHANGED = "default_changed"
    DEFAULT_REMOVED = "default_removed"
    DOC_CHANGED = "doc_changed"


class Rule(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ChangeKind
    breaks: frozenset[Direction]
    strict_breaks: frozenset[Direction] = NEITHER
    why: str
    #: resolution succeeds and a reader sees a value it would not have seen
    quiet: bool = False
    #: no effect on either reader, reported so a diff is complete
    cosmetic: bool = False
    #: not a change in its own right, a remark about the changes around it
    hint: bool = False


def _rule(kind: ChangeKind, breaks: frozenset[Direction], why: str, **kw: object) -> Rule:
    return Rule(kind=kind, breaks=breaks, why=why, **kw)  # type: ignore[arg-type]


RULES: dict[ChangeKind, Rule] = {
    ChangeKind.FIELD_ADDED_OPTIONAL: _rule(
        ChangeKind.FIELD_ADDED_OPTIONAL,
        NEITHER,
        "the new reader falls back to the default when the old writer omits the field, "
        "and the old reader skips a field it has no slot for",
    ),
    ChangeKind.FIELD_ADDED_REQUIRED: _rule(
        ChangeKind.FIELD_ADDED_REQUIRED,
        BACKWARD,
        "the new reader needs the field, the old writer never wrote it, and there is no "
        "default to fall back to",
    ),
    ChangeKind.FIELD_REMOVED_OPTIONAL: _rule(
        ChangeKind.FIELD_REMOVED_OPTIONAL,
        NEITHER,
        "the new reader skips a field the old writer still sends, and the old reader "
        "falls back to the default it already declared",
    ),
    # Wrong in this commit. Fixed, with the reasoning, two commits later.
    ChangeKind.FIELD_REMOVED_REQUIRED: _rule(
        ChangeKind.FIELD_REMOVED_REQUIRED,
        BACKWARD,
        "the field is gone and whoever needed it no longer gets it",
    ),
    ChangeKind.FIELD_RENAMED_WITH_ALIAS: _rule(
        ChangeKind.FIELD_RENAMED_WITH_ALIAS,
        FORWARD,
        "the new reader matches the old name through its alias; the old reader has no "
        "alias for a name that did not exist when it was written",
    ),
    ChangeKind.FIELD_REORDERED: _rule(
        ChangeKind.FIELD_REORDERED,
        NEITHER,
        "both dialects resolve a record field by name, so declaration order is not part "
        "of the contract",
        cosmetic=True,
    ),
    ChangeKind.LIKELY_RENAME: _rule(
        ChangeKind.LIKELY_RENAME,
        NEITHER,
        "one field left and one of the same type arrived, which is what a rename without "
        "an alias looks like from here; add the old name to the new field's aliases if "
        "that is what happened",
        hint=True,
    ),
    ChangeKind.TYPE_WIDENED: _rule(
        ChangeKind.TYPE_WIDENED,
        FORWARD,
        "the old writer's value promotes into the new reader's wider type; the old "
        "reader has nowhere to put the wider value coming back",
    ),
    ChangeKind.TYPE_NARROWED: _rule(
        ChangeKind.TYPE_NARROWED,
        BACKWARD,
        "the new reader cannot hold every value the old writer was allowed to write",
    ),
    ChangeKind.TYPE_INTERCHANGEABLE: _rule(
        ChangeKind.TYPE_INTERCHANGEABLE,
        NEITHER,
        "these two types promote to each other, so both readers cope",
    ),
    ChangeKind.TYPE_CHANGED: _rule(
        ChangeKind.TYPE_CHANGED,
        BOTH,
        "no promotion exists between the two types in either direction",
    ),
    ChangeKind.CONTAINER_KIND_CHANGED: _rule(
        ChangeKind.CONTAINER_KIND_CHANGED,
        BOTH,
        "a record, an array and a map are different shapes on the wire and neither "
        "reader can read the other",
    ),
    ChangeKind.NULLABILITY_ADDED: _rule(
        ChangeKind.NULLABILITY_ADDED,
        FORWARD,
        "the new writer can emit null and the old reader has no null branch to put it in",
    ),
    ChangeKind.NULLABILITY_REMOVED: _rule(
        ChangeKind.NULLABILITY_REMOVED,
        BACKWARD,
        "the old writer was allowed to emit null and the new reader has no null branch "
        "to put it in",
    ),
    ChangeKind.ENUM_SYMBOL_ADDED: _rule(
        ChangeKind.ENUM_SYMBOL_ADDED,
        FORWARD,
        "the old reader has never heard of the new symbol and declares no fallback",
    ),
    ChangeKind.ENUM_SYMBOL_ADDED_WITH_FALLBACK: _rule(
        ChangeKind.ENUM_SYMBOL_ADDED_WITH_FALLBACK,
        NEITHER,
        "the old reader resolves a symbol it does not know to its own fallback, so "
        "nothing fails and some records arrive as the fallback",
        strict_breaks=FORWARD,
        quiet=True,
    ),
    ChangeKind.ENUM_SYMBOL_REMOVED: _rule(
        ChangeKind.ENUM_SYMBOL_REMOVED,
        BACKWARD,
        "the old writer can still emit the dropped symbol and the new reader declares no fallback",
    ),
    ChangeKind.ENUM_SYMBOL_REMOVED_WITH_FALLBACK: _rule(
        ChangeKind.ENUM_SYMBOL_REMOVED_WITH_FALLBACK,
        NEITHER,
        "the new reader resolves the dropped symbol to its own fallback, so old records "
        "carrying it arrive as the fallback",
        strict_breaks=BACKWARD,
        quiet=True,
    ),
    ChangeKind.ENUM_FALLBACK_ADDED: _rule(
        ChangeKind.ENUM_FALLBACK_ADDED,
        NEITHER,
        "from this version on, a symbol the reader does not know resolves instead of failing",
    ),
    ChangeKind.ENUM_FALLBACK_REMOVED: _rule(
        ChangeKind.ENUM_FALLBACK_REMOVED,
        NEITHER,
        "nothing breaks today, and the next symbol anyone adds stops being readable by "
        "this version",
    ),
    ChangeKind.ENUM_SYMBOLS_REINDEXED: _rule(
        ChangeKind.ENUM_SYMBOLS_REINDEXED,
        BOTH,
        "the binary encoding writes an enum as the index of its symbol, so moving a "
        "symbol remaps every value already written: nothing errors and records decode "
        "to the wrong symbol",
    ),
    ChangeKind.DEFAULT_ADDED: _rule(
        ChangeKind.DEFAULT_ADDED,
        NEITHER,
        "a field the reader used to require now has a fallback, so nothing that "
        "resolved before stops resolving",
    ),
    ChangeKind.DEFAULT_CHANGED: _rule(
        ChangeKind.DEFAULT_CHANGED,
        NEITHER,
        "resolution still succeeds; a reader on the new schema that falls back now "
        "observes a different value than it used to, which is a change to what a "
        "missing value means rather than to what parses",
        strict_breaks=BACKWARD,
        quiet=True,
    ),
    ChangeKind.DEFAULT_REMOVED: _rule(
        ChangeKind.DEFAULT_REMOVED,
        NEITHER,
        "nothing breaks today, and removing this field later stops being forward "
        "compatible because the old reader has lost its fallback",
    ),
    ChangeKind.DOC_CHANGED: _rule(
        ChangeKind.DOC_CHANGED,
        NEITHER,
        "documentation is not on the wire",
        cosmetic=True,
    ),
}

assert set(RULES) == set(ChangeKind), "every change kind needs a rule"


class Change(BaseModel):
    """One observed difference, carrying its rule's answer with it."""

    model_config = ConfigDict(frozen=True)

    kind: ChangeKind
    #: dotted field path, with [] for an array's items and {} for a map's values
    path: str
    #: the concrete before and after, for the person reading the report
    detail: str
    breaks: frozenset[Direction]
    strict_breaks: frozenset[Direction] = NEITHER
    quiet: bool = False
    cosmetic: bool = False
    hint: bool = False

    @property
    def root_field(self) -> str:
        """The top level field this change sits under.

        Consumer impact is decided per declared field, and a consumer that reads
        `hits` is affected by a change to `hits[].score`.
        """
        head = self.path.split(".", 1)[0]
        for marker in ("[]", "{}"):
            head = head.replace(marker, "")
        return head

    def breaks_under(self, *, strict: bool) -> frozenset[Direction]:
        return self.breaks | self.strict_breaks if strict else self.breaks

    @property
    def why(self) -> str:
        return RULES[self.kind].why


def change(kind: ChangeKind, path: str, detail: str, **override: object) -> Change:
    """Build a Change from its rule, with room for a per-case override."""
    rule = RULES[kind]
    fields: dict[str, object] = {
        "kind": kind,
        "path": path,
        "detail": detail,
        "breaks": rule.breaks,
        "strict_breaks": rule.strict_breaks,
        "quiet": rule.quiet,
        "cosmetic": rule.cosmetic,
        "hint": rule.hint,
    }
    fields.update(override)
    return Change(**fields)  # type: ignore[arg-type]
