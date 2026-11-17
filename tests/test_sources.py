from datetime import date

from leyes_mx.sources import decode_html, parse_date, parse_index, parse_reform_page

INDEX_ROW = """
<table><tr>
 <td><p><font>124</font></td>
 <td><p><b><a href="ref/lse.htm"><font>LEY del Sector Eléctrico</font></a></b></p>
     <p><font>DOF 18/03/2025</font></p></td>
 <td><p><font>Sin reforma</font></td>
 <td><a href="pdf/LSE.pdf">pdf</a> <a href="doc/LSE.doc">doc</a> <a href="pdf_mov/x.pdf">m</a></td>
</tr><tr>
 <td><p><font>173</font></td>
 <td><p><b><a href="ref/lft.htm"><font>LEY Federal del Trabajo</font></a></b></p>
     <p><font>DOF 01/04/1970</font></p></td>
 <td><p><font>DOF 14/05/2026</font></td>
 <td><a href="pdf/LFT.pdf">pdf</a> <a href="doc/LFT.doc">doc</a></td>
</tr></table>
"""

REF_PAGE = """
<p>Publicación Original: | DOF 01-04-1970 | <a href="lft/LFT_orig_01abr70_ima.pdf">Imagen</a> |
Fe de erratas | DOF 30-04-1970 | Imagen |</p>
<p>Decretos de Reforma:</p>
<table>
<tr><td><span>54</span></td><td>
  <p><b>DECRETO</b> por el que se reforman diversas disposiciones de la Ley Federal del Trabajo.</p>
  <p>| <a href="lft/LFT_ref54_14may26.pdf">DOF 14-05-2026</a> | <a href="lft/LFT_ref54_14may26.doc">Word</a> |</p>
</td></tr>
<tr><td><span>22</span></td><td>
  <p><b>DECRETO </b>por el que se modifica la Ley Federal del Trabajo.</p>
  <p>| DOF 21-01-1988 | <a href="lft/LFT_ref22_21ene88_ima.pdf">Imagen</a> |</p>
</td></tr>
</table>
"""

CONSTITUTION_ROW = """
<table><tr>
 <td><p><font>285</font></td>
 <td><p><font><b>DECRETO</b> por el que se reforman los artículos 82, 116 y 122.</font></p></td>
 <td><p><font>07/10/2026</font></td>
 <td><a href="dof/CPEUM_ref_285_07oct26.pdf">PDF</a><a href="dof/CPEUM_ref_285_07oct26.doc">Word</a></td>
</tr></table>
"""


def test_parse_date_formats():
    assert parse_date("DOF 14/05/2026") == date(2026, 5, 14)
    assert parse_date("DOF 14-05-2026") == date(2026, 5, 14)
    assert parse_date("Sin reforma") is None


def test_index_rows():
    entries = parse_index(INDEX_ROW, "https://www.diputados.gob.mx/LeyesBiblio/index.htm")
    lse, lft = entries
    assert lse.abbr == "LSE" and lse.last_reform is None and lse.published == date(2025, 3, 18)
    assert lft.number == 173 and lft.name == "LEY Federal del Trabajo"
    assert lft.last_reform == date(2026, 5, 14)
    assert lft.doc_url == "https://www.diputados.gob.mx/LeyesBiblio/doc/LFT.doc"
    assert lft.ref_url == "https://www.diputados.gob.mx/LeyesBiblio/ref/lft.htm"


def test_reform_page_with_both_layouts():
    history = parse_reform_page(REF_PAGE, "https://www.diputados.gob.mx/LeyesBiblio/ref/lft.htm")
    assert history.original_date == date(1970, 4, 1)
    assert [d.number for d in history.decrees] == [22, 54]
    new = history.decrees[1]
    assert new.dof_date == date(2026, 5, 14)
    assert new.title.startswith("DECRETO por el que se reforman")
    assert new.doc_url.endswith("/ref/lft/LFT_ref54_14may26.doc")
    old = history.decrees[0]
    assert old.dof_date == date(1988, 1, 21)
    assert old.pdf_url.endswith("LFT_ref22_21ene88_ima.pdf") and old.doc_url is None


def test_constitution_chronological_layout():
    history = parse_reform_page(CONSTITUTION_ROW, "https://www.diputados.gob.mx/LeyesBiblio/ref/cpeum_crono.htm")
    (d,) = history.decrees
    assert d.number == 285 and d.dof_date == date(2026, 10, 7)
    assert d.pdf_url.endswith("/ref/dof/CPEUM_ref_285_07oct26.pdf")


def test_decrees_between():
    history = parse_reform_page(REF_PAGE, "https://x/ref/lft.htm")
    assert [d.number for d in history.decrees_between(date(1990, 1, 1), date(2026, 5, 14))] == [54]
    assert [d.number for d in history.decrees_between(None, date(2000, 1, 1))] == [22]


def test_decode_html_falls_back_to_cp1252():
    assert decode_html("Reforma Eléctrica".encode("cp1252")) == "Reforma Eléctrica"
