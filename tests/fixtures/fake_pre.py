"""A stand-in plugin, shaped exactly as a real one must be.

Masks `<<...>>` spans the way a dbt preprocessor masks Jinja: line-preserving,
with the masked content handed back so it still reaches the hash.
"""

import hashlib
import re

from keystones.preprocess import Refused

KEYSTONES_PREPROCESSOR_NAME = "fake"
KEYSTONES_PREPROCESSOR_VERSION = "1"

SPAN = re.compile(r"<<(.*?)>>")
# A span alone on its line is a directive, not a value; replacing it with an
# identifier would leave a bare name where a statement belongs. dbt's config
# block is the real instance of this.
BARE = re.compile(r"(?m)^[ \t]*<<(.*?)>>[ \t]*$")


def preprocess(src: str, *, upper: bool = False) -> tuple[str, str]:
    if not isinstance(upper, bool):
        raise ValueError(f"upper must be true or false, not {upper!r}")
    if "REFUSE" in src:
        raise Refused("this file uses a construct the fake plugin cannot mask")
    spans: list[str] = []

    def take(match: re.Match) -> str:
        body = " ".join(match.group(1).split())
        if upper:
            body = body.upper()
        spans.append(body)
        # Content-derived, so a slice renders the same as the whole file.
        return "__ks_" + hashlib.sha256(body.encode()).hexdigest()[:8]

    def drop(match: re.Match) -> str:
        spans.append(" ".join(match.group(1).split()))
        return ""

    return SPAN.sub(take, BARE.sub(drop, src)), "\n".join(spans)


def drops_a_line(src: str) -> tuple[str, str]:
    """Violates the line-preservation contract, on purpose."""
    return src.replace("\n", " ", 1), ""
