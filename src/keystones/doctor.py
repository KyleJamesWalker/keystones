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
from dataclasses import dataclass, field
from pathlib import Path

from keystones.models import Finding, Severity

API = "https://api.github.com"
_REMOTE_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)")


class Unavailable(Exception):
    """No token or no remote. A reason to skip, not to fail."""


CLASSIC = "branch protection"
REVIEW = "pull request review"
CODE_OWNER = "code owner review"
APPROVALS = "at least one approval"
STALE = "stale approvals dismissed"
CHECK = "required status check"


@dataclass(frozen=True)
class RequiredReviewers:
    """A ruleset `required_reviewers` entry: files matching these need approvals."""

    patterns: tuple[str, ...]
    minimum_approvals: int
    reviewer: str
    source: str

    def covering(self, path: str) -> str | None:
        """The pattern that puts `path` under this rule, if one does."""
        if self.minimum_approvals < 1:
            return None
        return next((p for p in self.patterns if _fnmatch_path(p, path)), None)


def _fnmatch_path(pattern: str, path: str) -> bool:
    """fnmatch with FNM_PATHNAME: `*` stays within a segment, `**` crosses them."""
    out, i, body = [], 0, pattern.lstrip("/")
    while i < len(body):
        if body.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif body.startswith("**", i):
            out.append(".*")
            i += 2
        else:
            char = body[i]
            out.append({"*": "[^/]*", "?": "[^/]"}.get(char, re.escape(char)))
            i += 1
    return re.fullmatch("".join(out), path) is not None


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    # Each requirement that holds, and the protection that makes it hold.
    satisfied: dict[str, str] = field(default_factory=dict)
    required_reviewers: list[RequiredReviewers] = field(default_factory=list)
    # Every ruleset applying to the branch and what it requires, relied on or not.
    rulesets: dict[str, list[str]] = field(default_factory=dict)


def slug(repo_root: Path, repo: str | None = None) -> tuple[str, str]:
    """`owner/name`: given, from `GITHUB_REPOSITORY`, or from the origin remote."""
    named = repo or os.environ.get("GITHUB_REPOSITORY")
    if named:
        owner, _, name = named.partition("/")
        if not owner or not name:
            raise Unavailable(f"repo must be owner/name, not {named!r}")
        return owner, name
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise Unavailable(
            "no origin remote; pass --repo owner/name or set GITHUB_REPOSITORY"
        ) from exc
    match = _REMOTE_RE.search(url)
    if not match:
        raise Unavailable(f"origin is not a GitHub remote: {url}")
    return match["owner"], match["repo"]


def _token() -> str:
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    try:
        token = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        token = ""
    if token:
        return token
    raise Unavailable(
        "no GH_TOKEN or GITHUB_TOKEN in the environment, and `gh auth token` gave none"
    )


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


def _paged(path: str, token: str) -> tuple[int, list]:
    items: list = []
    page = 1
    while True:
        status, body = _get(f"{path}?per_page=100&page={page}", token)
        if status != 200 or not isinstance(body, list):
            return status, items
        items += body
        if len(body) < 100:
            return status, items
        page += 1


def _ruleset_label(
    owner: str, repo: str, rule: dict, token: str, rulesets: dict
) -> str:
    """Also caches the ruleset itself in `rulesets`, None where it cannot be read."""
    ruleset_id = rule.get("ruleset_id")
    if ruleset_id not in rulesets:
        status, body = _get(f"/repos/{owner}/{repo}/rulesets/{ruleset_id}", token)
        rulesets[ruleset_id] = (
            body if status == 200 and isinstance(body, dict) else None
        )
    where = " ".join(
        str(part)
        for part in (rule.get("ruleset_source_type"), rule.get("ruleset_source"))
        if part
    ).lower()
    name = (rulesets[ruleset_id] or {}).get("name")
    label = f"ruleset '{name}'" if name else f"ruleset {ruleset_id}"
    return f"{label} ({where})" if where else label


def _rejected(status: int) -> Finding:
    # A token that was supplied but cannot read this is the case where a
    # rotated secret would otherwise leave the audit green forever.
    return Finding(
        "doctor",
        Severity.ERROR,
        f"the token was rejected: HTTP {status}. It is invalid or "
        "expired, so this audit vouches for nothing.",
    )


def _reviewers_in(params: dict, label: str) -> list[RequiredReviewers]:
    out = []
    for entry in params.get("required_reviewers") or []:
        reviewer = entry.get("reviewer") or {}
        out.append(
            RequiredReviewers(
                tuple(entry.get("file_patterns") or ()),
                int(entry.get("minimum_approvals") or 0),
                f"{reviewer.get('type', '')} {reviewer.get('id', '')}".strip(),
                label,
            )
        )
    return out


