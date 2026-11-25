'use strict';
// PRODUKT 5: Angebots-Erinnerung (Dachdecker, Maler, GaLaBau …)
// Angebot verschickt -> freundliches Nachfassen an Tag 2, 5 und 10 -> stoppt bei Antwort/Annahme/Ablehnung.
const db = require('../lib/db');
const scheduler = require('../lib/scheduler');
const contacts = require('../lib/contacts');
const channels = require('../lib/channels');
const C = require('./common');

const TOOL = 'angebote';
const fmtEur = (n) => (n || n === 0) && !isNaN(n) ? new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' }).format(n) : '';

async function offerSent(t, input) {
  C.requireTool(t, TOOL);
  const c = contacts.upsert(t.id, input, 'angebot');
  if (!c) return { ok: false, error: 'Telefon oder E-Mail fehlt' };
  const externalId = contacts.pick(input, ['offer_id', 'angebot_id', 'angebotsnummer', 'quote_id', 'id', 'nummer']) || db.id('ang_');
  const title = contacts.pick(input, ['title', 'titel', 'auftrag', 'projekt', 'bezeichnung', 'betreff', 'subject']) || 'Ihr Angebot';
  const amountRaw = contacts.pick(input, ['amount', 'summe', 'betrag', 'total', 'preis']);
  const amount = amountRaw ? Number(String(amountRaw).replace(/[^\d,.-]/g, '').replace(/\.(?=\d{3}(\D|$))/g, '').replace(',', '.')) : null;
  const existing = db.one('SELECT * FROM offers WHERE tenant=? AND external_id=?', t.id, externalId);
  if (existing) return { ok: true, offer: existing.id, duplicate: true };
  const oid = db.id('off_');
  db.run('INSERT INTO offers (id, tenant, contact_id, external_id, title, amount, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
    oid, t.id, c.id, externalId, title, isNaN(amount) ? null : amount, db.now(), db.now());
  db.logEvent(t.id, 'offer.sent', { offer: oid, contact: c.id });
  const seq = `${TOOL}:${oid}`;
  t.tools[TOOL].days.forEach((d, i) => {
    scheduler.schedule(t, TOOL, { step: i, sequenceKey: seq, contactId: c.id, payload: { offer: oid }, runAt: scheduler.after(t, d * 86400e3) });
  });
  return { ok: true, offer: oid, externalId, contact: c.id, scheduled: t.tools[TOOL].days.length };
}

const STATUS = { accepted: 'angenommen', angenommen: 'angenommen', won: 'angenommen', auftrag: 'angenommen',
  declined: 'abgelehnt', abgelehnt: 'abgelehnt', lost: 'abgelehnt', rejected: 'abgelehnt', open: 'offen', offen: 'offen' };

async function offerStatus(t, input) {
  const externalId = contacts.pick(input, ['offer_id', 'angebot_id', 'angebotsnummer', 'quote_id', 'id', 'nummer']);
  const status = STATUS[String(contacts.pick(input, ['status', 'state'])).toLowerCase()];
  const o = db.one('SELECT * FROM offers WHERE tenant=? AND (external_id=? OR id=?)', t.id, externalId, externalId);
  if (!o || !status) return { ok: false, error: 'Angebot oder Status unbekannt' };
  db.run('UPDATE offers SET status=?, updated_at=? WHERE id=?', status, db.now(), o.id);
  const stopped = status !== 'offen' ? scheduler.cancelSequence(`${TOOL}:${o.id}`, `Angebot ${status}`) : 0;
  if (status === 'angenommen') {
    contacts.setStatus(o.contact_id, 'kunde');
    await channels.notifyOwner(t, `Angebot angenommen: ${o.title}`, `${fmtEur(o.amount)} – ${o.external_id}`, { event: 'offer.accepted', offer: o.id });
  }
  return { ok: true, offer: o.id, status, stoppedReminders: stopped };
}

async function run(t, job) {
  const o = db.one('SELECT * FROM offers WHERE id=?', job.payload.offer);
  const c = contacts.byId(job.contact_id);
  if (!o || o.status !== 'offen' || !c || c.opted_out) return;
  await C.composeAndSend(t, c, TOOL, job.step, { angebot: o.title, betrag: fmtEur(o.amount), link: C.bookingLink(t, c, TOOL, job.sequence_key) },
    { anlass: 'Nachfassen zu verschicktem Angebot', angebot: o.title, betrag: fmtEur(o.amount), tag: t.tools[TOOL].days[job.step] });
}

scheduler.register(TOOL, run);
module.exports = { offerSent, offerStatus, TOOL };
