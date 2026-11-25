'use strict';
// PRODUKT 2: Bewertungs-Booster (Restaurants, Friseure, Werkstätten …)
// Nach Leistung/Besuch -> zeitversetzte Bitte um Google-Bewertung -> 1 Erinnerung, falls nicht geklickt.
// Rechtlich: Bewertungsanfragen gelten laut BGH (VI ZR 225/17) als Werbung -> Einwilligung standardmäßig Pflicht.
// Google-Richtlinie: KEIN "Review Gating" – alle Kunden bekommen denselben Link.
const db = require('../lib/db');
const scheduler = require('../lib/scheduler');
const contacts = require('../lib/contacts');
const C = require('./common');

const TOOL = 'bewertung';

async function jobCompleted(t, input) {
  C.requireTool(t, TOOL);
  const cfg = t.tools[TOOL];
  const c = contacts.upsert(t.id, input, 'auftrag-erledigt');
  if (!c) return { ok: false, error: 'Telefon oder E-Mail des Kunden fehlt' };
  const visit = contacts.parseDate(contacts.pick(input, ['date', 'datum', 'visit_date'])) || db.now();
  db.run('UPDATE contacts SET last_visit=?, updated_at=? WHERE id=?', visit, db.now(), c.id);
  if (!cfg.reviewUrl) return { ok: false, error: 'tools.bewertung.reviewUrl (Google-Bewertungslink) ist nicht konfiguriert' };
  if (cfg.requireConsent !== false && !c.marketing_consent) {
    db.logEvent(t.id, 'review.skipped', { contact: c.id, reason: 'keine Einwilligung' });
    return { ok: true, contact: c.id, scheduled: false, reason: 'keine Einwilligung (consent) – nicht angeschrieben' };
  }
  const m = C.meta(c);
  const cooldown = (cfg.cooldownDays || 120) * 86400e3 / ((t.demo && t.demo.timeScale) || 1);
  if (m.review_requested_at && Date.now() - Date.parse(m.review_requested_at) < cooldown) {
    return { ok: true, contact: c.id, scheduled: false, reason: 'wurde kürzlich bereits angefragt' };
  }
  if (C.hasActive(t, c.id, TOOL)) return { ok: true, contact: c.id, scheduled: false, reason: 'bereits geplant' };
  C.setMeta(c, { review_requested_at: db.now() });
  const seq = `${TOOL}:${c.id}:${Date.now()}`;
  scheduler.schedule(t, TOOL, { step: 0, sequenceKey: seq, contactId: c.id, runAt: scheduler.after(t, cfg.delayHours * 3600e3) });
  if (cfg.reminderDays) {
    scheduler.schedule(t, TOOL, { step: 1, sequenceKey: seq, contactId: c.id, runAt: scheduler.after(t, cfg.delayHours * 3600e3 + cfg.reminderDays * 86400e3) });
  }
  return { ok: true, contact: c.id, scheduled: true };
}

async function run(t, job) {
  const c = contacts.byId(job.contact_id);
  if (!c || c.opted_out) return;
  const link = C.trackedLink(t, c, 'review', t.tools[TOOL].reviewUrl, job.sequence_key);
  await C.composeAndSend(t, c, TOOL, job.step, { link }, { anlass: 'Bitte um Google-Bewertung nach Besuch' });
}

scheduler.register(TOOL, run);
module.exports = { jobCompleted, TOOL };
