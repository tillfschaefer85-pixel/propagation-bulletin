"""Doppelschutz fuer den Abendlauf.

Seit der Anstoss von aussen kommt (cron-job.org, puenktlich auf die
Minute), ist der GitHub-eigene Zeitplan nur noch die Rueckfallebene.
Beide zusammen wuerden aber zwei Push-Nachrichten erzeugen: eine um
20:30 vom externen Anstoss, und eine weitere, wenn GitHub seinen
Termin irgendwann im Laufe des Abends nachholt.

Anders als bei der Vertonung eines Podcasts, wo ein zweiter Lauf
schlicht feststellt, dass die Folge schon fertig ist, hinterlaesst ein
Push keine Spur, an der man ihn wiedererkennen koennte. Die Spur liegt
woanders: in der Laufhistorie von GitHub selbst. Dieses Modul fragt sie
ab und beantwortet eine einzige Frage - hat es heute schon einen
erfolgreichen, von aussen angestossenen Lauf gegeben?

Bewusst nur fuer den geplanten Lauf gedacht. Ein von Hand oder von
aussen gestarteter Lauf prueft nichts: wer auf "Run workflow" drueckt,
will die Nachricht.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .sources.http import FetchError, fetch_json

BERLIN = ZoneInfo("Europe/Berlin")
API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"


def runs_url(repo: str, workflow: str, *, per_page: int = 30) -> str:
    """Adresse der Laufhistorie eines Workflows, gefiltert auf externe Anstoesse."""
    return (
        f"{API_ROOT}/repos/{repo}/actions/workflows/{workflow}/runs"
        f"?event=workflow_dispatch&per_page={per_page}"
    )


def _parse_timestamp(raw: str) -> datetime | None:
    """GitHub liefert ISO-8601 mit Z; datetime.fromisoformat mag das erst ab 3.11."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def successful_dispatch_today(payload: Any, now: datetime) -> dict | None:
    """Der juengste erfolgreiche Anstoss-Lauf von heute, oder None.

    "Heute" meint den Kalendertag in Berliner Ortszeit, nicht in UTC -
    sonst waere ein Push um 00:30 im Sommer noch dem Vortag zugerechnet.

    Fehlende oder unerwartete Felder fuehren nicht zu einer Ausnahme:
    im Zweifel gilt der Lauf als nicht stattgefunden, und der geplante
    Lauf verschickt. Eine Nachricht zuviel ist besser als keine.
    """
    if not isinstance(payload, dict):
        return None

    today = now.astimezone(BERLIN).date()
    candidates = []
    for run in payload.get("workflow_runs") or []:
        if not isinstance(run, dict):
            continue
        if run.get("conclusion") != "success":
            continue
        started = _parse_timestamp(run.get("run_started_at") or run.get("created_at") or "")
        if started is None:
            continue
        if started.astimezone(BERLIN).date() != today:
            continue
        candidates.append((started, run))

    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


def already_notified_today(
    repo: str,
    workflow: str,
    token: str,
    now: datetime,
    **fetch_kwargs: Any,
) -> tuple[bool, str]:
    """Fragt die Laufhistorie ab. Gibt Antwort und Begruendung zurueck.

    Scheitert der Abruf, lautet die Antwort "nein" - der geplante Lauf
    verschickt dann. Ein Ausfall der Historie darf nicht dazu fuehren,
    dass der Push ausbleibt; das waere genau der stille Ausfall, den
    dieses Projekt sonst ueberall vermeidet.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    try:
        payload, _ = fetch_json(
            runs_url(repo, workflow), extra_headers=headers, retries=2, **fetch_kwargs
        )
    except (FetchError, OSError, json.JSONDecodeError):
        return False, "Laufhistorie nicht abrufbar - im Zweifel wird verschickt"

    run = successful_dispatch_today(payload, now)
    if run is None:
        return False, "Heute noch kein erfolgreicher Anstoss - Lauf geht weiter"
    started = run.get("run_started_at") or run.get("created_at") or "?"
    return True, f"Heute bereits per Anstoss gelaufen ({started}) - uebersprungen"


def main(argv: list[str] | None = None) -> int:
    """Beendet sich mit 0, wenn heute schon verschickt wurde (also: ueberspringen)."""
    import argparse
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="z.B. name/propagation-bulletin")
    parser.add_argument("--workflow", required=True, help="z.B. notify.yml")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GITHUB_TOKEN fehlt - Doppelschutz uebersprungen, Lauf geht weiter")
        return 1

    done, reason = already_notified_today(
        args.repo, args.workflow, token, datetime.now(BERLIN)
    )
    print(reason)
    return 0 if done else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
