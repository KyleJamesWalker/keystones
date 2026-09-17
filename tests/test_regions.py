"""Region markers and the fallback adapter: coverage for files with no parser."""

import subprocess
from pathlib import Path

import pytest

from keystones.adapters import fallback
from keystones.markers import RegionError, scan_lines

# A type no adapter parses, which is the whole point of this module.
CONFIG = """apiVersion: v1
kind: ConfigMap

# keystone:start(infra): vpc-peering-cidrs
peering:
  peerNetwork: prod-vpc
  exportRoutes: true
# keystone:end

logging:
  bucket: logs
"""


@pytest.fixture
def infra(repo, run_cli):
    (repo / "net.yaml").write_text(CONFIG)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            'categories = ["default", "finance"]',
            'categories = ["default", "finance", "infra"]',
        )
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "config"], cwd=repo, check=True)
    assert run_cli("add", "--id", "vpc-peering-cidrs", "-m", "Peering CIDRs.") == 0
    return repo


def edit_config(repo: Path, old: str, new: str) -> None:
    path = repo / "net.yaml"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_region_body_excludes_the_marker_lines():
    _, regions = scan_lines("net.yaml", CONFIG)
    assert regions["vpc-peering-cidrs"] == (5, 7)


def test_region_marker_carries_its_category():
    found, _ = scan_lines("net.yaml", CONFIG)
    assert found[0].category == "infra"


def test_unclosed_region_is_an_error():
    with pytest.raises(RegionError, match="never closed"):
        scan_lines("x.tf", "# keystone:start: a\nbody\n")


def test_nested_region_is_an_error():
    with pytest.raises(RegionError, match="before"):
        scan_lines("x.tf", "# keystone:start: a\n# keystone:start: b\n# keystone:end\n")


def test_end_without_start_is_an_error():
    with pytest.raises(RegionError, match="no matching start"):
        scan_lines("x.tf", "# keystone:end\n")


@pytest.mark.parametrize(
    "line",
    [
        "# keystone:start: a",
        "// keystone:start: a",
        "-- keystone:start: a",
        "<!-- keystone:start: a -->",
        "/* keystone:start: a */",
    ],
)
def test_comment_syntaxes(line):
    found, _ = scan_lines("x", f"{line}\nbody\n# keystone:end\n")
    assert found[0].id == "a"


def test_normalise_collapses_whitespace_noise():
    assert fallback.normalise("a  \n\n\n\nb\r\n\n") == "a\n\nb"


def test_adopting_a_region_records_its_line_range(infra):
    sidecar = (infra / "keystones" / "infra" / "vpc-peering-cidrs.md").read_text()
    assert "net.yaml#L5-L7" in sidecar
    assert "exportRoutes" in sidecar, "the stored source is the region body"


def test_clean_region_passes(infra, run_cli):
    assert run_cli("check", "--all", "--no-base") == 0


def test_editing_inside_the_region_trips_the_gate(infra, run_cli):
    edit_config(infra, "exportRoutes: true", "exportRoutes: false")
    assert run_cli("check", "--all", "--no-base") == 1


def test_editing_outside_the_region_does_not(infra, run_cli):
    """This is the whole point of regions over whole-file keystones."""
    edit_config(infra, "bucket: logs", "bucket: logs-v2")
    assert run_cli("check", "--all", "--no-base") == 0


def test_shifting_the_region_down_is_an_auto_fixable_move(infra, run_cli):
    edit_config(infra, "apiVersion: v1", "# added\n# lines\napiVersion: v1")
    assert run_cli("fix") == 0, "a pure move needs no note"
    assert run_cli("check", "--all", "--no-base") == 0
    sidecar = (infra / "keystones" / "infra" / "vpc-peering-cidrs.md").read_text()
    assert "net.yaml#L7-L9" in sidecar


def test_whole_file_keystone_on_an_unparsed_file(repo, run_cli):
    (repo / "schema.sql").write_text("-- keystone(file): schema\nSELECT 1;\n")
    assert run_cli("add", "--id", "schema", "-m", "Contract with the warehouse.") == 0
    assert run_cli("check", "--all", "--no-base") == 0
    (repo / "schema.sql").write_text("-- keystone(file): schema\nSELECT 2;\n")
    assert run_cli("check", "--all", "--no-base") == 1


def test_point_marker_without_a_parser_is_a_helpful_error(repo, run_cli):
    (repo / "app.conf").write_text("# keystone: no-parser\nsetting = 1\n")
    assert run_cli("check", "--all", "--no-base") == 1


def test_files_without_the_word_are_not_scanned(repo, run_cli):
    """The pre-filter is what keeps a whole-repo scan cheap."""
    from keystones.config import load
    from keystones.discovery import source_files

    (repo / "big.txt").write_text("nothing interesting\n" * 100)
    (repo / "marked.txt").write_text("# keystone(file): txt\ncontent\n")
    files = source_files(load(repo))
    assert "marked.txt" in files
    assert "big.txt" not in files


def test_binary_files_are_skipped(repo, run_cli):
    (repo / "blob.bin").write_bytes(b"keystone\x00\xff\xfe binary")
    assert run_cli("check", "--all", "--no-base") == 0


def test_ignore_file_directive_opts_a_file_out(repo, run_cli):
    """Documentation that shows markers must not be treated as carrying them."""
    (repo / "GUIDE.md").write_text(
        "<!-- keystones: ignore-file -->\n\nExample:\n\n    # keystone: example-id\n"
    )
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_marker_in_a_python_string_is_not_a_region(repo, run_cli):
    """The lexer view is what keeps test fixtures from registering markers."""
    (repo / "fixture.py").write_text(
        'SAMPLE = """\n# keystone:start(finance): not-real\nbody\n# keystone:end\n"""\n'
    )
    assert run_cli("check", "--all", "--no-base") == 0
