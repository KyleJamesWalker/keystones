"""Generic driver for a parser plugin. See keystones.parser for what it supplies.

Everything a grammar-backed adapter does with a tree happens here with the
plugin's tree instead, so a plugin author writes a parser, not an adapter.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

from keystones import markers as marker_grammar
from keystones.adapters import fallback
from keystones.adapters.base import ResolutionError
from keystones.adapters.masking import preprocessed
from keystones.config import ParserPlugin, Preprocessor, options_digest
from keystones.hashing import digest
from keystones.models import Marker, Scope, Target
from keystones.parser import Definition, Unparseable

ADAPTER_VERSION = 1


@dataclass(frozen=True)
class PluginSpec:
    extensions: tuple[str, ...]
    parser: ParserPlugin
    preprocessor: Preprocessor | None = None
    line_comment: str = "#"


_BY_EXTENSION: dict[str, PluginSpec] = {}
extensions: tuple[str, ...] = ()
name = "plugin"


def install(specs: tuple[PluginSpec, ...]) -> None:
    """Rebuild routing from scratch, so a second repo's load drops the first's."""
    global _BY_EXTENSION, extensions
    _BY_EXTENSION = {ext: spec for spec in specs for ext in spec.extensions}
    extensions = tuple(_BY_EXTENSION)


def spec_for(path: str) -> PluginSpec:
    for ext, spec in _BY_EXTENSION.items():
        if path.endswith(ext):
            return spec
    raise ResolutionError(f"{path}: no parser plugin claims this extension")


def hasher_id_for(spec: PluginSpec) -> str:
    plugin = f"+{spec.preprocessor.id}" if spec.preprocessor else ""
    pre_options = spec.preprocessor.options if spec.preprocessor else ()
    return (
        f"keystones-plugin/{ADAPTER_VERSION}+{spec.parser.identity}"
        f"/{options_digest(spec.parser.options, pre_options)}{plugin}"
    )


def _tree(spec: PluginSpec, src: str, fragment: bool = False):
    masked, _ = preprocessed(spec.preprocessor, src)
    parser = spec.parser.parser
    parse = parser.parse_fragment if fragment else parser.parse
    try:
        return parse(masked)
    except Unparseable as exc:
        raise ResolutionError(f"does not parse as {spec.parser.name}: {exc}") from exc


def _comment_lines(tree) -> list[tuple[int, str]]:
    return sorted(tree.comments())


def markers(path: str, src: str) -> list[Marker]:
    tree = _tree(spec_for(path), src)
    found, _ = marker_grammar.scan_lines(path, src, _comment_lines(tree))
    return found


def resolve(src: str, marker: Marker) -> Target:
    spec = spec_for(marker.path)
    tree = _tree(spec, src)

    if marker.scope is Scope.REGION:
        _, regions = marker_grammar.scan_lines(marker.path, src, _comment_lines(tree))
        if marker.id not in regions:
            raise ResolutionError(f"{marker.path}: region '{marker.id}' is unbalanced")
        start, end = regions[marker.id]
        if start > end:
            raise ResolutionError(f"{marker.path}: region '{marker.id}' is empty")
        return Target(marker.path, None, start, end, region=True)

    if marker.scope is Scope.FILE:
        return Target(marker.path, None, 1, max(len(src.splitlines()), 1))

    defs = tree.definitions()
    comment_lines = {line for line, _ in tree.comments()}
    lines = src.splitlines()

    def only_filler_between(a: int, b: int) -> bool:
        return all(
            n in comment_lines or not lines[n - 1].strip() for n in range(a + 1, b)
        )

    following = [
        d
        for d in defs
        if d.start > marker.lineno and only_filler_between(marker.lineno, d.start)
    ]
    if following:
        chosen = min(following, key=lambda d: d.start)
        return Target(marker.path, chosen.qualname, chosen.start, chosen.end)

    enclosing = [d for d in defs if d.start <= marker.lineno <= d.end]
    if enclosing:
        chosen = max(enclosing, key=lambda d: d.start)
        return Target(marker.path, chosen.qualname, chosen.start, chosen.end)

    raise ResolutionError(
        f"{marker.path}:{marker.lineno}: keystone '{marker.id}' attaches to nothing. "
        "Move it above a definition, or use a keystone:start / keystone:end region."
    )


def _definition(tree, target: Target) -> Definition:
    for d in tree.definitions():
        if d.qualname == target.qualname:
            return d
    raise ResolutionError(f"{target}: no longer present in {target.path}")


def hashes(src: str, target: Target) -> tuple[str, str]:
    if target.region:
        return fallback.hashes(src, target)
    spec = spec_for(target.path)
    tree = _tree(spec, src)
    definition = None if target.qualname is None else _definition(tree, target)
    rendered = tree.render(definition)
    comments = [
        text
        for line, text in _comment_lines(tree)
        if target.start <= line <= target.end and not marker_grammar.is_marker(text)
    ]
    # Masked content is hashed verbatim, from the target's own lines only.
    extra = preprocessed(spec.preprocessor, canonical_source(src, target))[1]
    body = rendered + ("\n--masked--\n" + extra if extra else "")
    return digest(body), digest(body + "\n--comments--\n" + "\n".join(comments))


def canonical_source(src: str, target: Target) -> str:
    if target.region:
        return fallback.canonical_source(src, target)
    return "\n".join(src.splitlines()[target.start - 1 : target.end])


def hash_stored_source(source: str, target: str) -> str:
    if "#L" in target:
        return fallback.hash_stored_source(source, target)
    spec = spec_for(target.split("::")[0])
    extra = preprocessed(spec.preprocessor, source)[1]
    suffix = "\n--masked--\n" + extra if extra else ""
    if "::" not in target:
        return digest(_tree(spec, source, fragment=True).render(None) + suffix)
    tree = _tree(spec, textwrap.dedent(source), fragment=True)
    defs = tree.definitions()
    return digest(tree.render(defs[0] if defs else None) + suffix)


def render_symbol(src: str, symbol: str) -> str | None:
    return None


def target_for_qualname(path: str, src: str, qualname: str) -> Target | None:
    for d in _tree(spec_for(path), src).definitions():
        if d.qualname == qualname:
            return Target(path, qualname, d.start, d.end)
    return None


def hasher_id_for_path(path: str) -> str:
    return hasher_id_for(spec_for(path))


def kind_for_path(path: str) -> str:
    spec = spec_for(path)
    return spec.preprocessor.name if spec.preprocessor else spec.parser.name


def duplicate_qualnames(path: str, src: str) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for d in _tree(spec_for(path), src).definitions():
        if d.qualname in seen:
            dupes.add(d.qualname)
        seen.add(d.qualname)
    return dupes


def comment_prefix(path: str) -> str:
    return spec_for(path).line_comment
