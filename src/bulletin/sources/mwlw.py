"""Handgepflegte Mittel- und Langwellenliste.

Anders als bei EiBi duerfen wir MWLIST nicht automatisiert abziehen und
veroeffentlichen - die Nutzungsbedingungen beschraenken das ausdruecklich
auf den privaten Gebrauch. Deshalb pflegt Till diese Liste selbst in
data/stations_mw_lw.yaml, mit seinen eigenen Empfangserfahrungen direkt
mit drin. Dieses Modul laedt sie nur und wandelt sie in dieselbe Link-Form
um, die auch die Kurzwellenseite aus EiBi erzeugt - fuer propagation.py
sind beide Quellen danach ununterscheidbar.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .eibi import LANGUAGE_CODES  # Wiederverwendung derselben Sprachkuerzel

VALID_LANGUAGES = frozenset(LANGUAGE_CODES.values()) | {None}


class ValidationError(ValueError):
    """Die Stationsliste ist formal falsch - besser jetzt scheitern als beim Rechnen."""


@dataclass(frozen=True)
class Station:
    """Ein Eintrag aus der handgepflegten Liste."""

    station_id: str
    name: str
    freq_khz: float
    lat: float
    lon: float
    power_kw: float
    language: str | None
    rarity_baseline: float
    notes: str = ""

    def __post_init__(self) -> None:
        # Wertebereich von Lat/Lon wird von geometry.Point geprueft -
        # dieselbe Regel soll nur an einer Stelle im Code stehen.
        from ..physics.geometry import Point

        try:
            Point(lat=self.lat, lon=self.lon)
        except ValueError as error:
            raise ValidationError(f"{self.station_id}: {error}") from error

        if not (148.5 <= self.freq_khz <= 1710.0):
            raise ValidationError(
                f"{self.station_id}: {self.freq_khz} kHz liegt ausserhalb LW/MW"
            )
        if not (0.0 <= self.rarity_baseline <= 1.0):
            raise ValidationError(
                f"{self.station_id}: rarity_baseline muss zwischen 0 und 1 liegen"
            )
        if self.power_kw <= 0.0:
            raise ValidationError(f"{self.station_id}: power_kw muss positiv sein")
        if self.language not in VALID_LANGUAGES:
            raise ValidationError(
                f"{self.station_id}: unbekannter Sprachcode {self.language!r}"
            )


def _station_from_dict(raw: dict[str, Any]) -> Station:
    try:
        return Station(
            station_id=str(raw["id"]),
            name=str(raw["name"]),
            freq_khz=float(raw["freq_khz"]),
            lat=float(raw["lat"]),
            lon=float(raw["lon"]),
            power_kw=float(raw["power_kw"]),
            language=raw.get("language"),
            rarity_baseline=float(raw.get("rarity_baseline", 0.5)),
            notes=str(raw.get("notes", "")),
        )
    except KeyError as error:
        raise ValidationError(f"Pflichtfeld fehlt: {error}") from error
    except (TypeError, ValueError) as error:
        raise ValidationError(f"Ungueltiger Wert in Eintrag {raw!r}: {error}") from error


def parse_stations(raw_yaml: str) -> list[Station]:
    """Die YAML-Datei parsen und formal pruefen.

    Wirft ValidationError bei doppelten IDs oder kaputten Feldern - lieber
    ein klarer Abbruch beim Laden als ein stiller Fehler mitten in der
    Bewertung.
    """
    data = yaml.safe_load(raw_yaml) or {}
    entries = data.get("stations", [])
    stations = [_station_from_dict(entry) for entry in entries]

    seen: set[str] = set()
    for station in stations:
        if station.station_id in seen:
            raise ValidationError(f"Doppelte station_id: {station.station_id}")
        seen.add(station.station_id)

    return stations


def load_stations(path: str | Path) -> list[Station]:
    with open(path, "r", encoding="utf-8") as handle:
        return parse_stations(handle.read())


def to_link(station: Station, rx) -> "Any":
    """Wandelt einen Stationseintrag in ein propagation.Link um.

    Import von propagation liegt bewusst in der Funktion, nicht am
    Dateikopf: physics-Module sollen nichts von sources wissen, aber
    sources duerfen physics zur Bequemlichkeit nutzen. Ein Modulzyklus
    waere sonst vorprogrammiert, sobald propagation.py selbst einmal
    etwas aus sources braucht.
    """
    from ..physics.geometry import Point
    from ..physics.propagation import Link

    return Link(
        station_id=station.station_id,
        freq_khz=station.freq_khz,
        tx=Point(lat=station.lat, lon=station.lon),
        rx=rx,
        power_kw=station.power_kw,
    )
