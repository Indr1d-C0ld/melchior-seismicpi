"""Archivio su disco: dati grezzi continui, inviluppi del rullo (helicorder), forme d'onda eventi.

Formato a blocchi (raw e eventi): ogni blocco è autodescrittivo, così buchi e cambi di
configurazione non richiedono metadati esterni.

    header '<dfHH' (16 byte): t0 (epoch UTC del primo campione), fs misurata, n campioni, LSB per g
    dati: n × 3 int16 little-endian, assi E, N, Z già orientati (conteggi grezzi, senza offset)

Inviluppi del rullo: un file per giorno UTC, 2 slot al secondo, 4 canali (E, N, Z, V = modulo 3D),
min/max per slot in int16 con unità 0,1 mg. Layout per canale contiguo: [canale][slot][min,max].
"""
from __future__ import annotations

import array
import datetime as dt
import os
import shutil
import struct
import sys
import time

HDR = struct.Struct("<dfHH")
CHANNELS = ("E", "N", "Z", "V")
SLOT_S = 0.5
SLOTS_PER_DAY = int(86400 / SLOT_S)
ENV_UNIT_MG = 0.1
EMPTY_MIN, EMPTY_MAX = 32767, -32768
LITTLE = sys.byteorder == "little"


def _utc(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc)


def day_key(t: float) -> str:
    return _utc(t).strftime("%Y%m%d")


def day_start(key: str) -> float:
    return dt.datetime.strptime(key, "%Y%m%d").replace(tzinfo=dt.timezone.utc).timestamp()


def _arr_bytes(a: array.array) -> bytes:
    if LITTLE:
        return a.tobytes()
    b = array.array("h", a)
    b.byteswap()
    return b.tobytes()


def _arr_from(buf: bytes) -> array.array:
    a = array.array("h")
    a.frombytes(buf)
    if not LITTLE:
        a.byteswap()
    return a


