'use strict';
// Persistenz: eingebautes SQLite von Node (keine externen Abhängigkeiten).
const { DatabaseSync } = require('node:sqlite');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

let db;

const SCHEMA = `
CREATE TABLE IF NOT EXISTS contacts (
  id TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  name TEXT, phone TEXT, email TEXT,
  source TEXT,
  marketing_consent INTEGER DEFAULT 0,
  opted_out INTEGER DEFAULT 0,
  last_visit TEXT,
  status TEXT DEFAULT 'neu',
  token TEXT UNIQUE,
  meta TEXT DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_tenant_phone ON contacts(tenant, phone);
CREATE INDEX IF NOT EXISTS idx_contacts_tenant_email ON contacts(tenant, email);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  contact_id TEXT,
  direction TEXT NOT NULL,      -- out | in
  channel TEXT NOT NULL,        -- sms | whatsapp | email | chat | voice
  tool TEXT,
  body TEXT,
  status TEXT,
  error TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_tenant ON messages(tenant, created_at);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  tool TEXT NOT NULL,
  step INTEGER DEFAULT 0,
  sequence_key TEXT,
  contact_id TEXT,
  payload TEXT DEFAULT '{}',
  run_at TEXT NOT NULL,
  status TEXT DEFAULT 'pending', -- pending | done | cancelled | failed
  attempts INTEGER DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, run_at);
CREATE INDEX IF NOT EXISTS idx_jobs_seq ON jobs(sequence_key, status);

CREATE TABLE IF NOT EXISTS appointments (
  id TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  contact_id TEXT,
  start TEXT NOT NULL,
  end TEXT NOT NULL,
  reason TEXT,
  source TEXT,
  status TEXT DEFAULT 'bestaetigt',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_appt_tenant ON appointments(tenant, start);

CREATE TABLE IF NOT EXISTS offers (
  id TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  contact_id TEXT,
  external_id TEXT,
  title TEXT,
  amount REAL,
  status TEXT DEFAULT 'offen',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS links (
  token TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  contact_id TEXT,
  kind TEXT,
  target TEXT,
  sequence_key TEXT,
  clicks INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  contact_id TEXT,
  channel TEXT,
  history TEXT DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge (
  id TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  source TEXT,
  title TEXT,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_tenant ON knowledge(tenant);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  type TEXT NOT NULL,
  data TEXT DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_tenant ON events(tenant, created_at);
`;

function init(file) {
  const target = file || process.env.INOVAI_DB || path.join(__dirname, '..', '..', 'data', 'inovai.db');
  if (target !== ':memory:') fs.mkdirSync(path.dirname(target), { recursive: true });
  db = new DatabaseSync(target);
  db.exec('PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;');
  db.exec(SCHEMA);
  // Migrationen für bestehende Installationen
  const cols = db.prepare('PRAGMA table_info(jobs)').all().map((c) => c.name);
  if (!cols.includes('claimed_at')) db.exec('ALTER TABLE jobs ADD COLUMN claimed_at TEXT');
  return db;
}

function get() {
  if (!db) init();
  return db;
}

const now = () => new Date().toISOString();
const id = (prefix = '') => prefix + crypto.randomUUID().replace(/-/g, '').slice(0, 20);
const token = () => crypto.randomBytes(12).toString('base64url');

function all(sql, ...params) { return get().prepare(sql).all(...params); }
function one(sql, ...params) { return get().prepare(sql).get(...params); }
function run(sql, ...params) { return get().prepare(sql).run(...params); }

function logEvent(tenant, type, data = {}) {
  run('INSERT INTO events (id, tenant, type, data, created_at) VALUES (?,?,?,?,?)',
    id('ev_'), tenant, type, JSON.stringify(data), now());
}

module.exports = { init, get, all, one, run, now, id, token, logEvent };
