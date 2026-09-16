"""Adapter registry, keyed by file extension."""

from __future__ import annotations

from pathlib import Path

from keystones.adapters import python as python_adapter

_ADAPTERS = (python_adapter,)


def for_path(path: str):
    suffix = Path(path).suffix
    for adapter in _ADAPTERS:
        if suffix in adapter.extensions:
            return adapter
    return None


def supported_extensions() -> tuple[str, ...]:
    return tuple(ext for adapter in _ADAPTERS for ext in adapter.extensions)
