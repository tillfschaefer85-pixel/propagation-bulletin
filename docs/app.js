/* Anzeige des Bulletins.
 *
 * Die Seite mischt zwei Dinge: den vorberechneten Teil aus dem Repository
 * (bulletin.json, stations.json - vom Morgenlauf geschrieben) und den
 * aktuellen Kp-Wert, den sie beim Oeffnen selbst von der NOAA holt.
 * Deshalb ist sie auch dann aktuell, wenn der Morgenlauf seit Stunden
 * vorbei ist.
 *
 * Die Bewertung selbst passiert NICHT hier. Der Morgenlauf hat fuer jede
 * Kp-Stufe von 0 bis 9 ein Gewinnerfenster vorberechnet; diese Seite
 * sucht nur die passende Spalte heraus. Damit bleibt die Physik an genau
 * einer Stelle im Projekt - in Python, mit Tests.
 *
 * Wenn der Abruf des Kp-Werts scheitert (etwa weil der Browser ihn als
 * fremde Herkunft blockiert), faellt die Seite auf Stufe 2 zurueck, sagt
 * das offen, und der Regler bleibt bedienbar. Ein blockierter Abruf darf
 * die Seite nicht leer lassen.
 */

const KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json";
const DEFAULT_KP = 2;

/* ---------- reine Hilfsfunktionen (unten fuer Tests exportiert) ---------- */

/** Rundet den fraktionalen Kp-Wert der NOAA auf die Stufe der Tabelle. */
function kpBucket(kp) {
  return Math.max(0, Math.min(9, Math.round(kp)));
}

/** Frequenz so schreiben, wie man sie am Geraet einstellt. */
function formatFrequency(khz) {
  if (khz < 30000) return { value: String(Math.round(khz)), unit: "kHz" };
  return { value: num(khz / 1000, 3), unit: "MHz" };
}

/**
 * Ordnet ein Zeitfenster relativ zum Jetzt ein.
 *
 * Spiegelt bewusst die Logik aus notify.py - dieselbe Frage, zwei
 * Laufzeitumgebungen. Wer eine der beiden aendert, muss an die andere
 * denken; die Alternative waere, den Text schon morgens festzuschreiben,
 * und dann stuende abends etwas Falsches da.
 */
function timingPhrase(slotHhmm, now) {
  const parts = slotHhmm.split(":");
  if (parts.length !== 2) return { text: slotHhmm, current: false };

  let slot = Number(parts[0]) * 60 + Number(parts[1]);
  let current = now.getHours() * 60 + now.getMinutes();
  if (slot < 6 * 60) slot += 24 * 60;
  if (current < 6 * 60) current += 24 * 60;

  const delta = slot - current;
  if (delta > 45) return { text: `ab ${slotHhmm}`, current: false };
  if (delta < -45) return { text: `läuft seit ${slotHhmm}`, current: false };
  return { text: `jetzt (${slotHhmm})`, current: true };
}

/** Position einer Frequenz auf einer durchgehenden logarithmischen Skala, 0 bis 1. */
function dialPosition(khz, min = 150, max = 30000) {
  const clamped = Math.max(min, Math.min(max, khz));
  return (Math.log10(clamped) - Math.log10(min)) / (Math.log10(max) - Math.log10(min));
}

/**
 * Teilt die heutigen Frequenzen in belegte Abschnitte.
 *
 * Eine durchgehende Skala von 150 kHz bis 30 MHz verschenkt zwei Drittel
 * ihrer Breite an Bereiche, in denen an den meisten Abenden nichts liegt -
 * und quetscht dafuer alles zwischen 3 und 11 MHz zusammen. Diese Funktion
 * sucht deshalb die tatsaechlich belegten Bereiche und laesst die Luecken
 * dazwischen zusammenschrumpfen.
 *
 * gapDecades: ab welchem Abstand (in Dekaden) eine Luecke als Bruch gilt.
 * padDecades: wieviel Luft links und rechts um einen Abschnitt bleibt.
 */
function frequencySegments(frequencies, { gapDecades = 0.12, padDecades = 0.03 } = {}) {
  const sorted = [...new Set(frequencies)].filter((f) => f > 0).sort((a, b) => a - b);
  if (!sorted.length) return [];

  const segments = [];
  let start = sorted[0];
  let previous = sorted[0];

  for (const freq of sorted.slice(1)) {
    if (Math.log10(freq) - Math.log10(previous) > gapDecades) {
      segments.push({ min: start, max: previous });
      start = freq;
    }
    previous = freq;
  }
  segments.push({ min: start, max: previous });

  // Ein Abschnitt mit nur einer Frequenz haette die Breite null - er bekommt
  // etwas Luft, damit sein Strich nicht auf einer Kante klebt.
  return segments.map((seg) => {
    const lo = Math.log10(seg.min) - padDecades;
    const hi = Math.log10(seg.max) + padDecades;
    return { min: 10 ** lo, max: 10 ** hi, span: hi - lo };
  });
}

