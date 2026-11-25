'use strict';
const db = require('./db');
const { normalizePhone } = require('./channels');

// Flexible Feldnamen, damit beliebige Systeme (CRM, Kasse, Telefonanlage, Formulare) ohne Mapping andocken können.
const ALIASES = {
  name: ['name', 'full_name', 'fullname', 'kunde', 'kundenname', 'customer', 'customer_name', 'vorname_nachname', 'contact_name'],
  firstName: ['vorname', 'first_name', 'firstname', 'given_name'],
  lastName: ['nachname', 'last_name', 'lastname', 'family_name', 'surname'],
  phone: ['phone', 'telefon', 'tel', 'mobile', 'mobil', 'handy', 'phone_number', 'telefonnummer', 'caller', 'from', 'from_number', 'caller_id', 'callerid', 'number', 'msisdn'],
  email: ['email', 'e_mail', 'e-mail', 'mail', 'emailaddress', 'email_address'],
  consent: ['consent', 'einwilligung', 'marketing_consent', 'opt_in', 'optin', 'newsletter', 'dsgvo'],
  lastVisit: ['last_visit', 'letzter_besuch', 'lastvisit', 'last_seen', 'zuletzt', 'letzter_termin', 'last_appointment']
};

function pick(obj, keys) {
  const lower = Object.fromEntries(Object.entries(obj || {}).map(([k, v]) => [k.toLowerCase().replace(/\s+/g, '_'), v]));
  for (const k of keys) if (lower[k] !== undefined && lower[k] !== null && String(lower[k]).trim() !== '') return String(lower[k]).trim();
  return '';
}

const truthy = (v) => /^(1|true|ja|yes|y|x|on|checked)$/i.test(String(v || '').trim());

// Deutsches oder ISO-Datum -> ISO
function parseDate(v) {
  if (!v) return null;
  const s = String(v).trim();
  const de = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{2,4})/);
  if (de) {
    const y = de[3].length === 2 ? 2000 + Number(de[3]) : Number(de[3]);
    return new Date(Date.UTC(y, Number(de[2]) - 1, Number(de[1]), 12)).toISOString();
  }
  const d = new Date(s);
  return isNaN(d) ? null : d.toISOString();
}

function extract(input) {
  let name = pick(input, ALIASES.name);
  const fn = pick(input, ALIASES.firstName), ln = pick(input, ALIASES.lastName);
  if (!name && (fn || ln)) name = `${fn} ${ln}`.trim();
  const email = pick(input, ALIASES.email).toLowerCase();
  // Namen bereinigen: keine Links/Steuerzeichen, max. 60 Zeichen (Name landet in SMS-Anrede)
  name = name.replace(/https?:\/\/\S+|www\.\S+/gi, '').replace(/[<>{}\[\]\r\n\t]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 60);
  return {
    name,
    phone: normalizePhone(pick(input, ALIASES.phone)),
    email: /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? email : '',
    consent: pick(input, ALIASES.consent),
    lastVisit: parseDate(pick(input, ALIASES.lastVisit))
  };
}

function find(tenant, { phone, email }) {
  if (phone) { const c = db.one('SELECT * FROM contacts WHERE tenant=? AND phone=?', tenant, phone); if (c) return c; }
  if (email) { const c = db.one('SELECT * FROM contacts WHERE tenant=? AND email=?', tenant, email); if (c) return c; }
  return null;
}

function byId(id) { return db.one('SELECT * FROM contacts WHERE id=?', id); }
function byToken(token) { return token ? db.one('SELECT * FROM contacts WHERE token=?', token) : null; }

// Legt Kontakt an oder aktualisiert vorhandenen (Dedup über Telefon/E-Mail).
// untrusted = Daten aus öffentlichen Formularen/Chat: bestehende Kontakte werden dann NICHT überschrieben
// (kein Ändern von Name/Einwilligung fremder Kontakte), nur leere Felder werden ergänzt.
function upsert(tenant, input, source, { untrusted = false } = {}) {
  const f = extract(input);
  if (!f.phone && !f.email) return null;
  const existing = find(tenant, f);
  const now = db.now();
  if (untrusted) f.consent = ''; // Werbe-Einwilligung nur über vertrauenswürdige Quellen (API/Import/Dashboard)
  if (existing && untrusted) {
    db.run(`UPDATE contacts SET name=COALESCE(NULLIF(name, ''), NULLIF(?, '')), phone=COALESCE(NULLIF(phone, ''), NULLIF(?, '')),
      email=COALESCE(NULLIF(email, ''), NULLIF(?, '')), updated_at=? WHERE id=?`, f.name, f.phone, f.email, now, existing.id);
    return byId(existing.id);
  }
  if (existing) {
    db.run(`UPDATE contacts SET name=COALESCE(NULLIF(?, ''), name), phone=COALESCE(NULLIF(?, ''), phone),
      email=COALESCE(NULLIF(?, ''), email), marketing_consent=CASE WHEN ? = '' THEN marketing_consent ELSE ? END,
      last_visit=COALESCE(?, last_visit), updated_at=? WHERE id=?`,
    f.name, f.phone, f.email, f.consent, truthy(f.consent) ? 1 : 0, f.lastVisit, now, existing.id);
    return byId(existing.id);
  }
  const cid = db.id('c_');
  db.run(`INSERT INTO contacts (id, tenant, name, phone, email, source, marketing_consent, last_visit, token, created_at, updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)`, cid, tenant, f.name, f.phone, f.email, source || '', truthy(f.consent) ? 1 : 0,
  f.lastVisit, db.token(), now, now);
  db.logEvent(tenant, 'contact.created', { contact: cid, source });
  return byId(cid);
}

function setStatus(id, status) { db.run('UPDATE contacts SET status=?, updated_at=? WHERE id=?', status, db.now(), id); }
function optOut(id) { db.run('UPDATE contacts SET opted_out=1, marketing_consent=0, updated_at=? WHERE id=?', db.now(), id); }

// DSGVO Art. 17: Kontakt inkl. aller Daten löschen
function erase(tenant, id) {
  for (const tbl of ['messages', 'jobs', 'appointments', 'offers', 'links', 'conversations']) {
    db.run(`DELETE FROM ${tbl} WHERE tenant=? AND contact_id=?`, tenant, id);
  }
  db.run('DELETE FROM contacts WHERE tenant=? AND id=?', tenant, id);
}

// DSGVO Art. 15: Auskunft
function exportData(tenant, id) {
  const out = { contact: db.one('SELECT * FROM contacts WHERE tenant=? AND id=?', tenant, id) };
  for (const tbl of ['messages', 'appointments', 'offers', 'conversations']) {
    out[tbl] = db.all(`SELECT * FROM ${tbl} WHERE tenant=? AND contact_id=?`, tenant, id);
  }
  return out;
}

module.exports = { upsert, find, byId, byToken, setStatus, optOut, erase, exportData, extract, pick, truthy, parseDate };
