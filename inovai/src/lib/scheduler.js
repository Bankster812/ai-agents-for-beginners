'use strict';
// Persistente Job-Warteschlange: überlebt Neustarts, verschickt nur im erlaubten Versandfenster,
// Sequenzen werden automatisch gestoppt (Antwort, Termin gebucht, Angebot angenommen …).
const db = require('./db');
const { intoSendWindow } = require('./time');

const handlers = new Map(); // tool -> async (tenant, job) => void
let timer = null;
let running = false;

function register(tool, fn) { handlers.set(tool, fn); }

// Zeitpunkt nach einer Verzögerung; im Demo-Modus wird die Zeit gerafft (z. B. 1 Tag = 10 s).
function after(t, ms, { respectWindow = true } = {}) {
  const scale = (t.demo && t.demo.timeScale) || 1;
  const at = new Date(Date.now() + ms / scale);
  return respectWindow && scale === 1 ? intoSendWindow(at, t) : at;
}

function schedule(t, tool, { step = 0, sequenceKey = null, contactId = null, payload = {}, runAt }) {
  const jid = db.id('job_');
  db.run(`INSERT INTO jobs (id, tenant, tool, step, sequence_key, contact_id, payload, run_at, created_at)
    VALUES (?,?,?,?,?,?,?,?,?)`, jid, t.id, tool, step, sequenceKey, contactId, JSON.stringify(payload),
  new Date(runAt || Date.now()).toISOString(), db.now());
  return jid;
}

function cancelSequence(sequenceKey, reason = 'gestoppt') {
  if (!sequenceKey) return 0;
  const r = db.run("UPDATE jobs SET status='cancelled', last_error=? WHERE sequence_key=? AND status='pending'", reason, sequenceKey);
  return Number(r.changes || 0);
}

// Stoppt alle laufenden Sequenzen eines Kontakts für bestimmte Tools
function cancelForContact(tenant, contactId, tools, reason) {
  const placeholders = tools.map(() => '?').join(',');
  const r = db.run(`UPDATE jobs SET status='cancelled', last_error=? WHERE tenant=? AND contact_id=? AND status='pending' AND tool IN (${placeholders})`,
    reason, tenant, contactId, ...tools);
  return Number(r.changes || 0);
}

async function tick(getTenant) {
  if (running) return 0;
  running = true;
  let n = 0;
  try {
    const due = db.all("SELECT * FROM jobs WHERE status='pending' AND run_at<=? ORDER BY run_at LIMIT 100", db.now());
    for (const job of due) {
      // Doppelte Ausführung verhindern (mehrere Instanzen)
      const claim = db.run("UPDATE jobs SET status='running', attempts=attempts+1, claimed_at=? WHERE id=? AND status='pending'", db.now(), job.id);
      if (!Number(claim.changes)) continue;
      const t = getTenant(job.tenant);
      const fn = handlers.get(job.tool);
      if (!t || !fn) { db.run("UPDATE jobs SET status='failed', last_error=? WHERE id=?", 'Mandant/Tool unbekannt', job.id); continue; }
      try {
        job.payload = JSON.parse(job.payload || '{}');
        await fn(t, job);
        db.run("UPDATE jobs SET status='done' WHERE id=? AND status='running'", job.id);
        n++;
      } catch (e) {
        const retry = job.attempts + 1 < 3;
        db.run('UPDATE jobs SET status=?, last_error=?, run_at=? WHERE id=?', retry ? 'pending' : 'failed', e.message,
          new Date(Date.now() + 5 * 60e3).toISOString(), job.id);
      }
    }
  } finally {
    running = false;
  }
  return n;
}

function start(getTenant, intervalMs = 5000) {
  if (timer) return;
  // nach Absturz wieder aufnehmen – nur Jobs, die seit >10 min hängen (laufende anderer Instanzen bleiben unberührt)
  db.run("UPDATE jobs SET status='pending' WHERE status='running' AND (claimed_at IS NULL OR claimed_at<?)", new Date(Date.now() - 10 * 60e3).toISOString());
  timer = setInterval(() => tick(getTenant).catch(() => {}), intervalMs);
  timer.unref();
}
function stop() { if (timer) clearInterval(timer); timer = null; }

module.exports = { register, schedule, cancelSequence, cancelForContact, tick, start, stop, after };
