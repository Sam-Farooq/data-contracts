"""Text for a build log, and JSON for anything else.

The JSON is built by hand rather than dumped from the models, because every set
in here would otherwise come out in whatever order the interpreter felt like and
two runs over the same tree would produce two different reports. A report a build
cannot diff is half a report.
"""

from __future__ import annotations

import json
import textwrap
from collections.abc import Iterable, Sequence

from contractlint.compat import PolicyResult, Verdict
from contractlint.consumers import ConsumerImpact
from contractlint.registry import Problem
from contractlint.rules import Change
from contractlint.types import BOTH, Direction, SchemaDoc

WIDTH = 92
TAG = 11
KIND = 34


def _directions(values: Iterable[Direction]) -> list[str]:
    return sorted(d.value for d in values)


def _short(label: str, subject: str) -> str:
    """A verdict label without the subject, for a block that already names it."""
    return label.removeprefix(f"{subject} ")


def _tag(change: Change, *, strict: bool) -> str:
    broken = change.breaks_under(strict=strict)
    if broken == BOTH:
        return "both"
    if broken:
        return next(iter(broken)).value
    if change.quiet:
        return "quiet"
    if change.cosmetic:
        return "cosmetic"
    if change.hint:
        return "hint"
    return "ok"


def wrap(text: str, indent: int) -> list[str]:
    pad = " " * indent
    return textwrap.wrap(text, width=WIDTH, initial_indent=pad, subsequent_indent=pad) or [pad]


def change_lines(change: Change, *, strict: bool, explain: bool = True) -> list[str]:
    tag = _tag(change, strict=strict)
    head = f"  [{tag}]".ljust(TAG + 3) + change.kind.value.ljust(KIND) + change.path
    out = [head, *wrap(change.detail, TAG + 5)]
    worth_explaining = bool(change.breaks_under(strict=strict)) or change.quiet or change.hint
    if explain and worth_explaining:
        out += wrap(change.why, TAG + 5)
    return out


def render_changes(changes: Sequence[Change], *, strict: bool, cosmetic: bool) -> list[str]:
    shown = [c for c in changes if cosmetic or not c.cosmetic]
    if not shown:
        return ["  no differences"]
    out: list[str] = []
    for c in shown:
        out += change_lines(c, strict=strict)
    return out


def render_verdict(verdict: Verdict, *, cosmetic: bool = False) -> str:
    holds = ", ".join(_directions(verdict.holds)) or "neither direction"
    header = f"{verdict.from_label} -> {verdict.to_label}   holds {holds}"
    lines = [header, ""]
    lines += render_changes(verdict.changes, strict=verdict.strict, cosmetic=cosmetic)
    return "\n".join(lines)


def render_policy_result(
    result: PolicyResult, *, cosmetic: bool = False, verbose: bool = False
) -> str:
    required = ", ".join(_directions(result.required)) or "nothing"
    lines = [
        f"{result.subject}: policy {result.policy.value}, requires {required}"
        + (", strict" if result.strict else ""),
        "",
    ]
    if not result.verdicts:
        lines.append("  no retained version to compare against")
        lines.append("")
        lines.append("pass: the first version of a subject promises nothing")
        return "\n".join(lines)

    for verdict in result.verdicts:
        holds = ", ".join(_directions(verdict.holds)) or "neither"
        mark = "ok  " if verdict.ok_for(result.required) else "FAIL"
        count = len(verdict.substantive)
        step = (
            f"{_short(verdict.from_label, result.subject)} -> "
            f"{_short(verdict.to_label, result.subject)}"
        )
        lines.append(
            f"  {mark}  {step}".ljust(24)
            + f"holds {holds}".ljust(30)
            + f"{count} change{'' if count == 1 else 's'}"
        )
    lines.append("")

    # On a failure, print the changes that cost a required direction and nothing
    # else. The rest is context for a question nobody asked, and a build log that
    # buries one blocker in five harmless lines gets skimmed.
    for verdict in result.failures:
        lines.append(
            f"  {_short(verdict.from_label, result.subject)} -> "
            f"{_short(verdict.to_label, result.subject)}"
        )
        blockers = verdict.blockers(result.required)
        lines += render_changes(blockers, strict=result.strict, cosmetic=cosmetic)
        rest = len(verdict.substantive) - len(blockers)
        if rest > 0:
            lines.append(
                f"      and {rest} other change{'' if rest == 1 else 's'} that cost nothing"
            )
        lines.append("")

    if verbose and not result.failures:
        for verdict in result.verdicts[-1:]:
            lines.append(
                f"  {_short(verdict.from_label, result.subject)} -> "
                f"{_short(verdict.to_label, result.subject)}"
            )
            lines += render_changes(verdict.changes, strict=result.strict, cosmetic=cosmetic)
            lines.append("")

    if result.ok:
        lines.append(f"pass: {required} holds against every version compared")
    else:
        missed = sorted({d.value for v in result.failures for d in result.required & v.broken})
        where = ", ".join(v.from_label for v in result.failures)
        lines.append(f"fail: {', '.join(missed)} required, and {where} does not hold it")
    return "\n".join(lines)


