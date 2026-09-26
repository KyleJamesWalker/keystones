"""`keystone add` markers: written by hand, completed by `keystones add`."""

from pathlib import Path

import pytest

from keystones import markers

PAYOUT = "billing/payout.py"


@pytest.fixture
def answers(monkeypatch):
    """Feed prompts from a list; running out behaves like a closed stdin."""
    queue: list[str] = []
    prompts: list[str] = []

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)

    def _give(*values: str) -> list[str]:
        queue.extend(values)
        return prompts

    return _give


def mark(repo: Path, placeholder: str = "# keystone add") -> None:
    path = repo / PAYOUT
    path.write_text(
        path.read_text().replace(
            "def compute_payout", f"{placeholder}\ndef compute_payout"
        )
    )


def test_add_completes_a_pending_marker(repo, run_cli, answers):
    mark(repo)
    answers("payout-rounding", "finance", "GAAP rounding.", "180d", "")
    assert run_cli("add") == 0
    assert "# keystone(finance): payout-rounding\n" in (repo / PAYOUT).read_text()
    sidecar_text = (repo / "keystones/finance/payout-rounding.md").read_text()
    assert "GAAP rounding." in sidecar_text
    assert "180d" in sidecar_text
    assert run_cli("check", "--all") == 0


def test_check_fails_on_a_pending_marker(repo, run_cli, capsys):
    mark(repo)
    assert run_cli("check", "--all") == 1
    err = capsys.readouterr().err
    assert f"{PAYOUT}:4: error: [pending]" in err
    assert "compute_payout" in err


def test_a_category_in_the_marker_is_not_asked_for(repo, run_cli, answers):
    mark(repo, "# keystone(finance) add")
    prompts = answers("payout-rounding", "GAAP rounding.", "", "")
    assert run_cli("add") == 0
    assert not any("category" in p for p in prompts)
    assert "# keystone(finance): payout-rounding" in (repo / PAYOUT).read_text()


def test_bad_answers_are_asked_again(repo, run_cli, answers, capsys):
    mark(repo)
    answers(
        *("bad id!", "payout"),
        *("nope", "finance"),
        *("", "why"),
        *("soon", ""),
        *("x.py::y", ""),
    )
    assert run_cli("add") == 0
    err = capsys.readouterr().err
    assert "use letters, digits" in err
    assert "unknown category 'nope'" in err
    assert "needs a reason" in err
    assert "x.py::y" in err


def test_an_id_already_in_use_is_refused(repo, run_cli, answers, capsys):
    mark(repo)
    (repo / "notes.yaml").write_text("# keystone(file): taken\na: 1\n")
    answers("taken", "fresh", "default", "why", "", "")
    assert run_cli("add", "--id", "taken", "-m", "x") == 0
    assert run_cli("add") == 0
    assert "'taken' is already a keystone" in capsys.readouterr().err


def test_closing_stdin_leaves_the_file_alone(repo, run_cli, answers):
    mark(repo)
    before = (repo / PAYOUT).read_text()
    answers("payout-rounding")
    assert run_cli("add") == 1
    assert (repo / PAYOUT).read_text() == before
    assert not (repo / "keystones/default/payout-rounding.md").exists()


def test_a_pending_region_in_a_parserless_file(repo, run_cli, answers):
    (repo / "net.yaml").write_text(
        "a: 1\n# keystone:start(finance) add\ncidr: 10.0.0.0/8\n# keystone:end\n"
    )
    answers("cidrs", "Peering CIDRs.", "", "")
    assert run_cli("add") == 0
    assert "# keystone:start(finance): cidrs\n" in (repo / "net.yaml").read_text()
    assert run_cli("check", "--all") == 0


def test_every_pending_marker_is_completed_in_one_run(repo, run_cli, answers):
    mark(repo)
    (repo / "net.yaml").write_text("# keystone(file) add\na: 1\n")
    answers("payout", "default", "why", "", "", "net", "finance", "why", "", "")
    assert run_cli("add") == 0
    assert "# keystone(file, finance): net" in (repo / "net.yaml").read_text()
    assert run_cli("check", "--all") == 0


def test_a_pending_marker_in_a_string_is_not_one(repo, run_cli, capsys):
    path = repo / PAYOUT
    path.write_text(path.read_text() + 'DOC = """\n# keystone add\n"""\n')
    assert run_cli("check", "--all") == 0
    assert run_cli("add") == 1
    assert "no `keystone add` markers" in capsys.readouterr().err


def test_fix_is_not_blocked_by_a_pending_marker(repo, run_cli):
    mark(repo)
    assert run_cli("fix") == 0


def test_an_unknown_category_in_the_marker_is_refused(repo, run_cli, answers, capsys):
    mark(repo, "# keystone(nope) add")
    assert run_cli("add") == 1
    assert "unknown category 'nope'" in capsys.readouterr().err


def test_an_id_without_a_reason_is_a_usage_error(repo, run_cli):
    assert run_cli("add", f"{PAYOUT}::compute_payout", "--id", "x") == 2


@pytest.mark.parametrize(
    ("line", "category", "expected"),
    [
        ("# keystone add", "default", "# keystone: x"),
        ("    # keystone add", "finance", "    # keystone(finance): x"),
        ("-- keystone:start add", "finance", "-- keystone:start(finance): x"),
        (
            "# keystone(file, hash=text) add",
            "finance",
            "# keystone(file, finance, hash=text): x",
        ),
        ("# keystone(ops) add", "finance", "# keystone(ops): x"),
        ("/* keystone add */", "default", "/* keystone: x */"),
        ("<!-- keystone:start add -->", "default", "<!-- keystone:start: x -->"),
    ],
)
def test_complete_pending(line, category, expected):
    assert markers.complete_pending(line, "x", category) == expected
    assert markers.is_marker(expected)


def test_a_marker_with_the_id_add_is_not_pending():
    assert markers.PENDING_RE.search("# keystone: add") is None
    assert markers.probe_pending("# keystone: add\n")[1] == {}


def test_a_form_feed_does_not_shift_the_marker(repo, run_cli, answers):
    path = repo / PAYOUT
    path.write_text("\x0c\n" + path.read_text())
    mark(repo)
    answers("payout-rounding", "default", "why", "", "")
    assert run_cli("add") == 0
    assert "# keystone: payout-rounding\ndef compute_payout" in path.read_text()
