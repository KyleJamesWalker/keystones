"""Core value types shared across adapters, checks and the CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Scope(StrEnum):
    NODE = "node"
    FILE = "file"
    REGION = "region"


@dataclass(frozen=True)
class Marker:
    """A `# keystone: <id>` comment found in a source file."""

    id: str
    category: str
    scope: Scope
    path: str
    lineno: int
    # Which basis gates this keystone. None means auto-detect, which is the
    # only answer for a file whose extension has exactly one parser.
    hash_kind: str | None = None


@dataclass(frozen=True)
class Target:
    """The AST node a marker resolved to."""

    path: str
    qualname: str | None
    start: int
    end: int
    region: bool = False

    def __str__(self) -> str:
        if self.qualname:
            return f"{self.path}::{self.qualname}"
        if self.region:
            return f"{self.path}#L{self.start}-L{self.end}"
        return self.path


@dataclass
class Entry:
    """A parsed sidecar record."""

    id: str
    category: str
    target: str
    hasher: str
    semantic: str
    text: str
    # The kind the marker asked for: "text", a grammar name, or "" for an
    # entry written before anyone had to choose.
    hash: str = ""
    review_every: str | None = None
    depends: list[str] = field(default_factory=list)
    depends_hash: str = ""
    why: str = ""
    source: str = ""
    source_lang: str = "python"
    history: list[str] = field(default_factory=list)
    path: str = ""


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity
    message: str
    path: str | None = None
    lineno: int | None = None
    owner_hint: str | None = None

    def format_plain(self) -> str:
        where = self.path or ""
        if where and self.lineno:
            where = f"{where}:{self.lineno}"
        prefix = f"{where}: " if where else ""
        return f"{prefix}{self.severity.value}: [{self.check}] {self.message}"

    def format_github(self) -> str:
        bits = []
        if self.path:
            bits.append(f"file={self.path}")
        if self.lineno:
            bits.append(f"line={self.lineno}")
        loc = ",".join(bits)
        title = f"title=keystones {self.check}"
        head = (
            f"::{self.severity.value} {loc},{title}"
            if loc
            else f"::{self.severity.value} {title}"
        )
        return f"{head}::{self.message}"
