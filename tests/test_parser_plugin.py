"""The parser hook: a plugin supplies the tree, keystones does the rest."""

from dataclasses import FrozenInstanceError

import pytest

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
