"""Tests fuer die Sendestandort-Tabelle."""

import unittest
from pathlib import Path

from bulletin.sources.tx_sites import TxSiteTable, load_tx_sites
from bulletin.physics.geometry import Point


class TestLookup(unittest.TestCase):
    def setUp(self):
        self.table = TxSiteTable({
            "D-n": Point(lat=52.6486, lon=12.9092),
            "G-d": Point(lat=52.3, lon=-2.1),
        })

    def test_known_site_is_found(self):
        point = self.table.lookup("D", "n")
        self.assertAlmostEqual(point.lat, 52.6486)
        self.assertAlmostEqual(point.lon, 12.9092)

    def test_unknown_site_returns_none(self):
        self.assertIsNone(self.table.lookup("USA", "o"))

    def test_empty_transmitter_site_returns_none_without_guessing(self):
        # Kein Rueckfall auf einen Landesmittelpunkt - lieber eine
        # ehrliche Luecke als eine geratene Koordinate.
        self.assertIsNone(self.table.lookup("D", ""))

    def test_known_country_wrong_site_code_returns_none(self):
        self.assertIsNone(self.table.lookup("D", "xx-does-not-exist"))

    def test_len_reports_entry_count(self):
        self.assertEqual(len(self.table), 2)

    def test_coverage_lists_unique_countries(self):
        self.assertEqual(self.table.coverage(), frozenset({"D", "G"}))


class TestLoadFromDisk(unittest.TestCase):
    def test_shipped_table_loads_and_resolves_known_sites(self):
        path = Path(__file__).resolve().parents[1] / "data" / "tx_sites.yaml"
        table = load_tx_sites(path)
        self.assertGreater(len(table), 30)

        nauen = table.lookup("D", "n")
        self.assertIsNotNone(nauen)
        self.assertAlmostEqual(nauen.lat, 52.6486, places=3)
        self.assertAlmostEqual(nauen.lon, 12.9092, places=3)

        droitwich = table.lookup("G", "d")
        self.assertIsNotNone(droitwich)
        self.assertAlmostEqual(droitwich.lat, 52.3, places=1)

    def test_shipped_table_covers_the_countries_seen_in_the_real_sample(self):
        # Diese Sender kamen im echten Testlauf gegen sked-a26.csv vor -
        # wenn die Tabelle sie nicht mehr findet, ist etwas kaputtgegangen.
        path = Path(__file__).resolve().parents[1] / "data" / "tx_sites.yaml"
        table = load_tx_sites(path)
        self.assertIsNotNone(table.lookup("D", "r"))   # Channel 292, Rohrbach
        self.assertIsNotNone(table.lookup("D", "n"))   # Nauen
        self.assertIsNotNone(table.lookup("D", "wa"))  # Winsen, Shortwave Radio Gold
        self.assertIsNotNone(table.lookup("HOL", "e")) # Elburg, Radio Delta Int.

    def test_missing_site_is_absent_not_guessed(self):
        path = Path(__file__).resolve().parents[1] / "data" / "tx_sites.yaml"
        table = load_tx_sites(path)
        # USA ist bewusst noch nicht abgedeckt (siehe Kommentar in der YAML).
        self.assertIsNone(table.lookup("USA", "o"))


if __name__ == "__main__":
    unittest.main()
