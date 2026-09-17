"""Regressions for the findings from the external review of the stack."""

import subprocess

import pytest

from keystones.adapters import treesitter as ts
from keystones.codeowners import owners_for, parse
from keystones.markers import is_marker, looks_like_a_marker

PAYOUT = "billing/payout.py"

DECOY = """from decimal import ROUND_HALF_UP, Decimal

if False:
    # keystone(finance): payout-rounding
    def compute_payout(amount: Decimal) -> Decimal:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compute_payout(amount: Decimal) -> Decimal:
    return amount * 0
"""


@pytest.fixture
def guarded(repo, run_cli):
    run_cli(
        "add",
        f"{PAYOUT}::compute_payout",
        "--id",
        "payout-rounding",
        "--category",
        "finance",
        "-m",
        "GAAP rounding.",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "keystone"], cwd=repo, check=True)
    return repo


# --- the target-identity hole ---------------------------------------------


def test_a_hash_identical_decoy_in_the_same_file_is_caught(guarded, run_cli, capsys):
    """The live function is rewritten; a dead copy keeps the hash intact."""
    (guarded / PAYOUT).write_text(DECOY)
    assert run_cli("check", "--all", "--no-base") == 1
    assert "[C6]" in capsys.readouterr().err


def test_moving_the_marker_to_another_file_is_caught(guarded, run_cli, capsys):
    (guarded / "billing" / "_legacy.py").write_text((guarded / PAYOUT).read_text())
    (guarded / PAYOUT).write_text(
        "from decimal import Decimal\n\n\n"
        "def compute_payout(amount: Decimal) -> Decimal:\n    return amount * 0\n"
    )
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C3]" in err and "no longer covers" in err


def test_a_cross_file_move_needs_a_note(guarded, run_cli):
    source = (guarded / PAYOUT).read_text()
    (guarded / "billing" / "moved.py").write_text(source)
    (guarded / PAYOUT).write_text("")
    assert run_cli("fix") == 1, "a move between files is never note-free"
    assert run_cli("fix", "-m", "relocated to moved.py.") == 0


# --- marker handling -------------------------------------------------------


def test_ignore_file_covers_python_point_markers(repo, run_cli):
    (repo / "fixture.py").write_text(
        "# keystones: ignore-file\n# keystone: example\ndef f():\n    return 1\n"
    )
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_comment_merely_mentioning_keystones_is_hashed(guarded, run_cli):
    """Only real markers are excluded from the text hash."""
    path = guarded / PAYOUT
    path.write_text(
        path.read_text().replace(
            "    return amount",
            "    # keystone rounding rule, do not touch\n    return amount",
        )
    )
    assert run_cli("check", "--all", "--no-base") == 1


def test_is_marker_is_stricter_than_the_prefilter():
    assert looks_like_a_marker("# talk about keystone rules")
    assert not is_marker("# talk about keystone rules")
    assert is_marker("# keystone: real-one")


def test_marker_category_must_match_the_sidecar_directory(guarded, run_cli, capsys):
    path = guarded / PAYOUT
    path.write_text(path.read_text().replace("keystone(finance):", "keystone:"))
    assert run_cli("check", "--all", "--no-base") == 1
    assert "[C7]" in capsys.readouterr().err


# --- CODEOWNERS ------------------------------------------------------------


def test_a_globbed_last_segment_is_not_recursive():
    """GitHub matches docs/* against files directly in docs/ only."""
    rules = parse("docs/* @org/docs\n")
    assert owners_for(rules, "docs/intro.md") is not None
    assert owners_for(rules, "docs/guide/intro.md") is None


def test_a_directory_rule_is_still_recursive():
    rules = parse("/keystones/ @org/eng\n")
    assert owners_for(rules, "keystones/finance/x.md") is not None


def test_c8_fails_when_a_star_rule_only_looks_like_coverage(repo, run_cli):
    (repo / ".github" / "CODEOWNERS").write_text(
        "/keystones/* @org/eng\n"
        "/pyproject.toml @org/eng\n"
        "/.github/CODEOWNERS @org/eng\n"
    )
    assert run_cli("check", "--all", "--no-base") == 1


# --- add ------------------------------------------------------------------


@pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")
def test_add_uses_the_language_comment_leader(repo, run_cli):
    (repo / "app.ts").write_text("export function f(): number {\n  return 1;\n}\n")
    assert run_cli("add", "app.ts::f", "--id", "ts-one", "-m", "why.") == 0
    assert "// keystone: ts-one" in (repo / "app.ts").read_text()


def test_add_rejects_an_unusable_id(repo, run_cli):
    assert (
        run_cli("add", f"{PAYOUT}::compute_payout", "--id", "pay out", "-m", "why.")
        == 1
    )
    assert not list((repo / "keystones").rglob("pay*.md"))


def test_add_validates_depends_before_touching_the_source(repo, run_cli):
    before = (repo / PAYOUT).read_text()
    assert (
        run_cli(
            "add",
            f"{PAYOUT}::compute_payout",
            "--id",
            "k",
            "-m",
            "why.",
            "--depends",
            "billing/nope.py::gone",
        )
        == 1
    )
    assert (repo / PAYOUT).read_text() == before, "no marker left behind"


