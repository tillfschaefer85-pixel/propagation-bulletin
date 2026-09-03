"""Tests fuer den Netzzugriff.

Kein Test hier darf eine echte Verbindung aufbauen. Der 'opener' wird
durch ein Fake ersetzt, das genau das tut, was wir gegen den echten Fall
pruefen wollen: erfolgreich antworten, dauerhaft scheitern, oder erst
nach ein paar Versuchen klappen.
"""

import io
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError

from bulletin.sources.http import Fetched, FetchError, fetch_json, fetch_text


class FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def always_succeeds(payload: bytes):
    def opener(request, timeout):
        return FakeResponse(payload)
    return opener


def always_fails():
    def opener(request, timeout):
        raise URLError("kein Netz in diesem Test")
    return opener


def fails_then_succeeds(times: int, payload: bytes):
    calls = {"n": 0}

    def opener(request, timeout):
        calls["n"] += 1
        if calls["n"] <= times:
            raise URLError("noch nicht")
        return FakeResponse(payload)
    return opener


class TestFetchText(unittest.TestCase):
    def test_successful_fetch_returns_text(self):
        result = fetch_text(
            "https://example.invalid/data",
            opener=always_succeeds(b"hallo welt"),
            sleep=lambda s: None,
        )
        self.assertEqual(result.text, "hallo welt")
        self.assertFalse(result.from_cache)

    def test_retries_before_succeeding(self):
        result = fetch_text(
            "https://example.invalid/data",
            opener=fails_then_succeeds(2, b"beim dritten mal"),
            sleep=lambda s: None,
            retries=3,
        )
        self.assertEqual(result.text, "beim dritten mal")

    def test_gives_up_after_exhausting_retries_without_cache(self):
        with self.assertRaises(FetchError):
            fetch_text(
                "https://example.invalid/data",
                opener=always_fails(),
                sleep=lambda s: None,
                retries=2,
            )

    def test_falls_back_to_cache_when_network_fails(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            # Erst erfolgreich fuellen...
            fetch_text(
                "https://example.invalid/data",
                cache_dir=cache_dir,
                opener=always_succeeds(b"gestriger stand"),
                sleep=lambda s: None,
            )
            # ...dann faellt das Netz aus, der Cache muss einspringen.
            result = fetch_text(
                "https://example.invalid/data",
                cache_dir=cache_dir,
                opener=always_fails(),
                sleep=lambda s: None,
                retries=2,
            )
            self.assertEqual(result.text, "gestriger stand")
            self.assertTrue(result.from_cache)

    def test_stale_cache_beyond_max_age_is_rejected(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            fetch_text(
                "https://example.invalid/data",
                cache_dir=cache_dir,
                opener=always_succeeds(b"uralt"),
                sleep=lambda s: None,
            )
            with self.assertRaises(FetchError):
                fetch_text(
                    "https://example.invalid/data",
                    cache_dir=cache_dir,
                    opener=always_fails(),
                    sleep=lambda s: None,
                    retries=1,
                    max_cache_age_days=0.0,
                )

    def test_no_sleep_calls_are_pointless_delays_in_tests(self):
        # Stellt sicher, dass wir sleep() tatsaechlich injizieren und
        # nicht heimlich time.sleep aus der Standardbibliothek nutzen.
        calls = []
        fetch_text(
            "https://example.invalid/data",
            opener=fails_then_succeeds(1, b"ok"),
            sleep=lambda s: calls.append(s),
            retries=2,
        )
        self.assertEqual(len(calls), 1)


class TestFetchJson(unittest.TestCase):
    def test_valid_json_is_parsed(self):
        parsed, fetched = fetch_json(
            "https://example.invalid/data.json",
            opener=always_succeeds(b'{"a": 1, "b": [1, 2, 3]}'),
            sleep=lambda s: None,
        )
        self.assertEqual(parsed, {"a": 1, "b": [1, 2, 3]})

    def test_invalid_json_raises_fetch_error(self):
        with self.assertRaises(FetchError):
            fetch_json(
                "https://example.invalid/data.json",
                opener=always_succeeds(b"das hier ist kein JSON"),
                sleep=lambda s: None,
            )


if __name__ == "__main__":
    unittest.main()
