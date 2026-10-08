"""A compatibility checker for schema changes."""

from contractlint.compat import Policy, PolicyResult, Verdict, evaluate, verdict_for
from contractlint.consumers import Consumer, ConsumerImpact, Upgrade, consumer_impact
from contractlint.diff import diff_schemas
from contractlint.errors import ContractError, SchemaError
from contractlint.loader import load_schema_file, load_schema_obj
from contractlint.rules import RULES, Change, ChangeKind
from contractlint.types import Direction, FieldNode, Kind, SchemaDoc, TypeNode

__all__ = [
    "RULES",
    "Change",
    "ChangeKind",
    "Consumer",
    "ConsumerImpact",
    "ContractError",
    "Direction",
    "FieldNode",
    "Kind",
    "Policy",
    "PolicyResult",
    "SchemaDoc",
    "SchemaError",
    "TypeNode",
    "Upgrade",
    "Verdict",
    "consumer_impact",
    "diff_schemas",
    "evaluate",
    "load_schema_file",
    "load_schema_obj",
    "verdict_for",
]
