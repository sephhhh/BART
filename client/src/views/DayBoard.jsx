import { Fragment, useState } from 'react';
import { api, useApi } from '../api';
import CoverageHeat from '../components/CoverageHeat';
import LinePill from '../components/LinePill';
import RouteMap from '../components/RouteMap';
import RouteTable from '../components/RouteTable';
import { Stat } from '../components/Stats';
import { fmt, hours } from '../time';

// Dispatcher view: every team's route on one date, coverage, and the day's assignments and events.
export default function DayBoard({ date }) {
  const routes = useApi(() => api.routes(date), [date]);
  const day = useApi(() => api.day(date), [date]);
  const [open, setOpen] = useState(null);       // team whose legs are expanded
  const [hover, setHover] = useState(null);     // [team, leg index | null] shown on the map
  const [msg, setMsg] = useState(null);

  const list = routes.data ?? [];
  const lines = new Set(list.flatMap(r => r.stats.lines));
  const onboard = list.reduce((x, r) => x + r.stats.onboardMin, 0);
  const span = list.reduce((x, r) => x + r.stats.spanMin, 0);
  const shownTeam = hover?.[0] ?? open;
  const shown = list.find(r => r.team === shownTeam);

  async function act(fn, text) {
    setMsg(null);
    try { await fn(); setMsg({ ok: true, text }); routes.reload(); }
    catch (e) { setMsg({ ok: false, text: e.message }); }
  }
  const resetDay = () => confirm(`Delete all ${list.length} routes planned for ${date}? This can't be undone.`) &&
    act(() => api.resetDay(date), `Cleared every route for ${date}.`);
  const removeTeam = team => confirm(`Delete ${team}'s route for ${date}?`) &&
    act(() => api.removeTeam(date, team), `Removed ${team}'s route.`);

  return (
    <div className="two-col">
      <div>
        {msg && <div className={`msg ${msg.ok ? 'ok' : 'err'}`}>{msg.text}</div>}
        {routes.error && <div className="msg err">{routes.error.message}</div>}

        <div className="card">
          <div className="shift-head">
            <h2>{date} <span className="muted sub">{day.data ? `${day.data.dayType} timetable` : ''}</span></h2>
            <div>
              <button type="button" className="small ghost" onClick={routes.reload}>Refresh</button>{' '}
              <button type="button" className="small warn" disabled={!list.length} onClick={resetDay}>Reset day</button>
            </div>
          </div>
          <div className="grid">
            <Stat value={list.length} label="teams planned" />
            <Stat value={`${lines.size} / 5`} label="lines covered" />
            <Stat value={span ? `${Math.round(100 * onboard / span)}%` : '–'} label="of paid time on board" />
            <Stat value={hours(onboard)} label="on board, all teams" />
            <Stat value={list.reduce((x, r) => x + r.stats.points, 0).toFixed(0)} label="points, all teams" />
          </div>
        </div>

        <div className="card">
          <h2>Teams <span className="muted sub">click a team to see its legs; hover to show it on the map</span></h2>
          {routes.loading && !list.length ? <p className="muted">Loading…</p> : !list.length ? <p className="muted">No team has clocked in for this date.</p> : (
            <div className="scroll">
              <table onMouseLeave={() => setHover(null)}>
                <thead><tr><th>Team</th><th>Start / end</th><th>Shift</th><th>On board</th><th>Rides</th><th>Lines</th><th>Points</th><th /></tr></thead>
                <tbody>
                  {list.map(r => (
                    <Fragment key={r.team}>
                      <tr className={`click ${open === r.team ? 'now' : ''}`} onClick={() => setOpen(open === r.team ? null : r.team)} onMouseEnter={() => setHover([r.team, null])}>
                        <td><b>{r.team}</b>{r.legs.some(l => l.replanned) && <span className="tag chg-tag">re-planned</span>}</td>
                        <td>{r.startName}{r.end !== r.start ? ` → ${r.endName}` : ''}</td>
                        <td>{fmt(r.clockIn)} – {fmt(r.clockOut)}</td>
                        <td className="num">{r.stats.onboardPct}%</td>
                        <td className="num">{r.stats.rides}</td>
                        <td>{r.stats.lines.map(l => <LinePill key={l} line={l} />)}</td>
                        <td className="num">{r.stats.points.toFixed(1)}</td>
                        <td><button type="button" className="small warn" onClick={e => { e.stopPropagation(); removeTeam(r.team); }}>Delete</button></td>
                      </tr>
                      {open === r.team && (
                        <tr className="nested"><td colSpan="8">
                          <RouteTable route={r} onHover={i => setHover([r.team, i])} />
                        </td></tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="card">
          <h2>Coverage <span className="muted sub">minutes on board by line and hour, all teams</span></h2>
          <CoverageHeat routes={list} />
        </div>

        <div className="card">
          <h2>On file for this date</h2>
          {day.error && <div className="msg err">{day.error.message}</div>}
          {day.data && <>
            <h3 className="sub">Events</h3>
            {day.data.events.length ? (
              <ul>{day.data.events.map((e, i) => <li key={i}>{[e.sports, e.other].filter(Boolean).join('; ')}{e.location ? ` (${e.location})` : ''}</li>)}</ul>
            ) : <p className="muted">None.</p>}
            <h3 className="sub">Assignments</h3>
            {day.data.assignments.length ? (
              <div className="scroll"><table>
                <thead><tr><th>Surveyor</th><th>Shift / activity</th><th>Kind</th><th className="wrap">Comment</th></tr></thead>
                <tbody>{day.data.assignments.map((a, i) => <tr key={i}><td>{a.surveyor}</td><td>{a.what}</td><td>{a.kind}</td><td className="wrap">{a.comment}</td></tr>)}</tbody>
              </table></div>
            ) : <p className="muted">None.</p>}
          </>}
        </div>
      </div>
      <RouteMap route={shown} focus={hover?.[0] === shownTeam ? hover?.[1] : null} empty="Hover or click a team to see its route." />
    </div>
  );
}
