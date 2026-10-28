import { state, get, post, del, h, card, configForm, modal, fmtDT, fmtAgo, fmtBytes, fmtDur, num, toast, errToast, confirm, download, seg } from '../lib.js';

const kv = (pairs) => h('div', { class: 'kv' }, pairs.filter(Boolean).flatMap(([k, v]) => [h('span', {}, k), h('span', {}, v ?? '–')]));
const meter = (pct, warn = 75, bad = 90) => h('div', { class: 'meter' }, h('i', { style: { width: Math.min(100, pct || 0) + '%', background: pct >= bad ? 'var(--bad)' : pct >= warn ? 'var(--warn)' : 'var(--accent)' } }));
const SD_MAKERS = { '0x000003': 'SanDisk', '0x00001b': 'Samsung', '0x000027': 'Phison', '0x000028': 'Lexar', '0x000074': 'Transcend', '0x00009f': 'Kingston (?)', '0x000041': 'Kingston', '0x00001d': 'AData', '0x000002': 'Kingston/Toshiba', '0x000013': 'Micron' };

export default async function (root) {
  const tabsEl = h('div', { class: 'tabs' }), body = h('div');
  let timer = null, tab = 'hw';
  const show = (t) => { tab = t; clearInterval(timer); [...tabsEl.children].forEach((b) => b.classList.toggle('on', b.dataset.t === t)); render(); };
  for (const [k, l] of [['hw', 'Hardware e telemetria'], ['sd', 'Archiviazione e SD'], ['svc', 'Servizi e registri'], ['sec', 'Accesso e sicurezza'], ['bk', 'Backup'], ['pw', 'Alimentazione e orologio']]) {
    tabsEl.append(h('button', { 'data-t': k, onclick: () => show(k) }, l));
  }
  root.append(tabsEl, body);

  async function hw() {
    const [x, sys] = await Promise.all([get('/api/system/hardware'), get('/api/system')]);
    const thr = x.throttled, vc = x.vc || {}, sd = x.sd, tel = x.telemetry || {}, mem = x.mem;
    const thrBox = thr ? h('div', { class: thr.undervoltage_now || thr.throttled_now ? 'note bad' : thr.undervoltage_past || thr.throttled_past ? 'note warn' : 'note ok' },
      thr.undervoltage_now ? '⚡ Sottotensione ADESSO: l\'alimentatore non basta, rischio di corruzione della SD e di letture sbagliate.' :
        thr.throttled_now ? 'CPU rallentata (throttling) in questo momento.' :
          thr.undervoltage_past || thr.throttled_past ? `Dall'avvio si sono verificati: ${[thr.undervoltage_past && 'sottotensione', thr.throttled_past && 'throttling'].filter(Boolean).join(', ')}.` :
            'Alimentazione regolare, nessun throttling dall\'avvio.', h('span', { class: 'muted' }, ` (codice ${thr.raw})`)) : h('div', { class: 'note' }, 'vcgencmd non disponibile: dati di alimentazione non leggibili.');
    body.replaceChildren(h('div', { class: 'stack' }, thrBox,
      h('div', { class: 'grid g4' },
        h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'Temperatura SoC'), h('span', { class: 'value' }, num(x.temp, 1), h('small', {}, ' °C')), meter(x.temp, 65, 75)),
        h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'CPU'), h('span', { class: 'value' }, num(tel.cpu_pct, 0), h('small', {}, ' %')), h('span', { class: 'sub' }, `${x.freq.cur_mhz || '?'} MHz · carico ${x.load.map((v) => num(v, 2)).join(' / ')}`)),
        h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'RAM'), h('span', { class: 'value' }, num(mem.pct, 0), h('small', {}, ' %')), h('span', { class: 'sub' }, `${fmtBytes(mem.available)} liberi di ${fmtBytes(mem.total)}`), meter(mem.pct)),
        h('div', { class: 'card stat' }, h('span', { class: 'label' }, 'Scritture SD (ultimo minuto)'), h('span', { class: 'value' }, tel.sd_write_kb_min != null ? num(tel.sd_write_kb_min, 0) : '–', h('small', {}, ' KB')), h('span', { class: 'sub' }, sd ? `${fmtBytes(sd.written_bytes)} scritti dall'avvio` : ''))),
      h('div', { class: 'grid g3' },
        card('Scheda', kv([['Modello', x.model], ['SoC / CPU', x.cpu.hardware || x.cpu.model_name], ['Revisione', x.cpu.revision], ['Seriale', x.cpu.serial], ['Architettura', `${x.cpu.arch} · ${x.cpu.cores} core`],
          ['Frequenza', `${x.freq.cur_mhz ?? '?'} MHz (min ${x.freq.min_mhz ?? '?'} · max ${x.freq.max_mhz ?? '?'}) · ${x.freq.governor || ''}`],
          ['Sistema', sys.os], ['Kernel', sys.kernel], ['Python', sys.python], ['Firmware', vc.firmware], ['Acceso da', fmtDur(x.uptime)]])),
        card('VideoCore: tensioni, clock, memoria', kv([['Tensione core', vc.volt_core], ['Tensione SDRAM c/i/p', [vc.volt_sdram_c, vc.volt_sdram_i, vc.volt_sdram_p].filter(Boolean).join(' / ')],
          ['Clock ARM', vc.clock_arm_mhz && `${vc.clock_arm_mhz} MHz`], ['Clock core', vc.clock_core_mhz && `${vc.clock_core_mhz} MHz`], ['Clock eMMC/SD', vc.clock_emmc_mhz && `${vc.clock_emmc_mhz} MHz`],
          ['Memoria ARM / GPU', `${vc.mem_arm || '?'} / ${vc.mem_gpu || '?'}`], ['Swap', `${fmtBytes(mem.swap_total - mem.swap_free)} usati di ${fmtBytes(mem.swap_total)}`]])),
        card('Scheda microSD', sd ? kv([['Modello', sd.name], ['Produttore', SD_MAKERS[sd.manfid] || sd.manfid], ['OEM', sd.oemid], ['Data di produzione', sd.date], ['Capacità', fmtBytes(sd.size_bytes)],
          ['Scritto dall\'avvio', `${fmtBytes(sd.written_bytes)} (${sd.writes} operazioni)`], ['Letto dall\'avvio', fmtBytes(sd.read_bytes)],
          ['Media scritture', `${fmtBytes(sd.written_bytes / Math.max(1, x.uptime) * 86400)}/giorno`], ['Disco dati', `${num(sys.disk.pct, 0)}% usato · ${fmtBytes(sys.disk.free)} liberi`]]) : h('div', { class: 'muted' }, 'Nessuna scheda SD (mmcblk0) rilevata: probabilmente non è un Raspberry.'))),
      h('div', { class: 'grid g3' },
        card('Rete', kv(Object.entries(x.net).map(([k, v]) => [k, `↓ ${fmtBytes(v.rx_bytes)} · ↑ ${fmtBytes(v.tx_bytes)}${v.rx_err + v.tx_err ? ` · errori ${v.rx_err + v.tx_err}` : ''}`]).concat([
          ['Traffico ultimo minuto', tel.net_rx_kb_min != null ? `↓ ${num(tel.net_rx_kb_min, 1)} KB · ↑ ${num(tel.net_tx_kb_min, 1)} KB` : null]]))),
        card('Dispositivi USB e I2C', h('div', {}, h('pre', {}, (x.usb || []).join('\n') || 'lsusb non disponibile'), h('div', { class: 'muted' }, `Bus I2C: ${x.i2c.join(', ') || 'nessuno (abilita I2C)'}`))),
        card('Processi del sismografo', h('table', {}, h('tbody', {}, x.processes.map((p) => h('tr', {}, h('td', { class: 'mono' }, p.pid), h('td', { class: 'wrap mono' }, p.cmd.replace(/.*python3? -m /, '')), h('td', { class: 'num' }, fmtBytes(p.rss)), h('td', { class: 'num' }, `${num(p.cpu_s, 0)} s CPU`))))))),
      h('p', { class: 'muted' }, 'Aggiornamento automatico ogni 10 secondi. Lo storico di temperature, CPU, scritture su SD e rete è in Statistiche → Sistema e telemetria.')));
  }

  async function sdTab() {
    const s = await get('/api/stats/storage');
    const fields = [
      { path: 'storage.raw_mode', label: 'Dati grezzi continui', type: 'select', options: [['ram', 'in RAM (protegge la SD, ultime ore)'], ['sd', 'su SD (storico lungo, ~52 MB/giorno)'], ['off', 'disattivati (solo rullo ed eventi)']],
        help: 'Sono i campioni originali di tutto il giorno: servono per rivedere una finestra qualsiasi del rullo. Le forme d\'onda degli eventi vengono salvate comunque su SD.' },
      { path: 'storage.raw_ram_hours', label: 'Ore tenute in RAM', type: 'number', unit: 'ore', min: 1, max: 72, help: '≈2,2 MB per ora a 100 Hz. Il Pi Zero ha 512 MB: fino a 12-24 ore restano ragionevoli.' },
      { path: 'storage.raw_retention_days', label: 'Conservazione su SD', type: 'number', unit: 'giorni', help: 'Solo con dati grezzi su SD.' },
      { path: 'storage.flush_interval_s', label: 'Scrittura su SD a blocchi ogni', type: 'number', unit: 's', help: 'Blocchi più grandi = meno cicli di scrittura (e più dati persi in caso di blackout).' },
      { path: 'storage.heli_flush_s', label: 'Salvataggio del rullo ogni', type: 'number', unit: 's' },
      { path: 'storage.stats_batch_minutes', label: 'Statistiche scritte a gruppi di', type: 'number', unit: 'minuti' },
      { path: 'storage.helicorder_retention_days', label: 'Conservazione del rullo', type: 'number', unit: 'giorni', help: '2,8 MB per giorno.' },
      { path: 'storage.stats_retention_days', label: 'Conservazione statistiche', type: 'number', unit: 'giorni' },
      { path: 'storage.min_free_mb', label: 'Spazio libero minimo', type: 'number', unit: 'MB', help: 'Sotto questa soglia si eliminano i dati grezzi più vecchi.' },
    ];
    const purge = (what, label, extra = {}) => h('button', { class: 'small danger', disabled: !state.admin, onclick: async () => {
      if (!await confirm(label, 'L\'operazione non si può annullare.', 'Elimina', true)) return;
      try { const r = await post('/api/system/purge', { what, ...extra }); toast(`Eliminati: ${r.removed.join(', ') || 'nulla'}`, 'ok'); render(); } catch (e) { errToast(e); }
    } }, label);
    body.replaceChildren(h('div', { class: 'grid g2' },
      card('Archiviazione e protezione della SD', configForm(fields)),
      h('div', { class: 'stack' },
        card('Occupazione', kv([['Dati grezzi', `${fmtBytes(s.raw_total)} (${s.raw_mode})`], ['Rullo', fmtBytes(s.heli)], ['Eventi', fmtBytes(s.events)], ['Database', fmtBytes(s.db)],
          ['SD', `${fmtBytes(s.disk.used)} usati di ${fmtBytes(s.disk.total)}`], ['RAM condivisa', s.ram ? `${fmtBytes(s.ram.used)} di ${fmtBytes(s.ram.total)}` : '–'],
          ['Scritture stimate del sismografo', `${fmtBytes(s.sd_writes_per_day.total)}/giorno + eventi`]])),
        card('Cosa fa l\'installazione per la SD', h('ul', { class: 'muted' },
          h('li', {}, 'registro di sistema (journald) solo in RAM, massimo 16 MB'), h('li', {}, '/tmp in RAM (tmpfs)'),
          h('li', {}, 'swap su SD disattivato (resta lo zram compresso in RAM, se presente)'), h('li', {}, 'filesystem montato con noatime e commit ogni 120 s'),
          h('li', {}, 'timer apt e man-db giornalieri disattivati'), h('li', {}, 'file di stato e socket in /run (RAM)'),
          h('li', {}, 'dati grezzi in RAM, rullo e statistiche scritti a blocchi'))),
        card('Pulizia manuale', h('div', {},
          h('div', { class: 'row' }, purge('raw', 'Elimina dati grezzi', { keep_days: 0 }), purge('events_noise', 'Elimina eventi di rumore non etichettati'),
            purge('stats', 'Azzera statistiche'), purge('log', 'Svuota registro')),
          h('div', { class: 'note warn', style: { marginTop: '12px' } },
            h('b', {}, 'Azzera tutti i dati acquisiti. '),
            'Elimina eventi e forme d\'onda, rullo, dati grezzi, statistiche, telemetria, registro e coda delle notifiche: utile dopo la fase di installazione e prova. Restano configurazione, password, sessioni, reti Wi-Fi e cataloghi ufficiali.',
            h('div', { class: 'row', style: { marginTop: '8px' } }, h('button', { class: 'danger', disabled: !state.admin, onclick: async () => {
              const inp = h('input', { placeholder: 'AZZERA' });
              const ok = await modal('Azzerare tutti i dati acquisiti?', h('div', { class: 'stack' },
                h('p', {}, 'Tutti gli eventi, il rullo, i dati grezzi e le statistiche verranno eliminati definitivamente. Per confermare scrivi AZZERA.'), inp),
                [{ label: 'Annulla', value: false }, { label: 'Elimina tutto', class: 'danger', value: true }]);
              if (!ok) return;
              if (inp.value.trim() !== 'AZZERA') { toast('Conferma non corretta: nulla è stato eliminato', 'warn'); return; }
              try { const r = await post('/api/system/purge', { what: 'all', confirm: 'AZZERA' }); toast(`Eliminati: ${r.removed.join(', ')}`, 'ok', 8000); render(); } catch (e) { errToast(e); }
            } }, 'Azzera tutti i dati acquisiti'))))))));
  }

  async function svc() {
    const sys = await get('/api/system');
    const unitSel = h('select', {}, ['seismicpi-acq', 'seismicpi-web', 'seismicpi-netwatch', 'NetworkManager', 'kernel'].map((u) => h('option', { value: u }, u)));
    const pre = h('pre', {}, '');
    const loadJ = async () => { try { const r = await get(`/api/system/journal?unit=${unitSel.value}&lines=300`); pre.textContent = r.text || '(vuoto)'; pre.scrollTop = pre.scrollHeight; } catch (e) { pre.textContent = e.message; } };
    unitSel.onchange = loadJ;
    const logs = await get('/api/log?limit=200');
    body.replaceChildren(h('div', { class: 'stack' },
      card('Servizi', h('table', {}, h('thead', {}, h('tr', {}, ['Servizio', 'Stato', 'Avviato', 'Riavvii', ''].map((x) => h('th', {}, x)))),
        h('tbody', {}, Object.entries(sys.services).map(([n, v]) => h('tr', {}, h('td', { class: 'mono' }, n),
          h('td', {}, h('span', { class: `pill ${v.ActiveState === 'active' ? 'ok' : 'bad'}` }, h('span', { class: 'dot' }), `${v.ActiveState || '?'} (${v.SubState || '?'})`)),
          h('td', {}, v.ActiveEnterTimestamp || '–'), h('td', {}, v.NRestarts ?? '–'),
          h('td', {}, state.admin ? h('button', { class: 'small', onclick: async () => { await post('/api/system/power', { action: 'restart_' + n.split('-')[1] }); toast(`${n} in riavvio`, 'ok'); } }, 'Riavvia') : null)))))),
      card('Registro del sismografo', h('div', { class: 'table-wrap', style: { maxHeight: '360px' } }, h('table', {}, h('tbody', {}, logs.map((l) => h('tr', {}, h('td', {}, fmtDT(l.t)),
        h('td', {}, h('span', { class: `badge ${l.level === 'error' ? 'c-sisma_confermato' : l.level === 'warning' ? 'c-possibile_sisma' : ''}` }, l.level)), h('td', {}, l.source), h('td', { class: 'wrap' }, l.message))))))),
      state.admin ? card('Journal di sistema', h('div', { class: 'stack' }, h('div', { class: 'row' }, unitSel, h('button', { class: 'small', onclick: loadJ }, '⟳ Aggiorna')), pre)) : null));
    if (state.admin) loadJ();
  }

  async function sec() {
    const old = h('input', { type: 'password', autocomplete: 'current-password' }), nw = h('input', { type: 'password', autocomplete: 'new-password' }), nw2 = h('input', { type: 'password', autocomplete: 'new-password' });
    const sess = state.admin ? await get('/api/sessions') : [];
    body.replaceChildren(h('div', { class: 'grid g2' },
      card('Cambia password', h('div', { class: 'form' }, h('label', { class: 'field' }, h('span', {}, 'Password attuale'), old), h('label', { class: 'field' }, h('span', {}, 'Nuova password'), nw),
        h('label', { class: 'field' }, h('span', {}, 'Ripeti'), nw2),
        h('div', { class: 'form-actions field wide' }, h('button', { class: 'primary', disabled: !state.admin, onclick: async () => {
          if (nw.value !== nw2.value) { toast('Le password non coincidono', 'bad'); return; }
          try { await post('/api/auth/password', { old: old.value, new: nw.value }); toast('Password cambiata: le altre sessioni sono state chiuse', 'ok'); } catch (e) { errToast(e); }
        } }, 'Cambia password')))),
      card('Accesso', configForm([
        { path: 'web.username', label: 'Nome utente amministratore' },
        { path: 'web.public_readonly', label: 'Consenti la consultazione senza accesso (sola lettura)', type: 'check', help: 'Chi è nella tua rete può vedere grafici ed eventi senza password; le impostazioni restano protette.' },
        { path: 'web.session_days', label: 'Durata delle sessioni', type: 'number', unit: 'giorni', min: 1, max: 365 },
      ])),
      card('Sessioni attive', h('div', {}, h('table', {}, h('tbody', {}, sess.map((s) => h('tr', {}, h('td', {}, s.current ? h('b', {}, 'questa') : s.token), h('td', {}, s.ip), h('td', { class: 'wrap' }, (s.ua || '').slice(0, 60)), h('td', {}, fmtAgo(s.created)))))),
        state.admin ? h('div', { class: 'row end', style: { marginTop: '8px' } }, h('button', { class: 'small', onclick: async () => { await del('/api/sessions'); toast('Altre sessioni chiuse', 'ok'); render(); } }, 'Chiudi le altre sessioni')) : null))));
  }

  async function bk() {
    const file = h('input', { type: 'file', accept: '.json,application/json' });
    body.replaceChildren(h('div', { class: 'grid g2' },
      card('Backup', h('div', { class: 'stack' }, h('p', { class: 'muted' }, 'Scarica configurazione completa (segreti inclusi: conservalo con cura) ed elenco degli eventi con le annotazioni. Le forme d\'onda si esportano dai singoli eventi in miniSEED.'),
        h('div', { class: 'row' }, h('button', { class: 'primary', disabled: !state.admin, onclick: () => download('/api/system/backup') }, '⬇ Scarica backup'),
          h('button', { onclick: () => download('/api/export/events.csv') }, '⬇ Eventi CSV')))),
      card('Ripristino configurazione', h('div', { class: 'stack' }, h('p', { class: 'muted' }, 'Carica un file di backup per ripristinare le impostazioni (gli eventi non vengono toccati; la password resta quella attuale se il backup non ne contiene una).'),
        file, h('button', { disabled: !state.admin, onclick: async () => {
          if (!file.files[0]) return;
          try { const data = JSON.parse(await file.files[0].text()); if (!await confirm('Ripristinare la configurazione?', 'Le impostazioni attuali verranno sostituite.', 'Ripristina', true)) return;
            await post('/api/system/restore', data); toast('Configurazione ripristinata', 'ok'); setTimeout(() => location.reload(), 800); } catch (e) { errToast(e); }
        } }, 'Ripristina')))));
  }

  async function pw() {
    const sys = await get('/api/system');
    const drift = (Date.now() / 1000 - sys.time);
    const act = (a, label, text, cls = 'danger') => h('button', { class: cls, disabled: !state.admin, onclick: async () => {
      if (!await confirm(label, text, label, true)) return;
      await post('/api/system/power', { action: a }); toast(`${label}: comando inviato`, 'ok');
    } }, label);
    body.replaceChildren(h('div', { class: 'grid g2' },
      card('Orologio', h('div', { class: 'stack' }, kv([['Ora del Pi', fmtDT(sys.time)], ['Fuso', sys.tz], ['Sincronizzato NTP', sys.clock_synced ? 'sì' : 'NO'],
        ['Differenza con questo dispositivo', `${num(drift, 1)} s`]]),
        h('div', { class: 'note' }, 'Il Pi Zero non ha un orologio a batteria: senza Internet riparte dall\'ultima ora salvata. In modalità Access Point puoi copiare l\'ora del tuo telefono/PC. Per l\'indipendenza totale si può aggiungere un modulo RTC DS3231 sullo stesso bus I2C (vedi Guida).'),
        h('button', { disabled: !state.admin || sys.clock_synced, onclick: async () => { try { const r = await post('/api/system/time', { epoch: Date.now() / 1000 }); toast(`Ora impostata: ${r.set}`, 'ok'); render(); } catch (e) { errToast(e); } } }, 'Imposta l\'ora da questo dispositivo'))),
      card('Alimentazione', h('div', { class: 'stack' }, h('p', { class: 'muted' }, 'Spegni sempre il Pi da qui prima di staccare la corrente: protegge la SD.'),
        h('div', { class: 'row' }, act('reboot', 'Riavvia il Pi', 'Il sismografo resterà fermo per circa un minuto.', ''), act('poweroff', 'Spegni il Pi', 'Per riaccenderlo dovrai staccare e riattaccare l\'alimentazione.')),
        h('div', { class: 'row' }, act('restart_acq', 'Riavvia acquisizione', 'Breve buco nei dati.', ''), act('restart_web', 'Riavvia dashboard', 'La pagina si ricollegherà da sola.', ''))))));
  }

  async function render() {
    body.replaceChildren(h('div', { class: 'empty' }, 'caricamento…'));
    try {
      if (tab === 'hw') { await hw(); timer = setInterval(() => hw().catch(() => {}), 10000); }
      else if (tab === 'sd') await sdTab();
      else if (tab === 'svc') await svc();
      else if (tab === 'sec') await sec();
      else if (tab === 'bk') await bk();
      else await pw();
    } catch (e) { body.replaceChildren(h('div', { class: 'note bad' }, e.message)); }
  }
  show('hw');
  return () => clearInterval(timer);
}
