"""Turn the paragraphs of a consolidated law into structured Markdown.

The input is the list of :class:`~leyes_mx.doc.Block` read from a LeyesBiblio
``.doc``. The output keeps every word of the source and only adds structure:

* the divisions (Libro, Título, Capítulo, Sección) become headings whose
  level depends on which divisions the law actually uses;
* every article of the body becomes a heading, so it gets a GitHub anchor;
* the editorial notes of the Cámara ("Párrafo reformado DOF ...") are kept,
  in italics, under the paragraph they annotate;
* transitorios (of the original law and of each reform decree) are preserved
  with their decree headings.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from .doc import Block

# --------------------------------------------------------------------------
# Regular expressions
# --------------------------------------------------------------------------

_SUFFIX_WORD = (
    r"(?:bis|ter|qu[áa]ter|quintus|quinquies|sextus|sexies|s[eé]ptimus|septies|octavus|octies|"
    r"nonus|nonies|novies|d[eé]cimus|decies|undecies|duodecies|terdecies|quaterdecies|quindecies)"
)
_SEP_AHEAD = r"(?=\s*(?:\.|[-–]|\s" + _SUFFIX_WORD + r"\b))"
# Letter suffixes: "15-A", "8o.-A", "410 A", "228 a" (never "5o. A los", where the
# dot is the separator and "A" starts the text).
_LETTER_SUFFIX = r"(?:\.?-|\s+-\s*|\s+)[A-Za-zÑñ](?![A-Za-záéíóúñÑ])" + _SEP_AHEAD

ARTICLE_RE = re.compile(
    r"^(?P<kw>art[ií]culos?|art\.)\s*"
    r"(?P<num>\d+)(?P<ord>[oº°ª]|\s+[º°ª])?"
    r"(?P<suf>(?:" + _LETTER_SUFFIX + r"|(?:\.?\s+|\s*-\s*)" + _SUFFIX_WORD + r"\b(?:\s+\d+(?=\s*[.\-–]))?)*)"
    r"\s*(?P<sep>\.\s*[-–]|[-–]|\.|:)"
    r"\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

ARTICLE_RANGE_RE = re.compile(
    r"^(?P<kw>art[ií]culos)\s+(?P<a>\d+)\s*[oº°]?\.?\s*(?:a|al|y)\s+(?P<b>\d+)\s*[oº°]?"
    r"\s*(?P<sep>\.\s*[-–]|[-–]|\.)\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

_ORDINAL_WORD = (
    r"(?:[uú]nic[oa]|primer[oa]?|segund[oa]|tercer[oa]?|cuart[oa]|quint[oa]|sext[oa]|"
    r"s[eé]ptim[oa]|octav[oa]|noven[oa]|d[eé]cim[oa]|und[eé]cim[oa]|duod[eé]cim[oa]|"
    r"vig[eé]sim[oa]|trig[eé]sim[oa]|cuadr[aá]g[eé]sim[oa]|quincuag[eé]sim[oa]|"
    r"sexag[eé]sim[oa]|septuag[eé]sim[oa]|octog[eé]sim[oa]|nonag[eé]sim[oa]|"
    r"cent[eé]sim[oa]|bis|ter)"
)

ORDINAL_ARTICLE_RE = re.compile(
    r"^(?P<label>(?:(?:art[ií]culos?|transitorios?)\s+)?"
    + _ORDINAL_WORD
    + r"(?:[\s-]+"
    + _ORDINAL_WORD
    + r")*(?:\s+transitorios?)?\s*(?:\.\s*[-–]|[-–]|\.|:))\s*(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)

_DIVISION_KINDS = {
    "libro": 0,
    "parte": 1,
    "título": 2,
    "titulo": 2,
    "capítulo": 3,
    "capitulo": 3,
    "sección": 4,
    "seccion": 4,
}

_ROMAN = r"(?=[IVXLCDM])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_DIVISION_ORDINAL = (
    r"(?:" + _ROMAN + r"|\d+|" + _ORDINAL_WORD + r"|preliminar|[uú]nico|[uú]nica|"
    r"primera|segunda|tercera|cuarta|quinta|sexta|s[eé]ptima|octava|novena|d[eé]cima"
    r"|vig[eé]sima|trig[eé]sima)"
)

DIVISION_RE = re.compile(
    r"^(?P<kind>libro|parte|t[ií]tulo|cap[ií]tulo|secci[oó]n)\s+"
    r"(?P<ord>" + _DIVISION_ORDINAL + r"(?:[\s-]+(?:" + _DIVISION_ORDINAL + r"))*"
    r"(?:\s+(?:bis|ter|qu[áa]ter|quinquies|sexies|[A-Z]))*)\b"
    r"\s*(?P<sep>\.\s*[-–]|[-–]|\.|:)?\s*(?P<name>.*)$",
    re.IGNORECASE | re.DOTALL,
)
# "PRIMERA PARTE", "SEGUNDA PARTE" (ordinal first, as in the Código Civil Federal).
DIVISION_PREFIX_RE = re.compile(
    r"^(?P<ord>primera|segunda|tercera|cuarta|quinta|sexta|s[eé]ptima|octava)\s+"
    r"(?P<kind>parte)\b\s*(?P<sep>\.\s*[-–]|[-–]|\.|:)?\s*(?P<name>.*)$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class Division:
    kind: str  # folded: libro, parte, titulo, capitulo, seccion
    label: str
    name: str

    @property
    def rank(self) -> int:
        return _DIVISION_KINDS[self.kind]


def match_division(text: str) -> Division | None:
    """Recognize a division heading such as ``TÍTULO PRIMERO`` or ``Capítulo III Bis``."""
    first, _, more = text.partition("\n")
    m = DIVISION_RE.match(first) or DIVISION_PREFIX_RE.match(first)
    if not m:
        return None
    name = m.group("name").strip()
    if name and (len(first) >= 220 or name.endswith((",", ";")) or name[0].islower()):
        return None  # a sentence that merely starts with "Capítulo ...", not a heading
    label = normalize_space(first[: m.start("name")].rstrip(" .:-–"))
    names = [name] if name else []
    names += [normalize_space(x) for x in more.split("\n") if x.strip()]
    return Division(kind=_fold(m.group("kind")), label=label, name=" ".join(names))


TRANSITORIOS_RE = re.compile(
    r"^(?:art[ií]culos?\s+)?(?:transitorios?|disposiciones\s+transitorias)\s*[.:]?$",
    re.IGNORECASE,
)
REFORM_TRANSITORIOS_RE = re.compile(r"^art[ií]culos\s+transitorios\s+de\s+decretos\s+de\s+reforma", re.IGNORECASE)
PUBLISHED_RE = re.compile(r"^publicad[oa]s?\s+en\s+el\s+diario\s+oficial", re.IGNORECASE)
LAST_REFORM_RE = re.compile(
    r"[uú]ltima\s+reforma\s+publicada(?:\s+en\s+el)?(?:\s+DOF)?[\s:]*(\d{1,2}-\d{1,2}-\d{4})",
    re.IGNORECASE,
)
_NOTE_DATE = re.compile(r"DOF\s+\d{2}-\d{2}-\d{4}")
_NOTE_SUBJECTS = {
    "artículo",
    "artículos",
    "fracción",
    "fracciones",
    "párrafo",
    "párrafos",
    "inciso",
    "incisos",
    "subinciso",
    "sub-inciso",
    "numeral",
    "numerales",
    "apartado",
    "apartados",
    "base",
    "bases",
    "capítulo",
    "título",
    "sección",
    "libro",
    "denominación",
    "epígrafe",
    "fe",
    "reforma",
    "derogado",
    "derogada",
    "suspensión",
    "último",
    "penúltimo",
    "encabezado",
    "rubro",
    "tabla",
    "anexo",
    "nota",
    "declaratoria",
    "segmento",
    "parte",
    "reubicado",
    "recorrido",
    "adicionado",
    "reformado",
    "transitorio",
    "porción",
    "oración",
    "la",
    "el",
    "los",
    "las",
    "se",
}
_NOTE_VERBS = re.compile(
    r"reformad|adicionad|derogad|recorrid|reubicad|original|vinculad|publicad|erratas|"
    r"reforma\s+DOF|suspensi|declarad|invalid|abrogad|modificad|aclaraci",
    re.IGNORECASE,
)


def _fold(text: str) -> str:
    """Lowercase and strip accents, for keyword comparisons."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def normalize_space(text: str) -> str:
    text = text.replace("\xa0", " ").replace("​", "")
    return re.sub(r"[ \t]+", " ", text).strip()


