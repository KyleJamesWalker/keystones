"""C11. Narrowing the indirection hole for symbols named explicitly."""

import pytest

from keystones import dependencies

HELPERS = "billing/helpers.py"
PAYOUT = "billing/payout.py"


@pytest.fixture
def with_deps(repo, run_cli):
    (repo / HELPERS).write_text(
        "BASE_RATE = 0.07\n\n\ndef quantize(x):\n    return round(x, 2)\n"
    )
    run_cli(
        "add",
        f"{PAYOUT}::compute_payout",
        "--id",
        "payout-rounding",
        "--category",
        "finance",
        "-m",
        "GAAP rounding.",
        "--depends",
        f"{HELPERS}::BASE_RATE",
        "--depends",
        f"{HELPERS}::quantize",
    )
    return repo


def edit_helper(repo, old, new):
    path = repo / HELPERS
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_declared_dependencies_are_recorded(with_deps):
    text = (with_deps / "keystones" / "finance" / "payout-rounding.md").read_text()
    assert "depends" in text and "depends_hash" in text


def test_clean_state_passes(with_deps, run_cli):
    assert run_cli("check", "--all", "--no-base") == 0


def test_changing_a_dependency_body_trips_c11(with_deps, run_cli):
    """The keystone itself is untouched; this is the indirection case."""
    edit_helper(with_deps, "round(x, 2)", "round(x, 4)")
    assert run_cli("check", "--all", "--no-base") == 1


def test_changing_a_dependency_constant_trips_c11(with_deps, run_cli):
    edit_helper(with_deps, "BASE_RATE = 0.07", "BASE_RATE = 0.09")
    assert run_cli("check", "--all", "--no-base") == 1


def test_reformatting_a_dependency_does_not_trip_c11(with_deps, run_cli):
    edit_helper(
        with_deps,
        "def quantize(x):\n    return round(x, 2)",
        "def quantize(\n    x,\n):\n    return round(  x , 2 )",
    )
    assert run_cli("check", "--all", "--no-base") == 0


def test_editing_an_unrelated_symbol_does_not_trip_c11(with_deps, run_cli):
    path = with_deps / HELPERS
    path.write_text(path.read_text() + "\n\ndef unrelated():\n    return 1\n")
    assert run_cli("check", "--all", "--no-base") == 0


def test_deleting_a_dependency_is_an_error(with_deps, run_cli):
    edit_helper(with_deps, "BASE_RATE = 0.07", "")
    assert run_cli("check", "--all", "--no-base") == 1


def test_fix_acknowledges_a_dependency_change(with_deps, run_cli):
    edit_helper(with_deps, "round(x, 2)", "round(x, 4)")
    assert run_cli("fix") == 1, "a dependency change is semantic, so -m is required"
    assert run_cli("fix", "-m", "helper precision widened.") == 0
    assert run_cli("check", "--all", "--no-base") == 0


def test_hash_is_order_independent(tmp_path):
    (tmp_path / "a.py").write_text("X = 1\nY = 2\n")
    forward = dependencies.combined_hash(tmp_path, ["a.py::X", "a.py::Y"])
    reverse = dependencies.combined_hash(tmp_path, ["a.py::Y", "a.py::X"])
    assert forward == reverse


def test_no_dependencies_is_a_stable_empty_hash(tmp_path):
    assert dependencies.combined_hash(tmp_path, []) == dependencies.EMPTY


def test_malformed_spec_raises(tmp_path):
    with pytest.raises(dependencies.UnresolvedDependency, match="expected path"):
        dependencies.combined_hash(tmp_path, ["no-separator"])


def test_absent_depends_hash_compares_equal_to_the_empty_hash(repo, run_cli):
    """A no-dependency entry stores no depends_hash; that must not read as drift."""
    run_cli(
        "add",
        "billing/payout.py::compute_payout",
        "--id",
        "plain",
        "--category",
        "finance",
        "-m",
        "why.",
    )
    path = repo / "billing" / "payout.py"
    path.write_text("# a new leading comment\n" + path.read_text())
    assert run_cli("fix") == 0, "a pure move must not demand a note"
