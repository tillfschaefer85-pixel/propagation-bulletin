"""Kugelgeometrie fuer Ausbreitungsstrecken.

Reine Rechenfunktionen: keine Netzwerkzugriffe, keine Dateien, keine Uhr.
Alles hier ist deterministisch und damit ohne Mocking testbar.

Konvention:
    Breite  (lat) in Grad, positiv = Nord
    Laenge  (lon) in Grad, positiv = Ost
    Peilung (bearing) in Grad, 0 = Nord, 90 = Ost, im Uhrzeigersinn
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Mittlerer Erdradius (IUGG). Fuer Funkstrecken ist die Kugelnaeherung
# vollkommen ausreichend - der Fehler gegenueber dem Ellipsoid liegt bei
# rund 0,3 %, was gegen die Unsicherheit der Ausbreitung nicht ins Gewicht faellt.
EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Point:
    """Ein Ort auf der Erdkugel."""

    lat: float
    lon: float

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat <= 90.0:
            raise ValueError(f"Breite ausserhalb [-90, 90]: {self.lat}")
        if not -180.0 <= self.lon <= 360.0:
            raise ValueError(f"Laenge ausserhalb [-180, 360]: {self.lon}")

    @property
    def lat_rad(self) -> float:
        return math.radians(self.lat)

    @property
    def lon_rad(self) -> float:
        return math.radians(self.lon)


# Der Empfangsstandort. Liegt hier, damit ihn nicht jedes Modul neu erfindet.
BERGHEIM = Point(lat=50.9553, lon=6.6394)


def normalize_bearing(deg: float) -> float:
    """Bildet einen Winkel auf [0, 360) ab."""
    return deg % 360.0


def great_circle_distance_km(a: Point, b: Point) -> float:
    """Kuerzeste Entfernung ueber die Kugeloberflaeche.

    Haversine-Formel, weil sie auch bei sehr kleinen Abstaenden numerisch
    stabil bleibt - anders als die naive Kosinusformel.
    """
    dlat = b.lat_rad - a.lat_rad
    dlon = b.lon_rad - a.lon_rad
    h = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(a.lat_rad) * math.cos(b.lat_rad) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, h)))


def initial_bearing_deg(a: Point, b: Point) -> float:
    """Anfangspeilung von a nach b (rechtweisend Nord).

    Das ist die Richtung, in die die Loop zeigen muss. Achtung: auf langen
    Strecken aendert sich die Peilung entlang des Grosskreises, der Startwert
    ist aber genau der, der am Empfangsort zaehlt.
    """
    dlon = b.lon_rad - a.lon_rad
    y = math.sin(dlon) * math.cos(b.lat_rad)
    x = math.cos(a.lat_rad) * math.sin(b.lat_rad) - math.sin(a.lat_rad) * math.cos(
        b.lat_rad
    ) * math.cos(dlon)
    return normalize_bearing(math.degrees(math.atan2(y, x)))


def loop_null_bearing_deg(bearing: float) -> tuple[float, float]:
    """Die beiden Richtungen, in denen eine Rahmenantenne ausnullt.

    Eine Loop hat ihr Minimum quer zur Hauptrichtung. Wer einen Stoerer
    ausblenden will, dreht ihn in eine dieser beiden Richtungen.
    """
    return normalize_bearing(bearing + 90.0), normalize_bearing(bearing - 90.0)


def path_points(a: Point, b: Point, count: int) -> list[Point]:
    """Gleichmaessig verteilte Stuetzpunkte auf dem Grosskreis von a nach b.

    Start- und Endpunkt sind enthalten. Gebraucht wird das fuer die Frage,
    welcher Anteil der Strecke im Dunkeln liegt - auf Mittel- und Langwelle
    ist genau das die entscheidende Groesse.
    """
    if count < 2:
        raise ValueError("Mindestens zwei Stuetzpunkte noetig")

    lat1, lon1 = a.lat_rad, a.lon_rad
    lat2, lon2 = b.lat_rad, b.lon_rad

    # Winkelabstand zwischen den Endpunkten
    d = great_circle_distance_km(a, b) / EARTH_RADIUS_KM
    if d == 0.0:
        return [a for _ in range(count)]

    points: list[Point] = []
    for i in range(count):
        f = i / (count - 1)
        # Sphaerische lineare Interpolation
        sin_d = math.sin(d)
        p = math.sin((1.0 - f) * d) / sin_d
        q = math.sin(f * d) / sin_d
        x = p * math.cos(lat1) * math.cos(lon1) + q * math.cos(lat2) * math.cos(lon2)
        y = p * math.cos(lat1) * math.sin(lon1) + q * math.cos(lat2) * math.sin(lon2)
        z = p * math.sin(lat1) + q * math.sin(lat2)
        points.append(
            Point(
                lat=math.degrees(math.atan2(z, math.sqrt(x * x + y * y))),
                lon=math.degrees(math.atan2(y, x)),
            )
        )
    return points


def midpoint(a: Point, b: Point) -> Point:
    """Mittelpunkt der Strecke - der Ort, dessen Ionosphaere auf Kurzwelle zaehlt."""
    return path_points(a, b, 3)[1]
