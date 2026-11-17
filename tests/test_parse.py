from datetime import date

import pytest

from leyes_mx.doc import Block
from leyes_mx.parse import (
    ARTICLE_RE,
    ArticleRef,
    escape_md,
    is_note,
    match_division,
    parse_law,
    render_table,
    suffix_key,
    validate_articles,
)


def law(*paragraphs, tables=None):
    blocks = [Block(text=p) for p in paragraphs]
    for at, rows in tables or []:
        blocks.insert(at, Block(rows=rows))
    return parse_law(blocks)


HEADER = (
    "LEY DE PRUEBA",
    "Nueva Ley publicada en el Diario Oficial de la Federación el 2 de enero de 2020",
    "TEXTO VIGENTE",
    "Última reforma publicada DOF 15-03-2024",
)


@pytest.mark.parametrize(
    ("text", "label", "number", "suffix", "rest"),
    [
        ("Artículo 1o.- La presente Ley", "Artículo 1o", 1, "", "La presente Ley"),
        ("Artículo 1o. En los Estados Unidos", "Artículo 1o", 1, "", "En los Estados Unidos"),
        ("Artículo 123. Toda persona", "Artículo 123", 123, "", "Toda persona"),
        ("Artículo 3o. Bis.- Para efectos", "Artículo 3o. Bis", 3, "Bis", "Para efectos"),
        ("Artículo 15-A.- Se consideran", "Artículo 15-A", 15, "A", "Se consideran"),
        ("Artículo 103-Bis.- La celebración", "Artículo 103-Bis", 103, "Bis", "La celebración"),
        ("Artículo 353-Ñ.- Los sindicatos", "Artículo 353-Ñ", 353, "Ñ", "Los sindicatos"),
        ("ARTICULO 27.- Las tierras", "ARTICULO 27", 27, "", "Las tierras"),
        ("Artículo 1916 Bis.- No estará", "Artículo 1916 Bis", 1916, "Bis", "No estará"),
    ],
)
def test_article_labels(text, label, number, suffix, rest):
    m = ARTICLE_RE.match(text)
    assert m
    assert text[: m.end("suf")].strip() == label
    assert int(m.group("num")) == number
    assert (m.group("suf") or "").strip(" .-") == suffix
    assert m.group("rest") == rest


@pytest.mark.parametrize(
    "text",
    ["Artículo reformado DOF 30-11-2012", "Artículos transitorios", "Artículo 27 constitucional establece"],
)
def test_not_article_labels(text):
    assert ARTICLE_RE.match(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Párrafo reformado DOF 15-01-2026",
        "Artículo reformado DOF 28-04-1978, 30-11-2012",
        "Capítulo adicionado DOF 28-04-1978. Denominación reformada DOF 30-11-2012",
        "Fe de erratas al párrafo DOF 06-09-1993",
        "Fracción recorrida DOF 01-05-2019",
    ],
)
def test_reform_notes(text):
    assert is_note(text)


@pytest.mark.parametrize(
    "text",
    [
        "Artículo 9o.- Se deroga.",
        "XIX. Con base en esta Constitución, la tenencia de le (sic DOF 03-02-1983) tierra ejidal",
        "La Federación publicará en el DOF el decreto respectivo.",
    ],
)
def test_law_text_is_not_a_note(text):
    assert not is_note(text)


@pytest.mark.parametrize(
    ("text", "kind", "label", "name"),
    [
        ("TÍTULO PRIMERO", "titulo", "TÍTULO PRIMERO", ""),
        ("TITULO PRIMERO\nPrincipios Generales", "titulo", "TITULO PRIMERO", "Principios Generales"),
        ("Capítulo III BIS", "capitulo", "Capítulo III BIS", ""),
        ("Capítulo Único", "capitulo", "Capítulo Único", ""),
        ("Sección Primera", "seccion", "Sección Primera", ""),
        ("LIBRO SEGUNDO", "libro", "LIBRO SEGUNDO", ""),
        ("PRIMERA PARTE", "parte", "PRIMERA PARTE", ""),
    ],
)
def test_divisions(text, kind, label, name):
    div = match_division(text)
    assert div is not None
    assert (div.kind, div.label, div.name) == (kind, label, name)


def test_sentence_starting_with_capitulo_is_not_a_heading():
    assert match_division("Capítulo I de esta Ley, se aplicará supletoriamente") is None


def test_structure_headings_and_articles():
    parsed = law(
        *HEADER,
        "Al margen un sello con el Escudo Nacional.",
        "TÍTULO PRIMERO",
        "Disposiciones Generales",
        "Capítulo I",
        "Del Objeto",
        "Artículo 1o.- Esta Ley es de orden público.",
        "Párrafo reformado DOF 15-03-2024",
        "Artículo 2o.- Para efectos de esta Ley:",
        "TRANSITORIOS",
        "Primero.- Entra en vigor al día siguiente.",
    )
    md = parsed.markdown_body
    assert parsed.title == "LEY DE PRUEBA"
    assert parsed.last_reform == date(2024, 3, 15)
    assert "## TÍTULO PRIMERO · Disposiciones Generales" in md
    assert "### Capítulo I · Del Objeto" in md
    assert "#### Artículo 1o.\n\nEsta Ley es de orden público." in md
    assert "*Párrafo reformado DOF 15-03-2024*" in md
    assert "## TRANSITORIOS" in md
    assert "**Primero.-** Entra en vigor al día siguiente." in md
    assert [a.label for a in parsed.articles] == ["Artículo 1o.", "Artículo 2o."]


