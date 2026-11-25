'use strict';
// Versandkanäle: SMS (Twilio oder seven.io), WhatsApp (Twilio), E-Mail (SMTP), Konsole (Test/Demo).
const db = require('./db');

const outbox = []; // Konsolen-Kanal (Tests/Demo)

// Telefonnummern auf E.164 normalisieren (Standard: Deutschland)
function normalizePhone(raw, defaultCc = '49') {
  if (!raw) return '';
  let s = String(raw).replace(/^whatsapp:/, '').replace(/[^\d+]/g, '');
  if (!s) return '';
  if (s.startsWith('00')) s = '+' + s.slice(2);
  else if (s.startsWith('0')) s = `+${defaultCc}` + s.slice(1);
  else if (!s.startsWith('+')) s = `+${defaultCc}` + s;
  return s.length >= 8 ? s : '';
}

// Deutsche Mobilfunknummern beginnen mit +4915, +4916, +4917. Andere Länder: als mobil annehmen.
function isMobile(phone) {
  if (!phone) return false;
  if (phone.startsWith('+49')) return /^\+491[567]/.test(phone);
  return true;
}

function smsConfigured(t) {
  const c = t.channels.sms;
  return !!(c && (c.provider === 'console' || (c.provider === 'seven' && c.apiKey) ||
    (c.accountSid && c.authToken && (c.from || c.messagingServiceSid))));
}
function waConfigured(t) {
  const c = t.channels.whatsapp;
  return !!(c && (c.provider === 'console' || (c.accountSid && c.authToken && c.from)));
}
function emailConfigured(t) {
  const c = t.channels.email;
  return !!(c && (c.provider === 'console' || (c.host && c.from)));
}

function recentInbound(t, contact, channel, hours = 24) {
  const since = new Date(Date.now() - hours * 3600e3).toISOString();
  return !!db.one('SELECT id FROM messages WHERE tenant=? AND contact_id=? AND direction=? AND channel=? AND created_at>?',
    t.id, contact.id, 'in', channel, since);
}

// Welche Kanäle können diesen Kontakt erreichen?
function availableChannels(t, contact, tool) {
  const pref = (tool && t.tools[tool] && t.tools[tool].channel) ? [t.tools[tool].channel] : (t.channels.order || ['sms', 'email']);
  const ok = [];
  for (const ch of pref) {
    if (ch === 'sms' && smsConfigured(t) && contact.phone && isMobile(contact.phone)) ok.push('sms');
    if (ch === 'whatsapp' && waConfigured(t) && contact.phone && isMobile(contact.phone)) {
      // Außerhalb des 24h-Fensters erlaubt WhatsApp nur freigegebene Vorlagen (contentSid)
      const tpl = t.channels.whatsapp.templates && t.channels.whatsapp.templates[tool];
      if (t.channels.whatsapp.provider === 'console' || tpl || recentInbound(t, contact, 'whatsapp')) ok.push('whatsapp');
    }
    if (ch === 'email' && emailConfigured(t) && contact.email) ok.push('email');
  }
  return ok;
}