def is_note(text: str) -> bool:
    """True for the Cámara's editorial notes, e.g. ``Párrafo reformado DOF 15-01-2026``."""
    if len(text) > 600 or "(sic" in text or not _NOTE_DATE.search(text):
        return False
    first = text.split(" ", 1)[0].strip(".,:;").lower()
    if first not in _NOTE_SUBJECTS:
        return False
    if ARTICLE_RE.match(text) or ARTICLE_RANGE_RE.match(text):
        return False
    return bool(_NOTE_VERBS.search(text))


# --------------------------------------------------------------------------
# Markdown helpers
# --------------------------------------------------------------------------

_MD_INLINE = re.compile(r"([\\`*_])")
_MD_BLOCK_START = re.compile(r"^(#|>|[-+=]|\d+[.)](?=\s|$)|\|)")


def escape_md(text: str) -> str:
    """Escape the few characters that would change how a paragraph renders."""
    text = _MD_INLINE.sub(r"\\\1", text)
    text = re.sub(r"<(?=[A-Za-z/!])", "&lt;", text)
    m = _MD_BLOCK_START.match(text)
    if m:
        token = m.group(1)
        if token[0].isdigit():
            text = token[:-1] + "\\" + text[len(token) - 1 :]
        else:
            text = "\\" + text
    return text


