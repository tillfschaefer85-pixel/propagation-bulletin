"""Tests fuer die Bewertung.

Eine Heuristik laesst sich nicht gegen die Wahrheit testen - es gibt
keine richtige Punktzahl. Testbar ist aber ihr Verhalten: was muss
groesser sein als was, was muss verschwinden, was darf sich nicht
aendern. Genau solche Invarianten stehen hier.
"""

import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bulletin.physics.geometry import BERGHEIM, Point
from bulletin.physics.propagation import (
    _field_strength_proxy,
    Link,
    Weights,
    best_slot_by_kp,
    classify_band,
    estimate_luf_mhz,
    score,
    score_mw_lw,
    score_sw,
)

BERLIN = ZoneInfo("Europe/Berlin")
WEIGHTS = Weights.load(Path(__file__).resolve().parents[1] / "data" / "weights.yaml")

FLEVOLAND = Point(52.3906, 5.4433)
SOLT = Point(46.4319, 19.0203)
DROITWICH = Point(52.2956, -2.1069)
ISSOUDUN = Point(46.9400, 1.9000)     # KW-Sender Frankreich
NAUEN = Point(52.6478, 12.9200)       # KW-Sender Brandenburg


def mw(freq, tx, power, station_id="test"):
    return Link(station_id=station_id, freq_khz=freq, tx=tx, rx=BERGHEIM, power_kw=power)


def sw(freq_khz, tx, power, bearing=None, station_id="test"):
    return Link(
        station_id=station_id,
        freq_khz=freq_khz,
        tx=tx,
        rx=BERGHEIM,
        power_kw=power,
        target_bearing_deg=bearing,
    )


class TestBandClassification(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(classify_band(198), "lw")
        self.assertEqual(classify_band(531), "mw")
        self.assertEqual(classify_band(1008), "mw")
        self.assertEqual(classify_band(1611), "mw")
        self.assertEqual(classify_band(6070), "sw")


class TestMediumWave(unittest.TestCase):
    def test_daylight_kills_distant_paths(self):
        # Mittags im Juni traegt die Raumwelle nicht - Solt muss auf null fallen.
        noon = datetime(2026, 6, 21, 13, 0, tzinfo=BERLIN)
        self.assertEqual(score_mw_lw(mw(540, SOLT, 2000), noon, 2, WEIGHTS).total, 0.0)

    def test_the_same_path_lives_at_night(self):
        night = datetime(2026, 12, 21, 22, 0, tzinfo=BERLIN)
        self.assertGreater(score_mw_lw(mw(540, SOLT, 2000), night, 2, WEIGHTS).total, 20.0)

    def test_groundwave_survives_daylight_nearby(self):
        # Flevoland liegt innerhalb der Bodenwellenreichweite und muss auch
        # am Tag einen Wert haben, sonst ist das Tor falsch modelliert.
        noon = datetime(2026, 6, 21, 13, 0, tzinfo=BERLIN)
        self.assertGreater(score_mw_lw(mw(1008, FLEVOLAND, 400), noon, 2, WEIGHTS).total, 0.0)

    def test_more_power_never_hurts(self):
        night = datetime(2026, 12, 21, 22, 0, tzinfo=BERLIN)
        weak = score_mw_lw(mw(540, SOLT, 100), night, 2, WEIGHTS).total
        strong = score_mw_lw(mw(540, SOLT, 2000), night, 2, WEIGHTS).total
        self.assertGreater(strong, weak)

    def test_distance_never_helps(self):
        night = datetime(2026, 12, 21, 22, 0, tzinfo=BERLIN)
        near = score_mw_lw(mw(1008, FLEVOLAND, 500), night, 2, WEIGHTS).total
        far = score_mw_lw(mw(1008, SOLT, 500), night, 2, WEIGHTS).total
        self.assertGreater(near, far)

    def test_field_strength_discriminates_inside_the_reference_distance(self):
        # Regression: eine frueher Fassung klammerte das Verhaeltnis statt
        # der Entfernung und lieferte damit fuer JEDE Strecke unterhalb der
        # Referenzentfernung (500 km) denselben Wert - also flach fuer
        # praktisch alle Stationen, die hier interessant sind.
        values = [_field_strength_proxy(100.0, d, WEIGHTS) for d in (50, 100, 200, 300, 400, 500)]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertGreater(values[0] - values[-1], 0.2)

    def test_field_strength_is_monotonic_across_the_whole_range(self):
        distances = [10, 30, 60, 120, 250, 400, 600, 900, 1500, 3000, 8000]
        values = [_field_strength_proxy(250.0, d, WEIGHTS) for d in distances]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_field_strength_stays_bounded_at_zero_distance(self):
        # Darf weder explodieren noch NaN liefern, wenn ein Sender am Ort steht.
        value = _field_strength_proxy(500.0, 0.0, WEIGHTS)
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 1.0)

    def test_storm_degrades_northern_paths_monotonically(self):
        night = datetime(2026, 12, 21, 22, 0, tzinfo=BERLIN)
        link = mw(198, DROITWICH, 500)
        values = [score_mw_lw(link, night, kp, WEIGHTS).total for kp in range(10)]
        self.assertEqual(values, sorted(values, reverse=True))
        self.assertLess(values[8], values[0])

    def test_summer_is_noisier_than_winter(self):
        # Gleiche Dunkelheit, gleiche Stoerung - nur die Jahreszeit unterscheidet sich.
        link = mw(540, SOLT, 2000)
        july = score_mw_lw(link, datetime(2026, 7, 15, 2, 0, tzinfo=BERLIN), 1, WEIGHTS)
        january = score_mw_lw(link, datetime(2026, 1, 15, 2, 0, tzinfo=BERLIN), 1, WEIGHTS)
        self.assertLess(july.total, january.total)
        self.assertLess(july.components["seasonal_noise"], january.components["seasonal_noise"])

    def test_components_are_reported(self):
        night = datetime(2026, 12, 21, 22, 0, tzinfo=BERLIN)
        result = score_mw_lw(mw(540, SOLT, 2000), night, 2, WEIGHTS)
        for key in ("darkness", "field", "geomagnetic", "seasonal_noise"):
            self.assertIn(key, result.components)


