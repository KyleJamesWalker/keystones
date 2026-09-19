"""Saying it once in pyproject.toml instead of on every marker."""

import pytest

from keystones import adapters
from keystones.adapters import fallback
from keystones.adapters import treesitter as ts
from keystones.config import ConfigError, load

pytestmark = pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")

DBT = """{{ config(materialized='incremental') }}
select
    order_id,
-- keystone:start(finance): rev-rec
    amount * 0.97 as net
-- keystone:end
from {{ ref('orders') }}
"""

BQ_VIEW = """-- keystone(finance): net_revenue
create or replace view net_revenue as
select order_id, amount * 0.97 as net from orders;
"""


def configure(repo, table):
    pyproject = repo / "pyproject.toml"
    base = pyproject.read_text().split("\n[[tool.keystones.language]]")[0]
    pyproject.write_text(base.rstrip() + "\n" + table)


TEXT_DEFAULT = """
[[tool.keystones.language]]
extensions = [".sql"]
hash = "text"
"""

BIGQUERY = """
[[tool.keystones.language]]
builtin = "sql_bigquery"
extensions = [".sql"]
"""


# --- a repo-wide default basis ----------------------------------------------


def test_a_configured_default_means_no_qualifier_is_needed(repo, run_cli):
    """The whole ask: an all-dbt repo says it once, not on every marker."""
    configure(repo, TEXT_DEFAULT)
    (repo / "rev.sql").write_text(DBT)
    assert run_cli("add", "--id", "rev-rec", "-m", "GAAP rev rec.") == 0
    assert run_cli("check", "--all", "--no-base") == 0
    sidecar = (repo / "keystones" / "finance" / "rev-rec.md").read_text()
    assert 'hash = "text"' in sidecar


def test_the_default_still_gates_a_change(repo, run_cli):
    configure(repo, TEXT_DEFAULT)
    path = repo / "rev.sql"
    path.write_text(DBT)
    run_cli("add", "--id", "rev-rec", "-m", "GAAP rev rec.")
    path.write_text(path.read_text().replace("0.97", "0.95"))
    assert run_cli("check", "--all", "--no-base") == 1


def test_a_marker_qualifier_still_wins_over_the_default(repo, run_cli):
    """Precedence: marker, then config, then auto-detect."""
    configure(repo, TEXT_DEFAULT)
    (repo / "v.sql").write_text(BQ_VIEW.replace("(finance)", "(finance, hash=sql)"))
    assert run_cli("add", "--id", "net_revenue", "-m", "Rev rec.") == 0
    sidecar = (repo / "keystones" / "finance" / "net_revenue.md").read_text()
    assert 'hash = "sql"' in sidecar


def test_add_stops_refusing_once_the_repo_has_said_what_it_wants(repo, run_cli):
    configure(repo, TEXT_DEFAULT)
    (repo / "rev.sql").write_text(DBT)
    assert run_cli("add", "--id", "rev-rec", "-m", "GAAP rev rec.") == 0


REGION_SQL = """create or replace view net_revenue as
-- keystone:start(finance): rev-rec
select order_id, amount * 0.97 as net from orders;
-- keystone:end
"""


def test_removing_the_default_is_caught_not_silent(repo, run_cli, capsys):
    """A config edit re-gates every entry under it; C14 is what notices.

    A region hashes identically either way - tree-sitter delegates regions to
    the text adapter - so the recorded kind is the only thing that differs.
    """
    configure(repo, TEXT_DEFAULT)
    (repo / "v.sql").write_text(REGION_SQL)
    assert run_cli("add", "--id", "rev-rec", "-m", "Rev rec.") == 0
    configure(repo, "")
    assert run_cli("check", "--all", "--no-base") == 1
    assert "[C14]" in capsys.readouterr().err


def test_a_point_marker_under_a_text_default_says_why(repo, run_cli, capsys):
    """The config path needs the same clear error the qualifier path gets."""
    configure(repo, TEXT_DEFAULT)
    (repo / "v.sql").write_text(BQ_VIEW)
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "no nodes to attach to" in err and "tool.keystones.language" in err


# --- overriding a builtin ----------------------------------------------------


def test_config_may_take_an_extension_from_a_builtin(repo, run_cli):
    """A deliberate, reviewed override is not the silent rebinding we refuse."""
    configure(repo, BIGQUERY)
    (repo / "v.sql").write_text(BQ_VIEW)
    assert run_cli("add", "--id", "net_revenue", "-m", "Rev rec.") == 0
    sidecar = (repo / "keystones" / "finance" / "net_revenue.md").read_text()
    assert 'hash = "sql_bigquery"' in sidecar


def test_two_config_tables_still_cannot_collide(repo):
    configure(repo, BIGQUERY + TEXT_DEFAULT)
    with pytest.raises(ConfigError, match=r"\.sql"):
        load(repo)


def test_a_builtin_selector_needs_no_node_types(repo):
    configure(repo, BIGQUERY)
    assert load(repo).languages[0].builtin == "sql_bigquery"


def test_an_unknown_builtin_names_the_ones_that_exist(repo):
    configure(repo, BIGQUERY.replace("sql_bigquery", "cobol"))
    with pytest.raises(ConfigError, match="sql"):
        load(repo)


def test_builtin_and_grammar_together_are_refused(repo):
    configure(repo, BIGQUERY + 'grammar = "sql"\ndefinitions = ["cte"]\n')
    with pytest.raises(ConfigError, match="builtin"):
        load(repo)


def test_a_table_with_no_parser_may_only_default_to_text(repo):
    configure(repo, TEXT_DEFAULT.replace('hash = "text"', 'hash = "sql"'))
    with pytest.raises(ConfigError, match="hash"):
        load(repo)


# --- bigquery ships as a builtin ---------------------------------------------


def test_bigquery_is_available_as_a_builtin():
    assert "sql_bigquery" in {spec.language for spec in ts.SPECS}


def test_the_default_kind_is_reported_first(repo):
    configure(repo, TEXT_DEFAULT)
    adapters.configure(load(repo))
    assert adapters.kinds_for("x.sql")[0] == "text"
    assert adapters.for_kind("x.sql", "text") is fallback
