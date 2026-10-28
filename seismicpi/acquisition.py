"""Servizio di acquisizione: legge il sensore, filtra, rileva gli eventi, archivia e trasmette.

È l'unico processo che tocca il sensore e i file raw/rullo; gira con priorità alta e non fa nulla
di lento nel ciclo principale (le scritture su SQLite vanno in un thread a parte).

    python3 -m seismicpi.acquisition
"""
from __future__ import annotations

import array
import asyncio
import collections
import concurrent.futures
import logging
import math
import os
import signal
import time

from . import VERSION, config, db, dsp, ipc, storage, sysinfo
from .analysis import EventAccumulator
from .sensor import SensorError, make as make_sensor

log = logging.getLogger("seismicpi.acq")


class SampleClock:
    """Assegna un tempo a ogni campione: il chip ha un oscillatore impreciso (±1-2%), quindi la
    frequenza vera si stima sul lungo periodo e la fase si corregge piano verso l'orologio di sistema.
    """

    def __init__(self, fs_nom: float):
        self.fs_nom = fs_nom
        self.reset()

    def reset(self):
        self.t_next = None
        self.p = 1.0 / self.fs_nom
        self.hist = collections.deque()
        self.count = 0

    @property
    def fs(self) -> float:
        return 1.0 / self.p

    def stamp(self, n: int, now: float) -> tuple[float, float, bool]:
        resync = False
        if self.t_next is None:
            resync = True
        else:
            t_last = self.t_next + (n - 1) * self.p
            err = now - 0.5 * self.p - t_last
            if abs(err) > 0.5:
                resync = True
                self.hist.clear()
            else:
                self.t_next += 0.05 * err
        if resync:
            self.t_next = now - (n - 0.5) * self.p
        t_first = self.t_next
        self.t_next += n * self.p
        self.count += n
        self.hist.append((now, self.count))
        while len(self.hist) > 2 and now - self.hist[0][0] > 300:
            self.hist.popleft()
        span = now - self.hist[0][0]
        if span > 20:
            fs = (self.count - self.hist[0][1]) / span
            if abs(fs / self.fs_nom - 1) < 0.05:
                self.p += (1.0 / fs - self.p) * 0.05
        return t_first, self.p, resync


def _axis_map(cfg):
    out = []
    for ch in "ENZ":
        spec = cfg["sensor"]["axis_map"][ch]
        out.append(("xyz".index(spec[1]), -1 if spec[0] == "-" else 1))
    return out


