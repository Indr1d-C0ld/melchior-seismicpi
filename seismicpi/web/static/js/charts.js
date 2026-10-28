// Grafici su canvas senza librerie esterne (la dashboard deve funzionare anche offline, in AP).
import { h, cssVar, fmtTime, fmtDate } from './lib.js';

function niceStep(range, target) {
  const raw = range / Math.max(1, target);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / mag;
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * mag;
}
// Ogni generatore di tacche rifiuta limiti non finiti e si ferma a MAX_TICKS: un asse vuoto o
// logaritmico con valori ≤ 0 non deve mai trasformarsi in un ciclo infinito.
const MAX_TICKS = 200;
const finite = (...v) => v.every(Number.isFinite);
export function niceTicks(lo, hi, target = 5) {
  if (!finite(lo, hi)) return [];
  if (!(hi > lo)) return [lo];
  const st = niceStep(hi - lo, target), out = [];
  if (!(st > 0)) return [lo];
  for (let v = Math.ceil(lo / st) * st; v <= hi + st * 1e-9 && out.length < MAX_TICKS; v += st) out.push(+v.toFixed(12));
  return out;
}
const TIME_STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800];
function timeTicks(lo, hi, px) {
  if (!finite(lo, hi) || !(hi > lo)) return { ticks: [], step: 60 };
  const target = Math.max(2, px / 90);
  const st = TIME_STEPS.find((s) => (hi - lo) / s <= target) || 604800;
  const off = st >= 86400 ? new Date().getTimezoneOffset() * -60 : 0;
  const out = [];
  for (let v = Math.ceil((lo + off) / st) * st - off; v <= hi && out.length < MAX_TICKS; v += st) out.push(v);
  return { ticks: out, step: st };
}
function fmtTick(v) {
  const a = Math.abs(v);
  if (a === 0) return '0';
  if (a >= 10000 || a < 0.001) return v.toExponential(0);
  if (a >= 100) return v.toFixed(0);
  if (a >= 1) return +v.toFixed(2) + '';
  return +v.toFixed(4) + '';
}

