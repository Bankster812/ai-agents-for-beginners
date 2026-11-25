'use strict';
const db = require('../lib/db');
const config = require('../lib/config');
const channels = require('../lib/channels');
const llm = require('../lib/llm');
const { render, buildVars } = require('../lib/templates');

// Tracking-Link (Klicks werden gezählt; Bewertungs-Klick stoppt die Erinnerung)
function trackedLink(t, contact, kind, target, sequenceKey = null) {
  const tok = db.token();
  db.run('INSERT INTO links (token, tenant, contact_id, kind, target, sequence_key, created_at) VALUES (?,?,?,?,?,?,?)',
    tok, t.id, contact ? contact.id : null, kind, target, sequenceKey, db.now());
  return `${config.baseUrl()}/l/${tok}`;
}

function bookingUrl(t, contact, src) {
  const q = new URLSearchParams();
  if (contact && contact.token) q.set('c', contact.token);
  if (src) q.set('src', src);
  return `${config.baseUrl()}/book/${t.id}${q.toString() ? '?' + q : ''}`;
}

function bookingLink(t, contact, src, sequenceKey) {
  return trackedLink(t, contact, 'booking', bookingUrl(t, contact, src), sequenceKey);
}

function templatesFor(t, tool) {
  return (t.templates && t.templates[tool]) || {};
}

// Rendert Vorlage für Schritt "step", personalisiert optional per KI und versendet.
async function composeAndSend(t, contact, tool, step, extraVars = {}, context = {}) {
  const tpl = templatesFor(t, tool);
  const sms = tpl.sms || [];
  const mail = tpl.email || [];
  const vars = buildVars(t, contact, extraVars);
  let text = sms.length ? render(sms[Math.min(step, sms.length - 1)], vars) : '';
  const m = mail.length ? mail[Math.min(step, mail.length - 1)] : null;
  let email = m ? { subject: render(m.subject, vars), body: render(m.body, vars) } : null;

  const first = channels.availableChannels(t, contact, tool)[0];
  const ctx = { schritt: step + 1, kunde: vars.vorname || null, ...context };
  if (first === 'email' && email) email = await llm.personalize(t, { tool, channel: 'email', draft: email, context: ctx });
  else if (first && text) text = await llm.personalize(t, { tool, channel: first, draft: text, context: ctx });

  const res = await channels.send(t, contact, tool, { text, email, variables: { 1: vars.vorname || '', 2: extraVars.link || '' } });
  db.logEvent(t.id, `${tool}.sent`, { contact: contact.id, step, ok: res.ok, channel: res.channel, reason: res.reason });
  return res;
}

function hasActive(t, contactId, tool) {
  return !!db.one("SELECT id FROM jobs WHERE tenant=? AND contact_id=? AND tool=? AND status='pending'", t.id, contactId, tool);
}

function contactLine(c) {
  return [c.name || 'Unbekannt', c.phone, c.email].filter(Boolean).join(' · ');
}

function meta(c) { try { return JSON.parse(c.meta || '{}'); } catch { return {}; } }
function setMeta(c, patch) {
  const m = { ...meta(c), ...patch };
  db.run('UPDATE contacts SET meta=?, updated_at=? WHERE id=?', JSON.stringify(m), db.now(), c.id);
  return m;
}

// Wird ein Tool für diesen Mandanten verkauft/aktiviert?
function requireTool(t, tool) {
  if (!t.tools[tool] || !t.tools[tool].enabled) {
    const e = new Error(`Tool "${tool}" ist für ${t.name} nicht aktiviert.`);
    e.status = 403;
    throw e;
  }
}

module.exports = { trackedLink, bookingUrl, bookingLink, composeAndSend, hasActive, contactLine, meta, setMeta, requireTool };
