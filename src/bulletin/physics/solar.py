"""Sonnenstand, Daemmerung und Dunkelheit entlang einer Funkstrecke.

Reine Rechenfunktionen ohne Netz und ohne Dateien. Alle Zeiten sind
timezone-aware und werden intern konsequent nach UTC gerechnet - der
GitHub-Runner laeuft in UTC, der Nutzer denkt in Ortszeit, und genau an
dieser Naht entstehen sonst die Fehler, die niemand bemerkt.

Algorithmus nach den Sonnenstandsgleichungen der NOAA (die Grundlage des
bekannten Solar Calculator). Genauigkeit deutlich besser als eine
Bogenminute im hier interessierenden Zeitraum - fuer Ausbreitungsfragen
um Groessenordnungen mehr, als noetig waere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from .geometry import Point

# Refraktionskorrigierte Sonnenhoehe fuer den Auf- und Untergang: der
# Sonnenrand beruehrt den Horizont, wenn der Mittelpunkt 0,833 Grad darunter steht.
HORIZON_DEG = -0.833
CIVIL_TWILIGHT_DEG = -6.0
NAUTICAL_TWILIGHT_DEG = -12.0

# Ab dieser Sonnenhoehe gilt die D-Schicht als weitgehend abgebaut. Erst
# dann traegt Mittel- und Langwelle ueber groessere Entfernungen.
D_LAYER_GONE_DEG = -6.0


@dataclass(frozen=True)
class SolarPosition:
    """Sonnenstand an einem Ort zu einer Zeit."""

    elevation_deg: float
    azimuth_deg: float


def _julian_day(when: datetime) -> float:
    """Julianisches Datum aus einem timezone-aware datetime."""
    if when.tzinfo is None:
        raise ValueError("Zeitangabe muss timezone-aware sein")
    utc = when.astimezone(timezone.utc)
    # Unix-Epoche = JD 2440587.5
    return utc.timestamp() / 86400.0 + 2440587.5


def _julian_century(jd: float) -> float:
    return (jd - 2451545.0) / 36525.0


def solar_position(point: Point, when: datetime) -> SolarPosition:
    """Sonnenhoehe und Azimut an einem Ort zu einer Zeit."""
    t = _julian_century(_julian_day(when))

    # Mittlere Laenge und mittlere Anomalie der Sonne
    mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    mean_anom_rad = math.radians(mean_anom)

    # Mittelpunktsgleichung -> wahre Laenge
    center = (
        math.sin(mean_anom_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2.0 * mean_anom_rad) * (0.019993 - 0.000101 * t)
        + math.sin(3.0 * mean_anom_rad) * 0.000289
    )
    true_long = mean_long + center

    # Scheinbare Laenge (Nutation und Aberration)
    omega = 125.04 - 1934.136 * t
    app_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    # Schiefe der Ekliptik
    seconds = 21.448 - t * (46.8150 + t * (0.00059 - t * 0.001813))
    obliquity = 23.0 + (26.0 + seconds / 60.0) / 60.0
    obliquity_corr = obliquity + 0.00256 * math.cos(math.radians(omega))
    obliquity_rad = math.radians(obliquity_corr)

    app_long_rad = math.radians(app_long)
    declination = math.asin(math.sin(obliquity_rad) * math.sin(app_long_rad))

    # Zeitgleichung in Minuten
    y = math.tan(obliquity_rad / 2.0) ** 2
    mean_long_rad = math.radians(mean_long)
    eq_time = 4.0 * math.degrees(
        y * math.sin(2.0 * mean_long_rad)
        - 2.0 * 0.016708634 * math.sin(mean_anom_rad)
        + 4.0
        * 0.016708634
        * y
        * math.sin(mean_anom_rad)
        * math.cos(2.0 * mean_long_rad)
        - 0.5 * y * y * math.sin(4.0 * mean_long_rad)
        - 1.25 * 0.016708634**2 * math.sin(2.0 * mean_anom_rad)
    )

    utc = when.astimezone(timezone.utc)
    # Mikrosekunden bewusst mitnehmen: ohne sie ist die Sonnenhoehe in
    # Sekundenstufen gequantelt, und die Bisektion bei Auf- und Untergang
    # kommt nicht unter rund 0,003 Grad Restfehler.
    minutes_of_day = (
        utc.hour * 60.0
        + utc.minute
        + utc.second / 60.0
        + utc.microsecond / 60_000_000.0
    )
    true_solar_time = (minutes_of_day + eq_time + 4.0 * point.lon) % 1440.0

    hour_angle = true_solar_time / 4.0 - 180.0
    if hour_angle < -180.0:
        hour_angle += 360.0
    hour_angle_rad = math.radians(hour_angle)

    lat_rad = point.lat_rad
    cos_zenith = math.sin(lat_rad) * math.sin(declination) + math.cos(
        lat_rad
    ) * math.cos(declination) * math.cos(hour_angle_rad)
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = math.acos(cos_zenith)
    elevation = 90.0 - math.degrees(zenith)

    # Azimut, von Nord im Uhrzeigersinn
    sin_zenith = math.sin(zenith)
    if abs(sin_zenith) < 1e-9:
        azimuth = 0.0
    else:
        cos_az = (math.sin(lat_rad) * math.cos(zenith) - math.sin(declination)) / (
            math.cos(lat_rad) * sin_zenith
        )
        cos_az = max(-1.0, min(1.0, cos_az))
        # NOAA-Konvention: nachmittags (Stundenwinkel positiv) um 180 Grad
        # gedreht, vormittags gespiegelt. Ohne diesen Schritt zeigt der
        # Azimut mittags nach Norden statt nach Sueden.
        raw = math.degrees(math.acos(cos_az))
        azimuth = (raw + 180.0) if hour_angle > 0.0 else (540.0 - raw)

    return SolarPosition(elevation_deg=elevation, azimuth_deg=azimuth % 360.0)


def sun_elevation_deg(point: Point, when: datetime) -> float:
    """Kurzform, weil in der Ausbreitungsrechnung fast nur die Hoehe zaehlt."""
    return solar_position(point, when).elevation_deg


def _crossing(
    point: Point,
    start: datetime,
    end: datetime,
    target_deg: float,
) -> datetime | None:
    """Sucht per Bisektion den Zeitpunkt, an dem die Sonnenhoehe target_deg kreuzt.

    Setzt voraus, dass zwischen start und end genau ein Vorzeichenwechsel liegt.
    """
    f_start = sun_elevation_deg(point, start) - target_deg
    f_end = sun_elevation_deg(point, end) - target_deg
    if f_start == 0.0:
        return start
    if f_start * f_end > 0.0:
        return None

    lo, hi = start, end
    for _ in range(40):  # bis auf Bruchteile einer Sekunde genau
        mid = lo + (hi - lo) / 2
        f_mid = sun_elevation_deg(point, mid) - target_deg
        if f_start * f_mid <= 0.0:
            hi = mid
        else:
            lo, f_start = mid, f_mid
    return lo + (hi - lo) / 2


def _scan_crossings(
    point: Point,
    day_start_utc: datetime,
    target_deg: float,
    step_minutes: int = 10,
) -> list[tuple[datetime, bool]]:
    """Findet alle Kreuzungen eines Tages. bool = True bei Anstieg (Aufgang)."""
    results: list[tuple[datetime, bool]] = []
    step = timedelta(minutes=step_minutes)
    prev_time = day_start_utc
    prev_val = sun_elevation_deg(point, prev_time) - target_deg
    steps = int(24 * 60 / step_minutes)
    for _ in range(steps):
        cur_time = prev_time + step
        cur_val = sun_elevation_deg(point, cur_time) - target_deg
        if prev_val < 0.0 <= cur_val or prev_val > 0.0 >= cur_val:
            crossing = _crossing(point, prev_time, cur_time, target_deg)
            if crossing is not None:
                results.append((crossing, cur_val > prev_val))
        prev_time, prev_val = cur_time, cur_val
    return results


def sunrise_sunset(
    point: Point,
    day_start_utc: datetime,
    target_deg: float = HORIZON_DEG,
) -> tuple[datetime | None, datetime | None]:
    """Auf- und Untergang innerhalb der 24 Stunden ab day_start_utc.

    Gibt (None, None) zurueck, wenn die Sonne durchgehend oben oder unten
    bleibt - Polartag und Polarnacht sind fuer nordische Sendestandorte
    ein realer Fall und duerfen nicht in eine Exception laufen.
    """
    crossings = _scan_crossings(point, day_start_utc, target_deg)
    rise = next((t for t, up in crossings if up), None)
    set_ = next((t for t, up in crossings if not up), None)
    return rise, set_


def darkness_fraction(
    a: Point,
    b: Point,
    when: datetime,
    *,
    threshold_deg: float = D_LAYER_GONE_DEG,
    samples: int = 21,
) -> float:
    """Anteil der Strecke, der zum Zeitpunkt when im Dunkeln liegt.

    0.0 = Strecke komplett in der Sonne, 1.0 = komplett in der Nacht.
    Das ist die Schluesselgroesse fuer Mittel- und Langwelle: erst wenn die
    D-Schicht ueber dem gesamten Weg abgebaut ist, traegt die Raumwelle.

    Gepuffert, weil die Bewertung dieselbe Strecke zum selben Zeitpunkt
    fuer jede der zehn Kp-Stufen erneut braucht - die Dunkelheit haengt
    aber gar nicht vom Kp-Wert ab. Ohne den Puffer wird jede dieser
    Rechnungen (21 Sonnenstaende) zehnmal umsonst ausgefuehrt.
    """
    return _darkness_fraction_cached(a, b, when, threshold_deg, samples)


@lru_cache(maxsize=200_000)
def _darkness_fraction_cached(
    a: Point, b: Point, when: datetime, threshold_deg: float, samples: int
) -> float:
    from .geometry import path_points  # lokal, um Zyklen zu vermeiden

    points = path_points(a, b, samples)
    dark = sum(1 for p in points if sun_elevation_deg(p, when) < threshold_deg)
    return dark / len(points)


def is_greyline(
    point: Point,
    when: datetime,
    *,
    window_deg: float = 6.0,
) -> bool:
    """Steht der Ort gerade in der Daemmerungszone?

    Die Grauzone ist das schmale Band, in dem die D-Schicht schon
    verschwunden, die F-Schicht aber noch ionisiert ist - dort sind
    Reichweiten moeglich, die sonst nie zustande kommen.
    """
    return -window_deg <= sun_elevation_deg(point, when) <= window_deg
