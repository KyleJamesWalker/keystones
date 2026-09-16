"""Python adapter, built on stdlib `ast` and `tokenize` only."""

from __future__ import annotations

import ast
import io
import re
import tokenize

from keystones.adapters.base import ResolutionError
from keystones.hashing import HASHER_ID, semantic_hash, text_hash
from keystones.models import Marker, Scope, Target

MARKER_RE = re.compile(
    r"#\s*keystone(?:\(\s*([^)]*?)\s*\))?\s*:\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*$"
)

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

name = "python"
hasher_id = HASHER_ID
extensions = (".py", ".pyi")


def _tokens(src: str) -> list[tokenize.TokenInfo]:
    try:
        return list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []


def markers(path: str, src: str) -> list[Marker]:
    """Find markers by lexing, so a marker-shaped string literal is not one."""
    found = []
    for tok in _tokens(src):
        if tok.type != tokenize.COMMENT:
            continue
        match = MARKER_RE.search(tok.string)
        if not match:
            continue
        qualifiers = [q.strip() for q in (match.group(1) or "").split(",") if q.strip()]
        scope = Scope.FILE if "file" in qualifiers else Scope.NODE
        category = next((q for q in qualifiers if q != "file"), "default")
        found.append(
            Marker(
                id=match.group(2),
                category=category,
                scope=scope,
                path=path,
                lineno=tok.start[0],
            )
        )
    return found


def _marker_lines(src: str) -> set[int]:
    return {
        tok.start[0]
        for tok in _tokens(src)
        if tok.type == tokenize.COMMENT and MARKER_RE.search(tok.string)
    }


def _next_code_line(src: str, after: int) -> int | None:
    skip = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    for tok in _tokens(src):
        if tok.start[0] > after and tok.type not in skip and tok.string.strip():
            return tok.start[0]
    return None


# keystone: resolution-decorator-rule
def _start_line(node: ast.AST) -> int:
    """Decorators are part of the definition, so they set its first line."""
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decorators])


def _definitions(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    out: list[tuple[str, ast.AST]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEFS):
                qualname = f"{prefix}.{child.name}" if prefix else child.name
                out.append((qualname, child))
                walk(child, qualname)
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def resolve(src: str, marker: Marker) -> Target:
    tree = ast.parse(src)
    if marker.scope is Scope.FILE:
        end = (
            max((getattr(n, "end_lineno", 1) or 1) for n in tree.body)
            if tree.body
            else 1
        )
        return Target(path=marker.path, qualname=None, start=1, end=end)

    defs = _definitions(tree)
    following = _next_code_line(src, marker.lineno)
    if following is not None:
        for qualname, node in defs:
            if _start_line(node) == following:
                return Target(marker.path, qualname, following, node.end_lineno)

    enclosing = [
        (qualname, node)
        for qualname, node in defs
        if _start_line(node) <= marker.lineno <= (node.end_lineno or marker.lineno)
    ]
    if enclosing:
        qualname, node = max(enclosing, key=lambda pair: _start_line(pair[1]))
        return Target(marker.path, qualname, _start_line(node), node.end_lineno)

    raise ResolutionError(
        f"{marker.path}:{marker.lineno}: keystone '{marker.id}' attaches to nothing. "
        "Move it above a definition, or use keystone(file) for file scope."
    )


def _node_for(src: str, target: Target) -> ast.AST:
    tree = ast.parse(src)
    if target.qualname is None:
        return tree
    for qualname, node in _definitions(tree):
        if qualname == target.qualname:
            return node
    raise ResolutionError(f"{target}: no longer present in {target.path}")


def _comments_in(src: str, start: int, end: int) -> list[str]:
    skip = _marker_lines(src)
    return [
        tok.string.strip()
        for tok in _tokens(src)
        if tok.type == tokenize.COMMENT
        and start <= tok.start[0] <= end
        and tok.start[0] not in skip
    ]


def hashes(src: str, target: Target) -> tuple[str, str]:
    node = _node_for(src, target)
    return semantic_hash(node), text_hash(
        node, _comments_in(src, target.start, target.end)
    )


def canonical_source(src: str, target: Target) -> str:
    """The stored code the reviewer reads, and the proof for a hasher migration."""
    node = _node_for(src, target)
    return ast.unparse(node)


def hash_fragment(source: str, is_module: bool) -> str:
    """Re-hash stored canonical source. Backs C5 and hasher migration proofs."""
    tree = ast.parse(source)
    node = tree if is_module else tree.body[0]
    return semantic_hash(node)


def target_for_qualname(path: str, src: str, qualname: str) -> Target | None:
    for name, node in _definitions(ast.parse(src)):
        if name == qualname:
            return Target(path, name, _start_line(node), node.end_lineno)
    return None
