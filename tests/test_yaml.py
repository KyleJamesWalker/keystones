"""`hash=yaml`: YAML gated on what it means, not on how it is laid out."""

import subprocess

import pytest

from keystones import adapters
from keystones.adapters import structured

needs_yaml = pytest.mark.skipif(not structured.available(), reason="needs PyYAML")

VALUES = "deploy/values.yaml"
SIDECAR = "keystones/default/replicas.md"

SRC = """# helm values
image:
  name: app
  tag: "1.2"
spec:
  # keystone: replicas
  replicas: 3
  ports: [80, 443]
policy:
  # keep in step with the SLO
  budget: 0.99
"""

YAML_TABLE = (
    '\n[[tool.keystones.language]]\nextensions = [".yaml", ".yml"]\nhash = "yaml"\n'
)


@pytest.fixture
def values(repo):
    (repo / "deploy").mkdir()
    (repo / VALUES).write_text(SRC)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + YAML_TABLE)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "values"], cwd=repo, check=True)
    return repo


@pytest.fixture
def gated(values, run_cli):
    assert run_cli("add", "--id", "replicas", "-m", "Capacity floor.") == 0
    return values


def edit(repo, old, new):
    path = repo / VALUES
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def check(run_cli, capsys):
    capsys.readouterr()
    status = run_cli("check", "--all", "--no-base")
    return status, capsys.readouterr().err


def test_text_stays_the_default_basis_for_yaml():
    assert adapters.kinds_for("net.yaml")[0] == "text"


@needs_yaml
def test_yaml_is_offered_as_a_basis():
    assert "yaml" in adapters.kinds_for("net.yaml")
    assert adapters.for_kind("net.yaml", "yaml") is structured


def test_the_missing_extra_is_named(monkeypatch):
    monkeypatch.setattr(structured, "available", lambda: False)
    with pytest.raises(adapters.UnknownKind, match=r"keystones\[yaml\]"):
        adapters.for_kind("net.yaml", "yaml")


@needs_yaml
def test_a_node_keystone_covers_the_key_and_records_the_basis(gated):
    sidecar = (gated / SIDECAR).read_text()
    assert f'target = "{VALUES}::spec.replicas"' in sidecar
    assert 'hash = "yaml"' in sidecar
    assert "keystones-yaml/2+pyyaml@" in sidecar
    assert "replicas: 3" in sidecar


@needs_yaml
def test_a_fresh_keystone_passes_every_check(gated, run_cli, capsys):
    assert check(run_cli, capsys) == (0, "")


@needs_yaml
def test_a_value_change_is_c3(gated, run_cli, capsys):
    edit(gated, "replicas: 3", "replicas: 2")
    status, err = check(run_cli, capsys)
    assert status == 1
    assert f"{VALUES}:6: error: [C3]" in err


@needs_yaml
@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("  replicas: 3", "  replicas:   3"),
        ("  replicas: 3", "  replicas: 3   # three"),
        ('image:\n  name: app\n  tag: "1.2"', "image:\n  tag: '1.2'\n  name: app"),
    ],
    ids=["spacing", "trailing comment", "sibling reorder and quotes"],
)
def test_layout_changes_are_invisible(gated, run_cli, capsys, old, new):
    edit(gated, old, new)
    assert check(run_cli, capsys)[0] == 0


@needs_yaml
def test_a_comment_line_edit_is_c4(values, run_cli, capsys):
    assert run_cli("add", f"{VALUES}::policy", "--id", "budget", "-m", "SLO.") == 0
    edit(values, "# keep in step with the SLO", "# keep in step with the error budget")
    status, err = check(run_cli, capsys)
    assert status == 1
    assert "[C4]" in err and "[C3]" not in err
    assert run_cli("fix") == 0


