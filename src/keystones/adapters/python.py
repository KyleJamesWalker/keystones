"""Python adapter, built on stdlib `ast` and `tokenize` only."""

from __future__ import annotations

import ast
import io
import tokenize

from keystones import markers as marker_grammar
from keystones.adapters import fallback
from keystones.adapters.base import ResolutionError
from keystones.hashing import HASHER_ID, render, semantic_hash, text_hash
from keystones.models import Marker, Scope, Target

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# Decorators that switch a test off. A repo adds its own in config.
DISABLERS = frozenset(
    {
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "unittest.expectedFailure",
    }
)

name = "python"
hasher_id = HASHER_ID
extensions = (".py", ".pyi")


def _tokens(src: str) -> list[tokenize.TokenInfo]:
    try:
        return list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []


def _comments(src: str) -> list[tuple[int, str]]:
    return [(t.start[0], t.string) for t in _tokens(src) if t.type == tokenize.COMMENT]


def _regions(path: str, src: str) -> dict[tuple[str, str], tuple[int, int]]:
    return marker_grammar.scan_lines(path, src, _comments(src))[1]


def markers(path: str, src: str) -> list[Marker]:
    """Everything comes from the lexer, so a marker-shaped string is not a marker.

    That matters for regions too: a test fixture holding an example region in a
    triple-quoted string must not register one.
    """
    if marker_grammar.is_ignored(src):
        return []
    found = [
        m
        for m in marker_grammar.scan_lines(path, src, _comments(src))[0]
        if m.scope is Scope.REGION
    ]
    for tok in _tokens(src):
        if tok.type != tokenize.COMMENT:
            continue
        point = marker_grammar.parse_point(tok.string, path, tok.start[0])
        if point is not None:
            found.append(point)
    return found


