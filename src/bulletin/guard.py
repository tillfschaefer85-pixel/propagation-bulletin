"""Torwaechter fuer die Zeitzonenfalle in GitHub Actions.

Cron in GitHub Actions laeuft in UTC und kennt keine Sommerzeit. Ein
Lauf, der ganzjaehrig um 20:30 deutscher Zeit stattfinden soll, braucht
deshalb zwei Cron-Zeilen - 18:30 UTC fuer die Sommerzeit, 19:30 UTC fuer
die Winterzeit - und im Job eine Pruefung, ob es lokal gerade wirklich
so weit ist. Ohne diese Pruefung wuerde der Push das halbe Jahr ueber
eine Stunde zu frueh oder zu spaet kommen, und in den Umstellungswochen
sogar zweimal.

Es gibt zwei Wege, das zu entscheiden.

Der bessere: GitHub liefert im Lauf selbst mit, WELCHE Cron-Zeile ihn
ausgeloest hat (github.event.schedule). Dann genuegt der Vergleich mit
der Zeile, die zur aktuellen Zeitzone gehoert - eine exakte Entscheidung,
die von Verspaetungen voellig unberuehrt bleibt. Das ist wichtiger als es
klingt: geplante Laeufe starten unter Last regelmaessig verspaetet, und
18:30 UTC ist eine der ueberlaufensten Zeiten ueberhaupt.

Der Rueckfallweg, wenn diese Angabe fehlt: das Zeitfenster. Es ist
einseitig - akzeptiert wird nur ab der Zielzeit bis zur Toleranzgrenze,
nie davor, denn Cron feuert nie zu frueh. Die Toleranz muss unter 60
Minuten bleiben, sonst kaeme die jeweils andere Zeile mit durch. Genau
diese Grenze ist der Grund, warum der Vergleich mit der Cron-Zeile
vorzuziehen ist: ein Lauf mit 50 Minuten Verspaetung wuerde hier still
verworfen, obwohl er der richtige war.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
DEFAULT_TOLERANCE_MINUTES = 45


def parse_hhmm(value: str) -> time:
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute))


def is_expected_local_time(
    target: time,
    now: datetime,
    *,
    tolerance_minutes: int = DEFAULT_TOLERANCE_MINUTES,
) -> bool:
    """Laeuft der Job gerade zur vorgesehenen Ortszeit (oder kurz danach)?

    tolerance_minutes muss kleiner als 60 bleiben, sonst laesst das
    Fenster die jeweils andere Cron-Zeile mit durch - die liegt genau
    eine Stunde entfernt.
    """
    if tolerance_minutes >= 60:
        raise ValueError(
            "Toleranz muss unter 60 Minuten bleiben, sonst passt die "
            "jeweils andere Cron-Zeile mit ins Fenster"
        )

    local = now.astimezone(BERLIN)
    target_minutes = target.hour * 60 + target.minute
    now_minutes = local.hour * 60 + local.minute

    # Ueber Mitternacht hinweg messen, falls das Fenster den Tag wechselt.
    delta = (now_minutes - target_minutes) % (24 * 60)
    return delta <= tolerance_minutes


def summer_time_active(now: datetime) -> bool:
    """Gilt gerade Sommerzeit? (Ortszeit liegt dann zwei Stunden vor UTC.)"""
    offset = now.astimezone(BERLIN).utcoffset()
    return offset is not None and offset.total_seconds() == 7200


def expected_cron(now: datetime, summer_cron: str, winter_cron: str) -> str:
    """Die Cron-Zeile, die zur aktuellen Zeitzone gehoert."""
    return summer_cron if summer_time_active(now) else winter_cron


def should_run(
    now: datetime,
    *,
    target: time,
    schedule: str | None = None,
    summer_cron: str | None = None,
    winter_cron: str | None = None,
    tolerance_minutes: int = DEFAULT_TOLERANCE_MINUTES,
) -> tuple[bool, str]:
    """Entscheidet, ob dieser Lauf weitergehen soll. Gibt auch die Begruendung zurueck.

    Kennt der Aufrufer die ausloesende Cron-Zeile, entscheidet der
    Vergleich mit der zur Zeitzone passenden Zeile - unabhaengig davon,
    wie spaet der Lauf tatsaechlich gestartet ist. Sonst bleibt das
    Zeitfenster als Rueckfall.
    """
    local = now.astimezone(BERLIN)

    if schedule and summer_cron and winter_cron:
        expected = expected_cron(now, summer_cron, winter_cron)
        season = "Sommerzeit" if summer_time_active(now) else "Winterzeit"
        if schedule.strip() == expected:
            return True, f"{season}: ausgeloest von {schedule} - das ist die richtige Zeile"
        return False, (
            f"{season}: ausgeloest von {schedule}, erwartet wird {expected} - uebersprungen"
        )

    ok = is_expected_local_time(target, now, tolerance_minutes=tolerance_minutes)
    return ok, (
        f"Ortszeit {local:%H:%M}, Ziel {target:%H:%M} - "
        + ("passt" if ok else "passt nicht, uebersprungen")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--at", required=True, help="Zielzeit in Ortszeit, z.B. 20:30")
    parser.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE_MINUTES)
    parser.add_argument("--schedule", default="", help="ausloesende Cron-Zeile (github.event.schedule)")
    parser.add_argument("--summer-cron", default="", help="Cron-Zeile fuer die Sommerzeit")
    parser.add_argument("--winter-cron", default="", help="Cron-Zeile fuer die Winterzeit")
    args = parser.parse_args(argv)

    ok, reason = should_run(
        datetime.now(BERLIN),
        target=parse_hhmm(args.at),
        schedule=args.schedule or None,
        summer_cron=args.summer_cron or None,
        winter_cron=args.winter_cron or None,
        tolerance_minutes=args.tolerance,
    )
    print(reason)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
