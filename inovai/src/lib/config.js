'use strict';
// Mandanten-Konfiguration: eine JSON-Datei pro Kunde in config/tenants/<id>.json.
// Geheimnisse können als "env:VARIABLE" referenziert werden und werden aus der Umgebung gelesen.
const fs = require('node:fs');
const path = require('node:path');
const { DEFAULT_TEMPLATES } = require('./templates');

const TENANT_DIR = process.env.INOVAI_TENANT_DIR || path.join(__dirname, '..', '..', 'config', 'tenants');

const DEFAULTS = {
  timezone: 'Europe/Berlin',
  locale: 'de-DE',
  sendWindow: { start: '08:00', end: '20:00', days: [1, 2, 3, 4, 5, 6] }, // 0 = Sonntag
  channels: { order: ['whatsapp', 'sms', 'email'] },
  notify: {},
  calendar: {
    slotMinutes: 30,
    bufferMinutes: 0,
    leadTimeHours: 2,
    horizonDays: 21,
    maxParallel: 1,
    openingHours: {
      mon: [['08:00', '12:00'], ['13:00', '17:00']],
      tue: [['08:00', '12:00'], ['13:00', '17:00']],
      wed: [['08:00', '12:00'], ['13:00', '17:00']],
      thu: [['08:00', '12:00'], ['13:00', '17:00']],
      fri: [['08:00', '13:00']],
      sat: [], sun: []
    },
    closedDates: []
  },
  ai: {
    enabled: true,
    personalize: true,
    model: null, // null => INOVAI_MODEL bzw. Standardmodell
    tone: 'freundlich, kompetent, kurz, Sie-Form, kein Marketing-Sprech'
  },
  tools: {
    rueckruf: { enabled: true, delaySeconds: 20, followupMinutes: 120, channel: null, forwardTo: null, ringSeconds: 20, voiceMessage: '' },
    bewertung: { enabled: true, delayHours: 2, reminderDays: 3, reviewUrl: '', channel: null },
    anfragen: { enabled: true, followupDays: [1, 3, 7], channel: null },
    reaktivierung: { enabled: true, days: [30, 60, 90], offer: '', requireConsent: true, channel: null },
    angebote: { enabled: true, days: [2, 5, 10], channel: null }
  },
  chatbot: {
    enabled: true,
    voice: true,
    color: '#0f62fe',
    position: 'right',
    title: 'Digitaler Assistent',
    greeting: 'Hallo! 👋 Ich bin der digitale Assistent. Wie kann ich Ihnen helfen? Sie können mir schreiben oder per Mikrofon mit mir sprechen.',
    quickReplies: ['Termin buchen', 'Leistungen & Preise', 'Rückruf anfordern', 'Öffnungszeiten'],
    privacyUrl: '',
    knowledge: '',
    crawl: { urls: [], maxPages: 40 }
  },
  templates: DEFAULT_TEMPLATES,
  demo: { timeScale: 1 }
};

function isObj(v) { return v && typeof v === 'object' && !Array.isArray(v); }

function deepMerge(base, over) {
  if (!isObj(base) || !isObj(over)) return over === undefined ? base : over;
  const out = { ...base };
  for (const [k, v] of Object.entries(over)) out[k] = isObj(v) && isObj(base[k]) ? deepMerge(base[k], v) : v;
  return out;
}

function resolveEnv(v) {
  if (typeof v === 'string' && v.startsWith('env:')) return process.env[v.slice(4)] || '';
  if (Array.isArray(v)) return v.map(resolveEnv);
  if (isObj(v)) return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, resolveEnv(x)]));
  return v;
}

const cache = new Map();

function normalize(raw) {
  const t = resolveEnv(deepMerge(DEFAULTS, raw));
  if (!t.id || !/^[a-z0-9-]{2,40}$/.test(t.id)) throw new Error(`Ungültige Mandanten-ID: ${t.id}`);
  if (!t.name) throw new Error(`Mandant ${t.id}: "name" fehlt`);
  if (process.env.INOVAI_DEMO === '1' && !raw.demo) t.demo = { timeScale: 8640 }; // 1 Tag = 10 Sekunden
  return t;
}

function loadAll() {
  cache.clear();
  if (!fs.existsSync(TENANT_DIR)) return cache;
  for (const f of fs.readdirSync(TENANT_DIR)) {
    if (!f.endsWith('.json')) continue;
    const raw = JSON.parse(fs.readFileSync(path.join(TENANT_DIR, f), 'utf8'));
    const t = normalize(raw);
    cache.set(t.id, t);
  }
  return cache;
}

function register(raw) { const t = normalize(raw); cache.set(t.id, t); return t; }
function tenant(id) { if (!cache.size) loadAll(); return cache.get(id) || null; }
function tenants() { if (!cache.size) loadAll(); return [...cache.values()]; }
function baseUrl() { return (process.env.INOVAI_BASE_URL || `http://localhost:${process.env.PORT || 3000}`).replace(/\/$/, ''); }

module.exports = { loadAll, register, tenant, tenants, baseUrl, deepMerge, DEFAULTS, TENANT_DIR };
