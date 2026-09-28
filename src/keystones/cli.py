"""Command line entry point. This is what the pre-commit hooks invoke."""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

from keystones import adapters, dependencies, gitref, sidecar
from keystones import markers as marker_grammar
from keystones.adapters.base import ResolutionError
from keystones.checks import disablers, oversized, run_all
from keystones.config import Config, ConfigError, load
from keystones.discovery import collect, scan
from keystones.discovery import unread as discovery_unread
from keystones.models import Entry, Finding, Marker, Scope, Severity


def _git_author(repo_root: Path) -> str:
    try:
        return (
            subprocess.run(
                ["git", "config", "user.name"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            or "unknown"
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _default_format() -> str:
    """Annotations when a runner will render them, plain text otherwise.

    The hooks are shared between CI and a developer's machine, so the format
    cannot be a flag in the hook entry.
    """
    return "github" if os.environ.get("GITHUB_ACTIONS") == "true" else "plain"


def _report(findings, fmt: str, repo_root: Path | None = None) -> None:
    for finding in findings:
        finding = _relative(finding, repo_root)
        line = finding.format_github() if fmt == "github" else finding.format_plain()
        print(line, file=sys.stderr)


def _relative(finding: Finding, repo_root: Path | None) -> Finding:
    """A sidecar's path is stored absolute; a finding should read, and attach
    to a pull request, by the repo-relative one."""
    if repo_root is None or not finding.path:
        return finding
    path = Path(finding.path)
    if not path.is_absolute():
        return finding
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return finding
    return dataclasses.replace(finding, path=str(rel))


def _entries(cfg: Config) -> list[Entry]:
    return sidecar.load_all(cfg.sidecar_root, cfg.categories)


def _staged_entries(
    cfg: Config, paths: list[str]
) -> tuple[dict[tuple[str, str], Entry], list[Finding]]:
    """The sidecars among `paths`, parsed, so each is checked with its marker."""
    entries: dict[tuple[str, str], Entry] = {}
    findings: list[Finding] = []
    prefix = f"{cfg.root.strip('/')}/"
    for rel in paths:
        if not rel.startswith(prefix) or not rel.endswith(".md"):
            continue
        parts = rel[len(prefix) :].split("/")
        full = cfg.repo_root / rel
        if len(parts) != 2 or parts[0] not in cfg.categories or not full.is_file():
            continue
        try:
            entry = sidecar.parse(full, parts[0])
        except (sidecar.SidecarError, ValueError) as exc:
            findings.append(Finding("sidecar", Severity.ERROR, str(exc), rel))
            continue
        entries[entry.key] = entry
    return entries, findings


def _check_paths(args, cfg: Config, paths: list[str]) -> int:
    """The staged hook: cost follows the files passed, not the size of the repo."""
    from keystones.checks import (
        c2_orphan_entries,
        c5_stored_source,
        c11_dependencies,
        c17_twins,
    )

    staged, findings = _staged_entries(cfg, paths)
    targets = {entry.target.split("::")[0].split("#")[0] for entry in staged.values()}
    resolved, found, skipped = collect(cfg, sorted({*paths, *targets}))
    findings += found

    entries = dict(staged)
    for item in resolved:
        # Every category, so a marker naming the wrong one still meets its entry.
        for category in cfg.categories:
            key = (category, item.marker.id)
            path = cfg.sidecar_path(*key)
            if key in entries or not path.is_file():
                continue
            try:
                entries[key] = sidecar.parse(path, category)
            except (sidecar.SidecarError, ValueError) as exc:
                rel = str(path.relative_to(cfg.repo_root))
                findings.append(Finding("sidecar", Severity.ERROR, str(exc), rel))
    findings += run_all(
        cfg,
        resolved,
        list(entries.values()),
        scoped=True,
        warn_only=args.warn_only,
    )
    too_big, unread = oversized(cfg, list(entries.values()))
    findings += too_big
    for rel in paths:
        full = cfg.repo_root / rel
        if (
            rel not in unread
            and full.is_file()
            and full.stat().st_size > cfg.max_scan_bytes
        ):
            findings.append(
                Finding(
                    "size",
                    Severity.WARNING,
                    f"{rel} is over max_scan_bytes ({cfg.max_scan_bytes}) and was "
                    "not read; a keystone in it is checked only by `check --all`",
                    rel,
                )
            )
    findings += c5_stored_source(staged)
    findings += c2_orphan_entries(resolved, staged, skipped | unread)
    # The staged keystones' own dependencies and twins: a copy or a helper
    # drifting is what the hook is for, and both cost only the files named.
    findings += c11_dependencies(cfg, list(entries.values()))
    findings += c17_twins(cfg, list(entries.values()))
    if not resolved and not staged and not findings:
        return 0
    _report(findings, args.format or _default_format(), cfg.repo_root)
    if all(f.severity is Severity.NOTICE for f in findings):
        print(
            f"keystones: {len(resolved)} keystone(s) verified. Not run here: C2, "
            "C6, C8, C9, C10, C12, and C11/C17 for keystones in other files; "
            "`keystones check --all` runs them."
        )
    return 1 if any(f.severity is Severity.ERROR for f in findings) else 0


def cmd_check(args, cfg: Config) -> int:
    paths = None if args.all else (args.paths or None)
    if paths is not None:
        return _check_paths(args, cfg, paths)
    base = None
    if not args.no_base:
        base = gitref.resolve_base(cfg.repo_root, args.base)
        if base is None:
            print(
                "keystones: no base ref available, skipping C9 (removal check). "
                "Pass --base <ref>, or --no-base to silence this.",
                file=sys.stderr,
            )
    resolved, findings, skipped = collect(cfg, None)
    entry_list = _entries(cfg)
    too_big, unread = oversized(cfg, entry_list)
    findings = findings + too_big
    findings += run_all(
        cfg,
        resolved,
        entry_list,
        warn_only=args.warn_only,
        base=base,
        skipped=skipped | unread,
    )
    _report(findings, args.format or _default_format(), cfg.repo_root)
    errors = [f for f in findings if f.severity is Severity.ERROR]
    if all(f.severity is Severity.NOTICE for f in findings):
        print(f"keystones: {len(resolved)} keystone(s) verified")
    return 1 if errors else 0


def _extend(args, cfg: Config, item, category: str, keystone_id: str, twins, depends):
    """`add --id <existing> --twin/--depends`: append to the entry, hashed now."""
    path = cfg.sidecar_path(category, keystone_id)
    entry = sidecar.parse(path, category)
    src = (cfg.repo_root / item.marker.path).read_text()
    try:
        semantic, _ = item.adapter.hashes(src, item.target)
    except ResolutionError as exc:
        print(f"keystones: {exc}", file=sys.stderr)
        return 1
    if semantic != entry.semantic:
        print(
            f"keystones: '{keystone_id}' has drifted; run `keystones fix` before "
            "adding to it",
            file=sys.stderr,
        )
        return 1
    new_twins = [t for t in twins if t not in entry.twins]
    problem = _twins_mismatch(cfg, new_twins, item.adapter, item.marker.path, semantic)
    if problem:
        print(f"keystones: {problem}", file=sys.stderr)
        return 1
    new_depends = [d for d in depends if d not in entry.depends]
    try:
        depends_hash = dependencies.combined_hash(
            cfg.repo_root, entry.depends + new_depends, cfg.max_scan_bytes
        )
    except dependencies.UnresolvedDependency as exc:
        print(f"keystones: {exc}", file=sys.stderr)
        return 1
    today = datetime.date.today().isoformat()
    author = _git_author(cfg.repo_root)
    if new_twins:
        entry.twins += new_twins
        entry.history.insert(
            0, f"{today} - twin added: {', '.join(new_twins)}. {author}"
        )
    if new_depends:
        entry.depends += new_depends
        entry.depends_hash = depends_hash
        entry.history.insert(
            0, f"{today} - depends added: {', '.join(new_depends)}. {author}"
        )
    sidecar.write(path, entry)
    print(f"keystones: extended '{keystone_id}' in {category}")
    return 0


def _twins_mismatch(
    cfg: Config, specs: list[str], adapter, rel: str, semantic: str
) -> str | None:
    """Why these twins cannot be recorded against this hash, or None."""
    from keystones import twins

    kind = adapter.kind_for_path(rel)
    for spec in specs:
        try:
            actual, _ = twins.semantic(cfg.repo_root, spec, kind)
        except twins.UnresolvedTwin as exc:
            return f"twin {exc}"
        if actual != semantic:
            return (
                f"twin {spec} does not match the keystone: the two hash "
                "differently, so one of them is not a copy of the other"
            )
    return None


class AmbiguousId(Exception):
    """A bare id that exists in more than one category."""


def _keys_named(values: list[str], keys) -> set[tuple[str, str]]:
    """`--id` values as (category, id) keys; `category/id` names one exactly."""
    out: set[tuple[str, str]] = set()
    for value in values:
        category, sep, keystone_id = value.rpartition("/")
        if sep:
            out.add((category, keystone_id))
            continue
        matches = sorted({key for key in keys if key[1] == value})
        if len(matches) > 1:
            raise AmbiguousId(
                f"id '{value}' exists in categories "
                f"{', '.join(c for c, _ in matches)}; say which with "
                f"--id {matches[0][0]}/{value}"
            )
        out.update(matches)
    return out


def cmd_fix(args, cfg: Config) -> int:
    resolved, _, findings, _ = scan(cfg, None)
    if findings:
        _report(findings, "plain", cfg.repo_root)
        return 1

    entries = {entry.key: entry for entry in _entries(cfg)}
    try:
        selected = _keys_named(args.id, entries) if args.id else None
    except AmbiguousId as exc:
        print(f"keystones: {exc}", file=sys.stderr)
        return 1
    author = _git_author(cfg.repo_root)
    today = datetime.date.today().isoformat()
    changed: list[str] = []
    failed = False

    for item in resolved:
        entry = entries.get(item.marker.key)
        if entry is None or (selected is not None and entry.key not in selected):
            continue
        src = (cfg.repo_root / item.marker.path).read_text()
        try:
            semantic, text = item.adapter.hashes(src, item.target)
        except ResolutionError as exc:
            print(f"keystones: {entry.id}: {exc}", file=sys.stderr)
            failed = True
            continue
        target_str = str(item.target)
        current_hasher = adapters.hasher_id(item.adapter, item.target)
        if entry.hasher and entry.hasher != current_hasher:
            # Writing here would store a hash the rest of the repo cannot
            # reproduce, and the next check would ask for another fix forever.
            print(
                f"keystones: '{entry.id}' was hashed by {entry.hasher} but "
                f"this environment is {current_hasher}. Writing now would "
                "store a hash nobody else reproduces. Install the version "
                "this repo uses, or run `keystones migrate` if the move is "
                "deliberate.",
                file=sys.stderr,
            )
            return 1
        try:
            depends_hash = dependencies.combined_hash(
                cfg.repo_root, entry.depends, cfg.max_scan_bytes
            )
        except dependencies.UnresolvedDependency as exc:
            print(f"keystones: {entry.id}: {exc}", file=sys.stderr)
            return 1
        # An entry with no dependencies stores no depends_hash, so the absent
        # value has to compare equal to the empty one or every move looks semantic.
        stored_depends = entry.depends_hash or dependencies.EMPTY
        disabled_by = disablers(cfg, item.adapter, src, item.target)
        semantic_changed = (
            semantic != entry.semantic
            or depends_hash != stored_depends
            or disabled_by != sorted(entry.disabled_by)
        )
        # A twin that no longer matches is not reconciled by writing: it needs
        # the copies brought back in line, or the twin taken off the list.
        problem = _twins_mismatch(
            cfg, entry.twins, item.adapter, item.marker.path, semantic
        )
        if problem:
            print(f"keystones: '{entry.id}': {problem}", file=sys.stderr)
            failed = True
            continue
        source = item.adapter.canonical_source(src, item.target)
        unchanged = (
            not semantic_changed
            and text == entry.text
            and target_str == entry.target
            and source == entry.source
        )
        if unchanged:
            continue
        if semantic_changed and not args.message:
            print(
                f"keystones: '{entry.id}' changed semantically; -m is required",
                file=sys.stderr,
            )
            return 1
        moved_file = (
            target_str.split("::")[0].split("#")[0]
            != entry.target.split("::")[0].split("#")[0]
        )
        if moved_file and not args.message:
            # Moving a marker to another file is how a decoy gets adopted, so
            # it is never note-free even when the hash is unchanged.
            print(
                f"keystones: '{entry.id}' moved to a different file; -m is required",
                file=sys.stderr,
            )
            return 1
        if target_str != entry.target and not semantic_changed and not args.message:
            entry.history.insert(0, f"{today} - moved to {target_str}. {author}")
        elif args.message:
            entry.history.insert(0, f"{today} - {args.message} {author}")
        elif not semantic_changed and text == entry.text and source != entry.source:
            entry.history.insert(0, f"{today} - stored source refreshed. {author}")
        entry.target = target_str
        entry.semantic = semantic
        entry.text = text
        entry.hash = adapters.kind_for(item.adapter, item.target)
        entry.hasher = adapters.hasher_id(item.adapter, item.target)
        entry.source = source
        entry.depends_hash = dependencies.combined_hash(
            cfg.repo_root, entry.depends, cfg.max_scan_bytes
        )
        entry.disabled_by = disabled_by
        sidecar.write(cfg.sidecar_path(entry.category, entry.id), entry)
        changed.append(entry.id)

    _write_index(cfg)
    print(
        f"keystones: updated {len(changed)} entr(ies): {', '.join(changed) or 'none'}"
    )
    return 1 if failed else 0


def _lang_for(path: str) -> str:
    return {
        ".py": "python",
        ".pyi": "python",
        ".tf": "hcl",
        ".sql": "sql",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
    }.get(Path(path).suffix, "")


def _chosen_basis(rel: str, src: str, preferred, kind: str | None, scope: Scope):
    """Resolve --hash, or refuse to guess when the preferred basis cannot read.

    Choosing silently here would bake a basis nobody agreed to into the hash,
    and the marker would not say which one produced it.
    """
    if kind:
        if kind == adapters.TEXT_KIND and scope is Scope.NODE:
            raise adapters.UnknownKind(
                f"{rel}: --hash text has no nodes to attach to. Drop the ::name "
                "for a whole-file keystone, or write a region by hand."
            )
        return adapters.for_kind(rel, kind)
    chosen = adapters.default_kind(rel)
    if chosen is not None:
        return adapters.for_kind(rel, chosen)
    try:
        preferred.markers(rel, src)
    except ResolutionError as exc:
        failed = preferred.kind_for_path(rel)
        options = " ".join(
            f"--hash {k}" for k in adapters.kinds_for(rel, exclude=failed)
        )
        raise adapters.UnknownKind(
            f"{rel} {exc} Say which basis to gate on: {options}"
        ) from exc
    return preferred


def _adopt(args, cfg: Config) -> int:
    """Create the sidecar for a marker already written into the source.

    This is the path C1 points at, and the only one that works for a region,
    whose boundaries are already in the file.
    """
    resolved, _, findings, _ = scan(cfg, None)
    wanted_category, _, keystone_id = args.id.rpartition("/")
    match = [
        item
        for item in resolved
        if item.marker.id == keystone_id
        and wanted_category in ("", item.marker.category)
    ]
    doomed = [m for m in match if cfg.unreviewed_by(m.marker.path)]
    match = [m for m in match if m not in doomed]
    if not match and doomed:
        item = doomed[0]
        print(
            f"keystones: [C18] {item.marker.path} matches "
            f"'{cfg.unreviewed_by(item.marker.path)}' in [tool.keystones] "
            "unreviewed, so it is rewritten without review and no gate can hold "
            "there.",
            file=sys.stderr,
        )
        return 1
    if not match:
        _report(findings, "plain", cfg.repo_root)
        unread = discovery_unread(cfg)
        if unread:
            print(
                f"keystones: not read, over max_scan_bytes ({cfg.max_scan_bytes}): "
                + ", ".join(unread),
                file=sys.stderr,
            )
        print(
            f"keystones: no marker with id '{args.id}' in the tree. Pass a target to "
            "write one, or check the id.",
            file=sys.stderr,
        )
        return 1
    categories = sorted({m.marker.category for m in match})
    if len(categories) > 1:
        print(
            f"keystones: id '{keystone_id}' is marked in categories "
            f"{', '.join(categories)}; say which with "
            f"--id {categories[0]}/{keystone_id}",
            file=sys.stderr,
        )
        return 1
    if len(match) > 1:
        where = ", ".join(f"{m.marker.path}:{m.marker.lineno}" for m in match)
        print(
            f"keystones: id '{keystone_id}' appears more than once: {where}",
            file=sys.stderr,
        )
        return 1

    item = match[0]
    category = item.marker.category
    # A problem in the target's own file blocks; one elsewhere is somebody
    # else's, and must not stop this keystone from being adopted.
    blocking = [f for f in findings if f.path == item.marker.path]
    if blocking:
        _report(blocking, "plain", cfg.repo_root)
        return 1
    for finding in findings:
        print(
            f"keystones: warning: elsewhere, {finding.format_plain()}", file=sys.stderr
        )
    if cfg.sidecar_path(category, keystone_id).exists():
        additions = list(getattr(args, "twins", None) or []), list(args.depends or [])
        if any(additions):
            return _extend(args, cfg, item, category, keystone_id, *additions)
        print(
            f"keystones: '{keystone_id}' already exists in {category}",
            file=sys.stderr,
        )
        return 1

    src = (cfg.repo_root / item.marker.path).read_text()
    try:
        semantic, text_digest = item.adapter.hashes(src, item.target)
    except ResolutionError as exc:
        print(f"keystones: {exc}", file=sys.stderr)
        return 1
    depends = list(args.depends or [])
    try:
        depends_hash = dependencies.combined_hash(
            cfg.repo_root, depends, cfg.max_scan_bytes
        )
    except dependencies.UnresolvedDependency as exc:
        print(f"keystones: {exc}", file=sys.stderr)
        return 1
    twin_specs = list(getattr(args, "twins", None) or [])
    problem = _twins_mismatch(cfg, twin_specs, item.adapter, item.marker.path, semantic)
    if problem:
        print(f"keystones: {problem}", file=sys.stderr)
        return 1
    entry = Entry(
        id=keystone_id,
        category=category,
        target=str(item.target),
        hash=adapters.kind_for(item.adapter, item.target),
        hasher=adapters.hasher_id(item.adapter, item.target),
        semantic=semantic,
        text=text_digest,
        review_every=args.review_every,
        depends=depends,
        depends_hash=depends_hash,
        twins=twin_specs,
        disabled_by=disablers(cfg, item.adapter, src, item.target),
        why=args.message,
        source=item.adapter.canonical_source(src, item.target),
        source_lang=_lang_for(item.marker.path),
        history=[
            f"{datetime.date.today().isoformat()} - initial keystone. "
            f"{_git_author(cfg.repo_root)}"
        ],
    )
    sidecar.write(cfg.sidecar_path(category, keystone_id), entry)
    _write_index(cfg)
    print(f"keystones: adopted '{keystone_id}' on {item.target}")
    if re.search(r"\[\d+\]$", str(item.target)):
        print(
            "keystones: note: that item is named by position, so inserting an "
            "item above it moves the keystone; give it a name, id or key to pin it"
        )
    return 0


def _ask(label: str, check, default: str = "") -> str:
    """Prompt until `check`, which returns a problem or None, accepts the answer."""
    suffix = f" [{default}]" if default else ""
    while True:
        answer = input(f"  {label}{suffix}: ").strip() or default
        problem = check(answer)
        if problem is None:
            return answer
        print(f"  {problem}", file=sys.stderr)


def _complete_pending(args, cfg: Config) -> int:
    """Ask for the details of each `keystone add` marker, then adopt it."""
    from keystones.staleness import DurationError, parse_duration

    resolved, pending, findings, _ = scan(cfg, None)
    if findings:
        _report(findings, "plain", cfg.repo_root)
        return 1
    if not pending:
        print(
            "keystones: no `keystone add` markers in the tree. Write one above "
            "the code to protect, or pass a target.",
            file=sys.stderr,
        )
        return 1

    taken = {item.marker.key for item in resolved} | {e.key for e in _entries(cfg)}

    def check_id(value: str) -> str | None:
        if not marker_grammar.ID_RE.match(value):
            return "use letters, digits, dot, dash or underscore"
        return None

    def check_category(value: str) -> str | None:
        return None if value in cfg.categories else f"unknown category '{value}'"

    def check_reason(value: str) -> str | None:
        return None if value else "a keystone needs a reason"

    def check_duration(value: str) -> str | None:
        try:
            if value:
                parse_duration(value)
        except DurationError as exc:
            return str(exc)
        return None

    def check_depends(value: str) -> str | None:
        try:
            dependencies.combined_hash(cfg.repo_root, value.replace(",", " ").split())
        except dependencies.UnresolvedDependency as exc:
            return str(exc)
        return None

    for item in sorted(pending, key=lambda i: (i.marker.path, i.marker.lineno)):
        path = cfg.repo_root / item.marker.path
        original = path.read_bytes()
        lines = marker_grammar.split_lines(original.decode("utf-8"))
        line = lines[item.marker.lineno - 1]
        body = line.rstrip("\r\n")
        named = marker_grammar.pending_category(body)
        where = f"{item.marker.path}:{item.marker.lineno}"
        if named is not None and check_category(named):
            print(f"keystones: {where}: {check_category(named)}", file=sys.stderr)
            return 1
        print(f"{where}: new keystone on {item.target}")
        try:
            marker_id = _ask("id", check_id)
            category = named or (
                _ask(
                    f"category ({', '.join(cfg.categories)})", check_category, "default"
                )
                if len(cfg.categories) > 1
                else "default"
            )
            # Ids are unique per category, so this can only be told once both are in.
            while (category, marker_id) in taken:
                print(
                    f"  '{marker_id}' is already a keystone in {category}",
                    file=sys.stderr,
                )
                marker_id = _ask("id", check_id)
            message = _ask("why is it load-bearing", check_reason)
            review_every = _ask(
                "review every, e.g. 180d (blank for none)", check_duration
            )
            depends = _ask(
                "depends on, path.py::Symbol (blank for none)", check_depends
            )
        except (EOFError, KeyboardInterrupt):
            print("\nkeystones: cancelled", file=sys.stderr)
            return 1

        lines[item.marker.lineno - 1] = (
            marker_grammar.complete_pending(body, marker_id, category)
            + line[len(body) :]
        )
        path.write_bytes("".join(lines).encode("utf-8"))
        status = _adopt(
            argparse.Namespace(
                id=f"{category}/{marker_id}",
                message=message,
                review_every=review_every or None,
                depends=depends.replace(",", " ").split(),
            ),
            cfg,
        )
        if status != 0:
            # Leave no marker behind without a sidecar to go with it.
            path.write_bytes(original)
            return status
        taken.add((category, marker_id))
    return 0


def _marker_on(adapter, rel: str, src: str, target, scope: Scope):
    """The marker already sitting on this target, so `add` adopts, not doubles."""
    try:
        found = adapters.for_path(rel).markers(rel, src)
    except Exception:
        return None
    for marker in found:
        if marker.scope is not scope:
            continue
        if scope is Scope.FILE:
            return marker
        try:
            resolved = adapter.resolve(src, marker)
        except (ResolutionError, SyntaxError):
            continue
        if resolved.qualname == target.qualname:
            return marker
    return None


def _file_scope_insert_line(src: str) -> int:
    """First line a comment may go on without breaking the file.

    A shebang has to stay on line 1 and a PEP 263 coding cookie within the first
    two, so a file-scope marker cannot simply be prepended.
    """
    lines = src.splitlines()
    at = 1
    if lines and lines[0].startswith("#!"):
        at = 2
    for index in range(at - 1, min(2, len(lines))):
        if "coding" in lines[index] and lines[index].lstrip().startswith("#"):
            at = index + 2
    return at


def cmd_add(args, cfg: Config) -> int:
    if args.target is None and args.id is None:
        return _complete_pending(args, cfg)
    extending = args.target is None and (args.twins or args.depends)
    if args.id is None or (args.message is None and not extending):
        print("keystones: add needs --id and -m", file=sys.stderr)
        return 2
    if args.target is None:
        return _adopt(args, cfg)
    if "::" in args.target:
        rel, qualname = args.target.split("::", 1)
        scope = Scope.NODE
    else:
        rel, qualname, scope = args.target, None, Scope.FILE

    if not marker_grammar.ID_RE.match(args.id):
        print(
            f"keystones: '{args.id}' is not a usable id; use letters, digits, "
            "dot, dash or underscore",
            file=sys.stderr,
        )
        return 1

    path = cfg.repo_root / rel
    if not path.is_file():
        print(f"keystones: no such file {rel}", file=sys.stderr)
        return 1
    if path.stat().st_size > cfg.max_scan_bytes:
        print(
            f"keystones: {rel} is {path.stat().st_size} bytes, over "
            f"max_scan_bytes ({cfg.max_scan_bytes}), so check would never read "
            "it. Raise [tool.keystones] max_scan_bytes, or keystone a smaller "
            "file.",
            file=sys.stderr,
        )
        return 1
    pattern = cfg.unreviewed_by(rel)
    if pattern is not None:
        print(
            f"keystones: [C18] {rel} matches '{pattern}' in [tool.keystones] "
            "unreviewed, so it is rewritten without review and no gate can hold "
            "there.",
            file=sys.stderr,
        )
        return 1
    adapter = adapters.for_path(rel)
    if adapter is None:
        print(f"keystones: no adapter for {rel}", file=sys.stderr)
        return 1
    if args.category not in cfg.categories:
        print(f"keystones: unknown category '{args.category}'", file=sys.stderr)
        return 1
    if cfg.sidecar_path(args.category, args.id).exists():
        print(
            f"keystones: '{args.id}' already exists in {args.category}", file=sys.stderr
        )
        return 1

    src = path.read_text()
    try:
        adapter = _chosen_basis(rel, src, adapter, args.hash_kind, scope)
    except adapters.UnknownKind as exc:
        print(f"keystones: {exc}", file=sys.stderr)
        return 1
    if scope is Scope.FILE:
        target = adapter.resolve(src, Marker(args.id, args.category, scope, rel, 1))
        insert_at, indent = _file_scope_insert_line(src), ""
    else:
        try:
            target = adapter.target_for_qualname(rel, src, qualname)
        except (ResolutionError, SyntaxError) as exc:
            print(f"keystones: {exc}", file=sys.stderr)
            return 1
        if target is None:
            print(f"keystones: {qualname} not found in {rel}", file=sys.stderr)
            return 1
        first = src.splitlines()[target.start - 1]
        insert_at = target.start
        indent = first[: len(first) - len(first.lstrip())]

    existing = _marker_on(adapter, rel, src, target, scope)
    if existing is not None:
        if existing.key != (args.category, args.id):
            print(
                f"keystones: {rel}::{qualname or ''} already carries keystone "
                f"'{existing.id}'; run `keystones add --id "
                f"{existing.category}/{existing.id}` to adopt it, or remove "
                "that marker first.",
                file=sys.stderr,
            )
            return 1
        return _adopt(
            argparse.Namespace(**{**vars(args), "id": f"{args.category}/{args.id}"}),
            cfg,
        )

    try:
        # Resolved before the file is touched: an unresolvable spec used to
        # leave a marker in the source with no sidecar behind it.
        depends = list(args.depends or [])
        depends_hash = dependencies.combined_hash(
            cfg.repo_root, depends, cfg.max_scan_bytes
        )
    except dependencies.UnresolvedDependency as exc:
        print(f"keystones: {exc}", file=sys.stderr)
        return 1

    # The file's language decides the comment syntax, not the chosen basis.
    leader = adapters.for_path(rel).comment_prefix(rel)
    quals = ["file"] if scope is Scope.FILE else []
    if args.category != "default":
        quals.append(args.category)
    if getattr(args, "hash_kind", None):
        quals.append(f"hash={args.hash_kind}")
    keyword = f"keystone({', '.join(quals)})" if quals else "keystone"
    lines = src.splitlines(keepends=True)
    lines.insert(insert_at - 1, f"{indent}{leader} {keyword}: {args.id}\n")
    path.write_text("".join(lines))

    new_src = path.read_text()
    new_target = (
        adapter.target_for_qualname(rel, new_src, qualname)
        if qualname
        else adapter.resolve(new_src, Marker(args.id, args.category, scope, rel, 1))
    )
    if qualname:
        marker = Marker(args.id, args.category, scope, rel, insert_at)
        try:
            landed = adapter.resolve(new_src, marker)
        except ResolutionError:
            landed = None
        names = {landed.qualname} if landed else set()
        if landed is not None and hasattr(adapter, "aliases"):
            names |= adapter.aliases(new_src, landed.qualname)
        if qualname not in names:
            path.write_text(src)
            print(
                f"keystones: a marker above {qualname} would attach to "
                f"{landed.qualname if landed else 'nothing'}, which shares its "
                f"first line. Move {qualname} onto its own line first.",
                file=sys.stderr,
            )
            return 1
        # The marker's own name for the target is what check resolves to, so
        # an index is recorded under its stable selector. An explicit
        # `[key=value]` the caller chose is kept; check accepts it as an alias.
        if not re.search(r"\[[^\]=]+=[^\]]*\]$", qualname):
            new_target = landed
    try:
        semantic, text = adapter.hashes(new_src, new_target)
    except ResolutionError as exc:
        # Leave no marker behind without a sidecar to go with it.
        path.write_text(src)
        print(f"keystones: {exc}", file=sys.stderr)
        return 1
    twin_specs = list(args.twins or [])
    problem = _twins_mismatch(cfg, twin_specs, adapter, rel, semantic)
    if problem:
        path.write_text(src)
        print(f"keystones: {problem}", file=sys.stderr)
        return 1
    entry = Entry(
        id=args.id,
        category=args.category,
        target=str(new_target),
        hash=adapters.kind_for(adapter, new_target),
        hasher=adapters.hasher_id(adapter, new_target),
        semantic=semantic,
        text=text,
        review_every=args.review_every,
        depends=depends,
        depends_hash=depends_hash,
        twins=twin_specs,
        disabled_by=disablers(cfg, adapter, new_src, new_target),
        why=args.message,
        source=adapter.canonical_source(new_src, new_target),
        source_lang=_lang_for(rel),
        history=[
            f"{datetime.date.today().isoformat()} - initial keystone. "
            f"{_git_author(cfg.repo_root)}"
        ],
    )
    sidecar.write(cfg.sidecar_path(args.category, args.id), entry)
    _write_index(cfg)
    print(f"keystones: added '{args.id}' on {new_target}")
    return 0


def cmd_doctor(args, cfg: Config) -> int:
    from keystones import doctor

    try:
        sidecar_paths = (
            [f"{cfg.root}/{category}/_probe.md" for category in cfg.categories]
            if cfg.codeowners_from_rulesets
            else None
        )
        report = doctor.audit(
            cfg.repo_root, args.required_check, sidecar_paths, args.repo
        )
    except doctor.Unavailable as exc:
        print(f"keystones doctor: skipped, {exc}", file=sys.stderr)
        return 0
    findings = report.findings
    _report(findings, args.format or _default_format(), cfg.repo_root)
    for requirement, source in report.satisfied.items():
        print(f"  {requirement}: {source}")
    for label, provides in report.rulesets.items():
        print(f"  {label} requires: {', '.join(provides) or 'nothing keystones needs'}")
    if not findings:
        print("keystones doctor: branch protection requires owner review")
    return 1 if any(f.severity is Severity.ERROR for f in findings) else 0


def cmd_migrate(args, cfg: Config) -> int:
    from keystones import migrate as migrate_mod

    resolved, _findings, _ = collect(cfg, None)
    outcomes = migrate_mod.plan(cfg, resolved, _entries(cfg))
    if not outcomes:
        print("keystones: every entry is already on this install's hasher")
        return 0

    for outcome in outcomes:
        state = (
            "provably unchanged"
            if outcome.proved
            else f"needs review: {outcome.reason}"
        )
        print(
            f"  {outcome.entry.id}: {outcome.old_hasher} -> "
            f"{outcome.new_hasher} ({state})"
        )

    blocked = [o for o in outcomes if not o.proved]
    if args.check:
        print(
            f"\nkeystones: {len(outcomes) - len(blocked)} migratable, "
            f"{len(blocked)} blocked"
        )
        return 1 if blocked else 0

    migrated = migrate_mod.apply(cfg, resolved, outcomes)
    _write_index(cfg)
    print(f"\nkeystones: migrated {len(migrated)} entr(ies)")
    # A twin that matched only under the old hasher is not provable across;
    # it is named here rather than left for the next check to find.
    from keystones import twins

    unreconciled = twins.check(cfg.repo_root, [e for e in migrated if e.twins])
    for finding in unreconciled:
        print(f"keystones: needs review: twin {finding.message}", file=sys.stderr)
    if blocked:
        print(
            f"keystones: {len(blocked)} left alone; the code changed too, so they need "
            '`keystones fix -m "<why>"` and their owner',
        )
        return 1
    return 1 if unreconciled else 0


def cmd_list(args, cfg: Config) -> int:
    from keystones.staleness import DurationError, age, humanize, parse_duration

    entries = _entries(cfg)
    if args.category:
        entries = [e for e in entries if e.category == args.category]

    rows = []
    for entry in sorted(entries, key=lambda e: (e.category, e.id)):
        label = ""
        overdue = False
        if entry.review_every:
            try:
                budget = parse_duration(entry.review_every)
            except DurationError:
                label = "bad review_every"
            else:
                rel = str(Path(entry.path).relative_to(cfg.repo_root))
                elapsed = age(cfg.repo_root, rel)
                if elapsed is not None:
                    overdue = elapsed > budget
                    label = f"{humanize(elapsed)} ago" + (" OVERDUE" if overdue else "")
        if args.stale and not overdue:
            continue
        rows.append((entry, label))

    for entry, label in rows:
        print(f"{entry.category:12} {entry.id:28} {entry.target:52} {label}")
    print(f"\n{len(rows)} keystone(s)")
    return 0


def _write_index(cfg: Config) -> None:
    entries = sorted(_entries(cfg), key=lambda e: (e.category, e.id))
    cfg.sidecar_root.mkdir(parents=True, exist_ok=True)
    cfg.index_path.write_text(sidecar.render_index(entries))


def cmd_index(args, cfg: Config) -> int:
    _write_index(cfg)
    print(f"keystones: wrote {cfg.index_path.relative_to(cfg.repo_root)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keystones", description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="verify every keystone")
    check.add_argument("paths", nargs="*")
    check.add_argument(
        "--all", action="store_true", help="whole repo, including global checks"
    )
    check.add_argument(
        "--warn-only", action="store_true", help="report drift without failing"
    )
    check.add_argument("--format", choices=("plain", "github"), default=None)
    check.add_argument("--base", help="ref to compare against for C9; inferred in CI")
    check.add_argument(
        "--no-base",
        action="store_true",
        help="skip the removal check entirely, and say nothing about it",
    )
    check.set_defaults(func=cmd_check)

    fix = sub.add_parser("fix", help="update hashes, source and history")
    fix.add_argument(
        "-m", "--message", help="why the code changed; required for semantic drift"
    )
    fix.add_argument(
        "--id",
        action="append",
        help="only this keystone; repeatable, so one note can cover several",
    )
    fix.set_defaults(func=cmd_fix)

    add = sub.add_parser(
        "add",
        help="write a marker and its sidecar entry, or adopt an existing marker",
    )
    add.add_argument(
        "target",
        nargs="?",
        help=(
            "path/to/file.py::QualName, or path/to/file.py for file scope. "
            "Omit it to adopt a marker already written into the source, or omit "
            "it and --id to fill in every `keystone add` marker interactively."
        ),
    )
    add.add_argument("--id")
    add.add_argument("--category", default="default")
    add.add_argument(
        "--hash",
        dest="hash_kind",
        help="basis to gate on: 'text', or a grammar name. Auto when the file "
        "has exactly one readable basis.",
    )
    add.add_argument(
        "--twin",
        dest="twins",
        action="append",
        help="a same-repo copy that must keep hashing like this keystone, "
        "path.py::Symbol or path for a whole file; repeatable",
    )
    add.add_argument("-m", "--message", help="why this is load-bearing")
    add.add_argument("--review-every", help="staleness budget, e.g. 180d")
    add.add_argument(
        "--depends",
        action="append",
        help="a same-repo symbol this keystone depends on, path.py::Symbol",
    )
    add.set_defaults(func=cmd_add)

    doc = sub.add_parser(
        "doctor", help="verify branch protection actually enforces review"
    )
    doc.add_argument("--required-check", default="keystones")
    doc.add_argument(
        "--repo",
        help="owner/name when there is no origin remote; GITHUB_REPOSITORY too",
    )
    doc.add_argument("--format", choices=("plain", "github"), default=None)
    doc.set_defaults(func=cmd_doctor)

    mig = sub.add_parser(
        "migrate", help="move entries onto this install's hasher, where provable"
    )
    mig.add_argument("--check", action="store_true", help="report without writing")
    mig.set_defaults(func=cmd_migrate)

    listing = sub.add_parser("list", help="show every keystone")
    listing.add_argument("--category")
    listing.add_argument("--stale", action="store_true", help="only overdue keystones")
    listing.set_defaults(func=cmd_list)

    index = sub.add_parser("index", help="regenerate INDEX.md")
    index.set_defaults(func=cmd_index)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load(args.repo_root)
    except ConfigError as exc:
        print(f"keystones: {exc}", file=sys.stderr)
        return 2
    adapters.configure(cfg)
    return args.func(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