export class Chart {
  constructor(opts = {}) {
    this.o = Object.assign({ height: 220, x: {}, y: {}, y2: null, series: [], legend: true, tooltip: true, zoom: false,
      markers: [], bands: [], pad: null, stacked: false }, opts);
    this.el = h('div', { class: 'chart' });
    this.canvas = h('canvas');
    this.tip = h('div', { class: 'tip' });
    this.legendEl = this.o.legend ? h('div', { class: 'legend' }) : null;
    this.el.append(this.canvas, this.tip);
    if (this.legendEl) this.el.append(this.legendEl);
    this.xs = []; this.ys = []; this.hidden = new Set(); this.view = null;
    this.canvas.style.height = this.o.height + 'px';
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(this.el);
    this._bind();
    this._legend();
  }
  destroy() { this.ro.disconnect(); }
  setSeries(series) { this.o.series = series; this._legend(); }
  setData(xs, ys) { this.xs = xs; this.ys = ys; this.draw(); }
  setMarkers(m) { this.o.markers = m; this.draw(); }
  resetZoom() { this.view = null; this.draw(); this.o.onzoom?.(null); }
  _legend() {
    if (!this.legendEl) return;
    this.legendEl.replaceChildren(...this.o.series.map((s, i) => {
      const sp = h('span', { class: this.hidden.has(i) ? 'off' : '' }, h('i', { style: { background: s.color } }), s.name);
      sp.onclick = () => { this.hidden.has(i) ? this.hidden.delete(i) : this.hidden.add(i); sp.classList.toggle('off'); this.draw(); };
      return sp;
    }));
  }
  _geom() {
    const dpr = window.devicePixelRatio || 1;
    const w = this.el.clientWidth || 300, hgt = this.o.height;
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(hgt * dpr)) {
      this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(hgt * dpr);
    }
    const p = this.o.pad || { l: this.o.y.hide ? 8 : 52, r: this.o.y2 ? 52 : 12, t: 8, b: this.o.x.hide ? 6 : 24 };
    return { dpr, w, h: hgt, p, pw: w - p.l - p.r, ph: hgt - p.t - p.b };
  }
  _xRange() {
    if (this.view) return this.view;
    const x = this.o.x;
    let lo = x.min, hi = x.max;
    if (lo == null || hi == null) {
      let a = Infinity, b = -Infinity;
      for (const v of this.xs) { if (v == null) continue; if (v < a) a = v; if (v > b) b = v; }
      if (lo == null) lo = a; if (hi == null) hi = b;
    }
    if (!finite(lo, hi)) {
      const now = Date.now() / 1000;
      [lo, hi] = x.type === 'time' ? [now - 60, now] : x.log ? [1, 10] : [0, 1];
    }
    if (x.log && !(lo > 0)) lo = hi > 0 ? hi / 1000 : 1;
    if (!(hi > lo)) { hi = lo + (x.log ? lo * 9 : 1); }
    return [lo, hi];
  }
  _yRange(axis, x0, x1) {
    const cfg = axis === 'y2' ? this.o.y2 : this.o.y;
    if (cfg.min != null && cfg.max != null) return [cfg.min, cfg.max];
    let lo = Infinity, hi = -Infinity;
    const i0 = this._idx(x0), i1 = this._idx(x1) + 1;
    this.o.series.forEach((s, si) => {
      if (this.hidden.has(si) || (s.axis || 'y') !== axis) return;
      const arr = this.ys[si] || [];
      for (let i = Math.max(0, i0 - 1); i < Math.min(arr.length, i1 + 1); i++) {
        const v = arr[i]; if (v == null || !Number.isFinite(v)) continue;
        if (cfg.log && v <= 0) continue;
        if (v < lo) lo = v; if (v > hi) hi = v;
      }
    });
    if (!Number.isFinite(lo)) { lo = cfg.log ? 0.1 : 0; hi = cfg.log ? 10 : 1; }
    if (cfg.min != null) lo = cfg.min; if (cfg.max != null) hi = cfg.max;
    if (cfg.minSpan && hi - lo < cfg.minSpan) { const c = (hi + lo) / 2; lo = c - cfg.minSpan / 2; hi = c + cfg.minSpan / 2; }
    if (cfg.symmetric) { const m = Math.max(Math.abs(lo), Math.abs(hi)) || 1; lo = -m; hi = m; }
    if (cfg.zero) { lo = Math.min(0, lo); hi = Math.max(0, hi); }
    if (cfg.log && !(lo > 0)) lo = hi > 0 ? hi / 1000 : 0.1;
    if (cfg.log) { lo = Math.pow(10, Math.floor(Math.log10(lo))); hi = Math.pow(10, Math.ceil(Math.log10(hi))); if (hi <= lo) hi = lo * 10; }
    else if (hi === lo) { hi += 1; lo -= 1; }
    else if (!cfg.symmetric) { const pd = (hi - lo) * 0.06; if (cfg.min == null && !cfg.zero) lo -= pd; if (cfg.max == null) hi += pd; }
    else { lo *= 1.06; hi *= 1.06; }
    return [lo, hi];
  }
  _idx(x) {
    const xs = this.xs; let lo = 0, hi = xs.length - 1;
    if (hi < 0) return 0;
    while (lo < hi) { const m = (lo + hi) >> 1; if (xs[m] < x) lo = m + 1; else hi = m; }
    return lo;
  }
  draw() {
    const g = this._geom(); const ctx = this.canvas.getContext('2d');
    ctx.setTransform(g.dpr, 0, 0, g.dpr, 0, 0);
    ctx.clearRect(0, 0, g.w, g.h);
    const [x0, x1] = this._xRange();
    const xl = this.o.x.log;
    const X = xl ? (v) => g.p.l + (Math.log10(v) - Math.log10(x0)) / (Math.log10(x1) - Math.log10(x0)) * g.pw
      : (v) => g.p.l + (v - x0) / (x1 - x0) * g.pw;
    const ranges = { y: this._yRange('y', x0, x1) };
    if (this.o.y2) ranges.y2 = this._yRange('y2', x0, x1);
    const Y = (axis) => {
      const [lo, hi] = ranges[axis]; const cfg = axis === 'y2' ? this.o.y2 : this.o.y;
      return cfg.log ? (v) => g.p.t + g.ph - (Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo)) * g.ph
        : (v) => g.p.t + g.ph - (v - lo) / (hi - lo) * g.ph;
    };
    this._X = X; this._g = g; this._x0 = x0; this._x1 = x1; this._Y = Y('y'); this._ranges = ranges;
    const grid = cssVar('--grid'), muted = cssVar('--muted');
    ctx.font = '11px system-ui, sans-serif'; ctx.lineWidth = 1;
    // bande
    for (const b of this.o.bands || []) {
      ctx.fillStyle = b.color; const a = Math.max(g.p.l, X(b.x0)), c = Math.min(g.p.l + g.pw, X(b.x1));
      if (c > a) ctx.fillRect(a, g.p.t, c - a, g.ph);
    }
    // griglia Y
    const yc = this.o.y, [ylo, yhi] = ranges.y, Yf = Y('y');
    const yt = yc.log ? logTicks(ylo, yhi) : niceTicks(ylo, yhi, Math.max(2, g.ph / 45));
    ctx.fillStyle = muted; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    for (const v of yt) {
      const y = Math.round(Yf(v)) + 0.5; if (y < g.p.t - 1 || y > g.p.t + g.ph + 1) continue;
      ctx.strokeStyle = grid; ctx.beginPath(); ctx.moveTo(g.p.l, y); ctx.lineTo(g.p.l + g.pw, y); ctx.stroke();
      if (!yc.hide) ctx.fillText((yc.fmt || fmtTick)(v), g.p.l - 6, y);
    }
    if (yc.label && !yc.hide) {
      ctx.save(); ctx.translate(11, g.p.t + g.ph / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = 'center'; ctx.fillText(yc.label, 0, 0); ctx.restore();
    }
    if (this.o.y2) {
      const Y2 = Y('y2'), [a, b] = ranges.y2;
      const t2 = this.o.y2.log ? logTicks(a, b) : niceTicks(a, b, Math.max(2, g.ph / 45));
      ctx.textAlign = 'left';
      for (const v of t2) { const y = Y2(v); if (y >= g.p.t && y <= g.p.t + g.ph) ctx.fillText((this.o.y2.fmt || fmtTick)(v), g.p.l + g.pw + 6, y); }
      if (this.o.y2.label) { ctx.save(); ctx.translate(g.w - 8, g.p.t + g.ph / 2); ctx.rotate(Math.PI / 2); ctx.textAlign = 'center'; ctx.fillText(this.o.y2.label, 0, 0); ctx.restore(); }
    }
    // griglia X
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    const xc = this.o.x;
    let xt, fmtX = xc.fmt;
    if (xc.type === 'time') {
      const r = timeTicks(x0, x1, g.pw); xt = r.ticks;
      fmtX = fmtX || ((v) => r.step >= 86400 ? fmtDate(v).slice(0, 5) : fmtTime(v, r.step < 60));
    } else if (xc.log) xt = logTicks(x0, x1);
    else if (xc.labels) xt = xc.labels.map((_, i) => i);
    else xt = niceTicks(x0, x1, Math.max(2, g.pw / 80));
    for (const v of xt) {
      const x = Math.round(X(v)) + 0.5; if (x < g.p.l - 1 || x > g.p.l + g.pw + 1) continue;
      ctx.strokeStyle = grid; ctx.beginPath(); ctx.moveTo(x, g.p.t); ctx.lineTo(x, g.p.t + g.ph); ctx.stroke();
      if (!xc.hide) ctx.fillText(xc.labels ? xc.labels[v] : (fmtX || fmtTick)(v), x, g.p.t + g.ph + 6);
    }
    // serie
    ctx.save(); ctx.beginPath(); ctx.rect(g.p.l, g.p.t, g.pw, g.ph); ctx.clip();
    this.o.series.forEach((s, si) => {
      if (this.hidden.has(si)) return;
      const arr = this.ys[si]; if (!arr) return;
      const Ys = Y(s.axis || 'y');
      ctx.strokeStyle = s.color; ctx.fillStyle = s.color; ctx.lineWidth = s.width || 1.3;
      if (s.type === 'bar') return this._bars(ctx, arr, X, Ys, s, g, si);
      if (s.type === 'points') {
        for (let i = 0; i < arr.length; i++) { const v = arr[i]; if (v == null) continue; ctx.beginPath(); ctx.arc(X(this.xs[i]), Ys(v), s.r || 2.5, 0, 7); ctx.fill(); }
        return;
      }
      this._line(ctx, arr, X, Ys, s, g, x0, x1);
    });
    // marcatori verticali
    for (const m of this.o.markers || []) {
      const x = X(m.x); if (x < g.p.l || x > g.p.l + g.pw) continue;
      ctx.strokeStyle = m.color || cssVar('--bad'); ctx.lineWidth = m.width || 1.2; ctx.setLineDash(m.dash || []);
      ctx.beginPath(); ctx.moveTo(x + 0.5, g.p.t); ctx.lineTo(x + 0.5, g.p.t + g.ph); ctx.stroke(); ctx.setLineDash([]);
      if (m.label) { ctx.fillStyle = m.color || cssVar('--bad'); ctx.textAlign = 'left'; ctx.textBaseline = 'top'; ctx.fillText(m.label, x + 3, g.p.t + 2 + (m.row || 0) * 13); }
    }
    for (const l of this.o.hlines || []) {
      const y = Yf(l.y); if (y < g.p.t || y > g.p.t + g.ph) continue;
      ctx.strokeStyle = l.color; ctx.setLineDash(l.dash || [4, 4]); ctx.beginPath(); ctx.moveTo(g.p.l, y); ctx.lineTo(g.p.l + g.pw, y); ctx.stroke(); ctx.setLineDash([]);
      if (l.label) { ctx.fillStyle = l.color; ctx.textAlign = 'right'; ctx.textBaseline = 'bottom'; ctx.fillText(l.label, g.p.l + g.pw - 4, y - 2); }
    }
    ctx.restore();
    if (this._sel) { ctx.fillStyle = 'rgba(96,165,250,.15)'; ctx.fillRect(Math.min(this._sel[0], this._sel[1]), g.p.t, Math.abs(this._sel[1] - this._sel[0]), g.ph); }
    this.o.afterDraw?.(ctx, this);
  }
  _line(ctx, arr, X, Y, s, g, x0, x1) {
    const xs = this.xs, n = Math.min(arr.length, xs.length);
    const i0 = Math.max(0, this._idx(x0) - 1), i1 = Math.min(n, this._idx(x1) + 2);
    const count = i1 - i0;
    if (s.type === 'area') ctx.globalAlpha = 0.25;
    if (count > g.pw * 2.5 && !this.o.x.log) {
      // decimazione min/max per colonna di pixel
      let col = -1, mn = 0, mx = 0, first = true;
      ctx.beginPath();
      const flush = () => { if (col >= 0) { const x = g.p.l + col + 0.5; if (first) { ctx.moveTo(x, Y(mx)); first = false; } else ctx.lineTo(x, Y(mx)); ctx.lineTo(x, Y(mn)); } };
      for (let i = i0; i < i1; i++) {
        const v = arr[i]; if (v == null) { flush(); col = -1; first = true; continue; }
        const c = Math.floor(X(xs[i]) - g.p.l);
        if (c !== col) { flush(); col = c; mn = mx = v; } else { if (v < mn) mn = v; if (v > mx) mx = v; }
      }
      flush(); ctx.stroke();
    } else {
      ctx.beginPath(); let pen = false;
      for (let i = i0; i < i1; i++) {
        const v = arr[i];
        if (v == null || (this.o.y.log && v <= 0)) { pen = false; continue; }
        const x = X(xs[i]), y = Y(v);
        if (s.type === 'step' && pen) ctx.lineTo(x, ctx._lastY ?? y);
        pen ? ctx.lineTo(x, y) : ctx.moveTo(x, y); pen = true; ctx._lastY = y;
      }
      if (s.type === 'area') { ctx.lineTo(X(xs[i1 - 1]), g.p.t + g.ph); ctx.lineTo(X(xs[i0]), g.p.t + g.ph); ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1; ctx.beginPath(); }
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }
  _bars(ctx, arr, X, Y, s, g, si) {
    const nser = this.o.series.filter((x, i) => x.type === 'bar' && !this.hidden.has(i)).length;
    const idx = this.o.series.filter((x, i) => x.type === 'bar' && !this.hidden.has(i) && i < si).length;
    const slot = this.xs.length > 1 ? Math.abs(X(this.xs[1]) - X(this.xs[0])) : g.pw / 2;
    const bw = Math.max(1, slot * 0.8 / (this.o.stacked ? 1 : nser));
    const base = Y(0 > this._ranges.y[0] ? 0 : this._ranges.y[0]);
    for (let i = 0; i < arr.length; i++) {
      const v = arr[i]; if (!v) continue;
      let off = 0;
      if (this.o.stacked) for (let k = 0; k < si; k++) if (!this.hidden.has(k)) off += this.ys[k]?.[i] || 0;
      const x = X(this.xs[i]) - slot * 0.4 + (this.o.stacked ? 0 : idx * bw);
      const y1 = Y(off + v), y0 = this.o.stacked ? Y(off) : base;
      ctx.fillRect(x, y1, bw, y0 - y1);
    }
  }
  _bind() {
    const c = this.canvas;
    c.addEventListener('mousemove', (e) => {
      const r = c.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
      if (this._drag != null) { this._sel = [this._drag, px]; this.draw(); }
      if (!this.o.tooltip || !this.xs.length || !this._g) return;
      const g = this._g; if (px < g.p.l || px > g.p.l + g.pw) { this.tip.style.display = 'none'; return; }
      const xv = this.o.x.log ? Math.pow(10, Math.log10(this._x0) + (px - g.p.l) / g.pw * (Math.log10(this._x1) - Math.log10(this._x0)))
        : this._x0 + (px - g.p.l) / g.pw * (this._x1 - this._x0);
      let i = this._idx(xv); if (i > 0 && Math.abs(this.xs[i - 1] - xv) < Math.abs(this.xs[i] - xv)) i--;
      const xl = this.o.x.labels ? this.o.x.labels[this.xs[i]] : this.o.x.type === 'time' ? (this.o.x.tipFmt || ((v) => `${fmtDate(v)} ${fmtTime(v)}`))(this.xs[i]) : (this.o.x.tipFmt || fmtTick)(this.xs[i]);
      const rows = this.o.series.map((s, si) => {
        if (this.hidden.has(si)) return null; const v = this.ys[si]?.[i]; if (v == null) return null;
        return h('div', {}, h('b', { style: { color: s.color } }, '■ '), `${s.name}: ${(s.fmt || this.o.y.tipFmt || fmtTick)(v)}${s.unit ? ' ' + s.unit : ''}`);
      }).filter(Boolean);
      this.tip.replaceChildren(h('div', { class: 'muted' }, xl), ...rows);
      this.tip.style.display = 'block';
      const tw = this.tip.offsetWidth;
      this.tip.style.left = (px + 14 + tw > r.width ? px - tw - 14 : px + 14) + 'px';
      this.tip.style.top = Math.max(0, py - 10) + 'px';
    });
    c.addEventListener('mouseleave', () => { this.tip.style.display = 'none'; });
    if (this.o.zoom) {
      c.addEventListener('mousedown', (e) => { const r = c.getBoundingClientRect(); this._drag = e.clientX - r.left; });
      window.addEventListener('mouseup', (e) => {
        if (this._drag == null) return;
        const r = c.getBoundingClientRect(), a = this._drag, b = e.clientX - r.left; this._drag = null; this._sel = null;
        if (Math.abs(b - a) < 5) { this.draw(); return; }
        const g = this._g, f = (px) => this._x0 + (Math.max(g.p.l, Math.min(g.p.l + g.pw, px)) - g.p.l) / g.pw * (this._x1 - this._x0);
        this.view = [f(Math.min(a, b)), f(Math.max(a, b))]; this.draw(); this.o.onzoom?.(this.view);
      });
      c.addEventListener('dblclick', () => this.resetZoom());
    }
    if (this.o.onclick) c.addEventListener('click', (e) => {
      const r = c.getBoundingClientRect(), g = this._g, px = e.clientX - r.left;
      if (px < g.p.l || px > g.p.l + g.pw) return;
      this.o.onclick(this._x0 + (px - g.p.l) / g.pw * (this._x1 - this._x0), e);
    });
  }
}

