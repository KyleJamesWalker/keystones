"""Every check. C9 lives in removal.py, C11 in dependencies.py."""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import zip_longest
from pathlib import Path

from keystones import adapters
from keystones.adapters.base import ResolutionError
from keystones.config import Config
from keystones.discovery import Resolved
from keystones.models import Entry, Finding, Severity

Key = tuple[str, str]


def shared_ids(resolved: list[Resolved], entries: dict[Key, Entry]) -> set[str]:
    """Ids used in more than one category, which a bare `--id` cannot name."""
    by_id: dict[str, set[str]] = defaultdict(set)
    for category, keystone_id in [*entries, *(i.marker.key for i in resolved)]:
        by_id[keystone_id].add(category)
    return {keystone_id for keystone_id, cats in by_id.items() if len(cats) > 1}


def ref(key: Key, shared: set[str]) -> str:
    """The id as `--id` accepts it: qualified only where a bare id is ambiguous."""
    category, keystone_id = key
    return f"{category}/{keystone_id}" if keystone_id in shared else keystone_id


def category_mismatches(
    resolved: list[Resolved], entries: dict[Key, Entry]
) -> dict[Key, Entry]:
    """Markers whose only entry sits in another category, by marker key.

    Reported once as C7 rather than as an orphan marker plus an orphan entry.
    """
    marked = {item.marker.key for item in resolved}
    out = {}
    for item in resolved:
        if item.marker.key in entries:
            continue
        elsewhere = [
            entry
            for key, entry in sorted(entries.items())
            if entry.id == item.marker.id and key not in marked
        ]
        if len(elsewhere) == 1:
            out[item.marker.key] = elsewhere[0]
    return out


def c1_orphan_markers(
    resolved: list[Resolved], entries: dict[Key, Entry]
) -> list[Finding]:
    out = []
    mismatched = category_mismatches(resolved, entries)
    shared = shared_ids(resolved, entries)
    for item in resolved:
        if item.marker.key not in entries and item.marker.key not in mismatched:
            out.append(
                Finding(
                    "C1",
                    Severity.ERROR,
                    f"keystone '{item.marker.id}' has no sidecar entry; "
                    f"run `keystones add --id {ref(item.marker.key, shared)} "
                    '-m "<why>"`',
                    item.marker.path,
                    item.marker.lineno,
                )
            )
    return out


def c2_orphan_entries(
    resolved: list[Resolved],
    entries: dict[Key, Entry],
    skipped: set[str] | None = None,
) -> list[Finding]:
    seen = {item.marker.key for item in resolved}
    paired = {entry.key for entry in category_mismatches(resolved, entries).values()}
    unreadable = skipped or set()
    return [
        Finding(
            "C2",
            Severity.ERROR,
            f"sidecar entry '{entry.id}' has no marker in the tree; the marker was "
            "removed or its file is excluded",
            entry.path,
        )
        for key, entry in sorted(entries.items())
        if key not in seen
        and key not in paired
        and entry.target.split("::")[0].split("#")[0] not in unreadable
    ]


