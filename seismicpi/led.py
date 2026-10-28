"""LED di stato su un GPIO (default GPIO17, pin 11), con una resistenza in serie verso GND.

Il pin è gestito dal kernel tramite l'overlay `gpio-led` (`dtoverlay=gpio-led,gpio=17,label=seismicpi`
in config.txt, messo da install.sh), che crea /sys/class/leds/seismicpi. Qui si scrive solo
`brightness` (0/1) seguendo dei motivi di lampeggio, oppure si affida il LED a un trigger del kernel
(attività della SD, battito cardiaco), che non costa CPU.

Modalità:
  status     stato del sismografo (vedi PATTERNS), con priorità allerta > evento > errore > AP > ok
  disk       attività della microSD (trigger kernel mmc0)
  heartbeat  battito del kernel (trigger heartbeat)
  on / off   sempre acceso / spento
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

log = logging.getLogger("seismicpi.led")

LED_DIR = os.environ.get("SEISMICPI_LED", "/sys/class/leds/seismicpi")

# motivi: sequenze (acceso ms, spento ms, ...) ripetute; None = fisso acceso
PATTERNS = {
    "ok": (80, 1920),                    # breve lampo ogni 2 s: acquisizione regolare
    "event": None,                       # fisso: evento in corso
    "error": (150, 150),                 # lampeggio veloce: sensore assente o acquisizione ferma
    "ap": (100, 150, 100, 1650),         # doppio lampo: modalità Access Point
    "alert": (60, 60),                   # rapidissimo: allerta forte (per alcuni secondi)
    "warmup": (500, 500),                # lento e regolare: avvio, LTA in calcolo
}
PATTERN_NAMES = {"ok": "acquisizione regolare", "event": "evento in corso", "error": "errore sensore/acquisizione",
                 "ap": "modalità Access Point", "alert": "allerta forte", "warmup": "avvio del trigger",
                 "off": "spento", "on": "acceso", "disk": "attività SD", "heartbeat": "battito del kernel"}


class LedController:
    def __init__(self, app):
        self.app = app
        self.alert_until = 0.0
        self.test = None                 # (motivo, fino a quando)
        self.current = None              # motivo attuale (per la dashboard)
        self.trigger = None
        self.level = None
        self.error = ""

    @property
    def cfg(self):
        return self.app["cfg"]["led"]

    @property
    def available(self) -> bool:
        return os.path.exists(os.path.join(LED_DIR, "brightness"))

    def _write(self, name, value):
        try:
            with open(os.path.join(LED_DIR, name), "w") as f:
                f.write(str(value))
            self.error = ""
        except OSError as e:
            if str(e) != self.error:
                log.warning("LED: impossibile scrivere %s: %s", name, e)
            self.error = str(e)

    def _set_trigger(self, trig):
        if trig != self.trigger:
            self._write("trigger", trig)
            self.trigger = trig
            self.level = None

    def _set(self, on: bool):
        v = 1 if on else 0
        if v != self.level:
            self._write("brightness", v)
            self.level = v

    def flash_alert(self, seconds=None):
        self.alert_until = time.time() + (seconds or self.cfg["alert_seconds"])

    def start_test(self, pattern: str, seconds: float = 6):
        self.test = (pattern, time.time() + seconds)

    def desired(self) -> str:
        """Motivo da mostrare in modalità 'status', in ordine di priorità."""
        now = time.time()
        if self.test and now < self.test[1]:
            return self.test[0]
        self.test = None
        if now < self.alert_until:
            return "alert"
        st = self.app.get("acq_status") or {}
        if not self.app["acq"].connected.is_set() or not st.get("sensor_ok"):
            return "error"
        if st.get("state") in ("active", "post"):
            return "event"
        from . import netmgr
        if netmgr.read_status().get("mode") == "ap":
            return "ap"
        if st.get("warmup", 1) < 1:
            return "warmup"
        return "ok"

    def status(self) -> dict:
        return {"available": self.available, "path": LED_DIR, "gpio": self.cfg["gpio"], "mode": self.cfg["mode"],
                "enabled": self.cfg["enabled"], "pattern": self.current,
                "pattern_name": PATTERN_NAMES.get(self.current or "", self.current), "error": self.error}

    async def run(self):
        while True:
            if not self.available:
                self.current = None
                await asyncio.sleep(10)
                continue
            mode = self.cfg["mode"] if self.cfg["enabled"] else "off"
            if mode in ("disk", "heartbeat") and not (self.test or time.time() < self.alert_until):
                self._set_trigger("mmc0" if mode == "disk" else "heartbeat")
                self.current = mode
                await asyncio.sleep(1)
                continue
            self._set_trigger("none")
            if mode in ("on", "off") and not (self.test or time.time() < self.alert_until):
                self._set(mode == "on")
                self.current = mode
                await asyncio.sleep(1)
                continue
            pat = self.desired()
            self.current = pat
            seq = PATTERNS[pat]
            if seq is None:
                self._set(True)
                await asyncio.sleep(0.5)
                continue
            # un ciclo completo del motivo, poi si rivaluta lo stato
            for i, ms in enumerate(seq):
                self._set(i % 2 == 0)
                await asyncio.sleep(ms / 1000)

    def shutdown(self):
        if self.available:
            self._set_trigger("none")
            self._set(False)
