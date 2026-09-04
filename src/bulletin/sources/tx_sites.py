"""Sendestandort-Nachschlagetabelle fuer EiBi-Eintraege.

EiBi liefert Frequenz, Sender, Sprache und einen Standort-*Code* - aber
keine Koordinate. Diese Tabelle (data/tx_sites.yaml) bildet den Code auf
Lat/Lon ab. Fehlt ein Standort, wird bewusst nicht geraten (siehe die
Kommentare in der YAML-Datei selbst) - der Aufrufer bekommt None zurueck
und entscheidet, was damit geschieht (in build.py: ueberspringen und
mitzaehlen).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..physics.geometry import Point


@dataclass(frozen=True)
class TxSite:
    """Ein Sendestandort: wo er liegt und wie er heisst.

    Der Name stand frueher nur als Kommentar in der YAML-Datei. Er gehoert
    in die Daten, weil die Seite ihn im Detailfenster zeigen soll -
    "Nauen" sagt mehr als 52,65 Grad Nord.
    """

    point: Point
    name: str | None = None


class TxSiteTable:
    """Nachschlagetabelle ITU-Code + Standort-Code -> Point."""

    def __init__(self, sites: dict[str, TxSite | Point]):
        # Point wird weiterhin angenommen, damit bestehende Tests und
        # Aufrufer nicht angefasst werden muessen.
        self._sites: dict[str, TxSite] = {
            key: value if isinstance(value, TxSite) else TxSite(point=value)
            for key, value in sites.items()
        }

    def __len__(self) -> int:
        return len(self._sites)

    def lookup(self, itu: str, transmitter_site: str) -> Point | None:
        """Sucht die Koordinate fuer einen EiBi-Eintrag.

        Ein leerer transmitter_site-Code bedeutet laut EiBi-README entweder
        "nur ein Sender im Land" oder "Standort unbekannt" - beides ist ohne
        weitere Pruefung nicht unterscheidbar, deshalb wird hier nicht auf
        einen Landes-Mittelpunkt zurueckgefallen, sondern konsequent None
        zurueckgegeben.
        """
        site = self.lookup_site(itu, transmitter_site)
        return site.point if site is not None else None

    def lookup_site(self, itu: str, transmitter_site: str) -> TxSite | None:
        """Wie lookup(), gibt aber den vollen Eintrag samt Namen zurueck."""
        if not transmitter_site:
            return None
        return self._sites.get(f"{itu}-{transmitter_site}")

    def coverage(self) -> frozenset[str]:
        """Die abgedeckten ITU-Laendercodes, fuer eine schnelle Uebersicht."""
        return frozenset(key.split("-", 1)[0] for key in self._sites)


def load_tx_sites(path: str | Path) -> TxSiteTable:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    sites: dict[str, TxSite] = {}
    for key, coords in data.get("sites", {}).items():
        sites[key] = TxSite(
            point=Point(lat=float(coords["lat"]), lon=float(coords["lon"])),
            name=coords.get("name"),
        )
    return TxSiteTable(sites)
