"""doctor. CODEOWNERS requests a reviewer; only branch protection requires one."""

import pytest

from keystones import doctor
from keystones.models import Severity

HEALTHY = {
    "required_pull_request_reviews": {
        "require_code_owner_reviews": True,
        "required_approving_review_count": 1,
        "dismiss_stale_reviews": True,
    },
    "enforce_admins": {"enabled": True},
    "required_status_checks": {"contexts": ["keystones", "test (py3.12)"]},
}


@pytest.fixture
def api(monkeypatch):
    """Route every API read through a table the test controls."""
    state = {"protection": HEALTHY, "errors": {"errors": []}, "protection_status": 200}

    def fake_get(path, token):
        if path.endswith("/codeowners/errors"):
            return 200, state["errors"]
        if "/branches/" in path:
            return state["protection_status"], state["protection"]
        return 200, {"default_branch": "main"}

    monkeypatch.setattr(doctor, "_get", fake_get)
    monkeypatch.setattr(doctor, "_token", lambda: "t")
    monkeypatch.setattr(doctor, "slug", lambda root: ("o", "r"))
    return state


def errors(findings):
    return [f for f in findings if f.severity is Severity.ERROR]


def test_healthy_repo_has_no_errors(tmp_path, api):
    assert errors(doctor.run(tmp_path)) == []


def test_missing_protection_is_an_error(tmp_path, api):
    api["protection_status"] = 404
    assert len(errors(doctor.run(tmp_path))) == 1


def test_code_owner_review_off_is_an_error(tmp_path, api):
    api["protection"] = {
        **HEALTHY,
        "required_pull_request_reviews": {
            **HEALTHY["required_pull_request_reviews"],
            "require_code_owner_reviews": False,
        },
    }
    assert any("Code Owners" in f.message for f in errors(doctor.run(tmp_path)))


def test_stale_approvals_not_dismissed_is_an_error(tmp_path, api):
    """Approve the sidecar, then push the real change: the gate is defeated."""
    api["protection"] = {
        **HEALTHY,
        "required_pull_request_reviews": {
            **HEALTHY["required_pull_request_reviews"],
            "dismiss_stale_reviews": False,
        },
    }
    assert any("stale" in f.message for f in errors(doctor.run(tmp_path)))


def test_missing_required_check_is_an_error(tmp_path, api):
    api["protection"] = {**HEALTHY, "required_status_checks": {"contexts": ["test"]}}
    assert any(
        "required status check" in f.message for f in errors(doctor.run(tmp_path))
    )


def test_admin_bypass_warns_but_does_not_fail(tmp_path, api):
    api["protection"] = {**HEALTHY, "enforce_admins": {"enabled": False}}
    findings = doctor.run(tmp_path)
    assert errors(findings) == []
    assert any(f.severity is Severity.WARNING for f in findings)


def test_invalid_codeowners_line_is_an_error(tmp_path, api):
    api["errors"] = {
        "errors": [
            {
                "line": 4,
                "message": "Unknown owner @org/typo",
                "path": ".github/CODEOWNERS",
            }
        ]
    }
    assert any("silently inert" in f.message for f in errors(doctor.run(tmp_path)))


def test_no_token_is_a_skip_not_a_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(doctor, "slug", lambda root: ("o", "r"))
    with pytest.raises(doctor.Unavailable):
        doctor.run(tmp_path)