def c3_c4_hashes(
    cfg: Config,
    resolved: list[Resolved],
    entries: dict[Key, Entry],
    warn_only: bool = False,
) -> list[Finding]:
    out = []
    severity = Severity.WARNING if warn_only else Severity.ERROR
    shared = shared_ids(resolved, entries)
    for item in resolved:
        entry = entries.get(item.marker.key)
        if entry is None:
            continue
        # The basis is recorded in two places on purpose, so editing one and
        # not the other is caught instead of quietly re-gating the keystone.
        actual_kind = adapters.kind_for(item.adapter, item.target)
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
        expected_hasher = adapters.hasher_id(item.adapter, item.target)
        rehashed = entry.hasher and entry.hasher != expected_hasher
        src = (cfg.repo_root / item.marker.path).read_text(encoding="utf-8")
        try:
            semantic, text = item.adapter.hashes(src, item.target)
        except ResolutionError as exc:
            out.append(
                Finding(
                    "resolve",
                    severity,
                    f"keystone '{entry.id}' cannot be hashed: {exc}",
                    item.marker.path,
                    item.marker.lineno,
                    owner_hint=entry.category,
                )
            )
            continue
        target = str(item.target)
        fix_hint = (
            f'run `keystones fix --id {ref(entry.key, shared)} -m "<why it changed>"`'
        )

        # An entry may name its target by an alias, such as an explicit YAML
        # `[key=value]` selector, that resolves to the same node.
        if (
            target != entry.target
            and "::" in entry.target
            and hasattr(item.adapter, "aliases")
        ):
            wanted = entry.target.split("::", 1)[1]
            if wanted in item.adapter.aliases(src, item.target.qualname):
                target = entry.target
        # A region is found by its marker, so a range that shifted under an
        # unrelated edit above it is not a different target.
        same_region = (
            item.target.region
            and entry.target.split("#")[0] == item.marker.path
            and "#L" in entry.target
            and target != entry.target
        )
        shifted = same_region and semantic == entry.semantic
        if same_region and not shifted:
            # A body edit is the cause; the range moving with it is incidental.
            target = entry.target
        if shifted and text == entry.text:
            out.append(
                Finding(
                    "C3",
                    Severity.NOTICE,
                    f"keystone '{entry.id}' moved from {entry.target} to {target} "
                    "with its body unchanged; `keystones fix` records the new "
                    "range and needs no note",
                    item.marker.path,
                    item.marker.lineno,
                )
            )
        elif shifted:
            out.append(
                Finding(
                    "C4",
                    severity,
                    f"comments inside keystone '{entry.id}' changed, and it now "
                    f"spans {target}. {fix_hint}",
                    item.marker.path,
                    item.marker.lineno,
                )
            )
        # Without this the marker can be moved onto a hash-identical decoy while
        # the real definition is rewritten, and every other check stays green.
        elif target != entry.target:
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
                    f"install uses {expected_hasher}, and the two disagree: "
                    f"{hasher_difference(entry.hasher, expected_hasher)}. "
                    "Whether the code changed cannot be told from here. "
                    "`keystones migrate --check` says what would move.",
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


def disablers(cfg: Config, adapter, src: str, target) -> list[str]:
    """What switches the target off from outside its hash; only Python can tell."""
    find = getattr(adapter, "disablers", None)
    if find is None or target.qualname is None or target.region:
        return []
    return find(src, target.qualname, cfg.disabling_decorators)


def c16_disabled(
    cfg: Config,
    resolved: list[Resolved],
    entries: dict[Key, Entry],
    warn_only: bool = False,
) -> list[Finding]:
    """Switching a guard test off is a review event even when no byte of it moves."""
    out = []
    severity = Severity.WARNING if warn_only else Severity.ERROR
    shared = shared_ids(resolved, entries)
    for item in resolved:
        entry = entries.get(item.marker.key)
        if entry is None:
            continue
        src = (cfg.repo_root / item.marker.path).read_text(encoding="utf-8")
        try:
            now = disablers(cfg, item.adapter, src, item.target)
        except SyntaxError:
            continue
        if now == sorted(entry.disabled_by):
            continue
        added = [d for d in now if d not in entry.disabled_by]
        removed = [d for d in entry.disabled_by if d not in now]
        what = (
            f"is switched off by {', '.join(added)}"
            if added
            else f"is no longer switched off by {', '.join(removed)}"
        )
        out.append(
            Finding(
                "C16",
                severity,
                f"keystone '{entry.id}' {what}. Its owner must review this. "
                f"run `keystones fix --id {ref(entry.key, shared)} "
                '-m "<why>"`',
                item.marker.path,
                item.marker.lineno,
                owner_hint=entry.category,
            )
        )
    return out


def c18_unreviewed(
    cfg: Config, resolved: list[Resolved], entries: dict[Key, Entry], scoped: bool
) -> list[Finding]:
    """A gate on a path nobody reviews is a gate that can only fail."""
    out = []
    seen: set[Key] = set()
    for item in resolved:
        pattern = cfg.unreviewed_by(item.marker.path)
        if pattern is None:
            continue
        seen.add(item.marker.key)
        # A bot copies marked files here; failing every run on the copy would
        # only get the path excluded. The marker is noted, never gated.
        out.append(
            Finding(
                "C18",
                Severity.NOTICE,
                f"marker '{item.marker.id}' in {item.marker.path} is ignored: "
                f"'{pattern}' in [tool.keystones] unreviewed says the path is "
                "rewritten without review, so no gate holds there",
                item.marker.path,
                item.marker.lineno,
            )
        )
    if scoped:
        return out
    for _key, entry in sorted(entries.items()):
        rel = entry.target.split("::")[0].split("#")[0]
        pattern = cfg.unreviewed_by(rel)
        if pattern is None:
            continue
        out.append(
            Finding(
                "C18",
                Severity.ERROR,
                f"keystone '{entry.id}' targets {rel}, which '{pattern}' in "
                "[tool.keystones] unreviewed says is rewritten without review, "
                "so no gate can hold there. Delete the entry, or take the path "
                "off the list.",
                entry.path,
                owner_hint=entry.category,
            )
        )
    return out


