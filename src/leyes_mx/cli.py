"""``leyes``: command line interface for leyes-mx."""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from .build import (
    Workspace,
    fetch_doc,
    fetch_reforms,
    load_index_dates,
    load_laws,
    render,
    save_index_dates,
    sha256,
    update,
    write_reforms,
)
from .net import PoliteClient
from .parse import ARTICLE_RANGE_RE, ARTICLE_RE, ArticleRef, validate_articles
from .render import SourceInfo, read_front_matter

log = logging.getLogger("leyes")

REQUIRED_FIELDS = (
    "title",
    "abbreviation",
    "slug",
    "published_dof",
    "last_reform_dof",
    "articles",
    "source",
    "source_sha256",
    "retrieved",
)


def _client(args: argparse.Namespace) -> PoliteClient:
    return PoliteClient(delay=args.delay)


def cmd_build(args: argparse.Namespace) -> int:
    ws = Workspace(Path(args.root))
    client = _client(args)
    _, laws = load_laws(client, ws, offline=args.offline, slugs=args.slugs or None)
    failures = 0
    state = load_index_dates(ws)
    print(f"{'slug':10} {'last reform':12} {'articles':>8}  check")
    for law in laws:
        try:
            data, retrieved = fetch_doc(client, ws, law, offline=args.offline)
            source = SourceInfo(url=law.index.doc_url or "", sha256=sha256(data), retrieved=retrieved)
            path = ws.law_path(law.slug)
            if path.exists():
                old = read_front_matter(path.read_text(encoding="utf-8"))
                if old.get("source_sha256") == source.sha256 and old.get("retrieved"):
                    source.retrieved = date.fromisoformat(str(old["retrieved"]))
            text, parsed, validation = render(law, data, source)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            if not args.no_reforms:
                write_reforms(ws, law, fetch_reforms(client, ws, law, offline=args.offline))
        except Exception as exc:
            failures += 1
            print(f"{law.slug:10} ERROR {exc}")
            continue
        state[law.slug] = str(law.last_reform or law.published)
        check = (
            "ok"
            if validation.ok
            else (
                f"missing {validation.missing[:5]} dup {validation.duplicates[:3]} order {validation.out_of_order[:3]}"
            )
        )
        when = (parsed.last_reform or law.published or "").__str__()
        print(f"{law.slug:10} {when:12} {parsed.article_count:8d}  {check}")
    save_index_dates(ws, state)
    return 1 if failures else 0


def cmd_update(args: argparse.Namespace) -> int:
    ws = Workspace(Path(args.root))
    results = update(_client(args), ws, args.slugs or None, full=args.full, commit=args.commit)
    changed = 0
    for r in results:
        if r.status != "unchanged" or args.verbose:
            print(f"{r.slug:10} {r.status:9} {r.last_reform or ''} {r.detail}")
        changed += r.status in ("updated", "new")
    errors = sum(r.status == "error" for r in results)
    print(f"{changed} changed, {errors} errors, {len(results)} checked")
    return 1 if errors else 0


def markdown_articles(text: str) -> list[ArticleRef]:
    """Body articles of a rendered law, read back from its headings."""
    refs: list[ArticleRef] = []
    for line in text.splitlines():
        m = re.match(r"^#{2,6} (Art[ií]culos? .+)$", line, re.IGNORECASE)
        if not m:
            continue
        label = m.group(1).replace("\\", "")
        if not label.endswith("."):
            label += "."
        if a := ARTICLE_RE.match(label):
            refs.append(ArticleRef(label, int(a.group("num")), (a.group("suf") or "").strip(" .-")))
        elif r := ARTICLE_RANGE_RE.match(label):
            refs.append(ArticleRef(label, int(r.group("a")), last=int(r.group("b"))))
    return refs


def cmd_validate(args: argparse.Namespace) -> int:
    ws = Workspace(Path(args.root))
    problems = 0
    total = 0
    files = sorted(p for p in ws.leyes.glob("*.md") if not p.name.endswith(".reformas.md"))
    if not files:
        print("no laws found in leyes/")
        return 1
    for path in files:
        text = path.read_text(encoding="utf-8")
        fm = read_front_matter(text)
        missing_fields = [f for f in REQUIRED_FIELDS if f not in fm]
        articles = markdown_articles(text)
        v = validate_articles(articles)
        total += v.articles
        issues = []
        if missing_fields:
            issues.append(f"front matter missing {missing_fields}")
        if fm.get("articles") != v.articles:
            issues.append(f"front matter says {fm.get('articles')} articles, headings say {v.articles}")
        if v.missing:
            issues.append(f"gaps in numbering: {v.missing[:8]}")
        if v.duplicates:
            issues.append(f"duplicated: {v.duplicates[:5]}")
        status = "ok" if not issues else "; ".join(issues)
        problems += bool(issues)
        print(f"{path.stem:10} {v.articles:6d} articles  {status}")
    print(f"{len(files)} laws, {total} articles, {problems} with issues")
    return 1 if problems and args.strict else 0


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def _commit_at(root: Path, path: str, when: str) -> str | None:
    rev = _git(root, "rev-list", "-1", f"--before={when} 23:59:59 -0600", "HEAD", "--", path).strip()
    return rev or None