class TestShortWave(unittest.TestCase):
    def test_frequency_far_above_the_muf_is_dead(self):
        night = datetime(2026, 12, 21, 22, 0, tzinfo=BERLIN)
        # 26 MHz nachts auf einer kurzen Strecke: unmoeglich.
        self.assertEqual(score_sw(sw(26000, NAUEN, 100), night, 2, WEIGHTS, f107=110).total, 0.0)

    def test_frequency_below_the_luf_is_dead_by_day(self):
        noon = datetime(2026, 6, 21, 13, 0, tzinfo=BERLIN)
        # 2,3 MHz mittags: die D-Schicht frisst alles.
        self.assertEqual(score_sw(sw(2300, ISSOUDUN, 100), noon, 2, WEIGHTS, f107=110).total, 0.0)

    def test_49m_works_at_night_but_not_at_noon(self):
        link = sw(6070, ISSOUDUN, 100)
        night = score_sw(link, datetime(2026, 12, 21, 21, 0, tzinfo=BERLIN), 2, WEIGHTS, f107=110)
        noon = score_sw(link, datetime(2026, 6, 21, 13, 0, tzinfo=BERLIN), 2, WEIGHTS, f107=110)
        self.assertGreater(night.total, noon.total)

    def test_skip_zone_suppresses_very_close_transmitters(self):
        night = datetime(2026, 12, 21, 21, 0, tzinfo=BERLIN)
        close = Point(50.9, 6.7)  # praktisch vor der Haustuer
        self.assertEqual(score_sw(sw(9600, close, 100), night, 2, WEIGHTS, f107=110).total, 0.0)

    def test_higher_flux_raises_the_muf(self):
        night = datetime(2026, 12, 21, 20, 0, tzinfo=BERLIN)
        link = sw(15000, SOLT, 250)
        quiet = score_sw(link, night, 2, WEIGHTS, f107=70)
        active = score_sw(link, night, 2, WEIGHTS, f107=200)
        self.assertGreater(active.components["muf_mhz"], quiet.components["muf_mhz"])

    def test_luf_is_lower_in_darkness(self):
        self.assertLess(estimate_luf_mhz(1.0, WEIGHTS), estimate_luf_mhz(0.0, WEIGHTS))

    def test_beam_pointing_elsewhere_is_penalised(self):
        night = datetime(2026, 12, 21, 21, 0, tzinfo=BERLIN)
        toward = sw(6070, ISSOUDUN, 100, bearing=30.0)     # Richtung Bergheim
        away = sw(6070, ISSOUDUN, 100, bearing=210.0)      # genau entgegengesetzt
        self.assertGreater(
            score_sw(toward, night, 2, WEIGHTS, f107=110).total,
            score_sw(away, night, 2, WEIGHTS, f107=110).total,
        )

    def test_unknown_beam_is_not_penalised(self):
        night = datetime(2026, 12, 21, 21, 0, tzinfo=BERLIN)
        result = score_sw(sw(6070, ISSOUDUN, 100), night, 2, WEIGHTS, f107=110)
        self.assertEqual(result.components["aiming"], 1.0)


