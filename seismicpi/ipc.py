"""Canale locale fra acquisizione e web: socket UNIX, un messaggio JSON per riga.

L'acquisizione è il server e trasmette a tutti i client (dati live, stato, eventi); i client
mandano comandi {"cmd": ..., "rid": n} e ricevono {"t": "reply", "rid": n, ...}.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

log = logging.getLogger("seismicpi.ipc")


def dumps(msg: dict) -> bytes:
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode()


class Server:
    def __init__(self, path: str, handler):
        self.path = path
        self.handler = handler          # async (msg) -> dict risposta
        self.clients: set[asyncio.StreamWriter] = set()
        self.server = None

    async def start(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self.server = await asyncio.start_unix_server(self._client, path=self.path)
        os.chmod(self.path, 0o660)

    async def _client(self, reader, writer):
        self.clients.add(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                try:
                    reply = await self.handler(msg) or {}
                except Exception as e:  # noqa: BLE001 – l'errore torna al chiamante
                    log.exception("comando %s fallito", msg.get("cmd"))
                    reply = {"ok": False, "error": str(e)}
                reply.update({"t": "reply", "rid": msg.get("rid")})
                writer.write(dumps(reply))
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.clients.discard(writer)
            writer.close()

    def broadcast(self, msg: dict):
        if not self.clients:
            return
        data = dumps(msg)
        for w in list(self.clients):
            # un client lento non deve bloccare l'acquisizione: oltre 1 MB in coda viene staccato
            if w.transport.get_write_buffer_size() > 1_000_000:
                self.clients.discard(w)
                w.close()
                continue
            w.write(data)


class Client:
    """Client con riconnessione automatica; on_message riceve i messaggi broadcast."""

    def __init__(self, path: str, on_message):
        self.path = path
        self.on_message = on_message
        self.writer = None
        self.pending: dict[int, asyncio.Future] = {}
        self.rid = 0
        self.connected = asyncio.Event()

    async def run(self):
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(self.path, limit=4 * 1024 * 1024)
            except (FileNotFoundError, ConnectionError, PermissionError):
                await asyncio.sleep(2)
                continue
            self.writer = writer
            self.connected.set()
            log.info("collegato all'acquisizione")
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    msg = json.loads(line)
                    if msg.get("t") == "reply":
                        fut = self.pending.pop(msg.get("rid"), None)
                        if fut and not fut.done():
                            fut.set_result(msg)
                    else:
                        await self.on_message(msg)
            except (ConnectionError, ValueError) as e:
                log.warning("canale acquisizione interrotto: %s", e)
            finally:
                self.connected.clear()
                self.writer = None
                for f in self.pending.values():
                    if not f.done():
                        f.set_exception(ConnectionError("acquisizione disconnessa"))
                self.pending.clear()
            await asyncio.sleep(1)

    async def call(self, cmd: str, timeout: float = 30, **kw) -> dict:
        if not self.writer:
            raise ConnectionError("servizio di acquisizione non raggiungibile")
        self.rid += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.rid] = fut
        self.writer.write(dumps({"cmd": cmd, "rid": self.rid, **kw}))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(self.rid, None)
