import { LINES, MAP_PATH } from './geometry.js';

export const LINE_KEYS = Object.keys(LINES);

// Which BART line a ride is on, from the timetable's line / direction text (e.g. "Yellow (to AN)").
export function lineKey(leg) {
  const text = `${leg.line ?? ''} ${leg.direction ?? ''}`.toLowerCase();
  return LINE_KEYS.find(k => text.includes(k)) ?? null;
}

// Station names in the database can be longer than the map's ("12th St. Oakland City Center" vs "12th St").
const norm = s => s.toLowerCase().replace(/\bstreet\b/g, 'st').replace(/[^a-z0-9]+/g, ' ').trim();
const ALIASES = {
  'san francisco international airport': 'SFO',
  'sfo airport': 'SFO',
  'san francisco airport': 'SFO',
  'oakland city center 12th st': '12th St',
};
const MAP_NAMES = [...new Set(Object.values(LINES).flatMap(l => l.stations))]
  .map(n => [norm(n), n])
  .sort((a, b) => b[0].length - a[0].length);   // longest first, so "south hayward" wins over "hayward"
const cache = new Map();

export function mapName(dbName) {
  if (!dbName) return null;
  if (cache.has(dbName)) return cache.get(dbName);
  const n = norm(dbName);
  const hit = ALIASES[n]
    ?? MAP_NAMES.find(([k]) => n === k)?.[1]
    ?? MAP_NAMES.find(([k]) => n.startsWith(k + ' '))?.[1]
    ?? MAP_NAMES.find(([k]) => k.startsWith(n + ' '))?.[1]
    ?? null;
  cache.set(dbName, hit);
  return hit;
}

export const stationPoint = (k, i) => {
  const P = MAP_PATH[k];
  return i < P.length ? P[i][0] : P[i - 1][P[i - 1].length - 1];
};

export function stationXY(dbName) {
  const n = mapName(dbName);
  for (const k of LINE_KEYS) {
    const i = LINES[k].stations.indexOf(n);
    if (i >= 0) return stationPoint(k, i);
  }
  return null;
}

// Where to draw one ride: the polyline along the line it rode (or any line serving both stations).
export function placeLeg(leg) {
  const a = mapName(leg.fromName), b = mapName(leg.toName);
  if (!a || !b || a === b) return null;
  const pref = lineKey(leg);
  for (const k of [pref, ...LINE_KEYS.filter(x => x !== pref)].filter(Boolean)) {
    const i = LINES[k].stations.indexOf(a), j = LINES[k].stations.indexOf(b);
    if (i >= 0 && j >= 0) {
      const points = MAP_PATH[k].slice(Math.min(i, j), Math.max(i, j)).flatMap((seg, n) => (n ? seg.slice(1) : seg));
      return { color: LINES[pref ?? k].color, points, from: stationPoint(k, i), to: stationPoint(k, j) };
    }
  }
  return null;
}
