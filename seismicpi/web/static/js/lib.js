// Utilità comuni: API, DOM, formattazione, unità, toast, modali, form dichiarativi.

export const state = {
  cfg: null, meta: null, status: null, admin: false, auth: null,
  listeners: new Set(),
};

export function emit(msg) { for (const fn of state.listeners) { try { fn(msg); } catch (e) { console.error(e); } } }
export function on(fn) { state.listeners.add(fn); return () => state.listeners.delete(fn); }

export async function api(path, opts = {}) {
  const o = { method: opts.method || 'GET', headers: {} };
  if (opts.body !== undefined) { o.body = JSON.stringify(opts.body); o.headers['Content-Type'] = 'application/json'; }
  const r = await fetch(path, o);
  if (opts.raw) { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r; }
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok || (data && data.ok === false)) {
    const msg = (data && (data.error || data.message)) || `HTTP ${r.status}`;
    const e = new Error(msg); e.status = r.status; throw e;
  }
  return data;
}
export const get = (p) => api(p);
export const post = (p, body = {}) => api(p, { method: 'POST', body });
export const patch = (p, body) => api(p, { method: 'PATCH', body });
export const del = (p) => api(p, { method: 'DELETE' });

// ------------------------------------------------------------------ DOM
export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'html') el.innerHTML = v;
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}
export const $ = (s, root = document) => root.querySelector(s);
export function clear(el) { while (el.firstChild) el.firstChild.remove(); return el; }
export function card(title, body, opts = {}) {
  return h('div', { class: 'card ' + (opts.class || '') },
    title ? h('div', { class: 'card-head' }, h('h2', {}, title), opts.actions || null) : null, body);
}

// ------------------------------------------------------------------ formattazione
const pad = (n) => String(n).padStart(2, '0');
export function utcMode() { return state.cfg?.web?.time_display === 'utc'; }
function parts(t) {
  const d = new Date(t * 1000);
  return utcMode()
    ? [d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate(), d.getUTCHours(), d.getUTCMinutes(), d.getUTCSeconds(), d.getUTCDay()]
    : [d.getFullYear(), d.getMonth() + 1, d.getDate(), d.getHours(), d.getMinutes(), d.getSeconds(), d.getDay()];
}
export function fmtTime(t, secs = true) {
  if (t == null) return '–';
  const p = parts(t);
  return `${pad(p[3])}:${pad(p[4])}${secs ? ':' + pad(p[5]) : ''}`;
}
export function fmtDate(t) { if (t == null) return '–'; const p = parts(t); return `${pad(p[2])}/${pad(p[1])}/${p[0]}`; }
export function fmtDT(t, secs = true) { return t == null ? '–' : `${fmtDate(t)} ${fmtTime(t, secs)}`; }
export function fmtMs(t) { const ms = Math.floor((t % 1) * 1000); return `${fmtTime(t)}.${String(ms).padStart(3, '0')}`; }
export function tzLabel() { return utcMode() ? 'UTC' : 'ora locale'; }
export function dayKey(t) { const d = new Date(t * 1000); return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}`; }
export function fmtAgo(t) {
  if (!t) return '–';
  const s = Date.now() / 1000 - t;
  if (s < 60) return `${Math.max(0, Math.round(s))} s fa`;
  if (s < 3600) return `${Math.round(s / 60)} min fa`;
  if (s < 86400) return `${Math.round(s / 3600)} h fa`;
  return `${Math.round(s / 86400)} g fa`;
}
export function fmtDur(s) {
  if (s == null) return '–';
  s = Math.round(s);
  const d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  if (d) return `${d}g ${hh}h`;
  if (hh) return `${hh}h ${m}m`;
  if (m) return `${m}m ${s % 60}s`;
  return `${s}s`;
}
export function fmtBytes(b) {
  if (b == null) return '–';
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b < 10 && i ? 1 : 0)} ${u[i]}`;
}
export function num(v, d = 2) { return v == null || Number.isNaN(v) ? '–' : Number(v).toFixed(d); }

// unità di accelerazione: i dati arrivano sempre in mg
export function units() { return state.cfg?.web?.units || 'mg'; }
export function accFactor(u = units()) { return u === 'gal' ? 0.980665 : u === 'ms2' ? 0.00980665 : 1; }
export function accUnit(u = units()) { return u === 'gal' ? 'cm/s²' : u === 'ms2' ? 'm/s²' : 'mg'; }
export function fmtAcc(mg, withUnit = true) {
  if (mg == null) return '–';
  const u = units(), v = mg * accFactor(u);
  const d = u === 'ms2' ? 4 : v < 1 ? 3 : v < 100 ? 2 : 1;
  return v.toFixed(d) + (withUnit ? ' ' + accUnit(u) : '');
}

const ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X'];
const MMI_COL = ['#ffffff', '#bfccff', '#a0e6ff', '#80ffff', '#7aff93', '#ffff00', '#ffc800', '#ff9100', '#ff0000', '#c80000'];
export function mmiBadge(m) {
  if (m == null) return h('span', { class: 'faint' }, '–');
  const i = Math.max(0, Math.min(9, Math.round(m) - 1));
  return h('span', { class: 'mmi', style: { background: MMI_COL[i] }, title: `MMI ${num(m, 1)}` }, ROMAN[i]);
}
export const mmiRoman = (m) => ROMAN[Math.max(0, Math.min(9, Math.round(m || 1) - 1))];
export function classBadge(c) {
  const desc = state.meta?.classes?.[c] || c;
  const short = { possibile_sisma: 'possibile sisma', sisma_confermato: 'sisma confermato', impulso: 'impulso',
    rumore_locale: 'rumore locale', manuale: 'manuale', test: 'test', sconosciuto: '?' }[c] || c;
  return h('span', { class: `badge c-${c}`, title: desc }, short);
}
export function labelName(l) { return l ? (state.meta?.labels?.[l] || l) : ''; }

// ------------------------------------------------------------------ toast e modali
export function toast(msg, kind = 'info', ms = 4000) {
  const el = h('div', { class: `toast ${kind}` }, msg);
  document.getElementById('toasts').append(el);
  setTimeout(() => el.remove(), ms);
}
export function errToast(e) { toast(e.message || String(e), 'bad', 6000); }

export function modal(title, body, actions = []) {
  return new Promise((resolve) => {
    const root = document.getElementById('modal-root');
    const close = (v) => { bg.remove(); resolve(v); };
    const btns = actions.map((a) => h('button', { class: a.class || '', onclick: async () => {
      if (a.handler) { try { const r = await a.handler(); if (r === false) return; close(r ?? a.value); } catch (e) { errToast(e); } }
      else close(a.value);
    } }, a.label));
    const bg = h('div', { class: 'modal-bg', onclick: (e) => { if (e.target === bg) close(null); } },
      h('div', { class: 'modal' }, h('h2', {}, title), body, h('div', { class: 'form-actions' }, btns)));
    root.append(bg);
    const f = bg.querySelector('input, select, textarea'); if (f) f.focus();
  });
}
export async function confirm(title, text, okLabel = 'Conferma', danger = false) {
  const v = await modal(title, h('p', {}, text), [
    { label: 'Annulla', value: false }, { label: okLabel, value: true, class: danger ? 'danger' : 'primary' }]);
  return !!v;
}

// ------------------------------------------------------------------ form dichiarativi legati alla configurazione
export function cfgGet(path, cfg = state.cfg) { return path.split('.').reduce((o, k) => (o == null ? o : o[k]), cfg); }
function setPath(obj, path, val) {
  const ks = path.split('.'); let o = obj;
  for (const k of ks.slice(0, -1)) o = o[k] ??= {};
  o[ks.at(-1)] = val;
}

