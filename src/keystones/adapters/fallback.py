"""The adapter of last resort, so no file type is beyond reach.

Any file can carry a file-scope or region keystone with no parser at all. The
canonical form is normalised text, which means a reformat of a whole-file
keystone does trip it. Region markers exist so YAML and the rest get
granularity instead of collapsing to the file.

Where the file's comment syntax is known, whole-line comments are kept out of
the semantic hash and in the text hash, so a comment edit is C4 rather than C3.
"""

from __future__ import annotations

import hashlib

from keystones import markers as marker_grammar
from keystones.adapters.base import ResolutionError
from keystones.models import Marker, Scope, Target

HASHER_ID = "keystones-text/3"

# Line-comment leaders for types no parser claims. A type not listed hashes
# every line, because guessing wrong would drop code from the hash.
_LEADERS = {
    "#": (
        ".yaml",
        ".yml",
        ".toml",
        ".cfg",
        ".conf",
        ".sh",
        ".bash",
        ".zsh",
        ".lkml",
        ".rb",
        ".properties",
        ".env",
        ".tfvars",
        ".hcl",
        ".tf",
    ),
    "--": (".sql", ".ddl", ".lua", ".hs"),
    "//": (
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".java",
        ".kt",
        ".swift",
        ".rs",
        ".scala",
        ".proto",
        ".dart",
        ".jsonc",
        ".ts",
        ".tsx",
        ".js",
        ".go",
    ),
}
_LEADER_BY_EXT = {ext: leader for leader, exts in _LEADERS.items() for ext in exts}
_LEADER_BY_NAME = {"Dockerfile": "#", "Makefile": "#", "Justfile": "#"}

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


def leader_for(path: str) -> str | None:
    """The line-comment leader this file uses, or None when nobody can say."""
    from keystones import adapters

    parsed = adapters.for_path(path, allow_fallback=False)
    if parsed is not None:
        return parsed.comment_prefix(path)
    return extension_leader(path)


def extension_leader(path: str) -> str | None:
    """The leader by file name alone, for a parser that declares none."""
    name = path.rsplit("/", 1)[-1]
    if name in _LEADER_BY_NAME:
        return _LEADER_BY_NAME[name]
    return next(
        (leader for ext, leader in _LEADER_BY_EXT.items() if path.endswith(ext)),
        None,
    )


# Leaders whose languages also write `/* */` block comments.
_BLOCK_COMMENT_LEADERS = ("--", "//")


def _without_comments(lines: list[str], leader: str | None) -> list[str]:
    """Whole-line comments out: `leader` lines, and for languages that have
    them, lines wholly inside a `/* */` block."""
    if leader is None:
        return lines
    out: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if inside:
            if "*/" in stripped:
                inside = False
                rest = stripped.split("*/", 1)[1].strip()
                if rest:
                    out.append(rest)
            continue
        if stripped.startswith(leader):
            continue
        if leader in _BLOCK_COMMENT_LEADERS and stripped.startswith("/*"):
            if "*/" in stripped:
                rest = stripped.split("*/", 1)[1].strip()
                if rest:
                    out.append(rest)
            else:
                inside = True
            continue
        out.append(line)
    return out


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
        if marker.key not in regions:
            raise ResolutionError(f"{marker.path}: region '{marker.id}' is unbalanced")
        start, end = regions[marker.key]
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


def _lines(src: str, target: Target) -> list[str]:
    return _without_markers(src.splitlines()[target.start - 1 : target.end])


def _body(src: str, target: Target) -> str:
    return normalise("\n".join(_lines(src, target)))


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pair(lines: list[str], path: str) -> tuple[str, str]:
    """Semantic without whole-line comments, text with them."""
    leader = leader_for(path)
    semantic = normalise("\n".join(_without_comments(lines, leader)))
    return _digest(semantic), _digest(normalise("\n".join(lines)))


def hashes(src: str, target: Target) -> tuple[str, str]:
    return _pair(_lines(src, target), target.path)


def canonical_source(src: str, target: Target) -> str:
    return _body(src, target)


def render_symbol(src: str, symbol: str, path: str = "") -> str | None:
    return None


def target_for_qualname(path: str, src: str, qualname: str) -> Target | None:
    return None


def hash_stored_source(source: str, target: str) -> str:
    path = target.split("::")[0].split("#")[0]
    return _pair(source.splitlines(), path)[0]


def hasher_id_for_path(path: str) -> str:
    return HASHER_ID


def duplicate_qualnames(path: str, src: str) -> set[str]:
    """No definitions without a parser, so nothing can shadow."""
    return set()


def comment_prefix(path: str) -> str:
    return leader_for(path) or "#"


def kind_for_path(path: str) -> str:
    return "text"
