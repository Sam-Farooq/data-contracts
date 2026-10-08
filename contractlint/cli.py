"""The command a pipeline calls.

Exit codes, because this is only useful if a build can act on it:

    0  compatible under the policy asked for
    1  a policy was violated, or a declared consumer breaks
    2  refused: usage, an unreadable file, an unknown subject, a broken registry
    3  `validate` found something wrong with the registry itself

2 and 3 are separate on purpose. A refusal means the checker could not answer,
and a build that treats that as a pass has no gate at all.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from contractlint import render
from contractlint.compat import PolicyResult, Verdict, evaluate, verdict_for
from contractlint.consumers import ConsumerImpact, consumer_impact
from contractlint.errors import ContractError
from contractlint.loader import load_schema_file
from contractlint.registry import Registry, Subject, load_registry
from contractlint.rules import RULES, ChangeKind
from contractlint.types import SchemaDoc

OK = 0
INCOMPATIBLE = 1
REFUSED = 2
BAD_REGISTRY = 3


class Refused(ContractError):
    """Something the user has to fix before there is a question to answer."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contractlint",
        description="Decide whether a schema change breaks the people reading it.",
    )
    parser.add_argument(
        "--registry",
        default="registry",
        metavar="DIR",
        help="the registry directory (default: registry)",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text", help="output shape")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="judge a change against a subject's policy")
    check.add_argument("subject", nargs="?", help="omit to check every subject's latest version")
    check.add_argument(
        "--proposed", metavar="FILE", help="a candidate schema file, rather than the latest version"
    )
    check.add_argument(
        "--strict",
        action="store_true",
        help="count the quiet changes too: a changed default, an enum symbol resolving "
        "to a fallback",
    )
    check.add_argument(
        "--consumers", action="store_true", help="also fail when a declared consumer breaks"
    )
    check.add_argument("--cosmetic", action="store_true", help="include cosmetic differences")
    check.add_argument(
        "--verbose", action="store_true", help="list the changes even when the policy passes"
    )

    diff = sub.add_parser("diff", help="print what changed between two versions")
    diff.add_argument("subject")
    diff.add_argument("from_version", type=int, metavar="FROM")
    diff.add_argument("to_version", type=int, nargs="?", metavar="TO", help="default: the latest")
    diff.add_argument("--proposed", metavar="FILE", help="compare against a file instead of TO")
    diff.add_argument("--strict", action="store_true")
    diff.add_argument("--cosmetic", action="store_true")

    consumers = sub.add_parser("consumers", help="name the consumers a change breaks")
    consumers.add_argument("subject")
    consumers.add_argument("--proposed", metavar="FILE")
    consumers.add_argument("--strict", action="store_true")

    show = sub.add_parser("show", help="print one version, normalized")
    show.add_argument("subject")
    show.add_argument("--version", type=int, help="default: the latest")

    sub.add_parser("validate", help="check the registry itself")
    sub.add_parser("rules", help="print the rule table")
    return parser


def _registry(path: str) -> Registry:
    try:
        return load_registry(path)
    except ContractError as exc:
        raise Refused(str(exc)) from exc


def _require_sound(registry: Registry) -> None:
    if registry.errors:
        raise Refused(
            "the registry has errors, so there is nothing to compare:\n"
            + render.render_problems(registry.errors)
        )


def _subject(registry: Registry, name: str) -> Subject:
    try:
        return registry.subject(name)
    except ContractError as exc:
        raise Refused(str(exc)) from exc


def _proposed(subject: Subject, path: str | None) -> tuple[SchemaDoc, list[SchemaDoc]]:
    """The schema under judgement, and the versions it has to live with."""
    if path is None:
        return subject.latest, subject.before(subject.latest_version)
    try:
        doc = load_schema_file(Path(path), subject=subject.name, version=subject.latest_version + 1)
    except ContractError as exc:
        raise Refused(str(exc)) from exc
    return doc, subject.history


def _impacts(
    registry: Registry, subject: Subject, proposed: SchemaDoc, *, strict: bool
) -> list[ConsumerImpact]:
    out = []
    for consumer in registry.consumers_of(subject.name):
        try:
            out.append(consumer_impact(consumer, subject.history, proposed, strict=strict))
        except ContractError as exc:
            raise Refused(str(exc)) from exc
    return out


def _cmd_check(args: argparse.Namespace, out) -> int:
    registry = _registry(args.registry)
    _require_sound(registry)
    if args.proposed and not args.subject:
        raise Refused("--proposed needs a subject, so there is something to compare it against")

    names = [args.subject] if args.subject else sorted(registry.subjects)
    results: list[PolicyResult] = []
    impacts: list[ConsumerImpact] = []
    for name in names:
        subject = _subject(registry, name)
        proposed, history = _proposed(subject, args.proposed)
        results.append(
            evaluate(subject.config.compatibility, history, proposed, strict=args.strict)
        )
        if args.consumers:
            impacts += _impacts(registry, subject, proposed, strict=args.strict)

    policy_ok = all(r.ok for r in results)
    consumers_ok = not any(i.broken for i in impacts)

    if args.format == "json":
        out.write(
            render.dumps(
                {
                    "registry": registry.root,
                    "ok": policy_ok and consumers_ok,
                    "policy_ok": policy_ok,
                    "consumers_ok": consumers_ok,
                    "subjects": [render.policy_json(r) for r in results],
                    "consumers": [render.impact_json(i, strict=args.strict) for i in impacts],
                    "problems": [p.line() for p in registry.problems],
                }
            )
        )
    else:
        blocks = [
            render.render_policy_result(r, cosmetic=args.cosmetic, verbose=args.verbose)
            for r in results
        ]
        if args.consumers:
            blocks.append(render.render_impacts(impacts, strict=args.strict))
        out.write("\n\n".join(blocks) + "\n")
    return OK if policy_ok and consumers_ok else INCOMPATIBLE


def _cmd_diff(args: argparse.Namespace, out) -> int:
    registry = _registry(args.registry)
    _require_sound(registry)
    subject = _subject(registry, args.subject)
    if args.from_version not in subject.versions:
        raise Refused(
            f"{subject.name} has no v{args.from_version}; it holds {sorted(subject.versions)}"
        )
    old = subject.versions[args.from_version]
    if args.proposed:
        new, _ = _proposed(subject, args.proposed)
    else:
        wanted = args.to_version if args.to_version is not None else subject.latest_version
        if wanted not in subject.versions:
            raise Refused(f"{subject.name} has no v{wanted}; it holds {sorted(subject.versions)}")
        new = subject.versions[wanted]
    verdict: Verdict = verdict_for(old, new, strict=args.strict)
    if args.format == "json":
        out.write(render.dumps(render.verdict_json(verdict)))
    else:
        out.write(render.render_verdict(verdict, cosmetic=args.cosmetic) + "\n")
    return OK


def _cmd_consumers(args: argparse.Namespace, out) -> int:
    registry = _registry(args.registry)
    _require_sound(registry)
    subject = _subject(registry, args.subject)
    proposed, _ = _proposed(subject, args.proposed)
    impacts = _impacts(registry, subject, proposed, strict=args.strict)
    if args.format == "json":
        out.write(
            render.dumps(
                {
                    "subject": subject.name,
                    "proposed": proposed.source_path or subject.latest.label(),
                    "ok": not any(i.broken for i in impacts),
                    "consumers": [render.impact_json(i, strict=args.strict) for i in impacts],
                }
            )
        )
    else:
        out.write(render.render_impacts(impacts, strict=args.strict) + "\n")
    return INCOMPATIBLE if any(i.broken for i in impacts) else OK


def _cmd_show(args: argparse.Namespace, out) -> int:
    registry = _registry(args.registry)
    _require_sound(registry)
    subject = _subject(registry, args.subject)
    wanted = args.version if args.version is not None else subject.latest_version
    if wanted not in subject.versions:
        raise Refused(f"{subject.name} has no v{wanted}; it holds {sorted(subject.versions)}")
    schema = subject.versions[wanted]
    if args.format == "json":
        out.write(
            render.dumps(
                {
                    "subject": schema.subject,
                    "version": schema.version,
                    "dialect": schema.dialect,
                    "assumptions": list(schema.assumptions),
                    "fields": [
                        {
                            "name": f.name,
                            "type": f.type.label(),
                            "required": f.required(),
                            "default": f.default if f.has_default else None,
                        }
                        for f in schema.root.fields
                    ],
                }
            )
        )
    else:
        out.write(render.render_schema(schema) + "\n")
    return OK


def _cmd_validate(args: argparse.Namespace, out) -> int:
    registry = _registry(args.registry)
    if args.format == "json":
        out.write(
            render.dumps(
                {
                    "registry": registry.root,
                    "ok": not registry.errors,
                    "subjects": sorted(registry.subjects),
                    "consumers": [c.name for c in registry.consumers],
                    "problems": [
                        {"level": p.level, "where": p.where, "message": p.message}
                        for p in registry.problems
                    ],
                }
            )
        )
    else:
        counts = (
            f"{len(registry.subjects)} subjects, "
            f"{sum(len(s.versions) for s in registry.subjects.values())} versions, "
            f"{len(registry.consumers)} consumers"
        )
        out.write(counts + "\n" + render.render_problems(registry.problems) + "\n")
    return BAD_REGISTRY if registry.errors else OK


def _cmd_rules(args: argparse.Namespace, out) -> int:
    if args.format == "json":
        out.write(
            render.dumps(
                [
                    {
                        "kind": kind.value,
                        "breaks": sorted(d.value for d in rule.breaks),
                        "breaks_under_strict": sorted(d.value for d in rule.strict_breaks),
                        "quiet": rule.quiet,
                        "cosmetic": rule.cosmetic,
                        "hint": rule.hint,
                        "why": rule.why,
                    }
                    for kind, rule in sorted(RULES.items(), key=lambda kv: kv[0].value)
                ]
            )
        )
        return OK
    lines = [f"{len(RULES)} rules", ""]
    for kind in sorted(ChangeKind, key=lambda k: k.value):
        rule = RULES[kind]
        breaks = ", ".join(sorted(d.value for d in rule.breaks)) or "nothing"
        extra = ", ".join(sorted(d.value for d in rule.strict_breaks))
        if extra:
            breaks += f" (and {extra} under --strict)"
        lines.append(f"  {kind.value.ljust(36)}breaks {breaks}")
        lines += render.wrap(rule.why, 40)
    out.write("\n".join(lines) + "\n")
    return OK


COMMANDS = {
    "check": _cmd_check,
    "diff": _cmd_diff,
    "consumers": _cmd_consumers,
    "show": _cmd_show,
    "validate": _cmd_validate,
    "rules": _cmd_rules,
}


def main(argv: Sequence[str] | None = None, out=None, err=None) -> int:
    args = build_parser().parse_args(argv)
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    try:
        return COMMANDS[args.command](args, out)
    except Refused as exc:
        err.write(f"contractlint: {exc}\n")
        return REFUSED
    except ContractError as exc:
        err.write(f"contractlint: {exc}\n")
        return REFUSED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
