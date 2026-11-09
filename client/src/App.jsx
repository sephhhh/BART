import { useEffect, useState } from 'react';
import { api, useApi } from './api';
import { isWeekend, today } from './time';
import CompareView from './views/CompareView';
import DayBoard from './views/DayBoard';
import TeamView from './views/TeamView';

const TABS = [['team', 'Team view'], ['day', 'Day board'], ['compare', 'Existing shifts']];
const fromHash = () => { const h = location.hash.slice(1); return TABS.some(([k]) => k === h) ? h : 'team'; };

export default function App() {
  const [tab, setTab] = useState(fromHash);
  const [date, setDate] = useState(today);
  const health = useApi(api.health, []);
  const stations = useApi(api.stations, [health.data?.ok]);

  useEffect(() => {
    const on = () => setTab(fromHash());
    addEventListener('hashchange', on);
    return () => removeEventListener('hashchange', on);
  }, []);

  const h = health.data;
  return (
    <>
      <header>
        <div className="head-row">
          <div>
            <h1>BART Passenger Experience Survey: Shift Optimizer</h1>
            <p>
              {health.loading ? 'Connecting…'
                : h?.ok ? `Connected to ${h.database} on ${h.host} · ${h.tables.stations} stations, ${h.tables.shift_library} existing shifts`
                : 'Not connected to the database'}
            </p>
          </div>
          {tab !== 'compare' && (
            <label className="date">Date
              <input type="date" value={date} onChange={e => e.target.value && setDate(e.target.value)} />
              <span>{isWeekend(date) ? 'weekend' : 'weekday'} timetable</span>
            </label>
          )}
        </div>
        <nav className="tabs">
          {TABS.map(([k, label]) => <a key={k} href={`#${k}`} className={tab === k ? 'on' : ''}>{label}</a>)}
        </nav>
      </header>
      <main>
        {health.error && <div className="msg err">{health.error.message}</div>}
        {h && !h.ok && (
          <div className="msg err">
            <b>Database problem:</b> {h.error}
            <br />Host: {h.host}. Check <code>server/.env</code>; RDS is private, so the API has to run on the EC2 instance or through an SSH tunnel (see README).{' '}
            <button type="button" className="small ghost" onClick={health.reload}>Try again</button>
          </div>
        )}
        {tab === 'team' && <TeamView date={date} stations={stations.data ?? []} />}
        {tab === 'day' && <DayBoard date={date} />}
        {tab === 'compare' && <CompareView />}
      </main>
    </>
  );
}
