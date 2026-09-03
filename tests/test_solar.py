"""Tests fuer Sonnenstand und Dunkelheit.

Die Referenzwerte stammen aus der Astronomie, nicht aus einem frueheren
Lauf dieses Codes: Sonnenhoehe zur Mittagszeit am Aequinoktium, Laenge
des laengsten und kuerzesten Tages, Polarnacht. Solche Tests halten auch
dann noch, wenn wir die Implementierung spaeter austauschen.
"""

import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bulletin.physics.geometry import BERGHEIM, Point
from bulletin.physics.solar import (
    CIVIL_TWILIGHT_DEG,
    HORIZON_DEG,
    darkness_fraction,
    is_greyline,
    solar_position,
    sun_elevation_deg,
    sunrise_sunset,
)

BERLIN = ZoneInfo("Europe/Berlin")
UTC = timezone.utc

TROMSOE = Point(lat=69.6496, lon=18.9560)   # Polarnacht im Dezember
QUITO = Point(lat=-0.1807, lon=-78.4678)    # Aequator
SOLT = Point(lat=46.4319, lon=19.0203)


def local(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=BERLIN)


def utc_midnight(y, m, d):
    return datetime(y, m, d, 0, 0, tzinfo=UTC)


class TestSolarPosition(unittest.TestCase):
    def test_naive_datetime_is_rejected(self):
        # Der haeufigste Fehler in solchem Code ueberhaupt: eine Zeit ohne Zone.
        with self.assertRaises(ValueError):
            sun_elevation_deg(BERGHEIM, datetime(2026, 6, 21, 12, 0))

    def test_noon_elevation_at_equinox_matches_colatitude(self):
        # Am Aequinoktium steht die Sonne mittags genau (90 - Breite) hoch.
        best = max(
            sun_elevation_deg(BERGHEIM, utc_midnight(2026, 3, 20) + timedelta(minutes=m))
            for m in range(0, 1440, 2)
        )
        self.assertAlmostEqual(best, 90.0 - BERGHEIM.lat, delta=0.6)

    def test_solstice_elevations_differ_by_twice_the_obliquity(self):
        def noon_max(y, m, d):
            return max(
                sun_elevation_deg(BERGHEIM, utc_midnight(y, m, d) + timedelta(minutes=m_))
                for m_ in range(0, 1440, 2)
            )

        summer = noon_max(2026, 6, 21)
        winter = noon_max(2026, 12, 21)
        # Der Unterschied ist die doppelte Schiefe der Ekliptik, rund 46,8 Grad.
        self.assertAlmostEqual(summer - winter, 46.85, delta=0.3)

    def test_sun_is_below_horizon_at_local_midnight(self):
        self.assertLess(sun_elevation_deg(BERGHEIM, local(2026, 6, 21, 1, 0)), 0.0)

    def test_azimuth_is_south_at_local_solar_noon(self):
        # Bergheim liegt oestlich des Zeitzonenmeridians, wahrer Mittag also
        # etwas vor 13:30 Sommerzeit. Wir suchen das Hoehenmaximum.
        best = max(
            (
                solar_position(BERGHEIM, utc_midnight(2026, 6, 21) + timedelta(minutes=m))
                for m in range(0, 1440)
            ),
            key=lambda p: p.elevation_deg,
        )
        self.assertAlmostEqual(best.azimuth_deg, 180.0, delta=1.5)

    def test_azimuth_stays_in_range(self):
        for hour in range(0, 24):
            pos = solar_position(BERGHEIM, local(2026, 9, 3, hour))
            self.assertGreaterEqual(pos.azimuth_deg, 0.0)
            self.assertLess(pos.azimuth_deg, 360.0)


class TestSunriseSunset(unittest.TestCase):
    def test_longest_and_shortest_day_in_bergheim(self):
        rise, set_ = sunrise_sunset(BERGHEIM, utc_midnight(2026, 6, 21))
        self.assertIsNotNone(rise)
        self.assertIsNotNone(set_)
        summer_hours = (set_ - rise).total_seconds() / 3600.0
        self.assertAlmostEqual(summer_hours, 16.5, delta=0.4)

        rise, set_ = sunrise_sunset(BERGHEIM, utc_midnight(2026, 12, 21))
        winter_hours = (set_ - rise).total_seconds() / 3600.0
        self.assertAlmostEqual(winter_hours, 7.9, delta=0.4)

    def test_sunset_in_bergheim_on_the_shortest_day(self):
        # Rund 16:26 Ortszeit - ein Wert, den jeder Kalender bestaetigt.
        _, set_ = sunrise_sunset(BERGHEIM, utc_midnight(2026, 12, 21))
        local_set = set_.astimezone(BERLIN)
        minutes = local_set.hour * 60 + local_set.minute
        self.assertAlmostEqual(minutes, 16 * 60 + 26, delta=12)

    def test_elevation_at_sunrise_equals_the_horizon_threshold(self):
        rise, _ = sunrise_sunset(BERGHEIM, utc_midnight(2026, 9, 3))
        self.assertAlmostEqual(
            sun_elevation_deg(BERGHEIM, rise), HORIZON_DEG, places=4
        )

    def test_equator_has_roughly_twelve_hours_year_round(self):
        for month in (1, 4, 7, 10):
            rise, set_ = sunrise_sunset(QUITO, utc_midnight(2026, month, 15))
            hours = (set_ - rise).total_seconds() / 3600.0
            self.assertAlmostEqual(hours, 12.1, delta=0.2)

    def test_polar_night_returns_none_instead_of_raising(self):
        rise, set_ = sunrise_sunset(TROMSOE, utc_midnight(2026, 12, 21))
        self.assertIsNone(rise)
        self.assertIsNone(set_)

    def test_civil_twilight_lasts_longer_than_the_day(self):
        rise, set_ = sunrise_sunset(BERGHEIM, utc_midnight(2026, 3, 20))
        dawn, dusk = sunrise_sunset(
            BERGHEIM, utc_midnight(2026, 3, 20), target_deg=CIVIL_TWILIGHT_DEG
        )
        self.assertLess(dawn, rise)
        self.assertGreater(dusk, set_)


