"""Weltraumwetter von der NOAA Space Weather Prediction Center.

Zwei Produkte werden gebraucht: der planetare Kp-Index (beobachtet und
als Prognose) und der 10-cm-Radiofluss als Ersatzwert fuer die
Sonnenaktivitaet. Beide sind offene JSON-Endpunkte ohne Schluessel.

Wichtige Eigenheit, die beim ersten echten Abruf auffiel: Kp wird nicht
in ganzen Stufen gefuehrt, sondern in Dritteln (0.33, 0.67, 1.00, 1.33, ...).
Das ist der uebliche "fraktionale Kp" der Geophysik. Unsere Bewertung in
propagation.py rechnet dagegen mit ganzzahligen Stufen 0 bis 9, weil das
Bulletin je Kp-Stufe ein Zeitfenster vorausberechnet. Der Uebergang
zwischen beiden ist bucket_kp(): Runden auf die naechste ganze Stufe.

Ausserdem tragen die beiden Kp-Endpunkte unterschiedliche Feldnamen -
"Kp" (Grossbuchstabe) beim beobachteten Index, "kp" (klein) bei der
Prognose. Das ist keine Nachlaessigkeit unsererseits, sondern so, wie
NOAA es liefert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .http import Fetched, fetch_json

OBSERVED_KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
FORECAST_KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
FLUX_URL = "https://services.swpc.noaa.gov/products/10cm-flux-30-day.json"

Observed = Literal["observed", "estimated", "predicted"]


@dataclass(frozen=True)
class KpSample:
    """Ein Kp-Wert zu einem Zeitpunkt."""

    when: datetime
    kp: float
    status: Observed = "observed"

    @property
    def bucket(self) -> int:
        """Ganzzahlige Kp-Stufe fuer die Tabelle in propagation.py.

        Rundet auf die naechste Stufe: 1.33 -> 1, 1.67 -> 2. An der
        Grenze von x.5 rundet Python kaufmaennisch (banker's rounding),
        was hier keine Rolle spielt, da die Werte nie exakt auf .5 fallen.
        """
        return max(0, min(9, round(self.kp)))


@dataclass(frozen=True)
class FluxSample:
    """10-cm-Radiofluss (F10.7) zu einem Zeitpunkt, in SFU."""

    when: datetime
    flux: float


def _parse_time_tag(raw: str) -> datetime:
    """NOAA liefert Zeiten ohne Zeitzone, sind aber immer UTC."""
    return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)


def parse_observed_kp(payload: list[dict]) -> list[KpSample]:
    return [
        KpSample(when=_parse_time_tag(entry["time_tag"]), kp=float(entry["Kp"]))
        for entry in payload
    ]


def parse_forecast_kp(payload: list[dict]) -> list[KpSample]:
    return [
        KpSample(
            when=_parse_time_tag(entry["time_tag"]),
            kp=float(entry["kp"]),
            status=entry.get("observed", "predicted"),
        )
        for entry in payload
    ]


def parse_flux(payload: list[dict]) -> list[FluxSample]:
    return [
        FluxSample(when=_parse_time_tag(entry["time_tag"]), flux=float(entry["flux"]))
        for entry in payload
    ]


def latest(samples: list) -> object | None:
    """Der jüngste Eintrag einer zeitlich sortierten Liste.

    NOAA liefert seine Produkte bereits chronologisch, ein erneutes
    Sortieren waere unnoetig - aber max() nach when ist robust, falls
    sich das je aendert.
    """
    if not samples:
        return None
    return max(samples, key=lambda s: s.when)


def fetch_observed_kp(*, cache_dir: Path, **kwargs) -> tuple[list[KpSample], Fetched]:
    payload, fetched = fetch_json(
        OBSERVED_KP_URL,
        cache_dir=cache_dir,
        max_cache_age_days=kwargs.pop("max_cache_age_days", 1.0),
        **kwargs,
    )
    return parse_observed_kp(payload), fetched


def fetch_forecast_kp(*, cache_dir: Path, **kwargs) -> tuple[list[KpSample], Fetched]:
    payload, fetched = fetch_json(
        FORECAST_KP_URL,
        cache_dir=cache_dir,
        max_cache_age_days=kwargs.pop("max_cache_age_days", 1.0),
        **kwargs,
    )
    return parse_forecast_kp(payload), fetched


def fetch_flux(*, cache_dir: Path, **kwargs) -> tuple[list[FluxSample], Fetched]:
    """F10.7-Fluss der letzten 30 Tage.

    Ein einzelner Tageswert waere anfaelliger fuer Ausreisser durch
    einzelne Flares - fuer die MUF-Schaetzung ist deshalb sinnvoller,
    spaeter einen kurzen gleitenden Durchschnitt zu bilden, statt nur
    den letzten Wert zu nehmen. Diese Funktion liefert dafuer die
    Rohdaten, die Glaettung passiert im Aufrufer.
    """
    payload, fetched = fetch_json(
        FLUX_URL,
        cache_dir=cache_dir,
        max_cache_age_days=kwargs.pop("max_cache_age_days", 2.0),
        **kwargs,
    )
    return parse_flux(payload), fetched


def smoothed_flux(samples: list[FluxSample], *, days: int = 3) -> float | None:
    """Gleitender Durchschnitt der letzten N Tage, als robuster Eingabewert."""
    if not samples:
        return None
    ordered = sorted(samples, key=lambda s: s.when)
    recent = ordered[-days:]
    return sum(s.flux for s in recent) / len(recent)
