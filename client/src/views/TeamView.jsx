import { useState } from 'react';
import { api, useApi, useStored } from '../api';
import RouteMap from '../components/RouteMap';
import RouteTable from '../components/RouteTable';
import { RouteStats } from '../components/Stats';
import { fmt, nowMin, parseHM, toHM, today } from '../time';

export function StationSelect({ stations, value, onChange }) {
  const homes = stations.filter(s => s.homeShifts > 0).sort((a, b) => b.homeShifts - a.homeShifts);
  return (
    <select value={value} onChange={e => onChange(e.target.value)} required>
      <option value="" disabled>Choose a station</option>
      {homes.length > 0 && (
        <optgroup label="Start stations used by existing shifts">
          {homes.map(s => <option key={`h${s.code}`} value={s.code}>{s.name}</option>)}
        </optgroup>
      )}
      <optgroup label="All stations">
        {stations.map(s => <option key={s.code} value={s.code}>{s.name}</option>)}
      </optgroup>
    </select>
  );
}

export default function TeamView({ date, stations }) {
  const routes = useApi(() => api.routes(date), [date]);
  const surveyors = useApi(api.surveyors, []);
  const [team, setTeam] = useStored('bart.team', '');
  const [members, setMembers] = useState(['', '']);
  const [form, setForm] = useStored('bart.clockin', { start: '', end: '', from: '14:10', to: '20:58' });
  const [where, setWhere] = useState({ at: '', time: toHM(nowMin()) });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [hover, setHover] = useState(null);

  const mine = routes.data?.find(r => r.team === team.trim());
  const isToday = date === today();
  const set = patch => setForm({ ...form, ...patch });

  const pickMember = (j, name) => {
    const m = [...members]; m[j] = name; setMembers(m);
    const names = m.filter(Boolean);
    if (names.length) setTeam(names.join('+'));
  };

  async function run(fn, ok) {
    setBusy(true); setMsg(null);
    try {
      const res = await fn();
      setMsg({ ok: true, text: ok(res) });
      routes.reload();
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  }

  const clockIn = e => {
    e.preventDefault();
    if (mine && !confirm(`${team} already has a route for ${date}. Plan a new one from scratch? The current route is replaced.`)) return;
    run(() => api.clockIn({ date, team: team.trim(), start: form.start, end: form.end || form.start,
      fromMin: parseHM(form.from), toMin: parseHM(form.to) }),
    r => `Route planned for ${r.team}: ${r.stats.rides} rides, ${r.stats.onboardPct}% of the shift on board.` +
      (r.balanced ? ' (Balance check: re-planned with a softer coverage discount so this team isn\'t left with scraps.)' : ''));
  };

  const missed = (at, timeMin, label) => run(
    () => api.missed({ date, team: team.trim(), at, timeMin }),
    r => (r.replanned ? `${label} ${r.message} Re-planned legs are blue.`.trim() : r.message));

  return (
    <div className="two-col">
      <div>
        <div className="card">
          <h2>Clock in <span className="muted sub">plans the best route given every team that clocked in before you</span></h2>
          <form className="controls" onSubmit={clockIn}>
            {surveyors.data?.length > 0 && [0, 1].map(j => (
              <label key={j}>Member {j + 1}
                <select value={members[j]} onChange={e => pickMember(j, e.target.value)}>
                  <option value="">–</option>
                  {surveyors.data.map(s => <option key={s.name} value={s.name}>{s.name}{s.status ? ` (${s.status})` : ''}</option>)}
                </select>
              </label>
            ))}
            <label>Team name
              <input value={team} onChange={e => setTeam(e.target.value)} required maxLength={80} placeholder="e.g. Julio+Eric" list="teams-today" />
              <datalist id="teams-today">{routes.data?.map(r => <option key={r.team} value={r.team} />)}</datalist>
            </label>
            <label>Start station<StationSelect stations={stations} value={form.start} onChange={v => set({ start: v, end: form.end || v })} /></label>
            <label>End station<StationSelect stations={stations} value={form.end || form.start} onChange={v => set({ end: v })} /></label>
            <label>Clock in<input type="time" value={form.from} onChange={e => set({ from: e.target.value })} required /></label>
            <label>Clock out<input type="time" value={form.to} onChange={e => set({ to: e.target.value })} required /></label>
            <button disabled={busy || !stations.length}>{busy ? 'Planning…' : mine ? 'Re-plan from scratch' : 'Clock in & plan route'}</button>
          </form>
          <p className="muted small">The route starts 15 min after clock-in (admin time) and ends 5 min before clock-out, with breaks at break-room stations. Takes a few seconds the first time each day type is used.</p>
        </div>

        {msg && <div className={`msg ${msg.ok ? 'ok' : 'err'}`}>{msg.text}</div>}
        {routes.error && <div className="msg err">{routes.error.message}</div>}

        {mine ? (
          <div className="card">
            <div className="shift-head">
              <h2>{mine.team}: {mine.startName} <span className="muted sub">{fmt(mine.clockIn)} – {fmt(mine.clockOut)}</span></h2>
              <button type="button" className="small ghost" onClick={routes.reload}>Refresh</button>
            </div>
            <RouteStats stats={mine.stats} />
            <h3 className="sub">Your route</h3>
            <RouteTable
              route={mine}
              onHover={setHover}
              doneBefore={isToday ? nowMin() : null}
              action={(l, i, done) => l.kind === 'ride' && !done && (
                <button type="button" className="small warn" disabled={busy}
                  onClick={() => missed(l.from, l.depart + 1, `Missed the ${fmt(l.depart)} train at ${l.fromName}.`)}>
                  Missed it
                </button>
              )}
            />

            <h3 className="sub">Off plan? Re-plan from where you are</h3>
            <form className="controls" onSubmit={e => { e.preventDefault(); missed(where.at, parseHM(where.time), ''); }}>
              <label>I'm at<StationSelect stations={stations} value={where.at} onChange={v => setWhere({ ...where, at: v })} /></label>
              <label>Time<input type="time" value={where.time} onChange={e => setWhere({ ...where, time: e.target.value })} required /></label>
              <button className="ghost" disabled={busy || !where.at}>Re-plan the rest of my shift</button>
            </form>
            <p className="muted small">Keeps what you've already ridden and every other team's route. Only your remaining legs change.</p>
          </div>
        ) : team.trim() && !routes.loading && !routes.error && (
          <div className="card muted">{team} has no route for {date} yet. Clock in above.</div>
        )}
      </div>
      <RouteMap route={mine} focus={hover} empty="Clock in to see your route." />
    </div>
  );
}
