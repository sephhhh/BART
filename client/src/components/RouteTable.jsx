import { fmt } from '../time';
import LinePill from './LinePill';

// One route, leg by leg. onHover(i | null) highlights a leg on the map; action(leg, i) adds a trailing cell.
export default function RouteTable({ route, onHover = () => {}, action, doneBefore = null }) {
  let n = 0;
  return (
    <div className="scroll">
      <table onMouseLeave={() => onHover(null)}>
        <thead>
          <tr><th>Leg</th><th>Board at</th><th>Get off at</th><th>Depart</th><th>Arrive</th><th>Line</th><th>Direction</th><th>Points</th>{action && <th />}</tr>
        </thead>
        <tbody>
          {route.legs.map((l, i) => {
            const done = doneBefore != null && l.arrive < doneBefore;
            const cls = [l.kind === 'ride' ? '' : l.kind === 'break' ? 'brk' : 'io', l.replanned && 'chg', done && 'done'].filter(Boolean).join(' ');
            const no = l.kind === 'clock_in' ? 0 : l.kind === 'clock_out' ? 99 : ++n;
            return (
              <tr key={i} className={cls} onMouseEnter={() => onHover(i)}>
                <td>{no}</td>
                {l.kind === 'ride' ? <>
                  <td>{l.fromName}</td><td>{l.toName}</td>
                  <td>{fmt(l.depart)}</td><td>{fmt(l.arrive)}</td>
                  <td><LinePill leg={l} /></td><td>{l.direction}</td><td className="num">{l.points?.toFixed(1)}</td>
                </> : l.kind === 'break' ? <>
                  <td colSpan="2">{l.arrive - l.depart}-minute break at {l.fromName}</td>
                  <td>{fmt(l.depart)}</td><td>{fmt(l.arrive)}</td><td colSpan="3" />
                </> : <>
                  <td colSpan="2">{l.kind === 'clock_in' ? 'Clock in' : 'Clock out'} at {l.fromName}</td>
                  <td>{fmt(l.depart)}</td><td colSpan="4" />
                </>}
                {action && <td>{action(l, i, done)}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
