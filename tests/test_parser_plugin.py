"""The parser hook: a plugin supplies the tree, keystones does the rest."""

from dataclasses import FrozenInstanceError

import pytest

from keystones import adapters
from keystones.config import ConfigError, load
from keystones.parser import Definition, Unparseable


def test_the_contract_module_is_the_only_import_a_plugin_needs():
    assert Definition("orders", 2, 9).qualname == "orders"
    assert issubclass(Unparseable, Exception)
    with pytest.raises(FrozenInstanceError):
        Definition("x", 1, 2).start = 5


PARSER_TABLE = """
[[tool.keystones.language]]
extensions = [".blk"]
parser = "fake_parser:parser"
"""

WITH_OPTIONS = """
[[tool.keystones.language]]
extensions = [".blk"]
parser = { plugin = "fake_parser:parser", fold = "keep" }
"""


def configure(repo, table):
    pyproject = repo / "pyproject.toml"
    base = pyproject.read_text().split("\n[[tool.keystones.language]]")[0]
    pyproject.write_text(base.rstrip() + "\n" + table)


# --- the table ---------------------------------------------------------------


def test_a_parser_table_resolves_the_factory(repo):
    configure(repo, PARSER_TABLE)
    lang = load(repo).languages[0]
    assert lang.parser.name == "blk"
    assert lang.parser.identity == "fakeblk@1.0/lower"
    assert lang.grammar is None and lang.builtin is None


def test_options_reach_the_factory(repo):
    configure(repo, WITH_OPTIONS)
    assert load(repo).languages[0].parser.identity == "fakeblk@1.0/keep"


def test_a_factory_that_rejects_an_option_is_a_config_error(repo):
    configure(repo, WITH_OPTIONS.replace('"keep"', '"shout"'))
    with pytest.raises(ConfigError, match="fold"):
        load(repo)


def test_parser_and_grammar_are_alternatives(repo):
    configure(
        repo, PARSER_TABLE.rstrip() + '\ngrammar = "sql"\ndefinitions = ["cte"]\n'
    )
    with pytest.raises(ConfigError, match="alternative"):
        load(repo)


def test_parser_and_builtin_are_alternatives(repo):
    configure(repo, PARSER_TABLE.rstrip() + '\nbuiltin = "sql"\n')
    with pytest.raises(ConfigError, match="alternative"):
        load(repo)


def test_grammar_shaping_keys_do_not_apply_to_a_parser(repo):
    configure(repo, PARSER_TABLE.rstrip() + '\ndefinitions = ["cte"]\n')
    with pytest.raises(ConfigError, match="definitions"):
        load(repo)


def test_a_parser_factory_must_be_callable(repo):
    configure(repo, PARSER_TABLE.replace("fake_parser:parser", "fake_parser:VERSION"))
    with pytest.raises(ConfigError, match="callable"):
        load(repo)


def test_the_default_hash_may_name_the_parser(repo):
    configure(repo, PARSER_TABLE.rstrip() + '\nhash = "blk"\n')
    assert load(repo).languages[0].hash == "blk"


def test_a_preprocessor_may_feed_a_parser(repo):
    configure(repo, PARSER_TABLE.rstrip() + '\npreprocessor = "fake_pre:preprocess"\n')
    lang = load(repo).languages[0]
    assert lang.parser is not None and lang.preprocessor is not None


MODEL = """# keystone(finance): orders
block orders
    SELECT id,   amount FROM raw
end

block totals
    select sum(amount) from orders
end
"""


def setup(repo, run_cli, table=PARSER_TABLE, src=MODEL):
    configure(repo, table)
    (repo / "rev.blk").write_text(src)
    return run_cli


def sidecar(repo):
    return (repo / "keystones" / "finance" / "orders.md").read_text()


# --- it is a real adapter ----------------------------------------------------


def test_a_marker_resolves_to_a_definition(repo, run_cli):
    run = setup(repo, run_cli)
    assert run("add", "--id", "orders", "-m", "Revenue source of truth.") == 0
    assert 'target = "rev.blk::orders"' in sidecar(repo)


def test_the_kind_is_the_parser_name(repo, run_cli):
    run = setup(repo, run_cli)
    run("add", "--id", "orders", "-m", "x")
    assert 'hash = "blk"' in sidecar(repo)
    assert "keystones-plugin/1+fakeblk@1.0/lower/" in sidecar(repo)


