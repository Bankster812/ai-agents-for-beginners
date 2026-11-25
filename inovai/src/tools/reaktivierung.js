'use strict';
// PRODUKT 4: Alt-Kunden-Rückholer (Fitnessstudios, Physiopraxen, Kosmetikstudios …)
// Kundenliste (CSV/Export/API) -> tägliche Prüfung -> nach 30/60/90 Tagen Inaktivität persönliche Nachricht -> Rückbuchung.
// Rechtlich: Werbliche SMS/E-Mail nur mit Einwilligung (§ 7 UWG) -> requireConsent standardmäßig aktiv.
const db = require('../lib/db');
const scheduler = require('../lib/scheduler');
const contacts = require('../lib/contacts');
const C = require('./common');

const TOOL = 'reaktivierung';
const SCAN = 'reaktivierung-scan';

function scale(t) { return (t.demo && t.demo.timeScale) || 1; }
function daysSince(t, iso) { return ((Date.now() - Date.parse(iso)) * scale(t)) / 86400e3; }

// Einzelner Besuch / Kauf / Termin -> Kunde ist aktiv, Rückhol-Stufe zurücksetzen
async function customerVisit(t, input) {
  const c = contacts.upsert(t.id, input, 'besuch');
  if (!c) return { ok: false, error: 'Telefon oder E-Mail fehlt' };
  const visit = contacts.parseDate(contacts.pick(input, ['date', 'datum', 'visit_date', ...['last_visit', 'letzter_besuch']])) || db.now();
  db.run('UPDATE contacts SET last_visit=?, updated_at=? WHERE id=?', visit, db.now(), c.id);
  const m = C.meta(c);
  if (m.reakt_stage) {
    db.logEvent(t.id, 'reactivation.won', { contact: c.id, stage: m.reakt_stage });
    C.setMeta(c, { reakt_stage: 0, reakt_won_at: db.now() });
  }
  return { ok: true, contact: c.id };
}

// Massenimport (z. B. CSV-Export aus Studio-Software)
function importCustomers(t, rows) {
  let imported = 0, skipped = 0;
  for (const r of rows) { const c = contacts.upsert(t.id, r, 'import'); c ? imported++ : skipped++; }
  db.logEvent(t.id, 'customers.imported', { imported, skipped });
  return { ok: true, imported, skipped };
}

async function runScan(t) {
  C.requireTool(t, TOOL);
  const cfg = t.tools[TOOL];
  const limit = cfg.dailyLimit || 200;
  const rows = db.all('SELECT * FROM contacts WHERE tenant=? AND opted_out=0 AND last_visit IS NOT NULL', t.id);
  let sent = 0, skippedNoConsent = 0;
  for (const c of rows) {
    if (sent >= limit) break;
    const m = C.meta(c);
    const stage = m.reakt_stage || 0;
    if (stage >= cfg.days.length) continue;
    const inactive = daysSince(t, c.last_visit);
    if (inactive < cfg.days[stage]) continue;
    // Abstand zwischen den Stufen einhalten (keine 3 Nachrichten auf einmal bei sehr alten Kunden)
    if (stage > 0 && m.reakt_last_at && daysSince(t, m.reakt_last_at) < cfg.days[stage] - cfg.days[stage - 1]) continue;
    if (cfg.requireConsent !== false && !c.marketing_consent) { skippedNoConsent++; continue; }
    const offer = cfg.offer ? cfg.offer : '';
    const res = await C.composeAndSend(t, c, TOOL, stage, {
      link: C.bookingLink(t, c, TOOL, `${TOOL}:${c.id}`),
      angebot_aktion: offer, tage: Math.round(inactive)
    }, { anlass: 'Kunde war länger nicht da', tage_inaktiv: Math.round(inactive), aktion: offer || null });
    C.setMeta(c, { reakt_stage: stage + 1, reakt_last_at: db.now() });
    if (res.ok) sent++;
  }
  db.logEvent(t.id, 'reactivation.scan', { sent, skippedNoConsent });
  return { ok: true, sent, skippedNoConsent };
}

// Täglicher Lauf (selbst-planend). Wird beim Start pro Mandant angelegt.
async function scanJob(t) {
  try { if (t.tools[TOOL].enabled) await runScan(t); } finally {
    scheduler.schedule(t, SCAN, { runAt: scheduler.after(t, 86400e3) });
  }
}

function ensureDaily(t) {
  if (!t.tools[TOOL].enabled) return;
  if (!db.one("SELECT id FROM jobs WHERE tenant=? AND tool=? AND status IN ('pending','running')", t.id, SCAN)) {
    scheduler.schedule(t, SCAN, { runAt: scheduler.after(t, 60e3) });
  }
}

scheduler.register(SCAN, scanJob);
module.exports = { customerVisit, importCustomers, runScan, ensureDaily, TOOL };
