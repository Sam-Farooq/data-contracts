"""Impact per consumer, which is a different question from the policy verdict."""

from __future__ import annotations

from pathlib import Path

import pytest

from contractlint.compat import evaluate
from contractlint.consumers import (
    Breakage,
    Consumer,
    Upgrade,
    consumer_impact,
    reads_change,
)
from contractlint.errors import RegistryError
from contractlint.loader import load_schema_file
from contractlint.registry import load_registry
from contractlint.rules import ChangeKind, change
from contractlint.types import Direction

from .conftest import avro, doc, field

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "registry"
PROPOSALS = ROOT / "proposals"


def proposal(filename: str, subject: str):
    registry = load_registry(REGISTRY)
    s = registry.subject(subject)
    return (
        registry,
        s,
        load_schema_file(PROPOSALS / filename, subject=subject, version=s.latest_version + 1),
    )


def who_breaks(filename: str, subject: str) -> set[str]:
    registry, s, prop = proposal(filename, subject)
    return {
        c.name for c in registry.consumers_of(subject) if consumer_impact(c, s.history, prop).broken
    }


# --- what each upgrade mode needs -----------------------------------------


@pytest.mark.parametrize(
    ("upgrade", "needs"),
    [
        (Upgrade.LAGGING, {Direction.FORWARD}),
        (Upgrade.LOCKSTEP, {Direction.BACKWARD}),
        (Upgrade.REPLAY, {Direction.BACKWARD, Direction.FORWARD}),
    ],
)
def test_the_upgrade_mode_decides_the_direction(upgrade, needs):
    consumer = Consumer(name="c", subject="s", version=1, upgrade=upgrade)
    assert consumer.needs == needs


def test_lagging_is_the_default_because_it_is_the_common_case():
    assert Consumer(name="c", subject="s", version=1).upgrade is Upgrade.LAGGING


def test_reading_everything_is_the_default():
    consumer = Consumer(name="c", subject="s", version=1)
    assert consumer.projects is False


def test_an_unknown_key_on_a_consumer_is_refused():
    with pytest.raises(ValueError, match="upgrades"):
        Consumer(name="c", subject="s", version=1, upgrades="lagging")


# --- which changes reach a consumer ---------------------------------------


@pytest.mark.parametrize(
    ("reads", "path", "reached"),
    [
        (("*",), "anything", True),
        (("amount",), "amount", True),
        (("amount",), "currency", False),
        (("merchant",), "merchant.category_code", True),
        (("merchant.merchant_id",), "merchant.category_code", False),
        (("merchant.merchant_id",), "merchant.merchant_id", True),
        (("hits",), "hits[].score", True),
        (("hits.score",), "hits[].score", True),
        (("hits.doc_id",), "hits[].score", False),
        (("amount",), "amount_minor", False),
    ],
)
def test_a_declared_field_covers_what_is_under_it(reads, path, reached):
    consumer = Consumer(name="c", subject="s", version=1, reads=reads)
    c = change(ChangeKind.TYPE_NARROWED, path, "long -> int")
    assert reads_change(consumer, c) is reached


def test_a_cosmetic_change_reaches_nobody():
    consumer = Consumer(name="c", subject="s", version=1)
    assert reads_change(consumer, change(ChangeKind.DOC_CHANGED, "x", "words")) is False


def test_a_hint_reaches_nobody():
    consumer = Consumer(name="c", subject="s", version=1)
    assert reads_change(consumer, change(ChangeKind.LIKELY_RENAME, "x", "a to b")) is False


# --- the three committed proposals ----------------------------------------


def test_dropping_the_channel_enum_passes_the_policy_and_breaks_two_consumers():
    """The headline case. A backward policy protects the consumer that upgrades
    first, and says nothing about the one that has not upgraded yet."""
    registry, s, prop = proposal(
        "payments.transaction.v4-drop-channel.avsc", "payments.transaction"
    )
    assert evaluate(s.config.compatibility, s.history, prop).ok is True
    assert who_breaks("payments.transaction.v4-drop-channel.avsc", "payments.transaction") == {
        "fraud-scoring",
        "warehouse-loader",
    }


