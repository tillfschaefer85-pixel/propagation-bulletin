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
  return { value: (khz / 1000).toFixed(3), unit: "MHz" };
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

/** Position einer Frequenz auf der logarithmischen Skala, 0 bis 1. */
function dialPosition(khz, min = 150, max = 30000) {
  const clamped = Math.max(min, Math.min(max, khz));
  return (Math.log10(clamped) - Math.log10(min)) / (Math.log10(max) - Math.log10(min));
}

/** Die Eintraege einer Liste, nach Punktzahl der gewaehlten Kp-Stufe sortiert. */
function entriesForBucket(bulletin, stations, kind, bucket) {
  return (bulletin.entries || [])
    .filter((e) => e.list_kind === kind && e.best_by_kp && stations[e.station_id])
    .map((e) => ({
      entry: e,
      station: stations[e.station_id],
      slot: e.best_by_kp[bucket],
    }))
    .sort((a, b) => b.slot.score - a.slot.score);
}

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

  const W = 1000;
  const H = 108;
  const baseline = 74;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  svg.setAttribute(
    "aria-label",
    `${rows.length} empfangbare Stationen zwischen Langwelle und 30 Megahertz`
  );

  const ns = "http://www.w3.org/2000/svg";
  const make = (tag, attrs) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };

  const axis = make("g", { class: "dial-axis" });
  axis.appendChild(make("line", { x1: 0, y1: baseline, x2: W, y2: baseline }));
  for (const [khz, label] of [[200, "LW"], [1000, "MW"], [6000, "49 m"], [15000, "19 m"]]) {
    const x = dialPosition(khz) * W;
    const t = make("text", { x: Math.min(x, W - 30), y: baseline + 18 });
    t.textContent = label;
    axis.appendChild(t);
  }
  svg.appendChild(axis);

  const best = Math.max(...rows.map((r) => r.slot.score));
  rows.forEach((row, i) => {
    const x = dialPosition(row.station.freq_khz) * W;
    const height = 10 + 44 * (row.slot.score / (best || 1));
    const g = make("g", { class: `dial-tick${row.slot.score < 25 ? " is-quiet" : ""}` });
    g.style.animationDelay = `${Math.min(i * 45, 600)}ms`;
    g.appendChild(make("line", { x1: x, y1: baseline, x2: x, y2: baseline - height }));
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
}

if (typeof module !== "undefined") {
  module.exports = {
    kpBucket,
    kpNoteFor,
    formatFrequency,
    timingPhrase,
    dialPosition,
    entriesForBucket,
    isCurrent,
  };
}