@needs_yaml
def test_a_whole_file_keystone_survives_a_reorder(values, run_cli, capsys):
    edit(values, "  # keystone: replicas\n", "")
    assert run_cli("add", VALUES, "--id", "values", "-m", "All of it.") == 0
    path = values / VALUES
    text = path.read_text()
    head, _, rest = text.partition('image:\n  name: app\n  tag: "1.2"\n')
    path.write_text(head + rest + 'image:\n  tag: "1.2"\n  name: app\n')
    assert check(run_cli, capsys)[0] == 0
    edit(values, "budget: 0.99", "budget: 0.95")
    assert check(run_cli, capsys)[0] == 1


@needs_yaml
def test_a_region_parses_on_its_own(values, run_cli, capsys):
    edit(
        values,
        "  # keystone: replicas\n  replicas: 3\n  ports: [80, 443]\n",
        "  # keystone:start: capacity\n  replicas: 3\n  ports: [80, 443]\n"
        "  # keystone:end\n",
    )
    assert run_cli("add", "--id", "capacity", "-m", "Capacity.") == 0
    assert check(run_cli, capsys) == (0, "")
    edit(values, "  ports: [80, 443]", "  ports:\n    - 80\n    - 443")
    assert run_cli("fix") == 0, "a pure layout change moves the region only"
    assert check(run_cli, capsys)[0] == 0
    edit(values, "replicas: 3", "replicas: 4")
    assert check(run_cli, capsys)[0] == 1


@needs_yaml
def test_a_yaml_key_is_a_dependency(gated, run_cli):
    assert (
        run_cli(
            "add",
            "billing/payout.py::compute_payout",
            "--id",
            "payout",
            "-m",
            "why.",
            "--depends",
            f"{VALUES}::image.tag",
        )
        == 0
    )
    edit(gated, 'tag: "1.2"', 'tag: "1.3"')
    assert run_cli("check", "--all", "--no-base") == 1


@needs_yaml
def test_a_duplicate_key_is_refused(values, run_cli, capsys):
    edit(values, "  ports: [80, 443]\n", "  ports: [80, 443]\n  replicas: 5\n")
    assert run_cli("add", "--id", "replicas", "-m", "why.") == 1
    assert "more than once" in capsys.readouterr().err


@needs_yaml
def test_a_text_keystone_moves_to_yaml_through_migrate(repo, run_cli, capsys):
    """A repo that gated YAML on text opts in; nobody touched the file."""
    (repo / "deploy").mkdir()
    (repo / VALUES).write_text(
        "# keystone(file): values\n" + SRC.replace("  # keystone: replicas\n", "")
    )
    assert run_cli("add", "--id", "values", "-m", "why.") == 0
    sidecar = repo / SIDECAR.replace("replicas", "values")
    assert 'hash = "text"' in sidecar.read_text()
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + YAML_TABLE)
    status, err = check(run_cli, capsys)
    assert status == 1 and "[C14]" in err
    assert run_cli("migrate") == 0
    assert 'hash = "yaml"' in sidecar.read_text()
    assert check(run_cli, capsys)[0] == 0


@needs_yaml
def test_a_marker_qualifier_asks_for_yaml_without_a_table(repo, run_cli, capsys):
    (repo / "deploy").mkdir()
    (repo / VALUES).write_text(
        SRC.replace("# keystone: replicas", "# keystone(hash=yaml): replicas")
    )
    assert run_cli("add", "--id", "replicas", "-m", "why.") == 0
    assert 'hash = "yaml"' in (repo / SIDECAR).read_text()
    edit(repo, "  replicas: 3", "  replicas:    3")
    assert check(run_cli, capsys)[0] == 0


# --- sequences ---------------------------------------------------------------

RECORDS = """items:
  - name: a
    value: 1
  # keystone: second
  - name: b
    value: 2
ports:
  - 80
  # keystone: https
  - 443
"""


@pytest.fixture
def records(repo, run_cli):
    (repo / "r.yaml").write_text(RECORDS)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + YAML_TABLE)
    assert run_cli("add", "--id", "second", "-m", "why.") == 0
    assert run_cli("add", "--id", "https", "-m", "why.") == 0
    return repo


