"""Tests fuer die Kugelgeometrie.

Geprueft wird gegen bekannte Wahrheiten, nicht gegen selbst erzeugte
Erwartungswerte - sonst testet man nur, dass der Code tut, was er tut.
"""

import math
import unittest

from bulletin.physics.geometry import (
    BERGHEIM,
    EARTH_RADIUS_KM,
    Point,
    great_circle_distance_km,
    initial_bearing_deg,
    loop_null_bearing_deg,
    midpoint,
    normalize_bearing,
    path_points,
)

# Bekannte Sendestandorte, die uns spaeter ohnehin begegnen
FLEVOLAND = Point(lat=52.3906, lon=5.4433)   # 1008 kHz, Zeewolde/NL
DROITWICH = Point(lat=52.2956, lon=-2.1069)  # 198 kHz, GB
SOLT = Point(lat=46.4319, lon=19.0203)       # 540 kHz, Ungarn


class TestDistance(unittest.TestCase):
    def test_identical_points_have_zero_distance(self):
        self.assertAlmostEqual(great_circle_distance_km(BERGHEIM, BERGHEIM), 0.0)

    def test_one_degree_of_latitude_is_about_111_km(self):
        # Ein Breitengrad entspricht definitionsgemaess 1/360 des Erdumfangs.
        expected = 2 * math.pi * EARTH_RADIUS_KM / 360.0
        actual = great_circle_distance_km(Point(0.0, 0.0), Point(1.0, 0.0))
        self.assertAlmostEqual(actual, expected, places=6)

    def test_quarter_circumference_from_equator_to_pole(self):
        actual = great_circle_distance_km(Point(0.0, 0.0), Point(90.0, 0.0))
        self.assertAlmostEqual(actual, math.pi * EARTH_RADIUS_KM / 2.0, places=6)

    def test_distance_is_symmetric(self):
        there = great_circle_distance_km(BERGHEIM, SOLT)
        back = great_circle_distance_km(SOLT, BERGHEIM)
        self.assertAlmostEqual(there, back, places=9)

    def test_known_paths_from_bergheim(self):
        # Groessenordnungen, die sich unabhaengig auf der Karte pruefen lassen.
        self.assertAlmostEqual(
            great_circle_distance_km(BERGHEIM, FLEVOLAND), 190.0, delta=15.0
        )
        self.assertAlmostEqual(
            great_circle_distance_km(BERGHEIM, DROITWICH), 620.0, delta=30.0
        )
        self.assertAlmostEqual(
            great_circle_distance_km(BERGHEIM, SOLT), 1010.0, delta=40.0
        )


class TestBearing(unittest.TestCase):
    def test_due_north(self):
        self.assertAlmostEqual(
            initial_bearing_deg(Point(0.0, 0.0), Point(10.0, 0.0)), 0.0, places=9
        )

    def test_due_east_along_equator(self):
        self.assertAlmostEqual(
            initial_bearing_deg(Point(0.0, 0.0), Point(0.0, 10.0)), 90.0, places=9
        )

    def test_due_south(self):
        self.assertAlmostEqual(
            initial_bearing_deg(Point(10.0, 0.0), Point(0.0, 0.0)), 180.0, places=9
        )

    def test_due_west(self):
        self.assertAlmostEqual(
            initial_bearing_deg(Point(0.0, 10.0), Point(0.0, 0.0)), 270.0, places=9
        )

    def test_bearing_stays_in_range(self):
        for target in (FLEVOLAND, DROITWICH, SOLT):
            bearing = initial_bearing_deg(BERGHEIM, target)
            self.assertGreaterEqual(bearing, 0.0)
            self.assertLess(bearing, 360.0)

    def test_known_directions_from_bergheim(self):
        # Flevoland liegt nordwestlich, Droitwich westnordwestlich,
        # Solt ostsuedoestlich. Wer hier ein Vorzeichen dreht, faellt auf.
        self.assertAlmostEqual(initial_bearing_deg(BERGHEIM, FLEVOLAND), 340.0, delta=8.0)
        self.assertAlmostEqual(initial_bearing_deg(BERGHEIM, DROITWICH), 292.0, delta=8.0)
        self.assertAlmostEqual(initial_bearing_deg(BERGHEIM, SOLT), 113.0, delta=8.0)

    def test_normalize_handles_negative_and_overflow(self):
        self.assertAlmostEqual(normalize_bearing(-90.0), 270.0)
        self.assertAlmostEqual(normalize_bearing(450.0), 90.0)

    def test_loop_nulls_are_perpendicular(self):
        first, second = loop_null_bearing_deg(328.0)
        self.assertAlmostEqual(first, 58.0)
        self.assertAlmostEqual(second, 238.0)


class TestPathPoints(unittest.TestCase):
    def test_endpoints_are_preserved(self):
        points = path_points(BERGHEIM, SOLT, 11)
        self.assertAlmostEqual(points[0].lat, BERGHEIM.lat, places=6)
        self.assertAlmostEqual(points[0].lon, BERGHEIM.lon, places=6)
        self.assertAlmostEqual(points[-1].lat, SOLT.lat, places=6)
        self.assertAlmostEqual(points[-1].lon, SOLT.lon, places=6)

    def test_points_are_evenly_spaced(self):
        points = path_points(BERGHEIM, SOLT, 9)
        legs = [
            great_circle_distance_km(points[i], points[i + 1])
            for i in range(len(points) - 1)
        ]
        self.assertAlmostEqual(min(legs), max(legs), places=6)

    def test_legs_sum_to_total_distance(self):
        points = path_points(BERGHEIM, DROITWICH, 25)
        total = sum(
            great_circle_distance_km(points[i], points[i + 1])
            for i in range(len(points) - 1)
        )
        direct = great_circle_distance_km(BERGHEIM, DROITWICH)
        self.assertAlmostEqual(total, direct, delta=0.01)

    def test_midpoint_is_equidistant(self):
        mid = midpoint(BERGHEIM, SOLT)
        self.assertAlmostEqual(
            great_circle_distance_km(BERGHEIM, mid),
            great_circle_distance_km(mid, SOLT),
            places=6,
        )

    def test_too_few_points_is_rejected(self):
        with self.assertRaises(ValueError):
            path_points(BERGHEIM, SOLT, 1)


class TestPointValidation(unittest.TestCase):
    def test_impossible_latitude_is_rejected(self):
        with self.assertRaises(ValueError):
            Point(lat=91.0, lon=0.0)


if __name__ == "__main__":
    unittest.main()
