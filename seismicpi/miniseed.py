"""Scrittore miniSEED 2.4 minimale (record da 512 byte, codifica INT16, big-endian).

Leggibile da ObsPy, SeisComP, Swarm ecc. Un record nuovo inizia a ogni buco nei dati.
"""
from __future__ import annotations

import datetime as dt
import struct

REC_LEN = 512
DATA_OFF = 64
PER_REC = (REC_LEN - DATA_OFF) // 2


def _btime(t: float) -> bytes:
    u = dt.datetime.fromtimestamp(t, dt.timezone.utc)
    frac = int(round(u.microsecond / 100))
    if frac >= 10000:
        u += dt.timedelta(seconds=1)
        frac = 0
    return struct.pack(">HHBBBBH", u.year, u.timetuple().tm_yday, u.hour, u.minute, u.second, 0, frac)


def _rate_fields(fs: float) -> tuple[int, int]:
    if abs(fs - round(fs)) < 1e-6:
        return int(round(fs)), 1
    return int(round(fs * 100)), -100


def _record(seq, net, sta, loc, cha, t0, fs, samples) -> bytes:
    factor, mult = _rate_fields(fs)
    hdr = (f"{seq % 1000000:06d}D ".encode() + sta.ljust(5)[:5].encode() + loc.ljust(2)[:2].encode()
           + cha.ljust(3)[:3].encode() + net.ljust(2)[:2].encode())
    hdr += _btime(t0)
    hdr += struct.pack(">HhhBBBBiHH", len(samples), factor, mult, 0, 0, 0, 1, 0, DATA_OFF, 48)
    b1000 = struct.pack(">HHBBBB", 1000, 0, 1, 1, 9, 0)
    body = struct.pack(f">{len(samples)}h", *samples)
    rec = hdr + b1000
    rec += b"\x00" * (DATA_OFF - len(rec)) + body
    return rec + b"\x00" * (REC_LEN - len(rec))


def segments(blocks, comp: int):
    """Raggruppa i blocchi in segmenti continui: [(t0, fs, [campioni int])]."""
    segs = []
    for t0, fs, _, data in blocks:
        vals = list(data[comp::3])
        if segs:
            st, sfs, sv = segs[-1]
            expected = st + len(sv) / sfs
            if abs(t0 - expected) < 0.5 / sfs:
                sv.extend(vals)
                continue
        segs.append((t0, fs, vals))
    return segs


def write(blocks, net: str, sta: str, loc: str, band: str, scale_note=None) -> bytes:
    """Tre canali <band>N{E,N,Z}; restituisce il contenuto del file .mseed."""
    out = bytearray()
    seq = 1
    for comp, name in enumerate("ENZ"):
        cha = f"{band}N{name}"
        for t0, fs, vals in segments(blocks, comp):
            for i in range(0, len(vals), PER_REC):
                chunk = vals[i:i + PER_REC]
                out += _record(seq, net, sta, loc, cha, t0 + i / fs, fs, chunk)
                seq += 1
    return bytes(out)