class Acquisition:
    def __init__(self):
        self.cfg = config.load()
        self.watch = config.ConfigWatcher()
        self.data_dir = config.DATA_DIR
        os.makedirs(self.data_dir, exist_ok=True)
        self.raw = None
        self._setup_raw()
        self.stats_pending = []
        self.heli = storage.Helicorder(self.data_dir)
        self.events = storage.EventStore(self.data_dir)
        self.dbx = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="db")
        self.con = None
        self.server = ipc.Server(config.IPC_PATH, self.handle)
        self.sensor = None
        self.sensor_info = {}
        self.sensor_ok = False
        self.sensor_error = ""
        self.next_open = 0.0
        self.consecutive_errors = 0
        self.paused = False
        self.started = time.time()
        self.counters = collections.Counter()
        self.temp = None
        self.clock_synced = None
        self.proc_cpu = sysinfo.ProcCpu()
        self.acq_cpu = 0.0
        self.collectors: list = []
        self.last_event_id = None
        self.ring = collections.deque()
        self.block = None
        self._setup_processing(initial=True)

    # ================================================================ configurazione / filtri
    def _setup_processing(self, initial=False):
        c = self.cfg
        fs = float(c["sensor"]["sample_rate"])
        self.fs_nom = fs
        if initial or not hasattr(self, "clock") or self.clock.fs_nom != fs:
            self.clock = SampleClock(fs)
        p, t = c["processing"], c["trigger"]
        self.amap = _axis_map(c)
        self.offsets = [c["sensor"]["offsets_g"][k] for k in "ENZ"]
        self.f_disp = [dsp.Biquad("hp", p["display_highpass_hz"], fs) for _ in range(3)]
        self.f_trig = [dsp.bandpass(p["trigger_highpass_hz"], p["trigger_lowpass_hz"], fs) for _ in range(3)]
        self.f_vel = [dsp.Velocity(fs, p["velocity_highpass_hz"]) for _ in range(3)]
        ch = t["channel"]
        n = 3 if ch == "any" else 1
        self.stalta = [dsp.StaLta(t["sta_s"], t["lta_s"], fs) for _ in range(n)]
        self.primed = False
        self.trig = t
        self.state = "idle"            # idle | active | post
        self.acc = None
        self.cooldown_until = 0.0
        self.manual_until = None
        self.ratio = 0.0
        self.sta_mg = self.lta_mg = 0.0
        keep = t["pre_event_s"] + t["max_duration_s"] + t["post_event_s"] + 30
        self.ring_keep = max(keep, 130)
        self.minute = None
        self._reset_minute()

    def _reset_minute(self):
        self.m_n = 0
        self.m_sq = [0.0, 0.0, 0.0, 0.0]
        self.m_peak = 0.0
        self.m_sum = [0.0, 0.0, 0.0]
        self.m_ratio = 0.0
        self.m_counters = collections.Counter()
        self.m_t0 = None

    def _reload(self, cfg):
        old = self.cfg
        self.cfg = cfg
        sensor_changed = old["sensor"] != cfg["sensor"]
        if sensor_changed or old["processing"] != cfg["processing"] or old["trigger"] != cfg["trigger"]:
            self._close_block()
            if self.state != "idle":
                log.info("configurazione cambiata durante un evento: l'evento viene chiuso")
                self._finalize(time.time())
            self._setup_processing()
        if cfg["storage"]["raw_mode"] != self.raw_mode:
            self._close_block()
            self._setup_raw()
        if sensor_changed:
            self._close_sensor("riconfigurazione")
            self.next_open = 0
        log.info("configurazione ricaricata")

    # ================================================================ sensore
    def _open_sensor(self):
        try:
            self.sensor = make_sensor(self.cfg)
            self.sensor_info = self.sensor.open()
            self.sensor_ok = True
            self.sensor_error = ""
            self.consecutive_errors = 0
            self.clock.reset()
            self.primed = False
            log.info("sensore aperto: %s", self.sensor_info)
            self._db(db.log, "info", "sensore", f"sensore attivo: {self.sensor_info.get('chip')}")
        except SensorError as e:
            self.sensor = None
            self.sensor_ok = False
            if str(e) != self.sensor_error:
                log.error("%s", e)
                self._db(db.log, "error", "sensore", str(e))
            self.sensor_error = str(e)
            self.next_open = time.monotonic() + 5

    def _close_sensor(self, why):
        if self.sensor:
            self.sensor.close()
        self.sensor = None
        self.sensor_ok = False
        self.sensor_error = why
        self._close_block()

    # ================================================================ blocchi raw
    def _close_block(self):
        b = self.block
        self.block = None
        if not b or len(b[3]) == 0:
            return
        blk = (b[0], b[1], b[2], b[3])
        if self.raw:
            self.raw.append(*blk)
        self.ring.append(blk)
        now_t = b[0]
        while self.ring and now_t - self.ring[0][0] > self.ring_keep:
            self.ring.popleft()

    # ================================================================ elaborazione
    def _process(self, samples, overflow, now):
        if overflow:
            self.counters["overflows"] += 1
            self.m_counters["overflows"] += 1
            self._close_block()
            self.clock.reset()
            log.warning("FIFO del sensore traboccata: buco nei dati")
        if not samples:
            return None
        t0, period, resync = self.clock.stamp(len(samples), now)
        if resync and self.clock.count > len(samples):
            self.counters["gaps"] += 1
            self.m_counters["gaps"] += 1
            self._close_block()
        lsb = self.sensor.lsb_per_g
        inv = 1.0 / lsb
        amap = self.amap
        off = self.offsets
        fd, ft, fv = self.f_disp, self.f_trig, self.f_vel
        st = self.stalta
        chan = self.trig["channel"]
        min_sta2 = self.trig["min_sta_mg"] ** 2
        on, offr = self.trig["on_ratio"], self.trig["off_ratio"]
        pga_thr = self.trig["pga_threshold_mg"]
        enabled = self.trig["enabled"]
        live = []
        rmax = 0.0
        heli_add = self.heli.add
        if self.block is None:
            self.block = [t0, 1.0 / period, lsb, array.array("h")]
        blk = self.block

        if not self.primed:
            raw0 = samples[0]
            g0 = [amap[i][1] * raw0[amap[i][0]] * inv for i in range(3)]
            for i in range(3):
                fd[i].prime(g0[i])
                ft[i].prime(g0[i])
            self.primed = True

        for k, s in enumerate(samples):
            t = t0 + k * period
            e = amap[0][1] * s[amap[0][0]]
            n = amap[1][1] * s[amap[1][0]]
            z = amap[2][1] * s[amap[2][0]]
            blk[3].extend((e, n, z))
            g = (e * inv, n * inv, z * inv)
            de = fd[0](g[0]) * 1000.0
            dn = fd[1](g[1]) * 1000.0
            dz = fd[2](g[2]) * 1000.0
            dv = math.sqrt(de * de + dn * dn + dz * dz)
            te = ft[0](g[0]) * 1000.0
            tn = ft[1](g[1]) * 1000.0
            tz = ft[2](g[2]) * 1000.0
            ve, vn, vz = fv[0](de / 1000.0), fv[1](dn / 1000.0), fv[2](dz / 1000.0)
            # funzione caratteristica (energia, mg²) e STA/LTA
            if chan == "any":
                r0, r1, r2 = st[0](te * te), st[1](tn * tn), st[2](tz * tz)
                ratio = max(r0, r1, r2)
                sta = max(st[0].sta, st[1].sta, st[2].sta)
                lta = max(st[0].lta, st[1].lta, st[2].lta)
            else:
                if chan == "Z":
                    cf = tz * tz
                elif chan == "E":
                    cf = te * te
                elif chan == "N":
                    cf = tn * tn
                elif chan == "horizontal":
                    cf = te * te + tn * tn
                else:
                    cf = te * te + tn * tn + tz * tz
                ratio = st[0](cf)
                sta, lta = st[0].sta, st[0].lta
            if ratio > rmax:
                rmax = ratio
            heli_add(t, (de, dn, dz, dv))
            live.extend((round(de, 2), round(dn, 2), round(dz, 2)))

            # statistiche al minuto
            mk = int(t // 60)
            if mk != self.minute:
                if self.minute is not None and self.m_n:
                    self._write_minute()
                self.minute = mk
                self._reset_minute()
            self.m_n += 1
            self.m_sq[0] += de * de
            self.m_sq[1] += dn * dn
            self.m_sq[2] += dz * dz
            self.m_sq[3] += dv * dv
            if dv > self.m_peak:
                self.m_peak = dv
            self.m_sum[0] += g[0] - off[0]
            self.m_sum[1] += g[1] - off[1]
            self.m_sum[2] += g[2] - off[2]
            if ratio > self.m_ratio:
                self.m_ratio = ratio
            for c in self.collectors:
                c.add(g)

            # macchina a stati del trigger
            state = self.state
            if state == "idle":
                if enabled and t >= self.cooldown_until:
                    if st[0].ready and ratio >= on and sta >= min_sta2:
                        self._trigger(t, "stalta")
                    elif pga_thr > 0 and dv >= pga_thr and st[0].ready:
                        self._trigger(t, "pga")
            if self.state != "idle":
                acc = self.acc
                if self.manual_until is not None:
                    active = t < self.manual_until
                else:
                    active = ratio >= offr or (acc.reason == "pga" and dv >= pga_thr * 0.5)
                if self.state == "active" and not active:
                    self.state = "post"
                    acc.t_detrigger = t
                elif self.state == "post" and active and self.manual_until is None and ratio >= on:
                    self.state = "active"        # nuovo impulso entro il post-evento: stesso evento
                    acc.t_detrigger = None
                acc.add((de, dn, dz), (ve, vn, vz), dv, ratio, self.state == "active")
                if self.state == "post" and t >= acc.t_detrigger + self.trig["post_event_s"]:
                    self._finalize(t)
                elif t - acc.t_trigger > self.trig["max_duration_s"]:
                    if acc.t_detrigger is None:
                        acc.t_detrigger = t
                    self._finalize(t)
            if len(blk[3]) >= 3 * int(self.fs_nom):
                self._close_block()
                blk = self.block = [t + period, 1.0 / period, lsb, array.array("h")]

        self.ratio = rmax
        self.sta_mg = math.sqrt(max(0.0, sta))
        self.lta_mg = math.sqrt(max(0.0, lta))
        for c in list(self.collectors):
            if c.done():
                self.collectors.remove(c)
        return {"t": "live", "t0": round(t0, 4), "fs": round(1.0 / period, 4), "d": live, "r": round(rmax, 2)}

    def _trigger(self, t, reason, hold=None):
        for s in self.stalta:
            s.frozen = self.trig["freeze_lta"]
        self.state = "active"
        lm = getattr(self, "last_minute", None) or {}
        noise = tuple(lm.get(k) or 3.0 for k in ("rms_e", "rms_n", "rms_z"))
        self.acc = EventAccumulator(t, reason, self.fs_nom, noise)
        self.manual_until = t + hold if hold else None
        self.counters["triggers"] += 1
        info = {"t": "event", "phase": "start", "t_trigger": t, "reason": reason,
                "ratio": round(self.ratio, 2)}
        log.info("TRIGGER (%s) a %s", reason, time.strftime("%H:%M:%S", time.gmtime(t)))
        self.server.broadcast(info)

    def _finalize(self, t_now):
        acc = self.acc
        self.state = "idle"
        self.acc = None
        self.manual_until = None
        for s in self.stalta:
            s.frozen = False
        self.cooldown_until = t_now + self.trig["cooldown_s"]
        if acc is None:
            return
        t_det = acc.t_detrigger or t_now
        res = acc.result(t_det)
        if acc.reason in ("stalta", "pga") and res["duration"] < self.trig["min_duration_s"]:
            self.counters["discarded"] += 1
            self.server.broadcast({"t": "event", "phase": "discarded", "t_trigger": acc.t_trigger,
                                   "duration": res["duration"]})
            return
        t_start = acc.t_trigger - self.trig["pre_event_s"]
        t_end = t_det + self.trig["post_event_s"]
        pool = list(self.ring)
        if self.block and self.block[3]:      # blocco in corso: copia, perché continua a crescere
            b = self.block
            pool.append((b[0], b[1], b[2], array.array("h", b[3])))
        blocks = [b for b in pool if b[0] < t_end and b[0] + len(b[3]) / 3 / b[1] > t_start]
        ev = dict(res)
        ev.update({"t_trigger": acc.t_trigger, "t_start": t_start, "t_end": t_end,
                   "temp_c": None if self.temp is None else round(self.temp, 1),
                   "clock_synced": None if self.clock_synced is None else int(self.clock_synced),
                   "fs": round(self.clock.fs, 3), "samples": sum(len(b[3]) // 3 for b in blocks)})
        ev.setdefault("extra", {})["sensor"] = self.sensor_info.get("chip")

        def store():
            eid = db.insert_event(self.con, ev)
            size = self.events.save(eid, blocks)
            self.con.execute("UPDATE events SET file_size=? WHERE id=?", (size, eid))
            return eid

        fut = asyncio.get_running_loop().run_in_executor(self.dbx, store)

        def done(f):
            try:
                eid = f.result()
            except Exception:  # noqa: BLE001
                log.exception("salvataggio evento fallito")
                return
            self.last_event_id = eid
            ev["id"] = eid
            log.info("evento #%d: %s PGA %.2f mg, MMI %.1f, %.1f s", eid, ev["class"], ev["pga"], ev["mmi"],
                     ev["duration"])
            self.server.broadcast({"t": "event", "phase": "end", "event": ev})
        fut.add_done_callback(done)

    def _write_minute(self):
        n = self.m_n
        rms = [round(math.sqrt(v / n), 4) for v in self.m_sq]
        row = (self.minute * 60, *rms, round(self.m_peak, 3),
               *(round(v / n, 6) for v in self.m_sum),
               None if self.temp is None else round(self.temp, 2), round(self.clock.fs, 4),
               self.m_counters["gaps"], self.m_counters["overflows"], self.m_counters["i2c_errors"],
               round(self.m_ratio, 2))
        self.last_minute = {"t": self.minute * 60, "rms_e": rms[0], "rms_n": rms[1], "rms_z": rms[2],
                            "rms_v": rms[3], "peak_v": round(self.m_peak, 3)}
        # le statistiche si accumulano e vanno nel DB a gruppi: meno scritture sulla SD
        self.stats_pending.append(row)
        if len(self.stats_pending) >= self.cfg["storage"]["stats_batch_minutes"]:
            self._flush_stats()
        self.server.broadcast({"t": "minute", **self.last_minute})

    def _flush_stats(self):
        rows, self.stats_pending = self.stats_pending, []
        if rows:
            self._db(lambda con: con.executemany(
                "INSERT OR REPLACE INTO stats_minute VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows))

    def _setup_raw(self):
        if self.raw:
            self.raw.flush()
            self.raw.close()
        base = storage.raw_base(self.cfg)
        self.raw = storage.RawArchive(base) if base else None
        self.raw_mode = self.cfg["storage"]["raw_mode"]
        log.info("archivio grezzo: %s", f"{self.raw_mode} ({base})" if base else "disattivato")

    def _db(self, fn, *args):
        if self.con is None:
            return
        try:
            asyncio.get_running_loop().run_in_executor(self.dbx, fn, self.con, *args)
        except RuntimeError:
            fn(self.con, *args)

    # ================================================================ comandi IPC
    async def handle(self, msg):
        cmd = msg.get("cmd")
        if cmd == "ping":
            return {"ok": True, "version": VERSION}
        if cmd == "status":
            return {"ok": True, "status": self.status()}
        if cmd == "reload":
            self._reload(config.load())
            return {"ok": True}
        if cmd == "recent":
            secs = min(float(msg.get("seconds", 60)), self.ring_keep)
            t_from = time.time() - secs
            blocks = list(self.ring)
            if self.block and self.block[3]:
                blocks.append(tuple(self.block))
            out = [{"t0": b[0], "fs": b[1], "lsb": b[2], "d": list(b[3])}
                   for b in blocks if b[0] + len(b[3]) / 3 / b[1] >= t_from]
            return {"ok": True, "blocks": out}
        if cmd == "measure":
            secs = float(msg.get("seconds", 5))
            col = _Collector(int(secs * self.fs_nom))
            self.collectors.append(col)
            res = await asyncio.wait_for(col.future, secs + 10)
            return {"ok": True, **res}
        if cmd == "orient":
            if not self.sensor_ok:
                return {"ok": False, "error": "sensore non disponibile"}
            sums = [0, 0, 0]
            for _ in range(20):
                v = self.sensor.read_instant()
                for i in range(3):
                    sums[i] += v[i]
                await asyncio.sleep(0.01)
            means = [s / 20 / self.sensor.lsb_per_g for s in sums]
            zi = max(range(3), key=lambda i: abs(means[i]))
            others = [i for i in range(3) if i != zi]
            proposal = {"Z": ("+" if means[zi] > 0 else "-") + "xyz"[zi],
                        "E": "+" + "xyz"[others[0]], "N": "+" + "xyz"[others[1]]}
            return {"ok": True, "chip_axes_g": [round(m, 4) for m in means], "proposal": proposal}
        if cmd == "selftest":
            if not self.sensor_ok:
                return {"ok": False, "error": "sensore non disponibile"}
            self.paused = True
            try:
                res = await asyncio.get_running_loop().run_in_executor(None, self.sensor.self_test)
            finally:
                self._close_block()
                self.clock.reset()
                self.paused = False
            return {"ok": True, "result": res}
        if cmd == "inject":
            if not hasattr(self.sensor, "inject"):
                return {"ok": False, "error": "disponibile solo con il sensore simulato"}
            return {"ok": True, "info": self.sensor.inject(msg.get("kind", "quake"), amp_mg=msg.get("amp_mg"))}
        if cmd == "record":
            if self.state != "idle":
                return {"ok": False, "error": "evento già in corso"}
            secs = max(5.0, min(float(msg.get("seconds", 60)), self.trig["max_duration_s"]))
            self._trigger(time.time(), "manuale", hold=secs)
            return {"ok": True, "seconds": secs}
        if cmd == "reopen":
            self._close_sensor("riapertura richiesta")
            self.next_open = 0
            return {"ok": True}
        return {"ok": False, "error": f"comando sconosciuto: {cmd}"}

    def status(self) -> dict:
        ev = None
        if self.acc:
            ev = {"t_trigger": self.acc.t_trigger, "reason": self.acc.reason, "pga": round(self.acc.pga_v, 2),
                  "peak_ratio": round(self.acc.peak_ratio, 2), "state": self.state}
        warm = self.stalta[0]
        return {
            "version": VERSION, "driver": self.cfg["sensor"]["driver"], "sensor_ok": self.sensor_ok,
            "sensor_error": self.sensor_error, "sensor": self.sensor_info, "paused": self.paused,
            "fs": round(self.clock.fs, 3), "fs_nominal": self.fs_nom,
            "temp": None if self.temp is None else round(self.temp, 2),
            "state": self.state, "ratio": round(self.ratio, 2), "sta_mg": round(self.sta_mg, 3),
            "lta_mg": round(self.lta_mg, 3), "warmup": round(min(1.0, warm.warm / max(1, warm.n_warm)), 3),
            "trigger_enabled": self.trig["enabled"], "on_ratio": self.trig["on_ratio"],
            "off_ratio": self.trig["off_ratio"],
            "counters": dict(self.counters), "event": ev, "last_event_id": self.last_event_id,
            "clock_synced": self.clock_synced, "uptime": round(time.time() - self.started),
            "acq_cpu": self.acq_cpu, "minute": getattr(self, "last_minute", None),
        }

    # ================================================================ ciclo principale
    async def run(self):
        loop = asyncio.get_running_loop()
        self.con = await loop.run_in_executor(self.dbx, db.connect)
        r = self.con.execute("SELECT MAX(id) FROM events").fetchone()
        self.last_event_id = r[0] if r else None
        await self.server.start()
        log.info("acquisizione avviata (v%s, driver %s, dati in %s)", VERSION, self.cfg["sensor"]["driver"],
                 self.data_dir)
        stop = asyncio.Event()
        for s in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(s, stop.set)
        t_status = t_cfg = t_slow = 0.0
        t_flush = t_heli = time.monotonic()
        t_house = time.monotonic() + 60
        t_ram = time.monotonic() + 120
        while not stop.is_set():
            interval = self.cfg["sensor"]["read_interval_ms"] / 1000
            t_loop = time.monotonic()
            if not self.sensor_ok and not self.paused and t_loop >= self.next_open:
                self._open_sensor()
            if self.sensor_ok and not self.paused:
                try:
                    samples, overflow = self.sensor.read()
                    self.consecutive_errors = 0
                    pkt = self._process(samples, overflow, time.time())
                    if pkt and self.server.clients:
                        self.server.broadcast(pkt)
                except SensorError as e:
                    self.counters["i2c_errors"] += 1
                    self.m_counters["i2c_errors"] += 1
                    self.consecutive_errors += 1
                    if self.consecutive_errors >= 5:
                        log.error("sensore perso: %s", e)
                        self._db(db.log, "error", "sensore", f"sensore perso: {e}")
                        self._close_sensor(str(e))
                        self.next_open = time.monotonic() + 5
                        self.server.broadcast({"t": "sensor", "ok": False, "error": str(e)})
            now_m = time.monotonic()
            if now_m - t_status >= 1.0:
                t_status = now_m
                if self.sensor_ok and not self.paused:
                    try:
                        self.temp = self.sensor.temperature() + self.cfg["sensor"]["temp_offset_c"]
                    except SensorError:
                        self.counters["i2c_errors"] += 1
                self.server.broadcast({"t": "status", **self.status()})
            st = self.cfg["storage"]
            # in RAM si scarica spesso (costa nulla e il web legge i dati freschi); su SD a blocchi grandi
            if now_m - t_flush >= (10 if self.raw_mode == "ram" else st["flush_interval_s"]):
                t_flush = now_m
                if self.raw:
                    self.raw.flush()
            if now_m - t_heli >= st["heli_flush_s"]:
                t_heli = now_m
                self.heli.flush()
            if self.raw_mode == "ram" and now_m >= t_ram:
                t_ram = now_m + 600
                if self.raw:
                    await loop.run_in_executor(None, self.raw.purge_hours, st["raw_ram_hours"])
            if now_m - t_cfg >= 2.0:
                t_cfg = now_m
                if self.watch.changed():
                    self._reload(self.watch.cfg)
            if now_m - t_slow >= 60:
                t_slow = now_m
                self.acq_cpu = self.proc_cpu.pct()
                self.clock_synced = await loop.run_in_executor(None, sysinfo.clock_synced)
            if now_m >= t_house:
                t_house = now_m + 3600
                await loop.run_in_executor(self.dbx, self._housekeeping)
            await asyncio.sleep(max(0.005, interval - (time.monotonic() - t_loop)))
        log.info("arresto: salvataggio buffer")
        if self.state != "idle":
            self._finalize(time.time())
        self._close_block()
        if self.raw:
            self.raw.flush(fsync=self.raw_mode == "sd")
            self.raw.close()
        self._flush_stats()
        self.heli._commit()
        self.heli.flush()
        if self.sensor:
            self.sensor.close()
        await asyncio.sleep(0.5)
        self.dbx.shutdown(wait=True)

    def _housekeeping(self):
        st = self.cfg["storage"]
        removed = []
        if self.raw and self.raw_mode == "sd":
            removed += self.raw.purge(st["raw_retention_days"])
        sd_raw = storage.RawArchive(self.data_dir) if self.raw_mode != "sd" else self.raw
        if self.raw_mode != "sd":
            removed += sd_raw.purge(st["raw_retention_days"])   # residui di quando si archiviava su SD
        removed += self.heli.purge(st["helicorder_retention_days"])
        db.purge(self.con, st["stats_retention_days"])
        while storage.disk_usage(self.data_dir)["free"] < st["min_free_mb"] * 1024 * 1024:
            d = sd_raw.drop_oldest()
            if not d:
                db.log(self.con, "warning", "archivio", "spazio su disco sotto la soglia minima")
                break
            removed.append(d)
            db.log(self.con, "warning", "archivio", f"spazio scarso: eliminato il giorno raw {d}")
        if removed:
            log.info("pulizia archivio: %s", removed)


class _Collector:
    """Media dei valori in g per n campioni (calibrazione)."""

    def __init__(self, n):
        self.n = n
        self.k = 0
        self.s = [0.0, 0.0, 0.0]
        self.sq = [0.0, 0.0, 0.0]
        self.future = asyncio.get_running_loop().create_future()

    def add(self, g):
        if self.k >= self.n:
            return
        for i in range(3):
            self.s[i] += g[i]
            self.sq[i] += g[i] * g[i]
        self.k += 1
        if self.k >= self.n and not self.future.done():
            mean = [v / self.n for v in self.s]
            std = [math.sqrt(max(0.0, self.sq[i] / self.n - mean[i] ** 2)) for i in range(3)]
            self.future.set_result({"mean_g": [round(m, 6) for m in mean],
                                    "std_mg": [round(s * 1000, 3) for s in std], "samples": self.n})

    def done(self):
        return self.k >= self.n


def main():
    logging.basicConfig(level=os.environ.get("SEISMICPI_LOG", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(Acquisition().run())


if __name__ == "__main__":
    main()