def cmd_diff(args: argparse.Namespace) -> int:
    """Show how a law changed between two dates (by DOF publication date)."""
    root = Path(args.root)
    path = f"leyes/{args.slug}.md"
    old = _commit_at(root, path, args.since)
    new = _commit_at(root, path, args.until) if args.until else _git(root, "rev-parse", "HEAD").strip()
    if old is None:
        first = _git(root, "log", "--reverse", "--format=%ad", "--date=short", "--", path).split()
        print(f"no version of {path} on or before {args.since}; history starts {first[0] if first else '?'}")
        return 1
    cmd = [
        "git",
        "--no-pager",
        "diff",
        "--stat" if args.stat else "--word-diff=color" if args.word else "--color=auto",
        old,
        new,
        "--",
        path,
    ]
    return subprocess.run(cmd, cwd=root).returncode


def cmd_log(args: argparse.Namespace) -> int:
    root = Path(args.root)
    out = _git(root, "log", "--format=%ad  %s", "--date=short", "--", f"leyes/{args.slug}.md")
    print(out, end="")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    from . import history

    ws = Workspace(Path(args.root))
    client = _client(args)
    archive = PoliteClient(delay=args.archive_delay)
    _, laws = load_laws(client, ws, offline=True, slugs=args.slugs or None)
    laws = [law for law in laws if law.entry.history]
    paths_file = ws.data / "historia" / "rutas.json"
    if args.action == "discover" or not paths_file.exists():
        patterns = history.discover(archive, ws, laws)
    else:
        patterns = json.loads(paths_file.read_text(encoding="utf-8"))
    if args.action == "discover":
        for slug, pats in patterns.items():
            print(f"{slug:10} {' '.join(pats)}")
        return 0

    versions: dict[str, list] = {}
    reforms = {}
    for law in laws:
        found, rows = history.collect(
            client, archive, ws, law, patterns.get(law.slug, []), max_captures=args.max_captures
        )
        versions[law.slug] = found
        reforms[law.slug] = fetch_reforms(client, ws, law, offline=True)
        history.write_manifest(ws, law, found, rows)
        days = ", ".join(v.day.strftime("%Y") for v in found)
        print(f"{law.slug:10} {len(found):3d} versions  ({days})", flush=True)
    if args.action == "collect":
        return 0
    commits = history.plan(laws, versions, reforms)
    head = history.build_branch(ws, commits)
    print(f"branch {history.HISTORY_BRANCH}: {len(commits)} commits, head {head[:10]}")
    if args.merge:
        merged = history.merge_branch(ws, versions=len(commits), laws=len(laws))
        print(f"merged into current branch: {merged[:10]}")
    return 0


def cmd_site(args: argparse.Namespace) -> int:
    from .site import build_site

    ws = Workspace(Path(args.root))
    stats = build_site(ws)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leyes", description="Mexican federal law as code.")
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    parser.add_argument("--delay", type=float, default=3.0, help="seconds between requests to the same host")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="download and convert laws into leyes/")
    p.add_argument("slugs", nargs="*")
    p.add_argument("--offline", action="store_true", help="use cached sources only")
    p.add_argument("--no-reforms", action="store_true", help="skip reform timelines")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("update", help="regenerate laws that changed at the source")
    p.add_argument("slugs", nargs="*")
    p.add_argument("--full", action="store_true", help="download every source and compare hashes")
    p.add_argument("--commit", action="store_true", help="commit each changed law as the bot")
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("validate", help="check front matter and article numbering of leyes/*.md")
    p.add_argument("--strict", action="store_true", help="exit 1 if any law has issues")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("diff", help="diff a law between two dates")
    p.add_argument("slug")
    p.add_argument("since", help="YYYY-MM-DD")
    p.add_argument("until", nargs="?", help="YYYY-MM-DD (default: latest)")
    p.add_argument("--word", action="store_true", help="word-level diff")
    p.add_argument("--stat", action="store_true", help="summary only")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("log", help="list the versions of a law")
    p.add_argument("slug")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("history", help="reconstruct history from archived versions")
    p.add_argument("action", choices=["discover", "collect", "import"])
    p.add_argument("slugs", nargs="*")
    p.add_argument("--archive-delay", type=float, default=3.0)
    p.add_argument("--max-captures", type=int, default=120, help="download budget per law")
    p.add_argument("--merge", action="store_true", help="merge the history branch after import")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("site", help="regenerate CHANGES.md and docs/data.json")
    p.set_defaults(func=cmd_site)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
