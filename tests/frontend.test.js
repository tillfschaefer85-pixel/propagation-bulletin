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
  assert.deepEqual(formatFrequency(49800), { value: "49.800", unit: "MHz" });
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