_VERSIONED = re.compile(r"^(?P<name>[^@/]+)@(?P<version>[^/]+)(?P<rest>(?:/[^/]*)*)$")
_SERIALIZERS = {
    "keystones-ts": "tree-sitter serializer",
    "keystones-ast": "Python AST serializer",
    "keystones-text": "text hasher",
    "keystones-plugin": "plugin adapter",
}
_PIN = (
    "Pin the version the repo chose, in additional_dependencies for the hook, "
    "or move every keystone with `keystones migrate`"
)


def hasher_difference(recorded: str, expected: str) -> str:
    """What moved between two hasher ids, in words that say what to do.

    `keystones-ts/2+typescript@1.20.0/<spec>+dbt/1`: family and serializer,
    then the grammar or parsing library with its version and spec digest,
    then any preprocessor with its version.
    """
    (r_family, _, r_serial), *r_parts = [
        seg.partition("/") if i == 0 else seg
        for i, seg in enumerate(recorded.split("+"))
    ]
    (e_family, _, e_serial), *e_parts = [
        seg.partition("/") if i == 0 else seg
        for i, seg in enumerate(expected.split("+"))
    ]
    if r_family != e_family:
        return (
            f"the basis moved from {r_family.removeprefix('keystones-')} to "
            f"{e_family.removeprefix('keystones-')}"
        )
    notes = []
    if r_serial != e_serial:
        label = _SERIALIZERS.get(r_family, "serializer")
        notes.append(f"keystones' {label} moved from {r_serial} to {e_serial}")
    for r_part, e_part in zip_longest(r_parts, e_parts, fillvalue=""):
        if r_part == e_part:
            continue
        r_match, e_match = _VERSIONED.match(r_part), _VERSIONED.match(e_part)
        if r_match and e_match and r_match["name"] == e_match["name"]:
            name = r_match["name"]
            if r_match["version"] != e_match["version"]:
                package = (
                    "tree-sitter-language-pack" if r_family == "keystones-ts" else name
                )
                notes.append(
                    f"{package} {r_match['version']} hashed the sidecar and "
                    f"{e_match['version']} is installed. {_PIN}"
                )
            if r_match["rest"] != e_match["rest"]:
                what = "spec" if r_family == "keystones-ts" else "options"
                notes.append(
                    f"the {name} {what} in [[tool.keystones.language]] changed"
                )
            continue
        r_name, _, r_version = r_part.partition("/")
        e_name, _, e_version = e_part.partition("/")
        if r_name and r_name == e_name:
            notes.append(f"preprocessor {r_name} moved from {r_version} to {e_version}")
        else:
            notes.append(f"{r_part or 'nothing'} became {e_part or 'nothing'}")
    return "; ".join(notes) or "the ids differ in a way keystones cannot name"


def oversized(cfg: Config, entry_list: list[Entry]) -> tuple[list[Finding], set[str]]:
    """Entries whose target file is over the cap: reported, and kept out of C2.

    A keystoned target must never pass because nobody read it.
    """
    findings: list[Finding] = []
    by_path: dict[str, list[Entry]] = {}
    for entry in sorted(entry_list, key=lambda e: (e.category, e.id)):
        rel = entry.target.split("::")[0].split("#")[0]
        by_path.setdefault(rel, []).append(entry)
    paths: set[str] = set()
    for rel, entries in sorted(by_path.items()):
        try:
            size = (cfg.repo_root / rel).stat().st_size
        except OSError:
            continue
        if size <= cfg.max_scan_bytes:
            continue
        paths.add(rel)
        names = ", ".join(f"'{e.id}'" for e in entries)
        findings.append(
            Finding(
                "size",
                Severity.ERROR,
                f"{rel} is {size} bytes, over max_scan_bytes "
                f"({cfg.max_scan_bytes}), so keystone(s) {names} in it were not "
                "checked. Raise [tool.keystones] max_scan_bytes, or move them to "
                "a smaller file.",
                rel,
                owner_hint=entries[0].category,
            )
        )
    return findings, paths


