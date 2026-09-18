"""The tree-sitter driver, across TypeScript, Go and HCL."""

import dataclasses
import os
import subprocess
import sys
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
    assert ts.hasher_id_for_path("app.ts").startswith("keystones-ts/3+typescript@")
    assert ts.hasher_id_for_path("main.tf").startswith("keystones-ts/3+hcl@")
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


def test_a_grammar_bump_with_identical_output_is_silent(repo, run_cli):
    """The common case: a pack bump whose grammar emits the same thing."""
    (repo / "app.ts").write_text(TS_SRC)
    run_cli("add", "--id", "payout-rounding", "-m", "Rounding contract.")
    sidecar = repo / "keystones" / "finance" / "payout-rounding.md"
    sidecar.write_text(sidecar.read_text().replace("@1.20.0", "@0.0.1"))
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_grammar_mismatch_that_disagrees_is_c13_not_drift(repo, run_cli, capsys):
    """A different grammar version says nothing about whether the code changed."""
    (repo / "app.ts").write_text(TS_SRC)
    run_cli("add", "--id", "payout-rounding", "-m", "Rounding contract.")
    sidecar = repo / "keystones" / "finance" / "payout-rounding.md"
    sidecar.write_text(sidecar.read_text().replace("@1.20.0", "@0.0.1"))
    path = repo / "app.ts"
    path.write_text(path.read_text().replace("* 100) / 100", "* 1000) / 1000"))
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C13]" in err and "[C3]" not in err


# Pinned under the grammar pack the dev group installs. A change here means a
# grammar moved, which shifts every stored hash in every consuming repo, so it
# is a SERIALIZER_VERSION decision and a migration, never a test edit.
PINNED_BY_LANGUAGE = {
    "typescript": (
        "app.ts",
        "// keystone: k\n"
        "export function computePayout(amount: number): number {\n"
        "  const rate = 1.07;\n"
        "  return Math.round(amount * rate * 100) / 100;\n"
        "}\n",
        "sha256:cb695dc29a66ae2412e88935ed520831f1408d50b112dfdef45e72c43cc06dfa",
    ),
    "go": (
        "m.go",
        "// keystone: k\nfunc ComputePayout(a float64) float64 {\n"
        "\treturn a * 1.07\n}\n",
        "sha256:91b4790c8ccaccbc31142dd67b624a6b245b05e09e0286a7ed84875e9e4de317",
    ),
    "hcl": (
        "m.tf",
        "# keystone: k\n"
        'resource "google_compute_network_peering" "prod" {\n'
        "  peer_network  = var.peer\n"
        "  export_routes = true\n"
        "}\n",
        "sha256:5521957ec081e77753bd920b7949006e9488c559d9ebcabbf99b9f24edc206a2",
    ),
}


@pytest.mark.parametrize("language", sorted(PINNED_BY_LANGUAGE))
def test_grammar_output_is_pinned(language):
    """The tree-sitter counterpart of the Python canary.

    Without this a grammar change is only caught when it happens to break a
    node-type assumption somewhere else, which is not the same as noticing that
    every stored hash just moved.
    """
    path, src, expected = PINNED_BY_LANGUAGE[language]
    marker = ts.markers(path, src)[0]
    assert ts.hashes(src, ts.resolve(src, marker))[0] == expected


def test_the_pinned_pack_is_the_one_under_test():
    """A canary is only evidence if you know which grammar produced it."""
    from importlib.metadata import version

    assert f"@{version('tree-sitter-language-pack')}/" in ts.hasher_id_for_path(
        "app.ts"
    )


def _spec(**overrides) -> ts.LanguageSpec:
    base = {
        "language": "typescript",
        "extensions": (".ts",),
        "definitions": frozenset({"function_declaration"}),
    }
    return ts.LanguageSpec(**{**base, **overrides})


def test_hasher_id_carries_a_digest_of_the_spec():
    """The id must identify the spec, not just the grammar that fed it.

    A configurable spec means two installs can share a language and a pack
    version and still serialise differently. Without this the disagreement
    reads as C3 drifted code, which is a lie about the source.
    """
    widened = _spec(
        definitions=frozenset({"function_declaration", "class_declaration"})
    )
    assert ts.hasher_id_for(_spec()) != ts.hasher_id_for(widened)


def test_hasher_id_ignores_fields_that_cannot_move_a_hash():
    """Otherwise a comment-leader edit bills every consumer a migration."""
    cosmetic = _spec(extensions=(".ts", ".mts"), line_comment="#")
    assert ts.hasher_id_for(_spec()) == ts.hasher_id_for(cosmetic)


def test_the_spec_digest_is_stable_across_processes():
    """frozenset iteration order follows PYTHONHASHSEED; the digest must not.

    An unsorted digest would hand every consumer a different id per run, and
    `fix` would refuse to write in the environment that just computed it.
    """
    script = (
        "from keystones.adapters import treesitter as ts;"
        "print(ts.hasher_id_for_path('app.ts'))"
    )
    ids = {
        subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout.strip()
        for seed in ("0", "1", "12345")
    }
    assert len(ids) == 1


def _respec(monkeypatch, **overrides) -> None:
    """Stand in for a language spec defined outside this file."""
    edited = dataclasses.replace(ts.spec_for("app.ts"), **overrides)
    monkeypatch.setitem(ts._BY_EXTENSION, ".ts", edited)


def test_a_spec_edit_with_identical_output_is_silent(repo, run_cli, monkeypatch):
    """Widening a spec past what this file uses must not churn every consumer."""
    (repo / "app.ts").write_text(TS_SRC)
    run_cli("add", "--id", "payout-rounding", "-m", "Rounding contract.")
    _respec(
        monkeypatch,
        definitions=ts.spec_for("app.ts").definitions | {"enum_declaration"},
    )
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_spec_edit_that_disagrees_is_c13_not_drift(
    repo, run_cli, monkeypatch, capsys
):
    """The failure this PR exists to prevent.

    Dropping `export_statement` from wrappers takes `export` out of the hash.
    Nobody touched app.ts, so reporting C3 would send its owner to review a
    change that never happened.
    """
    (repo / "app.ts").write_text(TS_SRC)
    run_cli("add", "--id", "payout-rounding", "-m", "Rounding contract.")
    _respec(monkeypatch, wrappers=frozenset())
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C13]" in err and "[C3]" not in err


def test_a_spec_edit_migrates_across_when_the_code_is_unchanged(
    repo, run_cli, monkeypatch
):
    (repo / "app.ts").write_text(TS_SRC)
    run_cli("add", "--id", "payout-rounding", "-m", "Rounding contract.")
    _respec(monkeypatch, wrappers=frozenset())
    assert run_cli("migrate") == 0
    assert run_cli("check", "--all", "--no-base") == 0
