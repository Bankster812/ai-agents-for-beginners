'use strict';
// Standard-Texte (deutsch, Sie-Form). Jeder Mandant kann sie in seiner Konfiguration unter "templates" überschreiben.
// Platzhalter: {{anrede}} {{vorname}} {{name}} {{firma}} {{firma_telefon}} {{link}} {{angebot}} {{betrag}} {{angebot_aktion}} {{signatur}}

const DEFAULT_TEMPLATES = {
  rueckruf: {
    sms: [
      '{{anrede}} danke für Ihren Anruf bei {{firma}}! Wir sind gerade im Einsatz und konnten nicht rangehen. Worum geht es? Antworten Sie einfach hier – oder buchen Sie direkt einen Rückruf-Termin: {{link}} {{signatur}}',
      '{{anrede}} kurze Erinnerung von {{firma}}: Sollen wir Sie zurückrufen? Antworten Sie mit einer Uhrzeit oder wählen Sie einen Termin: {{link}}'
    ],
    email: [
      { subject: 'Ihr Anruf bei {{firma}}', body: '{{anrede}}\n\nvielen Dank für Ihren Anruf. Leider konnten wir gerade nicht persönlich rangehen – wir sind beim Kunden im Einsatz.\n\nWorum geht es? Antworten Sie einfach auf diese E-Mail oder buchen Sie direkt einen Termin für unseren Rückruf:\n{{link}}\n\n{{signatur}}' },
      { subject: 'Sollen wir Sie zurückrufen?', body: '{{anrede}}\n\nwir möchten Sie nicht vergessen: Hier können Sie in 30 Sekunden einen Rückruf-Termin wählen:\n{{link}}\n\n{{signatur}}' }
    ]
  },
  bewertung: {
    sms: [
      '{{anrede}} vielen Dank, dass Sie bei {{firma}} waren! Wenn Sie zufrieden waren, würde uns eine kurze Google-Bewertung sehr helfen (dauert 1 Minute): {{link}} {{signatur}}',
      '{{anrede}} nur eine kleine Erinnerung: Ihre Meinung hilft anderen Kunden bei der Wahl. Hier geht es zur Bewertung: {{link}} Danke! {{signatur}}'
    ],
    email: [
      { subject: 'Wie zufrieden waren Sie mit {{firma}}?', body: '{{anrede}}\n\nvielen Dank für Ihr Vertrauen! Wir freuen uns sehr über eine kurze, ehrliche Bewertung bei Google – das dauert nur eine Minute und hilft anderen bei der Entscheidung:\n{{link}}\n\nHerzlichen Dank!\n{{signatur}}' },
      { subject: 'Eine Minute für {{firma}}?', body: '{{anrede}}\n\nfalls Sie noch einen Moment Zeit haben: Ihre Bewertung hilft uns sehr.\n{{link}}\n\n{{signatur}}' }
    ]
  },
  anfragen: {
    sms: [
      '{{anrede}} danke für Ihre Anfrage bei {{firma}}! Wir haben alles erhalten und melden uns persönlich. Am schnellsten geht es mit einem Termin für ein kurzes Gespräch: {{link}} {{signatur}}',
      '{{anrede}} haben Sie noch Fragen zu Ihrer Anfrage? Diese Woche sind noch Termine frei: {{link}} – oder antworten Sie einfach hier. {{signatur}}',
      '{{anrede}} wir möchten Ihre Anfrage nicht aus den Augen verlieren. Passt Ihnen ein kurzes Telefonat? Termin wählen: {{link}} {{signatur}}',
      '{{anrede}} letzte kurze Nachricht von uns: Falls das Thema noch aktuell ist, sind wir gerne für Sie da – {{link}} oder {{firma_telefon}}. {{signatur}}'
    ],
    email: [
      { subject: 'Ihre Anfrage bei {{firma}}', body: '{{anrede}}\n\nvielen Dank für Ihre Anfrage – sie ist bei uns angekommen. Damit wir Sie bestmöglich beraten können, wählen Sie gerne direkt einen Termin für ein kurzes Gespräch:\n{{link}}\n\n{{signatur}}' },
      { subject: 'Noch Fragen zu Ihrer Anfrage?', body: '{{anrede}}\n\nhaben Sie noch Fragen? Diese Woche sind noch Termine frei:\n{{link}}\n\nOder antworten Sie einfach auf diese E-Mail.\n\n{{signatur}}' },
      { subject: 'Kurzes Gespräch?', body: '{{anrede}}\n\nwir möchten Ihre Anfrage nicht aus den Augen verlieren. Passt Ihnen ein kurzes Telefonat?\n{{link}}\n\n{{signatur}}' },
      { subject: 'Ist Ihr Anliegen noch aktuell?', body: '{{anrede}}\n\nfalls Ihr Anliegen noch aktuell ist, sind wir gerne für Sie da: {{link}} oder telefonisch unter {{firma_telefon}}.\n\nDies ist unsere letzte Erinnerung.\n\n{{signatur}}' }
    ]
  },
  reaktivierung: {
    sms: [
      '{{anrede}} wir vermissen Sie bei {{firma}}! Wie läuft es bei Ihnen? Wenn Sie wieder vorbeikommen möchten: {{link}} {{angebot_aktion}} {{signatur}}',
      '{{anrede}} schon eine Weile her! {{angebot_aktion}} Termin in 30 Sekunden buchen: {{link}} {{signatur}}',
      '{{anrede}} wir würden uns freuen, Sie wiederzusehen. {{angebot_aktion}} Einfach hier buchen: {{link}} – Antworten Sie mit STOP, wenn Sie keine Nachrichten mehr möchten. {{signatur}}'
    ],
    email: [
      { subject: 'Wir vermissen Sie, {{vorname}}!', body: '{{anrede}}\n\nes ist eine Weile her, seit Sie bei uns waren – wir hoffen, es geht Ihnen gut!\n\n{{angebot_aktion}}\n\nIhren nächsten Termin können Sie hier in 30 Sekunden buchen:\n{{link}}\n\n{{signatur}}' },
      { subject: 'Ein kleines Willkommen-zurück von {{firma}}', body: '{{anrede}}\n\n{{angebot_aktion}}\n\nTermin buchen: {{link}}\n\n{{signatur}}' },
      { subject: 'Wir würden uns freuen, Sie wiederzusehen', body: '{{anrede}}\n\nwir möchten Sie nicht mit Nachrichten überhäufen – das ist unsere letzte Erinnerung. Falls Sie wiederkommen möchten:\n{{link}}\n\n{{signatur}}' }
    ]
  },
  angebote: {
    sms: [
      '{{anrede}} hier ist {{firma}}. Ich wollte kurz nachhören, ob Sie unser Angebot "{{angebot}}" erhalten haben und ob Fragen offen sind? Antworten Sie gerne direkt hier. {{signatur}}',
      '{{anrede}} haben Sie schon Gelegenheit gehabt, sich unser Angebot "{{angebot}}" anzusehen? Gerne besprechen wir es kurz telefonisch: {{link}} {{signatur}}',
      '{{anrede}} unser Angebot "{{angebot}}" ist noch für Sie reserviert. Sagen Sie uns gern Bescheid, wenn Sie starten möchten oder etwas angepasst werden soll. {{signatur}}'
    ],
    email: [
      { subject: 'Ihr Angebot: {{angebot}}', body: '{{anrede}}\n\nich wollte kurz nachhören, ob Sie unser Angebot "{{angebot}}" gut erhalten haben und ob noch Fragen offen sind. Antworten Sie gerne direkt auf diese E-Mail.\n\n{{signatur}}' },
      { subject: 'Fragen zu unserem Angebot?', body: '{{anrede}}\n\nhatten Sie schon Gelegenheit, sich unser Angebot "{{angebot}}" anzusehen? Gerne besprechen wir alles in einem kurzen Telefonat – Termin hier wählen:\n{{link}}\n\n{{signatur}}' },
      { subject: 'Ihr Angebot ist noch reserviert', body: '{{anrede}}\n\nunser Angebot "{{angebot}}" ist weiterhin für Sie reserviert. Sagen Sie uns gerne Bescheid, wenn Sie starten möchten oder etwas angepasst werden soll – wir kümmern uns darum.\n\n{{signatur}}' }
    ]
  }
};

function firstName(name) {
  if (!name) return '';
  return String(name).trim().split(/\s+/)[0];
}

function render(str, vars) {
  return String(str)
    .replace(/\{\{\s*(\w+)\s*\}\}/g, (_, k) => (vars[k] === undefined || vars[k] === null ? '' : String(vars[k])))
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/ +([.,!?])/g, '$1')
    .trim();
}

function buildVars(t, contact = {}, extra = {}) {
  const vor = firstName(contact.name);
  return {
    vorname: vor,
    name: contact.name || '',
    anrede: vor ? `Hallo ${vor},` : 'Guten Tag,',
    firma: t.name,
    firma_telefon: t.phone || '',
    signatur: t.signature || `– Ihr Team von ${t.name}`,
    ...extra
  };
}

module.exports = { DEFAULT_TEMPLATES, render, buildVars, firstName };
