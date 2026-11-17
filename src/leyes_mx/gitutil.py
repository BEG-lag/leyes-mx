"""Git helpers. Law text is always committed by a bot identity, never by a person.

Commits that record a version of a law are authored *and* committed by
``Diario Oficial (leyes-mx) <dof@leyes-mx.invalid>`` and dated with the DOF
publication date of the reform they reflect, so ``git log`` and ``git blame``
follow the legal timeline without attributing backdated activity to anyone.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path

from .sources import Decree

BOT_NAME = "Diario Oficial (leyes-mx)"
BOT_EMAIL = "dof@leyes-mx.invalid"
# Noon in Mexico City: the DOF is a morning publication.
DOF_TIME = "12:00:00 -0600"


def dof_timestamp(day: date) -> str:
    return f"{day.isoformat()} {DOF_TIME}"


def bot_env(when: date | datetime | str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME=BOT_NAME,
        GIT_AUTHOR_EMAIL=BOT_EMAIL,
        GIT_COMMITTER_NAME=BOT_NAME,
        GIT_COMMITTER_EMAIL=BOT_EMAIL,
    )
    if when is not None:
        stamp = dof_timestamp(when) if isinstance(when, date) and not isinstance(when, datetime) else str(when)
        env["GIT_AUTHOR_DATE"] = stamp
        env["GIT_COMMITTER_DATE"] = stamp
    return env


def git(root: Path, *args: str, env: dict[str, str] | None = None, input: str | bytes | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        input=input.encode() if isinstance(input, str) else input,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace')}")
    return result.stdout.decode()


def commit_as_bot(root: Path, paths: list[str], message: str, when: date | None) -> str:
    git(root, "add", "--", *paths)
    status = git(root, "diff", "--cached", "--name-only", "--", *paths).strip()
    if not status:
        return ""
    git(root, "commit", "-q", "-F", "-", "--", *paths, env=bot_env(when), input=message)
    return git(root, "rev-parse", "HEAD").strip()


def header_notes(header: list[str], day: date) -> list[str]:
    """Header lines dated ``day`` other than the plain "Última reforma" line."""
    stamp = day.strftime("%d-%m-%Y")
    return [line for line in header if stamp in line and not re.match(r"^[uú]ltima reforma", line, re.IGNORECASE)]


def _dmy(day: date) -> str:
    return day.strftime("%d-%m-%Y")


def reform_message(
    abbreviation: str,
    day: date,
    decrees: list[Decree],
    *,
    first: bool = False,
    original: bool = False,
    source_url: str | None = None,
    archived_url: str | None = None,
    notes: list[str] | None = None,
) -> str:
    """Commit message for one version of a law, citing the decrees it reflects.

    ``notes`` are header lines of the consolidated text dated on ``day`` that
    are not reform decrees, e.g. a declaration of invalidity by the SCJN or an
    update of amounts; they are cited when no decree matches the date.
    """
    notes = notes or []
    if original:
        subject = f"{abbreviation}: publicación original DOF {_dmy(day)}"
    elif first:
        subject = f"{abbreviation}: texto vigente al DOF {_dmy(day)}"
    elif len(decrees) > 1:
        span = sorted({d.dof_date for d in decrees})
        if len(span) == 1:
            subject = f"{abbreviation}: {len(decrees)} decretos de reforma, DOF {_dmy(day)}"
        else:
            subject = f"{abbreviation}: reformas DOF {_dmy(span[0])} a {_dmy(span[-1])}"
    elif not decrees:
        subject = f"{abbreviation}: actualización DOF {_dmy(day)}"
    else:
        subject = f"{abbreviation}: reforma DOF {_dmy(day)}"

    body: list[str] = []
    if first and not original:
        body.append(
            "Texto consolidado más antiguo disponible en el archivo; refleja todas "
            f"las reformas publicadas hasta el DOF {_dmy(day)}."
        )
    if not first and len({d.dof_date for d in decrees}) > 1:
        body.append("No hay una versión archivada entre estos decretos, así que este commit los agrupa.")
    if not first:
        for d in decrees:
            line = f"- {d.title} (DOF {_dmy(d.dof_date)})"
            if d.pdf_url:
                line += f"\n  {d.pdf_url}"
            body.append(line)
        if not decrees and notes:
            body.extend(f"- {note}" for note in notes)
        elif not decrees:
            body.append(
                "La Cámara de Diputados actualizó el texto consolidado sin un decreto "
                "listado en su página de reformas para esta fecha."
            )
    if source_url:
        body.append(f"Texto consolidado: {source_url}")
    if archived_url:
        body.append(f"Copia archivada: {archived_url}")
    return subject + "\n\n" + "\n\n".join(body) + "\n"
