"""Adapter registry. Extensions route to a parser; everything else falls back."""

from __future__ import annotations

from pathlib import Path

from keystones.adapters import fallback
from keystones.adapters import python as python_adapter

_PARSERS = (python_adapter,)


def for_path(path: str, allow_fallback: bool = True):
    suffix = Path(path).suffix
    for adapter in _PARSERS:
        if suffix in adapter.extensions:
            return adapter
    return fallback if allow_fallback else None


def parsed_extensions() -> tuple[str, ...]:
    return tuple(ext for adapter in _PARSERS for ext in adapter.extensions)


def supported_extensions() -> tuple[str, ...]:
    return parsed_extensions()
