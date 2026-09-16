"""Command line entry point. This is what the pre-commit hooks invoke."""

from __future__ import annotations

import argparse
import datetime
import subprocess
import sys
from pathlib import Path

from keystones import adapters, dependencies, gitref, sidecar
from keystones.checks import run_all
from keystones.config import Config, ConfigError, load
from keystones.discovery import collect
from keystones.models import Entry, Marker, Scope, Severity


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


def _report(findings, fmt: str) -> None:
    for finding in findings:
        line = finding.format_github() if fmt == "github" else finding.format_plain()
        print(line, file=sys.stderr)


def _entries(cfg: Config) -> list[Entry]:
    return sidecar.load_all(cfg.sidecar_root, cfg.categories)


def cmd_check(args, cfg: Config) -> int:
    paths = None if args.all else (args.paths or None)
    scoped = paths is not None
    base = None
    if not scoped:
        base = gitref.resolve_base(cfg.repo_root, args.base)
        if base is None and not args.no_base:
            print(
                "keystones: no base ref available, skipping C9 (removal check). "
                "Pass --base <ref>, or --no-base to silence this.",
                file=sys.stderr,
            )
    resolved, findings, skipped = collect(cfg, paths)
    findings = findings + run_all(
        cfg,
        resolved,
        _entries(cfg),
        scoped=scoped,
        warn_only=args.warn_only,
        base=base,
        skipped=skipped,
    )
    _report(findings, args.format)
    errors = [f for f in findings if f.severity is Severity.ERROR]
    if not findings:
        print(f"keystones: {len(resolved)} keystone(s) verified")
    return 1 if errors else 0


def cmd_fix(args, cfg: Config) -> int:
    resolved, findings, _ = collect(cfg, None)
    if findings:
        _report(findings, "plain")
        return 1

    entries = {entry.id: entry for entry in _entries(cfg)}
    author = _git_author(cfg.repo_root)
    today = datetime.date.today().isoformat()
    changed: list[str] = []

    for item in resolved:
        entry = entries.get(item.marker.id)
        if entry is None or (args.id and entry.id != args.id):
            continue
        src = (cfg.repo_root / item.marker.path).read_text()
        semantic, text = item.adapter.hashes(src, item.target)
        target_str = str(item.target)
        try:
            depends_hash = dependencies.combined_hash(cfg.repo_root, entry.depends)
        except dependencies.UnresolvedDependency as exc:
            print(f"keystones: {entry.id}: {exc}", file=sys.stderr)
            return 1
        # An entry with no dependencies stores no depends_hash, so the absent
        # value has to compare equal to the empty one or every move looks semantic.
        stored_depends = entry.depends_hash or dependencies.EMPTY
        semantic_changed = semantic != entry.semantic or depends_hash != stored_depends
        if not semantic_changed and text == entry.text and target_str == entry.target:
            continue
        if semantic_changed and not args.message:
            print(
                f"keystones: '{entry.id}' changed semantically; -m is required",
                file=sys.stderr,
            )
            return 1
        if target_str != entry.target and not semantic_changed:
            entry.history.insert(0, f"{today} - moved to {target_str}. {author}")
        elif args.message:
            entry.history.insert(0, f"{today} - {args.message} {author}")
        entry.target = target_str
        entry.semantic = semantic
        entry.text = text
        entry.hasher = item.adapter.hasher_id_for_path(item.marker.path)
        entry.source = item.adapter.canonical_source(src, item.target)
        entry.depends_hash = dependencies.combined_hash(cfg.repo_root, entry.depends)
        sidecar.write(cfg.sidecar_path(entry.category, entry.id), entry)
        changed.append(entry.id)

    _write_index(cfg)
    print(
        f"keystones: updated {len(changed)} entr(ies): {', '.join(changed) or 'none'}"
    )
    return 0


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


