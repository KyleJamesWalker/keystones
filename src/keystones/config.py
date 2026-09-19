"""`[tool.keystones]` in pyproject.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from keystones.preprocess import Refused

DEFAULT_EXCLUDE_DIRS = (".git", "node_modules", "vendor", "generated")

# What a builtin spec fixes; a table may only take one whole or declare its own.
SPEC_SHAPE_KEYS = frozenset(
    {
        "definitions",
        "comments",
        "name_fields",
        "wrappers",
        "label_children",
        "fold_case",
    }
)

LANGUAGE_KEYS = frozenset(
    {
        "grammar",
        "extensions",
        "definitions",
        "comments",
        "name_fields",
        "wrappers",
        "label_children",
        "line_comment",
        "fold_case",
        "builtin",
        "hash",
        "preprocessor",
        "parser",
    }
)


@dataclass(frozen=True)
class Preprocessor:
    """A resolved `package:attribute` plugin, with the identity it declares."""

    name: str
    version: str
    fn: object
    path: str
    options: tuple[tuple[str, object], ...] = ()

    @property
    def id(self) -> str:
        return f"{self.name}/{self.version}"

    @property
    def kwargs(self) -> dict:
        return dict(self.options)


@dataclass(frozen=True)
class ParserPlugin:
    """A resolved parser factory, already called with its options."""

    name: str
    identity: str
    parser: object
    path: str
    options: tuple[tuple[str, object], ...] = ()


@dataclass(frozen=True)
class LanguageConfig:
    """One `[[tool.keystones.language]]` table, validated but not yet a spec.

    Building the spec needs the tree-sitter adapter, which the config layer
    must not depend on; a repo can be configured on an install that has no
    grammar pack at all.
    """

    grammar: str | None
    extensions: tuple[str, ...]
    definitions: frozenset[str] | None = None
    # Names a spec this package ships, instead of declaring one here.
    builtin: str | None = None
    # The basis markers in these files get when they do not name one.
    hash: str | None = None
    preprocessor: Preprocessor | None = None
    parser: ParserPlugin | None = None
    comments: frozenset[str] | None = None
    name_fields: tuple[str, ...] | None = None
    wrappers: frozenset[str] | None = None
    label_children: tuple[str, ...] | None = None
    line_comment: str | None = None
    fold_case: tuple[str, ...] | None = None


@dataclass
class Config:
    repo_root: Path
    root: str = "keystones"
    exclude: tuple[str, ...] = ()
    categories: tuple[str, ...] = ("default",)
    languages: tuple[LanguageConfig, ...] = ()

    @property
    def sidecar_root(self) -> Path:
        return self.repo_root / self.root

    def category_dir(self, category: str) -> Path:
        return self.sidecar_root / category

    def sidecar_path(self, category: str, keystone_id: str) -> Path:
        return self.category_dir(category) / f"{keystone_id}.md"

    @property
    def index_path(self) -> Path:
        return self.sidecar_root / "INDEX.md"

    def is_excluded(self, rel_path: str) -> bool:
        parts = PurePosixPath(rel_path).parts
        if any(part in DEFAULT_EXCLUDE_DIRS for part in parts):
            return True
        # fnmatch does not match `x/y` against `**/x/y`, and users write the
        # `**/` form expecting it to cover the repo root as well.
        return any(
            fnmatch(rel_path, pat)
            or (pat.startswith("**/") and fnmatch(rel_path, pat[3:]))
            for pat in self.exclude
        )


class ConfigError(Exception):
    pass


def _unoverridable() -> frozenset[str]:
    """Extensions a table cannot take, because taking them would do nothing.

    Python is not a tree-sitter spec, and adapter lookup reaches it first, so a
    table claiming .py would install a spec that never gets used.
    """
    from keystones.adapters import python as python_adapter

    return frozenset(python_adapter.extensions)


def _strs(table: dict, key: str, where: str) -> list[str]:
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: {key} must be a list of strings")
    return value


def _plugin_ref(value: object, where: str, key: str) -> tuple[str, dict]:
    """`"pkg:attr"` or `{ plugin = "pkg:attr", ... }`; the rest are options."""
    if isinstance(value, str):
        return value, {}
    if not isinstance(value, dict) or not isinstance(value.get("plugin"), str):
        raise ConfigError(
            f"{where}: {key} must be a string 'module:attribute' or a table "
            f'with a plugin key, such as {key} = {{ plugin = "pkg:attr" }}'
        )
    return value["plugin"], {k: v for k, v in value.items() if k != "plugin"}


def _check_call(fn, options: dict, where: str, key: str, *positional) -> None:
    """Bind the options now, so a typo is a config error and not a traceback."""
    import inspect

    try:
        inspect.signature(fn).bind(*positional, **options)
    except TypeError as exc:
        raise ConfigError(
            f"{where}: {key} does not accept these options: {exc}"
        ) from exc


def options_digest(*option_sets: tuple[tuple[str, object], ...]) -> str:
    import hashlib

    payload = "|".join(
        ",".join(f"{k}={v!r}" for k, v in options) for options in option_sets
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _builtin_specs() -> dict[str, object]:
    from keystones.adapters import treesitter

    return {spec.language: spec for spec in treesitter.SPECS}


def _preprocessor(spec: str, options: dict, where: str) -> Preprocessor:
    import functools
    from importlib import import_module

    if spec.count(":") != 1 or not all(spec.split(":")):
        raise ConfigError(
            f"{where}: preprocessor '{spec}' must be written module:attribute"
        )
    module_name, attribute = spec.split(":")
    try:
        module = import_module(module_name)
    except ImportError as exc:
        raise ConfigError(
            f"{where}: cannot import '{module_name}' for preprocessor '{spec}'. "
            "Is the plugin installed in the environment keystones runs in?"
        ) from exc
    fn = getattr(module, attribute, None)
    if fn is None:
        raise ConfigError(f"{where}: '{module_name}' has no attribute '{attribute}'")
    if not callable(fn):
        raise ConfigError(f"{where}: preprocessor '{spec}' is not callable")
    missing = [
        const
        for const in ("KEYSTONES_PREPROCESSOR_NAME", "KEYSTONES_PREPROCESSOR_VERSION")
        if not getattr(module, const, None)
    ]
    if missing:
        raise ConfigError(
            f"{where}: '{module_name}' does not declare {', '.join(missing)}. "
            "A preprocessor's name and version are part of the hash identity."
        )
    _check_call(fn, options, where, "preprocessor", "")
    bound = functools.partial(fn, **options) if options else fn
    try:
        # Empty text is the one input every mask must accept; it proves the
        # option values before any file does.
        bound("")
    except Refused:
        pass
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"{where}: preprocessor '{spec}' rejected its options: {exc}"
        ) from exc
    return Preprocessor(
        name=str(module.KEYSTONES_PREPROCESSOR_NAME),
        version=str(module.KEYSTONES_PREPROCESSOR_VERSION),
        fn=bound,
        path=spec,
        options=tuple(sorted(options.items())),
    )


def _parser_plugin(spec: str, options: dict, where: str) -> ParserPlugin:
    from importlib import import_module

    if spec.count(":") != 1 or not all(spec.split(":")):
        raise ConfigError(f"{where}: parser '{spec}' must be written module:attribute")
    module_name, attribute = spec.split(":")
    try:
        module = import_module(module_name)
    except ImportError as exc:
        raise ConfigError(
            f"{where}: cannot import '{module_name}' for parser '{spec}'. "
            "Is the plugin installed in the environment keystones runs in?"
        ) from exc
    factory = getattr(module, attribute, None)
    if factory is None:
        raise ConfigError(f"{where}: '{module_name}' has no attribute '{attribute}'")
    if not callable(factory):
        raise ConfigError(f"{where}: parser '{spec}' is not callable")
    _check_call(factory, options, where, "parser")
    try:
        parser = factory(**options)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"{where}: parser '{spec}' rejected its options: {exc}"
        ) from exc
    missing = [
        attr
        for attr in ("name", "identity", "parse", "parse_fragment")
        if not getattr(parser, attr, None)
    ]
    if missing:
        raise ConfigError(
            f"{where}: the parser from '{spec}' lacks {', '.join(missing)}. "
            "See keystones.parser for the contract."
        )
    return ParserPlugin(
        name=str(parser.name),
        identity=str(parser.identity),
        parser=parser,
        path=spec,
        options=tuple(sorted(options.items())),
    )


def _language(table: dict, index: int, claimed: dict[str, str]) -> LanguageConfig:
    where = f"[[tool.keystones.language]] #{index + 1}"
    unknown = sorted(set(table) - LANGUAGE_KEYS)
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) {', '.join(unknown)}. "
            f"Valid keys: {', '.join(sorted(LANGUAGE_KEYS))}"
        )
    if "extensions" not in table:
        raise ConfigError(f"{where}: missing required key extensions")
    if "grammar" in table and "builtin" in table:
        raise ConfigError(
            f"{where}: grammar and builtin are alternatives. Use builtin to take "
            "a spec this package ships, grammar to declare one here."
        )
    if "parser" in table and ("grammar" in table or "builtin" in table):
        raise ConfigError(
            f"{where}: parser is an alternative to grammar and builtin. A parser "
            "plugin supplies the tree itself."
        )
    if "parser" in table:
        shaped = sorted(SPEC_SHAPE_KEYS & set(table))
        if shaped:
            raise ConfigError(
                f"{where}: {', '.join(shaped)} shape a tree-sitter grammar and do "
                "not apply to a parser plugin."
            )

    builtin = table.get("builtin")
    if builtin is not None:
        shipped = _builtin_specs()
        if builtin not in shipped:
            raise ConfigError(
                f"{where}: no builtin spec '{builtin}'. "
                f"Available: {', '.join(sorted(shipped))}"
            )
        fixed = sorted((SPEC_SHAPE_KEYS | {"line_comment"}) & set(table))
        if fixed:
            raise ConfigError(
                f"{where}: builtin '{builtin}' brings its own {', '.join(fixed)}. "
                "Declare the spec with grammar to change them."
            )

    grammar = table.get("grammar")
    if grammar is not None and (not isinstance(grammar, str) or not grammar):
        raise ConfigError(f"{where}: grammar must be a non-empty string")
    if grammar is not None and "definitions" not in table:
        raise ConfigError(f"{where}: missing required key definitions")
    parser = None
    if "parser" in table:
        ref, options = _plugin_ref(table["parser"], where, "parser")
        parser = _parser_plugin(ref, options, where)
    named = grammar or builtin or (parser.name if parser else None)
    label = "grammar" if grammar else "builtin" if builtin else "parser"
    where = f"{where} ({label} '{named}')" if named else where

    extensions = _strs(table, "extensions", where)
    if not extensions:
        raise ConfigError(f"{where}: extensions must not be empty")
    for ext in extensions:
        if not ext.startswith("."):
            raise ConfigError(f"{where}: extension '{ext}' must start with a dot")
        # Taking an extension from a builtin is fine: a table in a reviewed
        # pyproject is the opposite of the silent rebinding this guards
        # against. Two tables fighting over one extension is still a mistake.
        if ext in claimed:
            raise ConfigError(
                f"{where}: extension '{ext}' is already claimed by "
                f"'{claimed[ext]}'. One extension, one table."
            )
        if ext in _unoverridable():
            raise ConfigError(
                f"{where}: '{ext}' is handled by the Python adapter and cannot "
                "be reassigned."
            )
        claimed[ext] = named or "text"

    definitions = None
    if grammar is not None:
        definitions = _strs(table, "definitions", where)
        if not definitions:
            raise ConfigError(
                f"{where}: definitions must not be empty; without node types "
                "there is nothing to attach a keystone to"
            )

    preprocessor = None
    if "preprocessor" in table:
        if named is None:
            raise ConfigError(
                f"{where}: a preprocessor needs a grammar, builtin or parser to "
                "feed; it "
                "masks text so a parser can read it."
            )
        ref, options = _plugin_ref(table["preprocessor"], where, "preprocessor")
        preprocessor = _preprocessor(ref, options, where)

    default_hash = table.get("hash")
    if default_hash is not None:
        if not isinstance(default_hash, str):
            raise ConfigError(f"{where}: hash must be a string")
        offered = preprocessor.name if preprocessor else named
        available = {"text"} | ({offered} if offered else set())
        if default_hash not in available:
            raise ConfigError(
                f"{where}: hash '{default_hash}' is not a basis this table "
                f"offers. Available: {', '.join(sorted(available))}"
            )

    optional: dict[str, object] = {}
    for key in ("comments", "wrappers"):
        if key in table:
            optional[key] = frozenset(_strs(table, key, where))
    for key in ("name_fields", "label_children", "fold_case"):
        if key in table:
            optional[key] = tuple(_strs(table, key, where))
    if "line_comment" in table:
        if not isinstance(table["line_comment"], str):
            raise ConfigError(f"{where}: line_comment must be a string")
        optional["line_comment"] = table["line_comment"]

    return LanguageConfig(
        grammar=grammar,
        extensions=tuple(extensions),
        definitions=frozenset(definitions) if definitions else None,
        builtin=builtin,
        hash=default_hash,
        preprocessor=preprocessor,
        parser=parser,
        **optional,
    )


def _languages(data: dict) -> tuple[LanguageConfig, ...]:
    tables = data.get("language", [])
    if not isinstance(tables, list):
        raise ConfigError("tool.keystones.language must be an array of tables")
    claimed: dict[str, str] = {}
    return tuple(_language(t, i, claimed) for i, t in enumerate(tables))


def load(repo_root: Path | None = None) -> Config:
    root = Path(repo_root or Path.cwd()).resolve()
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        raise ConfigError(f"no pyproject.toml at {root}; run `keystones init` first")
    data = tomllib.loads(pyproject.read_text()).get("tool", {}).get("keystones")
    if data is None:
        raise ConfigError("no [tool.keystones] section in pyproject.toml")
    categories = tuple(data.get("categories", ["default"]))
    if "default" not in categories:
        categories = ("default", *categories)
    return Config(
        repo_root=root,
        root=data.get("root", "keystones"),
        exclude=tuple(data.get("exclude", [])),
        categories=categories,
        languages=_languages(data),
    )
