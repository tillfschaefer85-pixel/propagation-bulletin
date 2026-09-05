"""Tests fuer den Abendlauf.

Der Kern (compose) bekommt fertige Dicts und eine feste Uhrzeit herein -
damit ist jede Formulierung reproduzierbar pruefbar, ohne Netz und ohne
Abhaengigkeit davon, wann der Test laeuft.
"""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bulletin.notify import (
    DEFAULT_KP_BUCKET,
    PushMessage,
    _timing_phrase,
    compose,
    current_kp,
    send_ntfy,
)
from bulletin.sources.swpc import KpSample

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 9, 3, 20, 30, tzinfo=BERLIN)
PAGE = "https://example.invalid/bulletin/"


def kp_sample(value: float) -> KpSample:
    return KpSample(when=datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc), kp=value)


def slot(kp: int, t: str, score: float) -> dict:
    return {"kp": kp, "t": t, "score": score, "gate": 1.0, "components": {"field": 0.7}}


def entry(station_id: str, scores: dict[int, float], *, t="21:30", reason=None, kind="main") -> dict:
    return {
        "station_id": station_id,
        "list_kind": kind,
        "best_by_kp": [slot(k, t, scores.get(k, 50.0)) for k in range(10)],
        "rarity": {"baseline": 0.3, "today": None, "reason": reason},
        "interest_rank_score": 30.0,
    }


def station(station_id: str, name: str, freq: float, band="mw", bearing=333.0) -> dict:
    return {
        "station_id": station_id,
        "name": name,
        "band_class": band,
        "freq_khz": freq,
        "language": "nl",
        "distance_km": 180.0,
        "bearing_deg": bearing,
        "power_kw": 400.0,
        "source": "mwlw",
        "null_bearings_deg": [63.0, 243.0],
        "hints": [],
    }


def bulletin_with(entries, when: str = "2026-09-03") -> dict:
    return {
        "schema_version": 1,
        "date": when,
        "generated_at": "2026-09-03T05:00:00+00:00",
        "eibi_season": "a26",
        "days_until_season_change": 52,
        "f107_flux": 132.0,
        "entries": entries,
    }


def stations_with(items) -> dict:
    return {"schema_version": 1, "stations": {s["station_id"]: s for s in items}}


class TestCurrentKp(unittest.TestCase):
    def test_observed_wins_over_forecast(self):
        observed = [kp_sample(1.67)]
        forecast = [kp_sample(4.33)]
        self.assertAlmostEqual(current_kp(observed, forecast).kp, 1.67)

    def test_falls_back_to_forecast_when_nothing_observed(self):
        self.assertAlmostEqual(current_kp([], [kp_sample(3.0)]).kp, 3.0)

    def test_returns_none_when_both_are_empty(self):
        self.assertIsNone(current_kp([], []))


class TestTimingPhrase(unittest.TestCase):
    """Dieselbe Uhrzeit bedeutet je nach Jahreszeit 'kommt noch' oder 'laeuft laengst'."""

    def test_future_window_is_announced(self):
        self.assertEqual(_timing_phrase("22:00", NOW), "ab 22:00")

    def test_past_window_is_reported_as_running(self):
        self.assertEqual(_timing_phrase("19:00", NOW), "laeuft seit 19:00")

    def test_current_window_is_marked_as_now(self):
        self.assertEqual(_timing_phrase("20:30", NOW), "jetzt (20:30)")
        self.assertEqual(_timing_phrase("21:00", NOW), "jetzt (21:00)")

    def test_midnight_counts_as_later_tonight_not_this_morning(self):
        # 00:00 gehoert zum laufenden Abend. Ohne Sonderbehandlung waere
        # es "laeuft seit 00:00" - also 20 Stunden in der Vergangenheit.
        self.assertEqual(_timing_phrase("00:00", NOW), "ab 00:00")

    def test_malformed_time_is_passed_through_rather_than_crashing(self):
        self.assertEqual(_timing_phrase("kaputt", NOW), "kaputt")


class TestComposeStaleness(unittest.TestCase):
    def test_yesterdays_bulletin_triggers_a_warning_instead_of_recommendations(self):
        stale = bulletin_with([entry("a", {2: 90.0})], when="2026-09-02")
        message = compose(
            stale, stations_with([station("a", "Test", 1008)]), kp_sample(2.0),
            now=NOW, page_url=PAGE, quiet_threshold=25.0,
        )
        self.assertIn("nicht aktuell", message.title)
        self.assertIn("2026-09-02", message.body)
        self.assertGreater(message.priority, 3)

    def test_missing_date_is_also_treated_as_stale(self):
        broken = bulletin_with([entry("a", {2: 90.0})])
        del broken["date"]
        message = compose(
            broken, stations_with([station("a", "Test", 1008)]), kp_sample(2.0),
            now=NOW, page_url=PAGE, quiet_threshold=25.0,
        )
        self.assertIn("nicht aktuell", message.title)

    def test_todays_bulletin_produces_a_normal_message(self):
        fresh = bulletin_with([entry("a", {2: 90.0})])
        message = compose(
            fresh, stations_with([station("a", "Test", 1008)]), kp_sample(2.0),
            now=NOW, page_url=PAGE, quiet_threshold=25.0,
        )
        self.assertNotIn("nicht aktuell", message.title)


