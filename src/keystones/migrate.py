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
        if item.marker.key == entry.key:
            return item
    return None


def plan(cfg: Config, resolved: list[Resolved], entries: list[Entry]) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for entry in sorted(entries, key=lambda e: (e.id, e.category)):
        rel = entry.target.split("::")[0].split("#")[0]
        if adapters.needs_extra(rel):
            continue
        item = _target_of(resolved, entry)
        # A basis change from config or a marker edit is C14 until migrated;
        # the stored source is re-rendered under the basis the marker now names.
        kind_moved = (
            item is not None
            and bool(entry.hash)
            and entry.hash != adapters.kind_for(item.adapter, item.target)
        )
        adapter = item.adapter if kind_moved else adapters.for_entry(entry)
        expected = adapters.hasher_id(adapter, entry.target)
        if not kind_moved and (not entry.hasher or entry.hasher == expected):
            continue

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


def apply(
    cfg: Config, resolved: list[Resolved], outcomes: list[Outcome]
) -> list[Entry]:
    """Rewrite every proved entry; returns the entries written."""
    from keystones import dependencies, sidecar

    migrated: list[Entry] = []
    for outcome in outcomes:
        if not outcome.proved:
            continue
        entry = outcome.entry
        item = _target_of(resolved, entry)
        live = (cfg.repo_root / item.marker.path).read_text()
        semantic, text = item.adapter.hashes(live, item.target)
        entry.hasher = outcome.new_hasher
        entry.hash = adapters.kind_for(item.adapter, item.target)
        entry.semantic = semantic
        entry.text = text
        entry.target = str(item.target)
        entry.source = item.adapter.canonical_source(live, item.target)
        entry.depends_hash = dependencies.combined_hash(
            cfg.repo_root, entry.depends, cfg.max_scan_bytes
        )
        sidecar.write(Path(entry.path), entry)
        migrated.append(entry)
    return migrated
