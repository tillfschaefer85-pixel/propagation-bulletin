"""Torwaechter fuer die Zeitzonenfalle in GitHub Actions.

Cron in GitHub Actions laeuft in UTC und kennt keine Sommerzeit. Ein
Lauf, der ganzjaehrig um 20:30 deutscher Zeit stattfinden soll, braucht
deshalb zwei Cron-Zeilen - 18:30 UTC fuer die Sommerzeit, 19:30 UTC fuer
die Winterzeit - und im Job eine Pruefung, ob es lokal gerade wirklich
so weit ist. Ohne diese Pruefung wuerde der Push das halbe Jahr ueber
eine Stunde zu frueh oder zu spaet kommen, und in den Umstellungswochen
sogar zweimal.

Das Fenster ist bewusst einseitig: akzeptiert wird nur ab der Zielzeit
bis zur Toleranzgrenze, nie davor. Cron feuert naemlich nie zu frueh,
wohl aber verspaetet - geplante Laeufe starten unter Last regelmaessig
einige Minuten bis eine Viertelstunde spaeter. Ein symmetrisches
Fenster wuerde dagegen die falsche der beiden Cron-Zeilen mit
durchlassen, sobald sie eine Stunde daneben liegt.
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--at", required=True, help="Zielzeit in Ortszeit, z.B. 20:30")
    parser.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE_MINUTES)
    args = parser.parse_args(argv)

    now = datetime.now(BERLIN)
    if is_expected_local_time(parse_hhmm(args.at), now, tolerance_minutes=args.tolerance):
        print(f"Ortszeit {now:%H:%M} passt zu {args.at} - Lauf geht weiter")
        return 0

    print(f"Ortszeit {now:%H:%M} passt nicht zu {args.at} - Lauf wird uebersprungen")
    return 1


if __name__ == "__main__":
    sys.exit(main())
