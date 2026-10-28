"""Test del nucleo: python3 -m unittest discover -s tests"""
import array
import math
import os
import struct
import sys
import tempfile
import time
import unittest

TMP = tempfile.mkdtemp(prefix="seismicpi-test-")
os.environ["SEISMICPI_DATA"] = TMP
os.environ["SEISMICPI_RUN"] = os.path.join(TMP, "run")
os.environ["SEISMICPI_RAM"] = os.path.join(TMP, "ram")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from seismicpi import analysis, catalogs, config, dsp, miniseed, pngplot, storage  # noqa: E402
from seismicpi.acquisition import SampleClock  # noqa: E402


class TestDsp(unittest.TestCase):
    def test_highpass_removes_gravity(self):
        f = dsp.Biquad("hp", 0.5, 100)
        f.prime(1.0)
        out = [f(1.0) for _ in range(500)]
        self.assertLess(max(abs(x) for x in out), 1e-9)

    def test_lowpass_passes_dc(self):
        f = dsp.Biquad("lp", 10, 100)
        out = [f(1.0) for _ in range(2000)]
        self.assertAlmostEqual(out[-1], 1.0, places=6)

    def test_stalta_reacts_to_burst(self):
        s = dsp.StaLta(0.5, 10, 100)
        r = [s(1.0) for _ in range(1500)]
        self.assertAlmostEqual(r[-1], 1.0, places=2)
        burst = [s(25.0) for _ in range(100)]
        self.assertGreater(max(burst), 4)

    def test_mmi_relations(self):
        self.assertAlmostEqual(dsp.mmi_from_pga(10 ** 1.57), 1.78 + 1.55 * 1.57, places=6)
        self.assertEqual(dsp.mmi_roman(4.4), "IV")
        self.assertGreater(dsp.mmi_estimate(200, 15), 6)

    def test_distance_and_travel_times(self):
        d = dsp.haversine_km(41.9, 12.5, 45.46, 9.19)       # Roma–Milano ≈ 477 km
        self.assertTrue(470 < d < 485)
        tp, ts = dsp.travel_times(100, 10)
        self.assertTrue(10 < tp < 20 and ts > tp)
        tp_far, _ = dsp.travel_times(30 * 111.195, 0)
        self.assertAlmostEqual(tp_far, 373, delta=5)


class TestStorage(unittest.TestCase):
    def test_raw_roundtrip_and_series(self):
        base = tempfile.mkdtemp(dir=TMP)
        raw = storage.RawArchive(base)
        t0 = 1_700_000_000.0
        for b in range(3):
            data = array.array("h", [i % 50 for i in range(300)])
            raw.append(t0 + b, 100.0, 16384, data)
        raw.flush()
        blocks = raw.read(t0, t0 + 3)
        self.assertEqual(len(blocks), 3)
        ser = storage.blocks_to_series(blocks, t0, t0 + 3)
        self.assertEqual(len(ser[2]), 300)
        self.assertAlmostEqual(ser[2][1], 3 / 16384)

    def test_purge_hours(self):
        base = tempfile.mkdtemp(dir=TMP)
        raw = storage.RawArchive(base)
        now = time.time()
        for h in (10, 5, 0):
            raw.append(now - h * 3600, 100.0, 16384, array.array("h", [0] * 30))
        raw.flush()
        raw.close()
        removed = raw.purge_hours(6, now)
        self.assertEqual(len(removed), 1)

    def test_helicorder(self):
        base = tempfile.mkdtemp(dir=TMP)
        hc = storage.Helicorder(base)
        t = storage.day_start("20260101") + 100
        for i in range(400):
            hc.add(t + i / 100, (i % 7 - 3, 0.0, 5.0, 1.0))
        hc._commit()
        hc.flush()
        data = array.array("h")
        data.frombytes(hc.read("20260101", "E", 200, 208))
        self.assertEqual(data[0], -30)     # min -3 mg in unità 0,1 mg
        self.assertEqual(data[1], 30)
        empty = array.array("h")
        empty.frombytes(hc.read("20260101", "Z", 0, 1))
        self.assertEqual(list(empty), [storage.EMPTY_MIN, storage.EMPTY_MAX])

    def test_miniseed_records(self):
        t0 = 1_700_000_000.25
        blocks = [(t0, 100.0, 16384, array.array("h", [v for i in range(300) for v in (i, -i, 2 * i)]))]
        ms = miniseed.write(blocks, "XX", "SPI01", "00", "E")
        self.assertEqual(len(ms) % 512, 0)
        self.assertEqual(ms[6:7], b"D")
        self.assertEqual(ms[15:18], b"ENE")
        nsamp = struct.unpack(">H", ms[30:32])[0]
        self.assertEqual(nsamp, 224)
        self.assertEqual(struct.unpack(">H", ms[48:50])[0], 1000)


