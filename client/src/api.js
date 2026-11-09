import { useEffect, useState } from 'react';

// Every call goes to the FastAPI server under /api (proxied by Vite in development).
async function request(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Can't reach the API server. Is it running? (cd server && uvicorn api:app --reload)");
  }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const d = data?.detail;
    if (typeof d === 'string') throw new Error(d);
    if (Array.isArray(d)) throw new Error(d.map(x => `${x.loc?.slice(-1)[0] ?? ''} ${x.msg}`.trim()).join('; '));
    if (res.status >= 500 && !data) throw new Error("Can't reach the API server. Is it running? (cd server && uvicorn api:app --reload)");
    throw new Error(`Request failed (${res.status})`);
  }
  return data;
}

const q = params => new URLSearchParams(Object.entries(params).filter(([, v]) => v != null && v !== '')).toString();

export const api = {
  health: () => request('GET', '/api/health'),
  stations: () => request('GET', '/api/stations'),
  surveyors: () => request('GET', '/api/surveyors'),
  day: date => request('GET', `/api/day?${q({ date })}`),
  routes: (date, team) => request('GET', `/api/routes?${q({ date, team })}`),
  clockIn: body => request('POST', '/api/clock-in', body),
  missed: body => request('POST', '/api/missed', body),
  resetDay: date => request('DELETE', `/api/routes?${q({ date })}`),
  removeTeam: (date, team) => request('DELETE', `/api/routes?${q({ date, team })}`),
  shifts: () => request('GET', '/api/shifts'),
  compare: shift => request('GET', `/api/shifts/${shift}/compare`),
};

// Load data when deps change. Returns { data, error, loading, reload }.
export function useApi(fn, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [n, setN] = useState(0);
  useEffect(() => {
    let live = true;
    setState(s => ({ ...s, loading: true }));
    fn().then(
      data => live && setState({ data, error: null, loading: false }),
      error => live && setState({ data: null, error, loading: false }),
    );
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  return { ...state, reload: () => setN(x => x + 1) };
}

// A value remembered in this browser (team name, last times). Falls back to memory if storage is blocked.
export function useStored(key, initial) {
  const [v, setV] = useState(() => {
    try { const s = localStorage.getItem(key); return s == null ? initial : JSON.parse(s); } catch { return initial; }
  });
  const set = x => { setV(x); try { localStorage.setItem(key, JSON.stringify(x)); } catch { /* ignore */ } };
  return [v, set];
}
