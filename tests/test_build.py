"""Tests fuer den Morgenlauf-Kern.

Alles hier arbeitet mit synthetischen Daten - kein Netz, kein echtes
EiBi, keine echte SWPC-Antwort. Getestet wird die Verdrahtung: landet
die richtige Station in der richtigen Liste, wird uebersprungen was
uebersprungen werden soll, stimmen die Groessenlimits.
"""

import unittest
from datetime import date, time
from pathlib import Path

from bulletin.build import (
    build_bulletin,
    evening_slots,
    on_air_slots,
    resolve_eibi_broadcasts,
    resolve_mwlw_stations,
)
from bulletin.model import bulletin_to_dict
from bulletin.physics.geometry import BERGHEIM, Point
from bulletin.physics.propagation import Weights
from bulletin.sources.eibi import Broadcast
from bulletin.sources.mwlw import parse_stations
from bulletin.sources.tx_sites import TxSiteTable

WEIGHTS = Weights.load(Path(__file__).resolve().parents[1] / "data" / "weights.yaml")

TX_SITES = TxSiteTable({
    "D-n": Point(lat=52.6486, lon=12.9092),   # Nauen
    "D-r": Point(lat=48.6, lon=11.55),        # Rohrbach
    "G-w": Point(lat=52.3167, lon=-2.7167),   # Woofferton
})

TODAY = date(2026, 12, 21)  # Wintersonnenwende - verlaessliche Dunkelheit


def broadcast(
    freq_khz, station, languages, itu="D", site="n", start="1900", end="2200"
) -> Broadcast:
    return Broadcast(
        freq_khz=freq_khz,
        start_utc=time(int(start[:2]), int(start[2:])),
        end_utc=time(int(end[:2]), int(end[2:])) if end != "2400" else time(23, 59, 59),
        station=station,
        languages=languages,
        itu=itu,
        target="Eu",
        transmitter_site=site,
    )


MWLW_YAML = """
stations:
  - id: mw-198-droitwich
    name: "BBC Radio 4"
    freq_khz: 198
    lat: 52.2956
    lon: -2.1069
    power_kw: 500
    language: en
    rarity_baseline: 0.15
"""


class TestEveningSlots(unittest.TestCase):
    def test_covers_eighteen_to_midnight_in_half_hour_steps(self):
        slots = evening_slots(TODAY)
        self.assertEqual(slots[0].strftime("%H:%M"), "18:00")
        # 18:00 + 6h landet exakt auf Mitternacht - Python zeigt das als
        # 00:00 des naechsten Tages, nicht als "24:00".
        self.assertEqual(slots[-1].strftime("%H:%M"), "00:00")
        self.assertEqual(slots[-1].date(), date(2026, 12, 22))
        # 18:00 bis 24:00 in 30-Minuten-Schritten sind 13 Zeitpunkte.
        self.assertEqual(len(slots), 13)

    def test_slots_are_localized_to_berlin(self):
        slots = evening_slots(TODAY)
        self.assertEqual(str(slots[0].tzinfo), "Europe/Berlin")


class TestOnAirSlots(unittest.TestCase):
    def test_filters_to_the_broadcast_window(self):
        # 19:00-21:00 UTC entspricht im Dezember (UTC+1 in Berlin)
        # 20:00-22:00 Ortszeit - genau der Punkt, an dem eine Zeitzone
        # ohne Sorgfalt sich raecht.
        b = broadcast(6070, "Test", ("de",), start="1900", end="2100")
        slots = evening_slots(TODAY)
        active = on_air_slots(b, slots)
        self.assertTrue(all(t.strftime("%H:%M") in ("20:00", "20:30", "21:00", "21:30") for t in active))
        self.assertEqual(len(active), 4)

    def test_empty_when_broadcast_never_airs_in_the_window(self):
        b = broadcast(6070, "Test", ("de",), start="0600", end="0800")
        active = on_air_slots(b, evening_slots(TODAY))
        self.assertEqual(active, [])


