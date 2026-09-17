import ast

import pytest

from keystones.hashing import HASHER_ID, render, semantic_hash, text_hash

SAMPLE = """@retry(max_attempts=3)
def compute_payout(amount: Decimal) -> Decimal:
    \"\"\"Round per GAAP policy.\"\"\"
    total = amount * RATE
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
"""

# Pinned so the CI matrix proves the hash basis does not drift between CPython
# minors. A change here is a hasher version bump, never a test edit.
PINNED = "sha256:ea3855f8e1e2479cfa34034f3be4686f21b2b4893e8b702f33cee70f584d9597"


def node(src: str) -> ast.AST:
    return ast.parse(src).body[0]


def test_hasher_id_is_versioned():
    assert HASHER_ID == "keystones-ast/1"


def test_pinned_hash_is_stable_across_interpreters():
    assert semantic_hash(node(SAMPLE)) == PINNED


@pytest.mark.parametrize(
    "variant",
    [
        "def f(x):\n    return round(x, 2)",
        "def f( x ):\n        return round(  x , 2 )",
        "def f(\n    x,\n):\n    return round(x, 2)",
        "def f(x):\n    # a comment\n    return round(x, 2)",
    ],
)
def test_formatting_and_comments_do_not_change_the_semantic_hash(variant):
    baseline = semantic_hash(node("def f(x):\n    return round(x, 2)"))
    assert semantic_hash(node(variant)) == baseline


@pytest.mark.parametrize(
    "variant",
    [
        "def f(x):\n    return round(x, 4)",
        "def f(x):\n    return round(y, 2)",
        "def f(x, y):\n    return round(x, 2)",
        "def g(x):\n    return round(x, 2)",
        'def f(x):\n    """Doc."""\n    return round(x, 2)',
    ],
)
def test_semantic_changes_are_detected(variant):
    baseline = semantic_hash(node("def f(x):\n    return round(x, 2)"))
    assert semantic_hash(node(variant)) != baseline


def test_quote_style_and_int_float_are_distinguished():
    assert semantic_hash(node("x = 'a'")) == semantic_hash(node('x = "a"'))
    assert semantic_hash(node("x = 1")) != semantic_hash(node("x = 1.0"))


def test_empty_fields_are_omitted_for_cross_version_stability():
    """A field added in a later CPython arrives empty, so it must not render."""
    assert "type_params" not in render(node("def f():\n    pass"))
    assert "posonlyargs" not in render(node("def f(a):\n    pass"))


def test_text_hash_tracks_comments():
    target = node("def f(x):\n    return x")
    assert text_hash(target, ["# keep"]) != text_hash(target, ["# changed"])
    assert text_hash(target, []) != text_hash(target, ["# added"])
    assert text_hash(target, ["# same"]) == text_hash(target, ["# same"])
