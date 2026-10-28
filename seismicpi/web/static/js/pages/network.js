import { state, get, post, del, h, card, configForm, fmtDT, fmtAgo, toast, errToast, confirm, modal, num } from '../lib.js';

export default async function (root) {
  let info;
  try { info = await get('/api/network'); } catch (e) { root.append(h('div', { class: 'note bad' }, e.message)); return; }
  const s = info.status || {}, ad = info.adapter || {};
  const kv = (pairs) => h('div', { class: 'kv' }, pairs.flatMap(([k, v]) => [h('span', {}, k), h('span', {}, v ?? '–')]));
  const modeName = { client: 'collegato a una rete Wi-Fi', ap: 'Access Point di emergenza', offline: 'non collegato', avvio: 'avvio', sconosciuto: 'sconosciuto' };
  const status = kv([
    ['Modalità', h('b', {}, modeName[s.mode] || s.mode)], ['Da', s.since ? `${fmtDT(s.since)} (${fmtAgo(s.since)})` : null],
    ['Rete', s.wifi ? `${s.wifi.ssid} · segnale ${s.wifi.signal}% · canale ${s.wifi.channel} · ${s.wifi.rate}` : s.mode === 'ap' ? `${s.ap_ssid} (${s.ap_clients} dispositivi collegati)` : null],
    ['Indirizzi', (s.ip?.addresses || []).join(', ')], ['Gateway / DNS', `${s.ip?.gateway || '–'} / ${(s.ip?.dns || []).join(', ') || '–'}`],
    ['MAC', s.ip?.mac], ['Nome host', `${info.hostname} (raggiungibile anche come ${info.hostname}.local)`],
    ['Guardiano di rete', s.netwatch ? `attivo, aggiornato ${fmtAgo(s.updated)}` : 'non attivo (servizio seismicpi-netwatch)'],
    ['Forzatura', { auto: 'automatico', ap: 'Access Point forzato', client: 'AP disattivato' }[info.force]],
  ]);
  const adapter = kv([
    ['Interfaccia', ad.iface], ['Presente', ad.present ? 'sì' : 'NO'], ['Driver', ad.driver], ['USB', ad.usb],
    ['Modalità Access Point', ad.ap_supported == null ? 'non verificabile' : ad.ap_supported ? h('span', { style: { color: 'var(--ok)' } }, 'supportata') : h('span', { style: { color: 'var(--bad)' } }, 'NON supportata dal driver')],
    ['Modalità del driver', (ad.modes || []).join(', ')],
  ]);
  const errs = [info.error && h('div', { class: 'note bad' }, `NetworkManager: ${info.error}`), s.error && h('div', { class: 'note warn' }, s.error),
    ad.ap_supported === false && h('div', { class: 'note bad' }, 'Il driver di questo adattatore non supporta la modalità Access Point: il fallback non può funzionare. Serve un adattatore con chip compatibile (es. RTL8188EUS con driver aggiornato, MT7601U, RT5370).')].filter(Boolean);

  const savedEl = h('div');
  const renderSaved = () => {
    const nets = info.saved.filter((n) => !n.is_ap);
    savedEl.replaceChildren(nets.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Rete', 'Priorità', 'Auto', ''].map((x) => h('th', {}, x)))),
      h('tbody', {}, nets.map((n) => {
        const pr = h('input', { type: 'number', value: n.priority, style: { width: '70px' }, disabled: !state.admin });
        pr.onchange = async () => { try { await post(`/api/network/${n.uuid}/priority`, { priority: +pr.value }); toast('Priorità aggiornata', 'ok'); } catch (e) { errToast(e); } };
        return h('tr', {}, h('td', {}, h('b', {}, n.ssid || n.name), s.device?.connection === n.name ? h('span', { class: 'badge c-sisma_confermato', style: { marginLeft: '6px' } }, 'in uso') : null),
          h('td', {}, pr), h('td', {}, n.autoconnect ? 'sì' : 'no'),
          h('td', {}, state.admin ? h('div', { class: 'row' },
            h('button', { class: 'small', onclick: async () => { toast('Connessione in corso… se riesce, la dashboard sarà raggiungibile nella nuova rete.'); try { await post(`/api/network/${n.uuid}/connect`); toast('Collegato', 'ok'); } catch (e) { errToast(e); } } }, 'Collega'),
            h('button', { class: 'small danger', onclick: async () => { if (!await confirm('Dimenticare la rete?', n.ssid, 'Elimina', true)) return; await del(`/api/network/${n.uuid}`); location.reload(); } }, 'Elimina')) : null));
      }))) : h('div', { class: 'note warn' }, 'Nessuna rete salvata: al prossimo avvio partirà l\'Access Point.'));
  };
  renderSaved();
  const addDialog = async (ssid = '') => {
    const ss = h('input', { value: ssid, placeholder: 'nome rete (SSID)' }), pw = h('input', { type: 'password', placeholder: 'password (vuota se aperta)' });
    const pr = h('input', { type: 'number', value: 10 }), hid = h('input', { type: 'checkbox' });
    await modal('Aggiungi rete Wi-Fi', h('div', { class: 'form' }, h('label', { class: 'field' }, h('span', {}, 'SSID'), ss), h('label', { class: 'field' }, h('span', {}, 'Password'), pw),
      h('label', { class: 'field' }, h('span', {}, 'Priorità (più alta = preferita)'), pr), h('label', { class: 'field check' }, hid, h('span', {}, 'rete nascosta'))), [
      { label: 'Annulla', value: null },
      { label: 'Salva', class: 'primary', handler: async () => { await post('/api/network/add', { ssid: ss.value, password: pw.value, priority: +pr.value, hidden: hid.checked }); toast('Rete salvata', 'ok'); setTimeout(() => location.reload(), 500); } }]);
  };
  const scanEl = h('div');
  const scanBtn = h('button', { class: 'small', disabled: !state.admin, onclick: async () => {
    scanBtn.disabled = true; scanEl.replaceChildren(h('div', { class: 'muted' }, 'scansione in corso…'));
    try {
      const r = await post('/api/network/scan');
      scanEl.replaceChildren(h('table', {}, h('tbody', {}, r.networks.map((n) => h('tr', {}, h('td', {}, n.ssid), h('td', {}, `${n.signal}%`),
        h('td', {}, h('div', { class: 'meter', style: { width: '80px' } }, h('i', { style: { width: n.signal + '%' } }))), h('td', {}, n.security), h('td', {}, `ch ${n.channel}`),
        h('td', {}, h('button', { class: 'small', onclick: () => addDialog(n.ssid) }, 'Aggiungi')))))));
    } catch (e) { scanEl.replaceChildren(h('div', { class: 'note warn' }, e.message)); } finally { scanBtn.disabled = false; }
  } }, 'Cerca reti');
  const forceSeg = h('div', { class: 'row' }, ...[['auto', 'Automatico'], ['ap', 'Forza Access Point'], ['client', 'Disattiva Access Point']].map(([m, l]) =>
    h('button', { class: info.force === m ? 'small primary' : 'small', disabled: !state.admin, onclick: async () => {
      if (m === 'ap' && !await confirm('Forzare l\'Access Point?', `Il Pi lascerà la rete attuale e creerà la rete "${state.cfg.network.ap_ssid}". Per tornare alla dashboard collegati a quella rete e apri http://${state.cfg.network.ap_address}.`, 'Procedi')) return;
      await post('/api/network/mode', { mode: m }); toast('Impostazione applicata entro 30 secondi', 'ok'); setTimeout(() => location.reload(), 1500);
    } }, l)));
  const apFields = [
    { path: 'network.ap_ssid', label: 'Nome dell\'Access Point' },
    { path: 'network.ap_password', label: 'Password dell\'Access Point', type: 'password', help: '8-63 caratteri. Generata a caso dall\'installazione e mostrata alla fine; qui puoi sostituirla.' },
    { path: 'network.ap_channel', label: 'Canale', type: 'number', min: 1, max: 13, step: 1 },
    { path: 'network.ap_address', label: 'Indirizzo della dashboard in AP', help: 'Collegati all\'AP e apri http://questo-indirizzo' },
    { path: 'network.boot_wait_s', label: 'Attesa di una rete nota all\'avvio', type: 'number', unit: 's' },
    { path: 'network.check_interval_s', label: 'Controllo ogni', type: 'number', unit: 's' },
    { path: 'network.retry_known_every_s', label: 'In AP, riprova le reti note ogni', type: 'number', unit: 's', help: 'L\'AP si spegne per ~45 s durante il tentativo (una sola radio).' },
    { path: 'network.keep_ap_with_clients', label: 'Non interrompere l\'AP se qualcuno è collegato', type: 'check' },
    { path: 'network.interface', label: 'Interfaccia Wi-Fi' },
  ];
  root.append(h('div', { class: 'stack' }, ...errs,
    h('div', { class: 'grid g2' }, card('Stato della rete', status), card('Adattatore Wi-Fi', adapter)),
    h('div', { class: 'grid g2' },
      card('Reti Wi-Fi conosciute', h('div', { class: 'stack' }, savedEl, h('div', { class: 'row' }, h('button', { class: 'small primary', disabled: !state.admin, onclick: () => addDialog() }, '+ Aggiungi rete'), scanBtn), scanEl)),
      card('Access Point di emergenza', h('div', { class: 'stack' }, forceSeg,
        h('div', { class: 'muted' }, 'Se all\'avvio nessuna rete nota risponde entro il tempo di attesa, il Pi crea una propria rete Wi-Fi. Collegati e apri la dashboard all\'indirizzo indicato: da qui puoi aggiungere la nuova rete di casa.'),
        configForm(apFields)))),
    s.history?.length ? card('Cambi di modalità recenti', h('table', {}, h('tbody', {}, s.history.slice().reverse().map((x) => h('tr', {}, h('td', {}, fmtDT(x.t)), h('td', {}, `${x.from} → ${x.to}`), h('td', { class: 'wrap' }, x.why)))))) : null));
}
