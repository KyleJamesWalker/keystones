"""Version-stable canonical rendering of AST nodes.

`ast.unparse` is a pretty-printer and CPython does not guarantee its output
across minor versions, so it cannot back a stored hash. This renders node types
and field names instead, which only moves when the grammar moves.
"""

from __future__ import annotations

import ast
import hashlib

HASHER_ID = "keystones-ast/1"

# `ctx` is Load/Store/Del, fully implied by the node's position in the tree.
_SKIP_FIELDS = frozenset({"ctx"})


# keystone(hasher): hasher-empty-field-rule
def _is_default(value: object) -> bool:
    """Fields absent on older Pythons must render identically to empty ones.

    `type_params` (3.12) and `posonlyargs` (3.8) arrive as empty lists on code
    that does not use them; omitting empties keeps the hash stable across the
    versions in the support matrix.
    """
    return value is None or value == []


# keystone(hasher): hasher-render
def _render(node: object) -> str:
    if isinstance(node, ast.AST):
        parts = []
        for name in node._fields:
            if name in _SKIP_FIELDS:
                continue
            value = getattr(node, name, None)
            if _is_default(value):
                continue
            parts.append(f"{name}={_render(value)}")
        return f"{type(node).__name__}({','.join(parts)})"
    if isinstance(node, list):
        return f"[{','.join(_render(item) for item in node)}]"
    return f"{type(node).__name__}:{node!r}"


def render(node: ast.AST) -> str:
    """Canonical text for a node. Stable across formatting and comments."""
    return _render(node)


def semantic_hash(node: ast.AST) -> str:
    return _digest(render(node))


def text_hash(node: ast.AST, comments: list[str]) -> str:
    """Semantic rendering plus the comments inside the node's line range.

    Comments are not in the AST, so without this a `# do not reorder` line can
    be deleted from inside a keystone with the gate staying green.
    """
    payload = render(node) + "\n--comments--\n" + "\n".join(comments)
    return _digest(payload)


def _digest(payload: str) -> str:
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
