import { state, get, post, h, card, configForm, fmtDT, fmtAgo, toast, errToast, seg, patch } from '../lib.js';

export default async function (root) {
  const st = state.status?.alerts || {};
  const classes = Object.entries(state.meta.classes).filter(([k]) => !['manuale', 'test'].includes(k));
  const test = (channel, extra = {}) => h('button', { class: 'small', disabled: !state.admin, onclick: async (ev) => {
    ev.target.disabled = true;
    try { await post('/api/alerts/test', { channel, ...extra }); toast('Prova inviata', 'ok'); } catch (e) { errToast(e); } finally { ev.target.disabled = false; }
  } }, 'Invia prova');
  const tgState = h('div', { class: st.telegram_ok ? 'note ok' : st.telegram_ok === false ? 'note bad' : 'note' },
    st.telegram_ok ? `Bot collegato: @${st.telegram_bot || '?'}` : st.telegram_ok === false ? `Errore Telegram: ${st.telegram_error}` : 'Bot non attivo.');
  const chats = h('div');
  const checkBtn = h('button', { class: 'small', disabled: !state.admin, onclick: async () => {
    try {
      const r = await post('/api/alerts/telegram/check', {});
      const seen = Object.entries(r.seen_chats || {});
      chats.replaceChildren(h('div', { class: 'note ok' }, `Token valido: @${r.bot.username} (${r.bot.first_name}).`),
        seen.length ? h('table', {}, h('tbody', {}, seen.map(([id, c]) => h('tr', {}, h('td', { class: 'mono' }, id), h('td', {}, c.name), h('td', {}, fmtAgo(c.t)),
          h('td', {}, c.allowed ? h('span', { class: 'badge c-sisma_confermato' }, 'autorizzata') : h('button', { class: 'small', onclick: async () => {
            const ids = [...new Set([...(state.cfg.alerts.telegram.chat_ids || []), id])];
            const rr = await patch('/api/config', { alerts: { telegram: { chat_ids: ids } } }); state.cfg = rr.config; toast('Chat autorizzata', 'ok'); location.reload();
          } }, 'Autorizza')))))) : h('p', { class: 'muted' }, 'Nessuna chat ha ancora scritto al bot: apri Telegram, cerca il bot e premi Avvia (/start), poi ripeti la verifica.'));
    } catch (e) { errToast(e); }
  } }, 'Verifica token e chat');

  const muteBox = h('div', { class: 'row' }, h('span', { class: 'muted' }, st.muted_until > Date.now() / 1000 ? `Allerte sospese fino a ${fmtDT(st.muted_until)}` : 'Allerte attive'),
    ...[[60, '1 h'], [240, '4 h'], [720, '12 h'], [1440, '24 h'], [0, 'riattiva']].map(([m, l]) => h('button', { class: 'small', disabled: !state.admin, onclick: async () => {
      await post('/api/alerts/mute', { minutes: m }); toast(m ? `Allerte sospese per ${l}` : 'Allerte riattivate', 'ok'); setTimeout(() => location.reload(), 500);
    } }, m ? `Sospendi ${l}` : l)));

  const tgFields = [
    { path: 'alerts.telegram.enabled', label: 'Telegram attivo', type: 'check' },
    { path: 'alerts.telegram.token', label: 'Token del bot', type: 'password', wide: true, help: 'Crea un bot con @BotFather su Telegram (/newbot) e incolla qui il token.' },
    { path: 'alerts.telegram.chat_ids', label: 'Chat autorizzate (ID separati da virgola)', type: 'list', wide: true, help: 'Ricevono le allerte e possono usare i comandi. Un gruppo ha ID negativo.' },
    { path: 'alerts.telegram.commands_enabled', label: 'Comandi del bot (/stato, /grafico, /ultimi…)', type: 'check' },
    { path: 'alerts.telegram.send_image', label: 'Allega il sismogramma agli eventi', type: 'check' },
  ];
  const ruleFields = [
    { path: 'alerts.rules.classes', label: 'Classi di eventi da notificare', type: 'multi', options: classes, wide: true },
    { path: 'alerts.rules.min_mmi', label: 'Intensità minima (MMI)', type: 'number', step: 0.5, min: 1, max: 10 },
    { path: 'alerts.rules.min_pga_mg', label: 'PGA minima', type: 'number', unit: 'mg', step: 0.5 },
    { path: 'alerts.rules.max_per_hour', label: 'Massimo allerte all\'ora', type: 'number', min: 1, max: 100, step: 1, help: 'Evita raffiche di messaggi durante uno sciame o lavori in casa.' },
    { path: 'alerts.rules.notify_confirmed', label: 'Avvisa quando un evento viene confermato da INGV/EMSC', type: 'check' },
    { path: 'alerts.rules.quiet_hours_enabled', label: 'Ore di silenzio', type: 'check' },
    { path: 'alerts.rules.quiet_from', label: 'Silenzio dalle', type: 'time' },
    { path: 'alerts.rules.quiet_to', label: 'alle', type: 'time' },
    { path: 'alerts.rules.quiet_override_mmi', label: 'Ignora il silenzio da intensità', type: 'number', step: 0.5, help: 'Le scosse forti vengono notificate sempre. Da questa intensità parte anche l\'allerta immediata "scossa in corso".' },
    { section: 'Terremoti dai cataloghi (INGV/EMSC)' },
    { path: 'alerts.catalog.enabled', label: 'Allerte dai cataloghi', type: 'check' },
    { path: 'alerts.catalog.notify_regional', label: 'Avvisa per terremoti vicini anche se non rilevati', type: 'check' },
    { path: 'alerts.catalog.min_mag', label: 'Magnitudo minima', type: 'number', step: 0.1 },
    { path: 'alerts.catalog.max_radius_km', label: 'Entro', type: 'number', unit: 'km', step: 10 },
    { section: 'Sistema' },
    { path: 'alerts.system.enabled', label: 'Allerte di sistema', type: 'check' },
    { path: 'alerts.system.sensor_offline', label: 'Sensore non risponde', type: 'check' },
    { path: 'alerts.system.network_changes', label: 'Passaggio in modalità Access Point / ritorno in rete', type: 'check' },
    { path: 'alerts.system.cpu_temp_c', label: 'Temperatura CPU oltre', type: 'number', unit: '°C' },
    { path: 'alerts.system.disk_pct', label: 'Disco oltre', type: 'number', unit: '%' },
    { path: 'alerts.daily_report.enabled', label: 'Riepilogo giornaliero', type: 'check' },
    { path: 'alerts.daily_report.time', label: 'Orario del riepilogo', type: 'time' },
  ];
  const otherFields = [
    { section: 'Webhook (JSON in POST)' },
    { path: 'alerts.webhook.enabled', label: 'Webhook attivo', type: 'check' },
    { path: 'alerts.webhook.url', label: 'URL', wide: true, placeholder: 'https://…' },
    { path: 'alerts.webhook.secret', label: 'Segreto per firma HMAC-SHA256', type: 'password', help: 'Intestazione X-SeismicPi-Signature.' },
    { section: 'MQTT / Home Assistant' },
    { path: 'alerts.mqtt.enabled', label: 'MQTT attivo', type: 'check' },
    { path: 'alerts.mqtt.host', label: 'Broker' }, { path: 'alerts.mqtt.port', label: 'Porta', type: 'number' },
    { path: 'alerts.mqtt.username', label: 'Utente' }, { path: 'alerts.mqtt.password', label: 'Password', type: 'password' },
    { path: 'alerts.mqtt.topic', label: 'Topic base' },
    { path: 'alerts.mqtt.homeassistant_discovery', label: 'Autoconfigurazione Home Assistant', type: 'check' },
    { section: 'Uscita GPIO (cicalino o relè)' },
    { path: 'alerts.gpio.enabled', label: 'Uscita GPIO attiva', type: 'check' },
    { path: 'alerts.gpio.pin', label: 'GPIO (numerazione BCM)', type: 'number', min: 2, max: 27, step: 1, help: 'Default GPIO27 (pin 13). Non usare il pin del LED di stato.' },
    { path: 'alerts.gpio.active_high', label: 'Attivo alto', type: 'check' },
    { path: 'alerts.gpio.min_mmi', label: 'Da intensità', type: 'number', step: 0.5 },
    { path: 'alerts.gpio.duration_s', label: 'Durata', type: 'number', unit: 's' },
  ];
  const hist = h('div', { class: 'table-wrap', style: { maxHeight: '420px' } });
  const loadHist = async () => {
    const r = await get('/api/alerts/history');
    hist.replaceChildren(r.items.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Creato', 'Canale', 'Tipo', 'Testo', 'Stato'].map((x) => h('th', {}, x)))),
      h('tbody', {}, r.items.map((o) => h('tr', {}, h('td', {}, fmtDT(o.created)), h('td', {}, o.channel), h('td', {}, o.kind), h('td', { class: 'wrap' }, o.text.slice(0, 160)),
        h('td', {}, o.sent ? h('span', { style: { color: 'var(--ok)' } }, `inviato ${fmtAgo(o.sent)}`) : h('span', { style: { color: 'var(--warn)' }, title: o.last_error || '' }, `in coda (${o.attempts} tentativi)`))))))
      : h('div', { class: 'empty' }, 'Nessuna notifica inviata.'));
  };
  root.append(h('div', { class: 'stack' },
    h('div', { class: 'card' }, muteBox),
    h('div', { class: 'grid g2' },
      card('Telegram', h('div', { class: 'stack' }, tgState, configForm(tgFields, { extraActions: [checkBtn, test('telegram'), test('telegram', { image: true })] }), chats,
        h('details', {}, h('summary', {}, 'Comandi disponibili nel bot'), h('pre', {}, '/stato  /ultimi [n]  /evento <id>  /grafico [s]  /rumore  /oggi\n/catalogo  /registra [s]  /silenzia [min]  /riattiva  /ip  /aiuto')))),
      card('Quando avvisare', configForm(ruleFields, { extraActions: [test('report')] }))),
    h('div', { class: 'grid g2' },
      card('Altri canali', configForm(otherFields, { extraActions: [test('webhook'), test('mqtt'), test('gpio')] })),
      card('Storico notifiche', h('div', {}, hist, state.admin ? h('div', { class: 'row end', style: { marginTop: '8px' } }, h('button', { class: 'small', onclick: async () => { await post('/api/alerts/outbox/clear'); loadHist(); } }, 'Svuota coda non inviata')) : null))),
    h('p', { class: 'muted' }, 'Senza Internet (modalità Access Point) le notifiche restano in coda e partono appena il Pi torna in rete. MQTT richiede il pacchetto python3-paho-mqtt.')));
  // ---------------------------------------------------------------- LED di stato
  const ledInfo = h('div');
  const loadLed = async () => {
    try {
      const l = await get('/api/led');
      ledInfo.replaceChildren(l.available
        ? h('div', { class: 'note ok' }, `LED su GPIO${l.gpio} · ora: `, h('b', {}, l.enabled ? (l.pattern_name || '–') : 'disattivato'), l.error ? ` · errore: ${l.error}` : '')
        : h('div', { class: 'note warn' }, `LED non rilevato (${l.path}). Sul Raspberry serve l'overlay gpio-led, che install.sh aggiunge a config.txt: rilancia l'installazione e riavvia.`));
    } catch (e) { ledInfo.replaceChildren(h('div', { class: 'note bad' }, e.message)); }
  };
  const ledTest = (pat, label) => h('button', { class: 'small', disabled: !state.admin, onclick: async () => {
    try { await post('/api/led/test', { pattern: pat, seconds: 6 }); toast(`LED: ${label} per 6 secondi`, 'ok'); setTimeout(loadLed, 300); } catch (e) { errToast(e); }
  } }, label);
  const ledFields = [
    { path: 'led.enabled', label: 'LED di stato attivo', type: 'check' },
    { path: 'led.mode', label: 'Cosa mostra', type: 'select', options: [['status', 'stato del sismografo (consigliato)'], ['disk', 'attività della microSD'], ['heartbeat', 'battito del kernel'], ['on', 'sempre acceso'], ['off', 'spento']] },
    { path: 'led.alert_min_mmi', label: 'Lampeggio di allerta da intensità', type: 'number', step: 0.5, min: 1, max: 10, help: 'Funziona anche con le allerte sospese e senza Internet.' },
    { path: 'led.alert_seconds', label: 'Durata del lampeggio di allerta', type: 'number', unit: 's', min: 1, max: 600 },
  ];
  const legend = h('table', {}, h('tbody', {}, [
    ['battito lento (lampo ogni 2 s)', 'acquisizione regolare'], ['acceso fisso', 'evento in corso'], ['lampeggio lento regolare', 'avvio, calcolo del rumore di fondo'],
    ['lampeggio veloce', 'sensore assente o acquisizione ferma'], ['doppio lampo', 'modalità Access Point'], ['lampeggio rapidissimo', 'allerta forte'],
  ].map(([a, b]) => h('tr', {}, h('td', { class: 'wrap' }, a), h('td', { class: 'wrap muted' }, b)))));
  root.querySelector('.stack').append(card('LED di stato', h('div', { class: 'grid g2' },
    h('div', { class: 'stack' }, ledInfo, configForm(ledFields, { onsave: () => setTimeout(loadLed, 1500) })),
    h('div', { class: 'stack' }, h('div', { class: 'muted' }, 'Significato dei lampeggi in modalità "stato":'), legend,
      h('div', { class: 'row' }, h('span', { class: 'muted' }, 'Prova:'), ledTest('ok', 'regolare'), ledTest('event', 'evento'), ledTest('warmup', 'avvio'),
        ledTest('error', 'errore'), ledTest('ap', 'Access Point'), ledTest('alert', 'allerta'))))));
  await Promise.all([loadHist(), loadLed()]);
}
