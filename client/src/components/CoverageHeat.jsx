import { LINES } from '../map/geometry';
import { LINE_KEYS, lineKey } from '../map/match';
import LinePill from './LinePill';

// Minutes on board per line and hour, across every team's route that day. Darker = more coverage.
export default function CoverageHeat({ routes }) {
  const rides = routes.flatMap(r => r.legs.filter(l => l.kind === 'ride').map(l => ({ ...l, team: r.team })));
  if (!rides.length) return <p className="muted">No rides planned yet.</p>;
  // Always show the full survey day (shifts run 5:45am–11:30pm) so uncovered hours stand out; widen if a ride falls outside.
  const lo = Math.min(5, Math.floor(Math.min(...rides.map(l => l.depart)) / 60));
  const hi = Math.max(23, Math.floor((Math.max(...rides.map(l => l.arrive)) - 1) / 60));
  const hrs = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
  const grid = {}, byTeam = {};   // total minutes per cell, and per team within each cell
  for (const l of rides) {
    const k = lineKey(l) ?? 'other';
    for (const h of hrs) {
      const m = Math.min(l.arrive, (h + 1) * 60) - Math.max(l.depart, h * 60);
      if (m <= 0) continue;
      const c = `${k}|${h}`;
      grid[c] = (grid[c] ?? 0) + m;
      byTeam[c] ??= {};
      byTeam[c][l.team] = (byTeam[c][l.team] ?? 0) + m;
    }
  }
  const tip = c => byTeam[c] && Object.entries(byTeam[c]).sort((a, b) => b[1] - a[1]).map(([t, m]) => `${t}: ${m} min`).join('\n');
  const keys = [...LINE_KEYS, ...(Object.keys(grid).some(g => g.startsWith('other|')) ? ['other'] : [])];
  const max = Math.max(1, ...Object.values(grid));
  const label = h => { const x = h % 24; return `${x % 12 || 12}${x < 12 ? 'am' : 'pm'}`; };
  return (
    <div className="scroll">
      <table className="cov">
        <thead><tr><th>Line</th>{hrs.map(h => <th key={h}>{label(h)}</th>)}</tr></thead>
        <tbody>
          {keys.map(k => (
            <tr key={k}>
              <td>{k === 'other' ? 'Other' : <LinePill line={LINES[k].name} />}</td>
              {hrs.map(h => {
                const v = grid[`${k}|${h}`] ?? 0, a = v ? 0.12 + 0.6 * v / max : 0;
                return <td key={h} className="c" title={tip(`${k}|${h}`)} style={{ background: `rgba(11,92,173,${a.toFixed(2)})`, color: a > 0.45 ? '#fff' : undefined }}>{v || '–'}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
