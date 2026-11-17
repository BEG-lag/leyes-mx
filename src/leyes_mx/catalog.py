"""The curated list of laws (``catalog.yml``) joined with the LeyesBiblio index."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from .sources import IndexEntry

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "catalog.yml"


@dataclass
class CatalogEntry:
    key: str
    slug: str
    group: str
    ref_url: str | None = None
    former_names: list[str] = field(default_factory=list)
    history: bool = True


@dataclass
class Law:
    """A catalog entry resolved against the current index."""

    entry: CatalogEntry
    index: IndexEntry

    @property
    def slug(self) -> str:
        return self.entry.slug

    @property
    def name(self) -> str:
        return display_name(self.index.name)

    @property
    def abbreviation(self) -> str:
        """The Cámara's own abbreviation, taken from the file name when possible."""
        if self.index.doc_url:
            stem = self.index.doc_url.rsplit("/", 1)[-1].removesuffix(".doc")
            if re.fullmatch(r"[A-Za-z]+", stem) and stem.upper() == self.entry.key.upper():
                return stem
        return self.entry.key.upper()

    @property
    def ref_url(self) -> str:
        return self.entry.ref_url or self.index.ref_url or ""

    @property
    def published(self) -> date | None:
        return self.index.published

    @property
    def last_reform(self) -> date | None:
        return self.index.last_reform


def display_name(name: str) -> str:
    """``LEY Federal del Trabajo`` -> ``Ley Federal del Trabajo``."""
    first, _, rest = name.partition(" ")
    if first.isupper() and len(first) > 1:
        first = first.capitalize()
    return f"{first} {rest}".strip()


def load_catalog(path: Path = CATALOG_PATH) -> tuple[dict[str, str], list[CatalogEntry]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = [CatalogEntry(**item) for item in data["laws"]]
    slugs = [e.slug for e in entries]
    if len(slugs) != len(set(slugs)):
        raise ValueError("duplicate slug in catalog")
    return data.get("groups", {}), entries


def resolve(entries: list[CatalogEntry], index: list[IndexEntry]) -> list[Law]:
    by_key = {e.abbr.lower(): e for e in index if e.abbr}
    laws = []
    missing = []
    for entry in entries:
        found = by_key.get(entry.key.lower())
        if found is None:
            missing.append(entry.key)
            continue
        laws.append(Law(entry, found))
    if missing:
        raise LookupError(f"not in LeyesBiblio index: {', '.join(missing)}")
    return laws
