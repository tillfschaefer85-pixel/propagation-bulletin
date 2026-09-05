"""Netzzugriff mit Zeitlimit, Wiederholung und Cache.

Das einzige Modul neben den Quellen selbst, das ueberhaupt ins Netz darf.
Alles Weitere arbeitet mit dem, was hier zurueckkommt - damit bleibt die
Rechenlogik testbar, ohne dass ein Test jemals eine Verbindung braucht.

Der Cache ist ein reiner Ausfallschutz, kein Sparmechanismus: jeder Lauf
laedt die Ressource vollstaendig neu, und nur wenn das endgueltig
scheitert, wird auf die zuletzt erfolgreich geladene Fassung
zurueckgegriffen - sofern sie nicht aelter ist als max_cache_age_days.
Ein Bulletin mit gestrigen Sendeplaenen ist deutlich besser als gar
keins, solange das Alter sichtbar bleibt (siehe Fetched.from_cache).

Bedingte Anfragen (If-Modified-Since/ETag) waeren fuer die grosse
EiBi-Saisondatei eine sinnvolle Ergaenzung, sind aber bewusst noch nicht
umgesetzt - ein taeglicher Vollabruf von wenigen Megabyte ist
unproblematisch, und die Ehrlichkeit dieses Kommentars ist mehr wert als
eine Optimierung, die niemand gemessen hat.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

USER_AGENT = "propagation-bulletin/0.1 (privates Hobbyprojekt)"
DEFAULT_TIMEOUT = 20.0
DEFAULT_RETRIES = 3


class FetchError(RuntimeError):
    """Abruf endgueltig fehlgeschlagen und kein brauchbarer Cache vorhanden."""


@dataclass(frozen=True)
class Fetched:
    """Ergebnis eines Abrufs, inklusive Herkunft."""

    text: str
    from_cache: bool
    fetched_at: datetime

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.fetched_at).total_seconds()


def _cache_path(cache_dir: Path, url: str) -> Path:
    import hashlib

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{digest}.cache"


def fetch_text(
    url: str,
    *,
    cache_dir: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    max_cache_age_days: float | None = None,
    extra_headers: dict[str, str] | None = None,
    opener=urllib.request.urlopen,
    sleep=time.sleep,
) -> Fetched:
    """Laedt eine Textressource, faellt notfalls auf den Cache zurueck.

    opener und sleep sind Parameter, damit die Tests den Netzzugriff
    ersetzen koennen, ohne dass hier Testcode steht.
    """
    cache_file = _cache_path(cache_dir, url) if cache_dir else None

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            headers = {"User-Agent": USER_AGENT}
            if extra_headers:
                headers.update(extra_headers)
            request = urllib.request.Request(url, headers=headers)
            with opener(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
            if cache_file is not None:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(text, encoding="utf-8")
            return Fetched(text=text, from_cache=False, fetched_at=datetime.now(timezone.utc))
        except (urllib.error.URLError, OSError, ValueError) as error:
            last_error = error
            if attempt < retries - 1:
                sleep(2.0 ** attempt)  # 1 s, 2 s, 4 s - hoeflich gegenueber der Quelle

    if cache_file is not None and cache_file.exists():
        stamp = datetime.fromtimestamp(cache_file.stat().st_mtime, tz=timezone.utc)
        age_days = (datetime.now(timezone.utc) - stamp).total_seconds() / 86400.0
        if max_cache_age_days is None or age_days <= max_cache_age_days:
            return Fetched(
                text=cache_file.read_text(encoding="utf-8"),
                from_cache=True,
                fetched_at=stamp,
            )

    raise FetchError(f"Abruf fehlgeschlagen und kein brauchbarer Cache: {url}") from last_error


def fetch_json(url: str, **kwargs: Any) -> tuple[Any, Fetched]:
    """Wie fetch_text, gibt zusaetzlich das geparste JSON zurueck."""
    fetched = fetch_text(url, **kwargs)
    try:
        return json.loads(fetched.text), fetched
    except json.JSONDecodeError as error:
        raise FetchError(f"Antwort ist kein gueltiges JSON: {url}") from error
