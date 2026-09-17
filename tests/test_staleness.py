"""Staleness is derived from git history, never from a self-reported date."""

import datetime as dt

import pytest

from keystones.staleness import DurationError, humanize, parse_duration


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("180d", dt.timedelta(days=180)),
        ("1w", dt.timedelta(weeks=1)),
        ("5d3h2m", dt.timedelta(days=5, hours=3, minutes=2)),
        ("6M", dt.timedelta(days=180)),
        ("1y", dt.timedelta(days=365)),
        ("30s", dt.timedelta(seconds=30)),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


def test_lowercase_m_is_minutes_uppercase_is_months():
    assert parse_duration("2m") == dt.timedelta(minutes=2)
    assert parse_duration("2M") == dt.timedelta(days=60)


@pytest.mark.parametrize("text", ["", "180", "d", "180x", "1d junk", "-5d"])
def test_invalid_durations_raise(text):
    with pytest.raises(DurationError):
        parse_duration(text)


def test_humanize():
    assert humanize(dt.timedelta(days=5)) == "5d"
    assert humanize(dt.timedelta(days=45)) == "1M15d"
    assert humanize(dt.timedelta(days=400)) == "1y1M"


def test_no_reviewed_field_is_written(repo, run_cli):
    """A stored date would be an unverified self-report, so there isn't one."""
    run_cli(
        "add",
        "billing/payout.py::compute_payout",
        "--id",
        "r",
        "--category",
        "finance",
        "-m",
        "why.",
        "--review-every",
        "180d",
    )
    text = (repo / "keystones" / "finance" / "r.md").read_text()
    assert "review_every" in text
    assert "reviewed =" not in text


def test_a_fresh_keystone_is_not_stale(repo, run_cli):
    run_cli(
        "add",
        "billing/payout.py::compute_payout",
        "--id",
        "r",
        "--category",
        "finance",
        "-m",
        "why.",
        "--review-every",
        "180d",
    )
    assert run_cli("check", "--all", "--no-base") == 0


def test_an_overdue_keystone_warns_but_does_not_fail(repo, run_cli):
    """Going stale is a prompt to re-read, not a reason to break the build."""
    run_cli(
        "add",
        "billing/payout.py::compute_payout",
        "--id",
        "r",
        "--category",
        "finance",
        "-m",
        "why.",
        "--review-every",
        "1s",
    )
    assert run_cli("check", "--all", "--no-base") == 0
    assert run_cli("list", "--stale") == 0


def test_an_invalid_budget_is_an_error(repo, run_cli):
    run_cli(
        "add",
        "billing/payout.py::compute_payout",
        "--id",
        "r",
        "--category",
        "finance",
        "-m",
        "why.",
        "--review-every",
        "180d",
    )
    path = repo / "keystones" / "finance" / "r.md"
    path.write_text(
        path.read_text().replace('review_every = "180d"', 'review_every = "soon"')
    )
    assert run_cli("check", "--all", "--no-base") == 1
