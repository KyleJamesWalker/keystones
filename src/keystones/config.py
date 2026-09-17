"""`[tool.keystones]` in pyproject.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

DEFAULT_EXCLUDE_DIRS = (".git", "node_modules", "vendor", "generated")


@dataclass
class Config:
    repo_root: Path
    root: str = "keystones"
    exclude: tuple[str, ...] = ()
    categories: tuple[str, ...] = ("default",)

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
    )
