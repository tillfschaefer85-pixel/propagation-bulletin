"""Schreibt einen Schnappschuss des Weltraumwetters nach docs/.

Hintergrund: Die Seite soll den Kp-Wert beim Oeffnen selbst holen, damit
sie auch abends aktuell ist. Ob der Browser den direkten Abruf bei der
NOAA zulaesst, haengt aber daran, ob deren Server fremde Herkuenfte
erlaubt - und darauf sollte sich diese Seite nicht verlassen muessen.

Dieser Lauf legt den Wert deshalb regelmaessig als Datei im eigenen
Repository ab. Die Seite versucht weiterhin zuerst den direkten Abruf
(frischer) und faellt auf diese Datei zurueck (immer erlaubt, weil
gleiche Herkunft). Kp aendert sich ohnehin nur alle drei Stunden - eine
Datei, die alle drei Stunden erneuert wird, ist praktisch so gut wie der
Direktabruf.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .model import write_json
from .sources.swpc import fetch_flux, fetch_observed_kp, smoothed_flux
from .sources.swpc import latest as latest_sample

SCHEMA_VERSION = 1


def build_snapshot(kp_value: float | None, kp_time: str | None, flux: float | None) -> dict:
    """Reiner Kern: baut den Schnappschuss aus bereits geholten Werten."""
    return {
        "schema_version": SCHEMA_VERSION,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "kp": kp_value,
        "kp_time": kp_time,
        "f107_flux": flux,
    }


def run(*, docs_dir: Path, cache_dir: Path) -> dict:
    """Holt Kp und Fluss und schreibt docs/space-weather.json."""
    kp_value: float | None = None
    kp_time: str | None = None
    flux: float | None = None

    try:
        observed, _ = fetch_observed_kp(cache_dir=cache_dir)
        newest = latest_sample(observed)
        if newest is not None:
            kp_value = newest.kp
            kp_time = newest.when.isoformat()
    except Exception:
        pass  # Ein fehlender Wert ist besser als eine kaputte Datei.

    try:
        samples, _ = fetch_flux(cache_dir=cache_dir)
        flux = smoothed_flux(samples)
    except Exception:
        pass

    if kp_value is None and flux is None:
        raise RuntimeError("Weder Kp noch Fluss abrufbar - Schnappschuss nicht geschrieben")

    snapshot = build_snapshot(kp_value, kp_time, flux)
    write_json(snapshot, docs_dir / "space-weather.json")
    return snapshot


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parents[2]
    result = run(docs_dir=root / "docs", cache_dir=root / "data" / "cache")
    print(json.dumps(result), file=sys.stderr)
