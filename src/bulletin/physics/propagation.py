"""Bewertung der Empfangschance einer Strecke.

Wichtig zum Verstaendnis: das hier ist eine Rangfolge, keine
Feldstaerkevorhersage. Ziel ist die Frage "was steht heute Abend oben",
nicht "wieviel Mikrovolt kommen an". Alle Konstanten stehen deshalb in
weights.yaml und sind bewusst zum Nachjustieren gedacht.

Aufbau der Bewertung:

    score = tor * gewichtete Summe der Qualitaetsterme

Das Tor liegt zwischen 0 und 1 und bildet die harten Bedingungen ab
(Dunkelheit auf MW/LW, Frequenzfenster auf KW). Ist das Tor zu, hilft
keine Sendeleistung - deshalb multipliziert und nicht addiert.

Alle Teilwerte werden mit ausgegeben. Eine nackte Endzahl waere nicht
nachvollziehbar und damit auch nicht verbesserbar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from .geometry import Point, great_circle_distance_km, midpoint
from .solar import darkness_fraction, sun_elevation_deg

BandClass = Literal["lw", "mw", "sw"]

EARTH_RADIUS_KM = 6371.0088


def classify_band(freq_khz: float) -> BandClass:
    """Bandklasse aus der Frequenz. Die Grenzen folgen der ITU-Einteilung."""
    if freq_khz < 300.0:
        return "lw"
    if freq_khz <= 1710.0:
        return "mw"
    return "sw"


@dataclass(frozen=True)
class Weights:
    """Alle Stellschrauben der Bewertung."""

    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "Weights":
        with open(path, "r", encoding="utf-8") as handle:
            return cls(raw=yaml.safe_load(handle))

    def get(self, *keys: str) -> Any:
        node: Any = self.raw
        for key in keys:
            if key not in node:
                raise KeyError(f"Fehlender Gewichtsparameter: {'.'.join(keys)}")
            node = node[key]
        return node


@dataclass(frozen=True)
class Link:
    """Eine Strecke vom Empfangsort zu einem Sender."""

    station_id: str
    freq_khz: float
    tx: Point
    rx: Point
    power_kw: float
    target_bearing_deg: float | None = None  # Hauptstrahlrichtung, falls gerichtet

    @property
    def band_class(self) -> BandClass:
        return classify_band(self.freq_khz)

    @property
    def distance_km(self) -> float:
        return great_circle_distance_km(self.rx, self.tx)


def interest_score(total: float, rarity: float, exponent: float) -> float:
    """Interessantheit = Empfangbarkeit mal Seltenheit.

    Der Exponent steuert, wie stark die Seltenheit durchschlaegt: 0
    ergibt die reine Empfangbarkeit, 1 die volle Gewichtung.

    Steht bewusst als freie Funktion hier und nicht nur als Methode auf
    Score: build.py braucht dieselbe Rechnung auf einem bereits als
    dict vorliegenden Ergebnis, und zwei getippte Fassungen derselben
    Formel laufen frueher oder spaeter auseinander.
    """
    if rarity <= 0.0:
        return 0.0
    return total * rarity**exponent


@dataclass(frozen=True)
class Score:
    """Ergebnis einer Bewertung, mit offengelegten Einzelteilen."""

    total: float
    gate: float
    components: dict[str, float] = field(default_factory=dict)

    def with_rarity(self, rarity: float, exponent: float) -> float:
        """Interessantheit dieses Ergebnisses - siehe interest_score()."""
        return interest_score(self.total, rarity, exponent)


def _ramp(value: float, low: float, high: float) -> float:
    """Weicher Uebergang von 0 auf 1 zwischen low und high.

    Harte Schwellen erzeugen Sprünge in der Rangfolge, die niemand
    nachvollziehen kann - deshalb ueberall Rampen statt if-Kanten.
    """
    if high == low:
        return 1.0 if value >= high else 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _field_strength_proxy(power_kw: float, distance_km: float, w: Weights) -> float:
    """Ersatzwert fuer die ankommende Feldstaerke, normiert auf 0 bis 1.

    Bodenwelle und Raumwelle fallen unterschiedlich ab; wir nehmen einen
    gemeinsamen Exponenten und eine logarithmische Stauchung, weil
    Leistung in Dezibel wahrgenommen wird, nicht linear.

    Die Untergrenze wird auf die ENTFERNUNG gelegt, nicht auf das
    Verhaeltnis: eine Klammer um das Verhaeltnis (max(1.0, ...)) haette
    jede Strecke unterhalb der Referenzentfernung auf denselben Wert
    eingeebnet - und genau dort liegen die meisten Stationen, die hier
    ueberhaupt interessant sind. Flevoland (180 km) und ein Sender in
    500 km waeren ununterscheidbar gewesen.
    """
    exponent = w.get("common", "distance_exponent")
    reference = w.get("common", "reference_km")
    near_field = w.get("common", "near_field_km")
    effective_km = max(near_field, distance_km)
    ratio = power_kw / (effective_km / reference) ** exponent
    scale = w.get("common", "field_log_scale")
    return max(0.0, min(1.0, math.log10(1.0 + ratio) / scale))


def _seasonal_noise_factor(when: datetime, w: Weights) -> float:
    """Sommerliches Gewitterrauschen auf MW/LW.

    Im Juli macht das statische Rauschen mehr kaputt als jede Stoerung
    der Ionosphaere - im Januar ist der Effekt fast weg.
    """
    depth = w.get("mw_lw", "summer_noise_depth")
    day = when.timetuple().tm_yday
    # Maximum um den 15. Juli (Tag 196)
    seasonal = 0.5 * (1.0 + math.cos(2.0 * math.pi * (day - 196) / 365.25))
    return 1.0 - depth * seasonal


def _geomagnetic_penalty(path_mid: Point, kp: int, w: Weights) -> float:
    """Abschlag durch geomagnetische Stoerung, breitenabhaengig.

    Wege ueber den Norden brechen zuerst weg. Als Naeherung nehmen wir
    die geografische Breite des Streckenmittelpunkts - die geomagnetische
    Breite waere sauberer und ist ein Kandidat fuer spaeter.
    """
    lat = abs(path_mid.lat)
    lat_start = w.get("common", "kp_latitude_start")
    lat_full = w.get("common", "kp_latitude_full")
    exposure = _ramp(lat, lat_start, lat_full)
    per_kp = w.get("common", "kp_penalty_per_step")
    return max(0.0, 1.0 - exposure * per_kp * kp)


def score_mw_lw(link: Link, when: datetime, kp: int, w: Weights) -> Score:
    """Bewertung fuer Mittel- und Langwelle.

    Der Dunkelanteil dominiert alles: erst wenn die D-Schicht ueber der
    gesamten Strecke abgebaut ist, traegt die Raumwelle.
    """
    dark = darkness_fraction(link.rx, link.tx, when)
    dark_low = w.get("mw_lw", "darkness_gate_low")
    dark_high = w.get("mw_lw", "darkness_gate_high")

    # Kurze Strecken laufen ueber die Bodenwelle und brauchen keine Dunkelheit.
    groundwave_km = w.get("mw_lw", "groundwave_km")
    groundwave = 1.0 - _ramp(link.distance_km, groundwave_km * 0.5, groundwave_km)
    skywave_gate = _ramp(dark, dark_low, dark_high)
    gate = max(groundwave, skywave_gate)

    field = _field_strength_proxy(link.power_kw, link.distance_km, w)
    noise = _seasonal_noise_factor(when, w)
    geomag = _geomagnetic_penalty(midpoint(link.rx, link.tx), kp, w)

    quality = (
        w.get("mw_lw", "weight_field") * field
        + w.get("mw_lw", "weight_darkness") * dark
        + w.get("mw_lw", "weight_quiet") * geomag
    )
    total = gate * quality * noise

    return Score(
        total=round(100.0 * max(0.0, min(1.0, total)), 1),
        gate=round(gate, 3),
        components={
            "darkness": round(dark, 3),
            "field": round(field, 3),
            "geomagnetic": round(geomag, 3),
            "seasonal_noise": round(noise, 3),
        },
    )


def _hop_count(distance_km: float, w: Weights) -> int:
    max_hop = w.get("sw", "max_hop_km")
    return max(1, math.ceil(distance_km / max_hop))


def _secant_factor(distance_km: float, hops: int, w: Weights) -> float:
    """Verhaeltnis von MUF zur Grenzfrequenz, aus der Sprunggeometrie.

    Flach einfallende Wellen werden bei hoeheren Frequenzen reflektiert
    als steil einfallende - deshalb tragen weite Strecken hoehere Baender.
    """
    height = w.get("sw", "reflection_height_km")
    hop_km = distance_km / hops
    theta = hop_km / EARTH_RADIUS_KM / 2.0  # halbe Sprungweite im Erdmittelpunktswinkel
    numerator = EARTH_RADIUS_KM * math.sin(theta)
    denominator = EARTH_RADIUS_KM + height - EARTH_RADIUS_KM * math.cos(theta)
    incidence = math.atan2(numerator, denominator)
    return 1.0 / max(1e-6, math.cos(incidence))


def estimate_fof2_mhz(path_mid: Point, when: datetime, f107: float, w: Weights) -> float:
    """Grobe Grenzfrequenz der F2-Schicht ueber dem Streckenmittelpunkt.

    Bewusst simpel: Sonnenaktivitaet mal Sonnenstand, mit einem Nachtwert
    als Untergrenze. Sobald wir die Ionosonde Dourbes anbinden, ersetzt
    ein Messwert diese Schaetzung - die Schnittstelle bleibt dieselbe.
    """
    base = w.get("sw", "fof2_base_mhz")
    per_flux = w.get("sw", "fof2_per_sqrt_flux")
    day_value = base + per_flux * math.sqrt(max(0.0, f107))

    elevation = sun_elevation_deg(path_mid, when)
    # Sonnenhoehe 0 bis 60 Grad steuert den Tagesanteil
    daylight = _ramp(elevation, -10.0, 45.0)
    night_ratio = w.get("sw", "fof2_night_ratio")
    return day_value * (night_ratio + (1.0 - night_ratio) * daylight)


def estimate_luf_mhz(darkness: float, w: Weights) -> float:
    """Untere brauchbare Frequenz: was die D-Schicht wegabsorbiert."""
    day = w.get("sw", "luf_day_mhz")
    night = w.get("sw", "luf_night_mhz")
    return night + (day - night) * (1.0 - darkness)


def score_sw(
    link: Link, when: datetime, kp: int, w: Weights, *, f107: float
) -> Score:
    """Bewertung fuer Kurzwelle.

    Entscheidend ist, ob die Frequenz im Fenster zwischen LUF und MUF
    liegt. Darunter frisst die Absorption alles, darueber geht die Welle
    ins All.
    """
    mid = midpoint(link.rx, link.tx)
    dark = darkness_fraction(link.rx, link.tx, when)
    hops = _hop_count(link.distance_km, w)

    fof2 = estimate_fof2_mhz(mid, when, f107, w)
    geomag = _geomagnetic_penalty(mid, kp, w)
    muf = fof2 * _secant_factor(link.distance_km, hops, w) * geomag
    owf = muf * w.get("sw", "owf_ratio")  # optimale Arbeitsfrequenz, ~85 % der MUF
    luf = estimate_luf_mhz(dark, w)

    freq_mhz = link.freq_khz / 1000.0
    # Unten harte Absorptionskante, oben weicher Auslauf ueber die MUF hinaus
    below = _ramp(freq_mhz, luf * 0.85, luf * 1.15)
    above = 1.0 - _ramp(freq_mhz, owf, muf * w.get("sw", "muf_tail_ratio"))
    gate = below * above

    # Tote Zone: zu nahe Sender sind ueber die Raumwelle nicht erreichbar
    skip_km = w.get("sw", "skip_zone_km")
    skip = _ramp(link.distance_km, skip_km * 0.5, skip_km)

    field = _field_strength_proxy(link.power_kw, link.distance_km, w)
    hop_loss = w.get("sw", "hop_loss_factor") ** (hops - 1)
    aiming = _aiming_factor(link, w)

    quality = (
        w.get("sw", "weight_field") * field
        + w.get("sw", "weight_window") * _window_comfort(freq_mhz, luf, owf, muf)
        + w.get("sw", "weight_aiming") * aiming
    )
    total = gate * skip * hop_loss * quality

    return Score(
        total=round(100.0 * max(0.0, min(1.0, total)), 1),
        gate=round(gate, 3),
        components={
            "muf_mhz": round(muf, 2),
            "owf_mhz": round(owf, 2),
            "luf_mhz": round(luf, 2),
            "hops": float(hops),
            "field": round(field, 3),
            "aiming": round(aiming, 3),
            "darkness": round(dark, 3),
            "geomagnetic": round(geomag, 3),
        },
    )


def _window_comfort(freq_mhz: float, luf: float, owf: float, muf: float) -> float:
    """Wie bequem liegt die Frequenz im Fenster? 1 = genau auf der OWF.

    Das Optimum liegt bei der optimalen Arbeitsfrequenz, nicht in der
    Mitte des Fensters: knapp unterhalb der MUF ist die Absorption am
    geringsten. Oberhalb der OWF faellt der Wert ab, wird aber erst an
    der MUF null - sonst entstuende dort eine Kante, die es physikalisch
    nicht gibt.
    """
    if freq_mhz <= luf or freq_mhz >= muf or owf <= luf or muf <= owf:
        return 0.0
    if freq_mhz <= owf:
        position = (freq_mhz - luf) / (owf - luf)
    else:
        position = (muf - freq_mhz) / (muf - owf)
    return math.sin(0.5 * math.pi * max(0.0, min(1.0, position)))


def _aiming_factor(link: Link, w: Weights) -> float:
    """Abschlag, wenn die Sendeantenne woanders hin strahlt.

    Ein Sender, dessen Keule nach Zentralafrika zeigt, kommt bei Dir nur
    ueber die Nebenkeule an. EiBi liefert das Zielgebiet mit.
    """
    if link.target_bearing_deg is None:
        return 1.0  # ungerichtet oder unbekannt
    from .geometry import initial_bearing_deg

    to_rx = initial_bearing_deg(link.tx, link.rx)
    delta = abs((to_rx - link.target_bearing_deg + 180.0) % 360.0 - 180.0)
    sidelobe = w.get("sw", "sidelobe_floor")
    beamwidth = w.get("sw", "beamwidth_deg")
    return sidelobe + (1.0 - sidelobe) * (1.0 - _ramp(delta, beamwidth / 2.0, 180.0))


def score(
    link: Link, when: datetime, kp: int, w: Weights, *, f107: float = 120.0
) -> Score:
    """Bewertung passend zur Bandklasse."""
    if link.band_class == "sw":
        return score_sw(link, when, kp, w, f107=f107)
    return score_mw_lw(link, when, kp, w)


def best_slot_by_kp(
    link: Link,
    slots: list[datetime],
    w: Weights,
    *,
    f107: float = 120.0,
    kp_range: range = range(0, 10),
) -> list[dict[str, Any]]:
    """Fuer jede Kp-Stufe das beste Zeitfenster des Abends.

    Das ist genau das, was ins Bulletin geschrieben wird: nicht das volle
    Raster, sondern je Kp-Stufe der Gewinner. Das volle Raster wandert
    ins Archiv, damit ein spaeterer Wechsel der Darstellung nichts kostet.
    """
    results: list[dict[str, Any]] = []
    for kp in kp_range:
        scored = [(slot, score(link, slot, kp, w, f107=f107)) for slot in slots]
        slot, best = max(scored, key=lambda pair: pair[1].total)
        results.append(
            {
                "kp": kp,
                "t": slot.strftime("%H:%M"),
                "score": best.total,
                "gate": best.gate,
                "components": best.components,
            }
        )
    return results
