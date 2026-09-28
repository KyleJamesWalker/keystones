"""C13 names what moved between two hasher ids, so the fix is obvious."""

import pytest

from keystones.checks import hasher_difference

TS = "keystones-ts/3+typescript@1.20.0/d9c21109b66f"


@pytest.mark.parametrize(
    ("recorded", "expected", "said"),
    [
        (
            TS,
            "keystones-ts/3+typescript@1.21.0/d9c21109b66f",
            "tree-sitter-language-pack 1.20.0 hashed the sidecar and 1.21.0 is "
            "installed",
        ),
        (
            TS,
            "keystones-ts/3+typescript@1.20.0/2327218a87db",
            "the typescript spec in [[tool.keystones.language]] changed",
        ),
        (
            TS,
            "keystones-ts/4+typescript@1.20.0/d9c21109b66f",
            "keystones' tree-sitter serializer moved from 3 to 4",
        ),
        (
            f"{TS}+dbt/1",
            f"{TS}+dbt/2",
            "preprocessor dbt moved from 1 to 2",
        ),
        (
            "keystones-plugin/1+sqlglot@30.18.0/snowflake/9f1c0b2a7d3e",
            "keystones-plugin/1+sqlglot@30.19.0/snowflake/9f1c0b2a7d3e",
            "sqlglot 30.18.0 hashed the sidecar and 30.19.0 is installed",
        ),
        ("keystones-ast/1", "keystones-text/2", "the basis moved from ast to text"),
    ],
)
def test_the_difference_is_named(recorded, expected, said):
    assert said in hasher_difference(recorded, expected)


def test_a_pack_difference_says_how_to_pin():
    text = hasher_difference(TS, "keystones-ts/3+typescript@1.21.0/d9c21109b66f")
    assert "additional_dependencies" in text


def test_a_plugin_rendering_bump_is_named_as_such():
    text = hasher_difference(
        "keystones-plugin/1+lkml@1.3.7/render1+x/1",
        "keystones-plugin/1+lkml@1.3.7/render2+x/1",
    )
    assert "lkml's rendering moved from render1 to render2" in text
    assert "keystones migrate" in text
