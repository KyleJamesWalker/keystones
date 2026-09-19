"""Generic tree-sitter driver. Adding a language is a table entry, not code.

Installed with the `all` extra. Grammars are pinned exactly, because their
output feeds the hash and a floating grammar would fail every keystone in a
fleet on the same morning.
"""

from __future__ import annotations

import dataclasses
import hashlib
import textwrap
import warnings
from dataclasses import dataclass
from functools import cache

from keystones import markers as marker_grammar
from keystones.adapters import fallback
from keystones.adapters.base import ResolutionError
from keystones.adapters.masking import (  # noqa: F401
    ContractError,
    PreprocessorRefused,
    preprocessed,
)
from keystones.config import options_digest
from keystones.hashing import digest
from keystones.models import Marker, Scope, Target

SERIALIZER_VERSION = 2


class Unavailable(Exception):
    """The `all` extra is not installed."""


class ParseError(ResolutionError):
    """The grammar could not read the file.

    Hashing an error-recovery tree is worse than refusing one. Recovery shape
    is the least stable part of a grammar's output, so the hash would move on a
    pack bump with nobody having touched the file.
    """


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
    line_comment: str = "//"
    # Node-type prefixes whose text is case-insensitive. SQL keywords are the
    # case: sqlfluff rewrites `select` to `SELECT` without changing meaning.
    fold_case: tuple[str, ...] = ()
    # A plugin that masks what this grammar cannot read. See keystones.preprocess.
    preprocessor: object | None = None


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
        line_comment="#",
    ),
    LanguageSpec(
        language="sql_bigquery",
        extensions=(),
        definitions=frozenset({"create_table_statement", "cte"}),
        name_fields=(),
        label_children=("identifier",),
        line_comment="--",
    ),
    LanguageSpec(
        language="sql",
        extensions=(".sql",),
        definitions=frozenset(
            {"create_view", "create_table", "create_function", "cte"}
        ),
        # `marginalia` is this grammar's name for a /* */ block.
        comments=frozenset({"comment", "marginalia"}),
        name_fields=(),
        label_children=("identifier", "object_reference"),
        line_comment="--",
        fold_case=("keyword_",),
    ),
)

_BY_EXTENSION = {ext: spec for spec in SPECS for ext in spec.extensions}


def spec_from_config(language) -> LanguageSpec | None:
    """Build a spec from one validated `[[tool.keystones.language]]` table.

    None when the table declares no parser: it is only setting a default basis.
    """
    if language.builtin is not None:
        shipped = next(s for s in SPECS if s.language == language.builtin)
        return dataclasses.replace(
            shipped,
            extensions=language.extensions,
            preprocessor=language.preprocessor,
        )
    if language.grammar is None:
        return None
    optional_pre = (
        {"preprocessor": language.preprocessor} if language.preprocessor else {}
    )
    optional = {
        field: getattr(language, field)
        for field in (
            "comments",
            "name_fields",
            "wrappers",
            "label_children",
            "fold_case",
        )
        if getattr(language, field) is not None
    }
    if language.line_comment is not None:
        optional["line_comment"] = language.line_comment
    return LanguageSpec(
        language=language.grammar,
        extensions=language.extensions,
        definitions=language.definitions,
        **optional,
        **optional_pre,
    )


def install_user_specs(specs: tuple[LanguageSpec, ...]) -> None:
    """Rebuild extension routing from the builtins plus this repo's tables.

    Always rebuilds from SPECS, never from the current map, so loading a second
    repo drops the first one's languages instead of inheriting them.
    """
    global _BY_EXTENSION, extensions
    _BY_EXTENSION = {ext: spec for spec in (*SPECS, *specs) for ext in spec.extensions}
    extensions = tuple(_BY_EXTENSION)


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
        with warnings.catch_warnings():
            # tree_sitter._binding has not declared Py_MOD_GIL_NOT_USED, so a
            # free-threaded build re-enables the GIL and warns on every run.
            # Not actionable here, and not the user's problem.
            warnings.filterwarnings(
                "ignore", message=".*interpreter lock.*", category=RuntimeWarning
            )
            from tree_sitter_language_pack import get_parser
    except ImportError as exc:
        raise Unavailable(
            "tree-sitter support needs the extra: pip install 'keystones[all]'"
        ) from exc
    try:
        return get_parser(language)
    except Exception as exc:
        # A configured grammar the pack does not carry. Left to escape, this
        # surfaces as a traceback naming neither the grammar nor the table.
        raise Unavailable(
            f"no grammar '{language}' in tree-sitter-language-pack "
            f"{_pack_version()}. Check the name against the pack's language "
            "list, or drop the [[tool.keystones.language]] table for it."
        ) from exc


