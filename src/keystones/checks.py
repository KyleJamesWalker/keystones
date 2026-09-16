"""C1 through C7 and C10. C8, C9 and doctor land with the GitHub integration."""

from __future__ import annotations

from collections import defaultdict

from keystones.config import Config
from keystones.discovery import Resolved
from keystones.models import Entry, Finding, Severity

_FIX_HINT = 'run `keystones fix -m "<why it changed>"`'


def c1_orphan_markers(
    resolved: list[Resolved], entries: dict[str, Entry]
) -> list[Finding]:
    out = []
    for item in resolved:
        if item.marker.id not in entries:
            out.append(
                Finding(
                    "C1",
                    Severity.ERROR,
                    f"keystone '{item.marker.id}' has no sidecar entry; "
                    f"run `keystones add {item.target} --id {item.marker.id} "
                    f'--category {item.marker.category} -m "<why>"`',
                    item.marker.path,
                    item.marker.lineno,
                )
            )
    return out


def c2_orphan_entries(
    resolved: list[Resolved], entries: dict[str, Entry]
) -> list[Finding]:
    seen = {item.marker.id for item in resolved}
    return [
        Finding(
            "C2",
            Severity.ERROR,
            f"sidecar entry '{entry.id}' has no marker in the tree; the marker was "
            "removed or its file is excluded",
            entry.path,
        )
        for entry_id, entry in sorted(entries.items())
        if entry_id not in seen
    ]


def c3_c4_hashes(
    cfg: Config,
    resolved: list[Resolved],
    entries: dict[str, Entry],
    warn_only: bool = False,
) -> list[Finding]:
    out = []
    for item in resolved:
        entry = entries.get(item.marker.id)
        if entry is None:
            continue
        src = (cfg.repo_root / item.marker.path).read_text()
        semantic, text = item.adapter.hashes(src, item.target)
        if semantic != entry.semantic:
            out.append(
                Finding(
                    "C3",
                    Severity.WARNING if warn_only else Severity.ERROR,
                    f"keystone '{entry.id}' changed. Its owner must review this. "
                    f"{_FIX_HINT}",
                    item.marker.path,
                    item.marker.lineno,
                    owner_hint=entry.category,
                )
            )
        elif text != entry.text:
            out.append(
                Finding(
                    "C4",
                    Severity.WARNING if warn_only else Severity.ERROR,
                    f"comments inside keystone '{entry.id}' changed. {_FIX_HINT}",
                    item.marker.path,
                    item.marker.lineno,
                )
            )
    return out


def c5_stored_source(entries: dict[str, Entry]) -> list[Finding]:
    from keystones.adapters import python as python_adapter

    out = []
    for entry in sorted(entries.values(), key=lambda e: e.id):
        if not entry.source:
            out.append(
                Finding(
                    "C5",
                    Severity.ERROR,
                    f"'{entry.id}' stores no canonical source",
                    entry.path,
                )
            )
            continue
        try:
            actual = python_adapter.hash_fragment(
                entry.source, "::" not in entry.target
            )
        except SyntaxError as exc:
            out.append(
                Finding(
                    "C5",
                    Severity.ERROR,
                    f"'{entry.id}' stored source does not parse: {exc}",
                    entry.path,
                )
            )
            continue
        if actual != entry.semantic:
            out.append(
                Finding(
                    "C5",
                    Severity.ERROR,
                    f"'{entry.id}' stored source does not match its stored hash; "
                    "the entry was hand-edited",
                    entry.path,
                )
            )
    return out


def c6_uniqueness(resolved: list[Resolved]) -> list[Finding]:
    out = []
    by_id: dict[tuple[str, str], list[Resolved]] = defaultdict(list)
    by_target: dict[str, list[Resolved]] = defaultdict(list)
    for item in resolved:
        by_id[(item.marker.category, item.marker.id)].append(item)
        by_target[str(item.target)].append(item)

    for (category, keystone_id), items in sorted(by_id.items()):
        if len(items) > 1:
            where = ", ".join(f"{i.marker.path}:{i.marker.lineno}" for i in items)
            out.append(
                Finding(
                    "C6",
                    Severity.ERROR,
                    f"id '{keystone_id}' reused in category '{category}': {where}",
                )
            )
    for target, items in sorted(by_target.items()):
        if len(items) > 1:
            ids = ", ".join(sorted(i.marker.id for i in items))
            out.append(
                Finding("C6", Severity.ERROR, f"markers {ids} all resolve to {target}")
            )
    return out


def c7_categories(cfg: Config, resolved: list[Resolved]) -> list[Finding]:
    return [
        Finding(
            "C7",
            Severity.ERROR,
            f"unknown category '{item.marker.category}'; add it to "
            "[tool.keystones] categories in pyproject.toml",
            item.marker.path,
            item.marker.lineno,
        )
        for item in resolved
        if item.marker.category not in cfg.categories
    ]


def c10_index(cfg: Config, entries: dict[str, Entry]) -> list[Finding]:
    from keystones.sidecar import render_index

    expected = render_index(sorted(entries.values(), key=lambda e: (e.category, e.id)))
    path = cfg.index_path
    actual = path.read_text() if path.exists() else ""
    if actual != expected:
        return [
            Finding(
                "C10",
                Severity.ERROR,
                "INDEX.md is stale; run `keystones index`",
                str(path),
            )
        ]
    return []


def run_all(
    cfg: Config,
    resolved: list[Resolved],
    entry_list: list[Entry],
    *,
    scoped: bool = False,
    warn_only: bool = False,
) -> list[Finding]:
    """`scoped` means only some files were seen, so whole-repo checks are skipped."""
    entries = {entry.id: entry for entry in entry_list}
    findings = c1_orphan_markers(resolved, entries)
    findings += c3_c4_hashes(cfg, resolved, entries, warn_only=warn_only)
    findings += c7_categories(cfg, resolved)
    if not scoped:
        findings += c2_orphan_entries(resolved, entries)
        findings += c5_stored_source(entries)
        findings += c6_uniqueness(resolved)
        findings += c10_index(cfg, entries)
    return findings
