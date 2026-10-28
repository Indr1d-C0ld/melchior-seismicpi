"""Cataloghi sismici ufficiali (FDSN event, formato testo): INGV, EMSC, USGS.

Servono a confermare gli eventi locali: per ogni terremoto di catalogo si calcolano distanza,
azimut e tempi d'arrivo teorici P/S alla stazione; se un trigger locale cade nella finestra
d'arrivo, l'evento viene associato e marcato "sisma_confermato".
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import time

from . import dsp

log = logging.getLogger("seismicpi.catalogs")

SOURCES = {
    "ingv": ("INGV", "https://webservices.ingv.it/fdsnws/event/1/query"),
    "emsc": ("EMSC", "https://www.seismicportal.eu/fdsnws/event/1/query"),
    "usgs": ("USGS", "https://earthquake.usgs.gov/fdsnws/event/1/query"),
}


def _iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parse_text(source: str, body: str) -> list[dict]:
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if not lines:
        return []
    head = [h.strip().lstrip("#").strip().lower() for h in lines[0].split("|")]

    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        return None
    ci = {k: col(*v) for k, v in {
        "id": ("eventid",), "time": ("time",), "lat": ("latitude",), "lon": ("longitude",),
        "depth": ("depth/km", "depth"), "magtype": ("magtype",), "mag": ("magnitude",),
        "place": ("eventlocationname",)}.items()}
    out = []
    for ln in lines[1:]:
        if ln.startswith("#"):
            continue
        f = [x.strip() for x in ln.split("|")]
        try:
            ts = f[ci["time"]].replace("Z", "")
            t = dt.datetime.fromisoformat(ts).replace(tzinfo=dt.timezone.utc).timestamp()
            ev = {"id": f"{source}:{f[ci['id']]}", "source": source, "t_origin": t,
                  "lat": float(f[ci["lat"]]), "lon": float(f[ci["lon"]]),
                  "depth_km": float(f[ci["depth"]] or 0), "mag": float(f[ci["mag"]] or 0),
                  "mag_type": f[ci["magtype"]] if ci["magtype"] is not None else "",
                  "place": f[ci["place"]] if ci["place"] is not None else ""}
        except (ValueError, IndexError, TypeError):
            continue
        out.append(ev)
    return out


async def fetch(session, source: str, params: dict) -> list[dict]:
    url = SOURCES[source][1]
    q = {"format": "text", "orderby": "time", **params}
    async with session.get(url, params=q, timeout=30) as r:
        if r.status == 204:
            return []
        if r.status != 200:
            raise RuntimeError(f"{SOURCES[source][0]}: HTTP {r.status}")
        return parse_text(source, await r.text())


def enrich(ev: dict, lat, lon) -> dict:
    if lat is None or lon is None:
        ev.update(dist_km=None, azimuth=None, t_p=None, t_s=None)
        return ev
    d = dsp.haversine_km(lat, lon, ev["lat"], ev["lon"])
    tp, ts = dsp.travel_times(d, ev["depth_km"])
    ev.update(dist_km=round(d, 1), azimuth=round(dsp.bearing_deg(lat, lon, ev["lat"], ev["lon"]), 1),
              t_p=ev["t_origin"] + tp, t_s=ev["t_origin"] + ts)
    return ev


def is_duplicate(con, ev) -> bool:
    """Lo stesso terremoto arriva da più agenzie: si tiene il primo (le sorgenti sono in ordine di priorità)."""
    r = con.execute("SELECT lat, lon, source FROM catalog WHERE id != ? AND source != ? AND ABS(t_origin - ?) < 15",
                    (ev["id"], ev["source"], ev["t_origin"])).fetchall()
    return any(dsp.haversine_km(x[0], x[1], ev["lat"], ev["lon"]) < 80 for x in r)


def store(con, events: list[dict]) -> list[dict]:
    """Inserisce gli eventi nuovi e restituisce quelli effettivamente aggiunti."""
    new = []
    now = time.time()
    for ev in events:
        if con.execute("SELECT 1 FROM catalog WHERE id=?", (ev["id"],)).fetchone():
            con.execute("UPDATE catalog SET mag=?, mag_type=?, lat=?, lon=?, depth_km=?, place=?, dist_km=?,"
                        " azimuth=?, t_p=?, t_s=?, fetched=? WHERE id=?",
                        (ev["mag"], ev["mag_type"], ev["lat"], ev["lon"], ev["depth_km"], ev["place"],
                         ev["dist_km"], ev["azimuth"], ev["t_p"], ev["t_s"], now, ev["id"]))
            continue
        if is_duplicate(con, ev):
            continue
        con.execute("INSERT INTO catalog(id, source, t_origin, lat, lon, depth_km, mag, mag_type, place, dist_km,"
                    " azimuth, t_p, t_s, fetched) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ev["id"], ev["source"], ev["t_origin"], ev["lat"], ev["lon"], ev["depth_km"], ev["mag"],
                     ev["mag_type"], ev["place"], ev["dist_km"], ev["azimuth"], ev["t_p"], ev["t_s"], now))
        new.append(ev)
    return new


def detectable(mag, dist_km, margin=0.5) -> bool:
    """Un MEMS da 2-4 mg di rumore vede in pratica: M2 a 10 km, M4 a 100 km, M6 a 1000 km
    (stima empirica prudente; margin abbassa la soglia per non perdere casi limite)."""
    if mag is None or dist_km is None:
        return False
    return mag >= 2 + 2 * math.log10(max(dist_km, 10) / 10) - margin


def correlate(con, window_s: float, since: float) -> list[tuple[int, str]]:
    """Associa eventi locali non ancora associati agli eventi di catalogo compatibili.

    Un trigger è compatibile se cade tra (arrivo P − finestra) e (arrivo S + finestra + durata
    stimata della coda). Restituisce le coppie (evento locale, evento catalogo) appena associate.
    """
    matches = []
    cats = con.execute("SELECT * FROM catalog WHERE t_origin > ? AND t_p IS NOT NULL ORDER BY mag DESC",
                       (since,)).fetchall()
    for c in cats:
        if not detectable(c["mag"], c["dist_km"]):
            continue
        coda = min(300, 10 + (c["dist_km"] or 0) / 20)
        lo, hi = c["t_p"] - window_s, c["t_s"] + window_s + coda
        r = con.execute("SELECT id, extra, class FROM events WHERE catalog_id IS NULL AND t_trigger BETWEEN ? AND ?"
                        " AND class NOT IN ('impulso', 'manuale', 'test') ORDER BY pga DESC LIMIT 1", (lo, hi)).fetchone()
        if not r:
            continue
        extra = json.loads(r["extra"] or "{}")
        extra["auto_class"] = r["class"]
        con.execute("UPDATE events SET catalog_id=?, class='sisma_confermato', confidence=1.0, extra=? WHERE id=?",
                    (c["id"], json.dumps(extra), r["id"]))
        if c["local_event"] is None:
            con.execute("UPDATE catalog SET local_event=? WHERE id=?", (r["id"], c["id"]))
        matches.append((r["id"], c["id"]))
    return matches


async def poll(con, session, cfg) -> dict:
    """Un giro di aggiornamento su tutte le sorgenti configurate."""
    cc = cfg["catalogs"]
    st = cfg["station"]
    lat, lon = st["latitude"], st["longitude"]
    now = time.time()
    t1 = now - cc["lookback_hours"] * 3600
    got, errors = [], []
    for src in cc["sources"]:
        if src not in SOURCES:
            continue
        queries = [{"starttime": _iso(t1), "minmagnitude": cc["global_min_mag"]}]
        if lat is not None and lon is not None:
            queries.append({"starttime": _iso(t1), "minmagnitude": cc["min_mag"], "latitude": lat,
                            "longitude": lon, "maxradius": round(cc["radius_km"] / 111.195, 3)})
        for q in queries:
            try:
                got += await fetch(session, src, q)
            except Exception as e:  # noqa: BLE001 – rete assente, servizio giù: si riprova al giro dopo
                errors.append(f"{SOURCES[src][0]}: {e}")
    uniq = {e["id"]: enrich(e, lat, lon) for e in got}
    order = {s: i for i, s in enumerate(cc["sources"])}
    events = sorted(uniq.values(), key=lambda e: order.get(e["source"], 9))
    new = store(con, events)
    matches = correlate(con, cc["match_window_s"], now - max(cc["lookback_hours"], 72) * 3600)
    return {"fetched": len(events), "new": new, "matches": matches, "errors": errors, "t": now}
