'use strict';
// Minimaler HTTP-Router + Body-Parser + Rate-Limit (keine Framework-Abhängigkeit).
const crypto = require('node:crypto');

class Router {
  constructor() { this.routes = []; }
  add(method, pattern, handler) {
    const keys = [];
    const re = new RegExp('^' + pattern.replace(/\/:(\w+)/g, (_, k) => { keys.push(k); return '/([^/]+)'; }) + '/?$');
    this.routes.push({ method, re, keys, handler });
  }
  get(p, h) { this.add('GET', p, h); }
  post(p, h) { this.add('POST', p, h); }
  patch(p, h) { this.add('PATCH', p, h); }
  delete(p, h) { this.add('DELETE', p, h); }
  match(method, path) {
    for (const r of this.routes) {
      if (r.method !== method) continue;
      const m = path.match(r.re);
      if (m) {
        const params = {};
        for (let i = 0; i < r.keys.length; i++) {
          try { params[r.keys[i]] = decodeURIComponent(m[i + 1]); } catch { return null; } // kaputte %-Kodierung -> 404
        }
        return { handler: r.handler, params };
      }
    }
    return null;
  }
}

function parseCsv(text) {
  const src = String(text).replace(/^﻿/, '');
  const firstLine = src.split(/\r?\n/)[0] || '';
  const delim = (firstLine.match(/;/g) || []).length >= (firstLine.match(/,/g) || []).length ? ';' : ',';
  const rows = [];
  let row = [], cur = '', q = false;
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (q) {
      if (ch === '"' && src[i + 1] === '"') { cur += '"'; i++; } else if (ch === '"') q = false; else cur += ch;
    } else if (ch === '"') q = true;
    else if (ch === delim) { row.push(cur); cur = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && src[i + 1] === '\n') i++;
      row.push(cur); cur = '';
      if (row.some((c) => c.trim() !== '')) rows.push(row);
      row = [];
    } else cur += ch;
  }
  row.push(cur);
  if (row.some((c) => c.trim() !== '')) rows.push(row);
  if (!rows.length) return [];
  const header = rows[0].map((h) => h.trim().toLowerCase().replace(/\s+/g, '_'));
  return rows.slice(1).map((r) => Object.fromEntries(header.map((h, i) => [h, (r[i] || '').trim()])));
}

function readBody(req, limit = 2 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > limit) { reject(Object.assign(new Error('Anfrage zu groß'), { status: 413 })); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      const type = (req.headers['content-type'] || '').toLowerCase();
      try {
        if (!raw) return resolve({ body: {}, raw });
        if (type.includes('application/json')) return resolve({ body: JSON.parse(raw), raw });
        if (type.includes('x-www-form-urlencoded')) return resolve({ body: Object.fromEntries(new URLSearchParams(raw)), raw });
        if (type.includes('csv')) return resolve({ body: { rows: parseCsv(raw) }, raw });
        try { return resolve({ body: JSON.parse(raw), raw }); } catch { return resolve({ body: { text: raw }, raw }); }
      } catch {
        reject(Object.assign(new Error('Ungültiger Body (JSON erwartet)'), { status: 400 }));
      }
    });
    req.on('error', reject);
  });
}

const buckets = new Map();
// Token-Bucket: max "limit" Anfragen pro "windowMs" je Schlüssel
function rateLimit(key, limit, windowMs) {
  const now = Date.now();
  let b = buckets.get(key);
  if (!b || now - b.start > windowMs) { b = { start: now, n: 0, windowMs }; buckets.set(key, b); }
  b.n++;
  if (buckets.size > 50000) evict(now);
  return b.n <= limit;
}
// Nur abgelaufene Einträge entfernen (nie alle Limits auf einmal zurücksetzen)
function evict(now) {
  for (const [k, b] of buckets) if (now - b.start > b.windowMs) buckets.delete(k);
}
rateLimit.peek = (key, windowMs) => {
  const b = buckets.get(key);
  return b && Date.now() - b.start <= windowMs ? b.n : 0;
};
setInterval(() => evict(Date.now()), 60e3).unref();

function safeEqual(a, b) {
  const x = Buffer.from(String(a || '')), y = Buffer.from(String(b || ''));
  return x.length === y.length && x.length > 0 && crypto.timingSafeEqual(x, y);
}

// Twilio-Signaturprüfung (X-Twilio-Signature)
function twilioValid(authToken, url, params, signature) {
  if (!authToken) return false;
  const data = url + Object.keys(params).sort().map((k) => k + params[k]).join('');
  const expected = crypto.createHmac('sha1', authToken).update(Buffer.from(data, 'utf8')).digest('base64');
  return safeEqual(expected, signature);
}

module.exports = { Router, readBody, parseCsv, rateLimit, safeEqual, twilioValid };
