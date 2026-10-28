import { state, get, h, card, on, fmtAcc, fmtAgo, fmtDT, fmtTime, num, mmiBadge, classBadge, accFactor, accUnit, cssVar, fmtDur, fmtBytes, drawLoop } from '../lib.js';
import { Chart } from '../charts.js';
import { LiveBuffer } from '../livebuf.js';
import { Helicorder } from '../helicorder.js';

function stat(label, value, sub, extra) {
  return h('div', { class: 'card stat' }, h('span', { class: 'label' }, label), h('span', { class: 'value' }, value), h('span', { class: 'sub' }, sub), extra || null);
}

export default async function (root) {
  const tiles = h('div', { class: 'grid g6' });
  const buf = new LiveBuffer(60);
  const scope = new Chart({ height: 230, x: { type: 'time' }, y: { symmetric: true, label: accUnit(), minSpan: 2 / accFactor(), fmt: (v) => +(v * accFactor()).toPrecision(3) + '', tipFmt: (v) => (v * accFactor()).toFixed(3) },
    series: [{ name: 'E', color: cssVar('--e') }, { name: 'N', color: cssVar('--n') }, { name: 'Z', color: cssVar('--z') }], tooltip: true });
  const ratio = new Chart({ height: 90, x: { type: 'time', hide: true }, y: { min: 0, label: 'STA/LTA' }, legend: false,
    series: [{ name: 'STA/LTA', color: cssVar('--violet'), type: 'step' }] });
  let win = 30;
  const winSel = h('select', { onchange: () => { win = +winSel.value; } }, [10, 30, 60].map((s) => h('option', { value: s, selected: s === 30 }, `${s} s`)));
  const heli = new Helicorder({ hours: 3, lineMinutes: 10, height: 330, channel: state.cfg.web.helicorder.channel, style: state.cfg.web.helicorder.style,
    onpick: (t) => { location.hash = `#/finestra?from=${Math.floor(t - 60)}&to=${Math.floor(t + 120)}`; } });
  const evBody = h('div', { class: 'table-wrap' });
  const noise = new Chart({ height: 180, x: { type: 'time' }, y: { min: 0, label: accUnit(), fmt: (v) => +(v * accFactor()).toPrecision(3) + '' },
    series: [{ name: 'RMS 3D', color: cssVar('--accent'), unit: 'mg' }, { name: 'picco', color: cssVar('--faint'), unit: 'mg' }] });
  const catBody = h('div');

  root.append(h('div', { class: 'stack' },
    tiles,
    h('div', { class: 'grid g3' },
      h('div', { class: 'card span2' }, h('div', { class: 'card-head' }, h('h2', {}, 'Segnale in tempo reale'), h('span', { class: 'muted' }, 'finestra'), winSel,
        h('a', { href: '#/live', class: 'btn small' }, 'Analisi live →')), scope.el, ratio.el),
      card('Ultimi eventi', evBody, { actions: h('a', { href: '#/eventi', class: 'btn small' }, 'Registro →') })),
    h('div', { class: 'grid g3' },
      h('div', { class: 'card span2' }, h('div', { class: 'card-head' }, h('h2', {}, 'Rullo – ultime 3 ore'), h('a', { href: '#/rullo', class: 'btn small' }, 'Rullo completo →')), heli.el),
      h('div', { class: 'stack' }, card('Rumore di fondo – 24 ore', noise.el), card('Terremoti recenti (INGV/EMSC)', catBody, { actions: h('a', { href: '#/cataloghi', class: 'btn small' }, 'Tutti →') }))),
  ));

  function renderTiles() {
    const a = state.acq || {}, st = state.status || {}, m = a.minute || {}, sys = st.system || {}, net = st.network || {};
    const last = st.last_event;
    tiles.replaceChildren(
      stat('Sensore', a.sensor_ok ? h('span', { style: { color: 'var(--ok)' } }, a.driver === 'sim' ? 'Simulato' : 'Attivo') : h('span', { style: { color: 'var(--bad)' } }, 'Assente'),
        a.sensor_ok ? `${a.sensor?.chip || ''} · ${num(a.fs, 2)} Hz · ${num(a.temp, 1)} °C` : (a.sensor_error || '–')),
      stat('Rumore (1 min)', h('span', {}, fmtAcc(m.rms_v, false), ' ', h('small', {}, accUnit())),
        `E ${fmtAcc(m.rms_e, false)} · N ${fmtAcc(m.rms_n, false)} · Z ${fmtAcc(m.rms_z, false)}`),
      stat('Trigger', a.state && a.state !== 'idle' ? h('span', { style: { color: 'var(--bad)' } }, 'EVENTO') : h('span', {}, num(a.ratio, 2), ' ', h('small', {}, 'STA/LTA')),
        a.trigger_enabled === false ? 'rilevamento disattivato' : `attacco ${a.on_ratio} · STA ${fmtAcc(a.sta_mg)} · LTA ${fmtAcc(a.lta_mg)}`),
      stat('Ultime 24 ore', h('span', {}, st.events_24h ?? '–', ' ', h('small', {}, 'eventi')), `PGA max ${fmtAcc(st.max_pga_24h)}`),
      stat('Ultimo evento', last ? h('a', { href: `#/evento/${last.id}` }, fmtAgo(last.t_trigger)) : '–',
        last ? h('span', {}, classBadge(last.class), ' ', fmtAcc(last.pga), ' ', mmiBadge(last.mmi)) : 'nessun evento'),
      stat('Sistema', h('span', {}, num(sys.cpu_temp, 0), ' ', h('small', {}, '°C')),
        `disco ${num(sys.disk_pct, 0)}% · ${net.mode === 'ap' ? 'Access Point' : net.mode === 'client' ? (net.wifi?.ssid || 'Wi-Fi') : net.mode || '?'} · su da ${fmtDur(sys.uptime)}`),
    );
  }

  async function loadEvents() {
    const r = await get('/api/events?limit=8');
    if (!r.items.length) { evBody.replaceChildren(h('div', { class: 'empty' }, 'Nessun evento registrato finora.')); return; }
    evBody.replaceChildren(h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Ora'), h('th', {}, 'Tipo'), h('th', { class: 'num' }, 'PGA'), h('th', {}, 'MMI'))),
      h('tbody', {}, r.items.map((e) => h('tr', { class: 'click', onclick: () => { location.hash = `#/evento/${e.id}`; } },
        h('td', {}, fmtDT(e.t_trigger)), h('td', {}, classBadge(e.class)), h('td', { class: 'num' }, fmtAcc(e.pga)), h('td', {}, mmiBadge(e.mmi)))))));
  }
  async function loadNoise() {
    const now = Date.now() / 1000;
    const rows = await get(`/api/stats/minute?from=${now - 86400}&to=${now}&step=600`);
    noise.setData(rows.map((r) => r.t), [rows.map((r) => r.rms_v), rows.map((r) => r.peak_v)]);
  }
  async function loadCatalog() {
    const r = await get(`/api/catalog?from=${Date.now() / 1000 - 3 * 86400}&max_dist=${state.cfg.catalogs.radius_km}`);
    const items = r.items.slice(0, 6);
    catBody.replaceChildren(items.length ? h('table', {}, h('tbody', {}, items.map((c) => h('tr', {},
      h('td', {}, h('b', {}, `M${num(c.mag, 1)}`)), h('td', { class: 'wrap' }, c.place, h('div', { class: 'muted' }, `${fmtDT(c.t_origin, false)} · ${c.dist_km != null ? num(c.dist_km, 0) + ' km' : ''}`)),
      h('td', {}, c.local_event ? h('a', { href: `#/evento/${c.local_event}`, class: 'badge c-sisma_confermato' }, 'rilevato') : ''))))) :
      h('div', { class: 'empty' }, state.cfg.station.latitude == null ? 'Imposta la posizione della stazione per vedere i terremoti vicini.' : 'Nessun terremoto negli ultimi 3 giorni nel raggio impostato.'));
  }

  let stopLoop = null;
  const loop = () => {
    if (!buf.dirty) return; buf.dirty = false;
    const v = buf.view(win);
    scope.setData(v.t, [v.e, v.n, v.z]);
    const lo = v.t[0];
    const i0 = buf.ratioT.findIndex((t) => t >= lo);
    ratio.o.x.min = lo; ratio.o.x.max = v.t[v.t.length - 1];
    ratio.o.hlines = [{ y: state.acq?.on_ratio || 4, color: cssVar('--bad'), label: 'attacco' }];
    ratio.setData(buf.ratioT.slice(Math.max(0, i0)), [buf.ratio.slice(Math.max(0, i0))]);
  };
  stopLoop = drawLoop(loop);
  renderTiles();
  const off = on((m) => {
    if (m.t === 'status') renderTiles();
    if (m.t === 'event' && m.phase === 'end') { loadEvents(); heli.tick(); }
    if (m.t === 'catalog') loadCatalog();
  });
  const tilesTimer = setInterval(renderTiles, 5000);
  await Promise.all([loadEvents(), loadNoise(), loadCatalog(), heli.load()]).catch((e) => console.error(e));
  heli.startLive(10000);
  return () => { stopLoop(); buf.close(); off(); clearInterval(tilesTimer); heli.destroy(); scope.destroy(); ratio.destroy(); noise.destroy(); };
}
