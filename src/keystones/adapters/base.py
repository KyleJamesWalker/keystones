"""Adapter contract. Adding a language means adding one of these."""

from __future__ import annotations

from typing import Protocol

from keystones.models import Marker, Target


class ResolutionError(Exception):
    """A marker did not attach to anything."""


class Adapter(Protocol):
    name: str
    hasher_id: str
    extensions: tuple[str, ...]

    def markers(self, path: str, src: str) -> list[Marker]: ...

    def resolve(self, src: str, marker: Marker) -> Target: ...

    def hashes(self, src: str, target: Target) -> tuple[str, str]: ...

    def canonical_source(self, src: str, target: Target) -> str: ...
