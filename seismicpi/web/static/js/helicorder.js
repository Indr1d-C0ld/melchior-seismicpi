// Rullo digitale (helicorder): ricostruzione del tamburo a carta dei sismografi classici.
// Il server manda gli inviluppi min/max (0,1 mg) a 2 slot al secondo; l'accorpamento per pixel
// si fa qui, così il Pi legge solo il file senza elaborarlo.
import { h, cssVar, fmtTime, fmtDate, utcMode, accFactor, accUnit, num } from './lib.js';

const SLOT = 0.5, EMPTY_MIN = 32767, EMPTY_MAX = -32768;
const PAPER_INKS = ['#1d2a44', '#9b1c1c', '#1e40af', '#166534'];

function utcDayKey(t) {
  const d = new Date(t * 1000);
  return `${d.getUTCFullYear()}${String(d.getUTCMonth() + 1).padStart(2, '0')}${String(d.getUTCDate()).padStart(2, '0')}`;
}
function utcDayStart(t) { return Math.floor(t / 86400) * 86400; }

export class Helicorder {
  constructor(opts = {}) {
    this.o = Object.assign({ channel: 'Z', lineMinutes: 15, hours: 24, gain: 1, style: 'carta', clip: true,
      mode: 'live', showEvents: true, showCatalog: true, height: 760, onpick: null }, opts);
    this.el = h('div', { class: 'heli-wrap' });
    this.canvas = h('canvas', { style: { height: this.o.height + 'px' } });
    this.tip = h('div', { class: 'tip', style: { position: 'absolute', display: 'none', pointerEvents: 'none' } });
    this.el.append(this.canvas, this.tip);
    this.mins = null; this.maxs = null; this.t1 = 0; this.loadedUntil = 0; this.marks = { events: [], catalog: [] };
    this.autoScale = 1;
    this.ro = new ResizeObserver(() => this.draw()); this.ro.observe(this.el);
    this.canvas.addEventListener('click', (e) => this._click(e));
    this.canvas.addEventListener('mousemove', (e) => this._hover(e));
    this.canvas.addEventListener('mouseleave', () => { this.tip.style.display = 'none'; });
  }
  destroy() { this.ro.disconnect(); clearInterval(this.timer); }

