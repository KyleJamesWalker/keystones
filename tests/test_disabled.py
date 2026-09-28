"""C16: a keystoned test switched off from outside its own hash."""

import pytest

GUARD = """import pytest


class TestRounding:
    # keystone: guard
    def test_guard(self):
        assert 1
"""

SIDECAR = "keystones/default/guard.md"


@pytest.fixture
def guarded(repo, run_cli):
    (repo / "test_rounding.py").write_text(GUARD)
    assert run_cli("add", "--id", "guard", "-m", "Guards rounding.") == 0
    return repo


def edit(repo, old, new):
    path = repo / "test_rounding.py"
    path.write_text(path.read_text().replace(old, new, 1))


def check(run_cli, capsys, *args):
    capsys.readouterr()
    status = run_cli("check", *(args or ("--all", "--no-base")))
    return status, capsys.readouterr().err


@pytest.mark.parametrize(
    ("old", "new", "named"),
    [
        (
            "class TestRounding:",
            "@pytest.mark.skip(reason='flaky')\nclass TestRounding:",
            "pytest.mark.skip(reason='flaky') on class TestRounding",
        ),
        (
            "class TestRounding:\n",
            "class TestRounding:\n    pytestmark = pytest.mark.xfail\n\n",
            "pytest.mark.xfail on class TestRounding pytestmark",
        ),
        (
            "import pytest\n",
            "import pytest\n\n"
            "pytestmark = [pytest.mark.slow, pytest.mark.skipif(True, reason='x')]\n",
            "pytest.mark.skipif(True, reason='x') on module pytestmark",
        ),
        (
            "import pytest\n",
            "import pytest\nimport unittest as ut\n",
            None,
        ),
        (
            "import pytest\n\n\nclass TestRounding:",
            "import pytest\n\nskip = pytest.mark.skip\n\n\n@skip\nclass TestRounding:",
            "pytest.mark.skip on class TestRounding",
        ),
        (
            "import pytest\n",
            "import pytest\n\npytest.skip('all of it', allow_module_level=True)\n",
            "pytest.skip('all of it', allow_module_level=True) at module level",
        ),
        (
            "class TestRounding:\n",
            "class TestRounding:\n    __test__ = False\n\n",
            "__test__ = False on class TestRounding",
        ),
        (
            "import pytest\n",
            "import pytest\n\n__test__ = False\n",
            "__test__ = False at module level",
        ),
        (
            "import pytest\n\n\nclass TestRounding:",
            "import pytest\nimport sys\n\n"
            "off = pytest.mark.skipif(sys.platform != 'x', "
            "reason='r')\n\n\n@off\nclass TestRounding:",
            "pytest.mark.skipif(sys.platform != 'x', reason='r') on class TestRounding",
        ),
        (
            "import pytest\n",
            "import pytest\nimport sys\n\nif sys.platform == 'x':\n"
            "    pytest.skip('not here', allow_module_level=True)\n",
            "pytest.skip('not here', allow_module_level=True) at module level "
            "under `if sys.platform == 'x'`",
        ),
        (
            "class TestRounding:\n",
            "class TestRounding:\n    __test__ = 0\n\n",
            "__test__ = 0 on class TestRounding",
        ),
        (
            "import pytest\n",
            "import pytest\n",
            None,
        ),
    ],
    ids=[
        "class-decorator",
        "class-pytestmark",
        "module-pytestmark",
        "no-disabler",
        "module-alias",
        "module-level-skip",
        "class-__test__",
        "module-__test__",
        "call-valued-alias",
        "conditional-module-skip",
        "falsy-__test__",
        "nothing",
    ],
)
def test_a_skip_from_outside_the_function_is_c16(
    guarded, run_cli, capsys, old, new, named
):
    edit(guarded, old, new)
    status, err = check(run_cli, capsys)
    if named is None:
        assert status == 0, err
        return
    assert status == 1
    assert f"[C16] keystone 'guard' is switched off by {named}" in err
    assert "[C3]" not in err, "no byte of the function moved"


