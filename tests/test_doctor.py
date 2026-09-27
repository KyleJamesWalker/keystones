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
    state = {
        "protection": HEALTHY,
        "errors": {"errors": []},
        "protection_status": 200,
        "rules": [],
        "rules_status": 200,
        "rulesets": {},
    }

    def fake_get(path, token):
        if path.endswith("/codeowners/errors"):
            return 200, state["errors"]
        if "/rules/branches/" in path:
            return state["rules_status"], state["rules"]
        if "/rulesets/" in path:
            ruleset = state["rulesets"].get(int(path.rsplit("/", 1)[1]))
            return (200, ruleset) if ruleset else (404, {})
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


def fake_gh(monkeypatch, stdout="", error=None):
    """Stand in for `gh auth token`, so no test reads a real login."""
    import subprocess

    def run(argv, **kwargs):
        assert argv == ["gh", "auth", "token"]
        if error is not None:
            raise error
        return subprocess.CompletedProcess(argv, 0 if stdout else 1, stdout, "")

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(doctor.subprocess, "run", run)


@pytest.mark.parametrize("error", [None, FileNotFoundError("gh")])
def test_no_token_is_a_skip_not_a_failure(tmp_path, monkeypatch, error):
    fake_gh(monkeypatch, error=error)
    monkeypatch.setattr(doctor, "slug", lambda root: ("o", "r"))
    with pytest.raises(doctor.Unavailable, match="gh auth token"):
        doctor.run(tmp_path)


def test_a_gh_login_stands_in_for_a_token(monkeypatch):
    fake_gh(monkeypatch, stdout="gho_example\n")
    assert doctor._token() == "gho_example"


def test_an_environment_token_wins_over_gh(monkeypatch):
    fake_gh(monkeypatch, stdout="gho_example\n")
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    assert doctor._token() == "from-env"


def ruleset_rules(ruleset_id=7, source_type="Organization", **overrides):
    """What GET /rules/branches/{branch} returns for one healthy ruleset."""
    source = {
        "ruleset_id": ruleset_id,
        "ruleset_source_type": source_type,
        "ruleset_source": "acme",
    }
    params = {
        "required_approving_review_count": 1,
        "dismiss_stale_reviews_on_push": True,
        "require_code_owner_review": True,
        "require_last_push_approval": False,
        "required_review_thread_resolution": False,
        **overrides,
    }
    return [
        {**source, "type": "pull_request", "parameters": params},
        {
            **source,
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [{"context": "keystones"}],
                "strict_required_status_checks_policy": False,
            },
        },
    ]


def test_a_ruleset_alone_satisfies_every_requirement(tmp_path, api):
    api["protection_status"] = 404
    api["rules"] = ruleset_rules()
    api["rulesets"] = {7: {"name": "protect-main"}}
    report = doctor.audit(tmp_path)
    assert errors(report.findings) == []
    assert set(report.satisfied.values()) == {
        "ruleset 'protect-main' (organization acme)"
    }


def test_a_member_token_is_enough_when_a_ruleset_applies(tmp_path, api):
    """Classic protection needs admin to read; branch rules do not."""
    api["protection_status"] = 403
    api["rules"] = ruleset_rules()
    assert errors(doctor.run(tmp_path)) == []


def test_a_member_token_with_no_ruleset_is_still_a_skip(tmp_path, api):
    api["protection_status"] = 403
    with pytest.raises(doctor.Unavailable):
        doctor.run(tmp_path)


def test_a_ruleset_fills_what_classic_protection_leaves_out(tmp_path, api):
    api["protection"] = {
        **HEALTHY,
        "required_pull_request_reviews": {
            **HEALTHY["required_pull_request_reviews"],
            "dismiss_stale_reviews": False,
        },
    }
    api["rules"] = ruleset_rules(source_type="Repository")
    report = doctor.audit(tmp_path)
    assert errors(report.findings) == []
    assert report.satisfied[doctor.CODE_OWNER] == doctor.CLASSIC
    assert report.satisfied[doctor.STALE] == "ruleset 7 (repository acme)"


def test_a_ruleset_without_code_owner_review_is_an_error(tmp_path, api):
    api["protection_status"] = 404
    api["rules"] = ruleset_rules(require_code_owner_review=False)
    assert any("Code Owners" in f.message for f in errors(doctor.run(tmp_path)))


def test_required_reviewers_are_reported(tmp_path, api):
    api["protection_status"] = 404
    api["rules"] = ruleset_rules(
        required_reviewers=[
            {
                "file_patterns": ["keystones/**"],
                "minimum_approvals": 2,
                "reviewer": {"id": 42, "type": "Team"},
            }
        ]
    )
    (entry,) = doctor.audit(tmp_path).required_reviewers
    assert entry.patterns == ("keystones/**",)
    assert entry.minimum_approvals == 2
    assert entry.source == "ruleset 7 (organization acme)"


def test_a_token_the_rules_endpoint_rejects_is_an_error(tmp_path, api):
    api["protection_status"] = 404
    api["rules_status"] = 401
    assert any("rejected" in f.message for f in errors(doctor.run(tmp_path)))


def test_the_cli_names_the_source_of_each_requirement(repo, run_cli, api, capsys):
    api["protection_status"] = 404
    api["rules"] = ruleset_rules()
    api["rulesets"] = {7: {"name": "protect-main"}}
    assert run_cli("doctor") == 0
    out = capsys.readouterr().out
    assert "code owner review: ruleset 'protect-main' (organization acme)" in out


def warnings(findings):
    return [f.message for f in findings if f.severity is Severity.WARNING]


def test_a_ruleset_nobody_can_bypass_says_nothing(tmp_path, api):
    api["protection_status"] = 404
    api["rules"] = ruleset_rules()
    api["rulesets"] = {7: {"name": "protect-main", "bypass_actors": []}}
    assert warnings(doctor.run(tmp_path)) == []


def test_a_ruleset_with_bypass_actors_warns(tmp_path, api):
    api["protection_status"] = 404
    api["rules"] = ruleset_rules()
    api["rulesets"] = {
        7: {
            "name": "protect-main",
            "bypass_actors": [{"actor_type": "OrganizationAdmin"}],
        }
    }
    findings = doctor.run(tmp_path)
    assert errors(findings) == []
    assert warnings(findings) == [
        "ruleset 'protect-main' (organization acme) lets 1 actor(s) bypass it, so "
        "the gate holds by convention for them rather than by configuration"
    ]


@pytest.mark.parametrize(
    "rulesets", [{7: {"name": "protect-main"}}, {}], ids=["hidden", "unreadable"]
)
def test_a_bypass_list_the_token_cannot_see_warns(tmp_path, api, rulesets):
    """GitHub returns bypass_actors only to a token with write access."""
    api["protection_status"] = 404
    api["rules"] = ruleset_rules()
    api["rulesets"] = rulesets
    findings = doctor.run(tmp_path)
    assert errors(findings) == []
    (message,) = warnings(findings)
    assert "cannot read who may bypass it" in message
