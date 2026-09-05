"""Abendlauf: liest das fertige Bulletin, holt den aktuellen Kp-Wert, schickt einen Push.

Bewusst duenn. Hier wird nicht gerechnet und nichts committet - die
Physik ist morgens gelaufen, die Seite laedt die volatilen Werte selbst
nach. Dieser Lauf hat genau eine Aufgabe: die eine Zeile bauen, die den
Unterschied macht zwischen einer Benachrichtigung, die weggewischt wird,
und einer, die angetippt wird.

Wie im Rest des Projekts: reiner Kern (compose) ohne Netz und ohne Uhr-
Zugriff, darum herum eine Schale (run), die abruft und verschickt.

Zwei Dinge, die dieser Lauf zusaetzlich leistet:

- Er ist die Ausfallmeldung fuer den Morgenlauf. Ist das Bulletin nicht
  von heute, sagt der Push genau das - sonst wuerdest Du wochenlang
  Empfehlungen von einem eingefrorenen Stand bekommen, ohne es zu merken.
- Er formuliert zeitbewusst. Um 20:30 ist im Juni noch hell und im
  Dezember seit zwei Stunden dunkel; dieselbe Uhrzeit bedeutet je nach
  Jahreszeit "kommt noch" oder "laeuft laengst".
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .physics.propagation import Weights
from .sources.http import USER_AGENT, FetchError
from .sources.swpc import KpSample, fetch_forecast_kp, fetch_observed_kp, latest

BERLIN = ZoneInfo("Europe/Berlin")
NTFY_BASE = "https://ntfy.sh"
DEFAULT_KP_BUCKET = 2  # ruhige Normallage, falls die SWPC gerade nicht antwortet


@dataclass(frozen=True)
class PushMessage:
    """Was verschickt wird - unabhaengig vom Dienst, der es zustellt."""

    title: str
    body: str
    click_url: str | None = None
    priority: int = 3
    tags: tuple[str, ...] = ()

    def to_ntfy_payload(self, topic: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "topic": topic,
            "title": self.title,
            "message": self.body,
            "priority": self.priority,
        }
        if self.click_url:
            payload["click"] = self.click_url
        if self.tags:
            payload["tags"] = list(self.tags)
        return payload


def current_kp(
    observed: list[KpSample], forecast: list[KpSample]
) -> KpSample | None:
    """Der belastbarste aktuell verfuegbare Kp-Wert.

    Beobachtete Werte schlagen Prognosen. Beide Endpunkte hinken der
    Realitaet um bis zu drei Stunden hinterher - das ist die Natur des
    Index, kein Fehler.
    """
    newest_observed = latest(observed)
    if newest_observed is not None:
        return newest_observed
    return latest(forecast)


def _timing_phrase(slot_hhmm: str, now: datetime) -> str:
    """Ordnet ein Zeitfenster relativ zum Jetzt ein.

    Dieselbe Push-Zeit bedeutet im Sommer und im Winter voellig
    Verschiedenes: im Juni ist um 20:30 noch hell und das gute Fenster
    kommt erst, im Dezember laeuft es seit Stunden.
    """
    try:
        hour, minute = (int(part) for part in slot_hhmm.split(":"))
    except ValueError:
        return slot_hhmm

    slot_minutes = hour * 60 + minute
    now_minutes = now.hour * 60 + now.minute
    # Mitternacht (00:00) gehoert zum laufenden Abend, nicht zum Vortag.
    if slot_minutes < 6 * 60:
        slot_minutes += 24 * 60
    if now_minutes < 6 * 60:
        now_minutes += 24 * 60

    delta = slot_minutes - now_minutes
    if delta > 45:
        return f"ab {slot_hhmm}"
    if delta < -45:
        return f"laeuft seit {slot_hhmm}"
    return f"jetzt ({slot_hhmm})"


def _entry_line(entry: dict, station: dict, bucket: int, now: datetime) -> str:
    """Eine Zeile fuer eine Station im Nachrichtentext."""
    slot = entry["best_by_kp"][bucket]
    freq = station["freq_khz"]
    freq_text = f"{freq:.0f} kHz" if freq < 30000 else f"{freq / 1000:.3f} MHz"
    line = f"{freq_text} {station['name']} - {_timing_phrase(slot['t'], now)}"

    if station.get("band_class") in ("mw", "lw") and station.get("bearing_deg") is not None:
        line += f", Loop {station['bearing_deg']:.0f} Grad"

    reason = (entry.get("rarity") or {}).get("reason")
    if reason:
        line += f" [{reason}]"
    return line


def compose(
    bulletin: dict,
    stations: dict,
    kp: KpSample | None,
    *,
    now: datetime,
    page_url: str | None,
    quiet_threshold: float,
    max_lines: int = 3,
) -> PushMessage:
    """Baut die Push-Nachricht. Rein: keine Uhr, kein Netz, keine Dateien."""
    bucket = kp.bucket if kp is not None else DEFAULT_KP_BUCKET
    kp_text = f"Kp {kp.kp:.2f}".rstrip("0").rstrip(".") if kp is not None else "Kp unbekannt"

    # Ist das Bulletin ueberhaupt von heute? Diese Pruefung steht bewusst
    # vor allem anderen - ein veralteter Stand darf nicht als frische
    # Empfehlung durchgehen.
    bulletin_date = bulletin.get("date", "")
    if bulletin_date != now.date().isoformat():
        return PushMessage(
            title="Bulletin nicht aktuell",
            body=(
                f"Stand vom {bulletin_date or 'unbekannt'}, erwartet {now.date().isoformat()}. "
                "Der Morgenlauf ist vermutlich fehlgeschlagen."
            ),
            click_url=page_url,
            priority=4,
            tags=("warning",),
        )

    station_index = stations.get("stations", {})
    main_entries = [e for e in bulletin.get("entries", []) if e.get("list_kind") == "main"]

    scored = [
        (e, e["best_by_kp"][bucket]["score"])
        for e in main_entries
        if e.get("best_by_kp") and station_index.get(e["station_id"])
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    if not scored:
        return PushMessage(
            title=f"Heute Abend nichts zu holen ({kp_text})",
            body="Keine Station im Bulletin, die heute Abend in Frage kommt.",
            click_url=page_url,
            priority=2,
            tags=("mute",),
        )

    top_entry, top_score = scored[0]
    top_station = station_index[top_entry["station_id"]]
    quiet = top_score < quiet_threshold

    if quiet:
        title = f"Ruhiger Abend ({kp_text})"
    else:
        slot = top_entry["best_by_kp"][bucket]
        title = f"{kp_text}: {top_station['name']} {_timing_phrase(slot['t'], now)}"

    lines = [
        _entry_line(entry, station_index[entry["station_id"]], bucket, now)
        for entry, _ in scored[:max_lines]
    ]
    if quiet:
        lines.insert(0, "Beste verfuegbare Moeglichkeiten:")

    return PushMessage(
        title=title,
        body="\n".join(lines),
        click_url=page_url,
        priority=2 if quiet else 3,
        tags=() if quiet else ("radio",),
    )


def send_ntfy(
    message: PushMessage,
    *,
    topic: str,
    base_url: str = NTFY_BASE,
    token: str | None = None,
    timeout: float = 15.0,
    opener=urllib.request.urlopen,
) -> None:
    """Verschickt die Nachricht ueber ntfy.

    opener ist injizierbar, damit die Tests keine echte Verbindung
    brauchen - dasselbe Muster wie in sources/http.py.
    """
    payload = json.dumps(message.to_ntfy_payload(topic)).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(base_url, data=payload, headers=headers, method="POST")
    with opener(request, timeout=timeout) as response:
        response.read()


def run(
    *,
    docs_dir: Path,
    data_dir: Path,
    cache_dir: Path,
    topic: str,
    page_url: str | None = None,
    token: str | None = None,
    now: datetime | None = None,
) -> PushMessage:
    """Tatsaechlicher Abendlauf. Gibt die verschickte Nachricht zurueck.

    Faellt der Kp-Abruf aus, wird trotzdem verschickt - mit der ruhigen
    Normallage als Annahme und einem entsprechend ehrlichen Titel. Ein
    fehlender Weltraumwetterwert ist kein Grund, den Abend ausfallen zu
    lassen.
    """
    now = now or datetime.now(BERLIN)
    weights = Weights.load(data_dir / "weights.yaml")
    quiet_threshold = weights.get("notify", "quiet_evening_threshold")

    bulletin = json.loads((docs_dir / "bulletin.json").read_text(encoding="utf-8"))
    stations = json.loads((docs_dir / "stations.json").read_text(encoding="utf-8"))

    try:
        observed, _ = fetch_observed_kp(cache_dir=cache_dir)
        forecast, _ = fetch_forecast_kp(cache_dir=cache_dir)
        kp = current_kp(observed, forecast)
    except (FetchError, OSError, KeyError, ValueError):
        kp = None

    message = compose(
        bulletin,
        stations,
        kp,
        now=now,
        page_url=page_url,
        quiet_threshold=quiet_threshold,
    )
    send_ntfy(message, topic=topic, token=token)
    return message


if __name__ == "__main__":
    import os
    import sys

    root = Path(__file__).resolve().parents[2]
    ntfy_topic = os.environ.get("NTFY_TOPIC")
    if not ntfy_topic:
        print("NTFY_TOPIC ist nicht gesetzt", file=sys.stderr)
        raise SystemExit(1)

    sent = run(
        docs_dir=root / "docs",
        data_dir=root / "data",
        cache_dir=root / "data" / "cache",
        topic=ntfy_topic,
        page_url=os.environ.get("PAGE_URL"),
        token=os.environ.get("NTFY_TOKEN"),
    )
    print(f"{sent.title} | {sent.body}", file=sys.stderr)
