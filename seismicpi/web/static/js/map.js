// Mappa a tasselli minimale (OpenStreetMap). Senza Internet (modalità AP) mostra solo i marcatori.
import { h } from './lib.js';

const TILE = 256;
const lon2x = (lon, z) => (lon + 180) / 360 * TILE * 2 ** z;
const lat2y = (lat, z) => { const r = lat * Math.PI / 180; return (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * TILE * 2 ** z; };
const x2lon = (x, z) => x / (TILE * 2 ** z) * 360 - 180;
const y2lat = (y, z) => { const n = Math.PI - 2 * Math.PI * y / (TILE * 2 ** z); return 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))); };

export class TileMap {
  constructor({ lat = 42, lon = 12.5, zoom = 5, height = 340, onclick = null } = {}) {
    this.lat = lat; this.lon = lon; this.z = zoom; this.onclick = onclick; this.markers = []; this.circles = [];
    this.el = h('div', { class: 'map', style: { height: height + 'px' } });
    this.tiles = h('div'); this.overlay = h('div'); this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    Object.assign(this.svg.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', pointerEvents: 'none' });
    this.el.append(this.tiles, this.svg, this.overlay,
      h('div', { class: 'zoom' }, h('button', { class: 'small', onclick: (e) => { e.stopPropagation(); this.zoom(1); } }, '+'),
        h('button', { class: 'small', onclick: (e) => { e.stopPropagation(); this.zoom(-1); } }, '−')),
      h('div', { class: 'attr', html: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>' }));
    this._bind();
    this.ro = new ResizeObserver(() => this.render()); this.ro.observe(this.el);
  }
  destroy() { this.ro.disconnect(); }
  setView(lat, lon, z = this.z) { this.lat = lat; this.lon = lon; this.z = Math.max(2, Math.min(17, z)); this.render(); }
  zoom(d) { this.setView(this.lat, this.lon, this.z + d); }
  setMarkers(m) { this.markers = m; this.render(); }
  setCircles(c) { this.circles = c; this.render(); }
  fit(points) {
    if (!points.length) return;
    const lats = points.map((p) => p[0]), lons = points.map((p) => p[1]);
    const w = this.el.clientWidth || 600, hgt = this.el.clientHeight || 340;
    for (let z = 15; z >= 2; z--) {
      const dx = lon2x(Math.max(...lons), z) - lon2x(Math.min(...lons), z);
      const dy = lat2y(Math.min(...lats), z) - lat2y(Math.max(...lats), z);
      if (dx < w * 0.8 && dy < hgt * 0.8) {
        this.setView((Math.max(...lats) + Math.min(...lats)) / 2, (Math.max(...lons) + Math.min(...lons)) / 2, z); return;
      }
    }
  }
  render() {
    const w = this.el.clientWidth, hgt = this.el.clientHeight; if (!w) return;
    const z = this.z, cx = lon2x(this.lon, z), cy = lat2y(this.lat, z);
    const ox = cx - w / 2, oy = cy - hgt / 2, n = 2 ** z;
    const frag = document.createDocumentFragment();
    for (let tx = Math.floor(ox / TILE); tx <= Math.floor((ox + w) / TILE); tx++) {
      for (let ty = Math.floor(oy / TILE); ty <= Math.floor((oy + hgt) / TILE); ty++) {
        if (ty < 0 || ty >= n) continue;
        const img = h('img', { src: `https://tile.openstreetmap.org/${z}/${((tx % n) + n) % n}/${ty}.png`, alt: '', draggable: 'false', loading: 'lazy' });
        img.onerror = () => img.remove();
        img.style.left = (tx * TILE - ox) + 'px'; img.style.top = (ty * TILE - oy) + 'px';
        frag.append(img);
      }
    }
    this.tiles.replaceChildren(frag);
    this.svg.replaceChildren(...this.circles.map((c) => {
      const px = lon2x(c.lon, z) - ox, py = lat2y(c.lat, z) - oy;
      const edge = lat2y(c.lat + c.km / 111.2, z) - oy;
      const el = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      el.setAttribute('cx', px); el.setAttribute('cy', py); el.setAttribute('r', Math.abs(py - edge));
      el.setAttribute('fill', 'none'); el.setAttribute('stroke', c.color || '#60a5fa'); el.setAttribute('stroke-dasharray', '5 4'); el.setAttribute('stroke-width', '1.5');
      return el;
    }));
    this.overlay.replaceChildren(...this.markers.map((m) => {
      const px = lon2x(m.lon, z) - ox, py = lat2y(m.lat, z) - oy;
      const s = m.size || 12;
      const el = h('div', { class: 'mk', title: m.title || '', style: { left: px + 'px', top: py + 'px', width: s + 'px', height: s + 'px',
        background: m.color || '#ef4444', border: '2px solid #fff', borderRadius: m.square ? '3px' : '50%', boxShadow: '0 1px 4px rgba(0,0,0,.5)', cursor: m.onclick ? 'pointer' : 'default' } });
      if (m.onclick) el.onclick = (e) => { e.stopPropagation(); m.onclick(); };
      return el;
    }));
  }
  _bind() {
    let drag = null, moved = false;
    this.el.addEventListener('pointerdown', (e) => { if (e.target.tagName === 'BUTTON') return; drag = [e.clientX, e.clientY, lon2x(this.lon, this.z), lat2y(this.lat, this.z)]; moved = false; this.el.setPointerCapture(e.pointerId); });
    this.el.addEventListener('pointermove', (e) => {
      if (!drag) return; const dx = e.clientX - drag[0], dy = e.clientY - drag[1];
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      this.lon = x2lon(drag[2] - dx, this.z); this.lat = Math.max(-85, Math.min(85, y2lat(drag[3] - dy, this.z))); this.render();
    });
    this.el.addEventListener('pointerup', (e) => {
      if (drag && !moved && this.onclick && e.target.tagName !== 'BUTTON') {
        const r = this.el.getBoundingClientRect(), w = this.el.clientWidth, hgt = this.el.clientHeight;
        const x = lon2x(this.lon, this.z) - w / 2 + (e.clientX - r.left), y = lat2y(this.lat, this.z) - hgt / 2 + (e.clientY - r.top);
        this.onclick(y2lat(y, this.z), x2lon(x, this.z));
      }
      drag = null;
    });
    this.el.addEventListener('wheel', (e) => { e.preventDefault(); this.zoom(e.deltaY < 0 ? 1 : -1); }, { passive: false });
  }
}