def test_the_two_that_survive_dropping_channel_survive_for_different_reasons():
    registry, s, prop = proposal(
        "payments.transaction.v4-drop-channel.avsc", "payments.transaction"
    )
    by_name = {c.name: consumer_impact(c, s.history, prop) for c in registry.consumers_of(s.name)}

    # one does not read the field, and the near miss is kept rather than dropped
    recon = by_name["settlement-recon"]
    assert recon.broken is False
    assert [b.change.path for b in recon.passed_over] == ["channel"]

    # the other needs the other direction, so the change never applied to it
    notifications = by_name["card-notifications"]
    assert notifications.broken is False
    assert notifications.passed_over == ()


def test_fraud_scoring_breaks_forward_from_its_pin():
    registry, s, prop = proposal(
        "payments.transaction.v4-drop-channel.avsc", "payments.transaction"
    )
    consumer = next(c for c in registry.consumers if c.name == "fraud-scoring")
    impact = consumer_impact(consumer, s.history, prop)
    assert [
        (b.direction_label(), b.change.kind.value, b.change.path) for b in impact.breakages
    ] == [("forward", "field_removed_required", "channel")]
    assert impact.compared_against == ("payments.transaction v3",)


def test_a_replay_consumer_is_compared_against_every_version_up_to_its_pin():
    registry, s, prop = proposal("payments.transaction.v4-fee-returns.avsc", "payments.transaction")
    consumer = next(c for c in registry.consumers if c.name == "warehouse-loader")
    impact = consumer_impact(consumer, s.history, prop)
    assert impact.compared_against == (
        "payments.transaction v1",
        "payments.transaction v2",
        "payments.transaction v3",
    )
    assert [(b.from_label, b.change.kind.value) for b in impact.breakages] == [
        ("payments.transaction v1", "type_changed")
    ]


def test_the_field_coming_back_as_a_string_reaches_only_the_replay_consumer():
    assert who_breaks("payments.transaction.v4-fee-returns.avsc", "payments.transaction") == {
        "warehouse-loader"
    }


def test_a_new_required_property_fails_the_policy_and_reaches_nobody():
    """Worth keeping straight: the policy is right and so is the empty list.

    Adding a required field costs backward compatibility, and crm-sync is
    lagging, so it needs forward. The policy is protecting the version of
    crm-sync that has not been deployed yet.
    """
    registry, s, prop = proposal("crm.account.v3-require-region.schema.json", "crm.account")
    assert evaluate(s.config.compatibility, s.history, prop).ok is False
    assert who_breaks("crm.account.v3-require-region.schema.json", "crm.account") == set()


def test_nothing_in_the_committed_registry_breaks_its_own_consumers():
    """Every consumer, from where it is pinned, can read what the subject writes now."""
    registry = load_registry(REGISTRY)
    for consumer in registry.consumers:
        s = registry.subject(consumer.subject)
        impact = consumer_impact(consumer, s.history, s.latest)
        assert impact.broken is False, f"{consumer.name}: {impact.breakages}"
    assert len(registry.consumers) == 6


# --- the edges -------------------------------------------------------------


def test_a_consumer_pinned_to_a_version_that_is_gone_raises():
    consumer = Consumer(name="ghost", subject="test.subject", version=9)
    with pytest.raises(RegistryError, match="v9"):
        consumer_impact(consumer, [doc(avro(field("id", "string")))], doc(avro(), version=2))


def test_a_consumer_pinned_to_the_proposal_itself_compares_with_nothing_broken():
    history = [doc(avro(field("id", "string")), version=1)]
    proposed = doc(avro(field("id", "string")), version=2)
    impact = consumer_impact(
        Consumer(name="c", subject="test.subject", version=1), history, proposed
    )
    assert impact.breakages == ()