def pack_block(t0: float, fs: float, lsb: int, data: array.array) -> bytes:
    return HDR.pack(t0, fs, len(data) // 3, lsb) + _arr_bytes(data)


def iter_blocks(path: str):
    """Restituisce (t0, fs, lsb, array interlacciato ENZ) per ogni blocco del file."""
    try:
        with open(path, "rb") as f:
            buf = f.read()
    except FileNotFoundError:
        return
    pos, end = 0, len(buf)
    while pos + HDR.size <= end:
        t0, fs, n, lsb = HDR.unpack_from(buf, pos)
        pos += HDR.size
        size = n * 6
        if pos + size > end or n == 0 or not 1 <= fs <= 1000:
            break                              # coda troncata (spegnimento brusco)
        yield t0, fs, lsb, _arr_from(buf[pos:pos + size])
        pos += size


def write_blocks(path: str, blocks) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        for t0, fs, lsb, data in blocks:
            f.write(pack_block(t0, fs, lsb, data))
    os.replace(tmp, path)


class RawArchive:
    """Archivio continuo: raw/AAAAMMGG/HH.blk (ore UTC)."""

    def __init__(self, base: str):
        self.base = os.path.join(base, "raw")
        os.makedirs(self.base, exist_ok=True)
        self.f = None
        self.cur = None
        self.last_flush = time.monotonic()

    def path_for(self, t: float) -> str:
        u = _utc(t)
        return os.path.join(self.base, u.strftime("%Y%m%d"), u.strftime("%H") + ".blk")

    def append(self, t0, fs, lsb, data):
        p = self.path_for(t0)
        if p != self.cur:
            self.close()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            self.f = open(p, "ab", buffering=256 * 1024)
            self.cur = p
        self.f.write(pack_block(t0, fs, lsb, data))

    def flush(self, fsync=False):
        if self.f:
            self.f.flush()
            if fsync:
                os.fsync(self.f.fileno())
        self.last_flush = time.monotonic()

    def close(self):
        if self.f:
            self.f.close()
        self.f = None
        self.cur = None

    def read(self, t1: float, t2: float):
        """Blocchi che si sovrappongono a [t1, t2)."""
        out = []
        h = int(t1 // 3600) * 3600
        while h < t2:
            for b in iter_blocks(self.path_for(h)):
                t0, fs, _, data = b
                if t0 < t2 and t0 + (len(data) // 3) / fs > t1:
                    out.append(b)
            h += 3600
        return out

    def days(self) -> list[str]:
        try:
            return sorted(d for d in os.listdir(self.base) if d.isdigit())
        except FileNotFoundError:
            return []

    def day_size(self, key: str) -> int:
        d = os.path.join(self.base, key)
        return sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d))

    def purge(self, keep_days: int, now: float | None = None) -> list[str]:
        cutoff = day_key((now or time.time()) - keep_days * 86400)
        removed = []
        for d in self.days():
            if d < cutoff:
                shutil.rmtree(os.path.join(self.base, d), ignore_errors=True)
                removed.append(d)
        return removed

    def purge_hours(self, keep_hours: int, now: float | None = None) -> list[str]:
        """Pulizia oraria (archivio in RAM): elimina i file più vecchi di keep_hours."""
        cutoff = self.path_for((now or time.time()) - keep_hours * 3600)
        removed = []
        for d in self.days():
            dd = os.path.join(self.base, d)
            for f in sorted(os.listdir(dd)):
                p = os.path.join(dd, f)
                if p < cutoff and p != self.cur:
                    os.unlink(p)
                    removed.append(f"{d}/{f}")
            if not os.listdir(dd):
                os.rmdir(dd)
        return removed

    def drop_oldest(self) -> str | None:
        days = self.days()
        if len(days) <= 1:
            return None
        shutil.rmtree(os.path.join(self.base, days[0]), ignore_errors=True)
        return days[0]


def blocks_to_series(blocks, t1: float, t2: float, lsb_override=None):
    """Unisce blocchi in una serie uniforme: (t_start, fs, [E], [N], [Z]) in g, None nei buchi."""
    if not blocks:
        return None
    fs = blocks[-1][1]
    fs_nom = round(fs)
    n_tot = int(round((t2 - t1) * fs_nom))
    if n_tot <= 0:
        return None
    cols = [[None] * n_tot for _ in range(3)]
    for t0, bfs, lsb, data in blocks:
        k = int(round((t0 - t1) * fs_nom))
        scale = 1.0 / (lsb_override or lsb)
        n = len(data) // 3
        for i in range(n):
            j = k + i
            if 0 <= j < n_tot:
                cols[0][j] = data[3 * i] * scale
                cols[1][j] = data[3 * i + 1] * scale
                cols[2][j] = data[3 * i + 2] * scale
    return t1, float(fs_nom), cols[0], cols[1], cols[2]


class Helicorder:
    """Inviluppi min/max per il rullo. Il giorno corrente sta in RAM (2,8 MB) e va su disco a pezzi."""

    BYTES_PER_CH = SLOTS_PER_DAY * 4

    def __init__(self, base: str):
        self.base = os.path.join(base, "heli")
        os.makedirs(self.base, exist_ok=True)
        self.key = None
        self.buf = None
        self.dirty = [None] * 4                 # (slot_min, slot_max) da scrivere per canale
        self.acc_slot = None
        self.acc = None

    def path(self, key):
        return os.path.join(self.base, key + ".env")

    def _empty(self):
        one = array.array("h", [EMPTY_MIN, EMPTY_MAX]) * SLOTS_PER_DAY
        return bytearray(_arr_bytes(one) * 4)

    def _open_day(self, key):
        self.flush()
        p = self.path(key)
        try:
            with open(p, "rb") as f:
                data = f.read()
            if len(data) != self.BYTES_PER_CH * 4:
                raise ValueError
            self.buf = bytearray(data)
        except (FileNotFoundError, ValueError):
            self.buf = self._empty()
            with open(p, "wb") as f:
                f.write(self.buf)
        self.key = key
        self.dirty = [None] * 4

    def add(self, t: float, vals_mg) -> None:
        """vals_mg: (E, N, Z, V) filtrati, in mg."""
        slot_abs = int(t // SLOT_S)
        if slot_abs != self.acc_slot:
            self._commit()
            self.acc_slot = slot_abs
            self.acc = [[v, v] for v in vals_mg]
            return
        for a, v in zip(self.acc, vals_mg):
            if v < a[0]:
                a[0] = v
            elif v > a[1]:
                a[1] = v

    def _commit(self):
        if self.acc_slot is None:
            return
        t = self.acc_slot * SLOT_S
        key = day_key(t)
        if key != self.key:
            self._open_day(key)
        slot = int((t - day_start(key)) / SLOT_S)
        for ch, (mn, mx) in enumerate(self.acc):
            lo = max(-32768, min(32767, int(mn / ENV_UNIT_MG)))
            hi = max(-32768, min(32767, int(mx / ENV_UNIT_MG)))
            struct.pack_into("<hh", self.buf, ch * self.BYTES_PER_CH + slot * 4, lo, hi)
            d = self.dirty[ch]
            self.dirty[ch] = (slot, slot) if d is None else (min(d[0], slot), max(d[1], slot))

    def flush(self):
        if self.key is None or self.buf is None or not any(self.dirty):
            return
        with open(self.path(self.key), "r+b") as f:
            for ch, d in enumerate(self.dirty):
                if d is None:
                    continue
                off = ch * self.BYTES_PER_CH + d[0] * 4
                f.seek(off)
                f.write(self.buf[off:ch * self.BYTES_PER_CH + (d[1] + 1) * 4])
        self.dirty = [None] * 4

    def read(self, key: str, ch: str, slot_from: int = 0, slot_to: int = SLOTS_PER_DAY, merge: int = 1) -> bytes:
        """Coppie min/max int16 LE (0,1 mg) per il canale richiesto, accorpando `merge` slot."""
        ci = CHANNELS.index(ch)
        slot_from = max(0, slot_from)
        slot_to = min(SLOTS_PER_DAY, slot_to)
        off = ci * self.BYTES_PER_CH
        if key == self.key and self.buf is not None:
            raw = bytes(self.buf[off + slot_from * 4: off + slot_to * 4])
        else:
            try:
                with open(self.path(key), "rb") as f:
                    f.seek(off + slot_from * 4)
                    raw = f.read((slot_to - slot_from) * 4)
            except FileNotFoundError:
                raw = b""
        a = _arr_from(raw)
        if merge <= 1:
            return _arr_bytes(a)
        out = array.array("h")
        for i in range(0, len(a), merge * 2):
            seg = a[i:i + merge * 2]
            mins = [v for v in seg[0::2] if v != EMPTY_MIN]
            maxs = [v for v in seg[1::2] if v != EMPTY_MAX]
            out.append(min(mins) if mins else EMPTY_MIN)
            out.append(max(maxs) if maxs else EMPTY_MAX)
        return _arr_bytes(out)

    def days(self) -> list[str]:
        return sorted(f[:8] for f in os.listdir(self.base) if f.endswith(".env"))

    def purge(self, keep_days: int, now: float | None = None) -> list[str]:
        cutoff = day_key((now or time.time()) - keep_days * 86400)
        removed = []
        for k in self.days():
            if k < cutoff:
                os.unlink(self.path(k))
                removed.append(k)
        return removed


class EventStore:
    """Forme d'onda degli eventi: events/<id>.blk, conservate anche dopo la pulizia dei raw."""

    def __init__(self, base: str):
        self.base = os.path.join(base, "events")
        os.makedirs(self.base, exist_ok=True)

    def path(self, eid: int) -> str:
        return os.path.join(self.base, f"{int(eid)}.blk")

    def save(self, eid: int, blocks) -> int:
        write_blocks(self.path(eid), blocks)
        return os.path.getsize(self.path(eid))

    def load(self, eid: int):
        return list(iter_blocks(self.path(eid)))

    def delete(self, eid: int) -> None:
        try:
            os.unlink(self.path(eid))
        except FileNotFoundError:
            pass

    def total_size(self) -> int:
        return sum(os.path.getsize(os.path.join(self.base, f)) for f in os.listdir(self.base))


def raw_base(cfg: dict) -> str | None:
    """Cartella dell'archivio grezzo secondo la modalità scelta (None = non si archivia)."""
    from .config import DATA_DIR, RAM_DIR
    mode = cfg["storage"]["raw_mode"]
    return DATA_DIR if mode == "sd" else RAM_DIR if mode == "ram" else None


def disk_usage(path: str) -> dict:
    u = shutil.disk_usage(path)
    return {"total": u.total, "used": u.used, "free": u.free, "pct": round(u.used / u.total * 100, 1)}


def dir_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total
