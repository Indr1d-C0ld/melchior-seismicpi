"""Dashboard web e API (aiohttp): autenticazione, configurazione, eventi, forme d'onda, rullo,
statistiche, cataloghi, rete, sistema, WebSocket live. Ospita anche notifiche e cataloghi.

    python3 -m seismicpi.web
"""
from __future__ import annotations

import array
import asyncio
import base64
import concurrent.futures
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import logging
import os
import secrets
import shutil
import subprocess
import time

import aiohttp
from aiohttp import web

from .. import VERSION, analysis, catalogs, config, db, dsp, ipc, miniseed, netmgr, storage, sysinfo
from ..led import PATTERN_NAMES, LedController
from ..notify import Notifier

log = logging.getLogger("seismicpi.web")

STATIC = os.path.join(os.path.dirname(__file__), "static")
COOKIE = "seismicpi_session"
PBKDF2_ITER = 30000           # il Pi Zero è lento: ~1 s per verifica
routes = web.RouteTableDef()


def J(data, status=200):
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, default=str, separators=(",", ":")))


def err(msg, status=400):
    return J({"ok": False, "error": msg}, status)


# ======================================================================== password e sessioni

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ITER)
    return f"pbkdf2${PBKDF2_ITER}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, h = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt), int(it))
        return hmac.compare_digest(calc, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def session_ok(request) -> bool:
    tok = request.cookies.get(COOKIE)
    if not tok:
        return False
    r = request.app["db"].execute("SELECT expires FROM sessions WHERE token=?", (tok,)).fetchone()
    return bool(r and r[0] > time.time())


PUBLIC = {("GET", "/api/auth"), ("POST", "/api/auth/login"), ("POST", "/api/auth/setup")}


@web.middleware
async def auth_mw(request, handler):
    path = request.path
    if not (path.startswith("/api/") or path == "/ws"):
        return await handler(request)
    if (request.method, path) in PUBLIC:
        return await handler(request)
    cfg = request.app["cfg"]
    if not cfg["web"]["password_hash"]:
        return err("configurazione iniziale: imposta la password", 401)
    logged = session_ok(request)
    request["admin"] = logged
    if logged:
        return await handler(request)
    if cfg["web"]["public_readonly"] and request.method == "GET":
        return await handler(request)
    return err("accesso richiesto", 401)


@web.middleware
async def cache_mw(request, handler):
    """I file statici si rivalidano sempre (304 se invariati): dopo un aggiornamento il browser
    non deve tenere in cache moduli JavaScript vecchi."""
    resp = await handler(request)
    if request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def admin(fn):
    async def wrap(request):
        if not request.get("admin"):
            return err("serve l'accesso amministratore", 403)
        return await fn(request)
    wrap.__name__ = fn.__name__
    return wrap


@routes.get("/api/auth")
async def auth_info(request):
    cfg = request.app["cfg"]
    return J({"setup_needed": not cfg["web"]["password_hash"], "logged_in": session_ok(request),
              "public_readonly": cfg["web"]["public_readonly"], "station": cfg["station"]["name"],
              "username": cfg["web"]["username"] if session_ok(request) else None,
              "version": VERSION})


async def _new_session(request, resp):
    tok = secrets.token_urlsafe(32)
    days = request.app["cfg"]["web"]["session_days"]
    request.app["db"].execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                              (tok, time.time(), time.time() + days * 86400,
                               request.headers.get("User-Agent", "")[:200], request.remote))
    resp.set_cookie(COOKIE, tok, max_age=days * 86400, httponly=True, samesite="Strict")
    return resp


@routes.post("/api/auth/setup")
async def auth_setup(request):
    if request.app["cfg"]["web"]["password_hash"]:
        return err("password già impostata", 409)
    body = await request.json()
    pw = body.get("password", "")
    user = (body.get("username") or "admin").strip()
    if len(pw) < 6:
        return err("la password deve avere almeno 6 caratteri")
    h = await asyncio.get_running_loop().run_in_executor(None, hash_password, pw)
    await request.app["set_config"]({"web": {"password_hash": h, "username": user}})
    return await _new_session(request, J({"ok": True}))


@routes.post("/api/auth/login")
async def auth_login(request):
    app = request.app
    ip = request.remote
    fails = app["login_fails"].get(ip, [])
    fails = [t for t in fails if t > time.time() - 300]
    if len(fails) >= 8:
        return err("troppi tentativi: riprova tra qualche minuto", 429)
    body = await request.json()
    ok = await asyncio.get_running_loop().run_in_executor(
        None, check_password, body.get("password", ""), app["cfg"]["web"]["password_hash"])
    ok = ok and body.get("username", "").strip().lower() == app["cfg"]["web"]["username"].lower()
    if not ok:
        fails.append(time.time())
        app["login_fails"][ip] = fails
        await asyncio.sleep(1)
        return err("utente o password errati", 401)
    app["login_fails"].pop(ip, None)
    return await _new_session(request, J({"ok": True}))


@routes.post("/api/auth/logout")
async def auth_logout(request):
    tok = request.cookies.get(COOKIE)
    if tok:
        request.app["db"].execute("DELETE FROM sessions WHERE token=?", (tok,))
    resp = J({"ok": True})
    resp.del_cookie(COOKIE)
    return resp


@routes.post("/api/auth/password")
@admin
async def auth_password(request):
    body = await request.json()
    loop = asyncio.get_running_loop()
    if not await loop.run_in_executor(None, check_password, body.get("old", ""),
                                      request.app["cfg"]["web"]["password_hash"]):
        return err("password attuale errata", 403)
    if len(body.get("new", "")) < 6:
        return err("la nuova password deve avere almeno 6 caratteri")
    h = await loop.run_in_executor(None, hash_password, body["new"])
    await request.app["set_config"]({"web": {"password_hash": h}})
    tok = request.cookies.get(COOKIE)
    request.app["db"].execute("DELETE FROM sessions WHERE token != ?", (tok,))
    return J({"ok": True})


@routes.get("/api/sessions")
@admin
async def sessions_list(request):
    tok = request.cookies.get(COOKIE)
    rows = db.rows(request.app["db"].execute("SELECT * FROM sessions WHERE expires > ? ORDER BY created DESC",
                                             (time.time(),)))
    for r in rows:
        r["current"] = r["token"] == tok
        r["token"] = r["token"][:6] + "…"
    return J(rows)


@routes.delete("/api/sessions")
@admin
async def sessions_clear(request):
    tok = request.cookies.get(COOKIE)
    request.app["db"].execute("DELETE FROM sessions WHERE token != ?", (tok,))
    return J({"ok": True})


# ======================================================================== stato e configurazione

def system_summary(app) -> dict:
    du = storage.disk_usage(app["data_dir"])
    mem = sysinfo.meminfo()
    return {"cpu_temp": sysinfo.cpu_temp(), "load": sysinfo.loadavg(), "mem_pct": mem["pct"],
            "disk_pct": du["pct"], "disk_free": du["free"], "uptime": sysinfo.uptime(),
            "cpu_pct": app["cpu_last"], "hostname": netmgr.hostname()}


@routes.get("/api/status")
async def status(request):
    app = request.app
    n = app["notifier"]
    t_day = time.time() - 86400
    c = app["db"].execute("SELECT COUNT(*), MAX(pga) FROM events WHERE t_trigger > ?", (t_day,)).fetchone()
    last = app["db"].execute("SELECT * FROM events ORDER BY t_trigger DESC LIMIT 1").fetchone()
    return J({
        "now": time.time(), "version": VERSION, "admin": bool(request.get("admin")),
        "acq": app["acq_status"], "acq_connected": app["acq"].connected.is_set(),
        "system": system_summary(app), "network": netmgr.read_status(),
        "alerts": {"muted_until": app["cfg"]["alerts"]["muted_until"], "telegram_ok": n.tg_ok,
                   "telegram_error": n.tg_error, "mqtt_error": n.mqtt_error,
                   "telegram_bot": (n.tg_bot or {}).get("username")},
        "catalog": app["catalog_status"], "led": app["led"].status(),
        "events_24h": c[0], "max_pga_24h": c[1], "last_event": dict(last) if last else None,
    })