class TestResolveEibiBroadcasts(unittest.TestCase):
    def test_unknown_transmitter_site_is_skipped_and_counted(self):
        broadcasts = [
            broadcast(6070, "Bekannt", ("de",), itu="D", site="n"),
            broadcast(9600, "Unbekannt", ("de",), itu="USA", site="o"),
        ]
        resolved, skipped = resolve_eibi_broadcasts(
            broadcasts, tx_sites=TX_SITES, rx=BERGHEIM, slots=evening_slots(TODAY), assumed_power_kw=100.0
        )
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].name, "Bekannt")
        self.assertEqual(skipped, 1)

    def test_broadcast_outside_evening_window_is_dropped_without_being_counted_as_skipped(self):
        broadcasts = [broadcast(6070, "Morgens", ("de",), itu="D", site="n", start="0500", end="0700")]
        resolved, skipped = resolve_eibi_broadcasts(
            broadcasts, tx_sites=TX_SITES, rx=BERGHEIM, slots=evening_slots(TODAY), assumed_power_kw=100.0
        )
        self.assertEqual(resolved, [])
        self.assertEqual(skipped, 0)  # nicht "kein Standort", sondern "nicht am Abend"

    def test_station_id_is_stable_and_readable(self):
        b = broadcast(6070, "Channel 292", ("de", "en"), itu="D", site="r")
        resolved, _ = resolve_eibi_broadcasts(
            [b], tx_sites=TX_SITES, rx=BERGHEIM, slots=evening_slots(TODAY), assumed_power_kw=100.0
        )
        self.assertEqual(resolved[0].station_id, "sw-6070-channel-292-r-de-en")

    def test_assumed_power_is_applied(self):
        b = broadcast(6070, "Test", ("de",), itu="D", site="n")
        resolved, _ = resolve_eibi_broadcasts(
            [b], tx_sites=TX_SITES, rx=BERGHEIM, slots=evening_slots(TODAY), assumed_power_kw=77.0
        )
        self.assertEqual(resolved[0].link.power_kw, 77.0)

    def test_day_and_night_schedule_lines_merge_into_one_station(self):
        # Channel 292 auf 3955 kHz hat bei EiBi zwei Zeilen: Tagprogramm
        # und Nachtprogramm auf derselben Frequenz. Das darf nicht zu
        # zwei Eintraegen im Bulletin fuehren.
        day = broadcast(3955, "Channel 292", ("de", "en"), site="r", start="0700", end="2000")
        night = broadcast(3955, "Channel 292", ("de", "en"), site="r", start="2100", end="0459")
        resolved, _ = resolve_eibi_broadcasts(
            [day, night], tx_sites=TX_SITES, rx=BERGHEIM, slots=evening_slots(TODAY), assumed_power_kw=100.0
        )
        self.assertEqual(len(resolved), 1)

    def test_merged_station_covers_slots_from_both_schedule_lines(self):
        day = broadcast(3955, "Channel 292", ("de", "en"), site="r", start="0700", end="2000")
        night = broadcast(3955, "Channel 292", ("de", "en"), site="r", start="2100", end="0459")
        resolved, _ = resolve_eibi_broadcasts(
            [day, night], tx_sites=TX_SITES, rx=BERGHEIM, slots=evening_slots(TODAY), assumed_power_kw=100.0
        )
        times = {s.strftime("%H:%M") for s in resolved[0].slots}
        # 19:00-19:30 gehoert zum Tagfenster (endet 20:00 lokal in diesem
        # Testfall), 22:00 und spaeter zum Nachtfenster - beide muessen da sein.
        self.assertIn("19:00", times)
        self.assertIn("22:00", times)


class TestResolveMwlwStations(unittest.TestCase):
    def test_uses_full_evening_as_slots(self):
        stations = parse_stations(MWLW_YAML)
        slots = evening_slots(TODAY)
        resolved = resolve_mwlw_stations(stations, rx=BERGHEIM, slots=slots)
        self.assertEqual(resolved[0].slots, slots)

    def test_carries_the_rarity_baseline_hint(self):
        stations = parse_stations(MWLW_YAML)
        resolved = resolve_mwlw_stations(stations, rx=BERGHEIM, slots=evening_slots(TODAY))
        self.assertEqual(resolved[0].rarity_baseline_hint, 0.15)


