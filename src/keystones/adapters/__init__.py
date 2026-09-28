"""Adapter registry. Extensions route to a parser; everything else falls back."""

from __future__ import annotations

from pathlib import Path

from keystones.adapters import fallback, structured
from keystones.adapters import python as python_adapter


def _parsers():
    from keystones.adapters import plugin, treesitter

    parsers = [python_adapter]
    if plugin.extensions:
        parsers.append(plugin)
    if treesitter.available():
        parsers.append(treesitter)
    return tuple(parsers)


_CACHE: tuple | None = None
_DEFAULT_KIND: dict[str, str] = {}


def parsers() -> tuple:
    global _CACHE
    if _CACHE is None:
        _CACHE = _parsers()
    return _CACHE


def configure(cfg) -> None:
    """Install this repo's configured languages. Call once, after loading config."""
    global _CACHE, _DEFAULT_KIND
    from keystones.adapters import plugin, treesitter

    specs = [treesitter.spec_from_config(lang) for lang in cfg.languages]
    treesitter.install_user_specs(tuple(s for s in specs if s is not None))
    plugin.install(
        tuple(
            plugin.PluginSpec(
                extensions=lang.extensions,
                parser=lang.parser,
                preprocessor=lang.preprocessor,
                line_comment=lang.line_comment,
            )
            for lang in cfg.languages
            if lang.parser is not None
        )
    )
    _DEFAULT_KIND = {
        ext: lang.hash for lang in cfg.languages if lang.hash for ext in lang.extensions
    }
    _CACHE = None


def default_kind(path: str) -> str | None:
    """The basis this repo chose for these files, if it chose one."""
    return _DEFAULT_KIND.get(Path(path).suffix)


class UnknownKind(Exception):
    """A marker asked for a basis this file cannot be hashed with."""


TEXT_KIND = "text"


def kinds_for(path: str, exclude: str | None = None) -> tuple[str, ...]:
    """Every basis this file could be hashed with, best first.

    Text is always available; it is what a file with no parser already gets.
    """
    parsed = for_path(path, allow_fallback=False)
    kinds = [TEXT_KIND] if parsed is None else [parsed.kind_for_path(path), TEXT_KIND]
    # Opt-in, so it is offered after text rather than taking the extension.
    if path.endswith(structured.extensions) and structured.available():
        kinds.append(structured.KIND)
    chosen = default_kind(path)
    if chosen in kinds:
        kinds.remove(chosen)
        kinds.insert(0, chosen)
    return tuple(k for k in kinds if k != exclude)


def for_kind(path: str, kind: str):
    if kind == TEXT_KIND:
        return fallback
    parsed = for_path(path, allow_fallback=False)
    if parsed is not None and parsed.kind_for_path(path) == kind:
        return parsed
    if kind == structured.KIND and path.endswith(structured.extensions):
        if structured.available():
            return structured
        raise UnknownKind(
            f"{path}: hash=yaml needs the extra: pip install 'keystones[yaml]'"
        )
    raise UnknownKind(
        f"{path}: no '{kind}' basis here. Available: {', '.join(kinds_for(path))}"
    )


def for_entry(entry):
    """The adapter an entry was hashed with, by the basis it recorded.

    Routing by extension would check a text keystone against the grammar's
    hasher and skip it as migrated. A recorded kind the file no longer offers
    falls back to the extension's adapter: C14 names the disagreement, and
    `migrate` is what resolves it.
    """
    rel = entry.target.split("::")[0].split("#")[0]
    kind = entry.hash or default_kind(rel)
    if kind is None:
        return for_path(rel)
    try:
        return for_kind(rel, kind)
    except UnknownKind:
        return for_path(rel)


def for_path(path: str, allow_fallback: bool = True):
    suffix = Path(path).suffix
    for adapter in parsers():
        if suffix in adapter.extensions:
            return adapter
    return fallback if allow_fallback else None


def for_symbols(path: str):
    """The adapter that can name symbols in this file, for `depends` and twins.

    The text adapter names nothing, so a structured basis is preferred to it.
    """
    parsed = for_path(path, allow_fallback=False)
    if parsed is not None:
        return parsed
    if path.endswith(structured.extensions) and structured.available():
        return structured
    return fallback


def hasher_id(adapter, target) -> str:
    """The hasher an entry records. A region is text whatever its file's parser.

    `target` is a Target or the string an entry stores; `#L` marks a region.
    """
    if isinstance(target, str):
        region, path = "#L" in target, target.split("::")[0].split("#")[0]
    else:
        region, path = target.region, target.path
    if region:
        return fallback.HASHER_ID
    return adapter.hasher_id_for_path(path)


def needs_extra(path: str) -> bool:
    """A language we support, whose parser is not installed.

    Falling through to the text adapter here would compute a different hash and
    report the keystone as drifted, which is a lie about the code.
    """
    from keystones.adapters import plugin, treesitter

    suffix = Path(path).suffix
    if suffix in plugin.extensions:
        return False
    return suffix in treesitter.extensions and not treesitter.available()


def parsed_extensions() -> tuple[str, ...]:
    return tuple(ext for adapter in parsers() for ext in adapter.extensions)


def supported_extensions() -> tuple[str, ...]:
    return parsed_extensions()