@routes.get("/api/config")
async def get_config(request):
    return J(config.redacted(request.app["cfg"]))


@routes.patch("/api/config")
@admin
async def patch_config(request):
    changes = config.strip_unchanged_secrets(await request.json())
    if "web" in changes:
        changes["web"].pop("password_hash", None)
    try:
        await request.app["set_config"](changes)
    except config.ConfigError as e:
        return err(str(e))
    return J({"ok": True, "config": config.redacted(request.app["cfg"])})


@routes.get("/api/meta")
async def meta(request):
    return J({"classes": analysis.CLASSES, "labels": analysis.LABELS, "mmi": dsp.MMI_DESC,
              "sources": {k: v[0] for k, v in catalogs.SOURCES.items()}, "defaults": config.DEFAULTS})


# ======================================================================== eventi

EVENT_SORT = {"t_trigger", "pga", "pgv", "mmi", "duration", "dom_freq", "peak_ratio", "id", "class"}


def _event_filters(q):
    where, args = [], []
    if q.get("from"):
        where.append("t_trigger >= ?"); args.append(float(q["from"]))
    if q.get("to"):
        where.append("t_trigger < ?"); args.append(float(q["to"]))
    if q.get("cls"):
        cls = q["cls"].split(",")
        where.append(f"class IN ({','.join('?' * len(cls))})"); args += cls
    if q.get("label"):
        if q["label"] == "_none":
            where.append("(label IS NULL OR label = '')")
        else:
            where.append("label = ?"); args.append(q["label"])
    if q.get("min_pga"):
        where.append("pga >= ?"); args.append(float(q["min_pga"]))
    if q.get("min_mmi"):
        where.append("mmi >= ?"); args.append(float(q["min_mmi"]))
    if q.get("starred") == "1":
        where.append("starred = 1")
    if q.get("confirmed") == "1":
        where.append("catalog_id IS NOT NULL")
    if q.get("q"):
        where.append("(notes LIKE ? OR label LIKE ? OR CAST(id AS TEXT) = ?)")
        args += [f"%{q['q']}%", f"%{q['q']}%", q["q"]]
    return (" WHERE " + " AND ".join(where)) if where else "", args


@routes.get("/api/events")
async def events_list(request):
    q = request.query
    w, args = _event_filters(q)
    sort = q.get("sort", "t_trigger")
    sort = sort if sort in EVENT_SORT else "t_trigger"
    order = "ASC" if q.get("order") == "asc" else "DESC"
    limit = max(1, min(int(q.get("limit", 50)), 1000))
    offset = max(0, int(q.get("offset", 0)))
    con = request.app["db"]
    total = con.execute(f"SELECT COUNT(*) FROM events{w}", args).fetchone()[0]
    rows = db.rows(con.execute(
        f"SELECT e.*, c.mag AS cat_mag, c.place AS cat_place, c.dist_km AS cat_dist, c.source AS cat_source "
        f"FROM (SELECT * FROM events{w}) e LEFT JOIN catalog c ON c.id = e.catalog_id "
        f"ORDER BY {sort} {order} LIMIT ? OFFSET ?", args + [limit, offset]))
    return J({"total": total, "items": rows})


def _event_or_404(request):
    eid = int(request.match_info["id"])
    r = request.app["db"].execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    if not r:
        raise web.HTTPNotFound(text="evento inesistente")
    e = dict(r)
    e["extra"] = json.loads(e.get("extra") or "{}")
    return e


@routes.get("/api/events/{id:\\d+}")
async def event_get(request):
    e = _event_or_404(request)
    con = request.app["db"]
    if e["catalog_id"]:
        c = con.execute("SELECT * FROM catalog WHERE id=?", (e["catalog_id"],)).fetchone()
        e["catalog"] = dict(c) if c else None
    # candidati di catalogo nella finestra (per l'associazione manuale)
    e["candidates"] = db.rows(con.execute(
        "SELECT * FROM catalog WHERE t_origin BETWEEN ? AND ? ORDER BY mag DESC LIMIT 20",
        (e["t_trigger"] - 1500, e["t_trigger"] + 60)))
    prev = con.execute("SELECT id FROM events WHERE t_trigger < ? ORDER BY t_trigger DESC LIMIT 1", (e["t_trigger"],)).fetchone()
    nxt = con.execute("SELECT id FROM events WHERE t_trigger > ? ORDER BY t_trigger LIMIT 1", (e["t_trigger"],)).fetchone()
    e["prev_id"], e["next_id"] = (prev[0] if prev else None), (nxt[0] if nxt else None)
    e["class_desc"] = analysis.CLASSES.get(e["class"], e["class"])
    e["mmi_roman"] = dsp.mmi_roman(e["mmi"] or 1)
    e["mmi_desc"] = dsp.mmi_description(e["mmi"] or 1)
    return J(e)


@routes.patch("/api/events/{id:\\d+}")
@admin
async def event_patch(request):
    e = _event_or_404(request)
    body = await request.json()
    sets, args = [], []
    for k in ("label", "notes", "starred", "class"):
        if k in body:
            v = body[k]
            if k == "class" and v not in analysis.CLASSES:
                return err("classe non valida")
            if k == "label" and v and v not in analysis.LABELS:
                return err("etichetta non valida")
            sets.append(f"{k}=?"); args.append(v)
    if "catalog_id" in body:
        cid = body["catalog_id"] or None
        sets.append("catalog_id=?"); args.append(cid)
        con = request.app["db"]
        con.execute("UPDATE catalog SET local_event=NULL WHERE local_event=?", (e["id"],))
        if cid:
            con.execute("UPDATE catalog SET local_event=? WHERE id=?", (e["id"], cid))
            sets.append("class='sisma_confermato'")
    if sets:
        request.app["db"].execute(f"UPDATE events SET {','.join(sets)} WHERE id=?", args + [e["id"]])
    return J({"ok": True})


@routes.delete("/api/events/{id:\\d+}")
@admin
async def event_delete(request):
    e = _event_or_404(request)
    con = request.app["db"]
    con.execute("DELETE FROM events WHERE id=?", (e["id"],))
    con.execute("UPDATE catalog SET local_event=NULL WHERE local_event=?", (e["id"],))
    request.app["events"].delete(e["id"])
    return J({"ok": True})


@routes.post("/api/events/bulk")
@admin
async def events_bulk(request):
    body = await request.json()
    ids = [int(i) for i in body.get("ids", [])][:5000]
    con = request.app["db"]
    action = body.get("action")
    for i in ids:
        if action == "delete":
            con.execute("DELETE FROM events WHERE id=?", (i,))
            con.execute("UPDATE catalog SET local_event=NULL WHERE local_event=?", (i,))
            request.app["events"].delete(i)
        elif action == "label":
            con.execute("UPDATE events SET label=? WHERE id=?", (body.get("label") or None, i))
        elif action == "star":
            con.execute("UPDATE events SET starred=? WHERE id=?", (1 if body.get("value") else 0, i))
    return J({"ok": True, "count": len(ids)})


