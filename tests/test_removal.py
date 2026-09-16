"""C9. The check a self-consistency checker structurally cannot make."""

import subprocess
from pathlib import Path

import pytest

PAYOUT = "billing/payout.py"


def commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def based(repo, run_cli):
    """A repo with one committed keystone, plus the ref it was committed at."""
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
    return commit(repo, "add keystone")


def strip_marker(repo: Path) -> None:
    path = repo / PAYOUT
    path.write_text(
        path.read_text().replace("# keystone(finance): payout-rounding\n", "")
    )


def test_baseline_passes(repo, run_cli, based):
    assert run_cli("check", "--all", "--base", based) == 0


def test_coordinated_removal_fails_even_though_c1_and_c2_pass(repo, run_cli, based):
    """Delete the marker and its entry together: the repo is self-consistent."""
    strip_marker(repo)
    (repo / "keystones" / "finance" / "payout-rounding.md").unlink()
    run_cli("index")

    assert run_cli("check", "--all", "--no-base") == 0, (
        "self-consistency checks are satisfied"
    )
    assert run_cli("check", "--all", "--base", based) == 1, "C9 must still catch it"


def test_marker_only_removal_fails(repo, run_cli, based):
    strip_marker(repo)
    assert run_cli("check", "--all", "--base", based) == 1


def test_entry_only_removal_fails(repo, run_cli, based):
    (repo / "keystones" / "finance" / "payout-rounding.md").unlink()
    run_cli("index")
    assert run_cli("check", "--all", "--base", based) == 1


def test_widening_exclude_to_cover_a_keystone_fails(repo, run_cli, based):
    """Removing coverage without touching the keystone is still a removal."""
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            'categories = ["default", "finance"]',
            'categories = ["default", "finance"]\nexclude = ["billing/**"]',
        )
    )
    assert run_cli("check", "--all", "--base", based) == 1


def test_deregistering_a_category_fails(repo, run_cli, based):
    strip_marker(repo)
    (repo / "keystones" / "finance" / "payout-rounding.md").unlink()
    run_cli("index")
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            'categories = ["default", "finance"]', 'categories = ["default"]'
        )
    )
    assert run_cli("check", "--all", "--base", based) == 1


def test_adding_a_keystone_is_not_a_removal(repo, run_cli, based):
    run_cli(
        "add",
        f"{PAYOUT}::compute_payout".replace("compute_payout", "compute_payout"),
        "--id",
        "second",
        "--category",
        "default",
        "-m",
        "another.",
    ) if False else None
    (repo / "billing" / "fees.py").write_text("def compute_fee(x):\n    return x * 2\n")
    run_cli(
        "add", "billing/fees.py::compute_fee", "--id", "fee-calc", "-m", "Fee basis."
    )
    assert run_cli("check", "--all", "--base", based) == 0


def test_renaming_a_keystone_reads_as_a_removal(repo, run_cli, based):
    """An id is the stable key, so changing it drops the old keystone."""
    path = repo / PAYOUT
    path.write_text(path.read_text().replace("payout-rounding", "payout-rounding-v2"))
    old = repo / "keystones" / "finance" / "payout-rounding.md"
    old.rename(old.with_name("payout-rounding-v2.md"))
    run_cli("index")
    assert run_cli("check", "--all", "--base", based) == 1


def test_missing_base_ref_skips_c9_rather_than_guessing(repo, run_cli, based):
    strip_marker(repo)
    (repo / "keystones" / "finance" / "payout-rounding.md").unlink()
    run_cli("index")
    assert run_cli("check", "--all", "--no-base") == 0
