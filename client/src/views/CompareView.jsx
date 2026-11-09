import { useState } from 'react';
import { api, useApi } from '../api';
import RouteMap from '../components/RouteMap';
import RouteTable from '../components/RouteTable';
import { RouteStats } from '../components/Stats';

// The existing hand-built shift library, scored with the engine's rules for comparison.
export default function CompareView() {
  const shifts = useApi(api.shifts, []);
  const [dayType, setDayType] = useState('');
  const [id, setId] = useState(null);
  const [hover, setHover] = useState(null);
  const scored = useApi(() => (id == null ? Promise.resolve(null) : api.compare(id)), [id]);

  const list = (shifts.data ?? []).filter(s => !dayType || s.dayType === dayType);
  const types = [...new Set((shifts.data ?? []).map(s => s.dayType).filter(Boolean))];

  return (
    <div className="two-col">
      <div>
        {scored.data && (
          <div className="card">
            <h2>Shift {scored.data.shift} <span className="muted sub">{scored.data.dayType}, scored with the engine's rules</span></h2>
            {scored.data.issues?.length > 0 && (
              <div className="msg warn-msg">
                <b>Data issues in the shift library</b> (numbers below may be off):
                <ul className="small">{scored.data.issues.map(s => <li key={s}>{s}</li>)}</ul>
              </div>
            )}
            <RouteStats stats={scored.data.stats} />
            <h3 className="sub">Legs</h3>
            <RouteTable route={scored.data} onHover={setHover} />
            {scored.data.skipped.length > 0 && <>
              <p className="muted small">Left out (no matching train in this timetable):</p>
              <ul className="small">{scored.data.skipped.map(s => <li key={s}>{s}</li>)}</ul>
            </>}
          </div>
        )}
        {scored.error && <div className="msg err">{scored.error.message}</div>}

        <div className="card">
          <div className="shift-head">
            <h2>Existing shift library <span className="muted sub">pick one to score it the same way the engine scores its routes</span></h2>
            <label className="inline">Day type{' '}
              <select value={dayType} onChange={e => setDayType(e.target.value)}>
                <option value="">All</option>
                {types.map(t => <option key={t}>{t}</option>)}
              </select>
            </label>
          </div>
          {shifts.error && <div className="msg err">{shifts.error.message}</div>}
          {shifts.loading ? <p className="muted">Loading…</p> : (
            <div className="scroll tall">
              <table>
                <thead><tr><th>Shift</th><th>Day type</th><th>Start station</th><th>Clock in</th><th>Clock out</th><th>Rides</th></tr></thead>
                <tbody>
                  {list.map(s => (
                    <tr key={s.shift} className={`click ${s.shift === id ? 'now' : ''}`} onClick={() => { setId(s.shift); setHover(null); }}>
                      <td>{s.shift}</td><td>{s.dayType}</td><td>{s.home}</td><td>{s.clockIn}</td><td>{s.clockOut}</td><td className="num">{s.rides}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      <RouteMap route={scored.data} focus={hover} empty="Pick a shift to see where it goes." />
    </div>
  );
}