/**
 * Verteilt die Abschnitte auf die verfuegbare Breite.
 *
 * Jeder Abschnitt bekommt Platz nach seiner logarithmischen Ausdehnung,
 * aber mindestens einen Mindestanteil - sonst wuerde ein einzelner
 * Langwellensender zu einem unsichtbaren Strich am Rand.
 */
function layoutSegments(segments, width, { gapPx = 26, minShare = 0.12 } = {}) {
  if (!segments.length) return [];

  const usable = width - gapPx * (segments.length - 1);
  const totalSpan = segments.reduce((sum, seg) => sum + seg.span, 0) || 1;

  let shares = segments.map((seg) => seg.span / totalSpan);
  // Mindestanteil durchsetzen und den Rest proportional nachziehen
  const floor = Math.min(minShare, 1 / segments.length);
  const lifted = shares.map((share) => Math.max(share, floor));
  const scale = 1 / lifted.reduce((a, b) => a + b, 0);

  let x = 0;
  return segments.map((seg, i) => {
    const w = usable * lifted[i] * scale;
    const placed = { ...seg, x, width: w };
    x += w + gapPx;
    return placed;
  });
}

/**
 * Fasst benachbarte Abschnitte zusammen, die denselben Bandnamen tragen.
 *
 * Ohne das stünde "Mittelwelle" zweimal nebeneinander, nur weil zwischen
 * 1008 und 1485 kHz zufällig eine Lücke liegt - für den Leser sieht das
 * nach einem Fehler aus, nicht nach einer Information.
 */
function mergeSameBand(segments, labelFor) {
  const merged = [];
  for (const seg of segments) {
    const last = merged[merged.length - 1];
    if (last && labelFor(last.min) === labelFor(seg.min)) {
      const lo = Math.log10(last.min);
      const hi = Math.log10(seg.max);
      merged[merged.length - 1] = { min: last.min, max: seg.max, span: hi - lo };
    } else {
      merged.push({ ...seg });
    }
  }
  return merged;
}

/** Bildet eine Frequenz auf ihre X-Position im gestauchten Layout ab. */
function positionInSegments(khz, placed) {
  for (const seg of placed) {
    if (khz >= seg.min && khz <= seg.max) {
      const t = (Math.log10(khz) - Math.log10(seg.min)) / (Math.log10(seg.max) - Math.log10(seg.min));
      return seg.x + t * seg.width;
    }
  }
  return null;
}

/** Bandname zu einer Frequenz - fuer die Beschriftung der Abschnitte. */
function bandLabel(khz) {
  if (khz < 300) return "Langwelle";
  if (khz <= 1710) return "Mittelwelle";
  const meters = Math.round(300000 / khz);
  // Auf das naechstliegende Rundfunkband runden, damit "49 m" dasteht
  // und nicht "48 m" nur weil der Sender am Bandrand liegt.
  const bands = [120, 90, 75, 60, 49, 41, 31, 25, 22, 19, 16, 15, 13, 11];
  const nearest = bands.reduce((a, b) => (Math.abs(b - meters) < Math.abs(a - meters) ? b : a));
  return Math.abs(nearest - meters) <= 4 ? `${nearest} m` : `${meters} m`;
}

/**
 * Rangfolge zweier Zeilen: erst die Punktzahl, dann Festes.
 *
 * Der Nachschlag ist keine Kosmetik. Ohne ihn haengt bei gleicher
 * Punktzahl - auf Mittelwelle keine Seltenheit - davon ab, in welcher
 * Reihenfolge der Morgenlauf die Eintraege geschrieben hat, welche
 * Frequenz die Liste anzeigt. Das kann von Tag zu Tag springen, ohne
 * dass sich an der Ausbreitung etwas geaendert haette.
 */
function compareRows(a, b) {
  if (b.slot.score !== a.slot.score) return b.slot.score - a.slot.score;
  if (a.station.freq_khz !== b.station.freq_khz) return a.station.freq_khz - b.station.freq_khz;
  return String(a.entry.station_id).localeCompare(String(b.entry.station_id));
}

