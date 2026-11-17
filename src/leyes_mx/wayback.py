"""Find archived copies of LeyesBiblio files in the Internet Archive.

The Cámara de Diputados only serves the *current* consolidated text of each
law, but the Wayback Machine has been capturing those same ``.doc`` files
since 2006. Every distinct capture is a consolidated text as it stood after
some reform, published by the Cámara itself. This module lists and downloads
those captures through the public CDX API.

File naming on LeyesBiblio changed over time, for example for the Ley Federal
del Trabajo: ``doc/125.doc`` (until ~2014), ``doc/125_120615.doc`` (one file
per reform, ~2014 to 2021) and ``doc/LFT.doc`` (since ~2021). The legacy names
are recovered from archived snapshots of ``index.htm``.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

from bs4 import BeautifulSoup

from .net import PoliteClient

CDX_URL = "https://web.archive.org/cdx/search/cdx"
LEYESBIBLIO_HOST = "diputados.gob.mx/LeyesBiblio/"

# Snapshots of index.htm used to recover the legacy file names of each law.
INDEX_SNAPSHOT_YEARS = (2007, 2010, 2013, 2015, 2017, 2019, 2021, 2023, 2025)


@dataclass(frozen=True)
class Capture:
    timestamp: str  # yyyymmddhhmmss
    original: str
    digest: str

    @property
    def raw_url(self) -> str:
        """URL of the archived bytes, without the Wayback toolbar (``id_``)."""
        return f"https://web.archive.org/web/{self.timestamp}id_/{self.original}"

    @property
    def view_url(self) -> str:
        return f"https://web.archive.org/web/{self.timestamp}/{self.original}"

    @property
    def day(self) -> str:
        t = self.timestamp
        return f"{t[0:4]}-{t[4:6]}-{t[6:8]}"


def cdx_query(client: PoliteClient, url_pattern: str, **params: str) -> list[list[str]]:
    query = {
        "url": url_pattern,
        "output": "json",
        "fl": "timestamp,original,statuscode,digest",
        "filter": "statuscode:200",
        "collapse": "digest",
    }
    query.update(params)
    qs = "&".join(f"{k}={quote(v, safe='*/:')}" for k, v in query.items())
    data = client.get(f"{CDX_URL}?{qs}")
    if not data.strip():
        return []
    rows = json.loads(data)
    return rows[1:] if rows else []


def list_captures(client: PoliteClient, paths: list[str]) -> list[Capture]:
    """All distinct captures (by content digest) of the given LeyesBiblio paths.

    A path ending in ``*`` is a prefix query, e.g. ``doc/125_*``.
    """
    seen: set[str] = set()
    captures: list[Capture] = []
    for path in paths:
        for row in cdx_query(client, LEYESBIBLIO_HOST + path):
            timestamp, original, _status, digest = row[:4]
            if digest in seen or not original.lower().endswith(".doc"):
                continue
            seen.add(digest)
            captures.append(Capture(timestamp, original, digest))
    captures.sort(key=lambda c: c.timestamp)
    return captures


def closest_index_snapshot(client: PoliteClient, year: int) -> Capture | None:
    rows = cdx_query(
        client,
        LEYESBIBLIO_HOST + "index.htm",
        **{"from": str(year), "to": str(year), "limit": "1"},
    )
    if not rows:
        return None
    timestamp, original, _status, digest = rows[0][:4]
    return Capture(timestamp, original, digest)


_REF_RE = re.compile(r"(?:^|/)ref/([a-z0-9_]+)\.htm$", re.IGNORECASE)
_DOC_RE = re.compile(r"(?:^|/)(doc/[^/]+\.doc)$", re.IGNORECASE)


def normalize_name(name: str) -> str:
    """Accent- and case-insensitive key for law names across index versions."""
    text = unicodedata.normalize("NFD", name.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def doc_paths(index_html: str) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """``doc/...`` paths of each row of an index, keyed by reform page and by name.

    Recent indexes put a link to ``ref/<key>.htm`` in each row; older ones do
    not, so rows are also keyed by the normalized text of their first link.
    """
    soup = BeautifulSoup(index_html, "html.parser")
    by_ref: dict[str, set[str]] = {}
    by_name: dict[str, set[str]] = {}
    for tr in soup.find_all("tr"):
        if tr.find("tr"):
            continue  # only innermost rows
        refs, docs = set(), set()
        for a in tr.find_all("a"):
            href = (a.get("href") or "").strip()
            if m := _REF_RE.search(href):
                refs.add(m.group(1).lower())
            elif m := _DOC_RE.search(href):
                docs.add(m.group(1))
        if not docs:
            continue
        if len(refs) == 1:
            by_ref.setdefault(next(iter(refs)), set()).update(docs)
        # The name is whatever precedes the first "DOF dd-mm-yyyy" in the row.
        row_text = " ".join(tr.get_text(" ").split())
        name = re.sub(r"^\d+\s*", "", re.split(r"\bDOF\b", row_text)[0]).strip()
        if len(name) > 8:
            by_name.setdefault(normalize_name(name), set()).update(docs)
    return by_ref, by_name


def path_patterns(paths: set[str]) -> list[str]:
    """Turn concrete file names into CDX patterns.

    ``doc/125_120615.doc`` becomes the prefix ``doc/125_*`` so that every
    dated file of that law is found, not only the ones seen in a snapshot.
    """
    patterns: set[str] = set()
    for path in paths:
        m = re.match(r"^(doc/[^/_]+)_[^/]*\.doc$", path, re.IGNORECASE)
        patterns.add(f"{m.group(1)}_*" if m else path)
    return sorted(patterns)
