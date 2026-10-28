import { state, h, seg, patch, get, storageGet, storageSet, fmtDate, toast } from '../lib.js';
import { Helicorder } from '../helicorder.js';

export default async function (root, args, params) {
  const hc = state.cfg.web.helicorder;
  const opt = Object.assign({ channel: hc.channel, lineMinutes: hc.line_minutes, gain: hc.gain, style: hc.style, hours: 24, clip: true, mode: 'live' },
    storageGet('drum', {}));
  const save = () => storageSet('drum', opt);
  const heli = new Helicorder({ ...opt, height: Math.max(560, window.innerHeight - 190),
    onpick: (t) => { location.hash = `#/finestra?from=${Math.floor(t - 90)}&to=${Math.floor(t + 210)}&ch=${opt.channel}`; } });
  let dayTs = params.get('day') ? Number(params.get('day')) : null;
  if (dayTs) opt.mode = 'day';
  const dateIn = h('input', { type: 'date', value: new Date((dayTs || Date.now() / 1000) * 1000).toISOString().slice(0, 10) });
  const gainOut = h('span', { class: 'mono' }, `×${opt.gain}`);
  const gain = h('input', { type: 'range', min: -3, max: 5, step: 0.25, value: Math.log2(opt.gain) });
  gain.oninput = () => { opt.gain = +(2 ** gain.value).toFixed(2); gainOut.textContent = `×${opt.gain}`; heli.o.gain = opt.gain; heli.draw(); save(); };
  const reload = async () => {
    Object.assign(heli.o, { channel: opt.channel, lineMinutes: opt.lineMinutes, hours: opt.hours, style: opt.style, clip: opt.clip, mode: opt.mode });
    heli.canvas.style.height = Math.max(560, Math.min(2400, opt.hours * 60 / opt.lineMinutes * 9 + 60)) + 'px';
    heli.o.height = parseInt(heli.canvas.style.height, 10);
    await heli.load(opt.mode === 'day' ? dayTs : null);
    opt.mode === 'live' ? heli.startLive(5000) : heli.stopLive();
    save();
  };
  dateIn.onchange = () => { dayTs = new Date(dateIn.value + 'T12:00:00').getTime() / 1000; opt.mode = 'day'; modeSeg.querySelectorAll('button').forEach((b, i) => b.classList.toggle('on', i === 1)); reload(); };
  const modeSeg = seg([['live', 'In diretta'], ['day', 'Giorno']], opt.mode, (v) => { opt.mode = v; if (v === 'day' && !dayTs) dayTs = Date.now() / 1000; reload(); });
  const shift = (d) => { dayTs = (dayTs || Date.now() / 1000) + d * 86400; opt.mode = 'day'; dateIn.value = new Date(dayTs * 1000).toISOString().slice(0, 10); reload(); };
  const clip = h('input', { type: 'checkbox', checked: opt.clip, onchange: () => { opt.clip = clip.checked; heli.o.clip = clip.checked; heli.draw(); save(); } });
  const evChk = h('input', { type: 'checkbox', checked: true, onchange: () => { heli.o.showEvents = evChk.checked; heli.draw(); } });
  const catChk = h('input', { type: 'checkbox', checked: true, onchange: () => { heli.o.showCatalog = catChk.checked; heli.draw(); } });

  root.append(h('div', { class: 'stack' },
    h('div', { class: 'card' }, h('div', { class: 'row' },
      modeSeg, h('button', { class: 'small', onclick: () => shift(-1) }, '◀'), dateIn, h('button', { class: 'small', onclick: () => shift(1) }, '▶'),
      h('span', { class: 'muted' }, 'Canale'), seg([['Z', 'Z'], ['E', 'E'], ['N', 'N'], ['V', '3D']], opt.channel, (v) => { opt.channel = v; reload(); }),
      h('span', { class: 'muted' }, 'Riga'), seg([[5, '5′'], [10, '10′'], [15, '15′'], [30, '30′'], [60, '60′']], opt.lineMinutes, (v) => { opt.lineMinutes = v; reload(); }),
      h('span', { class: 'muted' }, 'Durata'), seg([[3, '3 h'], [6, '6 h'], [12, '12 h'], [24, '24 h']], opt.hours, (v) => { opt.hours = v; reload(); }),
    ), h('div', { class: 'row', style: { marginTop: '10px' } },
      h('span', { class: 'muted' }, 'Guadagno'), gain, gainOut,
      h('span', { class: 'muted' }, 'Stile'), seg([['carta', 'carta'], ['scuro', 'scuro']], opt.style, (v) => { opt.style = v; heli.o.style = v; heli.draw(); save(); }),
      h('label', { class: 'inline' }, clip, 'limita all\'interlinea'),
      h('label', { class: 'inline' }, evChk, 'eventi locali'),
      h('label', { class: 'inline' }, catChk, 'arrivi P/S dai cataloghi'),
      h('span', { class: 'spacer' }),
      h('button', { class: 'small', onclick: () => { const a = h('a', { href: heli.png(), download: `rullo_${opt.channel}_${fmtDate(heli.t1).replaceAll('/', '-')}.png` }); a.click(); } }, '⬇ PNG'),
      state.admin ? h('button', { class: 'small', onclick: async () => {
        const r = await patch('/api/config', { web: { helicorder: { channel: opt.channel, line_minutes: opt.lineMinutes, gain: opt.gain, style: opt.style } } });
        state.cfg = r.config; toast('Impostazioni del rullo salvate come predefinite', 'ok');
      } }, 'Salva come predefinito') : null)),
    heli.el));
  await reload();
  return () => heli.destroy();
}
