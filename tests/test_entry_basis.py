"""An entry is checked on the basis it recorded, not the one its extension implies."""

import pytest

from keystones.adapters import treesitter as ts

pytestmark = pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")

PLUGIN = """
[[tool.keystones.language]]
builtin = "sql"
extensions = [".sql"]
preprocessor = "fake_pre:preprocess"
"""

BUILTIN_ONLY = """
[[tool.keystones.language]]
builtin = "sql"
extensions = [".sql"]
"""

REFUSED_WITH_TEXT_REGION = """<<config(materialized='table')>>
select
-- keystone:start(finance, hash=text): rev-rec
    amount * 0.97 as net
-- keystone:end
from REFUSE
"""

PLAIN_VIEW = """-- keystone(finance): net_revenue
create or replace view net_revenue as
select order_id, amount * 0.97 as net from orders;
"""


def configure(repo, table):
    pyproject = repo / "pyproject.toml"
    base = pyproject.read_text().split("\n[[tool.keystones.language]]")[0]
    pyproject.write_text(base.rstrip() + "\n" + table)


def sidecar(repo, name):
    return repo / "keystones" / "finance" / f"{name}.md"


# --- a text keystone in a parsed extension -----------------------------------


def test_c5_catches_a_hand_edit_to_a_text_keystone_in_a_parsed_file(
    repo, run_cli, capsys
):
    """The documented way out of a refusal must still be tamper-proof."""
    configure(repo, PLUGIN)
    (repo / "rev.sql").write_text(REFUSED_WITH_TEXT_REGION)
    assert run_cli("add", "--id", "rev-rec", "-m", "GAAP rev rec.") == 0
    path = sidecar(repo, "rev-rec")
    path.write_text(path.read_text().replace("0.97", "0.50"))
    assert run_cli("check", "--all", "--no-base") == 1
    assert "[C5]" in capsys.readouterr().err


def test_migrate_leaves_a_text_keystone_on_text(repo, run_cli, capsys):
    configure(repo, PLUGIN)
    (repo / "rev.sql").write_text(REFUSED_WITH_TEXT_REGION)
    assert run_cli("add", "--id", "rev-rec", "-m", "GAAP rev rec.") == 0
    assert run_cli("migrate", "--check") == 0
    assert "already on this install's hasher" in capsys.readouterr().out


# --- enabling a plugin on an existing repo -----------------------------------


def test_migrate_moves_the_recorded_kind_along_with_the_hasher(repo, run_cli):
    """After `migrate`, `check` is clean; C14 is for hand edits, not for this."""
    configure(repo, BUILTIN_ONLY)
    (repo / "rev.sql").write_text(PLAIN_VIEW)
    assert run_cli("add", "--id", "net_revenue", "-m", "GAAP rev rec.") == 0
    configure(repo, PLUGIN)
    assert run_cli("check", "--all", "--no-base") == 1
    assert run_cli("migrate") == 0
    assert 'hash = "fake"' in sidecar(repo, "net_revenue").read_text()
    assert run_cli("check", "--all", "--no-base") == 0


def test_c14_points_at_migrate_when_the_table_changed(repo, run_cli, capsys):
    configure(repo, BUILTIN_ONLY)
    (repo / "rev.sql").write_text(PLAIN_VIEW)
    run_cli("add", "--id", "net_revenue", "-m", "GAAP rev rec.")
    configure(repo, PLUGIN)
    run_cli("check", "--all", "--no-base")
    assert "keystones migrate" in capsys.readouterr().err