/**
 * Schluessel, unter dem zwei Zeilen als derselbe Sender gelten:
 * Name und Bandklasse.
 *
 * Das Band gehoert bewusst dazu. Sieben Kurzwellenfrequenzen desselben
 * Programms sind eine Aufgabe - man dreht, bis eine davon kommt. Derselbe
 * Sender auf Mittelwelle ist eine andere: andere Tageszeit, andere
 * Antenne, andere Peilung. Die beiden zusammenzuwerfen wuerde eine
 * Empfangsmoeglichkeit verschlucken statt Doppeltes aufzuraeumen.
 *
 * Verglichen wird gross-/kleinschreibungsblind und ohne doppelte
 * Leerzeichen; weiter geht die Normalisierung absichtlich nicht.
 * "Radio Romania Int." und "Radio Romania International" bleiben zwei
 * Sender - lieber eine Dublette zuviel als zwei verschiedene Programme
 * stillschweigend verschmolzen.
 */
function stationKey(station) {
  const name = String(station.name || "").toLowerCase().replace(/\s+/g, " ").trim();
  return `${name}\u0000${station.band_class || ""}`;
}

/**
 * Behaelt je Sender und Band nur die beste Frequenz.
 *
 * Die verdraengten Zeilen sind nicht weg: sie haengen als `alternates`
 * am Gewinner, nach derselben Rangfolge sortiert. Die Liste zeigt ihre
 * Zahl, das Detailfenster zeigt sie einzeln - damit die Aufraeumung
 * nichts versteckt, was Du am Geraet brauchen koenntest.
 */
function collapseByStation(rows) {
  const groups = new Map();
  for (const row of rows) {
    const key = stationKey(row.station);
    const group = groups.get(key);
    if (group) group.push(row);
    else groups.set(key, [row]);
  }

  const winners = [];
  for (const group of groups.values()) {
    const [best, ...rest] = [...group].sort(compareRows);
    winners.push({ ...best, alternates: rest });
  }
  return winners.sort(compareRows);
}

/**
 * Die Eintraege einer Liste, nach Punktzahl der gewaehlten Kp-Stufe
 * sortiert und je Sender und Band auf die beste Frequenz zusammengefasst.
 */
function entriesForBucket(bulletin, stations, kind, bucket) {
  const rows = (bulletin.entries || [])
    .filter((e) => e.list_kind === kind && e.best_by_kp && stations[e.station_id])
    .map((e) => ({
      entry: e,
      station: stations[e.station_id],
      slot: e.best_by_kp[bucket],
    }));
  return collapseByStation(rows);
}

/**
 * Einzeiler unter der Station: was zusammengefasst wurde.
 *
 * Bis zu zwei Ausweichfrequenzen stehen ausgeschrieben da - so weit
 * reicht es, um beim Drehen gleich die naechste zu probieren. Darueber
 * wuerde die Zeile auf dem Telefon umbrechen, also nur noch die Zahl;
 * die Frequenzen selbst stehen im Detailfenster.
 */
function alternateHint(alternates) {
  if (!alternates.length) return "";
  if (alternates.length > 2) return `Sendet auf ${alternates.length} weiteren Frequenzen`;
  const list = alternates.map((alt) => {
    const f = formatFrequency(alt.station.freq_khz);
    return `${f.value} ${f.unit}`;
  });
  return `Sendet auch auf ${list.join(" und ")}`;
}

/** Deutsche Zahlschreibweise: Komma statt Punkt. */
function num(value, digits = 1) {
  return value.toFixed(digits).replace(".", ",");
}

/** Einordnung des Kp-Werts in Worte. */
function kpDescription(kp) {
  if (kp <= 1) return { label: "sehr ruhig", text: "Beste Bedingungen. Auch weite Wege über den Norden tragen." };
  if (kp <= 2) return { label: "ruhig", text: "Normallage. Nichts steht der Ausbreitung im Weg." };
  if (kp <= 3) return { label: "leicht unruhig", text: "Kaum spürbar. Sehr weite Nordwege können etwas schwächeln." };
  if (kp <= 4) return { label: "unruhig", text: "Nordwege werden merklich schlechter, Südwege bleiben stabil." };
  if (kp <= 5) return { label: "Sturm", text: "Geomagnetischer Sturm. Wege über hohe Breiten brechen weitgehend weg." };
  if (kp <= 6) return { label: "starker Sturm", text: "Auch mittlere Breiten leiden. Dafür kann Polarlicht sichtbar werden." };
  return { label: "schwerer Sturm", text: "Weiträumige Ausfälle auf Kurzwelle. Mittelwelle über Süden bleibt am ehesten." };
}

/**
 * Erklärt in einem Satz, warum eine Station heute oben oder unten steht.
 *
 * Ergänzt die Einzelwerte, ersetzt sie aber nicht: die Zahlen sind
 * ehrlicher, der Satz ist abends schneller zu erfassen.
 */