def _branch_rules(repo_root: Path) -> tuple[str, str, str, list]:
    owner, repo = slug(repo_root)
    token = _token()
    status, meta = _get(f"/repos/{owner}/{repo}", token)
    branch = meta.get("default_branch", "main") if isinstance(meta, dict) else "main"
    status, rules = _paged(f"/repos/{owner}/{repo}/rules/branches/{branch}", token)
    if status != 200:
        raise Unavailable(f"cannot read the rules for '{branch}': HTTP {status}")
    return owner, repo, token, [r for r in rules if isinstance(r, dict)]


def required_reviewers(repo_root: Path) -> list[RequiredReviewers]:
    """Only the ruleset rules that name reviewers for paths. See C8."""
    owner, repo, token, rules = _branch_rules(repo_root)
    rulesets: dict = {}
    out = []
    for rule in rules:
        if rule.get("type") == "pull_request":
            label = _ruleset_label(owner, repo, rule, token, rulesets)
            out += _reviewers_in(rule.get("parameters") or {}, label)
    return out


def rulesets_applying(repo_root: Path) -> list[str]:
    """The label of every ruleset with a rule on the default branch."""
    owner, repo, token, rules = _branch_rules(repo_root)
    rulesets: dict = {}
    labels: list[str] = []
    for rule in rules:
        label = _ruleset_label(owner, repo, rule, token, rulesets)
        if label not in labels:
            labels.append(label)
    return labels


def run(repo_root: Path, required_check: str = "keystones") -> list[Finding]:
    return audit(repo_root, required_check).findings