@needs_yaml
def test_a_list_item_gets_a_stable_selector(records):
    assert (
        'target = "r.yaml::items[name=b]"'
        in (records / "keystones" / "default" / "second.md").read_text()
    )
    assert (
        'target = "r.yaml::ports[1]"'
        in (records / "keystones" / "default" / "https.md").read_text()
    )


@needs_yaml
def test_an_item_edit_is_c3_and_a_sibling_edit_is_not(records, run_cli, capsys):
    path = records / "r.yaml"
    path.write_text(RECORDS.replace("value: 1", "value: 9"))
    assert check(run_cli, capsys)[0] == 0
    path.write_text(RECORDS.replace("value: 2", "value: 9"))
    status, err = check(run_cli, capsys)
    assert status == 1 and "[C3] keystone 'second'" in err


@needs_yaml
def test_reordering_items_keeps_a_keyed_selector_on_its_item(records, run_cli):
    (records / "r.yaml").write_text(
        "items:\n  # keystone: second\n  - name: b\n    value: 2\n  - name: a\n"
        "    value: 1\nports:\n  - 80\n  # keystone: https\n  - 443\n"
    )
    assert run_cli("check", "--all", "--no-base") == 0


@needs_yaml
def test_an_index_selector_is_recorded_under_its_stable_name(repo, run_cli):
    """`items[0]` is accepted, and the entry names the item by key so a later
    reorder does not retarget it."""
    unmarked = RECORDS.replace("  # keystone: second\n", "")
    (repo / "r.yaml").write_text(unmarked.replace("  # keystone: https\n", ""))
    args = ("add", "r.yaml::items[0]", "--id", "first", "--hash", "yaml", "-m", "w")
    assert run_cli(*args) == 0
    assert (
        'target = "r.yaml::items[name=a]"'
        in (repo / "keystones" / "default" / "first.md").read_text()
    )
    assert run_cli("check", "--all", "--no-base") == 0


@needs_yaml
def test_two_items_with_the_same_name_adopt_by_index(repo, run_cli, capsys):
    (repo / "r.yaml").write_text(RECORDS.replace("name: a", "name: b"))
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + YAML_TABLE)
    assert run_cli("add", "--id", "second", "-m", "why.") == 0
    assert "items[1]" in capsys.readouterr().out


@needs_yaml
def test_a_yaml_region_records_the_yaml_hasher(values, run_cli):
    """The YAML adapter hashes its own regions, so the text hasher id would
    be a lie about what produced the hash."""
    edit(
        values,
        "  # keystone: replicas\n  replicas: 3\n  ports: [80, 443]\n",
        "  # keystone:start: capacity\n  replicas: 3\n  ports: [80, 443]\n"
        "  # keystone:end\n",
    )
    assert run_cli("add", "--id", "capacity", "-m", "Capacity.") == 0
    sidecar = (values / "keystones" / "default" / "capacity.md").read_text()
    assert 'hash = "yaml"' in sidecar
    assert "keystones-yaml/2+pyyaml@" in sidecar
    assert run_cli("check", "--all", "--no-base") == 0


@needs_yaml
@pytest.mark.parametrize("style", ["|", "|-", ">"])
def test_a_block_scalar_node_passes_c5_right_after_add(repo, run_cli, capsys, style):
    """The stored slice is joined without its final newline, which clip
    chomping keeps, so the re-parsed value must not lose it."""
    (repo / "b.yaml").write_text(
        f"top:\n  # keystone(hash=yaml): spec\n  spec: {style}\n    - a: 1\n"
        "  other: x\n"
    )
    assert run_cli("add", "--id", "spec", "-m", "why.") == 0
    assert check(run_cli, capsys) == (0, "")


# --- selectors: scope, duplicates, configurable keys -------------------------

K8S = """a:
  # keystone(hash=yaml): pick
  - name: x
    v: 1
b:
  - name: dup
  - name: dup
"""


