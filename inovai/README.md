# INOVai Automation Suite

Schlüsselfertige KI-Automationen für kleine und mittlere deutsche Unternehmen – als **ein** Server, der beliebig viele Kunden (Mandanten) bedient. Jedes Produkt lässt sich pro Kunde einzeln an- und abschalten und verkaufen.

| # | Produkt | Zielkunden | Auslöser | Was passiert |
|---|---------|-----------|----------|--------------|
| 1 | **Rückruf-Automatik** | Heizung, Sanitär, Elektro, alle Handwerker | Verpasster Anruf (Twilio, sipgate, Placetel, jede Telefonanlage per Webhook) | SMS/WhatsApp in ~20 s mit Buchungslink, KI beantwortet Rückfragen, Erinnerung nach 2 h, Inhaber wird informiert |
| 2 | **Bewertungs-Booster** | Restaurants, Friseure, Werkstätten | „Auftrag erledigt“ aus Kasse/Rechnung/CSV | Bewertungsbitte nach 2 h, 1 Erinnerung nach 3 Tagen, Klick-Tracking stoppt Erinnerung |
| 3 | **Anfragen-Nachfasser** | Zahnärzte, Kanzleien, Makler | Kontaktformular, Ads, E-Mail-Parser, Chatbot | Antwort in < 60 s, Nachfassen an Tag 1/3/7, stoppt bei Antwort oder Terminbuchung |
| 4 | **Alt-Kunden-Rückholer** | Fitnessstudios, Physio, Kosmetik | Kundenliste (CSV-Export/API), tägliche Prüfung | Persönliche Nachricht nach 30/60/90 Tagen Inaktivität, Rückgewinnung wird gemessen |
| 5 | **Angebots-Erinnerung** | Dachdecker, Maler, GaLaBau | „Angebot verschickt“ aus Handwerkersoftware/CRM | Nachfassen an Tag 2/5/10, stoppt bei Antwort, Annahme oder Ablehnung |
| + | **Website-Chatbot mit Voice** | alle | Besucher auf der Website | Beantwortet jede Frage aus Firmenwissen + gecrawlter Website, **Text und Sprache**, erfasst Leads, bucht Termine, übergibt an Menschen |

Alles in Deutsch, DSGVO-bewusst (Einwilligungen, STOP-Abmeldung, Auskunft/Löschung per Klick, Self-Hosting in der EU), mit Versandfenster (keine SMS nachts/sonntags) und vollständigem Protokoll.

---

## Schnellstart (5 Minuten)

```bash
cd inovai
npm install
npm run dev          # Demo-Modus: 1 Tag = 10 Sekunden, alle Nachrichten nur im Protokoll
```

Dann öffnen:

- **Demo-Website mit Chatbot:** http://localhost:3000/demo – unten rechts chatten oder auf „Voice“ klicken und sprechen
- **Dashboard:** http://localhost:3000/admin – Kunden-ID `demo`, Token `demo-admin-bitte-aendern`
  - Tab **Test & Demo**: verpasste Anrufe, Anfragen, Angebote usw. per Klick simulieren und zusehen, wie die Nachrichten rausgehen – ideal für Verkaufsgespräche.
- **Buchungsseite:** http://localhost:3000/book/demo

Mit KI (empfohlen): `ANTHROPIC_API_KEY=... npm run dev`. Ohne Key läuft alles im Vorlagen-/Regel-Modus weiter (Textvorlagen, Chatbot antwortet aus der Wissensbasis, Terminbuchung per Klick).

## Produktion

```bash
cp .env.example .env                      # Werte eintragen
cp config/tenants/_vorlage-produktion.json.example config/tenants/kunde-xyz.json
docker compose up -d --build              # läuft auf Port 3000, Daten in ./data
```

Davor einen Reverse-Proxy mit HTTPS (z. B. Caddy: `app.inovai.de { reverse_proxy localhost:3000 }`). Hosting in Deutschland/EU (z. B. Hetzner, IONOS) empfohlen. Backup: Ordner `data/` sichern (SQLite).

## Neuen Kunden einrichten (Onboarding-Checkliste, ca. 30–60 min)

1. `config/tenants/<kunde>.json` aus der Vorlage anlegen: Name, Branche, Adresse, Telefon, Leistungen mit Preisen, FAQ, Öffnungszeiten, `apiKey` + `adminToken` (zufällig, z. B. `openssl rand -hex 24`).
2. **Kanäle:** SMS über Twilio oder seven.io (deutscher Anbieter), WhatsApp über Twilio (Vorlagen bei Meta freigeben lassen), E-Mail über SMTP des Kunden.
3. Server neu starten → Dashboard → **Integration**: dort stehen alle fertigen Snippets und URLs für diesen Kunden.
4. Je nach gekauftem Produkt anschließen:

