"""The command a pipeline calls, including what it refuses to do."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from contractlint.cli import BAD_REGISTRY, INCOMPATIBLE, OK, REFUSED, main

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = str(ROOT / "registry")
PROPOSALS = ROOT / "proposals"
DROP_CHANNEL = str(PROPOSALS / "payments.transaction.v4-drop-channel.avsc")
FEE_RETURNS = str(PROPOSALS / "payments.transaction.v4-fee-returns.avsc")
REQUIRE_REGION = str(PROPOSALS / "crm.account.v3-require-region.schema.json")


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(["--registry", REGISTRY, *argv], out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def run_json(*argv: str) -> tuple[int, dict | list]:
    code, out, _ = run("--format", "json", *argv)
    return code, json.loads(out)


# --- check -----------------------------------------------------------------


def test_the_committed_registry_passes_its_own_gate():
    code, out, _ = run("check")
    assert code == OK
    assert out.count("pass:") == 3


def test_checking_every_subject_covers_every_subject():
    _code, payload = run_json("check")
    assert [s["subject"] for s in payload["subjects"]] == [
        "crm.account",
        "payments.transaction",
        "search.query_log",
    ]
    assert payload["ok"] is True


def test_the_transitive_trap_fails_against_v1_and_passes_against_v3():
    code, out, _ = run("check", "payments.transaction", "--proposed", FEE_RETURNS)
    assert code == INCOMPATIBLE
    assert "FAIL  v1 -> v4" in out
    assert "ok    v3 -> v4" in out
    assert "fail: backward required, and payments.transaction v1 does not hold it" in out


def test_a_failure_prints_the_blocker_and_counts_the_rest():
    _code, out, _ = run("check", "payments.transaction", "--proposed", FEE_RETURNS)
    assert "type_changed" in out
    assert "and 5 other changes that cost nothing" in out


def test_dropping_an_enum_passes_the_policy_on_its_own():
    code, out, _ = run("check", "payments.transaction", "--proposed", DROP_CHANNEL)
    assert code == OK
    assert "pass: backward holds" in out


def test_the_same_change_fails_once_consumers_are_asked():
    code, out, _ = run("check", "payments.transaction", "--proposed", DROP_CHANNEL, "--consumers")
    assert code == INCOMPATIBLE
    assert "2 of 4 consumers break" in out
    assert "fail: fraud-scoring, warehouse-loader" in out


def test_a_required_property_fails_the_policy_while_no_consumer_notices():
    code, out, _ = run("check", "crm.account", "--proposed", REQUIRE_REGION, "--consumers")
    assert code == INCOMPATIBLE
    assert "field_added_required" in out
    assert "0 of 1 consumers break" in out


def test_strict_turns_a_quiet_change_into_a_failure(tmp_path):
    registry = tmp_path / "registry"
    subject = registry / "quiet.subject"
    subject.mkdir(parents=True)
    (subject / "subject.toml").write_text('compatibility = "backward"\nowner = "x"\n')
    record = '{"type":"record","name":"R","fields":[{"name":"x","type":"long","default":%s}]}'
    (subject / "v1.avsc").write_text(record % "0")
    (subject / "v2.avsc").write_text(record % "-1")

    out, err = io.StringIO(), io.StringIO()
    assert main(["--registry", str(registry), "check"], out=out, err=err) == OK
    assert (
        main(["--registry", str(registry), "check", "--strict"], out=out, err=err) == INCOMPATIBLE
    )


def test_the_json_report_is_byte_for_byte_stable():
    """Two runs over one tree have to produce one report, or nothing can diff it."""
    first = run("--format", "json", "check", "--consumers")[1]
    second = run("--format", "json", "check", "--consumers")[1]
    assert first == second
    assert first.index('"ok"') < first.index('"policy_ok"')


def test_the_json_report_carries_the_directions_as_sorted_lists():
    _code, payload = run_json("check", "payments.transaction", "--proposed", FEE_RETURNS)
    verdict = payload["subjects"][0]["verdicts"][0]
    assert verdict["broken"] == ["backward", "forward"]
    assert verdict["compatibility"] == "none"
    blocker = next(c for c in verdict["changes"] if c["kind"] == "type_changed")
    assert blocker["breaks"] == ["backward", "forward"]
    assert blocker["path"] == "fee_minor"


def test_a_verbose_pass_lists_the_changes_it_allowed():
    _code, out, _ = run("check", "payments.transaction", "--verbose")
    assert "field_added_optional" in out
    _code, quiet, _err = run("check", "payments.transaction")
    assert "field_added_optional" not in quiet


def test_cosmetic_changes_are_hidden_until_asked_for(tmp_path):
    registry = tmp_path / "registry"
    subject = registry / "doc.subject"
    subject.mkdir(parents=True)
    (subject / "subject.toml").write_text('compatibility = "full"\nowner = "x"\n')
    record = '{"type":"record","name":"R","fields":[{"name":"x","type":"long","doc":"%s"}]}'
    (subject / "v1.avsc").write_text(record % "before")
    (subject / "v2.avsc").write_text(record % "after")

    out = io.StringIO()
    main(["--registry", str(registry), "check", "--verbose"], out=out, err=io.StringIO())
    assert "doc_changed" not in out.getvalue()
    out = io.StringIO()
    main(
        ["--registry", str(registry), "check", "--verbose", "--cosmetic"],
        out=out,
        err=io.StringIO(),
    )
    assert "doc_changed" in out.getvalue()


# --- diff, consumers, show, validate, rules --------------------------------


def test_diff_names_both_versions_and_the_direction_it_costs():
    code, out, _ = run("diff", "payments.transaction", "1", "2")
    assert code == OK
    assert "payments.transaction v1 -> payments.transaction v2" in out
    assert "holds backward" in out
    assert "[forward]" in out


def test_diff_defaults_its_second_version_to_the_latest():
    _code, out, _err = run("diff", "payments.transaction", "2")
    assert "-> payments.transaction v3" in out


def test_diff_can_take_a_file_as_the_second_side():
    _code, out, _ = run("diff", "payments.transaction", "3", "--proposed", DROP_CHANNEL)
    assert "field_removed_required" in out


def test_consumers_exits_nonzero_when_one_breaks():
    code, out, _ = run("consumers", "payments.transaction", "--proposed", DROP_CHANNEL)
    assert code == INCOMPATIBLE
    assert "BREAKS  fraud-scoring [risk]" in out
    assert "passed over" in out


def test_consumers_with_no_proposal_judges_what_is_committed():
    code, out, _ = run("consumers", "payments.transaction")
    assert code == OK
    assert "pass: every declared consumer can still read what it is pinned to" in out


def test_consumers_reports_which_versions_each_one_was_compared_against():
    _code, payload = run_json("consumers", "payments.transaction", "--proposed", FEE_RETURNS)
    replay = next(c for c in payload["consumers"] if c["consumer"] == "warehouse-loader")
    assert replay["compared_against"] == [
        "payments.transaction v1",
        "payments.transaction v2",
        "payments.transaction v3",
    ]
    assert replay["needs"] == ["backward", "forward"]


def test_show_prints_the_loader_assumptions_with_the_fields():
    code, out, _ = run("show", "search.query_log")
    assert code == OK
    assert "latency_ms" in out
    assert "the loader decided these" in out


def test_show_can_ask_for_an_older_version():
    _code, payload = run_json("show", "search.query_log", "--version", "1")
    latency = next(f for f in payload["fields"] if f["name"] == "latency_ms")
    assert latency["type"] == "long"


def test_show_walks_into_a_nested_record():
    _code, out, _ = run("show", "payments.transaction")
    assert ".merchant_id" in out


def test_validate_counts_what_it_read():
    code, out, _ = run("validate")
    assert code == OK
    assert "3 subjects, 7 versions, 6 consumers" in out
    assert "the registry holds together" in out


def test_validate_exits_three_on_a_broken_registry(tmp_path):
    (tmp_path / "orphan.subject").mkdir()
    out, err = io.StringIO(), io.StringIO()
    code = main(["--registry", str(tmp_path), "validate"], out=out, err=err)
    assert code == BAD_REGISTRY
    assert "no subject.toml" in out.getvalue()


def test_rules_prints_every_rule_in_the_table():
    code, out, _ = run("rules")
    assert code == OK
    assert out.startswith("25 rules")
    assert "field_removed_required" in out
    assert "breaks forward" in out


def test_the_rules_json_is_the_table_itself():
    _code, payload = run_json("rules")
    assert len(payload) == 25
    removal = next(r for r in payload if r["kind"] == "field_removed_required")
    assert removal["breaks"] == ["forward"]
    quiet = next(r for r in payload if r["kind"] == "default_changed")
    assert quiet["breaks"] == []
    assert quiet["breaks_under_strict"] == ["backward"]


# --- refusals --------------------------------------------------------------


def test_an_unknown_subject_is_refused_and_lists_the_known_ones():
    code, _out, err = run("check", "payments.transactions")
    assert code == REFUSED
    assert "crm.account" in err


def test_a_proposal_with_no_subject_is_refused():
    code, _out, err = run("check", "--proposed", DROP_CHANNEL)
    assert code == REFUSED
    assert "--proposed needs a subject" in err


def test_an_unreadable_proposal_is_refused_rather_than_skipped():
    code, _out, err = run("check", "crm.account", "--proposed", "nowhere.avsc")
    assert code == REFUSED
    assert "cannot read the file" in err


def test_a_proposal_with_an_undeclarable_dialect_is_refused(tmp_path):
    candidate = tmp_path / "v9.json"
    candidate.write_text("{}")
    code, _out, err = run("check", "crm.account", "--proposed", str(candidate))
    assert code == REFUSED
    assert "cannot tell the dialect" in err


def test_a_registry_that_is_not_there_is_refused():
    out, err = io.StringIO(), io.StringIO()
    code = main(["--registry", "/no/such/registry", "check"], out=out, err=err)
    assert code == REFUSED
    assert "not a directory" in err.getvalue()


def test_a_broken_registry_refuses_the_check_rather_than_passing_it(tmp_path):
    """Exit 2 and exit 1 are different answers, and a build that conflates them
    has no gate: an unparseable schema would read as nothing to report."""
    subject = tmp_path / "broken.subject"
    subject.mkdir()
    (subject / "subject.toml").write_text('compatibility = "full"\nowner = "x"\n')
    (subject / "v1.avsc").write_text("{not json")
    out, err = io.StringIO(), io.StringIO()
    code = main(["--registry", str(tmp_path), "check"], out=out, err=err)
    assert code == REFUSED
    assert "nothing to compare" in err.getvalue()


def test_asking_for_a_version_that_does_not_exist_is_refused():
    code, _out, err = run("diff", "payments.transaction", "9")
    assert code == REFUSED
    assert "[1, 2, 3]" in err
    code, _out, err = run("show", "crm.account", "--version", "7")
    assert code == REFUSED
    assert "has no v7" in err


def test_no_command_at_all_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
