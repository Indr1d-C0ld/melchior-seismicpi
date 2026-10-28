"""Configurazione: valori di default, file JSON in DATA_DIR, merge profondo e validazione.

Il file è condiviso fra i processi (acquisizione, web, netwatch): chi scrive lo fa in modo
atomico (file temporaneo + rename) e notifica gli altri; chi legge ricarica quando cambia mtime.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading

DATA_DIR = os.environ.get("SEISMICPI_DATA", "/var/lib/seismicpi")
CONFIG_PATH = os.environ.get("SEISMICPI_CONFIG", os.path.join(DATA_DIR, "config.json"))
RUN_DIR = os.environ.get("SEISMICPI_RUN", "/run/seismicpi")             # tmpfs: stato volatile
RAM_DIR = os.environ.get("SEISMICPI_RAM", "/dev/shm/seismicpi")         # tmpfs grande: raw in RAM
IPC_PATH = os.environ.get("SEISMICPI_IPC", os.path.join(RUN_DIR, "acq.sock"))

DEFAULTS: dict = {
    "station": {
        "name": "SeismicPi",
        "network": "XX",          # codice rete SEED (XX = sperimentale)
        "code": "SPI01",          # codice stazione SEED (max 5 caratteri)
        "location": "00",
        "latitude": None,
        "longitude": None,
        "elevation_m": 0.0,
        "floor": "piano terra",
        "site_notes": "",
        "north_offset_deg": 0.0,  # di quanti gradi l'asse "N" del box è ruotato rispetto al nord
        "timezone": "Europe/Rome",
    },
    "sensor": {
        "driver": "mpu6050",      # mpu6050 | sim
        "i2c_bus": 1,
        "address": 0x68,
        "sample_rate": 100,       # Hz: 50 | 100 | 200
        "dlpf": 3,                # 0..6 (3 = banda accelerometro 44 Hz)
        "range_g": 2,             # 2 | 4 | 8 | 16
        "axis_map": {"E": "+x", "N": "+y", "Z": "+z"},
        "offsets_g": {"E": 0.0, "N": 0.0, "Z": 0.0},
        "temp_offset_c": 0.0,
        "read_interval_ms": 100,
    },
    "processing": {
        "display_highpass_hz": 0.2,
        "trigger_highpass_hz": 1.0,
        "trigger_lowpass_hz": 20.0,
        "velocity_highpass_hz": 0.2,
    },
    "trigger": {
        "enabled": True,
        "channel": "vector",      # Z | E | N | horizontal | vector | any
        "sta_s": 0.6,
        "lta_s": 30.0,
        "on_ratio": 4.0,
        "off_ratio": 1.6,
        "min_sta_mg": 0.8,        # ampiezza RMS minima dello STA per poter scattare (mg)
        "pga_threshold_mg": 25.0, # trigger assoluto, indipendente da STA/LTA (0 = disattivo)
        "min_duration_s": 0.3,
        "pre_event_s": 20.0,
        "post_event_s": 30.0,
        "max_duration_s": 240.0,
        "cooldown_s": 5.0,
        "freeze_lta": True,
    },
    "storage": {
        # protezione della SD: i dati grezzi continui (~52 MB/giorno a 100 Hz) vanno in RAM per
        # default; su SD restano solo forme d'onda degli eventi, rullo e database
        "raw_mode": "ram",            # ram | sd | off
        "raw_ram_hours": 6,           # ore di dati grezzi tenute in RAM (≈2,2 MB/ora a 100 Hz)
        "raw_retention_days": 7,      # solo con raw_mode = sd
        "helicorder_retention_days": 60,
        "stats_retention_days": 365,
        "min_free_mb": 300,
        "flush_interval_s": 120,      # scrittura dei raw su SD a blocchi (solo raw_mode = sd)
        "heli_flush_s": 300,          # rullo su SD ogni 5 minuti (in caso di blackout si perde al più questo)
        "stats_batch_minutes": 10,    # statistiche al minuto scritte nel DB a gruppi
    },
    "alerts": {
        "telegram": {
            "enabled": False,
            "token": "",
            "chat_ids": [],
            "commands_enabled": True,
            "send_image": True,
        },
        "rules": {
            "min_mmi": 2.0,
            "min_pga_mg": 3.0,
            "classes": ["possibile_sisma", "sisma_confermato"],
            "notify_confirmed": True,       # avvisa quando un evento viene confermato da un catalogo
            "max_per_hour": 10,
            "quiet_hours_enabled": False,
            "quiet_from": "23:00",
            "quiet_to": "07:00",
            "quiet_override_mmi": 4.0,
        },
        "catalog": {
            "enabled": True,
            "min_mag": 4.0,
            "max_radius_km": 300,
            "notify_regional": True,        # avvisa per eventi INGV/EMSC entro il raggio anche se non rilevati
        },
        "system": {
            "enabled": True,
            "cpu_temp_c": 75,
            "disk_pct": 90,
            "sensor_offline": True,
            "network_changes": True,
        },
        "daily_report": {"enabled": False, "time": "08:00"},
        "webhook": {"enabled": False, "url": "", "secret": ""},
        "mqtt": {"enabled": False, "host": "", "port": 1883, "username": "", "password": "",
                 "topic": "seismicpi", "homeassistant_discovery": True},
        "gpio": {"enabled": False, "pin": 17, "active_high": True, "min_mmi": 3.0, "duration_s": 5},
        "muted_until": 0,
    },
    "catalogs": {
        "enabled": True,
        "sources": ["ingv", "emsc"],        # ingv | emsc | usgs
        "poll_minutes": 10,
        "radius_km": 1000,
        "min_mag": 2.0,
        "global_min_mag": 6.5,              # eventi lontani: solo i forti (onde telesismiche)
        "lookback_hours": 48,
        "match_window_s": 20,
    },
    "network": {
        "ap_ssid": "SeismicPi",
        "ap_password": "sismografo",
        "ap_channel": 6,
        "ap_address": "192.168.4.1",
        "boot_wait_s": 90,
        "check_interval_s": 30,
        "retry_known_every_s": 300,
        "keep_ap_with_clients": True,
        "interface": "wlan0",
    },
    "web": {
        "port": 80,
        "username": "admin",
        "password_hash": "",
        "public_readonly": False,
        "session_days": 30,
        "units": "mg",                      # mg | gal | ms2
        "time_display": "local",            # local | utc
        "theme": "dark",
        "helicorder": {"line_minutes": 15, "channel": "Z", "gain": 1.0, "style": "carta"},
    },
}

_lock = threading.Lock()


def deep_merge(base: dict, over: dict) -> dict:
    """Restituisce base aggiornato con over (ricorsivo sui dizionari)."""
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class ConfigError(ValueError):
    pass


def _num(cfg, path, lo, hi, integer=False):
    node = cfg
    keys = path.split(".")
    for k in keys[:-1]:
        node = node[k]
    v = node[keys[-1]]
    try:
        v = int(v) if integer else float(v)
    except (TypeError, ValueError):
        raise ConfigError(f"{path}: valore non numerico")
    if not lo <= v <= hi:
        raise ConfigError(f"{path}: deve essere tra {lo} e {hi}")
    node[keys[-1]] = v


def validate(cfg: dict) -> dict:
    """Normalizza e controlla i valori; solleva ConfigError col primo problema trovato."""
    s = cfg["sensor"]
    if s["driver"] not in ("mpu6050", "sim"):
        raise ConfigError("sensor.driver: mpu6050 o sim")
    if int(s["sample_rate"]) not in (50, 100, 200):
        raise ConfigError("sensor.sample_rate: 50, 100 o 200 Hz")
    if int(s["range_g"]) not in (2, 4, 8, 16):
        raise ConfigError("sensor.range_g: 2, 4, 8 o 16")
    _num(cfg, "sensor.dlpf", 0, 6, True)
    _num(cfg, "sensor.read_interval_ms", 20, 500, True)
    axes = sorted(a[1] for a in s["axis_map"].values())
    if axes != ["x", "y", "z"] or any(a[0] not in "+-" for a in s["axis_map"].values()):
        raise ConfigError("sensor.axis_map: ogni asse x/y/z va usato una volta, con segno + o -")
    fs = int(s["sample_rate"])
    p = cfg["processing"]
    for k in ("display_highpass_hz", "trigger_highpass_hz", "velocity_highpass_hz"):
        _num(cfg, f"processing.{k}", 0.01, fs / 4)
    _num(cfg, "processing.trigger_lowpass_hz", 1, fs / 2 * 0.9)
    if p["trigger_lowpass_hz"] <= p["trigger_highpass_hz"]:
        raise ConfigError("processing: il passa-basso deve essere sopra il passa-alto")
    t = cfg["trigger"]
    if t["channel"] not in ("Z", "E", "N", "horizontal", "vector", "any"):
        raise ConfigError("trigger.channel non valido")
    _num(cfg, "trigger.sta_s", 0.05, 10)
    _num(cfg, "trigger.lta_s", 2, 600)
    if t["lta_s"] <= t["sta_s"] * 2:
        raise ConfigError("trigger: LTA deve essere almeno il doppio di STA")
    _num(cfg, "trigger.on_ratio", 1.2, 50)
    _num(cfg, "trigger.off_ratio", 0.5, 20)
    if t["off_ratio"] >= t["on_ratio"]:
        raise ConfigError("trigger: soglia di stacco deve essere sotto quella di attacco")
    _num(cfg, "trigger.min_sta_mg", 0, 1000)
    _num(cfg, "trigger.pga_threshold_mg", 0, 16000)
    _num(cfg, "trigger.pre_event_s", 2, 120)
    _num(cfg, "trigger.post_event_s", 2, 300)
    _num(cfg, "trigger.max_duration_s", 10, 1800)
    _num(cfg, "trigger.min_duration_s", 0, 30)
    _num(cfg, "trigger.cooldown_s", 0, 600)
    if cfg["storage"]["raw_mode"] not in ("ram", "sd", "off"):
        raise ConfigError("storage.raw_mode: ram, sd oppure off")
    _num(cfg, "storage.raw_ram_hours", 1, 72, True)
    _num(cfg, "storage.flush_interval_s", 5, 900, True)
    _num(cfg, "storage.heli_flush_s", 30, 3600, True)
    _num(cfg, "storage.stats_batch_minutes", 1, 60, True)
    _num(cfg, "storage.raw_retention_days", 0, 365, True)
    _num(cfg, "storage.helicorder_retention_days", 1, 3650, True)
    _num(cfg, "storage.stats_retention_days", 7, 3650, True)
    _num(cfg, "storage.min_free_mb", 50, 100000, True)
    st = cfg["station"]
    if st["latitude"] not in (None, ""):
        _num(cfg, "station.latitude", -90, 90)
    else:
        st["latitude"] = None
    if st["longitude"] not in (None, ""):
        _num(cfg, "station.longitude", -180, 180)
    else:
        st["longitude"] = None
    for k, n in (("network", 2), ("code", 5), ("location", 2)):
        st[k] = str(st[k]).upper()[:n]
    n = cfg["network"]
    if not 1 <= len(n["ap_ssid"]) <= 32:
        raise ConfigError("network.ap_ssid: 1-32 caratteri")
    if n["ap_password"] and not 8 <= len(n["ap_password"]) <= 63:
        raise ConfigError("network.ap_password: 8-63 caratteri (vuota = rete aperta)")
    _num(cfg, "network.ap_channel", 1, 13, True)
    tg = cfg["alerts"]["telegram"]
    tg["chat_ids"] = [str(c).strip() for c in tg["chat_ids"] if str(c).strip()]
    if cfg["web"]["units"] not in ("mg", "gal", "ms2"):
        raise ConfigError("web.units: mg, gal o ms2")
    return cfg


def load(path: str = CONFIG_PATH) -> dict:
    try:
        with open(path) as f:
            user = json.load(f)
    except FileNotFoundError:
        user = {}
    cfg = deep_merge(DEFAULTS, user)
    try:
        return validate(cfg)
    except ConfigError:
        # file corrotto o valori vecchi: si riparte dai default per le sezioni non valide
        return validate(deep_merge(DEFAULTS, {k: v for k, v in user.items() if k in ("station", "web", "alerts")}))


def save(cfg: dict, path: str = CONFIG_PATH) -> None:
    validate(cfg)
    with _lock:
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".config.")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def patch(cfg: dict, changes: dict) -> dict:
    """Applica modifiche parziali e valida; non salva."""
    return validate(deep_merge(cfg, changes))


SECRET_PATHS = (("alerts", "telegram", "token"), ("web", "password_hash"), ("alerts", "mqtt", "password"),
                ("alerts", "webhook", "secret"))


def redacted(cfg: dict) -> dict:
    """Copia senza segreti, per il browser (i campi segreti diventano '__set__' se valorizzati)."""
    out = copy.deepcopy(cfg)
    for path in SECRET_PATHS:
        node = out
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = "__set__" if node.get(path[-1]) else ""
    return out


def strip_unchanged_secrets(changes: dict) -> dict:
    """Il browser rimanda '__set__' per i segreti non toccati: vanno ignorati."""
    changes = copy.deepcopy(changes)
    for path in SECRET_PATHS:
        node = changes
        for k in path[:-1]:
            node = node.get(k) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict) and node.get(path[-1]) == "__set__":
            del node[path[-1]]
    return changes


class ConfigWatcher:
    """Ricarica il file quando cambia mtime (usato dai processi che non lo scrivono)."""

    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self.mtime = self._mtime()
        self.cfg = load(path)

    def _mtime(self):
        try:
            return os.stat(self.path).st_mtime_ns
        except FileNotFoundError:
            return 0

    def changed(self) -> bool:
        m = self._mtime()
        if m != self.mtime:
            self.mtime = m
            self.cfg = load(self.path)
            return True
        return False