class TestComposeContent(unittest.TestCase):
    def _compose(self, entries, stations, kp=kp_sample(2.0), threshold=25.0):
        return compose(
            bulletin_with(entries), stations_with(stations), kp,
            now=NOW, page_url=PAGE, quiet_threshold=threshold,
        )

    def test_title_names_the_best_station_and_the_kp_value(self):
        message = self._compose(
            [entry("a", {2: 88.0}, t="21:30")], [station("a", "Flevoland", 1008)]
        )
        self.assertIn("Flevoland", message.title)
        self.assertIn("Kp 2", message.title)

    def test_uses_the_kp_bucket_not_the_raw_value(self):
        # Kp 1.67 rundet auf Stufe 2 - der Score dieser Stufe muss zaehlen.
        entries = [entry("a", {2: 90.0, 1: 10.0})]
        message = self._compose(entries, [station("a", "Test", 1008)], kp=kp_sample(1.67))
        self.assertNotIn("Ruhiger Abend", message.title)

    def test_body_lists_several_stations_in_score_order(self):
        entries = [
            entry("low", {2: 40.0}),
            entry("high", {2: 95.0}),
            entry("mid", {2: 70.0}),
        ]
        stations = [
            station("low", "Schwach", 531),
            station("high", "Stark", 1008),
            station("mid", "Mittel", 756),
        ]
        message = self._compose(entries, stations)
        lines = message.body.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("Stark", lines[0])
        self.assertIn("Mittel", lines[1])
        self.assertIn("Schwach", lines[2])

    def test_dx_entries_do_not_appear_in_the_push(self):
        entries = [entry("main1", {2: 60.0}), entry("dx1", {2: 99.0}, kind="dx")]
        stations = [station("main1", "Hauptliste", 1008), station("dx1", "DX-Block", 540)]
        message = self._compose(entries, stations)
        self.assertIn("Hauptliste", message.body)
        self.assertNotIn("DX-Block", message.body)

    def test_mediumwave_line_includes_the_loop_bearing(self):
        message = self._compose(
            [entry("a", {2: 80.0})], [station("a", "Flevoland", 1008, band="mw", bearing=333.0)]
        )
        self.assertIn("Loop 333 Grad", message.body)

    def test_shortwave_line_omits_the_loop_bearing(self):
        message = self._compose(
            [entry("a", {2: 80.0})], [station("a", "Channel 292", 6070, band="sw")]
        )
        self.assertNotIn("Loop", message.body)

    def test_shortwave_frequency_is_shown_in_khz_below_thirty_megahertz(self):
        message = self._compose(
            [entry("a", {2: 80.0})], [station("a", "Channel 292", 6070, band="sw")]
        )
        self.assertIn("6070 kHz", message.body)

    def test_rarity_reason_is_surfaced(self):
        entries = [entry("a", {2: 80.0}, reason="Grauzone am Sender gegen 20:30")]
        message = self._compose(entries, [station("a", "Droitwich", 198, band="lw")])
        self.assertIn("Grauzone am Sender", message.body)

    def test_click_url_points_at_the_page(self):
        message = self._compose([entry("a", {2: 80.0})], [station("a", "Test", 1008)])
        self.assertEqual(message.click_url, PAGE)


