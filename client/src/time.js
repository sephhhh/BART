// Times are minutes after midnight, as in the database. Values past 1440 are after midnight.
export const fmt = t => {
  if (t == null) return '';
  t = Math.round(t);
  const h = Math.floor(t / 60) % 24;
  return `${h % 12 || 12}:${String(t % 60).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
};
export const toHM = t => `${String(Math.floor(t / 60) % 24).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
export const parseHM = s => { const [h, m] = s.split(':').map(Number); return h * 60 + m; };
export const hours = m => `${(m / 60).toFixed(1)} h`;

export const today = () => {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 10);
};
export const nowMin = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); };
export const isWeekend = iso => [0, 6].includes(new Date(iso + 'T12:00').getDay());
