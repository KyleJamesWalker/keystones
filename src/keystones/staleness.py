"""Staleness from git history rather than a self-reported date.

There is deliberately no `reviewed:` field. `fix` would write whatever date it
ran, which is an unverified self-report. Per-keystone sidecar files make the
last commit that touched the file the real answer.

The duration format and the git-age approach follow the `--age` rule in
KyleJamesWalker/pre-commit-hooks, so the two tools agree on what "180d" means.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
from pathlib import Path

_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}
_TOKEN_RE = re.compile(r"(\d+)([smhdwMy])")


class DurationError(ValueError):
    pass


def parse_duration(text: str) -> dt.timedelta:
    """Accepts compound durations such as `5d3h2m`. M is months, m is minutes."""
    tokens = _TOKEN_RE.findall(text)
    if not tokens or "".join(a + b for a, b in tokens) != text.strip():
        raise DurationError(
            f"invalid duration {text!r}; expected forms like 180d or 5d3h"
        )
    total = dt.timedelta()
    for amount, unit in tokens:
        count = int(amount)
        if unit == "M":
            total += dt.timedelta(days=count * 30)
        elif unit == "y":
            total += dt.timedelta(days=count * 365)
        else:
            total += dt.timedelta(**{_UNITS[unit]: count})
    return total


def last_touched(repo_root: Path, rel_path: str) -> dt.datetime | None:
    """Commit time of the sidecar, falling back to mtime when there is none."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", rel_path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if out:
            return dt.datetime.fromtimestamp(int(out))
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass
    full = repo_root / rel_path
    if full.exists():
        return dt.datetime.fromtimestamp(os.path.getmtime(full))
    return None


def age(repo_root: Path, rel_path: str) -> dt.timedelta | None:
    touched = last_touched(repo_root, rel_path)
    return None if touched is None else dt.datetime.now() - touched


def humanize(delta: dt.timedelta) -> str:
    days = delta.days
    if days >= 365:
        return f"{days // 365}y{(days % 365) // 30}M"
    if days >= 30:
        return f"{days // 30}M{days % 30}d"
    return f"{days}d"
