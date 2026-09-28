"""A built-in preprocessor for SQL with Python format placeholders.

`select * from {table} where day = '{day}'` does not parse, because `{table}`
is not SQL. This masks each placeholder with an identifier derived from its
text, so the residue parses and the placeholders still reach the hash. See
`keystones.preprocess` for the contract.

    [[tool.keystones.language]]
    builtin = "sql"
    extensions = [".sql"]
    preprocessor = "keystones.placeholders:preprocess"

A file with `{{` or `}}` is refused rather than guessed at: in `str.format`
they are escaped braces, and in Jinja they open an expression, which is
keystones-dbt's job.
"""

from __future__ import annotations

import hashlib
import re

from keystones.preprocess import Refused

KEYSTONES_PREPROCESSOR_NAME = "placeholders"
KEYSTONES_PREPROCESSOR_VERSION = "1"

# `{}`, `{0}`, `{name}`, `{obj.attr}`, with an optional `!r` and `:spec`.
PLACEHOLDER = re.compile(r"\{(?:[A-Za-z_][\w.]*|\d*)(?:![rsa])?(?::[^{}\n]*)?\}")


def preprocess(src: str) -> tuple[str, str]:
    if "{{" in src or "}}" in src:
        raise Refused(
            "doubled braces are an escape in str.format and an expression in "
            "Jinja; neither is a placeholder this can mask"
        )
    spans: list[str] = []

    def mask(match: re.Match) -> str:
        spans.append(match.group(0))
        return "__ks_" + hashlib.sha256(match.group(0).encode()).hexdigest()[:12]

    return PLACEHOLDER.sub(mask, src), "\n".join(spans)
