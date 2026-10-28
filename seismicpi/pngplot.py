"""Grafici PNG in puro Python (per Telegram): niente matplotlib, che sul Pi Zero impiega ~10 s
solo per l'import. Le tracce si disegnano per colonne di pixel (min/max), quindi il costo dipende
dalla larghezza dell'immagine e non dal numero di campioni.
"""
from __future__ import annotations

import struct
import zlib

BG = (16, 20, 28)
GRID = (40, 48, 62)
TEXT = (170, 180, 195)
COLORS = [(96, 165, 250), (52, 211, 153), (251, 146, 60)]
MARK = (239, 68, 68)

# font 3x5 minimale: bit di ogni riga, da sinistra
_F = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111", "3": "111001111001111",
    "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001010010010",
    "8": "111101111101111", "9": "111101111001111", ".": "000000000000010", ":": "000010000010000",
    "-": "000000111000000", "+": "000010111010000", " ": "000000000000000", "E": "111100111100111",
    "N": "101111111111101", "Z": "111001010100111", "S": "111100111001111", "M": "101111111101101",
    "G": "111100101101111", "A": "010101111101101", "P": "111101111100100", "V": "101101101101010",
    "I": "111010010010111", "/": "001001010100100", "H": "101101111101101", "T": "111010010010010",
    "C": "111100100100111", "L": "100100100100111", "R": "110101110101101", "O": "111101101101111",
    "D": "110101101101110", "U": "101101101101111", "X": "101101010101101", "K": "101110100110101",
    "F": "111100111100100", "B": "110101110101110", "W": "101101101111101", "Y": "101101010010010",
    "Q": "111101101111001", "J": "001001001101111", "%": "101001010100101", "#": "101111101111101",
}


class Canvas:
    def __init__(self, w, h, bg=BG):
        self.w, self.h = w, h
        self.px = bytearray(bytes(bg) * (w * h))

    def set(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i:i + 3] = bytes(c)

    def vline(self, x, y0, y1, c):
        if y0 > y1:
            y0, y1 = y1, y0
        for y in range(max(0, y0), min(self.h - 1, y1) + 1):
            self.set(x, y, c)

    def hline(self, x0, x1, y, c, dash=0):
        for x in range(max(0, x0), min(self.w - 1, x1) + 1):
            if not dash or (x // dash) % 2 == 0:
                self.set(x, y, c)

    def text(self, x, y, s, c=TEXT, scale=2):
        for ch in s.upper():
            g = _F.get(ch, _F[" "])
            for r in range(5):
                for col in range(3):
                    if g[r * 3 + col] == "1":
                        for dy in range(scale):
                            for dx in range(scale):
                                self.set(x + col * scale + dx, y + r * scale + dy, c)
            x += 4 * scale

    def png(self) -> bytes:
        raw = b"".join(b"\x00" + bytes(self.px[y * self.w * 3:(y + 1) * self.w * 3]) for y in range(self.h))

        def chunk(t, d):
            return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def waveform_png(t0: float, fs: float, traces: dict, title: str = "", mark_t: float | None = None,
                 w: int = 960, h: int = 520) -> bytes:
    """traces: {"E": [mg o None], ...} campionati da t0 a fs. Una fascia per traccia, scala comune."""
    cv = Canvas(w, h)
    left, right, top, bottom = 70, 12, 34 if title else 12, 26
    names = list(traces)
    n = len(names)
    band = (h - top - bottom) / n
    length = max(len(v) for v in traces.values()) if traces else 0
    if title:
        cv.text(left, 10, title[:100], TEXT, 2)
    peak = 0.0
    for v in traces.values():
        for x in v:
            if x is not None and abs(x) > peak:
                peak = abs(x)
    peak = peak or 1.0
    dur = length / fs if fs else 0
    plot_w = w - left - right
    # griglia temporale
    step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if dur / s <= 10), 600)
    k = 0
    while k * step <= dur:
        x = left + int(k * step / dur * plot_w) if dur else left
        cv.vline(x, top, h - bottom, GRID)
        cv.text(x - 6, h - bottom + 8, f"{k * step:g}", TEXT, 2)
        k += 1
    for ti, name in enumerate(names):
        vals = traces[name]
        y_mid = int(top + band * (ti + 0.5))
        half = band / 2 - 4
        cv.hline(left, w - right, y_mid, GRID, dash=4)
        cv.text(8, y_mid - 12, name, COLORS[ti % 3], 3)
        cv.text(8, y_mid + 8, f"{max(abs(x) for x in vals if x is not None) if any(x is not None for x in vals) else 0:.1f}",
                TEXT, 1)
        per = len(vals) / plot_w if plot_w else 1
        prev = None
        for px in range(plot_w):
            seg = [x for x in vals[int(px * per):max(int((px + 1) * per), int(px * per) + 1)] if x is not None]
            if not seg:
                prev = None
                continue
            lo, hi = min(seg), max(seg)
            y_lo = int(y_mid - hi / peak * half)
            y_hi = int(y_mid - lo / peak * half)
            if prev is not None:
                y_lo, y_hi = min(y_lo, prev), max(y_hi, prev)
            cv.vline(left + px, y_lo, y_hi, COLORS[ti % 3])
            prev = int(y_mid - seg[-1] / peak * half)
    if mark_t is not None and dur:
        x = left + int((mark_t - t0) / dur * plot_w)
        cv.vline(x, top, h - bottom, MARK)
    cv.text(w - 150, h - bottom + 8, f"PICCO {peak:.1f} MG", TEXT, 1)
    return cv.png()
