"""Failures that are the user's file rather than a bug in here.

Every one of these carries the path it was reading when it gave up. A checker
that says "invalid schema" and stops is a checker people route around.
"""

from __future__ import annotations


class ContractError(Exception):
    """Base for everything this package refuses to do."""


class SchemaError(ContractError):
    """A schema file this loader cannot represent, or will not guess at."""

    def __init__(self, message: str, *, path: str = "", source: str | None = None) -> None:
        where = f" at {path}" if path else ""
        in_file = f" in {source}" if source else ""
        super().__init__(f"{message}{where}{in_file}")
        self.message = message
        self.path = path
        self.source = source


class RegistryError(ContractError):
    """The registry on disk does not hold together."""
