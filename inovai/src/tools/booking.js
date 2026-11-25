'use strict';
// Gemeinsame Terminbuchung (Buchungsseite, Chatbot, Voice) inkl. Bestätigung und Benachrichtigung.
const scheduler = require('../lib/scheduler');
const contacts = require('../lib/contacts');
const channels = require('../lib/channels');
const calendar = require('../lib/calendar');
const C = require('./common');

async function bookAppointment(t, { contact: input, contactToken, start, reason, source }) {
  let c = contactToken ? contacts.byToken(contactToken) : null;
  if (c && c.tenant !== t.id) c = null;
  if (input) c = contacts.upsert(t.id, { ...(c ? { phone: c.phone, email: c.email } : {}), ...input }, source) || c;
  if (!c) return { ok: false, error: 'Bitte Telefonnummer oder E-Mail angeben.' };
  const res = await calendar.book(t, { contactId: c.id, start, reason, source });
  if (!res.ok) return res;
  contacts.setStatus(c.id, 'termin');
  // Termin gebucht -> Nachfass-Sequenzen beenden
  scheduler.cancelForContact(t.id, c.id, ['rueckruf', 'anfragen', 'angebote'], 'Termin gebucht');
  const m = C.meta(c);
  if (m.reakt_stage) C.setMeta(c, { reakt_stage: 0, reakt_won_at: new Date().toISOString() });
  const confirm = `Ihr Termin bei ${t.name} ist bestätigt: ${res.label}.${t.address ? ` Adresse: ${t.address}.` : ''} Falls etwas dazwischenkommt, antworten Sie einfach auf diese Nachricht.`;
  await channels.send(t, c, 'termin', { text: confirm, email: { subject: `Terminbestätigung: ${res.label}`, body: `Guten Tag${c.name ? ' ' + c.name : ''},\n\n${confirm}\n\n${t.signature || `Ihr Team von ${t.name}`}` } });
  await channels.notifyOwner(t, `Neuer Termin: ${res.label}`, `${C.contactLine(c)}\nAnliegen: ${reason || '-'}\nQuelle: ${source}`,
    { event: 'appointment.booked', appointment: res.appointment.id, contact: c.id, start: res.appointment.start, reason, source });
  return { ok: true, appointment: res.appointment, label: res.label, contact: c.id };
}

module.exports = { bookAppointment };
