// Elaborazione nel browser: filtri, FFT, spettri, spettrogramma. Il Pi Zero non ha CPU da
// sprecare: tutto ciò che serve solo per guardare i dati si calcola qui.

export class Biquad {
  constructor(kind, fc, fs, q = Math.SQRT1_2) {
    const w0 = 2 * Math.PI * fc / fs, cw = Math.cos(w0), sw = Math.sin(w0), al = sw / (2 * q);
    let b0, b1, b2;
    if (kind === 'lp') { b0 = (1 - cw) / 2; b1 = 1 - cw; b2 = b0; }
    else { b0 = (1 + cw) / 2; b1 = -(1 + cw); b2 = b0; }
    const a0 = 1 + al;
    this.b = [b0 / a0, b1 / a0, b2 / a0]; this.a = [-2 * cw / a0, (1 - al) / a0];
    this.x1 = this.x2 = this.y1 = this.y2 = 0;
  }
  prime(x) {
    const [b0, b1, b2] = this.b, [a1, a2] = this.a;
    this.x1 = this.x2 = x; this.y1 = this.y2 = x * (b0 + b1 + b2) / (1 + a1 + a2);
  }
  step(x) {
    const [b0, b1, b2] = this.b, [a1, a2] = this.a;
    const y = b0 * x + b1 * this.x1 + b2 * this.x2 - a1 * this.y1 - a2 * this.y2;
    this.x2 = this.x1; this.x1 = x; this.y2 = this.y1; this.y1 = y;
    return y;
  }
}

// filtro: {hp: Hz|0, lp: Hz|0}; zeroPhase applica il filtro avanti e indietro (niente ritardo)
export function filter(arr, fs, { hp = 0, lp = 0, zeroPhase = false, order = 2 } = {}) {
  const stages = [];
  for (let i = 0; i < order / 2; i++) {
    if (hp > 0 && hp < fs / 2) stages.push(() => new Biquad('hp', hp, fs));
    if (lp > 0 && lp < fs / 2) stages.push(() => new Biquad('lp', lp, fs));
  }
  const run = (src) => {
    const first = src.find((v) => v != null) ?? 0;     // ogni passata parte dal proprio primo valore
    const out = new Array(src.length);
    const fl = stages.map((s) => { const f = s(); f.prime(first); return f; });
    let last = first;
    for (let i = 0; i < src.length; i++) {
      let x = src[i];
      if (x == null) { out[i] = null; continue; }
      last = x;
      for (const f of fl) x = f.step(x);
      out[i] = x;
    }
    return out;
  };
  let out = run(arr);
  if (zeroPhase && stages.length) out = run(out.slice().reverse()).reverse();
  if (!stages.length) {
    const vals = arr.filter((v) => v != null);
    const mean = vals.reduce((a, b) => a + b, 0) / (vals.length || 1);
    out = arr.map((v) => (v == null ? null : v - mean));
  }
  return out;
}

export function integrate(arr, fs, hp = 0.2) {
  // accelerazione (mg) → velocità (mm/s), con passa-alto contro la deriva
  const dt = 1 / fs, k = 9.80665;
  const out = new Array(arr.length); let v = 0, prev = 0;
  const f = new Biquad('hp', hp, fs);
  for (let i = 0; i < arr.length; i++) {
    const a = arr[i] == null ? 0 : arr[i] * k / 1000;      // m/s²
    v += (a + prev) / 2 * dt; prev = a;
    out[i] = f.step(v) * 1000;                               // mm/s
  }
  return out;
}

export function fft(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = -2 * Math.PI / len, wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cr = 1, ci = 0;
      for (let j = 0; j < len / 2; j++) {
        const ar = re[i + j], ai = im[i + j];
        const br = re[i + j + len / 2] * cr - im[i + j + len / 2] * ci;
        const bi = re[i + j + len / 2] * ci + im[i + j + len / 2] * cr;
        re[i + j] = ar + br; im[i + j] = ai + bi;
        re[i + j + len / 2] = ar - br; im[i + j + len / 2] = ai - bi;
        const t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t;
      }
    }
  }
}

const hann = (n) => Array.from({ length: n }, (_, i) => 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (n - 1)));

// spettro di ampiezza (Welch): restituisce {f: [...], a: [...]} in unità del segnale
export function spectrum(arr, fs, nfft = 1024) {
  const data = arr.map((v) => v ?? 0);
  nfft = Math.min(nfft, 1 << Math.floor(Math.log2(Math.max(16, data.length))));
  const w = hann(nfft), wsum = w.reduce((a, b) => a + b, 0);
  const acc = new Float64Array(nfft / 2 + 1); let segs = 0;
  for (let s = 0; s + nfft <= data.length; s += nfft / 2) {
    const re = new Float64Array(nfft), im = new Float64Array(nfft);
    let mean = 0; for (let i = 0; i < nfft; i++) mean += data[s + i]; mean /= nfft;
    for (let i = 0; i < nfft; i++) re[i] = (data[s + i] - mean) * w[i];
    fft(re, im);
    for (let k = 0; k <= nfft / 2; k++) acc[k] += Math.hypot(re[k], im[k]) * 2 / wsum;
    segs++;
  }
  const f = [], a = [];
  for (let k = 1; k <= nfft / 2; k++) { f.push(k * fs / nfft); a.push(acc[k] / Math.max(1, segs)); }
  return { f, a };
}

// spettrogramma: matrice [colonne tempo][righe frequenza] in dB
export function spectrogram(arr, fs, nfft = 128, hop = 32) {
  const data = arr.map((v) => v ?? 0);
  const w = hann(nfft), cols = [];
  for (let s = 0; s + nfft <= data.length; s += hop) {
    const re = new Float64Array(nfft), im = new Float64Array(nfft);
    let mean = 0; for (let i = 0; i < nfft; i++) mean += data[s + i]; mean /= nfft;
    for (let i = 0; i < nfft; i++) re[i] = (data[s + i] - mean) * w[i];
    fft(re, im);
    const col = new Float32Array(nfft / 2);
    for (let k = 0; k < nfft / 2; k++) col[k] = 20 * Math.log10(Math.hypot(re[k], im[k]) + 1e-9);
    cols.push(col);
  }
  return { cols, nfft, hop, fs };
}

// STA/LTA classico, utile per rivedere il trigger sugli eventi
export function stalta(arr, fs, sta = 0.6, lta = 30) {
  const cs = 1 / (sta * fs), cl = 1 / (lta * fs);
  let s = 0, l = 0; const out = new Array(arr.length); const warm = Math.min(arr.length, lta * fs);
  for (let i = 0; i < arr.length; i++) {
    const x = arr[i] ?? 0, cf = x * x;
    s += (cf - s) * cs; l += (cf - l) * cl;
    out[i] = i < warm * 0.3 ? null : l > 0 ? s / l : 0;
  }
  return out;
}

export function peak(arr) { let m = 0; for (const v of arr) if (v != null && Math.abs(v) > m) m = Math.abs(v); return m; }
export function rms(arr) { let s = 0, n = 0; for (const v of arr) if (v != null) { s += v * v; n++; } return n ? Math.sqrt(s / n) : 0; }

// colormap viridis approssimata (0..1 → [r,g,b])
const VIR = [[68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142], [31, 158, 137], [53, 183, 121], [109, 205, 89], [180, 222, 44], [253, 231, 37]];
export function viridis(t) {
  t = Math.max(0, Math.min(1, t)) * (VIR.length - 1);
  const i = Math.floor(t), f = t - i, a = VIR[i], b = VIR[Math.min(VIR.length - 1, i + 1)];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}