class TestConfig(unittest.TestCase):
    def test_defaults_valid(self):
        cfg = config.validate(config.deep_merge(config.DEFAULTS, {}))
        self.assertEqual(cfg["storage"]["raw_mode"], "ram")

    def test_invalid_values(self):
        with self.assertRaises(config.ConfigError):
            config.patch(config.DEFAULTS, {"trigger": {"off_ratio": 9, "on_ratio": 4}})
        with self.assertRaises(config.ConfigError):
            config.patch(config.DEFAULTS, {"sensor": {"axis_map": {"E": "+x", "N": "+x", "Z": "+z"}}})

    def test_secrets_redacted(self):
        cfg = config.patch(config.DEFAULTS, {"alerts": {"telegram": {"token": "123:abc"}}})
        red = config.redacted(cfg)
        self.assertEqual(red["alerts"]["telegram"]["token"], "__set__")
        back = config.strip_unchanged_secrets(red)
        self.assertNotIn("token", back["alerts"]["telegram"])


class TestAnalysis(unittest.TestCase):
    def _event(self, freq, dur, amp=20.0, h_over_v=2.0):
        fs = 100.0
        acc = analysis.EventAccumulator(0.0, "stalta", fs)
        vel = [dsp.Velocity(fs, 0.2) for _ in range(3)]
        n = int((dur + 2) * fs)
        for i in range(n):
            t = i / fs
            env = math.sin(math.pi * t / dur) ** 2 if t < dur else 0.0     # inizio e fine graduali
            a = amp * env * math.sin(2 * math.pi * freq * t)
            comps = (a, a * 0.5, a / h_over_v)
            v = [vel[k](comps[k] / 1000) for k in range(3)]
            acc.add(comps, v, math.sqrt(sum(c * c for c in comps)), 5.0, t < dur)
        return acc.result(dur)

    def test_quake_like(self):
        r = self._event(3.0, 10.0)
        self.assertEqual(r["class"], "possibile_sisma")
        self.assertAlmostEqual(r["dom_freq"], 3.0, delta=0.6)

    def test_impulse(self):
        r = self._event(25.0, 0.5)
        self.assertEqual(r["class"], "impulso")


class TestCatalogs(unittest.TestCase):
    def test_parse_fdsn_text(self):
        body = ("#EventID|Time|Latitude|Longitude|Depth/Km|Author|Catalog|Contributor|ContributorID|MagType|Magnitude|MagAuthor|EventLocationName|EventType\n"
                "44173452|2026-10-28T09:12:33.120000|42.85|13.12|9.8|SURVEY-INGV||||ML|3.4|--|3 km SE Norcia (PG)|earthquake\n")
        ev = catalogs.parse_text("ingv", body)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["id"], "ingv:44173452")
        self.assertAlmostEqual(ev[0]["mag"], 3.4)
        catalogs.enrich(ev[0], 41.9, 12.5)
        self.assertTrue(100 < ev[0]["dist_km"] < 130)
        self.assertGreater(ev[0]["t_s"], ev[0]["t_p"])

    def test_detectable(self):
        self.assertTrue(catalogs.detectable(4.5, 100))
        self.assertFalse(catalogs.detectable(3.0, 500))


class TestClockAndPng(unittest.TestCase):
    def test_sample_clock_learns_rate(self):
        c = SampleClock(100.0)
        true_fs, t, n = 101.0, 1000.0, 0
        for _ in range(400):
            t += 0.1
            k = int((t - 1000.0) * true_fs) - n
            c.stamp(k, t)
            n += k
        self.assertAlmostEqual(c.fs, true_fs, delta=0.1)

    def test_png(self):
        png = pngplot.waveform_png(0, 100, {"E": [math.sin(i / 5) for i in range(1000)], "N": [None] * 1000,
                                            "Z": [0.0] * 1000}, "PROVA 12:00", 3.0, w=300, h=200)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
