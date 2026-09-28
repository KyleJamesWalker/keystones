"""YAML hashed by what it means, not by how it is laid out.

`hash=yaml` canonicalises the parsed document, so a reformat, a key reorder
or a quote-style change is invisible, and a comment edit is C4 rather than
C3. A node keystone sits above a mapping key and covers that key's value, at
any depth, as `spec.replicas`. Regions parse on their own after a dedent.

Opt-in: the text basis stays the default for `.yaml` and `.yml`, so a repo
already gating YAML on text moves only when it asks to.
"""

from __future__ import annotations

import json
import textwrap
from functools import cache

from keystones import markers as marker_grammar
from keystones.adapters import fallback
from keystones.adapters.base import ResolutionError
from keystones.hashing import digest
from keystones.models import Marker, Scope, Target

name = "yaml"
extensions = (".yaml", ".yml")
KIND = "yaml"
# Regions parse on their own here, so they are not handed to the text adapter.
hashes_regions = True
SERIALIZER_VERSION = 1


@cache
def available() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


def _yaml():
    try:
        import yaml
    except ImportError as exc:
        raise ResolutionError(
            "hash=yaml needs the extra: pip install 'keystones[yaml]'"
        ) from exc
    return yaml


def hasher_id_for_path(path: str) -> str:
    version = _yaml().__version__ if available() else "missing"
    return f"keystones-yaml/{SERIALIZER_VERSION}+pyyaml@{version}"


def kind_for_path(path: str) -> str:
    return KIND


def comment_prefix(path: str) -> str:
    return "#"


def _comment_lines(src: str) -> list[tuple[int, str]]:
    """Whole-line comments only; a marker is always one of those."""
    return [
        (lineno, line)
        for lineno, line in enumerate(src.splitlines(), start=1)
        if line.lstrip().startswith("#")
    ]


def markers(path: str, src: str) -> list[Marker]:
    if marker_grammar.is_ignored(src):
        return []
    _documents(src)
    found, _ = marker_grammar.scan_lines(path, src, _comment_lines(src))
    return found


def _documents(src: str) -> list:
    yaml = _yaml()
    try:
        return list(yaml.compose_all(src))
    except yaml.YAMLError as exc:
        raise ResolutionError(f"does not parse as YAML: {exc}") from exc


def _construct(node) -> object:
    yaml = _yaml()
    return yaml.SafeLoader("").construct_object(node, deep=True)


