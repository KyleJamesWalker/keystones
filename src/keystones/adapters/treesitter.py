"""Generic tree-sitter driver. Adding a language is a table entry, not code.

Installed with the `all` extra. Grammars are pinned exactly, because their
output feeds the hash and a floating grammar would fail every keystone in a
fleet on the same morning.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from keystones import markers as marker_grammar
from keystones.adapters import fallback
from keystones.adapters.base import ResolutionError
from keystones.hashing import digest
from keystones.models import Marker, Scope, Target

SERIALIZER_VERSION = 1


class Unavailable(Exception):
    """The `all` extra is not installed."""


@dataclass(frozen=True)
class LanguageSpec:
    language: str
    extensions: tuple[str, ...]
    definitions: frozenset[str]
    comments: frozenset[str] = frozenset({"comment", "line_comment", "block_comment"})
    name_fields: tuple[str, ...] = ("name",)
    # Nodes that wrap a definition and belong to it, such as `export` or a
    # decorator list. They set the definition's first line.
    wrappers: frozenset[str] = frozenset()
    label_children: tuple[str, ...] = ()


SPECS: tuple[LanguageSpec, ...] = (
    LanguageSpec(
        language="typescript",
        extensions=(".ts",),
        definitions=frozenset(
            {
                "function_declaration",
                "generator_function_declaration",
                "class_declaration",
                "method_definition",
                "interface_declaration",
                "type_alias_declaration",
                "variable_declarator",
                "abstract_class_declaration",
            }
        ),
        wrappers=frozenset(
            {"export_statement", "lexical_declaration", "ambient_declaration"}
        ),
    ),
    LanguageSpec(
        language="tsx",
        extensions=(".tsx",),
        definitions=frozenset(
            {
                "function_declaration",
                "class_declaration",
                "method_definition",
                "interface_declaration",
                "type_alias_declaration",
                "variable_declarator",
            }
        ),
        wrappers=frozenset({"export_statement", "lexical_declaration"}),
    ),
    LanguageSpec(
        language="javascript",
        extensions=(".js", ".jsx", ".mjs", ".cjs"),
        definitions=frozenset(
            {
                "function_declaration",
                "generator_function_declaration",
                "class_declaration",
                "method_definition",
                "variable_declarator",
            }
        ),
        wrappers=frozenset({"export_statement", "lexical_declaration"}),
    ),
    LanguageSpec(
        language="go",
        extensions=(".go",),
        definitions=frozenset(
            {"function_declaration", "method_declaration", "type_spec", "const_spec"}
        ),
        wrappers=frozenset({"type_declaration", "const_declaration"}),
    ),
    LanguageSpec(
        language="hcl",
        extensions=(".tf", ".hcl", ".tfvars"),
        definitions=frozenset({"block"}),
        name_fields=(),
        label_children=("identifier", "string_lit"),
    ),
)

_BY_EXTENSION = {ext: spec for spec in SPECS for ext in spec.extensions}


def spec_for(path: str) -> LanguageSpec | None:
    for ext, spec in _BY_EXTENSION.items():
        if path.endswith(ext):
            return spec
    return None


@cache
def _pack_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("tree-sitter-language-pack")
    except PackageNotFoundError:
        return "unknown"


@cache
def _parser(language: str):
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError as exc:
        raise Unavailable(
            "tree-sitter support needs the extra: pip install 'keystones[all]'"
        ) from exc
    return get_parser(language)


def hasher_id_for(spec: LanguageSpec) -> str:
    """Grammar version is part of the identity, so a bump is visible as one."""
    return f"keystones-ts/{SERIALIZER_VERSION}+{spec.language}@{_pack_version()}"


def _parse(spec: LanguageSpec, src: str):
    return _parser(spec.language).parse(src.encode("utf-8"))


# Separators a formatter adds or removes freely. Prettier writes a trailing
# comma whenever it wraps arguments, and ASI makes semicolons optional. The tree
# structure already encodes where one item ends and the next begins, so dropping
# these costs no precision. Operators are NOT here: + and - must not collide.
IGNORABLE_TOKENS = frozenset({",", ";"})


def _render(node, comments: frozenset[str]) -> str | None:
    if node.type in comments:
        return None
    if node.child_count == 0:
        if (
            not node.is_named
            and node.text.decode("utf-8", "replace") in IGNORABLE_TOKENS
        ):
            return None
        return f"{node.type}:{node.text.decode('utf-8', 'replace')!r}"
    parts = [
        rendered
        for child in node.children
        if (rendered := _render(child, comments)) is not None
    ]
    return f"{node.type}({','.join(parts)})"


def _node_name(node, spec: LanguageSpec) -> str | None:
    for field_name in spec.name_fields:
        child = node.child_by_field_name(field_name)
        if child is not None:
            return child.text.decode("utf-8", "replace")
    if spec.label_children:
        labels = [
            c.text.decode("utf-8", "replace").strip('"')
            for c in node.children
            if c.type in spec.label_children
        ]
        if labels:
            return ".".join(labels)
    return None


def _definitions(root, spec: LanguageSpec) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []

    def walk(node, prefix: str) -> None:
        for child in node.children:
            if child.type in spec.definitions:
                name = _node_name(child, spec)
                qualname = f"{prefix}.{name}" if prefix and name else (name or "")
                if qualname:
                    out.append((qualname, child))
                    walk(child, qualname)
                    continue
            walk(child, prefix)

    walk(root, "")
    return out


def _start_line(node, spec: LanguageSpec) -> int:
    """A wrapper such as `export` belongs to the definition it wraps."""
    current = node
    while current.parent is not None and current.parent.type in spec.wrappers:
        current = current.parent
    return current.start_point[0] + 1


def _comment_lines(root, spec: LanguageSpec) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    def walk(node) -> None:
        if node.type in spec.comments:
            text = node.text.decode("utf-8", "replace")
            for offset, line in enumerate(text.splitlines()):
                out.append((node.start_point[0] + 1 + offset, line))
        for child in node.children:
            walk(child)

    walk(root)
    return sorted(out)


def _next_code_line(root, spec: LanguageSpec, after: int) -> int | None:
    best: int | None = None

    def walk(node) -> None:
        nonlocal best
        if node.type in spec.comments:
            return
        if node.child_count == 0:
            line = node.start_point[0] + 1
            if line > after and (best is None or line < best):
                best = line
            return
        for child in node.children:
            walk(child)

    walk(root)
    return best


def markers(path: str, src: str) -> list[Marker]:
    spec = spec_for(path)
    root = _parse(spec, src).root_node
    found, _ = marker_grammar.scan_lines(path, src, _comment_lines(root, spec))
    return found


def _regions(path: str, src: str) -> dict[str, tuple[int, int]]:
    spec = spec_for(path)
    root = _parse(spec, src).root_node
    return marker_grammar.scan_lines(path, src, _comment_lines(root, spec))[1]


def resolve(src: str, marker: Marker) -> Target:
    spec = spec_for(marker.path)
    root = _parse(spec, src).root_node

    if marker.scope is Scope.REGION:
        regions = _regions(marker.path, src)
        if marker.id not in regions:
            raise ResolutionError(f"{marker.path}: region '{marker.id}' is unbalanced")
        start, end = regions[marker.id]
        if start > end:
            raise ResolutionError(f"{marker.path}: region '{marker.id}' is empty")
        return Target(marker.path, None, start, end, region=True)

    if marker.scope is Scope.FILE:
        return Target(marker.path, None, 1, max(len(src.splitlines()), 1))

    defs = _definitions(root, spec)
    following = _next_code_line(root, spec, marker.lineno)
    if following is not None:
        for qualname, node in defs:
            if _start_line(node, spec) == following:
                return Target(marker.path, qualname, following, node.end_point[0] + 1)

    enclosing = [
        (qualname, node)
        for qualname, node in defs
        if _start_line(node, spec) <= marker.lineno <= node.end_point[0] + 1
    ]
    if enclosing:
        qualname, node = max(enclosing, key=lambda pair: _start_line(pair[1], spec))
        return Target(
            marker.path, qualname, _start_line(node, spec), node.end_point[0] + 1
        )

    raise ResolutionError(
        f"{marker.path}:{marker.lineno}: keystone '{marker.id}' attaches to nothing. "
        "Move it above a definition, or use a keystone:start / keystone:end region."
    )


def _node_for(src: str, target: Target):
    spec = spec_for(target.path)
    root = _parse(spec, src).root_node
    for qualname, node in _definitions(root, spec):
        if qualname == target.qualname:
            return node, spec
    raise ResolutionError(f"{target}: no longer present in {target.path}")


def hashes(src: str, target: Target) -> tuple[str, str]:
    if target.region:
        return fallback.hashes(src, target)
    spec = spec_for(target.path)
    if target.qualname is None:
        rendered = _render(_parse(spec, src).root_node, spec.comments)
    else:
        node, spec = _node_for(src, target)
        rendered = _render(node, spec.comments)
    comments = [
        text
        for line, text in _comment_lines(_parse(spec, src).root_node, spec)
        if target.start <= line <= target.end
        and not marker_grammar.looks_like_a_marker(text)
    ]
    semantic = digest(rendered or "")
    return semantic, digest((rendered or "") + "\n--comments--\n" + "\n".join(comments))


def canonical_source(src: str, target: Target) -> str:
    """The raw slice, so a reviewer reads real code rather than a serialisation."""
    if target.region:
        return fallback.canonical_source(src, target)
    lines = src.splitlines()
    return "\n".join(lines[target.start - 1 : target.end])


def hash_stored_source(source: str, target: str) -> str:
    if "#L" in target:
        return fallback.hash_stored_source(source, target)
    path = target.split("::")[0]
    spec = spec_for(path)
    root = _parse(spec, source).root_node
    if "::" not in target:
        return digest(_render(root, spec.comments) or "")
    qualname = target.split("::", 1)[1]
    for name, node in _definitions(root, spec):
        if name == qualname or name.endswith(f".{qualname.split('.')[-1]}"):
            return digest(_render(node, spec.comments) or "")
    raise ResolutionError(f"{target}: stored source does not contain {qualname}")


def render_symbol(src: str, symbol: str) -> str | None:
    return None


def target_for_qualname(path: str, src: str, qualname: str) -> Target | None:
    spec = spec_for(path)
    root = _parse(spec, src).root_node
    for name, node in _definitions(root, spec):
        if name == qualname:
            return Target(path, name, _start_line(node, spec), node.end_point[0] + 1)
    return None


extensions: tuple[str, ...] = tuple(_BY_EXTENSION)
name = "treesitter"


def available() -> bool:
    try:
        _parser("hcl")
    except Exception:
        return False
    return True


def hasher_id_for_path(path: str) -> str:
    return hasher_id_for(spec_for(path))
