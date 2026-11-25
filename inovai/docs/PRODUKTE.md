# INOVai – Produktblätter für den Vertrieb

Preise = Vorschläge (orientiert an den Marktpreisen aus der Vorlage). Empfehlung: zusätzlich eine einmalige Einrichtungsgebühr (z. B. 490–990 €) und 3 Monate Mindestlaufzeit. SMS-/WhatsApp-Gebühren (ca. 7–9 ct/SMS) nach Verbrauch weiterberechnen oder pauschal einpreisen.

---

## 1. Rückruf-Automatik – 500 €/Monat
**Zielkunden:** Heizung, Sanitär, Elektro, Schlüsseldienst, alle Handwerker im Außeneinsatz.
**Problem:** Auf der Baustelle geht keiner ans Telefon. Wer niemanden erreicht, ruft meist direkt den nächsten Betrieb an.
**Lösung:** Jeder verpasste Anruf bekommt innerhalb von ~20 Sekunden eine freundliche SMS/WhatsApp mit Buchungslink. Fragt der Kunde zurück, antwortet die KI und vereinbart einen Termin. Der Chef bekommt alles aufs Handy.
**Beispielrechnung (Annahmen mit dem Kunden anpassen):** 5 verpasste Anrufe/Woche × 30 % zurückgewonnen × 400 € Auftragswert ≈ **2.400 €/Monat mehr Umsatz**.
**Einrichtung:** 30 min – Telefonanlage (Twilio, sipgate, Placetel oder Webhook) zeigt auf INOVai.
**Pitch:** „Sie verlieren keinen Anruf mehr – auch wenn Sie auf dem Dach stehen.“

## 2. Bewertungs-Booster – 750 €/Monat
**Zielkunden:** Restaurants, Friseure, Kosmetik, Autowerkstätten, Arztpraxen.
**Problem:** Zufriedene Kunden bewerten nicht – unzufriedene schon. 14 Bewertungen vs. Konkurrenz mit 200.
**Lösung:** Nach jedem Besuch/Auftrag geht automatisch eine persönliche Bitte um eine Google-Bewertung raus, mit einer Erinnerung. Klicks werden gemessen.
**Rechtssicher:** Nur Kunden mit Einwilligung (BGH-Urteil), kein „Review Gating“.
**Einrichtung:** 20 min – Google-Bewertungslink + Anbindung Kasse/Rechnungsprogramm oder täglicher CSV-Export.
**Pitch:** „Aus 14 Bewertungen werden in wenigen Monaten 200 – und Sie landen bei Google oben.“

## 3. Anfragen-Nachfasser – 1.500 €/Monat
**Zielkunden:** Zahnärzte (Implantate, Bleaching, Aligner), Kanzleien, Makler, Bauträger, Coaches.
**Problem:** Je später eine Anfrage beantwortet wird, desto geringer die Abschlusschance – und danach fasst oft niemand mehr nach.
**Lösung:** Antwort in < 60 Sekunden, Terminlink, bis zu 3 persönliche Nachfassungen (Tag 1, 3, 7). Stoppt sofort bei Antwort oder Buchung. Inhaber sieht jeden Lead sofort.
**Beispielrechnung:** 20 Anfragen/Monat × +3 zusätzliche Abschlüsse × 1.500 € = **4.500 €/Monat**.
**Einrichtung:** 30 min – Formular-Snippet oder Webhook vom bestehenden Formular/Ads.
**Pitch:** „Jede Anfrage wird in unter einer Minute beantwortet – auch nachts und am Wochenende.“

## 4. Alt-Kunden-Rückholer – 1.500 €/Monat
**Zielkunden:** Fitnessstudios, Physiotherapie, Kosmetik-/Nagelstudios, Friseure, Hundeschulen.
**Problem:** Hunderte frühere Kunden sind einfach weg – niemand meldet sich bei ihnen.
**Lösung:** Tägliche Prüfung der Kundenliste. Nach 30, 60 und 90 Tagen ohne Besuch geht eine persönliche Nachricht (optional mit Aktion) raus. Rückkehrer werden gemessen.
**Beispielrechnung:** 400 inaktive Kunden × 5 % Rückkehr × 60 €/Monat = **1.200 €/Monat wiederkehrend** – kumuliert jeden Monat.
**Einrichtung:** 30 min – CSV-Export aus der Studio-/Praxissoftware.
**Pitch:** „Ihre günstigsten Neukunden sind Ihre alten Kunden.“

## 5. Angebots-Erinnerung – 700 €/Monat
**Zielkunden:** Dachdecker, Maler, GaLaBau, Fensterbauer, Küchenstudios, Solarteure.
**Problem:** 10 Angebote pro Woche raus – und keiner fasst nach. Verloren geglaubte Aufträge sind oft nur vergessen.
**Lösung:** Freundliches Nachfassen an Tag 2, 5 und 10. Stoppt automatisch, wenn der Kunde antwortet, annimmt oder ablehnt.
**Beispielrechnung:** 40 Angebote/Monat × +2 zusätzliche Aufträge × 5.000 € = **10.000 €/Monat Umsatz**.
**Einrichtung:** 30 min – Anbindung Angebotsprogramm/CRM (Webhook, Make/Zapier) oder Upload.
**Pitch:** „Sie schicken das Angebot – INOVai holt den Auftrag.“

## + Website-Assistent mit Voice – Preisvorschlag 300–900 €/Monat (oder als Bundle-Bonus)
**Für alle Branchen.** Beantwortet rund um die Uhr jede Frage zu Leistungen, Preisen, Ablauf und Öffnungszeiten – per Text **und gesprochen** –, bucht Termine direkt in den Kalender, erfasst Anfragen und übergibt bei Bedarf an einen Mitarbeiter. Kennt die komplette Website (wird automatisch eingelesen).
**Einrichtung:** 15 min – eine Zeile Code auf die Website, Website einlesen lassen.
**Pitch:** „Ihr bester Mitarbeiter – kennt jedes Detail, spricht mit Ihren Kunden und macht nie Feierabend.“

---

## Bundles (Vorschlag)
| Paket | Inhalt | Preis/Monat |
|---|---|---|
| **Handwerk Komplett** | Rückruf-Automatik + Angebots-Erinnerung + Bewertungs-Booster + Website-Assistent | 1.690 € |
| **Praxis Komplett** | Anfragen-Nachfasser + Bewertungs-Booster + Alt-Kunden-Rückholer + Website-Assistent | 2.990 € |
| **Studio Komplett** | Alt-Kunden-Rückholer + Bewertungs-Booster + Website-Assistent | 1.990 € |

## Demo im Verkaufsgespräch (10 Minuten)
1. `npm run dev` → http://localhost:3000/demo öffnen, Kunden auf „🎙️ Mit dem Assistenten sprechen“ klicken lassen.
2. Dashboard → **Test & Demo** → Handynummer des Interessenten eintragen (nur mit echtem Twilio-Konto/kein DRY_RUN) → „Verpasster Anruf“ → SMS kommt auf seinem Handy an.
3. „+3 Tage vorspulen“ zeigt die Nachfass-Sequenzen; **Übersicht** zeigt die Kennzahlen.
4. Für die eigene Demo: `config/tenants/demo.json` an die Branche des Interessenten anpassen (Name, Leistungen, Preise) – der Chatbot weiß danach alles.
