"""Gestione Wi-Fi tramite NetworkManager (nmcli): reti note, scansione, Access Point di emergenza.

Funzioni sincrone: il web le chiama in un thread, netwatch direttamente.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

from .config import DATA_DIR, RUN_DIR

AP_CON = "seismicpi-ap"
STATUS_PATH = os.path.join(RUN_DIR, "net_status.json")   # tmpfs: aggiornato ogni 30 s
FORCE_PATH = os.path.join(DATA_DIR, "net_force")


class NetError(Exception):
    pass


def _run(args, timeout=30, check=True) -> str:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise NetError(f"{args[0]} non disponibile")
    except subprocess.TimeoutExpired:
        raise NetError(f"{' '.join(args[:3])}: timeout")
    if check and p.returncode != 0:
        raise NetError((p.stderr or p.stdout).strip() or f"{args[0]} uscito con codice {p.returncode}")
    return p.stdout


def _split(line: str) -> list[str]:
    """Divide una riga di `nmcli -t` rispettando i ':' protetti da backslash."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def nm_available() -> bool:
    try:
        _run(["nmcli", "-t", "general", "status"], timeout=10)
        return True
    except NetError:
        return False


def device(iface: str) -> dict:
    for line in _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"]).splitlines():
        f = _split(line)
        if f[0] == iface:
            return {"device": f[0], "type": f[1], "state": f[2], "connection": f[3] or None}
    return {"device": iface, "type": None, "state": "assente", "connection": None}


def ip_info(iface: str) -> dict:
    out = _run(["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,GENERAL.HWADDR", "device", "show", iface],
               check=False)
    info = {"addresses": [], "gateway": None, "dns": [], "mac": None}
    for line in out.splitlines():
        k, _, v = line.partition(":")
        v = v.replace("\\:", ":")
        if k.startswith("IP4.ADDRESS"):
            info["addresses"].append(v)
        elif k == "IP4.GATEWAY":
            info["gateway"] = v or None
        elif k.startswith("IP4.DNS"):
            info["dns"].append(v)
        elif k == "GENERAL.HWADDR":
            info["mac"] = v
    return info


def current_wifi(iface: str) -> dict | None:
    out = _run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY,CHAN,RATE", "device", "wifi", "list",
                "ifname", iface, "--rescan", "no"], check=False)
    for line in out.splitlines():
        f = _split(line)
        if f[0] == "*":
            return {"ssid": f[1], "signal": int(f[2] or 0), "security": f[3], "channel": f[4], "rate": f[5]}
    return None


def scan(iface: str) -> list[dict]:
    out = _run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,CHAN", "device", "wifi", "list", "ifname", iface,
                "--rescan", "yes"], timeout=45)
    nets = {}
    for line in out.splitlines():
        f = _split(line)
        if not f[0]:
            continue
        sig = int(f[1] or 0)
        if f[0] not in nets or nets[f[0]]["signal"] < sig:
            nets[f[0]] = {"ssid": f[0], "signal": sig, "security": f[2] or "aperta", "channel": f[3]}
    return sorted(nets.values(), key=lambda n: -n["signal"])


def saved() -> list[dict]:
    out = _run(["nmcli", "-t", "-f", "NAME,UUID,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY", "connection", "show"])
    res = []
    for line in out.splitlines():
        f = _split(line)
        if len(f) < 5 or f[2] != "802-11-wireless":
            continue
        det = _run(["nmcli", "-t", "-g", "802-11-wireless.ssid,802-11-wireless.mode", "connection", "show",
                    f[1]], check=False).splitlines()
        ssid = det[0] if det else ""
        mode = det[1] if len(det) > 1 else ""
        res.append({"name": f[0], "uuid": f[1], "autoconnect": f[3] == "yes",
                    "priority": int(f[4] or 0), "ssid": ssid, "mode": mode, "is_ap": f[0] == AP_CON or mode == "ap"})
    return res


def known_client_profiles() -> list[dict]:
    return [c for c in saved() if not c["is_ap"]]


def add(iface: str, ssid: str, password: str, priority: int = 10, hidden: bool = False) -> None:
    if not ssid or len(ssid) > 32:
        raise NetError("SSID non valido")
    for c in known_client_profiles():
        if c["ssid"] == ssid:
            _run(["nmcli", "connection", "delete", "uuid", c["uuid"]])
    args = ["nmcli", "connection", "add", "type", "wifi", "ifname", iface, "con-name", ssid, "ssid", ssid,
            "connection.autoconnect", "yes", "connection.autoconnect-priority", str(int(priority))]
    if hidden:
        args += ["802-11-wireless.hidden", "yes"]
    if password:
        if not 8 <= len(password) <= 63:
            raise NetError("la password WPA deve avere 8-63 caratteri")
        args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
    _run(args)


def delete(uuid: str) -> None:
    _run(["nmcli", "connection", "delete", "uuid", uuid])


