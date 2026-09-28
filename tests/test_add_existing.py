"""`add <target>` on a definition that already carries a marker."""

PAYOUT = "billing/payout.py"
SIDECAR = "keystones/finance/payout-rounding.md"


def marked(repo, line):
    path = repo / PAYOUT
    path.write_text(
        path.read_text().replace("def compute_payout", f"{line}\ndef compute_payout")
    )


def test_a_marker_with_the_same_id_is_adopted_not_doubled(repo, run_cli):
    marked(repo, "# keystone(finance): payout-rounding")
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
    assert (repo / PAYOUT).read_text().count("keystone") == 1
    assert (repo / SIDECAR).exists()
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_marker_with_another_id_is_refused_and_named(repo, run_cli, capsys):
    marked(repo, "# keystone(finance): rounding")
    before = (repo / PAYOUT).read_text()
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
        == 1
    )
    err = capsys.readouterr().err
    assert "already carries keystone 'rounding'" in err
    assert "keystones add --id finance/rounding" in err
    assert (repo / PAYOUT).read_text() == before
    assert not (repo / SIDECAR).exists()


def test_a_whole_file_marker_is_adopted_too(repo, run_cli):
    path = repo / PAYOUT
    path.write_text("# keystone(file): payout\n" + path.read_text())
    assert run_cli("add", PAYOUT, "--id", "payout", "-m", "All of it.") == 0
    assert path.read_text().count("keystone") == 1
    assert run_cli("check", "--all", "--no-base") == 0