function scoreExplanation(slot, station) {
  const c = slot.components || {};
  const freqMhz = station.freq_khz / 1000;

  if (slot.score <= 0) {
    if (station.band_class === "sw" && c.muf_mhz !== undefined) {
      if (freqMhz > c.muf_mhz) {
        return `Die Frequenz liegt über der höchsten heute nutzbaren (${num(c.muf_mhz)} MHz) — die Welle geht ins All statt zurückzukommen.`;
      }
      if (freqMhz < c.luf_mhz) {
        return `Die Frequenz liegt unter der niedrigsten heute brauchbaren (${num(c.luf_mhz)} MHz) — die Absorption frisst das Signal.`;
      }
    }
    if (c.darkness !== undefined && c.darkness < 0.7) {
      return "Die Strecke liegt noch zu weitgehend im Tageslicht — auf diesen Bändern trägt die Raumwelle erst in der Dunkelheit.";
    }
    return "Heute Abend außerhalb dessen, was die Ausbreitung hergibt.";
  }

  const reasons = [];
  if (station.band_class === "sw" && c.owf_mhz !== undefined) {
    const distance = Math.abs(freqMhz - c.owf_mhz);
    if (distance < 1.0) reasons.push("die Frequenz liegt fast genau im günstigsten Bereich");
    else if (freqMhz < c.owf_mhz) reasons.push(`die Frequenz liegt im nutzbaren Fenster (${num(c.luf_mhz)}–${num(c.muf_mhz)} MHz)`);
    else reasons.push("die Frequenz liegt am oberen Rand des nutzbaren Fensters");
  }
  if (c.darkness !== undefined) {
    if (c.darkness >= 0.99) reasons.push("die Strecke liegt vollständig im Dunkeln");
    else if (c.darkness >= 0.7) reasons.push(`die Strecke liegt zu ${Math.round(c.darkness * 100)} % im Dunkeln`);
  }
  if (c.field !== undefined) {
    if (c.field >= 0.8) reasons.push("Leistung und Entfernung sprechen deutlich dafür");
    else if (c.field <= 0.35) reasons.push("Leistung und Entfernung sprechen eher dagegen");
  }
  if (c.geomagnetic !== undefined && c.geomagnetic < 0.85) {
    reasons.push(`die geomagnetische Störung kostet auf diesem Weg rund ${Math.round((1 - c.geomagnetic) * 100)} %`);
  }
  if (c.aiming !== undefined && c.aiming < 0.6) {
    reasons.push("die Sendeantenne strahlt in eine andere Richtung");
  }

  if (!reasons.length) return "Solide Bedingungen ohne besonderen Ausschlag in eine Richtung.";
  const head = reasons[0][0].toUpperCase() + reasons[0].slice(1);
  return reasons.length === 1 ? head + "." : head + ", " + reasons.slice(1).join(", ") + ".";
}

/** Beschriftungen für die Einzelwerte im Detailfenster. */
const COMPONENT_LABELS = {
  darkness: ["Dunkelanteil der Strecke", (v) => `${Math.round(v * 100)} %`],
  field: ["Feldstärke-Schätzung", (v) => `${Math.round(v * 100)} von 100`],
  geomagnetic: ["Geomagnetischer Abschlag", (v) => `${Math.round((1 - v) * 100)} % Verlust`],
  aiming: ["Ausrichtung der Sendeantenne", (v) => (v >= 0.99 ? "ungerichtet oder auf uns" : `${Math.round(v * 100)} % der Hauptkeule`)],
  muf_mhz: ["Höchste nutzbare Frequenz", (v) => `${num(v, 2)} MHz`],
  owf_mhz: ["Günstigste Arbeitsfrequenz", (v) => `${num(v, 2)} MHz`],
  luf_mhz: ["Niedrigste brauchbare Frequenz", (v) => `${num(v, 2)} MHz`],
  hops: ["Sprünge an der Ionosphäre", (v) => `${v}`],
  seasonal_noise: ["Jahreszeitliches Rauschen", (v) => `${Math.round((1 - v) * 100)} % Abschlag`],
};

/** Ist das Bulletin von heute? */
function isCurrent(bulletinDate, now) {
  const iso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  return bulletinDate === iso;
}

/* ---------- Zustand ---------- */

const state = {
  bulletin: null,
  stations: {},
  bucket: DEFAULT_KP,
  liveKp: null,
  kpNote: "",
};

/* ---------- Detailfenster ---------- */

const LANGUAGE_NAMES = { de: "Deutsch", en: "Englisch", fr: "Französisch", nl: "Niederländisch" };

