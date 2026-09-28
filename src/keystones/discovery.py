"""Walk the repo and resolve every marker to a target."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from keystones import adapters
from keystones import markers as marker_grammar
from keystones.adapters import fallback
from keystones.adapters.base import ParseFailure, ResolutionError
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


def _readable(path: Path, cap: int = MAX_SCAN_BYTES) -> str | None:
    """Skip binaries and anything too large to be hand-annotated."""
    try:
        if path.stat().st_size > cap:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def unparseable(cfg: Config) -> list[tuple[str, str]]:
    """Every file a parser claims but cannot read, with the parser's first error.

    Reads files with no marker too, since the point is to find the spellings
    to shape before a marker goes in.
    """
    out: list[tuple[str, str]] = []
    for rel in _tracked_files(cfg.repo_root):
        if cfg.is_excluded(rel) or adapters.needs_extra(rel):
            continue
        adapter = adapters.for_path(rel, allow_fallback=False)
        if adapter is None:
            continue
        src = _readable(cfg.repo_root / rel, cfg.max_scan_bytes)
        if src is None:
            continue
        try:
            adapter.markers(rel, src)
        except SyntaxError as exc:
            out.append((rel, f"line {exc.lineno}: {exc.msg}"))
        except (ResolutionError, RegionError, MarkerError) as exc:
            out.append((rel, str(exc).splitlines()[0]))
    return out


def unread(cfg: Config) -> list[str]:
    """Tracked files over the cap, which a scan never opens."""
    out = []
    for rel in _tracked_files(cfg.repo_root):
        if cfg.is_excluded(rel):
            continue
        full = cfg.repo_root / rel
        try:
            if full.is_file() and full.stat().st_size > cfg.max_scan_bytes:
                out.append(rel)
        except OSError:
            continue
    return sorted(out)


def source_files(cfg: Config, paths: list[str] | None = None) -> list[str]:
    """Every file type is in scope now that a fallback adapter exists.

    A file is included only when the marker word appears in it, which keeps a
    whole-repo scan to a read per file and a parse per marked file.
    """
    candidates = paths if paths is not None else _tracked_files(cfg.repo_root)
    out = []
    sidecars = f"{cfg.root.strip('/')}/"
    for rel in candidates:
        if cfg.is_excluded(rel) or rel.startswith(sidecars):
            continue
        full = cfg.repo_root / rel
        if not full.is_file():
            continue
        # A file with no marker in it has nothing to resolve, whatever its
        # parser, and parsing it is where a whole-repo run spends its time.
        text = _readable(full, cfg.max_scan_bytes)
        if text is not None and marker_grammar.looks_like_a_marker(text):
            out.append(rel)
    return sorted(out)


def collect(
    cfg: Config, paths: list[str] | None = None
) -> tuple[list[Resolved], list[Finding], set[str]]:
    resolved, pending, findings, skipped = scan(cfg, paths)
    findings += [
        Finding(
            "pending",
            Severity.ERROR,
            f"a keystone on {item.target} is waiting for its details; "
            "run `keystones add`",
            item.marker.path,
            item.marker.lineno,
        )
        for item in pending
    ]
    return resolved, findings, skipped


def scan(
    cfg: Config, paths: list[str] | None = None
) -> tuple[list[Resolved], list[Resolved], list[Finding], set[str]]:
    """Like `collect`, with `keystone add` markers returned apart as pending.

    A pending item's marker id is a placeholder that exists only in memory.
    """
    resolved: list[Resolved] = []
    pending: list[Resolved] = []
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
        src = _readable(cfg.repo_root / rel, cfg.max_scan_bytes)
        if src is None:
            continue
        src, probes = marker_grammar.probe_pending(src)
        readable, why = True, ""
        try:
            found = preferred.markers(rel, src)
        except SyntaxError as exc:
            skipped.add(rel)
            findings.append(
                Finding(
                    "parse",
                    Severity.ERROR,
                    f"{rel} does not parse: {exc.msg}. A keystone in it cannot be "
                    "checked until it does.",
                    rel,
                    exc.lineno,
                )
            )
            continue
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
        if readable and preferred is not fallback:
            findings += _not_comments(rel, src, found, set(probes.values()))
        for marker in found:
            try:
                adapter = _adapter_for(rel, marker, preferred, readable, why)
            except adapters.UnknownKind as exc:
                if not readable and not marker.hash_kind:
                    # One finding for the file; a kind error per marker and a
                    # C2 per entry would all repeat that it does not parse.
                    if rel not in skipped:
                        skipped.add(rel)
                        findings.append(Finding("parse", Severity.ERROR, str(exc), rel))
                    continue
                findings.append(
                    Finding("kind", Severity.ERROR, str(exc), rel, marker.lineno)
                )
                continue
            if adapter is not preferred and hasattr(adapter, "markers"):
                # The text scan found it; the basis's own scan decides whether
                # it is a comment at all, as inside a YAML block scalar.
                try:
                    real = {m.lineno for m in adapter.markers(rel, src)}
                except (ResolutionError, RegionError, MarkerError):
                    real = None
                if real is not None and marker.lineno not in real:
                    findings.append(
                        Finding(
                            "marker",
                            Severity.WARNING,
                            "looks like a keystone marker but is not a comment, "
                            "so it attaches to nothing. Move it into a comment, "
                            "or add `keystones: ignore-file` if it is only an "
                            "example.",
                            rel,
                            marker.lineno,
                        )
                    )
                    continue
            try:
                target = adapter.resolve(src, marker)
            except ParseFailure as exc:
                # Once per file: every marker in it fails for the same reason,
                # and its entries are not orphans.
                if rel not in skipped:
                    skipped.add(rel)
                    findings.append(Finding("parse", Severity.ERROR, str(exc), rel))
                continue
            except (ResolutionError, SyntaxError, RegionError) as exc:
                findings.append(
                    Finding("resolve", Severity.ERROR, str(exc), rel, marker.lineno)
                )
                continue
            (pending if marker.id in probes else resolved).append(
                Resolved(marker, target, adapter)
            )
    return resolved, pending, findings, skipped


def _not_comments(
    rel: str, src: str, found: list[Marker], pending: set[int]
) -> list[Finding]:
    """Marker-shaped lines the lexer did not see as comments: a marker inside a
    string literal attaches to nothing, and saying so beats silence."""
    if marker_grammar.is_ignored(src):
        return []
    seen = {m.lineno for m in found} | pending
    out = []
    for lineno, raw in enumerate(marker_grammar.split_lines(src), start=1):
        line = raw.rstrip("\r\n")
        if lineno in seen or not marker_grammar.is_marker(line):
            continue
        if not marker_grammar.parse_point(line, rel, lineno) and not (
            marker_grammar.parse_region_start(line, rel, lineno)
        ):
            continue
        out.append(
            Finding(
                "marker",
                Severity.WARNING,
                "looks like a keystone marker but is not a comment, so it "
                "attaches to nothing. Move it into a comment, or add "
                "`keystones: ignore-file` if it is only an example.",
                rel,
                lineno,
            )
        )
    return out


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
