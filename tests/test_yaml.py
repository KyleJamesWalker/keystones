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
    assert "keystones-yaml/1+pyyaml@" in sidecar
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
