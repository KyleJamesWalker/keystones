import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
# Stand-in plugins live here, imported by the same path string a real one uses.
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))


@pytest.fixture(autouse=True)
def plain_output(monkeypatch):
    """Findings switch to GitHub annotations when GITHUB_ACTIONS is set.

    Tests assert on the plain form, so they must not change shape by virtue of
    running on a runner.
    """
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


@pytest.fixture(autouse=True)
def builtin_languages_only():
    """Configured languages install into module globals; unpick them after."""
    from keystones.adapters import treesitter

    original = treesitter.SPECS
    yield
    treesitter.SPECS = original
    treesitter.install_user_specs(())


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo configured for keystones, with one keystone-worthy function."""
    subprocess.run(["git", "init", "-q", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.email", "t@e.st"], cwd=tmp_path, check=True)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.keystones]\nroot = "keystones"\ncategories = ["default", "finance"]\n'
    )
    github = tmp_path / ".github"
    github.mkdir()
    (github / "CODEOWNERS").write_text(
        "/keystones/          @org/eng\n"
        "/pyproject.toml      @org/eng\n"
        "/.github/CODEOWNERS  @org/eng\n"
    )
    billing = tmp_path / "billing"
    billing.mkdir()
    (billing / "payout.py").write_text(PAYOUT_SRC)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return tmp_path


PAYOUT_SRC = """from decimal import ROUND_HALF_UP, Decimal


def compute_payout(amount: Decimal) -> Decimal:
    # do not reorder
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
"""


@pytest.fixture
def run_cli(repo: Path):
    from keystones.cli import main

    def _run(*args: str) -> int:
        return main(["--repo-root", str(repo), *args])

    return _run
