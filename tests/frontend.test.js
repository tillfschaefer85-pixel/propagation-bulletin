/* Tests fuer die reinen Funktionen der Seite.
 *
 * Laufen mit `node tests/frontend.test.js` und ohne Browser. Getestet
 * wird gegen die tatsaechlich erzeugten docs/bulletin.json und
 * docs/stations.json - nicht gegen erfundene Beispieldaten. Damit faellt
 * auf, wenn Python und JavaScript unterschiedliche Vorstellungen vom
 * Format entwickeln.
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const {
  kpBucket,
  formatFrequency,
  timingPhrase,
  dialPosition,
  entriesForBucket,
  isCurrent,
} = require("../docs/app.js");

const docs = path.join(__dirname, "..", "docs");
const bulletin = JSON.parse(fs.readFileSync(path.join(docs, "bulletin.json"), "utf8"));
const stations = JSON.parse(fs.readFileSync(path.join(docs, "stations.json"), "utf8")).stations;

test("kpBucket rundet die fraktionalen NOAA-Werte wie Python", () => {
  // Muss mit KpSample.bucket in sources/swpc.py uebereinstimmen.
  const cases = [[0.0, 0], [0.33, 0], [0.67, 1], [1.0, 1], [1.33, 1], [1.67, 2], [2.33, 2], [4.33, 4]];
  for (const [kp, expected] of cases) {
    assert.equal(kpBucket(kp), expected, `Kp ${kp}`);
  }
});

test("kpBucket bleibt im Bereich der Tabelle", () => {
  assert.equal(kpBucket(9.67), 9);
  assert.equal(kpBucket(-1), 0);
});

test("Frequenzen werden so geschrieben, wie man sie einstellt", () => {
  assert.deepEqual(formatFrequency(198), { value: "198", unit: "kHz" });
  assert.deepEqual(formatFrequency(6070), { value: "6070", unit: "kHz" });
  assert.deepEqual(formatFrequency(49800), { value: "49,800", unit: "MHz" });
});

test("timingPhrase unterscheidet kommt/jetzt/laeuft", () => {
  const now = new Date(2026, 8, 3, 20, 30);
  assert.equal(timingPhrase("22:00", now).text, "ab 22:00");
  assert.equal(timingPhrase("19:00", now).text, "läuft seit 19:00");
  assert.equal(timingPhrase("20:30", now).current, true);
});

test("Mitternacht gehoert zum laufenden Abend, nicht zum Vormittag", () => {
  const now = new Date(2026, 8, 3, 20, 30);
  assert.equal(timingPhrase("00:00", now).text, "ab 00:00");
});

test("timingPhrase stimmt mit der Formulierung aus notify.py ueberein", () => {
  // Dieselben drei Faelle stehen als Python-Test in tests/test_notify.py.
  // Laufen die beiden Fassungen auseinander, sagt die Seite etwas
  // anderes als der Push, den Du zehn Sekunden vorher bekommen hast.
  const now = new Date(2026, 8, 3, 20, 30);
  assert.equal(timingPhrase("22:00", now).text, "ab 22:00");
  assert.equal(timingPhrase("19:00", now).text, "läuft seit 19:00");
  assert.equal(timingPhrase("21:00", now).text, "jetzt (21:00)");
});

test("dialPosition ordnet die Baender monoton und im Bereich 0 bis 1", () => {
  const positions = [150, 198, 540, 1008, 3955, 6070, 15000, 30000].map((f) => dialPosition(f));
  for (let i = 1; i < positions.length; i++) {
    assert.ok(positions[i] > positions[i - 1], `${i}: nicht monoton`);
  }
  assert.ok(positions[0] >= 0 && positions[positions.length - 1] <= 1);
});

test("dialPosition faengt Frequenzen ausserhalb der Skala ab", () => {
  assert.equal(dialPosition(10), 0);
  assert.equal(dialPosition(99999), 1);
});

test("entriesForBucket liest das echte Bulletin und sortiert nach Punktzahl", () => {
  const rows = entriesForBucket(bulletin, stations, "main", 2);
  assert.ok(rows.length > 0, "Hauptliste ist leer - stimmt das Format noch?");
  for (let i = 1; i < rows.length; i++) {
    assert.ok(rows[i - 1].slot.score >= rows[i].slot.score, "nicht absteigend sortiert");
  }
});

test("jede Kp-Stufe der echten Daten ist abrufbar", () => {
  for (let bucket = 0; bucket <= 9; bucket++) {
    const rows = entriesForBucket(bulletin, stations, "main", bucket);
    for (const row of rows) {
      assert.equal(row.slot.kp, bucket, "Spalte passt nicht zur Stufe");
      assert.ok(typeof row.slot.t === "string" && row.slot.t.includes(":"));
    }
  }
});

test("Eintraege ohne Stammdaten werden uebersprungen statt zu werfen", () => {
  const broken = { entries: [{ station_id: "gibtsnicht", list_kind: "main", best_by_kp: [{ kp: 0, t: "20:00", score: 9 }] }] };
  assert.deepEqual(entriesForBucket(broken, {}, "main", 0), []);
});

test("DX-Eintraege landen nicht in der Hauptliste", () => {
  const main = entriesForBucket(bulletin, stations, "main", 2).map((r) => r.entry.station_id);
  const dx = entriesForBucket(bulletin, stations, "dx", 2).map((r) => r.entry.station_id);
  for (const id of dx) assert.ok(!main.includes(id));
});

test("isCurrent erkennt den heutigen Stand", () => {
  const now = new Date(2026, 8, 4, 20, 30);
  assert.equal(isCurrent("2026-09-04", now), true);
  assert.equal(isCurrent("2026-09-03", now), false);
});

test("isCurrent verrechnet sich nicht bei einstelligen Monaten und Tagen", () => {
  assert.equal(isCurrent("2026-01-05", new Date(2026, 0, 5, 12, 0)), true);
  assert.equal(isCurrent("2026-1-5", new Date(2026, 0, 5, 12, 0)), false);
});

test("kpNoteFor benennt die Herkunft des Werts unterschiedlich", () => {
  const { kpNoteFor } = require("../docs/app.js");
  const live = kpNoteFor({ value: 1.67, source: "live" });
  const snapshot = kpNoteFor({ value: 1.67, source: "snapshot" });
  assert.ok(live.includes("1,67"), "Dezimalkomma fehlt");
  assert.ok(snapshot.includes("1,67"));
  assert.notEqual(live, snapshot, "Rueckfallweg muss erkennbar sein");
});

/* ---------- Gestauchte Skala ---------- */

