"""CODEOWNERS parsing with GitHub's last-match-wins semantics.

A check for "some rule matches this path" would miss the case that matters: a
later broad rule such as `*.md @org/docs` silently reassigning every sidecar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# GitHub reads the first of these that exists.
SEARCH_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")


@dataclass(frozen=True)
class Rule:
    pattern: str
    owners: tuple[str, ...]
    lineno: int
    regex: re.Pattern[str]

    def matches(self, path: str) -> bool:
        return self.regex.match(path) is not None


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.startswith("/")
    body = pattern.lstrip("/")
    directory = body.endswith("/")
    body = body.rstrip("/")

    out = []
    i = 0
    while i < len(body):
        char = body[i]
        if char == "*":
            if body[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if body[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1

    prefix = "" if (anchored or "/" in body) else "(?:.*/)?"
    # A rule naming a directory owns everything beneath it.
    suffix = "/.*" if directory else "(?:/.*)?$"
    return re.compile(f"^{prefix}{''.join(out)}{suffix}")


def parse(text: str) -> list[Rule]:
    rules = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        pattern, owners = parts[0], tuple(parts[1:])
        rules.append(Rule(pattern, owners, lineno, _compile(pattern)))
    return rules


def find(repo_root: Path) -> tuple[Path | None, list[Rule]]:
    for rel in SEARCH_PATHS:
        path = repo_root / rel
        if path.is_file():
            return path, parse(path.read_text())
    return None, []


def owners_for(rules: list[Rule], path: str) -> Rule | None:
    """Last matching rule wins, which is how GitHub resolves it."""
    winner = None
    for rule in rules:
        if rule.matches(path):
            winner = rule
    return winner