function logTicks(lo, hi) {
  if (!finite(lo, hi) || !(lo > 0) || !(hi > lo)) return [];
  const out = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)) && out.length < MAX_TICKS; e++) out.push(Math.pow(10, e));
  return out.filter((v) => v >= lo * 0.999 && v <= hi * 1.001);
}

// mappa di calore: matrice [righe][colonne]
export class Heatmap {
  constructor({ rows, cols, height = 220, fmt = (v) => v, colors = null, title = '' }) {
    this.o = { rows, cols, height, fmt, colors };
    this.el = h('div', { class: 'chart' });
    this.canvas = h('canvas', { style: { height: height + 'px' } });
    this.tip = h('div', { class: 'tip' });
    this.el.append(this.canvas, this.tip);
    this.m = [];
    this.ro = new ResizeObserver(() => this.draw()); this.ro.observe(this.el);
    this.canvas.addEventListener('mousemove', (e) => this._hover(e));
    this.canvas.addEventListener('mouseleave', () => { this.tip.style.display = 'none'; });
  }
  setData(m, colorFn) { this.m = m; this.colorFn = colorFn; this.draw(); }
  destroy() { this.ro.disconnect(); }
  draw() {
    const dpr = window.devicePixelRatio || 1, w = this.el.clientWidth || 300, H = this.o.height;
    this.canvas.width = w * dpr; this.canvas.height = H * dpr;
    const ctx = this.canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, w, H);
    const L = 40, T = 4, B = 20, cw = (w - L - 6) / this.o.cols.length, ch = (H - T - B) / this.o.rows.length;
    this._geo = { L, T, cw, ch };
    let lo = Infinity, hi = -Infinity;
    for (const r of this.m) for (const v of r) if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    this._lo = lo; this._hi = hi;
    const { viridisFn } = this;
    for (let i = 0; i < this.m.length; i++) for (let j = 0; j < (this.m[i] || []).length; j++) {
      const v = this.m[i][j];
      if (v == null) { ctx.fillStyle = cssVar('--panel2'); }
      else {
        const t = hi > lo ? (v - lo) / (hi - lo) : 0.5;
        const c = (this.colorFn || viridisFn || defaultColor)(t);
        ctx.fillStyle = `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`;
      }
      ctx.fillRect(L + j * cw, T + i * ch, Math.ceil(cw) - 1, Math.ceil(ch) - 1);
    }
    ctx.fillStyle = cssVar('--muted'); ctx.font = '11px system-ui'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    this.o.rows.forEach((r, i) => ctx.fillText(r, L - 6, T + i * ch + ch / 2));
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    const every = Math.ceil(30 / cw);
    this.o.cols.forEach((c, j) => { if (j % every === 0) ctx.fillText(c, L + j * cw + cw / 2, H - B + 5); });
  }
  _hover(e) {
    if (!this._geo) return;
    const r = this.canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    const { L, T, cw, ch } = this._geo, j = Math.floor((x - L) / cw), i = Math.floor((y - T) / ch);
    const v = this.m[i]?.[j];
    if (i < 0 || j < 0 || i >= this.o.rows.length || j >= this.o.cols.length) { this.tip.style.display = 'none'; return; }
    this.tip.textContent = `${this.o.rows[i]} ${this.o.cols[j]}: ${v == null ? 'nessun dato' : this.o.fmt(v)}`;
    this.tip.style.display = 'block'; this.tip.style.left = (x + 12) + 'px'; this.tip.style.top = (y - 8) + 'px';
  }
}
function defaultColor(t) {
  const a = [30, 58, 138], b = [96, 165, 250], c = [251, 191, 36], d = [239, 68, 68];
  const seg = t < 0.5 ? [a, b, t * 2] : t < 0.8 ? [b, c, (t - 0.5) / 0.3] : [c, d, (t - 0.8) / 0.2];
  return [0, 1, 2].map((k) => seg[0][k] + (seg[1][k] - seg[0][k]) * seg[2]);
}

