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
    from keystones import discovery, sidecar

    def everything(*args, **kwargs):
        raise AssertionError("the staged path walked the whole repo")

    monkeypatch.setattr(discovery, "_tracked_files", everything)
    monkeypatch.setattr(sidecar, "load_all", everything)
    (gated / "notes.txt").write_text("hi\n")
    assert run_cli("check", PAYOUT, SIDECAR, "notes.txt") == 0


def test_a_malformed_sidecar_the_target_needs_is_a_finding(gated, run_cli, capsys):
    (gated / SIDECAR).write_text("# payout-rounding\n\nno toml block\n")
    assert run_cli("check", PAYOUT) == 1
    assert "error: [sidecar]" in capsys.readouterr().err
