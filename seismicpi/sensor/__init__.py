"""Driver dei sensori. Tutti espongono la stessa interfaccia (vedi Sensor)."""
from __future__ import annotations


class SensorError(Exception):
    pass


class Sensor:
    """Interfaccia comune.

    read() restituisce (campioni, overflow): campioni è una lista di terne (x, y, z) in conteggi
    grezzi sugli assi del chip; overflow è True se la FIFO è traboccata (dati persi: buco).
    """

    name = "?"
    lsb_per_g = 16384
    fs = 100.0

    def open(self) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def read(self) -> tuple[list[tuple[int, int, int]], bool]:
        raise NotImplementedError

    def temperature(self) -> float:
        return float("nan")

    def self_test(self) -> dict:
        return {"ok": None, "note": "non supportato"}

    def read_instant(self) -> tuple[int, int, int]:
        """Una lettura diretta (fuori FIFO), per la calibrazione."""
        raise NotImplementedError


def make(cfg: dict) -> Sensor:
    s = cfg["sensor"]
    if s["driver"] == "sim":
        from .simulated import SimSensor
        return SimSensor(s)
    from .mpu6050 import MPU6050
    return MPU6050(s)