def test_strict_reaches_a_consumer_through_a_changed_default():
    history = [doc(avro(field("x", "long", default=0)), version=1)]
    proposed = doc(avro(field("x", "long", default=-1)), version=2)
    lockstep = Consumer(
        name="c", subject="test.subject", version=1, upgrade=Upgrade.LOCKSTEP, reads=("x",)
    )
    assert consumer_impact(lockstep, history, proposed).broken is False
    assert consumer_impact(lockstep, history, proposed, strict=True).broken is True


def test_a_breakage_knows_which_direction_it_cost():
    both = Breakage(
        from_label="s v1",
        directions=frozenset(Direction),
        change=change(ChangeKind.TYPE_CHANGED, "x", "string -> long"),
    )
    assert both.direction_label() == "both"


# --- cross checks against the subjects -------------------------------------


def write_registry(tmp_path: Path, consumers: str) -> Path:
    directory = tmp_path / "one.subject"
    directory.mkdir()
    (directory / "subject.toml").write_text('compatibility = "full"\nowner = "x"\n')
    (directory / "v1.avsc").write_text(
        '{"type":"record","name":"R","fields":[{"name":"id","type":"string"}]}'
    )
    (tmp_path / "consumers.toml").write_text(consumers)
    return tmp_path


def test_a_consumer_naming_a_subject_that_does_not_exist_is_an_error(tmp_path):
    root = write_registry(
        tmp_path, '[[consumer]]\nname = "c"\nsubject = "other.subject"\nversion = 1\n'
    )
    assert "not a subject here" in load_registry(root).errors[0].message


def test_a_consumer_pinned_to_a_missing_version_is_an_error(tmp_path):
    root = write_registry(
        tmp_path, '[[consumer]]\nname = "c"\nsubject = "one.subject"\nversion = 4\n'
    )
    assert "pinned to v4" in load_registry(root).errors[0].message


def test_a_consumer_reading_a_field_that_does_not_exist_is_an_error(tmp_path):
    root = write_registry(
        tmp_path,
        '[[consumer]]\nname = "c"\nsubject = "one.subject"\nversion = 1\nreads = ["ghost"]\n',
    )
    assert "does not define" in load_registry(root).errors[0].message


def test_two_consumers_with_one_name_is_an_error(tmp_path):
    entry = '[[consumer]]\nname = "c"\nsubject = "one.subject"\nversion = 1\n'
    root = write_registry(tmp_path, entry * 2)
    assert "duplicate consumer name" in load_registry(root).errors[0].message


def test_an_unknown_upgrade_mode_is_an_error(tmp_path):
    root = write_registry(
        tmp_path,
        '[[consumer]]\nname = "c"\nsubject = "one.subject"\nversion = 1\nupgrade = "eventually"\n',
    )
    assert "upgrade" in load_registry(root).errors[0].message


def test_a_lockstep_consumer_that_is_already_behind_is_a_warning(tmp_path):
    directory = tmp_path / "one.subject"
    root = write_registry(
        tmp_path,
        '[[consumer]]\nname = "c"\nsubject = "one.subject"\nversion = 1\nupgrade = "lockstep"\n',
    )
    (directory / "v2.avsc").write_text(
        '{"type":"record","name":"R","fields":[{"name":"id","type":"string"}]}'
    )
    registry = load_registry(root)
    assert registry.errors == ()
    assert "so it is lagging" in registry.warnings[0].message


def test_a_registry_with_no_consumer_file_is_fine(tmp_path):
    directory = tmp_path / "one.subject"
    directory.mkdir()
    (directory / "subject.toml").write_text('compatibility = "full"\nowner = "x"\n')
    (directory / "v1.avsc").write_text(
        '{"type":"record","name":"R","fields":[{"name":"id","type":"string"}]}'
    )
    registry = load_registry(tmp_path)
    assert registry.consumers == ()
    assert registry.errors == ()


def test_the_consumer_file_being_the_wrong_shape_is_an_error(tmp_path):
    root = write_registry(tmp_path, 'consumer = "just a string"\n')
    assert "list of tables" in load_registry(root).errors[0].message
