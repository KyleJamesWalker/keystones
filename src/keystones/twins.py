"""`twins`: copies of a keystoned definition that must keep hashing the same.

A predicate duplicated across files, one per ad platform or one per job, is
reviewed once through the keystone and the copies are held to it. A twin is
a same-repo target hashed with the keystone's own basis, so it must be the
same language and shape; a dbt macro and its compiled SQL cannot be twins.
"""

from __future__ import annotations

from pathlib import Path

from keystones import adapters
from keystones.adapters.base import ResolutionError
from keystones.models import Entry, Finding, Marker, Scope, Severity, Target


class UnresolvedTwin(Exception):
    def __init__(self, spec: str, reason: str, basis: bool = False) -> None:
        super().__init__(f"{spec}: {reason}")
        self.spec = spec
        self.reason = reason
        # True when the keystone's basis cannot hash the twin's file at all.
        self.basis = basis


def resolve(repo_root: Path, spec: str, kind: str | None) -> tuple[object, Target, str]:
    """The adapter, target and source for a twin, hashed with `kind`."""
    rel, _, qualname = spec.partition("::")
    if "#" in rel:
        raise UnresolvedTwin(spec, "a region cannot be a twin; name a definition")
    path = repo_root / rel
    if not path.is_file():
        raise UnresolvedTwin(spec, f"{rel} does not exist")
    if adapters.needs_extra(rel):
        raise UnresolvedTwin(spec, f"{rel} needs a parser that is not installed")
    try:
        adapter = adapters.for_kind(rel, kind) if kind else adapters.for_symbols(rel)
    except adapters.UnknownKind as exc:
        raise UnresolvedTwin(spec, str(exc), basis=True) from exc
    src = path.read_text()
    try:
        if qualname:
            target = adapter.target_for_qualname(rel, src, qualname)
            if target is None:
                raise UnresolvedTwin(spec, f"{qualname} not found in {rel}")
        else:
            target = adapter.resolve(src, Marker("twin", "default", Scope.FILE, rel, 1))
    except (SyntaxError, ResolutionError) as exc:
        raise UnresolvedTwin(spec, f"{rel} does not parse: {exc}") from exc
    return adapter, target, src


def semantic(repo_root: Path, spec: str, kind: str | None) -> tuple[str, Target]:
    adapter, target, src = resolve(repo_root, spec, kind)
    try:
        return adapter.hashes(src, target)[0], target
    except ResolutionError as exc:
        raise UnresolvedTwin(spec, f"cannot be hashed: {exc}") from exc


def check(repo_root: Path, entries: list[Entry]) -> list[Finding]:
    """C17. A twin that no longer hashes like its keystone is unreviewed drift."""
    findings: list[Finding] = []
    for entry in sorted(entries, key=lambda e: (e.category, e.id)):
        for spec in entry.twins:
            rel = spec.partition("::")[0]
            try:
                actual, target = semantic(repo_root, spec, entry.hash or None)
            except UnresolvedTwin as exc:
                if exc.basis:
                    findings.append(
                        Finding(
                            "C14",
                            Severity.ERROR,
                            f"twin {spec} of keystone '{entry.id}' cannot be hashed "
                            f"with its basis (hash={entry.hash or 'auto'}): "
                            f"{exc.reason}. A twin must share the keystone's basis.",
                            rel,
                            owner_hint=entry.category,
                        )
                    )
                    continue
                findings.append(
                    Finding(
                        "C17",
                        Severity.ERROR,
                        f"twin of keystone '{entry.id}' cannot be found: {exc}. "
                        "Restore it, or drop it from `twins` in the sidecar.",
                        rel,
                        owner_hint=entry.category,
                    )
                )
                continue
            if actual != entry.semantic:
                findings.append(
                    Finding(
                        "C17",
                        Severity.ERROR,
                        f"{spec} no longer matches keystone '{entry.id}'. Bring the "
                        "copy back in line with the reviewed code, or change both "
                        'and run `keystones fix -m "<why>"`.',
                        rel,
                        target.start,
                        owner_hint=entry.category,
                    )
                )
    return findings
