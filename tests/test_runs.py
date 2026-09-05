"""Tests fuer den Doppelschutz.

Der Anlass: seit cron-job.org den Abendlauf puenktlich anstoesst, ist der
GitHub-Zeitplan nur noch Rueckfall. Ohne diese Pruefung kaeme die
Push-Nachricht zweimal - einmal um 20:30 und noch einmal, wenn GitHub
seinen eigenen Termin Stunden spaeter nachholt.

Die Leitlinie in allen Zweifelsfaellen: lieber eine Nachricht zuviel als
keine. Ein Ausfall der Laufhistorie darf den Push nicht verhindern.
"""

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bulletin.runs import (
    already_notified_today,
    runs_url,
    successful_dispatch_today,
)
from bulletin.sources.http import FetchError

BERLIN = ZoneInfo("Europe/Berlin")


def run(started: str, conclusion: str = "success") -> dict:
    return {"run_started_at": started, "created_at": started, "conclusion": conclusion}


def payload(*runs) -> dict:
    return {"workflow_runs": list(runs)}


class TestRunsUrl(unittest.TestCase):
    def test_url_filters_on_external_triggers(self):
        url = runs_url("till/propagation-bulletin", "notify.yml")
        self.assertIn("/repos/till/propagation-bulletin/actions/workflows/notify.yml/runs", url)
        self.assertIn("event=workflow_dispatch", url)

    def test_url_carries_a_page_size(self):
        self.assertIn("per_page=5", runs_url("a/b", "c.yml", per_page=5))


class TestSuccessfulDispatchToday(unittest.TestCase):
    NOW = datetime(2026, 9, 5, 22, 0, tzinfo=BERLIN)

    def test_finds_a_successful_run_from_today(self):
        found = successful_dispatch_today(payload(run("2026-09-05T18:30:12Z")), self.NOW)
        self.assertIsNotNone(found)

    def test_ignores_a_run_from_yesterday(self):
        self.assertIsNone(successful_dispatch_today(payload(run("2026-09-04T18:30:12Z")), self.NOW))

    def test_ignores_a_failed_run(self):
        self.assertIsNone(
            successful_dispatch_today(payload(run("2026-09-05T18:30:12Z", "failure")), self.NOW)
        )

    def test_ignores_a_cancelled_run(self):
        self.assertIsNone(
            successful_dispatch_today(payload(run("2026-09-05T18:30:12Z", "cancelled")), self.NOW)
        )

    def test_ignores_a_run_still_in_progress(self):
        in_progress = {"run_started_at": "2026-09-05T18:30:12Z", "conclusion": None}
        self.assertIsNone(successful_dispatch_today(payload(in_progress), self.NOW))

    def test_picks_the_most_recent_of_several(self):
        found = successful_dispatch_today(
            payload(run("2026-09-05T05:15:00Z"), run("2026-09-05T18:30:12Z")), self.NOW
        )
        self.assertIn("18:30", found["run_started_at"])

    def test_day_boundary_is_berlin_not_utc(self):
        # 22:30 UTC am 4. September ist in Berlin bereits der 5. September,
        # 00:30 Ortszeit. Nach UTC gerechnet waere der Lauf "gestern".
        now = datetime(2026, 9, 5, 1, 0, tzinfo=BERLIN)
        found = successful_dispatch_today(payload(run("2026-09-04T22:30:00Z")), now)
        self.assertIsNotNone(found, "Tagesgrenze wird nach UTC statt Ortszeit gezogen")

    def test_falls_back_to_created_at_when_start_is_missing(self):
        entry = {"created_at": "2026-09-05T18:30:12Z", "conclusion": "success"}
        self.assertIsNotNone(successful_dispatch_today(payload(entry), self.NOW))


class TestRobustness(unittest.TestCase):
    """Unerwartete Antworten duerfen nicht dazu fuehren, dass der Push ausbleibt."""

    NOW = datetime(2026, 9, 5, 22, 0, tzinfo=BERLIN)

    def test_empty_payload(self):
        self.assertIsNone(successful_dispatch_today({}, self.NOW))

    def test_payload_without_the_expected_key(self):
        self.assertIsNone(successful_dispatch_today({"message": "Not Found"}, self.NOW))

    def test_payload_is_not_a_dict(self):
        self.assertIsNone(successful_dispatch_today([], self.NOW))
        self.assertIsNone(successful_dispatch_today(None, self.NOW))

    def test_entries_that_are_not_dicts_are_skipped(self):
        self.assertIsNone(successful_dispatch_today(payload("kaputt", 42), self.NOW))

    def test_unparseable_timestamp_is_skipped(self):
        self.assertIsNone(
            successful_dispatch_today(payload(run("gestern irgendwann")), self.NOW)
        )

    def test_missing_timestamp_is_skipped(self):
        self.assertIsNone(successful_dispatch_today(payload({"conclusion": "success"}), self.NOW))


class TestAlreadyNotifiedToday(unittest.TestCase):
    NOW = datetime(2026, 9, 5, 22, 0, tzinfo=BERLIN)

    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _opener(self, body: bytes, capture=None):
        def opener(request, timeout):
            if capture is not None:
                capture["headers"] = {k.lower(): v for k, v in request.header_items()}
                capture["url"] = request.full_url
            return self.FakeResponse(body)
        return opener

    def test_reports_true_when_a_run_exists(self):
        import json

        body = json.dumps(payload(run("2026-09-05T18:30:12Z"))).encode()
        done, reason = already_notified_today(
            "a/b", "notify.yml", "geheim", self.NOW,
            opener=self._opener(body), sleep=lambda s: None,
        )
        self.assertTrue(done)
        self.assertIn("uebersprungen", reason)

    def test_reports_false_when_no_run_exists(self):
        import json

        done, reason = already_notified_today(
            "a/b", "notify.yml", "geheim", self.NOW,
            opener=self._opener(json.dumps(payload()).encode()), sleep=lambda s: None,
        )
        self.assertFalse(done)

    def test_token_is_sent_as_bearer(self):
        import json

        capture = {}
        already_notified_today(
            "a/b", "notify.yml", "geheim", self.NOW,
            opener=self._opener(json.dumps(payload()).encode(), capture), sleep=lambda s: None,
        )
        self.assertEqual(capture["headers"].get("authorization"), "Bearer geheim")
        self.assertEqual(capture["headers"].get("x-github-api-version"), "2022-11-28")

    def test_network_failure_means_send_anyway(self):
        from urllib.error import URLError

        def failing(request, timeout):
            raise URLError("kein Netz")

        done, reason = already_notified_today(
            "a/b", "notify.yml", "geheim", self.NOW,
            opener=failing, sleep=lambda s: None,
        )
        self.assertFalse(done, "Bei Ausfall der Historie muss verschickt werden")
        self.assertIn("nicht abrufbar", reason)

    def test_garbage_response_means_send_anyway(self):
        done, reason = already_notified_today(
            "a/b", "notify.yml", "geheim", self.NOW,
            opener=self._opener(b"<html>kein JSON</html>"), sleep=lambda s: None,
        )
        self.assertFalse(done)


if __name__ == "__main__":
    unittest.main()
