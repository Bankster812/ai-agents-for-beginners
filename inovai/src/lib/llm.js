'use strict';
// Claude-Anbindung über das offizielle Anthropic SDK.
// Ohne API-Key läuft alles weiter: Automationen nutzen die Textvorlagen, der Chatbot antwortet aus der Wissensbasis.
const DEFAULT_MODEL = process.env.INOVAI_MODEL || 'claude-opus-5-5';

let client = null;
let mock = null; // für Tests: async (params) => message

function enabled() {
  return !!(mock || process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN);
}

function getClient() {
  if (!client) {
    const Anthropic = require('@anthropic-ai/sdk');
    const Ctor = Anthropic.default || Anthropic;
    client = new Ctor({ maxRetries: 2, timeout: 60_000 });
  }
  return client;
}

// Eine Anfrage an die Messages API. Server-seitiger Refusal-Fallback ist aktiv ("default").
async function create(params, { model, effort = 'low' } = {}) {
  const req = {
    model: model || DEFAULT_MODEL,
    max_tokens: params.max_tokens || 4000,
    output_config: { effort },
    ...params
  };
  if (mock) return mock(req);
  const c = getClient();
  if (/^claude-(opus-5|sonnet-5-5|fable)/.test(req.model)) {
    return c.beta.messages.create({ ...req, betas: ['server-side-fallback-2026-07-01'], fallbacks: 'default' });
  }
  return c.messages.create(req);
}

function textOf(msg) {
  return (msg.content || []).filter((b) => b.type === 'text').map((b) => b.text).join('\n').trim();
}

// Personalisiert eine Vorlage leicht – Links, Fakten und Länge bleiben erhalten.
async function personalize(t, { tool, channel, draft, context }) {
  if (!enabled() || !t.ai.enabled || !t.ai.personalize) return draft;
  const isEmail = channel === 'email';
  const system = `Du schreibst kurze Kundennachrichten im Namen von "${t.name}" (${t.branche || 'Dienstleister'}).
Ton: ${t.ai.tone}. Sprache: Deutsch.
Regeln:
- Behalte alle Links, Telefonnummern, Namen, Beträge und Fakten exakt bei. Erfinde nichts dazu (keine Rabatte, Preise oder Zusagen).
- ${isEmail ? 'Gib JSON zurück: {"subject": "...", "body": "..."}. Body max. 120 Wörter.' : 'Max. 300 Zeichen, eine SMS/WhatsApp-Nachricht, höchstens 1 Emoji. Gib nur den Nachrichtentext zurück.'}
- Klinge wie ein Mensch aus dem Betrieb, nicht wie Werbung.`;
  const user = `Anlass: ${tool}\nKontext: ${JSON.stringify(context)}\nEntwurf:\n${isEmail ? JSON.stringify(draft) : draft}\n\nVerbessere den Entwurf passend zum Kontext.`;
  try {
    const msg = await create({ system, messages: [{ role: 'user', content: user }], max_tokens: 2000 }, { model: t.ai.model });
    if (msg.stop_reason === 'refusal') return draft;
    const out = textOf(msg);
    if (isEmail) {
      const j = JSON.parse(out.slice(out.indexOf('{'), out.lastIndexOf('}') + 1));
      if (!j.subject || !j.body) return draft;
      if (draft.body.includes('http') && !keepsLinks(draft.body, j.body)) return draft;
      return { subject: String(j.subject).slice(0, 150), body: String(j.body) };
    }
    if (!out || out.length > 480 || !keepsLinks(draft, out)) return draft;
    return out;
  } catch (e) {
    return draft;
  }
}

function keepsLinks(a, b) {
  const links = String(a).match(/https?:\/\/\S+/g) || [];
  return links.every((l) => String(b).includes(l.replace(/[.,)]+$/, '')));
}

// Antwort auf eine eingehende Kundennachricht (SMS/WhatsApp/E-Mail) – kurz, mit Ziel Termin/Rückruf.
async function replyToInbound(t, { history, contact, bookingLink, tool }) {
  if (!enabled() || !t.ai.enabled) return null;
  const system = `Du bist der digitale Assistent von "${t.name}" (${t.branche || ''}). Du antwortest per SMS/WhatsApp.
Ton: ${t.ai.tone}. Antworte auf Deutsch, max. 300 Zeichen.
Ziel: Anliegen kurz verstehen und einen Termin oder Rückruf vereinbaren. Buchungslink: ${bookingLink}
Telefon des Betriebs: ${t.phone || '-'}. Fakten zum Betrieb: ${(t.chatbot.knowledge || '').slice(0, 3000)}
Bei Notfällen (z. B. Wasserrohrbruch, Gasgeruch, Stromausfall) verweise sofort auf die Notfallnummer ${t.emergencyPhone || t.phone || ''}${/gas/i.test(t.branche || '') ? ' bzw. bei Gasgeruch auf den Gasnotruf/112' : ''}.
Erfinde keine Preise, Termine oder Zusagen. Wenn du etwas nicht weißt: ein Mitarbeiter meldet sich.`;
  const messages = history.slice(-12).map((m) => ({ role: m.role, content: m.content }));
  try {
    const msg = await create({ system, messages, max_tokens: 1500 }, { model: t.ai.model });
    if (msg.stop_reason === 'refusal') return null;
    return textOf(msg).slice(0, 480) || null;
  } catch {
    return null;
  }
}

module.exports = { enabled, create, textOf, personalize, replyToInbound, DEFAULT_MODEL, _setMock: (fn) => { mock = fn; } };
