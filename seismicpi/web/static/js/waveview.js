// Visualizzatore di forme d'onda (eventi e finestre del rullo): filtri, velocità, spettri,
// spettrogramma, moto della particella, letture P/S con stima della distanza.
import { state, h, card, seg, num, fmtAcc, fmtMs, accFactor, accUnit, cssVar, storageGet, storageSet } from './lib.js';
import { Chart, drawSpectrogram } from './charts.js';
import * as dsp from './dsp.js';

export function waveView(data, { tTrigger = null, markers = [] } = {}) {
  const opt = Object.assign({ filter: '0.5-0', qty: 'acc', comp: 'Z' }, storageGet('wave', {}));
  const cols = { E: cssVar('--e'), N: cssVar('--n'), Z: cssVar('--z') };
  const fs = data.fs, n = data.e.length;
  const t = Array.from({ length: n }, (_, i) => data.t0 + i / fs);
  const picks = { P: null, S: null };
  let pickMode = null;
  const root = h('div', { class: 'stack' });
  const info = h('span', { class: 'muted' });
  const pickInfo = h('div', { class: 'note' }, 'Lettura delle fasi: premi "Leggi P" o "Leggi S" e clicca sul sismogramma nel punto d\'arrivo. Con entrambe le letture si stima la distanza dall\'ipocentro (S−P × 8 km/s circa).');
  const charts = {};
  const sync = (view) => { for (const c of Object.values(charts)) { c.view = view; c.draw(); } };
  const mk = () => {
    const m = [...markers];
    if (tTrigger) m.push({ x: tTrigger, color: cssVar('--bad'), label: 'trigger', dash: [4, 3] });
    if (picks.P) m.push({ x: picks.P, color: '#a855f7', label: 'P', width: 2 });
    if (picks.S) m.push({ x: picks.S, color: '#06b6d4', label: 'S', width: 2 });
    return m;
  };
  const unitLbl = () => (opt.qty === 'vel' ? 'mm/s' : accUnit());
  const fmt = (v) => +(opt.qty === 'vel' ? v : v * accFactor()).toPrecision(3) + '';
  for (const c of ['E', 'N', 'Z']) {
    charts[c] = new Chart({ height: 150, x: { type: 'time', tipFmt: fmtMs }, y: { symmetric: true, fmt, label: '' }, legend: false, zoom: true, onzoom: sync,
      series: [{ name: c, color: cols[c] }], onclick: (x) => { if (pickMode) { picks[pickMode] = x; pickMode = null; redraw(); } } });
  }
  const ratio = new Chart({ height: 90, x: { type: 'time' }, y: { min: 0, label: 'STA/LTA' }, legend: false, zoom: true, onzoom: sync, series: [{ name: 'STA/LTA', color: cssVar('--violet') }] });
  charts.R = ratio;
  const spec = new Chart({ height: 220, x: { log: true, tipFmt: (v) => num(v, 2) + ' Hz' }, y: { log: true }, series: ['E', 'N', 'Z'].map((c) => ({ name: c, color: cols[c] })) });
  const sg = h('canvas', { style: { width: '100%', height: '220px', display: 'block' } });
  const pm = h('canvas', { style: { width: '100%', height: '240px', display: 'block' } });
  const peaks = h('div', { class: 'kv' });

  function series() {
    const [hp, lp] = opt.filter.split('-').map(Number);
    let out = ['e', 'n', 'z'].map((k) => dsp.filter(data[k], fs, { hp: hp || 0.05, lp, zeroPhase: true }));
    if (opt.qty === 'vel') out = out.map((a) => dsp.integrate(a, fs, Math.max(0.1, hp || 0.1)));
    return out;
  }
  function particle(e, nn, view) {
    const dpr = window.devicePixelRatio || 1, w = pm.clientWidth, H = pm.clientHeight;
    pm.width = w * dpr; pm.height = H * dpr;
    const ctx = pm.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, H);
    let i0 = 0, i1 = e.length;
    if (view) { i0 = Math.max(0, Math.floor((view[0] - data.t0) * fs)); i1 = Math.min(e.length, Math.ceil((view[1] - data.t0) * fs)); }
    const se = e.slice(i0, i1), sn = nn.slice(i0, i1);
    const m = Math.max(dsp.peak(se), dsp.peak(sn), 1e-6), cx = w / 2, cy = H / 2, r = Math.min(w, H) / 2 - 16;
    ctx.strokeStyle = cssVar('--grid'); ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7); ctx.moveTo(cx - r, cy); ctx.lineTo(cx + r, cy); ctx.moveTo(cx, cy - r); ctx.lineTo(cx, cy + r); ctx.stroke();
    ctx.fillStyle = cssVar('--muted'); ctx.font = '11px system-ui'; ctx.textAlign = 'center'; ctx.fillText('N', cx, cy - r - 4); ctx.fillText('E', cx + r + 8, cy + 4);
    ctx.lineWidth = 1;
    for (let i = 1; i < se.length; i++) {
      if (se[i] == null || sn[i] == null) continue;
      ctx.strokeStyle = `hsla(${200 + 140 * i / se.length},80%,60%,.75)`;
      ctx.beginPath(); ctx.moveTo(cx + se[i - 1] / m * r, cy - sn[i - 1] / m * r); ctx.lineTo(cx + se[i] / m * r, cy - sn[i] / m * r); ctx.stroke();
    }
    // direzione prevalente (asse principale della covarianza)
    let sxx = 0, syy = 0, sxy = 0;
    for (let i = 0; i < se.length; i++) { const a = se[i] || 0, b = sn[i] || 0; sxx += a * a; syy += b * b; sxy += a * b; }
    const ang = 0.5 * Math.atan2(2 * sxy, sxx - syy), az = (90 - ang * 180 / Math.PI + 360) % 180;
    ctx.strokeStyle = cssVar('--bad'); ctx.setLineDash([5, 4]); ctx.beginPath();
    ctx.moveTo(cx - Math.cos(ang) * r, cy + Math.sin(ang) * r); ctx.lineTo(cx + Math.cos(ang) * r, cy - Math.sin(ang) * r); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = cssVar('--muted'); ctx.fillText(`direzione prevalente ${num(az, 0)}° / ${num(az + 180, 0)}°`, cx, H - 2);
  }
  function redraw() {
    const [e, nn, z] = series();
    charts.E.setData(t, [e]); charts.N.setData(t, [nn]); charts.Z.setData(t, [z]);
    for (const c of ['E', 'N', 'Z']) { charts[c].o.y.label = unitLbl(); charts[c].setMarkers(mk()); }
    const [hp, lp] = state.cfg.processing ? [state.cfg.processing.trigger_highpass_hz, state.cfg.processing.trigger_lowpass_hz] : [1, 20];
    const tf = ['e', 'n', 'z'].map((k) => dsp.filter(data[k], fs, { hp, lp }));
    const cf = tf[0].map((v, i) => Math.hypot(v ?? 0, tf[1][i] ?? 0, tf[2][i] ?? 0));
    ratio.setData(t, [dsp.stalta(cf, fs, state.cfg.trigger.sta_s, Math.min(state.cfg.trigger.lta_s, n / fs / 3))]);
    ratio.setMarkers(mk());
    const sp = [e, nn, z].map((a) => dsp.spectrum(a, fs, 1024));
    spec.o.y.label = unitLbl(); spec.setData(sp[0].f, sp.map((s) => s.a));
    drawSpectrogram(sg, dsp.spectrogram({ E: e, N: nn, Z: z }[opt.comp], fs, 128, 16), { colormap: dsp.viridis, dbRange: 55 });
    particle(e, nn, charts.E.view);
    const pk = [e, nn, z].map(dsp.peak), dom = sp.map((s) => s.f[s.a.indexOf(Math.max(...s.a))]);
    const v = e.map((x, i) => Math.hypot(x ?? 0, nn[i] ?? 0, z[i] ?? 0));
    const u = opt.qty === 'vel' ? (x) => `${num(x, 3)} mm/s` : (x) => fmtAcc(x);
    peaks.replaceChildren(
      h('span', {}, 'Picco E'), h('span', {}, u(pk[0])), h('span', {}, 'Picco N'), h('span', {}, u(pk[1])), h('span', {}, 'Picco Z'), h('span', {}, u(pk[2])),
      h('span', {}, 'Picco 3D'), h('span', {}, u(dsp.peak(v))), h('span', {}, 'Freq. dominante E/N/Z'), h('span', {}, dom.map((x) => num(x, 2)).join(' / ') + ' Hz'),
      h('span', {}, 'Campioni'), h('span', {}, `${n} a ${num(fs, 1)} Hz (${num(n / fs, 1)} s)`));
    if (picks.P && picks.S) {
      const sp_ = picks.S - picks.P;
      pickInfo.className = 'note ok';
      pickInfo.textContent = `S − P = ${num(sp_, 2)} s → distanza ipocentrale stimata ≈ ${num(sp_ * 8, 0)} km (relazione empirica ~8 km/s per secondo di S−P, valida per eventi crostali entro qualche centinaio di km). Origine stimata ≈ ${fmtMs(picks.P - sp_ * 8 / 6.5)}.`;
    } else if (picks.P || picks.S) {
      pickInfo.className = 'note'; pickInfo.textContent = `Letta ${picks.P ? 'P alle ' + fmtMs(picks.P) : 'S alle ' + fmtMs(picks.S)}. Leggi anche l'altra fase per stimare la distanza.`;
    }
    info.textContent = `${opt.qty === 'vel' ? 'velocità (integrata)' : 'accelerazione'} · filtro ${opt.filter === '0-0' ? 'solo rimozione media' : opt.filter.replace('-0', ' Hz passa-alto').replace('-', '–') + (opt.filter.endsWith('-0') ? '' : ' Hz')} · trascina per ingrandire, doppio clic per tornare`;
  }
  const save = () => storageSet('wave', opt);
  const controls = h('div', { class: 'row' },
    h('span', { class: 'muted' }, 'Grandezza'), seg([['acc', 'accelerazione'], ['vel', 'velocità']], opt.qty, (v) => { opt.qty = v; save(); redraw(); }),
    h('span', { class: 'muted' }, 'Filtro'), seg([['0-0', 'grezzo'], ['0.5-0', 'PA 0,5 Hz'], ['1-10', '1–10 Hz'], ['0.5-5', '0,5–5 Hz'], ['2-20', '2–20 Hz'], ['5-0', 'PA 5 Hz']], opt.filter, (v) => { opt.filter = v; save(); redraw(); }),
    h('span', { class: 'spacer' }),
    h('button', { class: 'small', onclick: () => { pickMode = 'P'; info.textContent = 'clicca sull\'arrivo della P'; } }, 'Leggi P'),
    h('button', { class: 'small', onclick: () => { pickMode = 'S'; info.textContent = 'clicca sull\'arrivo della S'; } }, 'Leggi S'),
    h('button', { class: 'small ghost', onclick: () => { picks.P = picks.S = null; pickInfo.className = 'note'; redraw(); } }, 'Azzera letture'),
    h('button', { class: 'small', onclick: () => { for (const c of Object.values(charts)) c.view = null; redraw(); } }, 'Vista intera'));
  root.append(
    h('div', { class: 'card' }, controls, h('div', { style: { marginTop: '6px' } }, info),
      h('div', { class: 'stack', style: { marginTop: '10px' } }, ...['E', 'N', 'Z'].map((c) => h('div', {}, h('div', { class: 'muted', style: { fontSize: '12px' } }, c), charts[c].el)),
        h('div', {}, h('div', { class: 'muted', style: { fontSize: '12px' } }, 'STA/LTA ricalcolato (parametri attuali)'), ratio.el), pickInfo)),
    h('div', { class: 'grid g3' },
      card('Spettro di ampiezza', spec.el),
      card('Spettrogramma', h('div', {}, h('div', { class: 'row', style: { marginBottom: '6px' } }, seg([['E', 'E'], ['N', 'N'], ['Z', 'Z']], opt.comp, (v) => { opt.comp = v; save(); redraw(); })), sg)),
      card('Moto della particella (orizzontale)', h('div', {}, pm, peaks))));
  setTimeout(redraw, 0);
  root.destroy = () => { Object.values(charts).forEach((c) => c.destroy()); spec.destroy(); };
  return root;
}