def test_an_aliased_unittest_skip_is_recognised(guarded, run_cli, capsys):
    edit(guarded, "import pytest\n", "import pytest\nimport unittest as ut\n")
    edit(guarded, "class TestRounding:", "@ut.skip('later')\nclass TestRounding:")
    status, err = check(run_cli, capsys)
    assert status == 1
    assert "unittest.skip('later') on class TestRounding" in err


def test_a_non_pytest_disabler_counts_once_configured(guarded, run_cli, capsys):
    edit(
        guarded,
        "import pytest\n",
        "import pytest\nfrom acme.testing import quarantine\n",
    )
    edit(guarded, "class TestRounding:", "@quarantine\nclass TestRounding:")
    assert check(run_cli, capsys)[0] == 0, "unknown to keystones until configured"
    pyproject = guarded / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text() + 'disabling_decorators = ["acme.testing.quarantine"]\n'
    )
    status, err = check(run_cli, capsys)
    assert status == 1
    assert "acme.testing.quarantine on class TestRounding" in err


def test_an_owner_reviewed_skip_stays_green_until_it_changes(guarded, run_cli, capsys):
    edit(guarded, "class TestRounding:", "@pytest.mark.skip\nclass TestRounding:")
    assert run_cli("fix", "--id", "guard") == 1, "switching a guard off needs a note"
    assert (
        run_cli("fix", "--id", "guard", "-m", "Quarantined until the fix lands.") == 0
    )
    assert 'disabled_by = ["pytest.mark.skip on class TestRounding"]' in (
        (guarded / SIDECAR).read_text()
    )
    assert check(run_cli, capsys)[0] == 0

    edit(guarded, "@pytest.mark.skip\n", "")
    status, err = check(run_cli, capsys)
    assert status == 1
    assert "is no longer switched off by pytest.mark.skip on class TestRounding" in err
    assert run_cli("fix", "--id", "guard", "-m", "Back on.") == 0
    assert "disabled_by" not in (guarded / SIDECAR).read_text()
    assert check(run_cli, capsys)[0] == 0


def test_the_staged_hook_sees_it_and_warns(guarded, run_cli, capsys):
    edit(guarded, "class TestRounding:", "@pytest.mark.skip\nclass TestRounding:")
    status, err = check(run_cli, capsys, "--warn-only", "test_rounding.py")
    assert status == 0
    assert "warning: [C16]" in err


def test_disabling_decorators_must_be_a_list_of_names(repo, run_cli):
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + 'disabling_decorators = "flaky"\n')
    assert run_cli("check", "--all", "--no-base") == 2


def test_a_skip_on_the_function_itself_is_c3_alone(guarded, run_cli, capsys):
    """Its own decorators are inside the hash, so C3 already says it changed."""
    edit(guarded, "    def test_guard", "    @pytest.mark.skip\n    def test_guard")
    status, err = check(run_cli, capsys)
    assert status == 1
    assert "[C3]" in err
    assert "[C16]" not in err


def test_flipping_a_skipif_condition_is_c16(guarded, run_cli, capsys):
    """The mark name alone would read `skipif(False)` and `skipif(True)` alike."""
    edit(
        guarded,
        "class TestRounding:",
        "@pytest.mark.skipif(False, reason='x')\nclass TestRounding:",
    )
    assert run_cli("fix", "--id", "guard", "-m", "Guarded by a flag.") == 0
    assert check(run_cli, capsys)[0] == 0
    edit(guarded, "skipif(False,", "skipif(True,")
    status, err = check(run_cli, capsys)
    assert status == 1
    assert "switched off by pytest.mark.skipif(True, reason='x')" in err


def test_a_test_switched_off_after_its_class_is_c16(guarded, run_cli, capsys):
    path = guarded / "test_rounding.py"
    path.write_text(path.read_text() + "\n\nTestRounding.__test__ = False\n")
    status, err = check(run_cli, capsys)
    assert status == 1
    assert "__test__ = False on class TestRounding" in err


