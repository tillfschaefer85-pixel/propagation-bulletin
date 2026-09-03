"""Tests fuer die handgepflegte MW/LW-Liste.

Der Schwerpunkt liegt auf der Validierung: eine Liste, die Till von Hand
pflegt, wird irgendwann einen Tippfehler enthalten. Der Loader soll dann
klar scheitern, nicht mit einem stillschweigend falschen Wert weiterrechnen.
"""

import unittest
from pathlib import Path

from bulletin.sources.mwlw import Station, ValidationError, load_stations, parse_stations, to_link
from bulletin.physics.geometry import BERGHEIM

VALID_YAML = """
stations:
  - id: mw-198-droitwich
    name: "BBC Radio 4 (Droitwich)"
    freq_khz: 198
    lat: 52.2956
    lon: -2.1069
    power_kw: 500
    language: en
    rarity_baseline: 0.15
    notes: "Testfall"
  - id: mw-540-solt
    name: "Test Ungarn"
    freq_khz: 540
    lat: 46.4319
    lon: 19.0203
    power_kw: 2000
    language: null
    rarity_baseline: 0.05
"""


class TestParseStations(unittest.TestCase):
    def test_valid_file_parses_both_entries(self):
        stations = parse_stations(VALID_YAML)
        self.assertEqual(len(stations), 2)
        self.assertEqual(stations[0].station_id, "mw-198-droitwich")
        self.assertEqual(stations[0].language, "en")

    def test_missing_language_defaults_to_none(self):
        stations = parse_stations(VALID_YAML)
        self.assertIsNone(stations[1].language)

    def test_empty_file_returns_empty_list(self):
        self.assertEqual(parse_stations(""), [])
        self.assertEqual(parse_stations("stations: []"), [])

    def test_duplicate_id_is_rejected(self):
        duped = VALID_YAML + """
  - id: mw-198-droitwich
    name: "Zweiter Eintrag mit derselben ID"
    freq_khz: 999
    lat: 50.0
    lon: 6.0
    power_kw: 10
    rarity_baseline: 0.5
"""
        with self.assertRaises(ValidationError):
            parse_stations(duped)

    def test_missing_required_field_is_rejected(self):
        broken = """
stations:
  - id: incomplete
    name: "Fehlt was"
    freq_khz: 999
"""
        with self.assertRaises(ValidationError):
            parse_stations(broken)

    def test_frequency_outside_mw_lw_is_rejected(self):
        broken = """
stations:
  - id: too-high
    name: "Das ist Kurzwelle"
    freq_khz: 6070
    lat: 50.0
    lon: 6.0
    power_kw: 10
    rarity_baseline: 0.5
"""
        with self.assertRaises(ValidationError):
            parse_stations(broken)

    def test_rarity_out_of_range_is_rejected(self):
        broken = """
stations:
  - id: bad-rarity
    name: "X"
    freq_khz: 500
    lat: 50.0
    lon: 6.0
    power_kw: 10
    rarity_baseline: 1.5
"""
        with self.assertRaises(ValidationError):
            parse_stations(broken)

    def test_unknown_language_code_is_rejected(self):
        broken = """
stations:
  - id: bad-lang
    name: "X"
    freq_khz: 500
    lat: 50.0
    lon: 6.0
    power_kw: 10
    language: xx
    rarity_baseline: 0.5
"""
        with self.assertRaises(ValidationError):
            parse_stations(broken)

    def test_negative_power_is_rejected(self):
        broken = """
stations:
  - id: bad-power
    name: "X"
    freq_khz: 500
    lat: 50.0
    lon: 6.0
    power_kw: -10
    rarity_baseline: 0.5
"""
        with self.assertRaises(ValidationError):
            parse_stations(broken)

    def test_invalid_latitude_is_rejected(self):
        # Point() aus geometry.py prueft den Wertebereich - der Fehler
        # muss durchgereicht werden, nicht verschluckt.
        broken = """
stations:
  - id: bad-lat
    name: "X"
    freq_khz: 500
    lat: 200.0
    lon: 6.0
    power_kw: 10
    rarity_baseline: 0.5
"""
        with self.assertRaises((ValidationError, ValueError)):
            parse_stations(broken)


class TestLoadFromDisk(unittest.TestCase):
    def test_shipped_template_loads_and_validates(self):
        path = Path(__file__).resolve().parents[1] / "data" / "stations_mw_lw.yaml"
        stations = load_stations(path)
        self.assertGreaterEqual(len(stations), 1)
        ids = {s.station_id for s in stations}
        self.assertIn("mw-198-droitwich", ids)


class TestToLink(unittest.TestCase):
    def test_produces_a_link_usable_by_propagation(self):
        station = parse_stations(VALID_YAML)[0]
        link = to_link(station, BERGHEIM)
        self.assertEqual(link.station_id, "mw-198-droitwich")
        self.assertEqual(link.freq_khz, 198.0)
        self.assertEqual(link.rx, BERGHEIM)
        self.assertEqual(link.band_class, "lw")


if __name__ == "__main__":
    unittest.main()