def _spec_digest(spec: LanguageSpec) -> str:
    """Only the fields the serialiser reads, sorted: set order follows PYTHONHASHSEED.

    Extensions and the comment leader are excluded on purpose. Neither can move
    a hash, and billing every consumer a migration for a cosmetic edit is how a
    gate gets uninstalled.
    """
    payload = "|".join(
        (
            ",".join(sorted(spec.definitions)),
            ",".join(sorted(spec.comments)),
            ",".join(spec.name_fields),
            ",".join(sorted(spec.wrappers)),
            ",".join(spec.label_children),
            ",".join(spec.fold_case),
            spec.preprocessor.id if spec.preprocessor else "",
            options_digest(spec.preprocessor.options) if spec.preprocessor else "",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def hasher_id_for(spec: LanguageSpec) -> str:
    """Grammar version and spec are both part of the identity.

    The spec digest is what lets a language be defined outside this file: an
    edit to it surfaces as a hasher mismatch that `migrate` can prove across,
    rather than as drift in code nobody touched.
    """
    # The plugin is in the digest already; naming it here is so a person can
    # see it without decoding a hash, and so a plugin that breaks its own
    # hashes can be recognised rather than appearing to change by magic.
    plugin = f"+{spec.preprocessor.id}" if spec.preprocessor else ""
    return (
        f"keystones-ts/{SERIALIZER_VERSION}+{spec.language}"
        f"@{_pack_version()}/{_spec_digest(spec)}{plugin}"
    )


def _parse(spec: LanguageSpec, src: str, strict: bool = True):
    src, _ = preprocessed(spec.preprocessor, src)
    tree = _parser(spec.language).parse(src.encode("utf-8"))
    if strict and tree.root_node.has_error:
        raise ParseError(
            f"does not parse as {spec.language}. A templating layer the grammar "
            "cannot read (dbt Jinja, ERB) will do this."
        )
    return tree


# Separators a formatter adds or removes freely. Prettier writes a trailing
# comma whenever it wraps arguments, and ASI makes semicolons optional. The tree
# structure already encodes where one item ends and the next begins, so dropping
# these costs no precision. Operators are NOT here: + and - must not collide.
# Formatters add or remove a trailing separator freely; the tree already encodes
# where one item ends. Interior commas are kept, because in JS `[a,,b]` is a
# different array from `[a,b]`.
IGNORABLE_TRAILING = frozenset({",", ";"})

_JS_LIKE = frozenset({"typescript", "tsx", "javascript"})


def _is_trailing_separator(node) -> bool:
    if node.is_named or node.text.decode("utf-8", "replace") not in IGNORABLE_TRAILING:
        return False
    parent = node.parent
    if parent is None:
        return False
    siblings = [c for c in parent.children if c.type not in ("comment",)]
    index = siblings.index(node)
    return all(not c.is_named for c in siblings[index + 1 :])


def _unwrap_parameter(node):
    """A bare `identifier` and `required_parameter(identifier)` are one param."""
    if node.type in ("required_parameter", "optional_parameter"):
        named = [c for c in node.children if c.is_named]
        if len(named) == 1:
            return named[0]
    return node


def _normalise_leaf(node, spec: LanguageSpec) -> str | None:
    """Fold away spellings a formatter changes without changing meaning."""
    text = node.text.decode("utf-8", "replace")
    if spec.fold_case and node.type.startswith(spec.fold_case):
        return f"{node.type}:{text.lower()!r}"
    if spec.language in _JS_LIKE and node.type == "number":
        try:
            value = float(text.replace("_", ""))
        except ValueError:
            return f"number:{text}"
        return f"number:{value!r}"
    # An anonymous leaf is a literal from the grammar, so its type is the only
    # spelling that can produce it. Folding is a no-op where the grammar is
    # case-sensitive and the whole point where it is not, as in BigQuery SQL,
    # whose keywords are anonymous `SELECT` rather than a named keyword node.
    if not node.is_named and text.isalpha():
        return f"{node.type}:{text.lower()!r}"
    return f"{node.type}:{text!r}"


def _render(
    node, comments: frozenset[str], spec: LanguageSpec | None = None
) -> str | None:
    if node.type in comments:
        return None
    if spec is not None:
        if _is_trailing_separator(node):
            return None
        if spec.language in _JS_LIKE:
            # `(a)` and `a` are the same expression; prettier removes the parens.
            if node.type == "parenthesized_expression":
                inner = [c for c in node.children if c.is_named]
                if len(inner) == 1:
                    return _render(inner[0], comments, spec)
            # Quote style is prettier's choice. `string` carries its quote
            # tokens as children, so this intercepts the node, not the leaf.
            if node.type == "string":
                return "string:" + "".join(
                    c.text.decode("utf-8", "replace")
                    for c in node.children
                    if c.type in ("string_fragment", "escape_sequence")
                )
            # `x => x` and `(x) => x` differ only in prettier's arrowParens,
            # and TypeScript wraps each parameter in required_parameter while
            # JavaScript does not.
            if node.type == "arrow_function":
                params, rest = [], []
                for child in node.children:
                    if not params and child.type == "identifier":
                        params = [_render(child, comments, spec)]
                    elif not params and child.type == "formal_parameters":
                        params = [
                            _render(_unwrap_parameter(gc), comments, spec)
                            for gc in child.children
                            if gc.is_named
                        ]
                    else:
                        rendered = _render(child, comments, spec)
                        if rendered is not None:
                            rest.append(rendered)
                joined = ",".join(x for x in params if x)
                return f"arrow_function(params({joined}),{','.join(rest)})"
    if node.child_count == 0:
        if spec is not None:
            return _normalise_leaf(node, spec)
        return f"{node.type}:{node.text.decode('utf-8', 'replace')!r}"
    parts = [
        rendered
        for child in node.children
        if (rendered := _render(child, comments, spec)) is not None
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


def _outermost(node, spec: LanguageSpec):
    """Climb wrappers so `export` and a const/let/var keyword are inside the hash.

    Without this, un-exporting a symbol or changing `const` to `let` is invisible.
    """
    current = node
    while current.parent is not None and current.parent.type in spec.wrappers:
        current = current.parent
    return current


def hashes(src: str, target: Target) -> tuple[str, str]:
    if target.region:
        return fallback.hashes(src, target)
    spec = spec_for(target.path)
    if target.qualname is None:
        rendered = _render(_parse(spec, src).root_node, spec.comments, spec)
    else:
        node, spec = _node_for(src, target)
        rendered = _render(_outermost(node, spec), spec.comments, spec)
    comments = [
        text
        for line, text in _comment_lines(_parse(spec, src).root_node, spec)
        if target.start <= line <= target.end and not marker_grammar.is_marker(text)
    ]
    # Masked-out content is not an escape hatch: it is hashed verbatim. Taken
    # from the target's own lines, not the file's, or a span anywhere in the
    # file would trip every keystone in it.
    extra = preprocessed(spec.preprocessor, canonical_source(src, target))[1]
    body = (rendered or "") + ("\n--masked--\n" + extra if extra else "")
    semantic = digest(body)
    return semantic, digest(body + "\n--comments--\n" + "\n".join(comments))


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
    extra = preprocessed(spec.preprocessor, source)[1]
    suffix = "\n--masked--\n" + extra if extra else ""
    if "::" not in target:
        root = _parse(spec, source, strict=False).root_node
        return digest((_render(root, spec.comments, spec) or "") + suffix)

    # The stored slice is the node's own text, so a method arrives indented
    # and without its class. Dedent it and take the first definition found at
    # any depth rather than matching the qualname, which has no context here.
    fragment = textwrap.dedent(source)

    root = _parse(spec, fragment, strict=False).root_node
    for _name, node in _definitions(root, spec):
        # Must render from the same node `hashes` does: the outermost wrapper.
        return digest(
            (_render(_outermost(node, spec), spec.comments, spec) or "") + suffix
        )

    if spec.language in _JS_LIKE:
        # A method fragment does not parse as a method standing alone, so it
        # needs a class shell; the shell itself is a definition, hence the
        # prefix filter.
        shell = "__keystones__"
        root = _parse(spec, f"class {shell} {{\n{fragment}\n}}", strict=False).root_node
        for name, node in _definitions(root, spec):
            if name.startswith(f"{shell}."):
                return digest((_render(node, spec.comments, spec) or "") + suffix)

    for child in root.children:
        if child.is_named and child.type not in spec.comments:
            return digest((_render(child, spec.comments, spec) or "") + suffix)
    raise ResolutionError(f"{target}: stored source contains no definition")


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


def kind_for_path(path: str) -> str:
    """The plugin's name when one is attached: that is what a reviewer needs."""
    spec = spec_for(path)
    return spec.preprocessor.name if spec.preprocessor else spec.language


def duplicate_qualnames(path: str, src: str) -> set[str]:
    """See the Python adapter; same hazard, same resolution."""
    spec = spec_for(path)
    seen: set[str] = set()
    dupes: set[str] = set()
    for qualname, _ in _definitions(_parse(spec, src).root_node, spec):
        if qualname in seen:
            dupes.add(qualname)
        seen.add(qualname)
    return dupes


def comment_prefix(path: str) -> str:
    return spec_for(path).line_comment
