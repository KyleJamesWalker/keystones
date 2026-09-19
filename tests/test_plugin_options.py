"""Per-project plugin options, written next to the plugin that owns them."""

import pytest

from keystones.adapters import treesitter as ts
from keystones.config import ConfigError, load

pytestmark = pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")

STRING_FORM = """
[[tool.keystones.language]]
builtin = "sql"
extensions = [".sql"]
preprocessor = "fake_pre:preprocess"
"""

TABLE_FORM = """
[[tool.keystones.language]]
builtin = "sql"
extensions = [".sql"]
preprocessor = { plugin = "fake_pre:preprocess", upper = true }
"""


def configure(repo, table):
    pyproject = repo / "pyproject.toml"
    base = pyproject.read_text().split("\n[[tool.keystones.language]]")[0]
    pyproject.write_text(base.rstrip() + "\n" + table)


def test_a_string_reference_means_no_options(repo):
    configure(repo, STRING_FORM)
    pre = load(repo).languages[0].preprocessor
    assert pre.path == "fake_pre:preprocess"
    assert pre.kwargs == {}


def test_a_table_reference_carries_options_to_the_plugin(repo):
    configure(repo, TABLE_FORM)
    pre = load(repo).languages[0].preprocessor
    assert pre.kwargs == {"upper": True}
    _masked, extra = pre.fn("select <<ref('a')>>\n")
    assert extra == "REF('A')"


def test_an_option_the_plugin_cannot_take_is_a_config_error(repo):
    configure(repo, TABLE_FORM.replace("upper = true", "shout = true"))
    with pytest.raises(ConfigError, match="shout"):
        load(repo)


def test_a_table_without_plugin_is_a_config_error(repo):
    configure(repo, TABLE_FORM.replace('plugin = "fake_pre:preprocess", ', ""))
    with pytest.raises(ConfigError, match="plugin"):
        load(repo)


def test_options_are_part_of_the_hasher(repo):
    """Changing an option changes the mask, so it must read as a migration."""
    configure(repo, STRING_FORM)
    plain = ts.hasher_id_for(ts.spec_from_config(load(repo).languages[0]))
    configure(repo, TABLE_FORM)
    upper = ts.hasher_id_for(ts.spec_from_config(load(repo).languages[0]))
    assert plain != upper


def test_an_option_value_the_plugin_rejects_is_a_config_error(repo):
    """Bound at load by a probe with empty text, so no file is touched first."""
    configure(repo, TABLE_FORM.replace("upper = true", 'upper = "loud"'))
    with pytest.raises(ConfigError, match="upper must be true or false"):
        load(repo)