| Produkt | Anschluss beim Kunden |
|---|---|
| Rückruf-Automatik | Twilio: Status-Callback der Nummer auf `/hooks/twilio/<kunde>/voice` · sipgate: Push-URL `/hooks/sipgate/<kunde>?key=…` · Placetel: Notify-URL `/hooks/placetel/<kunde>?key=…` · jede andere Anlage/Fritzbox-Lösung: `POST /api/v1/<kunde>/events/missed-call` mit `{"phone":"…"}` |
| Bewertungs-Booster | Google-Bewertungslink in `tools.bewertung.reviewUrl` · Kasse/Rechnungsprogramm/Make/Zapier ruft `events/job-completed` auf – oder täglicher CSV-Upload |
| Anfragen-Nachfasser | HTML-Formular-Snippet aus dem Dashboard einfügen **oder** bestehendes Formular (WordPress/Contact Form 7, Typeform, Ads-Leads) per Webhook an `events/lead` |
| Alt-Kunden-Rückholer | CSV-Export der Kundensoftware an `import/customers` (Spalten frei benennbar: name, telefon, email, letzter_besuch, einwilligung) · neue Besuche an `events/customer-visit` |
| Angebots-Erinnerung | Angebotsprogramm/CRM (z. B. per Make) an `events/offer-sent` und `events/offer-status` |
| Chatbot | Eine Zeile vor `</body>`: `<script src="https://app.inovai.de/widget.js" data-tenant="<kunde>" async></script>` · Dashboard → Wissensbasis → **Website crawlen** |

5. Kalender: externen Kalender des Kunden (Google/Outlook/Apple, private iCal-URL) in `calendar.busyIcsUrl` → belegte Zeiten werden nie angeboten. Gebuchte Termine per ICS-Feed (`calendar.icsToken`) in jeden Kalender abonnieren.
6. Test: `INOVAI_DRY_RUN=1` setzen, im Dashboard alles durchspielen, dann scharf schalten.

### Feldnamen sind flexibel
Alle Schnittstellen verstehen gängige deutsche und englische Feldnamen ohne Mapping: `name | kunde | vorname+nachname`, `phone | telefon | handy | mobil | caller | from`, `email | e-mail | mail`, `einwilligung | consent | opt_in`, `letzter_besuch | last_visit`, `angebotsnummer | offer_id`, `summe | betrag | amount` … Telefonnummern werden automatisch ins internationale Format gebracht; Festnetznummern bekommen keine SMS (stattdessen Rückruf-Aufgabe an den Inhaber). JSON, Formulardaten und CSV werden akzeptiert, auch Listen (Massenimport).

## API-Überblick

Authentifizierung: Header `X-API-Key: <apiKey>` (oder `?key=`).

```
POST /api/v1/:kunde/events/missed-call      {phone}
POST /api/v1/:kunde/events/job-completed    {name, phone|email, consent}
POST /api/v1/:kunde/events/lead             {name, phone|email, message, service}
POST /api/v1/:kunde/events/offer-sent       {name, phone|email, offer_id, title, amount}
POST /api/v1/:kunde/events/offer-status     {offer_id, status: angenommen|abgelehnt}
POST /api/v1/:kunde/events/customer-visit   {phone|email, date}
POST /api/v1/:kunde/import/customers        CSV oder JSON-Liste
POST /api/v1/:kunde/reaktivierung/run       sofort prüfen (läuft sonst täglich automatisch)
POST /api/v1/:kunde/inbound/:kanal          eingehende Antwort (sms|whatsapp|email) {from, text}
```

Ausgehend: `notify.webhookUrl` erhält jedes Ereignis (neuer Lead, Termin gebucht, Antwort, Angebot angenommen …) als JSON, optional HMAC-signiert (`X-INOVai-Signature`) → so landen die Daten in jedem CRM.

## Wie die Produkte im Detail arbeiten