def canonical(value: object) -> str:
    """One spelling per meaning: sorted keys, no whitespace, dates as text."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _end_line(node, lines: list[str]) -> int:
    """PyYAML ends a block collection at the next token; a scalar on its line."""
    yaml = _yaml()
    mark = node.end_mark
    block = mark.column == 0 or (
        isinstance(node, yaml.CollectionNode) and not node.flow_style
    )
    end = mark.line if block else mark.line + 1
    while end > node.start_mark.line + 1 and (
        not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")
    ):
        end -= 1
    return end


# A list item is named by one of these keys when it has one, so inserting or
# reordering items does not move the keystone. Otherwise by its index.
ITEM_KEYS = ("name", "id", "key")


def _item_selector(item, index: int) -> str:
    yaml = _yaml()
    if isinstance(item, yaml.MappingNode):
        scalars = {
            k.value: v.value
            for k, v in item.value
            if isinstance(k, yaml.ScalarNode) and isinstance(v, yaml.ScalarNode)
        }
        for key in ITEM_KEYS:
            if key in scalars:
                return f"[{key}={scalars[key]}]"
    return f"[{index}]"


def _keys(src: str) -> list[tuple[str, object, int, int]]:
    """Every mapping key and list item at any depth: (qualname, node, start, end).

    A keyed item is listed under its selector and, as an alias, its index.
    """
    yaml = _yaml()
    lines = src.splitlines()
    out: list[tuple[str, object, int, int]] = []

    def walk(node, prefix: str) -> None:
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if not isinstance(key, yaml.ScalarNode):
                    continue
                qualname = f"{prefix}.{key.value}" if prefix else str(key.value)
                out.append(
                    (qualname, value, key.start_mark.line + 1, _end_line(value, lines))
                )
                walk(value, qualname)
        elif isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                span = (item.start_mark.line + 1, _end_line(item, lines))
                selector = _item_selector(item, index)
                qualname = f"{prefix}{selector}"
                out.append((qualname, item, *span))
                if selector != f"[{index}]":
                    out.append((f"{prefix}[{index}]", item, *span))
                walk(item, qualname)

    for document in _documents(src):
        walk(document, "")
    return out


def _key_for(src: str, qualname: str, path: str):
    matches = [k for k in _keys(src) if k[0] == qualname]
    if len(matches) > 1:
        raise ResolutionError(
            f"{path}: {qualname} is defined more than once, so a keystone on it "
            "cannot say which one it protects"
        )
    return matches[0] if matches else None


def resolve(src: str, marker: Marker) -> Target:
    if marker.scope is Scope.REGION:
        _, regions = marker_grammar.scan_lines(marker.path, src, _comment_lines(src))
        if marker.key not in regions:
            raise ResolutionError(f"{marker.path}: region '{marker.id}' is unbalanced")
        start, end = regions[marker.key]
        if start > end:
            raise ResolutionError(f"{marker.path}: region '{marker.id}' is empty")
        return Target(marker.path, None, start, end, region=True)
    if marker.scope is Scope.FILE:
        _documents(src)
        return Target(marker.path, None, 1, max(len(src.splitlines()), 1))
    comments = {lineno for lineno, _ in _comment_lines(src)}
    lines = src.splitlines()
    following = marker.lineno + 1
    while following <= len(lines) and (
        following in comments or not lines[following - 1].strip()
    ):
        following += 1
    for qualname, _value, start, end in _keys(src):
        if start == following:
            _key_for(src, qualname, marker.path)
            return Target(marker.path, qualname, start, end)
    raise ResolutionError(
        f"{marker.path}:{marker.lineno}: keystone '{marker.id}' attaches to nothing. "
        "Move it above a mapping key, or use a keystone:start / keystone:end region."
    )


def target_for_qualname(path: str, src: str, qualname: str) -> Target | None:
    found = _key_for(src, qualname, path)
    if found is None:
        return None
    _, _, start, end = found
    return Target(path, qualname, start, end)


def _value_of(src: str, target: Target) -> object:
    if target.qualname is None:
        docs = [_construct(d) for d in _documents(src)]
        return docs[0] if len(docs) == 1 else docs
    found = _key_for(src, target.qualname, target.path)
    if found is None:
        raise ResolutionError(f"{target}: no longer present in {target.path}")
    return _construct(found[1])


def _region_value(body: str, target: str) -> object:
    yaml = _yaml()
    try:
        docs = list(yaml.safe_load_all(textwrap.dedent(body)))
    except yaml.YAMLError as exc:
        raise ResolutionError(
            f"{target}: the region does not parse as YAML on its own: {exc}. "
            "Widen it to a whole key, or gate it with hash=text."
        ) from exc
    return docs[0] if len(docs) == 1 else docs


def hashes(src: str, target: Target) -> tuple[str, str]:
    if target.region:
        body = fallback.canonical_source(src, target)
        value = _region_value(body, str(target))
    else:
        value = _value_of(src, target)
    rendered = canonical(value)
    comments = [
        text
        for lineno, text in _comment_lines(src)
        if target.start <= lineno <= target.end and not marker_grammar.is_marker(text)
    ]
    return digest(rendered), digest(rendered + "\n--comments--\n" + "\n".join(comments))


def canonical_source(src: str, target: Target) -> str:
    """The raw slice, so a reviewer reads YAML rather than a serialisation."""
    if target.region:
        return fallback.canonical_source(src, target)
    return "\n".join(src.splitlines()[target.start - 1 : target.end])


def hash_stored_source(source: str, target: str) -> str:
    if "#L" in target:
        return digest(canonical(_region_value(source, target)))
    if "::" not in target:
        docs = list(_yaml().safe_load_all(source))
        return digest(canonical(docs[0] if len(docs) == 1 else docs))
    # A stored node slice is `key: value` or `- item`, so the value is the one
    # entry of what it loads as.
    loaded = _yaml().safe_load(textwrap.dedent(source))
    if isinstance(loaded, dict) and len(loaded) == 1:
        return digest(canonical(next(iter(loaded.values()))))
    if isinstance(loaded, list) and len(loaded) == 1:
        return digest(canonical(loaded[0]))
    raise ResolutionError(f"{target}: stored source is not a single key or item")


def aliases(src: str, qualname: str) -> set[str]:
    """Every name for the node `qualname` names: a keyed item and its index."""
    keys = _keys(src)
    nodes = [node for name, node, *_ in keys if name == qualname]
    if len(nodes) != 1:
        return {qualname}
    return {name for name, node, *_ in keys if node is nodes[0]}


def render_symbol(src: str, symbol: str, path: str = "") -> str | None:
    found = _key_for(src, symbol, path)
    return None if found is None else canonical(_construct(found[1]))


def duplicate_qualnames(path: str, src: str) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for qualname, *_ in _keys(src):
        if qualname in seen:
            dupes.add(qualname)
        seen.add(qualname)
    return dupes
