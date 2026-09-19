"""Every check. C9 lives in removal.py, C11 in dependencies.py."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from keystones.config import Config
from keystones.discovery import Resolved
from keystones.models import Entry, Finding, Severity


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
                    f'run `keystones add --id {item.marker.id} -m "<why>"`',
                    item.marker.path,
                    item.marker.lineno,
                )
            )
    return out


def c2_orphan_entries(
    resolved: list[Resolved],
    entries: dict[str, Entry],
    skipped: set[str] | None = None,
) -> list[Finding]:
    seen = {item.marker.id for item in resolved}
    unreadable = skipped or set()
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
        and entry.target.split("::")[0].split("#")[0] not in unreadable
    ]


def c3_c4_hashes(
    cfg: Config,
    resolved: list[Resolved],
    entries: dict[str, Entry],
    warn_only: bool = False,
) -> list[Finding]:
    out = []
    severity = Severity.WARNING if warn_only else Severity.ERROR
    for item in resolved:
        entry = entries.get(item.marker.id)
        if entry is None:
            continue
        # The basis is recorded in two places on purpose, so editing one and
        # not the other is caught instead of quietly re-gating the keystone.
        actual_kind = item.adapter.kind_for_path(item.marker.path)
        if entry.hash and entry.hash != actual_kind:
            out.append(
                Finding(
                    "C14",
                    severity,
                    f"keystone '{entry.id}' is recorded as hash={entry.hash} but "
                    f"its marker gates on hash={actual_kind}. If pyproject.toml "
                    "changed, `keystones migrate` moves it; otherwise one of the "
                    "two was edited without the other, so make them agree.",
                    item.marker.path,
                    item.marker.lineno,
                    owner_hint=entry.category,
                )
            )
            continue
        expected_hasher = item.adapter.hasher_id_for_path(item.marker.path)
        rehashed = entry.hasher and entry.hasher != expected_hasher
        src = (cfg.repo_root / item.marker.path).read_text(encoding="utf-8")
        semantic, text = item.adapter.hashes(src, item.target)
        target = str(item.target)
        fix_hint = f'run `keystones fix --id {entry.id} -m "<why it changed>"`'

        # Without this the marker can be moved onto a hash-identical decoy while
        # the real definition is rewritten, and every other check stays green.
        if target != entry.target:
            out.append(
                Finding(
                    "C3",
                    severity,
                    f"keystone '{entry.id}' no longer covers {entry.target}; its "
                    f"marker now sits on {target}. Whatever that entry protected "
                    f"may no longer be protected. {fix_hint}",
                    item.marker.path,
                    item.marker.lineno,
                    owner_hint=entry.category,
                )
            )
        elif semantic != entry.semantic and rehashed:
            out.append(
                Finding(
                    "C13",
                    severity,
                    f"keystone '{entry.id}' was hashed by {entry.hasher}, this "
                    f"install uses {expected_hasher}, and the two disagree. "
                    "Whether the code changed cannot be told from here. Run "
                    "`keystones migrate --check`, or install the grammar "
                    "version this repo pins.",
                    item.marker.path,
                    item.marker.lineno,
                    owner_hint=entry.category,
                )
            )
        elif semantic != entry.semantic:
            out.append(
                Finding(
                    "C3",
                    severity,
                    f"keystone '{entry.id}' changed. Its owner must review "
                    f"this. {fix_hint}",
                    item.marker.path,
                    item.marker.lineno,
                    owner_hint=entry.category,
                )
            )
        elif text != entry.text:
            out.append(
                Finding(
                    "C4",
                    severity,
                    f"comments inside keystone '{entry.id}' changed. {fix_hint}",
                    item.marker.path,
                    item.marker.lineno,
                )
            )
    return out


def c5_stored_source(entries: dict[str, Entry]) -> list[Finding]:
    from keystones import adapters

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
        rel = entry.target.split("::")[0].split("#")[0]
        if adapters.needs_extra(rel):
            continue
        adapter = adapters.for_entry(entry)
        if entry.hasher and entry.hasher != adapter.hasher_id_for_path(rel):
            continue
        try:
            actual = adapter.hash_stored_source(entry.source, entry.target)
        except Exception as exc:  # any adapter failure is a finding, not a crash
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
    skipped: set[str] | None = None,
) -> list[Finding]:
    """`scoped` means only some files were seen, so whole-repo checks are skipped."""
    entries = {entry.id: entry for entry in entry_list}
    findings = c1_orphan_markers(resolved, entries)
    findings += c3_c4_hashes(cfg, resolved, entries, warn_only=warn_only)
    findings += c7_categories(cfg, resolved)
    findings += c7_category_agreement(resolved, entries)
    if not scoped:
        findings += c2_orphan_entries(resolved, entries, skipped)
        findings += c5_stored_source(entries)
        findings += c6_uniqueness(resolved)
        findings += c6_shadowed_targets(cfg, resolved)
        findings += c8_ownership(cfg)
        findings += c11_dependencies(cfg, entry_list)
        findings += stale_report(cfg, entry_list)
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


def c11_dependencies(cfg: Config, entry_list: list[Entry]) -> list[Finding]:
    from keystones import dependencies

    return dependencies.check(cfg.repo_root, entry_list)


def stale_report(cfg: Config, entry_list: list[Entry]) -> list[Finding]:
    """Warnings only. A keystone going stale is a prompt, not a build break."""
    from keystones.staleness import DurationError, age, humanize, parse_duration

    findings = []
    for entry in sorted(entry_list, key=lambda e: e.id):
        if not entry.review_every:
            continue
        try:
            budget = parse_duration(entry.review_every)
        except DurationError as exc:
            findings.append(Finding("C12", Severity.ERROR, str(exc), entry.path))
            continue
        rel = str(Path(entry.path).relative_to(cfg.repo_root))
        elapsed = age(cfg.repo_root, rel)
        if elapsed is not None and elapsed > budget:
            findings.append(
                Finding(
                    "C12",
                    Severity.WARNING,
                    f"keystone '{entry.id}' was last reviewed {humanize(elapsed)} ago, "
                    f"over its {entry.review_every} budget",
                    entry.path,
                    owner_hint=entry.category,
                )
            )
    return findings


def hasher_mismatch(cfg: Config, entry_list: list[Entry]) -> list[Finding]:
    """Reported on its own, never as C3.

    A stored hash produced by a different serializer or grammar version says
    nothing about whether the code changed, so failing it as drift would send
    people to `fix` and rubber-stamp a real review.
    """
    from keystones import adapters

    out = []
    for entry in sorted(entry_list, key=lambda e: e.id):
        rel = entry.target.split("::")[0].split("#")[0]
        if adapters.needs_extra(rel):
            continue
        adapter = adapters.for_path(rel)
        expected = adapter.hasher_id_for_path(rel)
        if entry.hasher and entry.hasher != expected:
            out.append(
                Finding(
                    "C13",
                    Severity.ERROR,
                    f"keystone '{entry.id}' was hashed by {entry.hasher} but this "
                    f"install uses {expected}, so C3, C4 and C5 cannot verify "
                    "it at all. Run `keystones migrate` to prove it across, or "
                    "install the matching extra.",
                    entry.path,
                )
            )
    return out


def c6_shadowed_targets(cfg: Config, resolved: list[Resolved]) -> list[Finding]:
    """A second definition of the same name lets a decoy sit under the marker.

    Resolution takes the first match, so the protected function can be rewritten
    below while a hash-identical copy keeps the gate green.
    """
    out = []
    checked: set[str] = set()
    for item in resolved:
        if item.target.qualname is None or item.marker.path in checked:
            continue
        checked.add(item.marker.path)
        src = (cfg.repo_root / item.marker.path).read_text(encoding="utf-8")
        for qualname in sorted(item.adapter.duplicate_qualnames(item.marker.path, src)):
            out.append(
                Finding(
                    "C6",
                    Severity.ERROR,
                    f"{item.marker.path} defines '{qualname}' more than once, so a "
                    "keystone on it cannot say which one it protects",
                    item.marker.path,
                )
            )
    return out


def c7_category_agreement(
    resolved: list[Resolved], entries: dict[str, Entry]
) -> list[Finding]:
    """A mismatch means a different team reviews than the marker names."""
    out = []
    for item in resolved:
        entry = entries.get(item.marker.id)
        if entry is not None and entry.category != item.marker.category:
            out.append(
                Finding(
                    "C7",
                    Severity.ERROR,
                    f"marker for '{entry.id}' says category "
                    f"'{item.marker.category}' but its sidecar sits in "
                    f"'{entry.category}', so a different team owns the review",
                    item.marker.path,
                    item.marker.lineno,
                )
            )
    return out
