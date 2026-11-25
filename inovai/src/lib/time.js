'use strict';
// Zeitzonen-Helfer ohne externe Bibliotheken (Intl API).
const DAY_KEYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

function parts(date, tz) {
  const f = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', weekday: 'short'
  });
  const o = {};
  for (const p of f.formatToParts(date)) o[p.type] = p.value;
  const wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(o.weekday);
  return { y: +o.year, m: +o.month, d: +o.day, h: +o.hour, min: +o.minute, s: +o.second, weekday: wd };
}

function offsetMs(date, tz) {
  const p = parts(date, tz);
  return Date.UTC(p.y, p.m - 1, p.d, p.h, p.min, p.s) - Math.floor(date.getTime() / 1000) * 1000;
}

// Lokale Wanduhrzeit in einer Zeitzone -> Date (UTC)
function zoned(y, m, d, h, min, tz) {
  const guess = Date.UTC(y, m - 1, d, h, min);
  let ts = guess - offsetMs(new Date(guess), tz);
  ts = guess - offsetMs(new Date(ts), tz);
  return new Date(ts);
}

const hm = (s) => { const [h, m] = String(s).split(':').map(Number); return h * 60 + (m || 0); };
const pad = (n) => String(n).padStart(2, '0');
const ymd = (p) => `${p.y}-${pad(p.m)}-${pad(p.d)}`;

// Verschiebt einen Versandzeitpunkt in das erlaubte Versandfenster (z. B. Mo–Sa 08–20 Uhr).
function intoSendWindow(date, tenant) {
  const w = tenant.sendWindow || {};
  if (!w.start || !w.end) return date;
  const tz = tenant.timezone;
  const days = w.days || [0, 1, 2, 3, 4, 5, 6];
  let d = new Date(date);
  for (let i = 0; i < 9; i++) {
    const p = parts(d, tz);
    const mins = p.h * 60 + p.min;
    if (days.includes(p.weekday) && mins >= hm(w.start) && mins < hm(w.end)) return d;
    if (days.includes(p.weekday) && mins < hm(w.start)) {
      return zoned(p.y, p.m, p.d, Math.floor(hm(w.start) / 60), hm(w.start) % 60, tz);
    }
    // nächster Tag, Fensterbeginn
    const next = zoned(p.y, p.m, p.d, 12, 0, tz);
    next.setUTCDate(next.getUTCDate() + 1);
    const np = parts(next, tz);
    d = zoned(np.y, np.m, np.d, Math.floor(hm(w.start) / 60), hm(w.start) % 60, tz);
    if (days.includes(np.weekday)) return d;
  }
  return d;
}

function fmtDateTime(date, tz, locale = 'de-DE') {
  return new Intl.DateTimeFormat(locale, {
    timeZone: tz, weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit'
  }).format(new Date(date)) + ' Uhr';
}

module.exports = { parts, zoned, hm, ymd, pad, intoSendWindow, fmtDateTime, DAY_KEYS };
