import { state, get, post, h, card, fmtDT, fmtAgo, num, toast, errToast, seg, cssVar } from '../lib.js';
import { TileMap } from '../map.js';
import { Chart } from '../charts.js';

export default async function (root) {
  const st = state.cfg.station, cc = state.cfg.catalogs;
  const opt = { days: 7, minMag: cc.min_mag, maxDist: cc.radius_km };
  const map = new TileMap({ height: 420, lat: st.latitude ?? 42, lon: st.longitude ?? 12.5, zoom: st.latitude != null ? 6 : 5 });
  const tbl = h('div', { class: 'table-wrap', style: { maxHeight: '520px' } });
  const statusEl = h('div', { class: 'muted' });
  const scatter = new Chart({ height: 260, x: { log: true, label: 'km', tipFmt: (v) => num(v, 0) + ' km' }, y: { label: 'magnitudo', min: 0 },
    series: [{ name: 'non rilevati', color: cssVar('--faint'), type: 'points' }, { name: 'rilevati da SeismicPi', color: cssVar('--bad'), type: 'points', r: 4 }] });
  async function load() {
    const r = await get(`/api/catalog?from=${Date.now() / 1000 - opt.days * 86400}&min_mag=${opt.minMag}&max_dist=${opt.maxDist}`);
    const s = r.status;
    statusEl.textContent = s ? `Ultimo aggiornamento ${fmtAgo(s.t)} · ${s.fetched} eventi ricevuti${s.errors?.length ? ' · errori: ' + s.errors.join('; ') : ''}` : 'Nessun aggiornamento ancora eseguito (serve Internet).';
    const items = r.items;
    const markers = items.map((c) => ({ lat: c.lat, lon: c.lon, size: 6 + c.mag * 3, color: c.local_event ? '#ef4444' : c.mag >= 5 ? '#f97316' : '#eab308',
      title: `M${num(c.mag, 1)} ${c.place}`, onclick: c.local_event ? () => { location.hash = `#/evento/${c.local_event}`; } : null }));
    if (st.latitude != null) markers.push({ lat: st.latitude, lon: st.longitude, color: '#2563eb', square: true, size: 14, title: 'SeismicPi' });
    map.setMarkers(markers);
    if (st.latitude != null) map.setCircles([{ lat: st.latitude, lon: st.longitude, km: opt.maxDist, color: '#60a5fa' }]);
    tbl.replaceChildren(items.length ? h('table', {}, h('thead', {}, h('tr', {}, ['Ora origine', 'M', 'Zona', 'Prof.', 'Distanza', 'Arrivo P', 'Fonte', 'SeismicPi'].map((x) => h('th', {}, x)))),
      h('tbody', {}, items.map((c) => h('tr', {},
        h('td', {}, fmtDT(c.t_origin)), h('td', {}, h('b', {}, num(c.mag, 1)), ' ', h('small', { class: 'muted' }, c.mag_type)), h('td', { class: 'wrap' }, c.place),
        h('td', { class: 'num' }, `${num(c.depth_km, 0)} km`), h('td', { class: 'num' }, c.dist_km != null ? `${num(c.dist_km, 0)} km · ${num(c.azimuth, 0)}°` : '–'),
        h('td', {}, c.t_p ? h('a', { href: `#/finestra?from=${Math.floor(c.t_p - 60)}&to=${Math.floor(c.t_s + 180)}` }, fmtDT(c.t_p)) : '–'),
        h('td', {}, c.source.toUpperCase()),
        h('td', {}, c.local_event ? h('a', { class: 'badge c-sisma_confermato', href: `#/evento/${c.local_event}` }, `#${c.local_event}`) : h('span', { class: 'faint' }, '–'))))))
      : h('div', { class: 'empty' }, 'Nessun terremoto con questi filtri.'));
    const pts = items.filter((c) => c.dist_km != null).sort((a, b) => a.dist_km - b.dist_km);
    scatter.setData(pts.map((c) => Math.max(1, c.dist_km)), [pts.map((c) => (c.local_event ? null : c.mag)), pts.map((c) => (c.local_event ? c.mag : null))]);
  }
  const minIn = h('input', { type: 'number', step: 0.5, value: opt.minMag, style: { width: '70px' }, onchange: () => { opt.minMag = +minIn.value; load(); } });
  const distIn = h('input', { type: 'number', step: 50, value: opt.maxDist, style: { width: '90px' }, onchange: () => { opt.maxDist = +distIn.value; load(); } });
  root.append(h('div', { class: 'stack' },
    st.latitude == null ? h('div', { class: 'note warn' }, 'Posizione della stazione non impostata: distanze, arrivi teorici e associazione automatica non sono disponibili. Impostala in ', h('a', { href: '#/stazione' }, 'Stazione e posizione'), '.') : null,
    h('div', { class: 'card' }, h('div', { class: 'row' },
      seg([[1, '24 h'], [7, '7 giorni'], [30, '30 giorni'], [90, '90 giorni'], [365, '1 anno']], opt.days, (v) => { opt.days = v; load(); }),
      h('span', { class: 'muted' }, 'M ≥'), minIn, h('span', { class: 'muted' }, 'entro'), distIn, h('span', { class: 'muted' }, 'km'),
      h('span', { class: 'spacer' }), statusEl,
      state.admin ? h('button', { class: 'small', onclick: async (ev) => {
        ev.target.disabled = true;
        try { const r = await post('/api/catalog/refresh'); toast(`Cataloghi aggiornati: ${r.new} nuovi, ${r.matches} associazioni${r.errors.length ? '\n' + r.errors.join('\n') : ''}`, r.errors.length ? 'warn' : 'ok'); load(); }
        catch (e) { errToast(e); } finally { ev.target.disabled = false; }
      } }, '⟳ Aggiorna ora') : null)),
    h('div', { class: 'grid g3' }, h('div', { class: 'card span2 flat' }, map.el), card('Magnitudo e distanza: cosa riesce a sentire il sensore', scatter.el)),
    card(`Terremoti dai cataloghi (${cc.sources.map((s) => state.meta.sources[s]).join(', ')})`, tbl),
    h('p', { class: 'muted' }, 'Fonti: INGV (webservices.ingv.it), EMSC (seismicportal.eu), USGS. Gli arrivi P/S sono stimati con un modello semplificato: servono a individuare la finestra in cui cercare il segnale.')));
  await load().catch(errToast);
  return () => { map.destroy(); scatter.destroy(); };
}
