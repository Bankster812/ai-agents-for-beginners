'use strict';
// PRODUKT 3: Anfragen-Nachfasser (Zahnarztpraxen, Kanzleien, Makler …)
// Neue Anfrage (Formular, E-Mail-Parser, Ads, Chatbot) -> Antwort in < 60 s -> bis zu 3 Nachfassungen -> Termin.
const db = require('../lib/db');
const scheduler = require('../lib/scheduler');
const contacts = require('../lib/contacts');
const channels = require('../lib/channels');
const C = require('./common');

const TOOL = 'anfragen';

async function newLead(t, input, { untrusted = false } = {}) {
  C.requireTool(t, TOOL);
  const cfg = t.tools[TOOL];
  const c = contacts.upsert(t.id, input, contacts.pick(input, ['source', 'quelle', 'kanal', 'utm_source']) || 'anfrage', { untrusted });
  if (!c) return { ok: false, error: 'Telefon oder E-Mail fehlt' };
  const message = contacts.pick(input, ['message', 'nachricht', 'anliegen', 'text', 'comment', 'kommentar', 'beschreibung']);
  const service = contacts.pick(input, ['service', 'leistung', 'interesse', 'interest', 'thema', 'subject', 'betreff']);
  contacts.setStatus(c.id, 'lead');
  C.setMeta(c, { anliegen: message.slice(0, 1000), leistung: service });
  db.logEvent(t.id, 'lead.created', { contact: c.id, service });
  await channels.notifyOwner(t, `Neue Anfrage: ${service || 'allgemein'}`,
    `${C.contactLine(c)}\n\nAnliegen: ${message || '-'}\n\nDer Kunde wurde automatisch kontaktiert.`, { event: 'lead.created', contact: c.id, service, message });

  if (C.hasActive(t, c.id, TOOL)) return { ok: true, contact: c.id, duplicate: true };
  const seq = `${TOOL}:${c.id}:${Date.now()}`;
  scheduler.schedule(t, TOOL, { step: 0, sequenceKey: seq, contactId: c.id, payload: { service, message }, runAt: scheduler.after(t, 5000, { respectWindow: false }) });
  cfg.followupDays.forEach((d, i) => {
    scheduler.schedule(t, TOOL, { step: i + 1, sequenceKey: seq, contactId: c.id, payload: { service, message }, runAt: scheduler.after(t, d * 86400e3) });
  });
  return { ok: true, contact: c.id, scheduled: 1 + cfg.followupDays.length };
}

async function run(t, job) {
  const c = contacts.byId(job.contact_id);
  if (!c || c.opted_out || ['kunde', 'verloren', 'termin'].includes(c.status)) return;
  await C.composeAndSend(t, c, TOOL, job.step, { link: C.bookingLink(t, c, TOOL, job.sequence_key), leistung: job.payload.service || '' },
    { anlass: 'neue Kundenanfrage', leistung: String(job.payload.service || '').slice(0, 60) }); // Freitext des Absenders bewusst NICHT an die KI (Prompt-Injection)
}

scheduler.register(TOOL, run);
module.exports = { newLead, TOOL };