- **Sequenzen stoppen automatisch**, sobald der Kunde antwortet, einen Termin bucht, das Angebot angenommen/abgelehnt wird oder er „STOP“ schreibt.
- **Versandfenster** (Standard Mo–Sa 8–20 Uhr) – Ausnahme: Sofortantworten auf Anrufe/Anfragen, weil der Kunde gerade selbst Kontakt aufgenommen hat.
- **KI-Personalisierung**: Claude formuliert jede Vorlage passend zum Kontext um, darf aber keine Fakten, Preise oder Rabatte erfinden; Links werden geprüft – fehlt einer, wird die Original-Vorlage gesendet.
- **Antworten der Kunden** (SMS/WhatsApp/E-Mail) beantwortet die KI kurz mit Ziel Termin; der Inhaber bekommt jede Antwort weitergeleitet. Notfälle werden auf die Notfallnummer verwiesen.
- **Vorlagen** pro Kunde überschreibbar (`templates.<produkt>.sms[]` / `.email[]`), Platzhalter `{{vorname}} {{firma}} {{link}} {{angebot}} …`.

## Website-Chatbot mit Voice

- Ein Script-Tag, läuft in Shadow DOM (keine CSS-Konflikte), mobil im Vollbild, merkt sich das Gespräch beim Seitenwechsel.
- **Text-Chat** + **Spracheingabe** (Mikrofon-Taste) + **Voice-Modus** (freihändiges Gespräch: Assistent spricht, hört danach automatisch wieder zu). Nutzt die Spracherkennung/-ausgabe des Browsers (Chrome, Edge, Safari; in Firefox nur Text). Vor der ersten Nutzung wird der Besucher darüber informiert, dass der Browser-Hersteller die Sprache verarbeitet.
- **Wissen:** Firmenprofil aus der Konfiguration (Leistungen, Preise, FAQ, Öffnungszeiten, Fakten) + automatisch gecrawlte Website inkl. Sitemap und strukturierter Daten. Bei normaler Firmenwebsite kommt das **komplette** Wissen in den (gecachten) Prompt – der Bot kennt jedes Detail; bei sehr großen Websites wird pro Frage gesucht.
- **Werkzeuge der KI:** freie Termine abfragen, Termin buchen, Lead speichern (startet automatisch den Anfragen-Nachfasser), an Menschen übergeben.
- Antwortet in der Sprache des Besuchers, verweist bei Notfällen auf Notruf/Notdienst, erfindet keine Preise.
- JavaScript-API für eigene Buttons: `INOVai.open()`, `INOVai.ask("Termin buchen")`, `INOVai.voice()`.

## Rechtliches (kein Rechtsrat – mit Kunden bzw. Anwalt abstimmen)

- **Bewertungsanfragen** gelten nach BGH (VI ZR 225/17) als Werbung → standardmäßig nur mit Einwilligung (`tools.bewertung.requireConsent`). Kein „Review Gating“ (Google-Richtlinie): alle bekommen denselben Link.
- **Rückhol-Nachrichten** sind Werbung (§ 7 UWG) → nur mit Einwilligung (`tools.reaktivierung.requireConsent`).
- Jede SMS enthält „Abmelden: STOP“; Abmeldungen werden sofort und dauerhaft beachtet.
- DSGVO: Auskunft (Export) und Löschung pro Kontakt im Dashboard; AV-Verträge mit Twilio/seven.io/Anthropic/Hoster abschließen; Datenschutzerklärung des Kunden um Chatbot/SMS ergänzen.
- WhatsApp: außerhalb des 24-h-Fensters nur von Meta freigegebene Vorlagen (`channels.whatsapp.templates`).

## Tests

```bash
npm test                 # 19 End-to-End-Tests über die echte HTTP-API (alle 5 Produkte, Chatbot, Sicherheit, DSGVO)
npm run e2e              # Browser-Test (Playwright): Widget Text + Voice, Buchung, Buchungsseite, Dashboard
                         # einmalig: npm i -D playwright && npx playwright install chromium
```

## Architektur

```
src/server.js            HTTP-Server, alle Routen (keine Framework-Abhängigkeit)
src/lib/                 db (SQLite), config (Mandanten), scheduler (persistente Jobs), channels (SMS/WhatsApp/E-Mail),
                         calendar (Slots, Buchung, ICS), knowledge (Crawler + Suche), llm (Claude), templates, time
src/tools/               rueckruf, bewertung, anfragen, reaktivierung, angebote, inbound, booking
src/chatbot/agent.js     Chatbot mit Werkzeugen (Claude Tool Use) + Regel-Modus
public/                  widget.js (Chat + Voice), book.html, admin.html, demo.html
config/tenants/          eine JSON-Datei pro Kunde
```

Abhängigkeiten: nur `@anthropic-ai/sdk` und `nodemailer`. Node ≥ 22.5.
