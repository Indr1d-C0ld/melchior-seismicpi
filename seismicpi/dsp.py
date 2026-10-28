"""Elaborazione del segnale in puro Python (niente numpy/scipy: sul Pi Zero costano RAM e secondi
di import). Tutto è incrementale, campione per campione, per stare dietro alla FIFO del sensore.
"""
from __future__ import annotations

import math

G = 9.80665          # m/s²
GAL_PER_G = 980.665  # cm/s² per g


class Biquad:
    """Filtro IIR del secondo ordine Butterworth (formule RBJ), forma diretta I."""

    __slots__ = ("b0", "b1", "b2", "a1", "a2", "x1", "x2", "y1", "y2")

    def __init__(self, kind: str, fc: float, fs: float, q: float = 1 / math.sqrt(2)):
        w0 = 2 * math.pi * fc / fs
        cw, sw = math.cos(w0), math.sin(w0)
        alpha = sw / (2 * q)
        if kind == "lp":
            b0, b1, b2 = (1 - cw) / 2, 1 - cw, (1 - cw) / 2
        elif kind == "hp":
            b0, b1, b2 = (1 + cw) / 2, -(1 + cw), (1 + cw) / 2
        else:
            raise ValueError(kind)
        a0, a1, a2 = 1 + alpha, -2 * cw, 1 - alpha
        self.b0, self.b1, self.b2 = b0 / a0, b1 / a0, b2 / a0
        self.a1, self.a2 = a1 / a0, a2 / a0
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    def prime(self, x: float) -> None:
        """Inizializza lo stato come se l'ingresso fosse costante = x da sempre (evita il transitorio)."""
        self.x1 = self.x2 = x
        dc = (self.b0 + self.b1 + self.b2) / (1 + self.a1 + self.a2)
        self.y1 = self.y2 = x * dc

    def __call__(self, x: float) -> float:
        y = self.b0 * x + self.b1 * self.x1 + self.b2 * self.x2 - self.a1 * self.y1 - self.a2 * self.y2
        self.x2, self.x1 = self.x1, x
        self.y2, self.y1 = self.y1, y
        return y


class Chain:
    def __init__(self, *filters):
        self.filters = filters

    def prime(self, x: float) -> None:
        for f in self.filters:
            f.prime(x)
            x = x * (f.b0 + f.b1 + f.b2) / (1 + f.a1 + f.a2)

    def __call__(self, x: float) -> float:
        for f in self.filters:
            x = f(x)
        return x


def bandpass(lo: float, hi: float, fs: float) -> Chain:
    return Chain(Biquad("hp", lo, fs), Biquad("lp", hi, fs))


class Velocity:
    """Accelerazione (g) → velocità (cm/s): integrazione trapezoidale + passa-alto anti-deriva."""

    __slots__ = ("dt", "prev", "v", "hp")

    def __init__(self, fs: float, hp_hz: float):
        self.dt = 1.0 / fs
        self.prev = 0.0
        self.v = 0.0
        self.hp = Biquad("hp", hp_hz, fs)

    def __call__(self, a_g: float) -> float:
        a = a_g * GAL_PER_G
        self.v += (a + self.prev) * 0.5 * self.dt
        self.prev = a
        return self.hp(self.v)


class StaLta:
    """STA/LTA ricorsivo classico sulla funzione caratteristica (energia, mg²)."""

    __slots__ = ("cs", "cl", "sta", "lta", "frozen", "warm", "n_warm")

    def __init__(self, sta_s: float, lta_s: float, fs: float):
        self.cs = 1.0 / max(1.0, sta_s * fs)
        self.cl = 1.0 / max(1.0, lta_s * fs)
        self.sta = 0.0
        self.lta = 0.0
        self.frozen = False
        self.n_warm = int(lta_s * fs)
        self.warm = 0

    @property
    def ready(self) -> bool:
        return self.warm >= self.n_warm

    def __call__(self, cf: float) -> float:
        if self.warm < self.n_warm:
            # riscaldamento: LTA = media semplice dei valori visti, così parte già corretto
            self.warm += 1
            self.lta += (cf - self.lta) / self.warm
            self.sta += (cf - self.sta) * max(self.cs, 1.0 / self.warm)
            return 0.0
        self.sta += (cf - self.sta) * self.cs
        if not self.frozen:
            self.lta += (cf - self.lta) * self.cl
        return self.sta / self.lta if self.lta > 1e-12 else 0.0


