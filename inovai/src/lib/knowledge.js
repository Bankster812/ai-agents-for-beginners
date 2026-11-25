'use strict';
// Wissensbasis für den Chatbot: Firmendaten aus der Konfiguration + automatisch gecrawlte Website.
const db = require('./db');
const { safeFetch } = require('./net');

const STOP = new Set('der die das und oder ein eine einer eines einem einen ist sind war wir sie ich du er es zu zum zur im in am an auf für mit von vom bei aus wie was wer wo wann ob nicht auch noch nur so dass den dem des als bis über unter nach vor kann können haben hat habe ihr ihre ihren uns unser unsere mein meine man sich da hier dort sehr mehr gibt'.split(' '));

// Umlaute falten (Vertrag/Verträge, Förderung/Foerderung werden gleich behandelt)
function norm(s) {
  return String(s).toLowerCase().replace(/ä|ae/g, 'a').replace(/ö|oe/g, 'o').replace(/ü|ue/g, 'u').replace(/ß/g, 'ss');
}
function stem(w) { return w.replace(/(ungen|ung|en|er|e|n|s)$/, '') || w; }
function tokens(s) {
  return norm(s).split(/[^a-z0-9€]+/).filter((w) => w.length > 1 && !STOP.has(w)).map(stem);
}

function chunk(text, size = 900) {
  const paras = String(text).split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
  const out = [];
  let cur = '';
  for (const p of paras) {
    if ((cur + '\n' + p).length > size && cur) { out.push(cur); cur = ''; }
    cur = cur ? cur + '\n' + p : p;
    while (cur.length > size * 1.5) { out.push(cur.slice(0, size)); cur = cur.slice(size); }
  }
  if (cur) out.push(cur);
  return out;
}

function addText(tenant, { title = '', content, source = 'manuell' }) {
  let n = 0;
  for (const c of chunk(content)) {
    db.run('INSERT INTO knowledge (id, tenant, source, title, content, created_at) VALUES (?,?,?,?,?,?)', db.id('kn_'), tenant, source, title, c, db.now());
    n++;
  }
  return n;
}

function clear(tenant, sourcePrefix) {
  if (sourcePrefix) db.run('DELETE FROM knowledge WHERE tenant=? AND source LIKE ?', tenant, sourcePrefix + '%');
  else db.run('DELETE FROM knowledge WHERE tenant=?', tenant);
}

function allChunks(tenant) { return db.all('SELECT * FROM knowledge WHERE tenant=? ORDER BY source, created_at', tenant); }
function totalSize(tenant) { const r = db.one('SELECT SUM(LENGTH(content)) AS n FROM knowledge WHERE tenant=?', tenant); return (r && r.n) || 0; }

// BM25-Suche
function search(tenant, query, k = 6) {
  const docs = allChunks(tenant);
  if (!docs.length) return [];
  const q = [...new Set(tokens(query))];
  if (!q.length) return [];
  const toks = docs.map((d) => tokens(d.title + ' ' + d.content));
  const avg = toks.reduce((a, b) => a + b.length, 0) / toks.length;
  const df = Object.fromEntries(q.map((w) => [w, toks.filter((t) => t.includes(w)).length]));
  const scored = docs.map((d, i) => {
    const tf = {};
    for (const w of toks[i]) tf[w] = (tf[w] || 0) + 1;
    let s = 0;
    for (const w of q) {
      if (!tf[w]) continue;
      const idf = Math.log(1 + (docs.length - df[w] + 0.5) / (df[w] + 0.5));
      s += idf * (tf[w] * 2.2) / (tf[w] + 1.2 * (0.25 + 0.75 * toks[i].length / avg));
    }
    return { ...d, score: s };
  });
  return scored.filter((d) => d.score > 0).sort((a, b) => b.score - a.score).slice(0, k);
}

// ---------- Website-Crawler ----------
function decode(s) {
  return s.replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'")
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&auml;/g, 'ä').replace(/&ouml;/g, 'ö').replace(/&uuml;/g, 'ü')
    .replace(/&Auml;/g, 'Ä').replace(/&Ouml;/g, 'Ö').replace(/&Uuml;/g, 'Ü').replace(/&szlig;/g, 'ß').replace(/&euro;/g, '€')
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)));
}

