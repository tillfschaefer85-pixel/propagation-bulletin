"""Sendestandort-Nachschlagetabelle fuer EiBi-Eintraege.

EiBi liefert Frequenz, Sender, Sprache und einen Standort-*Code* - aber
keine Koordinate. Diese Tabelle (data/tx_sites.yaml) bildet den Code auf
Lat/Lon ab. Fehlt ein Standort, wird bewusst nicht geraten (siehe die
Kommentare in der YAML-Datei selbst) - der Aufrufer bekommt None zurueck
und entscheidet, was damit geschieht (in build.py: ueberspringen und
mitzaehlen).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..physics.geometry import Point


class TxSiteTable:
    """Nachschlagetabelle ITU-Code + Standort-Code -> Point."""

    def __init__(self, sites: dict[str, Point]):
        self._sites = sites

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
        if not transmitter_site:
            return None
        return self._sites.get(f"{itu}-{transmitter_site}")

    def coverage(self) -> frozenset[str]:
        """Die abgedeckten ITU-Laendercodes, fuer eine schnelle Uebersicht."""
        return frozenset(key.split("-", 1)[0] for key in self._sites)


def load_tx_sites(path: str | Path) -> TxSiteTable:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    sites: dict[str, Point] = {}
    for key, coords in data.get("sites", {}).items():
        sites[key] = Point(lat=float(coords["lat"]), lon=float(coords["lon"]))
    return TxSiteTable(sites)
