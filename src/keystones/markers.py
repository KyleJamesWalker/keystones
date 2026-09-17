"""Marker grammar, shared by every adapter.

One grammar everywhere means a marker reads the same in Python, Terraform and
YAML, and an adapter only has to say where comments are.
"""

from __future__ import annotations

import re

from keystones.models import Marker, Scope

# Comment openers across the languages a fallback adapter has to cope with.
_OPENER = r"(?:#|//|--|/\*|<!--|;|%|\*)"

_QUALIFIERS = r"(?:\(\s*(?P<qualifiers>[^)]*?)\s*\))?"
_ID = r"(?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)"
ID_RE = re.compile(rf"^{_ID}$")

POINT_RE = re.compile(
    rf"{_OPENER}\s*keystone{_QUALIFIERS}\s*:\s*{_ID}\s*(?:\*/|-->)?\s*$"
)
START_RE = re.compile(
    rf"{_OPENER}\s*keystone:start{_QUALIFIERS}\s*:\s*{_ID}\s*(?:\*/|-->)?\s*$"
)
END_RE = re.compile(rf"{_OPENER}\s*keystone:end\s*(?:\*/|-->)?\s*$")

ANY = "keystone"

# An escape hatch for files that talk about markers rather than carrying them:
# documentation, test fixtures, this project's own README.
IGNORE_RE = re.compile(r"keystones:\s*ignore-file")


def looks_like_a_marker(text: str) -> bool:
    """Cheap pre-filter so whole-repo scanning does not parse every file."""
    return ANY in text


def is_marker(text: str) -> bool:
    """An actual marker, not merely a comment that mentions one.

    The text hash excludes marker lines. Excluding everything containing the
    word would let `# keystone rounding rule, do not touch` be deleted with
    the gate green.
    """
    return (
        POINT_RE.search(text) is not None
        or START_RE.search(text) is not None
        or END_RE.search(text) is not None
    )


def _split(qualifiers: str | None) -> tuple[Scope, str]:
    parts = [q.strip() for q in (qualifiers or "").split(",") if q.strip()]
    scope = Scope.FILE if "file" in parts else Scope.NODE
    category = next((q for q in parts if q != "file"), "default")
    return scope, category


def parse_point(text: str, path: str, lineno: int) -> Marker | None:
    match = START_RE.search(text)
    if match:
        return None
    match = POINT_RE.search(text)
    if not match:
        return None
    scope, category = _split(match.group("qualifiers"))
    return Marker(match.group("id"), category, scope, path, lineno)


def parse_region_start(text: str, path: str, lineno: int) -> Marker | None:
    match = START_RE.search(text)
    if not match:
        return None
    _, category = _split(match.group("qualifiers"))
    return Marker(match.group("id"), category, Scope.REGION, path, lineno)


def is_region_end(text: str) -> bool:
    return END_RE.search(text) is not None


def is_ignored(src: str) -> bool:
    return IGNORE_RE.search(src) is not None


def scan_lines(
    path: str,
    src: str,
    candidates: list[tuple[int, str]] | None = None,
) -> tuple[list[Marker], dict[str, tuple[int, int]]]:
    """Scan for point and region markers, returning each region's body range.

    `candidates` lets an adapter with a lexer supply only real comments, so a
    marker inside a string literal is not one. Without it every line is
    considered, which is all a parser-less file can offer.
    """
    markers: list[Marker] = []
    regions: dict[str, tuple[int, int]] = {}
    open_marker: Marker | None = None

    if is_ignored(src):
        return markers, regions

    if candidates is None:
        candidates = list(enumerate(src.splitlines(), start=1))

    for lineno, line in candidates:
        start = parse_region_start(line, path, lineno)
        if start is not None:
            if open_marker is not None:
                raise RegionError(
                    f"{path}:{lineno}: keystone:start for '{start.id}' before "
                    f"'{open_marker.id}' was closed"
                )
            open_marker = start
            continue
        if is_region_end(line):
            if open_marker is None:
                raise RegionError(
                    f"{path}:{lineno}: keystone:end with no matching start"
                )
            regions[open_marker.id] = (open_marker.lineno + 1, lineno - 1)
            markers.append(open_marker)
            open_marker = None
            continue
        point = parse_point(line, path, lineno)
        if point is not None:
            markers.append(point)

    if open_marker is not None:
        raise RegionError(
            f"{path}:{open_marker.lineno}: keystone:start for '{open_marker.id}' "
            "is never closed"
        )
    return markers, regions


class RegionError(Exception):
    """An unbalanced region. Always an error, never a silent file-scope keystone."""
