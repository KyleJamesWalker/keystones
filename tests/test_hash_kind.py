"""The `hash=` marker qualifier: which basis a keystone is gated on."""

import pytest

from keystones import markers as mg
from keystones.models import Scope


def parse(line: str):
    return mg.parse_point(line, "m.sql", 1)


def test_no_qualifier_leaves_the_kind_unset():
    """Auto-detect stays the default, so every existing marker is untouched."""
    assert parse("-- keystone: rev").hash_kind is None
    assert parse("-- keystone(finance): rev").hash_kind is None


def test_a_hash_qualifier_is_read():
    assert parse("-- keystone(hash=text): rev").hash_kind == "text"
    assert parse("-- keystone(finance, hash=sql): rev").hash_kind == "sql"


def test_the_hash_qualifier_is_not_mistaken_for_a_category():
    marker = parse("-- keystone(finance, hash=text): rev")
    assert marker.category == "finance"
    assert marker.scope is Scope.NODE


def test_a_category_may_be_called_text():
    """Keying the qualifier is what makes this unambiguous."""
    marker = parse("-- keystone(text): rev")
    assert marker.category == "text" and marker.hash_kind is None


def test_it_combines_with_file_scope():
    marker = parse("-- keystone(file, hash=text): schema")
    assert marker.scope is Scope.FILE and marker.hash_kind == "text"


def test_regions_carry_it_too():
    marker = mg.parse_region_start("-- keystone:start(f, hash=text): r", "m.sql", 1)
    assert marker.hash_kind == "text" and marker.category == "f"


def test_an_empty_hash_qualifier_is_refused():
    with pytest.raises(mg.MarkerError, match="hash="):
        parse("-- keystone(hash=): rev")


def test_two_hash_qualifiers_are_refused():
    with pytest.raises(mg.MarkerError, match="hash="):
        parse("-- keystone(hash=text, hash=sql): rev")


# --- selecting an adapter by kind -------------------------------------------

from keystones import adapters  # noqa: E402
from keystones.adapters import fallback  # noqa: E402
from keystones.adapters import treesitter as ts  # noqa: E402

needs_extra = pytest.mark.skipif(not ts.available(), reason="needs the 'all' extra")


@needs_extra
def test_kinds_for_a_parsed_extension_offer_text_too():
    assert set(adapters.kinds_for("m.sql")) == {"sql", "text"}


def test_kinds_for_an_unparsed_extension_are_text_only():
    assert adapters.kinds_for("net.yaml") == ("text",)


@needs_extra
def test_a_kind_picks_its_adapter():
    assert adapters.for_kind("m.sql", "text") is fallback
    assert adapters.for_kind("m.sql", "sql") is ts


def test_an_unknown_kind_names_what_is_available():
    with pytest.raises(adapters.UnknownKind, match="text"):
        adapters.for_kind("net.yaml", "sql")


# --- the sidecar records the choice -----------------------------------------

from keystones.models import Entry  # noqa: E402
from keystones.sidecar import parse as parse_sidecar  # noqa: E402
from keystones.sidecar import render  # noqa: E402


def _entry(**over) -> Entry:
    base = dict(
        id="rev",
        category="finance",
        target="m.sql#L2-L3",
        hash="text",
        hasher="keystones-text/1",
        semantic="sha256:a",
        text="sha256:a",
    )
    return Entry(**{**base, **over})


def test_the_sidecar_records_the_hash_kind():
    assert 'hash = "text"' in render(_entry())


def test_the_hash_kind_survives_a_round_trip(tmp_path):
    path = tmp_path / "rev.md"
    path.write_text(render(_entry(hash="sql")))
    assert parse_sidecar(path, "finance").hash == "sql"


def test_an_entry_written_before_this_field_still_parses(tmp_path):
    """Nothing is released yet, but a missing field must not be a crash."""
    path = tmp_path / "rev.md"
    path.write_text(render(_entry()).replace('hash = "text"\n', ""))
    assert parse_sidecar(path, "finance").hash == ""


# --- end to end: choosing a basis for a file the grammar cannot read --------

DBT_MODEL = """{{ config(materialized='incremental') }}
select
    order_id,
-- keystone:start(finance{q}): rev-rec
    amount * 0.97 as net_revenue
-- keystone:end
from {{{{ ref('orders') }}}}
"""

CLEAN_SQL = """-- keystone({q}): net_revenue
create or replace view net_revenue as
select order_id, amount * 0.97 as net from orders;
"""


def write(repo, name, template, q=""):
    path = repo / name
    path.write_text(template.format(q=q))
    return path


@needs_extra
def test_an_unreadable_file_without_a_kind_says_to_choose(repo, run_cli, capsys):
    write(repo, "rev.sql", DBT_MODEL)
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "hash=" in err and "text" in err


