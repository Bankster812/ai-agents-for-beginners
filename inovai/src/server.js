'use strict';
// INOVai Automation Suite – HTTP-Server
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const db = require('./lib/db');
const config = require('./lib/config');
const scheduler = require('./lib/scheduler');
const calendar = require('./lib/calendar');
const knowledge = require('./lib/knowledge');
const contactsLib = require('./lib/contacts');
const llm = require('./lib/llm');
const { Router, readBody, rateLimit, safeEqual, twilioValid } = require('./lib/http');
const rueckruf = require('./tools/rueckruf');
const bewertung = require('./tools/bewertung');
const anfragen = require('./tools/anfragen');
const reaktivierung = require('./tools/reaktivierung');
const angebote = require('./tools/angebote');
const inbound = require('./tools/inbound');
const { bookAppointment } = require('./tools/booking');
const agent = require('./chatbot/agent');

const PUBLIC = path.join(__dirname, '..', 'public');
const r = new Router();

// ---------- Hilfen ----------
function send(res, status, data, headers = {}) {
  const isStr = typeof data === 'string' || Buffer.isBuffer(data);
  res.writeHead(status, { 'Content-Type': isStr ? 'text/plain; charset=utf-8' : 'application/json; charset=utf-8', ...headers });
  res.end(isStr ? data : JSON.stringify(data));
}
function html(res, file, replacements = {}) {
  let s = fs.readFileSync(path.join(PUBLIC, file), 'utf8');
  for (const [k, v] of Object.entries(replacements)) s = s.split(`{{${k}}}`).join(v);
  send(res, 200, s, { 'Content-Type': 'text/html; charset=utf-8' });
}
const escHtml = (s) => String(s || '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function err(status, message) { return Object.assign(new Error(message), { status }); }
function ip(req) { return (req.headers['x-forwarded-for'] || '').split(',')[0].trim() || req.socket.remoteAddress || '?'; }

function tenantOr404(id) {
  const t = config.tenant(id);
  if (!t) throw err(404, 'Mandant nicht gefunden');
  return t;
}
function requireApiKey(req, t, query) {
  const key = req.headers['x-api-key'] || query.get('key') || (req.headers.authorization || '').replace(/^Bearer /, '');
  if (!safeEqual(key, t.apiKey)) throw err(401, 'API-Key fehlt oder ist falsch (Header X-API-Key oder ?key=)');
}
function requireAdmin(req, t, query) {
  const tok = (req.headers.authorization || '').replace(/^Bearer /, '') || query.get('token');
  if (safeEqual(tok, process.env.INOVAI_ADMIN_TOKEN) || (t && safeEqual(tok, t.adminToken))) return;
  throw err(401, 'Nicht angemeldet');
}

// ---------- Seiten & Assets ----------
r.get('/', (req, res) => send(res, 302, '', { Location: '/demo' }));
r.get('/health', (req, res) => send(res, 200, { ok: true, ai: llm.enabled(), model: llm.DEFAULT_MODEL, tenants: config.tenants().length }));
r.get('/widget.js', (req, res) => send(res, 200, fs.readFileSync(path.join(PUBLIC, 'widget.js')), { 'Content-Type': 'application/javascript; charset=utf-8', 'Cache-Control': 'public, max-age=300', 'Access-Control-Allow-Origin': '*' }));
r.get('/admin', (req, res) => html(res, 'admin.html'));
r.get('/demo', (req, res) => { const t = config.tenants()[0]; if (!t) return send(res, 404, 'Kein Mandant konfiguriert'); send(res, 302, '', { Location: `/demo/${t.id}` }); });
r.get('/demo/:tenant', (req, res, { params }) => {
  const t = tenantOr404(params.tenant);
  html(res, 'demo.html', { TENANT: escHtml(t.id), NAME: escHtml(t.name), BRANCHE: escHtml(t.branche), PHONE: escHtml(t.phone), ADDRESS: escHtml(t.address), SLOGAN: escHtml(t.slogan || ''), SERVICES: (t.services || []).map((s) => `<li><b>${escHtml(s.name)}</b>${s.price ? ` – ${escHtml(s.price)}` : ''}<br><small>${escHtml(s.description || '')}</small></li>`).join('') });
});
r.get('/book/:tenant', (req, res, { params }) => {
  const t = tenantOr404(params.tenant);
  html(res, 'book.html', { TENANT: escHtml(t.id), NAME: escHtml(t.name), COLOR: escHtml(t.chatbot.color), PRIVACY: escHtml(t.chatbot.privacyUrl || '#'), ADDRESS: escHtml(t.address || '') });
});

// Tracking-Link
r.get('/l/:token', (req, res, { params }) => {
  const l = db.one('SELECT * FROM links WHERE token=?', params.token);
  if (!l) return send(res, 404, 'Link abgelaufen');
  db.run('UPDATE links SET clicks=clicks+1 WHERE token=?', l.token);
  db.logEvent(l.tenant, `link.${l.kind}.clicked`, { contact: l.contact_id });
  if (l.kind === 'review' && l.sequence_key) scheduler.cancelSequence(l.sequence_key, 'Bewertungslink geklickt');
  send(res, 302, '', { Location: l.target });
});

// ICS-Feed
r.get('/cal/:tenant/:file', (req, res, { params }) => {
  const t = tenantOr404(params.tenant);
  if (!t.calendar.icsToken || !safeEqual(params.file.replace(/\.ics$/, ''), t.calendar.icsToken)) throw err(404, 'Nicht gefunden');
  send(res, 200, calendar.icsFeed(t), { 'Content-Type': 'text/calendar; charset=utf-8' });
});

// ---------- Öffentliche API (Widget, Buchungsseite, Formulare) ----------
function publicGuard(req, t, bucket, limit = 30) {
  if (!rateLimit(`${bucket}:${t.id}:${ip(req)}`, limit, 60e3)) throw err(429, 'Zu viele Anfragen – bitte kurz warten.');
}
r.get('/api/public/:tenant/config', (req, res, { params }) => send(res, 200, agent.widgetConfig(tenantOr404(params.tenant))));
r.post('/api/public/:tenant/chat', async (req, res, { params, body }) => {
  const t = tenantOr404(params.tenant);
  if (!t.chatbot.enabled) throw err(403, 'Chatbot deaktiviert');
  publicGuard(req, t, 'chat', 20);
  send(res, 200, await agent.chat(t, { sessionId: body.sessionId, message: body.message, voice: !!body.voice, page: body.page }));
});
r.get('/api/public/:tenant/slots', async (req, res, { params, query }) => {
  const t = tenantOr404(params.tenant);
  publicGuard(req, t, 'slots', 60);
  const from = query.get('from') ? new Date(query.get('from')) : new Date();
  send(res, 200, { slots: await calendar.freeSlots(t, { from: isNaN(from) || from < new Date() ? new Date() : from, days: Number(query.get('days')) || 14, limit: 120 }) });
});
r.get('/api/public/:tenant/me', (req, res, { params, query }) => {
  const t = tenantOr404(params.tenant);
  const c = contactsLib.byToken(query.get('c'));
  send(res, 200, c && c.tenant === t.id ? { name: c.name || '', hasPhone: !!c.phone, hasEmail: !!c.email } : {});
});
r.post('/api/public/:tenant/book', async (req, res, { params, body }) => {
  const t = tenantOr404(params.tenant);
  publicGuard(req, t, 'book', 10);
  if (body.website) throw err(400, 'Spam erkannt'); // Honeypot
  const input = {};
  for (const k of ['name', 'phone', 'email']) if (body[k]) input[k] = String(body[k]).slice(0, 200);
  if (body.consent) input.consent = 'ja';
  const out = await bookAppointment(t, { contact: Object.keys(input).length ? input : null, contactToken: body.c, start: body.start, reason: String(body.reason || '').slice(0, 500), source: body.src ? `buchungsseite:${String(body.src).slice(0, 30)}` : 'buchungsseite' });
  send(res, out.ok ? 200 : 409, out.ok ? { ok: true, label: out.label } : out);
});
// Universelles Kontaktformular (HTML-Form oder fetch) -> Anfragen-Nachfasser
r.post('/api/public/:tenant/lead', async (req, res, { params, body }) => {
  const t = tenantOr404(params.tenant);
  publicGuard(req, t, 'lead', 5);
  if (body.website || body._gotcha) throw err(400, 'Spam erkannt');
  const out = t.tools.anfragen.enabled ? await anfragen.newLead(t, { ...body, source: body.source || 'website-formular' })
    : (contactsLib.upsert(t.id, body, 'website-formular') ? { ok: true } : { ok: false, error: 'Telefon oder E-Mail fehlt' });
  const redirect = body._redirect && /^https?:\/\//.test(body._redirect) ? body._redirect : null;
  if (redirect && out.ok) return send(res, 303, '', { Location: redirect });
  send(res, out.ok ? 200 : 400, out);
});

// ---------- Integrations-API (API-Key) ----------
const EVENTS = {
  'missed-call': rueckruf.missedCall,
  'job-completed': bewertung.jobCompleted,
  lead: anfragen.newLead,
  'offer-sent': angebote.offerSent,
  'offer-status': angebote.offerStatus,
  'customer-visit': reaktivierung.customerVisit
};
r.post('/api/v1/:tenant/events/:event', async (req, res, { params, body, query }) => {
  const t = tenantOr404(params.tenant);
  requireApiKey(req, t, query);
  const fn = EVENTS[params.event];
  if (!fn) throw err(404, `Unbekanntes Ereignis. Erlaubt: ${Object.keys(EVENTS).join(', ')}`);
  const items = Array.isArray(body) ? body : Array.isArray(body.rows) ? body.rows : [body];
  const results = [];
  for (const it of items.slice(0, 1000)) results.push(await fn(t, it));
  send(res, 200, items.length === 1 ? results[0] : { ok: true, count: results.length, results });
});
r.post('/api/v1/:tenant/import/customers', (req, res, { params, body, query }) => {
  const t = tenantOr404(params.tenant);
  requireApiKey(req, t, query);
  const rows = Array.isArray(body) ? body : body.rows || [];
  send(res, 200, reaktivierung.importCustomers(t, rows));
});
r.post('/api/v1/:tenant/reaktivierung/run', async (req, res, { params, query }) => {
  const t = tenantOr404(params.tenant);
  requireApiKey(req, t, query);
  send(res, 200, await reaktivierung.runScan(t));
});
// Eingehende E-Mail (z. B. von Postmark/Mailgun/Make weitergeleitet)
r.post('/api/v1/:tenant/inbound/:channel', async (req, res, { params, body, query }) => {
  const t = tenantOr404(params.tenant);
  requireApiKey(req, t, query);
  const channel = ['sms', 'whatsapp', 'email'].includes(params.channel) ? params.channel : 'sms';
  const from = body.from || body.From || body.sender || body.phone || body.email;
  const text = body.text || body.body || body.Body || body.message || body['stripped-text'] || body.TextBody || '';
  send(res, 200, await inbound.handle(t, { channel, from: String(from || '').replace(/^.*<([^>]+)>.*$/, '$1'), body: text }));
});

// ---------- Telefonanlagen & Provider ----------
function twilioCheck(req, t, body, kind) {
  const c = (kind === 'whatsapp' ? t.channels.whatsapp : t.channels.sms) || t.channels.sms || {};
  const url = `${config.baseUrl()}${req.url}`;
  if (c.authToken && c.validateSignature !== false && !twilioValid(c.authToken, url, body, req.headers['x-twilio-signature'])) throw err(403, 'Ungültige Twilio-Signatur');
}
const TWIML_EMPTY = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>';
const xml = (s) => `<?xml version="1.0" encoding="UTF-8"?><Response>${s}</Response>`;
const sayDe = (t, text) => `<Say language="de-DE" voice="${escHtml(t.tools.rueckruf.ttsVoice || 'Polly.Vicki')}">${escHtml(text)}</Say>`;
const MISSED = ['no-answer', 'busy', 'failed', 'canceled'];

// Twilio Voice-URL der Rufnummer (schlüsselfertig): stellt zum Handy/Festnetz des Betriebs durch;
// nimmt niemand ab, folgt eine Ansage und die Rückruf-Automatik startet.
r.post('/hooks/twilio/:tenant/incoming', async (req, res, { params, body }) => {
  const t = tenantOr404(params.tenant);
  twilioCheck(req, t, body);
  const cfg = t.tools.rueckruf;
  const action = `${config.baseUrl()}/hooks/twilio/${t.id}/voice`;
  if (!cfg.forwardTo) {
    await rueckruf.missedCall(t, { phone: body.From || body.Caller });
    return send(res, 200, xml(sayDe(t, cfg.voiceMessage || `Willkommen bei ${t.name}. Wir sind gerade im Einsatz. Sie erhalten sofort eine SMS, über die Sie einen Rückruftermin wählen können.`) + '<Hangup/>'), { 'Content-Type': 'text/xml' });
  }
  const targets = [].concat(cfg.forwardTo).map((n) => `<Number>${escHtml(n)}</Number>`).join('');
  send(res, 200, xml(`<Dial timeout="${Number(cfg.ringSeconds) || 20}" action="${escHtml(action)}" method="POST" answerOnBridge="true">${targets}</Dial>`), { 'Content-Type': 'text/xml' });
});

// Dial-Action bzw. Status-Callback: verpasst = no-answer/busy/failed/canceled
// Wichtig: DialCallStatus (Ergebnis der Weiterleitung) hat Vorrang vor CallStatus (Status des Eingangsanrufs).
r.post('/hooks/twilio/:tenant/voice', async (req, res, { params, body }) => {
  const t = tenantOr404(params.tenant);
  twilioCheck(req, t, body);
  const isDialAction = body.DialCallStatus !== undefined;
  const status = String(isDialAction ? body.DialCallStatus : body.CallStatus || '').toLowerCase();
  if (MISSED.includes(status)) {
    await rueckruf.missedCall(t, { phone: body.From || body.Caller });
    // Anrufer ist bei einer Dial-Action noch in der Leitung (außer er hat selbst aufgelegt)
    if (isDialAction && status !== 'canceled') {
      const msg = t.tools.rueckruf.voiceMessage || `Leider ist bei ${t.name} gerade niemand erreichbar. Sie erhalten sofort eine SMS, über die Sie einen Rückruftermin wählen können. Auf Wiederhören!`;
      return send(res, 200, xml(sayDe(t, msg) + '<Hangup/>'), { 'Content-Type': 'text/xml' });
    }
  }
  send(res, 200, TWIML_EMPTY, { 'Content-Type': 'text/xml' });
});
// Twilio SMS/WhatsApp eingehend
r.post('/hooks/twilio/:tenant/sms', async (req, res, { params, body }) => {
  const t = tenantOr404(params.tenant);
  const isWa = String(body.From || '').startsWith('whatsapp:');
  twilioCheck(req, t, body, isWa ? 'whatsapp' : 'sms');
  await inbound.handle(t, { channel: isWa ? 'whatsapp' : 'sms', from: body.From, body: body.Body });
  send(res, 200, TWIML_EMPTY, { 'Content-Type': 'text/xml' });
});
// sipgate.io Push-API (hangup mit cause=cancel/noAnswer/busy)
r.post('/hooks/sipgate/:tenant', async (req, res, { params, body, query }) => {
  const t = tenantOr404(params.tenant);
  requireApiKey(req, t, query);
  if (body.event === 'hangup' && body.direction !== 'out' && ['cancel', 'noAnswer', 'busy', 'notFound', 'congestion'].includes(body.cause)) {
    await rueckruf.missedCall(t, { phone: body.from });
  }
  send(res, 200, '<?xml version="1.0" encoding="UTF-8"?><Response/>', { 'Content-Type': 'application/xml' });
});
// Placetel Notify-API (HungUp mit type=missed/voicemail/busy/unavailable)
r.post('/hooks/placetel/:tenant', async (req, res, { params, body, query }) => {
  const t = tenantOr404(params.tenant);
  requireApiKey(req, t, query);
  if (/hungup/i.test(body.event || '') && ['missed', 'voicemail', 'busy', 'unavailable', 'canceled'].includes(String(body.type || '').toLowerCase())) {
    await rueckruf.missedCall(t, { phone: body.from });
  }
  send(res, 200, { ok: true });
});

// ---------- Admin-API (Dashboard) ----------
r.get('/api/admin/tenants', (req, res, { query }) => {
  requireAdmin(req, null, query);
  send(res, 200, config.tenants().map((t) => ({ id: t.id, name: t.name, branche: t.branche })));
});
function adminT(req, params, query) { const t = tenantOr404(params.tenant); requireAdmin(req, t, query); return t; }

r.get('/api/admin/:tenant/overview', (req, res, { params, query }) => {
  const t = adminT(req, params, query);
  const since = new Date(Date.now() - 30 * 86400e3).toISOString();
  const count = (sql, ...a) => (db.one(sql, ...a) || {}).n || 0;
  const ev = (type) => count('SELECT COUNT(*) AS n FROM events WHERE tenant=? AND type=? AND created_at>?', t.id, type, since);
  send(res, 200, {
    tenant: { id: t.id, name: t.name, branche: t.branche, tools: Object.fromEntries(Object.entries(t.tools).map(([k, v]) => [k, v.enabled])), ai: llm.enabled() && t.ai.enabled, model: t.ai.model || llm.DEFAULT_MODEL, demoTimeScale: t.demo.timeScale },
    last30: {
      verpassteAnrufe: ev('call.missed'), neueAnfragen: ev('lead.created'), termine: ev('appointment.booked'),
      bewertungsKlicks: ev('link.review.clicked'), angeboteVerschickt: ev('offer.sent'),
      angeboteAngenommen: count("SELECT COUNT(*) AS n FROM offers WHERE tenant=? AND status='angenommen' AND updated_at>?", t.id, since),
      rueckgewonnen: ev('reactivation.won'), chats: ev('chat.started'),
      nachrichtenRaus: count("SELECT COUNT(*) AS n FROM messages WHERE tenant=? AND direction='out' AND created_at>?", t.id, since),
      antwortenRein: count("SELECT COUNT(*) AS n FROM messages WHERE tenant=? AND direction='in' AND channel NOT IN ('chat','voice') AND created_at>?", t.id, since)
    },
    jobsPending: count("SELECT COUNT(*) AS n FROM jobs WHERE tenant=? AND status='pending'", t.id),
    kontakte: count('SELECT COUNT(*) AS n FROM contacts WHERE tenant=?', t.id),
    wissen: { chunks: count('SELECT COUNT(*) AS n FROM knowledge WHERE tenant=?', t.id), zeichen: knowledge.totalSize(t.id) }
  });
});
const LISTS = {
  contacts: 'SELECT * FROM contacts WHERE tenant=? ORDER BY updated_at DESC LIMIT 500',
  messages: 'SELECT m.*, c.name AS contact_name FROM messages m LEFT JOIN contacts c ON c.id=m.contact_id WHERE m.tenant=? ORDER BY m.created_at DESC LIMIT 300',
  appointments: 'SELECT a.*, c.name AS contact_name, c.phone, c.email FROM appointments a LEFT JOIN contacts c ON c.id=a.contact_id WHERE a.tenant=? ORDER BY a.start DESC LIMIT 300',
  jobs: 'SELECT j.*, c.name AS contact_name FROM jobs j LEFT JOIN contacts c ON c.id=j.contact_id WHERE j.tenant=? ORDER BY j.run_at DESC LIMIT 300',
  offers: 'SELECT o.*, c.name AS contact_name FROM offers o LEFT JOIN contacts c ON c.id=o.contact_id WHERE o.tenant=? ORDER BY o.created_at DESC LIMIT 300',
  events: 'SELECT * FROM events WHERE tenant=? ORDER BY created_at DESC LIMIT 300',
  conversations: 'SELECT * FROM conversations WHERE tenant=? ORDER BY updated_at DESC LIMIT 100',
  knowledge: 'SELECT id, source, title, substr(content,1,300) AS preview, length(content) AS size FROM knowledge WHERE tenant=? ORDER BY source LIMIT 500'
};
r.get('/api/admin/:tenant/list/:what', (req, res, { params, query }) => {
  const t = adminT(req, params, query);
  if (!LISTS[params.what]) throw err(404, 'Unbekannte Liste');
  send(res, 200, db.all(LISTS[params.what], t.id));
});
r.patch('/api/admin/:tenant/contacts/:id', (req, res, { params, query, body }) => {
  const t = adminT(req, params, query);
  const c = contactsLib.byId(params.id);
  if (!c || c.tenant !== t.id) throw err(404, 'Kontakt nicht gefunden');
  if (body.status) {
    contactsLib.setStatus(c.id, String(body.status).slice(0, 30));
    if (['kunde', 'verloren'].includes(body.status)) scheduler.cancelForContact(t.id, c.id, ['rueckruf', 'anfragen'], `Status: ${body.status}`);
  }
  if (body.marketing_consent !== undefined) db.run('UPDATE contacts SET marketing_consent=? WHERE id=?', body.marketing_consent ? 1 : 0, c.id);
  if (body.opted_out) { contactsLib.optOut(c.id); scheduler.cancelForContact(t.id, c.id, ['rueckruf', 'anfragen', 'angebote', 'reaktivierung', 'bewertung'], 'abgemeldet'); }
  send(res, 200, contactsLib.byId(c.id));
});
r.get('/api/admin/:tenant/contacts/:id/export', (req, res, { params, query }) => send(res, 200, contactsLib.exportData(adminT(req, params, query).id, params.id)));
r.delete('/api/admin/:tenant/contacts/:id', (req, res, { params, query }) => { contactsLib.erase(adminT(req, params, query).id, params.id); send(res, 200, { ok: true }); });
r.post('/api/admin/:tenant/appointments/:id/cancel', (req, res, { params, query }) => { calendar.cancel(adminT(req, params, query), params.id); send(res, 200, { ok: true }); });
r.post('/api/admin/:tenant/knowledge', (req, res, { params, query, body }) => {
  const t = adminT(req, params, query);
  if (!body.content) throw err(400, 'content fehlt');
  send(res, 200, { ok: true, chunks: knowledge.addText(t.id, { title: body.title || '', content: String(body.content), source: 'manuell' }) });
});
r.post('/api/admin/:tenant/knowledge/crawl', async (req, res, { params, query, body }) => {
  const t = adminT(req, params, query);
  const urls = (body.urls && body.urls.length ? body.urls : t.chatbot.crawl.urls.length ? t.chatbot.crawl.urls : [t.website]).filter(Boolean);
  if (!urls.length) throw err(400, 'Keine URL angegeben');
  send(res, 200, await knowledge.crawl(t.id, urls, body.maxPages || t.chatbot.crawl.maxPages));
});
r.delete('/api/admin/:tenant/knowledge', (req, res, { params, query }) => { const t = adminT(req, params, query); knowledge.clear(t.id, query.get('source') || 'manuell'); send(res, 200, { ok: true }); });
r.post('/api/admin/:tenant/simulate/:event', async (req, res, { params, query, body }) => {
  const t = adminT(req, params, query);
  if (params.event === 'inbound') return send(res, 200, await inbound.handle(t, { channel: body.channel || 'sms', from: body.from, body: body.text }));
  if (params.event === 'reaktivierung') return send(res, 200, await reaktivierung.runScan(t));
  if (params.event === 'import') return send(res, 200, reaktivierung.importCustomers(t, body.rows || []));
  const fn = EVENTS[params.event];
  if (!fn) throw err(404, 'Unbekanntes Ereignis');
  send(res, 200, await fn(t, body));
});
r.post('/api/admin/:tenant/scheduler/run', async (req, res, { params, query, body }) => {
  const t = adminT(req, params, query);
  if (body.fastForwardHours) {
    const ms = Number(body.fastForwardHours) * 3600e3;
    for (const j of db.all("SELECT id, run_at FROM jobs WHERE tenant=? AND status='pending'", t.id)) {
      db.run('UPDATE jobs SET run_at=? WHERE id=?', new Date(Date.parse(j.run_at) - ms).toISOString(), j.id);
    }
  }
  send(res, 200, { ok: true, executed: await scheduler.tick(config.tenant) });
});
r.get('/api/admin/:tenant/setup', (req, res, { params, query }) => {
  const t = adminT(req, params, query);
  const b = config.baseUrl();
  send(res, 200, {
    embed: `<script src="${b}/widget.js" data-tenant="${t.id}" async></script>`,
    bookingPage: `${b}/book/${t.id}`,
    leadForm: `<form action="${b}/api/public/${t.id}/lead" method="POST">\n  <input name="name" placeholder="Name" required>\n  <input name="phone" placeholder="Telefon">\n  <input name="email" type="email" placeholder="E-Mail">\n  <textarea name="message" placeholder="Ihr Anliegen"></textarea>\n  <label><input type="checkbox" name="consent" value="ja"> Ich willige ein, per SMS/E-Mail kontaktiert zu werden.</label>\n  <input name="website" style="display:none" tabindex="-1" autocomplete="off">\n  <input type="hidden" name="_redirect" value="${t.website || 'https://'}/danke">\n  <button>Senden</button>\n</form>`,
    webhooks: Object.fromEntries(Object.keys(EVENTS).map((e) => [e, `${b}/api/v1/${t.id}/events/${e}`])),
    importCustomers: `${b}/api/v1/${t.id}/import/customers`,
    inboundEmail: `${b}/api/v1/${t.id}/inbound/email`,
    twilio: { voiceUrl: `${b}/hooks/twilio/${t.id}/incoming`, voiceStatusCallback: `${b}/hooks/twilio/${t.id}/voice`, messaging: `${b}/hooks/twilio/${t.id}/sms` },
    sipgate: `${b}/hooks/sipgate/${t.id}?key=API_KEY`,
    placetel: `${b}/hooks/placetel/${t.id}?key=API_KEY`,
    icsFeed: t.calendar.icsToken ? `${b}/cal/${t.id}/${t.calendar.icsToken}.ics` : '(calendar.icsToken setzen)',
    apiKeyHeader: 'X-API-Key: <apiKey aus der Mandanten-Konfiguration>'
  });
});

// ---------- Server ----------
function cors(req, res) {
  if (req.url.startsWith('/api/public/') || req.url.startsWith('/widget.js')) {
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  }
}

function createServer() {
  return http.createServer(async (req, res) => {
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
    cors(req, res);
    if (req.method === 'OPTIONS') return send(res, 204, '');
    const url = new URL(req.url, 'http://x');
    const m = r.match(req.method, url.pathname);
    if (!m) return send(res, 404, { error: 'Nicht gefunden' });
    try {
      const { body } = ['POST', 'PATCH', 'PUT'].includes(req.method) ? await readBody(req) : { body: {} };
      await m.handler(req, res, { params: m.params, query: url.searchParams, body });
    } catch (e) {
      if (!e.status) console.error(e);
      if (!res.headersSent) send(res, e.status || 500, { ok: false, error: e.status ? e.message : 'Interner Fehler' });
    }
  });
}

function boot({ dbFile, startScheduler = true } = {}) {
  db.init(dbFile);
  config.loadAll();
  for (const t of config.tenants()) {
    agent.seedProfile(t);
    reaktivierung.ensureDaily(t);
  }
  if (startScheduler) scheduler.start(config.tenant, Number(process.env.INOVAI_TICK_MS || 5000));
}

if (require.main === module) {
  boot();
  const port = Number(process.env.PORT || 3000);
  createServer().listen(port, () => {
    console.log(`INOVai läuft auf ${config.baseUrl()} (Port ${port})`);
    console.log(`  Mandanten: ${config.tenants().map((t) => t.id).join(', ') || '(keine)'} | KI: ${llm.enabled() ? llm.DEFAULT_MODEL : 'aus (Vorlagen-/Regel-Modus)'}`);
    console.log(`  Demo-Website: ${config.baseUrl()}/demo   Dashboard: ${config.baseUrl()}/admin`);
  });
}

module.exports = { createServer, boot };
