"""Who breaks, rather than whether something breaks.

A subject policy is one promise to everybody. It is deliberately conservative,
so it says no to changes that no live consumer would notice, and it says nothing
about which of them a real break would reach. This module answers the narrower
question, and it needs two declarations a schema does not carry: which version a
consumer is pinned to, and when it upgrades relative to the producer.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from contractlint.compat import verdict_for
from contractlint.errors import RegistryError
from contractlint.rules import Change
from contractlint.types import BOTH, Direction, SchemaDoc

#: A consumer that does not project reads with its whole pinned schema, so any
#: resolution failure reaches it whether or not the application touches the
#: field. That is the conservative default.
ALL_FIELDS = "*"

BACKWARD = frozenset({Direction.BACKWARD})
FORWARD = frozenset({Direction.FORWARD})


class Upgrade(StrEnum):
    """When a consumer moves, relative to the producer.

    This is the thing that decides which direction it needs, and no schema file
    anywhere records it.
    """

    #: stays on its pinned schema while the producer moves ahead
    LAGGING = "lagging"
    #: deploys with the producer, then drains what is already in flight
    LOCKSTEP = "lockstep"
    #: re-reads retained history with the new schema, and is not upgraded first
    REPLAY = "replay"


class Consumer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    subject: str
    version: int
    owner: str = ""
    upgrade: Upgrade = Upgrade.LAGGING
    #: the field paths this consumer resolves. The default is everything, which
    #: is what a consumer deserializing with its full pinned schema does.
    reads: tuple[str, ...] = (ALL_FIELDS,)

    @property
    def projects(self) -> bool:
        return ALL_FIELDS not in self.reads

    @property
    def needs(self) -> frozenset[Direction]:
        """The directions this consumer's own position requires."""
        if self.upgrade is Upgrade.LAGGING:
            return FORWARD
        if self.upgrade is Upgrade.LOCKSTEP:
            return BACKWARD
        return BOTH


def _segments(path: str) -> tuple[str, ...]:
    out = []
    for part in path.split("."):
        out.append(part.replace("[]", "").replace("{}", ""))
    return tuple(out)


def reads_change(consumer: Consumer, change: Change) -> bool:
    """Whether this change touches something the consumer resolves.

    A declared field covers everything under it, so a consumer reading `merchant`
    is reached by a change to `merchant.category_code`. The reverse is not true.
    """
    if change.cosmetic or change.hint:
        return False
    if not consumer.projects:
        return True
    here = _segments(change.path)
    return any(here[: len(want)] == want for want in (_segments(r) for r in consumer.reads))


class Breakage(BaseModel):
    """One change, costing one consumer one direction, from one version."""

    model_config = ConfigDict(frozen=True)

    from_label: str
    directions: frozenset[Direction]
    change: Change

    def direction_label(self) -> str:
        if self.directions == BOTH:
            return "both"
        return next(iter(sorted(d.value for d in self.directions)))


class ConsumerImpact(BaseModel):
    model_config = ConfigDict(frozen=True)

    consumer: Consumer
    #: breaking changes this consumer resolves
    breakages: tuple[Breakage, ...]
    #: breaking changes outside what it declared it reads. Kept rather than
    #: dropped: a consumer's `reads` list is a claim, and a reviewer looking at a
    #: near miss wants to see the claim it rested on.
    passed_over: tuple[Breakage, ...]
    compared_against: tuple[str, ...]

    @property
    def broken(self) -> bool:
        return bool(self.breakages)


def consumer_impact(
    consumer: Consumer,
    history: Sequence[SchemaDoc],
    proposed: SchemaDoc,
    *,
    strict: bool = False,
) -> ConsumerImpact:
    """What a proposal does to one consumer.

    A lagging consumer is compared from its pin, needing forward. A lockstep one
    from its pin, needing backward. A replay consumer needs backward from every
    retained version, because it will run the new schema over all of it, and
    forward from its pin, because it is not upgraded before the producer.
    """
    by_version = {d.version: d for d in history if d.version is not None}
    pinned = by_version.get(consumer.version)
    if pinned is None:
        raise RegistryError(
            f"consumer {consumer.name!r} is pinned to {consumer.subject} v{consumer.version}, "
            f"which the registry does not hold"
        )

    comparisons: list[tuple[SchemaDoc, frozenset[Direction]]] = []
    if consumer.upgrade is Upgrade.REPLAY:
        comparisons.extend(
            (by_version[n], BACKWARD) for n in sorted(by_version) if n <= consumer.version
        )
        comparisons.append((pinned, FORWARD))
    elif consumer.upgrade is Upgrade.LOCKSTEP:
        comparisons.append((pinned, BACKWARD))
    else:
        comparisons.append((pinned, FORWARD))

    breakages: list[Breakage] = []
    passed_over: list[Breakage] = []
    labels: list[str] = []
    for old, directions in comparisons:
        verdict = verdict_for(old, proposed, strict=strict)
        if old.label() not in labels:
            labels.append(old.label())
        for change in verdict.blockers(directions):
            found = Breakage(
                from_label=old.label(),
                directions=directions & change.breaks_under(strict=strict),
                change=change,
            )
            if reads_change(consumer, change):
                if found not in breakages:
                    breakages.append(found)
            elif found not in passed_over:
                passed_over.append(found)

    return ConsumerImpact(
        consumer=consumer,
        breakages=tuple(breakages),
        passed_over=tuple(passed_over),
        compared_against=tuple(labels),
    )
