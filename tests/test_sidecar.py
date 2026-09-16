import pytest

from keystones import sidecar
from keystones.models import Entry


def make_entry(**kwargs) -> Entry:
    base = dict(
        id="payout-rounding",
        category="finance",
        target="billing/payout.py::compute_payout",
        hasher="keystones-ast/1",
        semantic="sha256:aa",
        text="sha256:bb",
        why="GAAP rounding, see the 2026 sign-off.",
        source="def compute_payout(amount):\n    return amount",
        history=["2026-09-16 - initial keystone. kyle"],
    )
    base.update(kwargs)
    return Entry(**base)


def test_roundtrip(tmp_path):
    entry = make_entry(review_every="180d", depends=["billing/rates.py::BASE_RATE"])
    path = tmp_path / "payout-rounding.md"
    sidecar.write(path, entry)
    back = sidecar.parse(path, "finance")
    for field in (
        "target",
        "hasher",
        "semantic",
        "text",
        "review_every",
        "depends",
        "why",
        "source",
        "history",
    ):
        assert getattr(back, field) == getattr(entry, field), field


def test_optional_fields_are_omitted_when_empty(tmp_path):
    path = tmp_path / "x.md"
    sidecar.write(path, make_entry())
    raw = path.read_text()
    assert "review_every" not in raw
    assert "depends" not in raw


def test_missing_metadata_block_is_an_error(tmp_path):
    path = tmp_path / "broken.md"
    path.write_text("# broken\n\nno metadata here\n")
    with pytest.raises(sidecar.SidecarError, match="no ```toml"):
        sidecar.parse(path, "default")


def test_incomplete_metadata_is_an_error(tmp_path):
    path = tmp_path / "broken.md"
    path.write_text('# broken\n\n```toml\ntarget = "a.py::f"\n```\n')
    with pytest.raises(sidecar.SidecarError, match="missing"):
        sidecar.parse(path, "default")


def test_index_groups_by_category():
    rendered = sidecar.render_index(
        [make_entry(id="a", category="default"), make_entry(id="b", category="finance")]
    )
    assert "## default" in rendered and "## finance" in rendered
    assert "(default/a.md)" in rendered and "(finance/b.md)" in rendered


def test_index_handles_no_entries():
    assert "No keystones yet." in sidecar.render_index([])
