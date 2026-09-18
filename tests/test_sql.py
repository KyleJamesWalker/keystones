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
