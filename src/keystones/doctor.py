"""Verify the repository is configured so owner review is actually required.

CODEOWNERS requests a reviewer. It does not require one. Without the branch
protection settings below, every other check in this tool is advisory.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from keystones.models import Finding, Severity

API = "https://api.github.com"
_REMOTE_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)")


class Unavailable(Exception):
    """No token or no remote. A reason to skip, not to fail."""


def slug(repo_root: Path) -> tuple[str, str]:
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise Unavailable("no origin remote") from exc
    match = _REMOTE_RE.search(url)
    if not match:
        raise Unavailable(f"origin is not a GitHub remote: {url}")
    return match["owner"], match["repo"]


def _token() -> str:
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    raise Unavailable("no GH_TOKEN or GITHUB_TOKEN in the environment")


def _get(path: str, token: str) -> tuple[int, object]:
    request = urllib.request.Request(
        f"{API}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except urllib.error.URLError as exc:
        raise Unavailable(f"cannot reach the GitHub API: {exc.reason}") from exc


def run(repo_root: Path, required_check: str = "keystones") -> list[Finding]:
    owner, repo = slug(repo_root)
    token = _token()
    findings: list[Finding] = []

    status, errors = _get(f"/repos/{owner}/{repo}/codeowners/errors", token)
    if status == 200 and isinstance(errors, dict):
        for error in errors.get("errors", []):
            findings.append(
                Finding(
                    "doctor",
                    Severity.ERROR,
                    f"CODEOWNERS line {error.get('line')}: {error.get('message')}. "
                    "An invalid line is silently inert, so the owner is never "
                    "requested.",
                    error.get("path"),
                    error.get("line"),
                )
            )

    status, meta = _get(f"/repos/{owner}/{repo}", token)
    branch = meta.get("default_branch", "main") if isinstance(meta, dict) else "main"

    status, protection = _get(
        f"/repos/{owner}/{repo}/branches/{branch}/protection", token
    )
    if status == 404:
        findings.append(
            Finding(
                "doctor",
                Severity.ERROR,
                f"branch '{branch}' has no protection, so nothing requires review "
                "and every keystone is advisory",
            )
        )
        return findings
    if status == 403:
        # A valid token without admin scope genuinely cannot look. The default
        # GITHUB_TOKEN is always this, so failing here would fail every CI run.
        raise Unavailable("the token cannot read branch protection (needs admin scope)")
    if status == 401:
        # A token that was supplied but cannot read this is the case where a
        # rotated secret would otherwise leave the audit green forever.
        findings.append(
            Finding(
                "doctor",
                Severity.ERROR,
                f"the token was rejected: HTTP {status}. It is invalid or "
                "expired, so this audit vouches for nothing.",
            )
        )
        return findings
    if status != 200 or not isinstance(protection, dict):
        findings.append(
            Finding(
                "doctor",
                Severity.ERROR,
                f"unexpected response reading branch protection: HTTP {status}",
            )
        )
        return findings

    reviews = protection.get("required_pull_request_reviews")
    if not reviews:
        findings.append(
            Finding(
                "doctor",
                Severity.ERROR,
                f"'{branch}' does not require pull request review",
            )
        )
    else:
        if not reviews.get("require_code_owner_reviews"):
            findings.append(
                Finding(
                    "doctor",
                    Severity.ERROR,
                    "'Require review from Code Owners' is off, so CODEOWNERS only "
                    "suggests a reviewer and the gate has no teeth",
                )
            )
        if reviews.get("required_approving_review_count", 0) < 1:
            findings.append(
                Finding("doctor", Severity.ERROR, "required approving reviews is 0")
            )
        if not reviews.get("dismiss_stale_reviews"):
            findings.append(
                Finding(
                    "doctor",
                    Severity.ERROR,
                    "'Dismiss stale pull request approvals' is off. Get the sidecar "
                    "approved, then push the commit that changes the keystone, and the "
                    "stale approval still counts. This defeats the gate outright.",
                )
            )

    if not protection.get("enforce_admins", {}).get("enabled"):
        findings.append(
            Finding(
                "doctor",
                Severity.WARNING,
                "administrators can bypass protection, so the gate holds by convention "
                "for them rather than by configuration",
            )
        )

    contexts = protection.get("required_status_checks", {}).get("contexts", [])
    if not any(required_check in context for context in contexts):
        findings.append(
            Finding(
                "doctor",
                Severity.ERROR,
                f"no required status check matches '{required_check}'; the "
                "keystones job can fail without blocking a merge. "
                f"Required: {contexts or 'none'}",
            )
        )
    return findings
