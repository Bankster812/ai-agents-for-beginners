/*! INOVai Website-Assistent – Text- & Voice-Chat, Terminbuchung, Lead-Erfassung.
 *  Einbindung: <script src="https://IHR-SERVER/widget.js" data-tenant="KUNDEN-ID" async></script>
 */
(function () {
  'use strict';
  if (window.__inovaiWidget) return;
  window.__inovaiWidget = true;

  var script = document.currentScript || document.querySelector('script[data-tenant][src*="widget.js"]');
  if (!script) return;
  var TENANT = script.getAttribute('data-tenant');
  var API = (script.getAttribute('data-api') || new URL(script.src, location.href).origin).replace(/\/$/, '');
  var AUTO_OPEN = script.getAttribute('data-open') === 'true';
  var KEY = 'inovai_' + TENANT;

  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  var TTS = 'speechSynthesis' in window;

  var cfg = null, root, els = {}, busy = false, voiceMode = false, rec = null, listening = false, speaking = false, speakGen = 0, emptyListens = 0;
  var sessionId = localStorage.getItem(KEY + '_sid') || '';
  var log = [];
  try { log = JSON.parse(sessionStorage.getItem(KEY + '_log') || '[]'); } catch (e) { log = []; }

  function api(path, opts) {
    opts = opts || {};
    return fetch(API + '/api/public/' + encodeURIComponent(TENANT) + path, {
      method: opts.method || 'GET',
      headers: opts.body ? { 'Content-Type': 'application/json' } : {},
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) { return r.json().then(function (j) { if (!r.ok && !j.error) j.error = 'Fehler ' + r.status; return j; }); });
  }

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function md(s) {
    var h = esc(s);
    h = h.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
    h = h.replace(/(https?:\/\/[^\s<]+[^\s<.,)])/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
    h = h.replace(/^\s*[-•]\s+(.+)$/gm, '<li>$1</li>').replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');
    return h.replace(/\n/g, '<br>').replace(/<\/li><br>/g, '</li>');
  }

  var CSS = function (c, pos) {
    return ':host{all:initial}*{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}' +
      '.btn{position:fixed;' + pos + ':20px;bottom:20px;width:62px;height:62px;border-radius:50%;background:' + c + ';color:#fff;border:0;cursor:pointer;box-shadow:0 8px 24px rgba(0,0,0,.25);display:flex;align-items:center;justify-content:center;z-index:2147483000;transition:transform .2s}' +
      '.btn:hover{transform:scale(1.07)}.btn svg{width:30px;height:30px}' +
      '.teaser{position:fixed;' + pos + ':92px;bottom:34px;background:#fff;color:#222;padding:10px 14px;border-radius:14px;box-shadow:0 6px 20px rgba(0,0,0,.18);font-size:14px;max-width:240px;z-index:2147483000;cursor:pointer}' +
      '.panel{position:fixed;' + pos + ':20px;bottom:94px;width:380px;max-width:calc(100vw - 24px);height:600px;max-height:calc(100vh - 120px);background:#fff;border-radius:18px;box-shadow:0 18px 50px rgba(0,0,0,.28);display:none;flex-direction:column;overflow:hidden;z-index:2147483001;color:#1c1c1c}' +
      '.panel.open{display:flex}' +
      '@media(max-width:520px){.panel{' + pos + ':0;bottom:0;width:100vw;max-width:100vw;height:100%;max-height:100%;border-radius:0}}' +
      '.hd{background:' + c + ';color:#fff;padding:14px 16px;display:flex;align-items:center;gap:10px}.hd .t{flex:1;font-weight:600;font-size:15px;line-height:1.2}.hd small{display:block;font-weight:400;opacity:.85;font-size:12px}' +
      '.ib{background:rgba(255,255,255,.18);border:0;color:#fff;border-radius:10px;height:34px;min-width:34px;padding:0 8px;cursor:pointer;font-size:13px;display:flex;align-items:center;gap:5px}.ib.on{background:#fff;color:' + c + '}.ib svg{width:18px;height:18px}' +
      '.msgs{flex:1;overflow-y:auto;padding:14px;background:#f6f7f9;display:flex;flex-direction:column;gap:8px}' +
      '.m{max-width:86%;padding:9px 12px;border-radius:14px;font-size:14px;line-height:1.45;word-wrap:break-word}.m ul{margin:4px 0;padding-left:18px}.m a{color:inherit}' +
      '.m.bot{background:#fff;border:1px solid #e6e8eb;align-self:flex-start;border-bottom-left-radius:4px}.m.me{background:' + c + ';color:#fff;align-self:flex-end;border-bottom-right-radius:4px}' +
      '.m.sys{align-self:center;background:transparent;color:#667;font-size:12px;text-align:center}' +
      '.typing span{display:inline-block;width:7px;height:7px;margin:0 2px;border-radius:50%;background:#aab;animation:b 1s infinite}.typing span:nth-child(2){animation-delay:.15s}.typing span:nth-child(3){animation-delay:.3s}@keyframes b{0%,80%,100%{opacity:.3}40%{opacity:1}}' +
      '.chips{display:flex;flex-wrap:wrap;gap:6px;padding:0 14px 8px;background:#f6f7f9}.chip{border:1px solid ' + c + ';color:' + c + ';background:#fff;border-radius:16px;padding:6px 11px;font-size:13px;cursor:pointer}.chip:hover{background:' + c + ';color:#fff}' +
      '.slots{display:grid;grid-template-columns:1fr 1fr;gap:6px;align-self:stretch}.slot{border:1px solid #d5d9df;background:#fff;border-radius:10px;padding:8px 6px;font-size:12.5px;cursor:pointer;text-align:center}.slot:hover{border-color:' + c + ';color:' + c + '}' +
      '.form{background:#fff;border:1px solid #e6e8eb;border-radius:14px;padding:12px;display:flex;flex-direction:column;gap:7px;align-self:stretch}.form input,.form textarea{border:1px solid #d5d9df;border-radius:9px;padding:9px 10px;font-size:14px;width:100%}.form textarea{min-height:60px;resize:vertical}.form label{font-size:11.5px;color:#556;display:flex;gap:6px;align-items:flex-start}.form button{background:' + c + ';color:#fff;border:0;border-radius:10px;padding:10px;font-size:14px;cursor:pointer}.form .err{color:#c0392b;font-size:12px}' +
      '.ft{border-top:1px solid #e6e8eb;padding:10px;display:flex;gap:8px;align-items:flex-end;background:#fff}.ft textarea{flex:1;border:1px solid #d5d9df;border-radius:12px;padding:10px 12px;font-size:14px;resize:none;max-height:110px;min-height:42px;outline:none}.ft textarea:focus{border-color:' + c + '}' +
      '.rb{width:42px;height:42px;border-radius:12px;border:0;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0}.send{background:' + c + ';color:#fff}.mic{background:#eef0f3;color:#333}.mic.rec{background:#e03131;color:#fff;animation:p 1.2s infinite}@keyframes p{0%{box-shadow:0 0 0 0 rgba(224,49,49,.5)}70%{box-shadow:0 0 0 10px rgba(224,49,49,0)}}.rb svg{width:20px;height:20px}' +
      '.vbar{display:none;align-items:center;gap:10px;padding:8px 14px;background:#fff7e6;font-size:12.5px;color:#7a4b00;border-top:1px solid #ffe3a3}.vbar.show{display:flex}.vbar b{flex:1}' +
      '.legal{font-size:10.5px;color:#889;text-align:center;padding:4px 10px 8px;background:#fff}.legal a{color:#889}';
  };

  var ICON = {
    chat: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 3C6.5 3 2 6.6 2 11c0 2.4 1.3 4.6 3.4 6.1L4.5 21l4.3-2.3c1 .2 2.1.3 3.2.3 5.5 0 10-3.6 10-8s-4.5-8-10-8z"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    mic: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 14a3 3 0 003-3V5a3 3 0 10-6 0v6a3 3 0 003 3zm5-3a5 5 0 01-10 0H5a7 7 0 006 6.9V21h2v-3.1A7 7 0 0019 11h-2z"/></svg>',
    send: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 20l18-8L3 4v6l12 2-12 2z"/></svg>',
    wave: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3 10h2v4H3zm4-3h2v10H7zm4-4h2v18h-2zm4 4h2v10h-2zm4 3h2v4h-2z"/></svg>'
  };

  function build() {
    var host = document.createElement('div');
    host.id = 'inovai-widget';
    document.body.appendChild(host);
    root = host.attachShadow ? host.attachShadow({ mode: 'open' }) : host;
    var pos = cfg.position === 'left' ? 'left' : 'right';
    root.innerHTML = '<style>' + CSS(cfg.color || '#0f62fe', pos) + '</style>' +
      '<button class="btn" aria-label="Chat öffnen">' + ICON.chat + '</button>' +
      '<div class="teaser" hidden></div>' +
      '<div class="panel" role="dialog" aria-label="' + esc(cfg.title) + '">' +
      '<div class="hd"><div class="t">' + esc(cfg.title) + '<small>' + esc(cfg.name) + ' · antwortet sofort</small></div>' +
      (cfg.voice ? '<button class="ib voice" title="Sprachmodus: freihändig sprechen" aria-label="Sprachmodus">' + ICON.wave + '<span>Voice</span></button>' : '') +
      '<button class="ib x" aria-label="Schließen">' + ICON.close + '</button></div>' +
      '<div class="msgs" aria-live="polite"></div><div class="chips"></div>' +
      '<div class="vbar"><b>🎙️ Sprachmodus aktiv – sprechen Sie einfach.</b><button class="ib vstop" style="background:#7a4b00">Beenden</button></div>' +
      '<div class="ft"><textarea rows="1" placeholder="Nachricht schreiben …" aria-label="Nachricht"></textarea>' +
      (cfg.voice ? '<button class="rb mic" aria-label="Spracheingabe" title="Gedrückt sprechen / klicken">' + ICON.mic + '</button>' : '') +
      '<button class="rb send" aria-label="Senden">' + ICON.send + '</button></div>' +
      '<div class="legal">KI-Assistent · Antworten ohne Gewähr' + (cfg.privacyUrl ? ' · <a href="' + esc(cfg.privacyUrl) + '" target="_blank" rel="noopener">Datenschutz</a>' : '') + '</div></div>';
    ['btn', 'teaser', 'panel', 'msgs', 'chips', 'vbar'].forEach(function (k) { els[k] = root.querySelector('.' + k); });
    els.input = root.querySelector('textarea');
    els.send = root.querySelector('.send');
    els.mic = root.querySelector('.mic');
    els.voice = root.querySelector('.voice');

    els.btn.onclick = toggle;
    els.teaser.onclick = function () { els.teaser.hidden = true; open(); };
    root.querySelector('.x').onclick = close;
    els.send.onclick = function () { submit(); };
    els.input.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } });
    els.input.addEventListener('input', function () { els.input.style.height = 'auto'; els.input.style.height = Math.min(110, els.input.scrollHeight) + 'px'; });
    if (els.mic) {
      if (!SR) { els.mic.title = 'Spracheingabe wird von diesem Browser nicht unterstützt (bitte Chrome, Edge oder Safari nutzen).'; els.mic.style.opacity = '.45'; }
      els.mic.onclick = function () { if (!SR) return sys('Spracheingabe wird von diesem Browser nicht unterstützt. Bitte Chrome, Edge oder Safari verwenden – oder einfach schreiben.'); listening ? stopListening() : withVoiceConsent(function () { stopSpeaking(); startListening(false); }); };
    }
    if (els.voice) {
      if (!SR) els.voice.style.display = 'none';
      els.voice.onclick = function () { voiceMode ? endVoiceMode() : withVoiceConsent(startVoiceMode); };
      root.querySelector('.vstop').onclick = endVoiceMode;
    }

    if (log.length) log.forEach(function (e) { render(e, true); });
    else { addMsg('bot', cfg.greeting); }
    chips(cfg.quickReplies || []);

    if (AUTO_OPEN || sessionStorage.getItem(KEY + '_open') === '1') open();
    else if (!sessionStorage.getItem(KEY + '_teased')) {
      setTimeout(function () {
        if (els.panel.classList.contains('open')) return;
        els.teaser.textContent = 'Fragen? Ich helfe sofort – auch per Sprache 🎙️';
        els.teaser.hidden = false;
        sessionStorage.setItem(KEY + '_teased', '1');
      }, 6000);
    }
  }

  function save() { try { sessionStorage.setItem(KEY + '_log', JSON.stringify(log.slice(-60))); } catch (e) { /* voll */ } }
  function scroll() { els.msgs.scrollTop = els.msgs.scrollHeight; }
  function open() { els.panel.classList.add('open'); els.teaser.hidden = true; sessionStorage.setItem(KEY + '_open', '1'); setTimeout(function () { els.input.focus(); scroll(); }, 50); }
  function close() { els.panel.classList.remove('open'); sessionStorage.removeItem(KEY + '_open'); endVoiceMode(); }
  function toggle() { els.panel.classList.contains('open') ? close() : open(); }

  function render(e, restoring) {
    if (e.k === 'msg') {
      var d = document.createElement('div');
      d.className = 'm ' + e.who;
      d.innerHTML = e.who === 'me' ? esc(e.text) : md(e.text);
      els.msgs.appendChild(d);
    } else if (e.k === 'ui' && !restoring) renderUi(e.ui);
    else if (e.k === 'ui' && restoring && e.ui.type === 'booked') addMsg('sys', '✅ Termin gebucht: ' + e.ui.label, true);
    scroll();
  }
  function addMsg(who, text, noSave) {
    var e = { k: 'msg', who: who, text: text };
    if (!noSave) { log.push(e); save(); }
    render(e);
  }
  function sys(text) { var d = document.createElement('div'); d.className = 'm sys'; d.textContent = text; els.msgs.appendChild(d); scroll(); }

  function chips(list) {
    els.chips.innerHTML = '';
    (list || []).forEach(function (q) {
      var b = document.createElement('button');
      b.className = 'chip'; b.textContent = q;
      b.onclick = function () { els.chips.innerHTML = ''; submit(q); };
      els.chips.appendChild(b);
    });
  }

  function typing(on) {
    var t = els.msgs.querySelector('.typing');
    if (on && !t) { t = document.createElement('div'); t.className = 'm bot typing'; t.innerHTML = '<span></span><span></span><span></span>'; els.msgs.appendChild(t); scroll(); }
    if (!on && t) t.remove();
  }

  function submit(text, fromVoice) {
    text = (text != null ? text : els.input.value).trim();
    if (!text || busy) return;
    els.input.value = ''; els.input.style.height = 'auto';
    els.chips.innerHTML = '';
    addMsg('me', text);
    busy = true; typing(true);
    api('/chat', { method: 'POST', body: { sessionId: sessionId, message: text, voice: !!(fromVoice || voiceMode), page: location.pathname } })
      .then(function (res) {
        typing(false); busy = false;
        if (res.error) {
          var em = res.error && /Zu viele|ausgelastet/.test(res.error) ? res.error : 'Entschuldigung, gerade gibt es ein technisches Problem. ' + (cfg.phone ? 'Sie erreichen uns telefonisch unter ' + cfg.phone + '.' : '');
          addMsg('bot', em);
          if (fromVoice || voiceMode) speak(em);
          return;
        }
        if (res.sessionId) { sessionId = res.sessionId; localStorage.setItem(KEY + '_sid', sessionId); }
        addMsg('bot', res.reply);
        if (res.ui) { log.push({ k: 'ui', ui: res.ui }); save(); renderUi(res.ui); }
        if (fromVoice || voiceMode) speak(res.speech || res.reply);
      })
      .catch(function () { typing(false); busy = false; addMsg('bot', 'Verbindung fehlgeschlagen. Bitte versuchen Sie es erneut.'); if (fromVoice || voiceMode) speak('Die Verbindung ist fehlgeschlagen. Bitte versuchen Sie es noch einmal.'); });
  }

  // ---------- Interaktive Elemente: Termine, Formulare ----------
  function renderUi(ui) {
    if (!ui) return;
    if (ui.type === 'slots' && ui.slots && ui.slots.length) {
      var wrap = document.createElement('div');
      wrap.className = 'slots';
      ui.slots.slice(0, 8).forEach(function (s) {
        var b = document.createElement('button');
        b.className = 'slot'; b.textContent = s.label.replace(/^(\w+), /, '$1\n').replace(' Uhr', '');
        b.style.whiteSpace = 'pre-line';
        b.onclick = function () { wrap.remove(); bookingForm(s); };
        wrap.appendChild(b);
      });
      els.msgs.appendChild(wrap);
    } else if (ui.type === 'lead_form') leadForm();
    else if (ui.type === 'booked') sys('✅ Termin gebucht: ' + ui.label);
    scroll();
  }

  function formEl(fields, btnText, onSubmit) {
    var f = document.createElement('form');
    f.className = 'form';
    f.innerHTML = fields + '<label><input type="checkbox" name="consent" value="ja"> <span>Ich bin einverstanden, dass ' + esc(cfg.name) + ' mich zur Bearbeitung kontaktiert (SMS/E-Mail/Telefon). Widerruf jederzeit möglich.' + (cfg.privacyUrl ? ' <a href="' + esc(cfg.privacyUrl) + '" target="_blank" rel="noopener">Datenschutz</a>' : '') + '</span></label>' +
      '<div class="err" hidden></div><button type="submit">' + esc(btnText) + '</button>';
    f.onsubmit = function (ev) {
      ev.preventDefault();
      var data = {}; new FormData(f).forEach(function (v, k) { data[k] = v; });
      var err = f.querySelector('.err');
      if (!data.name || (!data.phone && !data.email)) { err.textContent = 'Bitte Name und Telefon oder E-Mail angeben.'; err.hidden = false; return; }
      if (!data.consent) { err.textContent = 'Bitte bestätigen Sie die Einwilligung zur Kontaktaufnahme.'; err.hidden = false; return; }
      f.querySelector('button').disabled = true;
      onSubmit(data, function (msg) { err.textContent = msg; err.hidden = false; f.querySelector('button').disabled = false; });
    };
    els.msgs.appendChild(f); scroll();
    var first = f.querySelector('input'); if (first) first.focus();
    return f;
  }

  function bookingForm(slot) {
    addMsg('me', 'Termin: ' + slot.label);
    var f = formEl('<b style="font-size:13px">📅 ' + esc(slot.label) + '</b><input name="name" placeholder="Ihr Name" autocomplete="name"><input name="phone" placeholder="Telefon (für Bestätigung)" autocomplete="tel"><input name="email" type="email" placeholder="E-Mail (optional)" autocomplete="email"><input name="reason" placeholder="Worum geht es? (optional)">', 'Termin verbindlich buchen', function (d, fail) {
      api('/book', { method: 'POST', body: { start: slot.start, name: d.name, phone: d.phone, email: d.email, reason: d.reason, consent: true, src: 'chat-widget' } }).then(function (r) {
        if (!r.ok) return fail(r.error || 'Buchung fehlgeschlagen');
        f.remove();
        var ui = { type: 'booked', label: r.label };
        log.push({ k: 'ui', ui: ui }); save();
        addMsg('bot', '✅ Perfekt, Ihr Termin am **' + r.label + '** ist gebucht. Sie erhalten gleich eine Bestätigung.');
        if (voiceMode) speak('Perfekt, Ihr Termin ist gebucht. Sie erhalten gleich eine Bestätigung.');
      }).catch(function () { fail('Verbindung fehlgeschlagen'); });
    });
  }

  function leadForm() {
    var f = formEl('<b style="font-size:13px">📞 Rückruf / Anfrage</b><input name="name" placeholder="Ihr Name" autocomplete="name"><input name="phone" placeholder="Telefon" autocomplete="tel"><input name="email" type="email" placeholder="E-Mail" autocomplete="email"><textarea name="message" placeholder="Ihr Anliegen"></textarea>', 'Absenden', function (d, fail) {
      api('/lead', { method: 'POST', body: { name: d.name, phone: d.phone, email: d.email, message: d.message, consent: d.consent, source: 'chat-widget' } }).then(function (r) {
        if (!r.ok) return fail(r.error || 'Senden fehlgeschlagen');
        f.remove();
        addMsg('bot', 'Danke, ' + esc(d.name.split(' ')[0]) + '! Ihre Anfrage ist bei uns angekommen – wir melden uns schnellstmöglich.');
      }).catch(function () { fail('Verbindung fehlgeschlagen'); });
    });
  }

  // ---------- Voice ----------
  function withVoiceConsent(cb) {
    if (localStorage.getItem(KEY + '_vc') === '1') return cb();
    var box = document.createElement('div');
    box.className = 'form';
    box.innerHTML = '<b style="font-size:13px">🎙️ Sprachfunktion aktivieren?</b><span style="font-size:12.5px;color:#445">Für die Spracherkennung nutzt Ihr Browser den Spracherkennungsdienst seines Herstellers (z. B. Google oder Apple). Ihre gesprochenen Worte werden dafür an diesen Dienst übertragen. Sie können jederzeit wieder schreiben.</span><button type="button" class="ok">Verstanden, Mikrofon nutzen</button><button type="button" class="no" style="background:#eef0f3;color:#333">Lieber schreiben</button>';
    box.querySelector('.ok').onclick = function () { localStorage.setItem(KEY + '_vc', '1'); box.remove(); cb(); };
    box.querySelector('.no').onclick = function () { box.remove(); els.input.focus(); };
    els.msgs.appendChild(box); scroll();
  }

  function germanVoice() {
    var vs = speechSynthesis.getVoices().filter(function (v) { return /^de(-|_|$)/i.test(v.lang); });
    var pref = vs.filter(function (v) { return /natural|neural|premium|enhanced|google|anna|petra|helena|katja/i.test(v.name); });
    return pref[0] || vs[0] || null;
  }

  function speak(text) {
    if (!TTS || !text) { if (voiceMode) startListening(true); return; }
    stopSpeaking();
    var gen = ++speakGen; // nur die neueste Ausgabe darf danach das Mikrofon öffnen
    var parts = String(text).match(/[^.!?]+[.!?]*/g) || [text];
    speaking = true;
    parts.forEach(function (p, i) {
      var u = new SpeechSynthesisUtterance(p.trim());
      u.lang = (cfg.locale || 'de-DE'); u.rate = 1.03;
      var v = germanVoice(); if (v) u.voice = v;
      if (i === parts.length - 1) u.onend = u.onerror = function () {
        if (gen !== speakGen) return;
        speaking = false;
        if (voiceMode) setTimeout(function () { if (gen === speakGen && !speaking) startListening(true); }, 250);
      };
      speechSynthesis.speak(u);
    });
  }
  function stopSpeaking() { speakGen++; if (TTS) speechSynthesis.cancel(); speaking = false; }

  function startListening(auto) {
    if (!SR || listening || speaking) return;
    rec = new SR();
    rec.lang = cfg.locale || 'de-DE';
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 1;
    var finalText = '', interimEl = null;
    rec.onstart = function () { listening = true; if (els.mic) els.mic.classList.add('rec'); els.input.placeholder = 'Ich höre zu …'; };
    rec.onresult = function (e) {
      var interim = '';
      for (var i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += e.results[i][0].transcript; else interim += e.results[i][0].transcript;
      }
      els.input.value = (finalText + ' ' + interim).trim();
      if (interimEl) interimEl.remove();
    };
    var fatal = false;
    rec.onerror = function (e) {
      if (e.error === 'no-speech' || e.error === 'aborted') return;
      fatal = true;
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') sys('Mikrofon-Zugriff wurde verweigert. Sie können die Berechtigung in den Browser-Einstellungen erteilen.');
      else if (e.error === 'audio-capture') sys('Kein Mikrofon gefunden. Bitte schreiben Sie Ihre Frage.');
      else sys('Die Spracherkennung ist gerade nicht verfügbar (' + e.error + '). Bitte schreiben Sie Ihre Frage.');
      endVoiceMode();
    };
    rec.onend = function () {
      listening = false;
      if (els.mic) els.mic.classList.remove('rec');
      els.input.placeholder = 'Nachricht schreiben …';
      var text = (finalText || els.input.value).trim();
      if (fatal) return;
      if (text) { emptyListens = 0; submit(text, true); }
      else if (voiceMode && auto) {
        // nach 3 Runden Stille den Sprachmodus beenden statt endlos weiterzuhören
        if (++emptyListens >= 3) { emptyListens = 0; sys('Sprachmodus pausiert – tippen Sie auf „Voice“, um weiterzusprechen.'); endVoiceMode(); return; }
        setTimeout(function () { if (voiceMode && !busy && !speaking) startListening(true); }, 400);
      }
    };
    try { rec.start(); } catch (e) { listening = false; }
  }
  function stopListening() { if (rec) try { rec.stop(); } catch (e) { /* schon gestoppt */ } }

  function startVoiceMode() {
    voiceMode = true;
    els.voice && els.voice.classList.add('on');
    els.vbar.classList.add('show');
    open();
    speak('Hallo! Ich höre Ihnen zu. Wie kann ich helfen?');
  }
  function endVoiceMode() {
    voiceMode = false;
    if (els.voice) els.voice.classList.remove('on');
    if (els.vbar) els.vbar.classList.remove('show');
    stopListening(); stopSpeaking();
  }

  // Öffentliche Mini-API: window.INOVai.open() / .ask("Text")
  window.INOVai = { open: function () { open(); }, close: function () { close(); }, ask: function (t) { open(); submit(t); }, voice: function () { withVoiceConsent(startVoiceMode); } };

  function init() {
    api('/config').then(function (c) {
      if (!c || c.error) return;
      cfg = c;
      if (TTS) speechSynthesis.getVoices();
      build();
    }).catch(function () { /* Server nicht erreichbar: Widget bleibt unsichtbar */ });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
