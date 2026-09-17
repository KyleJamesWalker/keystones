"""Move entries onto a new hasher or grammar version, with proof.

A hash basis change says nothing about whether the code changed, so it must not
be laundered through `fix`. Instead the stored canonical source is re-rendered
under the new hasher: if that matches the new hash of the live code, the code is
provably unchanged and the entry migrates with no note and no owner review. If
it does not match, a real change coincides with the upgrade and it goes through
the normal gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from keystones import adapters
from keystones.config import Config
from keystones.discovery import Resolved
from keystones.models import Entry


@dataclass
class Outcome:
    entry: Entry
    old_hasher: str
    new_hasher: str
    proved: bool
    reason: str = ""


def _target_of(resolved: list[Resolved], entry: Entry) -> Resolved | None:
    for item in resolved:
        if item.marker.id == entry.id:
            return item
    return None


def plan(cfg: Config, resolved: list[Resolved], entries: list[Entry]) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for entry in sorted(entries, key=lambda e: e.id):
        rel = entry.target.split("::")[0].split("#")[0]
        if adapters.needs_extra(rel):
            continue
        adapter = adapters.for_path(rel)
        expected = adapter.hasher_id_for_path(rel)
        if not entry.hasher or entry.hasher == expected:
            continue

        item = _target_of(resolved, entry)
        if item is None:
            outcomes.append(
                Outcome(
                    entry,
                    entry.hasher,
                    expected,
                    False,
                    "its marker is not in the tree",
                )
            )
            continue

        live = (cfg.repo_root / item.marker.path).read_text()
        try:
            live_semantic, _ = item.adapter.hashes(live, item.target)
            from_stored = adapter.hash_stored_source(entry.source, str(item.target))
        except Exception as exc:
            outcomes.append(
                Outcome(
                    entry, entry.hasher, expected, False, f"cannot re-render: {exc}"
                )
            )
            continue

        if from_stored == live_semantic:
            outcomes.append(Outcome(entry, entry.hasher, expected, True))
        else:
            outcomes.append(
                Outcome(
                    entry,
                    entry.hasher,
                    expected,
                    False,
                    "the code also changed, so this needs the normal review",
                )
            )
    return outcomes


def apply(cfg: Config, resolved: list[Resolved], outcomes: list[Outcome]) -> int:
    from keystones import dependencies, sidecar

    migrated = 0
    for outcome in outcomes:
        if not outcome.proved:
            continue
        entry = outcome.entry
        item = _target_of(resolved, entry)
        live = (cfg.repo_root / item.marker.path).read_text()
        semantic, text = item.adapter.hashes(live, item.target)
        entry.hasher = outcome.new_hasher
        entry.semantic = semantic
        entry.text = text
        entry.target = str(item.target)
        entry.source = item.adapter.canonical_source(live, item.target)
        entry.depends_hash = dependencies.combined_hash(cfg.repo_root, entry.depends)
        sidecar.write(Path(entry.path), entry)
        migrated += 1
    return migrated
