import { LINES } from '../map/geometry';
import { LINE_KEYS, lineKey } from '../map/match';
import LinePill from './LinePill';

// Minutes on board per line and hour, across every team's route that day. Darker = more coverage.
export default function CoverageHeat({ routes }) {
  const rides = routes.flatMap(r => r.legs.filter(l => l.kind === 'ride'));
  if (!rides.length) return <p className="muted">No rides planned yet.</p>;
  const lo = Math.floor(Math.min(...rides.map(l => l.depart)) / 60);
  const hi = Math.floor((Math.max(...rides.map(l => l.arrive)) - 1) / 60);
  const hrs = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
  const grid = {};
  for (const l of rides) {
    const k = lineKey(l) ?? 'other';
    for (const h of hrs) {
      const m = Math.min(l.arrive, (h + 1) * 60) - Math.max(l.depart, h * 60);
      if (m > 0) grid[`${k}|${h}`] = (grid[`${k}|${h}`] ?? 0) + m;
    }
  }
  const keys = [...LINE_KEYS, ...(Object.keys(grid).some(g => g.startsWith('other|')) ? ['other'] : [])];
  const max = Math.max(1, ...Object.values(grid));
  const label = h => { const x = h % 24; return `${x % 12 || 12}${x < 12 ? 'a' : 'p'}`; };
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
                return <td key={h} className="c" style={{ background: `rgba(11,92,173,${a.toFixed(2)})`, color: a > 0.45 ? '#fff' : undefined }}>{v || '–'}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
