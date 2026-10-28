import { state, get, patch, del, h, card, fmtDT, fmtMs, fmtAcc, num, mmiBadge, classBadge, toast, errToast, confirm, cssVar, download } from '../lib.js';
import { waveView } from '../waveview.js';
import { TileMap } from '../map.js';

export default async function (root, args) {
  const id = +args[0];
  let e;
  try { e = await get(`/api/events/${id}`); } catch (err) { root.append(h('div', { class: 'note bad' }, `Evento #${id}: ${err.message}`)); return; }
  const data = await get(`/api/events/${id}/waveform`);
  const st = state.cfg.station;
  const cat = e.catalog;
  const markers = [];
  if (cat) {
    markers.push({ x: cat.t_p, color: '#a855f7', label: 'P teorica', dash: [2, 3], row: 1 });
    markers.push({ x: cat.t_s, color: '#06b6d4', label: 'S teorica', dash: [2, 3], row: 1 });
  }
  const kv = (pairs) => h('div', { class: 'kv' }, pairs.flatMap(([k, v]) => [h('span', {}, k), h('span', {}, v)]));
  const params = kv([
    ['Trigger', `${fmtDT(e.t_trigger)} (${fmtMs(e.t_trigger).slice(-4)})`],
    ['Origine trigger', { stalta: 'STA/LTA', pga: 'soglia di accelerazione', manuale: 'manuale', test: 'prova' }[e.trigger_reason] || e.trigger_reason],
    ['Durata sopra soglia', `${num(e.duration, 2)} s`],
    ['Finestra registrata', `${num(e.t_end - e.t_start, 0)} s · ${e.samples} campioni a ${num(e.fs, 2)} Hz`],
    ['PGA (modulo 3D)', fmtAcc(e.pga)],
    ['PGA E / N / Z', `${fmtAcc(e.pga_e, false)} / ${fmtAcc(e.pga_n, false)} / ${fmtAcc(e.pga_z)}`],
    ['PGV max', `${num(e.pgv, 4)} cm/s (E ${num(e.pgv_e, 4)} · N ${num(e.pgv_n, 4)} · Z ${num(e.pgv_z, 4)})`],
    ['Intensità di Arias', `${num(e.arias * 1000, 4)} mm/s`],
    ['CAV', `${num(e.cav * 1000, 3)} mg·s`],
    ['Frequenza dominante', e.dom_freq ? `${num(e.dom_freq, 2)} Hz (asse ${e.dom_axis})` : '–'],
    ['Rapporto H/V picchi', num(e.hv_ratio, 2)],
    ['Picco STA/LTA', num(e.peak_ratio, 2)],
    ['Temperatura sensore', e.temp_c != null ? `${num(e.temp_c, 1)} °C` : '–'],
    ['Orologio', e.clock_synced ? 'sincronizzato (NTP)' : e.clock_synced === 0 ? 'NON sincronizzato' : '?'],
  ]);
  const intensity = h('div', { class: 'stack' },
    h('div', { class: 'row' }, h('span', { style: { transform: 'scale(1.6)', transformOrigin: 'left', display: 'inline-block', marginRight: '18px' } }, mmiBadge(e.mmi)),
      h('div', {}, h('b', {}, `Grado ${e.mmi_roman} (${num(e.mmi, 1)})`), h('div', { class: 'muted' }, e.mmi_desc))),
    h('div', { class: 'row' }, classBadge(e.class), h('span', { class: 'muted' }, `${e.class_desc} · affidabilità ${Math.round((e.confidence || 0) * 100)}%`)),
    e.extra?.why ? h('div', { class: 'muted' }, 'Motivo: ', e.extra.why) : null,
    e.extra?.auto_class ? h('div', { class: 'muted' }, `Classificazione automatica originale: ${state.meta.classes[e.extra.auto_class] || e.extra.auto_class}`) : null,
    h('div', { class: 'faint', style: { fontSize: '12px' } }, 'L\'intensità è stimata da PGA/PGV (Worden et al. 2012) nel punto in cui si trova il sensore: dipende molto dal piano e dal fissaggio.'));

  // annotazioni
  const labelSel = h('select', { disabled: !state.admin }, h('option', { value: '' }, '– nessuna –'), Object.entries(state.meta.labels).map(([k, v]) => h('option', { value: k, selected: e.label === k }, v)));
  const classSel = h('select', { disabled: !state.admin }, Object.entries(state.meta.classes).map(([k, v]) => h('option', { value: k, selected: e.class === k }, v)));
  const notes = h('textarea', { disabled: !state.admin, placeholder: 'note: cosa è successo, chi l\'ha sentito…' }, e.notes || '');
  const star = h('input', { type: 'checkbox', checked: !!e.starred, disabled: !state.admin });
  const saveNotes = async () => {
    try { await patch(`/api/events/${id}`, { label: labelSel.value || null, notes: notes.value, starred: star.checked ? 1 : 0, class: classSel.value }); toast('Evento aggiornato', 'ok'); }
    catch (err) { errToast(err); }
  };
  const annot = h('div', { class: 'stack' },
    h('label', { class: 'field' }, h('span', {}, 'Etichetta (cosa l\'ha causato)'), labelSel),
    h('label', { class: 'field' }, h('span', {}, 'Classe'), classSel),
    h('label', { class: 'field' }, h('span', {}, 'Note'), notes),
    h('label', { class: 'inline' }, star, '★ preferito (non viene eliminato dalle pulizie)'),
    state.admin ? h('div', { class: 'row end' }, h('button', { class: 'primary', onclick: saveNotes }, 'Salva')) : null);

  // catalogo
  const catBody = h('div', { class: 'stack' });
  if (cat) {
    catBody.append(kv([
      ['Agenzia', cat.source.toUpperCase()], ['Magnitudo', `${num(cat.mag, 1)} ${cat.mag_type || ''}`], ['Zona', cat.place],
      ['Origine', fmtDT(cat.t_origin)], ['Profondità', `${num(cat.depth_km, 1)} km`],
      ['Distanza / azimut', cat.dist_km != null ? `${num(cat.dist_km, 0)} km · ${num(cat.azimuth, 0)}°` : '–'],
      ['Arrivo P / S teorico', `${fmtMs(cat.t_p)} / ${fmtMs(cat.t_s)}`],
      ['Trigger − P teorica', `${num(e.t_trigger - cat.t_p, 1)} s`]]));
  } else catBody.append(h('div', { class: 'muted' }, 'Nessun terremoto di catalogo associato.'));
  if (state.admin && e.candidates?.length) {
    const s = h('select', {}, h('option', { value: '' }, '– nessuna associazione –'),
      e.candidates.map((c) => h('option', { value: c.id, selected: c.id === e.catalog_id }, `M${num(c.mag, 1)} ${c.place} (${c.dist_km != null ? num(c.dist_km, 0) + ' km, ' : ''}${fmtDT(c.t_origin)})`)));
    catBody.append(h('div', { class: 'row' }, s, h('button', { class: 'small', onclick: async () => { await patch(`/api/events/${id}`, { catalog_id: s.value || null }); location.reload(); } }, 'Associa')));
  }
  let map = null;
  if (st.latitude != null && cat) {
    map = new TileMap({ height: 260 });
    map.setMarkers([{ lat: st.latitude, lon: st.longitude, color: '#2563eb', square: true, title: 'stazione' }, { lat: cat.lat, lon: cat.lon, color: '#ef4444', size: 10 + cat.mag * 3, title: cat.place }]);
    catBody.append(map.el);
    setTimeout(() => map.fit([[st.latitude, st.longitude], [cat.lat, cat.lon]]), 50);
  }

  const wv = waveView(data, { tTrigger: e.t_trigger, markers });
  const nav = h('div', { class: 'row' },
    h('a', { class: 'btn small', href: '#/eventi' }, '← Registro'),
    e.prev_id ? h('a', { class: 'btn small', href: `#/evento/${e.prev_id}` }, '◀ precedente') : null,
    e.next_id ? h('a', { class: 'btn small', href: `#/evento/${e.next_id}` }, 'successivo ▶') : null,
    h('h2', { style: { margin: '0 0 0 8px' } }, `Evento #${id}`), classBadge(e.class), mmiBadge(e.mmi),
    h('span', { class: 'spacer' }),
    h('button', { class: 'small', onclick: () => download(`/api/events/${id}/export.mseed`) }, '⬇ miniSEED'),
    h('button', { class: 'small', onclick: () => download(`/api/events/${id}/export.csv`) }, '⬇ CSV'),
    h('button', { class: 'small', onclick: () => download(`/api/events/${id}/export.json`) }, '⬇ JSON'),
    h('a', { class: 'btn small', href: `/api/events/${id}/image.png`, target: '_blank' }, '🖼 PNG'),
    h('a', { class: 'btn small', href: `#/rullo?day=${Math.floor(e.t_trigger)}` }, 'Sul rullo'),
    state.admin ? h('button', { class: 'small danger', onclick: async () => {
      if (!await confirm('Eliminare l\'evento?', 'Evento e forma d\'onda verranno eliminati definitivamente.', 'Elimina', true)) return;
      await del(`/api/events/${id}`); location.hash = '#/eventi';
    } }, 'Elimina') : null);

  root.append(h('div', { class: 'stack' }, h('div', { class: 'card' }, nav),
    h('div', { class: 'grid g3' }, card('Intensità e classificazione', intensity), card('Parametri', params), card('Annotazioni', annot)),
    wv,
    card('Confronto con i cataloghi ufficiali', catBody)));
  return () => { wv.destroy(); map?.destroy(); };
}
