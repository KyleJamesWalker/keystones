"""`depends`: pull named same-repo symbols into a keystone's identity.

A keystone on `compute_payout` says nothing about a helper it calls, so a
behavioural change one frame away passes green. This narrows that hole for
symbols named explicitly. It does not close it, and nothing can close it
without whole-program analysis.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from keystones import adapters
from keystones.models import Finding, Severity

EMPTY = "sha256:" + hashlib.sha256(b"").hexdigest()


class UnresolvedDependency(Exception):
    def __init__(self, spec: str, reason: str) -> None:
        super().__init__(f"{spec}: {reason}")
        self.spec = spec
        self.reason = reason


def _render_one(repo_root: Path, spec: str) -> str:
    if "::" not in spec:
        raise UnresolvedDependency(spec, "expected path/to/file.py::Symbol")
    rel, symbol = spec.split("::", 1)
    adapter = adapters.for_path(rel)
    if adapter is None:
        raise UnresolvedDependency(spec, f"no adapter handles {rel}")
    path = repo_root / rel
    if not path.is_file():
        raise UnresolvedDependency(spec, f"{rel} does not exist")
    try:
        rendered = adapter.render_symbol(path.read_text(), symbol)
    except SyntaxError as exc:
        raise UnresolvedDependency(spec, f"{rel} does not parse: {exc}") from exc
    if rendered is None:
        raise UnresolvedDependency(spec, f"{symbol} not found in {rel}")
    return rendered


def combined_hash(repo_root: Path, specs: list[str]) -> str:
    """Order-independent, so reordering the list is not a change."""
    if not specs:
        return EMPTY
    parts = [f"{spec}={_render_one(repo_root, spec)}" for spec in sorted(set(specs))]
    payload = "\n".join(parts)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def check(repo_root: Path, entries) -> list[Finding]:
    """C11. A declared dependency changing is an owner-review event."""
    findings: list[Finding] = []
    for entry in sorted(entries, key=lambda e: e.id):
        if not entry.depends:
            continue
        try:
            actual = combined_hash(repo_root, entry.depends)
        except UnresolvedDependency as exc:
            findings.append(
                Finding(
                    "C11",
                    Severity.ERROR,
                    f"keystone '{entry.id}' declares a dependency that no longer "
                    f"resolves: {exc}",
                    entry.path,
                    owner_hint=entry.category,
                )
            )
            continue
        if actual != entry.depends_hash:
            findings.append(
                Finding(
                    "C11",
                    Severity.ERROR,
                    f"a dependency of keystone '{entry.id}' changed. Its owner must "
                    'review this. run `keystones fix -m "<why it changed>"`',
                    entry.path,
                    owner_hint=entry.category,
                )
            )
    return findings
