"""Generated indexes: CHANGES.md, docs/ (static page) and the README tables."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date
from importlib.resources import files
from pathlib import Path

from .build import Workspace
from .catalog import load_catalog
from .render import read_front_matter

REPO_URL = "https://github.com/BEG-lag/leyes-mx"
BLOB_URL = f"{REPO_URL}/blob/main"


def _dmy(value: object) -> str:
    if not value:
        return ""
    d = value if isinstance(value, date) else date.fromisoformat(str(value))
    return d.strftime("%d-%m-%Y")


def _versions_in_git(root: Path, path: str) -> int:
    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", "HEAD", "--", path],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return int(out.strip() or 0)
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return 0


def _first_version(root: Path, path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "log", "--reverse", "--format=%ad", "--date=short", "--", path],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        return out[0] if out else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def collect(ws: Workspace) -> dict:
    groups, entries = load_catalog(ws.root / "catalog.yml")
    laws = []
    recent = []
    for entry in entries:
        path = ws.law_path(entry.slug)
        if not path.exists():
            continue
        fm = read_front_matter(path.read_text(encoding="utf-8"))
        reforms_path = ws.reforms_json_path(entry.slug)
        reforms = json.loads(reforms_path.read_text(encoding="utf-8")) if reforms_path.exists() else {}
        decrees = reforms.get("decrees", [])
        rel = f"leyes/{entry.slug}.md"
        laws.append(
            {
                "slug": entry.slug,
                "group": entry.group,
                "group_name": groups.get(entry.group, entry.group),
                "title": fm.get("title"),
                "abbreviation": fm.get("abbreviation"),
                "published": str(fm.get("published_dof") or ""),
                "last_reform": str(fm.get("last_reform_dof") or ""),
                "articles": fm.get("articles"),
                "decrees": len(decrees),
                "versions": _versions_in_git(ws.root, rel),
                "history_since": _first_version(ws.root, rel),
                "url": f"{BLOB_URL}/{rel}",
                "reforms_url": f"{BLOB_URL}/leyes/{entry.slug}.reformas.md",
                "source": fm.get("source"),
            }
        )
        for d in decrees:
            recent.append(
                {
                    "date": d["dof_date"],
                    "slug": entry.slug,
                    "abbreviation": fm.get("abbreviation"),
                    "title": d["title"],
                    "pdf": d.get("pdf_url"),
                }
            )
    recent.sort(key=lambda r: (r["date"], r["slug"]), reverse=True)
    # "As of" the newest change in the data, not today: regenerating without
    # new reforms must produce identical files (no empty daily commits).
    as_of = max((law["last_reform"] or law["published"] for law in laws), default="")
    return {
        "generated": as_of,
        "groups": groups,
        "laws": laws,
        "recent": recent[:80],
        "totals": {
            "laws": len(laws),
            "articles": sum(law["articles"] or 0 for law in laws),
            "decrees": sum(law["decrees"] for law in laws),
            "versions": sum(law["versions"] for law in laws),
            "laws_with_history": sum(1 for law in laws if law["versions"] > 1),
            "earliest_version": min((law["history_since"] for law in laws if law["history_since"]), default=None),
        },
    }


def laws_table(data: dict, lang: str = "en") -> str:
    head = {
        "en": "| Law | Abbr. | Published (DOF) | Last reform (DOF) | Articles | Reform decrees | Versions in git |",
        "es": "| Ley | Abrev. | Publicación (DOF) | Última reforma (DOF) | Artículos | Decretos de reforma | Versiones en git |",
    }[lang]
    none = {"en": "none", "es": "sin reforma"}[lang]
    lines = []
    for group, group_name in data["groups"].items():
        rows = [law for law in data["laws"] if law["group"] == group]
        if not rows:
            continue
        lines += ["", f"**{group_name}**", "", head, "|---|---|---|---|---:|---:|---:|"]
        for law in rows:
            lines.append(
                f"| [{law['title']}](leyes/{law['slug']}.md) | {law['abbreviation']} | {_dmy(law['published'])} | "
                f"{_dmy(law['last_reform']) or none} | {law['articles']:,} | "
                f"[{law['decrees']}](leyes/{law['slug']}.reformas.md) | {law['versions']} |"
            )
    return "\n".join(lines).strip() + "\n"


def stats_line(data: dict, lang: str = "en") -> str:
    t = data["totals"]
    since = (t["earliest_version"] or "")[:4]
    if lang == "es":
        return (
            f"**{t['laws']} leyes** · **{t['articles']:,} artículos** · "
            f"**{t['decrees']:,} decretos de reforma** catalogados · "
            f"**{t['versions']:,} versiones** en git, de {t['laws_with_history']} leyes con historia"
            + (f" desde {since}" if since else "")
            + f" · datos al DOF {_dmy(data['generated'])}\n"
        )
    return (
        f"**{t['laws']} laws** · **{t['articles']:,} articles** · "
        f"**{t['decrees']:,} reform decrees** catalogued · "
        f"**{t['versions']:,} versions** in git across {t['laws_with_history']} laws with history"
        + (f" back to {since}" if since else "")
        + f" · data as of DOF {_dmy(data['generated'])}\n"
    )


def history_block(data: dict, lang: str = "en") -> str:
    """Versions per law actually present in git, most versions first."""
    rows = sorted(
        (law for law in data["laws"] if law["versions"] > 1),
        key=lambda law: (-law["versions"], law["slug"]),
    )
    t = data["totals"]
    if lang == "es":
        intro = (
            f"Resultado: **{t['versions']:,} versiones** en git. {t['laws_with_history']} de "
            f"{t['laws']} leyes tienen más de una versión; la más antigua es de "
            f"{(t['earliest_version'] or '')[:4]}. Detalle por ley en "
            "[`data/historia/`](data/historia/)."
        )
        head = "| Ley | Versiones | Desde |"
    else:
        intro = (
            f"Result: **{t['versions']:,} versions** in git. {t['laws_with_history']} of "
            f"{t['laws']} laws have more than one version; the oldest dates from "
            f"{(t['earliest_version'] or '')[:4]}. Per-law detail in "
            "[`data/historia/`](data/historia/)."
        )
        head = "| Law | Versions | Since |"
    lines = [intro, "", head, "|---|---:|---|"]
    for law in rows:
        lines.append(
            f"| [{law['abbreviation']}](https://github.com/BEG-lag/leyes-mx/commits/main/leyes/{law['slug']}.md) "
            f"· {law['title']} | {law['versions']} | {law['history_since']} |"
        )
    return "\n".join(lines) + "\n"


def changes_md(data: dict) -> str:
    t = data["totals"]
    lines = [
        "# CHANGES: reformas recientes / recent reforms",
        "",
        f"Generated by `leyes site`; data as of DOF {_dmy(data['generated'])}. {t['laws']} laws, "
        f"{t['decrees']:,} reform decrees listed by the Cámara de Diputados, "
        f"{t['versions']:,} versions in git.",
        "",
        "Every row is a decree published in the Diario Oficial de la Federación that "
        "reformed one of the tracked laws. Use `git log -- leyes/<slug>.md` to see the "
        "text change it produced.",
        "",
        "| DOF | Ley | Decreto |",
        "|---|---|---|",
    ]
    for r in data["recent"]:
        when = f"[{_dmy(r['date'])}]({r['pdf']})" if r.get("pdf") else _dmy(r["date"])
        title = r["title"].replace("|", "\\|")
        lines.append(f"| {when} | [{r['abbreviation']}](leyes/{r['slug']}.md) | {title} |")
    lines.append("")
    return "\n".join(lines)


def _replace_block(text: str, name: str, content: str) -> str:
    pattern = re.compile(rf"(<!-- {name}:start -->\n).*?(<!-- {name}:end -->)", re.DOTALL)
    return pattern.sub(lambda m: m.group(1) + content + m.group(2), text)


def build_site(ws: Workspace) -> dict:
    data = collect(ws)
    (ws.root / "CHANGES.md").write_text(changes_md(data), encoding="utf-8")
    docs = ws.root / "docs"
    docs.mkdir(exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    (docs / "data.json").write_text(payload + "\n", encoding="utf-8")
    template = files("leyes_mx").joinpath("templates/index.html").read_text(encoding="utf-8")
    page = template.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    (docs / "index.html").write_text(page, encoding="utf-8")
    for name, lang in (("README.md", "en"), ("README.es.md", "es")):
        path = ws.root / name
        if path.exists():
            text = path.read_text(encoding="utf-8")
            text = _replace_block(text, "leyes:table", laws_table(data, lang))
            text = _replace_block(text, "leyes:stats", stats_line(data, lang))
            text = _replace_block(text, "history", history_block(data, lang))
            path.write_text(text, encoding="utf-8")
    return data["totals"]
