"""Languages defined in pyproject.toml rather than in the SPECS table."""

import subprocess
from pathlib import Path

import pytest

from keystones import adapters
from keystones.adapters import fallback
from keystones.adapters import treesitter as ts
from keystones.config import ConfigError, load

pytestmark = pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")

# A dialect the package does not ship, so this exercises the config path
# rather than shadowing the builtin `sql` spec.
SQL_TABLE = """
[[tool.keystones.language]]
grammar = "sql_bigquery"
extensions = [".bqsql"]
definitions = ["create_table_statement", "cte"]
name_fields = []
label_children = ["identifier"]
line_comment = "--"
"""

VIEW_SRC = """-- keystone(finance): net-revenue
create or replace view net_revenue as
with adjusted as (
    select order_id, amount * 0.97 as net from orders
)
select * from adjusted;
"""


def configure(repo: Path, table: str = SQL_TABLE) -> Path:
    """Replace any table written by an earlier call, rather than stacking one."""
    pyproject = repo / "pyproject.toml"
    base = pyproject.read_text().split("\n[[tool.keystones.language]]")[0]
    pyproject.write_text(base.rstrip() + "\n" + table)
    return repo


def test_a_configured_language_resolves_to_a_node(repo, run_cli):
    """The whole point: granularity without shipping a spec in this package."""
    configure(repo)
    (repo / "rev.bqsql").write_text(VIEW_SRC)
    assert run_cli("add", "--id", "net-revenue", "-m", "Rev rec rule.") == 0
    sidecar = (repo / "keystones" / "finance" / "net-revenue.md").read_text()
    assert 'target = "rev.bqsql::net_revenue"' in sidecar


def test_a_configured_language_gates_on_change(repo, run_cli):
    configure(repo)
    path = repo / "rev.bqsql"
    path.write_text(VIEW_SRC)
    run_cli("add", "--id", "net-revenue", "-m", "Rev rec rule.")
    assert run_cli("check", "--all", "--no-base") == 0
    path.write_text(path.read_text().replace("0.97", "0.95"))
    assert run_cli("check", "--all", "--no-base") == 1


def test_reindenting_a_configured_language_does_not_trip_it(repo, run_cli):
    """Node granularity has to buy reformat immunity or it buys nothing."""
    configure(repo)
    path = repo / "rev.bqsql"
    path.write_text(VIEW_SRC)
    run_cli("add", "--id", "net-revenue", "-m", "Rev rec rule.")
    path.write_text(VIEW_SRC.replace("with adjusted as (\n", "with adjusted as (\n\n"))
    assert run_cli("check", "--all", "--no-base") == 0


def test_the_hasher_id_names_the_configured_grammar(repo, run_cli):
    configure(repo)
    (repo / "rev.bqsql").write_text(VIEW_SRC)
    run_cli("add", "--id", "net-revenue", "-m", "Rev rec rule.")
    sidecar = (repo / "keystones" / "finance" / "net-revenue.md").read_text()
    assert "keystones-ts/2+sql_bigquery@" in sidecar


def test_a_table_edit_that_cannot_move_the_hash_is_silent(repo, run_cli):
    """Narrowing `definitions` changes what is addressable, not what is hashed.

    The id moves because the spec did, then the hash reproduces and nothing is
    said. Churning here would make the table uneditable in practice.
    """
    configure(repo)
    (repo / "rev.bqsql").write_text(VIEW_SRC)
    run_cli("add", "--id", "net-revenue", "-m", "Rev rec rule.")
    configure(
        repo,
        SQL_TABLE.replace(
            '"create_table_statement", "cte"', '"create_table_statement"'
        ),
    )
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_table_edit_that_moves_the_hash_is_c13_not_drift(repo, run_cli, capsys):
    """What PR 1 bought. Nobody touched rev.bqsql, so C3 would be a lie.

    Climbing to `source_file` widens the hash to the whole file, so this edit
    moves the basis for real.
    """
    configure(repo)
    (repo / "rev.bqsql").write_text(VIEW_SRC)
    run_cli("add", "--id", "net-revenue", "-m", "Rev rec rule.")
    configure(repo, SQL_TABLE + 'wrappers = ["source_file"]\n')
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C13]" in err and "[C3]" not in err


def test_an_unknown_key_is_refused(repo):
    """A typo would otherwise build a spec that matches nothing, quietly."""
    configure(repo, SQL_TABLE.replace("definitions =", "definitons ="))
    with pytest.raises(ConfigError, match="definitons"):
        load(repo)


def test_an_extension_owned_by_a_builtin_is_refused(repo):
    configure(repo, SQL_TABLE.replace('[".bqsql"]', '[".ts"]'))
    with pytest.raises(ConfigError, match=r"\.ts"):
        load(repo)


def test_two_tables_cannot_claim_one_extension(repo):
    configure(repo, SQL_TABLE + SQL_TABLE.replace('"sql_bigquery"', '"sql"'))
    with pytest.raises(ConfigError, match=r"\.bqsql"):
        load(repo)


def test_an_extension_must_be_a_suffix(repo):
    configure(repo, SQL_TABLE.replace('[".bqsql"]', '["bqsql"]'))
    with pytest.raises(ConfigError, match="must start with"):
        load(repo)


def test_a_table_without_definitions_is_refused(repo):
    configure(
        repo, SQL_TABLE.replace('definitions = ["create_table_statement", "cte"]\n', "")
    )
    with pytest.raises(ConfigError, match="definitions"):
        load(repo)


def test_a_grammar_the_pack_does_not_ship_is_reported_clearly(repo, run_cli, capsys):
    """`plsql` is a plausible guess and the pack does not carry it."""
    configure(repo, SQL_TABLE.replace('grammar = "sql_bigquery"', 'grammar = "plsql"'))
    (repo / "rev.bqsql").write_text(VIEW_SRC)
    assert run_cli("check", "--all", "--no-base") == 1
    out = capsys.readouterr()
    assert "plsql" in (out.err + out.out)


def test_config_languages_do_not_leak_between_repos(repo, run_cli, tmp_path):
    """Specs are global state; a second repo must not inherit the first's."""
    configure(repo)
    (repo / "rev.bqsql").write_text(VIEW_SRC)
    run_cli("add", "--id", "net-revenue", "-m", "Rev rec rule.")

    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=other, check=True)
    (other / "pyproject.toml").write_text('[tool.keystones]\nroot = "keystones"\n')
    adapters.configure(load(other))
    assert adapters.for_path("x.bqsql") is fallback