def set_priority(uuid: str, priority: int) -> None:
    _run(["nmcli", "connection", "modify", "uuid", uuid, "connection.autoconnect-priority", str(int(priority))])


def connect(uuid: str) -> None:
    _run(["nmcli", "connection", "up", "uuid", uuid], timeout=60)


def reconnect(iface: str) -> None:
    """Chiede a NetworkManager di collegare l'interfaccia alla migliore rete nota disponibile
    (azzera anche il limite di tentativi automatici che NM applica dopo alcuni fallimenti)."""
    _run(["nmcli", "device", "connect", iface], timeout=60, check=False)


def ensure_ap(ncfg: dict) -> None:
    """Crea o aggiorna il profilo dell'Access Point (non si attiva da solo: autoconnect=no)."""
    iface = ncfg["interface"]
    addr = f"{ncfg['ap_address']}/24"
    common = ["connection.autoconnect", "no", "802-11-wireless.ssid", ncfg["ap_ssid"],
              "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
              "802-11-wireless.channel", str(ncfg["ap_channel"]),
              "ipv4.method", "shared", "ipv4.addresses", addr, "ipv6.method", "disabled"]
    if ncfg["ap_password"]:
        sec = ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", ncfg["ap_password"],
               "wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp", "wifi-sec.group", "ccmp"]
    else:
        sec = []                                   # rete aperta
    exists = any(c["name"] == AP_CON for c in saved())
    if exists:
        _run(["nmcli", "connection", "modify", AP_CON, "connection.interface-name", iface] + common + sec)
        if not ncfg["ap_password"]:
            _run(["nmcli", "connection", "modify", AP_CON, "remove", "wifi-sec"], check=False)
    else:
        _run(["nmcli", "connection", "add", "type", "wifi", "ifname", iface, "con-name", AP_CON] + common + sec)


def ap_up(ncfg: dict) -> None:
    ensure_ap(ncfg)
    _run(["nmcli", "connection", "up", AP_CON], timeout=60)


def ap_down() -> None:
    _run(["nmcli", "connection", "down", AP_CON], check=False)


def ap_active(iface: str) -> bool:
    return device(iface).get("connection") == AP_CON


def ap_clients(iface: str) -> int:
    try:
        out = _run(["iw", "dev", iface, "station", "dump"], check=False, timeout=10)
    except NetError:
        return 0
    return out.count("Station ")


def adapter_info(iface: str) -> dict:
    """Driver, chip USB e supporto alla modalità AP dell'adattatore Wi-Fi."""
    info = {"iface": iface, "present": os.path.exists(f"/sys/class/net/{iface}"), "driver": None,
            "usb": None, "ap_supported": None, "phy": None}
    if not info["present"]:
        return info
    try:
        info["driver"] = os.path.basename(os.readlink(f"/sys/class/net/{iface}/device/driver"))
    except OSError:
        pass
    try:
        dev = os.path.realpath(f"/sys/class/net/{iface}/device/..")
        vid = open(os.path.join(dev, "idVendor")).read().strip()
        pid = open(os.path.join(dev, "idProduct")).read().strip()
        prod = ""
        for f in ("manufacturer", "product"):
            p = os.path.join(dev, f)
            if os.path.exists(p):
                prod += open(p).read().strip() + " "
        info["usb"] = f"{vid}:{pid} {prod.strip()}"
    except OSError:
        pass
    try:
        phy = open(f"/sys/class/net/{iface}/phy80211/name").read().strip()
        info["phy"] = phy
        out = _run(["iw", "phy", phy, "info"], check=False, timeout=10)
        m = re.search(r"Supported interface modes:(.*?)(?:\n\t\S|\n\S)", out, re.S)
        if m:
            info["ap_supported"] = "* AP" in m.group(1)
            info["modes"] = [x.strip("* \t") for x in m.group(1).strip().splitlines() if x.strip()]
    except (OSError, NetError):
        pass
    return info


def hostname() -> str:
    # SEISMICPI_HOSTNAME: nome mostrato al posto di quello reale (demo, screenshot)
    return os.environ.get("SEISMICPI_HOSTNAME") or os.uname().nodename


# ------------------------------------------------------------------ stato condiviso con netwatch

def read_status() -> dict:
    try:
        with open(STATUS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"mode": "sconosciuto", "netwatch": False}


def write_status(st: dict) -> None:
    st["updated"] = time.time()
    os.makedirs(RUN_DIR, exist_ok=True)
    tmp = STATUS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATUS_PATH)


def get_force() -> str:
    try:
        with open(FORCE_PATH) as f:
            v = f.read().strip()
        return v if v in ("ap", "client") else "auto"
    except OSError:
        return "auto"


def set_force(mode: str) -> None:
    if mode == "auto":
        try:
            os.unlink(FORCE_PATH)
        except FileNotFoundError:
            pass
        return
    with open(FORCE_PATH, "w") as f:
        f.write(mode)
