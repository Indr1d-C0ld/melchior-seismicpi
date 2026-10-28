import { state, h, card, mmiBadge } from '../lib.js';

const WIRING = `
<svg viewBox="0 0 640 300" class="wiring" xmlns="http://www.w3.org/2000/svg" font-family="system-ui,sans-serif" font-size="12">
  <rect x="20" y="30" width="250" height="240" rx="12" fill="#14532d" stroke="#22c55e"/>
  <text x="145" y="22" text-anchor="middle" fill="currentColor" font-weight="600">Raspberry Pi Zero – header GPIO (vista dall'alto)</text>
  <g font-size="11">
    <circle cx="200" cy="60" r="9" fill="#ef4444"/><text x="190" y="64" text-anchor="end" fill="#fff">1 · 3V3</text>
    <circle cx="228" cy="60" r="9" fill="#9ca3af"/><text x="242" y="64" fill="#fff">2 · 5V</text>
    <circle cx="200" cy="88" r="9" fill="#3b82f6"/><text x="190" y="92" text-anchor="end" fill="#fff">3 · SDA (GPIO2)</text>
    <circle cx="228" cy="88" r="9" fill="#9ca3af"/><text x="242" y="92" fill="#fff">4 · 5V</text>
    <circle cx="200" cy="116" r="9" fill="#a855f7"/><text x="190" y="120" text-anchor="end" fill="#fff">5 · SCL (GPIO3)</text>
    <circle cx="228" cy="116" r="9" fill="#111827" stroke="#fff"/><text x="242" y="120" fill="#fff">6 · GND</text>
    <circle cx="200" cy="144" r="9" fill="#f59e0b"/><text x="190" y="148" text-anchor="end" fill="#fff">7 · GPIO4 (INT, facolt.)</text>
    <circle cx="228" cy="144" r="9" fill="#374151"/>
    <circle cx="200" cy="172" r="9" fill="#374151"/><circle cx="228" cy="172" r="9" fill="#374151"/>
    <circle cx="200" cy="200" r="9" fill="#22d3ee"/><text x="190" y="204" text-anchor="end" fill="#fff">11 · GPIO17 (cicalino)</text>
    <circle cx="228" cy="200" r="9" fill="#374151"/>
    <text x="145" y="250" text-anchor="middle" fill="#bbf7d0">il pin 1 è quello vicino alla microSD</text>
  </g>
  <rect x="440" y="50" width="170" height="210" rx="10" fill="#1d4ed8" stroke="#93c5fd"/>
  <text x="525" y="42" text-anchor="middle" fill="currentColor" font-weight="600">GY-521 (MPU-6050)</text>
  <g fill="#fff" font-size="11">
    <text x="470" y="78">VCC</text><text x="470" y="102">GND</text><text x="470" y="126">SCL</text><text x="470" y="150">SDA</text>
    <text x="470" y="174">XDA – libero</text><text x="470" y="198">XCL – libero</text><text x="470" y="222">AD0 – libero/GND</text><text x="470" y="246">INT</text>
  </g>
  <g fill="none" stroke-width="3">
    <path d="M209 60 C 320 60, 360 74, 456 74" stroke="#ef4444"/>
    <path d="M237 116 C 330 116, 360 98, 456 98" stroke="#111827" stroke-dasharray="0" />
    <path d="M209 116 C 320 130, 360 122, 456 122" stroke="#a855f7"/>
    <path d="M209 88 C 320 100, 360 146, 456 146" stroke="#3b82f6"/>
    <path d="M209 144 C 320 160, 360 242, 456 242" stroke="#f59e0b" stroke-dasharray="6 4"/>
  </g>
  <g fill="#e5e7eb"><circle cx="456" cy="74" r="4"/><circle cx="456" cy="98" r="4"/><circle cx="456" cy="122" r="4"/><circle cx="456" cy="146" r="4"/><circle cx="456" cy="242" r="4"/></g>
</svg>`;

