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
    assert regions[("infra", "vpc-peering-cidrs")] == (5, 7)


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
    """.ddl has no parser; .sql is a builtin tree-sitter language now."""
    (repo / "schema.ddl").write_text("-- keystone(file): schema\nSELECT 1;\n")
    assert run_cli("add", "--id", "schema", "-m", "Contract with the warehouse.") == 0
    assert run_cli("check", "--all", "--no-base") == 0
    (repo / "schema.ddl").write_text("-- keystone(file): schema\nSELECT 2;\n")
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


def test_a_keystone_end_line_is_not_a_point_marker():
    from keystones.markers import parse_point

    assert parse_point("# keystone:end", "x.py", 3) is None


def test_a_comment_edit_inside_a_region_is_comment_drift(infra, run_cli, capsys):
    edit_config(
        infra, "  exportRoutes: true", "  # routes leave the VPC\n  exportRoutes: true"
    )
    assert run_cli("fix") == 0, "a new comment line moves the region, no note"
    edit_config(infra, "# routes leave the VPC", "# routes leave the VPC, reviewed")
    capsys.readouterr()
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C4] comments inside keystone 'vpc-peering-cidrs' changed" in err
    assert "[C3]" not in err
    assert run_cli("fix") == 0, "a comment edit needs no note"
    assert run_cli("check", "--all", "--no-base") == 0


def test_a_region_records_the_text_hasher_whatever_the_file(repo, run_cli):
    """A Python region is hashed as text, so it must say so, or a text hasher
    bump would read as drift in code nobody touched."""
    (repo / "rules.py").write_text(
        "# keystone:start: limits\nLIMIT = 1  # per day\n# keystone:end\n"
    )
    assert run_cli("add", "--id", "limits", "-m", "Rate limit.") == 0
    sidecar = (repo / "keystones" / "default" / "limits.md").read_text()
    assert f'hasher = "{fallback.HASHER_ID}"' in sidecar
    (repo / "rules.py").write_text(
        "# keystone:start: limits\n# tightened 2026\nLIMIT = 1  # per day\n"
        "# keystone:end\n"
    )
    assert run_cli("check", "--all", "--no-base") == 1
    assert run_cli("fix") == 0


def test_a_file_with_no_known_comment_syntax_hashes_every_line(repo, run_cli, capsys):
    (repo / "notes.custom").write_text(
        "# keystone:start: n\n# note\nvalue\n# keystone:end\n"
    )
    assert run_cli("add", "--id", "n", "-m", "Why.") == 0
    (repo / "notes.custom").write_text(
        "# keystone:start: n\n# edited\nvalue\n# keystone:end\n"
    )
    capsys.readouterr()
    assert run_cli("check", "--all", "--no-base") == 1
    assert "[C3]" in capsys.readouterr().err


def test_a_region_hashed_before_comments_were_split_migrates_across(infra, run_cli):
    """The stored source still carries the comment, so the proof holds."""
    edit_config(
        infra, "  exportRoutes: true", "  # routes leave the VPC\n  exportRoutes: true"
    )
    assert run_cli("fix") == 0
    path = infra / "keystones" / "infra" / "vpc-peering-cidrs.md"
    raw = path.read_text()
    old_hasher = 'hasher = "keystones-text/1"'
    raw = raw.replace(f'hasher = "{fallback.HASHER_ID}"', old_hasher)
    semantic = raw.split('semantic = "', 1)[1].split('"', 1)[0]
    raw = raw.replace(
        f'semantic = "{semantic}"', 'semantic = "sha256:' + "0" * 64 + '"'
    )
    path.write_text(raw)
    assert run_cli("migrate") == 0
    assert f'hasher = "{fallback.HASHER_ID}"' in path.read_text()
    assert run_cli("check", "--all", "--no-base") == 0


@pytest.mark.parametrize(
    ("path", "leader"),
    [
        ("a.yaml", "#"),
        ("a.sql", "--"),
        ("a.py", "#"),
        ("a.java", "//"),
        ("a.custom", None),
    ],
)
def test_comment_leaders_by_file_type(path, leader):
    assert fallback.leader_for(path) == leader


# --- a region that only moved ------------------------------------------------


def test_a_region_that_only_moved_is_a_notice_not_drift(infra, run_cli, capsys):
    """Every unrelated edit above a region shifts its lines; paging the owner
    for that is what gets regions removed."""
    edit_config(infra, "apiVersion: v1", "# added\n# lines\napiVersion: v1")
    capsys.readouterr()
    assert run_cli("check", "--all", "--no-base") == 0
    err = capsys.readouterr().err
    assert "notice: [C3] keystone 'vpc-peering-cidrs' moved" in err
    assert "net.yaml#L7-L9" in err
    assert "error" not in err


def test_a_moved_region_whose_body_changed_is_still_c3(infra, run_cli, capsys):
    edit_config(infra, "apiVersion: v1", "# added\napiVersion: v1")
    edit_config(infra, "exportRoutes: true", "exportRoutes: false")
    capsys.readouterr()
    assert run_cli("check", "--all", "--no-base") == 1
    assert "error: [C3]" in capsys.readouterr().err


def test_the_staged_hook_stays_quiet_on_a_pure_move(infra, run_cli, capsys):
    edit_config(infra, "apiVersion: v1", "# added\napiVersion: v1")
    capsys.readouterr()
    assert run_cli("check", "--warn-only", "net.yaml") == 0
    assert "error" not in capsys.readouterr().err


def test_a_moved_region_whose_body_changed_says_changed(infra, run_cli, capsys):
    """The cause is the body edit; the shift is incidental."""
    edit_config(infra, "apiVersion: v1", "# added\napiVersion: v1")
    edit_config(infra, "exportRoutes: true", "exportRoutes: false")
    capsys.readouterr()
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C3] keystone 'vpc-peering-cidrs' changed" in err
    assert "no longer covers" not in err


def test_a_block_comment_edit_inside_a_sql_region_is_c4(repo, run_cli, capsys):
    """Only `--` lines were comments to the text hasher; `/* */` was code."""
    (repo / "q.ddl").write_text(
        "-- keystone:start: q\n/* the contract\n   with billing */\nselect 1 as x\n"
        "-- keystone:end\n"
    )
    assert run_cli("add", "--id", "q", "-m", "why.") == 0
    path = repo / "q.ddl"
    path.write_text(path.read_text().replace("with billing", "with finance"))
    capsys.readouterr()
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C4]" in err and "[C3]" not in err


def test_a_text_two_region_migrates_to_text_three(infra, run_cli):
    path = infra / "keystones" / "infra" / "vpc-peering-cidrs.md"
    path.write_text(path.read_text().replace("keystones-text/3", "keystones-text/3"))
    assert run_cli("migrate") == 0
    assert "keystones-text/3" in path.read_text()
