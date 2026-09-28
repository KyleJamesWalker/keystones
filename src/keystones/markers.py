"""Marker grammar, shared by every adapter.

One grammar everywhere means a marker reads the same in Python, Terraform and
YAML, and an adapter only has to say where comments are.
"""

from __future__ import annotations

import io
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
PENDING_RE = re.compile(
    rf"(?P<head>{_OPENER}\s*keystone(?::start)?){_QUALIFIERS}\s+add"
    r"(?P<tail>\s*(?:\*/|-->)?\s*)$"
)
_PROBE = "keystones-pending-{}"

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


def _split(qualifiers: str | None) -> tuple[Scope, str, str | None]:
    """Scope, category and hash kind out of `(file, finance, hash=text)`.

    The hash qualifier is keyed so it cannot be confused with a category that
    happens to be called `text`.
    """
    parts = [q.strip() for q in (qualifiers or "").split(",") if q.strip()]
    kinds, plain = [], []
    for part in parts:
        key, sep, value = part.partition("=")
        (kinds if sep and key.strip() == "hash" else plain).append(
            value.strip() if sep else part
        )
    if len(kinds) > 1:
        raise MarkerError(f"more than one hash= qualifier: {', '.join(kinds)}")
    if kinds and not kinds[0]:
        raise MarkerError("hash= needs a value, such as hash=text")
    scope = Scope.FILE if "file" in plain else Scope.NODE
    category = next((q for q in plain if q != "file"), "default")
    return scope, category, (kinds[0] if kinds else None)


def parse_point(text: str, path: str, lineno: int) -> Marker | None:
    if START_RE.search(text) or END_RE.search(text):
        return None
    match = POINT_RE.search(text)
    if not match:
        return None
    scope, category, kind = _split(match.group("qualifiers"))
    return Marker(match.group("id"), category, scope, path, lineno, kind)


def parse_region_start(text: str, path: str, lineno: int) -> Marker | None:
    match = START_RE.search(text)
    if not match:
        return None
    _, category, kind = _split(match.group("qualifiers"))
    return Marker(match.group("id"), category, Scope.REGION, path, lineno, kind)


def is_region_end(text: str) -> bool:
    return END_RE.search(text) is not None


def is_ignored(src: str) -> bool:
    return IGNORE_RE.search(src) is not None


def pending_category(line: str) -> str | None:
    """The category a `keystone(...) add` line already names, if any."""
    match = PENDING_RE.search(line)
    if match is None:
        return None
    plain = [
        q.strip()
        for q in (match.group("qualifiers") or "").split(",")
        if q.strip() and "=" not in q and q.strip() != "file"
    ]
    return plain[0] if plain else None


def complete_pending(line: str, marker_id: str, category: str | None = None) -> str:
    """Rewrite a `keystone add` line into a marker for `marker_id`.

    `category` is added to the qualifiers unless the line already names one.
    """
    match = PENDING_RE.search(line)
    if match is None:
        raise MarkerError(f"not a pending marker: {line.strip()}")
    quals = [q.strip() for q in (match.group("qualifiers") or "").split(",")]
    quals = [q for q in quals if q]
    if category and category != "default" and pending_category(line) is None:
        quals.insert(quals.index("file") + 1 if "file" in quals else 0, category)
    keyword = match.group("head") + (f"({', '.join(quals)})" if quals else "")
    return f"{line[: match.start()]}{keyword}: {marker_id}{match.group('tail')}"


def split_lines(text: str) -> list[str]:
    """Lines as a lexer numbers them; `str.splitlines` also breaks on a form feed."""
    return io.StringIO(text, newline="").readlines()


def probe_pending(src: str) -> tuple[str, dict[str, int]]:
    """Stand a throwaway id in for every `keystone add` line.

    The adapter's own lexer then decides which of them are real comments, so a
    pending marker inside a string literal is not one.
    """
    if "add" not in src or is_ignored(src):
        return src, {}
    lines = split_lines(src)
    probes: dict[str, int] = {}
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        if PENDING_RE.search(body) is None:
            continue
        probe = _PROBE.format(index + 1)
        probes[probe] = index + 1
        lines[index] = complete_pending(body, probe) + line[len(body) :]
    return ("".join(lines), probes) if probes else (src, {})


def scan_lines(
    path: str,
    src: str,
    candidates: list[tuple[int, str]] | None = None,
) -> tuple[list[Marker], dict[tuple[str, str], tuple[int, int]]]:
    """Scan for point and region markers, returning each region's body range.

    `candidates` lets an adapter with a lexer supply only real comments, so a
    marker inside a string literal is not one. Without it every line is
    considered, which is all a parser-less file can offer.
    """
    markers: list[Marker] = []
    regions: dict[tuple[str, str], tuple[int, int]] = {}
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
            regions[open_marker.key] = (open_marker.lineno + 1, lineno - 1)
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


class MarkerError(Exception):
    """A marker is written wrongly, as opposed to attached to nothing."""


class RegionError(Exception):
    """An unbalanced region. Always an error, never a silent file-scope keystone."""