def test_file_scope_marker_goes_below_a_shebang(repo, run_cli):
    script = repo / "tool.py"
    script.write_text("#!/usr/bin/env python3\nX = 1\n")
    assert run_cli("add", "tool.py", "--id", "script", "-m", "why.") == 0
    assert script.read_text().splitlines()[0] == "#!/usr/bin/env python3"


# --- sidecar robustness ----------------------------------------------------


def test_a_payload_containing_a_fence_round_trips(repo, run_cli):
    doc = repo / "runbook.md"
    doc.write_text(
        "# Runbook\n\n<!-- keystone:start(finance): steps -->\n"
        "Run:\n\n```bash\nmake deploy\n```\n\n<!-- keystone:end -->\n"
    )
    assert run_cli("add", "--id", "steps", "-m", "Deploy contract.") == 0
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_payload_containing_a_heading_round_trips(repo, run_cli):
    doc = repo / "notes.md"
    doc.write_text(
        "<!-- keystone:start(finance): notes -->\n## Heading inside\ntext\n"
        "<!-- keystone:end -->\n"
    )
    assert run_cli("add", "--id", "notes", "-m", "why.") == 0
    assert run_cli("check", "--all", "--no-base") == 0


# --- excludes --------------------------------------------------------------


def test_default_excludes_apply_at_the_repo_root(repo, run_cli):
    (repo / "generated").mkdir()
    (repo / "generated" / "g.py").write_text(
        "# keystone: gen\ndef f():\n    return 1\n"
    )
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_configured_double_star_exclude_covers_the_root(repo, run_cli):
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + 'exclude = ["**/build/**"]\n')
    (repo / "build").mkdir()
    (repo / "build" / "b.py").write_text("# keystone: built\ndef f():\n    return 1\n")
    assert run_cli("check", "--all", "--no-base") == 0


# --- CLI -------------------------------------------------------------------


def test_no_base_actually_skips_the_removal_check(guarded, run_cli, monkeypatch):
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    (guarded / PAYOUT).write_text("")
    assert run_cli("check", "--all", "--no-base") == 1
    monkeypatch.delenv("GITHUB_BASE_REF")


# --- tree-sitter normalisation --------------------------------------------


def one(src, path="app.ts"):
    marker = ts.markers(path, src)[0]
    return ts.hashes(src, ts.resolve(src, marker))[0]


M = "// keystone: k\n"


@pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")
@pytest.mark.parametrize(
    ("label", "a", "b"),
    [
        ("quote style", "function f() { return 'a'; }", 'function f() { return "a"; }'),
        (
            "number form",
            "function f() { return 1.50; }",
            "function f() { return 1.5; }",
        ),
        ("parens", "function f() { return (a); }", "function f() { return a; }"),
        ("arrow parens", "const g = x => x;", "const g = (x) => x;"),
        (
            "trailing comma",
            "function f(a, b,) { return a; }",
            "function f(a, b) { return a; }",
        ),
    ],
)
def test_prettier_normalisations_do_not_trip_the_gate(label, a, b):
    assert one(M + a) == one(M + b), label


@pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")
@pytest.mark.parametrize(
    ("label", "a", "b"),
    [
        ("const vs let", "const g = () => 1;", "let g = () => 1;"),
        (
            "export dropped",
            "export function f() { return 1; }",
            "function f() { return 1; }",
        ),
        (
            "array hole",
            "function f() { return [a,,b]; }",
            "function f() { return [a,b]; }",
        ),
        (
            "operator",
            "function f(a,b) { return a + b; }",
            "function f(a,b) { return a - b; }",
        ),
    ],
)
def test_meaningful_differences_are_still_detected(label, a, b):
    assert one(M + a) != one(M + b), label


@pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")
def test_a_typescript_method_keystone_does_not_crash_check(repo, run_cli):
    (repo / "app.ts").write_text(
        "export class Ledger {\n  // keystone(finance): post\n"
        "  post(entry: string): void {\n    return;\n  }\n}\n"
    )
    assert run_cli("add", "--id", "post", "-m", "Posting contract.") == 0
    assert run_cli("check", "--all", "--no-base") == 0


# --- doctor ----------------------------------------------------------------


def test_doctor_reports_an_unusable_token_rather_than_passing(tmp_path, monkeypatch):
    from keystones import doctor
    from keystones.models import Severity

    monkeypatch.setattr(doctor, "_token", lambda: "t")
    monkeypatch.setattr(doctor, "slug", lambda root: ("o", "r"))
    monkeypatch.setattr(
        doctor,
        "_get",
        lambda path, token: (
            (200, {"errors": []})
            if path.endswith("/codeowners/errors")
            else (
                (401, {}) if "/branches/" in path else (200, {"default_branch": "main"})
            )
        ),
    )
    findings = doctor.run(tmp_path)
    assert [f for f in findings if f.severity is Severity.ERROR]
