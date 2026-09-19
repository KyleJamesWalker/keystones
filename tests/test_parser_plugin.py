"""The parser hook: a plugin supplies the tree, keystones does the rest."""

from dataclasses import FrozenInstanceError

import pytest

from keystones.parser import Definition, Unparseable


def test_the_contract_module_is_the_only_import_a_plugin_needs():
    assert Definition("orders", 2, 9).qualname == "orders"
    assert issubclass(Unparseable, Exception)
    with pytest.raises(FrozenInstanceError):
        Definition("x", 1, 2).start = 5
