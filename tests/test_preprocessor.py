"""The preprocessor hook: a plugin masks what the grammar cannot read."""

import pytest

from keystones import adapters
from keystones.adapters import treesitter as ts
from keystones.adapters.base import ResolutionError
from keystones.config import ConfigError, load

pytestmark = pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")

TABLE = """
[[tool.keystones.language]]
builtin = "sql"
extensions = [".sql"]
preprocessor = "fake_pre:preprocess"
"""

TEMPLATED = """<<directive here>>
-- keystone(finance): net_revenue
create or replace view net_revenue as
select order_id, amount * 0.97 as net from <<source('orders')>>;
"""


def configure(repo, table=TABLE):
    pyproject = repo / "pyproject.toml"
    base = pyproject.read_text().split("\n[[tool.keystones.language]]")[0]
    pyproject.write_text(base.rstrip() + "\n" + table)


def setup(repo, run_cli, table=TABLE, src=TEMPLATED):
    configure(repo, table)
    (repo / "rev.sql").write_text(src)
    return run_cli


# --- it makes an unreadable file readable ------------------------------------


def test_a_masked_file_resolves_to_a_node(repo, run_cli):
    """Without the mask this file does not parse at all."""
    setup(repo, run_cli)
    assert run_cli("add", "--id", "net_revenue", "-m", "Rev rec.") == 0
    sidecar = (repo / "keystones" / "finance" / "net_revenue.md").read_text()
    assert 'target = "rev.sql::net_revenue"' in sidecar


def test_the_kind_is_the_preprocessor_not_the_grammar(repo, run_cli):
    """What a reviewer needs to know is that a plugin is in play."""
    setup(repo, run_cli)
    run_cli("add", "--id", "net_revenue", "-m", "Rev rec.")
    assert (
        'hash = "fake"'
        in (repo / "keystones" / "finance" / "net_revenue.md").read_text()
    )


def test_the_plugin_is_visible_in_the_hasher(repo, run_cli):
    setup(repo, run_cli)
    run_cli("add", "--id", "net_revenue", "-m", "Rev rec.")
    sidecar = (repo / "keystones" / "finance" / "net_revenue.md").read_text()
    assert "+fake/1" in sidecar


def test_sql_changes_still_gate(repo, run_cli):
    setup(repo, run_cli)
    run_cli("add", "--id", "net_revenue", "-m", "Rev rec.")
    path = repo / "rev.sql"
    path.write_text(path.read_text().replace("0.97", "0.95"))
    assert run_cli("check", "--all", "--no-base") == 1


def test_masked_content_still_gates(repo, run_cli):
    """The whole reason `extra` exists: a masked span is not an escape hatch."""
    setup(repo, run_cli)
    run_cli("add", "--id", "net_revenue", "-m", "Rev rec.")
    path = repo / "rev.sql"
    path.write_text(path.read_text().replace("source('orders')", "source('payments')"))
    assert run_cli("check", "--all", "--no-base") == 1


def test_reformatting_inside_a_masked_span_does_not_gate(repo, run_cli):
    setup(repo, run_cli)
    run_cli("add", "--id", "net_revenue", "-m", "Rev rec.")
    path = repo / "rev.sql"
    path.write_text(
        path.read_text().replace("<<source('orders')>>", "<<  source('orders')  >>")
    )
    assert run_cli("check", "--all", "--no-base") == 0


# --- refusal -----------------------------------------------------------------


def test_a_refusal_says_why_and_what_to_do(repo, run_cli, capsys):
    """19.6% of real dbt models are the reason this path exists."""
    setup(repo, run_cli, src=TEMPLATED.replace("directive here", "REFUSE"))
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "cannot mask" in err and "hash=text" in err


def test_a_refusal_does_not_silently_fall_back_to_text(repo, run_cli):
    setup(repo, run_cli, src=TEMPLATED.replace("directive here", "REFUSE"))
    assert run_cli("add", "--id", "net_revenue", "-m", "Rev rec.") == 1


# --- the contract is enforced, not documented --------------------------------


def test_a_plugin_that_loses_a_line_is_caught(repo, run_cli, capsys):
    """Targets are line numbers; a shifted file corrupts every one of them."""
    setup(repo, run_cli, table=TABLE.replace(":preprocess", ":drops_a_line"))
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "line" in err and "drops_a_line" in err


# --- identity ----------------------------------------------------------------


def test_the_preprocessor_version_reaches_the_spec_digest(repo, monkeypatch):
    import fake_pre

    configure(repo)
    adapters.configure(load(repo))
    before = ts.hasher_id_for_path("rev.sql")
    monkeypatch.setattr(fake_pre, "KEYSTONES_PREPROCESSOR_VERSION", "2")
    adapters.configure(load(repo))
    assert ts.hasher_id_for_path("rev.sql") != before


# --- config validation -------------------------------------------------------


def test_an_unimportable_preprocessor_is_refused(repo):
    configure(repo, TABLE.replace("fake_pre:preprocess", "nope_missing:preprocess"))
    with pytest.raises(ConfigError, match="nope_missing"):
        load(repo)


def test_a_preprocessor_without_the_attribute_is_refused(repo):
    configure(repo, TABLE.replace("fake_pre:preprocess", "fake_pre:absent"))
    with pytest.raises(ConfigError, match="absent"):
        load(repo)


def test_a_malformed_import_path_is_refused(repo):
    configure(repo, TABLE.replace("fake_pre:preprocess", "fake_pre.preprocess"))
    with pytest.raises(ConfigError, match="module:attribute"):
        load(repo)


def test_a_preprocessor_that_is_not_callable_is_refused(repo):
    configure(repo, TABLE.replace("fake_pre:preprocess", "fake_pre:SPAN"))
    with pytest.raises(ConfigError, match="not callable"):
        load(repo)


def test_a_preprocessor_without_a_name_is_refused(repo):
    configure(repo, TABLE.replace("fake_pre:preprocess", "nameless_pre:preprocess"))
    with pytest.raises(ConfigError, match="KEYSTONES_PREPROCESSOR_NAME"):
        load(repo)


def test_a_preprocessor_needs_a_parser_to_feed(repo):
    configure(
        repo,
        "\n[[tool.keystones.language]]\n"
        'extensions = [".sql"]\n'
        'preprocessor = "fake_pre:preprocess"\n',
    )
    with pytest.raises(ConfigError, match="grammar"):
        load(repo)


def test_a_masked_span_outside_the_target_does_not_trip_it(repo, run_cli):
    """`extra` is scoped to the target's lines, like everything else."""
    setup(repo, run_cli)
    run_cli("add", "--id", "net_revenue", "-m", "Rev rec.")
    path = repo / "rev.sql"
    path.write_text(path.read_text() + "\nselect * from <<ref('elsewhere')>>;\n")
    assert run_cli("check", "--all", "--no-base") == 0


def test_masking_is_shared_between_adapters():
    """Both adapters must hold a plugin to the same contract, from one place."""
    from keystones.adapters import masking

    assert masking.preprocessed(None, "a\nb") == ("a\nb", "")
    assert issubclass(masking.ContractError, ResolutionError)
    assert issubclass(masking.PreprocessorRefused, ResolutionError)