def _series_json(blocks, t1, t2, max_points=None):
    ser = storage.blocks_to_series(blocks, t1, t2)
    if not ser:
        return {"t0": t1, "fs": 0, "e": [], "n": [], "z": []}
    t0, fs, e, n, z = ser
    step = 1
    if max_points and len(e) > max_points:
        step = -(-len(e) // max_points)

    def conv(v):
        return [None if x is None else round(x * 1000, 2) for x in v[::step]]
    return {"t0": t0, "fs": fs / step, "decimated": step, "e": conv(e), "n": conv(n), "z": conv(z)}


@routes.get("/api/events/{id:\\d+}/waveform")
async def event_waveform(request):
    e = _event_or_404(request)
    blocks = request.app["events"].load(e["id"])
    mp = int(request.query.get("max_points", 0)) or None
    data = await asyncio.get_running_loop().run_in_executor(None, _series_json, blocks, e["t_start"], e["t_end"], mp)
    data["t_trigger"] = e["t_trigger"]
    return J(data)


@routes.get("/api/events/{id:\\d+}/export.{fmt}")
async def event_export(request):
    e = _event_or_404(request)
    fmt = request.match_info["fmt"]
    blocks = request.app["events"].load(e["id"])
    name = f"seismicpi_{e['id']}_{dt.datetime.fromtimestamp(e['t_trigger'], dt.timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    return await _export(request, blocks, e["t_start"], e["t_end"], fmt, name, meta=e)


@routes.get("/api/events/{id:\\d+}/image.png")
async def event_image(request):
    e = _event_or_404(request)
    png = await request.app["notifier"].event_png(e["id"])
    if not png:
        raise web.HTTPNotFound()
    return web.Response(body=png, content_type="image/png")


async def _export(request, blocks, t1, t2, fmt, name, meta=None):
    cfg = request.app["cfg"]
    st = cfg["station"]
    loop = asyncio.get_running_loop()
    if fmt == "mseed":
        band = dsp.seed_band(cfg["sensor"]["sample_rate"])
        body = await loop.run_in_executor(None, miniseed.write, blocks, st["network"], st["code"], st["location"], band)
        ctype = "application/vnd.fdsn.mseed"
    elif fmt == "csv":
        def build():
            ser = storage.blocks_to_series(blocks, t1, t2)
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["time_utc", "epoch", "E_mg", "N_mg", "Z_mg"])
            if ser:
                t0, fs, e, n, z = ser
                for i in range(len(e)):
                    if e[i] is None:
                        continue
                    t = t0 + i / fs
                    w.writerow([dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat(timespec="milliseconds"), f"{t:.3f}",
                                f"{e[i] * 1000:.3f}", f"{n[i] * 1000:.3f}", f"{z[i] * 1000:.3f}"])
            return out.getvalue().encode()
        body = await loop.run_in_executor(None, build)
        ctype = "text/csv"
    elif fmt == "json":
        data = await loop.run_in_executor(None, _series_json, blocks, t1, t2, None)
        data["station"] = st
        data["units"] = "mg"
        if meta:
            data["event"] = meta
        body = json.dumps(data, default=str).encode()
        ctype = "application/json"
    else:
        raise web.HTTPNotFound()
    return web.Response(body=body, content_type=ctype,
                        headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})


def raw_archive(app):
    base = storage.raw_base(app["cfg"])
    return storage.RawArchive(base) if base else None


async def _raw_blocks(app, t1, t2):
    raw = raw_archive(app)
    blocks = await asyncio.get_running_loop().run_in_executor(None, raw.read, t1, t2) if raw else []
    if t2 > time.time() - 30 and app["acq"].connected.is_set():
        try:
            r = await app["acq"].call("recent", seconds=min(120, time.time() - t1 + 1), timeout=10)
            seen = {round(b[0], 3) for b in blocks}
            for b in r.get("blocks", []):
                if round(b["t0"], 3) not in seen and b["t0"] < t2:
                    blocks.append((b["t0"], b["fs"], b["lsb"], array.array("h", b["d"])))
            blocks.sort(key=lambda b: b[0])
        except (ConnectionError, asyncio.TimeoutError):
            pass
    return blocks


@routes.get("/api/waveform")
async def waveform(request):
    q = request.query
    t1, t2 = float(q["from"]), float(q["to"])
    if t2 <= t1 or t2 - t1 > 3600:
        return err("finestra non valida (max 60 minuti)")
    blocks = await _raw_blocks(request.app, t1, t2)
    mp = int(q.get("max_points", 0)) or None
    return J(await asyncio.get_running_loop().run_in_executor(None, _series_json, blocks, t1, t2, mp))


@routes.get("/api/export/raw.{fmt}")
async def export_raw(request):
    q = request.query
    t1, t2 = float(q["from"]), float(q["to"])
    if t2 <= t1 or t2 - t1 > 6 * 3600:
        return err("finestra non valida (max 6 ore)")
    blocks = await _raw_blocks(request.app, t1, t2)
    if not blocks:
        return err("nessun dato nell'intervallo (i dati grezzi vengono conservati per "
                   f"{request.app['cfg']['storage']['raw_retention_days']} giorni)", 404)
    name = f"seismicpi_raw_{dt.datetime.fromtimestamp(t1, dt.timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    return await _export(request, blocks, t1, t2, request.match_info["fmt"], name)


