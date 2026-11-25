'use strict';
// Browser-Test (Playwright/Chromium): Widget Text + Voice, Buchung im Chat, Buchungsseite, Dashboard.
// Start: npm run e2e   (Screenshots landen in test/e2e/screens/)
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const ROOT = path.join(__dirname, '..', '..');
const PORT = 3222;
const BASE = `http://127.0.0.1:${PORT}`;
const SHOTS = process.env.E2E_SHOTS || path.join(__dirname, 'screens');

// Simuliertes Mikrofon + Sprachausgabe: der Test "spricht" über window.__say(text)
const FAKE_VOICE = `
  window.__spoken = [];
  window.__say = null;
  class FakeRec {
    constructor(){ this.lang='de-DE'; }
    start(){ window.__rec = this; setTimeout(()=>this.onstart && this.onstart(), 10);
      window.__say = (txt) => { const r=[{transcript:txt}]; r.isFinal=true; const res=[r];
        this.onresult && this.onresult({resultIndex:0, results:res}); setTimeout(()=>this.onend && this.onend(), 10); window.__say=null; }; }
    stop(){ setTimeout(()=>this.onend && this.onend(), 5); }
  }
  window.SpeechRecognition = FakeRec; window.webkitSpeechRecognition = FakeRec;
  const synth = { speak(u){ window.__spoken.push(u.text); setTimeout(()=>u.onend && u.onend(), 20); }, cancel(){}, getVoices(){ return [{name:'Anna', lang:'de-DE'}]; } };
  Object.defineProperty(window, 'speechSynthesis', { value: synth, configurable: true });
  window.SpeechSynthesisUtterance = function(t){ this.text=t; };
`;

