"""Database SQLite (WAL, condiviso tra acquisizione e web): eventi, statistiche, catalogo esterno,
coda notifiche, sessioni, registro di sistema.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

from .config import DATA_DIR

DB_PATH = os.environ.get("SEISMICPI_DB", os.path.join(DATA_DIR, "seismicpi.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    t_trigger REAL NOT NULL,          -- istante del trigger (epoch UTC)
    t_start REAL NOT NULL,            -- inizio finestra registrata (pre-evento incluso)
    t_end REAL NOT NULL,
    duration REAL,                    -- durata sopra soglia (s)
    trigger_reason TEXT,              -- stalta | pga | manuale | test
    peak_ratio REAL,
    pga_e REAL, pga_n REAL, pga_z REAL, pga REAL,   -- mg, picco del modulo 3D
    pgv_e REAL, pgv_n REAL, pgv_z REAL, pgv REAL,   -- cm/s
    pgd REAL,                                        -- cm
    arias REAL,                       -- intensità di Arias (m/s)
    cav REAL,                         -- velocità assoluta cumulata (g·s)
    dom_freq REAL,                    -- frequenza dominante (Hz)
    dom_axis TEXT,
    hv_ratio REAL,                    -- rapporto orizzontale/verticale dei picchi
    mmi REAL,
    class TEXT,                       -- classificazione automatica
    confidence REAL,
    label TEXT,                       -- etichetta manuale dell'utente
    notes TEXT DEFAULT '',
    starred INTEGER DEFAULT 0,
    catalog_id TEXT,                  -- evento di catalogo associato
    temp_c REAL,
    clock_synced INTEGER,
    fs REAL,
    samples INTEGER,
    file_size INTEGER,
    notified INTEGER DEFAULT 0,
    extra TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ev_time ON events(t_trigger);

CREATE TABLE IF NOT EXISTS stats_minute (
    t INTEGER PRIMARY KEY,            -- inizio minuto (epoch)
    rms_e REAL, rms_n REAL, rms_z REAL, rms_v REAL,   -- mg, segnale filtrato
    peak_v REAL,
    mean_e REAL, mean_n REAL, mean_z REAL,           -- g, componente statica (inclinazione)
    temp_c REAL,
    fs REAL,                          -- frequenza effettiva misurata
    gaps INTEGER, overflows INTEGER, i2c_errors INTEGER,
    stalta_max REAL
);

CREATE TABLE IF NOT EXISTS sys_stats (
    t INTEGER PRIMARY KEY,
    cpu_pct REAL, cpu_temp REAL, load1 REAL, mem_pct REAL, disk_pct REAL,
    wifi_signal INTEGER, net_mode TEXT, acq_cpu REAL
);

CREATE TABLE IF NOT EXISTS catalog (
    id TEXT PRIMARY KEY,              -- sorgente:id
    source TEXT, t_origin REAL, lat REAL, lon REAL, depth_km REAL,
    mag REAL, mag_type TEXT, place TEXT,
    dist_km REAL, azimuth REAL, t_p REAL, t_s REAL,
    local_event INTEGER,              -- evento locale associato
    notified INTEGER DEFAULT 0,
    fetched REAL
);
CREATE INDEX IF NOT EXISTS cat_time ON catalog(t_origin);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY,
    created REAL, channel TEXT, payload TEXT,
    attempts INTEGER DEFAULT 0, last_error TEXT, sent REAL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, created REAL, expires REAL, ua TEXT, ip TEXT
);

CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY, t REAL, level TEXT, source TEXT, message TEXT
);
CREATE INDEX IF NOT EXISTS log_time ON log(t);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


MIGRATIONS = {   # colonne aggiunte dopo la prima versione: (tabella, colonna, tipo)
    ("sys_stats", "cpu_freq", "INTEGER"), ("sys_stats", "sd_write_kb", "REAL"), ("sys_stats", "net_rx_kb", "REAL"),
    ("sys_stats", "net_tx_kb", "REAL"), ("sys_stats", "undervoltage", "INTEGER"), ("sys_stats", "mem_avail_mb", "REAL"),
}


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=10, check_same_thread=False, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=10000")
    con.executescript(SCHEMA)
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    return con


def rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def log(con, level: str, source: str, message: str) -> None:
    con.execute("INSERT INTO log(t, level, source, message) VALUES (?,?,?,?)",
                (time.time(), level, source, message))


def kv_get(con, k, default=None):
    r = con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def kv_set(con, k, v):
    con.execute("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, json.dumps(v)))


EVENT_FIELDS = ("t_trigger", "t_start", "t_end", "duration", "trigger_reason", "peak_ratio",
                "pga_e", "pga_n", "pga_z", "pga", "pgv_e", "pgv_n", "pgv_z", "pgv", "pgd",
                "arias", "cav", "dom_freq", "dom_axis", "hv_ratio", "mmi", "class", "confidence",
                "temp_c", "clock_synced", "fs", "samples", "extra")


def insert_event(con, ev: dict) -> int:
    vals = [json.dumps(ev.get(k, {})) if k == "extra" else ev.get(k) for k in EVENT_FIELDS]
    cur = con.execute(f"INSERT INTO events({','.join(EVENT_FIELDS)}) VALUES ({','.join('?' * len(vals))})", vals)
    return cur.lastrowid


def purge(con, stats_days: int, now: float | None = None) -> None:
    now = now or time.time()
    con.execute("DELETE FROM stats_minute WHERE t < ?", (now - stats_days * 86400,))
    con.execute("DELETE FROM sys_stats WHERE t < ?", (now - stats_days * 86400,))
    con.execute("DELETE FROM log WHERE t < ?", (now - 30 * 86400,))
    con.execute("DELETE FROM outbox WHERE sent IS NOT NULL AND sent < ?", (now - 30 * 86400,))
    con.execute("DELETE FROM sessions WHERE expires < ?", (now,))
    con.execute("DELETE FROM catalog WHERE t_origin < ? AND local_event IS NULL", (now - 365 * 86400,))
