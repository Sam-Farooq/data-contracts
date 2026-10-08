"""A compatibility checker for schema changes."""

from contractlint.diff import diff_schemas
from contractlint.errors import ContractError, SchemaError
from contractlint.loader import load_schema_file, load_schema_obj
from contractlint.rules import RULES, Change, ChangeKind
from contractlint.types import Direction, FieldNode, Kind, SchemaDoc, TypeNode

__all__ = [
    "RULES",
    "Change",
    "ChangeKind",
    "ContractError",
    "Direction",
    "FieldNode",
    "Kind",
    "SchemaDoc",
    "SchemaError",
    "TypeNode",
    "diff_schemas",
    "load_schema_file",
    "load_schema_obj",
]