@routes.get("/api/export/events.csv")
async def export_events(request):
    w, args = _event_filters(request.query)
    rows = db.rows(request.app["db"].execute(f"SELECT * FROM events{w} ORDER BY t_trigger", args))
    out = io.StringIO()
    cols = ["id", "time_utc"] + [c for c in db.EVENT_FIELDS if c != "extra"] + ["label", "notes", "starred", "catalog_id"]
    wr = csv.writer(out)
    wr.writerow(cols)
    for r in rows:
        r["time_utc"] = dt.datetime.fromtimestamp(r["t_trigger"], dt.timezone.utc).isoformat(timespec="seconds")
        wr.writerow([r.get(c) for c in cols])
    return web.Response(text=out.getvalue(), content_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="seismicpi_eventi.csv"'})


# ======================================================================== rullo (helicorder)

@routes.get("/api/heli/days")
async def heli_days(request):
    return J(request.app["heli"].days())


@routes.get("/api/heli")
async def heli(request):
    q = request.query
    key = q.get("day") or storage.day_key(time.time())
    ch = q.get("ch", "Z")
    if ch not in storage.CHANNELS or not key.isdigit():
        return err("parametri non validi")
    merge = max(1, min(int(q.get("merge", 1)), 240))
    s_from = int(q.get("slot_from", 0))
    s_to = int(q.get("slot_to", storage.SLOTS_PER_DAY))
    data = await asyncio.get_running_loop().run_in_executor(
        None, request.app["heli"].read, key, ch, s_from, s_to, merge)
    return web.Response(body=data, content_type="application/octet-stream",
                        headers={"X-Day-Start": str(storage.day_start(key)), "X-Merge": str(merge),
                                 "X-Slot-Seconds": str(storage.SLOT_S), "Cache-Control": "no-store"})


@routes.get("/api/heli/marks")
async def heli_marks(request):
    q = request.query
    t1, t2 = float(q["from"]), float(q["to"])
    con = request.app["db"]
    ev = db.rows(con.execute("SELECT id, t_trigger, t_end, class, pga, mmi, label FROM events "
                             "WHERE t_trigger BETWEEN ? AND ?", (t1, t2)))
    cat = db.rows(con.execute("SELECT id, t_origin, t_p, t_s, mag, place, dist_km, local_event FROM catalog "
                              "WHERE t_p BETWEEN ? AND ? ORDER BY mag DESC LIMIT 500", (t1, t2)))
    # sul rullo solo i terremoti che un MEMS può plausibilmente vedere (o già associati):
    # M ≥ 2 a 10 km, M ≥ 4 a 100 km, M ≥ 6 a 1000 km
    cat = [c for c in cat if c["local_event"] or catalogs.detectable(c["mag"], c["dist_km"], margin=0)][:100]
    return J({"events": ev, "catalog": cat})


# ======================================================================== statistiche

def _ro(app, fn, *a):
    return asyncio.get_running_loop().run_in_executor(app["ro_pool"], lambda: fn(app["ro_con"](), *a))


@routes.get("/api/stats/minute")
async def stats_minute(request):
    q = request.query
    t2 = float(q.get("to", time.time()))
    t1 = float(q.get("from", t2 - 86400))
    step = max(60, int(q.get("step", 60)))

    def run(con):
        return db.rows(con.execute(
            "SELECT (t/?)*? AS t, AVG(rms_e) rms_e, AVG(rms_n) rms_n, AVG(rms_z) rms_z, AVG(rms_v) rms_v, "
            "MIN(rms_v) rms_v_min, MAX(rms_v) rms_v_max, MAX(peak_v) peak_v, AVG(mean_e) mean_e, AVG(mean_n) mean_n, "
            "AVG(mean_z) mean_z, AVG(temp_c) temp_c, AVG(fs) fs, SUM(gaps) gaps, SUM(overflows) overflows, "
            "SUM(i2c_errors) i2c_errors, MAX(stalta_max) stalta_max, COUNT(*) n "
            "FROM stats_minute WHERE t BETWEEN ? AND ? GROUP BY 1 ORDER BY 1", (step, step, t1, t2)))
    return J(await _ro(request.app, run))


@routes.get("/api/stats/system")
async def stats_system(request):
    q = request.query
    t2 = float(q.get("to", time.time()))
    t1 = float(q.get("from", t2 - 86400))
    step = max(60, int(q.get("step", 300)))

    def run(con):
        return db.rows(con.execute(
            "SELECT (t/?)*? AS t, AVG(cpu_pct) cpu_pct, MAX(cpu_temp) cpu_temp, AVG(load1) load1, AVG(mem_pct) mem_pct, "
            "MAX(disk_pct) disk_pct, AVG(wifi_signal) wifi_signal, AVG(acq_cpu) acq_cpu, AVG(cpu_freq) cpu_freq, "
            "SUM(sd_write_kb) sd_write_kb, SUM(net_rx_kb) net_rx_kb, SUM(net_tx_kb) net_tx_kb, MAX(undervoltage) undervoltage, "
            "MIN(mem_avail_mb) mem_avail_mb FROM sys_stats "
            "WHERE t BETWEEN ? AND ? GROUP BY 1 ORDER BY 1", (step, step, t1, t2)))
    return J(await _ro(request.app, run))


@routes.get("/api/stats/noise_profile")
async def noise_profile(request):
    days = max(1, min(int(request.query.get("days", 14)), 365))

    def run(con):
        rows = con.execute(
            "SELECT CAST(strftime('%w', t, 'unixepoch', 'localtime') AS INT) wd, "
            "CAST(strftime('%H', t, 'unixepoch', 'localtime') AS INT) h, AVG(rms_v), MAX(peak_v), COUNT(*) "
            "FROM stats_minute WHERE t > ? GROUP BY 1, 2", (time.time() - days * 86400,)).fetchall()
        m = [[None] * 24 for _ in range(7)]
        pk = [[None] * 24 for _ in range(7)]
        for wd, h, avg, peak, n in rows:
            m[wd][h] = round(avg, 3) if avg is not None else None
            pk[wd][h] = round(peak, 2) if peak is not None else None
        hourly = con.execute(
            "SELECT CAST(strftime('%H', t, 'unixepoch', 'localtime') AS INT) h, AVG(rms_v), MIN(rms_v), MAX(rms_v) "
            "FROM stats_minute WHERE t > ? GROUP BY 1 ORDER BY 1", (time.time() - days * 86400,)).fetchall()
        return {"matrix": m, "peak": pk, "hourly": [list(r) for r in hourly], "days": days}
    return J(await _ro(request.app, run))


@routes.get("/api/stats/events")
async def stats_events(request):
    days = max(1, min(int(request.query.get("days", 30)), 3650))
    t1 = time.time() - days * 86400

    def run(con):
        daily = con.execute(
            "SELECT strftime('%Y-%m-%d', t_trigger, 'unixepoch', 'localtime') d, class, COUNT(*) FROM events "
            "WHERE t_trigger > ? GROUP BY 1, 2 ORDER BY 1", (t1,)).fetchall()
        hours = con.execute(
            "SELECT CAST(strftime('%H', t_trigger, 'unixepoch', 'localtime') AS INT) h, class, COUNT(*) FROM events "
            "WHERE t_trigger > ? GROUP BY 1, 2", (t1,)).fetchall()
        by_class = con.execute("SELECT class, COUNT(*), AVG(pga), MAX(pga), AVG(duration) FROM events "
                               "WHERE t_trigger > ? GROUP BY 1", (t1,)).fetchall()
        by_label = con.execute("SELECT COALESCE(label, ''), COUNT(*) FROM events WHERE t_trigger > ? GROUP BY 1",
                               (t1,)).fetchall()
        pgas = [r[0] for r in con.execute("SELECT pga FROM events WHERE t_trigger > ? AND pga > 0", (t1,))]
        mmis = con.execute("SELECT CAST(ROUND(mmi) AS INT), COUNT(*) FROM events WHERE t_trigger > ? GROUP BY 1",
                           (t1,)).fetchall()
        freq = con.execute("SELECT dom_freq, duration, pga, class FROM events WHERE t_trigger > ? AND dom_freq IS NOT NULL "
                           "ORDER BY t_trigger DESC LIMIT 2000", (t1,)).fetchall()
        top = db.rows(con.execute("SELECT id, t_trigger, class, pga, mmi, label FROM events WHERE t_trigger > ? "
                                  "ORDER BY pga DESC LIMIT 10", (t1,)))
        cat = con.execute("SELECT COUNT(*), SUM(local_event IS NOT NULL) FROM catalog WHERE t_origin > ? AND dist_km <= ?",
                          (t1, request.app["cfg"]["catalogs"]["radius_km"])).fetchone()
        catm = db.rows(con.execute("SELECT mag, dist_km, local_event IS NOT NULL AS seen FROM catalog "
                                   "WHERE t_origin > ? AND dist_km IS NOT NULL", (t1,)))
        # istogramma logaritmico della PGA e curva cumulativa
        bins = [0.5 * 10 ** (i / 4) for i in range(17)]
        hist = [0] * (len(bins) - 1)
        for p in pgas:
            for i in range(len(bins) - 1):
                if bins[i] <= p < bins[i + 1]:
                    hist[i] += 1
                    break
        srt = sorted(pgas, reverse=True)
        cumul = [[p, i + 1] for i, p in enumerate(srt)][:500]
        return {"days": days, "daily": [list(r) for r in daily], "hours": [list(r) for r in hours],
                "by_class": [list(r) for r in by_class], "by_label": [list(r) for r in by_label],
                "pga_bins": bins, "pga_hist": hist, "pga_cumul": cumul, "mmi": [list(r) for r in mmis],
                "freq": [list(r) for r in freq], "top": top,
                "catalog_near": cat[0] or 0, "catalog_seen": cat[1] or 0, "catalog_points": catm,
                "total": len(pgas)}
    return J(await _ro(request.app, run))


def sd_writes_estimate(cfg) -> dict:
    """Stima dei byte scritti ogni giorno sulla SD dal sismografo (escluso il sistema operativo)."""
    fs = cfg["sensor"]["sample_rate"]
    st = cfg["storage"]
    raw = int(fs * 6 * 86400 * 1.027) if st["raw_mode"] == "sd" else 0
    heli = int(86400 / storage.SLOT_S * 16)                       # contenuto effettivo del rullo
    stats = int(1440 * 200 + 1440 * 120)                           # righe statistiche + sistema (WAL incluso)
    return {"raw": raw, "helicorder": heli, "stats": stats, "events": "dipende dal numero di eventi",
            "total": raw + heli + stats}


@routes.get("/api/stats/storage")
async def stats_storage(request):
    app = request.app

    def run():
        raw = raw_archive(app)
        raw_days = [{"day": d, "bytes": raw.day_size(d)} for d in raw.days()] if raw else []
        heli = storage.dir_size(app["heli"].base)
        ev = app["events"].total_size()
        dbs = sum(os.path.getsize(p) for p in (db.DB_PATH, db.DB_PATH + "-wal") if os.path.exists(p))
        return {"raw_days": raw_days, "raw_total": sum(d["bytes"] for d in raw_days), "heli": heli, "events": ev,
                "db": dbs, "disk": storage.disk_usage(app["data_dir"]),
                "raw_mode": app["cfg"]["storage"]["raw_mode"], "ram": storage.disk_usage("/dev/shm") if os.path.isdir("/dev/shm") else None,
                "bytes_per_day": int(app["cfg"]["sensor"]["sample_rate"] * 6 * 86400 * 1.027),
                "sd_writes_per_day": sd_writes_estimate(app["cfg"])}
    return J(await asyncio.get_running_loop().run_in_executor(None, run))


# ======================================================================== cataloghi

@routes.get("/api/catalog")
async def catalog_list(request):
    q = request.query
    t1 = float(q.get("from", time.time() - 7 * 86400))
    t2 = float(q.get("to", time.time() + 3600))
    min_mag = float(q.get("min_mag", 0))
    max_dist = float(q.get("max_dist", 1e9))
    rows = db.rows(request.app["db"].execute(
        "SELECT * FROM catalog WHERE t_origin BETWEEN ? AND ? AND mag >= ? AND (dist_km IS NULL OR dist_km <= ?) "
        "ORDER BY t_origin DESC LIMIT 1000", (t1, t2, min_mag, max_dist)))
    return J({"items": rows, "status": request.app["catalog_status"]})


@routes.post("/api/catalog/refresh")
@admin
async def catalog_refresh(request):
    res = await request.app["poll_catalogs"]()
    return J({"ok": True, "fetched": res["fetched"], "new": len(res["new"]), "matches": len(res["matches"]),
              "errors": res["errors"]})


# ======================================================================== sensore

async def _acq(request, cmd, timeout=30, **kw):
    try:
        r = await request.app["acq"].call(cmd, timeout=timeout, **kw)
    except (ConnectionError, asyncio.TimeoutError) as e:
        raise web.HTTPServiceUnavailable(text=json.dumps({"ok": False, "error": f"acquisizione: {e or 'timeout'}"}),
                                         content_type="application/json")
    return r


@routes.post("/api/sensor/calibrate")
@admin
async def sensor_calibrate(request):
    """Azzera gli offset: a riposo E e N devono valere 0 g, Z 1 g (sensore in bolla)."""
    body = await request.json() if request.can_read_body else {}
    secs = float(body.get("seconds", 10))
    cfg = request.app["cfg"]
    old = cfg["sensor"]["offsets_g"]
    r = await _acq(request, "measure", timeout=secs + 15, seconds=secs)
    if not r.get("ok"):
        return err(r.get("error", "misura fallita"))
    m = r["mean_g"]          # valori orientati, prima della correzione degli offset
    new = {"E": round(m[0], 6), "N": round(m[1], 6), "Z": round(m[2] - 1.0, 6)}
    tilt = dsp_tilt(m)
    if body.get("apply", True):
        await request.app["set_config"]({"sensor": {"offsets_g": new}})
    return J({"ok": True, "mean_g": m, "std_mg": r["std_mg"], "offsets": new, "previous": old, "tilt_deg": tilt})


def dsp_tilt(m):
    import math
    h = math.hypot(m[0], m[1])
    return round(math.degrees(math.atan2(h, abs(m[2]))), 3)


@routes.post("/api/sensor/noise")
@admin
async def sensor_noise(request):
    body = await request.json() if request.can_read_body else {}
    secs = max(2.0, min(float(body.get("seconds", 30)), 120))
    r = await _acq(request, "measure", timeout=secs + 15, seconds=secs)
    return J(r)


@routes.post("/api/sensor/orient")
@admin
async def sensor_orient(request):
    body = await request.json() if request.can_read_body else {}
    r = await _acq(request, "orient")
    if r.get("ok") and body.get("apply"):
        await request.app["set_config"]({"sensor": {"axis_map": r["proposal"],
                                                    "offsets_g": {"E": 0.0, "N": 0.0, "Z": 0.0}}})
    return J(r)


@routes.post("/api/sensor/selftest")
@admin
async def sensor_selftest(request):
    return J(await _acq(request, "selftest", timeout=30))


@routes.post("/api/sensor/reopen")
@admin
async def sensor_reopen(request):
    return J(await _acq(request, "reopen"))


@routes.post("/api/sensor/inject")
@admin
async def sensor_inject(request):
    body = await request.json()
    return J(await _acq(request, "inject", kind=body.get("kind", "quake"), amp_mg=body.get("amp_mg")))


@routes.post("/api/record")
@admin
async def record(request):
    body = await request.json() if request.can_read_body else {}
    return J(await _acq(request, "record", seconds=body.get("seconds", 60)))


@routes.get("/api/i2c")
@admin
async def i2c_scan(request):
    """Scansione del bus I2C (i2cdetect) per verificare il cablaggio."""
    bus = str(request.app["cfg"]["sensor"]["i2c_bus"])
    if not shutil.which("i2cdetect"):
        return err("i2cdetect non installato (apt install i2c-tools)")
    p = await asyncio.create_subprocess_exec("i2cdetect", "-y", bus, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.PIPE)
    out, e = await p.communicate()
    found = []
    for line in out.decode().splitlines()[1:]:
        parts = line.split()
        if not parts:
            continue
        base = int(parts[0].rstrip(":"), 16)
        for i, cell in enumerate(parts[1:]):
            if cell not in ("--", "UU") and len(cell) == 2:
                found.append(hex(base + i))
            elif cell == "UU":
                found.append(hex(base + i) + " (in uso dal kernel)")
    return J({"ok": p.returncode == 0, "output": out.decode() or e.decode(), "found": found, "bus": bus})


# ======================================================================== allerte

@routes.post("/api/alerts/test")
@admin
async def alerts_test(request):
    body = await request.json()
    n = request.app["notifier"]
    ch = body.get("channel", "telegram")
    try:
        if ch == "telegram":
            tg = request.app["cfg"]["alerts"]["telegram"]
            if not tg["token"] or not tg["chat_ids"]:
                return err("servono token del bot e almeno una chat autorizzata")
            img = await n.recent_png(30) if body.get("image") else None
            text = f"🧪 Messaggio di prova da <b>{request.app['cfg']['station']['name']}</b>\n" + n.status_text()
            for chat in tg["chat_ids"]:
                if img:
                    await n.tg_photo(chat, img, text)
                else:
                    await n.tg_send(chat, text)
        elif ch == "webhook":
            await n._deliver("webhook", {"kind": "test", "text": "prova SeismicPi", "t": time.time()})
        elif ch == "mqtt":
            n.mqtt_setup()
            if n.mqtt_error:
                return err(n.mqtt_error)
            n.mqtt_publish("test", {"kind": "test", "t": time.time()})
        elif ch == "gpio":
            await n.gpio_pulse(2)
        elif ch == "report":
            n.dispatch("report", n.report_text(24))
        else:
            return err("canale sconosciuto")
    except Exception as e:  # noqa: BLE001
        return err(f"invio fallito: {e}")
    return J({"ok": True})


@routes.post("/api/alerts/telegram/check")
@admin
async def telegram_check(request):
    body = await request.json()
    token = body.get("token") or request.app["cfg"]["alerts"]["telegram"]["token"]
    if not token or token == "__set__":
        token = request.app["cfg"]["alerts"]["telegram"]["token"]
    try:
        me = await request.app["notifier"].tg("getMe", token=token)
    except Exception as e:  # noqa: BLE001
        return err(f"token non valido o Telegram irraggiungibile: {e}")
    return J({"ok": True, "bot": me, "seen_chats": db.kv_get(request.app["db"], "tg_seen_chats", {})})


@routes.get("/api/alerts/history")
async def alerts_history(request):
    rows = db.rows(request.app["db"].execute("SELECT * FROM outbox ORDER BY id DESC LIMIT 200"))
    for r in rows:
        p = json.loads(r["payload"])
        r["kind"] = p.get("kind")
        r["text"] = p.get("text", "")
        del r["payload"]
    return J({"items": rows, "seen_chats": db.kv_get(request.app["db"], "tg_seen_chats", {})})


@routes.post("/api/alerts/mute")
@admin
async def alerts_mute(request):
    body = await request.json()
    mins = float(body.get("minutes", 60))
    await request.app["set_config"]({"alerts": {"muted_until": time.time() + mins * 60 if mins > 0 else 0}})
    return J({"ok": True, "muted_until": request.app["cfg"]["alerts"]["muted_until"]})


@routes.post("/api/alerts/outbox/clear")
@admin
async def outbox_clear(request):
    request.app["db"].execute("DELETE FROM outbox WHERE sent IS NULL")
    return J({"ok": True})


# ======================================================================== LED di stato

@routes.get("/api/led")
async def led_info(request):
    return J({**request.app["led"].status(), "patterns": PATTERN_NAMES})


@routes.post("/api/led/test")
@admin
async def led_test(request):
    b = await request.json()
    led = request.app["led"]
    pat = b.get("pattern", "alert")
    if pat not in ("ok", "event", "error", "ap", "alert", "warmup"):
        return err("motivo sconosciuto")
    if not led.available:
        return err("LED non presente: manca l'overlay gpio-led (rilancia install.sh e riavvia)")
    led.start_test(pat, float(b.get("seconds", 6)))
    return J({"ok": True})


# ======================================================================== rete

def _net(fn, *a):
    return asyncio.get_running_loop().run_in_executor(None, fn, *a)


@routes.get("/api/network")
async def network_info(request):
    iface = request.app["cfg"]["network"]["interface"]
    st = netmgr.read_status()
    try:
        saved = await _net(netmgr.saved)
        nm_err = None
    except netmgr.NetError as e:
        saved, nm_err = [], str(e)
    return J({"status": st, "saved": saved, "adapter": await _net(netmgr.adapter_info, iface),
              "force": netmgr.get_force(), "error": nm_err, "hostname": netmgr.hostname()})


@routes.post("/api/network/scan")
@admin
async def network_scan(request):
    try:
        return J({"ok": True, "networks": await _net(netmgr.scan, request.app["cfg"]["network"]["interface"])})
    except netmgr.NetError as e:
        return err(f"scansione non riuscita: {e} (in modalità Access Point molti adattatori non possono cercare)")


@routes.post("/api/network/add")
@admin
async def network_add(request):
    b = await request.json()
    try:
        await _net(netmgr.add, request.app["cfg"]["network"]["interface"], b.get("ssid", ""), b.get("password", ""),
                   int(b.get("priority", 10)), bool(b.get("hidden")))
    except netmgr.NetError as e:
        return err(str(e))
    return J({"ok": True})


@routes.post("/api/network/{uuid}/priority")
@admin
async def network_priority(request):
    b = await request.json()
    try:
        await _net(netmgr.set_priority, request.match_info["uuid"], int(b.get("priority", 0)))
    except netmgr.NetError as e:
        return err(str(e))
    return J({"ok": True})


@routes.delete("/api/network/{uuid}")
@admin
async def network_delete(request):
    try:
        await _net(netmgr.delete, request.match_info["uuid"])
    except netmgr.NetError as e:
        return err(str(e))
    return J({"ok": True})


@routes.post("/api/network/{uuid}/connect")
@admin
async def network_connect(request):
    netmgr.set_force("auto")
    try:
        await _net(netmgr.connect, request.match_info["uuid"])
    except netmgr.NetError as e:
        return err(str(e))
    return J({"ok": True})


@routes.post("/api/network/mode")
@admin
async def network_mode(request):
    b = await request.json()
    mode = b.get("mode", "auto")
    if mode not in ("auto", "ap", "client"):
        return err("modalità non valida")
    if mode == "ap":
        ad = await _net(netmgr.adapter_info, request.app["cfg"]["network"]["interface"])
        if ad.get("ap_supported") is False:
            return err(f"l'adattatore Wi-Fi (driver {ad.get('driver') or '?'}) non supporta la modalità Access Point")
    netmgr.set_force(mode)
    return J({"ok": True, "note": "il cambio avviene entro il prossimo controllo del guardiano di rete"})


# ======================================================================== sistema

@routes.get("/api/system")
async def system_info(request):
    app = request.app

    def run():
        services = {}
        for s in ("seismicpi-acq", "seismicpi-web", "seismicpi-netwatch"):
            try:
                out = subprocess.run(["systemctl", "show", s, "-p", "ActiveState,SubState,ActiveEnterTimestamp,NRestarts"],
                                     capture_output=True, text=True, timeout=5).stdout
                services[s] = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            except (OSError, subprocess.TimeoutExpired):
                services[s] = {}
        return {"model": sysinfo.model(), "os": sysinfo.os_release(), "kernel": os.uname().release,
                "python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
                "uptime": sysinfo.uptime(), "load": sysinfo.loadavg(), "mem": sysinfo.meminfo(),
                "cpu_temp": sysinfo.cpu_temp(), "throttled": sysinfo.throttled(),
                "disk": storage.disk_usage(app["data_dir"]), "clock_synced": sysinfo.clock_synced(),
                "time": time.time(), "tz": time.strftime("%Z %z"), "services": services, "version": VERSION,
                "data_dir": app["data_dir"], "hostname": netmgr.hostname()}
    return J(await asyncio.get_running_loop().run_in_executor(None, run))


async def _sh(*args, timeout=30):
    p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, e = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        raise RuntimeError("timeout")
    if p.returncode != 0:
        raise RuntimeError((e or out).decode().strip() or f"codice {p.returncode}")
    return out.decode()


@routes.get("/api/system/hardware")
async def system_hardware(request):
    hw = await asyncio.get_running_loop().run_in_executor(None, sysinfo.hardware)
    hw["telemetry"] = request.app.get("telemetry")
    hw["mem"] = sysinfo.meminfo()
    hw["uptime"] = sysinfo.uptime()
    hw["load"] = sysinfo.loadavg()
    return J(hw)


@routes.post("/api/system/power")
@admin
async def system_power(request):
    b = await request.json()
    action = b.get("action")
    cmds = {"reboot": ["systemctl", "reboot"], "poweroff": ["systemctl", "poweroff"],
            "restart_acq": ["systemctl", "restart", "seismicpi-acq"],
            "restart_web": ["systemctl", "restart", "seismicpi-web"],
            "restart_netwatch": ["systemctl", "restart", "seismicpi-netwatch"]}
    if action not in cmds:
        return err("azione non valida")
    db.log(request.app["db"], "warning", "sistema", f"azione richiesta dalla dashboard: {action}")

    async def later():
        await asyncio.sleep(1)
        try:
            await _sh(*cmds[action])
        except Exception as e:  # noqa: BLE001
            log.error("%s fallito: %s", action, e)
    asyncio.get_running_loop().create_task(later())
    return J({"ok": True})


@routes.post("/api/system/time")
@admin
async def system_time(request):
    """Imposta l'ora dal browser: utile in modalità Access Point, senza Internet né modulo RTC."""
    b = await request.json()
    t = float(b["epoch"])
    if sysinfo.clock_synced():
        return err("l'orologio è già sincronizzato via NTP")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
    try:
        await _sh("timedatectl", "set-ntp", "false")
        await _sh("timedatectl", "set-time", stamp)
        await _sh("timedatectl", "set-ntp", "true")
    except RuntimeError as e:
        return err(f"impossibile impostare l'ora: {e}")
    db.log(request.app["db"], "warning", "sistema", f"ora impostata manualmente dal browser: {stamp}")
    return J({"ok": True, "set": stamp})


@routes.get("/api/system/journal")
@admin
async def system_journal(request):
    unit = request.query.get("unit", "seismicpi-acq")
    if unit not in ("seismicpi-acq", "seismicpi-web", "seismicpi-netwatch", "NetworkManager", "kernel"):
        return err("unità non consentita")
    lines = str(max(10, min(int(request.query.get("lines", 200)), 2000)))
    args = ["journalctl", "-k"] if unit == "kernel" else ["journalctl", "-u", unit]
    try:
        out = await _sh(*args, "-n", lines, "--no-pager", "-o", "short-iso")
    except (RuntimeError, FileNotFoundError) as e:
        return err(f"journal non disponibile: {e}")
    return J({"unit": unit, "text": out})


@routes.get("/api/log")
async def app_log(request):
    q = request.query
    level = q.get("level")
    args, w = [], ""
    if level:
        w = " WHERE level = ?"
        args.append(level)
    rows = db.rows(request.app["db"].execute(f"SELECT * FROM log{w} ORDER BY id DESC LIMIT ?",
                                             args + [min(int(q.get("limit", 200)), 2000)]))
    return J(rows)


@routes.get("/api/system/backup")
@admin
async def system_backup(request):
    con = request.app["db"]
    data = {"version": VERSION, "created": time.time(), "config": request.app["cfg"],
            "events": db.rows(con.execute("SELECT * FROM events ORDER BY id")),
            "kv": {r[0]: r[1] for r in con.execute("SELECT k, v FROM kv")}}
    name = f"seismicpi_backup_{time.strftime('%Y%m%d_%H%M')}.json"
    return web.Response(text=json.dumps(data, default=str, indent=1), content_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@routes.post("/api/system/restore")
@admin
async def system_restore(request):
    b = await request.json()
    cfg = b.get("config")
    if not isinstance(cfg, dict):
        return err("file di backup non valido")
    keep_pw = request.app["cfg"]["web"]["password_hash"]
    try:
        new = config.validate(config.deep_merge(config.DEFAULTS, cfg))
    except config.ConfigError as e:
        return err(f"configurazione non valida: {e}")
    if not new["web"]["password_hash"]:
        new["web"]["password_hash"] = keep_pw
    await request.app["replace_config"](new)
    return J({"ok": True})


@routes.post("/api/system/purge")
@admin
async def system_purge(request):
    b = await request.json()
    what = b.get("what")
    app = request.app
    if what == "raw":
        days = int(b.get("keep_days", 0))
        raw = raw_archive(app)
        removed = await asyncio.get_running_loop().run_in_executor(None, raw.purge, days) if raw else []
    elif what == "stats":
        app["db"].execute("DELETE FROM stats_minute")
        app["db"].execute("DELETE FROM sys_stats")
        removed = ["statistiche"]
    elif what == "events_noise":
        ids = [r[0] for r in app["db"].execute("SELECT id FROM events WHERE class IN ('impulso','rumore_locale') "
                                               "AND starred = 0 AND (label IS NULL OR label = '')")]
        for i in ids:
            app["db"].execute("DELETE FROM events WHERE id=?", (i,))
            app["events"].delete(i)
        removed = [f"{len(ids)} eventi di rumore"]
    elif what == "log":
        app["db"].execute("DELETE FROM log")
        removed = ["registro"]
    elif what == "all":
        # tutti i dati acquisiti; restano configurazione, password, sessioni, reti e cataloghi ufficiali
        if b.get("confirm") != "AZZERA":
            return err("conferma mancante")
        try:
            r = await app["acq"].call("purge_data", timeout=60)
            g = lambda k: f"{r.get(k, 0)} {'giorno' if r.get(k, 0) == 1 else 'giorni'}"   # noqa: E731
            acq_note = f"{g('raw_days')} di dati grezzi, {g('heli_days')} di rullo"
        except (ConnectionError, asyncio.TimeoutError):
            return err("il servizio di acquisizione non risponde: riprova tra poco")
        con = app["db"]
        n_ev = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        for (eid,) in con.execute("SELECT id FROM events").fetchall():
            app["events"].delete(eid)
        for table in ("events", "stats_minute", "sys_stats", "log", "outbox"):
            con.execute(f"DELETE FROM {table}")
        con.execute("UPDATE catalog SET local_event = NULL")
        con.execute("DELETE FROM sqlite_sequence WHERE name = 'events'") if con.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone() else None
        def compact():
            c = db.connect()
            c.execute("VACUUM")
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
        await asyncio.get_running_loop().run_in_executor(None, compact)
        removed = [f"{n_ev} eventi con le forme d'onda", acq_note, "statistiche", "telemetria", "registro",
                   "coda delle notifiche"]
    else:
        return err("operazione sconosciuta")
    db.log(app["db"], "warning", "archivio", f"pulizia manuale: {what}")
    return J({"ok": True, "removed": removed})


# ======================================================================== WebSocket

class Hub:
    def __init__(self):
        self.clients: dict[web.WebSocketResponse, dict] = {}

    def broadcast(self, msg: dict, topic: str | None = None):
        if not self.clients:
            return
        topic = topic or msg.get("t")
        data = None
        for ws, c in list(self.clients.items()):
            if topic not in c["subs"]:
                continue
            q = c["queue"]
            if q.full():
                c["dropped"] += 1
                continue
            if data is None:
                data = json.dumps(msg, separators=(",", ":"), default=str)
            q.put_nowait(data)


@routes.get("/ws")
async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    hub = request.app["hub"]
    client = {"subs": {"status", "event", "minute", "alert", "sensor", "catalog"},
              "queue": asyncio.Queue(200), "dropped": 0}
    hub.clients[ws] = client

    async def sender():
        while True:
            data = await client["queue"].get()
            await ws.send_str(data)
    task = asyncio.get_running_loop().create_task(sender())
    if request.app["acq_status"]:
        await ws.send_str(json.dumps({"t": "status", **request.app["acq_status"]}, default=str))
    try:
        async for m in ws:
            if m.type == aiohttp.WSMsgType.TEXT:
                try:
                    cmd = json.loads(m.data)
                except ValueError:
                    continue
                if "sub" in cmd:
                    client["subs"] = set(cmd["sub"]) | {"alert"}
    finally:
        task.cancel()
        hub.clients.pop(ws, None)
    return ws


# ======================================================================== pagine statiche

@routes.get("/")
async def index(request):
    return web.FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})