export default async function (root) {
  const mmi = state.meta.mmi.map((d, i) => h('tr', {}, h('td', {}, mmiBadge(i + 1)), h('td', { class: 'wrap' }, d)));
  root.append(h('div', { class: 'stack guide' },
    h('div', { class: 'grid g2' },
      card('Collegamento del sensore', h('div', {}, h('div', { html: WIRING, style: { color: 'var(--text)' } }),
        h('ul', {},
          h('li', {}, h('b', {}, 'VCC → pin 1 (3,3 V)'), ' · ', h('b', {}, 'GND → pin 6'), ' · ', h('b', {}, 'SCL → pin 5'), ' · ', h('b', {}, 'SDA → pin 3'), '. INT → pin 7 è facoltativo (il software legge la FIFO a intervalli).'),
          h('li', {}, 'Nella foto del vecchio montaggio il connettore a 4 vie sul GY-521 sembrava spostato di un pin (SDA libero, un filo su XCL): ricontrolla che ogni filo sia sul pin giusto.'),
          h('li', {}, 'Lascia XDA/XCL scollegati. AD0 libero o a GND = indirizzo 0x68.'),
          h('li', {}, 'Verifica in Sensore → Scansione bus I2C: deve comparire 0x68.')))),
      card('Installazione meccanica (conta più del software)', h('ul', {},
        h('li', {}, 'Fissa il GY-521 rigidamente al fondo del box (viti M2 con distanziali metallici o colla epossidica): la colla a caldo è elastica e attenua le vibrazioni.'),
        h('li', {}, 'Il box va appoggiato o, meglio, fissato su un pavimento solido: piano terra o cantina, su cemento/piastrelle, lontano da lavatrici, frigoriferi, caldaie.'),
        h('li', {}, 'Metti il sensore in bolla, poi esegui Sensore → Orientamento automatico e Calibra.'),
        h('li', {}, 'Allinea un lato del box al nord (o indica la rotazione in Stazione) per interpretare il moto della particella.'),
        h('li', {}, 'Accorcia i cavi o fissali al box: un cavo che oscilla è un microfono.'),
        h('li', {}, 'Usa un alimentatore stabile da almeno 2,5 A: la sottotensione corrompe la SD e aggiunge rumore.'),
        h('li', {}, 'Opzionale: modulo RTC DS3231 sullo stesso bus I2C (pin 1, 3, 5, 6) per avere l\'ora esatta anche senza Internet; l\'installatore lo abilita con --rtc.')))),
    h('div', { class: 'grid g2' },
      card('Come leggere gli eventi', h('div', {},
        h('p', {}, h('b', {}, 'PGA'), ': picco di accelerazione del suolo (al punto del sensore). ', h('b', {}, 'PGV'), ': picco di velocità, più legato al danno. ', h('b', {}, 'Arias'), ' e ', h('b', {}, 'CAV'), ': energia complessiva dello scuotimento.'),
        h('p', {}, h('b', {}, 'Frequenza dominante'), ': i terremoti stanno tipicamente tra 0,5 e 10 Hz; passi, porte e macchinari sono più acuti (10-40 Hz).'),
        h('p', {}, h('b', {}, 'Rapporto H/V'), ': le onde S e superficiali muovono soprattutto il piano orizzontale; un urto sul pavimento è spesso verticale.'),
        h('p', {}, h('b', {}, 'S − P'), ': nell\'evento usa "Leggi P" e "Leggi S": ogni secondo di ritardo vale circa 8 km di distanza.'),
        h('p', {}, h('b', {}, 'Classificazione'), ': euristica e prudente; la conferma arriva dall\'associazione con i cataloghi INGV/EMSC. Etichetta tu gli eventi (passi, porta, traffico…): servono a capire il rumore di casa e a tarare il trigger.'))),
      card('Scala di intensità (MMI/MCS approssimata)', h('table', {}, h('tbody', {}, mmi)))),
    h('div', { class: 'grid g2' },
      card('Accesso e rete', h('ul', {},
        h('li', {}, 'In rete di casa: http://seismicpi.local (o l\'indirizzo IP mostrato in Rete).'),
        h('li', {}, `Senza reti note il Pi crea la rete "${state.cfg.network.ap_ssid}": collegati e apri http://${state.cfg.network.ap_address}.`),
        h('li', {}, 'Da lì aggiungi la nuova rete in Rete → Aggiungi rete; il Pi ci proverà da solo a intervalli regolari.'),
        h('li', {}, 'Telegram funziona solo con Internet: in modalità AP le notifiche restano in coda.'))),
      card('Problemi comuni', h('ul', {},
        h('li', {}, h('b', {}, 'Sensore assente'), ': cablaggio (vedi sopra), I2C non abilitato (raspi-config), alimentazione 3,3 V.'),
        h('li', {}, h('b', {}, 'Troppi eventi'), ': alza la soglia di attacco o la PGA minima, usa la preimpostazione "Robusto", sposta il sensore lontano dai passaggi.'),
        h('li', {}, h('b', {}, 'Nessun evento'), ': controlla il livello di rumore in Statistiche; prova "Simula sisma" solo con il driver simulato; batti un colpo vicino al box per vedere un impulso.'),
        h('li', {}, h('b', {}, 'Buchi o overflow'), ': CPU sovraccarica (riduci a 100 Hz), alimentatore debole, SD lenta.'),
        h('li', {}, h('b', {}, 'Orari sbagliati'), ': orologio non sincronizzato; imposta l\'ora dal browser o aggiungi un RTC.'))))));
}
