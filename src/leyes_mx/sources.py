"""Parsers for the Cámara de Diputados "LeyesBiblio" pages.

* ``index.htm`` lists every federal law in force, with its DOF publication
  date, the date of its last reform and links to the consolidated text.
* ``ref/<abbr>.htm`` lists, for one law, the original publication and every
  reform decree with its DOF date and a link to the decree.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://www.diputados.gob.mx/LeyesBiblio/"
INDEX_URL = urljoin(BASE_URL, "index.htm")

_DATE_SLASH = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_DATE_DASH = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_date(text: str) -> date | None:
    """Parse ``dd/mm/yyyy`` or ``dd-mm-yyyy`` (the formats LeyesBiblio uses)."""
    m = _DATE_SLASH.search(text) or _DATE_DASH.search(text)
    if not m:
        return None
    d, mth, y = (int(g) for g in m.groups())
    return date(y, mth, d)


def decode_html(data: bytes) -> str:
    """LeyesBiblio pages are served as ISO-8859-1 (really cp1252)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


@dataclass
class IndexEntry:
    number: int
    name: str
    ref_url: str | None
    published: date | None
    last_reform: date | None
    doc_url: str | None
    pdf_url: str | None

    @property
    def abbr(self) -> str | None:
        """Abbreviation taken from the reform page name, e.g. ``ref/lft.htm``."""
        if not self.ref_url:
            return None
        return self.ref_url.rsplit("/", 1)[-1].removesuffix(".htm").upper()


def parse_index(html: str, base_url: str = INDEX_URL) -> list[IndexEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries: dict[int, IndexEntry] = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 4:
            continue
        number_text = _clean(tds[0].get_text())
        if not number_text.isdigit():
            continue
        name_cell = tds[1]
        link = name_cell.find("a")
        name = _clean(link.get_text()) if link else ""
        if not name:
            continue
        published = parse_date(name_cell.get_text())
        reform_text = _clean(tds[2].get_text())
        last_reform = parse_date(reform_text)
        doc_url = pdf_url = None
        for a in tds[3].find_all("a"):
            href = a.get("href") or ""
            if href.lower().startswith("doc/") and href.lower().endswith(".doc"):
                doc_url = urljoin(base_url, href)
            elif href.lower().startswith("pdf/"):
                pdf_url = urljoin(base_url, href)
        number = int(number_text)
        entries.setdefault(
            number,
            IndexEntry(
                number=number,
                name=name,
                ref_url=urljoin(base_url, link["href"]) if link and link.get("href") else None,
                published=published,
                last_reform=last_reform,
                doc_url=doc_url,
                pdf_url=pdf_url,
            ),
        )
    return [entries[k] for k in sorted(entries)]


@dataclass
class Decree:
    number: int
    title: str
    dof_date: date
    pdf_url: str | None = None
    doc_url: str | None = None

    def to_json(self) -> dict:
        data = asdict(self)
        data["dof_date"] = self.dof_date.isoformat()
        return data


@dataclass
class ReformHistory:
    ref_url: str
    original_date: date | None = None
    original_url: str | None = None
    errata: list[tuple[date, str | None]] = field(default_factory=list)
    decrees: list[Decree] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "ref_url": self.ref_url,
            "original": {
                "dof_date": self.original_date.isoformat() if self.original_date else None,
                "url": self.original_url,
            },
            "errata": [{"dof_date": d.isoformat(), "url": u} for d, u in self.errata],
            "decrees": [d.to_json() for d in sorted(self.decrees, key=lambda d: d.number)],
        }

    def decrees_on(self, day: date) -> list[Decree]:
        return [d for d in self.decrees if d.dof_date == day]

    def decrees_between(self, after: date | None, until: date) -> list[Decree]:
        """Decrees published in the half-open interval (after, until]."""
        return sorted(
            (d for d in self.decrees if (after is None or d.dof_date > after) and d.dof_date <= until),
            key=lambda d: (d.dof_date, d.number),
        )


def parse_reform_page(html: str, ref_url: str) -> ReformHistory:
    soup = BeautifulSoup(html, "html.parser")
    history = ReformHistory(ref_url=ref_url)
    decrees: dict[int, Decree] = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        number_text = _clean(tds[0].get_text())
        if not number_text.isdigit():
            continue
        body = tds[1]
        rest = " ".join(_clean(td.get_text(" ")) for td in tds[1:])
        m = re.search(r"DOF\s*(\d{1,2}-\d{1,2}-\d{4})", rest)
        if not m:
            # Constitution layout: a separate cell holding just dd/mm/yyyy.
            for td in tds[2:]:
                m = re.fullmatch(r"(\d{1,2}/\d{1,2}/\d{4})", _clean(td.get_text()))
                if m:
                    break
        if not m:
            continue
        pdf_url = image_url = doc_url = None
        for a in tr.find_all("a"):
            href = a.get("href") or ""
            low = href.lower()
            if low.endswith(".doc") and doc_url is None:
                doc_url = urljoin(ref_url, href)
            elif low.endswith("_ima.pdf") and image_url is None:
                image_url = urljoin(ref_url, href)
            elif low.endswith(".pdf") and pdf_url is None:
                pdf_url = urljoin(ref_url, href)
        first_p = body.find("p")
        title = _clean(first_p.get_text(" ") if first_p else body.get_text(" "))
        title = re.sub(r"\s*\|.*$", "", title)
        title = re.sub(r"\s+([.,;:])", r"\1", title)  # "Trabajo ." -> "Trabajo."
        number = int(number_text)
        decrees.setdefault(
            number,
            Decree(
                number=number,
                title=title,
                dof_date=parse_date(m.group(1)),
                pdf_url=pdf_url or image_url,
                doc_url=doc_url,
            ),
        )
    history.decrees = sorted(decrees.values(), key=lambda d: d.number)

    text = soup.get_text(" ")
    m = re.search(r"Publicaci[óo]n Original:\s*\|?\s*DOF\s*(\d{2}-\d{2}-\d{4})", _clean(text))
    if m:
        history.original_date = parse_date(m.group(1))
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if "_orig_" in href.lower() and history.original_url is None and "_fe_" not in href.lower():
            history.original_url = urljoin(ref_url, href)
    seen: set[date] = set()
    head = _clean(text).split("Decretos de Reforma", 1)[0]
    for m in re.finditer(r"Fe de\s+erratas\s*\|\s*DOF\s*(\d{2}-\d{2}-\d{4})", head):
        day = parse_date(m.group(1))
        if day and day not in seen:
            seen.add(day)
            history.errata.append((day, None))
    return history