class TestComposeCollapsesDuplicateStations(unittest.TestCase):
    """Ein Programm auf mehreren Frequenzen darf den Push nicht fuellen.

    Dieselbe Regel gilt auf der Seite (collapseByStation in docs/app.js).
    Laufen die beiden Fassungen auseinander, sagt der Push etwas anderes
    als die Seite, die zehn Sekunden spaeter aufgeht.
    """

    def _compose(self, entries, stations, kp=kp_sample(2.0), threshold=25.0):
        return compose(
            bulletin_with(entries), stations_with(stations), kp,
            now=NOW, page_url=PAGE, quiet_threshold=threshold,
        )

    def _romania(self):
        entries = [
            entry("r1", {2: 41.2}),
            entry("r2", {2: 58.9}),
            entry("r3", {2: 33.0}),
            entry("c", {2: 68.4}),
        ]
        stations = [
            station("r1", "Radio Romania Int.", 5955, band="sw"),
            station("r2", "Radio Romania Int.", 6030, band="sw"),
            station("r3", "Radio Romania Int.", 7350, band="sw"),
            station("c", "Channel 292", 3955, band="sw"),
        ]
        return entries, stations

    def test_only_the_best_frequency_of_a_station_appears(self):
        message = self._compose(*self._romania())
        lines = message.body.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("Channel 292", lines[0])
        self.assertIn("6030 kHz", lines[1])
        self.assertNotIn("5955", message.body)
        self.assertNotIn("7350", message.body)

    def test_the_line_says_how_many_frequencies_were_folded_in(self):
        message = self._compose(*self._romania())
        self.assertIn("+2 Frequenzen", message.body)

    def test_a_single_folded_frequency_is_counted_in_the_singular(self):
        entries = [entry("r1", {2: 41.2}), entry("r2", {2: 58.9})]
        stations = [
            station("r1", "Radio Romania Int.", 5955, band="sw"),
            station("r2", "Radio Romania Int.", 6030, band="sw"),
        ]
        message = self._compose(entries, stations)
        self.assertIn("+1 Frequenz", message.body)
        self.assertNotIn("+1 Frequenzen", message.body)

    def test_a_station_without_parallel_frequencies_gets_no_suffix(self):
        message = self._compose([entry("a", {2: 80.0})], [station("a", "Test", 1008)])
        self.assertNotIn("Frequenz", message.body)

    def test_the_band_separates_mediumwave_from_shortwave(self):
        entries = [entry("s", {2: 58.9}), entry("m", {2: 44.0})]
        stations = [
            station("s", "Radio Romania Int.", 6030, band="sw"),
            station("m", "Radio Romania Int.", 1314, band="mw"),
        ]
        message = self._compose(entries, stations)
        self.assertEqual(len(message.body.splitlines()), 2)

    def test_the_title_names_the_winning_frequency_of_the_group(self):
        message = self._compose(*self._romania())
        self.assertIn("Channel 292", message.title)

    def test_equal_scores_are_broken_by_frequency_not_by_input_order(self):
        # Auf Mittelwelle bekommen viele Eintraege dieselbe Punktzahl.
        # Ohne festen Nachschlag spraenge der Push von Tag zu Tag.
        stations = [
            station("hoch", "Doppelt", 9600, band="sw"),
            station("tief", "Doppelt", 6030, band="sw"),
        ]
        forwards = self._compose([entry("hoch", {2: 50.0}), entry("tief", {2: 50.0})], stations)
        backwards = self._compose([entry("tief", {2: 50.0}), entry("hoch", {2: 50.0})], stations)
        self.assertIn("6030 kHz", forwards.body)
        self.assertIn("6030 kHz", backwards.body)

    def test_case_and_spacing_do_not_split_a_station(self):
        entries = [entry("a", {2: 60.0}), entry("b", {2: 50.0})]
        stations = [
            station("a", "Radio  Romania Int. ", 6030, band="sw"),
            station("b", "radio romania int.", 7350, band="sw"),
        ]
        message = self._compose(entries, stations)
        self.assertEqual(len(message.body.splitlines()), 1)

    def test_the_collapse_follows_the_kp_bucket(self):
        # Welche Frequenz gewinnt, haengt an der Lage - genau wie der
        # Regler auf der Seite es zeigt.
        entries = [entry("a", {2: 60.0, 5: 10.0}), entry("b", {2: 30.0, 5: 45.0})]
        stations = [
            station("a", "Radio Romania Int.", 5955, band="sw"),
            station("b", "Radio Romania Int.", 6030, band="sw"),
        ]
        self.assertIn("5955 kHz", self._compose(entries, stations, kp=kp_sample(2.0)).body)
        self.assertIn("6030 kHz", self._compose(entries, stations, kp=kp_sample(5.0)).body)


class TestComposeQuietEvening(unittest.TestCase):
    def test_below_threshold_is_announced_as_quiet(self):
        message = compose(
            bulletin_with([entry("a", {2: 12.0})]),
            stations_with([station("a", "Test", 1008)]),
            kp_sample(2.0), now=NOW, page_url=PAGE, quiet_threshold=25.0,
        )
        self.assertIn("Ruhiger Abend", message.title)

    def test_quiet_evening_still_lists_what_is_available(self):
        message = compose(
            bulletin_with([entry("a", {2: 12.0})]),
            stations_with([station("a", "Test", 1008)]),
            kp_sample(2.0), now=NOW, page_url=PAGE, quiet_threshold=25.0,
        )
        self.assertIn("Test", message.body)

    def test_quiet_evening_uses_lower_priority(self):
        quiet = compose(
            bulletin_with([entry("a", {2: 12.0})]),
            stations_with([station("a", "Test", 1008)]),
            kp_sample(2.0), now=NOW, page_url=PAGE, quiet_threshold=25.0,
        )
        loud = compose(
            bulletin_with([entry("a", {2: 90.0})]),
            stations_with([station("a", "Test", 1008)]),
            kp_sample(2.0), now=NOW, page_url=PAGE, quiet_threshold=25.0,
        )
        self.assertLess(quiet.priority, loud.priority)


