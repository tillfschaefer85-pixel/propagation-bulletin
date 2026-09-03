"""Tests fuer den Torwaechter.

Der eigentliche Beweis steht in TestBothCronLinesAcrossSeasons: dort
laufen genau die beiden UTC-Zeiten durch, die im Workflow eingetragen
sind, einmal im Sommer und einmal im Winter. Von den vier Kombinationen
darf jeweils nur eine durchkommen - sonst kaeme der Push doppelt oder
eine Stunde daneben.
"""

import unittest
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from bulletin.guard import (
    BERLIN,
    is_expected_local_time,
    main,
    parse_hhmm,
)

TARGET = time(20, 30)


def utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TestParseHhmm(unittest.TestCase):
    def test_parses_a_normal_time(self):
        self.assertEqual(parse_hhmm("20:30"), time(20, 30))

    def test_parses_midnight(self):
        self.assertEqual(parse_hhmm("00:00"), time(0, 0))


class TestWindow(unittest.TestCase):
    def test_exactly_on_target_is_accepted(self):
        now = datetime(2026, 9, 3, 20, 30, tzinfo=BERLIN)
        self.assertTrue(is_expected_local_time(TARGET, now))

    def test_slightly_late_is_accepted(self):
        # GitHub startet geplante Laeufe regelmaessig verspaetet.
        for minutes in (5, 15, 30, 44):
            now = datetime(2026, 9, 3, 20, 30, tzinfo=BERLIN).replace(
                hour=20 + (30 + minutes) // 60, minute=(30 + minutes) % 60
            )
            self.assertTrue(is_expected_local_time(TARGET, now), f"+{minutes} min")

    def test_too_late_is_rejected(self):
        now = datetime(2026, 9, 3, 21, 20, tzinfo=BERLIN)  # +50 min
        self.assertFalse(is_expected_local_time(TARGET, now))

    def test_early_is_rejected_because_cron_never_fires_early(self):
        now = datetime(2026, 9, 3, 20, 25, tzinfo=BERLIN)
        self.assertFalse(is_expected_local_time(TARGET, now))

    def test_one_hour_off_is_rejected_in_both_directions(self):
        self.assertFalse(is_expected_local_time(TARGET, datetime(2026, 9, 3, 19, 30, tzinfo=BERLIN)))
        self.assertFalse(is_expected_local_time(TARGET, datetime(2026, 9, 3, 21, 30, tzinfo=BERLIN)))

    def test_tolerance_of_sixty_or_more_is_refused_as_unsafe(self):
        # Ein 60-Minuten-Fenster wuerde die jeweils andere Cron-Zeile
        # mit durchlassen - genau der Fehler, den der Waechter verhindern soll.
        with self.assertRaises(ValueError):
            is_expected_local_time(TARGET, datetime(2026, 9, 3, 20, 30, tzinfo=BERLIN), tolerance_minutes=60)

    def test_window_across_midnight(self):
        target = time(23, 50)
        self.assertTrue(is_expected_local_time(target, datetime(2026, 9, 4, 0, 10, tzinfo=BERLIN)))
        self.assertFalse(is_expected_local_time(target, datetime(2026, 9, 4, 1, 0, tzinfo=BERLIN)))


class TestBothCronLinesAcrossSeasons(unittest.TestCase):
    """Die beiden Cron-Zeilen aus notify.yml, in beiden Jahreszeiten.

    Sommerzeit ist UTC+2, Winterzeit UTC+1. Von den vier Kombinationen
    darf genau die Haelfte durchkommen.
    """

    SUMMER_CRON_UTC = 18  # 18:30 UTC
    WINTER_CRON_UTC = 19  # 19:30 UTC

    def test_summer_cron_passes_in_summer(self):
        now = utc(2026, 7, 15, self.SUMMER_CRON_UTC, 30)  # = 20:30 Ortszeit
        self.assertTrue(is_expected_local_time(TARGET, now))

    def test_winter_cron_is_blocked_in_summer(self):
        now = utc(2026, 7, 15, self.WINTER_CRON_UTC, 30)  # = 21:30 Ortszeit
        self.assertFalse(is_expected_local_time(TARGET, now))

    def test_winter_cron_passes_in_winter(self):
        now = utc(2026, 1, 15, self.WINTER_CRON_UTC, 30)  # = 20:30 Ortszeit
        self.assertTrue(is_expected_local_time(TARGET, now))

    def test_summer_cron_is_blocked_in_winter(self):
        now = utc(2026, 1, 15, self.SUMMER_CRON_UTC, 30)  # = 19:30 Ortszeit
        self.assertFalse(is_expected_local_time(TARGET, now))

    def test_exactly_one_cron_line_fires_on_any_day_of_the_year(self):
        # Der eigentliche Regressionstest: an keinem Tag des Jahres
        # duerfen beide Zeilen durchkommen (Push doppelt) oder keine
        # (Push faellt aus).
        from datetime import timedelta

        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        while day.year == 2026:
            passes = sum(
                1
                for hour in (self.SUMMER_CRON_UTC, self.WINTER_CRON_UTC)
                if is_expected_local_time(TARGET, day.replace(hour=hour, minute=30))
            )
            self.assertEqual(passes, 1, f"{day.date()}: {passes} Cron-Zeilen wuerden feuern")
            day += timedelta(days=1)

    def test_delayed_run_still_fires_exactly_once(self):
        # Auch mit 20 Minuten Verspaetung darf sich das Bild nicht drehen.
        from datetime import timedelta

        for month, expected_hour in ((7, self.SUMMER_CRON_UTC), (1, self.WINTER_CRON_UTC)):
            base = datetime(2026, month, 15, tzinfo=timezone.utc)
            passes = [
                hour
                for hour in (self.SUMMER_CRON_UTC, self.WINTER_CRON_UTC)
                if is_expected_local_time(TARGET, base.replace(hour=hour, minute=50))
            ]
            self.assertEqual(passes, [expected_hour], f"Monat {month}: {passes}")


class TestMainExitCodes(unittest.TestCase):
    def test_returns_nonzero_when_the_time_does_not_match(self):
        # Ohne Zeitreise laesst sich nur die "passt nicht"-Seite sicher
        # pruefen: eine Zielzeit, die garantiert nicht jetzt ist.
        now_local = datetime.now(BERLIN)
        far_away = (now_local.hour + 6) % 24
        self.assertEqual(main(["--at", f"{far_away:02d}:00"]), 1)

    def test_returns_zero_when_the_time_matches(self):
        now_local = datetime.now(BERLIN)
        self.assertEqual(main(["--at", f"{now_local:%H:%M}"]), 0)


if __name__ == "__main__":
    unittest.main()
