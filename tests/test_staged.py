"""`keystones check <paths>`, which the staged hook runs on every text file."""

import subprocess

import pytest

PAYOUT = "billing/payout.py"
SIDECAR = "keystones/finance/payout-rounding.md"

NET = "a: 1\n# keystone:start: cidrs\nb: 2\n# keystone:end\n"


@pytest.fixture
def gated(repo, run_cli):
    assert (
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
        == 0
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "gate"], cwd=repo, check=True)
    return repo


@pytest.mark.parametrize("name", ["README.md", "notes.txt", "helpers.py"])
def test_a_file_with_nothing_to_check_is_silent(gated, run_cli, capsys, name):
    (gated / name).write_text("nothing to see here\n")
    assert run_cli("check", name) == 0
    assert capsys.readouterr() == ("", "")


def test_a_sidecar_only_commit_checks_its_keystone(gated, run_cli, capsys):
    """Hand-editing the stored source is caught without staging the target."""
    path = gated / SIDECAR
    path.write_text(path.read_text().replace("'0.01'", "'0.1'"))
    assert run_cli("check", SIDECAR) == 1
    assert "[C5] 'payout-rounding' stored source does not match" in (
        capsys.readouterr().err
    )


def test_a_sidecar_whose_hash_was_edited_fails_on_its_target(gated, run_cli, capsys):
    path = gated / SIDECAR
    raw = path.read_text()
    semantic = raw.split('semantic = "', 1)[1].split('"', 1)[0]
    path.write_text(raw.replace(semantic, "sha256:" + "0" * 64))
    assert run_cli("check", SIDECAR) == 1
    err = capsys.readouterr().err
    assert f"{PAYOUT}:4: error: [C3]" in err


def test_an_untouched_sidecar_passes(gated, run_cli, capsys):
    assert run_cli("check", SIDECAR) == 0
    assert "1 keystone(s) verified" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["net.yaml", "views.lkml"])
def test_a_marker_in_an_unparsed_type_is_checked(repo, run_cli, capsys, name):
    (repo / name).write_text(NET)
    assert run_cli("add", "--id", "cidrs", "-m", "Peering.") == 0
    (repo / name).write_text(NET.replace("b: 2", "b: 3"))
    capsys.readouterr()
    assert run_cli("check", name) == 1
    assert f"{name}:2: error: [C3] keystone 'cidrs' changed" in (
        capsys.readouterr().err
    )


def test_the_staged_path_reads_only_what_it_is_given(gated, run_cli, monkeypatch):
    """Sidecars are read for the reverse lookup; the source tree never is."""
    from keystones import discovery

    def everything(*args, **kwargs):
        raise AssertionError("the staged path walked the whole repo")

    monkeypatch.setattr(discovery, "_tracked_files", everything)
    (gated / "notes.txt").write_text("hi\n")
    assert run_cli("check", PAYOUT, SIDECAR, "notes.txt") == 0


def test_a_malformed_sidecar_the_target_needs_is_a_finding(gated, run_cli, capsys):
    (gated / SIDECAR).write_text("# payout-rounding\n\nno toml block\n")
    assert run_cli("check", PAYOUT) == 1
    assert "error: [sidecar]" in capsys.readouterr().err


# --- what the staged hook checks beyond the file --------------------------------


def test_a_staged_keystones_twin_is_checked(repo, run_cli, capsys):
    (repo / "copy.py").write_text(
        "from decimal import ROUND_HALF_UP, Decimal\n\n\n"
        "def compute_payout(amount: Decimal) -> Decimal:\n"
        '    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)\n'
    )
    assert (
        run_cli(
            "add",
            f"{PAYOUT}::compute_payout",
            "--id",
            "p",
            "-m",
            "w",
            "--twin",
            "copy.py::compute_payout",
        )
        == 0
    )
    (repo / "copy.py").write_text((repo / "copy.py").read_text().replace("0.01", "0.1"))
    capsys.readouterr()
    assert run_cli("check", PAYOUT) == 1
    assert "[C17]" in capsys.readouterr().err


def test_a_staged_keystones_dependency_is_checked(repo, run_cli, capsys):
    (repo / "rates.py").write_text("BASE = 0.07\n")
    assert (
        run_cli(
            "add",
            f"{PAYOUT}::compute_payout",
            "--id",
            "p",
            "-m",
            "w",
            "--depends",
            "rates.py::BASE",
        )
        == 0
    )
    (repo / "rates.py").write_text("BASE = 0.09\n")
    capsys.readouterr()
    assert run_cli("check", PAYOUT) == 1
    assert "[C11]" in capsys.readouterr().err


def test_the_staged_summary_names_what_it_did_not_run(gated, run_cli, capsys):
    assert run_cli("check", PAYOUT) == 0
    out = capsys.readouterr().out
    assert "1 keystone(s) verified" in out
    assert "check --all" in out and "C9" in out


def test_a_staged_file_that_is_someone_elses_twin_is_checked(repo, run_cli, capsys):
    (repo / "copy.py").write_text(
        "from decimal import ROUND_HALF_UP, Decimal\n\n\n"
        "def compute_payout(amount: Decimal) -> Decimal:\n"
        '    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)\n'
    )
    args = ("add", f"{PAYOUT}::compute_payout", "--id", "p", "-m", "w")
    assert run_cli(*args, "--twin", "copy.py::compute_payout") == 0
    (repo / "copy.py").write_text((repo / "copy.py").read_text().replace("0.01", "0.1"))
    capsys.readouterr()
    assert run_cli("check", "copy.py") == 1
    assert "[C17]" in capsys.readouterr().err


def test_a_staged_file_that_is_someone_elses_dependency_is_checked(
    repo, run_cli, capsys
):
    (repo / "rates.py").write_text("BASE = 0.07\n")
    args = ("add", f"{PAYOUT}::compute_payout", "--id", "p", "-m", "w")
    assert run_cli(*args, "--depends", "rates.py::BASE") == 0
    (repo / "rates.py").write_text("BASE = 0.09\n")
    capsys.readouterr()
    assert run_cli("check", "rates.py") == 1
    assert "[C11]" in capsys.readouterr().err


def test_a_path_that_does_not_exist_is_an_error(gated, run_cli, capsys):
    """A typo in a hook's file list must not pass as clean."""
    assert run_cli("check", "nope.py") == 1
    assert "nope.py" in capsys.readouterr().err


def test_a_clean_twin_only_path_says_what_it_verified(repo, run_cli, capsys):
    (repo / "copy.py").write_text(
        "from decimal import ROUND_HALF_UP, Decimal\n\n\n"
        "def compute_payout(amount: Decimal) -> Decimal:\n"
        '    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)\n'
    )
    args = ("add", f"{PAYOUT}::compute_payout", "--id", "p", "-m", "w")
    assert run_cli(*args, "--twin", "copy.py::compute_payout") == 0
    capsys.readouterr()
    assert run_cli("check", "copy.py") == 0
    assert "verified through twins or depends" in capsys.readouterr().out
