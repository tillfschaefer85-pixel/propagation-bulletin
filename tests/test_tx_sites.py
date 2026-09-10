"""Tests fuer die Sendestandort-Tabelle."""

import unittest
from pathlib import Path

from bulletin.sources.tx_sites import TxSite, TxSiteTable, load_tx_sites
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


class TestCountryDefaults(unittest.TestCase):
    """Länder mit nur einer Sendeanlage.

    Die EiBi-README sagt es ausdrücklich: "No such code is used if there is
    only one transmitter site in that country." Ein leeres Standortfeld ist
    dort keine Lücke, sondern eine Aussage — der Vatikan kam mit 31 Treffern
    in der Diagnose vor, ohne je auflösbar zu sein.
    """

    def setUp(self):
        self.table = TxSiteTable(
            {"BUL-s": Point(lat=42.8167, lon=23.2167), "D-n": Point(lat=52.6486, lon=12.9092)},
            {
                "CVA": TxSite(point=Point(lat=42.05, lon=12.3167), name="Santa Maria di Galeria"),
                "ASC": TxSite(point=Point(lat=-7.9, lon=-14.3833), name="Ascension Island"),
            },
        )

    def test_empty_code_resolves_for_single_site_countries(self):
        site = self.table.lookup_site("CVA", "")
        self.assertIsNotNone(site)
        self.assertEqual(site.name, "Santa Maria di Galeria")

    def test_empty_code_stays_unresolved_for_other_countries(self):
        # Rumänien hat mehrere Anlagen - hier wäre jede Wahl geraten.
        self.assertIsNone(self.table.lookup_site("ROU", ""))

    def test_relay_naming_only_a_single_site_country_resolves(self):
        site = self.table.lookup_site("G", "/ASC")
        self.assertIsNotNone(site)
        self.assertEqual(site.name, "Ascension Island")

    def test_relay_naming_a_multi_site_country_stays_unresolved(self):
        self.assertIsNone(self.table.lookup_site("G", "/CYP"))

    def test_explicit_site_code_wins_over_the_country_default(self):
        site = self.table.lookup_site("D", "n")
        self.assertAlmostEqual(site.point.lat, 52.6486, places=3)

    def test_coverage_includes_default_only_countries(self):
        self.assertIn("CVA", self.table.coverage())

    def test_table_without_defaults_behaves_as_before(self):
        plain = TxSiteTable({"D-n": Point(lat=52.6486, lon=12.9092)})
        self.assertIsNone(plain.lookup_site("CVA", ""))


class TestShippedTableAfterExpansion(unittest.TestCase):
    """Die Codes aus der echten Diagnose müssen jetzt auflösbar sein."""

    def setUp(self):
        self.table = load_tx_sites(
            Path(__file__).resolve().parents[1] / "data" / "tx_sites.yaml"
        )

    def test_the_most_frequent_missing_codes_now_resolve(self):
        # Genau die Spitzenreiter aus dem Protokoll des echten Laufs.
        for itu, code, hits in [
            ("KRE", "u", 87), ("CHN", "ka", 78), ("G", "/OMA-a", 31),
            ("CVA", "", 31), ("CHN", "b", 30), ("NZL", "r", 29),
            ("CHN", "x", 27), ("J", "y", 23), ("G", "/ASC", 21),
            ("CHN", "k", 19), ("POL", "p", 19), ("F", "g", 18),
            ("CHN", "u", 17), ("CHN", "t", 16), ("INS", "j", 13),
            ("IND", "b", 13), ("B", "b", 11), ("RUS", "c", 11),
            ("CUB", "", 11), ("KOR", "k", 10), ("CUB", "b", 10),
        ]:
            with self.subTest(code=f"{itu}-{code}"):
                self.assertIsNotNone(
                    self.table.lookup_site(itu, code),
                    f"{itu}-{code} ({hits} Treffer) ist weiterhin nicht auflösbar",
                )

    def test_ambiguous_cases_are_still_refused(self):
        for itu, code in [("ROU", ""), ("G", "/CYP"), ("CHN", "/MLI")]:
            with self.subTest(code=f"{itu}-{code}"):
                self.assertIsNone(self.table.lookup_site(itu, code))

    def test_table_has_grown_substantially(self):
        self.assertGreaterEqual(len(self.table), 90)
        self.assertGreaterEqual(len(self.table.coverage()), 50)
