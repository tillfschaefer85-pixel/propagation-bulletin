"""Filter gegen Nicht-Rundfunk.

EiBi fuehrt neben Rundfunk auch Flugfunk, Seefunk, Zeitzeichen und
andere Dienste. Ein Eintrag wie "Shannon Aeradio" auf 10021 kHz ist als
englischsprachig gefuehrt und rutscht deshalb durch jeden Sprachfilter.

Statt an EiBis internen Kennzeichnungen zu raten - deren Konventionen
sich aendern koennen und die hier nicht verlaesslich pruefbar sind -
entscheidet die Frequenz: Rundfunk hat international zugeteilte Baender,
alles andere liegt ausserhalb. Die Baender stehen in
data/broadcast_bands.yaml und lassen sich dort erweitern.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Band:
    """Ein zusammenhaengender Rundfunkbereich."""

    name: str
    min_khz: float
    max_khz: float

    def contains(self, khz: float) -> bool:
        return self.min_khz <= khz <= self.max_khz


@dataclass(frozen=True)
class BandPlan:
    """Die Menge der Rundfunkbaender, plus Schalter zum Abschalten des Filters."""

    bands: tuple[Band, ...]
    enabled: bool = True

    def __len__(self) -> int:
        return len(self.bands)

    def band_for(self, khz: float) -> Band | None:
        for band in self.bands:
            if band.contains(khz):
                return band
        return None

    def is_broadcast(self, khz: float) -> bool:
        """Liegt die Frequenz in einem Rundfunkband?

        Bei abgeschaltetem Filter gilt alles als Rundfunk - dann bleibt
        die Liste so, wie EiBi sie liefert.
        """
        if not self.enabled:
            return True
        return self.band_for(khz) is not None


def parse_band_plan(raw_yaml: str) -> BandPlan:
    data = yaml.safe_load(raw_yaml) or {}
    bands = tuple(
        Band(name=str(b["name"]), min_khz=float(b["min"]), max_khz=float(b["max"]))
        for b in data.get("bands", [])
    )
    return BandPlan(bands=bands, enabled=bool(data.get("enabled", True)))


def load_band_plan(path: str | Path) -> BandPlan:
    with open(path, "r", encoding="utf-8") as handle:
        return parse_band_plan(handle.read())


# Wird gebraucht, wenn kein Bandplan uebergeben wird: dann filtert nichts.
NO_FILTER = BandPlan(bands=(), enabled=False)
