"""Read and write the per-keystone sidecar file.

The metadata block is TOML rather than YAML so it parses with stdlib `tomllib`
and phase 1 stays dependency-free.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from keystones.models import Entry

_META_RE = re.compile(r"^```toml\n(.*?)^```\n", re.DOTALL | re.MULTILINE)
_SECTION_RE = re.compile(r"^## (.+?)\n(.*?)(?=^## |\Z)", re.DOTALL | re.MULTILINE)
_CODE_RE = re.compile(r"^(`{3,})(\w*)\n(.*?)^\1", re.DOTALL | re.MULTILINE)


class SidecarError(Exception):
    pass


def _split_sections(raw: str) -> dict[str, str]:
    """Split on `## ` headings, ignoring anything inside a fenced block.

    The stored source is arbitrary text and routinely contains both.
    """
    sections: dict[str, str] = {}
    name: str | None = None
    body: list[str] = []
    fence: str | None = None
    for line in raw.splitlines():
        stripped = line.lstrip()
        if fence is None:
            match = re.match(r"(`{3,})", stripped)
            if match:
                fence = match.group(1)
        elif stripped.startswith(fence) and stripped.strip("`") == "":
            fence = None
        if fence is None and line.startswith("## "):
            if name is not None:
                sections[name] = "\n".join(body)
            name = line[3:].strip().lower()
            body = []
            continue
        if name is not None:
            body.append(line)
    if name is not None:
        sections[name] = "\n".join(body)
    return sections


def parse(path: Path, category: str) -> Entry:
    raw = path.read_text()
    meta_match = _META_RE.search(raw)
    if not meta_match:
        raise SidecarError(f"{path}: no ```toml metadata block")
    meta = tomllib.loads(meta_match.group(1))

    sections = _split_sections(raw)
    why = sections.get("why", "").strip()

    source, source_lang = "", "python"
    if "canonical source" in sections:
        code_match = _CODE_RE.search(sections["canonical source"])
        if code_match:
            source_lang = code_match.group(2) or ""
            source = code_match.group(3).rstrip("\n")

    history = [
        line.strip()[2:].strip()
        for line in sections.get("history", "").splitlines()
        if line.strip().startswith("- ")
    ]

    missing = [k for k in ("target", "hasher", "semantic", "text") if k not in meta]
    if missing:
        raise SidecarError(f"{path}: metadata missing {', '.join(missing)}")

    return Entry(
        id=path.stem,
        category=category,
        target=meta["target"],
        hash=meta.get("hash", ""),
        hasher=meta["hasher"],
        semantic=meta["semantic"],
        text=meta["text"],
        review_every=meta.get("review_every"),
        depends=list(meta.get("depends", [])),
        depends_hash=meta.get("depends_hash", ""),
        why=why,
        source=source,
        source_lang=source_lang,
        history=history,
        path=str(path),
    )


def _toml_value(value: object) -> str:
    # json.dumps escapes quotes and backslashes correctly; writing them raw
    # produces a sidecar that tomllib rejects.
    if isinstance(value, list):
        items = ", ".join(json.dumps(str(item)) for item in value)
        return f"[{items}]"
    return json.dumps(str(value))


def render(entry: Entry) -> str:
    meta = {
        "target": entry.target,
        "hash": entry.hash,
        "hasher": entry.hasher,
        "semantic": entry.semantic,
        "text": entry.text,
    }
    if not entry.hash:
        del meta["hash"]
    if entry.review_every:
        meta["review_every"] = entry.review_every
    if entry.depends:
        meta["depends"] = entry.depends
        meta["depends_hash"] = entry.depends_hash

    lines = [f"# {entry.id}", "", "```toml"]
    lines += [f"{key} = {_toml_value(value)}" for key, value in meta.items()]
    # A payload carrying its own fence would close the block early, and C5
    # would then report the entry as hand-edited forever.
    longest = max(
        (len(m) for m in re.findall(r"^`{3,}", entry.source, re.M)), default=0
    )
    fence = "`" * max(3, longest + 1)

    lines += ["```", "", "## Why", ""]
    lines.append(entry.why.strip() or "TODO: why is this load-bearing?")
    lines += [
        "",
        "## Canonical source",
        "",
        f"{fence}{entry.source_lang}",
        entry.source,
        fence,
        "",
    ]
    lines += ["## History", ""]
    lines += [f"- {item}" for item in entry.history] or ["- (none)"]
    return "\n".join(lines) + "\n"


def write(path: Path, entry: Entry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(entry))


def load_all(sidecar_root: Path, categories: tuple[str, ...]) -> list[Entry]:
    entries = []
    for category in categories:
        directory = sidecar_root / category
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            entries.append(parse(path, category))
    return entries


def render_index(entries: list[Entry]) -> str:
    lines = ["# Keystones", "", "Generated by `keystones index`. Do not hand-edit.", ""]
    if not entries:
        lines += ["No keystones yet.", ""]
        return "\n".join(lines)
    current = None
    for entry in entries:
        if entry.category != current:
            current = entry.category
            if lines[-1] != "":
                lines.append("")
            lines += [f"## {current}", "", "| id | target | why |", "|---|---|---|"]
        summary = " ".join(entry.why.split())
        if len(summary) > 80:
            summary = summary[:77] + "..."
        lines.append(
            f"| [{entry.id}]({entry.category}/{entry.id}.md) "
            f"| `{entry.target}` | {summary} |"
        )
    lines.append("")
    return "\n".join(lines)