def audit(
    repo_root: Path,
    required_check: str = "keystones",
    sidecar_paths: list[str] | None = None,
    repo: str | None = None,
) -> Report:
    """Classic branch protection and rulesets together, as GitHub enforces them.

    `sidecar_paths`, given under `codeowners_from_rulesets`, lets required
    reviewers covering every one of them stand in for code owner review.
    """
    from keystones import codeowners

    owner, repo = slug(repo_root, repo)
    token = _token()
    report = Report()
    findings = report.findings
    # "Require review from Code Owners" requires nobody without a CODEOWNERS
    # file, so the flag alone does not satisfy the requirement.
    has_codeowners = codeowners.find(repo_root)[0] is not None

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
    if status == 401:
        findings.append(_rejected(status))
        return report
    if status not in (200, 403, 404) or (
        status == 200 and not isinstance(protection, dict)
    ):
        findings.append(
            Finding(
                "doctor",
                Severity.ERROR,
                f"unexpected response reading branch protection: HTTP {status}",
            )
        )
        return report
    classic = protection if status == 200 else None

    # Readable with a member token, unlike classic protection.
    rules_status, rules = _paged(
        f"/repos/{owner}/{repo}/rules/branches/{branch}", token
    )
    if rules_status == 401:
        findings.append(_rejected(rules_status))
        return report
    if rules_status != 200:
        findings.append(
            Finding(
                "doctor",
                Severity.ERROR,
                f"unexpected response reading the rules for '{branch}': "
                f"HTTP {rules_status}",
            )
        )
        return report
    rulesets: dict = {}
    rules = [
        (rule, _ruleset_label(owner, repo, rule, token, rulesets))
        for rule in rules
        if isinstance(rule, dict)
        and rule.get("type") in ("pull_request", "required_status_checks")
    ]

    if classic is None and not rules:
        if status == 403:
            # A valid token without admin scope genuinely cannot look. The
            # default GITHUB_TOKEN is always this, so failing here would fail
            # every CI run.
            raise Unavailable(
                "the token cannot read branch protection (needs admin scope), "
                f"and no ruleset applies to '{branch}'"
            )
        findings.append(
            Finding(
                "doctor",
                Severity.ERROR,
                f"branch '{branch}' has no protection and no ruleset, so nothing "
                "requires review and every keystone is advisory",
            )
        )
        return report

    satisfied = report.satisfied
    contexts: list[str] = []
    reviews = (classic or {}).get("required_pull_request_reviews")
    if reviews:
        satisfied.setdefault(REVIEW, CLASSIC)
        if reviews.get("require_code_owner_reviews") and has_codeowners:
            satisfied.setdefault(CODE_OWNER, CLASSIC)
        if reviews.get("required_approving_review_count", 0) >= 1:
            satisfied.setdefault(APPROVALS, CLASSIC)
        if reviews.get("dismiss_stale_reviews"):
            satisfied.setdefault(STALE, CLASSIC)
    classic_contexts = ((classic or {}).get("required_status_checks") or {}).get(
        "contexts", []
    )
    contexts += classic_contexts
    if any(required_check in context for context in classic_contexts):
        satisfied.setdefault(CHECK, CLASSIC)

    for rule, label in rules:
        params = rule.get("parameters") or {}
        provides = report.rulesets.setdefault(label, [])
        if rule["type"] == "required_status_checks":
            ruled = [
                check.get("context", "")
                for check in params.get("required_status_checks") or []
            ]
            contexts += ruled
            if any(required_check in context for context in ruled):
                satisfied.setdefault(CHECK, label)
                provides.append(CHECK)
            continue
        satisfied.setdefault(REVIEW, label)
        provides.append(REVIEW)
        if params.get("require_code_owner_review"):
            provides.append(CODE_OWNER)
            if has_codeowners:
                satisfied.setdefault(CODE_OWNER, label)
        if params.get("required_approving_review_count", 0) >= 1:
            satisfied.setdefault(APPROVALS, label)
            provides.append(APPROVALS)
        if params.get("dismiss_stale_reviews_on_push"):
            satisfied.setdefault(STALE, label)
            provides.append(STALE)
        report.required_reviewers += _reviewers_in(params, label)

    if CODE_OWNER not in satisfied and sidecar_paths:
        covering = [
            next((r for r in report.required_reviewers if r.covering(p)), None)
            for p in sidecar_paths
        ]
        if all(covering):
            sources = sorted({r.source for r in covering})
            satisfied[CODE_OWNER] = "required reviewers in " + ", ".join(sources)

    if CODE_OWNER not in satisfied and not has_codeowners and REVIEW in satisfied:
        findings.append(
            Finding(
                "doctor",
                Severity.ERROR,
                "no CODEOWNERS file, so 'Require review from Code Owners' "
                "requires nobody and the sidecars are unguarded",
            )
        )
    if REVIEW not in satisfied:
        findings.append(
            Finding(
                "doctor",
                Severity.ERROR,
                f"'{branch}' does not require pull request review",
            )
        )
    else:
        if CODE_OWNER not in satisfied:
            findings.append(
                Finding(
                    "doctor",
                    Severity.ERROR,
                    "'Require review from Code Owners' is off, so CODEOWNERS only "
                    "suggests a reviewer and the gate has no teeth",
                )
            )
        if APPROVALS not in satisfied:
            findings.append(
                Finding("doctor", Severity.ERROR, "required approving reviews is 0")
            )
        if STALE not in satisfied:
            findings.append(
                Finding(
                    "doctor",
                    Severity.ERROR,
                    "'Dismiss stale pull request approvals' is off. Get the sidecar "
                    "approved, then push the commit that changes the keystone, and the "
                    "stale approval still counts. This defeats the gate outright.",
                )
            )

    if classic is not None and not (classic.get("enforce_admins") or {}).get("enabled"):
        findings.append(
            Finding(
                "doctor",
                Severity.WARNING,
                "administrators can bypass protection, so the gate holds by convention "
                "for them rather than by configuration",
            )
        )

    # Every applying ruleset, relied on or not: one beside classic protection
    # still lets its bypass actors merge past the gate.
    audited: set[str] = set()
    for rule, label in rules:
        ruleset = rulesets.get(rule.get("ruleset_id"))
        if label in audited:
            continue
        audited.add(label)
        # Returned only to a token with write access to the ruleset.
        if ruleset is None or "bypass_actors" not in ruleset:
            findings.append(
                Finding(
                    "doctor",
                    Severity.WARNING,
                    f"{label}: this token cannot read who may bypass it, which "
                    "needs write access to the ruleset, so the gate may hold by "
                    "convention for someone",
                )
            )
        elif ruleset["bypass_actors"]:
            findings.append(
                Finding(
                    "doctor",
                    Severity.WARNING,
                    f"{label} lets {len(ruleset['bypass_actors'])} actor(s) bypass "
                    "it, so the gate holds by convention for them rather than by "
                    "configuration",
                )
            )

    if CHECK not in satisfied:
        findings.append(
            Finding(
                "doctor",
                Severity.ERROR,
                f"no required status check matches '{required_check}'; the "
                "keystones job can fail without blocking a merge. "
                f"Required: {contexts or 'none'}",
            )
        )
    return report
