"""Assemble the final Markdown files: front matter + body, and reform timelines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import yaml

from .parse import ParsedLaw, escape_md
from .sources import ReformHistory


@dataclass
class SourceInfo:
    url: str
    sha256: str
    retrieved: date
    archived_url: str | None = None


def _dmy(day: date | None) -> str:
    return day.strftime("%d-%m-%Y") if day else ""


def front_matter(
    *,
    title: str,
    abbreviation: str,
    slug: str,
    published: date | None,
    last_reform: date | None,
    articles: int,
    source: SourceInfo,
    reforms_url: str | None,
) -> str:
    data: dict[str, object] = {
        "title": title,
        "abbreviation": abbreviation,
        "slug": slug,
        "published_dof": published,
        "last_reform_dof": last_reform,
        "articles": articles,
        "source": source.url,
    }
    if source.archived_url:
        data["archived"] = source.archived_url
    data["source_sha256"] = source.sha256
    data["retrieved"] = source.retrieved
    if reforms_url:
        data["reforms"] = reforms_url
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=1000)
    return f"---\n{dumped}---\n"


def render_law(
    parsed: ParsedLaw,
    *,
    title: str,
    abbreviation: str,
    slug: str,
    published: date | None,
    source: SourceInfo,
    reforms_url: str | None,
) -> str:
    fm = front_matter(
        title=title,
        abbreviation=abbreviation,
        slug=slug,
        published=published,
        last_reform=parsed.last_reform,
        articles=parsed.article_count,
        source=source,
        reforms_url=reforms_url,
    )
    return fm + "\n" + parsed.markdown_body


def read_front_matter(text: str) -> dict:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    return yaml.safe_load(text[4:end]) or {}


def render_reforms(title: str, slug: str, history: ReformHistory, published: date | None) -> str:
    """Human-readable reform timeline, newest first."""
    decrees = sorted(history.decrees, key=lambda d: (d.dof_date, d.number), reverse=True)
    lines = [
        f"# {title}: reformas",
        "",
        f"Texto vigente: [`{slug}.md`]({slug}.md) · Fuente: [LeyesBiblio, Cámara de Diputados]({history.ref_url})",
        "",
        f"Publicación original en el DOF: **{_dmy(published or history.original_date)}** · "
        f"Decretos de reforma: **{len(decrees)}**",
        "",
    ]
    if decrees:
        lines += ["| # | DOF | Decreto |", "|---:|---|---|"]
        for d in decrees:
            when = f"[{_dmy(d.dof_date)}]({d.pdf_url})" if d.pdf_url else _dmy(d.dof_date)
            title_md = escape_md(d.title).replace("|", "\\|")
            lines.append(f"| {d.number} | {when} | {title_md} |")
    else:
        lines.append("Sin reformas desde su publicación.")
    lines.append("")
    return "\n".join(lines)
