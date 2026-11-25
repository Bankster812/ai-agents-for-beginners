'use strict';
// Eingebauter Terminkalender: Öffnungszeiten, Puffer, Vorlaufzeit, Parallel-Termine,
// Abgleich mit externem Kalender (Google/Outlook/Apple über iCal-URL) und ICS-Feed zum Abonnieren.
const db = require('./db');
const { safeFetch } = require('./net');
const { parts, zoned, hm, ymd, DAY_KEYS, fmtDateTime } = require('./time');

const busyCache = new Map(); // tenant -> { at, ranges }

function parseIcsDate(v, tzidDefault) {
  // Formate: 20250101T090000Z | 20250101T090000 (lokal) | 20250101 (ganztägig)
  const m = v.match(/(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?/);
  if (!m) return null;
  const [, y, mo, d, h = '00', mi = '00', , z] = m;
  if (z) return new Date(Date.UTC(+y, +mo - 1, +d, +h, +mi));
  return zoned(+y, +mo, +d, +h, +mi, tzidDefault);
}

function parseIcs(text, tz) {
  const unfolded = text.replace(/\r?\n[ \t]/g, '');
  const ranges = [];
  for (const ev of unfolded.split('BEGIN:VEVENT').slice(1)) {
    const body = ev.split('END:VEVENT')[0];
    if (/^STATUS:CANCELLED/m.test(body) || /^TRANSP:TRANSPARENT/m.test(body)) continue;
    const ds = body.match(/^DTSTART(?:;[^:]*?TZID=([^;:]+))?[^:]*:(.+)$/m);
    const de = body.match(/^DTEND(?:;[^:]*?TZID=([^;:]+))?[^:]*:(.+)$/m);
    if (!ds) continue;
    const start = parseIcsDate(ds[2].trim(), ds[1] || tz);
    let end = de ? parseIcsDate(de[2].trim(), de[1] || tz) : null;
    if (!end && start) end = new Date(start.getTime() + (/T/.test(ds[2]) ? 3600e3 : 86400e3));
    if (start && end) ranges.push([start.getTime(), end.getTime()]);
  }
  return ranges;
}

async function externalBusy(t) {
  const url = t.calendar.busyIcsUrl;
  if (!url) return [];
  const c = busyCache.get(t.id);
  if (c && Date.now() - c.at < 5 * 60e3) return c.ranges;
  try {
    const r = await safeFetch(url, { timeoutMs: 8000, maxBytes: 5 * 1024 * 1024 });
    const ranges = parseIcs(r.text, t.timezone);
    busyCache.set(t.id, { at: Date.now(), ranges });
    return ranges;
  } catch {
    return c ? c.ranges : [];
  }
}

function internalBusy(t, from, to) {
  return db.all("SELECT start, end FROM appointments WHERE tenant=? AND status!='storniert' AND end>? AND start<?",
    t.id, from.toISOString(), to.toISOString()).map((a) => [Date.parse(a.start), Date.parse(a.end)]);
}

// Freie Slots ab "from" für "days" Tage
async function freeSlots(t, { from = new Date(), days, limit = 40 } = {}) {
  const cal = t.calendar;
  const tz = t.timezone;
  const horizon = Math.min(days || cal.horizonDays, cal.horizonDays);
  const earliest = Date.now() + cal.leadTimeHours * 3600e3;
  const end = new Date(from.getTime() + horizon * 86400e3);
  const busy = [...internalBusy(t, from, end), ...(await externalBusy(t))];
  const slots = [];
  const slotMs = cal.slotMinutes * 60e3;
  const bufMs = (cal.bufferMinutes || 0) * 60e3;
  const startP = parts(from, tz);
  for (let i = 0; i <= horizon && slots.length < limit; i++) {
    const noon = zoned(startP.y, startP.m, startP.d, 12, 0, tz);
    noon.setUTCDate(noon.getUTCDate() + i);
    const p = parts(noon, tz);
    if ((cal.closedDates || []).includes(ymd(p))) continue;
    const ranges = cal.openingHours[DAY_KEYS[p.weekday]] || [];
    for (const [a, b] of ranges) {
      for (let m = hm(a); m + cal.slotMinutes <= hm(b); m += cal.slotMinutes) {
        const s = zoned(p.y, p.m, p.d, Math.floor(m / 60), m % 60, tz).getTime();
        if (s < earliest || s < from.getTime()) continue;
        const e = s + slotMs;
        const overlapping = busy.filter(([bs, be]) => bs < e + bufMs && be > s - bufMs).length;
        if (overlapping >= (cal.maxParallel || 1)) continue;
        slots.push({ start: new Date(s).toISOString(), end: new Date(e).toISOString(), label: fmtDateTime(s, tz) });
        if (slots.length >= limit) break;
      }
    }
  }
  return slots;
}

async function isFree(t, startIso) {
  const s = new Date(startIso);
  if (isNaN(s)) return false;
  const slots = await freeSlots(t, { from: new Date(s.getTime() - 60e3), days: 1, limit: 200 });
  return slots.some((x) => x.start === s.toISOString());
}

// Termin buchen (mit Konfliktprüfung)
async function book(t, { contactId, start, reason = '', source = 'web' }) {
  if (!(await isFree(t, start))) return { ok: false, error: 'Dieser Termin ist leider nicht mehr frei.' };
  const s = new Date(start);
  const e = new Date(s.getTime() + t.calendar.slotMinutes * 60e3);
  const aid = db.id('apt_');
  db.run('INSERT INTO appointments (id, tenant, contact_id, start, end, reason, source, created_at) VALUES (?,?,?,?,?,?,?,?)',
    aid, t.id, contactId, s.toISOString(), e.toISOString(), String(reason).slice(0, 500), source, db.now());
  db.logEvent(t.id, 'appointment.booked', { appointment: aid, contact: contactId, source });
  return { ok: true, appointment: db.one('SELECT * FROM appointments WHERE id=?', aid), label: fmtDateTime(s, t.timezone) };
}

function cancel(t, id) {
  db.run("UPDATE appointments SET status='storniert' WHERE tenant=? AND id=?", t.id, id);
}

const icsEsc = (s) => String(s || '').replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n');
const icsDate = (iso) => new Date(iso).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');

// ICS-Feed aller Termine (in Google/Outlook/Apple abonnierbar)
function icsFeed(t) {
  const rows = db.all(`SELECT a.*, c.name, c.phone, c.email FROM appointments a LEFT JOIN contacts c ON c.id=a.contact_id
    WHERE a.tenant=? AND a.status!='storniert' ORDER BY a.start`, t.id);
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//INOVai//Termine//DE', `X-WR-CALNAME:${icsEsc(t.name)} – INOVai Termine`, 'CALSCALE:GREGORIAN'];
  for (const a of rows) {
    lines.push('BEGIN:VEVENT', `UID:${a.id}@inovai`, `DTSTAMP:${icsDate(a.created_at)}`, `DTSTART:${icsDate(a.start)}`, `DTEND:${icsDate(a.end)}`,
      `SUMMARY:${icsEsc(`${a.name || 'Kunde'} – ${a.reason || 'Termin'}`)}`,
      `DESCRIPTION:${icsEsc(`Tel: ${a.phone || '-'}\nE-Mail: ${a.email || '-'}\nAnliegen: ${a.reason || '-'}\nQuelle: ${a.source}`)}`,
      'END:VEVENT');
  }
  lines.push('END:VCALENDAR');
  return lines.join('\r\n');
}

module.exports = { freeSlots, book, cancel, isFree, icsFeed, parseIcs };
