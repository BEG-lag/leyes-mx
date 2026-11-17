"""Reconstruct the reform history of each law from archived consolidated texts.

Pipeline:

1. ``discover``: recover every file name a law has had on LeyesBiblio from
   archived snapshots of ``index.htm`` (see :mod:`leyes_mx.wayback`).
2. ``collect``: list the Wayback Machine captures of those files, download each
   distinct one, read its "Última reforma publicada DOF dd-mm-yyyy" header and
   keep one capture per reform date (the latest one). Versions older than the
   law's own publication date belong to an abrogated namesake and are dropped.
3. ``build_branch``: write an orphan branch where each commit is one version of
   one law, in global chronological order, authored and committed by the bot
   with the DOF date of the reform. The current text from LeyesBiblio closes
   the history of every law.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .build import Workspace, convert, fetch_doc, fetch_reforms, sha256
from .catalog import Law
from .gitutil import BOT_EMAIL, BOT_NAME, bot_env, git, header_notes, reform_message
from .net import PoliteClient
from .render import SourceInfo, render_law
from .sources import ReformHistory, decode_html
from .wayback import (
    INDEX_SNAPSHOT_YEARS,
    Capture,
    closest_index_snapshot,
    doc_paths,
    list_captures,
    normalize_name,
    path_patterns,
)

log = logging.getLogger(__name__)

HISTORY_BRANCH = "historia"


def _clean_original(url: str) -> str:
    """``http://www.diputados.gob.mx:80/LeyesBiblio/...`` -> canonical https URL."""
    return re.sub(r"^https?://(?:www\.)?diputados\.gob\.mx(?::80)?/", "https://www.diputados.gob.mx/", url)


# --------------------------------------------------------------------------
# 1. Legacy file names
# --------------------------------------------------------------------------


def discover(client: PoliteClient, ws: Workspace, laws: list[Law]) -> dict[str, list[str]]:
    snapshots_dir = ws.cache / "wayback" / "index"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    by_ref: dict[str, set[str]] = {}
    by_name: dict[str, set[str]] = {}
    for year in INDEX_SNAPSHOT_YEARS:
        cached = snapshots_dir / f"{year}.htm"
        if not cached.exists():
            capture = closest_index_snapshot(client, year)
            if capture is None:
                continue
            cached.write_bytes(client.get(capture.raw_url))
        refs, names = doc_paths(decode_html(cached.read_bytes()))
        for k, v in refs.items():
            by_ref.setdefault(k, set()).update(v)
        for k, v in names.items():
            by_name.setdefault(k, set()).update(v)

    result: dict[str, list[str]] = {}
    for law in laws:
        paths: set[str] = set(by_ref.get(law.entry.key, set()))
        for name in [law.index.name, *law.entry.former_names]:
            paths |= by_name.get(normalize_name(name), set())
        if law.index.doc_url:
            paths.add(law.index.doc_url.split("/LeyesBiblio/", 1)[1])
        result[law.slug] = path_patterns(paths)
    out = ws.data / "historia" / "rutas.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


# --------------------------------------------------------------------------
# 2. Archived versions
# --------------------------------------------------------------------------


@dataclass
class Version:
    slug: str
    day: date
    data: bytes
    source: SourceInfo
    capture: Capture | None = None  # None for the live text
    articles: int = 0
    warnings: list[str] = field(default_factory=list)


def _dated_name(original: str) -> bool:
    return bool(re.search(r"/doc/[^/_]+_[^/]+\.doc$", original, re.IGNORECASE))


def _select_captures(captures: list[Capture]) -> list[Capture]:
    """One capture per dated file name (its content is fixed); all for undated names."""
    latest: dict[str, Capture] = {}
    keep: list[Capture] = []
    for c in captures:
        key = _clean_original(c.original).lower()
        if _dated_name(c.original):
            if key not in latest or c.timestamp > latest[key].timestamp:
                latest[key] = c
        else:
            keep.append(c)
    return sorted(keep + list(latest.values()), key=lambda c: c.timestamp)


def download_capture(client: PoliteClient, ws: Workspace, slug: str, capture: Capture) -> bytes:
    cached = ws.cache / "wayback" / slug / f"{capture.timestamp}_{capture.digest[:8]}.doc"
    if cached.exists():
        return cached.read_bytes()
    data = client.get(capture.raw_url)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(data)
    return data


