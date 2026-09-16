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

    path = cfg.index_path
    if not entries and not path.exists():
        return []
    expected = render_index(sorted(entries.values(), key=lambda e: (e.category, e.id)))
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


def c9_removed(
    cfg: Config, resolved: list[Resolved], entry_list: list[Entry], base: str
) -> list[Finding]:
    from keystones import gitref
    from keystones.removal import c9_removals, inventory_at, inventory_head

    point = gitref.merge_base(cfg.repo_root, base) or base
    return c9_removals(
        inventory_at(cfg.repo_root, point), inventory_head(cfg, resolved, entry_list)
    )


def run_all(
    cfg: Config,
    resolved: list[Resolved],
    entry_list: list[Entry],
    *,
    scoped: bool = False,
    warn_only: bool = False,
    base: str | None = None,
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
        findings += c8_ownership(cfg)
        findings += c10_index(cfg, entries)
        if base:
            findings += c9_removed(cfg, resolved, entry_list, base)
    return findings


def c8_ownership(cfg: Config) -> list[Finding]:
    """The gate is only real if its own files are owned. See keystones/codeowners.py."""
    from keystones import codeowners

    owners_file, rules = codeowners.find(cfg.repo_root)
    if owners_file is None:
        return [
            Finding(
                "C8",
                Severity.ERROR,
                "no CODEOWNERS file; every keystone is unguarded. Expected one of "
                + ", ".join(codeowners.SEARCH_PATHS),
            )
        ]

    owners_rel = str(owners_file.relative_to(cfg.repo_root))
    findings: list[Finding] = []

    gate_files = [owners_rel, "pyproject.toml"]
    for extra in (".pre-commit-config.yaml", ".pre-commit-hooks.yaml"):
        if (cfg.repo_root / extra).is_file():
            gate_files.append(extra)
    workflows = cfg.repo_root / ".github" / "workflows"
    if workflows.is_dir():
        gate_files += sorted(
            str(p.relative_to(cfg.repo_root)) for p in workflows.glob("*.y*ml")
        )

    for rel in gate_files:
        rule = codeowners.owners_for(rules, rel)
        if rule is None or not rule.owners:
            findings.append(
                Finding(
                    "C8",
                    Severity.ERROR,
                    f"{rel} is part of the gate but has no CODEOWNERS owner, so the "
                    "gate can be removed without review",
                    owners_rel,
                )
            )

    for category in cfg.categories:
        probe = f"{cfg.root}/{category}/_probe.md"
        rule = codeowners.owners_for(rules, probe)
        if rule is None or not rule.owners:
            findings.append(
                Finding(
                    "C8",
                    Severity.ERROR,
                    f"category '{category}' has no CODEOWNERS owner; editing its "
                    "sidecars would require no review",
                    owners_rel,
                )
            )
            continue
        if not rule.pattern.lstrip("/").startswith(f"{cfg.root}/"):
            findings.append(
                Finding(
                    "C8",
                    Severity.ERROR,
                    f"category '{category}' is owned by '{rule.pattern}' "
                    f"(line {rule.lineno}), a rule outside {cfg.root}/. CODEOWNERS is "
                    "last-match-wins, so that pattern silently reassigned the sidecars",
                    owners_rel,
                    rule.lineno,
                )
            )
    return findings