def _escape_cell(text: str) -> str:
    parts = [escape_md(normalize_space(p)) for p in text.split("\n") if p.strip()]
    return "<br>".join(parts).replace("|", "\\|")


def render_table(rows: list[list[str]]) -> str:
    width = max((len(r) for r in rows), default=0)
    if width == 0:
        return ""
    if width == 1:
        # One-cell tables are editorial boxes ("Nota: ..."): render as a quote.
        lines = []
        for row in rows:
            for part in row[0].split("\n"):
                part = normalize_space(part)
                lines.append(f"> {escape_md(part)}" if part else ">")
        while lines and lines[-1] == ">":
            lines.pop()
        return "\n".join(lines)
    out = ["| " + " | ".join([" "] * width) + " |", "|" + "---|" * width]
    for row in rows:
        cells = [_escape_cell(c) for c in row] + [""] * (width - len(row))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class ArticleRef:
    label: str
    number: int
    suffix: str = ""
    last: int | None = None  # for ranges such as "Artículos 5o. a 10."


@dataclass
class ParsedLaw:
    title: str
    header: list[str]
    last_reform: date | None
    markdown_body: str
    articles: list[ArticleRef] = field(default_factory=list)
    divisions: dict[str, int] = field(default_factory=dict)
    transitorio_blocks: int = 0
    reform_decrees: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def article_count(self) -> int:
        return len(self.articles)


@dataclass
class _Item:
    text: str = ""
    rows: list[list[str]] | None = None


def _flatten(blocks: list[Block]) -> list[_Item]:
    items: list[_Item] = []
    for block in blocks:
        if block.is_table:
            rows = [[c for c in row] for row in block.rows or []]
            if any(any(c.strip() for c in row) for row in rows):
                items.append(_Item(rows=rows))
            continue
        text = block.text.replace("\r", "\n")
        lines = [normalize_space(x) for x in text.split("\n")]
        lines = [x for x in lines if x]
        if not lines:
            continue
        if len(lines) > 1 and match_division(lines[0]):
            # "TITULO PRIMERO<line break>Principios Generales": keep together.
            items.append(_Item(text="\n".join(lines)))
        else:
            items.extend(_Item(text=x) for x in lines)
    return items


def _header_dates(text: str) -> list[date]:
    found = []
    for m in [*LAST_REFORM_RE.finditer(text), *re.finditer(r"DOF\s+(\d{1,2}-\d{1,2}-\d{4})", text)]:
        d, mth, y = (int(x) for x in m.group(1).split("-"))
        try:
            found.append(date(y, mth, d))
        except ValueError:
            continue
    return found


_SUFFIX_RANK = {
    "bis": 2,
    "ter": 3,
    "quater": 4,
    "quintus": 5,
    "quinquies": 5,
    "sextus": 6,
    "sexies": 6,
    "septimus": 7,
    "septies": 7,
    "octavus": 8,
    "octies": 8,
    "nonus": 9,
    "nonies": 9,
    "novies": 9,
    "decimus": 10,
    "decies": 10,
}


_ALPHABET = "abcdefghijklmn~opqrstuvwxyz"  # "~" stands for ñ, which sorts after n


def suffix_key(suffix: str) -> tuple[tuple[int, int], ...]:
    """Sort key for article suffixes.

    '' < Bis < Ter < Quáter ... < A < A Bis < B ... < N < Ñ < O, which is the
    order the Código Fiscal uses (29, 29 Bis, 29-A, 29-A Bis, 29-B).
    """
    key: list[tuple[int, int]] = []
    for word in _fold(suffix.replace("Ñ", "ñ").replace("ñ", "~")).replace("-", " ").replace(".", " ").split():
        if word.isdigit():
            key.append((2, int(word)))
        elif word in _SUFFIX_RANK:
            key.append((0, _SUFFIX_RANK[word]))
        elif len(word) == 1:
            key.append((1, _ALPHABET.find(word) + 1))
        else:
            key.append((3, 0))
    return tuple(key)


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def _is_body_start(item: _Item) -> bool:
    if item.rows is not None:
        return False
    if match_division(item.text):
        return True
    m = ARTICLE_RE.match(item.text)
    return bool(m and int(m.group("num")) == 1)