def render_impacts(impacts: Sequence[ConsumerImpact], *, strict: bool = False) -> str:
    if not impacts:
        return "no consumers declared for this subject"
    broken = [i for i in impacts if i.broken]
    lines = [f"{len(broken)} of {len(impacts)} consumers break", ""]
    for impact in impacts:
        consumer = impact.consumer
        mark = "BREAKS" if impact.broken else "ok    "
        owner = f" [{consumer.owner}]" if consumer.owner else ""
        scope = "projects" if consumer.projects else "reads everything"
        lines.append(
            f"  {mark}  {consumer.name}{owner}".ljust(44)
            + f"pinned v{consumer.version}, {consumer.upgrade.value}, {scope}"
        )
        for breakage in impact.breakages:
            lines.append(
                f"          {breakage.direction_label()} from {breakage.from_label}: "
                f"{breakage.change.kind.value} at {breakage.change.path}"
            )
            lines += wrap(breakage.change.detail, 12)
        for breakage in impact.passed_over:
            lines.append(
                f"          passed over: {breakage.change.kind.value} at "
                f"{breakage.change.path}, which it does not read"
            )
    lines.append("")
    if broken:
        lines.append("fail: " + ", ".join(sorted(i.consumer.name for i in broken)))
    else:
        lines.append("pass: every declared consumer can still read this subject")
    return "\n".join(lines)


def render_schema(schema: SchemaDoc) -> str:
    lines = [
        f"{schema.label()}   dialect {schema.dialect}",
        f"source {schema.source_path or 'inline'}",
        "",
    ]
    for field in schema.root.fields:
        state = "required" if field.required() else f"default {field.default!r}"
        lines.append(f"  {field.name.ljust(24)}{field.type.label().ljust(34)}{state}")
        if field.type.fields:
            for nested in field.type.fields:
                nested_state = "required" if nested.required() else f"default {nested.default!r}"
                lines.append(
                    f"    .{nested.name.ljust(22)}{nested.type.label().ljust(34)}{nested_state}"
                )
    if schema.assumptions:
        lines += ["", "the loader decided these, the file did not say:"]
        for note in schema.assumptions:
            lines += wrap(f"- {note}", 2)
    return "\n".join(lines)


def render_problems(problems: Sequence[Problem]) -> str:
    if not problems:
        return "the registry holds together"
    return "\n".join(f"  {p.line()}" for p in problems)


# --- JSON ------------------------------------------------------------------


def change_json(change: Change, *, strict: bool) -> dict[str, object]:
    return {
        "kind": change.kind.value,
        "path": change.path,
        "detail": change.detail,
        "breaks": _directions(change.breaks_under(strict=strict)),
        "quiet": change.quiet,
        "cosmetic": change.cosmetic,
        "hint": change.hint,
        "why": change.why,
    }


def verdict_json(verdict: Verdict) -> dict[str, object]:
    return {
        "from": verdict.from_label,
        "to": verdict.to_label,
        "holds": _directions(verdict.holds),
        "broken": _directions(verdict.broken),
        "compatibility": verdict.name(),
        "changes": [change_json(c, strict=verdict.strict) for c in verdict.changes],
    }


def policy_json(result: PolicyResult) -> dict[str, object]:
    return {
        "subject": result.subject,
        "policy": result.policy.value,
        "requires": _directions(result.required),
        "strict": result.strict,
        "ok": result.ok,
        "verdicts": [verdict_json(v) for v in result.verdicts],
    }


def impact_json(impact: ConsumerImpact, *, strict: bool) -> dict[str, object]:
    return {
        "consumer": impact.consumer.name,
        "owner": impact.consumer.owner,
        "subject": impact.consumer.subject,
        "pinned": impact.consumer.version,
        "upgrade": impact.consumer.upgrade.value,
        "needs": _directions(impact.consumer.needs),
        "projects": impact.consumer.projects,
        "broken": impact.broken,
        "compared_against": list(impact.compared_against),
        "breakages": [
            {
                "from": b.from_label,
                "directions": _directions(b.directions),
                "change": change_json(b.change, strict=strict),
            }
            for b in impact.breakages
        ],
        "passed_over": [
            {
                "from": b.from_label,
                "directions": _directions(b.directions),
                "change": change_json(b.change, strict=strict),
            }
            for b in impact.passed_over
        ],
    }


def dumps(payload: object) -> str:
    """One report, stable key order, newline terminated."""
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
