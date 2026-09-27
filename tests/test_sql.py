"""The builtin SQL spec, and the keyword folding it needs to be usable."""

import pytest

from keystones.adapters import treesitter as ts

pytestmark = pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")

VIEW = """-- keystone(finance): net-revenue
create or replace view net_revenue as
with adjusted as (
    select order_id, amount * 0.97 as net from orders
)
select * from adjusted;
"""


def semantic(src: str, path: str = "rev.sql") -> str:
    marker = ts.markers(path, src)[0]
    return ts.hashes(src, ts.resolve(src, marker))[0]


def test_sql_routes_to_tree_sitter():
    assert ts.spec_for("rev.sql") is not None
    assert ts.spec_for("rev.sql").language == "sql"


def test_a_view_is_addressable_by_name():
    target = ts.resolve(VIEW, ts.markers("rev.sql", VIEW)[0])
    assert target.qualname == "net_revenue"


def test_a_cte_is_addressable_inside_its_view():
    root = ts._parse(ts.spec_for("rev.sql"), VIEW).root_node
    names = [name for name, _ in ts._definitions(root, ts.spec_for("rev.sql"))]
    assert "net_revenue.adjusted" in names


def test_keyword_case_does_not_move_the_hash():
    """sqlfluff's capitalisation rules would otherwise trip every SQL keystone."""
    shouted = VIEW.replace("create or replace view", "CREATE OR REPLACE VIEW")
    shouted = shouted.replace("select", "SELECT").replace("from", "FROM")
    shouted = shouted.replace("with", "WITH").replace(" as ", " AS ")
    assert semantic(shouted) == semantic(VIEW)


def test_identifier_case_does_move_the_hash():
    """Quoted identifiers are case-sensitive, so folding them would be wrong."""
    assert semantic(VIEW.replace("net_revenue", "NET_REVENUE")) != semantic(VIEW)


def test_reindenting_does_not_move_the_hash():
    assert semantic(VIEW.replace("    select", "        select")) == semantic(VIEW)


def test_changing_the_literal_does_move_the_hash():
    assert semantic(VIEW.replace("0.97", "0.95")) != semantic(VIEW)


def test_a_block_comment_is_not_hashed_as_code():
    """SQL calls the block form `marginalia`, which the default set omits."""
    commented = VIEW.replace(
        "select * from adjusted;",
        "/* finance signed this off */\nselect * from adjusted;",
    )
    assert semantic(commented) == semantic(VIEW)


def test_a_marker_on_sql_is_written_with_a_double_dash():
    """`#` is valid MySQL and a syntax error in Postgres, Snowflake and BigQuery."""
    assert ts.comment_prefix("rev.sql") == "--"


DBT_MODEL = """{{ config(materialized='incremental') }}

-- keystone(finance): rev
select order_id, amount * 0.97 as net from {{ ref('orders') }}
"""


def test_a_file_the_grammar_cannot_parse_is_refused_not_hashed():
    """dbt Jinja shreds into ERROR nodes; hashing that recovery tree is a trap.

    Error recovery is the least stable part of a grammar's output, so the hash
    would move on a pack bump with nobody having touched the model.
    """
    with pytest.raises(ts.ParseError, match="does not parse"):
        ts.markers("model.sql", DBT_MODEL)


ORDERS = """with
  raw as (
    select 1 as x
  ),
  -- keystone(finance): net
  net as (
    select x * 0.97 as x from raw
  )
select * from net
"""

LEADING_COMMA = """with
  raw as (
    select 1 as x
  )
  -- keystone(finance): net
  , net as (
    select x * 0.97 as x from raw
  )
select * from net
"""

NOT_LAST = """with
  -- keystone(finance): net
  net as (
    select 0.97 as x
  ),
  raw as (
    select x from net
  )
select * from raw
"""


@pytest.mark.parametrize(
    "src", [ORDERS, LEADING_COMMA, NOT_LAST], ids=["last", "leading-comma", "not-last"]
)
def test_a_cte_below_the_with_line_rehashes_from_its_stored_source(src):
    """The stored slice is `net as (...)`, which is not SQL on its own."""
    target = ts.resolve(src, ts.markers("orders.sql", src)[0])
    stored = ts.canonical_source(src, target)
    assert ts.hash_stored_source(stored, str(target)) == ts.hashes(src, target)[0]


def test_a_freshly_added_cte_passes_every_check(repo, run_cli, capsys):
    (repo / "orders.sql").write_text(
        ORDERS.replace("  -- keystone(finance): net\n", "")
    )
    assert run_cli("add", "orders.sql::net", "--id", "net", "-m", "Net rate.") == 0
    assert run_cli("check", "--all", "--no-base") == 0, capsys.readouterr().err


def test_add_refuses_a_cte_that_shares_its_first_line(repo, run_cli, capsys):
    """A marker above the line would attach to the first CTE on it, not this one."""
    src = "with a as (select 1 as x), b as (select x from a) select * from b\n"
    (repo / "orders.sql").write_text(src)
    assert run_cli("add", "orders.sql::b", "--id", "b", "-m", "Why.") == 1
    assert "own line" in capsys.readouterr().err
    assert (repo / "orders.sql").read_text() == src
    assert not (repo / "keystones" / "default" / "b.md").exists()
