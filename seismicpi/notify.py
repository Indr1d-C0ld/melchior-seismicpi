"""Allerte: Telegram (messaggi, immagini, comandi del bot), webhook, MQTT, uscita GPIO.

Ogni messaggio passa dalla tabella `outbox`: se la rete manca (es. modalità Access Point) resta
in coda e parte appena possibile.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import logging
import shutil
import subprocess
import time

import aiohttp

from . import analysis, db, dsp, netmgr, pngplot, storage, sysinfo

log = logging.getLogger("seismicpi.notify")

TG_API = "https://api.telegram.org/bot{token}/{method}"


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_time(t, tz_local=True) -> str:
    if t is None:
        return "?"
    d = dt.datetime.fromtimestamp(t) if tz_local else dt.datetime.fromtimestamp(t, dt.timezone.utc)
    return d.strftime("%d/%m/%Y %H:%M:%S")


def _in_quiet(rules) -> bool:
    if not rules["quiet_hours_enabled"]:
        return False
    now = dt.datetime.now().strftime("%H:%M")
    a, b = rules["quiet_from"], rules["quiet_to"]
    return (a <= now < b) if a <= b else (now >= a or now < b)


class Notifier:
    def __init__(self, app):
        self.app = app
        self.session: aiohttp.ClientSession | None = None
        self.tg_offset = None
        self.tg_ok = None
        self.tg_error = ""
        self.tg_bot = None
        self.early_sent = set()
        self.sys_flags = {}
        self.mqtt = None
        self.mqtt_error = ""
        self.last_report_day = None

    @property
    def cfg(self):
        return self.app["cfg"]

    @property
    def con(self):
        return self.app["db"]

    # ============================================================ coda
    def enqueue(self, channel: str, payload: dict):
        self.con.execute("INSERT INTO outbox(created, channel, payload) VALUES (?,?,?)",
                         (time.time(), channel, json.dumps(payload)))

    def muted(self) -> bool:
        return self.cfg["alerts"]["muted_until"] > time.time()

    async def outbox_loop(self):
        while True:
            await asyncio.sleep(4)
            try:
                rows = self.con.execute("SELECT * FROM outbox WHERE sent IS NULL AND attempts < 30 "
                                        "ORDER BY id LIMIT 10").fetchall()
                for r in rows:
                    p = json.loads(r["payload"])
                    try:
                        await self._deliver(r["channel"], p)
                        self.con.execute("UPDATE outbox SET sent=?, last_error=NULL WHERE id=?", (time.time(), r["id"]))
                    except Exception as e:  # noqa: BLE001
                        self.con.execute("UPDATE outbox SET attempts=attempts+1, last_error=? WHERE id=?",
                                         (str(e)[:300], r["id"]))
                        break               # rete giù: inutile insistere sugli altri adesso
            except Exception:  # noqa: BLE001
                log.exception("errore nella coda notifiche")

    async def _deliver(self, channel, p):
        if channel == "telegram":
            tg = self.cfg["alerts"]["telegram"]
            if not tg["enabled"] or not tg["token"]:
                return
            chats = [p["chat"]] if p.get("chat") else tg["chat_ids"]
            img = None
            if p.get("event_image") and tg["send_image"]:
                img = await self.event_png(p["event_image"])
            elif p.get("recent_image"):
                img = await self.recent_png(p["recent_image"])
            for chat in chats:
                if img:
                    await self.tg_photo(chat, img, p["text"])
                else:
                    await self.tg_send(chat, p["text"])
        elif channel == "webhook":
            wh = self.cfg["alerts"]["webhook"]
            if not wh["enabled"] or not wh["url"]:
                return
            body = json.dumps(p).encode()
            headers = {"Content-Type": "application/json"}
            if wh["secret"]:
                headers["X-SeismicPi-Signature"] = hmac.new(wh["secret"].encode(), body, hashlib.sha256).hexdigest()
            async with self.session.post(wh["url"], data=body, headers=headers, timeout=20) as r:
                if r.status >= 400:
                    raise RuntimeError(f"webhook HTTP {r.status}")

    # ============================================================ Telegram
    async def tg(self, method, token=None, **kw):
        token = token or self.cfg["alerts"]["telegram"]["token"]
        url = TG_API.format(token=token, method=method)
        timeout = kw.pop("_timeout", 30)
        data = kw.pop("_data", None)
        if data is not None:
            async with self.session.post(url, data=data, timeout=timeout) as r:
                res = await r.json(content_type=None)
        else:
            async with self.session.post(url, json=kw, timeout=timeout) as r:
                res = await r.json(content_type=None)
        if not res.get("ok"):
            raise RuntimeError(f"Telegram: {res.get('description', 'errore')}")
        return res["result"]

    async def tg_send(self, chat, text):
        return await self.tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML",
                             disable_web_page_preview=True)

    async def tg_photo(self, chat, png: bytes, caption: str):
        form = aiohttp.FormData()
        form.add_field("chat_id", str(chat))
        form.add_field("caption", caption[:1024])
        form.add_field("parse_mode", "HTML")
        form.add_field("photo", png, filename="sismogramma.png", content_type="image/png")
        return await self.tg("sendPhoto", _data=form, _timeout=60)

    async def tg_loop(self):
        """Riceve i comandi del bot (long polling)."""
        while True:
            tg = self.cfg["alerts"]["telegram"]
            if not (tg["enabled"] and tg["token"]):
                self.tg_ok = None
                await asyncio.sleep(10)
                continue
            try:
                if self.tg_bot is None or self.tg_bot.get("_token") != tg["token"]:
                    me = await self.tg("getMe")
                    self.tg_bot = {**me, "_token": tg["token"]}
                if not tg["commands_enabled"]:
                    self.tg_ok, self.tg_error = True, ""
                    await asyncio.sleep(30)
                    continue
                kw = {"timeout": 25, "allowed_updates": ["message"]}
                if self.tg_offset is not None:
                    kw["offset"] = self.tg_offset
                updates = await self.tg("getUpdates", _timeout=40, **kw)
                self.tg_ok, self.tg_error = True, ""
                for u in updates:
                    self.tg_offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    if msg.get("text"):
                        await self._command(msg)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.tg_ok, self.tg_error = False, str(e)
                await asyncio.sleep(20)

    async def _command(self, msg):
        chat = str(msg["chat"]["id"])
        text = msg["text"].strip()
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        allowed = chat in self.cfg["alerts"]["telegram"]["chat_ids"]
        seen = db.kv_get(self.con, "tg_seen_chats", {})
        who = msg["chat"].get("title") or " ".join(filter(None, [msg["chat"].get("first_name"),
                                                                 msg["chat"].get("last_name")])) or msg["chat"].get("username", "")
        seen[chat] = {"name": who, "t": time.time(), "allowed": allowed}
        db.kv_set(self.con, "tg_seen_chats", seen)
        if cmd == "/start" or not allowed:
            reply = (f"👋 Ciao! Questo è il bot di <b>{esc(self.cfg['station']['name'])}</b>.\n"
                     f"L'ID di questa chat è <code>{chat}</code>.\n")
            reply += ("Sei già autorizzato: /aiuto per i comandi." if allowed else
                      "Per ricevere allerte e usare i comandi, aggiungilo nella dashboard (Allerte → Telegram).")
            await self.tg_send(chat, reply)
            return
        handler = {
            "/aiuto": self._c_help, "/help": self._c_help, "/stato": self._c_status, "/ultimi": self._c_last,
            "/evento": self._c_event, "/grafico": self._c_plot, "/rumore": self._c_noise, "/oggi": self._c_today,
            "/silenzia": self._c_mute, "/riattiva": self._c_unmute, "/ip": self._c_ip, "/catalogo": self._c_catalog,
            "/registra": self._c_record,
        }.get(cmd)
        if not handler:
            await self.tg_send(chat, "Comando non riconosciuto. /aiuto per l'elenco.")
            return
        try:
            await handler(chat, arg.strip())
        except Exception as e:  # noqa: BLE001
            log.exception("comando %s", cmd)
            await self.tg_send(chat, f"⚠️ Errore: {esc(e)}")

    async def _c_help(self, chat, arg):
        await self.tg_send(chat, "\n".join([
            "<b>Comandi SeismicPi</b>",
            "/stato – stato del sismografo e del sistema",
            "/ultimi [n] – ultimi eventi registrati",
            "/evento &lt;id&gt; – dettagli e sismogramma di un evento",
            "/grafico [secondi] – sismogramma degli ultimi secondi (max 120)",
            "/rumore – livello di rumore attuale e dell'ultima ora",
            "/oggi – riepilogo delle ultime 24 ore",
            "/catalogo – ultimi terremoti INGV/EMSC vicini",
            "/registra [secondi] – registra subito un evento manuale",
            "/silenzia [minuti] – sospende le allerte (default 60)",
            "/riattiva – riattiva le allerte",
            "/ip – indirizzi di rete del Pi"]))

    async def _c_status(self, chat, arg):
        await self.tg_send(chat, self.status_text())

    def status_text(self) -> str:
        st = self.app["acq_status"] or {}
        net = netmgr.read_status()
        du = storage.disk_usage(self.app["data_dir"])
        m = st.get("minute") or {}
        lines = [f"📟 <b>{esc(self.cfg['station']['name'])}</b>",
                 f"Sensore: {'✅ attivo' if st.get('sensor_ok') else '❌ ' + esc(st.get('sensor_error') or 'non disponibile')}",
                 f"Campionamento: {st.get('fs', '?')} Hz · stato trigger: {st.get('state', '?')}",
                 f"Rumore (1 min): {m.get('rms_v', '?')} mg RMS · STA/LTA {st.get('ratio', '?')}",
                 f"Temperatura sensore: {st.get('temp', '?')} °C · CPU {sysinfo.cpu_temp() or '?'} °C",
                 f"Orologio: {'sincronizzato' if st.get('clock_synced') else 'NON sincronizzato'}",
                 f"Rete: {net.get('mode')} · disco {du['pct']}%",
                 f"Uptime acquisizione: {dt.timedelta(seconds=int(st.get('uptime', 0)))}"]
        if self.muted():
            lines.append(f"🔕 Allerte sospese fino alle {fmt_time(self.cfg['alerts']['muted_until'])}")
        return "\n".join(lines)

    async def _c_last(self, chat, arg):
        n = max(1, min(int(arg or 5), 20))
        rows = db.rows(self.con.execute("SELECT * FROM events ORDER BY t_trigger DESC LIMIT ?", (n,)))
        if not rows:
            await self.tg_send(chat, "Nessun evento registrato.")
            return
        lines = ["<b>Ultimi eventi</b>"]
        for e in rows:
            lines.append(f"#{e['id']} {fmt_time(e['t_trigger'])} · {analysis.CLASSES.get(e['class'], e['class'])} · "
                         f"{e['pga']:.1f} mg · MMI {dsp.mmi_roman(e['mmi'] or 1)}")
        await self.tg_send(chat, "\n".join(lines))

    async def _c_event(self, chat, arg):
        if not arg.isdigit():
            await self.tg_send(chat, "Uso: /evento &lt;id&gt;")
            return
        e = self.con.execute("SELECT * FROM events WHERE id=?", (int(arg),)).fetchone()
        if not e:
            await self.tg_send(chat, "Evento non trovato.")
            return
        text = self.event_text(dict(e))
        img = await self.event_png(int(arg))
        if img:
            await self.tg_photo(chat, img, text)
        else:
            await self.tg_send(chat, text)

    async def _c_plot(self, chat, arg):
        secs = max(10, min(int(arg or 60), 120))
        img = await self.recent_png(secs)
        if not img:
            await self.tg_send(chat, "Dati non disponibili.")
            return
        await self.tg_photo(chat, img, f"Ultimi {secs} s · {fmt_time(time.time())}")

    async def _c_noise(self, chat, arg):
        r = self.con.execute("SELECT AVG(rms_v), MAX(peak_v), MIN(rms_v), MAX(rms_v) FROM stats_minute WHERE t > ?",
                             (time.time() - 3600,)).fetchone()
        st = self.app["acq_status"] or {}
        m = st.get("minute") or {}
        await self.tg_send(chat, f"<b>Rumore</b>\nUltimo minuto: {m.get('rms_v', '?')} mg RMS\n"
                                 f"Ultima ora: media {r[0] or 0:.2f} mg (min {r[2] or 0:.2f}, max {r[3] or 0:.2f}), "
                                 f"picco {r[1] or 0:.1f} mg")

    async def _c_today(self, chat, arg):
        await self.tg_send(chat, self.report_text(24))

    async def _c_catalog(self, chat, arg):
        rows = db.rows(self.con.execute("SELECT * FROM catalog WHERE t_origin > ? ORDER BY t_origin DESC LIMIT 8",
                                        (time.time() - 7 * 86400,)))
        if not rows:
            await self.tg_send(chat, "Nessun evento di catalogo negli ultimi 7 giorni.")
            return
        lines = ["<b>Terremoti recenti (cataloghi)</b>"]
        for c in rows:
            lines.append(f"M{c['mag']:.1f} {esc(c['place'])} · {fmt_time(c['t_origin'])}"
                         + (f" · {c['dist_km']:.0f} km" if c["dist_km"] is not None else "")
                         + (" · ✅ rilevato" if c["local_event"] else ""))
        await self.tg_send(chat, "\n".join(lines))

    async def _c_record(self, chat, arg):
        secs = max(5, min(int(arg or 60), 240))
        r = await self.app["acq"].call("record", seconds=secs)
        await self.tg_send(chat, f"⏺ Registrazione manuale di {secs} s avviata." if r.get("ok") else
                           f"Non posso registrare: {esc(r.get('error'))}")

    async def _c_mute(self, chat, arg):
        mins = max(1, min(int(arg or 60), 7 * 1440))
        await self.app["set_config"]({"alerts": {"muted_until": time.time() + mins * 60}})
        await self.tg_send(chat, f"🔕 Allerte sospese per {mins} minuti.")

    async def _c_unmute(self, chat, arg):
        await self.app["set_config"]({"alerts": {"muted_until": 0}})
        await self.tg_send(chat, "🔔 Allerte riattivate.")

    async def _c_ip(self, chat, arg):
        net = netmgr.read_status()
        ip = (net.get("ip") or {}).get("addresses") or []
        await self.tg_send(chat, f"Host: <code>{esc(netmgr.hostname())}</code>\nModalità: {net.get('mode')}\n"
                                 f"Indirizzi: {esc(', '.join(ip) or '?')}")

    # ============================================================ testi e immagini
    def event_text(self, e: dict) -> str:
        units = self.cfg["web"]["units"]
        cls = analysis.CLASSES.get(e["class"], e["class"])
        mmi = e["mmi"] or 1
        lines = [f"{'🔴' if mmi >= 4 else '🟠' if mmi >= 3 else '🟡'} <b>{esc(cls)}</b> · evento #{e['id']}",
                 f"🕒 {fmt_time(e['t_trigger'])}",
                 f"📈 PGA {fmt_acc(e['pga'], units)} (E {e['pga_e']:.1f} · N {e['pga_n']:.1f} · Z {e['pga_z']:.1f} mg)",
                 f"💨 PGV {e['pgv']:.3f} cm/s · durata {e['duration']:.1f} s"
                 + (f" · f dom. {e['dom_freq']:.1f} Hz" if e.get("dom_freq") else ""),
                 f"🏠 Intensità stimata: {dsp.mmi_roman(mmi)} ({mmi:.1f}) – {dsp.mmi_description(mmi)}"]
        if e.get("catalog_id"):
            c = self.con.execute("SELECT * FROM catalog WHERE id=?", (e["catalog_id"],)).fetchone()
            if c:
                lines.append(f"📡 {c['source'].upper()}: M{c['mag']:.1f} {esc(c['place'])}"
                             + (f", {c['dist_km']:.0f} km" if c["dist_km"] is not None else ""))
        if e.get("clock_synced") == 0:
            lines.append("⚠️ orologio non sincronizzato: l'orario può essere impreciso")
        return "\n".join(lines)

    async def event_png(self, eid: int) -> bytes | None:
        def build():
            e = self.con.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
            blocks = self.app["events"].load(eid)
            if not e or not blocks:
                return None
            return _png_from_blocks(blocks, e["t_start"], e["t_end"], e["t_trigger"],
                                    f"#{eid} {fmt_time(e['t_trigger'])} PGA {e['pga']:.1f} MG")
        return await asyncio.get_running_loop().run_in_executor(None, build)

    async def recent_png(self, secs: int) -> bytes | None:
        r = await self.app["acq"].call("recent", seconds=secs)
        import array
        blocks = [(b["t0"], b["fs"], b["lsb"], array.array("h", b["d"])) for b in r.get("blocks", [])]
        if not blocks:
            return None
        now = time.time()
        return await asyncio.get_running_loop().run_in_executor(
            None, _png_from_blocks, blocks, now - secs, now, None, f"ULTIMI {secs} S")

    def report_text(self, hours=24) -> str:
        t1 = time.time() - hours * 3600
        by = self.con.execute("SELECT class, COUNT(*), MAX(pga), MAX(mmi) FROM events WHERE t_trigger > ? GROUP BY class",
                              (t1,)).fetchall()
        noise = self.con.execute("SELECT AVG(rms_v), MIN(rms_v), MAX(rms_v) FROM stats_minute WHERE t > ?", (t1,)).fetchone()
        cats = self.con.execute("SELECT COUNT(*), MAX(mag) FROM catalog WHERE t_origin > ? AND dist_km < ?",
                                (t1, self.cfg["catalogs"]["radius_km"])).fetchone()
        tot = sum(r[1] for r in by)
        lines = [f"📊 <b>Riepilogo ultime {hours} ore</b> · {esc(self.cfg['station']['name'])}",
                 f"Eventi registrati: {tot}"]
        for r in by:
            lines.append(f" • {analysis.CLASSES.get(r[0], r[0])}: {r[1]} (PGA max {r[2]:.1f} mg)")
        if noise[0] is not None:
            lines.append(f"Rumore medio {noise[0]:.2f} mg (min {noise[1]:.2f} · max {noise[2]:.2f})")
        if cats[0]:
            lines.append(f"Terremoti nel raggio di {self.cfg['catalogs']['radius_km']} km: {cats[0]} (max M{cats[1]:.1f})")
        du = storage.disk_usage(self.app["data_dir"])
        lines.append(f"Disco {du['pct']}% · CPU {sysinfo.cpu_temp() or '?'} °C")
        return "\n".join(lines)

    # ============================================================ regole
    def on_event_end(self, e: dict):
        rules = self.cfg["alerts"]["rules"]
        mmi = e.get("mmi") or 1
        if not (e["class"] in rules["classes"] and mmi >= rules["min_mmi"] and e["pga"] >= rules["min_pga_mg"]):
            return
        if not self._can_alert(mmi):
            return
        recent = self.con.execute("SELECT COUNT(*) FROM outbox WHERE created > ? AND payload LIKE '%\"kind\": \"event\"%'",
                                  (time.time() - 3600,)).fetchone()[0]
        if recent >= rules["max_per_hour"] * max(1, len(self._channels())):
            log.info("limite allerte orarie raggiunto: evento #%s non notificato", e["id"])
            return
        self.dispatch("event", self.event_text(e), event=e, image=e["id"])
        self.led_alert(mmi)
        self.con.execute("UPDATE events SET notified=1 WHERE id=?", (e["id"],))
        g = self.cfg["alerts"]["gpio"]
        if g["enabled"] and mmi >= g["min_mmi"]:
            asyncio.get_running_loop().create_task(self.gpio_pulse())

    def _can_alert(self, mmi) -> bool:
        rules = self.cfg["alerts"]["rules"]
        if self.muted():
            return False
        if _in_quiet(rules) and mmi < rules["quiet_override_mmi"]:
            return False
        return True

    def on_status(self, st: dict):
        """Allerta immediata se lo scuotimento in corso è già forte (prima della fine dell'evento)."""
        ev = st.get("event")
        if not ev:
            return
        mmi = dsp.mmi_from_pga(ev["pga"] / 1000 * dsp.GAL_PER_G)
        key = ev["t_trigger"]
        if mmi >= max(4.0, self.cfg["alerts"]["rules"]["quiet_override_mmi"]) and key not in self.early_sent:
            self.early_sent.add(key)
            self.led_alert(mmi)
            if self._can_alert(mmi):
                self.dispatch("early", f"🚨 <b>Scossa in corso!</b>\n{fmt_time(key)} · PGA {ev['pga']:.1f} mg · "
                                       f"intensità stimata {dsp.mmi_roman(mmi)}\nSeguirà il resoconto completo.")

    def on_catalog(self, res: dict):
        ac = self.cfg["alerts"]["catalog"]
        if not ac["enabled"]:
            return
        for c in res["new"]:
            if time.time() - c["t_origin"] > 3 * 3600:
                continue
            near = c["dist_km"] is not None and c["dist_km"] <= ac["max_radius_km"]
            if not (ac["notify_regional"] and near and c["mag"] >= ac["min_mag"]):
                continue
            if self.muted():
                continue
            local = self.con.execute("SELECT local_event FROM catalog WHERE id=?", (c["id"],)).fetchone()
            seen = local and local[0]
            self.dispatch("catalog", f"📡 <b>{c['source'].upper()}: terremoto M{c['mag']:.1f}</b>\n{esc(c['place'])}\n"
                                     f"🕒 {fmt_time(c['t_origin'])} · profondità {c['depth_km']:.0f} km\n"
                                     f"📍 {c['dist_km']:.0f} km dalla stazione (azimut {c['azimuth']:.0f}°)\n"
                                     + ("✅ registrato anche da SeismicPi" if seen else "– non rilevato localmente"),
                          catalog=c)
        if self.cfg["alerts"]["rules"]["notify_confirmed"]:
            for eid, cid in res["matches"]:
                e = self.con.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
                if e and not self.muted():
                    self.dispatch("confirmed", "✅ <b>Evento confermato</b>\n" + self.event_text(dict(e)), event=dict(e))

    def system_alert(self, key: str, active: bool, text: str):
        """Allerta di sistema con isteresi: si notifica il cambio di stato, una volta."""
        if not self.cfg["alerts"]["system"]["enabled"]:
            self.sys_flags[key] = active
            return
        prev = self.sys_flags.get(key)
        self.sys_flags[key] = active
        if prev is None and not active:
            return
        if prev != active:
            self.dispatch("system", ("⚠️ " if active else "✅ ") + text)

    def _channels(self):
        a = self.cfg["alerts"]
        ch = []
        if a["telegram"]["enabled"] and a["telegram"]["token"] and a["telegram"]["chat_ids"]:
            ch.append("telegram")
        if a["webhook"]["enabled"] and a["webhook"]["url"]:
            ch.append("webhook")
        return ch

    def dispatch(self, kind: str, text: str, event=None, catalog=None, image=None):
        payload_wh = {"kind": kind, "station": self.cfg["station"]["code"], "t": time.time(),
                      "text": _strip_html(text), "event": event, "catalog": catalog}
        for ch in self._channels():
            if ch == "telegram":
                self.enqueue("telegram", {"kind": kind, "text": text, "event_image": image})
            else:
                self.enqueue("webhook", payload_wh)
        self.mqtt_publish(kind, payload_wh)
        self.app["hub"].broadcast({"t": "alert", "kind": kind, "text": _strip_html(text)})

    def led_alert(self, mmi):
        """Lampeggio rapido del LED di stato per le scosse forti (anche con le allerte sospese:
        è una spia locale, non disturba nessuno)."""
        led = self.app.get("led")
        if led and mmi >= self.cfg["led"]["alert_min_mmi"]:
            led.flash_alert()

    # ============================================================ GPIO
    async def gpio_pulse(self, seconds=None):
        g = self.cfg["alerts"]["gpio"]
        pin = int(g["pin"])
        if self.cfg["led"]["enabled"] and pin == int(self.cfg["led"]["gpio"]):
            raise RuntimeError(f"GPIO{pin} è già usato dal LED di stato: scegli un altro pin (es. 27)")
        on, off = ("dh", "dl") if g["active_high"] else ("dl", "dh")
        if not shutil.which("pinctrl"):
            raise RuntimeError("comando pinctrl non disponibile")
        subprocess.run(["pinctrl", "set", str(pin), "op", on], check=True, timeout=5)
        await asyncio.sleep(seconds or g["duration_s"])
        subprocess.run(["pinctrl", "set", str(pin), "op", off], check=True, timeout=5)

    # ============================================================ MQTT (facoltativo)
    def mqtt_setup(self):
        m = self.cfg["alerts"]["mqtt"]
        if self.mqtt:
            try:
                self.mqtt.loop_stop()
                self.mqtt.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.mqtt = None
        if not m["enabled"] or not m["host"]:
            self.mqtt_error = ""
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.mqtt_error = "libreria paho-mqtt non installata (apt install python3-paho-mqtt)"
            return
        try:
            try:
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"seismicpi-{self.cfg['station']['code']}")
            except AttributeError:
                c = mqtt.Client(client_id=f"seismicpi-{self.cfg['station']['code']}")
            if m["username"]:
                c.username_pw_set(m["username"], m["password"])
            c.will_set(f"{m['topic']}/availability", "offline", retain=True)
            c.connect_async(m["host"], int(m["port"]), 60)
            c.loop_start()
            self.mqtt = c
            self.mqtt_error = ""
            c.publish(f"{m['topic']}/availability", "online", retain=True)
            if m["homeassistant_discovery"]:
                self._ha_discovery()
        except Exception as e:  # noqa: BLE001
            self.mqtt_error = str(e)

    def _ha_discovery(self):
        m = self.cfg["alerts"]["mqtt"]
        code = self.cfg["station"]["code"].lower()
        dev = {"identifiers": [f"seismicpi_{code}"], "name": self.cfg["station"]["name"], "manufacturer": "SeismicPi",
               "model": "Raspberry Pi Zero + MPU-6050"}
        sensors = [("noise", "Rumore sismico", "mg", "{{ value_json.rms_v }}", "state"),
                   ("last_pga", "Ultimo evento PGA", "mg", "{{ value_json.pga }}", "event"),
                   ("last_mmi", "Ultimo evento intensità", None, "{{ value_json.mmi }}", "event"),
                   ("temp", "Temperatura sensore", "°C", "{{ value_json.temp }}", "state")]
        for key, name, unit, tpl, topic in sensors:
            cfg = {"name": name, "unique_id": f"seismicpi_{code}_{key}", "state_topic": f"{m['topic']}/{topic}",
                   "value_template": tpl, "device": dev, "availability_topic": f"{m['topic']}/availability"}
            if unit:
                cfg["unit_of_measurement"] = unit
            self.mqtt.publish(f"homeassistant/sensor/seismicpi_{code}/{key}/config", json.dumps(cfg), retain=True)
        self.mqtt.publish(f"homeassistant/binary_sensor/seismicpi_{code}/shaking/config", json.dumps({
            "name": "Vibrazione in corso", "unique_id": f"seismicpi_{code}_shaking", "device_class": "vibration",
            "state_topic": f"{m['topic']}/shaking", "device": dev,
            "availability_topic": f"{m['topic']}/availability"}), retain=True)

    def mqtt_publish(self, kind, payload):
        if not self.mqtt:
            return
        t = self.cfg["alerts"]["mqtt"]["topic"]
        ev = payload.get("event")
        if kind in ("event", "confirmed") and ev:
            self.mqtt.publish(f"{t}/event", json.dumps({k: ev.get(k) for k in (
                "id", "t_trigger", "pga", "pgv", "mmi", "class", "duration", "dom_freq", "catalog_id")}), retain=True)
        self.mqtt.publish(f"{t}/alert", json.dumps(payload, default=str))

    def mqtt_state(self, st: dict, shaking: bool | None = None):
        if not self.mqtt:
            return
        t = self.cfg["alerts"]["mqtt"]["topic"]
        m = st.get("minute") or {}
        self.mqtt.publish(f"{t}/state", json.dumps({"rms_v": m.get("rms_v"), "temp": st.get("temp"),
                                                   "sensor_ok": st.get("sensor_ok"), "fs": st.get("fs")}))
        if shaking is not None:
            self.mqtt.publish(f"{t}/shaking", "ON" if shaking else "OFF", retain=True)

    # ============================================================ riepilogo giornaliero
    async def daily_loop(self):
        while True:
            await asyncio.sleep(30)
            dr = self.cfg["alerts"]["daily_report"]
            if not dr["enabled"]:
                continue
            now = dt.datetime.now()
            if now.strftime("%H:%M") >= dr["time"] and self.last_report_day != now.date():
                if self.last_report_day is None and now.strftime("%H:%M") > dr["time"][:3] + "59":
                    self.last_report_day = now.date()      # avvio dopo l'orario: si aspetta domani
                    continue
                self.last_report_day = now.date()
                self.dispatch("report", self.report_text(24))


def fmt_acc(mg: float, units: str) -> str:
    if units == "gal":
        return f"{mg / 1000 * dsp.GAL_PER_G:.2f} cm/s²"
    if units == "ms2":
        return f"{mg / 1000 * dsp.G:.4f} m/s²"
    return f"{mg:.2f} mg"


def _strip_html(s: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", s).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _png_from_blocks(blocks, t1, t2, mark, title):
    ser = storage.blocks_to_series(blocks, t1, t2)
    if not ser:
        return None
    t0, fs, e, n, z = ser
    traces = {}
    for name, v in (("E", e), ("N", n), ("Z", z)):
        vals = [x for x in v if x is not None]
        mean = sum(vals) / len(vals) if vals else 0
        hp = dsp.Biquad("hp", 0.5, fs)
        hp.prime(0.0)
        out = []
        for x in v:
            out.append(None if x is None else hp(x - mean) * 1000)
        traces[name] = out
    return pngplot.waveform_png(t0, fs, traces, title, mark)
