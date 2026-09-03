"""Tests fuer das Datenmodell und die JSON-Serialisierung.

Der Kernpunkt: was rauskommt, muss echtes JSON sein (Tupel -> Listen)
und muss sich unveraendert wieder einlesen lassen (Rundreise-Test).
"""

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from bulletin.model import (
    Bulletin,
    BulletinEntry,
    BestSlot,
    Rarity,
    StationMeta,
    SCHEMA_VERSION,
    bulletin_to_dict,
    stations_to_dict,
    write_bulletin,
    write_json,
    write_stations,
)


def make_station(station_id="mw-1008-flevoland") -> StationMeta:
    return StationMeta(
        station_id=station_id,
        name="Test Sender",
        band_class="mw",
        freq_khz=1008.0,
        language="nl",
        distance_km=180.3,
        bearing_deg=333.1,
        power_kw=400.0,
        source="mwlw",
        null_bearings_deg=(63.1, 243.1),
        hints=("Loop auf 333 Grad", "Stoerer aus Sueden ausnullbar"),
    )


def make_slot(kp: int, score: float) -> BestSlot:
    return BestSlot(kp=kp, t="21:30", score=score, gate=1.0, components={"darkness": 0.9})


def make_entry() -> BulletinEntry:
    return BulletinEntry(
        station_id="mw-1008-flevoland",
        list_kind="main",
        best_by_kp=tuple(make_slot(kp, 90.0 - kp * 5) for kp in range(10)),
        rarity=Rarity(baseline=0.35, today=0.6, reason="Grauzonenoeffnung"),
        interest_rank_score=44.4,
    )


def make_bulletin() -> Bulletin:
    return Bulletin(
        schema_version=SCHEMA_VERSION,
        date="2026-09-03",
        generated_at="2026-09-03T05:12:00+00:00",
        eibi_season="a26",
        days_until_season_change=52,
        f107_flux=132.0,
        entries=(make_entry(),),
    )


class TestRarity(unittest.TestCase):
    def test_combined_prefers_today_over_baseline(self):
        r = Rarity(baseline=0.2, today=0.9)
        self.assertEqual(r.combined(), 0.9)

    def test_combined_falls_back_to_baseline_when_today_is_none(self):
        r = Rarity(baseline=0.2, today=None)
        self.assertEqual(r.combined(), 0.2)

    def test_combined_handles_explicit_zero_today(self):
        # 0.0 ist ein gueltiger, informativer Wert und darf nicht mit
        # "nicht gesetzt" verwechselt werden - das ist der Grund, warum
        # today als Optional[float] und nicht als float mit Default 0
        # modelliert ist.
        r = Rarity(baseline=0.5, today=0.0)
        self.assertEqual(r.combined(), 0.0)


class TestStationsSerialization(unittest.TestCase):
    def test_stations_to_dict_is_keyed_by_id(self):
        data = stations_to_dict([make_station("a"), make_station("b")])
        self.assertEqual(set(data["stations"].keys()), {"a", "b"})
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)

    def test_tuples_become_lists_for_json(self):
        data = stations_to_dict([make_station()])
        entry = data["stations"]["mw-1008-flevoland"]
        self.assertIsInstance(entry["null_bearings_deg"], list)
        self.assertIsInstance(entry["hints"], list)
        self.assertEqual(entry["hints"], ["Loop auf 333 Grad", "Stoerer aus Sueden ausnullbar"])

    def test_result_is_actually_json_serializable(self):
        data = stations_to_dict([make_station()])
        # Wuerfe eine echte Exception, wenn irgendwo noch ein Tupel,
        # ein dataclass-Objekt oder etwas anderes Nicht-JSON-taugliches
        # durchgerutscht ist.
        json.dumps(data)


class TestBulletinSerialization(unittest.TestCase):
    def test_best_by_kp_has_all_ten_steps_after_serialization(self):
        data = bulletin_to_dict(make_bulletin())
        slots = data["entries"][0]["best_by_kp"]
        self.assertEqual(len(slots), 10)
        self.assertEqual([s["kp"] for s in slots], list(range(10)))

    def test_rarity_nested_dict_round_trips(self):
        data = bulletin_to_dict(make_bulletin())
        rarity = data["entries"][0]["rarity"]
        self.assertEqual(rarity["baseline"], 0.35)
        self.assertEqual(rarity["today"], 0.6)
        self.assertEqual(rarity["reason"], "Grauzonenoeffnung")

    def test_result_is_actually_json_serializable(self):
        json.dumps(bulletin_to_dict(make_bulletin()))


class TestWriteJson(unittest.TestCase):
    def test_writes_readable_indented_json_with_trailing_newline(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            write_json({"a": 1, "b": [1, 2, 3]}, path)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.endswith("\n"))
            self.assertIn("\n  ", text)  # eingerueckt, kein Einzeiler

    def test_creates_parent_directories(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "deeper" / "out.json"
            write_json({"x": 1}, path)
            self.assertTrue(path.exists())

    def test_round_trip_preserves_content(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.json"
            original = {"a": [1, 2, {"b": "c"}], "d": None}
            write_json(original, path)
            reloaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(original, reloaded)


class TestWriteBulletinAndStations(unittest.TestCase):
    def test_write_bulletin_creates_live_file_and_archive_copy(self):
        with TemporaryDirectory() as tmp:
            docs_dir = Path(tmp) / "docs"
            live, archive = write_bulletin(
                make_bulletin(), docs_dir=docs_dir, today=date(2026, 9, 3)
            )
            self.assertEqual(live, docs_dir / "bulletin.json")
            self.assertEqual(archive, docs_dir / "archive" / "2026-09-03.json")
            self.assertTrue(live.exists())
            self.assertTrue(archive.exists())

    def test_archive_and_live_content_are_identical(self):
        with TemporaryDirectory() as tmp:
            docs_dir = Path(tmp) / "docs"
            live, archive = write_bulletin(
                make_bulletin(), docs_dir=docs_dir, today=date(2026, 9, 3)
            )
            self.assertEqual(live.read_text(encoding="utf-8"), archive.read_text(encoding="utf-8"))

    def test_write_stations_creates_stations_json(self):
        with TemporaryDirectory() as tmp:
            docs_dir = Path(tmp) / "docs"
            path = write_stations([make_station()], docs_dir=docs_dir)
            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("mw-1008-flevoland", data["stations"])


if __name__ == "__main__":
    unittest.main()