# ======================================================================== avvio e cicli di servizio

async def on_acq_message(app, msg):
    t = msg.get("t")
    hub, n = app["hub"], app["notifier"]
    if t == "live":
        hub.broadcast(msg)
        return
    if t == "status":
        msg.pop("t", None)
        app["acq_status"] = msg
        hub.broadcast({"t": "status", **msg})
        n.on_status(msg)
        if not msg.get("sensor_ok"):
            app["sensor_down_since"] = app.get("sensor_down_since") or time.time()
            if time.time() - app["sensor_down_since"] > 60 and app["cfg"]["alerts"]["system"]["sensor_offline"]:
                n.system_alert("sensor", True, f"Sensore non disponibile: {msg.get('sensor_error')}")
        else:
            app["sensor_down_since"] = None
            n.system_alert("sensor", False, "Sensore di nuovo attivo")
        if time.time() - app["mqtt_last"] > 60:
            app["mqtt_last"] = time.time()
            n.mqtt_state(msg)
        return
    hub.broadcast(msg)
    if t == "event" and msg.get("phase") == "start":
        n.mqtt_state(app["acq_status"] or {}, shaking=True)
    elif t == "event" and msg.get("phase") == "end":
        ev = msg["event"]
        n.mqtt_state(app["acq_status"] or {}, shaking=False)
        try:
            # il catalogo potrebbe già contenere il terremoto (eventi lontani arrivano dopo la pubblicazione)
            catalogs.correlate(app["db"], app["cfg"]["catalogs"]["match_window_s"], time.time() - 86400)
            r = app["db"].execute("SELECT class, catalog_id FROM events WHERE id=?", (ev["id"],)).fetchone()
            if r:
                ev["class"], ev["catalog_id"] = r[0], r[1]
            n.on_event_end(ev)
        except Exception:  # noqa: BLE001
            log.exception("gestione fine evento")
        if ev["class"] == "possibile_sisma" and app["cfg"]["catalogs"]["enabled"]:
            for delay in (180, 600, 1500):         # i cataloghi pubblicano dopo qualche minuto
                asyncio.get_running_loop().call_later(delay, lambda: asyncio.ensure_future(app["poll_catalogs"]()))


