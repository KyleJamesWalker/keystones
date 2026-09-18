"""The adapter of last resort, so no file type is beyond reach.

Any file can carry a file-scope or region keystone with no parser at all. The
canonical form is normalised text, which means a reformat of a whole-file
keystone does trip it. Region markers exist so YAML and the rest get
granularity instead of collapsing to the file.
"""

from __future__ import annotations

import hashlib

from keystones import markers as marker_grammar
from keystones.adapters.base import ResolutionError
from keystones.models import Marker, Scope, Target

HASHER_ID = "keystones-text/1"

name = "fallback"
hasher_id = HASHER_ID
extensions = ()


def normalise(text: str) -> str:
    """LF endings, no trailing whitespace, no runs of blank lines."""
    out: list[str] = []
    blank = False
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line:
            if blank:
                continue
            blank = True
        else:
            blank = False
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def _without_markers(lines: list[str]) -> list[str]:
    return [
        line
        for line in lines
        if marker_grammar.parse_point(line, "", 1) is None
        and marker_grammar.parse_region_start(line, "", 1) is None
        and not marker_grammar.is_region_end(line)
    ]


def markers(path: str, src: str) -> list[Marker]:
    found, _ = marker_grammar.scan_lines(path, src)
    return found


def resolve(src: str, marker: Marker) -> Target:
    lines = src.splitlines()
    if marker.scope is Scope.REGION:
        _, regions = marker_grammar.scan_lines(marker.path, src)
        if marker.id not in regions:
            raise ResolutionError(f"{marker.path}: region '{marker.id}' is unbalanced")
        start, end = regions[marker.id]
        if start > end:
            raise ResolutionError(f"{marker.path}: region '{marker.id}' is empty")
        return Target(marker.path, None, start, end, region=True)
    if marker.scope is Scope.NODE:
        raise ResolutionError(
            f"{marker.path}:{marker.lineno}: '{marker.id}' has no definition to attach "
            f"to. {marker.path} has no parser, so use keystone(file) or a "
            "keystone:start / keystone:end region."
        )
    return Target(marker.path, None, 1, max(len(lines), 1))


def _body(src: str, target: Target) -> str:
    lines = src.splitlines()[target.start - 1 : target.end]
    return normalise("\n".join(_without_markers(lines)))


def hashes(src: str, target: Target) -> tuple[str, str]:
    """No AST, so there is nothing to separate comments from. Both hashes match."""
    digest = "sha256:" + hashlib.sha256(_body(src, target).encode("utf-8")).hexdigest()
    return digest, digest


def canonical_source(src: str, target: Target) -> str:
    return _body(src, target)


def render_symbol(src: str, symbol: str) -> str | None:
    return None


def target_for_qualname(path: str, src: str, qualname: str) -> Target | None:
    return None


def hash_stored_source(source: str, target: str) -> str:
    return "sha256:" + hashlib.sha256(normalise(source).encode("utf-8")).hexdigest()


def hasher_id_for_path(path: str) -> str:
    return HASHER_ID


def duplicate_qualnames(path: str, src: str) -> set[str]:
    """No definitions without a parser, so nothing can shadow."""
    return set()


def comment_prefix(path: str) -> str:
    return "#"


def kind_for_path(path: str) -> str:
    return "text"
