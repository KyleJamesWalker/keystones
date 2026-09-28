"""C17: copies of a keystoned definition must keep hashing the same."""

import pytest

PAYOUT = "billing/payout.py"
COPY = "reports/payout_copy.py"
SIDECAR = "keystones/finance/payout-rounding.md"

COPY_SRC = """from decimal import ROUND_HALF_UP, Decimal


def compute_payout(amount: Decimal) -> Decimal:
    # a copy kept for the reporting job
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
"""


@pytest.fixture
def twinned(repo, run_cli):
    (repo / "reports").mkdir()
    (repo / COPY).write_text(COPY_SRC)
    assert (
        run_cli(
            "add",
            f"{PAYOUT}::compute_payout",
            "--id",
            "payout-rounding",
            "--category",
            "finance",
            "-m",
            "GAAP rounding, and the reporting job carries a copy.",
            "--twin",
            f"{COPY}::compute_payout",
        )
        == 0
    )
    return repo


def edit(repo, rel, old, new):
    path = repo / rel
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_twins_are_recorded(twinned):
    assert f'twins = ["{COPY}::compute_payout"]' in (twinned / SIDECAR).read_text()


def test_clean_twins_pass(twinned, run_cli):
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_twin_drifting_alone_is_c17(twinned, run_cli, capsys):
    edit(twinned, COPY, "ROUND_HALF_UP)", "ROUND_HALF_EVEN)")
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert f"{COPY}:4: error: [C17] {COPY}::compute_payout" in err
    assert "no longer matches keystone 'payout-rounding'" in err
    assert "[C3]" not in err


def test_the_keystone_changing_without_its_twin_is_c3_then_c17(
    twinned, run_cli, capsys
):
    """A twin is held to the reviewed code, so the copy falls behind only
    once the keystone's own change has been reviewed."""
    edit(twinned, PAYOUT, "ROUND_HALF_UP)", "ROUND_HALF_EVEN)")
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C3]" in err and "[C17]" not in err
    assert run_cli("fix", "-m", "Banker's rounding.") == 0
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C17]" in err and "[C3]" not in err


def test_both_changing_together_needs_one_review(twinned, run_cli, capsys):
    edit(twinned, PAYOUT, "ROUND_HALF_UP)", "ROUND_HALF_EVEN)")
    edit(twinned, COPY, "ROUND_HALF_UP)", "ROUND_HALF_EVEN)")
    assert run_cli("fix", "-m", "Banker's rounding, both copies.") == 0
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_twin_comment_edit_is_not_drift(twinned, run_cli):
    edit(twinned, COPY, "# a copy kept for the reporting job", "# see payout.py")
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_twin_that_disappears_is_c17(twinned, run_cli, capsys):
    (twinned / COPY).write_text("def other():\n    return 1\n")
    assert run_cli("check", "--all", "--no-base") == 1
    assert "[C17]" in capsys.readouterr().err


def test_add_refuses_a_twin_that_does_not_match(repo, run_cli, capsys):
    (repo / "reports").mkdir()
    (repo / COPY).write_text(COPY_SRC.replace("0.01", "0.1"))
    status = run_cli(
        "add",
        f"{PAYOUT}::compute_payout",
        "--id",
        "payout-rounding",
        "--category",
        "finance",
        "-m",
        "why.",
        "--twin",
        f"{COPY}::compute_payout",
    )
    assert status == 1
    err = capsys.readouterr().err
    assert "does not match" in err
    assert not (repo / SIDECAR).exists()
    assert "keystone" not in (repo / PAYOUT).read_text(), "no marker left behind"


def test_a_whole_file_twin(repo, run_cli):
    (repo / "a.txt").write_text("# keystone(file): rule\nvalue = 1\n")
    (repo / "b.txt").write_text("value = 1\n")
    assert run_cli("add", "--id", "rule", "-m", "why.", "--twin", "b.txt") == 0
    assert run_cli("check", "--all", "--no-base") == 0
    (repo / "b.txt").write_text("value = 2\n")
    assert run_cli("check", "--all", "--no-base") == 1


def test_a_twin_with_no_recorded_basis_uses_the_symbol_adapter(repo):
    """An entry written before a basis was recorded has hash = ''. Its twins
    must still resolve through an adapter that can name symbols."""
    from keystones import twins
    from keystones.adapters import python as python_adapter

    (repo / "copy.py").write_text("def compute_payout():\n    return 1\n")
    adapter, target, _ = twins.resolve(repo, "copy.py::compute_payout", None)
    assert adapter is python_adapter
    assert target.qualname == "compute_payout"


def test_a_yaml_twin_with_no_recorded_basis_is_found(repo):
    import pytest

    from keystones import twins
    from keystones.adapters import structured

    if not structured.available():
        pytest.skip("needs PyYAML")
    (repo / "v.yaml").write_text("spec:\n  replicas: 3\n")
    adapter, target, _ = twins.resolve(repo, "v.yaml::spec.replicas", None)
    assert adapter is structured
    assert target.qualname == "spec.replicas"