class TestComposeDegradedCases(unittest.TestCase):
    def test_missing_kp_says_so_and_uses_the_default_bucket(self):
        entries = [entry("a", {DEFAULT_KP_BUCKET: 80.0})]
        message = compose(
            bulletin_with(entries), stations_with([station("a", "Test", 1008)]), None,
            now=NOW, page_url=PAGE, quiet_threshold=25.0,
        )
        self.assertIn("Kp unbekannt", message.title)

    def test_empty_bulletin_produces_an_honest_message_not_a_crash(self):
        message = compose(
            bulletin_with([]), stations_with([]), kp_sample(2.0),
            now=NOW, page_url=PAGE, quiet_threshold=25.0,
        )
        self.assertIn("nichts zu holen", message.title)

    def test_entry_without_matching_station_metadata_is_skipped(self):
        # Sollte nie vorkommen, waere aber ein KeyError mitten in der Nacht.
        entries = [entry("ghost", {2: 99.0}), entry("real", {2: 50.0})]
        message = compose(
            bulletin_with(entries), stations_with([station("real", "Echt", 1008)]),
            kp_sample(2.0), now=NOW, page_url=PAGE, quiet_threshold=25.0,
        )
        self.assertIn("Echt", message.body)
        self.assertNotIn("ghost", message.body)


class TestNtfyPayload(unittest.TestCase):
    def test_payload_contains_the_required_fields(self):
        payload = PushMessage(
            title="T", body="B", click_url="https://x.invalid", tags=("radio",)
        ).to_ntfy_payload("mein-topic")
        self.assertEqual(payload["topic"], "mein-topic")
        self.assertEqual(payload["title"], "T")
        self.assertEqual(payload["message"], "B")
        self.assertEqual(payload["click"], "https://x.invalid")
        self.assertEqual(payload["tags"], ["radio"])

    def test_optional_fields_are_omitted_when_unset(self):
        payload = PushMessage(title="T", body="B").to_ntfy_payload("topic")
        self.assertNotIn("click", payload)
        self.assertNotIn("tags", payload)

    def test_payload_is_json_serializable(self):
        json.dumps(PushMessage(title="T", body="B", tags=("a",)).to_ntfy_payload("t"))


class TestSendNtfy(unittest.TestCase):
    def test_posts_json_to_the_configured_endpoint(self):
        captured = {}

        class FakeResponse:
            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_opener(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["headers"] = dict(request.header_items())
            return FakeResponse()

        send_ntfy(
            PushMessage(title="Titel", body="Text"),
            topic="mein-topic",
            base_url="https://ntfy.invalid",
            opener=fake_opener,
        )
        self.assertEqual(captured["url"], "https://ntfy.invalid")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"]["topic"], "mein-topic")
        self.assertEqual(captured["body"]["title"], "Titel")

    def test_token_is_sent_as_bearer_when_provided(self):
        captured = {}

        class FakeResponse:
            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_opener(request, timeout):
            captured["headers"] = {k.lower(): v for k, v in request.header_items()}
            return FakeResponse()

        send_ntfy(
            PushMessage(title="T", body="B"), topic="t", token="geheim", opener=fake_opener
        )
        self.assertEqual(captured["headers"].get("Authorization".lower()), "Bearer geheim")

    def test_no_authorization_header_without_token(self):
        captured = {}

        class FakeResponse:
            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_opener(request, timeout):
            captured["headers"] = {k.lower(): v for k, v in request.header_items()}
            return FakeResponse()

        send_ntfy(PushMessage(title="T", body="B"), topic="t", opener=fake_opener)
        self.assertNotIn("authorization", captured["headers"])


class TestQuietThresholdComesFromWeights(unittest.TestCase):
    def test_weights_file_defines_the_threshold(self):
        from bulletin.physics.propagation import Weights

        weights = Weights.load(Path(__file__).resolve().parents[1] / "data" / "weights.yaml")
        threshold = weights.get("notify", "quiet_evening_threshold")
        self.assertIsInstance(threshold, (int, float))
        self.assertGreater(threshold, 0)


if __name__ == "__main__":
    unittest.main()
