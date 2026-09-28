"""Files over `max_scan_bytes` are never read, and never silently pass."""

import pytest

from keystones.discovery import MAX_SCAN_BYTES

BIG = "big.py"


def write_big(repo, marker: bool = True):
    head = "# keystone: f\n" if marker else ""
    body = head + "def f():\n    return 1\n\n" + ("x = 1\n" * (MAX_SCAN_BYTES // 6))
    (repo / BIG).write_text(body)


def test_add_refuses_a_file_over_the_cap(repo, run_cli, capsys):
    write_big(repo, marker=False)
    assert run_cli("add", f"{BIG}::f", "--id", "f", "-m", "why.") == 1
    err = capsys.readouterr().err
    assert "max_scan_bytes" in err
    assert "keystone" not in (repo / BIG).read_text()


def test_an_entry_whose_target_grew_past_the_cap_is_reported(repo, run_cli, capsys):
    (repo / BIG).write_text("# keystone: f\ndef f():\n    return 1\n")
    assert run_cli("add", "--id", "f", "-m", "why.") == 0
    write_big(repo)
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[size]" in err and "max_scan_bytes" in err
    assert "[C2]" not in err, "the marker is there; the file is just unread"
    capsys.readouterr()
    assert run_cli("check", BIG) == 0, "staged mode cannot know what is in it"
    assert "warning: [size]" in capsys.readouterr().err


def test_the_cap_is_configurable(repo, run_cli):
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + "max_scan_bytes = 10000000\n")
    write_big(repo, marker=False)
    assert run_cli("add", f"{BIG}::f", "--id", "f", "-m", "why.") == 0
    assert run_cli("check", "--all", "--no-base") == 0


@pytest.mark.parametrize("value", ['"big"', "0"])
def test_the_cap_must_be_a_positive_integer(repo, run_cli, value):
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + f"max_scan_bytes = {value}\n")
    assert run_cli("check", "--all", "--no-base") == 2


# --- follow-ups ----------------------------------------------------------------


def test_adopting_a_marker_in_an_over_cap_file_names_the_file(repo, run_cli, capsys):
    write_big(repo)
    assert run_cli("add", "--id", "f", "-m", "why.") == 1
    err = capsys.readouterr().err
    assert "max_scan_bytes" in err and BIG in err


def test_size_names_every_keystone_in_the_file(repo, run_cli, capsys):
    (repo / BIG).write_text(
        "# keystone: f\ndef f():\n    return 1\n\n\n"
        "# keystone: g\ndef g():\n    return 2\n"
    )
    assert run_cli("add", "--id", "f", "-m", "why.") == 0
    assert run_cli("add", "--id", "g", "-m", "why.") == 0
    path = repo / BIG
    path.write_text(path.read_text() + "x = 1\n" * (MAX_SCAN_BYTES // 6))
    capsys.readouterr()
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert err.count("[size]") == 1
    assert "'f'" in err and "'g'" in err


def test_a_dependency_in_an_over_cap_file_is_refused_and_reported(
    repo, run_cli, capsys
):
    (repo / "rates.py").write_text("BASE = 0.07\n")
    assert (
        run_cli(
            "add",
            "billing/payout.py::compute_payout",
            "--id",
            "p",
            "-m",
            "w",
            "--depends",
            "rates.py::BASE",
        )
        == 0
    )
    (repo / "rates.py").write_text("BASE = 0.07\n" + "x = 1\n" * (MAX_SCAN_BYTES // 6))
    capsys.readouterr()
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C11]" in err and "max_scan_bytes" in err
    (repo / "big2.py").write_text("Y = 1\n" + "x = 1\n" * (MAX_SCAN_BYTES // 6))
    (repo / "h.py").write_text("def h():\n    return 1\n")
    status = run_cli(
        "add", "h.py::h", "--id", "q", "-m", "w", "--depends", "big2.py::Y"
    )
    assert status == 1
    assert "max_scan_bytes" in capsys.readouterr().err