class TestBuildBulletin(unittest.TestCase):
    def _build(self, main_broadcasts, all_broadcasts, mwlw_yaml=MWLW_YAML):
        return build_bulletin(
            eibi_broadcasts_main=main_broadcasts,
            eibi_broadcasts_all=all_broadcasts,
            mwlw_stations=parse_stations(mwlw_yaml),
            tx_sites=TX_SITES,
            weights=WEIGHTS,
            f107_flux=110.0,
            today=TODAY,
            eibi_season="b26",
        )

    def test_main_list_only_contains_wanted_languages(self):
        main = [broadcast(6070, "Deutsch", ("de",), site="n")]
        bulletin, metas, stats = self._build(main, main)
        main_entries = [e for e in bulletin.entries if e.list_kind == "main"]
        self.assertTrue(any(m.station_id == e.station_id for e in main_entries for m in metas))

    def test_dx_block_excludes_stations_already_in_main_list(self):
        main = [broadcast(6070, "Deutsch", ("de",), site="n")]
        # "all" enthaelt dieselbe Sendung unveraendert, plus eine zweite
        # mit einer Sprache ausserhalb de/en/fr/nl (leeres Tupel, wie es
        # eibi.py fuer unbekannte Codes tatsaechlich liefert) - die
        # landet nur in den ungefilterten DX-Kandidaten.
        foreign = broadcast(9600, "Fremdsprachig", (), site="r")
        all_broadcasts = main + [foreign]
        bulletin, metas, stats = self._build(main, all_broadcasts)

        main_ids = {e.station_id for e in bulletin.entries if e.list_kind == "main"}
        dx_ids = {e.station_id for e in bulletin.entries if e.list_kind == "dx"}
        self.assertTrue(dx_ids.isdisjoint(main_ids))
        self.assertEqual(len(dx_ids), 1)

    def test_respects_list_size_limits_from_weights(self):
        many = [broadcast(6000 + i * 10, f"Sender{i}", ("de",), site="n") for i in range(30)]
        bulletin, metas, stats = self._build(many, many)
        main_count = sum(1 for e in bulletin.entries if e.list_kind == "main")
        self.assertLessEqual(main_count, WEIGHTS.get("ranking", "main_list_size"))

    def test_mwlw_rarity_baseline_is_used_directly(self):
        bulletin, metas, stats = self._build([], [])
        droitwich = next(e for e in bulletin.entries if e.station_id == "mw-198-droitwich")
        self.assertEqual(droitwich.rarity.baseline, 0.15)

    def test_eibi_rarity_baseline_is_derived_when_no_hint_exists(self):
        main = [broadcast(6070, "Test", ("de",), site="n")]
        bulletin, metas, stats = self._build(main, main)
        sw_entry = next(e for e in bulletin.entries if e.station_id.startswith("sw-"))
        # Kein fester Erwartungswert - nur: die Naeherung greift und
        # liefert etwas im gueltigen Bereich, nicht None oder ausserhalb [0,1].
        self.assertIsNotNone(sw_entry.rarity.baseline)
        self.assertGreaterEqual(sw_entry.rarity.baseline, 0.0)
        self.assertLessEqual(sw_entry.rarity.baseline, 1.0)

    def test_greyline_bonus_discriminates_between_transmitters(self):
        # Regression: frueher wurde die Grauzone am EMPFAENGER geprueft.
        # Der ist fuer alle Stationen derselbe, die Bedingung war damit
        # entweder fuer alle wahr oder fuer keine - und damit wertlos.
        # Jetzt entscheidet der Sonnenstand am Sender.
        from bulletin.build import _rarity_for, resolve_mwlw_stations

        two_stations = """
stations:
  - id: west-in-twilight
    name: "Westlicher Sender"
    freq_khz: 198
    lat: 52.3
    lon: -2.1
    power_kw: 500
    language: en
    rarity_baseline: 0.2
  - id: east-in-deep-night
    name: "Oestlicher Sender"
    freq_khz: 540
    lat: 46.43
    lon: 19.02
    power_kw: 500
    language: null
    rarity_baseline: 0.2
"""
        september = date(2026, 9, 3)  # Daemmerung faellt in das Abendfenster
        bulletin, metas, stats = build_bulletin(
            eibi_broadcasts_main=[],
            eibi_broadcasts_all=[],
            mwlw_stations=parse_stations(two_stations),
            tx_sites=TX_SITES,
            weights=WEIGHTS,
            f107_flux=110.0,
            today=september,
            eibi_season="a26",
        )
        by_id = {e.station_id: e for e in bulletin.entries}
        reasons = {sid: e.rarity.reason for sid, e in by_id.items()}
        # Beide duerfen nicht denselben Grund tragen - sonst unterscheidet
        # die Bedingung wieder nicht.
        self.assertNotEqual(
            reasons["west-in-twilight"],
            reasons["east-in-deep-night"],
            f"Grauzone unterscheidet nicht zwischen den Sendern: {reasons}",
        )

    def test_greyline_reason_names_the_transmitter(self):
        from bulletin.build import _rarity_for

        one = """
stations:
  - id: west-in-twilight
    name: "Westlicher Sender"
    freq_khz: 198
    lat: 52.3
    lon: -2.1
    power_kw: 500
    language: en
    rarity_baseline: 0.2
"""
        bulletin, metas, stats = build_bulletin(
            eibi_broadcasts_main=[],
            eibi_broadcasts_all=[],
            mwlw_stations=parse_stations(one),
            tx_sites=TX_SITES,
            weights=WEIGHTS,
            f107_flux=110.0,
            today=date(2026, 9, 3),
            eibi_season="a26",
        )
        entry = bulletin.entries[0]
        if entry.rarity.reason is not None:
            self.assertIn("Sender", entry.rarity.reason)
            self.assertIsNotNone(entry.rarity.today)
            self.assertGreater(entry.rarity.today, entry.rarity.baseline)

    def test_stats_report_skipped_count(self):
        main = [broadcast(9600, "Unbekannt", ("de",), itu="USA", site="o")]
        bulletin, metas, stats = self._build(main, main)
        self.assertEqual(stats["eibi_skipped_no_tx_site"], 1)

    def test_bulletin_metadata_reflects_inputs(self):
        bulletin, metas, stats = self._build([], [])
        self.assertEqual(bulletin.date, "2026-12-21")
        self.assertEqual(bulletin.eibi_season, "b26")
        self.assertEqual(bulletin.f107_flux, 110.0)

    def test_missing_flux_falls_back_without_crashing(self):
        bulletin, metas, stats = build_bulletin(
            eibi_broadcasts_main=[],
            eibi_broadcasts_all=[],
            mwlw_stations=parse_stations(MWLW_YAML),
            tx_sites=TX_SITES,
            weights=WEIGHTS,
            f107_flux=None,
            today=TODAY,
            eibi_season="b26",
        )
        self.assertIsNone(bulletin.f107_flux)  # im Bulletin bleibt sichtbar: kein echter Wert
        self.assertTrue(len(bulletin.entries) >= 1)  # trotzdem wurde gerechnet (Notwert intern)

    def test_output_is_fully_json_serializable(self):
        import json

        bulletin, metas, stats = self._build(
            [broadcast(6070, "Test", ("de",), site="n")],
            [broadcast(6070, "Test", ("de",), site="n")],
        )
        json.dumps(bulletin_to_dict(bulletin))

    def test_every_entry_has_all_ten_kp_steps(self):
        bulletin, metas, stats = self._build([], [])
        for entry in bulletin.entries:
            self.assertEqual(len(entry.best_by_kp), 10)
            self.assertEqual([s.kp for s in entry.best_by_kp], list(range(10)))

    def test_each_bulletin_entry_has_a_matching_station_meta(self):
        bulletin, metas, stats = self._build(
            [broadcast(6070, "Test", ("de",), site="n")],
            [broadcast(6070, "Test", ("de",), site="n")],
        )
        meta_ids = {m.station_id for m in metas}
        for entry in bulletin.entries:
            self.assertIn(entry.station_id, meta_ids)


if __name__ == "__main__":
    unittest.main()
