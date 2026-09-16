"""The gate end to end: add, drift, refuse, fix, pass."""

from pathlib import Path

PAYOUT = "billing/payout.py"


def add_keystone(run_cli) -> int:
    return run_cli(
        "add",
        f"{PAYOUT}::compute_payout",
        "--id",
        "payout-rounding",
        "--category",
        "finance",
        "-m",
        "GAAP rounding, see finance sign-off.",
    )


def edit(repo: Path, old: str, new: str) -> None:
    path = repo / PAYOUT
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_add_writes_marker_and_sidecar(repo, run_cli):
    assert add_keystone(run_cli) == 0
    source = (repo / PAYOUT).read_text()
    assert "# keystone(finance): payout-rounding" in source
    sidecar_file = repo / "keystones" / "finance" / "payout-rounding.md"
    assert sidecar_file.exists()
    assert "GAAP rounding" in sidecar_file.read_text()
    assert (repo / "keystones" / "INDEX.md").exists()


def test_clean_repo_passes(repo, run_cli):
    add_keystone(run_cli)
    assert run_cli("check", "--all") == 0


def test_reformatting_does_not_trip_the_gate(repo, run_cli):
    add_keystone(run_cli)
    edit(
        repo,
        "def compute_payout(amount: Decimal) -> Decimal:",
        "def compute_payout(\n    amount: Decimal,\n) -> Decimal:",
    )
    assert run_cli("check", "--all") == 0


def test_semantic_change_fails_the_gate(repo, run_cli):
    add_keystone(run_cli)
    edit(repo, "ROUND_HALF_UP", "ROUND_HALF_EVEN")
    assert run_cli("check", "--all") == 1


def test_fix_refuses_semantic_drift_without_a_note(repo, run_cli):
    add_keystone(run_cli)
    edit(repo, "ROUND_HALF_UP", "ROUND_HALF_EVEN")
    assert run_cli("fix") == 1


def test_fix_with_a_note_restores_the_gate_and_records_history(repo, run_cli):
    add_keystone(run_cli)
    edit(repo, "ROUND_HALF_UP", "ROUND_HALF_EVEN")
    assert run_cli("fix", "-m", "moved to banker rounding per policy review.") == 0
    assert run_cli("check", "--all") == 0
    sidecar_text = (repo / "keystones" / "finance" / "payout-rounding.md").read_text()
    assert "banker rounding" in sidecar_text
    assert "ROUND_HALF_EVEN" in sidecar_text, "stored source must track the change"


def test_comment_only_edit_trips_c4(repo, run_cli):
    add_keystone(run_cli)
    edit(repo, "# do not reorder", "# reordering is fine now")
    assert run_cli("check", "--all") == 1


def test_deleting_the_marker_alone_trips_c2(repo, run_cli):
    add_keystone(run_cli)
    edit(repo, "# keystone(finance): payout-rounding\n", "")
    assert run_cli("check", "--all") == 1


def test_orphan_marker_trips_c1(repo, run_cli):
    path = repo / PAYOUT
    path.write_text("# keystone: no-entry\n" + path.read_text())
    assert run_cli("check", "--all") == 1


def test_unknown_category_trips_c7(repo, run_cli):
    add_keystone(run_cli)
    edit(repo, "# keystone(finance):", "# keystone(nope):")
    assert run_cli("check", "--all") == 1


def test_hand_edited_hash_trips_c5(repo, run_cli):
    add_keystone(run_cli)
    sidecar_file = repo / "keystones" / "finance" / "payout-rounding.md"
    sidecar_file.write_text(
        sidecar_file.read_text().replace('semantic = "sha256:', 'semantic = "sha256:0')
    )
    assert run_cli("check", "--all") == 1


def test_stale_index_trips_c10(repo, run_cli):
    add_keystone(run_cli)
    (repo / "keystones" / "INDEX.md").write_text("# Keystones\n\nstale\n")
    assert run_cli("check", "--all") == 1


def test_scoped_run_skips_whole_repo_checks(repo, run_cli):
    """A staged-files run cannot see orphan entries, so it must not claim to."""
    add_keystone(run_cli)
    edit(repo, "# keystone(finance): payout-rounding\n", "")
    assert run_cli("check", PAYOUT) == 0
    assert run_cli("check", "--all") == 1


def test_warn_only_reports_without_failing(repo, run_cli):
    add_keystone(run_cli)
    edit(repo, "ROUND_HALF_UP", "ROUND_HALF_EVEN")
    assert run_cli("check", PAYOUT, "--warn-only") == 0


def test_list_and_index_commands(repo, run_cli):
    add_keystone(run_cli)
    assert run_cli("list") == 0
    assert run_cli("list", "--category", "finance") == 0
    assert run_cli("index") == 0
