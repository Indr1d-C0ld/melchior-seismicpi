import { state, get, post, h, card, fmtDT, fmtAcc, num, mmiBadge, classBadge, labelName, toast, errToast, confirm, storageGet, storageSet, accUnit } from '../lib.js';

export default async function (root) {
  const f = Object.assign({ days: 30, cls: '', label: '', min_pga: '', min_mmi: '', starred: false, confirmed: false, q: '', sort: 't_trigger', order: 'desc', limit: 50, offset: 0 },
    storageGet('events_filter', {}));
  f.offset = 0;
  const sel = new Set();
  const classes = Object.entries(state.meta.classes), labels = Object.entries(state.meta.labels);
  const inp = (k, attrs = {}) => { const el = h('input', { value: f[k], ...attrs }); el.onchange = () => { f[k] = el.value; f.offset = 0; load(); }; return el; };
  const chk = (k, label) => { const el = h('input', { type: 'checkbox', checked: f[k] }); el.onchange = () => { f[k] = el.checked; f.offset = 0; load(); }; return h('label', { class: 'inline' }, el, label); };
  const sl = (k, opts) => { const el = h('select', {}, opts.map(([v, l]) => h('option', { value: v, selected: String(v) === String(f[k]) }, l))); el.onchange = () => { f[k] = el.value; f.offset = 0; load(); }; return el; };
  const filters = h('div', { class: 'row' },
    sl('days', [[1, 'ultime 24 ore'], [7, '7 giorni'], [30, '30 giorni'], [90, '90 giorni'], [365, '1 anno'], [0, 'tutto']]),
    sl('cls', [['', 'tutte le classi'], ...classes.map(([k, v]) => [k, v])]),
    sl('label', [['', 'tutte le etichette'], ['_none', 'senza etichetta'], ...labels]),
    h('span', { class: 'muted' }, 'PGA ≥'), inp('min_pga', { type: 'number', step: 'any', style: { width: '80px' }, placeholder: 'mg' }),
    h('span', { class: 'muted' }, 'MMI ≥'), inp('min_mmi', { type: 'number', step: 0.5, style: { width: '70px' } }),
    chk('starred', '★ preferiti'), chk('confirmed', 'confermati da catalogo'),
    inp('q', { type: 'search', placeholder: 'cerca nelle note / n°', style: { width: '170px' } }));
  const tableEl = h('div', { class: 'table-wrap' });
  const pager = h('div', { class: 'row' });
  const bulk = h('div', { class: 'row' });
  const summary = h('span', { class: 'muted' });

  const qs = () => {
    const p = new URLSearchParams({ sort: f.sort, order: f.order, limit: f.limit, offset: f.offset });
    if (+f.days) p.set('from', Date.now() / 1000 - f.days * 86400);
    for (const k of ['cls', 'label', 'min_pga', 'min_mmi', 'q']) if (f[k]) p.set(k, f[k]);
    if (f.starred) p.set('starred', '1');
    if (f.confirmed) p.set('confirmed', '1');
    return p;
  };
  const cols = [
    ['', null], ['id', 'N°'], ['t_trigger', 'Data e ora'], ['class', 'Classe'], [null, 'Etichetta'], ['pga', `PGA (${accUnit()})`], ['pgv', 'PGV cm/s'],
    ['mmi', 'MMI'], ['duration', 'Durata'], ['dom_freq', 'f dom.'], ['peak_ratio', 'STA/LTA'], [null, 'Catalogo'], [null, '★'],
  ];
  async function load() {
    storageSet('events_filter', { ...f, offset: 0 });
    let r;
    try { r = await get('/api/events?' + qs()); } catch (e) { errToast(e); return; }
    summary.textContent = `${r.total} eventi`;
    const head = h('tr', {}, cols.map(([k, l]) => {
      if (k === '') {
        const all = h('input', { type: 'checkbox', onchange: () => { r.items.forEach((e) => (all.checked ? sel.add(e.id) : sel.delete(e.id))); load(); } });
        return h('th', {}, all);
      }
      const th = h('th', { class: k ? 'sort' : '' }, l, k === f.sort ? (f.order === 'asc' ? ' ▲' : ' ▼') : '');
      if (k) th.onclick = () => { f.order = f.sort === k && f.order === 'desc' ? 'asc' : 'desc'; f.sort = k; load(); };
      return th;
    }));
    const rows = r.items.map((e) => {
      const cb = h('input', { type: 'checkbox', checked: sel.has(e.id), onclick: (ev) => ev.stopPropagation(), onchange: () => { cb.checked ? sel.add(e.id) : sel.delete(e.id); renderBulk(); } });
      const star = h('span', { style: { cursor: state.admin ? 'pointer' : 'default', color: e.starred ? 'var(--warn)' : 'var(--faint)' } }, e.starred ? '★' : '☆');
      if (state.admin) star.onclick = async (ev) => { ev.stopPropagation(); await post('/api/events/bulk', { ids: [e.id], action: 'star', value: !e.starred }); load(); };
      return h('tr', { class: 'click', onclick: () => { location.hash = `#/evento/${e.id}`; } },
        h('td', {}, cb), h('td', { class: 'mono' }, e.id), h('td', {}, fmtDT(e.t_trigger)), h('td', {}, classBadge(e.class)),
        h('td', {}, labelName(e.label)), h('td', { class: 'num' }, fmtAcc(e.pga, false)), h('td', { class: 'num' }, num(e.pgv, 3)),
        h('td', {}, mmiBadge(e.mmi)), h('td', { class: 'num' }, num(e.duration, 1) + ' s'), h('td', { class: 'num' }, e.dom_freq ? num(e.dom_freq, 1) + ' Hz' : '–'),
        h('td', { class: 'num' }, num(e.peak_ratio, 1)),
        h('td', {}, e.catalog_id ? h('span', { title: e.cat_place }, `M${num(e.cat_mag, 1)} · ${num(e.cat_dist, 0)} km`) : ''), h('td', {}, star));
    });
    tableEl.replaceChildren(r.items.length ? h('table', {}, h('thead', {}, head), h('tbody', {}, rows)) : h('div', { class: 'empty' }, 'Nessun evento con questi filtri.'));
    const pages = Math.max(1, Math.ceil(r.total / f.limit)), page = Math.floor(f.offset / f.limit) + 1;
    pager.replaceChildren(
      h('button', { class: 'small', disabled: page <= 1, onclick: () => { f.offset -= f.limit; load(); } }, '◀'),
      h('span', { class: 'muted' }, `pagina ${page} di ${pages}`),
      h('button', { class: 'small', disabled: page >= pages, onclick: () => { f.offset += f.limit; load(); } }, '▶'),
      h('select', { onchange: (e) => { f.limit = +e.target.value; f.offset = 0; load(); } }, [25, 50, 100, 250].map((n) => h('option', { value: n, selected: n === +f.limit }, `${n} per pagina`))));
    renderBulk();
  }
  function renderBulk() {
    if (!state.admin || !sel.size) { bulk.replaceChildren(); return; }
    const lab = h('select', {}, h('option', { value: '' }, '– togli etichetta –'), Object.entries(state.meta.labels).map(([k, v]) => h('option', { value: k }, v)));
    bulk.replaceChildren(h('b', {}, `${sel.size} selezionati`),
      lab, h('button', { class: 'small', onclick: async () => { await post('/api/events/bulk', { ids: [...sel], action: 'label', label: lab.value }); toast('Etichette aggiornate', 'ok'); load(); } }, 'Applica etichetta'),
      h('button', { class: 'small', onclick: async () => { await post('/api/events/bulk', { ids: [...sel], action: 'star', value: true }); load(); } }, '★ Preferiti'),
      h('button', { class: 'small danger', onclick: async () => {
        if (!await confirm('Eliminare gli eventi?', `${sel.size} eventi e le loro forme d'onda verranno eliminati definitivamente.`, 'Elimina', true)) return;
        await post('/api/events/bulk', { ids: [...sel], action: 'delete' }); sel.clear(); toast('Eventi eliminati', 'ok'); load();
      } }, 'Elimina'),
      h('button', { class: 'small ghost', onclick: () => { sel.clear(); load(); } }, 'Deseleziona'));
  }
  root.append(h('div', { class: 'stack' },
    h('div', { class: 'card' }, filters),
    h('div', { class: 'card' },
      h('div', { class: 'card-head' }, h('h2', {}, 'Registro eventi'), summary, h('a', { class: 'btn small', href: '#', onclick: (e) => { e.preventDefault(); location.href = '/api/export/events.csv?' + qs(); } }, '⬇ CSV')),
      bulk, tableEl, h('div', { class: 'row', style: { marginTop: '10px' } }, pager))));
  await load();
}