async function twilioSend(c, to, body, extra = {}) {
  const params = new URLSearchParams({ To: to, ...extra });
  if (body) params.set('Body', body);
  if (c.messagingServiceSid && !to.startsWith('whatsapp:')) params.set('MessagingServiceSid', c.messagingServiceSid);
  else params.set('From', c.from);
  if (c.statusCallback) params.set('StatusCallback', c.statusCallback);
  const res = await fetch(`https://api.twilio.com/2010-04-01/Accounts/${c.accountSid}/Messages.json`, {
    method: 'POST',
    headers: {
      Authorization: 'Basic ' + Buffer.from(`${c.accountSid}:${c.authToken}`).toString('base64'),
      'Content-Type': 'application/x-www-form-urlencoded'
    },
    body: params
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`Twilio ${res.status}: ${json.message || 'Fehler'}`);
  return json.sid;
}

async function sevenSend(c, to, text) {
  const res = await fetch('https://gateway.seven.io/api/sms', {
    method: 'POST',
    headers: { 'X-Api-Key': c.apiKey, 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ to, text, from: c.from || 'INFO' })
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok || (json.success && String(json.success) !== '100')) throw new Error(`seven.io: ${JSON.stringify(json).slice(0, 200)}`);
  return String((json.messages && json.messages[0] && json.messages[0].id) || 'ok');
}

let mailers = new Map();
function mailer(t) {
  if (!mailers.has(t.id)) {
    const nodemailer = require('nodemailer');
    const c = t.channels.email;
    mailers.set(t.id, nodemailer.createTransport({
      host: c.host, port: Number(c.port || 587), secure: Number(c.port) === 465,
      auth: c.user ? { user: c.user, pass: c.pass } : undefined
    }));
  }
  return mailers.get(t.id);
}

function smsFooter(t, text) {
  if (!t.channels.sms || t.channels.sms.optOutHint === false || text.length > 700) return text;
  if (t.channels.sms && t.channels.sms.optOutHint === false) return text;
  return /STOP/.test(text) ? text : `${text} (Abmelden: STOP)`;
}

async function deliver(t, channel, contact, msg, tool) {
  const c = t.channels[channel] || {};
  if (channel === 'sms' && msg.text) msg = { ...msg, text: smsFooter(t, msg.text) };
  if (c.provider === 'console' || process.env.INOVAI_DRY_RUN === '1') {
    outbox.push({ tenant: t.id, channel, to: channel === 'email' ? contact.email : contact.phone, tool, ...msg });
    return 'console';
  }
  if (channel === 'sms') {
    return c.provider === 'seven' ? sevenSend(c, contact.phone, msg.text) : twilioSend(c, contact.phone, msg.text);
  }
  if (channel === 'whatsapp') {
    const tpl = c.templates && c.templates[tool];
    if (tpl && !recentInbound(t, contact, 'whatsapp')) {
      return twilioSend(c, `whatsapp:${contact.phone}`, null, { ContentSid: tpl, ContentVariables: JSON.stringify(msg.variables || {}) });
    }
    return twilioSend(c, `whatsapp:${contact.phone}`, msg.text);
  }
  if (channel === 'email') {
    const info = await mailer(t).sendMail({
      from: c.from, to: contact.email, replyTo: c.replyTo || t.email || c.from,
      subject: msg.subject, text: msg.body,
      headers: c.listUnsubscribe ? { 'List-Unsubscribe': `<${c.listUnsubscribe}>` } : undefined
    });
    return info.messageId;
  }
  throw new Error(`Unbekannter Kanal: ${channel}`);
}

// Sendet eine Nachricht über den ersten verfügbaren Kanal; protokolliert alles in "messages".
async function send(t, contact, tool, content, { forceChannel } = {}) {
  if (contact.opted_out) return { ok: false, reason: 'abgemeldet' };
  const chans = forceChannel ? [forceChannel] : availableChannels(t, contact, tool);
  if (!chans.length) {
    db.logEvent(t.id, 'send.unreachable', { contact: contact.id, tool });
    return { ok: false, reason: 'kein erreichbarer Kanal (Mobilnummer/E-Mail fehlt oder Kanal nicht konfiguriert)' };
  }
  let lastErr;
  for (const ch of chans) {
    const msg = ch === 'email' ? content.email : { text: content.text, variables: content.variables };
    if (!msg || (ch !== 'email' && !msg.text)) continue;
    const mid = db.id('msg_');
    try {
      const ref = await deliver(t, ch, contact, msg, tool);
      db.run('INSERT INTO messages (id, tenant, contact_id, direction, channel, tool, body, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        mid, t.id, contact.id, 'out', ch, tool, ch === 'email' ? `${msg.subject}\n\n${msg.body}` : msg.text, `gesendet:${ref}`, db.now());
      return { ok: true, channel: ch, id: mid };
    } catch (e) {
      lastErr = e;
      db.run('INSERT INTO messages (id, tenant, contact_id, direction, channel, tool, body, status, error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
        mid, t.id, contact.id, 'out', ch, tool, ch === 'email' ? msg.subject : msg.text, 'fehler', e.message, db.now());
    }
  }
  return { ok: false, reason: lastErr ? lastErr.message : 'kein Inhalt' };
}

// Benachrichtigung an den Inhaber (E-Mail und/oder Webhook), z. B. bei neuem Lead oder Termin.
async function notifyOwner(t, subject, body, data = {}) {
  db.logEvent(t.id, 'notify', { subject, ...data });
  const tasks = [];
  const n = t.notify || {};
  if (n.email && emailConfigured(t)) {
    const fake = { id: null, email: n.email };
    tasks.push(deliver(t, 'email', fake, { subject: `[${t.name}] ${subject}`, body }, 'notify').catch(() => {}));
  }
  if (n.sms && smsConfigured(t)) {
    tasks.push(deliver(t, 'sms', { phone: normalizePhone(n.sms) }, { text: `${subject}: ${body}`.slice(0, 300) }, 'notify').catch(() => {}));
  }
  if (n.webhookUrl) tasks.push(webhook(t, n.webhookUrl, { event: data.event || 'notify', subject, body, data }));
  await Promise.all(tasks);
}

// Ausgehender Webhook (Zapier, Make, n8n, CRM)
async function webhook(t, url, payload) {
  try {
    const crypto = require('node:crypto');
    const body = JSON.stringify({ tenant: t.id, at: db.now(), ...payload });
    const headers = { 'Content-Type': 'application/json' };
    if (t.notify && t.notify.webhookSecret) {
      headers['X-INOVai-Signature'] = crypto.createHmac('sha256', t.notify.webhookSecret).update(body).digest('hex');
    }
    await fetch(url, { method: 'POST', headers, body, signal: AbortSignal.timeout(8000) });
  } catch (e) {
    db.logEvent(t.id, 'webhook.error', { url, error: e.message });
  }
}

module.exports = { send, notifyOwner, webhook, normalizePhone, isMobile, availableChannels, outbox, _resetMailers: () => { mailers = new Map(); } };