async def catalog_loop(app):
    await asyncio.sleep(20)
    while True:
        if app["cfg"]["catalogs"]["enabled"]:
            await app["poll_catalogs"]()
        await asyncio.sleep(app["cfg"]["catalogs"]["poll_minutes"] * 60)


async def sys_loop(app):
    cpu = sysinfo.CpuMeter()
    n = app["notifier"]
    last_mode = None
    dev = sysinfo.root_device()
    prev_blk = sysinfo.block_stats(dev) if dev else {}
    prev_net = sysinfo.net_counters()
    pending = []
    while True:
        await asyncio.sleep(60)
        try:
            app["cpu_last"] = cpu.pct()
            temp = sysinfo.cpu_temp()
            du = storage.disk_usage(app["data_dir"])
            net = netmgr.read_status()
            sig = (net.get("wifi") or {}).get("signal")
            st = app["acq_status"] or {}
            blk = sysinfo.block_stats(dev) if dev else {}
            sd_kb = (blk.get("written_bytes", 0) - prev_blk.get("written_bytes", 0)) / 1024 if blk else None
            prev_blk = blk
            nc = sysinfo.net_counters()
            iface = app["cfg"]["network"]["interface"]
            rx = tx = None
            if iface in nc and iface in prev_net:
                rx = (nc[iface]["rx_bytes"] - prev_net[iface]["rx_bytes"]) / 1024
                tx = (nc[iface]["tx_bytes"] - prev_net[iface]["tx_bytes"]) / 1024
            prev_net = nc
            thr = sysinfo.throttled() or {}
            mem = sysinfo.meminfo()
            app["telemetry"] = {"t": time.time(), "sd_write_kb_min": sd_kb, "net_rx_kb_min": rx, "net_tx_kb_min": tx,
                                "cpu_pct": app["cpu_last"], "cpu_temp": temp}
            pending.append((int(time.time()) // 60 * 60, app["cpu_last"], temp, sysinfo.loadavg()[0], mem["pct"],
                            du["pct"], sig, net.get("mode"), st.get("acq_cpu"), sysinfo.cpu_freq()["cur_mhz"],
                            sd_kb, rx, tx, int(bool(thr.get("undervoltage_now"))), round(mem["available"] / 2**20, 1)))
            # come per le statistiche sismiche: scrittura a gruppi per risparmiare la SD
            if len(pending) >= app["cfg"]["storage"]["stats_batch_minutes"]:
                app["db"].executemany(
                    "INSERT OR REPLACE INTO sys_stats(t, cpu_pct, cpu_temp, load1, mem_pct, disk_pct, wifi_signal, net_mode,"
                    " acq_cpu, cpu_freq, sd_write_kb, net_rx_kb, net_tx_kb, undervoltage, mem_avail_mb)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", pending)
                pending = []
            s = app["cfg"]["alerts"]["system"]
            if temp is not None:
                n.system_alert("cpu_temp", temp >= s["cpu_temp_c"], f"Temperatura CPU {temp:.0f} °C")
            n.system_alert("disk", du["pct"] >= s["disk_pct"], f"Disco al {du['pct']}%")
            if thr:
                n.system_alert("undervoltage", thr.get("undervoltage_now", False),
                               "Sottotensione di alimentazione: usa un alimentatore da almeno 2,5 A")
            mode = net.get("mode")
            if s["network_changes"] and mode in ("ap", "client") and last_mode and mode != last_mode:
                n.system_alert("ap_mode", mode == "ap",
                               "Modalità Access Point attiva (nessuna rete nota)" if mode == "ap"
                               else f"Di nuovo in rete: {(net.get('wifi') or {}).get('ssid', '?')}")
            if mode in ("ap", "client"):
                last_mode = mode
        except Exception:  # noqa: BLE001
            log.exception("statistiche di sistema")


