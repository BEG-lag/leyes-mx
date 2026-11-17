import subprocess
from datetime import date
from pathlib import Path

import pytest

from leyes_mx.build import Workspace
from leyes_mx.catalog import CatalogEntry, Law, display_name
from leyes_mx.gitutil import BOT_EMAIL, BOT_NAME, reform_message
from leyes_mx.history import (
    PlannedCommit,
    Version,
    _clean_original,
    _select_captures,
    bisect_versions,
    build_branch,
)
from leyes_mx.render import SourceInfo, read_front_matter, render_law
from leyes_mx.sources import Decree, IndexEntry
from leyes_mx.wayback import Capture, doc_paths, normalize_name, path_patterns


def test_path_patterns_turn_dated_names_into_prefixes():
    assert path_patterns({"doc/125_120615.doc", "doc/125.doc", "doc/LFT.doc"}) == [
        "doc/125.doc",
        "doc/125_*",
        "doc/LFT.doc",
    ]


def test_doc_paths_by_ref_and_by_name():
    html = """<table>
    <tr><td>141</td><td><b>LEY Federal del Trabajo</b> DOF 01-04-1970</td>
        <td><a href="decre/LFT_17ene06.doc">x</a></td><td><a href="doc/125.doc">WORD</a></td></tr>
    <tr><td>173</td><td><a href="ref/lft.htm">LEY Federal del Trabajo</a> DOF 01/04/1970</td>
        <td><a href="doc/LFT.doc">doc</a></td></tr>
    </table>"""
    by_ref, by_name = doc_paths(html)
    assert by_ref == {"lft": {"doc/LFT.doc"}}
    assert by_name[normalize_name("Ley Federal del Trabajo")] == {"doc/125.doc", "doc/LFT.doc"}


def test_select_captures_keeps_latest_of_each_dated_file():
    caps = [
        Capture("20150101000000", "http://www.diputados.gob.mx:80/LeyesBiblio/doc/125_120615.doc", "A"),
        Capture("20170101000000", "http://www.diputados.gob.mx/LeyesBiblio/doc/125_120615.doc", "B"),
        Capture("20120101000000", "http://www.diputados.gob.mx/LeyesBiblio/doc/125.doc", "C"),
        Capture("20130101000000", "http://www.diputados.gob.mx/LeyesBiblio/doc/125.doc", "D"),
    ]
    assert [c.digest for c in _select_captures(caps)] == ["C", "D", "B"]
    assert _clean_original(caps[0].original) == "https://www.diputados.gob.mx/LeyesBiblio/doc/125_120615.doc"


def test_reform_message_cites_decrees():
    decrees = [
        Decree(53, "DECRETO por el que se adiciona el artículo 283 Quáter.", date(2026, 5, 1), "https://x/53.pdf"),
        Decree(54, "DECRETO por el que se reforman diversas disposiciones.", date(2026, 5, 14), "https://x/54.pdf"),
    ]
    one = reform_message("LFT", date(2026, 5, 14), decrees[1:], source_url="https://x/LFT.doc")
    assert one.startswith("LFT: reforma DOF 14-05-2026\n\n- DECRETO por el que se reforman")
    assert "https://x/54.pdf" in one and "Texto consolidado: https://x/LFT.doc" in one
    many = reform_message("LFT", date(2026, 5, 14), decrees)
    assert many.startswith("LFT: reformas DOF 01-05-2026 a 14-05-2026")
    first = reform_message("CPEUM", date(2006, 4, 7), [], first=True)
    assert first.startswith("CPEUM: texto vigente al DOF 07-04-2006")
    original = reform_message("LSE", date(2025, 3, 18), [], first=True, original=True)
    assert original.startswith("LSE: publicación original DOF 18-03-2025")


def test_display_name():
    assert display_name("LEY Federal del Trabajo") == "Ley Federal del Trabajo"
    assert display_name("CONSTITUCIÓN Política de los Estados Unidos Mexicanos").startswith("Constitución Política")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    return tmp_path


def _law(slug: str) -> Law:
    entry = CatalogEntry(key=slug, slug=slug, group="general")
    index = IndexEntry(1, "LEY de Prueba", None, date(2020, 1, 2), None, None, None)
    return Law(entry, index)


def _planned(law: Law, day: date, body: str) -> PlannedCommit:
    from leyes_mx.parse import ParsedLaw

    parsed = ParsedLaw(title="LEY DE PRUEBA", header=[], last_reform=day, markdown_body=body)
    source = SourceInfo(url="https://example.invalid/x.doc", sha256="0" * 64, retrieved=day)
    version = Version(slug=law.slug, day=day, data=b"", source=source)
    text = render_law(
        parsed,
        title="Ley de Prueba",
        abbreviation="LP",
        slug=law.slug,
        published=date(2020, 1, 2),
        source=source,
        reforms_url=None,
    )
    return PlannedCommit(law, version, f"LP: reforma DOF {day:%d-%m-%Y}\n", text)


def test_history_branch_is_written_by_the_bot_with_dof_dates(repo: Path):
    a, b = _law("lp"), _law("otra")
    commits = [
        _planned(a, date(2021, 3, 1), "# v1\n"),
        _planned(b, date(2022, 6, 15), "# otra\n"),
        _planned(a, date(2024, 3, 15), "# v2\n"),
    ]
    build_branch(Workspace(repo), commits, branch="historia")
    log = subprocess.run(
        ["git", "log", "--format=%an|%ae|%cn|%ce|%aI|%cI|%s", "historia"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert len(log) == 3
    for line in log:
        an, ae, cn, ce, ad, cd, _ = line.split("|")
        assert (an, ae, cn, ce) == (BOT_NAME, BOT_EMAIL, BOT_NAME, BOT_EMAIL)
        assert ad == cd
    assert log[0].split("|")[4] == "2024-03-15T12:00:00-06:00"
    per_file = subprocess.run(
        ["git", "log", "--format=%ad", "--date=short", "historia", "--", "leyes/lp.md"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert per_file == ["2024-03-15", "2021-03-01"]
    shown = subprocess.run(
        ["git", "show", "historia:leyes/lp.md"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    assert read_front_matter(shown)["last_reform_dof"] == date(2024, 3, 15)


def test_bisection_finds_every_version_with_fewer_downloads():
    # 40 captures of one file; the text changed at captures 7, 8 and 31.
    days = [date(2010, 1, 1)] * 7 + [date(2011, 5, 2)] + [date(2012, 3, 3)] * 23 + [date(2014, 9, 9)] * 9
    looked: list[int] = []

    def day_of(i):
        looked.append(i)
        return days[i]

    bisect_versions(list(range(len(days))), day_of)
    assert {days[i] for i in looked} == set(days)
    assert len(set(looked)) < len(days) // 2
