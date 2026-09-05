"""Tests fuer den Torwaechter.

Der eigentliche Beweis steht in TestBothCronLinesAcrossSeasons: dort
laufen genau die beiden UTC-Zeiten durch, die im Workflow eingetragen
sind, einmal im Sommer und einmal im Winter. Von den vier Kombinationen
darf jeweils nur eine durchkommen - sonst kaeme der Push doppelt oder
eine Stunde daneben.
"""

import re
import unittest
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bulletin.guard import (
    BERLIN,
    expected_cron,
    is_expected_local_time,
    main,
    parse_hhmm,
    should_run,
    summer_time_active,
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


class TestScheduleBasedDecision(unittest.TestCase):
    """Entscheidung anhand der ausloesenden Cron-Zeile statt der Uhrzeit.

    Der Anlass: ein Abend ohne Push. Der Lauf war gestartet, aber mit mehr
    als 45 Minuten Verzug - und wurde deshalb still verworfen, obwohl er
    der richtige war. 18:30 UTC ist eine der ueberlaufensten Cron-Zeiten
    ueberhaupt, Verspaetungen sind dort die Regel, nicht die Ausnahme.
    """

    SUMMER = "30 18 * * *"
    WINTER = "30 19 * * *"

    def _run(self, now, schedule):
        return should_run(
            now, target=TARGET, schedule=schedule,
            summer_cron=self.SUMMER, winter_cron=self.WINTER,
        )

    def test_correct_line_passes_even_when_badly_delayed(self):
        # 50 Minuten Verzug - genau der Fall, der vorher durchfiel.
        delayed = utc(2026, 7, 15, 19, 20)
        ok, reason = self._run(delayed, self.SUMMER)
        self.assertTrue(ok, reason)

    def test_correct_line_passes_even_after_two_hours(self):
        very_late = utc(2026, 7, 15, 20, 40)
        self.assertTrue(self._run(very_late, self.SUMMER)[0])

    def test_wrong_line_is_blocked_in_summer(self):
        self.assertFalse(self._run(utc(2026, 7, 15, 19, 30), self.WINTER)[0])

    def test_correct_line_in_winter(self):
        self.assertTrue(self._run(utc(2026, 1, 15, 19, 30), self.WINTER)[0])

    def test_wrong_line_is_blocked_in_winter(self):
        self.assertFalse(self._run(utc(2026, 1, 15, 18, 30), self.SUMMER)[0])

    def test_exactly_one_line_passes_on_every_day_of_the_year(self):
        from datetime import timedelta

        day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        while day.year == 2026:
            passes = sum(
                1 for cron in (self.SUMMER, self.WINTER)
                if self._run(day.replace(hour=18, minute=30), cron)[0]
            )
            self.assertEqual(passes, 1, f"{day.date()}: {passes} Zeilen wuerden feuern")
            day += timedelta(days=1)

    def test_reason_is_informative_in_both_directions(self):
        ok, reason = self._run(utc(2026, 7, 15, 18, 30), self.SUMMER)
        self.assertIn("Sommerzeit", reason)
        ok, reason = self._run(utc(2026, 7, 15, 18, 30), self.WINTER)
        self.assertIn("uebersprungen", reason)

    def test_falls_back_to_the_time_window_without_a_schedule(self):
        # Ohne Angabe der Cron-Zeile bleibt das alte Verhalten.
        ok, _ = should_run(datetime(2026, 9, 3, 20, 35, tzinfo=BERLIN), target=TARGET)
        self.assertTrue(ok)
        ok, _ = should_run(datetime(2026, 9, 3, 21, 40, tzinfo=BERLIN), target=TARGET)
        self.assertFalse(ok)


class TestSummerTimeDetection(unittest.TestCase):
    def test_july_is_summer_time(self):
        self.assertTrue(summer_time_active(utc(2026, 7, 15, 12)))

    def test_january_is_winter_time(self):
        self.assertFalse(summer_time_active(utc(2026, 1, 15, 12)))

    def test_the_changeover_days_are_handled(self):
        # 2026: Umstellung am 29. Maerz und am 25. Oktober, jeweils 01:00 UTC.
        self.assertFalse(summer_time_active(utc(2026, 3, 29, 0, 30)))
        self.assertTrue(summer_time_active(utc(2026, 3, 29, 1, 30)))
        self.assertTrue(summer_time_active(utc(2026, 10, 25, 0, 30)))
        self.assertFalse(summer_time_active(utc(2026, 10, 25, 1, 30)))

    def test_expected_cron_follows_the_season(self):
        self.assertEqual(expected_cron(utc(2026, 7, 15, 12), "S", "W"), "S")
        self.assertEqual(expected_cron(utc(2026, 1, 15, 12), "S", "W"), "W")


class TestWorkflowsMatchTheirGuard(unittest.TestCase):
    """Die Cron-Zeilen im Workflow müssen zu den Argumenten des Wächters passen.

    Seit der Wächter anhand der auslösenden Cron-Zeile entscheidet, stehen
    dieselben Zeitangaben an zwei Stellen derselben Datei. Wer eine ändert
    und die andere vergisst, baut einen Lauf, der sich selbst jeden Tag
    aussperrt - und zwar lautlos, weil das Überspringen kein Fehler ist.
    """

    WORKFLOWS = {
        "notify.yml": ("--summer-cron", "--winter-cron"),
        "build.yml": ("--summer-cron", "--winter-cron"),
    }

    def _read(self, name):
        path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / name
        return path.read_text(encoding="utf-8")

    def _cron_lines(self, text):
        return re.findall(r'-\s*cron:\s*"([^"]+)"', text)

    def _guard_arg(self, text, flag):
        match = re.search(rf'{flag}\s+"([^"]+)"', text)
        return match.group(1) if match else None

    def test_each_workflow_has_exactly_two_cron_lines(self):
        for name in self.WORKFLOWS:
            self.assertEqual(len(self._cron_lines(self._read(name))), 2, name)

    def test_guard_arguments_match_the_cron_lines(self):
        for name in self.WORKFLOWS:
            text = self._read(name)
            crons = set(self._cron_lines(text))
            summer = self._guard_arg(text, "--summer-cron")
            winter = self._guard_arg(text, "--winter-cron")
            self.assertIsNotNone(summer, f"{name}: --summer-cron fehlt")
            self.assertIsNotNone(winter, f"{name}: --winter-cron fehlt")
            self.assertEqual({summer, winter}, crons, f"{name}: Cron-Zeilen und Wächter weichen ab")

    def test_the_two_lines_are_exactly_one_hour_apart(self):
        for name in self.WORKFLOWS:
            crons = self._cron_lines(self._read(name))
            minutes = {c.split()[0] for c in crons}
            hours = sorted(int(c.split()[1]) for c in crons)
            self.assertEqual(len(minutes), 1, f"{name}: unterschiedliche Minuten")
            self.assertEqual(hours[1] - hours[0], 1, f"{name}: nicht eine Stunde auseinander")

    def test_cron_avoids_the_congested_full_and_half_hour(self):
        # Zur vollen und halben Stunde sind Verzögerungen und ausgefallene
        # Läufe am häufigsten - genau deshalb stehen dort krumme Minuten.
        for name in self.WORKFLOWS:
            for cron in self._cron_lines(self._read(name)):
                minute = int(cron.split()[0])
                self.assertNotIn(minute, (0, 30), f"{name}: {cron} liegt auf einer überlaufenen Minute")

    def test_target_time_matches_the_summer_cron(self):
        # --at ist der Rückfallweg; er soll dieselbe Ortszeit meinen wie die
        # Cron-Zeilen, sonst widersprechen sich die beiden Verfahren.
        for name in self.WORKFLOWS:
            text = self._read(name)
            at = self._guard_arg(text, "--at")
            summer = self._guard_arg(text, "--summer-cron")
            minute, hour = summer.split()[0], int(summer.split()[1])
            self.assertEqual(at, f"{hour + 2:02d}:{int(minute):02d}", f"{name}: --at passt nicht zur Cron-Zeile")
