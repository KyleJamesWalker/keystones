"""Ids are unique within a category, as `<root>/<category>/<id>.md` implies."""

import subprocess

import pytest

A = "def f():\n    return 1\n"
B = "def g():\n    return 2\n"


def edit(path, old, new):
    path.write_text(path.read_text().replace(old, new))


@pytest.fixture
def twin(repo, run_cli):
    """The same id in two categories, each on its own function."""
    (repo / "a.py").write_text(A)
    (repo / "b.py").write_text(B)
    assert run_cli("add", "a.py::f", "--id", "rate", "-m", "A.") == 0
    assert (
        run_cli("add", "b.py::g", "--id", "rate", "--category", "finance", "-m", "B.")
        == 0
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "twin"], cwd=repo, check=True)
    return repo


def test_one_id_in_two_categories_is_two_keystones(twin, run_cli, capsys):
    assert run_cli("check", "--all", "--no-base") == 0, capsys.readouterr().err


def test_drift_is_reported_against_the_right_entry(twin, run_cli, capsys):
    edit(twin / "b.py", "2", "3")
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert err.count("[C3]") == 1
    assert "b.py:1: error: [C3]" in err
    assert "keystones fix --id finance/rate" in err, "a bare id would be ambiguous"


def test_an_id_reused_within_one_category_is_still_c6(repo, run_cli, capsys):
    (repo / "a.py").write_text("# keystone: rate\n" + A)
    (repo / "b.py").write_text("# keystone: rate\n" + B)
    assert run_cli("check", "--all", "--no-base") == 1
    assert "id 'rate' reused in category 'default'" in capsys.readouterr().err


def test_fix_refuses_a_bare_id_that_names_two_keystones(twin, run_cli, capsys):
    edit(twin / "a.py", "1", "5")
    edit(twin / "b.py", "2", "3")
    assert run_cli("fix", "--id", "rate", "-m", "Both.") == 1
    assert "say which with --id default/rate" in capsys.readouterr().err


def test_fix_takes_the_category_qualified_form(twin, run_cli, capsys):
    edit(twin / "a.py", "1", "5")
    edit(twin / "b.py", "2", "3")
    assert run_cli("fix", "--id", "finance/rate", "-m", "Rate moved.") == 0
    capsys.readouterr()
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "a.py:1: error: [C3]" in err
    assert "b.py" not in err


def test_adopting_a_bare_id_marked_in_two_categories_asks_which(repo, run_cli, capsys):
    (repo / "a.py").write_text("# keystone: rate\n" + A)
    (repo / "b.py").write_text("# keystone(finance): rate\n" + B)
    assert run_cli("add", "--id", "rate", "-m", "Why.") == 1
    assert "say which with --id default/rate" in capsys.readouterr().err
    assert run_cli("add", "--id", "finance/rate", "-m", "Why.") == 0
    assert (repo / "keystones" / "finance" / "rate.md").exists()
    assert not (repo / "keystones" / "default" / "rate.md").exists()


def test_removing_one_of_two_twins_is_c9_for_its_category(twin, run_cli, capsys):
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=twin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    edit(twin / "b.py", "# keystone(finance): rate\n", "")
    (twin / "keystones" / "finance" / "rate.md").unlink()
    run_cli("index")
    assert run_cli("check", "--all", "--base", base) == 1
    err = capsys.readouterr().err
    assert "owner of category 'finance'" in err
    assert "owner of category 'default'" not in err
