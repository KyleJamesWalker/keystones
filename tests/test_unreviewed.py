"""C18: a path rewritten without review cannot hold a keystone."""

import pytest

MIRROR = "deploy/mirror/app.yaml"
BODY = "# keystone(file): app\nimage: app:1\n"


@pytest.fixture
def gitops(repo):
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + 'unreviewed = ["deploy/mirror/**"]\n')
    (repo / "deploy" / "mirror").mkdir(parents=True)
    (repo / MIRROR).write_text(BODY)
    return repo


def test_add_refuses_a_path_rewritten_without_review(gitops, run_cli, capsys):
    assert run_cli("add", "--id", "app", "-m", "Pinned image.") == 1
    err = capsys.readouterr().err
    assert "[C18]" in err
    assert "deploy/mirror/**" in err
    assert not (gitops / "keystones" / "default" / "app.md").exists()


def test_a_marker_in_such_a_path_is_c18(gitops, run_cli, capsys):
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert f"{MIRROR}:1: error: [C18]" in err
    assert "[C1]" not in err, "C18 says why; an orphan report would only confuse"


def test_the_staged_hook_sees_it_too(gitops, run_cli, capsys):
    assert run_cli("check", MIRROR) == 1
    assert "[C18]" in capsys.readouterr().err


def test_an_entry_whose_target_moved_under_the_list_is_c18(repo, run_cli, capsys):
    (repo / "deploy" / "mirror").mkdir(parents=True)
    (repo / MIRROR).write_text(BODY)
    assert run_cli("add", "--id", "app", "-m", "Pinned image.") == 0
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + 'unreviewed = ["deploy/**"]\n')
    assert run_cli("check", "--all", "--no-base") == 1
    err = capsys.readouterr().err
    assert "[C18]" in err
    assert err.count("[C18]") == 1, "the marker and its entry are one finding"


def test_unreviewed_must_be_a_list_of_patterns(repo, run_cli):
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + 'unreviewed = "deploy"\n')
    assert run_cli("check", "--all", "--no-base") == 2


def test_a_path_outside_the_list_is_untouched(gitops, run_cli):
    (gitops / "app.yaml").write_text(BODY)
    (gitops / MIRROR).write_text("image: app:1\n")
    assert run_cli("add", "--id", "app", "-m", "Pinned image.") == 0
    assert run_cli("check", "--all", "--no-base") == 0


def test_adopting_ignores_the_copy_under_the_list(gitops, run_cli, capsys):
    """A bot mirror carries the same marker; only the reviewed file counts."""
    (gitops / "app.yaml").write_text(BODY)
    assert run_cli("add", "--id", "app", "-m", "Pinned image.") == 0, (
        capsys.readouterr().err
    )
    sidecar = (gitops / "keystones" / "default" / "app.md").read_text()
    assert 'target = "app.yaml"' in sidecar
