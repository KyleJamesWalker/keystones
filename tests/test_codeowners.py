"""C8 and the CODEOWNERS resolver."""

import pytest

from keystones.codeowners import owners_for, parse

RULES = """
/keystones/finance/  @org/finance-eng
/pyproject.toml      @org/staff-eng
*.py                 @org/eng
docs/                @org/docs
"""


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("keystones/finance/payout.md", "/keystones/finance/"),
        ("pyproject.toml", "/pyproject.toml"),
        ("src/deep/nested/mod.py", "*.py"),
        ("docs/guide/intro.md", "docs/"),
        ("README.md", None),
    ],
)
def test_pattern_resolution(path, expected):
    rule = owners_for(parse(RULES), path)
    assert (rule.pattern if rule else None) == expected


def test_last_match_wins_not_first():
    """The failure mode a naive 'some rule matches' check cannot see."""
    rules = parse("/keystones/finance/ @org/finance-eng\n*.md @org/docs\n")
    winner = owners_for(rules, "keystones/finance/payout.md")
    assert winner.owners == ("@org/docs",), "a later broad rule reassigns the sidecar"


def test_anchored_pattern_does_not_match_elsewhere():
    rules = parse("/build/ @org/eng\n")
    assert owners_for(rules, "build/out.txt") is not None
    assert owners_for(rules, "src/build/out.txt") is None


def test_star_does_not_cross_a_slash():
    rules = parse("/src/*.py @org/eng\n")
    assert owners_for(rules, "src/mod.py") is not None
    assert owners_for(rules, "src/pkg/mod.py") is None


def test_doublestar_crosses_slashes():
    rules = parse("/src/**/*.py @org/eng\n")
    assert owners_for(rules, "src/pkg/deep/mod.py") is not None


def test_comments_and_blank_lines_ignored():
    assert len(parse("# comment\n\n/a @o\n")) == 1


def test_rule_without_owners_is_parsed_as_unowned():
    rule = owners_for(
        parse("/keystones/ @org/eng\n/keystones/tmp/\n"), "keystones/tmp/x.md"
    )
    assert rule.owners == ()


def test_c8_passes_on_a_well_configured_repo(repo, run_cli):
    assert run_cli("check", "--all", "--no-base") == 0


def test_c8_fails_without_codeowners(repo, run_cli):
    (repo / ".github" / "CODEOWNERS").unlink()
    assert run_cli("check", "--all", "--no-base") == 1


def test_c8_fails_when_a_category_is_unowned(repo, run_cli):
    (repo / ".github" / "CODEOWNERS").write_text("/pyproject.toml @org/eng\n")
    assert run_cli("check", "--all", "--no-base") == 1


def test_c8_catches_silent_reassignment_by_a_later_rule(repo, run_cli):
    """A trailing *.md rule takes the sidecars away from their owner."""
    (repo / ".github" / "CODEOWNERS").write_text(
        "/keystones/          @org/eng\n"
        "/pyproject.toml      @org/eng\n"
        "/.github/CODEOWNERS  @org/eng\n"
        "*.md                 @org/docs\n"
    )
    assert run_cli("check", "--all", "--no-base") == 1


def test_c8_fails_when_the_gate_config_is_unowned(repo, run_cli):
    (repo / ".github" / "CODEOWNERS").write_text(
        "/keystones/          @org/eng\n/.github/CODEOWNERS  @org/eng\n"
    )
    assert run_cli("check", "--all", "--no-base") == 1
