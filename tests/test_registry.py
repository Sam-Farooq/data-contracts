"""The registry on disk, and the committed one in particular."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contractlint.compat import Policy, evaluate
from contractlint.errors import RegistryError
from contractlint.registry import field_at, load_registry

from .conftest import avro, field

REGISTRY = Path(__file__).resolve().parent.parent / "registry"


def write_subject(
    root: Path, name: str, policy: str = "backward", files: dict[str, dict] | None = None
) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "subject.toml").write_text(
        f'compatibility = "{policy}"\nowner = "nobody"\n', encoding="utf-8"
    )
    for filename, obj in (files or {"v1.avsc": avro(field("id", "string"))}).items():
        (directory / filename).write_text(json.dumps(obj), encoding="utf-8")
    return directory


# --- the committed registry ------------------------------------------------


def test_the_committed_registry_loads_without_a_problem():
    registry = load_registry(REGISTRY)
    assert [p.line() for p in registry.problems] == []
    assert sorted(registry.subjects) == ["crm.account", "payments.transaction", "search.query_log"]


@pytest.mark.parametrize(
    ("name", "policy", "versions"),
    [
        ("payments.transaction", Policy.BACKWARD_TRANSITIVE, [1, 2, 3]),
        ("crm.account", Policy.FULL, [1, 2]),
        ("search.query_log", Policy.BACKWARD, [1, 2]),
    ],
)
def test_each_subject_declares_what_the_readme_says_it_does(name, policy, versions):
    subject = load_registry(REGISTRY).subject(name)
    assert subject.config.compatibility is policy
    assert sorted(subject.versions) == versions


def test_the_committed_registry_satisfies_its_own_policies():
    """The gate has to pass on the tree it ships, or nobody will believe it."""
    registry = load_registry(REGISTRY)
    for subject in registry.subjects.values():
        result = evaluate(
            subject.config.compatibility, subject.before(subject.latest_version), subject.latest
        )
        assert result.ok, f"{subject.name}: {[v.from_label for v in result.failures]}"


def test_the_transaction_history_is_backward_only_rather_than_full():
    """v2 dropped a required field, so a v1 reader cannot read v3 data."""
    subject = load_registry(REGISTRY).subject("payments.transaction")
    result = evaluate(Policy.BACKWARD_TRANSITIVE, subject.before(3), subject.latest)
    by_source = {v.from_label: v.name() for v in result.verdicts}
    assert by_source == {
        "payments.transaction v1": "backward",
        "payments.transaction v2": "full",
    }


def test_widening_the_latency_cost_search_its_forward_compatibility():
    subject = load_registry(REGISTRY).subject("search.query_log")
    verdict = evaluate(Policy.BACKWARD, subject.before(2), subject.latest).verdicts[0]
    assert verdict.name() == "backward"
    assert [(c.kind.value, c.path, c.detail) for c in verdict.substantive] == [
        ("type_widened", "latency_ms", "long -> double")
    ]


# --- structure -------------------------------------------------------------


def test_a_missing_subject_config_is_an_error(tmp_path):
    (tmp_path / "lonely.subject").mkdir()
    registry = load_registry(tmp_path)
    assert [p.message for p in registry.errors] == ["no subject.toml"]


def test_a_version_gap_is_an_error(tmp_path):
    write_subject(
        tmp_path,
        "gappy",
        files={"v1.avsc": avro(field("id", "string")), "v3.avsc": avro(field("id", "string"))},
    )
    registry = load_registry(tmp_path)
    assert "missing [2]" in registry.errors[0].message


def test_a_subject_with_no_version_files_is_an_error(tmp_path):
    directory = tmp_path / "empty.subject"
    directory.mkdir()
    (directory / "subject.toml").write_text('compatibility = "full"\nowner = "x"\n')
    registry = load_registry(tmp_path)
    assert [p.message for p in registry.errors] == ["no version files"]
    assert registry.subjects == {}


def test_an_unknown_policy_name_is_refused_rather_than_defaulted(tmp_path):
    write_subject(tmp_path, "typo.subject", policy="backwards")
    registry = load_registry(tmp_path)
    assert "compatibility" in registry.errors[0].message


def test_an_unknown_key_in_the_subject_config_is_refused(tmp_path):
    directory = tmp_path / "extra.subject"
    directory.mkdir()
    (directory / "subject.toml").write_text(
        'compatibility = "full"\nowner = "x"\ncompatibilty = "none"\n'
    )
    (directory / "v1.avsc").write_text(json.dumps(avro(field("id", "string"))))
    registry = load_registry(tmp_path)
    assert "compatibilty" in registry.errors[0].message


def test_broken_toml_is_reported_against_the_file(tmp_path):
    directory = tmp_path / "bad.subject"
    directory.mkdir()
    (directory / "subject.toml").write_text("compatibility = ")
    registry = load_registry(tmp_path)
    assert registry.errors[0].where == "bad.subject/subject.toml"


def test_one_unreadable_schema_does_not_stop_the_other_subjects(tmp_path):
    write_subject(tmp_path, "fine.subject")
    directory = tmp_path / "broken.subject"
    directory.mkdir()
    (directory / "subject.toml").write_text('compatibility = "full"\nowner = "x"\n')
    (directory / "v1.avsc").write_text('{"type": "record"')
    registry = load_registry(tmp_path)
    assert list(registry.subjects) == ["fine.subject"]
    assert registry.errors[0].where == "broken.subject/v1.avsc"
    assert "not valid JSON" in registry.errors[0].message


def test_a_file_that_is_not_a_version_is_a_warning_not_an_error(tmp_path):
    directory = write_subject(tmp_path, "noisy.subject")
    (directory / "notes.md").write_text("thinking out loud")
    registry = load_registry(tmp_path)
    assert registry.errors == ()
    assert [p.where for p in registry.warnings] == ["noisy.subject/notes.md"]


def test_two_dialects_in_one_subject_is_a_warning(tmp_path):
    directory = write_subject(tmp_path, "mixed.subject")
    (directory / "v2.schema.json").write_text(
        json.dumps({"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}})
    )
    registry = load_registry(tmp_path)
    assert registry.errors == ()
    assert "2 dialects" in registry.warnings[0].message


def test_an_empty_registry_says_so(tmp_path):
    registry = load_registry(tmp_path)
    assert [p.message for p in registry.problems] == ["no subjects"]


def test_a_registry_path_that_is_not_a_directory_raises(tmp_path):
    bogus = tmp_path / "nope"
    with pytest.raises(RegistryError):
        load_registry(bogus)


def test_asking_for_an_unknown_subject_lists_the_known_ones():
    registry = load_registry(REGISTRY)
    with pytest.raises(RegistryError) as exc:
        registry.subject("payments.transactions")
    assert "crm.account" in str(exc.value)


def test_before_returns_the_versions_a_proposal_has_to_live_with():
    subject = load_registry(REGISTRY).subject("payments.transaction")
    assert [d.version for d in subject.before(3)] == [1, 2]
    assert [d.version for d in subject.before(1)] == []
    assert [d.version for d in subject.history] == [1, 2, 3]


# --- field paths -----------------------------------------------------------


@pytest.mark.parametrize(
    ("subject_name", "path", "exists"),
    [
        ("payments.transaction", "amount_minor", True),
        ("payments.transaction", "merchant", True),
        ("payments.transaction", "merchant.merchant_id", True),
        ("payments.transaction", "merchant.country", True),
        ("payments.transaction", "merchant.postcode", False),
        ("payments.transaction", "mcc", False),
        ("payments.transaction", "amount_minor.currency", False),
        ("search.query_log", "hits", True),
        ("search.query_log", "hits.doc_id", True),
        ("search.query_log", "hits.score", True),
        ("search.query_log", "hits.rank", False),
        ("crm.account", "tags", True),
    ],
)
def test_a_dotted_path_resolves_through_containers(subject_name, path, exists):
    schema = load_registry(REGISTRY).subject(subject_name).latest
    assert field_at(schema, path) is exists
