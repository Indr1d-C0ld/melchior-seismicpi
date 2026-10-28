"""Informazioni di sistema lette da /proc e /sys (niente psutil: sul Pi Zero ogni MB conta)."""
from __future__ import annotations

import os
import subprocess
import time


def clock_synced() -> bool | None:
    if os.path.exists("/run/systemd/timesync/synchronized"):
        return True
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return out == "yes"
    except (OSError, subprocess.TimeoutExpired):
        return None


def cpu_temp() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000
    except (OSError, ValueError):
        return None


def throttled() -> dict | None:
    """Stato di sotto-alimentazione/throttling del firmware Raspberry (vcgencmd)."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5).stdout
        v = int(out.strip().split("=")[1], 16)
    except (OSError, IndexError, ValueError, subprocess.TimeoutExpired):
        return None
    return {"raw": hex(v), "undervoltage_now": bool(v & 1), "throttled_now": bool(v & 4),
            "undervoltage_past": bool(v & 0x10000), "throttled_past": bool(v & 0x40000)}


class CpuMeter:
    def __init__(self):
        self.prev = self._read()

    @staticmethod
    def _read():
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:]]
        idle = v[3] + (v[4] if len(v) > 4 else 0)
        return sum(v), idle

    def pct(self) -> float:
        tot, idle = self._read()
        dt, di = tot - self.prev[0], idle - self.prev[1]
        self.prev = (tot, idle)
        return round(100 * (1 - di / dt), 1) if dt > 0 else 0.0


class ProcCpu:
    """CPU usata da un processo (default: questo), in % di un core."""

    def __init__(self, pid="self"):
        self.pid = pid
        self.prev = (self._ticks(), time.monotonic())

    def _ticks(self):
        try:
            with open(f"/proc/{self.pid}/stat") as f:
                parts = f.read().rsplit(")", 1)[1].split()
            return int(parts[11]) + int(parts[12])
        except (OSError, IndexError, ValueError):
            return 0

    def pct(self) -> float:
        t, now = self._ticks(), time.monotonic()
        dt = now - self.prev[1]
        val = (t - self.prev[0]) / os.sysconf("SC_CLK_TCK") / dt * 100 if dt > 0 else 0
        self.prev = (t, now)
        return round(val, 1)


def meminfo() -> dict:
    m = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            m[k] = int(v.split()[0]) * 1024
    total = m.get("MemTotal", 1)
    avail = m.get("MemAvailable", m.get("MemFree", 0))
    return {"total": total, "available": avail, "pct": round(100 * (1 - avail / total), 1),
            "swap_total": m.get("SwapTotal", 0), "swap_free": m.get("SwapFree", 0)}


def uptime() -> float:
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def loadavg() -> list[float]:
    return list(os.getloadavg())


def model() -> str:
    try:
        with open("/proc/device-tree/model") as f:
            return f.read().strip("\x00\n")
    except OSError:
        return os.uname().machine


def os_release() -> str:
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "?"


# ------------------------------------------------------------------ telemetria hardware Raspberry

def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _vc(*args) -> str | None:
    try:
        p = subprocess.run(["vcgencmd", *args], capture_output=True, text=True, timeout=5)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def cpu_freq() -> dict:
    base = "/sys/devices/system/cpu/cpu0/cpufreq/"
    def mhz(f):
        v = _read(base + f)
        return int(v) // 1000 if v and v.isdigit() else None
    return {"cur_mhz": mhz("scaling_cur_freq"), "min_mhz": mhz("cpuinfo_min_freq"), "max_mhz": mhz("cpuinfo_max_freq"),
            "governor": _read(base + "scaling_governor")}


def cpu_info() -> dict:
    info = {"cores": os.cpu_count()}
    txt = _read("/proc/cpuinfo", "")
    for line in txt.splitlines():
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k in ("model name", "Hardware", "Revision", "Serial") and v:
            info[k.lower().replace(" ", "_")] = v
        if k == "Features":
            info["features"] = v
    info["arch"] = os.uname().machine
    return info


def vcgencmd_info() -> dict:
    """Tensioni, clock e memoria del VideoCore (solo Raspberry Pi)."""
    out = {}
    for dom in ("core", "sdram_c", "sdram_i", "sdram_p"):
        v = _vc("measure_volts", dom)
        if v and "=" in v:
            out[f"volt_{dom}"] = v.split("=")[1]
    for clk in ("arm", "core", "emmc"):
        v = _vc("measure_clock", clk)
        if v and "=" in v:
            try:
                out[f"clock_{clk}_mhz"] = round(int(v.split("=")[1]) / 1e6)
            except ValueError:
                pass
    for m in ("arm", "gpu"):
        v = _vc("get_mem", m)
        if v and "=" in v:
            out[f"mem_{m}"] = v.split("=")[1]
    ver = _vc("version")
    if ver:
        out["firmware"] = ver.splitlines()[0]
    return out


def sd_card() -> dict | None:
    """Dati della scheda SD (nome, produttore, data) e settori scritti dall'avvio."""
    dev = "/sys/block/mmcblk0"
    if not os.path.isdir(dev):
        return None
    d = {k: _read(f"{dev}/device/{k}") for k in ("name", "manfid", "oemid", "date", "serial", "type", "cid")}
    size = _read(f"{dev}/size")
    d["size_bytes"] = int(size) * 512 if size and size.isdigit() else None
    d.update(block_stats("mmcblk0"))
    return d