  // inizio finestra: in modalità "giorno" la mezzanotte del giorno scelto (fuso di visualizzazione)
  windowStart(dayTs = null) {
    const L = this.o.lineMinutes * 60, span = this.o.hours * 3600;
    if (this.o.mode === 'day' && dayTs != null) {
      const d = new Date(dayTs * 1000);
      return utcMode() ? Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) / 1000
        : new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() / 1000;
    }
    const now = Date.now() / 1000;
    return Math.floor((now - span) / L) * L + L;
  }

  async load(dayTs = null) {
    this.t1 = this.windowStart(dayTs);
    const n = Math.round(this.o.hours * 3600 / SLOT);
    this.mins = new Int16Array(n).fill(EMPTY_MIN);
    this.maxs = new Int16Array(n).fill(EMPTY_MAX);
    await this._fetch(this.t1, this.t1 + this.o.hours * 3600);
    await this._marks();
    this._autoscale();
    this.draw();
  }

  async _fetch(a, b) {
    b = Math.min(b, Date.now() / 1000 + 1);
    for (let d = utcDayStart(a); d < b; d += 86400) {
      const s0 = Math.max(0, Math.floor((Math.max(a, d) - d) / SLOT)), s1 = Math.min(172800, Math.ceil((Math.min(b, d + 86400) - d) / SLOT));
      if (s1 <= s0) continue;
      const r = await fetch(`/api/heli?day=${utcDayKey(d)}&ch=${this.o.channel}&slot_from=${s0}&slot_to=${s1}`);
      if (!r.ok) continue;
      const arr = new Int16Array(await r.arrayBuffer());
      for (let i = 0; i < arr.length / 2; i++) {
        const t = d + (s0 + i) * SLOT, k = Math.round((t - this.t1) / SLOT);
        if (k >= 0 && k < this.mins.length) { this.mins[k] = arr[2 * i]; this.maxs[k] = arr[2 * i + 1]; }
      }
    }
    this.loadedUntil = b;
  }

  async _marks() {
    try {
      const r = await fetch(`/api/heli/marks?from=${this.t1}&to=${this.t1 + this.o.hours * 3600}`);
      if (r.ok) this.marks = await r.json();
    } catch { /* offline */ }
  }

  // aggiornamento incrementale (solo gli ultimi secondi)
  async tick() {
    if (this.o.mode !== 'live' || !this.mins) return;
    const now = Date.now() / 1000;
    if (now > this.t1 + this.o.hours * 3600 + 5) { await this.load(); return; }
    await this._fetch(Math.max(this.t1, this.loadedUntil - 15), now);
    if (!this._mt || now - this._mt > 30) { this._mt = now; await this._marks(); }
    this.draw();
  }
  startLive(ms = 5000) { clearInterval(this.timer); this.timer = setInterval(() => this.tick(), ms); }
  stopLive() { clearInterval(this.timer); }

  // scala automatica: la mediana dell'ampiezza picco-picco occupa ~12% dell'altezza di riga
  _autoscale() {
    const pp = [];
    for (let i = 0; i < this.mins.length; i += 7) if (this.mins[i] !== EMPTY_MIN) pp.push(this.maxs[i] - this.mins[i]);
    pp.sort((a, b) => a - b);
    const med = pp.length ? pp[Math.floor(pp.length / 2)] : 20;
    this.autoScale = Math.max(med, 4);
  }

  _geom() {
    const dpr = window.devicePixelRatio || 1, w = this.el.clientWidth || 600, H = this.o.height;
    const nLines = Math.round(this.o.hours * 60 / this.o.lineMinutes);
    const L = 58, R = 46, T = 34, B = 26;
    return { dpr, w, H, L, R, T, B, nLines, pw: w - L - R, lh: (H - T - B) / nLines };
  }

  draw() {
    if (!this.mins) return;
    const g = this._geom(); this._g = g;
    const c = this.canvas; c.width = g.w * g.dpr; c.height = g.H * g.dpr;
    const ctx = c.getContext('2d'); ctx.setTransform(g.dpr, 0, 0, g.dpr, 0, 0);
    const paper = this.o.style === 'carta';
    ctx.fillStyle = paper ? cssVar('--paper') : cssVar('--panel'); ctx.fillRect(0, 0, g.w, g.H);
    const inkCols = paper ? PAPER_INKS : [cssVar('--e'), cssVar('--n'), cssVar('--z'), cssVar('--v')];
    const label = paper ? '#5b5340' : cssVar('--muted');
    const gridCol = paper ? 'rgba(80,70,40,.12)' : cssVar('--grid');
    const lineSec = this.o.lineMinutes * 60, slotsPerLine = lineSec / SLOT;
    const half = g.lh / 2, scale = (g.lh * 0.12 / this.autoScale) * 2 * this.o.gain;   // px per 0,1 mg
    ctx.font = '11px system-ui, sans-serif';
    // intestazione
    ctx.fillStyle = label; ctx.textBaseline = 'middle'; ctx.textAlign = 'left';
    const mgPerLine = (g.lh / 2) / scale / 10;
    ctx.fillText(`Canale ${this.o.channel} · ${fmtDate(this.t1)} ${fmtTime(this.t1, false)} → ${fmtDate(this.t1 + this.o.hours * 3600)} ${fmtTime(this.t1 + this.o.hours * 3600, false)} (${utcMode() ? 'UTC' : 'ora locale'})`, g.L, 14);
    ctx.textAlign = 'right';
    ctx.fillText(`mezza riga = ${num(mgPerLine * accFactor(), 2)} ${accUnit()} · guadagno ×${this.o.gain}`, g.w - g.R, 14);
    // griglia minuti
    ctx.textAlign = 'center';
    const tickMin = this.o.lineMinutes <= 15 ? 1 : this.o.lineMinutes <= 30 ? 5 : 10;
    for (let m = 0; m <= this.o.lineMinutes; m += tickMin) {
      const x = g.L + m * 60 / lineSec * g.pw;
      ctx.strokeStyle = gridCol; ctx.lineWidth = m % 5 === 0 ? 1 : 0.5;
      ctx.beginPath(); ctx.moveTo(x, g.T); ctx.lineTo(x, g.H - g.B); ctx.stroke();
      if (m % (tickMin * (this.o.lineMinutes <= 15 ? 5 : 1)) === 0 || m === this.o.lineMinutes) { ctx.fillStyle = label; ctx.fillText(`+${m}`, x, g.H - g.B + 12); }
    }
    // tracce
    const now = Date.now() / 1000;
    for (let li = 0; li < g.nLines; li++) {
      const y0 = g.T + li * g.lh + half, ts = this.t1 + li * lineSec;
      ctx.fillStyle = label; ctx.textAlign = 'right';
      if (g.lh > 9 || li % Math.ceil(10 / g.lh) === 0) ctx.fillText(fmtTime(ts, false), g.L - 8, y0);
      if (ts > now) { ctx.strokeStyle = gridCol; ctx.beginPath(); ctx.moveTo(g.L, y0); ctx.lineTo(g.L + g.pw, y0); ctx.stroke(); continue; }
      ctx.strokeStyle = inkCols[li % inkCols.length]; ctx.lineWidth = 1; ctx.beginPath();
      const base = li * slotsPerLine, perPx = slotsPerLine / g.pw;
      let prevY = null;
      for (let x = 0; x < g.pw; x++) {
        const a = base + Math.floor(x * perPx), b = Math.max(a + 1, base + Math.floor((x + 1) * perPx));
        let mn = EMPTY_MIN, mx = EMPTY_MAX;
        for (let k = a; k < b && k < this.mins.length; k++) {
          if (this.mins[k] === EMPTY_MIN) continue;
          if (this.mins[k] < mn) mn = this.mins[k]; if (this.maxs[k] > mx) mx = this.maxs[k];
        }
        if (mn === EMPTY_MIN) { prevY = null; continue; }
        let yA = y0 - mx * scale, yB = y0 - mn * scale;
        if (this.o.clip) { yA = Math.max(y0 - half * 1.9, yA); yB = Math.min(y0 + half * 1.9, yB); }
        if (prevY != null) { yA = Math.min(yA, prevY); yB = Math.max(yB, prevY); }
        const px = g.L + x + 0.5;
        ctx.moveTo(px, yA); ctx.lineTo(px, yB + 0.6);
        prevY = (yA + yB) / 2;
      }
      ctx.stroke();
    }
    // posizione attuale
    if (this.o.mode === 'live' && now < this.t1 + this.o.hours * 3600) {
      const [x, y] = this._pos(now); ctx.fillStyle = cssVar('--bad'); ctx.beginPath(); ctx.arc(x, y, 3.5, 0, 7); ctx.fill();
    }
    // marcatori eventi locali
    if (this.o.showEvents) for (const e of this.marks.events || []) {
      const [x, y] = this._pos(e.t_trigger); if (x == null) continue;
      const col = e.class === 'sisma_confermato' ? '#dc2626' : e.class === 'possibile_sisma' ? '#d97706' : paper ? '#64748b' : cssVar('--faint');
      ctx.fillStyle = col; ctx.globalAlpha = 0.85;
      ctx.fillRect(x - 1, y - half * 0.95, 2.5, half * 1.9);
      ctx.globalAlpha = 1; ctx.textAlign = 'left'; ctx.fillText(`#${e.id}`, x + 3, y - half * 0.6);
    }
    // arrivi teorici dai cataloghi
    if (this.o.showCatalog) for (const c of this.marks.catalog || []) {
      for (const [t, lab] of [[c.t_p, 'P'], [c.t_s, 'S']]) {
        const [x, y] = this._pos(t); if (x == null) continue;
        ctx.fillStyle = lab === 'P' ? '#7c3aed' : '#0891b2';
        ctx.beginPath(); ctx.moveTo(x, y - half); ctx.lineTo(x - 4, y - half - 7); ctx.lineTo(x + 4, y - half - 7); ctx.fill();
        ctx.textAlign = 'left';
        ctx.fillText(lab === 'P' ? `P M${num(c.mag, 1)} ${(c.place || '').slice(0, 28)}` : 'S', x + 5, y - half - 3);
      }
    }
    ctx.fillStyle = label; ctx.textAlign = 'left';
    ctx.fillText('clic su una riga per aprire il sismogramma', g.L, g.H - 6);
  }
  _pos(t) {
    const g = this._g; if (!g) return [null, null];
    const lineSec = this.o.lineMinutes * 60, rel = t - this.t1;
    if (rel < 0 || rel >= this.o.hours * 3600) return [null, null];
    const li = Math.floor(rel / lineSec);
    return [g.L + (rel - li * lineSec) / lineSec * g.pw, g.T + li * g.lh + g.lh / 2];
  }
  _timeAt(e) {
    const g = this._g; if (!g) return null;
    const r = this.canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    if (x < g.L || x > g.L + g.pw || y < g.T || y > g.H - g.B) return null;
    const li = Math.floor((y - g.T) / g.lh);
    return this.t1 + li * this.o.lineMinutes * 60 + (x - g.L) / g.pw * this.o.lineMinutes * 60;
  }
  _click(e) { const t = this._timeAt(e); if (t != null && this.o.onpick) this.o.onpick(t); }
  _hover(e) {
    const t = this._timeAt(e);
    if (t == null) { this.tip.style.display = 'none'; return; }
    const k = Math.round((t - this.t1) / SLOT);
    const mn = this.mins[k], mx = this.maxs[k];
    const v = mn === EMPTY_MIN ? 'nessun dato' : `${num(Math.max(Math.abs(mn), Math.abs(mx)) / 10 * accFactor(), 2)} ${accUnit()}`;
    const ev = (this.marks.events || []).find((x) => Math.abs(x.t_trigger - t) < 20);
    this.tip.textContent = `${fmtDate(t)} ${fmtTime(t)} · ${v}${ev ? ` · evento #${ev.id}` : ''}`;
    const r = this.canvas.getBoundingClientRect();
    this.tip.style.display = 'block'; this.tip.style.left = (e.clientX - r.left + 12) + 'px'; this.tip.style.top = (e.clientY - r.top - 24) + 'px';
  }
  png() { return this.canvas.toDataURL('image/png'); }
}
