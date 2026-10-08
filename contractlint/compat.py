"""Verdicts and policies.

A verdict is about one pair of schemas. A policy result is about a proposal
against a subject's history, which is not the same question: a chain of
individually compatible steps is not compatible with where it started, and the
transitive policies are the only ones that notice.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from contractlint.diff import diff_schemas
from contractlint.rules import Change
from contractlint.types import BOTH, NEITHER, Direction, SchemaDoc


class Policy(StrEnum):
    """What a subject promises its consumers.

    The transitive variants hold against every retained version rather than the
    previous one. They cost a comparison per version and they catch the case a
    pairwise check cannot see.
    """

    NONE = "none"
    BACKWARD = "backward"
    FORWARD = "forward"
    FULL = "full"
    BACKWARD_TRANSITIVE = "backward_transitive"
    FORWARD_TRANSITIVE = "forward_transitive"
    FULL_TRANSITIVE = "full_transitive"

    @property
    def directions(self) -> frozenset[Direction]:
        if self is Policy.NONE:
            return NEITHER
        if self in (Policy.BACKWARD, Policy.BACKWARD_TRANSITIVE):
            return frozenset({Direction.BACKWARD})
        if self in (Policy.FORWARD, Policy.FORWARD_TRANSITIVE):
            return frozenset({Direction.FORWARD})
        return BOTH

    @property
    def transitive(self) -> bool:
        return self.value.endswith("_transitive")


class Verdict(BaseModel):
    """One comparison, with the changes that produced it."""

    model_config = ConfigDict(frozen=True)

    from_label: str
    to_label: str
    changes: tuple[Change, ...]
    strict: bool = False

    @property
    def broken(self) -> frozenset[Direction]:
        out: frozenset[Direction] = NEITHER
        for c in self.changes:
            out |= c.breaks_under(strict=self.strict)
        return out

    @property
    def holds(self) -> frozenset[Direction]:
        return BOTH - self.broken

    @property
    def substantive(self) -> tuple[Change, ...]:
        """Changes worth printing by default: not cosmetic, not a hint."""
        return tuple(c for c in self.changes if not c.cosmetic and not c.hint)

    @property
    def hints(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.hint)

    def ok_for(self, required: frozenset[Direction]) -> bool:
        return not (required & self.broken)

    def blockers(self, required: frozenset[Direction]) -> tuple[Change, ...]:
        """The changes that cost a required direction, and only those."""
        return tuple(c for c in self.changes if required & c.breaks_under(strict=self.strict))

    def name(self) -> str:
        """full, backward, forward or none, as a single word."""
        holds = self.holds
        if holds == BOTH:
            return "full"
        if holds == frozenset({Direction.BACKWARD}):
            return "backward"
        if holds == frozenset({Direction.FORWARD}):
            return "forward"
        return "none"


def verdict_for(old: SchemaDoc, new: SchemaDoc, *, strict: bool = False) -> Verdict:
    """Compare two versions of a subject."""
    return Verdict(
        from_label=old.label(),
        to_label=new.label(),
        changes=tuple(diff_schemas(old, new)),
        strict=strict,
    )


class PolicyResult(BaseModel):
    """A proposal judged against a subject's history under one policy."""

    model_config = ConfigDict(frozen=True)

    subject: str
    policy: Policy
    verdicts: tuple[Verdict, ...]
    strict: bool = False

    @property
    def required(self) -> frozenset[Direction]:
        return self.policy.directions

    @property
    def failures(self) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if not v.ok_for(self.required))

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def changed(self) -> bool:
        return any(v.substantive for v in self.verdicts)


def evaluate(
    policy: Policy,
    history: Sequence[SchemaDoc],
    proposed: SchemaDoc,
    *,
    strict: bool = False,
) -> PolicyResult:
    """Judge `proposed` against the versions it has to live with.

    A pairwise policy compares against the newest retained version. A transitive
    one compares against all of them, oldest first, which is what makes a field
    coming back with a different type visible: it is compatible with the version
    that dropped it and not with the version that had it.

    An empty history is not a failure. The first version of a subject promises
    nothing to anybody, because nobody has written data with it yet.
    """
    ordered = sorted(history, key=lambda d: (d.version is None, d.version or 0))
    if not ordered:
        targets: list[SchemaDoc] = []
    elif policy.transitive:
        targets = list(ordered)
    else:
        targets = [ordered[-1]]
    return PolicyResult(
        subject=proposed.subject,
        policy=policy,
        verdicts=tuple(verdict_for(old, proposed, strict=strict) for old in targets),
        strict=strict,
    )