def test_a_meaning_change_gates(repo, run_cli):
    run = setup(repo, run_cli)
    run("add", "--id", "orders", "-m", "x")
    (repo / "rev.blk").write_text(
        MODEL.replace("amount FROM raw", "amount FROM staged")
    )
    assert run("check", "--all", "--no-base") == 1


def test_a_reformat_does_not_gate(repo, run_cli):
    run = setup(repo, run_cli)
    run("add", "--id", "orders", "-m", "x")
    (repo / "rev.blk").write_text(
        MODEL.replace("SELECT id,   amount", "select id, amount")
    )
    assert run("check", "--all", "--no-base") == 0


def test_a_change_outside_the_definition_does_not_gate(repo, run_cli):
    run = setup(repo, run_cli)
    run("add", "--id", "orders", "-m", "x")
    (repo / "rev.blk").write_text(MODEL.replace("sum(amount)", "count(*)"))
    assert run("check", "--all", "--no-base") == 0


def test_c5_catches_a_hand_edited_sidecar(repo, run_cli, capsys):
    run = setup(repo, run_cli)
    run("add", "--id", "orders", "-m", "x")
    path = repo / "keystones" / "finance" / "orders.md"
    path.write_text(path.read_text().replace("FROM raw", "FROM elsewhere"))
    assert run("check", "--all", "--no-base") == 1
    assert "[C5]" in capsys.readouterr().err


def test_a_comment_change_is_c4_not_c3(repo, run_cli, capsys):
    """Comments are excluded from the rendering, so only the text hash moves."""
    run = setup(repo, run_cli)
    run("add", "--id", "orders", "-m", "x")
    (repo / "rev.blk").write_text(MODEL.replace("    SELECT", "    # why\n    SELECT"))
    assert run("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C4]" in err and "[C3]" not in err


def test_an_unparseable_file_points_at_hash_text(repo, run_cli, capsys):
    run = setup(repo, run_cli, src="# keystone(finance): orders\nnot a block\n")
    assert run("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "does not parse as blk" in err and "hash=text" in err


def test_an_option_change_is_a_migration_not_drift(repo, run_cli, capsys):
    run = setup(repo, run_cli)
    run("add", "--id", "orders", "-m", "x")
    configure(repo, WITH_OPTIONS)
    assert run("check", "--all", "--no-base") == 1
    assert run("migrate") == 0
    assert "fakeblk@1.0/keep" in sidecar(repo)


# --- composed with a preprocessor --------------------------------------------

COMPOSED = PARSER_TABLE.rstrip() + '\npreprocessor = "fake_pre:preprocess"\n'
TEMPLATED = MODEL.replace("FROM raw", "FROM <<source('raw')>>")


def test_a_preprocessor_feeds_the_parser(repo, run_cli):
    run = setup(repo, run_cli, COMPOSED, TEMPLATED)
    assert run("add", "--id", "orders", "-m", "x") == 0
    text = sidecar(repo)
    assert 'hash = "fake"' in text and "+fake/1" in text


def test_masked_content_still_gates_through_a_parser(repo, run_cli):
    run = setup(repo, run_cli, COMPOSED, TEMPLATED)
    run("add", "--id", "orders", "-m", "x")
    (repo / "rev.blk").write_text(TEMPLATED.replace("source('raw')", "source('stg')"))
    assert run("check", "--all", "--no-base") == 1


def test_a_refusal_reaches_the_user_through_a_parser(repo, run_cli, capsys):
    run = setup(
        repo,
        run_cli,
        COMPOSED,
        TEMPLATED.replace("block orders", "block orders REFUSE"),
    )
    assert run("check", "--all", "--no-base") == 1
    assert "refused this file" in capsys.readouterr().err


# --- the registry ------------------------------------------------------------


def test_the_plugin_adapter_wins_its_extension(repo):
    configure(repo, PARSER_TABLE)
    adapters.configure(load(repo))
    assert adapters.for_path("x.blk").name == "plugin"
    assert adapters.kinds_for("x.blk") == ("blk", "text")


def test_a_plugin_extension_never_needs_the_tree_sitter_extra(repo):
    """A .sql parser plugin must work on an install with no grammar pack."""
    configure(repo, PARSER_TABLE.replace('".blk"', '".sql"'))
    adapters.configure(load(repo))
    assert adapters.needs_extra("x.sql") is False
