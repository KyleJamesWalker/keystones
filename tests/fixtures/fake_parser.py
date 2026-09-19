"""A stand-in parser plugin for a tiny block language.

    block orders
        SELECT id   FROM raw
    end

Shaped exactly as a real one must be: definitions with line spans, comment
lines, a rendering that ignores formatting, a fragment parser that agrees with
the in-context one, and an identity that names the library version.
"""

from __future__ import annotations

from dataclasses import dataclass

from keystones.parser import Definition, Unparseable

VERSION = "1.0"


def parser(*, fold: str = "lower"):
    if fold not in ("lower", "keep"):
        raise ValueError(f"fold must be 'lower' or 'keep', not {fold!r}")
    return FakeParser(fold)


@dataclass(frozen=True)
class FakeParser:
    fold: str
    name: str = "blk"

    @property
    def identity(self) -> str:
        return f"fakeblk@{VERSION}/{self.fold}"

    def parse(self, src: str):
        return _Tree(src, self.fold, fragment=False)

    def parse_fragment(self, src: str):
        return _Tree(src, self.fold, fragment=True)


class _Tree:
    def __init__(self, src: str, fold: str, fragment: bool):
        self.lines = src.splitlines()
        self.fold = fold
        self._defs: list[Definition] = []
        self._comments: list[tuple[int, str]] = []
        stack: list[tuple[str, int]] = []
        for lineno, raw in enumerate(self.lines, start=1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                self._comments.append((lineno, line))
            elif line.startswith("block "):
                name = line.split(None, 1)[1]
                qual = ".".join([*(n for n, _ in stack), name])
                stack.append((qual, lineno))
            elif line == "end":
                if not stack:
                    raise Unparseable(f"line {lineno}: 'end' closes nothing")
                qual, start = stack.pop()
                self._defs.append(Definition(qual, start, lineno))
            elif not stack and not fragment:
                raise Unparseable(f"line {lineno}: text outside a block")
        if stack:
            raise Unparseable(f"block '{stack[-1][0]}' is never closed")
        self._defs.sort(key=lambda d: d.start)

    def definitions(self) -> list[Definition]:
        return list(self._defs)

    def comments(self) -> list[tuple[int, str]]:
        return list(self._comments)

    def render(self, definition: Definition | None) -> str:
        if definition is None:
            start, end = 1, len(self.lines)
        else:
            start, end = definition.start, definition.end
        body = []
        for raw in self.lines[start - 1 : end]:
            line = " ".join(raw.split())
            if not line or line.startswith("#"):
                continue
            if line.startswith("block "):
                line = "block " + line.split(None, 1)[1]
            elif line != "end" and self.fold == "lower":
                line = line.lower()
            body.append(line)
        return "\n".join(body)