@needs_yaml
def test_duplicate_names_in_an_unrelated_list_are_not_c6(repo, run_cli, capsys):
    (repo / "k.yaml").write_text(K8S)
    assert run_cli("add", "--id", "pick", "-m", "why.") == 0
    assert check(run_cli, capsys) == (0, "")


@needs_yaml
def test_a_duplicate_in_the_keystoned_list_falls_back_to_position(
    repo, run_cli, capsys
):
    (repo / "k.yaml").write_text(K8S.replace("    v: 1\n", "    v: 1\n  - name: x\n"))
    assert run_cli("add", "--id", "pick", "-m", "why.") == 0
    out = capsys.readouterr().out
    assert "a[0]" in out and "position" in out
    assert run_cli("check", "--all", "--no-base") == 0


@needs_yaml
def test_add_on_an_ambiguous_selector_refuses_cleanly(repo, run_cli, capsys):
    (repo / "k.yaml").write_text(K8S.replace("  # keystone(hash=yaml): pick\n", ""))
    args = ("add", "k.yaml::b[name=dup]", "--id", "d", "--hash", "yaml", "-m", "w")
    assert run_cli(*args) == 1
    err = capsys.readouterr().err
    assert "more than once" in err and "b[0]" in err


@needs_yaml
def test_documents_are_separate_namespaces(repo, run_cli, capsys):
    (repo / "m.yaml").write_text(
        "spec:\n  replicas: 1\n---\nspec:\n  # keystone(hash=yaml): second\n"
        "  replicas: 2\n"
    )
    assert run_cli("add", "--id", "second", "-m", "why.") == 0
    sidecar = (repo / "keystones" / "default" / "second.md").read_text()
    assert 'target = "m.yaml::doc[1].spec.replicas"' in sidecar
    assert check(run_cli, capsys) == (0, "")
    path = repo / "m.yaml"
    path.write_text(path.read_text().replace("replicas: 2", "replicas: 3"))
    assert check(run_cli, capsys)[0] == 1


@needs_yaml
def test_selector_keys_are_configurable(repo, run_cli):
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + 'yaml_selector_keys = ["slug"]\n')
    (repo / "s.yaml").write_text(
        "items:\n  - slug: a\n    v: 1\n  # keystone(hash=yaml): b\n"
        "  - slug: b\n    v: 2\n"
    )
    assert run_cli("add", "--id", "b", "-m", "why.") == 0
    assert (
        'target = "s.yaml::items[slug=b]"'
        in (repo / "keystones" / "default" / "b.md").read_text()
    )


@needs_yaml
def test_an_explicit_selector_may_use_any_scalar_key(repo, run_cli):
    (repo / "s.yaml").write_text(
        "items:\n  - code: z\n    v: 1\n  - code: y\n    v: 2\n"
    )
    args = ("add", "s.yaml::items[code=y]", "--id", "y", "--hash", "yaml", "-m", "w")
    assert run_cli(*args) == 0
    assert (
        'target = "s.yaml::items[code=y]"'
        in (repo / "keystones" / "default" / "y.md").read_text()
    )
    assert run_cli("check", "--all", "--no-base") == 0


@needs_yaml
def test_fix_refreshes_the_stored_source_when_only_its_text_moved(
    gated, run_cli, capsys
):
    """Hashes equal, so nothing to review, but the sidecar should show the
    code as it is now written."""
    edit(gated, "  replicas: 3", "  replicas:    3")
    assert check(run_cli, capsys)[0] == 0
    assert run_cli("fix") == 0
    sidecar = (gated / SIDECAR).read_text()
    assert "replicas:    3" in sidecar
    assert "stored source refreshed" in sidecar


