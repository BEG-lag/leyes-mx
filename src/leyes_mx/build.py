"""Download, convert and write laws; detect and commit new reforms."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .catalog import Law, load_catalog, resolve
from .doc import read_blocks
from .gitutil import commit_as_bot, git, header_notes, reform_message
from .net import PoliteClient
from .parse import ParsedLaw, Validation, parse_law, validate_articles
from .render import SourceInfo, read_front_matter, render_law, render_reforms
from .sources import INDEX_URL, IndexEntry, ReformHistory, decode_html, parse_index, parse_reform_page

log = logging.getLogger(__name__)


@dataclass
class Workspace:
    root: Path

    @property
    def cache(self) -> Path:
        return self.root / ".cache"

    @property
    def leyes(self) -> Path:
        return self.root / "leyes"

    @property
    def data(self) -> Path:
        return self.root / "data"

    def law_path(self, slug: str) -> Path:
        return self.leyes / f"{slug}.md"

    def reforms_md_path(self, slug: str) -> Path:
        return self.leyes / f"{slug}.reformas.md"

    def reforms_json_path(self, slug: str) -> Path:
        return self.data / "reformas" / f"{slug}.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_index(client: PoliteClient, ws: Workspace, offline: bool = False) -> list[IndexEntry]:
    cached = ws.cache / "index.htm"
    if offline and cached.exists():
        data = cached.read_bytes()
    else:
        data = client.get(INDEX_URL)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return parse_index(decode_html(data))


def load_laws(
    client: PoliteClient, ws: Workspace, offline: bool = False, slugs: list[str] | None = None
) -> tuple[dict[str, str], list[Law]]:
    groups, entries = load_catalog(ws.root / "catalog.yml")
    if slugs:
        unknown = set(slugs) - {e.slug for e in entries}
        if unknown:
            raise SystemExit(f"unknown slug(s): {', '.join(sorted(unknown))}")
        entries = [e for e in entries if e.slug in slugs]
    return groups, resolve(entries, fetch_index(client, ws, offline=offline))


def fetch_doc(client: PoliteClient, ws: Workspace, law: Law, offline: bool = False) -> tuple[bytes, date]:
    """The current .doc of a law (from the cache when offline)."""
    if not law.index.doc_url:
        raise LookupError(f"{law.slug}: no .doc link in the index")
    cached = ws.cache / "sources" / f"{law.slug}.doc"
    stamp = cached.with_suffix(".retrieved")
    if offline and cached.exists():
        return cached.read_bytes(), date.fromisoformat(stamp.read_text().strip())
    data = client.get(law.index.doc_url)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(data)
    today = date.today()
    stamp.write_text(today.isoformat())
    return data, today


def convert(data: bytes) -> tuple[ParsedLaw, Validation]:
    parsed = parse_law(read_blocks(data))
    return parsed, validate_articles(parsed.articles)


def render(law: Law, data: bytes, source: SourceInfo) -> tuple[str, ParsedLaw, Validation]:
    parsed, validation = convert(data)
    text = render_law(
        parsed,
        title=law.name,
        abbreviation=law.abbreviation,
        slug=law.slug,
        published=law.published,
        source=source,
        reforms_url=law.ref_url,
    )
    return text, parsed, validation


def fetch_reforms(client: PoliteClient, ws: Workspace, law: Law, offline: bool = False) -> ReformHistory:
    cached = ws.cache / "ref" / f"{law.slug}.htm"
    if offline and cached.exists():
        data = cached.read_bytes()
    else:
        data = client.get(law.ref_url)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return parse_reform_page(decode_html(data), law.ref_url)


def write_reforms(ws: Workspace, law: Law, history: ReformHistory) -> list[Path]:
    md = ws.reforms_md_path(law.slug)
    js = ws.reforms_json_path(law.slug)
    js.parent.mkdir(parents=True, exist_ok=True)
    md.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "slug": law.slug,
        "title": law.name,
        "abbreviation": law.abbreviation,
        "published_dof": law.published.isoformat() if law.published else None,
        **history.to_json(),
    }
    js.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md.write_text(render_reforms(law.name, law.slug, history, law.published), encoding="utf-8")
    return [md, js]


def _same_text(old: str, new: str) -> bool:
    """Compare two renderings ignoring retrieval metadata."""

    def strip(text: str) -> str:
        return "\n".join(line for line in text.splitlines() if not line.startswith(("retrieved:", "source_sha256:")))

    return strip(old) == strip(new)


@dataclass
class UpdateResult:
    slug: str
    status: str  # unchanged | updated | new | error
    last_reform: date | None = None
    detail: str = ""


def update(
    client: PoliteClient,
    ws: Workspace,
    slugs: list[str] | None = None,
    *,
    full: bool = False,
    commit: bool = False,
    reforms: bool = True,
) -> list[UpdateResult]:
    """Check LeyesBiblio for changed laws, regenerate them and optionally commit.

    By default a law is downloaded again only when the index shows a different
    last-reform date than the file in ``leyes/``. With ``full`` every source
    file is downloaded and compared by content hash, which also catches silent
    corrections made by the Cámara.
    """
    _, laws = load_laws(client, ws, slugs=slugs)
    state = load_index_dates(ws)
    before = dict(state)
    results: list[UpdateResult] = []
    pending: list[tuple[Law, Path, ParsedLaw, ReformHistory | None, date | None]] = []
    for law in laws:
        path = ws.law_path(law.slug)
        old_text = path.read_text(encoding="utf-8") if path.exists() else ""
        old = read_front_matter(old_text)
        index_date = str(law.last_reform or law.published)
        old_date = str(old.get("last_reform_dof") or old.get("published_dof"))
        seen_date = state.get(law.slug)
        state[law.slug] = index_date
        # The index and the file can disagree for a while (the Cámara updates
        # them separately), so a law is fetched when the index date moves, not
        # every day the two differ.
        if old and not full and index_date in (old_date, seen_date):
            results.append(UpdateResult(law.slug, "unchanged", _to_date(index_date)))
            continue
        try:
            data, retrieved = fetch_doc(client, ws, law)
            digest = sha256(data)
            if old and old.get("source_sha256") == digest:
                results.append(UpdateResult(law.slug, "unchanged", _to_date(index_date), "same file"))
                continue
            source = SourceInfo(url=law.index.doc_url or "", sha256=digest, retrieved=retrieved)
            text, parsed, validation = render(law, data, source)
        except Exception as exc:  # keep going with the other laws
            log.exception("%s failed", law.slug)
            results.append(UpdateResult(law.slug, "error", detail=str(exc)))
            continue
        if old_text and _same_text(old_text, text):
            results.append(UpdateResult(law.slug, "unchanged", parsed.last_reform, "same text"))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        history = fetch_reforms(client, ws, law) if reforms else None
        if history is not None:
            write_reforms(ws, law, history)
        status = "updated" if old_text else "new"
        detail = (
            ""
            if validation.ok
            else f"validation: {len(validation.missing)} missing, {len(validation.duplicates)} duplicated"
        )
        results.append(UpdateResult(law.slug, status, parsed.last_reform or law.published, detail))
        pending.append((law, path, parsed, history, _to_date(old.get("last_reform_dof"))))

    if commit:
        # Oldest reform first, so the log stays in legal order.
        pending.sort(key=lambda p: (p[2].last_reform or p[0].published or date.min, p[0].slug))
        for law, path, parsed, history, previous in pending:
            when = parsed.last_reform or law.published
            is_new = previous is None and not (ws.law_path(law.slug).exists() and _tracked(ws, path))
            decrees = history.decrees_between(previous, when) if history and when and not is_new else []
            message = reform_message(
                law.abbreviation,
                when,
                decrees,
                first=is_new,
                original=parsed.last_reform is None,
                source_url=law.index.doc_url,
                notes=header_notes(parsed.header, when),
            )
            paths = [str(path.relative_to(ws.root))]
            if history is not None:
                paths += [
                    str(ws.reforms_md_path(law.slug).relative_to(ws.root)),
                    str(ws.reforms_json_path(law.slug).relative_to(ws.root)),
                ]
            commit_as_bot(ws.root, paths, message, when)
    if state != before:
        save_index_dates(ws, state)
        if commit:
            commit_as_bot(
                ws.root,
                [str(index_dates_path(ws).relative_to(ws.root))],
                "chore(data): record LeyesBiblio index dates\n",
                None,
            )
    return results


def index_dates_path(ws: Workspace) -> Path:
    return ws.data / "indice.json"


def load_index_dates(ws: Workspace) -> dict[str, str]:
    path = index_dates_path(ws)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_index_dates(ws: Workspace, state: dict[str, str]) -> None:
    """Last-change date shown by the LeyesBiblio index for each law."""
    path = index_dates_path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(state.items())), indent=1) + "\n", encoding="utf-8")


def _tracked(ws: Workspace, path: Path) -> bool:
    """True if git already tracks the file (so this is an update, not a new law)."""
    try:
        git(ws.root, "ls-files", "--error-unmatch", str(path.relative_to(ws.root)))
    except RuntimeError:
        return False
    return True


def _to_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value)
    return None
