// Avvio della dashboard: autenticazione, navigazione, WebSocket, barra di stato.
import { state, api, get, post, h, $, clear, emit, on, toast, errToast, fmtTime, fmtDT, fmtAgo, num, fmtAcc, modal, storageGet, storageSet } from './lib.js';

const ICONS = {
  home: 'M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  live: 'M2 12h4l3-8 4 16 3-10 2 4h4',
  drum: 'M4 5h16M4 9h16M4 13h16M4 17h16M7 5l1 2-1 2 1 2',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  trigger: 'M13 2L4 14h7l-1 8 9-12h-7z',
  chip: 'M7 7h10v10H7zM9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4',
  pin: 'M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11zM12 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  bell: 'M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8M10 21h4',
  wifi: 'M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01',
  cog: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  book: 'M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5zM4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5',
};
export function icon(name) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor');
  s.setAttribute('stroke-width', '1.8'); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path'); p.setAttribute('d', ICONS[name] || ''); s.append(p);
  return s;
}

const PAGES = [
  { sec: 'Monitoraggio' },
  { path: '', title: 'Panoramica', icon: 'home', mod: 'overview' },
  { path: 'live', title: 'Segnale live', icon: 'live', mod: 'live' },
  { path: 'rullo', title: 'Rullo', icon: 'drum', mod: 'drum' },
  { path: 'eventi', title: 'Registro eventi', icon: 'list', mod: 'events' },
  { path: 'evento', title: 'Evento', mod: 'event', hidden: true },
  { path: 'finestra', title: 'Sismogramma', mod: 'window', hidden: true },
  { path: 'cataloghi', title: 'Cataloghi ufficiali', icon: 'globe', mod: 'catalog' },
  { path: 'statistiche', title: 'Statistiche', icon: 'chart', mod: 'stats' },
  { sec: 'Configurazione' },
  { path: 'rilevamento', title: 'Rilevamento', icon: 'trigger', mod: 'trigger' },
  { path: 'sensore', title: 'Sensore', icon: 'chip', mod: 'sensor' },
  { path: 'stazione', title: 'Stazione e posizione', icon: 'pin', mod: 'station' },
  { path: 'allerte', title: 'Allerte', icon: 'bell', mod: 'alerts' },
  { path: 'rete', title: 'Rete', icon: 'wifi', mod: 'network' },
  { path: 'sistema', title: 'Sistema', icon: 'cog', mod: 'system' },
  { sec: 'Aiuto' },
  { path: 'guida', title: 'Guida', icon: 'book', mod: 'guide' },
];

// ------------------------------------------------------------------ WebSocket
let ws = null, wsSubs = new Set(['status', 'event', 'minute', 'alert', 'sensor', 'catalog', 'config']), wsRetry = 0;
export function wsSubscribe(extra = []) {
  wsSubs = new Set(['status', 'event', 'minute', 'alert', 'sensor', 'catalog', 'config', ...extra]);
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ sub: [...wsSubs] }));
}
function wsConnect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { wsRetry = 0; ws.send(JSON.stringify({ sub: [...wsSubs] })); setConn(true); };
  ws.onmessage = (m) => {
    let msg; try { msg = JSON.parse(m.data); } catch { return; }
    if (msg.t === 'status') { state.acq = msg; renderTop(); }
    if (msg.t === 'alert') toast(msg.text, msg.kind === 'event' || msg.kind === 'early' ? 'warn' : 'info', 8000);
    if (msg.t === 'event' && msg.phase === 'end') {
      const e = msg.event;
      toast(`Evento #${e.id}: ${state.meta?.classes?.[e.class] || e.class}\nPGA ${fmtAcc(e.pga)} · ${num(e.duration, 1)} s`, 'warn', 7000);
    }
    if (msg.t === 'config') get('/api/config').then((c) => { state.cfg = c; }).catch(() => {});
    emit(msg);
  };
  ws.onclose = () => { setConn(false); setTimeout(wsConnect, Math.min(10000, 1000 * 2 ** wsRetry++)); };
}
function setConn(ok) { state.wsOk = ok; renderTop(); }