def c5_stored_source(entries: dict[Key, Entry]) -> list[Finding]:
    from keystones import adapters

    out = []
    for entry in sorted(entries.values(), key=lambda e: (e.id, e.category)):
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
        if entry.hasher and entry.hasher != adapters.hasher_id(adapter, entry.target):
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


def c10_index(cfg: Config, entries: dict[Key, Entry]) -> list[Finding]:
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
    entries = {entry.key: entry for entry in entry_list}
    findings = c18_unreviewed(cfg, resolved, entries, scoped)
    doomed = {f.path for f in findings}
    resolved = [item for item in resolved if item.marker.path not in doomed]
    findings += c1_orphan_markers(resolved, entries)
    findings += c3_c4_hashes(cfg, resolved, entries, warn_only=warn_only)
    findings += c7_categories(cfg, resolved)
    findings += c7_category_agreement(resolved, entries)
    findings += c16_disabled(cfg, resolved, entries, warn_only=warn_only)
    if not scoped:
        findings += c2_orphan_entries(resolved, entries, skipped)
        findings += c5_stored_source(entries)
        findings += c6_uniqueness(resolved)
        findings += c6_shadowed_targets(cfg, resolved)
        used = {e.category for e in entry_list} | {i.marker.category for i in resolved}
        findings += c8_ownership(cfg, used)
        findings += c11_dependencies(cfg, entry_list)
        findings += c17_twins(cfg, entry_list)
        findings += stale_report(cfg, entry_list)
        findings += c10_index(cfg, entries)
        if base:
            findings += c9_removed(cfg, resolved, entry_list, base)
    return findings