function closeModal() {
  const existing = document.getElementById("modal");
  if (existing) existing.remove();
  document.body.classList.remove("modal-open");
}

/**
 * Öffnet ein Overlay. Bewusst schlicht gehalten: schliessen per Knopf,
 * per Klick auf den Hintergrund und per Escape - wer eines davon vergisst,
 * baut eine Falle für den, der gerade einhändig am Radio sitzt.
 */
function openModal(title, buildBody) {
  closeModal();

  const overlay = el("div", "modal-overlay");
  overlay.id = "modal";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", title);

  const sheet = el("div", "modal-sheet");
  const head = el("div", "modal-head");
  head.appendChild(el("h3", null, title));

  const close = el("button", "modal-close", "\u00d7");
  close.setAttribute("aria-label", "Schließen");
  close.addEventListener("click", closeModal);
  head.appendChild(close);
  sheet.appendChild(head);

  const body = el("div", "modal-body");
  buildBody(body);
  sheet.appendChild(body);

  overlay.appendChild(sheet);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeModal();
  });
  document.body.appendChild(overlay);
  document.body.classList.add("modal-open");
  close.focus();
}

function defRow(parent, term, value) {
  const row = el("div", "def-row");
  row.appendChild(el("span", "def-term", term));
  row.appendChild(el("span", "def-value", value));
  parent.appendChild(row);
}

function openKpInfo() {
  openModal("Geomagnetische Lage", (body) => {
    const description = kpDescription(state.bucket);
    body.appendChild(
      el("p", null,
        `Der Kp-Index misst, wie stark das Erdmagnetfeld gerade gestört ist — auf einer Skala von 0 bis 9. ` +
        `Er wird alle drei Stunden weltweit aus Messstationen bestimmt.`)
    );
    body.appendChild(
      el("p", null,
        `Für den Empfang zählt das, weil Störungen vor allem Funkwege über hohe Breiten treffen: ` +
        `Was über Skandinavien oder Grönland läuft, bricht zuerst weg, während Wege nach Süden ` +
        `weitgehend unbeeindruckt bleiben.`)
    );

    const now = el("div", "kp-now");
    now.appendChild(el("strong", null, `Aktuell Kp ${state.bucket} — ${description.label}`));
    now.appendChild(el("div", null, description.text));
    body.appendChild(now);

    body.appendChild(
      el("p", "modal-aside",
        `Der Regler verändert nicht die Messung, sondern zeigt, wie die Rangfolge bei einer anderen Lage aussähe. ` +
        `Für jede Stufe von 0 bis 9 wurde heute früh ein eigenes Ergebnis vorberechnet.`)
    );
  });
}

function openStationDetail(row) {
  const { entry, station, slot } = row;
  openModal(station.name, (body) => {
    const freq = formatFrequency(station.freq_khz);
    defRow(body, "Frequenz", `${freq.value} ${freq.unit}`);
    if (station.language) defRow(body, "Sprache", LANGUAGE_NAMES[station.language] || station.language);
    if (station.site_name) defRow(body, "Sendestandort", station.site_name);
    defRow(body, "Entfernung", `${Math.round(station.distance_km)} km`);
    defRow(body, "Peilung", `${Math.round(station.bearing_deg)}\u00b0`);
    if (station.null_bearings_deg) {
      defRow(body, "Loop-Nullstellen",
        station.null_bearings_deg.map((d) => `${Math.round(d)}\u00b0`).join(" und "));
    }
    if (station.power_kw) defRow(body, "Sendeleistung", `${station.power_kw} kW`);

    body.appendChild(el("h4", null, "Bewertung"));
    defRow(body, "Punktzahl", `${num(slot.score)} von 100`);
    defRow(body, "Bestes Fenster", slot.t);

    const explanation = el("p", "modal-explain", scoreExplanation(slot, station));
    body.appendChild(explanation);

    const components = slot.components || {};
    const known = Object.keys(COMPONENT_LABELS).filter((k) => components[k] !== undefined);
    if (known.length) {
      const list = el("div", "def-list");
      for (const key of known) {
        const [label, format] = COMPONENT_LABELS[key];
        defRow(list, label, format(components[key]));
      }
      body.appendChild(list);
    }

    const alternates = row.alternates || [];
    if (alternates.length) {
      body.appendChild(el("h4", null, "Weitere Frequenzen"));
      for (const alt of alternates) {
        const f = formatFrequency(alt.station.freq_khz);
        defRow(body, `${f.value} ${f.unit}`,
          alt.slot.score > 0
            ? `${num(alt.slot.score)} Punkte · ${alt.slot.t}`
            : "heute Abend nicht erreichbar");
      }
      body.appendChild(
        el("p", "modal-aside",
          "Dasselbe Programm, parallel ausgestrahlt. Die Liste zeigt nur die " +
          "heute beste dieser Frequenzen — kommt sie nicht durch, sind das die " +
          "naechsten Versuche.")
      );
    }

    if (entry.rarity) {
      body.appendChild(el("h4", null, "Seltenheit"));
      defRow(body, "Grundwert", `${Math.round(entry.rarity.baseline * 100)} von 100`);
      if (entry.rarity.reason) defRow(body, "Heute", entry.rarity.reason);
    }

    body.appendChild(
      el("p", "modal-aside",
        "Die Punktzahl ist eine Rangfolge, keine Feldstärkevorhersage — sie beantwortet, " +
        "was heute Abend oben steht, nicht wieviel Mikrovolt ankommen.")
    );
  });
}