@needs_yaml
@pytest.mark.parametrize("style", ["|+", ">", ">-", ">+"])
def test_every_block_scalar_style_passes_c5_after_add(repo, run_cli, capsys, style):
    (repo / "b.yaml").write_text(
        f"top:\n  # keystone(hash=yaml): spec\n  spec: {style}\n    line\n\n"
        "  other: x\n"
    )
    assert run_cli("add", "--id", "spec", "-m", "why.") == 0
    assert check(run_cli, capsys) == (0, "")


@needs_yaml
def test_a_value_on_the_last_line_without_a_newline_passes_c5(repo, run_cli, capsys):
    (repo / "b.yaml").write_text("a:\n  # keystone(hash=yaml): k\n  b: 1")
    assert run_cli("add", "--id", "k", "-m", "why.") == 0
    assert check(run_cli, capsys) == (0, "")


@needs_yaml
def test_an_ambiguous_item_adopts_by_index_with_a_note(repo, run_cli, capsys):
    """The refusal suggested the index form; it has to be accepted."""
    (repo / "k.yaml").write_text(
        "b:\n  - name: dup\n  # keystone(hash=yaml): second\n  - name: dup\n    v: 2\n"
    )
    assert run_cli("add", "--id", "second", "-m", "why.") == 0
    out = capsys.readouterr().out
    assert "b[1]" in out and "position" in out
    assert (
        'target = "k.yaml::b[1]"'
        in (repo / "keystones" / "default" / "second.md").read_text()
    )
    assert run_cli("check", "--all", "--no-base") == 0
    args = ("add", "k.yaml::b[0]", "--id", "first", "--hash", "yaml", "-m", "w")
    assert run_cli(*args) == 0


@needs_yaml
def test_a_file_that_does_not_parse_is_one_finding_not_one_per_keystone(
    values, run_cli, capsys
):
    """C2 per entry and a kind finding per marker all say the same thing."""
    assert run_cli("add", "--id", "replicas", "-m", "why.") == 0
    assert run_cli("add", f"{VALUES}::policy", "--id", "budget", "-m", "SLO.") == 0
    path = values / VALUES
    path.write_text(path.read_text() + "broken: [unclosed\n")
    status, err = check(run_cli, capsys)
    assert status == 1
    assert err.count("[parse]") == 1
    assert "[C2]" not in err and "[kind]" not in err


# --- the end of a file, and keep chomping inside a mapping ---------------------------

EOF_CASES = {
    "mapping at eof": (
        "a:\n  # keystone(hash=yaml): k\n  b:\n    c: 1\n    d: 2",
        "a.b",
    ),
    "block scalar at eof": (
        "steps:\n  - name: s\n    # keystone(hash=yaml): k\n    run: |\n      echo hi",
        "steps[name=s].run",
    ),
    "keep chomp inside a mapping": (
        "a:\n  # keystone(hash=yaml): k\n  b:\n    t: |+\n      x\n\n  c: 1\n",
        "a.b",
    ),
}


@needs_yaml
@pytest.mark.parametrize("case", sorted(EOF_CASES))
def test_a_node_at_the_end_of_the_file_or_with_keep_chomping_passes_c5(
    repo, run_cli, capsys, case
):
    src, target = EOF_CASES[case]
    (repo / "e.yaml").write_text(src)
    assert run_cli("add", "--id", "k", "-m", "why.") == 0
    sidecar = next((repo / "keystones" / "default").glob("*.md")).read_text()
    assert f'target = "e.yaml::{target}"' in sidecar
    assert check(run_cli, capsys) == (0, "")


@needs_yaml
def test_an_entry_from_the_first_yaml_hasher_migrates_across(gated, run_cli):
    """A slice that moved under the new end-of-file handling must be proved
    across by migrate, not paged to its owner as drift."""
    sidecar = gated / SIDECAR
    sidecar.write_text(
        sidecar.read_text().replace("keystones-yaml/2+", "keystones-yaml/1+")
    )
    assert run_cli("migrate") == 0
    assert "keystones-yaml/2+" in sidecar.read_text()
    assert run_cli("check", "--all", "--no-base") == 0