// fields: [{path, label, type: number|text|select|check|password|time|textarea|list, help, min, max, step, unit, options, wide}]
export function configForm(fields, opts = {}) {
  const inputs = [];
  const changes = {};
  const dirtyNote = h('span', { class: 'dirty-note' });
  const saveBtn = h('button', { class: 'primary', disabled: !state.admin }, opts.saveLabel || 'Salva');
  const markDirty = () => { dirtyNote.textContent = Object.keys(changes).length ? 'modifiche non salvate' : ''; };
  const form = h('div', { class: 'form' });
  for (const f of fields) {
    if (f.section) { form.append(h('h3', { class: 'field wide' }, f.section)); continue; }
    if (f.note) { form.append(h('div', { class: 'note field wide' }, f.note)); continue; }
    const cur = cfgGet(f.path);
    let input;
    const change = (v) => { changes[f.path] = v; markDirty(); f.onchange?.(v); };
    if (f.type === 'select') {
      input = h('select', { disabled: !state.admin },
        f.options.map(([v, l]) => h('option', { value: String(v), selected: String(v) === String(cur) }, l)));
      input.onchange = () => { const o = f.options.find(([v]) => String(v) === input.value); change(o ? o[0] : input.value); };
    } else if (f.type === 'check') {
      input = h('input', { type: 'checkbox', checked: !!cur, disabled: !state.admin });
      input.onchange = () => change(input.checked);
    } else if (f.type === 'textarea') {
      input = h('textarea', { disabled: !state.admin }, cur ?? '');
      input.oninput = () => change(input.value);
    } else if (f.type === 'list') {
      input = h('input', { type: 'text', value: (cur || []).join(', '), disabled: !state.admin, placeholder: f.placeholder || '' });
      input.oninput = () => change(input.value.split(',').map((s) => s.trim()).filter(Boolean));
    } else if (f.type === 'multi') {
      input = h('div', { class: 'row' }, f.options.map(([v, l]) => {
        const cb = h('input', { type: 'checkbox', checked: (cur || []).includes(v), disabled: !state.admin });
        cb.onchange = () => {
          const set = new Set(changes[f.path] ?? cur ?? []);
          cb.checked ? set.add(v) : set.delete(v);
          change(f.options.map(([x]) => x).filter((x) => set.has(x)));
        };
        return h('label', { class: 'inline' }, cb, l);
      }));
    } else {
      const isNum = f.type === 'number';
      const isPw = f.type === 'password';
      input = h('input', {
        type: isNum ? 'number' : isPw ? 'password' : f.type || 'text', disabled: !state.admin,
        value: isPw ? '' : cur ?? '', min: f.min, max: f.max, step: f.step ?? (isNum ? 'any' : undefined),
        placeholder: isPw ? (cur === '__set__' ? '•••••• (impostato, lascia vuoto per non cambiare)' : 'non impostato') : f.placeholder || '',
        autocomplete: isPw ? 'new-password' : 'off',
      });
      input.oninput = () => {
        if (isPw && !input.value) { delete changes[f.path]; markDirty(); return; }
        change(isNum ? (input.value === '' ? null : Number(input.value)) : input.value);
      };
    }
    inputs.push({ f, input });
    const help = f.help ? h('div', { class: 'help' }, f.help) : null;
    const label = f.label + (f.restart ? ' ⟳' : '');
    if (f.type === 'check') {
      form.append(h('label', { class: 'field check' + (f.wide ? ' wide' : '') }, input, h('div', {}, h('span', {}, label), help)));
    } else {
      const ctl = f.unit ? h('div', { class: 'unit' }, input, h('em', {}, f.unit)) : input;
      form.append(h('label', { class: 'field' + (f.wide ? ' wide' : '') }, h('span', {}, label), ctl, help));
    }
  }
  saveBtn.onclick = async () => {
    if (!Object.keys(changes).length) { toast('Nessuna modifica'); return; }
    const body = {};
    for (const [p, v] of Object.entries(changes)) setPath(body, p, v);
    saveBtn.disabled = true;
    try {
      const r = await patch('/api/config', body);
      state.cfg = r.config;
      for (const k of Object.keys(changes)) delete changes[k];
      markDirty();
      toast('Impostazioni salvate', 'ok');
      opts.onsave?.(r.config);
      emit({ t: 'config_local' });
    } catch (e) { errToast(e); } finally { saveBtn.disabled = !state.admin; }
  };
  const actions = h('div', { class: 'form-actions' }, dirtyNote, opts.extraActions || null, saveBtn);
  return h('div', {}, form, state.admin ? actions : h('p', { class: 'muted' }, 'Accedi come amministratore per modificare.'));
}

export function seg(options, value, onchange) {
  const wrap = h('div', { class: 'seg' });
  const btns = options.map(([v, l]) => {
    const b = h('button', { class: String(v) === String(value) ? 'on' : '' }, l);
    b.onclick = () => { btns.forEach((x) => x.classList.remove('on')); b.classList.add('on'); onchange(v); };
    return b;
  });
  wrap.append(...btns);
  return wrap;
}

export function download(url) { const a = h('a', { href: url, download: '' }); document.body.append(a); a.click(); a.remove(); }

export function storageGet(k, d) { try { const v = localStorage.getItem('spi.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } }
export function storageSet(k, v) { try { localStorage.setItem('spi.' + k, JSON.stringify(v)); } catch { /* privato */ } }

export function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
