"""A compatibility checker for schema changes.

The public surface is small on purpose: load two schemas, diff them, ask which
directions survive.
"""

from contractlint.compat import Policy, Verdict, evaluate, verdict_for
from contractlint.diff import diff_schemas
from contractlint.loader import load_schema_file, load_schema_obj
from contractlint.rules import RULES, Change, ChangeKind
from contractlint.types import Direction, FieldNode, Kind, SchemaDoc, TypeNode

__all__ = [
    "RULES",
    "Change",
    "ChangeKind",
    "Direction",
    "FieldNode",
    "Kind",
    "Policy",
    "SchemaDoc",
    "TypeNode",
    "Verdict",
    "diff_schemas",
    "evaluate",
    "load_schema_file",
    "load_schema_obj",
    "verdict_for",
]