// ------------------------------------------------------------------ barra superiore
function pill(cls, text, title) { return h('span', { class: `pill ${cls}`, title: title || '' }, h('span', { class: 'dot' }), text); }
function renderTop() {
  const el = $('#top-status'); if (!el) return;
  const a = state.acq, st = state.status;
  const pills = [];
  if (!state.wsOk) pills.push(pill('bad', 'disconnesso'));
  if (a) {
    pills.push(a.sensor_ok ? pill('ok', `${a.driver === 'sim' ? 'SIMULATO' : 'sensore'} ${num(a.fs, 1)} Hz`) : pill('bad', 'sensore assente', a.sensor_error));
    if (a.state !== 'idle') pills.push(pill('live', `EVENTO in corso · ${fmtAcc(a.event?.pga)}`));
    else pills.push(pill(a.ratio > (a.off_ratio || 1.5) ? 'warn' : 'ok', `STA/LTA ${num(a.ratio, 1)}`));
    if (a.minute) pills.push(pill('', `rumore ${fmtAcc(a.minute.rms_v)}`));
  }
  const net = st?.network;
  if (net?.mode === 'ap') pills.push(pill('warn', `Access Point ${net.ap_ssid || ''}`));
  if (st?.alerts?.muted_until > Date.now() / 1000) pills.push(pill('warn', 'allerte sospese'));
  el.replaceChildren(...pills);
  renderBanner();
}
function renderBanner() {
  const b = $('#banner'), a = state.acq, st = state.status, msgs = [];
  let warn = true;
  if (st && !st.acq_connected) { msgs.push('Il servizio di acquisizione non risponde.'); warn = false; }
  else if (a && !a.sensor_ok) { msgs.push(`Sensore non disponibile: ${a.sensor_error || 'errore sconosciuto'} – controlla il cablaggio (Sensore → Diagnostica I2C).`); warn = false; }
  if (a && a.clock_synced === false) msgs.push('Orologio non sincronizzato via NTP: gli orari potrebbero essere sbagliati. In modalità Access Point puoi impostare l\'ora dal browser in Sistema.');
  if (a && a.warmup < 1) msgs.push(`Avvio del trigger: calcolo del rumore di fondo (LTA) al ${Math.round(a.warmup * 100)}%.`);
  b.className = warn ? 'warn' : '';
  b.textContent = msgs.join('  ·  ');
}

async function refreshStatus() {
  try { state.status = await get('/api/status'); state.admin = state.status.admin; renderTop(); renderSide(); }
  catch (e) { if (e.status === 401) location.reload(); }
}

function renderSide() {
  const st = state.status; if (!st) return;
  const s = st.system || {};
  $('#side-foot').replaceChildren(
    h('div', {}, `v${st.version} · ${s.hostname || ''}`),
    h('div', {}, `CPU ${num(s.cpu_temp, 0)} °C · disco ${num(s.disk_pct, 0)}%`),
    h('div', {}, `eventi 24h: ${st.events_24h}`));
  const lb = $('#login-btn');
  lb.textContent = state.admin ? 'Esci' : 'Accedi';
}

// ------------------------------------------------------------------ navigazione
let cleanup = null, current = null;
function buildNav() {
  const nav = $('#nav'); clear(nav);
  for (const p of PAGES) {
    if (p.sec) { nav.append(h('div', { class: 'sec' }, p.sec)); continue; }
    if (p.hidden) continue;
    nav.append(h('a', { href: '#/' + p.path, 'data-path': p.path }, icon(p.icon), p.title));
  }
}
async function route() {
  const hash = location.hash.replace(/^#\/?/, '');
  const [path, ...rest] = hash.split('/');
  const page = PAGES.find((p) => !p.sec && p.path === path.split('?')[0]) || PAGES[1];
  document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.path === page.path || (page.path === 'evento' && a.dataset.path === 'eventi')));
  $('#sidebar').classList.remove('open');
  if (cleanup) { try { cleanup(); } catch (e) { console.error(e); } cleanup = null; }
  wsSubscribe([]);
  const view = clear($('#view'));
  $('#page-title').textContent = page.title;
  document.title = `${page.title} · ${state.cfg?.station?.name || 'SeismicPi'}`;
  current = page;
  const params = new URLSearchParams(hash.includes('?') ? hash.split('?')[1] : '');
  try {
    const mod = await import(`./pages/${page.mod}.js`);
    if (current !== page) return;
    cleanup = (await mod.default(view, rest.map((r) => r.split('?')[0]), params)) || null;
  } catch (e) {
    console.error(e);
    view.append(h('div', { class: 'note bad' }, `Errore nel caricamento della pagina: ${e.message}`));
  }
}

