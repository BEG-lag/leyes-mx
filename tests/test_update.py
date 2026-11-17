"""End to end: a law changes at the source and the update commits it as the bot."""

import subprocess
from pathlib import Path

from leyes_mx.build import Workspace, update
from leyes_mx.gitutil import BOT_EMAIL, BOT_NAME
from leyes_mx.render import read_front_matter
from leyes_mx.sources import INDEX_URL

FIXTURE = Path(__file__).parent / "fixtures" / "mini-ley.doc"
BASE = "https://www.diputados.gob.mx/LeyesBiblio/"

INDEX = """<table><tr><td>1</td>
<td><a href="ref/lp.htm">LEY de Prueba</a> DOF 02/01/2020</td>
<td>DOF {last}</td>
<td><a href="doc/LP.doc">doc</a></td></tr></table>"""

REFORMS = """<p>Publicación Original: | DOF 02-01-2020 |</p><table>
<tr><td>1</td><td><p>DECRETO por el que se reforma la Ley de Prueba.</p>
<p>| <a href="lp/LP_ref01_15mar24.pdf">DOF 15-03-2024</a> |</p></td></tr></table>"""


class FakeClient:
    def __init__(self, last: str):
        self.last = last
        self.requests: list[str] = []

    def get(self, url: str) -> bytes:
        self.requests.append(url)
        if url == INDEX_URL:
            return INDEX.format(last=self.last).encode()
        if url == BASE + "doc/LP.doc":
            return FIXTURE.read_bytes()
        if url == BASE + "ref/lp.htm":
            return REFORMS.encode()
        raise AssertionError(f"unexpected request {url}")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def test_update_writes_and_commits_as_the_bot(tmp_path: Path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "catalog.yml").write_text(
        "groups: {general: General}\nlaws:\n  - {key: lp, slug: lp, group: general}\n"
    )
    ws = Workspace(tmp_path)

    results = update(FakeClient("15/03/2024"), ws, commit=True)
    assert [(r.slug, r.status) for r in results] == [("lp", "new")]

    text = (tmp_path / "leyes" / "lp.md").read_text()
    fm = read_front_matter(text)
    assert fm["title"] == "Ley de Prueba" and fm["articles"] == 3
    assert str(fm["last_reform_dof"]) == "2024-03-15"
    assert "#### Artículo 2o. Bis." in text

    log = git(tmp_path, "log", "--format=%an|%ae|%cn|%ce|%aI|%s").splitlines()
    law_commit = next(line for line in log if "DOF" in line)
    an, ae, cn, ce, ad, subject = law_commit.split("|")
    assert (an, ae, cn, ce) == (BOT_NAME, BOT_EMAIL, BOT_NAME, BOT_EMAIL)
    assert ad == "2024-03-15T12:00:00-06:00"
    # A law that enters the repository starts with its current text.
    assert subject == "LP: texto vigente al DOF 15-03-2024"

    # Nothing changed at the source: no download, no commit.
    client = FakeClient("15/03/2024")
    again = update(client, ws, commit=True)
    assert [r.status for r in again] == ["unchanged"]
    assert client.requests == [INDEX_URL]
    assert len(git(tmp_path, "log", "--format=%H").split()) == len(log)


def test_reform_of_a_tracked_law_cites_the_decree(tmp_path: Path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "catalog.yml").write_text(
        "groups: {general: General}\nlaws:\n  - {key: lp, slug: lp, group: general}\n"
    )
    ws = Workspace(tmp_path)
    update(FakeClient("15/03/2024"), ws, commit=True)
    # Pretend the tracked text predates the reform, then let the update find it.
    path = tmp_path / "leyes" / "lp.md"
    text = path.read_text().replace("last_reform_dof: 2024-03-15", "last_reform_dof: 2023-01-01")
    text = text.replace("source_sha256: ", "source_sha256: 0", 1)
    path.write_text(text)
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "older text")
    (tmp_path / "data" / "indice.json").unlink()
    update(FakeClient("15/03/2024"), ws, commit=True)
    subject, _, body = git(tmp_path, "log", "-1", "--format=%s%n%n%b", "--", "leyes/lp.md").partition("\n\n")
    assert subject == "LP: reforma DOF 15-03-2024"
    assert "DECRETO por el que se reforma la Ley de Prueba. (DOF 15-03-2024)" in body
