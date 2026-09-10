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

    def __init__(
        self,
        sites: dict[str, TxSite | Point],
        country_defaults: dict[str, TxSite] | None = None,
    ):
        self._country_defaults: dict[str, TxSite] = country_defaults or {}
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
        """Wie lookup(), gibt aber den vollen Eintrag samt Namen zurueck.

        Behandelt auch Uebernahmen. EiBi schreibt sie mit fuehrendem
        Schraegstrich: Radio Taiwan mit dem Eintrag "/BUL-s" sendet nicht
        aus Taiwan, sondern ueber die bulgarische Anlage in Sofia. Die
        Herkunft der Station sagt dann nichts ueber den Funkweg - was
        zaehlt, ist der Standort der Antenne.

        Das ist im internationalen Kurzwellenrundfunk der Normalfall, nicht
        die Ausnahme: BBC ueber Zypern, Radio Taiwan ueber Bulgarien, HCJB
        ueber Deutschland. Frueher fielen all diese Sendungen durch, weil
        stur "TWN-/BUL-s" gesucht wurde - ein Schluessel, den es nicht
        geben kann.
        """
        if transmitter_site.startswith("/"):
            relay = transmitter_site[1:]
            # "/BUL-s" nennt Land und Anlage, "/CYP" nur das Land. Im zweiten
            # Fall hilft nur ein Laenderstandard - und den gibt es nur, wo das
            # Land tatsaechlich bloss eine Anlage hat.
            if "-" in relay:
                return self._sites.get(relay)
            return self._country_defaults.get(relay)

        if not transmitter_site:
            # Ein leeres Feld ist laut EiBi-README keine Luecke, sondern eine
            # Aussage: "No such code is used if there is only one transmitter
            # site in that country." Fuer solche Laender ist die Koordinate
            # eindeutig. Fuer alle anderen bleibt es unaufloesbar.
            return self._country_defaults.get(itu)

        return self._sites.get(f"{itu}-{transmitter_site}")

    def coverage(self) -> frozenset[str]:
        """Die abgedeckten ITU-Laendercodes, fuer eine schnelle Uebersicht."""
        return frozenset(key.split("-", 1)[0] for key in self._sites) | frozenset(
            self._country_defaults
        )


def load_tx_sites(path: str | Path) -> TxSiteTable:
    with open(path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    def to_site(coords: dict) -> TxSite:
        return TxSite(
            point=Point(lat=float(coords["lat"]), lon=float(coords["lon"])),
            name=coords.get("name"),
        )

    sites = {key: to_site(v) for key, v in (data.get("sites") or {}).items()}
    defaults = {key: to_site(v) for key, v in (data.get("country_defaults") or {}).items()}
    return TxSiteTable(sites, defaults)