/* ---------- Aufbau der Anzeige ---------- */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderDial() {
  const host = document.getElementById("dial");
  host.replaceChildren();

  const rows = [
    ...entriesForBucket(state.bulletin, state.stations, "main", state.bucket),
    ...entriesForBucket(state.bulletin, state.stations, "dx", state.bucket),
  ].filter((r) => r.slot.score > 0);

  if (!rows.length) return;

  // Die Zeichenflaeche wird in echten Pixeln aufgespannt, nicht in
  // gestreckten Einheiten: preserveAspectRatio="none" hatte die Schrift
  // horizontal auf ein Drittel gequetscht, weil das viewBox-Verhaeltnis
  // nicht zum Kasten passte.
  const W = Math.max(280, Math.round(host.clientWidth || 360));
  const H = 150;
  const baseline = 96;
  const ns = "http://www.w3.org/2000/svg";
  const make = (tag, attrs) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };

  const svg = make("svg", {
    viewBox: `0 0 ${W} ${H}`,
    role: "img",
    "aria-label": `${rows.length} empfangbare Stationen, nach Bändern gruppiert`,
  });

  // Nur die tatsaechlich belegten Bereiche bekommen Platz. Zwischen den
  // Abschnitten steht eine Bruchstelle statt leerer Skala.
  const placed = layoutSegments(
    mergeSameBand(frequencySegments(rows.map((r) => r.station.freq_khz)), bandLabel),
    W,
    { gapPx: 18 }
  );

  const best = Math.max(...rows.map((r) => r.slot.score));

  placed.forEach((seg, index) => {
    const g = make("g", { class: "dial-axis" });
    g.appendChild(make("line", { x1: seg.x, y1: baseline, x2: seg.x + seg.width, y2: baseline }));

    // Beschriftung: Bandname und der abgedeckte Frequenzbereich
    const inSeg = rows.filter((r) => r.station.freq_khz >= seg.min && r.station.freq_khz <= seg.max);
    const lo = Math.min(...inSeg.map((r) => r.station.freq_khz));
    const hi = Math.max(...inSeg.map((r) => r.station.freq_khz));
    const mid = seg.x + seg.width / 2;

    const name = make("text", { x: mid, y: baseline + 26, "text-anchor": "middle", class: "dial-band-name" });
    name.textContent = bandLabel(lo);
    g.appendChild(name);

    const range = make("text", { x: mid, y: baseline + 44, "text-anchor": "middle", class: "dial-band-range" });
    range.textContent = lo === hi ? `${Math.round(lo)} kHz` : `${Math.round(lo)}–${Math.round(hi)} kHz`;
    g.appendChild(range);

    svg.appendChild(g);

    // Bruchstelle zwischen zwei Abschnitten
    if (index > 0) {
      const prev = placed[index - 1];
      const bx = (prev.x + prev.width + seg.x) / 2;
      const brk = make("g", { class: "dial-break" });
      brk.appendChild(make("path", { d: `M${bx - 5} ${baseline + 6} l6 -12 M${bx + 1} ${baseline + 6} l6 -12` }));
      svg.appendChild(brk);
    }
  });

  rows.forEach((row, i) => {
    const x = positionInSegments(row.station.freq_khz, placed);
    if (x === null) return;
    const height = 14 + 60 * (row.slot.score / (best || 1));
    const g = make("g", { class: `dial-tick${row.slot.score < 25 ? " is-quiet" : ""}` });
    g.style.animationDelay = `${Math.min(i * 40, 500)}ms`;
    g.appendChild(make("line", { x1: x, y1: baseline, x2: x, y2: baseline - height }));

    // Grosszuegige, unsichtbare Trefferflaeche: ein 3 px breiter Strich
    // laesst sich mit dem Finger nicht treffen.
    const hit = make("rect", {
      x: x - 14, y: baseline - height - 8, width: 28, height: height + 16,
      fill: "transparent", style: "cursor:pointer",
    });
    hit.addEventListener("click", () => openStationDetail(row));
    g.appendChild(hit);

    svg.appendChild(g);
  });

  host.appendChild(svg);
}

