"""The tree-sitter driver, across TypeScript, Go and HCL."""

import subprocess
from pathlib import Path

import pytest

from keystones.adapters import treesitter as ts
from keystones.adapters.base import ResolutionError

pytestmark = pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")

TS_SRC = """import { Entry } from "./types";

// keystone(finance): payout-rounding
export function computePayout(amount: number): number {
  // do not reorder
  return Math.round(amount * 100) / 100;
}

export class Ledger {
  post(entry: Entry): void {
    return;
  }
}
"""

GO_SRC = """package main

// keystone(finance): go-payout
func ComputePayout(a float64) float64 {
\treturn a * 1.07
}
"""

TF_SRC = """# keystone(infra): peering
resource "google_compute_network_peering" "prod" {
  peer_network  = var.peer
  export_routes = true
}
"""


def only(path, src):
    found = ts.markers(path, src)
    assert len(found) == 1, [m.id for m in found]
    return found[0]


def semantic(path, src):
    marker = only(path, src)
    return ts.hashes(src, ts.resolve(src, marker))[0]


def text(path, src):
    marker = only(path, src)
    return ts.hashes(src, ts.resolve(src, marker))[1]


@pytest.mark.parametrize(
    ("path", "src", "qualname"),
    [
        ("app.ts", TS_SRC, "computePayout"),
        ("ledger.go", GO_SRC, "ComputePayout"),
        ("main.tf", TF_SRC, "resource.google_compute_network_peering.prod"),
    ],
)
def test_resolution(path, src, qualname):
    assert ts.resolve(src, only(path, src)).qualname == qualname


def test_export_wrapper_belongs_to_the_definition():
    """The marker sits above `export`, not above `function`."""
    target = ts.resolve(TS_SRC, only("app.ts", TS_SRC))
    assert TS_SRC.splitlines()[target.start - 1].startswith("export function")


def test_nested_qualname_in_typescript():
    src = TS_SRC.replace(
        "export class Ledger {", "// keystone: post\nexport class Ledger {"
    )
    found = {m.id: m for m in ts.markers("app.ts", src)}
    assert ts.resolve(src, found["post"]).qualname == "Ledger"


@pytest.mark.parametrize(
    ("path", "src", "old", "new"),
    [
        (
            "app.ts",
            TS_SRC,
            "return Math.round(amount * 100) / 100;",
            "return Math.round(\n    amount * 100,\n  ) / 100;",
        ),
        ("ledger.go", GO_SRC, "\treturn a * 1.07", "\treturn a *   1.07"),
        ("main.tf", TF_SRC, "peer_network  = var.peer", "peer_network = var.peer"),
    ],
)
def test_reformatting_does_not_change_the_hash(path, src, old, new):
    assert semantic(path, src) == semantic(path, src.replace(old, new))


@pytest.mark.parametrize(
    ("path", "src", "old", "new"),
    [
        ("app.ts", TS_SRC, "* 100) / 100", "* 1000) / 1000"),
        ("ledger.go", GO_SRC, "a * 1.07", "a * 1.09"),
        ("main.tf", TF_SRC, "export_routes = true", "export_routes = false"),
    ],
)
def test_semantic_changes_are_detected(path, src, old, new):
    assert semantic(path, src) != semantic(path, src.replace(old, new))


def test_operators_are_part_of_the_hash():
    """Unnamed tokens carry meaning; dropping them would collide + with -."""
    plus = "// keystone: op\nfunction f(a, b) { return a + b; }\n"
    minus = "// keystone: op\nfunction f(a, b) { return a - b; }\n"
    assert semantic("x.ts", plus) != semantic("x.ts", minus)


def test_comment_edits_move_only_the_text_hash():
    changed = TS_SRC.replace("// do not reorder", "// reordering is fine")
    assert semantic("app.ts", TS_SRC) == semantic("app.ts", changed)
    assert text("app.ts", TS_SRC) != text("app.ts", changed)


def test_marker_inside_a_string_literal_is_not_a_marker():
    src = 'const doc = "// keystone: not-real";\n'
    assert ts.markers("app.ts", src) == []


def test_marker_attached_to_nothing_is_an_error():
    src = "// keystone: dangling\nconst x = 1;\n".replace("const x = 1;", "")
    with pytest.raises(ResolutionError, match="attaches to nothing"):
        ts.resolve(src, only("app.ts", src))


def test_hasher_id_carries_language_and_grammar_version():
    assert ts.hasher_id_for_path("app.ts").startswith("keystones-ts/2+typescript@")
    assert ts.hasher_id_for_path("main.tf").startswith("keystones-ts/2+hcl@")
    assert ts.hasher_id_for_path("app.ts") != ts.hasher_id_for_path("ledger.go")


def test_stored_source_rehashes_to_the_stored_hash():
    """C5's proof has to work for tree-sitter targets too."""
    marker = only("app.ts", TS_SRC)
    target = ts.resolve(TS_SRC, marker)
    stored = ts.canonical_source(TS_SRC, target)
    assert ts.hash_stored_source(stored, str(target)) == ts.hashes(TS_SRC, target)[0]


def test_canonical_source_is_readable_code():
    target = ts.resolve(TS_SRC, only("app.ts", TS_SRC))
    assert ts.canonical_source(TS_SRC, target).startswith(
        "export function computePayout"
    )


def test_region_inside_a_typescript_file():
    src = (
        "const a = 1;\n"
        "// keystone:start(finance): r\n"
        "const b = 2;\n"
        "// keystone:end\n"
        "const c = 3;\n"
    )
    target = ts.resolve(src, only("x.ts", src))
    assert target.region and (target.start, target.end) == (3, 3)


def test_ignore_file_directive_applies_to_treesitter_files():
    src = "// keystones: ignore-file\n// keystone: example\nfunction f() {}\n"
    assert ts.markers("x.ts", src) == []


def test_end_to_end_through_the_cli(repo, run_cli):
    (repo / "app.ts").write_text(TS_SRC)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('"finance"]', '"finance"]'))
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "ts"], cwd=repo, check=True)

    assert run_cli("add", "--id", "payout-rounding", "-m", "Rounding contract.") == 0
    assert run_cli("check", "--all", "--no-base") == 0

    path: Path = repo / "app.ts"
    path.write_text(path.read_text().replace("* 100) / 100", "* 1000) / 1000"))
    assert run_cli("check", "--all", "--no-base") == 1
    assert run_cli("fix", "-m", "precision widened.") == 0
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_grammar_mismatch_fails_but_not_as_drift(repo, run_cli, capsys):
    """A different grammar version says nothing about whether the code changed."""
    (repo / "app.ts").write_text(TS_SRC)
    run_cli("add", "--id", "payout-rounding", "-m", "Rounding contract.")
    sidecar = repo / "keystones" / "finance" / "payout-rounding.md"
    sidecar.write_text(
        sidecar.read_text().replace("@1.20.0", "@0.0.1").replace("@1.", "@0.")
    )
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C13]" in err and "[C3]" not in err
