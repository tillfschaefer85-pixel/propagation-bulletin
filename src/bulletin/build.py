"""Morgenlauf: fasst die Quellen zusammen, bewertet, schreibt die Ausgabedateien.

Wie im ganzen Projekt gilt: ein reiner Kern (build_bulletin), der nur
mit bereits geladenen Werten arbeitet und ohne Netz testbar ist, umgeben
von einer duennen IO-Schale (run), die tatsaechlich Quellen abruft und
Dateien schreibt. Wer das hier liest, um zu verstehen, WIE bewertet
wird, ist im falschen Modul - das steht in physics/propagation.py.
Hier steht nur, WELCHE Stationen ueberhaupt in die Bewertung kommen und
wie die Ergebnisse zu bulletin.json werden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .model import (
    SCHEMA_VERSION,
    Bulletin,
    BulletinEntry,
    BestSlot,
    Rarity,
    StationMeta,
    write_bulletin,
    write_stations,
)
from .physics.geometry import (
    BERGHEIM,
    Point,
    great_circle_distance_km,
    initial_bearing_deg,
    loop_null_bearing_deg,
)
from .physics.propagation import Link, Weights, best_slot_by_kp, interest_score
from .physics.solar import is_greyline
from .sources.bands import NO_FILTER, BandPlan, load_band_plan
from .sources.eibi import Broadcast, WANTED_LANGUAGES, days_until_season_change, load_schedule, season_code
from .sources.mwlw import Station as MwlwStation
from .sources.mwlw import load_stations as load_mwlw_stations
from .sources.mwlw import to_link as mwlw_to_link
from .sources.swpc import fetch_flux, smoothed_flux
from .sources.tx_sites import TxSiteTable, load_tx_sites

BERLIN = ZoneInfo("Europe/Berlin")
EVENING_START_HOUR = 18
EVENING_END_HOUR = 24
SLOT_MINUTES = 30
INTEREST_REFERENCE_KP = 2  # Referenzstufe fuer die Rangfolge - siehe unten
FALLBACK_FLUX = 120.0  # mittlere Sonnenaktivitaet, greift nur wenn SWPC ausfaellt


def evening_slots(local_date: date) -> list[datetime]:
    """Halbstundenraster von 18 bis 24 Uhr Ortszeit fuer einen Abend."""
    start = datetime(local_date.year, local_date.month, local_date.day, EVENING_START_HOUR, tzinfo=BERLIN)
    steps = (EVENING_END_HOUR - EVENING_START_HOUR) * 60 // SLOT_MINUTES
    return [start + timedelta(minutes=SLOT_MINUTES * i) for i in range(steps + 1)]


def on_air_slots(broadcast: Broadcast, slots: list[datetime]) -> list[datetime]:
    """Nur die Zeitfenster, in denen die Sendung laut Fahrplan tatsaechlich laeuft."""
    return [s for s in slots if broadcast.is_on_air(s)]


@dataclass(frozen=True)
class ResolvedStation:
    """Eine bewertbare Station: Link plus das, was spaeter in stations.json landet."""

    station_id: str
    name: str
    band_class: str
    freq_khz: float
    language: str | None
    link: Link
    source: str
    rarity_baseline_hint: float | None  # nur bei mwlw bekannt
    slots: list[datetime]
    site_name: str | None = None


def _slug(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in text).strip("-")


def _station_id_for_eibi(b: Broadcast) -> str:
    """Frequenz, Sender, Standort und Sprache ergeben eine stabile, lesbare ID.

    Nicht formal garantiert eindeutig, aber in der Praxis: zwei Sendungen
    derselben Station auf derselben Frequenz vom selben Standort in
    derselben Sprache waeren ohnehin ununterscheidbar.
    """
    lang = "-".join(b.languages) if b.languages else "xx"
    site = b.transmitter_site or "site"
    return f"sw-{int(b.freq_khz)}-{_slug(b.station)}-{site}-{lang}"


def resolve_eibi_broadcasts(
    broadcasts: list[Broadcast],
    *,
    tx_sites: TxSiteTable,
    rx: Point,
    slots: list[datetime],
    assumed_power_kw: float,
) -> tuple[list[ResolvedStation], int]:
    """Loest EiBi-Sendungen in bewertbare Stationen auf.

    Der zweite Rueckgabewert ist die Zahl der Eintraege, die mangels
    Sendestandort in tx_sites.yaml uebersprungen wurden - das Mass dafuer,
    wie viel die Tabelle noch nicht abdeckt.

    Ein und derselbe physische Sender taucht bei EiBi oft als mehrere
    Zeilen auf - typischerweise ein Tag- und ein Nachtprogramm auf
    derselben Frequenz, manchmal auch mehr Sprachwechsel als Zeilen.
    Solche Zeilen ergeben dieselbe station_id (gleiche Frequenz, gleicher
    Sender, gleicher Standort, gleiche erkannte Sprache) und werden hier
    zu einer einzigen Station mit der Vereinigung ihrer On-Air-Fenster
    zusammengefuehrt - sonst stuende derselbe Sender zweimal im Bulletin.
    """
    active_by_id: dict[str, list[datetime]] = {}
    meta_by_id: dict[str, tuple[Broadcast, "Link", str | None]] = {}
    skipped = 0

    for b in broadcasts:
        site = tx_sites.lookup_site(b.itu, b.transmitter_site)
        if site is None:
            skipped += 1
            continue
        tx = site.point
        active = on_air_slots(b, slots)
        if not active:
            continue

        link = Link(
            station_id=_station_id_for_eibi(b),
            freq_khz=b.freq_khz,
            tx=tx,
            rx=rx,
            power_kw=assumed_power_kw,
        )
        existing = active_by_id.get(link.station_id, [])
        # Reihenfolge und Duplikate spielen fuer die spaetere Bewertung
        # keine Rolle - best_slot_by_kp geht ohnehin jeden Slot einzeln durch.
        active_by_id[link.station_id] = existing + [s for s in active if s not in existing]
        meta_by_id[link.station_id] = (b, link, site.name)

    resolved = [
        ResolvedStation(
            station_id=station_id,
            name=b.station,
            band_class=link.band_class,
            freq_khz=b.freq_khz,
            language=b.languages[0] if b.languages else None,
            link=link,
            source="eibi",
            rarity_baseline_hint=None,
            slots=sorted(active_by_id[station_id]),
            site_name=site_name,
        )
        for station_id, (b, link, site_name) in meta_by_id.items()
    ]
    return resolved, skipped


def resolve_mwlw_stations(
    stations: list[MwlwStation], *, rx: Point, slots: list[datetime]
) -> list[ResolvedStation]:
    """Wandelt die handgepflegte Liste in dieselbe ResolvedStation-Form.

    Ohne bekannte Sendezeiten wird angenommen, dass die Station im ganzen
    Abendfenster laeuft - fuer die grossen Dauersender auf MW/LW eine
    vernuenftige Annahme, fuer alles andere ein Punkt, den Du beim
    Eintragen im Blick behalten solltest.
    """
    resolved = []
    for s in stations:
        link = mwlw_to_link(s, rx)
        resolved.append(
            ResolvedStation(
                station_id=s.station_id,
                name=s.name,
                band_class=link.band_class,
                freq_khz=s.freq_khz,
                language=s.language,
                link=link,
                source="mwlw",
                rarity_baseline_hint=s.rarity_baseline,
                slots=slots,
            )
        )
    return resolved


def _rarity_for(rs: ResolvedStation, best_by_kp: list[dict]) -> Rarity:
    """Baseline aus der Handliste uebernehmen oder aus der Feldstaerke schaetzen.

    EiBi liefert keine Seltenheit. Die Naeherung - schwaches Signal gilt
    als seltener - ist grob, aber besser als gar keine Sortierung, und
    wird ersetzt, sobald das Empfangslogbuch existiert.
    """
    reference = best_by_kp[INTEREST_REFERENCE_KP]
    if rs.rarity_baseline_hint is not None:
        baseline = rs.rarity_baseline_hint
    else:
        field = reference["components"].get("field", 0.5)
        baseline = max(0.0, min(1.0, 1.0 - field))

    today = None
    reason = None
    if rs.band_class in ("mw", "lw"):
        # Geprueft wird der SENDER, nicht der Empfaenger. Der Empfangsort
        # ist fuer alle Stationen derselbe - eine Bedingung, die nur ihn
        # betrachtet, ist entweder fuer alle wahr oder fuer keine und
        # taugt damit nicht zur Unterscheidung. Interessant ist, ob der
        # Sender in der Daemmerungszone steht: dort ist die D-Schicht
        # schon abgebaut, die F-Schicht aber noch ionisiert.
        #
        # Geprueft wird ausserdem der GANZE Abend, nicht nur das am besten
        # bewertete Zeitfenster. Das beste Fenster ist fast immer das
        # dunkelste - und Grauzone heisst gerade "noch nicht dunkel".
        # Eine Pruefung nur dort waere nie wahr geworden.
        greyline_slot = next(
            (slot for slot in rs.slots if is_greyline(rs.link.tx, slot)), None
        )
        if greyline_slot is not None:
            today = min(1.0, baseline + 0.3)
            reason = f"Grauzone am Sender gegen {greyline_slot.strftime('%H:%M')}"

    return Rarity(baseline=round(baseline, 3), today=today, reason=reason)


def _station_meta(rs: ResolvedStation) -> StationMeta:
    distance = great_circle_distance_km(rs.link.rx, rs.link.tx)
    bearing = initial_bearing_deg(rs.link.rx, rs.link.tx)

    null_bearings = None
    hints: tuple[str, ...] = ()
    if rs.band_class in ("mw", "lw"):
        null_bearings = loop_null_bearing_deg(bearing)
        hints = (
            f"Loop auf {bearing:.0f} Grad, Stoerer bei "
            f"{null_bearings[0]:.0f} oder {null_bearings[1]:.0f} Grad ausnullbar",
        )

    return StationMeta(
        station_id=rs.station_id,
        name=rs.name,
        site_name=rs.site_name,
        band_class=rs.band_class,
        freq_khz=rs.freq_khz,
        language=rs.language,
        distance_km=round(distance, 1),
        bearing_deg=round(bearing, 1),
        power_kw=rs.link.power_kw,
        source=rs.source,
        null_bearings_deg=null_bearings,
        hints=hints,
    )


def build_bulletin(
    *,
    eibi_broadcasts_main: list[Broadcast],
    eibi_broadcasts_all: list[Broadcast],
    mwlw_stations: list[MwlwStation],
    tx_sites: TxSiteTable,
    weights: Weights,
    f107_flux: float | None,
    today: date,
    eibi_season: str,
    rx: Point = BERGHEIM,
    band_plan: BandPlan = NO_FILTER,
) -> tuple[Bulletin, list[StationMeta], dict]:
    """Reiner Kern: aus geladenen Rohdaten wird das Tagesbulletin.

    eibi_broadcasts_main ist bereits auf de/en/fr/nl gefiltert (fuer die
    Hauptliste), eibi_broadcasts_all ist ungefiltert (fuer den DX-Block -
    "was ist heute Abend ungewoehnlich", unabhaengig von der Sprache).

    Der dritte Rueckgabewert ist ein kleines Statistik-Dict fuers Logging
    im IO-Teil (u.a. wie viele EiBi-Eintraege mangels Sendestandort
    uebersprungen wurden) - es landet nicht in den Ausgabedateien.
    """
    slots = evening_slots(today)
    f107 = f107_flux if f107_flux is not None else FALLBACK_FLUX
    power = weights.get("sw", "assumed_power_kw")

    # Nicht-Rundfunk aussortieren, bevor irgendetwas bewertet wird.
    # Flugfunk wie "Shannon Aeradio" ist als englischsprachig gefuehrt und
    # kaeme sonst durch den Sprachfilter direkt in die Hauptliste.
    before = len(eibi_broadcasts_main) + len(eibi_broadcasts_all)
    eibi_broadcasts_main = [b for b in eibi_broadcasts_main if band_plan.is_broadcast(b.freq_khz)]
    eibi_broadcasts_all = [b for b in eibi_broadcasts_all if band_plan.is_broadcast(b.freq_khz)]
    dropped_non_broadcast = before - len(eibi_broadcasts_main) - len(eibi_broadcasts_all)

    main_from_eibi, skipped = resolve_eibi_broadcasts(
        eibi_broadcasts_main, tx_sites=tx_sites, rx=rx, slots=slots, assumed_power_kw=power
    )
    main_from_mwlw = resolve_mwlw_stations(mwlw_stations, rx=rx, slots=slots)
    main_candidates = main_from_eibi + main_from_mwlw

    all_from_eibi, _ = resolve_eibi_broadcasts(
        eibi_broadcasts_all, tx_sites=tx_sites, rx=rx, slots=slots, assumed_power_kw=power
    )
    main_ids = {c.station_id for c in main_candidates}
    dx_candidates = [c for c in all_from_eibi if c.station_id not in main_ids]

    exponent = weights.get("ranking", "rarity_exponent")
    main_size = weights.get("ranking", "main_list_size")
    dx_size = weights.get("ranking", "dx_block_size")

    entries: list[BulletinEntry] = []
    metas: list[StationMeta] = []

    def process(candidates: list[ResolvedStation], list_kind: str, limit: int) -> None:
        scored = []
        for rs in candidates:
            best_raw = best_slot_by_kp(rs.link, rs.slots, weights, f107=f107)
            rarity = _rarity_for(rs, best_raw)
            combined = rarity.combined()
            interest = interest_score(best_raw[INTEREST_REFERENCE_KP]["score"], combined, exponent)
            scored.append((rs, best_raw, rarity, interest))
        scored.sort(key=lambda t: t[3], reverse=True)
        for rs, best_raw, rarity, interest in scored[:limit]:
            entries.append(
                BulletinEntry(
                    station_id=rs.station_id,
                    list_kind=list_kind,
                    best_by_kp=tuple(BestSlot(**b) for b in best_raw),
                    rarity=rarity,
                    interest_rank_score=round(interest, 2),
                )
            )
            metas.append(_station_meta(rs))

    process(main_candidates, "main", main_size)
    process(dx_candidates, "dx", dx_size)

    bulletin = Bulletin(
        schema_version=SCHEMA_VERSION,
        date=today.isoformat(),
        generated_at=datetime.now(timezone.utc).isoformat(),
        eibi_season=eibi_season,
        days_until_season_change=days_until_season_change(today),
        f107_flux=f107_flux,
        entries=tuple(entries),
    )
    stats = {
        "eibi_dropped_non_broadcast": dropped_non_broadcast,
        "eibi_skipped_no_tx_site": skipped,
        "main_candidates": len(main_candidates),
        "dx_candidates": len(dx_candidates),
    }
    return bulletin, metas, stats


def run(*, docs_dir: Path, data_dir: Path, cache_dir: Path) -> dict:
    """Tatsaechlicher Morgenlauf: holt alle Quellen, schreibt die Dateien.

    Getrennt von build_bulletin(), damit der Kern ohne Netz testbar bleibt.
    Ein Fehlschlag beim Fluss-Abruf ist nicht fatal - build_bulletin faengt
    einen fehlenden Wert mit FALLBACK_FLUX auf; ein Fehlschlag bei EiBi oder
    den Gewichten dagegen soll den Lauf sichtbar abbrechen.
    """
    weights = Weights.load(data_dir / "weights.yaml")
    today = datetime.now(BERLIN).date()
    season = season_code(today)

    eibi_main, _ = load_schedule(today, cache_dir=cache_dir, languages=WANTED_LANGUAGES)
    eibi_all, _ = load_schedule(today, cache_dir=cache_dir, languages=None)
    mwlw_stations = load_mwlw_stations(data_dir / "stations_mw_lw.yaml")
    tx_sites = load_tx_sites(data_dir / "tx_sites.yaml")
    band_plan = load_band_plan(data_dir / "broadcast_bands.yaml")

    try:
        flux_samples, _ = fetch_flux(cache_dir=cache_dir)
        f107 = smoothed_flux(flux_samples)
    except Exception:
        f107 = None

    bulletin, metas, stats = build_bulletin(
        eibi_broadcasts_main=eibi_main,
        eibi_broadcasts_all=eibi_all,
        mwlw_stations=mwlw_stations,
        tx_sites=tx_sites,
        weights=weights,
        f107_flux=f107,
        today=today,
        eibi_season=season,
        band_plan=band_plan,
    )

    write_stations(metas, docs_dir=docs_dir)
    write_bulletin(bulletin, docs_dir=docs_dir, today=today)
    return stats


if __name__ == "__main__":
    import sys

    root = Path(__file__).resolve().parents[2]
    result = run(docs_dir=root / "docs", data_dir=root / "data", cache_dir=root / "data" / "cache")
    print(result, file=sys.stderr)