def _adopt(args, cfg: Config) -> int:
    """Create the sidecar for a marker already written into the source.

    This is the path C1 points at, and the only one that works for a region,
    whose boundaries are already in the file.
    """
    resolved, findings, _ = collect(cfg, None)
    if findings:
        _report(findings, "plain")
        return 1

    match = [item for item in resolved if item.marker.id == args.id]
    if not match:
        print(
            f"keystones: no marker with id '{args.id}' in the tree. Pass a target to "
            "write one, or check the id.",
            file=sys.stderr,
        )
        return 1
    if len(match) > 1:
        where = ", ".join(f"{m.marker.path}:{m.marker.lineno}" for m in match)
        print(
            f"keystones: id '{args.id}' appears more than once: {where}",
            file=sys.stderr,
        )
        return 1

    item = match[0]
    category = item.marker.category
    if cfg.sidecar_path(category, args.id).exists():
        print(f"keystones: '{args.id}' already exists in {category}", file=sys.stderr)
        return 1

    src = (cfg.repo_root / item.marker.path).read_text()
    semantic, text_digest = item.adapter.hashes(src, item.target)
    depends = list(args.depends or [])
    entry = Entry(
        id=args.id,
        category=category,
        target=str(item.target),
        hasher=item.adapter.hasher_id_for_path(item.marker.path),
        semantic=semantic,
        text=text_digest,
        review_every=args.review_every,
        depends=depends,
        depends_hash=dependencies.combined_hash(cfg.repo_root, depends),
        why=args.message,
        source=item.adapter.canonical_source(src, item.target),
        source_lang=_lang_for(item.marker.path),
        history=[
            f"{datetime.date.today().isoformat()} - initial keystone. "
            f"{_git_author(cfg.repo_root)}"
        ],
    )
    sidecar.write(cfg.sidecar_path(category, args.id), entry)
    _write_index(cfg)
    print(f"keystones: adopted '{args.id}' on {item.target}")
    return 0


def cmd_add(args, cfg: Config) -> int:
    if args.target is None:
        return _adopt(args, cfg)
    if "::" in args.target:
        rel, qualname = args.target.split("::", 1)
        scope = Scope.NODE
    else:
        rel, qualname, scope = args.target, None, Scope.FILE

    path = cfg.repo_root / rel
    if not path.is_file():
        print(f"keystones: no such file {rel}", file=sys.stderr)
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
    if scope is Scope.FILE:
        target = adapter.resolve(src, Marker(args.id, args.category, scope, rel, 1))
        insert_at, indent = 1, ""
    else:
        target = adapter.target_for_qualname(rel, src, qualname)
        if target is None:
            print(f"keystones: {qualname} not found in {rel}", file=sys.stderr)
            return 1
        first = src.splitlines()[target.start - 1]
        insert_at = target.start
        indent = first[: len(first) - len(first.lstrip())]

    keyword = "keystone" if args.category == "default" else f"keystone({args.category})"
    lines = src.splitlines(keepends=True)
    lines.insert(insert_at - 1, f"{indent}# {keyword}: {args.id}\n")
    path.write_text("".join(lines))

    new_src = path.read_text()
    new_target = (
        adapter.target_for_qualname(rel, new_src, qualname)
        if qualname
        else adapter.resolve(new_src, Marker(args.id, args.category, scope, rel, 1))
    )
    semantic, text = adapter.hashes(new_src, new_target)
    entry = Entry(
        id=args.id,
        category=args.category,
        target=str(new_target),
        hasher=adapter.hasher_id_for_path(rel),
        semantic=semantic,
        text=text,
        review_every=args.review_every,
        depends=list(args.depends or []),
        depends_hash=dependencies.combined_hash(
            cfg.repo_root, list(args.depends or [])
        ),
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
        findings = doctor.run(cfg.repo_root, args.required_check)
    except doctor.Unavailable as exc:
        print(f"keystones doctor: skipped, {exc}", file=sys.stderr)
        return 0
    _report(findings, args.format)
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
    print(f"\nkeystones: migrated {migrated} entr(ies)")
    if blocked:
        print(
            f"keystones: {len(blocked)} left alone; the code changed too, so they need "
            '`keystones fix -m "<why>"` and their owner',
        )
        return 1
    return 0


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
    check.add_argument("--format", choices=("plain", "github"), default="plain")
    check.add_argument("--base", help="ref to compare against for C9; inferred in CI")
    check.add_argument(
        "--no-base", action="store_true", help="skip C9 without a notice"
    )
    check.set_defaults(func=cmd_check)

    fix = sub.add_parser("fix", help="update hashes, source and history")
    fix.add_argument(
        "-m", "--message", help="why the code changed; required for semantic drift"
    )
    fix.add_argument("--id", help="only this keystone")
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
            "Omit it to adopt a marker already written into the source."
        ),
    )
    add.add_argument("--id", required=True)
    add.add_argument("--category", default="default")
    add.add_argument("-m", "--message", required=True, help="why this is load-bearing")
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
    doc.add_argument("--format", choices=("plain", "github"), default="plain")
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
    return args.func(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