def _marker_lines(src: str) -> set[int]:
    return {
        tok.start[0]
        for tok in _tokens(src)
        if tok.type == tokenize.COMMENT and marker_grammar.is_marker(tok.string)
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
    if marker.scope is Scope.REGION:
        regions = _regions(marker.path, src)
        if marker.key not in regions:
            raise ResolutionError(f"{marker.path}: region '{marker.id}' is unbalanced")
        start, end = regions[marker.key]
        if start > end:
            raise ResolutionError(f"{marker.path}: region '{marker.id}' is empty")
        return Target(marker.path, None, start, end, region=True)
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
        for name, node in _bindings(tree):
            if node.lineno == following and _constant(tree, name, marker.path):
                return Target(marker.path, name, following, node.end_lineno)

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
    constant = _constant(tree, target.qualname, target.path)
    if constant is not None:
        return constant
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
    if target.region:
        return fallback.hashes(src, target)
    node = _node_for(src, target)
    return semantic_hash(node), text_hash(
        node, _comments_in(src, target.start, target.end)
    )


def canonical_source(src: str, target: Target) -> str:
    """The stored code the reviewer reads, and the proof for a hasher migration."""
    if target.region:
        return fallback.canonical_source(src, target)
    node = _node_for(src, target)
    return ast.unparse(node)


def hash_stored_source(source: str, target: str) -> str:
    """Re-hash stored canonical source. Backs C5 and hasher migration proofs."""
    if "#L" in target:
        return fallback.hash_stored_source(source, target)
    tree = ast.parse(source)
    if "::" not in target:
        return semantic_hash(tree)
    if not tree.body:
        raise ResolutionError(f"{target}: stored source contains no definition")
    return semantic_hash(tree.body[0])


def target_for_qualname(path: str, src: str, qualname: str) -> Target | None:
    tree = ast.parse(src)
    for name, node in _definitions(tree):
        if name == qualname:
            return Target(path, name, _start_line(node), node.end_lineno)
    constant = _constant(tree, qualname, path)
    if constant is not None:
        return Target(path, qualname, constant.lineno, constant.end_lineno)
    return None


def _assignments(body: list[ast.stmt], prefix: str = "") -> list[tuple[str, ast.AST]]:
    out: list[tuple[str, ast.AST]] = []
    for node in body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        out += [(f"{prefix}{name}", node) for name in names]
    return out


def _module_assignments(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    return _assignments(tree.body)


def _class_attributes(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    return [
        pair
        for qualname, node in _definitions(tree)
        if isinstance(node, ast.ClassDef)
        for pair in _assignments(node.body, f"{qualname}.")
    ]


def _bindings(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Module constants and class attributes, as `NAME` and `Class.ATTR`."""
    return [*_module_assignments(tree), *_class_attributes(tree)]


def _constant(tree: ast.Module, name: str, path: str) -> ast.AST | None:
    """The one binding of `name`, which a node keystone may cover.

    A second binding would let a hash-identical decoy sit under the marker while
    the one that wins at runtime is rewritten, the hazard C6 guards for defs.
    """
    bound = [node for bound_name, node in _bindings(tree) if bound_name == name]
    if len(bound) > 1:
        raise ResolutionError(
            f"{path}: {name} is assigned more than once in the same scope, so a "
            "keystone on it cannot say which binding it protects"
        )
    return bound[0] if bound else None


def render_symbol(src: str, symbol: str) -> str | None:
    """Canonical text for a `depends` target: a definition, constant or attribute."""
    tree = ast.parse(src)
    for qualname, node in _definitions(tree):
        if qualname == symbol:
            return render(node)
    for name, node in _bindings(tree):
        if name == symbol:
            return render(node)
    return None


def _imports(tree: ast.Module) -> dict[str, str]:
    """Local names bound by module-level imports, to the dotted name they stand for."""
    names: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _dotted(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Call):
        expr = expr.func
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    return ".".join([expr.id, *reversed(parts)])


def _marks(body: list[ast.stmt]) -> list[ast.AST]:
    """What a `pytestmark = ...` in this scope applies, one expression per mark."""
    out: list[ast.AST] = []
    for name, node in _assignments(body):
        if name == "pytestmark" and node.value is not None:
            value = node.value
            out += value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
    return out


def disablers(src: str, qualname: str, extra: tuple[str, ...] = ()) -> list[str]:
    """Every decorator or mark that switches this definition off, and from where.

    Only the definition's own decorators are inside its hash. One on an
    enclosing class, or a `pytestmark`, disables it without moving a byte of it.
    """
    tree = ast.parse(src)
    defs = dict(_definitions(tree))
    node = defs.get(qualname)
    if not isinstance(node, _DEFS):
        return []
    imports = _imports(tree)
    wanted = DISABLERS | set(extra)
    found: list[str] = []

    def scan(exprs: list[ast.AST], where: str) -> None:
        for expr in exprs:
            written = _dotted(expr)
            if written is None:
                continue
            head, _, rest = written.partition(".")
            name = imports.get(head, head) + (f".{rest}" if rest else "")
            if any(name == w or name.endswith(f".{w}") for w in wanted):
                found.append(f"{name} on {where}")

    scan(node.decorator_list, qualname)
    parts = qualname.split(".")
    for depth in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:depth])
        enclosing = defs.get(prefix)
        if isinstance(enclosing, ast.ClassDef):
            scan(enclosing.decorator_list, f"class {prefix}")
            scan(_marks(enclosing.body), f"class {prefix} pytestmark")
    scan(_marks(tree.body), "module pytestmark")
    return sorted(set(found))


def hasher_id_for_path(path: str) -> str:
    return HASHER_ID


def duplicate_qualnames(path: str, src: str) -> set[str]:
    """Qualnames defined more than once in one file.

    Resolution picks the first, so a second definition of the same name lets a
    hash-identical decoy sit under the marker while the live one is rewritten.
    """
    seen: set[str] = set()
    dupes: set[str] = set()
    for qualname, _ in _definitions(ast.parse(src)):
        if qualname in seen:
            dupes.add(qualname)
        seen.add(qualname)
    return dupes


def comment_prefix(path: str) -> str:
    return "#"


def kind_for_path(path: str) -> str:
    return "python"
