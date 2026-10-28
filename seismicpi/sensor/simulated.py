"""Sensore simulato: rumore realistico del MEMS, rumore antropico giorno/notte, urti, passi e
terremoti sintetici (fase P, fase S, coda). Serve per sviluppare e provare la dashboard senza Pi.

Variabili d'ambiente: SEISMICPI_SIM_RATE moltiplica la frequenza degli eventi (default 1).
"""
from __future__ import annotations

import math
import os
import random
import time

from . import Sensor


class _Burst:
    def __init__(self, t0, dur, comps):
        self.t0, self.t1, self.comps = t0, t0 + dur, comps   # comps: funzioni t->(x,y,z) in g


class SimSensor(Sensor):
    name = "simulato"

    def __init__(self, scfg: dict):
        self.fs = float(scfg["sample_rate"])
        self.range_g = int(scfg["range_g"])
        self.lsb_per_g = {2: 16384, 4: 8192, 8: 4096, 16: 2048}[self.range_g]
        self.fs_true = self.fs * 1.0025          # l'oscillatore del chip non è mai esatto
        self.rate = float(os.environ.get("SEISMICPI_SIM_RATE", "1"))
        self.rng = random.Random()
        self.t_start = None
        self.n = 0
        self.bursts: list[_Burst] = []
        self.next_check = 0.0
        self.tilt = [0.012, -0.007]
        self.temp = 31.0
        self.ar = [0.0, 0.0, 0.0]

    def open(self) -> dict:
        self.t_start = time.time()
        self.n = 0
        return {"chip": "simulatore", "who_am_i": 0, "fs": self.fs, "range_g": self.range_g,
                "dlpf_hz": 44, "bus": None, "address": None}

    # ------------------------------------------------------------ generatori di eventi
    def inject(self, kind: str, t0: float | None = None, amp_mg: float | None = None) -> dict:
        t0 = (t0 or self._now_sim()) + 0.5
        r = self.rng
        if kind == "impulse":
            a = (amp_mg or r.uniform(15, 70)) / 1000
            f = r.uniform(18, 30)
            ph = [r.uniform(0, 6.28) for _ in range(3)]
            w = [r.uniform(0.3, 1), r.uniform(0.3, 1), 1]
            self.bursts.append(_Burst(t0, 0.6, lambda t: tuple(
                a * w[i] * math.exp(-t / 0.08) * math.sin(2 * math.pi * f * t + ph[i]) for i in range(3))))
        elif kind == "steps":
            a = (amp_mg or r.uniform(4, 12)) / 1000
            period = r.uniform(0.5, 0.65)
            n = r.randint(6, 16)
            for k in range(n):
                tk = t0 + k * period
                f = r.uniform(12, 20)
                self.bursts.append(_Burst(tk, 0.35, lambda t, f=f: (
                    0.4 * a * math.exp(-t / 0.05) * math.sin(2 * math.pi * f * t),
                    0.3 * a * math.exp(-t / 0.05) * math.cos(2 * math.pi * f * t),
                    a * math.exp(-t / 0.05) * math.sin(2 * math.pi * f * t + 1))))
        elif kind == "quake":
            a_s = (amp_mg or math.exp(r.uniform(math.log(4), math.log(120)))) / 1000
            a_p = a_s * r.uniform(0.2, 0.4)
            sp = r.uniform(2.5, 12)                 # S-P in secondi (≈ 20-100 km)
            fp, fs_ = r.uniform(5, 9), r.uniform(1.5, 4)
            coda = r.uniform(6, 20)
            az = r.uniform(0, 2 * math.pi)
            ph = [r.uniform(0, 6.28) for _ in range(6)]

            def env(t, rise, decay):
                return (1 - math.exp(-t / rise)) * math.exp(-t / decay)

            def quake(t):
                p = a_p * env(t, 0.15, 3 + sp / 3)
                vp = p * (math.sin(2 * math.pi * fp * t + ph[0]) + 0.5 * math.sin(2 * math.pi * fp * 1.7 * t + ph[1]))
                hp = 0.4 * vp
                s = 0.0
                if t >= sp:
                    ts = t - sp
                    s = a_s * env(ts, 0.4, coda)
                sx = s * (math.sin(2 * math.pi * fs_ * t + ph[2]) + 0.4 * math.sin(2 * math.pi * fs_ * 2.3 * t + ph[3]))
                sz = 0.35 * s * math.sin(2 * math.pi * fs_ * 1.3 * t + ph[4])
                return (hp * math.cos(az) + sx * math.cos(az + 1.57),
                        hp * math.sin(az) + sx * math.sin(az + 1.57) * 0.8,
                        vp + sz)
            self.bursts.append(_Burst(t0, sp + coda * 5, quake))
            return {"kind": kind, "amp_mg": round(a_s * 1000, 1), "s_p": round(sp, 1)}
        else:
            raise ValueError(kind)
        return {"kind": kind}

    def _now_sim(self):
        return self.t_start + self.n / self.fs_true

    def _maybe_spawn(self, t):
        if t < self.next_check:
            return
        self.next_check = t + 1.0
        hour = time.localtime(t).tm_hour
        day = 7 <= hour <= 22
        r = self.rng.random
        if r() < self.rate * (1 / 900 if day else 1 / 3600):
            self.inject("impulse", t)
        if r() < self.rate * (1 / 1200 if day else 1 / 7200):
            self.inject("steps", t)
        if r() < self.rate / 2700:
            self.inject("quake", t)

    # ------------------------------------------------------------ campioni
    def _sample(self, t):
        r = self.rng
        hour = time.localtime(t).tm_hour + time.localtime(t).tm_min / 60
        cultural = 0.0008 + 0.0012 * max(0.0, math.sin((hour - 6) / 17 * math.pi)) if 6 <= hour <= 23 else 0.0008
        out = [0.0, 0.0, 0.0]
        for i in range(3):
            # rumore del MEMS (bianco ~2.4 mg rms) + componente antropica colorata
            self.ar[i] = 0.97 * self.ar[i] + r.gauss(0, cultural) * 0.25
            out[i] = r.gauss(0, 0.0024) + self.ar[i]
        # micro-oscillazione dell'edificio (~3 Hz) sempre presente
        out[0] += 0.0003 * math.sin(2 * math.pi * 3.1 * t)
        out[1] += 0.0002 * math.sin(2 * math.pi * 2.7 * t + 1)
        alive = []
        for b in self.bursts:
            if t >= b.t1:
                continue
            alive.append(b)
            if t >= b.t0:
                x, y, z = b.comps(t - b.t0)
                out[0] += x; out[1] += y; out[2] += z
        self.bursts = alive
        # gravità + inclinazione lenta + deriva termica
        drift = (self.temp - 30) * 0.0004
        out[0] += self.tilt[0] + drift
        out[1] += self.tilt[1] - drift * 0.5
        out[2] += 1.0 + drift * 0.3
        lim = 32767
        return tuple(max(-lim - 1, min(lim, int(round(v * self.lsb_per_g)))) for v in out)

    def read(self):
        now = time.time()
        target = int((now - self.t_start) * self.fs_true)
        out = []
        while self.n < target and len(out) < 2000:
            t = self._now_sim()
            self._maybe_spawn(t)
            out.append(self._sample(t))
            self.n += 1
        if self.n < target:          # processo rimasto fermo troppo: si simula l'overflow
            self.n = target
            return out, True
        self.temp += (31 + 3 * math.sin(now / 3600) - self.temp) * 0.001
        self.tilt[0] += self.rng.gauss(0, 1e-7)
        return out, False

    def temperature(self) -> float:
        return self.temp

    def read_instant(self):
        return self._sample(time.time())

    def self_test(self) -> dict:
        return {"ok": True, "chip": "simulatore", "axes": {
            ax: {"response": 1520.0, "factory": 1500.0, "deviation_pct": 1.3, "pass": True} for ax in "xyz"},
            "note": "sensore simulato"}