// ------------------------------------------------------------------ accesso
async function loginDialog() {
  const user = h('input', { type: 'text', placeholder: 'utente', value: 'admin', autocomplete: 'username' });
  const pw = h('input', { type: 'password', placeholder: 'password', autocomplete: 'current-password' });
  const doLogin = async () => { await post('/api/auth/login', { username: user.value, password: pw.value }); location.reload(); };
  pw.onkeydown = (e) => { if (e.key === 'Enter') doLogin().catch(errToast); };
  await modal('Accesso amministratore', h('div', { class: 'login-fields' }, user, pw), [
    { label: 'Annulla', value: null }, { label: 'Accedi', class: 'primary', handler: doLogin }]);
}
function setupScreen(auth) {
  const user = h('input', { type: 'text', placeholder: 'nome utente', value: 'admin', autocomplete: 'username' });
  const pw = h('input', { type: 'password', placeholder: 'nuova password (min. 6 caratteri)', autocomplete: 'new-password' });
  const pw2 = h('input', { type: 'password', placeholder: 'ripeti la password', autocomplete: 'new-password' });
  const go = async () => {
    if (pw.value !== pw2.value) { toast('Le password non coincidono', 'bad'); return; }
    try { await post('/api/auth/setup', { username: user.value, password: pw.value }); location.reload(); } catch (e) { errToast(e); }
  };
  document.body.replaceChildren(h('div', { class: 'card login-box stack' },
    h('h2', {}, `Benvenuto in ${auth.station}`),
    h('p', { class: 'muted' }, 'Primo avvio: scegli la password di amministrazione della dashboard. Servirà per modificare impostazioni, rete e allerte.'),
    user, pw, pw2, h('button', { class: 'primary', onclick: go }, 'Imposta e continua')), h('div', { id: 'toasts' }));
}
function loginScreen(auth) {
  const user = h('input', { type: 'text', placeholder: 'utente', value: 'admin', autocomplete: 'username' });
  const pw = h('input', { type: 'password', placeholder: 'password', autocomplete: 'current-password' });
  const go = async () => { try { await post('/api/auth/login', { username: user.value, password: pw.value }); location.reload(); } catch (e) { errToast(e); } };
  pw.onkeydown = (e) => { if (e.key === 'Enter') go(); };
  document.body.replaceChildren(h('div', { class: 'card login-box stack' },
    h('h2', {}, auth.station), h('p', { class: 'muted' }, 'Accesso alla dashboard del sismografo.'),
    user, pw, h('button', { class: 'primary', onclick: go }, 'Accedi')), h('div', { id: 'toasts' }));
  pw.focus();
}

// ------------------------------------------------------------------ avvio
function applyTheme(t) { document.documentElement.dataset.theme = t; storageSet('theme', t); }

async function boot() {
  applyTheme(storageGet('theme', 'dark'));
  const auth = await get('/api/auth');
  if (auth.setup_needed) return setupScreen(auth);
  if (!auth.logged_in && !auth.public_readonly) return loginScreen(auth);
  state.admin = auth.logged_in;
  [state.cfg, state.meta] = await Promise.all([get('/api/config'), get('/api/meta')]);
  $('#brand-name').textContent = state.cfg.station.name;
  $('#brand-sub').textContent = `${state.cfg.station.network}.${state.cfg.station.code} · sismografo`;
  buildNav();
  $('#menu-btn').onclick = () => $('#sidebar').classList.toggle('open');
  $('#theme-btn').onclick = () => { applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'); emit({ t: 'theme' }); };
  $('#login-btn').onclick = async () => {
    if (state.admin) { await post('/api/auth/logout'); location.reload(); } else loginDialog();
  };
  setInterval(() => { $('#clock').textContent = fmtTime(Date.now() / 1000); }, 1000);
  await refreshStatus();
  setInterval(refreshStatus, 15000);
  wsConnect();
  window.addEventListener('hashchange', route);
  route();
}

on((m) => { if (m.t === 'config_local') { $('#brand-name').textContent = state.cfg.station.name; } });
boot().catch((e) => { document.body.textContent = 'Errore di avvio: ' + e.message; });
