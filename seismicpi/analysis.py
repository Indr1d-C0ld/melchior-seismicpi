"""Parametri e classificazione degli eventi.

I parametri si accumulano campione per campione durante l'evento (EventAccumulator): a fine
evento non serve rielaborare la forma d'onda, cosa che sul Pi Zero bloccherebbe l'acquisizione.

La classificazione è euristica, basata su durata, frequenza dominante e rapporto H/V: separa
bene gli urti domestici dai segnali sismici, ma la conferma vera arriva dai cataloghi INGV/EMSC.
"""
from __future__ import annotations

import math

from . import dsp

CLASSES = {
    "impulso": "Impulso locale (urto, porta, oggetto caduto)",
    "rumore_locale": "Rumore locale (passi, traffico, elettrodomestici)",
    "possibile_sisma": "Possibile terremoto",
    "sisma_confermato": "Terremoto confermato da catalogo",
    "manuale": "Registrazione manuale",
    "test": "Evento di prova",
    "sconosciuto": "Non classificato",
}

LABELS = {
    "terremoto": "Terremoto",
    "passi": "Passi / persone",
    "porta": "Porta / urto",
    "traffico": "Traffico / mezzi pesanti",
    "elettrodomestico": "Elettrodomestico",
    "vento": "Vento / temporale",
    "lavori": "Lavori / cantiere",
    "animale": "Animale domestico",
    "prova": "Prova / test",
    "falso": "Falso allarme",
    "altro": "Altro",
}


class EventAccumulator:
    def __init__(self, t_trigger: float, reason: str, fs: float, noise_mg=(3.0, 3.0, 3.0)):
        self.t_trigger = t_trigger
        self.reason = reason
        self.fs = fs
        self.dt = 1.0 / fs
        self.pga = [0.0, 0.0, 0.0]          # mg per asse
        self.pga_v = 0.0
        self.pgv = [0.0, 0.0, 0.0]          # cm/s
        self.peak_ratio = 0.0
        self.arias = 0.0                    # somma a² dt (m²/s³)
        self.cav = 0.0                      # somma |a| dt (g·s)
        # passaggi per lo zero "con isteresi" (trigger di Schmitt): si conta un'oscillazione solo
        # quando il segnale passa da +soglia a −soglia, così il rumore bianco del MEMS non conta
        self.zc = [0, 0, 0]
        self.sign = [0, 0, 0]
        self.thr = [max(1.0, 2.5 * n) for n in noise_mg]
        self.zc_first = [None, None, None]      # campione del primo e dell'ultimo passaggio contato
        self.zc_last = [None, None, None]
        self.active_samples = 0
        self.t_detrigger = None
        self.n = 0

    def add(self, acc_mg, vel_cms, v_mg, ratio, active: bool):
        self.n += 1
        if ratio > self.peak_ratio:
            self.peak_ratio = ratio
        if v_mg > self.pga_v:
            self.pga_v = v_mg
        for i in range(3):
            a = acc_mg[i]
            aa = a if a >= 0 else -a
            if aa > self.pga[i]:
                self.pga[i] = aa
            vv = vel_cms[i] if vel_cms[i] >= 0 else -vel_cms[i]
            if vv > self.pgv[i]:
                self.pgv[i] = vv
            if active:
                th = self.thr[i]
                sg = 1 if a > th else -1 if a < -th else 0
                if sg and sg != self.sign[i]:
                    if self.sign[i]:
                        self.zc[i] += 1
                        if self.zc_first[i] is None:
                            self.zc_first[i] = self.n
                        self.zc_last[i] = self.n
                    self.sign[i] = sg
        g = v_mg / 1000.0
        self.arias += (g * dsp.G) ** 2 * self.dt
        self.cav += g * self.dt
        if active:
            self.active_samples += 1

    def result(self, t_end_active: float) -> dict:
        duration = max(0.0, t_end_active - self.t_trigger)
        dom = max(range(3), key=lambda i: self.pga[i])
        act_s = self.active_samples / self.fs
        # frequenza dominante: oscillazioni sopra il rumore nella parte attiva; se sono troppo poche
        # si ricava dal rapporto PGA/PGV dell'asse principale (f = a / 2πv per un'onda armonica)
        pga_ax = self.pga[dom] / 1000 * dsp.GAL_PER_G
        if self.zc[dom] >= 4 and self.zc_last[dom] > self.zc_first[dom]:
            span = (self.zc_last[dom] - self.zc_first[dom]) / self.fs
            dom_freq = (self.zc[dom] - 1) / (2 * span)
        elif self.pgv[dom] > 1e-6 and act_s > 0.2:
            dom_freq = pga_ax / (2 * math.pi * self.pgv[dom])
        else:
            dom_freq = None
        h = max(self.pga[0], self.pga[1])
        hv = h / self.pga[2] if self.pga[2] > 0 else None
        pga_gal = self.pga_v / 1000 * dsp.GAL_PER_G
        pgv = max(self.pgv)
        mmi = dsp.mmi_estimate(pga_gal, pgv)
        r = {
            "duration": round(duration, 2), "trigger_reason": self.reason,
            "peak_ratio": round(self.peak_ratio, 2),
            "pga_e": round(self.pga[0], 3), "pga_n": round(self.pga[1], 3), "pga_z": round(self.pga[2], 3),
            "pga": round(self.pga_v, 3),
            "pgv_e": round(self.pgv[0], 4), "pgv_n": round(self.pgv[1], 4), "pgv_z": round(self.pgv[2], 4),
            "pgv": round(pgv, 4),
            "arias": round(math.pi / (2 * dsp.G) * self.arias, 6),
            "cav": round(self.cav, 5),
            "dom_freq": round(dom_freq, 2) if dom_freq else None,
            "dom_axis": "ENZ"[dom],
            "hv_ratio": round(hv, 2) if hv else None,
            "mmi": mmi,
        }
        cls, conf, why = classify(r)
        r.update({"class": cls, "confidence": conf})
        r.setdefault("extra", {})["why"] = why
        return r


def classify(p: dict) -> tuple[str, float, str]:
    dur = p["duration"] or 0
    f = p["dom_freq"]
    hv = p["hv_ratio"] or 1
    if p.get("trigger_reason") in ("manuale", "test"):
        return p["trigger_reason"], 1.0, "evento creato a mano"
    if f is None:
        return "sconosciuto", 0.3, "segnale troppo breve per stimare la frequenza"
    if dur < 2.0 and f > 9:
        return "impulso", 0.75, f"breve ({dur:.1f} s) e ad alta frequenza ({f:.0f} Hz)"
    if f > 13:
        return "rumore_locale", 0.6, f"frequenza dominante alta ({f:.0f} Hz): tipica di sorgenti vicine"
    if dur >= 2.5 and 0.3 <= f <= 12:
        conf = 0.45
        reasons = [f"durata {dur:.0f} s", f"frequenza {f:.1f} Hz"]
        if dur >= 6:
            conf += 0.15
        if hv >= 1.3:
            conf += 0.15
            reasons.append(f"moto prevalente orizzontale (H/V {hv:.1f})")
        if f <= 6:
            conf += 0.1
        return "possibile_sisma", round(min(conf, 0.9), 2), ", ".join(reasons)
    if dur < 2.5:
        return "rumore_locale", 0.5, f"durata breve ({dur:.1f} s)"
    return "sconosciuto", 0.3, "caratteristiche non tipiche"