async function main() {
  fs.mkdirSync(SHOTS, { recursive: true });
  const dbFile = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'inovai-e2e-')), 'e2e.db');
  const srv = spawn(process.execPath, ['--disable-warning=ExperimentalWarning', 'src/server.js'], {
    cwd: ROOT, env: { ...process.env, PORT: String(PORT), INOVAI_DB: dbFile, INOVAI_BASE_URL: BASE, ANTHROPIC_API_KEY: '', ANTHROPIC_AUTH_TOKEN: '' }, stdio: 'pipe'
  });
  let log = '';
  srv.stdout.on('data', (d) => { log += d; });
  srv.stderr.on('data', (d) => { log += d; });
  for (let i = 0; i < 50 && !log.includes('läuft'); i++) await new Promise((r) => setTimeout(r, 100));

  const browser = await chromium.launch();
  const results = [];
  const step = async (name, fn) => { await fn(); results.push(name); console.log('  ✔', name); };
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: 'de-DE' });
    await ctx.addInitScript(FAKE_VOICE);
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));

    await step('Demo-Website lädt, Widget erscheint', async () => {
      await page.goto(`${BASE}/demo/demo`);
      await page.locator('#inovai-widget .btn').waitFor();
      await page.locator('#inovai-widget .btn').click();
      await page.locator('#inovai-widget .panel.open').waitFor();
      await page.locator('#inovai-widget .m.bot', { hasText: 'digitale Assistent' }).waitFor();
    });

    await step('Textfrage wird aus Firmenwissen beantwortet', async () => {
      await page.locator('#inovai-widget textarea').fill('Was kostet eine Heizungswartung?');
      await page.keyboard.press('Enter');
      await page.locator('#inovai-widget .m.bot', { hasText: '149' }).waitFor();
    });

    await step('Termin im Chat buchen (Slots -> Formular -> Bestätigung)', async () => {
      await page.locator('#inovai-widget textarea').fill('Ich möchte einen Termin buchen');
      await page.keyboard.press('Enter');
      await page.locator('#inovai-widget .slot').first().click();
      const f = page.locator('#inovai-widget form.form');
      await f.locator('input[name=name]').fill('Erika Mustermann');
      await f.locator('input[name=phone]').fill('0171 9998887');
      await f.locator('button[type=submit]').click();
      await f.locator('.err', { hasText: 'Einwilligung' }).waitFor(); // ohne Häkchen abgelehnt
      await f.locator('input[name=consent]').check();
      await f.locator('button[type=submit]').click();
      await page.locator('#inovai-widget .m.bot', { hasText: 'ist gebucht' }).waitFor();
      await page.screenshot({ path: path.join(SHOTS, '1-chat-buchung.png') });
    });

    await step('Voice-Gespräch: Einwilligung, Begrüßung gesprochen, Frage per Sprache, Antwort vorgelesen', async () => {
      await page.locator('#inovai-widget .ib.voice').click();
      await page.locator('#inovai-widget .form', { hasText: 'Sprachfunktion aktivieren' }).waitFor();
      await page.locator('#inovai-widget .form .ok').click();
      await page.locator('#inovai-widget .vbar.show').waitFor();
      await page.waitForFunction(() => window.__spoken.join(' ').includes('Ich höre Ihnen zu'));
      await page.waitForFunction(() => typeof window.__say === 'function', null, { timeout: 5000 });
      await page.evaluate(() => window.__say('Welche Zahlungsarten habt ihr?'));
      await page.locator('#inovai-widget .m.me', { hasText: 'Zahlungsarten' }).waitFor();
      await page.locator('#inovai-widget .m.bot', { hasText: 'Überweisung' }).waitFor();
      await page.waitForFunction(() => window.__spoken.join(' ').includes('Überweisung'));
      // Gesprächsmodus hört danach automatisch weiter zu
      await page.waitForFunction(() => typeof window.__say === 'function', null, { timeout: 5000 });
      await page.screenshot({ path: path.join(SHOTS, '2-voice-modus.png') });
      await page.locator('#inovai-widget .vstop').click();
    });

    await step('Mobile-Ansicht: Widget im Vollbild', async () => {
      const m = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, locale: 'de-DE' });
      const p = await m.newPage();
      await p.goto(`${BASE}/demo/demo`);
      await p.locator('#inovai-widget .btn').click();
      const box = await p.locator('#inovai-widget .panel').boundingBox();
      assert.ok(box.width >= 389, 'Panel füllt Breite');
      await p.screenshot({ path: path.join(SHOTS, '3-mobil.png') });
      await m.close();
    });

    await step('Buchungsseite (Link aus SMS) bucht Termin', async () => {
      const p = await ctx.newPage();
      await p.goto(`${BASE}/book/demo`);
      await p.locator('.day').first().waitFor();
      await p.locator('.day').nth(1).click();
      await p.locator('.time').first().click();
      await p.fill('input[name=name]', 'Max Buchung');
      await p.fill('input[name=phone]', '0151 1231234');
      await p.check('input[name=consent]');
      await p.click('button.go');
      await p.locator('.msg.ok').waitFor();
      await p.screenshot({ path: path.join(SHOTS, '4-buchungsseite.png') });
    });

    await step('Dashboard: Login, Ereignis simulieren, Nachricht sichtbar', async () => {
      const p = await ctx.newPage();
      await p.goto(`${BASE}/admin`);
      await p.fill('#lk', 'demo-admin-bitte-aendern');
      await p.click('text=Anmelden');
      await p.locator('.kpi').first().waitFor();
      await p.click('nav a[data-t=test]');
      await p.click('text=📞 Verpasster Anruf');
      await p.locator('#out', { hasText: 'autoReply' }).waitFor();
      await p.click('text=Fällige Jobs jetzt ausführen');
      await new Promise((r) => setTimeout(r, 300));
      await p.click('nav a[data-t=appointments]');
      await p.locator('td', { hasText: 'Erika Mustermann' }).waitFor();
      await p.click('nav a[data-t=messages]');
      await p.locator('td', { hasText: 'ist bestätigt' }).first().waitFor();
      await p.click('nav a[data-t=overview]');
      await p.locator('.kpi').first().waitFor();
      await p.screenshot({ path: path.join(SHOTS, '5-dashboard.png'), fullPage: true });
    });

    assert.deepEqual(errors, [], 'keine JavaScript-Fehler auf der Seite');
    console.log(`\nE2E: ${results.length} Schritte erfolgreich. Screenshots: ${SHOTS}`);
  } catch (e) {
    console.error('E2E FEHLGESCHLAGEN:', e.message, '\nServer-Log:\n', log.slice(-2000));
    process.exitCode = 1;
  } finally {
    await browser.close();
    srv.kill();
  }
}
main();
