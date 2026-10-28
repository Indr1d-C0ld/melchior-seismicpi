"""Driver MPU-6050 (modulo GY-521) via I2C, con lettura dalla FIFO hardware.

Solo l'accelerometro va in FIFO (6 byte/campione): 1024 byte = 170 campioni, cioè 1,7 s di
margine a 100 Hz prima di perdere dati se il processo resta bloccato.
"""
from __future__ import annotations

import time

from . import Sensor, SensorError

# registri
SELF_TEST_X, SELF_TEST_A = 0x0D, 0x10
SMPLRT_DIV, CONFIG, GYRO_CONFIG, ACCEL_CONFIG = 0x19, 0x1A, 0x1B, 0x1C
FIFO_EN, INT_ENABLE, INT_STATUS = 0x23, 0x38, 0x3A
ACCEL_XOUT_H, TEMP_OUT_H = 0x3B, 0x41
USER_CTRL, PWR_MGMT_1, PWR_MGMT_2 = 0x6A, 0x6B, 0x6C
FIFO_COUNTH, FIFO_R_W, WHO_AM_I = 0x72, 0x74, 0x75

KNOWN_IDS = {0x68: "MPU-6050", 0x98: "MPU-6050 (clone)", 0x70: "MPU-6500", 0x71: "MPU-9250",
             0x73: "MPU-9255", 0x72: "MPU-6050 (clone 0x72)", 0x19: "MPU-6886"}
DLPF_BW = {0: 260, 1: 184, 2: 94, 3: 44, 4: 21, 5: 10, 6: 5}


def _s16(hi, lo):
    v = (hi << 8) | lo
    return v - 65536 if v & 0x8000 else v


