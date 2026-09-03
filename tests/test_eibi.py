"""Tests fuer den EiBi-Sendeplan.

Zwei Dinge stehen im Vordergrund: die Saisonrechnung darf am Umschalttag
nicht daneben liegen, und der Parser darf an einer kaputten Zeile nicht
zerbrechen - eine 30.000-Zeilen-Datei hat immer ein paar Ausreisser.
"""

import unittest
from datetime import date, datetime, time, timedelta, timezone

from bulletin.sources.eibi import (
    Broadcast,
    days_until_season_change,
    parse_days,
    parse_line,
    parse_schedule,
    schedule_url,
    season_code,
)


class TestSeasonCode(unittest.TestCase):
    def test_late_march_2026_is_still_b25(self):
        # Saisonwechsel 2026 ist der 29. Maerz (letzter Sonntag).
        self.assertEqual(season_code(date(2026, 3, 28)), "b25")

    def test_change_day_itself_is_a26(self):
        self.assertEqual(season_code(date(2026, 3, 29)), "a26")

    def test_day_after_is_a26(self):
        self.assertEqual(season_code(date(2026, 3, 30)), "a26")

    def test_summer_is_a26(self):
        self.assertEqual(season_code(date(2026, 7, 15)), "a26")

    def test_late_october_switches_to_b26(self):
        # Letzter Sonntag im Oktober 2026 ist der 25.10.
        self.assertEqual(season_code(date(2026, 10, 24)), "a26")
        self.assertEqual(season_code(date(2026, 10, 25)), "b26")
        self.assertEqual(season_code(date(2026, 10, 26)), "b26")

    def test_new_year_still_belongs_to_previous_b_season(self):
        self.assertEqual(season_code(date(2027, 1, 10)), "b26")

    def test_url_contains_season_code(self):
        self.assertEqual(
            schedule_url(date(2026, 7, 15)), "http://www.eibispace.de/dx/sked-a26.csv"
        )

    def test_days_until_change_counts_down_to_next_boundary(self):
        # 29.03.2026 ist Wechseltag, naechster ist der 25.10.2026.
        remaining = days_until_season_change(date(2026, 9, 3))
        expected = (date(2026, 10, 25) - date(2026, 9, 3)).days
        self.assertEqual(remaining, expected)

    def test_days_until_change_is_zero_only_never_negative(self):
        for day_offset in range(0, 400, 17):
            d = date(2026, 1, 1)
            from datetime import timedelta
            d = d + timedelta(days=day_offset)
            self.assertGreaterEqual(days_until_season_change(d), 0)


class TestParseLine(unittest.TestCase):
    def test_typical_line_parses(self):
        line = "6070;1900-2200;.......;D;CHANNEL 292;D;wEUR;Alt.9955;;;"
        entry = parse_line(line)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.freq_khz, 6070.0)
        self.assertEqual(entry.start_utc, time(19, 0))
        self.assertEqual(entry.end_utc, time(22, 0))
        self.assertEqual(entry.languages, ("de",))
        self.assertEqual(entry.station, "CHANNEL 292")

    def test_comment_lines_are_ignored(self):
        self.assertIsNone(parse_line("; das hier ist ein Kommentar"))
        self.assertIsNone(parse_line("# ebenso"))
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("   "))

    def test_header_line_is_ignored(self):
        self.assertIsNone(parse_line("kHz;Time;Days;ITU;Station;Lang;Target;Remarks"))

    def test_malformed_line_returns_none_not_exception(self):
        self.assertIsNone(parse_line("nicht;genug;felder"))
        self.assertIsNone(parse_line("6070;keinbindestrich;.......;D;X;D;wEUR;"))
        self.assertIsNone(parse_line("6070;19AB-2200;.......;D;X;D;wEUR;"))

    def test_unknown_language_code_maps_to_empty_tuple(self):
        line = "9600;0600-0800;.......;E;WRMI;ZZ;NAm;"
        entry = parse_line(line)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.languages, ())

    def test_bilingual_entry_keeps_both_recognized_languages(self):
        # Channel 292 fuehrt Deutsch und Englisch im Wechsel - das darf
        # nicht komplett unter den Tisch fallen, nur weil "D,E" als
        # Ganzes kein bekannter Einzelcode ist.
        line = "3955;0700-2000;;D;Channel 292;D,E;Eu;r;1;;"
        entry = parse_line(line)
        self.assertEqual(entry.languages, ("de", "en"))

    def test_bilingual_entry_with_one_unknown_code_keeps_the_known_one(self):
        line = "531;0450-1330;;ALG;R.Algiers Int.;A,F;NAf;fk;1;;"
        entry = parse_line(line)
        self.assertEqual(entry.languages, ("fr",))

    def test_midnight_written_as_2400_is_handled(self):
        line = "1485;2200-2400;.......;D;Kall;D;eEUR;"
        entry = parse_line(line)
        self.assertEqual(entry.end_utc, time(23, 59, 59))

    def test_transmitter_site_field_is_captured(self):
        # Feld 8 (Index 7) ist der Senderstandort-Code, z.B. "n" fuer Nauen.
        line = "6070;1900-2200;.......;D;CHANNEL 292;D;wEUR;n;;;"
        entry = parse_line(line)
        self.assertEqual(entry.transmitter_site, "n")

    def test_missing_transmitter_site_field_defaults_to_empty(self):
        line = "6070;1900-2200;.......;D;CHANNEL 292;D;wEUR"
        entry = parse_line(line)
        self.assertEqual(entry.transmitter_site, "")


