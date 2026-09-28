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


# --- tree-sitter targets --------------------------------------------------

from keystones.adapters import treesitter as ts  # noqa: E402

needs_extra = pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")

TERRAGRUNT = "infra/terragrunt.hcl"
TG_SRC = """inputs = {
  region   = "us-east1"
  replicas = 3
}

locals {
  rate = 0.07
}

resource "google_compute_network_peering" "prod" {
  peer_network  = var.peer
  export_routes = true
}
"""


@pytest.fixture
def with_hcl_deps(repo, run_cli):
    (repo / "infra").mkdir()
    (repo / TERRAGRUNT).write_text(TG_SRC)
    assert (
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
            f"{TERRAGRUNT}::inputs",
            "--depends",
            f"{TERRAGRUNT}::locals.rate",
            "--depends",
            f"{TERRAGRUNT}::resource.google_compute_network_peering.prod.export_routes",
        )
        == 0
    )
    return repo


def edit_hcl(repo, old, new):
    path = repo / TERRAGRUNT
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


@needs_extra
def test_hcl_attributes_resolve_and_pass_clean(with_hcl_deps, run_cli):
    assert run_cli("check", "--all", "--no-base") == 0


@needs_extra
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("replicas = 3", "replicas = 5"),
        ("rate = 0.07", "rate = 0.09"),
        ("export_routes = true", "export_routes = false"),
    ],
    ids=["top-level attribute", "locals attribute", "block attribute"],
)
def test_changing_an_hcl_attribute_trips_c11(with_hcl_deps, run_cli, old, new):
    edit_hcl(with_hcl_deps, old, new)
    assert run_cli("check", "--all", "--no-base") == 1


@needs_extra
def test_reformatting_hcl_does_not_trip_c11(with_hcl_deps, run_cli):
    edit_hcl(with_hcl_deps, '  region   = "us-east1"', '  region = "us-east1"')
    edit_hcl(with_hcl_deps, "peer_network  = var.peer", "peer_network = var.peer")
    assert run_cli("check", "--all", "--no-base") == 0


@needs_extra
def test_a_sibling_attribute_edit_does_not_trip_c11(with_hcl_deps, run_cli):
    edit_hcl(with_hcl_deps, "peer_network  = var.peer", "peer_network  = var.other")
    assert run_cli("check", "--all", "--no-base") == 0


@needs_extra
def test_a_block_is_a_dependency_too(repo, run_cli):
    (repo / "infra").mkdir()
    (repo / TERRAGRUNT).write_text(TG_SRC)
    assert (
        run_cli(
            "add",
            f"{PAYOUT}::compute_payout",
            "--id",
            "p",
            "--category",
            "finance",
            "-m",
            "why.",
            "--depends",
            f"{TERRAGRUNT}::resource.google_compute_network_peering.prod",
        )
        == 0
    )
    edit_hcl(repo, "peer_network  = var.peer", "peer_network  = var.other")
    assert run_cli("check", "--all", "--no-base") == 1


@needs_extra
def test_a_typescript_definition_is_a_dependency(repo, run_cli):
    (repo / "fees.ts").write_text("export const FEE = 0.03;\nexport function f() {}\n")
    assert (
        run_cli(
            "add",
            f"{PAYOUT}::compute_payout",
            "--id",
            "p",
            "--category",
            "finance",
            "-m",
            "why.",
            "--depends",
            "fees.ts::FEE",
        )
        == 0
    )
    (repo / "fees.ts").write_text("export const FEE = 0.04;\nexport function f() {}\n")
    assert run_cli("check", "--all", "--no-base") == 1


@needs_extra
def test_an_unknown_hcl_symbol_is_refused(repo, run_cli, capsys):
    (repo / "infra").mkdir()
    (repo / TERRAGRUNT).write_text(TG_SRC)
    status = run_cli(
        "add",
        f"{PAYOUT}::compute_payout",
        "--id",
        "p",
        "--category",
        "finance",
        "-m",
        "why.",
        "--depends",
        f"{TERRAGRUNT}::locals.nope",
    )
    assert status == 1
    assert "locals.nope not found" in capsys.readouterr().err
