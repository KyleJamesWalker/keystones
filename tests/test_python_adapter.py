import pytest

from keystones.adapters import python as adapter
from keystones.adapters.base import ResolutionError
from keystones.models import Scope

DECORATED = """import functools


# keystone(finance): payout-rounding
@functools.cache
def compute_payout(amount):
    return round(amount, 2)
"""

NESTED = """class Ledger:
    # keystone: ledger-post
    def post(self, entry):
        return entry
"""

INSIDE_BODY = """def handler(request):
    if request.urgent:
        # keystone: urgent-path
        return fast(request)
    return slow(request)
"""


def one_marker(src):
    markers = adapter.markers("x.py", src)
    assert len(markers) == 1
    return markers[0]


def test_marker_above_decorator_attaches_to_the_function():
    marker = one_marker(DECORATED)
    target = adapter.resolve(DECORATED, marker)
    assert target.qualname == "compute_payout"
    assert target.start == 5, "decorator line is the start of the definition"


def test_category_and_default_category():
    assert one_marker(DECORATED).category == "finance"
    assert one_marker(NESTED).category == "default"


def test_nested_qualname():
    target = adapter.resolve(NESTED, one_marker(NESTED))
    assert target.qualname == "Ledger.post"


def test_marker_inside_a_body_attaches_to_the_enclosing_definition():
    target = adapter.resolve(INSIDE_BODY, one_marker(INSIDE_BODY))
    assert target.qualname == "handler"


def test_file_scope():
    src = "# keystone(file, finance): whole-thing\nX = 1\n"
    marker = one_marker(src)
    assert marker.scope is Scope.FILE
    assert marker.category == "finance"
    assert adapter.resolve(src, marker).qualname is None


def test_marker_shaped_string_literal_is_not_a_marker():
    assert adapter.markers("x.py", 'DOC = "# keystone: not-real"\n') == []


def test_marker_attached_to_nothing_is_an_error():
    src = "# keystone: dangling\nprint(1)\n"
    with pytest.raises(ResolutionError, match="attaches to nothing"):
        adapter.resolve(src, one_marker(src))


def test_marker_comment_excluded_from_text_hash():
    """Renaming a category must not read as a comment edit."""
    a = "# keystone: x\ndef f():\n    return 1\n"
    b = "# keystone(finance): x\ndef f():\n    return 1\n"
    ta = adapter.resolve(a, one_marker(a))
    tb = adapter.resolve(b, one_marker(b))
    assert adapter.hashes(a, ta) == adapter.hashes(b, tb)


def test_canonical_source_includes_decorators():
    target = adapter.resolve(DECORATED, one_marker(DECORATED))
    source = adapter.canonical_source(DECORATED, target)
    assert source.startswith("@functools.cache")
    rehashed = adapter.hash_stored_source(source, "x.py::compute_payout")
    assert rehashed == adapter.hashes(DECORATED, target)[0]


GUARD = """import pytest


# keystone: guard
def test_guard():
    assert 1
"""


@pytest.mark.parametrize(
    "decorator",
    [
        "@pytest.mark.skip(reason='flaky')",
        "@pytest.mark.xfail",
        "@pytest.mark.skipif(True, reason='x')",
    ],
)
def test_switching_off_a_guard_test_is_drift(repo, run_cli, capsys, decorator):
    """The change most worth catching on a test keystone is the one that disables it."""
    (repo / "test_guard.py").write_text(GUARD)
    assert run_cli("add", "--id", "guard", "-m", "Guards the rounding contract.") == 0
    (repo / "test_guard.py").write_text(
        GUARD.replace("def test_guard", f"{decorator}\ndef test_guard")
    )
    assert run_cli("check", "--all", "--no-base") == 1
    assert "[C3] keystone 'guard' changed" in capsys.readouterr().err


RATES = """BASE_RATE = 0.03


class Fees:
    SURCHARGE: float = 0.5

    class Card:
        FLAT = 1


def fee(amount):
    return amount * BASE_RATE
"""


def test_a_module_constant_takes_a_node_marker():
    src = RATES.replace("BASE_RATE = 0.03", "# keystone: base-rate\nBASE_RATE = 0.03")
    target = adapter.resolve(src, one_marker(src))
    assert (target.qualname, target.start, target.end) == ("BASE_RATE", 2, 2)


def test_a_constant_keystone_gates_its_value_not_its_spelling():
    src = RATES.replace("BASE_RATE = 0.03", "# keystone: base-rate\nBASE_RATE = 0.03")
    target = adapter.resolve(src, one_marker(src))
    before = adapter.hashes(src, target)[0]
    assert adapter.hashes(src.replace("= 0.03", "=0.030"), target)[0] == before
    assert adapter.hashes(src.replace("= 0.03", "= 0.04"), target)[0] != before


def test_a_constant_bound_twice_refuses_a_keystone():
    """A decoy binding above the one that wins at runtime."""
    src = "# keystone: base-rate\nBASE_RATE = 0.03\nBASE_RATE = 0.9\n"
    with pytest.raises(ResolutionError, match="assigned more than once"):
        adapter.resolve(src, one_marker(src))


def test_a_marker_in_a_class_body_still_covers_the_class():
    """Resolving it to the attribute would silently move existing keystones."""
    src = RATES.replace(
        "    SURCHARGE: float = 0.5", "    # keystone: fees\n    SURCHARGE: float = 0.5"
    )
    assert adapter.resolve(src, one_marker(src)).qualname == "Fees"


@pytest.mark.parametrize(
    ("symbol", "old", "new"),
    [
        ("BASE_RATE", "0.03", "0.04"),
        ("Fees.SURCHARGE", "0.5", "0.6"),
        ("Fees.Card.FLAT", "FLAT = 1", "FLAT = 2"),
    ],
)
def test_depends_reaches_constants_and_class_attributes(symbol, old, new):
    before = adapter.render_symbol(RATES, symbol)
    assert before is not None
    assert adapter.render_symbol(RATES.replace(old, new), symbol) != before


def test_a_constant_keystone_end_to_end(repo, run_cli, capsys):
    (repo / "rates.py").write_text(RATES)
    assert run_cli("add", "rates.py::BASE_RATE", "--id", "base-rate", "-m", "Why.") == 0
    assert "# keystone: base-rate\nBASE_RATE" in (repo / "rates.py").read_text()
    assert run_cli("check", "--all", "--no-base") == 0, capsys.readouterr().err
    path = repo / "rates.py"
    path.write_text(path.read_text().replace("0.03", "0.04"))
    assert run_cli("check", "--all", "--no-base") == 1
    assert "[C3] keystone 'base-rate' changed" in capsys.readouterr().err


def test_a_class_attribute_dependency_end_to_end(repo, run_cli, capsys):
    (repo / "rates.py").write_text(RATES)
    assert (
        run_cli(
            "add",
            "rates.py::fee",
            "--id",
            "fee",
            "-m",
            "Why.",
            "--depends",
            "rates.py::Fees.SURCHARGE",
        )
        == 0
    )
    path = repo / "rates.py"
    path.write_text(path.read_text().replace("0.5", "0.6"))
    assert run_cli("check", "--all", "--no-base") == 1
    assert "[C11] a dependency of keystone 'fee' changed" in capsys.readouterr().err
