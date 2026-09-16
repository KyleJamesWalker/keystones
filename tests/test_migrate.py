"""Hasher and grammar version bumps, proved rather than rubber-stamped."""

from pathlib import Path

import pytest

PAYOUT = "billing/payout.py"
SIDECAR = ("keystones", "finance", "r.md")


@pytest.fixture
def migrated_repo(repo, run_cli):
    run_cli(
        "add",
        f"{PAYOUT}::compute_payout",
        "--id",
        "r",
        "--category",
        "finance",
        "-m",
        "GAAP rounding.",
    )
    return repo


def sidecar_path(repo: Path) -> Path:
    return repo.joinpath(*SIDECAR)


def set_hasher(repo: Path, value: str) -> None:
    path = sidecar_path(repo)
    text = path.read_text()
    line = next(ln for ln in text.splitlines() if ln.startswith("hasher = "))
    path.write_text(text.replace(line, f'hasher = "{value}"'))


def test_nothing_to_do_on_a_current_repo(migrated_repo, run_cli):
    assert run_cli("migrate") == 0


def test_a_hasher_bump_warns_rather_than_reporting_drift(migrated_repo, run_cli):
    set_hasher(migrated_repo, "keystones-ast/0")
    assert run_cli("check", "--all", "--no-base") == 0, (
        "C13 is a warning, not a failure"
    )


def test_unchanged_code_migrates_with_no_note_and_no_review(migrated_repo, run_cli):
    set_hasher(migrated_repo, "keystones-ast/0")
    assert run_cli("migrate") == 0
    text = sidecar_path(migrated_repo).read_text()
    assert 'hasher = "keystones-ast/1"' in text
    assert run_cli("check", "--all", "--no-base") == 0


def test_the_proof_comes_from_the_stored_source_not_the_stored_hash(
    migrated_repo, run_cli
):
    """Corrupt the stored hash: migration must still reach the right answer."""
    path = sidecar_path(migrated_repo)
    original = path.read_text()
    true_hash = next(ln for ln in original.splitlines() if ln.startswith("semantic = "))
    path.write_text(original.replace(true_hash, 'semantic = "sha256:deadbeef"'))
    set_hasher(migrated_repo, "keystones-ast/0")

    assert run_cli("migrate") == 0
    assert true_hash in path.read_text(), "recomputed from the stored canonical source"
    assert run_cli("check", "--all", "--no-base") == 0


def test_code_that_also_changed_is_left_for_the_normal_gate(migrated_repo, run_cli):
    set_hasher(migrated_repo, "keystones-ast/0")
    source = migrated_repo / PAYOUT
    source.write_text(source.read_text().replace("ROUND_HALF_UP", "ROUND_HALF_EVEN"))

    assert run_cli("migrate") == 1, (
        "a real change must not be laundered through migrate"
    )
    assert 'hasher = "keystones-ast/0"' in sidecar_path(migrated_repo).read_text()


def test_check_mode_writes_nothing(migrated_repo, run_cli):
    set_hasher(migrated_repo, "keystones-ast/0")
    before = sidecar_path(migrated_repo).read_text()
    assert run_cli("migrate", "--check") == 0
    assert sidecar_path(migrated_repo).read_text() == before


def test_check_mode_fails_when_something_is_blocked(migrated_repo, run_cli):
    set_hasher(migrated_repo, "keystones-ast/0")
    source = migrated_repo / PAYOUT
    source.write_text(source.read_text().replace("ROUND_HALF_UP", "ROUND_HALF_EVEN"))
    assert run_cli("migrate", "--check") == 1


def test_an_entry_whose_marker_is_gone_is_reported_not_migrated(migrated_repo, run_cli):
    set_hasher(migrated_repo, "keystones-ast/0")
    source = migrated_repo / PAYOUT
    source.write_text(source.read_text().replace("# keystone(finance): r\n", ""))
    assert run_cli("migrate") == 1
