"""C9. The check that a coordinated removal cannot satisfy.

C1 and C2 are symmetric self-consistency checks, so deleting a marker and its
sidecar entry in one change leaves the repository internally consistent and
green. Only a comparison against the base ref sees it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from keystones import adapters, gitref
from keystones.config import DEFAULT_EXCLUDE_DIRS, Config
from keystones.models import Finding, Severity


@dataclass
class Inventory:
    markers: dict[str, str] = field(default_factory=dict)
    marker_paths: dict[str, str] = field(default_factory=dict)
    entries: dict[str, str] = field(default_factory=dict)
    categories: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def excludes(self, rel_path: str) -> bool:
        parts = PurePosixPath(rel_path).parts
        if any(part in DEFAULT_EXCLUDE_DIRS for part in parts):
            return True
        return any(
            fnmatch(rel_path, pat)
            or (pat.startswith("**/") and fnmatch(rel_path, pat[3:]))
            for pat in self.exclude
        )


def _config_at(
    repo_root: Path, ref: str
) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    raw = gitref.read_at(repo_root, ref, "pyproject.toml")
    if raw is None:
        return "keystones", ("default",), ()
    try:
        data = tomllib.loads(raw).get("tool", {}).get("keystones", {})
    except tomllib.TOMLDecodeError:
        return "keystones", ("default",), ()
    categories = tuple(data.get("categories", ["default"]))
    if "default" not in categories:
        categories = ("default", *categories)
    return data.get("root", "keystones"), categories, tuple(data.get("exclude", []))


def inventory_at(repo_root: Path, ref: str) -> Inventory:
    root, categories, exclude = _config_at(repo_root, ref)
    inv = Inventory(categories=categories, exclude=exclude)
    supported = adapters.supported_extensions()

    for rel in gitref.files_at(repo_root, ref):
        if rel.startswith(f"{root}/") and rel.endswith(".md"):
            parts = Path(rel).parts
            if len(parts) == 3 and parts[2] != "INDEX.md":
                inv.entries[Path(rel).stem] = parts[1]
            continue
        if not rel.endswith(supported) or inv.excludes(rel):
            continue
        adapter = adapters.for_path(rel)
        src = gitref.read_at(repo_root, ref, rel)
        if adapter is None or src is None:
            continue
        try:
            found = adapter.markers(rel, src)
        except Exception:
            # The base ref is history; a file that no longer parses there
            # tells us nothing and must not fail the current check.
            continue
        for marker in found:
            inv.markers[marker.id] = marker.category
            inv.marker_paths[marker.id] = rel
    return inv


def inventory_head(cfg: Config, resolved, entry_list) -> Inventory:
    return Inventory(
        markers={item.marker.id: item.marker.category for item in resolved},
        marker_paths={item.marker.id: item.marker.path for item in resolved},
        entries={entry.id: entry.category for entry in entry_list},
        categories=cfg.categories,
        exclude=cfg.exclude,
    )


def c9_removals(base: Inventory, head: Inventory) -> list[Finding]:
    """Every removal names the category losing coverage, so CODEOWNERS applies."""
    findings: list[Finding] = []

    for keystone_id, category in sorted(base.markers.items()):
        if keystone_id in head.markers:
            continue
        path = base.marker_paths.get(keystone_id, "")
        if head.excludes(path):
            reason = (
                f"its file {path} is now covered by an exclude pattern, which removes "
                "the keystone without touching it"
            )
        elif keystone_id in head.entries:
            reason = f"the marker was deleted from {path} but the sidecar entry remains"
        else:
            reason = f"the marker in {path} and its sidecar entry were both deleted"
        findings.append(
            Finding(
                "C9",
                Severity.ERROR,
                f"keystone '{keystone_id}' was removed: {reason}. This needs approval "
                f"from the owner of category '{category}'.",
                path or None,
                owner_hint=category,
            )
        )

    for keystone_id, category in sorted(base.entries.items()):
        if keystone_id in head.entries or keystone_id in base.markers:
            continue
        findings.append(
            Finding(
                "C9",
                Severity.ERROR,
                f"sidecar entry '{keystone_id}' was deleted. This needs approval from "
                f"the owner of category '{category}'.",
                owner_hint=category,
            )
        )

    dropped = [c for c in base.categories if c not in head.categories]
    for category in dropped:
        findings.append(
            Finding(
                "C9",
                Severity.ERROR,
                f"category '{category}' was removed from [tool.keystones]; every "
                "keystone it guarded is now unguarded",
                "pyproject.toml",
                owner_hint=category,
            )
        )
    return findings
