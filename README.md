# Ausbreitungs-Bulletin

Täglich berechnetes Empfangs-Bulletin für Lang-, Mittel- und Kurzwelle,
zugeschnitten auf einen festen Empfangsstandort. Eine Seite zum Nachsehen,
ein Push am Abend zum Erinnern.

## Wie es läuft

Vier Läufe als GitHub Actions:

| Lauf | Wann | Was er tut |
|---|---|---|
| `build.yml` | 05:15 per cron-job.org | Rechnet das Bulletin und committet es nach `docs/` |
| `space-weather.yml` | alle 3 Stunden | Legt den aktuellen Kp-Wert als Datei ab |
| `notify.yml` | 20:30 per cron-job.org | Liest das Bulletin und verschickt einen Push |
| `keepalive.yml` | monatlich | Hält die GitHub-Zeitpläne wach |

**Der Anstoß kommt von außen.** GitHub behandelt geplante Läufe als
nachrangig und verzögert sie bei Last erheblich — beobachtet wurden hier
über 45 Minuten, in anderen Projekten Stunden. Ein kostenloser Auftrag
bei cron-job.org ruft deshalb minutengenau die GitHub-Schnittstelle auf
und startet den Lauf wie ein Klick auf „Run workflow". Weil der Dienst
Zeitzonen kennt, genügt ein Eintrag mit `Europe/Berlin`; die
Zeitumstellung erledigt sich von selbst.

Die GitHub-eigenen Zeitpläne bleiben als Rückfallebene bestehen. Damit
die Nachricht nicht zweimal kommt, fragt der geplante Abendlauf vorher
die eigene Laufhistorie ab: Gab es heute schon einen erfolgreichen Lauf
per Anstoß, beendet er sich still. Beim Morgenlauf braucht es das nicht —
ein zweiter Lauf stellt fest, dass sich nichts geändert hat, und
committet nichts.

Der Morgenlauf rechnet den planbaren Teil: Sendepläne, Sonnenstände,
Grauzone und für jede geomagnetische Stufe von 0 bis 9 ein bestes
Zeitfenster je Station. Die Seite sucht daraus beim Öffnen nur die
passende Spalte heraus — gerechnet wird im Browser nichts.

## Einrichten

1. **Repository anlegen und Dateien hochladen.** Der Standardbranch muss
   `main` heißen, sonst laufen die geplanten Jobs nicht an — GitHub
   startet Cron nur auf dem Standardbranch.

2. **Pages einschalten.** Settings → Pages → Source: „Deploy from a
   branch", Branch `main`, Ordner `/docs`. Nach dem ersten Deploy steht
   die Adresse dort.

3. **Schreibrechte für Actions.** Settings → Actions → General →
   Workflow permissions: „Read and write permissions". Ohne das kann der
   Bau-Lauf sein Ergebnis nicht committen.

4. **Push-Ziel anlegen.** In der ntfy-App (Android/iOS) ein Thema
   abonnieren. Der Themenname ist faktisch das Passwort — bei ntfy gibt
   es keine Anmeldung, wer den Namen kennt, liest mit. Also etwas
   Unerratbares wählen.

5. **Secrets und Variablen setzen.** Settings → Secrets and variables →
   Actions:
   - Secret `NTFY_TOPIC`: der Themenname aus Schritt 4
   - Secret `NTFY_TOKEN`: nur nötig bei einem eigenen ntfy-Server mit Anmeldung
   - Variable `PAGE_URL`: die Adresse aus Schritt 2, damit der Push ein Ziel zum Antippen hat

6. **Externen Anstoß einrichten.** Ein fein granuliertes Personal Access
   Token anlegen (Settings → Developer settings), beschränkt auf dieses
   Repository, Berechtigung **Actions: Read and write**. Dann bei
   cron-job.org zwei Aufträge:

   | Zeit (Europe/Berlin) | Adresse |
   |---|---|
   | täglich 05:15 | `.../actions/workflows/build.yml/dispatches` |
   | täglich 20:30 | `.../actions/workflows/notify.yml/dispatches` |

   Basis ist `https://api.github.com/repos/<name>/propagation-bulletin`.
   Methode POST, Rumpf `{"ref":"main"}`, Kopfzeilen
   `Authorization: Bearer <Token>`, `Accept: application/vnd.github+json`
   und `X-GitHub-Api-Version: 2022-11-28`. Das Token gilt höchstens ein
   Jahr — Ablauf im Kalender vormerken, sonst bleibt der Push eines Tages
   ohne Vorwarnung aus.