def test_heading_levels_follow_the_divisions_in_use():
    parsed = law(*HEADER, "Capítulo I", "Del Objeto", "Artículo 1.- Uno.", "Artículo 2.- Dos.")
    assert "## Capítulo I · Del Objeto" in parsed.markdown_body
    assert "### Artículo 1." in parsed.markdown_body


def test_transitorios_of_reform_decrees():
    parsed = law(
        *HEADER,
        "Artículo 1.- Uno.",
        "TRANSITORIOS",
        "Único.- Vigencia.",
        "ARTÍCULOS TRANSITORIOS DE DECRETOS DE REFORMA",
        "DECRETO por el que se reforma la Ley de Prueba.",
        "Publicado en el Diario Oficial de la Federación el 15 de marzo de 2024",
        "Artículo Único.- Se reforma el artículo 1.",
        "Transitorio",
        "Único.- El presente Decreto entrará en vigor al día siguiente.",
    )
    md = parsed.markdown_body
    assert "### DECRETO por el que se reforma la Ley de Prueba." in md
    assert "*Publicado en el Diario Oficial de la Federación el 15 de marzo de 2024*" in md
    assert "**Artículo Único.-** Se reforma el artículo 1." in md
    assert parsed.reform_decrees == 1
    # Transitorio articles are not body articles.
    assert parsed.article_count == 1


def test_one_cell_tables_are_quotes_and_others_are_tables():
    assert render_table([["Nota: texto\n\nsegundo párrafo"]]) == "> Nota: texto\n>\n> segundo párrafo"
    table = render_table([["1", "Pérdida | total", "85%"]])
    assert table.splitlines()[1] == "|---|---|---|"
    assert "Pérdida \\| total" in table


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("3. Lo que", "3\\. Lo que"),
        ("- guion", "\\- guion"),
        ("# no es encabezado", "\\# no es encabezado"),
        ("los _Juntas_ y *notas*", "los \\_Juntas\\_ y \\*notas\\*"),
        ("I. Fracción", "I. Fracción"),
        ("a < b y <script>", "a < b y &lt;script>"),
        ("de $1,000.00 a $5,000.00", "de \\$1,000.00 a \\$5,000.00"),
    ],
)
def test_markdown_escaping(raw, escaped):
    assert escape_md(raw) == escaped


def test_validation_reports_gaps_duplicates_and_order():
    refs = [
        ArticleRef("A1", 1),
        ArticleRef("A3", 3),
        ArticleRef("A3", 3),
        ArticleRef("A2", 2),
        ArticleRef("A4", 4),
        ArticleRef("A4 Bis", 4, "Bis"),
        ArticleRef("A4-A", 4, "A"),
    ]
    v = validate_articles(refs)
    assert v.missing == []
    assert v.duplicates == ["A3"]
    assert v.out_of_order == ["A2"]
    v = validate_articles([ArticleRef("A1", 1), ArticleRef("A4", 4)])
    assert v.missing == [2, 3]
    assert not v.ok


def test_ranges_fill_the_numbering():
    v = validate_articles([ArticleRef("A1", 1), ArticleRef("Artículos 2 a 9", 2, last=9), ArticleRef("A10", 10)])
    assert v.ok and v.highest == 10


def test_suffix_order():
    assert suffix_key("") < suffix_key("Bis") < suffix_key("Ter") < suffix_key("Quáter")
    assert suffix_key("A") < suffix_key("B") < suffix_key("N") < suffix_key("Ñ") < suffix_key("O")
    # Código Fiscal: 29, 29 Bis, 29-A, 29-A Bis, 29-B; "Bis" and "B" never collide.
    keys = [suffix_key(s) for s in ("", "Bis", "A", "A Bis", "B")]
    assert keys == sorted(keys) and len(set(keys)) == 5


def test_letter_suffixes_and_ordinals():
    cases = {
        "Artículo 228 a.- Los términos": ("228", "a"),
        "Artículo 228 a bis.- Los": ("228", "a bis"),
        "Artículo 228 o.- Los": ("228", "o"),
        "Artículo 8o.-A.- El ejercicio": ("8", "A"),
        "Artículo 5o.-A. Los actos": ("5", "A"),
        "Artículo 410 A.- El adoptado": ("410", "A"),
        "Artículo 199 Sextus. Los delitos": ("199", "Sextus"),
    }
    for text, (num, suffix) in cases.items():
        m = ARTICLE_RE.match(text)
        assert m, text
        assert (m.group("num"), (m.group("suf") or "").strip(" .-")) == (num, suffix), text
    m = ARTICLE_RE.match("Artículo 5o.- A falta de disposición expresa")
    assert m.group("suf") == "" and m.group("rest").startswith("A falta")
    m = ARTICLE_RE.match("Artículo 5o. A los socios corresponde")
    assert m.group("suf") == "" and m.group("rest").startswith("A los")
