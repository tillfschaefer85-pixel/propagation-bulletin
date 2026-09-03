"""Kurzwellen-Sendeplan von EiBi.

Die Liste erscheint halbjaehrlich in zwei Saisons, die den Umstellungen
der Sommerzeit folgen: A-Saison vom letzten Sonntag im Maerz bis zum
letzten Sonntag im Oktober, danach B-Saison. Die Datei traegt den
Saisoncode im Namen, etwa sked-a26.csv.

Genau hier liegt die Falle, die man erst im Herbst bemerkt: Wer den
Dateinamen einmal fest eintraegt, zeigt ab Ende Oktober stillschweigend
veraltete Sendezeiten an. Deshalb wird die Saison bei jedem Lauf aus dem
Datum bestimmt und beim Wechsel die neue Datei geholt.

Die Nutzungsbedingungen der Datei erlauben freies Kopieren und
Weitergeben - anders als bei den Mittelwellendaten, die wir deshalb
selbst pflegen.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .http import Fetched, fetch_text

BASE_URL = "http://www.eibispace.de/dx/"

# Wochentagsabkuerzungen von EiBi, auf Pythons datetime.weekday()-Zaehlung
# abgebildet (Montag=0 .. Sonntag=6) - praktischerweise dieselbe Ordnung,
# in der auch die README ihre Ziffern-Schreibweise "1245" zaehlt (1=Montag).
_DAY_ABBR: dict[str, int] = {"Mo": 0, "Tu": 1, "We": 2, "Th": 3, "Fr": 4, "Sa": 5, "Su": 6}
_DAY_DIGIT: dict[str, int] = {str(i + 1): i for i in range(7)}


def _range_weekdays(start_tok: str, end_tok: str) -> frozenset[int] | None:
    """Wochentage eines Bereichs wie 'Mo-Fr' oder 'We-Mo', mit Wochenumbruch.

    'We-Mo' bedeutet Mittwoch bis Montag - also alles ausser Dienstag.
    Ist der Starttag spaeter in der Woche als der Endtag, wird ueber
    Sonntag hinweg gezaehlt, statt einen leeren Bereich zu liefern.
    """
    start = _DAY_ABBR.get(start_tok.title())
    end = _DAY_ABBR.get(end_tok.title())
    if start is None or end is None:
        return None
    if start <= end:
        return frozenset(range(start, end + 1))
    return frozenset(list(range(start, 7)) + list(range(0, end + 1)))


def _concatenated_weekdays(token: str) -> frozenset[int] | None:
    """Aneinandergehaengte Zweierkuerzel wie 'SaSu' oder 'MoTuWe'.

    Jede Zweierpaarung muss eine bekannte Abkuerzung sein - schon eine
    einzige ungueltige Paarung laesst die ganze Zeile als unparsebar
    gelten (siehe parse_days: das faellt dann auf "keine Einschraenkung"
    zurueck, statt einen Teil falsch zu deuten).
    """
    if len(token) % 2 != 0:
        return None
    days: set[int] = set()
    for i in range(0, len(token), 2):
        day = _DAY_ABBR.get(token[i : i + 2].title())
        if day is None:
            return None
        days.add(day)
    return frozenset(days)


def _digit_weekdays(token: str) -> frozenset[int] | None:
    """Ziffernschreibweise wie '1245' (Montag, Dienstag, Donnerstag, Freitag)."""
    days: set[int] = set()
    for ch in token:
        day = _DAY_DIGIT.get(ch)
        if day is None:
            return None
        days.add(day)
    return frozenset(days)


def parse_days(days: str) -> frozenset[int] | None:
    """Wandelt EiBis Tage-Feld in eine Menge von Wochentagen (0=Mo..6=So).

    Gibt None zurueck, wenn das Feld leer ist ODER wenn es sich nicht als
    reine Wochentagsangabe deuten laesst - das deckt neben "taeglich" auch
    alle Sonderkommentare ab, die dasselbe Feld ueberladen (irr, alt, harm,
    imod, Haj, Ram, tent, test, LSB, USB) sowie kalendertag-gebundene Faelle
    wie "1.Sa" (erster Samstag im Monat), "Last7" (letzter Sonntag) oder
    "MF-15" (Mo-Fr, aber nur bis zum 15. des Monats). Diese Faelle wuerden
    echte Kalenderlogik brauchen, die wir hier nicht abbilden - eine
    Sendung mit einem solchen Eintrag gilt deshalb bewusst als an jedem
    Wochentag moeglich, statt faelschlich ausgeschlossen zu werden.
    """
    days = days.strip()
    if not days:
        return None

    result: set[int] = set()
    for part in days.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_tok, _, end_tok = part.partition("-")
            parsed = _range_weekdays(start_tok, end_tok)
        elif part.isdigit():
            parsed = _digit_weekdays(part)
        else:
            parsed = _concatenated_weekdays(part)
        if parsed is None:
            return None  # ein einziger unklarer Teil macht die ganze Angabe unklar
        result |= parsed

    return frozenset(result) if result else None

# EiBi-Sprachschluessel. ACHTUNG: diese Zuordnung ist gegen die
# Schluesseldatei von EiBi zu pruefen, bevor der erste echte Lauf zaehlt -
# ein falscher Code filtert stillschweigend die Haelfte weg.
LANGUAGE_CODES: dict[str, str] = {
    "D": "de",
    "E": "en",
    "F": "fr",
    "NL": "nl",
}

# Umkehrung fuer den Filter
WANTED_LANGUAGES = ("de", "en", "fr", "nl")


def _last_sunday(year: int, month: int) -> date:
    """Letzter Sonntag eines Monats - der Termin der Saisonwechsel."""
    last_day = calendar.monthrange(year, month)[1]
    day = date(year, month, last_day)
    return day - timedelta(days=(day.weekday() - 6) % 7)


def season_code(for_date: date) -> str:
    """Saisoncode wie 'a26' oder 'b26' fuer ein Datum.

    Die B-Saison laeuft ueber den Jahreswechsel: der 10. Januar 2027
    gehoert noch zur Saison b26.
    """
    year = for_date.year
    start_a = _last_sunday(year, 3)
    start_b = _last_sunday(year, 10)

    if for_date < start_a:
        # Noch B-Saison des Vorjahres
        return f"b{str(year - 1)[-2:]}"
    if for_date < start_b:
        return f"a{str(year)[-2:]}"
    return f"b{str(year)[-2:]}"


def schedule_url(for_date: date) -> str:
    return f"{BASE_URL}sked-{season_code(for_date)}.csv"


def days_until_season_change(for_date: date) -> int:
    """Tage bis zum naechsten Saisonwechsel - fuer eine Vorwarnung im Bulletin."""
    year = for_date.year
    candidates = [
        _last_sunday(year, 3),
        _last_sunday(year, 10),
        _last_sunday(year + 1, 3),
    ]
    for candidate in candidates:
        if candidate > for_date:
            return (candidate - for_date).days
    return 0


@dataclass(frozen=True)
class Broadcast:
    """Eine Sendung aus dem Fahrplan."""

    freq_khz: float
    start_utc: time
    end_utc: time
    station: str
    languages: tuple[str, ...]
    itu: str
    target: str
    days: str = ""
    transmitter_site: str = ""

    @property
    def crosses_midnight(self) -> bool:
        return self.end_utc <= self.start_utc

    @property
    def weekdays(self) -> frozenset[int] | None:
        """Erlaubte Wochentage (0=Montag..6=Sonntag), oder None fuer 'jeden Tag'."""
        return parse_days(self.days)

    def is_on_air(self, when: datetime) -> bool:
        """Laeuft die Sendung zu diesem Zeitpunkt?

        Sendungen ueber Mitternacht sind der Normalfall im Abendprogramm
        und muessen gesondert behandelt werden - sonst verschwindet
        genau das, was Du hoeren willst. Der massgebliche Wochentag ist
        dabei immer der Tag, an dem die Sendung BEGANN: liegt "when"
        schon nach Mitternacht, aber noch vor dem Sendeschluss, hat die
        Sendung tatsaechlich gestern angefangen.
        """
        when_utc = when.astimezone(timezone.utc)
        moment = when_utc.time()

        if self.crosses_midnight:
            on_air = moment >= self.start_utc or moment < self.end_utc
            start_day = when_utc.date() if moment >= self.start_utc else when_utc.date() - timedelta(days=1)
        else:
            on_air = self.start_utc <= moment < self.end_utc
            start_day = when_utc.date()

        if not on_air:
            return False

        weekdays = self.weekdays
        return weekdays is None or start_day.weekday() in weekdays


def _parse_hhmm(raw: str) -> time:
    raw = raw.strip()
    if len(raw) != 4 or not raw.isdigit():
        raise ValueError(f"Ungueltige Zeitangabe: {raw!r}")
    hour, minute = int(raw[:2]), int(raw[2:])
    if hour == 24:  # EiBi schreibt das Tagesende als 2400
        return time(23, 59, 59)
    return time(hour, minute)


def parse_line(line: str) -> Broadcast | None:
    """Eine Zeile der Sked-Datei.

    Feldreihenfolge laut Formatbeschreibung:
        kHz;Zeit;Tage;ITU;Station;Sprache;Zielgebiet;Bemerkung;P;Start;Stop

    Unbrauchbare Zeilen geben None zurueck statt zu werfen: eine einzige
    kaputte Zeile in einer 30.000-Zeilen-Datei darf den Lauf nicht kippen.
    """
    line = line.strip()
    if not line or line.startswith((";", "#")):
        return None

    fields = line.split(";")
    if len(fields) < 7:
        return None
    if not fields[0].strip().replace(".", "").isdigit():
        return None  # Kopfzeile oder Kommentar

    try:
        freq = float(fields[0])
        span = fields[1].strip()
        if "-" not in span:
            return None
        start_raw, end_raw = span.split("-", 1)
        start, end = _parse_hhmm(start_raw), _parse_hhmm(end_raw)
    except ValueError:
        return None

    code = fields[5].strip()
    # Manche Sendungen fuehren mehrere Sprachen zugleich, getrennt durch
    # Komma (z.B. "D,E" fuer Deutsch und Englisch abwechselnd). Frueher
    # wurde ein solcher Eintrag komplett verworfen, weil "D,E" als Ganzes
    # kein bekannter Code ist - dabei ist Channel 292 auf 6070 kHz genau
    # so eingetragen und gehoert eindeutig zur Zielgruppe.
    languages = tuple(
        LANGUAGE_CODES[part]
        for part in (p.strip() for p in code.split(","))
        if part in LANGUAGE_CODES
    )
    return Broadcast(
        freq_khz=freq,
        start_utc=start,
        end_utc=end,
        days=fields[2].strip(),
        itu=fields[3].strip(),
        station=fields[4].strip(),
        languages=languages,
        target=fields[6].strip(),
        transmitter_site=fields[7].strip() if len(fields) > 7 else "",
    )


def parse_schedule(text: str, *, languages: tuple[str, ...] | None = WANTED_LANGUAGES) -> list[Broadcast]:
    """Ganze Datei parsen, optional auf Sprachen gefiltert.

    Eine Sendung passt, sobald mindestens eine ihrer Sprachen im
    gewuenschten Set liegt - ein zweisprachiger Eintrag "D,E" gehoert
    also schon dazu, wenn nur Deutsch gewuenscht ist.
    """
    wanted = set(languages) if languages is not None else None
    result: list[Broadcast] = []
    for line in text.splitlines():
        entry = parse_line(line)
        if entry is None:
            continue
        if wanted is not None and not (set(entry.languages) & wanted):
            continue
        result.append(entry)
    return result


def load_schedule(
    for_date: date,
    *,
    cache_dir: Path,
    languages: tuple[str, ...] | None = WANTED_LANGUAGES,
    **fetch_kwargs,
) -> tuple[list[Broadcast], Fetched]:
    """Fahrplan der passenden Saison holen und parsen.

    Der Cache darf hier grosszuegig alt sein: ein Fahrplan aendert sich
    innerhalb einer Saison nur in Details.
    """
    fetched = fetch_text(
        schedule_url(for_date),
        cache_dir=cache_dir,
        max_cache_age_days=fetch_kwargs.pop("max_cache_age_days", 200.0),
        **fetch_kwargs,
    )
    return parse_schedule(fetched.text, languages=languages), fetched
