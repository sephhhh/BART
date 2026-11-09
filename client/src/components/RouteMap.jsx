import { placeLeg, stationXY } from '../map/match';
import { fmt } from '../time';
import LinePill from './LinePill';

// BART system map with a route traced on it. focus = index into route.legs of one ride to show alone.
export default function RouteMap({ route, focus = null, empty = 'Pick a team or shift to see where it goes.' }) {
  const focused = route && focus != null && route.legs[focus]?.kind === 'ride' ? route.legs[focus] : null;
  const rides = !route ? [] : focused ? [focused] : route.legs.filter(l => l.kind === 'ride');
  const placed = rides.map(placeLeg);
  const shown = placed.filter(Boolean);
  const missing = placed.length - shown.length;
  const pts = P => P.map(p => p.join(',')).join(' ');

  // rings: every place this route gets on or off; the start/end station is green
  const home = route && stationXY(route.startName);
  const rings = new Map();
  shown.forEach(p => { rings.set(p.from.join(), p.from); rings.set(p.to.join(), p.to); });
  if (home && !focused) rings.set(home.join(), home);

  return (
    <aside className="card map">
      <h2>Route map</h2>
      <div className="muted map-head">
        {!route ? empty : focused
          ? <>Leg: <LinePill leg={focused} /> {focused.fromName} → {focused.toName}, {fmt(focused.depart)}–{fmt(focused.arrive)}</>
          : <>{route.team}: {route.startName}, {fmt(route.clockIn)} – {fmt(route.clockOut)}</>}
      </div>
      <svg viewBox="40 25 930 770" fill="none" strokeLinecap="round" strokeLinejoin="round" role="img" aria-label="BART system map">
        <image href={`${import.meta.env.BASE_URL}bart-map.png`} width="1001" height="804" />
        {route && <rect width="1001" height="804" fill="#fff" opacity=".55" />}
        {shown.map((p, i) => <polyline key={`o${i}`} points={pts(p.points)} stroke="#0b2545" strokeWidth="11" />)}
        {shown.map((p, i) => <polyline key={`c${i}`} points={pts(p.points)} stroke={p.color} strokeWidth="7" />)}
        {[...rings.values()].map(([x, y]) => {
          const isHome = home && x === home[0] && y === home[1];
          return <circle key={`${x},${y}`} cx={x} cy={y} r={isHome ? 9 : 6} fill={isHome ? '#1f9d55' : '#fff'} stroke="#0b2545" strokeWidth="3" />;
        })}
      </svg>
      {missing > 0 && <p className="muted small">{missing} ride{missing > 1 ? 's' : ''} not drawn: station not on this map.</p>}
      <div className="map-key">
        <span><svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7" fill="#1f9d55" stroke="#0b2545" strokeWidth="3" /></svg>Start / end station</span>
        <span><svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="6" fill="#fff" stroke="#0b2545" strokeWidth="3" /></svg>Change trains here</span>
      </div>
      <p className="muted small">BART system map, © BART 2025. Hover a leg to show it alone.</p>
    </aside>
  );
}