const {
  frequencySegments, layoutSegments, positionInSegments, bandLabel,
  kpDescription, scoreExplanation,
} = require("../docs/app.js");

test("frequencySegments fasst eng beieinander liegende Frequenzen zusammen", () => {
  const segments = frequencySegments([3955, 3985, 6070, 6155]);
  assert.equal(segments.length, 2, "75m und 49m sind zwei Abschnitte");
});

test("frequencySegments trennt Langwelle von Kurzwelle", () => {
  const segments = frequencySegments([198, 6070]);
  assert.equal(segments.length, 2);
  assert.ok(segments[0].max < 1000);
  assert.ok(segments[1].min > 1000);
});

test("frequencySegments kommt mit einer einzelnen Frequenz zurecht", () => {
  const segments = frequencySegments([6070]);
  assert.equal(segments.length, 1);
  assert.ok(segments[0].max > segments[0].min, "Breite darf nicht null sein");
});

test("frequencySegments liefert für leere Eingabe eine leere Liste", () => {
  assert.deepEqual(frequencySegments([]), []);
});

test("layoutSegments füllt die Breite ohne Überlappung", () => {
  const placed = layoutSegments(frequencySegments([198, 3955, 6070, 15100]), 1000);
  for (let i = 1; i < placed.length; i++) {
    assert.ok(placed[i].x >= placed[i - 1].x + placed[i - 1].width, "Abschnitte überlappen");
  }
  const last = placed[placed.length - 1];
  assert.ok(last.x + last.width <= 1000.5, "läuft über die Breite hinaus");
});

test("ein einzelner Sender in einem Band bekommt sichtbare Breite", () => {
  // Der eigentliche Zweck der Stauchung: Droitwich allein auf Langwelle
  // darf kein unsichtbarer Strich am Rand werden.
  const placed = layoutSegments(frequencySegments([198, 5900, 5950, 6000, 6070, 6155]), 1000);
  const lw = placed[0];
  assert.ok(lw.width > 80, `Langwellenabschnitt zu schmal: ${lw.width}`);
});

test("positionInSegments bildet Frequenzen monoton ab", () => {
  const freqs = [198, 3955, 6070, 15100];
  const placed = layoutSegments(frequencySegments(freqs), 1000);
  const xs = freqs.map((f) => positionInSegments(f, placed));
  for (const x of xs) assert.ok(x !== null, "Frequenz nicht platzierbar");
  for (let i = 1; i < xs.length; i++) assert.ok(xs[i] > xs[i - 1], "nicht monoton");
});

test("positionInSegments meldet Frequenzen außerhalb aller Abschnitte", () => {
  const placed = layoutSegments(frequencySegments([6070]), 1000);
  assert.equal(positionInSegments(198, placed), null);
});

