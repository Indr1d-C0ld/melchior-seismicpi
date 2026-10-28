import { state, h, card, on, seg, num, accFactor, accUnit, cssVar, fmtAcc, storageGet, storageSet, post, toast, errToast } from '../lib.js';
import { Chart, drawSpectrogram } from '../charts.js';
import { LiveBuffer } from '../livebuf.js';
import * as dsp from '../dsp.js';

export default async function (root) {
  const opt = Object.assign({ win: 30, layout: 'stack', hp: 0, lp: 0, spec: 'Z', paused: false }, storageGet('live', {}));
  const save = () => storageSet('live', opt);
  const buf = new LiveBuffer(120);
  const fmt = (v) => +(v * accFactor()).toPrecision(3) + '';
  const yAx = () => ({ symmetric: true, minSpan: 2 / accFactor(), label: accUnit(), fmt, tipFmt: (v) => (v * accFactor()).toFixed(3) });
  const cols = { E: cssVar('--e'), N: cssVar('--n'), Z: cssVar('--z'), V: cssVar('--v') };
  const charts = {};
  const tracesEl = h('div', { class: 'stack' });
  function buildTraces() {
    Object.values(charts).forEach((c) => c.destroy());
    for (const k of Object.keys(charts)) delete charts[k];
    tracesEl.replaceChildren();
    if (opt.layout === 'stack') {
      for (const ch of ['E', 'N', 'Z']) {
        charts[ch] = new Chart({ height: 150, x: { type: 'time' }, y: yAx(), legend: false, series: [{ name: ch, color: cols[ch] }] });
        tracesEl.append(h('div', {}, h('div', { class: 'muted', style: { fontSize: '12px', marginBottom: '2px' } }, { E: 'Est-Ovest (E)', N: 'Nord-Sud (N)', Z: 'Verticale (Z)' }[ch]), charts[ch].el));
      }
    } else {
      charts.all = new Chart({ height: 340, x: { type: 'time' }, y: yAx(), series: ['E', 'N', 'Z'].map((c) => ({ name: c, color: cols[c] })) });
      tracesEl.append(charts.all.el);
    }
    charts.V = new Chart({ height: 120, x: { type: 'time' }, y: { min: 0, label: accUnit(), fmt }, legend: false, series: [{ name: 'modulo 3D', color: cols.V, type: 'area' }] });
    tracesEl.append(h('div', { class: 'muted', style: { fontSize: '12px' } }, 'Modulo del vettore 3D'), charts.V.el);
  }
  const ratio = new Chart({ height: 120, x: { type: 'time' }, y: { min: 0, label: 'STA/LTA' }, legend: false, series: [{ name: 'STA/LTA', color: cssVar('--violet'), type: 'step' }] });
  const spec = new Chart({ height: 220, x: { log: true, label: 'Hz', tipFmt: (v) => num(v, 2) + ' Hz' }, y: { log: true, label: accUnit() }, series: ['E', 'N', 'Z'].map((c) => ({ name: c, color: cols[c] })) });
  const sgCanvas = h('canvas', { style: { width: '100%', height: '220px', display: 'block' } });
  const pm = h('canvas', { style: { width: '100%', height: '260px', display: 'block' } });
  const stats = h('div', { class: 'kv' });
  const pauseBtn = h('button', { class: 'small', onclick: () => { opt.paused = !opt.paused; pauseBtn.textContent = opt.paused ? '▶ Riprendi' : '⏸ Pausa'; } }, '⏸ Pausa');

  const controls = h('div', { class: 'row' },
    h('span', { class: 'muted' }, 'Finestra'), seg([[10, '10 s'], [30, '30 s'], [60, '1 min'], [120, '2 min']], opt.win, (v) => { opt.win = v; save(); }),
    h('span', { class: 'muted' }, 'Tracce'), seg([['stack', 'separate'], ['overlay', 'sovrapposte']], opt.layout, (v) => { opt.layout = v; save(); buildTraces(); }),
    h('span', { class: 'muted' }, 'Filtro'), seg([['0-0', 'nessuno'], ['1-0', 'PA 1 Hz'], ['0.5-10', '0,5–10 Hz'], ['1-5', '1–5 Hz'], ['5-0', 'PA 5 Hz']], `${opt.hp}-${opt.lp}`, (v) => { [opt.hp, opt.lp] = v.split('-').map(Number); save(); }),
    h('span', { class: 'spacer' }), pauseBtn,
    state.admin ? h('button', { class: 'small', onclick: async () => { try { await post('/api/record', { seconds: 60 }); toast('Registrazione manuale di 60 s avviata', 'ok'); } catch (e) { errToast(e); } } }, '⏺ Registra 60 s') : null,
    state.admin && state.acq?.driver === 'sim' ? h('button', { class: 'small', onclick: async () => { try { const r = await post('/api/sensor/inject', { kind: 'quake' }); toast(`Terremoto sintetico: ${r.info.amp_mg} mg, S-P ${r.info.s_p} s`, 'ok'); } catch (e) { errToast(e); } } }, '🧪 Simula sisma') : null,
  );
  buildTraces();
  root.append(h('div', { class: 'stack' },
    h('div', { class: 'card' }, controls),
    h('div', { class: 'grid g3' },
      h('div', { class: 'card span2' }, h('h2', {}, 'Accelerazione (filtrata dal Pi: passa-alto ', num(state.cfg.processing.display_highpass_hz, 1), ' Hz)'), tracesEl),
      h('div', { class: 'stack' },
        card('Valori nella finestra', stats),
        card('Funzione di trigger STA/LTA', ratio.el),
        card('Moto della particella (piano orizzontale)', pm))),
    h('div', { class: 'grid g2' },
      card('Spettro di ampiezza', spec.el),
      card('Spettrogramma', h('div', {}, h('div', { class: 'row', style: { marginBottom: '6px' } }, h('span', { class: 'muted' }, 'Componente'),
        seg([['E', 'E'], ['N', 'N'], ['Z', 'Z']], opt.spec, (v) => { opt.spec = v; save(); })), sgCanvas)))));

  function particle(e, n) {
    const dpr = window.devicePixelRatio || 1, w = pm.clientWidth, H = pm.clientHeight;
    pm.width = w * dpr; pm.height = H * dpr;
    const ctx = pm.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, H);
    const m = Math.max(dsp.peak(e), dsp.peak(n), 0.5), cx = w / 2, cy = H / 2, r = Math.min(w, H) / 2 - 18;
    ctx.strokeStyle = cssVar('--grid'); ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7); ctx.moveTo(cx - r, cy); ctx.lineTo(cx + r, cy); ctx.moveTo(cx, cy - r); ctx.lineTo(cx, cy + r); ctx.stroke();
    ctx.fillStyle = cssVar('--muted'); ctx.font = '11px system-ui'; ctx.textAlign = 'center';
    ctx.fillText('N', cx, cy - r - 6); ctx.fillText('E', cx + r + 9, cy + 4); ctx.fillText(`raggio ${fmtAcc(m)}`, cx, H - 2);
    ctx.strokeStyle = cssVar('--accent'); ctx.globalAlpha = 0.75; ctx.beginPath();
    const st = Math.max(0, e.length - 1500);
    for (let i = st; i < e.length; i++) { const x = cx + e[i] / m * r, y = cy - n[i] / m * r; i === st ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
    ctx.stroke(); ctx.globalAlpha = 1;
  }

  let raf = 0, slowT = 0;
  const loop = (ts) => {
    raf = requestAnimationFrame(loop);
    if (opt.paused || !buf.dirty) return;
    buf.dirty = false;
    const v = buf.view(opt.win);
    if (!v.t.length) return;
    const f = (a) => (opt.hp || opt.lp ? dsp.filter(a, buf.fs, { hp: opt.hp, lp: opt.lp }) : a);
    const e = f(v.e), n = f(v.n), z = f(v.z), vv = e.map((x, i) => Math.hypot(x, n[i], z[i]));
    if (charts.all) charts.all.setData(v.t, [e, n, z]);
    else { charts.E.setData(v.t, [e]); charts.N.setData(v.t, [n]); charts.Z.setData(v.t, [z]); }
    charts.V.setData(v.t, [vv]);
    const i0 = buf.ratioT.findIndex((t) => t >= v.t[0]);
    ratio.o.x.min = v.t[0]; ratio.o.x.max = v.t[v.t.length - 1];
    ratio.o.hlines = [{ y: state.acq?.on_ratio || 4, color: cssVar('--bad'), label: 'attacco' }, { y: state.acq?.off_ratio || 1.6, color: cssVar('--warn'), label: 'stacco' }];
    ratio.setData(buf.ratioT.slice(Math.max(0, i0)), [buf.ratio.slice(Math.max(0, i0))]);
    if (ts - slowT > 1000) {
      slowT = ts;
      const sp = ['E', 'N', 'Z'].map((c, i) => dsp.spectrum([e, n, z][i], buf.fs, 512));
      spec.setData(sp[0].f, sp.map((s) => s.a));
      drawSpectrogram(sgCanvas, dsp.spectrogram({ E: e, N: n, Z: z }[opt.spec], buf.fs, 128, 16), { colormap: dsp.viridis, dbRange: 50 });
      particle(e, n);
      const pk = [dsp.peak(e), dsp.peak(n), dsp.peak(z)], rm = [dsp.rms(e), dsp.rms(n), dsp.rms(z)];
      const dom = sp.map((s) => s.f[s.a.indexOf(Math.max(...s.a))]);
      stats.replaceChildren(
        h('span', {}, 'Picco E / N / Z'), h('span', {}, pk.map((x) => fmtAcc(x, false)).join(' / '), ' ', accUnit()),
        h('span', {}, 'RMS E / N / Z'), h('span', {}, rm.map((x) => fmtAcc(x, false)).join(' / '), ' ', accUnit()),
        h('span', {}, 'Picco modulo 3D'), h('span', {}, fmtAcc(dsp.peak(vv))),
        h('span', {}, 'Freq. dominante'), h('span', {}, dom.map((x) => num(x, 1)).join(' / '), ' Hz'),
        h('span', {}, 'Campionamento'), h('span', {}, `${num(state.acq?.fs, 3)} Hz (nominale ${state.cfg.sensor.sample_rate})`),
        h('span', {}, 'Temperatura sensore'), h('span', {}, `${num(state.acq?.temp, 1)} °C`));
    }
  };
  raf = requestAnimationFrame(loop);
  const off = on((m) => { if (m.t === 'theme') { buildTraces(); } });
  return () => { cancelAnimationFrame(raf); buf.close(); off(); Object.values(charts).forEach((c) => c.destroy()); ratio.destroy(); spec.destroy(); };
}