def bisect_versions(items: list, day_of: Callable[[object], date | None]) -> None:
    """Call ``day_of`` on enough items to see every distinct version.

    ``items`` are captures of one file in time order, so their versions never
    go backwards: when both ends of a range share a date, so does everything
    in between. Unreadable items (``None``) force a split.
    """
    if not items:
        return

    def walk(lo: int, hi: int) -> None:
        if hi - lo <= 1:
            return
        first, last = day_of(items[lo]), day_of(items[hi])
        if first is not None and first == last:
            return
        mid = (lo + hi) // 2
        day_of(items[mid])
        walk(lo, mid)
        walk(mid, hi)

    day_of(items[0])
    day_of(items[-1])
    walk(0, len(items) - 1)


def collect(
    client: PoliteClient,
    archive_client: PoliteClient,
    ws: Workspace,
    law: Law,
    patterns: list[str],
    max_captures: int = 120,
) -> tuple[list[Version], list[dict]]:
    """Archived versions of a law plus the current one, oldest first.

    Files with a date in their name (``doc/125_120615.doc``) hold one version
    each, so only their latest capture is read. A file with a fixed name
    (``doc/LFT.doc``) changes over time; since versions only move forward, if
    two captures carry the same reform date every capture between them does
    too. Bisection over the captures of each such file therefore finds every
    distinct version while downloading far fewer files.
    """
    log_rows: list[dict] = []
    captures_file = ws.cache / "wayback" / law.slug / "captures.json"
    if captures_file.exists():
        captures = [Capture(**c) for c in json.loads(captures_file.read_text())]
    else:
        captures = list_captures(archive_client, patterns)
        captures_file.parent.mkdir(parents=True, exist_ok=True)
        captures_file.write_text(json.dumps([c.__dict__ for c in captures], indent=1))
    selected = _select_captures(captures)

    live_data, retrieved = fetch_doc(client, ws, law, offline=True)
    live_parsed, _ = convert(live_data)
    live_day = live_parsed.last_reform or law.published
    by_day: dict[date, Version] = {}
    seen: dict[str, date | None] = {}
    budget = [max_captures]

    def examine(capture: Capture) -> date | None:
        if capture.timestamp + capture.digest in seen:
            return seen[capture.timestamp + capture.digest]
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        row: dict = {"capture": capture.timestamp, "url": _clean_original(capture.original)}
        day = None
        try:
            data = download_capture(client=archive_client, ws=ws, slug=law.slug, capture=capture)
            parsed, validation = convert(data)
        except Exception as exc:  # unreadable or not a .doc (e.g. an HTML error page)
            row["skipped"] = f"unreadable: {exc}"[:200]
        else:
            day = parsed.last_reform or law.published
            row["dof"] = day.isoformat() if day else None
            if day is None or (law.published and day < law.published):
                row["skipped"] = "older than this law (abrogated namesake or no date)"
            elif day > live_day:
                row["skipped"] = "newer than the current text"
            elif not parsed.articles:
                row["skipped"] = "no articles parsed"
            else:
                current = by_day.get(day)
                if current is None or (current.capture and current.capture.timestamp < capture.timestamp):
                    by_day[day] = Version(
                        slug=law.slug,
                        day=day,
                        data=data,
                        capture=capture,
                        articles=parsed.article_count,
                        source=SourceInfo(
                            url=_clean_original(capture.original),
                            sha256=sha256(data),
                            retrieved=date.fromisoformat(capture.day),
                            archived_url=capture.view_url,
                        ),
                        warnings=[] if validation.ok else [f"{len(validation.missing)} missing article numbers"],
                    )
                row["kept"] = True
        log_rows.append(row)
        seen[capture.timestamp + capture.digest] = day
        return day

    groups: dict[str, list[Capture]] = {}
    for capture in selected:
        if _dated_name(capture.original):
            examine(capture)
        else:
            groups.setdefault(_clean_original(capture.original).lower(), []).append(capture)
    for group in groups.values():
        bisect_versions(group, examine)

    by_day[live_day] = Version(
        slug=law.slug,
        day=live_day,
        data=live_data,
        articles=live_parsed.article_count,
        source=SourceInfo(url=law.index.doc_url or "", sha256=sha256(live_data), retrieved=retrieved),
    )
    log_rows.sort(key=lambda r: r["capture"])
    return [by_day[d] for d in sorted(by_day)], log_rows