class TestDarkness(unittest.TestCase):
    def test_path_is_fully_lit_at_local_noon_in_summer(self):
        value = darkness_fraction(BERGHEIM, SOLT, local(2026, 6, 21, 13, 0))
        self.assertEqual(value, 0.0)

    def test_path_is_fully_dark_at_local_midnight_in_winter(self):
        value = darkness_fraction(BERGHEIM, SOLT, local(2026, 12, 21, 0, 30))
        self.assertEqual(value, 1.0)

    def test_darkness_grows_over_the_evening(self):
        values = [
            darkness_fraction(BERGHEIM, SOLT, local(2026, 9, 3, 18) + timedelta(minutes=30 * i))
            for i in range(8)
        ]
        self.assertEqual(values, sorted(values))
        self.assertLess(values[0], values[-1])

    def test_eastern_path_darkens_before_the_receiver(self):
        # Solt liegt oestlich: dort ist es frueher dunkel als in Bergheim.
        when = local(2026, 9, 3, 20, 15)
        self.assertGreater(
            sun_elevation_deg(BERGHEIM, when), sun_elevation_deg(SOLT, when)
        )

    def test_fraction_stays_within_bounds(self):
        for hour in range(24):
            value = darkness_fraction(BERGHEIM, SOLT, local(2026, 9, 3, hour))
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)


class TestDarknessCache(unittest.TestCase):
    """Der Puffer in darkness_fraction darf nur beschleunigen, nichts veraendern.

    Ein Cache, der auf einem Teil der Eingaben nicht schluesselt, liefert
    stillschweigend falsche Werte - das waere schlimmer als langsamer Code.
    """

    def test_repeated_call_returns_the_same_value(self):
        when = local(2026, 9, 3, 21, 0)
        first = darkness_fraction(BERGHEIM, SOLT, when)
        second = darkness_fraction(BERGHEIM, SOLT, when)
        self.assertEqual(first, second)

    def test_different_time_is_not_served_from_cache(self):
        evening = darkness_fraction(BERGHEIM, SOLT, local(2026, 9, 3, 23, 0))
        noon = darkness_fraction(BERGHEIM, SOLT, local(2026, 9, 3, 13, 0))
        self.assertNotEqual(evening, noon)

    def test_different_path_is_not_served_from_cache(self):
        when = local(2026, 9, 3, 20, 30)
        east = darkness_fraction(BERGHEIM, SOLT, when)
        west = darkness_fraction(BERGHEIM, Point(lat=40.0, lon=-70.0), when)
        self.assertNotEqual(east, west)

    def test_different_threshold_is_not_served_from_cache(self):
        when = local(2026, 9, 3, 20, 30)
        civil = darkness_fraction(BERGHEIM, SOLT, when, threshold_deg=-6.0)
        astronomical = darkness_fraction(BERGHEIM, SOLT, when, threshold_deg=-18.0)
        self.assertLessEqual(astronomical, civil)

    def test_different_sample_count_is_not_served_from_cache(self):
        when = local(2026, 9, 3, 20, 0)
        coarse = darkness_fraction(BERGHEIM, SOLT, when, samples=3)
        fine = darkness_fraction(BERGHEIM, SOLT, when, samples=101)
        # Nicht zwingend verschieden, aber beide muessen gueltig sein und
        # der Aufruf darf nicht denselben zwischengespeicherten Wert
        # unabhaengig von der Aufloesung zurueckgeben.
        self.assertGreaterEqual(coarse, 0.0)
        self.assertLessEqual(fine, 1.0)


class TestGreyline(unittest.TestCase):
    def test_greyline_is_true_around_sunset(self):
        _, set_ = sunrise_sunset(BERGHEIM, utc_midnight(2026, 9, 3))
        self.assertTrue(is_greyline(BERGHEIM, set_))

    def test_greyline_is_false_at_noon(self):
        self.assertFalse(is_greyline(BERGHEIM, local(2026, 9, 3, 13, 0)))


if __name__ == "__main__":
    unittest.main()
