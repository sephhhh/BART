import { hours } from '../time';
import LinePill from './LinePill';

export function Stat({ value, label }) {
  return <div className="stat"><b>{value}</b><span>{label}</span></div>;
}

// Summary tiles for one route (engine route or scored existing shift).
export function RouteStats({ stats }) {
  return (
    <div className="grid">
      <Stat value={`${stats.onboardPct}%`} label={`on board (${stats.onboardMin} of ${stats.spanMin} min)`} />
      <Stat value={stats.points.toFixed(1)} label="points (crowding-weighted minutes)" />
      <Stat value={stats.rides} label={`rides, ${stats.breaks} break${stats.breaks === 1 ? '' : 's'}`} />
      <Stat value={stats.shortestTransfer ?? '–'} label="shortest transfer (min)" />
      <Stat value={hours(stats.spanMin)} label="shift length" />
      <div className="stat"><b>{stats.lines.length ? stats.lines.map(l => <LinePill key={l} line={l} />) : '–'}</b><span>lines ridden</span></div>
    </div>
  );
}