class TestRankingAndSlots(unittest.TestCase):
    def test_rarity_lifts_the_rare_station_above_the_battleship(self):
        night = datetime(2026, 12, 21, 22, 0, tzinfo=BERLIN)
        battleship = score_mw_lw(mw(540, SOLT, 2000), night, 2, WEIGHTS)   # 2 MW, immer da
        rare = score_mw_lw(mw(1008, FLEVOLAND, 400), night, 2, WEIGHTS)
        exponent = WEIGHTS.get("ranking", "rarity_exponent")

        self.assertGreater(battleship.total, 0.0)
        # Nach reiner Empfangbarkeit gewinnt das Dickschiff nicht zwingend,
        # nach Interessantheit muss die seltene Station vorne liegen.
        self.assertGreater(
            rare.with_rarity(0.9, exponent),
            battleship.with_rarity(0.05, exponent),
        )

    def test_rarity_zero_removes_a_station_from_the_interest_ranking(self):
        night = datetime(2026, 12, 21, 22, 0, tzinfo=BERLIN)
        result = score_mw_lw(mw(540, SOLT, 2000), night, 2, WEIGHTS)
        self.assertEqual(result.with_rarity(0.0, 0.6), 0.0)

    def test_best_slot_covers_all_ten_kp_steps(self):
        slots = [
            datetime(2026, 12, 21, 18, 0, tzinfo=BERLIN).replace(hour=18) 
            for _ in range(1)
        ]
        slots = [
            datetime(2026, 12, 21, hour, minute, tzinfo=BERLIN)
            for hour in range(18, 24)
            for minute in (0, 30)
        ]
        result = best_slot_by_kp(mw(198, DROITWICH, 500), slots, WEIGHTS)
        self.assertEqual([entry["kp"] for entry in result], list(range(10)))
        for entry in result:
            self.assertIn(":", entry["t"])
            self.assertGreaterEqual(entry["score"], 0.0)

    def test_best_score_never_rises_with_kp(self):
        slots = [
            datetime(2026, 12, 21, hour, minute, tzinfo=BERLIN)
            for hour in range(18, 24)
            for minute in (0, 30)
        ]
        scores = [entry["score"] for entry in best_slot_by_kp(mw(198, DROITWICH, 500), slots, WEIGHTS)]
        self.assertEqual(scores, sorted(scores, reverse=True))


class TestWeightsFile(unittest.TestCase):
    def test_missing_parameter_fails_loudly(self):
        with self.assertRaises(KeyError):
            WEIGHTS.get("sw", "does_not_exist")

    def test_dispatch_matches_band_class(self):
        night = datetime(2026, 12, 21, 22, 0, tzinfo=BERLIN)
        direct = score_mw_lw(mw(540, SOLT, 2000), night, 2, WEIGHTS).total
        dispatched = score(mw(540, SOLT, 2000), night, 2, WEIGHTS).total
        self.assertEqual(direct, dispatched)


if __name__ == "__main__":
    unittest.main()
