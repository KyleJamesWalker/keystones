"""Reading the repository at another ref, for base-versus-head comparison."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def resolve_base(repo_root: Path, explicit: str | None = None) -> str | None:
    """pre-commit supplies a ref on pre-push; Actions supplies the PR base.

    Returns None rather than guessing, because a wrong base turns every
    keystone in the repo into a false removal.
    """
    for candidate in (explicit, os.environ.get("PRE_COMMIT_FROM_REF")):
        if candidate and exists(repo_root, candidate):
            return candidate
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref:
        for candidate in (f"origin/{base_ref}", base_ref):
            if exists(repo_root, candidate):
                return candidate
    return None


def exists(repo_root: Path, ref: str) -> bool:
    return (
        _git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        is not None
    )


def merge_base(repo_root: Path, ref: str) -> str | None:
    out = _git(repo_root, "merge-base", ref, "HEAD")
    return out.strip() if out else None


def files_at(repo_root: Path, ref: str) -> list[str]:
    out = _git(repo_root, "ls-tree", "-r", "--name-only", ref)
    return [line for line in out.splitlines() if line] if out else []


def read_at(repo_root: Path, ref: str, path: str) -> str | None:
    return _git(repo_root, "show", f"{ref}:{path}")