function bearingIndicator(deg) {
  const ns = "http://www.w3.org/2000/svg";
  const wrap = el("span", "bearing");
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("aria-hidden", "true");

  const circle = document.createElementNS(ns, "circle");
  circle.setAttribute("cx", "10");
  circle.setAttribute("cy", "10");
  circle.setAttribute("r", "8.5");
  svg.appendChild(circle);

  const rad = ((deg - 90) * Math.PI) / 180;
  const line = document.createElementNS(ns, "line");
  line.setAttribute("x1", "10");
  line.setAttribute("y1", "10");
  line.setAttribute("x2", String(10 + 7.5 * Math.cos(rad)));
  line.setAttribute("y2", String(10 + 7.5 * Math.sin(rad)));
  svg.appendChild(line);

  wrap.appendChild(svg);
  wrap.appendChild(document.createTextNode(`Loop ${Math.round(deg)}°`));
  return wrap;
}

function renderStation(row, now) {
  const { entry, station, slot } = row;
  const node = el("article", `station${slot.score <= 0 ? " is-dead" : ""}`);
  node.tabIndex = 0;
  node.setAttribute("role", "button");
  node.setAttribute("aria-label", `${station.name}, Details öffnen`);
  node.addEventListener("click", () => openStationDetail(row));
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openStationDetail(row);
    }
  });

  const freq = formatFrequency(station.freq_khz);
  const freqNode = el("div", "freq num", freq.value);
  freqNode.appendChild(el("span", null, freq.unit));
  node.appendChild(freqNode);

  node.appendChild(el("div", "name", station.name));

  const when = el("div", "when");
  if (slot.score <= 0) {
    when.textContent = "heute Abend nicht erreichbar";
  } else {
    const phrase = timingPhrase(slot.t, now);
    const span = el("span", phrase.current ? "now" : null, phrase.text);
    when.appendChild(span);
    if (station.band_class !== "sw" && typeof station.bearing_deg === "number") {
      when.appendChild(document.createTextNode(" · "));
      when.appendChild(bearingIndicator(station.bearing_deg));
    }
  }
  node.appendChild(when);

  const detail = el("div");
  if (slot.score > 0) {
    const meter = el("div", "meter");
    const fill = el("i");
    fill.style.width = `${Math.max(2, Math.min(100, slot.score))}%`;
    meter.appendChild(fill);
    detail.appendChild(meter);
  }
  const reason = entry.rarity && entry.rarity.reason;
  if (reason) detail.appendChild(el("div", "aside", reason));

  const alternates = row.alternates || [];
  if (alternates.length) {
    detail.appendChild(el("div", "aside", alternateHint(alternates)));
  }

  node.appendChild(detail);

  return node;
}

function renderLists() {
  const host = document.getElementById("lists");
  host.replaceChildren();
  const now = new Date();

  const sections = [
    ["main", "Hauptliste", "Keine Station aus Deinen Sprachen heute Abend."],
    ["dx", "DX-Block", "Heute nichts Ungewöhnliches in Reichweite."],
  ];

  for (const [kind, title, emptyText] of sections) {
    const rows = entriesForBucket(state.bulletin, state.stations, kind, state.bucket);
    host.appendChild(el("h2", null, title));
    if (!rows.length) {
      host.appendChild(el("p", "empty", emptyText));
      continue;
    }
    for (const row of rows) host.appendChild(renderStation(row, now));
  }
}

function renderKp() {
  document.getElementById("kp-value").textContent = `Kp ${state.bucket}`;
  document.getElementById("kp-source").textContent = state.kpNote;
  document.getElementById("kp-slider").value = String(state.bucket);
}

function renderBanner() {
  const banner = document.getElementById("banner");
  banner.replaceChildren();
  if (!isCurrent(state.bulletin.date, new Date())) {
    banner.textContent =
      `Dieses Bulletin ist vom ${state.bulletin.date}, nicht von heute. ` +
      `Der Morgenlauf ist vermutlich ausgefallen — die Zeiten unten stimmen nicht mehr.`;
  }
}

