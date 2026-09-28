"""The built-in preprocessor for SQL with Python format placeholders."""

import pytest

from keystones import placeholders
from keystones.adapters import treesitter as ts
from keystones.preprocess import Refused

needs_extra = pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")

TABLE = """
[[tool.keystones.language]]
builtin = "{builtin}"
extensions = [".sql"]
preprocessor = "keystones.placeholders:preprocess"
"""

ORDERS = """with
  -- keystone(finance): net
  net as (
    select order_id, amount * {rate} as net
    from {project}.sales.orders
    where day = '{run_date}'
  )
select * from net
"""


def configure(repo, builtin="sql"):
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + TABLE.format(builtin=builtin))


@pytest.mark.parametrize(
    "placeholder", ["{}", "{0}", "{name}", "{obj.attr}", "{rate!r}", "{rate:.2f}"]
)
def test_each_placeholder_form_is_masked(placeholder):
    masked, extra = placeholders.preprocess(f"select {placeholder} from t")
    assert "{" not in masked
    assert extra == placeholder


def test_the_mask_keeps_every_line_and_derives_from_content():
    src = "select {a}\nfrom {b}\nwhere {a} = 1\n"
    masked, _ = placeholders.preprocess(src)
    assert masked.count("\n") == src.count("\n")
    first, _, third = masked.splitlines()
    assert first.split()[1] == third.split()[1], "same text, same mask"


@pytest.mark.parametrize("src", ["select '{{literal}}'", "select {{ ref('x') }}"])
def test_doubled_braces_are_refused(src):
    with pytest.raises(Refused, match="doubled braces"):
        placeholders.preprocess(src)


@needs_extra
@pytest.mark.parametrize("builtin", ["sql", "sql_bigquery"])
def test_a_templated_model_is_gated_node_by_node(repo, run_cli, capsys, builtin):
    configure(repo, builtin)
    (repo / "orders.sql").write_text(ORDERS)
    assert run_cli("add", "--id", "net", "-m", "Net rate.") == 0
    sidecar = (repo / "keystones" / "finance" / "net.md").read_text()
    assert 'target = "orders.sql::net"' in sidecar
    assert 'hash = "placeholders"' in sidecar
    assert run_cli("check", "--all", "--no-base") == 0, capsys.readouterr().err


@needs_extra
@pytest.mark.parametrize(
    ("old", "new", "status"),
    [
        ("{rate}", "{fee}", 1),
        ("{project}", "{other}", 1),
        ("amount * {rate}", "amount  *  {rate}", 0),
        ("select * from net", "select order_id from net", 0),
    ],
    ids=["placeholder", "table", "reformat", "outside"],
)
def test_a_placeholder_is_part_of_the_hash(repo, run_cli, capsys, old, new, status):
    configure(repo)
    (repo / "orders.sql").write_text(ORDERS)
    assert run_cli("add", "--id", "net", "-m", "Net rate.") == 0
    (repo / "orders.sql").write_text(ORDERS.replace(old, new))
    capsys.readouterr()
    assert run_cli("check", "--all", "--no-base") == status, capsys.readouterr().err
