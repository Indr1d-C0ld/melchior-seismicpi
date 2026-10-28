import { state, h, card, configForm, patch, toast, errToast, num } from '../lib.js';
import { TileMap } from '../map.js';

export default async function (root) {
  const st = state.cfg.station;
  const fields = [
    { path: 'station.name', label: 'Nome della stazione', help: 'Compare nella dashboard, nei messaggi Telegram e nei file esportati.' },
    { path: 'station.network', label: 'Codice rete SEED', help: '2 caratteri. XX = rete sperimentale/privata.' },
    { path: 'station.code', label: 'Codice stazione SEED', help: 'Fino a 5 caratteri, usato nei file miniSEED.' },
    { path: 'station.location', label: 'Codice location SEED', help: 'Di solito 00.' },
    { path: 'station.latitude', label: 'Latitudine', type: 'number', unit: '°', step: 0.00001, help: 'Clicca sulla mappa per impostarla.' },
    { path: 'station.longitude', label: 'Longitudine', type: 'number', unit: '°', step: 0.00001 },
    { path: 'station.elevation_m', label: 'Quota', type: 'number', unit: 'm s.l.m.', step: 1 },
    { path: 'station.north_offset_deg', label: 'Rotazione del box rispetto al nord', type: 'number', unit: '°', step: 1,
      help: 'Di quanti gradi (in senso orario) l\'asse "N" del sensore è ruotato rispetto al nord geografico. Serve per interpretare il moto della particella.' },
    { path: 'station.floor', label: 'Piano / collocazione', help: 'Es. "cantina, su pavimento in cemento".' },
    { path: 'station.timezone', label: 'Fuso orario', help: 'Il Pi lavora in UTC; la dashboard mostra l\'ora del browser oppure UTC.' },
    { path: 'station.site_notes', label: 'Note sul sito', type: 'textarea', wide: true },
    { section: 'Visualizzazione' },
    { path: 'web.units', label: 'Unità di accelerazione', type: 'select', options: [['mg', 'mg (millesimi di g)'], ['gal', 'cm/s² (gal)'], ['ms2', 'm/s²']] },
    { path: 'web.time_display', label: 'Orari mostrati in', type: 'select', options: [['local', 'ora locale del browser'], ['utc', 'UTC (come i sismologi)']] },
  ];
  const map = new TileMap({ height: 420, lat: st.latitude ?? 42.5, lon: st.longitude ?? 12.5, zoom: st.latitude != null ? 13 : 5,
    onclick: async (lat, lon) => {
      if (!state.admin) return;
      try {
        const r = await patch('/api/config', { station: { latitude: +lat.toFixed(5), longitude: +lon.toFixed(5) } });
        state.cfg = r.config; toast(`Posizione impostata: ${lat.toFixed(5)}, ${lon.toFixed(5)}`, 'ok'); place();
        const lt = root.querySelectorAll('input[type=number]'); lt[0].value = lat.toFixed(5); lt[1].value = lon.toFixed(5);
      } catch (e) { errToast(e); }
    } });
  const place = () => {
    const s = state.cfg.station;
    if (s.latitude != null) {
      map.setMarkers([{ lat: s.latitude, lon: s.longitude, color: '#2563eb', square: true, size: 16, title: s.name }]);
      map.setCircles([{ lat: s.latitude, lon: s.longitude, km: state.cfg.catalogs.radius_km, color: '#60a5fa' }, { lat: s.latitude, lon: s.longitude, km: state.cfg.alerts.catalog.max_radius_km, color: '#f59e0b' }]);
    }
  };
  place();
  const gps = h('button', { class: 'small', disabled: !state.admin || !navigator.geolocation, onclick: () => {
    navigator.geolocation.getCurrentPosition(async (p) => {
      const r = await patch('/api/config', { station: { latitude: +p.coords.latitude.toFixed(5), longitude: +p.coords.longitude.toFixed(5), elevation_m: p.coords.altitude ? Math.round(p.coords.altitude) : state.cfg.station.elevation_m } });
      state.cfg = r.config; place(); map.setView(p.coords.latitude, p.coords.longitude, 15); toast('Posizione presa dal dispositivo', 'ok');
    }, (e) => errToast(new Error('Geolocalizzazione non disponibile: ' + e.message + ' (funziona solo in HTTPS o su localhost)')));
  } }, '📍 Usa la posizione di questo dispositivo');
  root.append(h('div', { class: 'grid g2' },
    card('Stazione', configForm(fields, { onsave: place })),
    h('div', { class: 'stack' },
      h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('h2', {}, 'Posizione'), gps), map.el,
        h('p', { class: 'muted' }, 'Clicca sulla mappa per spostare la stazione. Cerchio blu: raggio dei cataloghi; cerchio arancio: raggio delle allerte regionali. Senza Internet (modalità Access Point) la mappa non carica i tasselli.')),
      card('Perché serve la posizione', h('div', { class: 'muted' },
        h('p', {}, 'Con le coordinate il sismografo calcola la distanza di ogni terremoto dei cataloghi INGV/EMSC, i tempi teorici di arrivo delle onde P e S alla tua casa e associa automaticamente i propri eventi a quelli ufficiali (conferma).'),
        h('p', {}, 'La precisione di qualche decina di metri è più che sufficiente.'))))));
  return () => map.destroy();
}