test("bandLabel benennt die Bänder wie am Gerät", () => {
  assert.equal(bandLabel(198), "Langwelle");
  assert.equal(bandLabel(1008), "Mittelwelle");
  assert.equal(bandLabel(6070), "49 m");
  assert.equal(bandLabel(3955), "75 m");
  assert.equal(bandLabel(15100), "19 m");
});

/* ---------- Erklärungen ---------- */

test("kpDescription deckt die ganze Skala ab und bleibt verständlich", () => {
  for (let kp = 0; kp <= 9; kp++) {
    const d = kpDescription(kp);
    assert.ok(d.label && d.text, `Kp ${kp} ohne Beschreibung`);
    assert.ok(d.text.length > 20, `Kp ${kp}: Text zu dürftig`);
  }
});

test("kpDescription unterscheidet ruhig von Sturm", () => {
  assert.notEqual(kpDescription(1).label, kpDescription(6).label);
});

test("scoreExplanation nennt bei totem Score den Grund", () => {
  const station = { freq_khz: 26000, band_class: "sw" };
  const slot = { score: 0, components: { muf_mhz: 7.5, luf_mhz: 2.2, darkness: 1.0 } };
  const text = scoreExplanation(slot, station);
  assert.ok(text.includes("über der höchsten"), text);
});

test("scoreExplanation erkennt auch den unteren Rand", () => {
  const station = { freq_khz: 1800, band_class: "sw" };
  const slot = { score: 0, components: { muf_mhz: 7.5, luf_mhz: 6.0, darkness: 0.2 } };
  assert.ok(scoreExplanation(slot, station).includes("unter der niedrigsten"));
});

test("scoreExplanation nennt bei gutem Score die tragenden Gründe", () => {
  const station = { freq_khz: 6070, band_class: "sw" };
  const slot = { score: 68, components: { muf_mhz: 7.5, owf_mhz: 6.4, luf_mhz: 2.2, darkness: 1.0, field: 0.6, geomagnetic: 0.95 } };
  const text = scoreExplanation(slot, station);
  assert.ok(text.includes("Dunkeln"), text);
  assert.ok(text.endsWith("."), "kein sauberer Satz");
});

test("scoreExplanation beginnt immer groß und endet mit Punkt", () => {
  const cases = [
    [{ freq_khz: 1008, band_class: "mw" }, { score: 80, components: { darkness: 1.0, field: 0.9 } }],
    [{ freq_khz: 198, band_class: "lw" }, { score: 30, components: { darkness: 0.75, field: 0.3, geomagnetic: 0.7 } }],
    [{ freq_khz: 6070, band_class: "sw" }, { score: 12, components: {} }],
  ];
  for (const [station, slot] of cases) {
    const text = scoreExplanation(slot, station);
    assert.equal(text[0], text[0].toUpperCase(), text);
    assert.ok(text.endsWith("."), text);
  }
});

test("scoreExplanation weist auf eine weggedrehte Sendeantenne hin", () => {
  const station = { freq_khz: 9600, band_class: "sw" };
  const slot = { score: 25, components: { muf_mhz: 12, owf_mhz: 10, luf_mhz: 2.2, darkness: 1.0, aiming: 0.25 } };
  assert.ok(scoreExplanation(slot, station).includes("andere Richtung"));
});

test("Stammdaten mit und ohne Sendestandort sind beide gültig", () => {
  // Frueher stand hier die Forderung, dass die eingecheckte
  // stations.json einen Standortnamen enthaelt. Das war ein schlechter
  // Test: die Datei erzeugt der Morgenlauf, und nach dem Einbau eines
  // neuen Feldes hinkt sie zwangslaeufig einen Lauf hinterher. Er wurde
  // rot, obwohl nichts kaputt war. Ob das Feld korrekt entsteht, prueft
  // jetzt die Python-Seite (tests/test_build.py); hier zaehlt nur, dass
  // die Anzeige mit beiden Faellen zurechtkommt.
  for (const station of Object.values(stations)) {
    if (station.site_name !== undefined && station.site_name !== null) {
      assert.equal(typeof station.site_name, "string");
      assert.ok(station.site_name.length > 0, "leerer Standortname");
    }
  }
});

test("Zahlen erscheinen in deutscher Schreibweise mit Komma", () => {
  const { num } = require("../docs/app.js");
  assert.equal(num(7.48, 2), "7,48");
  assert.equal(num(68.4), "68,4");
  assert.equal(num(1), "1,0");
});

test("Erklärtexte enthalten keine englischen Dezimalpunkte", () => {
  const station = { freq_khz: 6070, band_class: "sw" };
  const slot = { score: 68, components: { muf_mhz: 7.48, owf_mhz: 6.36, luf_mhz: 2.2, darkness: 1.0 } };
  const text = scoreExplanation(slot, station);
  assert.ok(!/\d\.\d/.test(text), `Dezimalpunkt gefunden: ${text}`);
});
