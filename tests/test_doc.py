from pathlib import Path

import pytest

from leyes_mx.doc import DocFormatError, read_blocks, read_blocks_from_path

FIXTURE = Path(__file__).parent / "fixtures" / "mini-ley.doc"


def texts(blocks):
    return [b.text for b in blocks if not b.is_table]


def test_reads_paragraphs_in_order():
    blocks = read_blocks_from_path(FIXTURE)
    paragraphs = texts(blocks)
    assert paragraphs[0] == "LEY DE PRUEBA"
    assert "Última reforma publicada DOF 15-03-2024" in paragraphs
    assert paragraphs.index("TRANSITORIOS") > paragraphs.index("Artículo 2o. Bis.- Se deroga.")


def test_manual_line_break_is_kept_inside_the_paragraph():
    assert "TÍTULO PRIMERO\nDisposiciones Generales" in texts(read_blocks_from_path(FIXTURE))


def test_field_codes_are_dropped_and_results_kept():
    article = next(t for t in texts(read_blocks_from_path(FIXTURE)) if t.startswith("Artículo 1o."))
    assert "HYPERLINK" not in article
    assert article.endswith("Véase el Diario Oficial.")


def test_tables_become_rows_of_cells():
    tables = [b for b in read_blocks_from_path(FIXTURE) if b.is_table]
    assert len(tables) == 1
    assert tables[0].rows == [["Año", "Horas"], ["2026", "48"], ["2030", "40"]]


def test_rejects_files_that_are_not_word_documents():
    with pytest.raises(DocFormatError):
        read_blocks(b"<html>404 Not Found</html>")
