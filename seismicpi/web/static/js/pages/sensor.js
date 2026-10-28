import { state, get, post, patch, h, card, configForm, num, fmtAcc, toast, errToast, confirm, modal } from '../lib.js';

export default async function (root) {
  const a = state.acq || {};
  const s = state.cfg.sensor;
  const kv = (pairs) => h('div', { class: 'kv' }, pairs.flatMap(([k, v]) => [h('span', {}, k), h('span', {}, v ?? '–')]));
  const info = kv([
    ['Stato', a.sensor_ok ? 'attivo' : `non disponibile: ${a.sensor_error || '?'}`],
    ['Chip', a.sensor?.chip], ['Driver', a.driver], ['Bus / indirizzo', a.sensor?.bus != null ? `i2c-${a.sensor.bus} @ 0x${(+a.sensor.address).toString(16)}` : '–'],
    ['Campionamento', `${num(a.fs, 3)} Hz misurati (nominale ${a.fs_nominal} Hz)`], ['Fondo scala', `±${a.sensor?.range_g ?? s.range_g} g`],
    ['Filtro anti-alias (DLPF)', `${a.sensor?.dlpf_hz ?? '?'} Hz`], ['Temperatura', `${num(a.temp, 2)} °C`],
    ['Risoluzione', `${num(1000 / ({ 2: 16384, 4: 8192, 8: 4096, 16: 2048 }[s.range_g]) * 1000, 0)} µg per conteggio`],
    ['Buchi / overflow / errori I2C', `${a.counters?.gaps || 0} / ${a.counters?.overflows || 0} / ${a.counters?.i2c_errors || 0}`],
    ['Orientamento', `E=${s.axis_map.E} · N=${s.axis_map.N} · Z=${s.axis_map.Z}`],
    ['Offset (g)', `E ${num(s.offsets_g.E, 4)} · N ${num(s.offsets_g.N, 4)} · Z ${num(s.offsets_g.Z, 4)}`],
  ]);
  const out = h('div', { class: 'stack' });
  const act = (label, fn, cls = '') => h('button', { class: cls, disabled: !state.admin, onclick: async (ev) => {
    ev.target.disabled = true; try { await fn(); } catch (e) { errToast(e); } finally { ev.target.disabled = false; } } }, label);
  const tools = h('div', { class: 'stack' },
    h('div', { class: 'row' },
      act('Calibra (azzera offset)', async () => {
        if (!await confirm('Calibrazione', 'Il sensore deve essere fermo e in bolla. Misuro per 10 secondi la gravità e azzero gli offset: E e N a 0 g, Z a 1 g. Non toccare il box durante la misura.', 'Avvia')) return;
        toast('Misura in corso (10 s)…');
        const r = await post('/api/sensor/calibrate', { seconds: 10 });
        out.prepend(h('div', { class: 'note ok' }, `Calibrazione: medie E ${num(r.mean_g[0], 5)} · N ${num(r.mean_g[1], 5)} · Z ${num(r.mean_g[2], 5)} g; inclinazione ${num(r.tilt_deg, 2)}°; rumore ${r.std_mg.map((x) => num(x, 2)).join(' / ')} mg. Offset salvati.`));
        toast('Calibrazione salvata', 'ok');
      }, 'primary'),
      act('Orientamento automatico', async () => {
        const r = await post('/api/sensor/orient', {});
        const p = r.proposal;
        const ok = await modal('Orientamento rilevato', h('div', {}, h('p', {}, `Gravità sugli assi del chip: x ${num(r.chip_axes_g[0], 3)} g, y ${num(r.chip_axes_g[1], 3)} g, z ${num(r.chip_axes_g[2], 3)} g.`),
          h('p', {}, `Proposta: Z (verticale) = ${p.Z}, E = ${p.E}, N = ${p.N}. Dopo, ricordati di indicare in "Stazione" di quanti gradi il box è ruotato rispetto al nord e di ricalibrare.`)),
          [{ label: 'Annulla', value: false }, { label: 'Applica', class: 'primary', value: true }]);
        if (ok) { await post('/api/sensor/orient', { apply: true }); toast('Orientamento applicato', 'ok'); setTimeout(() => location.reload(), 800); }
      }),
      act('Misura rumore (30 s)', async () => {
        toast('Misuro il rumore per 30 s: evita di camminare vicino al sensore…');
        const r = await post('/api/sensor/noise', { seconds: 30 });
        out.prepend(h('div', { class: 'note' }, `Rumore (deviazione standard, segnale grezzo) E ${num(r.std_mg[0], 2)} · N ${num(r.std_mg[1], 2)} · Z ${num(r.std_mg[2], 2)} mg su ${r.samples} campioni. Tipico MPU-6050: 2-5 mg.`));
      }),
      act('Autotest di fabbrica', async () => {
        const r = (await post('/api/sensor/selftest')).result;
        out.prepend(h('div', { class: r.ok ? 'note ok' : 'note warn' }, h('b', {}, r.ok ? 'Autotest superato' : 'Autotest non superato'),
          h('div', {}, Object.entries(r.axes || {}).map(([k, v]) => `${k}: risposta ${v.response}, attesa ${v.factory}, scarto ${v.deviation_pct}%`).join(' · ')), h('div', { class: 'muted' }, r.note || '')));
      }),
      act('Scansione bus I2C', async () => {
        const r = await get('/api/i2c');
        out.prepend(h('div', { class: 'stack' }, h('div', { class: r.found.some((x) => x.startsWith('0x68') || x.startsWith('0x69')) ? 'note ok' : 'note warn' },
          r.found.length ? `Dispositivi trovati su i2c-${r.bus}: ${r.found.join(', ')}. L'MPU-6050 risponde a 0x68 (AD0 a massa) o 0x69.` : 'Nessun dispositivo trovato: controlla alimentazione, SDA/SCL e che l\'I2C sia abilitato.'), h('pre', {}, r.output)));
      }),
      act('Riapri sensore', async () => { await post('/api/sensor/reopen'); toast('Sensore riaperto', 'ok'); }),
    ), out);
  const fields = [
    { path: 'sensor.driver', label: 'Driver', type: 'select', options: [['mpu6050', 'MPU-6050 (I2C)'], ['sim', 'simulatore (prove senza hardware)']] },
    { path: 'sensor.sample_rate', label: 'Frequenza di campionamento', type: 'select', options: [[50, '50 Hz'], [100, '100 Hz (consigliata)'], [200, '200 Hz (più CPU)']],
      help: '100 Hz copre tutte le frequenze sismiche utili (fino a ~40 Hz). 200 Hz raddoppia CPU e scritture.' },
    { path: 'sensor.range_g', label: 'Fondo scala', type: 'select', options: [[2, '±2 g (massima risoluzione)'], [4, '±4 g'], [8, '±8 g'], [16, '±16 g']] },
    { path: 'sensor.dlpf', label: 'Filtro anti-alias interno', type: 'select', options: [[1, '184 Hz'], [2, '94 Hz'], [3, '44 Hz (consigliato a 100 Hz)'], [4, '21 Hz'], [5, '10 Hz'], [6, '5 Hz']],
      help: 'Deve stare sotto metà della frequenza di campionamento.' },
    { path: 'sensor.i2c_bus', label: 'Bus I2C', type: 'number', min: 0, max: 20, step: 1 },
    { path: 'sensor.address', label: 'Indirizzo I2C', type: 'select', options: [[104, '0x68 (AD0 a massa)'], [105, '0x69 (AD0 a 3,3 V)']] },
    { path: 'sensor.read_interval_ms', label: 'Intervallo di lettura FIFO', type: 'number', unit: 'ms', min: 20, max: 500, help: 'La FIFO contiene 1,7 s di dati a 100 Hz: 100 ms lascia ampio margine.' },
    { path: 'sensor.temp_offset_c', label: 'Correzione temperatura', type: 'number', unit: '°C', step: 0.1 },
    { section: 'Orientamento degli assi' },
    { path: 'sensor.axis_map.E', label: 'Asse Est', type: 'select', options: ['+x', '-x', '+y', '-y', '+z', '-z'].map((v) => [v, v]) },
    { path: 'sensor.axis_map.N', label: 'Asse Nord', type: 'select', options: ['+x', '-x', '+y', '-y', '+z', '-z'].map((v) => [v, v]) },
    { path: 'sensor.axis_map.Z', label: 'Asse Verticale', type: 'select', options: ['+x', '-x', '+y', '-y', '+z', '-z'].map((v) => [v, v]) },
    { section: 'Offset (impostati dalla calibrazione)' },
    { path: 'sensor.offsets_g.E', label: 'Offset E', type: 'number', unit: 'g', step: 0.0001 },
    { path: 'sensor.offsets_g.N', label: 'Offset N', type: 'number', unit: 'g', step: 0.0001 },
    { path: 'sensor.offsets_g.Z', label: 'Offset Z', type: 'number', unit: 'g', step: 0.0001 },
  ];
  root.append(h('div', { class: 'stack' },
    h('div', { class: 'grid g2' }, card('Stato del sensore', info), card('Strumenti', tools)),
    card('Configurazione', configForm(fields)),
    card('Limiti fisici dell\'MPU-6050', h('div', { class: 'muted' },
      h('p', {}, 'È un accelerometro MEMS a 16 bit pensato per droni e smartphone: rumore ~400 µg/√Hz, cioè 2-4 mg RMS nella banda sismica. Sente bene scosse locali e vicine percepibili dalle persone (intensità III e oltre) e terremoti forti regionali; non vede il microsisma né i terremoti lontani di magnitudo moderata.'),
      h('p', {}, 'Per migliorarne la resa: fissalo rigidamente (vite o colla epossidica, non colla a caldo) su un pavimento al piano terra o in cantina, lontano da elettrodomestici; stabilizza la temperatura; evita cavi Dupont penzolanti che trasmettono vibrazioni.')))));
}