function renderFooter() {
  const footer = document.getElementById("footer");
  footer.replaceChildren();
  const bits = [];
  if (state.bulletin.f107_flux) bits.push(`Solarer Fluss ${Math.round(state.bulletin.f107_flux)}`);
  bits.push(`Sendeplan ${state.bulletin.eibi_season.toUpperCase()}`);
  if (state.bulletin.days_until_season_change <= 14) {
    bits.push(`Saisonwechsel in ${state.bulletin.days_until_season_change} Tagen`);
  }
  footer.textContent = bits.join(" · ");
}

function renderAll() {
  renderKp();
  renderDial();
  renderLists();
}

/* ---------- Laden ---------- */

async function loadLiveKp() {
  // Direkter Abruf bei der NOAA: der frischeste Wert, aber der einzige
  // Teil des Frontends, der eine fremde Herkunft anspricht - und damit
  // der wahrscheinlichste Ausfall. Ob der Browser ihn zulaesst, haengt
  // an den Einstellungen des NOAA-Servers.
  const response = await fetch(KP_URL, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const rows = await response.json();
  const newest = rows[rows.length - 1];
  const value = Number(newest.Kp !== undefined ? newest.Kp : newest.kp);
  if (!Number.isFinite(value)) throw new Error("Kein brauchbarer Kp-Wert");
  return { value, source: "live" };
}

async function loadSnapshotKp() {
  // Rueckfallweg aus dem eigenen Repository, alle drei Stunden von einem
  // eigenen Lauf aufgefrischt. Gleiche Herkunft, wird also nie blockiert.
  const response = await fetch("space-weather.json", { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  const value = Number(data.kp);
  if (!Number.isFinite(value)) throw new Error("Kein brauchbarer Kp-Wert");
  return { value, source: "snapshot", fetchedAt: data.fetched_at };
}

/** Beschreibt, woher der angezeigte Kp-Wert stammt. */
function kpNoteFor(result) {
  const value = result.value.toFixed(2).replace(".", ",");
  if (result.source === "live") {
    return `Gemessen: ${value} — verschieben, um andere Lagen zu vergleichen`;
  }
  return `Gemessen: ${value}, Stand der letzten vollen Stunden — verschieben zum Vergleichen`;
}

async function loadKp() {
  try {
    return await loadLiveKp();
  } catch (error) {
    return await loadSnapshotKp();
  }
}

async function start() {
  try {
    const [bulletin, stations] = await Promise.all([
      fetch("bulletin.json", { cache: "no-store" }).then((r) => r.json()),
      fetch("stations.json", { cache: "no-store" }).then((r) => r.json()),
    ]);
    state.bulletin = bulletin;
    state.stations = stations.stations || {};
  } catch (error) {
    document.getElementById("lists").replaceChildren(
      el("p", "empty", "Das Bulletin lässt sich nicht laden. Prüfe, ob der Morgenlauf durchgelaufen ist.")
    );
    return;
  }

  document.getElementById("bulletin-date").textContent = new Date(
    state.bulletin.date + "T12:00:00"
  ).toLocaleDateString("de-DE", { day: "numeric", month: "long" });

  renderBanner();
  renderFooter();

  state.kpNote = "Kp wird geholt …";
  renderAll();

  try {
    const measured = await loadKp();
    state.liveKp = measured.value;
    state.bucket = kpBucket(measured.value);
    state.kpNote = kpNoteFor(measured);
  } catch (error) {
    state.kpNote = "Aktueller Wert nicht abrufbar, angenommen wird eine ruhige Lage";
  }
  renderAll();

  document.getElementById("kp-info").addEventListener("click", openKpInfo);

  // Die Skala rechnet in echten Pixeln - beim Drehen des Geräts muss sie neu.
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderDial, 150);
  });

  document.getElementById("kp-slider").addEventListener("input", (event) => {
    state.bucket = Number(event.target.value);
    if (state.liveKp !== null && state.bucket !== kpBucket(state.liveKp)) {
      state.kpNote = `Angenommen — gemessen sind ${state.liveKp.toFixed(2).replace(".", ",")}`;
    } else if (state.liveKp !== null) {
      state.kpNote = `Gemessen: ${state.liveKp.toFixed(2).replace(".", ",")} — verschieben, um andere Lagen zu vergleichen`;
    }
    renderAll();
  });
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", start);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });
}

if (typeof module !== "undefined") {
  module.exports = {
    kpBucket,
    num,
    kpNoteFor,
    kpDescription,
    scoreExplanation,
    frequencySegments,
    layoutSegments,
    mergeSameBand,
    positionInSegments,
    bandLabel,
    COMPONENT_LABELS,
    formatFrequency,
    timingPhrase,
    dialPosition,
    stationKey,
    collapseByStation,
    alternateHint,
    entriesForBucket,
    isCurrent,
  };
}