def test_an_alias_on_the_function_itself_is_c16_when_flipped(guarded, run_cli, capsys):
    """The function's hash holds only the name `off`; the alias's arguments
    live at module level, outside it."""
    edit(
        guarded, "import pytest\n", "import pytest\n\noff = pytest.mark.skipif(False)\n"
    )
    edit(guarded, "    def test_guard", "    @off\n    def test_guard")
    assert run_cli("fix", "--id", "guard", "-m", "Aliased mark.") == 0
    assert check(run_cli, capsys)[0] == 0
    edit(guarded, "skipif(False)", "skipif(True)")
    status, err = check(run_cli, capsys)
    assert status == 1
    assert "[C16]" in err
    assert "pytest.mark.skipif(True) via off on TestRounding.test_guard" in err


def test_ast_warnings_in_a_keystoned_file_stay_quiet(repo, run_cli, recwarn):
    path = repo / "billing" / "payout.py"
    path.write_text('# keystone: p\ndef compute_payout(x):\n    return "\\\\d"\n')
    run_cli("add", "--id", "p", "-m", "w")
    run_cli("check", "--all", "--no-base")
    assert not [w for w in recwarn if issubclass(w.category, SyntaxWarning)]


def test_a_condition_held_in_a_module_name_is_part_of_the_mark(
    guarded, run_cli, capsys
):
    edit(guarded, "import pytest\n", "import pytest\n\nSKIP = False\n")
    edit(
        guarded,
        "class TestRounding:",
        "@pytest.mark.skipif(SKIP, reason='r')\nclass TestRounding:",
    )
    assert run_cli("fix", "--id", "guard", "-m", "Guarded by a flag.") == 0
    assert "SKIP=False" in (guarded / SIDECAR).read_text()
    assert check(run_cli, capsys)[0] == 0
    edit(guarded, "SKIP = False", "SKIP = True")
    status, err = check(run_cli, capsys)
    assert status == 1 and "[C16]" in err and "SKIP=True" in err


def test_a_plain_alias_on_the_test_is_recorded_so_it_cannot_turn_into_a_skip(
    guarded, run_cli, capsys
):
    edit(guarded, "import pytest\n", "import pytest\n\noff = pytest.mark.slow\n")
    edit(guarded, "    def test_guard", "    @off\n    def test_guard")
    assert run_cli("fix", "--id", "guard", "-m", "Marked slow.") == 0
    assert "pytest.mark.slow via off" in (guarded / SIDECAR).read_text()
    assert check(run_cli, capsys)[0] == 0
    edit(guarded, "off = pytest.mark.slow", "off = pytest.mark.skip")
    status, err = check(run_cli, capsys)
    assert status == 1 and "[C16]" in err


@pytest.mark.parametrize(
    ("setup", "flip"),
    [
        ("SKIP = False\n", ("SKIP = False", "SKIP = True")),
        ("FLAG = False\nSKIP = FLAG\n", ("FLAG = False", "FLAG = True")),
        (
            "import os\nSKIP = False\nif os.environ.get('X'):\n    SKIP = True\n",
            ("SKIP = True", "SKIP = False"),
        ),
    ],
    ids=["direct", "chained", "conditional"],
)
def test_a_flipped_module_constant_behind_a_direct_mark_is_c16(
    guarded, run_cli, capsys, setup, flip
):
    edit(guarded, "import pytest\n", "import pytest\n" + setup)
    edit(
        guarded,
        "    def test_guard",
        "    @pytest.mark.skipif(SKIP, reason='r')\n    def test_guard",
    )
    assert run_cli("fix", "--id", "guard", "-m", "Flagged.") == 0
    assert check(run_cli, capsys)[0] == 0
    edit(guarded, *flip)
    status, err = check(run_cli, capsys)
    assert status == 1 and "[C16]" in err


def test_a_condition_that_cannot_be_read_is_recorded_as_such(guarded, run_cli):
    edit(guarded, "import pytest\n", "import pytest\nfrom flags import SKIP\n")
    edit(
        guarded,
        "    def test_guard",
        "    @pytest.mark.skipif(SKIP)\n    def test_guard",
    )
    assert run_cli("fix", "--id", "guard", "-m", "Flagged.") == 0
    assert "not statically known" in (guarded / SIDECAR).read_text()
