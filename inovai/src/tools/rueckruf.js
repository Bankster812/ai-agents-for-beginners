'use strict';
// PRODUKT 1: Rückruf-Automatik (Zielgruppe: Heizung/Sanitär/Elektro, alle mit verpassten Anrufen)
// Verpasster Anruf -> innerhalb ~1 Minute SMS/WhatsApp mit Buchungslink -> KI beantwortet Rückfragen -> Termin.
const db = require('../lib/db');
const scheduler = require('../lib/scheduler');
const contacts = require('../lib/contacts');
const channels = require('../lib/channels');
const C = require('./common');

const TOOL = 'rueckruf';

async function missedCall(t, input) {
  C.requireTool(t, TOOL);
  const c = contacts.upsert(t.id, input, 'verpasster-anruf');
  if (!c) return { ok: false, error: 'Keine Rufnummer übermittelt (unterdrückte Nummer?)' };
  db.logEvent(t.id, 'call.missed', { contact: c.id });
  if (C.hasActive(t, c.id, TOOL)) return { ok: true, contact: c.id, duplicate: true };

  const cfg = t.tools[TOOL];
  const seq = `${TOOL}:${c.id}:${Date.now()}`;
  if (!channels.availableChannels(t, c, TOOL).length) {
    // z. B. Festnetznummer ohne E-Mail: Inhaber bekommt sofort eine Rückruf-Aufgabe
    await channels.notifyOwner(t, 'Bitte zurückrufen (keine SMS möglich)', `Verpasster Anruf: ${C.contactLine(c)}\nDie Nummer ist kein Mobilfunkanschluss – bitte persönlich zurückrufen.`, { event: 'call.missed', contact: c.id });
    return { ok: true, contact: c.id, autoReply: false, reason: 'nicht per SMS erreichbar – Inhaber benachrichtigt' };
  }
  // Sofortantwort: Kunde hat gerade angerufen, daher kein Versandfenster
  scheduler.schedule(t, TOOL, { step: 0, sequenceKey: seq, contactId: c.id, runAt: scheduler.after(t, cfg.delaySeconds * 1000, { respectWindow: false }) });
  if (cfg.followupMinutes) {
    scheduler.schedule(t, TOOL, { step: 1, sequenceKey: seq, contactId: c.id, runAt: scheduler.after(t, (cfg.delaySeconds + cfg.followupMinutes * 60) * 1000) });
  }
  await channels.notifyOwner(t, 'Verpasster Anruf – Kunde wird automatisch kontaktiert', C.contactLine(c), { event: 'call.missed', contact: c.id });
  return { ok: true, contact: c.id, autoReply: true };
}

async function run(t, job) {
  const c = contacts.byId(job.contact_id);
  if (!c || c.opted_out) return;
  await C.composeAndSend(t, c, TOOL, job.step, { link: C.bookingLink(t, c, TOOL, job.sequence_key) }, { anlass: 'verpasster Anruf' });
}

// Anruf wurde doch angenommen (oder Rückruf erfolgt) -> ausstehende Rückruf-Erinnerung stoppen
function callAnswered(t, phone) {
  const c = contacts.find(t.id, { phone: channels.normalizePhone(phone) });
  if (!c) return 0;
  return scheduler.cancelForContact(t.id, c.id, [TOOL], 'Anruf angenommen');
}

scheduler.register(TOOL, run);
module.exports = { missedCall, callAnswered, TOOL };
