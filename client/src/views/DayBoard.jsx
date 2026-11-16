import { Fragment, useState } from 'react';
import { api, useApi } from '../api';
import CoverageHeat from '../components/CoverageHeat';
import LinePill from '../components/LinePill';
import RouteMap from '../components/RouteMap';
import RouteTable from '../components/RouteTable';
import { Stat } from '../components/Stats';
import { LINE_KEYS, lineKey } from '../map/match';
import { fmt, hours } from '../time';

// Dispatcher view: every team's route on one date, coverage, and the day's assignments and events.
export default function DayBoard({ date }) {
  const routes = useApi(() => api.routes(date), [date]);
  const day = useApi(() => api.day(date), [date]);
  const [open, setOpen] = useState(null);       // team whose legs are expanded
  const [hover, setHover] = useState(null);     // [team, leg index | null] shown on the map
  const [msg, setMsg] = useState(null);

  const list = routes.data ?? [];
  const covered = new Set(list.flatMap(r => r.stats.lines.map(l => lineKey({ line: l }))));
  const missing = LINE_KEYS.filter(k => !covered.has(k));
  const scheduled = new Set((day.data?.assignments ?? []).map(a => a.surveyor)).size;
  const niceDate = new Date(`${date}T00:00`).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric', year: 'numeric' });
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
            <h2>{niceDate} <span className="muted sub">{day.data ? `${day.data.dayType} timetable` : ''}</span></h2>
            <div>
              <button type="button" className="small ghost" onClick={routes.reload}>Refresh</button>{' '}
              <button type="button" className="small warn" disabled={!list.length} onClick={resetDay}>Reset day</button>
            </div>
          </div>
          <div className="grid one-row">
            <Stat value={list.length} label="teams clocked in" hint="Teams that have a planned route for this date." />
            <Stat value={scheduled} label={`surveyor${scheduled === 1 ? '' : 's'} scheduled`}
              hint="People with an assignment on file for this date (see On File for This Date)." />
            <Stat value={`${LINE_KEYS.length - missing.length} / ${LINE_KEYS.length}`}
              label={missing.length ? <>lines covered<br />missing {missing.map(k => <LinePill key={k} line={k} />)}</> : 'lines covered'} />
            <Stat value={span ? `${Math.round(100 * onboard / span)}%` : '–'} label="of paid time on board" />
            <Stat value={hours(onboard)} label="on board, all teams" />
            <Stat value={list.reduce((x, r) => x + r.stats.points, 0).toFixed(0)} label="points, all teams"
              hint="The engine's score for how useful the riding time is. Each minute on a train earns points, more on busier trains (more riders to survey). Points shrink by half for each earlier team already on the same line, direction and hour, so spreading teams out scores higher." />
          </div>
        </div>

        <div className="card">
          <h2>Teams <span className="muted sub">click a team to see its legs; hover to show it on the map</span></h2>
          {routes.loading && !list.length ? <p className="muted">Loading…</p> : !list.length ? <p className="muted">No team has clocked in for this date.</p> : (
            <div>
              <table onMouseLeave={() => setHover(null)}>
                <thead><tr><th>Team</th><th>Start / end</th><th>Shift</th><th>On board</th><th>Rides</th><th>Lines</th><th>Points</th><th /></tr></thead>
                <tbody>
                  {list.map(r => (
                    <Fragment key={r.team}>
                      <tr className={`click ${open === r.team ? 'now' : ''}`} onClick={() => setOpen(open === r.team ? null : r.team)} onMouseEnter={() => setHover([r.team, null])}>
                        <td><b>{r.team}</b>{r.legs.some(l => l.replanned) && <span className="tag chg-tag">re-planned</span>}</td>
                        <td className="wrap">{r.startName}{r.end !== r.start ? ` → ${r.endName}` : ''}</td>
                        <td>{fmt(r.clockIn)} – {fmt(r.clockOut)}</td>
                        <td className="num">{r.stats.onboardPct}%</td>
                        <td className="num">{r.stats.rides}</td>
                        <td className="wrap">{r.stats.lines.map(l => <LinePill key={l} line={l} />)}</td>
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
          <h2><span className="hint" title="Each cell is the total minutes all teams combined spent riding that line during that hour (e.g. 45 under 5pm = 45 minutes on that line between 5:00 and 6:00). Only time on board counts, not waiting. Darker = more minutes; – = nobody rode it. Pale or empty cells are lines and hours that got little or no surveying.">Coverage</span> <span className="muted sub">minutes on board by line and hour, all teams</span></h2>
          <CoverageHeat routes={list} />
        </div>

        <div className="card">
          <h2>On File for This Date</h2>
          {day.error && <div className="msg err">{day.error.message}</div>}
          {day.data && <>
            <h3 className="sub">Events</h3>
            {day.data.events.length ? (
              <ul>{day.data.events.map((e, i) => <li key={i}>{[e.sports, e.other].filter(Boolean).join('; ')}{e.location ? ` (${e.location})` : ''}</li>)}</ul>
            ) : <p className="muted">None.</p>}
            <h3 className="sub">Assignments</h3>
            {day.data.assignments.length ? (
              <div className="scroll"><table>
                <thead><tr><th>Surveyor</th><th><span className="hint" title="What this surveyor was scheduled to do that day, from BART's Team Views workbook. A number is a shift from the existing shift library (see the Existing shifts tab); text is any other activity, such as training or office work.">Shift / activity</span></th><th><span className="hint" title="route shift: a shift number found in the shift library. unknown shift: a number that isn't in the library (likely a typo or a removed shift). other activity: anything that isn't a numbered shift.">Kind</span></th><th className="wrap">Comment</th></tr></thead>
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
