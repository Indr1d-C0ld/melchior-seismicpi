import { state, get, h, fmtDT, num, download, post, toast, errToast } from '../lib.js';
import { waveView } from '../waveview.js';

// sismogramma di una finestra arbitraria dell'archivio grezzo (aperta dal rullo)
export default async function (root, args, params) {
  let t1 = Number(params.get('from')), t2 = Number(params.get('to'));
  const go = (a, b) => { location.hash = `#/finestra?from=${Math.floor(a)}&to=${Math.floor(b)}`; };
  const span = t2 - t1;
  const bar = h('div', { class: 'card' }, h('div', { class: 'row' },
    h('a', { class: 'btn small', href: '#/rullo' }, '← Rullo'),
    h('button', { class: 'small', onclick: () => go(t1 - span / 2, t2 - span / 2) }, '◀◀'),
    h('b', {}, `${fmtDT(t1)} → ${fmtDT(t2)}`), h('span', { class: 'muted' }, `(${num(span / 60, 1)} min)`),
    h('button', { class: 'small', onclick: () => go(t1 + span / 2, t2 + span / 2) }, '▶▶'),
    h('button', { class: 'small', onclick: () => go(t1 + span / 4, t2 - span / 4) }, 'zoom +'),
    h('button', { class: 'small', onclick: () => go(t1 - span / 2, t2 + span / 2) }, 'zoom −'),
    h('span', { class: 'spacer' }),
    h('button', { class: 'small', onclick: () => download(`/api/export/raw.mseed?from=${t1}&to=${t2}`) }, '⬇ miniSEED'),
    h('button', { class: 'small', onclick: () => download(`/api/export/raw.csv?from=${t1}&to=${t2}`) }, '⬇ CSV')));
  root.append(bar);
  let data;
  try { data = await get(`/api/waveform?from=${t1}&to=${t2}`); } catch (e) { root.append(h('div', { class: 'note bad' }, e.message)); return; }
  if (!data.e.length || data.e.every((v) => v == null)) {
    const mode = state.cfg.storage.raw_mode;
    root.append(h('div', { class: 'note warn' }, mode === 'off' ? 'L\'archivio dei dati grezzi è disattivato (Sistema → Archiviazione): restano solo il rullo e le forme d\'onda degli eventi.'
      : mode === 'ram' ? `Nessun dato grezzo per questo intervallo: in modalità RAM si conservano solo le ultime ${state.cfg.storage.raw_ram_hours} ore (e si perdono al riavvio). Il rullo resta disponibile; gli eventi hanno la loro forma d'onda salvata.`
        : 'Nessun dato grezzo per questo intervallo (fuori dal periodo di conservazione o buco nei dati).'));
    return;
  }
  const wv = waveView(data);
  root.append(h('div', { style: { marginTop: '14px' } }, wv));
  return () => wv.destroy();
}