def build_app() -> web.Application:
    app = web.Application(middlewares=[cache_mw, auth_mw], client_max_size=20 * 1024 * 1024)
    app["cfg"] = config.load()
    app["data_dir"] = config.DATA_DIR
    os.makedirs(app["data_dir"], exist_ok=True)
    app["db"] = db.connect()
    app["heli"] = storage.Helicorder(app["data_dir"])
    app["events"] = storage.EventStore(app["data_dir"])
    app["hub"] = Hub()
    app["acq_status"] = None
    app["catalog_status"] = None
    app["cpu_last"] = None
    app["login_fails"] = {}
    app["mqtt_last"] = 0
    app["sensor_down_since"] = None
    app["ro_pool"] = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="ro")
    holder = {}

    def ro_con():
        if "c" not in holder:
            holder["c"] = db.connect()
        return holder["c"]
    app["ro_con"] = ro_con
    app["acq"] = ipc.Client(config.IPC_PATH, lambda m: on_acq_message(app, m))
    app["notifier"] = Notifier(app)
    app["led"] = LedController(app)
    watcher = config.ConfigWatcher()

    async def after_change(old, new):
        try:
            await app["acq"].call("reload", timeout=5)
        except (ConnectionError, asyncio.TimeoutError):
            pass
        if old["alerts"]["mqtt"] != new["alerts"]["mqtt"]:
            app["notifier"].mqtt_setup()
        if old["network"] != new["network"]:
            try:
                await _net(netmgr.ensure_ap, new["network"])
            except netmgr.NetError as e:
                log.warning("profilo AP non aggiornato: %s", e)
        app["hub"].broadcast({"t": "config"})

    async def set_config(changes):
        old = app["cfg"]
        new = config.patch(old, changes)
        config.save(new)
        app["cfg"] = new
        watcher.changed()
        await after_change(old, new)

    async def replace_config(new):
        old = app["cfg"]
        config.save(new)
        app["cfg"] = new
        watcher.changed()
        await after_change(old, new)

    app["set_config"] = set_config
    app["replace_config"] = replace_config
    poll_lock = asyncio.Lock()

    async def poll_catalogs():
        async with poll_lock:
            res = await catalogs.poll(app["db"], app["notifier"].session, app["cfg"])
            app["catalog_status"] = {"t": res["t"], "fetched": res["fetched"], "new": len(res["new"]),
                                     "matches": len(res["matches"]), "errors": res["errors"]}
            if res["errors"]:
                log.info("cataloghi: %s", "; ".join(res["errors"]))
            app["notifier"].on_catalog(res)
            if res["new"] or res["matches"]:
                app["hub"].broadcast({"t": "catalog", "new": len(res["new"]), "matches": len(res["matches"])})
            return res
    app["poll_catalogs"] = poll_catalogs

    async def watch_config():
        while True:
            await asyncio.sleep(3)
            if watcher.changed() and watcher.cfg != app["cfg"]:
                old = app["cfg"]
                app["cfg"] = watcher.cfg
                if old["alerts"]["mqtt"] != app["cfg"]["alerts"]["mqtt"]:
                    app["notifier"].mqtt_setup()

    async def startup(app):
        n = app["notifier"]
        n.session = aiohttp.ClientSession(headers={"User-Agent": f"SeismicPi/{VERSION}"})
        n.mqtt_setup()
        loop = asyncio.get_running_loop()
        app["tasks"] = [loop.create_task(c) for c in (
            app["acq"].run(), n.outbox_loop(), n.tg_loop(), n.daily_loop(), catalog_loop(app), sys_loop(app),
            app["led"].run(),
            watch_config())]

    async def cleanup(app):
        app["led"].shutdown()
        for t in app["tasks"]:
            t.cancel()
        await app["notifier"].session.close()
        for ws in list(app["hub"].clients):
            await ws.close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)
    app.add_routes(routes)
    app.router.add_static("/static/", STATIC, append_version=False)
    return app


def main():
    logging.basicConfig(level=os.environ.get("SEISMICPI_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    app = build_app()
    port = int(os.environ.get("SEISMICPI_PORT", app["cfg"]["web"]["port"]))
    web.run_app(app, host=os.environ.get("SEISMICPI_HOST", "0.0.0.0"), port=port, print=None,
                access_log=None)


if __name__ == "__main__":
    main()