class TestParseDays(unittest.TestCase):
    def test_empty_string_means_every_day(self):
        self.assertIsNone(parse_days(""))
        self.assertIsNone(parse_days("   "))

    def test_simple_range(self):
        self.assertEqual(parse_days("Mo-Fr"), frozenset({0, 1, 2, 3, 4}))

    def test_adjacent_pair_range(self):
        self.assertEqual(parse_days("Fr-Sa"), frozenset({4, 5}))
        self.assertEqual(parse_days("Su-Mo"), frozenset({6, 0}))

    def test_range_wraps_across_the_week_boundary(self):
        # We-Mo = Mittwoch bis Montag = alles ausser Dienstag.
        self.assertEqual(parse_days("We-Mo"), frozenset({2, 3, 4, 5, 6, 0}))
        self.assertEqual(parse_days("Th-Tu"), frozenset({3, 4, 5, 6, 0, 1}))
        self.assertEqual(parse_days("Su-Fr"), frozenset({6, 0, 1, 2, 3, 4}))

    def test_concatenated_pair(self):
        self.assertEqual(parse_days("SaSu"), frozenset({5, 6}))

    def test_concatenated_triple(self):
        self.assertEqual(parse_days("MoTuWe"), frozenset({0, 1, 2}))

    def test_single_day(self):
        self.assertEqual(parse_days("Sa"), frozenset({5}))

    def test_comma_separated_list(self):
        self.assertEqual(parse_days("Tu,Th"), frozenset({1, 3}))

    def test_comma_can_mix_ranges_and_single_days(self):
        self.assertEqual(parse_days("Mo-We,Fr"), frozenset({0, 1, 2, 4}))

    def test_digit_notation(self):
        # README-Beispiel: "1245" = Montag, Dienstag, Donnerstag, Freitag.
        self.assertEqual(parse_days("1245"), frozenset({0, 1, 3, 4}))

    def test_digit_notation_from_real_sample(self):
        self.assertEqual(parse_days("13567"), frozenset({0, 2, 4, 5, 6}))

    def test_irregular_and_special_comments_are_unrestricted(self):
        for code in ("irr", "alt", "altFr", "harm", "imod", "Haj", "Ram", "tent", "test", "LSB", "USB"):
            self.assertIsNone(parse_days(code), f"{code!r} sollte als unbeschraenkt gelten")

    def test_calendar_bound_special_cases_are_unrestricted(self):
        # Diese brauchen echte Kalenderlogik (Monatstag/Wochenzaehlung),
        # die wir bewusst nicht abbilden - siehe Docstring von parse_days.
        for code in ("1.Sa", "1WeFr", "Last7", "MF-15", "15Sep"):
            self.assertIsNone(parse_days(code), f"{code!r} sollte als unbeschraenkt gelten")

    def test_garbage_input_falls_back_to_unrestricted_rather_than_raising(self):
        self.assertIsNone(parse_days("???"))
        self.assertIsNone(parse_days("XyZq"))