@needs_extra
def test_an_unreadable_file_with_hash_text_just_works(repo, run_cli):
    """A dbt region, gated on normalised text because someone said so."""
    write(repo, "rev.sql", DBT_MODEL, q=", hash=text")
    assert run_cli("add", "--id", "rev-rec", "-m", "GAAP rev rec.") == 0
    assert run_cli("check", "--all", "--no-base") == 0
    sidecar = (repo / "keystones" / "finance" / "rev-rec.md").read_text()
    assert 'hash = "text"' in sidecar
    assert 'hasher = "keystones-text/1"' in sidecar


@needs_extra
def test_hash_text_still_gates_a_change(repo, run_cli):
    path = write(repo, "rev.sql", DBT_MODEL, q=", hash=text")
    run_cli("add", "--id", "rev-rec", "-m", "GAAP rev rec.")
    path.write_text(path.read_text().replace("0.97", "0.95"))
    assert run_cli("check", "--all", "--no-base") == 1


@needs_extra
def test_hash_text_on_a_file_that_parses_is_a_deliberate_downgrade(repo, run_cli):
    write(repo, "v.sql", CLEAN_SQL, q="finance, file, hash=text")
    assert run_cli("add", "--id", "net_revenue", "-m", "Rev rec.") == 0
    sidecar = (repo / "keystones" / "finance" / "net_revenue.md").read_text()
    assert 'hash = "text"' in sidecar


@needs_extra
def test_hash_text_cannot_attach_to_a_node(repo, run_cli, capsys):
    """Text hashing has no AST, so a point marker under it is incoherent."""
    write(repo, "v.sql", CLEAN_SQL, q="finance, hash=text")
    assert run_cli("check", "--all", "--no-base") == 1
    assert "no nodes" in capsys.readouterr().err


@needs_extra
def test_a_clean_file_needs_no_qualifier(repo, run_cli):
    write(repo, "v.sql", CLEAN_SQL, q="finance")
    assert run_cli("add", "--id", "net_revenue", "-m", "Rev rec.") == 0
    sidecar = (repo / "keystones" / "finance" / "net_revenue.md").read_text()
    assert 'hash = "sql"' in sidecar


@needs_extra
def test_add_refuses_to_choose_for_you(repo, run_cli, capsys):
    write(repo, "rev.sql", DBT_MODEL)
    assert run_cli("add", "--id", "rev-rec", "-m", "GAAP rev rec.") == 1
    err = capsys.readouterr().err
    assert "hash=text" in err, "adopting an existing marker means editing it"


@needs_extra
def test_add_hash_text_writes_the_qualifier_into_the_source(repo, run_cli):
    (repo / "v.sql").write_text(CLEAN_SQL.format(q="").split("\n", 1)[1])
    assert (
        run_cli(
            "add",
            "v.sql",
            "--id",
            "net_revenue",
            "--category",
            "finance",
            "--hash",
            "text",
            "-m",
            "Rev rec.",
        )
        == 0
    )
    assert "hash=text" in (repo / "v.sql").read_text()


@needs_extra
def test_a_recorded_kind_is_not_re_derived_from_the_path(repo, run_cli):
    """The point of recording it: the file becoming readable must not flip it.

    Strip the templating and rev.sql parses as SQL. Re-deriving the basis from
    the path would move this keystone onto the AST hasher and report C13 on a
    region nobody touched.
    """
    path = write(repo, "rev.sql", DBT_MODEL, q=", hash=text")
    run_cli("add", "--id", "rev-rec", "-m", "GAAP rev rec.")
    de_jinja = (
        path.read_text()
        .replace("{{ config(materialized='incremental') }}\n", "")
        .replace("from {{ ref('orders') }}", "from orders")
    )
    path.write_text(de_jinja)
    assert run_cli("check", "--all", "--no-base") == 0, "the region body is untouched"
    assert (
        'hash = "text"' in (repo / "keystones" / "finance" / "rev-rec.md").read_text()
    )

    path.write_text(de_jinja.replace("0.97", "0.95"))
    assert run_cli("check", "--all", "--no-base") == 1, "now the body did change"


@needs_extra
def test_marker_and_sidecar_must_agree_on_the_kind(repo, run_cli, capsys):
    write(repo, "v.sql", CLEAN_SQL, q="finance")
    run_cli("add", "--id", "net_revenue", "-m", "Rev rec.")
    sidecar = repo / "keystones" / "finance" / "net_revenue.md"
    sidecar.write_text(sidecar.read_text().replace('hash = "sql"', 'hash = "text"'))
    assert run_cli("check", "--all", "--no-base") == 1
    assert "[C14]" in capsys.readouterr().err


# --- the hash qualifier's spelling -------------------------------------------


def test_hash_qualifier_tolerates_spaces_around_the_equals():
    marker = mg.parse_point("-- keystone(finance, hash = text): x", "a.sql", 1)
    assert marker.hash_kind == "text"
    assert marker.category == "finance"


def test_hash_qualifier_with_a_space_and_no_value_is_still_an_error():
    with pytest.raises(mg.MarkerError):
        mg.parse_point("-- keystone(hash = ): x", "a.sql", 1)