# ------------------------------------------------------------------ intensità macrosismica

def mmi_from_pga(pga_gal: float) -> float:
    """Worden et al. (2012), relazione GMICE senza termini M/R. PGA in cm/s²."""
    if pga_gal <= 0:
        return 1.0
    lg = math.log10(pga_gal)
    mmi = 1.78 + 1.55 * lg if lg <= 1.57 else -1.60 + 3.70 * lg
    return max(1.0, min(10.0, mmi))


def mmi_from_pgv(pgv_cms: float) -> float:
    """Worden et al. (2012). PGV in cm/s."""
    if pgv_cms <= 0:
        return 1.0
    lg = math.log10(pgv_cms)
    mmi = 3.78 + 1.47 * lg if lg <= 0.53 else 2.89 + 3.16 * lg
    return max(1.0, min(10.0, mmi))


def mmi_estimate(pga_gal: float, pgv_cms: float) -> float:
    """Sotto il V grado domina la PGA; sopra si media con la stima da PGV (più affidabile)."""
    a = mmi_from_pga(pga_gal)
    v = mmi_from_pgv(pgv_cms)
    if a < 5 and v < 5:
        return round(a, 1)
    return round((a + v) / 2, 1)


ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
MMI_DESC = [
    "non avvertito",
    "avvertito solo da pochi a riposo, ai piani alti",
    "avvertito in casa, come il passaggio di un camion leggero",
    "avvertito da molti in casa; vetri e stoviglie vibrano",
    "avvertito da quasi tutti; oggetti instabili si spostano",
    "avvertito da tutti; caduta di oggetti, lievi danni",
    "difficile stare in piedi; danni moderati",
    "danni considerevoli agli edifici ordinari",
    "danni gravi, crolli parziali",
    "distruzione diffusa",
]


def mmi_roman(mmi: float) -> str:
    return ROMAN[max(0, min(9, int(round(mmi)) - 1))]


def mmi_description(mmi: float) -> str:
    return MMI_DESC[max(0, min(9, int(round(mmi)) - 1))]


def seed_band(fs: float) -> str:
    """Codice di banda SEED per un sensore a corto periodo (E: 80-250 Hz, S: 10-80 Hz)."""
    return "E" if fs >= 80 else "S"


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def travel_times(dist_km: float, depth_km: float) -> tuple[float, float]:
    """Tempi di arrivo P e S molto semplificati.

    Entro ~1500 km: crosta/mantello superiore con velocità medie (P 6.5, S 3.75 km/s sulla distanza
    ipocentrale, con un piccolo aumento oltre i 200 km per le fasi Pn/Sn più veloci). Oltre:
    approssimazione della curva IASP91 per la P diretta (errore di qualche decina di secondi).
    """
    hyp = math.hypot(dist_km, depth_km or 0.0)
    if dist_km <= 200:
        return hyp / 6.5, hyp / 3.75
    if dist_km <= 1500:
        return 200 / 6.5 + (hyp - 200) / 8.0, 200 / 3.75 + (hyp - 200) / 4.5
    deg = min(dist_km / 111.195, 100.0)
    tp = _interp(deg, _P_TABLE)
    return tp, tp * 1.8


# tempi P approssimati (IASP91, sorgente superficiale): (distanza in gradi, secondi)
_P_TABLE = [(10, 148), (15, 210), (20, 277), (25, 328), (30, 373), (40, 460), (50, 537),
            (60, 607), (70, 668), (80, 720), (90, 770), (100, 820)]


def _interp(x, table):
    if x <= table[0][0]:
        return table[0][1] * x / table[0][0]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]