// spettrogramma su canvas
export function drawSpectrogram(canvas, sg, { fmax = null, dbRange = 60, colormap }) {
  const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, H = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = H * dpr;
  const ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const L = 52, R = 12, T = 6, B = 22, pw = w - L - R, ph = H - T - B;
  ctx.clearRect(0, 0, w, H);
  if (!sg.cols.length) return;
  const bins = sg.cols[0].length, fNyq = sg.fs / 2, fTop = Math.min(fmax || fNyq, fNyq);
  const kTop = Math.max(1, Math.round(fTop / fNyq * bins));
  let mx = -Infinity; for (const c of sg.cols) for (let k = 1; k < kTop; k++) mx = Math.max(mx, c[k]);
  const img = ctx.createImageData(Math.max(1, Math.round(pw * dpr)), Math.max(1, Math.round(ph * dpr)));
  const IW = img.width, IH = img.height;
  for (let x = 0; x < IW; x++) {
    const col = sg.cols[Math.min(sg.cols.length - 1, Math.floor(x / IW * sg.cols.length))];
    for (let y = 0; y < IH; y++) {
      const k = Math.min(kTop - 1, Math.floor((1 - y / IH) * kTop));
      const t = (col[k] - (mx - dbRange)) / dbRange;
      const c = colormap(t); const o = (y * IW + x) * 4;
      img.data[o] = c[0]; img.data[o + 1] = c[1]; img.data[o + 2] = c[2]; img.data[o + 3] = 255;
    }
  }
  ctx.putImageData(img, L * dpr, T * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = cssVar('--muted'); ctx.font = '11px system-ui'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  for (const f of niceTicks(0, fTop, 5)) { const y = T + ph - f / fTop * ph; ctx.fillText(f + ' Hz', L - 6, y); }
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  const dur = (sg.cols.length * sg.hop + sg.nfft) / sg.fs;
  for (const s of niceTicks(0, dur, Math.max(2, pw / 90))) ctx.fillText(s + ' s', L + s / dur * pw, T + ph + 6);
}
