"""`[tool.keystones]` in pyproject.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

DEFAULT_EXCLUDE_DIRS = (".git", "node_modules", "vendor", "generated")

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
    }
)
REQUIRED_LANGUAGE_KEYS = ("grammar", "extensions", "definitions")


@dataclass(frozen=True)
class LanguageConfig:
    """One `[[tool.keystones.language]]` table, validated but not yet a spec.

    Building the spec needs the tree-sitter adapter, which the config layer
    must not depend on; a repo can be configured on an install that has no
    grammar pack at all.
    """

    grammar: str
    extensions: tuple[str, ...]
    definitions: frozenset[str]
    comments: frozenset[str] | None = None
    name_fields: tuple[str, ...] | None = None
    wrappers: frozenset[str] | None = None
    label_children: tuple[str, ...] | None = None
    line_comment: str | None = None


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


def _builtin_extensions() -> dict[str, str]:
    """Extension -> the builtin that owns it, for collision reporting."""
    from keystones.adapters import python as python_adapter
    from keystones.adapters import treesitter

    owners = {ext: "python" for ext in python_adapter.extensions}
    for spec in treesitter.SPECS:
        for ext in spec.extensions:
            owners[ext] = spec.language
    return owners


def _strs(table: dict, key: str, where: str) -> list[str]:
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{where}: {key} must be a list of strings")
    return value


def _language(table: dict, index: int, claimed: dict[str, str]) -> LanguageConfig:
    where = f"[[tool.keystones.language]] #{index + 1}"
    unknown = sorted(set(table) - LANGUAGE_KEYS)
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) {', '.join(unknown)}. "
            f"Valid keys: {', '.join(sorted(LANGUAGE_KEYS))}"
        )
    missing = [key for key in REQUIRED_LANGUAGE_KEYS if key not in table]
    if missing:
        raise ConfigError(f"{where}: missing required key(s) {', '.join(missing)}")

    grammar = table["grammar"]
    if not isinstance(grammar, str) or not grammar:
        raise ConfigError(f"{where}: grammar must be a non-empty string")
    where = f"{where} (grammar '{grammar}')"

    extensions = _strs(table, "extensions", where)
    if not extensions:
        raise ConfigError(f"{where}: extensions must not be empty")
    for ext in extensions:
        if not ext.startswith("."):
            raise ConfigError(f"{where}: extension '{ext}' must start with a dot")
        # Silently rebinding an extension would change the hash of every file
        # with it, which is indistinguishable from the code having changed.
        if ext in claimed:
            raise ConfigError(
                f"{where}: extension '{ext}' is already handled by "
                f"'{claimed[ext]}'. One extension, one parser."
            )
        claimed[ext] = grammar

    definitions = _strs(table, "definitions", where)
    if not definitions:
        raise ConfigError(
            f"{where}: definitions must not be empty; without node types there "
            "is nothing to attach a keystone to"
        )

    optional: dict[str, object] = {}
    for key in ("comments", "wrappers"):
        if key in table:
            optional[key] = frozenset(_strs(table, key, where))
    for key in ("name_fields", "label_children"):
        if key in table:
            optional[key] = tuple(_strs(table, key, where))
    if "line_comment" in table:
        if not isinstance(table["line_comment"], str):
            raise ConfigError(f"{where}: line_comment must be a string")
        optional["line_comment"] = table["line_comment"]

    return LanguageConfig(
        grammar=grammar,
        extensions=tuple(extensions),
        definitions=frozenset(definitions),
        **optional,
    )


def _languages(data: dict) -> tuple[LanguageConfig, ...]:
    tables = data.get("language", [])
    if not isinstance(tables, list):
        raise ConfigError("tool.keystones.language must be an array of tables")
    claimed = _builtin_extensions()
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
