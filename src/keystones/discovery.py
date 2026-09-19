"""Walk the repo and resolve every marker to a target."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from keystones import adapters
from keystones import markers as marker_grammar
from keystones.adapters import fallback
from keystones.adapters.base import ResolutionError
from keystones.adapters.masking import ContractError
from keystones.adapters.treesitter import Unavailable
from keystones.config import Config
from keystones.markers import MarkerError, RegionError
from keystones.models import Finding, Marker, Scope, Severity, Target


@dataclass(frozen=True)
class Resolved:
    marker: Marker
    target: Target
    adapter: object


def _tracked_files(repo_root: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return [line for line in out.splitlines() if line]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [
            str(p.relative_to(repo_root)) for p in repo_root.rglob("*") if p.is_file()
        ]


MAX_SCAN_BYTES = 2_000_000


def _readable(path: Path) -> str | None:
    """Skip binaries and anything too large to be hand-annotated."""
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def source_files(cfg: Config, paths: list[str] | None = None) -> list[str]:
    """Every file type is in scope now that a fallback adapter exists.

    Parsed extensions are always considered. Everything else is included only
    when the word appears in it, which keeps a whole-repo scan cheap.
    """
    candidates = paths if paths is not None else _tracked_files(cfg.repo_root)
    parsed = adapters.parsed_extensions()
    out = []
    for rel in candidates:
        if cfg.is_excluded(rel):
            continue
        full = cfg.repo_root / rel
        if not full.is_file():
            continue
        if rel.endswith(parsed):
            out.append(rel)
            continue
        text = _readable(full)
        if text is not None and marker_grammar.looks_like_a_marker(text):
            out.append(rel)
    return sorted(out)


def collect(
    cfg: Config, paths: list[str] | None = None
) -> tuple[list[Resolved], list[Finding], set[str]]:
    resolved: list[Resolved] = []
    findings: list[Finding] = []
    skipped: set[str] = set()
    for rel in source_files(cfg, paths):
        if adapters.needs_extra(rel):
            skipped.add(rel)
            findings.append(
                Finding(
                    "extra",
                    Severity.ERROR,
                    f"{rel} needs a parser that is not installed. "
                    "Run: pip install 'keystones[all]'",
                    rel,
                )
            )
            continue
        preferred = adapters.for_path(rel)
        if preferred is None:
            continue
        src = _readable(cfg.repo_root / rel)
        if src is None:
            continue
        readable, why = True, ""
        try:
            found = preferred.markers(rel, src)
        except RegionError as exc:
            findings.append(Finding("region", Severity.ERROR, str(exc), rel))
            continue
        except Unavailable as exc:
            skipped.add(rel)
            findings.append(Finding("grammar", Severity.ERROR, str(exc), rel))
            continue
        except MarkerError as exc:
            findings.append(Finding("marker", Severity.ERROR, f"{rel}: {exc}", rel))
            continue
        except ContractError as exc:
            # A plugin bug, not a choice anyone can make in this repo.
            findings.append(Finding("plugin", Severity.ERROR, f"{rel}: {exc}", rel))
            continue
        except ResolutionError as exc:
            # The grammar cannot read the file, or a plugin declined it. Markers
            # are comments, so a text scan still finds them, and one that names
            # a basis is honoured.
            readable = False
            why = str(exc)
            try:
                found = fallback.markers(rel, src)
            except (RegionError, MarkerError) as exc:
                findings.append(Finding("marker", Severity.ERROR, f"{rel}: {exc}", rel))
                continue
        for marker in found:
            try:
                adapter = _adapter_for(rel, marker, preferred, readable, why)
            except adapters.UnknownKind as exc:
                findings.append(
                    Finding("kind", Severity.ERROR, str(exc), rel, marker.lineno)
                )
                continue
            try:
                target = adapter.resolve(src, marker)
            except (ResolutionError, SyntaxError, RegionError) as exc:
                findings.append(
                    Finding("resolve", Severity.ERROR, str(exc), rel, marker.lineno)
                )
                continue
            resolved.append(Resolved(marker, target, adapter))
    return resolved, findings, skipped


def _adapter_for(rel: str, marker: Marker, preferred, readable: bool, why: str = ""):
    """The basis the marker asked for, or the only one available.

    Recorded rather than re-derived, so a grammar that starts reading a file it
    could not read before does not silently move that file's hash basis.
    """
    kind = marker.hash_kind or adapters.default_kind(rel)
    if kind is not None:
        if kind == adapters.TEXT_KIND and marker.scope is Scope.NODE:
            where = (
                "asks for hash=text"
                if marker.hash_kind
                else f"gets hash=text from [[tool.keystones.language]] for {rel}"
            )
            raise adapters.UnknownKind(
                f"{rel}:{marker.lineno}: '{marker.id}' {where}, which has no "
                "nodes to attach to. Use keystone(file, hash=text), a "
                "keystone:start / keystone:end region, or hash= to override."
            )
        return adapters.for_kind(rel, kind)
    if readable:
        return preferred
    raise adapters.UnknownKind(
        f"{rel}:{marker.lineno}: '{marker.id}' has no basis to be hashed with. "
        + (why or f"{rel} does not parse as {preferred.kind_for_path(rel)}")
        + ". Say which with a hash= qualifier: "
        + ", ".join(
            "hash=" + k
            for k in adapters.kinds_for(rel, exclude=preferred.kind_for_path(rel))
        )
    )
