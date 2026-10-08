"""Verdicts, policies, and the transitive case a pairwise check cannot see."""

from __future__ import annotations

import pytest

from contractlint.compat import Policy, evaluate, verdict_for
from contractlint.types import Direction

from .conftest import avro, doc, field

BW = Direction.BACKWARD
FW = Direction.FORWARD


def test_an_unchanged_schema_holds_both_directions():
    v = verdict_for(doc(avro(field("x", "string"))), doc(avro(field("x", "string")), version=2))
    assert v.changes == ()
    assert v.name() == "full"
    assert v.holds == frozenset({BW, FW})


def test_adding_a_required_field_holds_forward_only():
    v = verdict_for(
        doc(avro(field("x", "string"))),
        doc(avro(field("x", "string"), field("y", "long")), version=2),
    )
    assert v.name() == "forward"
    assert v.broken == frozenset({BW})


def test_a_change_that_breaks_both_directions_holds_none():
    v = verdict_for(doc(avro(field("x", "string"))), doc(avro(field("x", "long")), version=2))
    assert v.name() == "none"


def test_the_labels_carry_the_versions():
    v = verdict_for(doc(avro(field("x", "string"))), doc(avro(field("x", "string")), version=4))
    assert v.from_label == "test.subject v1"
    assert v.to_label == "test.subject v4"


def test_cosmetic_and_hint_changes_are_separated_from_the_substantive_ones():
    old = doc(avro(field("a", "long", doc="before")))
    new = doc(avro(field("b", "long", doc="before")), version=2)
    v = verdict_for(old, new)
    assert len(v.changes) == 3
    assert {c.kind.value for c in v.substantive} == {
        "field_removed_required",
        "field_added_required",
    }
    assert len(v.hints) == 1


@pytest.mark.parametrize(
    ("policy", "directions", "transitive"),
    [
        (Policy.NONE, set(), False),
        (Policy.BACKWARD, {BW}, False),
        (Policy.FORWARD, {FW}, False),
        (Policy.FULL, {BW, FW}, False),
        (Policy.BACKWARD_TRANSITIVE, {BW}, True),
        (Policy.FORWARD_TRANSITIVE, {FW}, True),
        (Policy.FULL_TRANSITIVE, {BW, FW}, True),
    ],
)
def test_what_each_policy_requires(policy, directions, transitive):
    assert policy.directions == directions
    assert policy.transitive is transitive


def test_a_pairwise_policy_compares_against_the_newest_version_only():
    history = [
        doc(avro(field("id", "string"), field("fee", "long")), version=1),
        doc(avro(field("id", "string")), version=2),
    ]
    proposed = doc(avro(field("id", "string"), field("fee", "string", default="")), version=3)
    result = evaluate(Policy.BACKWARD, history, proposed)
    assert len(result.verdicts) == 1
    assert result.verdicts[0].from_label.endswith("v2")
    assert result.ok is True


def test_a_transitive_policy_catches_a_field_that_came_back_with_another_type():
    """v1 had fee as a long. v2 dropped it. v3 brings it back as a string.

    Against v2 the field is simply new and defaulted, which is compatible. A
    reader on v3 handed a record written by v1 finds a long where it wants a
    string, and long does not promote to string.
    """
    history = [
        doc(avro(field("id", "string"), field("fee", "long")), version=1),
        doc(avro(field("id", "string")), version=2),
    ]
    proposed = doc(avro(field("id", "string"), field("fee", "string", default="")), version=3)
    result = evaluate(Policy.BACKWARD_TRANSITIVE, history, proposed)
    assert len(result.verdicts) == 2
    assert result.ok is False
    assert [v.from_label for v in result.failures] == ["test.subject v1"]
    assert [c.kind.value for c in result.failures[0].blockers(result.required)] == ["type_changed"]


def test_the_transitive_comparison_runs_oldest_first():
    history = [
        doc(avro(field("id", "string")), version=2),
        doc(avro(field("id", "string")), version=1),
        doc(avro(field("id", "string")), version=3),
    ]
    result = evaluate(Policy.FULL_TRANSITIVE, history, doc(avro(field("id", "string")), version=4))
    assert [v.from_label for v in result.verdicts] == [
        "test.subject v1",
        "test.subject v2",
        "test.subject v3",
    ]


def test_the_none_policy_still_reports_the_changes_it_allows():
    history = [doc(avro(field("id", "string"), field("fee", "long")), version=1)]
    result = evaluate(Policy.NONE, history, doc(avro(field("id", "long")), version=2))
    assert result.ok is True
    assert result.changed is True
    assert len(result.verdicts[0].substantive) == 2


def test_the_first_version_of_a_subject_has_nothing_to_be_compatible_with():
    result = evaluate(Policy.FULL_TRANSITIVE, [], doc(avro(field("id", "string"))))
    assert result.verdicts == ()
    assert result.ok is True
    assert result.changed is False


def test_strict_turns_a_quiet_change_into_a_policy_failure():
    history = [doc(avro(field("x", "long", default=0)), version=1)]
    proposed = doc(avro(field("x", "long", default=-1)), version=2)
    assert evaluate(Policy.BACKWARD, history, proposed).ok is True
    assert evaluate(Policy.BACKWARD, history, proposed, strict=True).ok is False


def test_strict_does_not_invent_a_failure_in_a_direction_nobody_asked_for():
    history = [doc(avro(field("x", "long", default=0)), version=1)]
    proposed = doc(avro(field("x", "long", default=-1)), version=2)
    assert evaluate(Policy.FORWARD, history, proposed, strict=True).ok is True
