import { state, h, card, configForm, on, num, fmtAcc, cssVar, accUnit, drawLoop } from '../lib.js';
import { Chart } from '../charts.js';
import { LiveBuffer } from '../livebuf.js';

export default async function (root) {
  const fields = [
    { section: 'Trigger STA/LTA' },
    { path: 'trigger.enabled', label: 'Rilevamento eventi attivo', type: 'check', help: 'Se disattivato il sismografo continua a registrare rullo e statistiche, ma non crea eventi.' },
    { path: 'trigger.channel', label: 'Canale di trigger', type: 'select', options: [['vector', 'modulo 3D (consigliato)'], ['Z', 'solo verticale'], ['horizontal', 'piano orizzontale'], ['E', 'solo E'], ['N', 'solo N'], ['any', 'qualsiasi asse']],
      help: 'Su quale combinazione di assi si calcola l\'energia per il confronto STA/LTA.' },
    { path: 'trigger.sta_s', label: 'Finestra STA (breve)', type: 'number', unit: 's', min: 0.05, max: 10, step: 0.1, help: 'Media a breve termine: 0,3-1 s per eventi locali, 1-3 s per terremoti lontani.' },
    { path: 'trigger.lta_s', label: 'Finestra LTA (lunga)', type: 'number', unit: 's', min: 2, max: 600, step: 1, help: 'Rumore di fondo di riferimento: 20-60 s. Più lunga = più stabile ma più lenta ad adattarsi.' },
    { path: 'trigger.on_ratio', label: 'Soglia di attacco', type: 'number', min: 1.2, max: 50, step: 0.1, help: 'Il trigger scatta quando STA/LTA supera questo valore. Più alta = meno falsi allarmi.' },
    { path: 'trigger.off_ratio', label: 'Soglia di stacco', type: 'number', min: 0.5, max: 20, step: 0.1, help: 'L\'evento termina quando STA/LTA scende sotto questo valore.' },
    { path: 'trigger.min_sta_mg', label: 'Ampiezza minima STA', type: 'number', unit: 'mg RMS', min: 0, step: 0.1, help: 'Evita trigger in notti molto quiete quando LTA è minuscolo e basta un niente per raddoppiarlo.' },
    { path: 'trigger.pga_threshold_mg', label: 'Trigger ad accelerazione assoluta', type: 'number', unit: 'mg', min: 0, step: 1, help: 'Scatta comunque se il modulo 3D supera questo valore (0 = disattivato). Utile per scosse forti con inizio graduale.' },
    { path: 'trigger.freeze_lta', label: 'Congela LTA durante l\'evento', type: 'check', help: 'Impedisce che l\'evento stesso alzi il rumore di riferimento accorciandone la durata.' },
    { section: 'Durata e finestra registrata' },
    { path: 'trigger.min_duration_s', label: 'Durata minima', type: 'number', unit: 's', min: 0, step: 0.1, help: 'Trigger più brevi vengono scartati (picchi singoli, disturbi elettrici).' },
    { path: 'trigger.pre_event_s', label: 'Pre-evento', type: 'number', unit: 's', min: 2, max: 120, help: 'Secondi registrati prima del trigger (serve per vedere l\'arrivo della P).' },
    { path: 'trigger.post_event_s', label: 'Post-evento', type: 'number', unit: 's', min: 2, max: 300, help: 'Secondi registrati dopo lo stacco (coda del sisma).' },
    { path: 'trigger.max_duration_s', label: 'Durata massima', type: 'number', unit: 's', min: 10, max: 1800 },
    { path: 'trigger.cooldown_s', label: 'Pausa dopo un evento', type: 'number', unit: 's', min: 0, max: 600 },
    { section: 'Filtri digitali (sul Pi)' },
    { path: 'processing.trigger_highpass_hz', label: 'Trigger: passa-alto', type: 'number', unit: 'Hz', step: 0.1, help: 'Toglie gravità e derive lente prima del calcolo dell\'energia.' },
    { path: 'processing.trigger_lowpass_hz', label: 'Trigger: passa-basso', type: 'number', unit: 'Hz', step: 1, help: 'Toglie il rumore ad alta frequenza (motori, ventole). I terremoti stanno quasi tutti sotto i 15 Hz.' },
    { path: 'processing.display_highpass_hz', label: 'Live e rullo: passa-alto', type: 'number', unit: 'Hz', step: 0.05, help: 'Filtro dei dati mostrati in diretta e sul rullo (PGA inclusa).' },
    { path: 'processing.velocity_highpass_hz', label: 'Velocità (PGV): passa-alto', type: 'number', unit: 'Hz', step: 0.05, help: 'Contro la deriva dell\'integrazione. 0,1-0,5 Hz con un MEMS.' },
  ];
  // anteprima: il rapporto STA/LTA live con le soglie configurate
  const buf = new LiveBuffer(120);
  const ratio = new Chart({ height: 200, x: { type: 'time' }, y: { min: 0, label: 'STA/LTA' }, legend: false, series: [{ name: 'STA/LTA', color: cssVar('--violet'), type: 'step' }] });
  const info = h('div', { class: 'kv' });
  const presets = h('div', { class: 'row' }, ...[
    ['Sensibile', { sta_s: 0.5, lta_s: 30, on_ratio: 3, off_ratio: 1.4, min_sta_mg: 0.5 }, 'più eventi, più falsi allarmi'],
    ['Bilanciato', { sta_s: 0.6, lta_s: 30, on_ratio: 4, off_ratio: 1.6, min_sta_mg: 0.8 }, 'default'],
    ['Robusto (casa rumorosa)', { sta_s: 1, lta_s: 60, on_ratio: 6, off_ratio: 2, min_sta_mg: 2 }, 'solo segnali netti'],
    ['Telesismi', { sta_s: 3, lta_s: 120, on_ratio: 3, off_ratio: 1.5, min_sta_mg: 0.5 }, 'onde lente, forti e lontane'],
  ].map(([l, p, tip]) => h('button', { class: 'small', title: tip, disabled: !state.admin, onclick: async () => {
    const { patch } = await import('../lib.js');
    const r = await patch('/api/config', { trigger: p }); state.cfg = r.config; location.reload();
  } }, l)));
  root.append(h('div', { class: 'grid g3' },
    h('div', { class: 'card span2' }, h('h2', {}, 'Parametri di rilevamento'), h('div', { class: 'note', style: { marginBottom: '12px' } },
      'Le modifiche si applicano subito al servizio di acquisizione (il trigger riparte dopo il tempo LTA necessario a misurare il rumore). Preimpostazioni: ', presets), configForm(fields)),
    h('div', { class: 'stack' }, card('STA/LTA in tempo reale', h('div', {}, ratio.el, info)),
      card('Come funziona', h('div', { class: 'muted' },
        h('p', {}, 'STA/LTA confronta l\'energia del segnale negli ultimi istanti (STA) con quella del rumore di fondo (LTA). Un terremoto fa crescere improvvisamente STA: quando il rapporto supera la soglia di attacco nasce un evento, che termina quando scende sotto la soglia di stacco.'),
        h('p', {}, 'Ogni evento viene poi classificato (impulso locale, rumore locale, possibile sisma) in base a durata, frequenza dominante e rapporto orizzontale/verticale, e confrontato con i cataloghi INGV/EMSC per la conferma.'))))));
  let stopLoop = null;
  const loop = () => {
    if (!buf.dirty) return; buf.dirty = false;
    const t = buf.ratioT;
    ratio.o.hlines = [{ y: state.cfg.trigger.on_ratio, color: cssVar('--bad'), label: 'attacco' }, { y: state.cfg.trigger.off_ratio, color: cssVar('--warn'), label: 'stacco' }];
    ratio.setData(t, [buf.ratio]);
    const a = state.acq || {};
    info.replaceChildren(h('span', {}, 'STA / LTA'), h('span', {}, `${fmtAcc(a.sta_mg)} / ${fmtAcc(a.lta_mg)} RMS`), h('span', {}, 'Rapporto attuale'), h('span', {}, num(a.ratio, 2)),
      h('span', {}, 'Stato'), h('span', {}, a.state === 'idle' ? 'in ascolto' : 'EVENTO in corso'), h('span', {}, 'Trigger registrati'), h('span', {}, `${a.counters?.triggers || 0} (scartati ${a.counters?.discarded || 0})`));
  };
  stopLoop = drawLoop(loop);
  return () => { stopLoop(); buf.close(); ratio.destroy(); };
}
