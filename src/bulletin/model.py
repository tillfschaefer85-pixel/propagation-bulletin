"""Datenmodell fuer die Ausgabedateien.

Zwei Dateien, zwei Lebenszyklen:

    stations.json   Stammdaten (Name, Standort, Peilung, Entfernung).
                     Aendert sich selten, der Browser darf sie cachen.
    bulletin.json    Tagesergebnis: je Station das Gewinnerfenster fuer
                     jede der zehn Kp-Stufen, plus Interessantheit.

Ein dritter Ausgang ist die Archivkopie unter archive/<datum>.json -
identisch zu bulletin.json, nur dauerhaft aufgehoben. Sie ist die
Datengrundlage fuer den spaeteren Wechsel von "taeglich" auf
"nur bei guten Bedingungen".

Alles hier ist reine Datenhaltung: keine Physik, kein Netz. Die
Bewertung kommt aus physics.propagation, die Rohdaten aus sources.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1

BandClass = Literal["lw", "mw", "sw"]
ListKind = Literal["main", "dx"]


@dataclass(frozen=True)
class StationMeta:
    """Stammdaten einer Station - landet in stations.json."""

    station_id: str
    name: str
    band_class: BandClass
    freq_khz: float
    language: str | None
    distance_km: float
    bearing_deg: float
    power_kw: float
    source: Literal["eibi", "mwlw"]
    site_name: str | None = None
    null_bearings_deg: tuple[float, float] | None = None
    hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rarity:
    """Seltenheit einer Station - siehe Architekturentscheidung dazu.

    baseline steht schon beim Bau fest (dauerhaft schwer/leicht).
    today wird gesetzt, wenn heute etwas Ungewoehnliches moeglich ist
    (z.B. eine Grauzonenoeffnung); sonst bleibt es leer. Das dritte,
    wertvollste Kriterium - "fuer Dich noch nie geloggt" - kommt erst
    mit dem Empfangslogbuch und ist hier bewusst noch nicht vorgesehen.
    """

    baseline: float
    today: float | None = None
    reason: str | None = None

    def combined(self) -> float:
        """Der Wert, der tatsaechlich in die Rangfolge eingeht."""
        return self.today if self.today is not None else self.baseline


@dataclass(frozen=True)
class BestSlot:
    """Das Gewinnerfenster fuer eine Kp-Stufe - ein Eintrag aus best_by_kp()."""

    kp: int
    t: str
    score: float
    gate: float
    components: dict[str, float]


@dataclass(frozen=True)
class BulletinEntry:
    """Eine Station im Tagesbulletin, mit Verweis auf ihre Stammdaten."""

    station_id: str
    list_kind: ListKind
    best_by_kp: tuple[BestSlot, ...]
    rarity: Rarity
    interest_rank_score: float  # score bei Kp=2 (Referenzwert) * rarity, siehe build.py


@dataclass(frozen=True)
class Bulletin:
    """Das komplette Tagesergebnis."""

    schema_version: int
    date: str  # ISO-Datum, Ortszeit des Abends
    generated_at: str  # ISO-Zeitstempel UTC
    eibi_season: str
    days_until_season_change: int
    f107_flux: float | None
    entries: tuple[BulletinEntry, ...]


def _to_jsonable(value: Any) -> Any:
    """Wandelt Dataclass-Werte in JSON-taugliche Strukturen um.

    dataclasses.asdict() allein reicht nicht: Tupel sollen als Listen
    erscheinen (JSON kennt kein Tupel), und die Struktur soll lesbar
    bleiben, nicht nur technisch korrekt.
    """
    if isinstance(value, tuple):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def stations_to_dict(stations: list[StationMeta]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "stations": {s.station_id: _to_jsonable(asdict(s)) for s in stations},
    }


def bulletin_to_dict(bulletin: Bulletin) -> dict[str, Any]:
    return _to_jsonable(asdict(bulletin))


def write_json(data: dict[str, Any], path: str | Path) -> None:
    """Schreibt kompakt lesbares JSON - fuer ein Repo, das Diffs zeigt.

    sort_keys=False, weil die Einfuegereihenfolge (z.B. bester Kp zuerst)
    bewusst gewaehlt ist und nicht alphabetisch durcheinandergewuerfelt
    werden soll.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_bulletin(bulletin: Bulletin, *, docs_dir: Path, today: date) -> tuple[Path, Path]:
    """Schreibt bulletin.json und die Archivkopie, gibt beide Pfade zurueck."""
    data = bulletin_to_dict(bulletin)
    live_path = docs_dir / "bulletin.json"
    archive_path = docs_dir / "archive" / f"{today.isoformat()}.json"
    write_json(data, live_path)
    write_json(data, archive_path)
    return live_path, archive_path


def write_stations(stations: list[StationMeta], *, docs_dir: Path) -> Path:
    path = docs_dir / "stations.json"
    write_json(stations_to_dict(stations), path)
    return path
