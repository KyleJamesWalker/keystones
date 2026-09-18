"""Adapter registry. Extensions route to a parser; everything else falls back."""

from __future__ import annotations

from pathlib import Path

from keystones.adapters import fallback
from keystones.adapters import python as python_adapter


def _parsers():
    parsers = [python_adapter]
    from keystones.adapters import treesitter

    if treesitter.available():
        parsers.append(treesitter)
    return tuple(parsers)


_CACHE: tuple | None = None


def parsers() -> tuple:
    global _CACHE
    if _CACHE is None:
        _CACHE = _parsers()
    return _CACHE


def configure(cfg) -> None:
    """Install this repo's configured languages. Call once, after loading config."""
    global _CACHE
    from keystones.adapters import treesitter

    treesitter.install_user_specs(
        tuple(treesitter.spec_from_config(lang) for lang in cfg.languages)
    )
    _CACHE = None


def for_path(path: str, allow_fallback: bool = True):
    suffix = Path(path).suffix
    for adapter in parsers():
        if suffix in adapter.extensions:
            return adapter
    return fallback if allow_fallback else None


def needs_extra(path: str) -> bool:
    """A language we support, whose parser is not installed.

    Falling through to the text adapter here would compute a different hash and
    report the keystone as drifted, which is a lie about the code.
    """
    from keystones.adapters import treesitter

    return Path(path).suffix in treesitter.extensions and not treesitter.available()


def parsed_extensions() -> tuple[str, ...]:
    return tuple(ext for adapter in parsers() for ext in adapter.extensions)


def supported_extensions() -> tuple[str, ...]:
    return parsed_extensions()
