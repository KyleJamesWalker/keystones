"""Walk the repo and resolve every marker to a target."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from keystones import adapters
from keystones.adapters.base import ResolutionError
from keystones.config import Config
from keystones.models import Finding, Marker, Severity, Target


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


def source_files(cfg: Config, paths: list[str] | None = None) -> list[str]:
    candidates = paths if paths is not None else _tracked_files(cfg.repo_root)
    supported = adapters.supported_extensions()
    return sorted(
        rel
        for rel in candidates
        if rel.endswith(supported)
        and not cfg.is_excluded(rel)
        and (cfg.repo_root / rel).is_file()
    )


def collect(
    cfg: Config, paths: list[str] | None = None
) -> tuple[list[Resolved], list[Finding]]:
    resolved: list[Resolved] = []
    findings: list[Finding] = []
    for rel in source_files(cfg, paths):
        adapter = adapters.for_path(rel)
        if adapter is None:
            continue
        src = (cfg.repo_root / rel).read_text()
        for marker in adapter.markers(rel, src):
            try:
                target = adapter.resolve(src, marker)
            except (ResolutionError, SyntaxError) as exc:
                findings.append(
                    Finding("resolve", Severity.ERROR, str(exc), rel, marker.lineno)
                )
                continue
            resolved.append(Resolved(marker, target, adapter))
    return resolved, findings
