"""The registry on disk.

A subject is a directory. Its policy is one TOML file, its versions are one
schema file each, named v1, v2, v3. There is no server and no state: what the
checker knows is what the working tree holds, which is why a pull request can
be judged before anything is published.

Loading never raises for a problem it can describe. Everything it finds lands in
`Registry.problems`, so one run reports every broken subject rather than the
first one.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from contractlint.compat import Policy
from contractlint.errors import RegistryError, SchemaError
from contractlint.loader import load_schema_file
from contractlint.types import Kind, SchemaDoc, TypeNode

VERSION_FILE = re.compile(r"^v(\d+)\.(avsc|schema\.json)$")
SUBJECT_CONFIG = "subject.toml"


class Problem(BaseModel):
    """Something wrong with the registry itself rather than with a change."""

    model_config = ConfigDict(frozen=True)

    level: Literal["error", "warning"]
    where: str
    message: str

    def line(self) -> str:
        return f"{self.level}: {self.where}: {self.message}"


class SubjectConfig(BaseModel):
    # A policy name with a typo must not quietly become the default, and neither
    # must a key nobody reads. Both are the kind of mistake that makes a gate
    # pass for the wrong reason.
    model_config = ConfigDict(frozen=True, extra="forbid")

    compatibility: Policy
    owner: str
    description: str = ""


class Subject(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    config: SubjectConfig
    versions: dict[int, SchemaDoc]

    @property
    def history(self) -> list[SchemaDoc]:
        """Every retained version, oldest first."""
        return [self.versions[n] for n in sorted(self.versions)]

    @property
    def latest_version(self) -> int:
        if not self.versions:
            raise RegistryError(f"{self.name} has no versions")
        return max(self.versions)

    @property
    def latest(self) -> SchemaDoc:
        return self.versions[self.latest_version]

    def before(self, version: int) -> list[SchemaDoc]:
        """The versions a proposal for `version` has to live with."""
        return [self.versions[n] for n in sorted(self.versions) if n < version]


class Registry(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: str
    subjects: dict[str, Subject]
    problems: tuple[Problem, ...] = ()

    @property
    def errors(self) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.level == "error")

    @property
    def warnings(self) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.level == "warning")

    def subject(self, name: str) -> Subject:
        try:
            return self.subjects[name]
        except KeyError:
            known = ", ".join(sorted(self.subjects)) or "none"
            raise RegistryError(f"no subject named {name!r}; the registry holds {known}") from None


def field_at(schema: SchemaDoc, path: str) -> bool:
    """Whether a dotted path names something in this schema.

    Containers are walked through rather than spelled out, so `hits.score`
    resolves inside an array of records. A consumer declaring what it reads
    should not have to write the array brackets.
    """
    node: TypeNode | None = schema.root
    for segment in path.split("."):
        while node is not None and node.kind in (Kind.ARRAY, Kind.MAP):
            node = node.items if node.kind is Kind.ARRAY else node.values
        if node is None or node.kind is not Kind.RECORD:
            return False
        field = node.field_map().get(segment)
        if field is None:
            return False
        node = field.type
    return True


def _load_subject(directory: Path, problems: list[Problem]) -> Subject | None:
    name = directory.name
    config_path = directory / SUBJECT_CONFIG
    if not config_path.exists():
        problems.append(Problem(level="error", where=name, message=f"no {SUBJECT_CONFIG}"))
        return None
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        problems.append(Problem(level="error", where=f"{name}/{SUBJECT_CONFIG}", message=str(exc)))
        return None
    try:
        config = SubjectConfig(**raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"]) or "config"
        problems.append(
            Problem(
                level="error",
                where=f"{name}/{SUBJECT_CONFIG}",
                message=f"{field}: {first['msg']}",
            )
        )
        return None

    versions: dict[int, SchemaDoc] = {}
    dialects: set[str] = set()
    for path in sorted(directory.iterdir()):
        if path.name == SUBJECT_CONFIG or path.is_dir():
            continue
        match = VERSION_FILE.match(path.name)
        if match is None:
            problems.append(
                Problem(
                    level="warning",
                    where=f"{name}/{path.name}",
                    message="not a version file, so nothing reads it; expected vN.avsc "
                    "or vN.schema.json",
                )
            )
            continue
        number = int(match.group(1))
        try:
            doc = load_schema_file(path, subject=name, version=number)
        except SchemaError as exc:
            problems.append(Problem(level="error", where=f"{name}/{path.name}", message=str(exc)))
            continue
        versions[number] = doc
        dialects.add(doc.dialect)

    if not versions:
        problems.append(Problem(level="error", where=name, message="no version files"))
        return None
    if len(dialects) > 1:
        problems.append(
            Problem(
                level="warning",
                where=name,
                message=f"versions are written in {len(dialects)} dialects "
                f"({', '.join(sorted(dialects))}); comparable, and a reviewer reading the "
                "files will be comparing two notations by eye",
            )
        )
    expected = list(range(1, max(versions) + 1))
    missing = [n for n in expected if n not in versions]
    if missing:
        problems.append(
            Problem(
                level="error",
                where=name,
                message=f"version numbers are not contiguous, missing {missing}; a "
                "transitive policy would check against a history with a hole in it",
            )
        )
    return Subject(name=name, config=config, versions=versions)


def load_registry(root: str | Path) -> Registry:
    """Read every subject under `root`."""
    base = Path(root)
    if not base.is_dir():
        raise RegistryError(f"{base} is not a directory")
    problems: list[Problem] = []
    subjects: dict[str, Subject] = {}
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        subject = _load_subject(directory, problems)
        if subject is not None:
            subjects[subject.name] = subject
    if not subjects and not problems:
        problems.append(Problem(level="warning", where=str(base), message="no subjects"))
    return Registry(root=str(base), subjects=subjects, problems=tuple(problems))
