import { state, get, h, card, seg, num, fmtAcc, fmtDT, accFactor, accUnit, cssVar, classBadge, mmiBadge, labelName, fmtBytes } from '../lib.js';
import { Chart, Heatmap } from '../charts.js';

const CLASS_COL = { possibile_sisma: '#f59e0b', sisma_confermato: '#ef4444', impulso: '#60a5fa', rumore_locale: '#64748b', manuale: '#a78bfa', test: '#a78bfa', sconosciuto: '#94a3b8' };
const DAYS = ['Dom', 'Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab'];

export default async function (root) {
  const tabsEl = h('div', { class: 'tabs' });
  const body = h('div');
  const charts = [];
  const mkChart = (o) => { const c = new Chart(o); charts.push(c); return c; };
  const clearCharts = () => { charts.splice(0).forEach((c) => c.destroy()); };
  const fmtA = (v) => +(v * accFactor()).toPrecision(3) + '';
  let tab = 'eventi', range = 7;
  const show = (t) => { tab = t; [...tabsEl.children].forEach((b) => b.classList.toggle('on', b.dataset.t === t)); render(); };
  for (const [k, l] of [['eventi', 'Eventi'], ['rumore', 'Rumore e sensore'], ['inclinazione', 'Inclinazione e deriva'], ['sistema', 'Sistema e telemetria'], ['archivio', 'Archivio']]) {
    tabsEl.append(h('button', { 'data-t': k, onclick: () => show(k) }, l));
  }
  const rangeSeg = seg([[1, '24 h'], [7, '7 g'], [30, '30 g'], [90, '90 g'], [365, '1 anno']], range, (v) => { range = v; render(); });
  root.append(h('div', { class: 'row', style: { marginBottom: '6px' } }, tabsEl, h('span', { class: 'spacer' }), rangeSeg), body);

  async function render() {
    clearCharts();
    body.replaceChildren(h('div', { class: 'empty' }, 'caricamento…'));
    const now = Date.now() / 1000, t1 = now - range * 86400;
    const step = range <= 1 ? 300 : range <= 7 ? 1800 : range <= 30 ? 3600 * 3 : 86400;
    if (tab === 'eventi') {
      const s = await get(`/api/stats/events?days=${range}`);
      const days = [...new Set(s.daily.map((r) => r[0]))];
      const classes = [...new Set(s.daily.map((r) => r[1]))];
      const daily = mkChart({ height: 240, x: { labels: days }, y: { min: 0, label: 'eventi' }, stacked: true,
        series: classes.map((c) => ({ name: state.meta.classes[c] || c, color: CLASS_COL[c] || '#888', type: 'bar' })) });
      daily.setData(days.map((_, i) => i), classes.map((c) => days.map((d) => s.daily.find((r) => r[0] === d && r[1] === c)?.[2] || 0)));
      const hrs = mkChart({ height: 200, x: { labels: Array.from({ length: 24 }, (_, i) => String(i)) }, y: { min: 0 }, stacked: true,
        series: classes.map((c) => ({ name: state.meta.classes[c] || c, color: CLASS_COL[c] || '#888', type: 'bar' })) });
      hrs.setData([...Array(24).keys()], classes.map((c) => [...Array(24).keys()].map((hh) => s.hours.find((r) => r[0] === hh && r[1] === c)?.[2] || 0)));
      const labs = s.pga_bins.slice(0, -1).map((b) => fmtA(b));
      const hist = mkChart({ height: 200, x: { labels: labs }, y: { min: 0 }, series: [{ name: 'eventi', color: cssVar('--accent'), type: 'bar' }] });
      hist.setData(labs.map((_, i) => i), [s.pga_hist]);
      const cum = mkChart({ height: 220, x: { log: true, label: accUnit(), tipFmt: (v) => fmtAcc(v) }, y: { log: true, label: 'n. eventi ≥ PGA' }, legend: false,
        series: [{ name: 'eventi con PGA ≥', color: cssVar('--warn'), type: 'points' }] });
      const cs = s.pga_cumul.slice().reverse();
      cum.setData(cs.map((r) => r[0]), [cs.map((r) => r[1])]);
      const fd = mkChart({ height: 220, x: { log: true, label: 'Hz', tipFmt: (v) => num(v, 1) + ' Hz' }, y: { log: true, label: 'durata s' },
        series: Object.keys(CLASS_COL).map((c) => ({ name: state.meta.classes[c] || c, color: CLASS_COL[c], type: 'points' })) });
      const fpts = s.freq.filter((r) => r[0] > 0 && r[1] > 0).sort((a, b) => a[0] - b[0]);
      fd.setData(fpts.map((r) => r[0]), Object.keys(CLASS_COL).map((c) => fpts.map((r) => (r[3] === c ? r[1] : null))));
      const tot = s.by_class.reduce((a, r) => a + r[1], 0);
      body.replaceChildren(h('div', { class: 'stack' },
        h('div', { class: 'grid g4' },
          h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'Eventi'), h('span', { class: 'value' }, tot), h('span', { class: 'sub' }, `in ${range} giorni`)),
          h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'Possibili sismi'), h('span', { class: 'value' }, (s.by_class.find((r) => r[0] === 'possibile_sisma')?.[1] || 0) + (s.by_class.find((r) => r[0] === 'sisma_confermato')?.[1] || 0)), h('span', { class: 'sub' }, `di cui confermati ${s.by_class.find((r) => r[0] === 'sisma_confermato')?.[1] || 0}`)),
          h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'Terremoti vicini (cataloghi)'), h('span', { class: 'value' }, s.catalog_near), h('span', { class: 'sub' }, `rilevati: ${s.catalog_seen} (${s.catalog_near ? Math.round(s.catalog_seen / s.catalog_near * 100) : 0}%)`)),
          h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'PGA massima'), h('span', { class: 'value' }, s.top[0] ? fmtAcc(s.top[0].pga) : '–'), h('span', { class: 'sub' }, s.top[0] ? fmtDT(s.top[0].t_trigger) : ''))),
        card('Eventi al giorno per classe', daily.el),
        h('div', { class: 'grid g2' }, card('Distribuzione oraria (ora locale)', hrs.el), card(`Istogramma PGA (${accUnit()})`, hist.el)),
        h('div', { class: 'grid g2' }, card('Curva cumulativa delle ampiezze (tipo Gutenberg-Richter)', cum.el), card('Frequenza dominante e durata per classe', fd.el)),
        h('div', { class: 'grid g2' },
          card('Per classe', h('table', {}, h('thead', {}, h('tr', {}, ['Classe', 'n.', 'PGA media', 'PGA max', 'durata media'].map((x) => h('th', {}, x)))),
            h('tbody', {}, s.by_class.map((r) => h('tr', {}, h('td', {}, classBadge(r[0])), h('td', { class: 'num' }, r[1]), h('td', { class: 'num' }, fmtAcc(r[2])), h('td', { class: 'num' }, fmtAcc(r[3])), h('td', { class: 'num' }, num(r[4], 1) + ' s')))))),
          card('Eventi più forti', h('table', {}, h('tbody', {}, s.top.map((e) => h('tr', { class: 'click', onclick: () => { location.hash = `#/evento/${e.id}`; } },
            h('td', {}, `#${e.id}`), h('td', {}, fmtDT(e.t_trigger)), h('td', {}, classBadge(e.class)), h('td', {}, labelName(e.label)), h('td', { class: 'num' }, fmtAcc(e.pga)), h('td', {}, mmiBadge(e.mmi))))))))));
    } else if (tab === 'rumore') {
      const rows = await get(`/api/stats/minute?from=${t1}&to=${now}&step=${step}`);
      const prof = await get(`/api/stats/noise_profile?days=${Math.max(7, range)}`);
      const T = rows.map((r) => r.t);
      const yA = { min: 0, label: accUnit(), fmt: fmtA };
      const n1 = mkChart({ height: 240, x: { type: 'time' }, y: yA, zoom: true, series: [
        { name: 'RMS E', color: cssVar('--e') }, { name: 'RMS N', color: cssVar('--n') }, { name: 'RMS Z', color: cssVar('--z') }, { name: 'RMS 3D', color: cssVar('--v'), width: 2 }] });
      n1.setData(T, [rows.map((r) => r.rms_e), rows.map((r) => r.rms_n), rows.map((r) => r.rms_z), rows.map((r) => r.rms_v)]);
      const n2 = mkChart({ height: 200, x: { type: 'time' }, y: { label: accUnit(), fmt: fmtA, log: true }, zoom: true, series: [
        { name: 'RMS min', color: cssVar('--ok') }, { name: 'RMS max', color: cssVar('--warn') }, { name: 'picco', color: cssVar('--bad') }] });
      n2.setData(T, [rows.map((r) => r.rms_v_min), rows.map((r) => r.rms_v_max), rows.map((r) => r.peak_v)]);
      const sr = mkChart({ height: 180, x: { type: 'time' }, y: { label: 'Hz', tipFmt: (v) => num(v, 4) }, series: [{ name: 'frequenza misurata', color: cssVar('--accent') }] });
      sr.setData(T, [rows.map((r) => r.fs)]);
      const qual = mkChart({ height: 180, x: { type: 'time' }, y: { min: 0 }, series: [
        { name: 'buchi', color: cssVar('--warn'), type: 'bar' }, { name: 'overflow FIFO', color: cssVar('--bad'), type: 'bar' }, { name: 'errori I2C', color: cssVar('--violet'), type: 'bar' }] });
      qual.setData(T, [rows.map((r) => r.gaps), rows.map((r) => r.overflows), rows.map((r) => r.i2c_errors)]);
      const hm = new Heatmap({ rows: DAYS, cols: Array.from({ length: 24 }, (_, i) => String(i)), height: 220, fmt: (v) => fmtAcc(v) });
      hm.setData(prof.matrix);
      const hourly = mkChart({ height: 200, x: { labels: Array.from({ length: 24 }, (_, i) => `${i}`) }, y: { min: 0, fmt: fmtA, label: accUnit() }, series: [
        { name: 'media', color: cssVar('--accent') }, { name: 'minimo', color: cssVar('--ok') }, { name: 'massimo', color: cssVar('--warn') }] });
      hourly.setData(prof.hourly.map((r) => r[0]), [prof.hourly.map((r) => r[1]), prof.hourly.map((r) => r[2]), prof.hourly.map((r) => r[3])]);
      const vals = rows.map((r) => r.rms_v).filter((v) => v != null).sort((a, b) => a - b);
      const pct = (p) => vals.length ? vals[Math.floor(p * (vals.length - 1))] : null;
      body.replaceChildren(h('div', { class: 'stack' },
        h('div', { class: 'grid g4' }, ...[['5° percentile (notte quieta)', pct(0.05)], ['Mediana', pct(0.5)], ['95° percentile', pct(0.95)], ['Soglia minima rilevabile ≈', pct(0.5) ? pct(0.5) * state.cfg.trigger.on_ratio ** 0.5 : null]]
          .map(([l, v]) => h('div', { class: 'card stat' }, h('span', { class: 'label' }, l), h('span', { class: 'value' }, fmtAcc(v))))),
        card('Rumore di fondo RMS per componente (trascina per ingrandire)', n1.el),
        card('Dinamica del rumore (scala logaritmica)', n2.el),
        h('div', { class: 'grid g2' }, card(`Mappa del rumore: giorno × ora (ultimi ${prof.days} giorni)`, hm.el), card('Profilo orario medio del rumore', hourly.el)),
        h('div', { class: 'grid g2' }, card('Frequenza di campionamento effettiva (oscillatore del chip)', sr.el), card('Qualità dei dati', qual.el)),
        h('p', { class: 'muted' }, 'Il rumore di giorno è dominato dall\'attività umana (passi, traffico, elettrodomestici); di notte resta il rumore elettronico del MEMS (~2-4 mg RMS per l\'MPU-6050). Più basso è il rumore notturno, più piccoli sono i terremoti che il sensore può distinguere.')));
      charts.push(hm);
    } else if (tab === 'inclinazione') {
      const rows = await get(`/api/stats/minute?from=${t1}&to=${now}&step=${step}`);
      const T = rows.map((r) => r.t);
      const base = rows.find((r) => r.mean_e != null) || {};
      const tilt = (r, k) => (r[k] == null || base[k] == null ? null : Math.asin(Math.max(-1, Math.min(1, r[k] - base[k]))) * 1e6);
      const t = mkChart({ height: 240, x: { type: 'time' }, y: { label: 'µrad', tipFmt: (v) => num(v, 0) + ' µrad' }, zoom: true, series: [
        { name: 'inclinazione E', color: cssVar('--e') }, { name: 'inclinazione N', color: cssVar('--n') }] });
      t.setData(T, [rows.map((r) => tilt(r, 'mean_e')), rows.map((r) => tilt(r, 'mean_n'))]);
      const z = mkChart({ height: 200, x: { type: 'time' }, y: { label: 'g', tipFmt: (v) => num(v, 5) + ' g' }, series: [{ name: 'Z statico', color: cssVar('--z') }] });
      z.setData(T, [rows.map((r) => r.mean_z)]);
      const tc = mkChart({ height: 200, x: { type: 'time' }, y: { label: '°C' }, series: [{ name: 'temperatura sensore', color: cssVar('--warn') }] });
      tc.setData(T, [rows.map((r) => r.temp_c)]);
      const sc = mkChart({ height: 220, x: { label: '°C', tipFmt: (v) => num(v, 2) + ' °C' }, y: { label: 'mg', tipFmt: (v) => num(v, 2) + ' mg' }, series: [{ name: 'E statico vs temperatura', color: cssVar('--e'), type: 'points' }, { name: 'N', color: cssVar('--n'), type: 'points' }] });
      const pts = rows.filter((r) => r.temp_c != null).sort((a, b) => a.temp_c - b.temp_c);
      sc.setData(pts.map((r) => r.temp_c), [pts.map((r) => (r.mean_e - (base.mean_e || 0)) * 1000), pts.map((r) => (r.mean_n - (base.mean_n || 0)) * 1000)]);
      body.replaceChildren(h('div', { class: 'stack' },
        h('div', { class: 'note' }, 'La componente statica (gravità) misurata minuto per minuto rivela inclinazioni lentissime del pavimento/edificio (assestamenti, dilatazioni termiche, umidità) e la deriva termica del sensore. Valori relativi all\'inizio dell\'intervallo.'),
        card('Inclinazione relativa', t.el), h('div', { class: 'grid g2' }, card('Componente verticale statica', z.el), card('Temperatura del sensore', tc.el)),
        card('Deriva termica: offset orizzontale in funzione della temperatura', sc.el)));
    } else if (tab === 'sistema') {
      const rows = await get(`/api/stats/system?from=${t1}&to=${now}&step=${step}`);
      const T = rows.map((r) => r.t);
      const c1 = mkChart({ height: 200, x: { type: 'time' }, y: { min: 0, max: 100, label: '%' }, series: [{ name: 'CPU totale', color: cssVar('--accent') }, { name: 'acquisizione', color: cssVar('--z') }, { name: 'RAM', color: cssVar('--violet') }] });
      c1.setData(T, [rows.map((r) => r.cpu_pct), rows.map((r) => r.acq_cpu), rows.map((r) => r.mem_pct)]);
      const c2 = mkChart({ height: 200, x: { type: 'time' }, y: { label: '°C' }, series: [{ name: 'temperatura CPU', color: cssVar('--bad') }] });
      c2.setData(T, [rows.map((r) => r.cpu_temp)]);
      const c3 = mkChart({ height: 200, x: { type: 'time' }, y: { min: 0, label: 'MHz' }, series: [{ name: 'frequenza CPU', color: cssVar('--n') }] });
      c3.setData(T, [rows.map((r) => r.cpu_freq)]);
      const c4 = mkChart({ height: 200, x: { type: 'time' }, y: { min: 0, label: 'KB' }, series: [{ name: 'scritture su SD', color: cssVar('--warn'), type: 'bar' }] });
      c4.setData(T, [rows.map((r) => r.sd_write_kb)]);
      const c5 = mkChart({ height: 200, x: { type: 'time' }, y: { min: 0, label: 'KB' }, series: [{ name: 'ricevuti', color: cssVar('--e') }, { name: 'inviati', color: cssVar('--n') }] });
      c5.setData(T, [rows.map((r) => r.net_rx_kb), rows.map((r) => r.net_tx_kb)]);
      const c6 = mkChart({ height: 200, x: { type: 'time' }, y: { min: 0, max: 100, label: '%' }, series: [{ name: 'segnale Wi-Fi', color: cssVar('--accent') }, { name: 'disco', color: cssVar('--faint') }] });
      c6.setData(T, [rows.map((r) => r.wifi_signal), rows.map((r) => r.disk_pct)]);
      const c7 = mkChart({ height: 200, x: { type: 'time' }, y: { min: 0 }, series: [{ name: 'carico medio (1 min)', color: cssVar('--z') }, { name: 'sottotensione', color: cssVar('--bad'), type: 'bar' }] });
      c7.setData(T, [rows.map((r) => r.load1), rows.map((r) => r.undervoltage)]);
      const totSd = rows.reduce((a, r) => a + (r.sd_write_kb || 0), 0) * 1024;
      body.replaceChildren(h('div', { class: 'stack' },
        h('div', { class: 'grid g4' },
          h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'Scritto su SD nel periodo'), h('span', { class: 'value' }, fmtBytes(totSd)), h('span', { class: 'sub' }, `≈ ${fmtBytes(totSd / Math.max(1, range))} al giorno (tutto il sistema)`)),
          h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'Temperatura CPU max'), h('span', { class: 'value' }, num(Math.max(...rows.map((r) => r.cpu_temp || 0)), 1), h('small', {}, ' °C'))),
          h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'CPU media'), h('span', { class: 'value' }, num(rows.reduce((a, r) => a + (r.cpu_pct || 0), 0) / Math.max(1, rows.length), 0), h('small', {}, ' %'))),
          h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'Minuti in sottotensione'), h('span', { class: 'value' }, rows.reduce((a, r) => a + (r.undervoltage || 0), 0)))),
        h('div', { class: 'grid g2' }, card('CPU e memoria', c1.el), card('Temperatura SoC', c2.el)),
        h('div', { class: 'grid g2' }, card('Frequenza CPU', c3.el), card('Scritture sulla SD (KB per intervallo)', c4.el)),
        h('div', { class: 'grid g2' }, card('Traffico Wi-Fi (KB per intervallo)', c5.el), card('Segnale Wi-Fi e occupazione disco', c6.el)),
        card('Carico e alimentazione', c7.el)));
    } else if (tab === 'archivio') {
      const s = await get('/api/stats/storage');
      const w = s.sd_writes_per_day;
      const raw = mkChart({ height: 200, x: { labels: s.raw_days.map((d) => d.day.slice(6) + '/' + d.day.slice(4, 6)) }, y: { min: 0, fmt: (v) => fmtBytes(v) }, series: [{ name: 'dati grezzi', color: cssVar('--accent'), type: 'bar', fmt: fmtBytes }] });
      raw.setData(s.raw_days.map((_, i) => i), [s.raw_days.map((d) => d.bytes)]);
      body.replaceChildren(h('div', { class: 'stack' },
        h('div', { class: 'grid g4' }, ...[['Dati grezzi', s.raw_total, `modalità ${s.raw_mode === 'ram' ? 'RAM' : s.raw_mode === 'sd' ? 'SD' : 'disattivata'}`], ['Rullo (inviluppi)', s.heli, 'su SD'], ['Forme d\'onda eventi', s.events, 'su SD'], ['Database', s.db, 'su SD']]
          .map(([l, v, sub]) => h('div', { class: 'card stat' }, h('span', { class: 'label' }, l), h('span', { class: 'value' }, fmtBytes(v)), h('span', { class: 'sub' }, sub)))),
        h('div', { class: 'grid g2' },
          card('Scritture stimate del sismografo su SD', h('div', { class: 'kv' },
            h('span', {}, 'Dati grezzi'), h('span', {}, s.raw_mode === 'sd' ? fmtBytes(w.raw) + '/giorno' : '0 (in RAM o disattivati)'),
            h('span', {}, 'Rullo'), h('span', {}, fmtBytes(w.helicorder) + '/giorno (a blocchi ogni ' + state.cfg.storage.heli_flush_s / 60 + ' min)'),
            h('span', {}, 'Statistiche'), h('span', {}, '≈ ' + fmtBytes(w.stats) + '/giorno (a gruppi di ' + state.cfg.storage.stats_batch_minutes + ' min)'),
            h('span', {}, 'Eventi'), h('span', {}, '≈ 60-400 KB per evento'),
            h('span', {}, 'Totale stimato'), h('b', {}, fmtBytes(w.total) + '/giorno + eventi'))),
          card('Spazio', h('div', { class: 'kv' },
            h('span', {}, 'SD totale'), h('span', {}, fmtBytes(s.disk.total)), h('span', {}, 'SD libera'), h('span', {}, `${fmtBytes(s.disk.free)} (${num(100 - s.disk.pct, 0)}%)`),
            h('span', {}, 'RAM condivisa (/dev/shm)'), h('span', {}, s.ram ? `${fmtBytes(s.ram.used)} usati di ${fmtBytes(s.ram.total)}` : '–'),
            h('span', {}, 'Raw a 100 Hz'), h('span', {}, fmtBytes(s.bytes_per_day) + ' al giorno')))),
        s.raw_days.length ? card('Dati grezzi per giorno', raw.el) : null));
    }
  }
  show('eventi');
  return () => clearCharts();
}
