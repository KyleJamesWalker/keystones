"""Run a preprocessor plugin and hold it to its contract, for any adapter."""

from __future__ import annotations

from keystones.adapters.base import ResolutionError
from keystones.preprocess import Refused


class ContractError(ResolutionError):
    """A preprocessor broke the contract it is held to."""


class PreprocessorRefused(ResolutionError):
    """The plugin declined this file, which is a supported answer."""


def preprocessed(preprocessor, src: str) -> tuple[str, str]:
    """Run the plugin, holding it to the line-preservation contract.

    A plugin that shifts lines corrupts every target in the file, silently and
    in a way that looks like the code moved. Counting lines is nearly free, so
    this is checked rather than documented.
    """
    if preprocessor is None:
        return src, ""
    try:
        masked, extra = preprocessor.fn(src)
    except Refused as exc:
        raise PreprocessorRefused(
            f"{preprocessor.path} refused this file: {exc}"
        ) from exc
    if masked.count("\n") != src.count("\n"):
        raise ContractError(
            f"preprocessor {preprocessor.path} changed the line count of "
            f"this file, from {src.count(chr(10)) + 1} lines to "
            f"{masked.count(chr(10)) + 1}. Targets are line numbers, so a mask "
            "has to be line-preserving."
        )
    return masked, extra
