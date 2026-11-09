import { LINES } from '../map/geometry';
import { lineKey } from '../map/match';

// A colored pill for a line. Pass a ride leg, or a line name/key string.
export default function LinePill({ leg, line }) {
  const key = leg ? lineKey(leg) : lineKey({ line });
  const L = key && LINES[key];
  const label = L ? L.name : (leg?.line ?? line ?? '?');
  return <span className="pill" style={{ background: L ? L.color : '#5d6678' }}>{label}</span>;
}