class TestIsOnAirWithWeekday(unittest.TestCase):
    def test_restricted_to_weekend_excludes_a_weekday(self):
        b = Broadcast(6070, time(19, 0), time(22, 0), "T", ("de",), "D", "eEUR", days="SaSu")
        monday = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)  # ein Montag
        saturday = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)  # ein Samstag
        self.assertFalse(b.is_on_air(monday))
        self.assertTrue(b.is_on_air(saturday))

    def test_midnight_crossing_broadcast_attributes_to_its_start_day(self):
        # Sendung Freitag-Samstag, 22:00-02:00 UTC. Um 01:00 UTC am
        # Samstag hat sie tatsaechlich schon FREITAG begonnen - das muss
        # als "an" gelten, obwohl "when" schon auf einen Sonntag faellt,
        # wenn die Tage nur Fr-Sa waeren und man den Prüfzeitpunkt naiv nehmen würde.
        b = Broadcast(6070, time(22, 0), time(2, 0), "T", ("de",), "D", "eEUR", days="Fr-Sa")
        # 2026-09-05 ist ein Samstag; 01:00 UTC an diesem Tag gehoert zur
        # Sendung, die am Freitag (2026-09-04) um 22:00 UTC begann.
        early_saturday = datetime(2026, 9, 5, 1, 0, tzinfo=timezone.utc)
        self.assertTrue(b.is_on_air(early_saturday))

    def test_midnight_crossing_broadcast_excludes_a_day_it_does_not_start_on(self):
        # Nur Mo-Di erlaubt. Um 01:00 UTC am Donnerstag waere der Start-Tag
        # Mittwoch - nicht in der erlaubten Menge.
        b = Broadcast(6070, time(22, 0), time(2, 0), "T", ("de",), "D", "eEUR", days="Mo-Tu")
        early_thursday = datetime(2026, 9, 3, 1, 0, tzinfo=timezone.utc)
        self.assertFalse(b.is_on_air(early_thursday))

    def test_unrestricted_days_field_runs_every_day(self):
        b = Broadcast(6070, time(19, 0), time(22, 0), "T", ("de",), "D", "eEUR", days="")
        for day_offset in range(7):
            when = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc) + timedelta(days=day_offset)
            self.assertTrue(b.is_on_air(when))

    def test_wrong_time_of_day_is_still_excluded_regardless_of_weekday(self):
        b = Broadcast(6070, time(19, 0), time(22, 0), "T", ("de",), "D", "eEUR", days="")
        wrong_time_on_allowed_day = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
        self.assertFalse(b.is_on_air(wrong_time_on_allowed_day))



    def test_simple_evening_slot(self):
        b = Broadcast(6070, time(19, 0), time(22, 0), "T", ("de",), "D", "eEUR")
        self.assertTrue(b.is_on_air(datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc)))
        self.assertFalse(b.is_on_air(datetime(2026, 9, 3, 22, 30, tzinfo=timezone.utc)))

    def test_slot_crossing_midnight(self):
        b = Broadcast(9600, time(22, 0), time(2, 0), "T", ("en",), "F", "wEUR")
        self.assertTrue(b.crosses_midnight)
        self.assertTrue(b.is_on_air(datetime(2026, 9, 3, 23, 30, tzinfo=timezone.utc)))
        self.assertTrue(b.is_on_air(datetime(2026, 9, 4, 1, 0, tzinfo=timezone.utc)))
        self.assertFalse(b.is_on_air(datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc)))

    def test_local_time_is_converted_to_utc(self):
        from zoneinfo import ZoneInfo
        berlin = ZoneInfo("Europe/Berlin")
        b = Broadcast(6070, time(19, 0), time(22, 0), "T", ("de",), "D", "eEUR")
        # 21:30 Uhr Sommerzeit (UTC+2) entspricht 19:30 UTC - noch innerhalb.
        self.assertTrue(b.is_on_air(datetime(2026, 9, 3, 21, 30, tzinfo=berlin)))


class TestParseSchedule(unittest.TestCase):
    SAMPLE = "\n".join([
        "kHz;Time;Days;ITU;Station;Lang;Target;Remarks",
        "; Kommentarzeile",
        "6070;1900-2200;.......;D;CHANNEL 292;D;wEUR;",
        "9600;0600-0900;.......;F;RFI;F;wAFR;",
        "15000;1200-1400;.......;E;BBC;E;eEUR;",
        "7200;1000-1200;.......;E;VOA;ZZ;eAFR;",  # unbekannte Sprache
        "3955;0700-2000;;D;Channel 292 (echt);D,E;Eu;r;1;;",  # zweisprachig
        "kaputte;zeile",
    ])

    def test_filters_to_wanted_languages_by_default(self):
        entries = parse_schedule(self.SAMPLE)
        self.assertEqual(len(entries), 4)
        self.assertEqual(
            {e.station for e in entries},
            {"CHANNEL 292", "RFI", "BBC", "Channel 292 (echt)"},
        )

    def test_languages_none_keeps_everything_parseable(self):
        entries = parse_schedule(self.SAMPLE, languages=None)
        self.assertEqual(len(entries), 5)

    def test_custom_language_filter(self):
        entries = parse_schedule(self.SAMPLE, languages=("fr",))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].station, "RFI")

    def test_bilingual_entry_matches_either_of_its_languages(self):
        # Nur Englisch gewuenscht: der zweisprachige Eintrag muss trotzdem
        # durchkommen, weil "en" eine seiner beiden Sprachen ist.
        entries = parse_schedule(self.SAMPLE, languages=("en",))
        self.assertIn("Channel 292 (echt)", {e.station for e in entries})

    def test_broken_line_does_not_abort_parsing(self):
        entries = parse_schedule(self.SAMPLE)
        # Die letzte Zeile ist kaputt, trotzdem kommen die guten durch.
        self.assertEqual(len(entries), 4)


if __name__ == "__main__":
    unittest.main()
