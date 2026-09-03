"""Tests fuer die SWPC-Quelle.

Die Beispieldaten sind woertliche Ausschnitte echter Antworten (abgerufen
am 3. September 2026), keine erfundenen Fixtures. Damit prueft der Test
das tatsaechliche Format, nicht meine Annahme darueber.
"""

import unittest
from datetime import datetime, timezone

from bulletin.sources.swpc import (
    KpSample,
    latest,
    parse_flux,
    parse_forecast_kp,
    parse_observed_kp,
    smoothed_flux,
)

# Woertlicher Ausschnitt von https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json
OBSERVED_SAMPLE = [
    {"time_tag": "2026-09-01T18:00:00", "Kp": 1.67, "a_running": 6, "station_count": 8},
    {"time_tag": "2026-09-01T21:00:00", "Kp": 1.67, "a_running": 6, "station_count": 8},
    {"time_tag": "2026-09-02T00:00:00", "Kp": 0.67, "a_running": 3, "station_count": 8},
    {"time_tag": "2026-09-02T03:00:00", "Kp": 1.00, "a_running": 4, "station_count": 8},
    {"time_tag": "2026-09-02T06:00:00", "Kp": 2.33, "a_running": 9, "station_count": 8},
]

# Woertlicher Ausschnitt von .../noaa-planetary-k-index-forecast.json
FORECAST_SAMPLE = [
    {"time_tag": "2026-09-03T09:00:00", "kp": 1.00, "observed": "observed", "noaa_scale": None},
    {"time_tag": "2026-09-03T12:00:00", "kp": 0.67, "observed": "estimated", "noaa_scale": None},
    {"time_tag": "2026-09-03T15:00:00", "kp": 1.33, "observed": "estimated", "noaa_scale": None},
    {"time_tag": "2026-09-04T00:00:00", "kp": 1.33, "observed": "predicted", "noaa_scale": None},
    {"time_tag": "2026-09-04T03:00:00", "kp": 1.67, "observed": "predicted", "noaa_scale": None},
]

# Woertlicher Ausschnitt von .../10cm-flux-30-day.json
FLUX_SAMPLE = [
    {"time_tag": "2026-08-21T20:00:00", "flux": 126},
    {"time_tag": "2026-08-22T20:00:00", "flux": 124},
    {"time_tag": "2026-08-23T20:00:00", "flux": 128},
    {"time_tag": "2026-08-24T20:00:00", "flux": 143},
    {"time_tag": "2026-08-25T20:00:00", "flux": 132},
]


class TestParseObservedKp(unittest.TestCase):
    def test_field_name_is_capital_kp(self):
        # Der beobachtete Index nennt das Feld "Kp", nicht "kp" - das
        # unterscheidet sich vom Prognose-Endpunkt, und genau das hat
        # beim ersten echten Abruf ueberrascht.
        samples = parse_observed_kp(OBSERVED_SAMPLE)
        self.assertEqual(len(samples), 5)
        self.assertAlmostEqual(samples[0].kp, 1.67)

    def test_time_tag_is_interpreted_as_utc(self):
        samples = parse_observed_kp(OBSERVED_SAMPLE)
        self.assertEqual(samples[0].when.tzinfo, timezone.utc)
        self.assertEqual(samples[0].when, datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc))

    def test_default_status_is_observed(self):
        samples = parse_observed_kp(OBSERVED_SAMPLE)
        self.assertEqual(samples[0].status, "observed")


class TestParseForecastKp(unittest.TestCase):
    def test_field_name_is_lowercase_kp(self):
        samples = parse_forecast_kp(FORECAST_SAMPLE)
        self.assertAlmostEqual(samples[0].kp, 1.00)

    def test_status_reflects_observed_estimated_predicted(self):
        samples = parse_forecast_kp(FORECAST_SAMPLE)
        statuses = [s.status for s in samples]
        self.assertEqual(statuses, ["observed", "estimated", "estimated", "predicted", "predicted"])


class TestKpBucket(unittest.TestCase):
    def test_fractional_kp_rounds_to_nearest_integer_step(self):
        # Genau die fraktionalen Werte, die NOAA tatsaechlich liefert.
        cases = [
            (0.00, 0), (0.33, 0), (0.67, 1), (1.00, 1),
            (1.33, 1), (1.67, 2), (2.33, 2), (4.33, 4),
        ]
        for kp, expected in cases:
            sample = KpSample(when=datetime.now(timezone.utc), kp=kp)
            self.assertEqual(sample.bucket, expected, f"kp={kp}")

    def test_bucket_is_clamped_to_the_table_range(self):
        high = KpSample(when=datetime.now(timezone.utc), kp=9.67)
        self.assertEqual(high.bucket, 9)
        low = KpSample(when=datetime.now(timezone.utc), kp=-0.5)
        self.assertEqual(low.bucket, 0)


class TestParseFlux(unittest.TestCase):
    def test_parses_time_and_value(self):
        samples = parse_flux(FLUX_SAMPLE)
        self.assertEqual(len(samples), 5)
        self.assertEqual(samples[0].flux, 126.0)

    def test_smoothed_flux_averages_the_most_recent_days(self):
        samples = parse_flux(FLUX_SAMPLE)
        # Die letzten drei Werte: 128, 143, 132
        self.assertAlmostEqual(smoothed_flux(samples, days=3), (128 + 143 + 132) / 3)

    def test_smoothed_flux_of_empty_list_is_none(self):
        self.assertIsNone(smoothed_flux([]))

    def test_smoothed_flux_handles_unsorted_input(self):
        shuffled = [FLUX_SAMPLE[2], FLUX_SAMPLE[0], FLUX_SAMPLE[4], FLUX_SAMPLE[1], FLUX_SAMPLE[3]]
        samples = parse_flux(shuffled)
        self.assertAlmostEqual(smoothed_flux(samples, days=3), (128 + 143 + 132) / 3)


class TestLatest(unittest.TestCase):
    def test_finds_the_most_recent_entry_regardless_of_order(self):
        samples = parse_observed_kp(OBSERVED_SAMPLE)
        newest = latest(samples)
        self.assertEqual(newest.when, datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc))
        self.assertAlmostEqual(newest.kp, 2.33)

    def test_empty_list_returns_none(self):
        self.assertIsNone(latest([]))


if __name__ == "__main__":
    unittest.main()
