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


class TestRelays(unittest.TestCase):
    """Übernahmen: EiBi schreibt sie mit führendem Schrägstrich.

    Der Anlass war eine Diagnosezeile aus dem echten Lauf: "TWN-/BUL-s".
    Radio Taiwan sendet über Sofia — die Anlage steht längst in unserer
    Tabelle, nur wurde nach einem Schlüssel gesucht, den es nicht geben
    kann. Im internationalen Kurzwellenrundfunk sind solche Übernahmen
    der Normalfall.
    """

    def setUp(self):
        self.table = TxSiteTable({
            "BUL-s": Point(lat=42.8167, lon=23.2167),
            "D-n": Point(lat=52.6486, lon=12.9092),
            "G-w": Point(lat=52.3167, lon=-2.7167),
        })

    def test_relay_with_country_and_site_resolves_to_the_antenna(self):
        site = self.table.lookup_site("TWN", "/BUL-s")
        self.assertIsNotNone(site)
        self.assertAlmostEqual(site.point.lat, 42.8167, places=3)

    def test_relay_ignores_the_broadcasters_home_country(self):
        # Egal wer sendet - was zählt, ist die Anlage.
        for home in ("TWN", "USA", "J", "IND"):
            self.assertIsNotNone(self.table.lookup_site(home, "/BUL-s"), home)

    def test_relay_naming_only_a_country_is_not_guessed(self):
        # "/CYP" nennt keine Anlage. Ein Ländermittelpunkt wäre geraten.
        self.assertIsNone(self.table.lookup_site("G", "/CYP"))

    def test_unknown_relay_site_returns_none(self):
        self.assertIsNone(self.table.lookup_site("EQA", "/D-we"))

    def test_direct_lookup_still_works(self):
        self.assertIsNotNone(self.table.lookup_site("D", "n"))

    def test_direct_code_is_not_confused_with_a_relay(self):
        # "n" ist Nauen für Deutschland, nicht für irgendein anderes Land.
        self.assertIsNone(self.table.lookup_site("USA", "n"))

    def test_lookup_returns_the_relay_point_too(self):
        point = self.table.lookup("TWN", "/BUL-s")
        self.assertIsNotNone(point)
        self.assertAlmostEqual(point.lon, 23.2167, places=3)