def block_stats(dev: str) -> dict:
    """Byte letti/scritti dall'avvio (da /sys/block/<dev>/stat)."""
    s = _read(f"/sys/block/{dev}/stat")
    if not s:
        return {}
    f = s.split()
    return {"read_bytes": int(f[2]) * 512, "written_bytes": int(f[6]) * 512, "writes": int(f[4])}


def root_device() -> str | None:
    for dev in ("mmcblk0", "sda", "nvme0n1"):
        if os.path.isdir(f"/sys/block/{dev}"):
            return dev
    return None


def net_counters() -> dict:
    out = {}
    for line in (_read("/proc/net/dev", "") or "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        f = rest.split()
        if len(f) >= 9 and name.strip() != "lo":
            out[name.strip()] = {"rx_bytes": int(f[0]), "tx_bytes": int(f[8]), "rx_err": int(f[2]), "tx_err": int(f[10])}
    return out


def usb_devices() -> list[str]:
    try:
        return subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return []


def i2c_buses() -> list[str]:
    try:
        return sorted(d for d in os.listdir("/dev") if d.startswith("i2c-"))
    except OSError:
        return []


def processes(names=("seismicpi",)) -> list[dict]:
    """Processi del sismografo con memoria residente e CPU cumulata."""
    out = []
    tck = os.sysconf("SC_CLK_TCK")
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        cmd = (_read(f"/proc/{pid}/cmdline", "") or "").replace("\x00", " ").strip()
        if not any(n in cmd for n in names):
            continue
        st = _read(f"/proc/{pid}/status", "") or ""
        rss = next((int(line.split()[1]) * 1024 for line in st.splitlines() if line.startswith("VmRSS")), 0)
        try:
            parts = (_read(f"/proc/{pid}/stat") or "").rsplit(")", 1)[1].split()
            cpu_s = (int(parts[11]) + int(parts[12])) / tck
        except (IndexError, ValueError):
            cpu_s = None
        out.append({"pid": int(pid), "cmd": cmd[-80:], "rss": rss, "cpu_s": cpu_s})
    return out


def hardware() -> dict:
    return {"model": model(), "cpu": cpu_info(), "freq": cpu_freq(), "vc": vcgencmd_info(),
            "throttled": throttled(), "sd": sd_card(), "net": net_counters(), "usb": usb_devices(),
            "i2c": i2c_buses(), "processes": processes(), "temp": cpu_temp()}
