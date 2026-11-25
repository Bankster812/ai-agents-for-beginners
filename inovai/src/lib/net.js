'use strict';
// Sicheres Abrufen externer URLs (Website-Crawler, Kalender-Abgleich):
// nur http(s), keine internen/privaten Adressen (SSRF-Schutz), Weiterleitungen einzeln geprüft, Größenlimit.
const dns = require('node:dns').promises;
const net = require('node:net');

function privateIp(ip) {
  if (net.isIPv4(ip)) {
    const [a, b] = ip.split('.').map(Number);
    return a === 10 || a === 127 || a === 0 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) || a >= 224;
  }
  const v = ip.toLowerCase();
  if (v.startsWith('::ffff:')) return privateIp(v.slice(7));
  return v === '::1' || v === '::' || v.startsWith('fc') || v.startsWith('fd') || v.startsWith('fe80');
}

async function assertPublicUrl(raw) {
  const u = new URL(raw);
  if (!/^https?:$/.test(u.protocol)) throw new Error('Nur http/https erlaubt');
  if (process.env.INOVAI_ALLOW_PRIVATE_FETCH === '1') return u; // nur für lokale Tests
  const host = u.hostname.replace(/^\[|\]$/g, '');
  const addrs = net.isIP(host) ? [{ address: host }] : await dns.lookup(host, { all: true });
  if (!addrs.length || addrs.some((a) => privateIp(a.address))) throw new Error('Interne Adressen sind nicht erlaubt');
  return u;
}

async function safeFetch(raw, { maxBytes = 3 * 1024 * 1024, timeoutMs = 10000, headers = {} } = {}) {
  let url = raw;
  for (let hop = 0; hop < 5; hop++) {
    await assertPublicUrl(url);
    const res = await fetch(url, { headers, redirect: 'manual', signal: AbortSignal.timeout(timeoutMs) });
    if ([301, 302, 303, 307, 308].includes(res.status) && res.headers.get('location')) {
      url = new URL(res.headers.get('location'), url).toString();
      continue;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    const chunks = [];
    let size = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > maxBytes) { reader.cancel(); throw new Error('Antwort zu groß'); }
      chunks.push(value);
    }
    return { url, type: res.headers.get('content-type') || '', text: Buffer.concat(chunks).toString('utf8') };
  }
  throw new Error('Zu viele Weiterleitungen');
}

module.exports = { safeFetch, assertPublicUrl, privateIp };
