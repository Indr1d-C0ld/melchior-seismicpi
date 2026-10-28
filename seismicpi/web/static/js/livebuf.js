// Buffer circolare dei dati live (mg, già filtrati passa-alto dal Pi) ricevuti via WebSocket.
import { on } from './lib.js';
import { wsSubscribe } from './app.js';

export class LiveBuffer {
  constructor(seconds = 60, fs = 100) {
    this.seconds = seconds; this.resize(fs);
    this.ratio = []; this.ratioT = [];
    this.off = on((m) => { if (m.t === 'live') this.push(m); });
    wsSubscribe(['live']);
  }
  resize(fs) {
    this.fs = fs; this.cap = Math.ceil(this.seconds * fs) + 50;
    this.t = new Float64Array(this.cap); this.e = new Float32Array(this.cap); this.n = new Float32Array(this.cap); this.z = new Float32Array(this.cap);
    this.len = 0; this.head = 0;
  }
  push(m) {
    if (Math.abs(m.fs - this.fs) > 5) this.resize(Math.round(m.fs));
    const d = m.d, k = d.length / 3, dt = 1 / m.fs;
    if (this.len && m.t0 - this.lastT > 2) this.gap = true;
    for (let i = 0; i < k; i++) {
      const p = this.head;
      this.t[p] = m.t0 + i * dt; this.e[p] = d[3 * i]; this.n[p] = d[3 * i + 1]; this.z[p] = d[3 * i + 2];
      this.head = (p + 1) % this.cap; if (this.len < this.cap) this.len++;
    }
    this.lastT = m.t0 + (k - 1) * dt;
    this.ratio.push(m.r); this.ratioT.push(m.t0);
    while (this.ratioT.length && this.ratioT[0] < this.lastT - this.seconds) { this.ratio.shift(); this.ratioT.shift(); }
    this.dirty = true;
  }
  // ultimi `secs` secondi come array ordinati
  view(secs = this.seconds) {
    const n = Math.min(this.len, Math.ceil(secs * this.fs));
    const t = new Array(n), e = new Array(n), nn = new Array(n), z = new Array(n), v = new Array(n);
    const start = (this.head - n + this.cap) % this.cap;
    for (let i = 0; i < n; i++) {
      const p = (start + i) % this.cap;
      t[i] = this.t[p]; e[i] = this.e[p]; nn[i] = this.n[p]; z[i] = this.z[p];
      v[i] = Math.hypot(e[i], nn[i], z[i]);
    }
    return { t, e, n: nn, z, v };
  }
  close() { this.off(); wsSubscribe([]); }
}
