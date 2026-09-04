"""Tests fuer den Rundfunkband-Filter.

Der Anlass steht als eigener Testfall drin: "Shannon Aeradio" auf
10021 kHz ist Flugfunk, wird von EiBi als englischsprachig gefuehrt und
kam deshalb ungefiltert in die Hauptliste.
"""

import unittest
from pathlib import Path

from bulletin.sources.bands import (
    NO_FILTER,
    Band,
    BandPlan,
    load_band_plan,
    parse_band_plan,
)

PLAN = load_band_plan(Path(__file__).resolve().parents[1] / "data" / "broadcast_bands.yaml")


class TestBand(unittest.TestCase):
    def test_contains_is_inclusive_at_both_edges(self):
        band = Band(name="Test", min_khz=5900, max_khz=6200)
        self.assertTrue(band.contains(5900))
        self.assertTrue(band.contains(6200))
        self.assertTrue(band.contains(6070))
        self.assertFalse(band.contains(5899))
        self.assertFalse(band.contains(6201))


class TestShippedPlan(unittest.TestCase):
    def test_flight_radio_is_excluded(self):
        # Der eigentliche Anlass: Shannon Aeradio, 10021 kHz.
        self.assertFalse(PLAN.is_broadcast(10021))

    def test_other_utility_frequencies_are_excluded(self):
        for khz, what in [
            (4610, "zwischen 60 m und 49 m"),
            (8992, "Militaerflugfunk"),
            (10000, "Zeitzeichen WWV"),
            (12579, "Seefunk"),
            (16804, "Seefunk"),
            (2187.5, "Seenot-Rufkanal"),
        ]:
            self.assertFalse(PLAN.is_broadcast(khz), f"{khz} kHz ({what}) sollte draussen sein")

    def test_real_broadcast_stations_survive(self):
        # Alles Sender, die im Projektverlauf tatsaechlich vorkamen.
        for khz, what in [
            (198, "Droitwich, Langwelle"),
            (963, "Mittelwelle"),
            (1008, "Flevoland, Mittelwelle"),
            (1485, "Ortssender, Mittelwelle"),
            (3955, "Channel 292, 75 m"),
            (3985, "Radio Delta, 75 m"),
            (5900, "Radio Horizon, 49 m"),
            (6005, "Radio Taiwan ueber Bulgarien"),
            (6070, "Channel 292, 49 m"),
            (9600, "Issoudun, 31 m"),
            (15100, "19 m"),
        ]:
            self.assertTrue(PLAN.is_broadcast(khz), f"{khz} kHz ({what}) darf nicht wegfallen")

    def test_band_for_names_the_band(self):
        self.assertEqual(PLAN.band_for(6070).name, "49 m")
        self.assertEqual(PLAN.band_for(198).name, "Langwelle")
        self.assertEqual(PLAN.band_for(1008).name, "Mittelwelle")
        self.assertIsNone(PLAN.band_for(10021))

    def test_plan_is_enabled_by_default(self):
        self.assertTrue(PLAN.enabled)

    def test_plan_covers_all_the_usual_bands(self):
        self.assertGreaterEqual(len(PLAN), 15)


class TestSwitch(unittest.TestCase):
    def test_disabled_plan_lets_everything_through(self):
        plan = parse_band_plan("enabled: false\nbands:\n  - {name: X, min: 100, max: 200}\n")
        self.assertTrue(plan.is_broadcast(10021))
        self.assertTrue(plan.is_broadcast(150))

    def test_no_filter_constant_lets_everything_through(self):
        self.assertTrue(NO_FILTER.is_broadcast(10021))

    def test_empty_band_list_with_filter_on_blocks_everything(self):
        # Nicht unbedingt sinnvoll, aber es soll nachvollziehbar sein und
        # nicht stillschweigend das Gegenteil tun.
        plan = parse_band_plan("enabled: true\nbands: []\n")
        self.assertFalse(plan.is_broadcast(6070))


class TestParsing(unittest.TestCase):
    def test_missing_enabled_defaults_to_on(self):
        plan = parse_band_plan("bands:\n  - {name: X, min: 100, max: 200}\n")
        self.assertTrue(plan.enabled)

    def test_empty_document_yields_an_empty_plan(self):
        plan = parse_band_plan("")
        self.assertEqual(len(plan), 0)

    def test_pirate_band_can_be_added_by_hand(self):
        # Der in der YAML dokumentierte Weg muss auch funktionieren.
        plan = parse_band_plan(
            "enabled: true\nbands:\n  - {name: 'Piraten 48 m', min: 6200, max: 6400}\n"
        )
        self.assertTrue(plan.is_broadcast(6295))
        self.assertEqual(plan.band_for(6295).name, "Piraten 48 m")


if __name__ == "__main__":
    unittest.main()