7. **Einmal von Hand auslösen.** Actions → „Bulletin bauen" → „Run
   workflow". Danach „Push verschicken" ebenso. Beide haben
   `workflow_dispatch`, damit Du nicht bis morgen früh warten musst.

## Was Du selbst pflegst

`data/stations_mw_lw.yaml` — Deine eigenen Mittel- und Langwellen-Einträge
mit Peilungen und Erfahrungswerten. Ergänzt und überschreibt, was
automatisch aus dem Sendeplan kommt.

`data/tx_sites.yaml` — Koordinaten von Sendestandorten. Deckt derzeit
Europa und HCJB Ecuador ab. Ein Sender ohne Eintrag wird übersprungen und
im Log mitgezählt — kommt derselbe häufig vor, lohnt sich ein Eintrag.

`data/broadcast_bands.yaml` — welche Frequenzbereiche als Rundfunk gelten.
EiBi führt auch Flug- und Seefunk; der Filter hält sie draußen. `enabled:
false` schaltet ihn ab, einzelne Bänder lassen sich ergänzen — etwa der
Piratenbereich um 6200–6400 kHz.

`data/weights.yaml` — alle Stellschrauben der Bewertung. Startwerte einer
Heuristik, ausdrücklich zum Nachjustieren gedacht. Änderungen hier sind
Datenänderungen, kein Deployment.

## Betrieb

**Der Abendlauf ist die Ausfallmeldung für den Morgenlauf.** Ist das
Bulletin nicht von heute, kommt statt einer Empfehlung eine Warnung mit
dem tatsächlichen Stand. Ohne das würdest Du wochenlang Empfehlungen von
einem eingefrorenen Stand bekommen, ohne es zu merken.

**Zeitumstellung.** Cron in Actions läuft in UTC und kennt keine
Sommerzeit. Jeder zeitgebundene Lauf hat deshalb zwei Cron-Zeilen und
einen Torwächter. Dieser entscheidet anhand der Cron-Zeile, die den Lauf
ausgelöst hat — nicht anhand der Uhrzeit. Das ist der Unterschied
zwischen „läuft zuverlässig" und „läuft meistens": geplante Läufe starten
unter Last regelmäßig verspätet, und eine Prüfung per Zeitfenster müsste
unter 60 Minuten Toleranz bleiben, sonst käme die jeweils andere Zeile
mit durch. Ein Lauf mit 50 Minuten Verzug wäre so still verworfen worden.
Ein Test geht jeden Tag eines Jahres durch und stellt sicher, dass immer
genau eine der beiden Zeilen feuert.

**Geplante Jobs schlafen ein.** GitHub deaktiviert Cron-Workflows in
Repositories, in denen 60 Tage lang nichts passiert. Die Commits des
Bots zählen dafür nicht immer. Kommt irgendwann kein Push mehr, lohnt
ein Blick in den Actions-Reiter — dort steht dann ein Knopf zum
Reaktivieren.

**Saisonwechsel der Sendepläne.** Die Sendeplandatei wechselt am letzten
Sonntag im März und im Oktober. Der Lauf erkennt das selbst und holt die
neue Datei; die Fußzeile der Seite warnt zwei Wochen vorher.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests   # Physik, Quellen, Läufe
node --test tests/frontend.test.js                     # die Seite
```

Die Tests prüfen Verhalten, nicht Punktzahlen: Tagespfade auf Mittelwelle
müssen sterben, mehr Leistung darf nie schaden, der Score darf mit
steigender Störung nie steigen. Solche Aussagen überleben das
Nachjustieren der Gewichte — feste Referenzzahlen würden bei jeder
Änderung rot.

## Grenzen, die bewusst so sind

Das hier ist eine Rangfolge, keine Feldstärkevorhersage. Die nächtliche
Grenzfrequenz ist an einem einzigen bekannten Fall kalibriert und trägt
einen großen Teil der Kurzwellenbewertung — eine Ionosonde als Messwert
wäre die größte einzelne Verbesserung.

Seltenheit bedeutet derzeit „dauerhaft schwer" und „heute ungewöhnlich".
Das dritte und wertvollste Kriterium — „hattest Du noch nie im Log" —
braucht ein Empfangslogbuch; das Datenmodell hält den Platz dafür frei.

Exotische Wochentagsangaben im Sendeplan („erster Samstag im Monat",
„nur bis zum 15.") werden als „jeden Tag möglich" behandelt statt falsch
ausgeschlossen.
