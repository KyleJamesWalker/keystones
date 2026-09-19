"""An adapter that cannot hash a target reports it; it never crashes the run."""

import pytest

from keystones.adapters import python as python_adapter
from keystones.adapters.base import ResolutionError

PAYOUT = "billing/payout.py"


@pytest.fixture
def refusing_hashes(monkeypatch):
    """A plugin may accept a file and refuse one target's lines, e.g. when a
    boundary cuts through a masked span. Simulated on the Python adapter."""

    def refuse(src, target):
        raise ResolutionError("a masked span crosses the boundary of this target")

    monkeypatch.setattr(python_adapter, "hashes", refuse)


def test_add_reports_the_failure_and_leaves_the_file_alone(
    repo, run_cli, capsys, refusing_hashes
):
    before = (repo / PAYOUT).read_text()
    assert run_cli("add", f"{PAYOUT}::compute_payout", "--id", "r", "-m", "x") == 1
    err = capsys.readouterr().err
    assert "crosses the boundary" in err and "Traceback" not in err
    assert (repo / PAYOUT).read_text() == before
    assert not (repo / "keystones" / "default" / "r.md").exists()


def test_check_reports_the_failure_as_a_finding(repo, run_cli, capsys, monkeypatch):
    run_cli("add", f"{PAYOUT}::compute_payout", "--id", "r", "-m", "x")
    capsys.readouterr()

    def refuse(src, target):
        raise ResolutionError("a masked span crosses the boundary of this target")

    monkeypatch.setattr(python_adapter, "hashes", refuse)
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "'r' cannot be hashed" in err and "crosses the boundary" in err


def test_fix_reports_the_failure_and_continues(repo, run_cli, capsys, monkeypatch):
    run_cli("add", f"{PAYOUT}::compute_payout", "--id", "r", "-m", "x")
    capsys.readouterr()

    def refuse(src, target):
        raise ResolutionError("a masked span crosses the boundary of this target")

    monkeypatch.setattr(python_adapter, "hashes", refuse)
    assert run_cli("fix", "-m", "why") == 1
    assert "crosses the boundary" in capsys.readouterr().err