class MPU6050(Sensor):
    name = "MPU-6050"

    def __init__(self, scfg: dict):
        self.bus_no = int(scfg.get("i2c_bus", 1))
        self.addr = int(scfg.get("address", 0x68))
        self.fs = float(scfg["sample_rate"])
        self.dlpf = int(scfg.get("dlpf", 3))
        self.range_g = int(scfg["range_g"])
        self.lsb_per_g = {2: 16384, 4: 8192, 8: 4096, 16: 2048}[self.range_g]
        self.bus = None
        self.chip = "?"

    # ------------------------------------------------------------ I2C
    def _w(self, reg, val):
        self.bus.write_byte_data(self.addr, reg, val & 0xFF)

    def _r(self, reg):
        return self.bus.read_byte_data(self.addr, reg)

    def _rn(self, reg, n):
        from smbus2 import i2c_msg
        w = i2c_msg.write(self.addr, [reg])
        r = i2c_msg.read(self.addr, n)
        self.bus.i2c_rdwr(w, r)
        return list(r)

    # ------------------------------------------------------------ ciclo di vita
    def open(self) -> dict:
        try:
            from smbus2 import SMBus
        except ImportError as e:
            raise SensorError("modulo smbus2 mancante (apt install python3-smbus2)") from e
        try:
            self.bus = SMBus(self.bus_no)
            who = self._r(WHO_AM_I)
        except (OSError, FileNotFoundError) as e:
            self.close()
            raise SensorError(f"sensore non risponde su i2c-{self.bus_no} @0x{self.addr:02x}: {e}") from e
        self.chip = KNOWN_IDS.get(who, f"sconosciuto (WHO_AM_I=0x{who:02x})")
        try:
            self._w(PWR_MGMT_1, 0x80)           # reset
            time.sleep(0.1)
            self._w(PWR_MGMT_1, 0x01)           # sveglia, clock PLL dal giroscopio X (più stabile)
            time.sleep(0.05)
            self._w(PWR_MGMT_2, 0x00)
            self._w(CONFIG, self.dlpf if self.dlpf else 1)   # con DLPF attivo il clock interno è 1 kHz
            self._w(SMPLRT_DIV, int(round(1000 / self.fs)) - 1)
            self._w(GYRO_CONFIG, 0x00)
            self._w(ACCEL_CONFIG, {2: 0, 4: 1, 8: 2, 16: 3}[self.range_g] << 3)
            self._w(INT_ENABLE, 0x10)           # abilita il flag di overflow FIFO
            self.reset_fifo()
        except OSError as e:
            self.close()
            raise SensorError(f"configurazione sensore fallita: {e}") from e
        return {"chip": self.chip, "who_am_i": who, "fs": self.fs, "range_g": self.range_g,
                "dlpf_hz": DLPF_BW.get(self.dlpf, "?"), "bus": self.bus_no, "address": self.addr}

    def reset_fifo(self):
        self._w(USER_CTRL, 0x00)
        self._w(FIFO_EN, 0x00)
        self._w(USER_CTRL, 0x04)                # FIFO_RESET
        time.sleep(0.01)
        self._w(USER_CTRL, 0x40)                # FIFO_EN
        self._w(FIFO_EN, 0x08)                  # solo accelerometro
        self._r(INT_STATUS)                     # azzera i flag

    def close(self):
        if self.bus is not None:
            try:
                self.bus.close()
            except OSError:
                pass
        self.bus = None

    # ------------------------------------------------------------ dati
    def read(self):
        try:
            status = self._r(INT_STATUS)
            if status & 0x10:
                self.reset_fifo()
                return [], True
            hi, lo = self._rn(FIFO_COUNTH, 2)
            count = (hi << 8) | lo
            n = count // 6
            if n == 0:
                return [], False
            out = []
            # letture a pezzi da al più 252 byte (42 campioni): alcuni adattatori I2C limitano la dimensione
            while n > 0:
                k = min(n, 42)
                b = self._rn(FIFO_R_W, k * 6)
                for i in range(0, k * 6, 6):
                    out.append((_s16(b[i], b[i + 1]), _s16(b[i + 2], b[i + 3]), _s16(b[i + 4], b[i + 5])))
                n -= k
            return out, False
        except OSError as e:
            raise SensorError(f"errore I2C: {e}") from e

    def temperature(self) -> float:
        try:
            hi, lo = self._rn(TEMP_OUT_H, 2)
        except OSError as e:
            raise SensorError(f"errore I2C: {e}") from e
        return _s16(hi, lo) / 340.0 + 36.53

    def read_instant(self):
        try:
            b = self._rn(ACCEL_XOUT_H, 6)
        except OSError as e:
            raise SensorError(f"errore I2C: {e}") from e
        return _s16(b[0], b[1]), _s16(b[2], b[3]), _s16(b[4], b[5])

    # ------------------------------------------------------------ autotest di fabbrica
    def self_test(self) -> dict:
        """Autotest dell'accelerometro secondo il datasheet MPU-6000/6050 (rev. 3.4, §4.1).

        Si misura la risposta con l'auto-eccitazione attiva a ±8 g e la si confronta con il valore
        di fabbrica (trim): scostamento entro ±14% = superato.
        """
        def avg(n=50):
            sx = sy = sz = 0
            for _ in range(n):
                x, y, z = self.read_instant()
                sx += x; sy += y; sz += z
                time.sleep(0.005)
            return sx / n, sy / n, sz / n

        try:
            self._w(FIFO_EN, 0x00)
            self._w(ACCEL_CONFIG, 2 << 3)               # ±8 g
            time.sleep(0.1)
            off = avg()
            self._w(ACCEL_CONFIG, 0xE0 | (2 << 3))      # self-test X/Y/Z
            time.sleep(0.1)
            on = avg()
            raw = [self._r(SELF_TEST_X + i) for i in range(3)]
            ra = self._r(SELF_TEST_A)
            a_test = [((raw[0] >> 3) & 0x1C) | ((ra >> 4) & 0x03),
                      ((raw[1] >> 3) & 0x1C) | ((ra >> 2) & 0x03),
                      ((raw[2] >> 3) & 0x1C) | (ra & 0x03)]
        except OSError as e:
            raise SensorError(f"errore I2C durante l'autotest: {e}") from e
        finally:
            try:
                self._w(ACCEL_CONFIG, {2: 0, 4: 1, 8: 2, 16: 3}[self.range_g] << 3)
                self.reset_fifo()
            except OSError:
                pass
        res = {}
        ok = True
        for i, ax in enumerate("xyz"):
            ft = 4096 * 0.34 * (0.92 / 0.34) ** ((a_test[i] - 1) / 30) if a_test[i] else 0
            str_ = on[i] - off[i]
            dev = (str_ - ft) / ft * 100 if ft else None
            passed = dev is not None and abs(dev) <= 14
            ok &= passed
            res[ax] = {"response": round(str_, 1), "factory": round(ft, 1),
                       "deviation_pct": None if dev is None else round(dev, 1), "pass": passed}
        return {"ok": ok, "axes": res, "chip": self.chip,
                "note": "i cloni economici spesso hanno trim di fabbrica assenti: in quel caso il test non è significativo"}
