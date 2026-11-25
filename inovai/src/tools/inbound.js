'use strict';
// Eingehende Antworten (SMS/WhatsApp/E-Mail): Abmeldung, Sequenzen stoppen, KI-Antwort, Inhaber informieren.
const db = require('../lib/db');
const scheduler = require('../lib/scheduler');
const contacts = require('../lib/contacts');
const channels = require('../lib/channels');
const llm = require('../lib/llm');
const C = require('./common');

const STOP_RE = /\b(stop|stopp|abmelden|abbestellen|unsubscribe|austragen|abmeldung)\b|keine (weiteren |sms |nachrichten )*(nachrichten|sms|werbung) mehr|nicht mehr (anschreiben|kontaktieren)/i;
const NURTURE = ['rueckruf', 'anfragen', 'angebote', 'reaktivierung', 'bewertung'];

function conversation(t, contactId, channel) {
  let conv = db.one('SELECT * FROM conversations WHERE tenant=? AND contact_id=? AND channel=?', t.id, contactId, channel);
  if (!conv) {
    const id = db.id('conv_');
    db.run('INSERT INTO conversations (id, tenant, contact_id, channel, created_at, updated_at) VALUES (?,?,?,?,?,?)', id, t.id, contactId, channel, db.now(), db.now());
    conv = db.one('SELECT * FROM conversations WHERE id=?', id);
  }
  return conv;
}

function lastOutboundTool(t, contactId) {
  const m = db.one("SELECT tool FROM messages WHERE tenant=? AND contact_id=? AND direction='out' AND tool IS NOT NULL AND tool!='reply' ORDER BY created_at DESC LIMIT 1", t.id, contactId);
  return m ? m.tool : null;
}

async function handle(t, { channel, from, body }) {
  const isEmail = channel === 'email';
  let c = contacts.find(t.id, isEmail ? { email: String(from).toLowerCase() } : { phone: channels.normalizePhone(from) });
  if (!c) c = contacts.upsert(t.id, isEmail ? { email: from } : { phone: from }, `eingehend-${channel}`);
  if (!c) return { ok: false, error: 'Absender unbekannt' };
  const text = String(body || '').trim().slice(0, 2000);
  db.run('INSERT INTO messages (id, tenant, contact_id, direction, channel, body, status, created_at) VALUES (?,?,?,?,?,?,?,?)',
    db.id('msg_'), t.id, c.id, 'in', channel, text, 'empfangen', db.now());

  if (STOP_RE.test(text)) {
    contacts.optOut(c.id);
    scheduler.cancelForContact(t.id, c.id, NURTURE, 'Abmeldung');
    await channels.send(t, { ...c, opted_out: 0 }, 'reply', { text: `Sie wurden abgemeldet und erhalten keine weiteren Nachrichten von ${t.name}.`, email: { subject: 'Abmeldung bestätigt', body: `Sie wurden abgemeldet und erhalten keine weiteren Nachrichten von ${t.name}.` } }, { forceChannel: channel === 'whatsapp' ? 'whatsapp' : isEmail ? 'email' : 'sms' });
    db.logEvent(t.id, 'contact.opted_out', { contact: c.id });
    return { ok: true, optedOut: true };
  }

  const tool = lastOutboundTool(t, c.id);
  // Kunde hat reagiert -> keine weiteren automatischen Erinnerungen (außer Bewertung)
  const stopped = scheduler.cancelForContact(t.id, c.id, ['rueckruf', 'anfragen', 'angebote'], 'Kunde hat geantwortet');
  if (['neu', 'lead'].includes(c.status)) contacts.setStatus(c.id, 'geantwortet');
  // Kunde hat reagiert -> Rückhol-Sequenz beenden (Inhaber übernimmt persönlich)
  if (C.meta(c).reakt_stage) C.setMeta(c, { reakt_stage: (t.tools.reaktivierung.days || []).length, reakt_replied_at: db.now() });

  const conv = conversation(t, c.id, channel);
  const history = JSON.parse(conv.history || '[]');
  history.push({ role: 'user', content: text });
  let reply = await llm.replyToInbound(t, { history, contact: c, bookingLink: C.bookingUrl(t, c, 'antwort'), tool });
  if (!reply) reply = `Danke für Ihre Nachricht! Wir melden uns schnellstmöglich persönlich. Termin direkt buchen: ${C.bookingUrl(t, c, 'antwort')}`;
  history.push({ role: 'assistant', content: reply });
  db.run('UPDATE conversations SET history=?, updated_at=? WHERE id=?', JSON.stringify(history.slice(-30)), db.now(), conv.id);

  const res = await channels.send(t, c, 'reply', { text: reply, email: { subject: `Re: Ihre Nachricht an ${t.name}`, body: reply } }, { forceChannel: isEmail ? 'email' : channel });
  await channels.notifyOwner(t, `Antwort von ${c.name || c.phone || c.email}${tool ? ` (${tool})` : ''}`,
    `Kunde: ${C.contactLine(c)}\nNachricht: ${text}\n\nAutomatische Antwort: ${reply}`, { event: 'message.inbound', contact: c.id, tool });
  return { ok: true, contact: c.id, reply, replied: res.ok, stoppedReminders: stopped };
}

module.exports = { handle, STOP_RE };