function htmlToText(html) {
  const title = decode(((html.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [])[1] || '').trim());
  const desc = decode(((html.match(/<meta[^>]+name=["']description["'][^>]+content=["']([^"']*)/i) || [])[1] || '').trim());
  // strukturierte Daten (JSON-LD) enthalten oft Adresse, Öffnungszeiten, Preise
  const ld = [...html.matchAll(/<script[^>]+application\/ld\+json[^>]*>([\s\S]*?)<\/script>/gi)].map((m) => m[1].trim()).join('\n');
  let body = html.replace(/<(script|style|noscript|svg|iframe|template)[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<(br|\/p|\/div|\/li|\/h[1-6]|\/tr|\/section|\/article|\/header|\/footer)[^>]*>/gi, '\n')
    .replace(/<li[^>]*>/gi, '\n• ')
    .replace(/<[^>]+>/g, ' ');
  body = decode(body).replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n\n').trim();
  const text = [desc, body, ld ? `Strukturierte Daten: ${ld.slice(0, 4000)}` : ''].filter(Boolean).join('\n\n');
  return { title, text };
}

function links(html, base) {
  const out = new Set();
  for (const m of html.matchAll(/href=["']([^"'#]+)["']/gi)) {
    try {
      const u = new URL(m[1], base);
      if (u.origin !== new URL(base).origin) continue;
      if (/\.(pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|mp4|mp3|css|js|ico|xml)$/i.test(u.pathname)) continue;
      u.hash = ''; u.search = '';
      out.add(u.toString());
    } catch { /* ungültiger Link */ }
  }
  return [...out];
}

async function fetchText(url) {
  const r = await safeFetch(url, { headers: { 'User-Agent': 'INOVai-Knowledge-Bot/1.0 (+Website-Assistent)' } });
  if (!/html|xml|text/.test(r.type)) throw new Error('kein HTML');
  return r.text;
}

async function crawl(tenant, startUrls, maxPages = 40) {
  maxPages = Math.max(1, Math.min(Number(maxPages) || 40, 150));
  const origins = new Set(startUrls.map((u) => new URL(u).origin));
  const queue = [...startUrls];
  const seen = new Set();
  const pages = [];
  // sitemap.xml mitnehmen, falls vorhanden
  for (const s of startUrls) {
    try {
      const sm = await fetchText(new URL('/sitemap.xml', s).toString());
      for (const m of sm.matchAll(/<loc>([^<]+)<\/loc>/g)) {
        try { const u = new URL(m[1].trim()); if (origins.has(u.origin) && !/sitemap/i.test(u.pathname)) queue.push(u.toString()); } catch { /* ungültig */ }
      }
    } catch { /* keine Sitemap */ }
  }
  while (queue.length && pages.length < maxPages) {
    const url = queue.shift();
    if (seen.has(url)) continue;
    seen.add(url);
    try {
      const html = await fetchText(url);
      const { title, text } = htmlToText(html);
      if (text.length > 80) pages.push({ url, title, text });
      for (const l of links(html, url)) if (!seen.has(l)) queue.push(l);
    } catch { /* Seite übersprungen */ }
  }
  if (pages.length) {
    clear(tenant, 'web:');
    for (const p of pages) addText(tenant, { title: p.title, content: p.text.slice(0, 30000), source: `web:${p.url}` });
  }
  db.logEvent(tenant, 'knowledge.crawled', { pages: pages.length });
  return { pages: pages.length, urls: pages.map((p) => p.url) };
}

const ABBR = /(?:^|\s)(inkl|ca|bzw|zzgl|ggf|evtl|usw|Nr|Str|max|min|z\.B|u\.a|d\.h|Tel|Std|Mio|Mrd)\.$/i;
function splitSentences(line) {
  if (line.length <= 300) return [line];
  const out = [];
  let cur = '';
  for (const part of line.split(/(?<=[.!?])\s+(?=[A-ZÄÖÜ])/)) {
    cur = cur ? `${cur} ${part}` : part;
    if (!ABBR.test(cur)) { out.push(cur); cur = ''; }
  }
  if (cur) out.push(cur);
  return out;
}

// Satz-/Zeilen-genaue Suche (Regel-Modus ohne KI): liefert die passendsten Zeilen statt ganzer Abschnitte
function searchLines(tenant, query, k = 3) {
  const q = [...new Set(tokens(query))];
  if (!q.length) return [];
  const lines = [];
  for (const d of allChunks(tenant)) {
    for (const raw of d.content.split('\n').flatMap(splitSentences)) {
      const line = raw.trim();
      if (line.length < 12 || line.length > 600) continue;
      const lt = new Set(tokens(line));
      let s = 0;
      for (const w of q) if (lt.has(w)) s += 1 + Math.min(w.length, 10) / 10;
      if (s > 0) lines.push({ line, score: s / Math.sqrt(Math.max(4, lt.size)) });
    }
  }
  const seen = new Set();
  return lines.sort((a, b) => b.score - a.score).filter((l) => !seen.has(l.line) && seen.add(l.line)).slice(0, k).map((l) => l.line);
}

module.exports = { searchLines, addText, clear, search, crawl, allChunks, totalSize, htmlToText, tokens, chunk };
