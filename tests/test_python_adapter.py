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
    src = "# keystone: dangling\nX = 1\n"
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