_HEADER_RE = re.compile(
    r"texto vigente|[uú]ltima reforma|publicad[oa]s? en el diario oficial|^los d[ií]as|^en cuatro partes|"
    r"^\(antes\b",
    re.IGNORECASE,
)
_LOOKS_LIKE_ARTICLE = re.compile(r"^art[ií]culos?\s+\d", re.IGNORECASE)
_PRELIMINARY_HEADINGS = {"disposiciones preliminares", "disposiciones generales"}


def parse_law(blocks: list[Block]) -> ParsedLaw:
    items = _flatten(blocks)
    if not items:
        raise ValueError("empty document")
    title = items[0].text.replace("\n", " ")
    pos = 1

    # Header: publication line, "TEXTO VIGENTE", "Última reforma publicada ...".
    # The header is the run of lines right after the title: publication, "TEXTO
    # VIGENTE", "Última reforma publicada DOF ...", declarations of invalidity...
    header_end = 0
    for i in range(1, min(len(items), 12)):
        t = items[i].text
        if items[i].rows is not None or not (_HEADER_RE.search(t) or _NOTE_DATE.search(t)):
            break
        header_end = i
    header = [items[i].text for i in range(1, header_end + 1) if items[i].rows is None]
    # The text is current as of the latest DOF date in the header: usually the
    # last reform, sometimes a later SCJN invalidity declaration or an update of
    # amounts, which the Cámara also reports as the last change of the law.
    header_dates = [d for line in header for d in _header_dates(line)]
    last_reform = max(header_dates) if header_dates else None
    pos = header_end + 1

    # Where the articulated body starts and the transitorios begin.
    body_start = next((i for i in range(pos, len(items)) if _is_body_start(items[i])), None)
    warnings: list[str] = []
    if body_start is None:
        warnings.append("no article 1 or division heading found; whole text kept as preamble")
        body_start = len(items)
    trans_start = next(
        (i for i in range(body_start, len(items)) if items[i].rows is None and TRANSITORIOS_RE.match(items[i].text)),
        len(items),
    )

    # Division kinds actually used decide the heading levels.
    kinds_used: dict[int, str] = {}
    for item in items[body_start:trans_start]:
        if item.rows is None:
            div = match_division(item.text)
            if div:
                kinds_used.setdefault(div.rank, div.kind)
    level_of = {rank: 2 + n for n, rank in enumerate(sorted(kinds_used))}
    article_level = min(6, 2 + len(kinds_used))

    out: list[str] = []
    articles: list[ArticleRef] = []
    unparsed: list[str] = []
    divisions: dict[str, int] = {}

    def emit(chunk: str) -> None:
        if chunk:
            out.append(chunk)

    def emit_plain(item: _Item) -> None:
        if item.rows is not None:
            emit(render_table(item.rows))
        elif is_note(item.text):
            emit(f"*{escape_md(item.text)}*")
        else:
            emit(escape_md(item.text))

    # Header and preamble.
    out.append(f"# {escape_md(title)}")
    for line in header:
        emit(escape_md(line))
    pre = items[pos:body_start]
    if (
        pre
        and body_start < len(items)
        and ARTICLE_RE.match(items[body_start].text or "")
        and pre[-1].rows is None
        and _fold(pre[-1].text) in _PRELIMINARY_HEADINGS
    ):
        pre, prelim = pre[:-1], pre[-1]
    else:
        prelim = None
    for item in pre:
        emit_plain(item)
    if prelim is not None:
        emit(f"{'#' * level_of.get(min(level_of, default=0), 2)} {escape_md(prelim.text)}")

    # Body.
    i = body_start
    while i < trans_start:
        item = items[i]
        i += 1
        if item.rows is not None:
            emit(render_table(item.rows))
            continue
        text = item.text
        if is_note(text):
            emit(f"*{escape_md(text)}*")
            continue
        div = match_division(text)
        if div:
            name = div.name
            if not name and i < trans_start:
                nxt = items[i]
                if (
                    nxt.rows is None
                    and not is_note(nxt.text)
                    and not ARTICLE_RE.match(nxt.text)
                    and not ARTICLE_RANGE_RE.match(nxt.text)
                    and not match_division(nxt.text)
                    and not TRANSITORIOS_RE.match(nxt.text)
                    and len(nxt.text) < 250
                ):
                    name = nxt.text
                    i += 1
            heading = div.label + (" · " + name if name else "")
            emit(f"{'#' * level_of[div.rank]} {escape_md(heading)}")
            divisions[div.kind] = divisions.get(div.kind, 0) + 1
            continue
        m = ARTICLE_RANGE_RE.match(text)
        if m:
            label = text[: m.start("sep")].strip() + "."
            articles.append(ArticleRef(label=label, number=int(m.group("a")), last=int(m.group("b"))))
            emit(f"{'#' * article_level} {escape_md(label)}")
            if m.group("rest").strip():
                emit(escape_md(m.group("rest").strip()))
            continue
        m = ARTICLE_RE.match(text)
        if m:
            # Headings always end in "." ("Artículo 890-" in the source becomes
            # "Artículo 890."), so anchors and labels are uniform.
            label = text[: m.end("suf")].strip().rstrip(".") + "."
            articles.append(
                ArticleRef(
                    label=normalize_space(label),
                    number=int(m.group("num")),
                    suffix=normalize_space(m.group("suf") or "").strip(" .-"),
                )
            )
            emit(f"{'#' * article_level} {escape_md(normalize_space(label))}")
            rest = m.group("rest").strip()
            if rest:
                emit(escape_md(rest))
            continue
        if _LOOKS_LIKE_ARTICLE.match(text):
            unparsed.append(text[:60])
        emit_plain(item)

    # Transitorios of the original text and of each reform decree.
    transitorio_blocks = 0
    reform_decrees = 0
    in_reforms = False
    i = trans_start
    while i < len(items):
        item = items[i]
        i += 1
        if item.rows is not None:
            emit(render_table(item.rows))
            continue
        text = item.text.replace("\n", " ")
        nxt = next((x for x in items[i : i + 2] if x.rows is None), None)
        if REFORM_TRANSITORIOS_RE.match(text):
            in_reforms = True
            emit(f"## {escape_md(text)}")
            continue
        if nxt is not None and PUBLISHED_RE.match(nxt.text) and len(text) > 15 and not PUBLISHED_RE.match(text):
            reform_decrees += 1
            emit(f"### {escape_md(text)}")
            continue
        if PUBLISHED_RE.match(text):
            emit(f"*{escape_md(text)}*")
            continue
        if TRANSITORIOS_RE.match(text):
            transitorio_blocks += 1
            if not in_reforms and transitorio_blocks == 1:
                emit(f"## {escape_md(text)}")
            else:
                emit(f"**{escape_md(text)}**")
            continue
        if is_note(text):
            emit(f"*{escape_md(text)}*")
            continue
        m = ORDINAL_ARTICLE_RE.match(text) or ARTICLE_RE.match(text)
        if m:
            split = m.start("rest")
            label = text[:split].strip()
            rest = text[split:].strip()
            emit(f"**{escape_md(label)}** {escape_md(rest)}".rstrip())
            continue
        emit(escape_md(text))

    if not articles:
        warnings.append("no articles parsed in the body")
    warnings += [f"article-like paragraph not parsed: {u}" for u in unparsed]
    body = "\n\n".join(out) + "\n"
    return ParsedLaw(
        title=title,
        header=header,
        last_reform=last_reform,
        markdown_body=body,
        articles=articles,
        divisions=divisions,
        transitorio_blocks=transitorio_blocks,
        reform_decrees=reform_decrees,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


@dataclass
class Validation:
    articles: int
    highest: int
    missing: list[int]
    duplicates: list[str]
    out_of_order: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.duplicates and not self.out_of_order


def validate_articles(articles: list[ArticleRef]) -> Validation:
    """Check that body articles run 1..N without gaps, repeats or reordering.

    Repealed articles stay in the text ("Artículo 9o.- Se deroga."), so a gap
    in the numbering almost always means the parser missed an article label,
    and a repeated label means two articles were merged into one key.
    """
    seen: set[tuple] = set()
    numbers: set[int] = set()
    duplicates: list[str] = []
    out_of_order: list[str] = []
    prev: tuple | None = None
    for a in articles:
        key = (a.number, suffix_key(a.suffix))
        if key in seen:
            duplicates.append(a.label)
        seen.add(key)
        # Suffix order varies between laws (the CFF has 29 Bis before 29-A but
        # 70-A before 70 Bis), so only a smaller base number counts as disorder.
        if prev is not None and a.number < prev[0]:
            out_of_order.append(a.label)
        prev = key
        numbers.update(range(a.number, (a.last or a.number) + 1))
    highest = max(numbers, default=0)
    missing = [n for n in range(1, highest + 1) if n not in numbers]
    return Validation(len(articles), highest, missing, duplicates, out_of_order)
