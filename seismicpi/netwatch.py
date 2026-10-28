"""Guardiano della rete: se all'avvio (o dopo una disconnessione prolungata) il Pi non è collegato a
nessuna rete Wi-Fi nota, accende l'Access Point "SeismicPi"; periodicamente lo spegne per
qualche secondo e riprova le reti note (una sola radio: non si può cercare mentre fa da AP).

    python3 -m seismicpi.netwatch
"""
from __future__ import annotations

import logging
import signal
import time

from . import config, netmgr

log = logging.getLogger("seismicpi.netwatch")


class NetWatch:
    def __init__(self):
        self.watch = config.ConfigWatcher()
        self.mode = "avvio"
        self.since = time.time()
        self.boot = time.monotonic()
        self.disconnected_since = None
        self.last_retry = time.monotonic()
        self.history = []
        self.error = None
        self.stop = False

    @property
    def n(self):
        return self.watch.cfg["network"]

    def _set_mode(self, mode, why):
        if mode != self.mode:
            log.info("modalità rete: %s → %s (%s)", self.mode, mode, why)
            self.history.append({"t": time.time(), "from": self.mode, "to": mode, "why": why})
            self.history = self.history[-30:]
            self.mode = mode
            self.since = time.time()

    def _client_connected(self) -> dict | None:
        dev = netmgr.device(self.n["interface"])
        if dev["state"] == "connected" and dev["connection"] and dev["connection"] != netmgr.AP_CON:
            return dev
        return None

    def publish(self):
        iface = self.n["interface"]
        st = {"netwatch": True, "mode": self.mode, "since": self.since, "force": netmgr.get_force(),
              "history": self.history, "error": self.error, "iface": iface}
        try:
            st["device"] = netmgr.device(iface)
            st["ip"] = netmgr.ip_info(iface)
            st["wifi"] = netmgr.current_wifi(iface) if self.mode == "client" else None
            st["ap_clients"] = netmgr.ap_clients(iface) if self.mode == "ap" else 0
            st["ap_ssid"] = self.n["ap_ssid"]
            st["ap_address"] = self.n["ap_address"]
        except netmgr.NetError as e:
            st["error"] = str(e)
        netmgr.write_status(st)

    def start_ap(self, why):
        try:
            netmgr.ap_up(self.n)
            self.error = None
            self._set_mode("ap", why)
        except netmgr.NetError as e:
            self.error = f"impossibile avviare l'Access Point: {e}"
            log.error(self.error)
            self._set_mode("offline", "AP non disponibile")

    def try_known(self) -> bool:
        """Spegne l'AP e lascia a NetworkManager il tempo di collegarsi a una rete nota."""
        log.info("provo le reti note…")
        netmgr.ap_down()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not self.stop:
            time.sleep(3)
            if self._client_connected():
                return True
        return False

    def tick(self):
        self.watch.changed()
        force = netmgr.get_force()
        now = time.monotonic()
        client = self._client_connected()
        ap = netmgr.ap_active(self.n["interface"])

        if force == "ap":
            if not ap:
                self.start_ap("forzato dalla dashboard")
            else:
                self._set_mode("ap", "forzato")
            return
        if force == "client" and ap:
            netmgr.ap_down()
            self._set_mode("offline", "AP spento dalla dashboard")
            return

        if client:
            self.disconnected_since = None
            self._set_mode("client", f"collegato a {client['connection']}")
            return
        if ap:
            self._set_mode("ap", "Access Point attivo")
            if now - self.last_retry >= self.n["retry_known_every_s"]:
                self.last_retry = now
                if self.n["keep_ap_with_clients"] and netmgr.ap_clients(self.n["interface"]) > 0:
                    log.info("dispositivi collegati all'AP: rimando il tentativo sulle reti note")
                    return
                if not netmgr.known_client_profiles():
                    return                      # nessuna rete salvata: inutile spegnere l'AP
                if self.try_known():
                    self._set_mode("client", "rete nota tornata disponibile")
                else:
                    self.start_ap("nessuna rete nota raggiungibile")
            return
        # né client né AP
        if self.disconnected_since is None:
            self.disconnected_since = now
        waited = now - self.disconnected_since
        limit = self.n["boot_wait_s"] if now - self.boot < self.n["boot_wait_s"] * 2 else self.n["boot_wait_s"] / 2
        self._set_mode("offline", "in attesa di una rete nota")
        if waited >= limit and force != "client":
            self.last_retry = now
            self.start_ap(f"nessuna rete nota dopo {int(waited)} s")

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stop", True))
        if not netmgr.nm_available():
            log.error("NetworkManager non disponibile: il fallback in Access Point non può funzionare")
        try:
            netmgr.ensure_ap(self.n)
        except netmgr.NetError as e:
            log.warning("profilo AP non creato: %s", e)
        while not self.stop:
            try:
                self.tick()
                self.publish()
            except netmgr.NetError as e:
                self.error = str(e)
                log.warning("%s", e)
            except Exception:  # noqa: BLE001 – il guardiano non deve morire
                log.exception("errore inatteso")
            for _ in range(int(self.n["check_interval_s"])):
                if self.stop:
                    break
                time.sleep(1)


def main():
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    NetWatch().run()


if __name__ == "__main__":
    main()