# --------------------------------------------------------------------------
# 3. The history branch
# --------------------------------------------------------------------------


@dataclass
class PlannedCommit:
    law: Law
    version: Version
    message: str
    text: str


def plan(laws: list[Law], versions: dict[str, list[Version]], reforms: dict[str, ReformHistory]) -> list[PlannedCommit]:
    commits: list[PlannedCommit] = []
    for law in laws:
        previous: Version | None = None
        last_text = None
        for version in versions.get(law.slug, []):
            parsed, _ = convert(version.data)
            text = render_law(
                parsed,
                title=law.name,
                abbreviation=law.abbreviation,
                slug=law.slug,
                published=law.published,
                source=version.source,
                reforms_url=law.ref_url,
            )
            if last_text is not None and _body(last_text) == _body(text):
                continue  # same text, only metadata differs
            history = reforms.get(law.slug)
            decrees = (
                history.decrees_between(previous.day if previous else None, version.day) if history and previous else []
            )
            message = reform_message(
                law.abbreviation,
                version.day,
                decrees,
                first=previous is None,
                original=previous is None and version.day == law.published,
                source_url=version.source.url,
                archived_url=version.source.archived_url,
                notes=header_notes(parsed.header, version.day),
            )
            commits.append(PlannedCommit(law, version, message, text))
            previous, last_text = version, text
    commits.sort(key=lambda c: (c.version.day, c.law.slug))
    return commits


def _body(text: str) -> str:
    end = text.find("\n---\n", 4)
    return text[end + 5 :] if end > 0 else text


def build_branch(ws: Workspace, commits: list[PlannedCommit], branch: str = HISTORY_BRANCH) -> str:
    """Write the commits on an orphan branch using git plumbing (no checkout)."""
    index_file = ws.root / ".git" / "leyes-historia.index"
    env_base = {"GIT_INDEX_FILE": str(index_file)}
    if index_file.exists():
        index_file.unlink()
    git(ws.root, "read-tree", "--empty", env={**bot_env(), **env_base})
    parent: str | None = None
    for c in commits:
        env = {**bot_env(c.version.day), **env_base}
        blob = git(ws.root, "hash-object", "-w", "--stdin", env=env, input=c.text).strip()
        path = f"leyes/{c.law.slug}.md"
        git(ws.root, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
        tree = git(ws.root, "write-tree", env=env).strip()
        args = ["commit-tree", tree]
        if parent:
            args += ["-p", parent]
        parent = git(ws.root, *args, env=env, input=c.message).strip()
    index_file.unlink(missing_ok=True)
    if parent is None:
        raise RuntimeError("no commits to write")
    git(ws.root, "update-ref", f"refs/heads/{branch}", parent)
    return parent


def merge_branch(ws: Workspace, branch: str = HISTORY_BRANCH, versions: int = 0, laws: int = 0) -> str:
    """Merge the reconstructed history into the current branch (as the bot, dated now)."""
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    message = (
        "Importa el historial reconstruido de las leyes\n\n"
        f"{versions} versiones de {laws} leyes, una por commit, cada una fechada con la "
        "publicación en el DOF de la reforma que refleja. Fuente: textos consolidados "
        "de la Cámara de Diputados (LeyesBiblio) archivados por el Internet Archive.\n"
    )
    git(ws.root, "merge", "--no-ff", "--allow-unrelated-histories", "-q", "-m", message, branch, env=bot_env(now))
    return git(ws.root, "rev-parse", "HEAD").strip()


def write_manifest(ws: Workspace, law: Law, versions: list[Version], log_rows: list[dict]) -> Path:
    out = ws.data / "historia" / f"{law.slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "slug": law.slug,
        "abbreviation": law.abbreviation,
        "versions": [
            {
                "dof": v.day.isoformat(),
                "source": v.source.url,
                "archived": v.source.archived_url,
                "sha256": v.source.sha256,
                "articles": v.articles,
                **({"warnings": v.warnings} if v.warnings else {}),
            }
            for v in versions
        ],
        "captures_examined": log_rows,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


__all__ = [
    "BOT_EMAIL",
    "BOT_NAME",
    "HISTORY_BRANCH",
    "build_branch",
    "collect",
    "discover",
    "fetch_reforms",
    "merge_branch",
    "plan",
    "write_manifest",
]