class _Rulesets:
    """The rules C8 may accept in place of CODEOWNERS, read once and only on demand.

    The read is a GitHub API call, so a repo CODEOWNERS fully covers never pays it.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._rules: list | None = None
        self.findings: list[Finding] = []

    @property
    def rules(self) -> list:
        if self._rules is None:
            self._rules, self.findings = _ruleset_reviewers(self._cfg)
        return self._rules


def _ruleset_reviewers(cfg: Config) -> tuple[list, list[Finding]]:
    from keystones import doctor

    if not cfg.codeowners_from_rulesets:
        return [], []
    try:
        rules = doctor.required_reviewers(cfg.repo_root)
        if rules:
            return rules, []
        try:
            labels = doctor.rulesets_applying(cfg.repo_root)
        except doctor.Unavailable:
            labels = []
        return [], [
            Finding(
                "C8",
                Severity.NOTICE,
                f"codeowners_from_rulesets is on: read {len(labels)} ruleset(s)"
                + (": " + ", ".join(labels) if labels else "")
                + f"; none has a required_reviewers pattern covering "
                f"{cfg.root}/<category>/, so CODEOWNERS alone decides",
            )
        ]
    except doctor.Unavailable as exc:
        return [], [
            Finding(
                "C8",
                Severity.WARNING,
                f"codeowners_from_rulesets is on, but the branch rules could not "
                f"be read ({exc}), so only CODEOWNERS was checked",
            )
        ]


def _covered_by_ruleset(
    rulesets: _Rulesets, rel: str, problem: Finding, what: str | None = None
) -> Finding:
    """A notice naming the ruleset that covers `rel`, or the problem unchanged."""
    for rule in rulesets.rules:
        pattern = rule.covering(rel)
        if pattern is not None:
            return Finding(
                "C8",
                Severity.NOTICE,
                f"{what or rel} is owned by {rule.source}, which requires "
                f"{rule.minimum_approvals} approval(s) from {rule.reviewer} on "
                f"'{pattern}'",
            )
    return problem


def _shadowed(what: str, rule, hidden, owners_rel: str | None) -> Finding:
    return Finding(
        "C8",
        Severity.ERROR,
        f"{what} owned by '{rule.pattern}' (line {rule.lineno}), which comes "
        f"later and overrides '{hidden.pattern}' (line {hidden.lineno}). "
        "CODEOWNERS is last-match-wins, so the broader rule silently took the "
        "path from the owner the specific rule named; move it above",
        owners_rel,
        rule.lineno,
    )


def c8_ownership(cfg: Config, used: set[str] | None = None) -> list[Finding]:
    """The gate is only real if its own files are owned. See keystones/codeowners.py.

    A category named in config was meant to be owned. The implicit `default`
    was not chosen by anyone, so it is probed only once a keystone uses it.
    """
    from keystones import codeowners

    owners_file, rules = codeowners.find(cfg.repo_root)
    rulesets = _Rulesets(cfg)
    findings: list[Finding] = []
    if owners_file is None and not rulesets.rules:
        return [
            *rulesets.findings,
            Finding(
                "C8",
                Severity.ERROR,
                "no CODEOWNERS file; every keystone is unguarded. Expected one of "
                + ", ".join(codeowners.SEARCH_PATHS),
            ),
        ]

    owners_rel = str(owners_file.relative_to(cfg.repo_root)) if owners_file else None

    gate_files = [owners_rel] if owners_rel else []
    gate_files.append("pyproject.toml")
    for extra in (".pre-commit-config.yaml", ".pre-commit-hooks.yaml"):
        if (cfg.repo_root / extra).is_file():
            gate_files.append(extra)
    workflows = cfg.repo_root / ".github" / "workflows"
    if workflows.is_dir():
        gate_files += sorted(
            str(p.relative_to(cfg.repo_root)) for p in workflows.glob("*.y*ml")
        )

    for rel in gate_files:
        rule, hidden = codeowners.shadowed(rules, rel)
        if rule is None or not rule.owners:
            findings.append(
                _covered_by_ruleset(
                    rulesets,
                    rel,
                    Finding(
                        "C8",
                        Severity.ERROR,
                        f"{rel} is part of the gate but has no CODEOWNERS owner, "
                        "so the gate can be removed without review",
                        owners_rel,
                    ),
                )
            )
        elif hidden is not None:
            findings.append(_shadowed(f"{rel} is", rule, hidden, owners_rel))

    for category in cfg.categories:
        implicit = category == "default" and cfg.implicit_default
        if implicit and used is not None and category not in used:
            continue
        probe = f"{cfg.root}/{category}/_probe.md"
        rule, hidden = codeowners.shadowed(rules, probe)
        if rule is None or not rule.owners:
            problem = Finding(
                "C8",
                Severity.ERROR,
                f"category '{category}' has no CODEOWNERS owner; editing its "
                "sidecars would require no review",
                owners_rel,
            )
        elif hidden is not None:
            problem = _shadowed(f"category '{category}' is", rule, hidden, owners_rel)
        elif not rule.pattern.lstrip("/").startswith(f"{cfg.root}/"):
            problem = Finding(
                "C8",
                Severity.ERROR,
                f"category '{category}' is owned by '{rule.pattern}' "
                f"(line {rule.lineno}), a rule outside {cfg.root}/. CODEOWNERS is "
                "last-match-wins, so that pattern silently reassigned the sidecars",
                owners_rel,
                rule.lineno,
            )
        else:
            continue
        findings.append(
            _covered_by_ruleset(
                rulesets,
                probe,
                problem,
                f"category '{category}' ({cfg.root}/{category}/)",
            )
        )
    return [*rulesets.findings, *findings]


def c17_twins(cfg: Config, entry_list: list[Entry]) -> list[Finding]:
    from keystones import twins

    return twins.check(cfg.repo_root, entry_list)


def c11_dependencies(cfg: Config, entry_list: list[Entry]) -> list[Finding]:
    from keystones import dependencies

    return dependencies.check(cfg.repo_root, entry_list, cfg.max_scan_bytes)


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
        expected = adapters.hasher_id(adapter, entry.target)
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
        targets = {
            i.target.qualname
            for i in resolved
            if i.marker.path == item.marker.path and i.target.qualname
        }
        try:
            dupes = item.adapter.duplicate_qualnames(item.marker.path, src, targets)
        except TypeError:
            dupes = item.adapter.duplicate_qualnames(item.marker.path, src)
        for qualname in sorted(dupes):
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
    resolved: list[Resolved], entries: dict[Key, Entry]
) -> list[Finding]:
    """A mismatch means a different team reviews than the marker names."""
    mismatched = category_mismatches(resolved, entries)
    return [
        Finding(
            "C7",
            Severity.ERROR,
            f"marker for '{item.marker.id}' says category "
            f"'{item.marker.category}' but its sidecar sits in "
            f"'{mismatched[item.marker.key].category}', so a different team owns "
            "the review",
            item.marker.path,
            item.marker.lineno,
        )
        for item in resolved
        if item.marker.key in mismatched
    ]
