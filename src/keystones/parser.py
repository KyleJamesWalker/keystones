"""The contract a parser plugin implements.

A parser plugin reads a language keystones has no grammar for - sqlglot's
Snowflake dialect being the case this exists for - and hands back a small
tree: definitions with line spans, comment lines, and a canonical rendering.
Keystones does everything else: marker discovery, resolution, hashing, the
sidecar, and the C5 re-hash. This module and `keystones.preprocess` are the
only things a plugin imports from keystones.

A table names a factory and passes every other key to it:

    [[tool.keystones.language]]
    extensions = [".sql"]
    parser = { plugin = "keystones_dbt.parsers:sqlglot", dialect = "snowflake" }

    def sqlglot(*, dialect: str) -> Parser: ...

The factory raises TypeError or ValueError for an option it cannot take.

The object it returns provides:

    name: str        # the hash kind when no preprocessor is attached
    identity: str    # shown in the hasher; include every version that can
                     # move render()'s output, such as the parsing library's
    def parse(src: str) -> Tree
    def parse_fragment(src: str) -> Tree

`parse` raises `Unparseable` when the text is not the language. Never return
an error-recovery tree: its shape is the least stable thing about a parser,
so the hash would move on a library bump with nobody touching the file.

`parse_fragment` accepts a stored slice standing alone - a CTE without its
WITH, a method without its class. Its first definition, or `render(None)`
when it has none, must render identically to the same definition in context.
C5 re-hashes the stored slice on its own to prove the sidecar was not
hand-edited, so this is load-bearing.

A Tree provides:

    def definitions() -> list[Definition]    # document order; nesting allowed
    def comments() -> list[tuple[int, str]]  # (line, text) per comment line
    def render(definition: Definition | None) -> str   # None: the whole tree

`render` must be a pure function of the text given: a reformat must not
change it, a change of meaning must. Comments are excluded from it; keystones
hashes them separately.
"""

from __future__ import annotations

from dataclasses import dataclass


class Unparseable(Exception):
    """The text is not this parser's language."""


@dataclass(frozen=True)
class Definition:
    """Something a keystone can attach to, by name, spanning whole lines."""

    qualname: str
    start: int
    end: int
